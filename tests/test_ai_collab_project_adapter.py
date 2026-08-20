# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient

"""Contract tests for the generic, config-driven project adapter.

The adapter is exercised exactly the way the Host runs it: as a subprocess
with one JSON request on stdin and ``AI_COLLAB_PROJECT_ROOT`` in the
environment. The fixture project is synthetic and deliberately not named
after any real project, because the point of the adapter is that project
identity comes from the project's own declaration files.
"""

from __future__ import annotations

import json
import importlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ADAPTER = SCRIPTS / "ai_collab_project_adapter.py"
ADAPTER_ID = "ai-collab-project-adapter-v1"

DESCRIPTOR_TEMPLATE = """\
schema_version: 1
project_key: {project_key}
product_contract_version: "1.0"
workspace_adapter: {workspace_adapter}
repo_manifest: repo_manifest.yaml
environment_adapter: {environment_adapter}
gate_registry: gates.yaml
participant_driver_contract: 2
collaboration_policy_schema: 1
"""

MANIFEST_TEMPLATE = """\
schema_version: 1
project_key: {project_key}
repos:
  - repo_key: {project_key}
    classification: required
    placement: project_root
    path: .
    remote: git@github.com:example/{project_key}.git
    base_branch: main
    provision_order: 0
    provision_after: []
    acceptance_layer: base
    smoke_policy: required
    dependency_lock: requirements.lock
    python_source_path: src
    python_import_name: samplepkg
  - repo_key: helper-lib
    classification: required
    placement: bundle_sibling
    path: helper-lib
    remote: https://github.com/example/helper-lib.git
    base_branch: main
    provision_order: 10
    provision_after: [{project_key}]
    acceptance_layer: base
    smoke_policy: optional
"""


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _git_output(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *arguments], stderr=subprocess.DEVNULL
    ).decode().strip()


def _stale_index_snapshot(repo: Path, tracked_file: Path) -> tuple[Path, bytes, int]:
    """Make cached stat data stale, then capture the exact index publication."""

    details = tracked_file.stat()
    os.utime(
        tracked_file,
        ns=(details.st_atime_ns, max(1, details.st_mtime_ns - 10_000_000_000)),
    )
    index = Path(_git_output(repo, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = repo / index
    return index, index.read_bytes(), index.stat().st_mtime_ns


def _assert_index_unchanged(snapshot: tuple[Path, bytes, int]) -> None:
    index, contents, modified = snapshot
    assert index.read_bytes() == contents
    assert index.stat().st_mtime_ns == modified


def _init_repo(path: Path, remote: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Adapter Test")
    _git(path, "add", "-A")
    _git(path, "commit", "--allow-empty", "-m", "initial")
    _git(path, "remote", "add", "origin", remote)


def _build_project(base: Path, project_key: str = "sampleproject") -> Path:
    project = base / "canonical" / project_key
    project.mkdir(parents=True)
    (project / "project_descriptor.yaml").write_text(
        DESCRIPTOR_TEMPLATE.format(
            project_key=project_key,
            workspace_adapter="ai-collab-workspace-v1",
            environment_adapter="ai-collab-environment-v1",
        ),
        encoding="utf-8",
    )
    (project / "gates.yaml").write_text(
        "schema_version: 1\n"
        f"registry_id: ai-collab-scenario-harness-{project_key}-v1.0-20260817\n",
        encoding="utf-8",
    )
    (project / "repo_manifest.yaml").write_text(
        MANIFEST_TEMPLATE.format(project_key=project_key), encoding="utf-8"
    )
    (project / "ai_collab_team_policies.json").write_text(
        json.dumps(
            {"schema_version": 1, "templates": [{"template_id": "team.solo"}]}
        ),
        encoding="utf-8",
    )
    package = project / "src" / "samplepkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "requirements.lock").write_text("samplepkg==1.0\n", encoding="utf-8")
    _init_repo(project, f"git@github.com:example/{project_key}.git")
    _init_repo(
        project.parent / "helper-lib", "https://github.com/example/helper-lib.git"
    )
    return project.resolve()


def _call(
    project_root: Path,
    operation: str,
    payload: dict[str, Any],
    *,
    adapter_id: str = ADAPTER_ID,
    extra_environment: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None, bytes]:
    request = {
        "adapter_protocol_version": 1,
        "adapter_id": adapter_id,
        "operation": operation,
        "payload": payload,
    }
    completed = subprocess.run(
        [sys.executable, str(ADAPTER)],
        input=json.dumps(request).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            **(extra_environment or {}),
            "AI_COLLAB_PROJECT_ROOT": str(project_root),
        },
    )
    reply = None
    if completed.returncode == 0:
        reply = json.loads(completed.stdout)
    return completed.returncode, reply, completed.stderr


def _remote_only_helper(project: Path, tmp_path: Path) -> tuple[str, dict[str, str]]:
    helper = project.parent / "helper-lib"
    bare = tmp_path / "helper-remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(helper), str(bare)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(helper)
    remote = f"ssh://git@localhost:22{bare}"
    manifest = project / "repo_manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "https://github.com/example/helper-lib.git", remote
        ),
        encoding="utf-8",
    )
    ssh = tmp_path / "test-ssh.py"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import os,shlex,sys\n"
        "if '-G' in sys.argv: raise SystemExit(0)\n"
        "parts=shlex.split(sys.argv[-1])\n"
        "if len(parts)!=2 or parts[0]!='git-upload-pack': raise SystemExit(2)\n"
        "os.execvp(parts[0], parts)\n",
        encoding="utf-8",
    )
    ssh.chmod(0o700)
    return remote, {"GIT_SSH_COMMAND": str(ssh), "GIT_SSH_VARIANT": "ssh"}


def _assert_failed(
    result: tuple[int, dict[str, Any] | None, bytes], expected_code: str
) -> dict[str, Any]:
    code, reply, stderr = result
    assert code == 0, stderr
    assert reply is not None
    assert reply["outcome"] == "failed"
    error = reply["result"]["error"]
    assert error["code"] == expected_code
    assert error["mutation_state"] == "not_started"
    return error


def _load_adapter_module() -> ModuleType:
    scripts = str(SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("ai_collab_project_adapter")


def _write_unit_binding_marker(
    adapter: ModuleType,
    bundle: Path,
    *,
    plan: dict[str, Any],
    receipt: dict[str, Any],
    journal: dict[str, Any],
) -> dict[str, Any]:
    snapshot = {
        "snapshot_contract_version": 1,
        "scenario": receipt["scenario"],
        "plan_digest": adapter.canonical_json_sha256(plan),
        "receipt_digest": adapter.canonical_json_sha256(receipt),
        "components": [
            {
                "component_id": item["component_id"],
                "revision_kind": item["revision_kind"],
                "exact_revision": item["realized_revision"],
                "target_ref": item["target_ref"],
                "content_digest": item["content_digest"],
            }
            for item in receipt["components"]
        ],
    }
    snapshot["snapshot_digest"] = adapter.canonical_json_sha256(snapshot)
    value = {
        "journal": journal,
        "receipt": receipt,
        "review_snapshot": snapshot,
    }
    marker = bundle / ".ai-collab-harness-binding.json"
    marker.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o600)
    return value


@pytest.fixture
def adapter_unit_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Small valid private bundle for high-risk adapter fault injection."""

    adapter = _load_adapter_module()
    canonical = tmp_path / "canonical" / "unit-project"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(adapter, "ROOT", canonical)

    parent = tmp_path / "workspace-unit"
    parent.mkdir(mode=0o700)
    bundle = parent / "bundle"
    bundle.mkdir(mode=0o700)
    component = bundle / "component"
    component.mkdir(mode=0o700)
    environment = bundle / ".venv"
    environment.mkdir(mode=0o700)

    scenario = {"scenario_id": "scenario-unit", "scenario_generation": 1}
    plan = {
        "plan_contract_version": 1,
        "plan_id": "plan:unit",
        "operation_id": "wsop-plan-unit",
        "scenario": scenario,
        "components": [{"component_id": "component"}],
        "environment": {"environment_id": "environment:unit"},
    }
    provision_journal = {
        "journal_contract_version": 1,
        "operation_id": "wsop-plan-unit",
        "operation_kind": "provision",
        "plan_digest": adapter.canonical_json_sha256(plan),
        "scenario": scenario,
        "operation_fence": None,
        "events": [],
    }
    component_receipt = {
        "component_id": "component",
        "placement": "bundle_sibling",
        "logical_path": "component",
        "revision_kind": "commit",
        "realized_revision": "1" * 40,
        "target_ref": "main",
        "content_digest": "2" * 64,
    }
    receipt = {
        "receipt_contract_version": 1,
        "receipt_id": "receipt:unit",
        "plan_digest": adapter.canonical_json_sha256(plan),
        "operation_id": "wsop-plan-unit",
        "base_receipt_digest": None,
        "scenario": scenario,
        "workspace_id": "workspace-unit",
        "workspace_binding_digest": "3" * 64,
        "components": [component_receipt],
        "environment": {
            "environment_id": "environment:unit",
            "environment_binding_digest": "4" * 64,
        },
        "journal_digest": adapter.canonical_json_sha256(provision_journal),
        "source_wip_before_digest": "5" * 64,
        "source_wip_after_digest": "5" * 64,
        "finalization": {
            "staging_binding_digest": "6" * 64,
            "atomic_publish": True,
            "expected_registry_revision": 0,
            "committed_ready_revision": 1,
        },
        "state": "ready",
    }
    _write_unit_binding_marker(
        adapter,
        bundle,
        plan=plan,
        receipt=receipt,
        journal=provision_journal,
    )

    observation_state = {"state": "aligned"}
    wip_digest = "7" * 64

    def fake_status(payload: dict[str, Any]) -> dict[str, Any]:
        observed_receipt = payload["receipt"]
        return {
            "journal": {},
            "observation": {
                "state": observation_state["state"],
                "drift_codes": (
                    [] if observation_state["state"] == "aligned" else ["workspace.drift"]
                ),
                "wip_summary_digest": wip_digest,
                "ownership_summary_digest": "8" * 64,
                "receipt_digest": adapter.canonical_json_sha256(observed_receipt),
            },
        }

    monkeypatch.setattr(adapter, "_status", fake_status)
    return {
        "adapter": adapter,
        "parent": parent,
        "bundle": bundle,
        "component": component,
        "plan": plan,
        "receipt": receipt,
        "wip_digest": wip_digest,
        "observation_state": observation_state,
    }


def _repair_payload(fixture: dict[str, Any], operation_id: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "bundle_path": str(fixture["bundle"]),
        "plan": fixture["plan"],
        "receipt": fixture["receipt"],
        "expected_wip_summary_digest": fixture["wip_digest"],
    }


def _destroy_payload(
    fixture: dict[str, Any],
    operation_id: str,
    *,
    receipt: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "bundle_path": str(fixture["bundle"]),
        "plan": fixture["plan"],
        "receipt": receipt or fixture["receipt"],
        "expected_wip_summary_digest": fixture["wip_digest"],
        "force": force,
    }


def _recover_payload(
    fixture: dict[str, Any],
    operation_id: str,
    *,
    prior_operation_id: str,
    prior_operation_kind: str,
    force: bool = False,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "bundle_path": str(fixture["bundle"]),
        "plan": fixture["plan"],
        "receipt": fixture["receipt"],
        "expected_wip_summary_digest": fixture["wip_digest"],
        "prior_operation": {
            "operation_id": prior_operation_id,
            "operation_kind": prior_operation_kind,
            "force": force,
            "claim_digest": "9" * 64,
        },
    }


def _assert_unknown_adapter_error(
    adapter: ModuleType,
    call: Any,
    expected_code: str,
) -> Any:
    with pytest.raises(adapter.AdapterError) as raised:
        call()
    assert raised.value.code == expected_code
    assert raised.value.mutation_state == "unknown"
    assert raised.value.retryable is True
    return raised.value


def _assert_not_started_adapter_error(adapter: ModuleType, call: Any) -> Any:
    with pytest.raises(adapter.AdapterError) as raised:
        call()
    assert raised.value.mutation_state == "not_started"
    assert raised.value.retryable is False
    return raised.value


def test_descriptor_declares_manual_recover_operation() -> None:
    adapter = _load_adapter_module()

    assert all(
        "recover" in descriptor["operations"]
        for descriptor in adapter._descriptors()
    )


def test_recover_prior_repair_with_exact_base_is_read_only_and_idempotent(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _recover_payload(
        fixture,
        "wsop-recover-repair-base",
        prior_operation_id="wsop-prior-repair-base",
        prior_operation_kind="repair",
    )
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker_before = marker_path.read_bytes()

    first = adapter._recover(payload)
    second = adapter._recover(payload)

    assert second == first
    assert set(first) == {
        "journal",
        "receipt",
        "observation",
        "review_snapshot",
        "recovery",
    }
    assert first["journal"]["operation_id"] == "wsop-recover-repair-base"
    assert first["journal"]["operation_kind"] == "recover"
    assert first["receipt"] == fixture["receipt"]
    assert first["observation"]["state"] == "aligned"
    assert first["observation"]["wip_summary_digest"] == fixture["wip_digest"]
    assert first["recovery"] == {
        "prior_operation_id": "wsop-prior-repair-base",
        "prior_operation_kind": "repair",
        "prior_claim_digest": "9" * 64,
        "resolution": "ready",
    }
    assert marker_path.read_bytes() == marker_before


def test_recover_prior_repair_adopts_only_its_exact_published_marker(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-repair-adopt"
    repaired = adapter._repair(_repair_payload(fixture, prior_operation_id))
    payload = _recover_payload(
        fixture,
        "wsop-recover-repair-adopt",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="repair",
    )
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker_before = marker_path.read_bytes()

    recovered = adapter._recover(payload)

    assert recovered["receipt"] == repaired["receipt"]
    assert recovered["review_snapshot"] == repaired["review_snapshot"]
    assert recovered["recovery"]["resolution"] == "ready"
    assert recovered["observation"]["receipt_digest"] == (
        adapter.canonical_json_sha256(repaired["receipt"])
    )
    assert marker_path.read_bytes() == marker_before
    assert adapter._recover(payload) == recovered


def test_recover_prior_repair_rejects_invalid_marker_without_rewriting_it(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["review_snapshot"]["snapshot_digest"] = "f" * 64
    marker_path.write_text(
        json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
    )
    marker_before = marker_path.read_bytes()
    payload = _recover_payload(
        fixture,
        "wsop-recover-repair-invalid",
        prior_operation_id="wsop-prior-repair-invalid",
        prior_operation_kind="repair",
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert marker_path.read_bytes() == marker_before


def test_recover_prior_repair_never_adopts_missing_resolution(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    shutil.rmtree(fixture["bundle"])
    payload = _recover_payload(
        fixture,
        "wsop-recover-repair-missing",
        prior_operation_id="wsop-prior-repair-missing",
        prior_operation_kind="repair",
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert not fixture["bundle"].exists()


def test_recover_prior_destroy_with_exact_bundle_is_read_only_and_idempotent(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-bundle",
        prior_operation_id="wsop-prior-destroy-bundle",
        prior_operation_kind="destroy",
    )
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker_before = marker_path.read_bytes()

    first = adapter._recover(payload)
    second = adapter._recover(payload)

    assert second == first
    assert first["receipt"] == fixture["receipt"]
    assert first["recovery"]["resolution"] == "ready"
    assert marker_path.read_bytes() == marker_before


def test_recover_prior_destroy_rolls_exact_stage_back_and_replays_deterministically(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-destroy-stage"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-stage",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )

    first = adapter._recover(payload)

    assert first["recovery"]["resolution"] == "ready"
    assert fixture["bundle"].is_dir()
    assert not exact_stage.exists()
    assert adapter._recover(payload) == first


@pytest.mark.parametrize("concurrent_target_kind", ["empty-directory", "file"])
def test_recover_no_replace_rename_preserves_concurrent_bundle_and_exact_stage(
    adapter_unit_workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    concurrent_target_kind: str,
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = f"wsop-prior-recover-race-{concurrent_target_kind}"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    stage_inode = exact_stage.stat(follow_symlinks=False).st_ino
    stage_marker = exact_stage / ".ai-collab-harness-binding.json"
    stage_marker_bytes = stage_marker.read_bytes()
    payload = _recover_payload(
        fixture,
        f"wsop-recover-race-{concurrent_target_kind}",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )
    original_rename = adapter._rename_directory_no_replace
    rename_entered = threading.Event()
    release_rename = threading.Event()

    def rename_after_concurrent_publish(source: Path, target: Path) -> None:
        rename_entered.set()
        assert release_rename.wait(timeout=3)
        original_rename(source, target)

    monkeypatch.setattr(
        adapter, "_rename_directory_no_replace", rename_after_concurrent_publish
    )
    outcomes: list[tuple[str, Any]] = []

    def execute() -> None:
        try:
            outcomes.append(("completed", adapter._recover(payload)))
        except adapter.AdapterError as exc:
            outcomes.append(("failed", exc))

    thread = threading.Thread(target=execute)
    thread.start()
    assert rename_entered.wait(timeout=3)
    concurrent_bytes = b"concurrent bundle must survive\n"
    if concurrent_target_kind == "empty-directory":
        fixture["bundle"].mkdir(mode=0o700)
    else:
        fixture["bundle"].write_bytes(concurrent_bytes)
    concurrent_inode = fixture["bundle"].stat(follow_symlinks=False).st_ino
    release_rename.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    assert len(outcomes) == 1
    kind, failure = outcomes[0]
    assert kind == "failed"
    assert failure.code == "workspace.recover-target-conflict"
    assert failure.mutation_state == "not_started"
    assert failure.retryable is False
    assert fixture["bundle"].stat(follow_symlinks=False).st_ino == concurrent_inode
    if concurrent_target_kind == "file":
        assert fixture["bundle"].read_bytes() == concurrent_bytes
    else:
        assert fixture["bundle"].is_dir()
        assert list(fixture["bundle"].iterdir()) == []
    assert exact_stage.stat(follow_symlinks=False).st_ino == stage_inode
    assert stage_marker.read_bytes() == stage_marker_bytes


def test_concurrent_exact_recover_stage_has_one_result_and_one_safe_failure(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-concurrent-recover"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-concurrent-stage",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )
    original_rename = adapter._rename_directory_no_replace
    barrier = threading.Barrier(2)

    def racing_rename(source: Path, target: Path) -> None:
        if Path(source) == exact_stage:
            barrier.wait(timeout=3)
        original_rename(source, target)

    monkeypatch.setattr(adapter, "_rename_directory_no_replace", racing_rename)
    outcomes: list[tuple[str, Any]] = []

    def execute() -> None:
        try:
            outcomes.append(("completed", adapter._recover(payload)))
        except adapter.AdapterError as exc:
            outcomes.append(("failed", exc))

    threads = [threading.Thread(target=execute) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    completed = [value for kind, value in outcomes if kind == "completed"]
    failures = [value for kind, value in outcomes if kind == "failed"]
    assert len(completed) == 1
    assert len(failures) == 1
    if failures[0].code == "workspace.recover-target-conflict":
        assert failures[0].mutation_state == "not_started"
        assert failures[0].retryable is False
    else:
        assert failures[0].code == "workspace.recover-outcome-unknown"
        assert failures[0].mutation_state == "unknown"
        assert failures[0].retryable is True
    monkeypatch.setattr(adapter, "_rename_directory_no_replace", original_rename)
    assert adapter._recover(payload) == completed[0]


def test_recover_force_destroy_stage_restores_degraded_wip_without_requiring_alignment(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    fixture["observation_state"]["state"] = "degraded"
    prior_operation_id = "wsop-prior-force-destroy-stage"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-force-destroy-stage",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
        force=True,
    )

    recovered = adapter._recover(payload)

    assert recovered["recovery"]["resolution"] == "ready"
    assert recovered["observation"]["state"] == "degraded"
    assert recovered["observation"]["wip_summary_digest"] == fixture["wip_digest"]
    assert fixture["bundle"].is_dir()
    assert not exact_stage.exists()
    assert adapter._recover(payload) == recovered


def test_recover_nonforce_destroy_stage_keeps_degraded_wip_staged(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    fixture["observation_state"]["state"] = "degraded"
    prior_operation_id = "wsop-prior-nonforce-destroy-stage"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-nonforce-destroy-stage",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert exact_stage.is_dir()
    assert not fixture["bundle"].exists()


def test_recover_prior_destroy_fsync_crash_is_unknown_then_exactly_replayable(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-destroy-fsync"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-fsync",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )
    original_fsync = adapter._fsync_directory

    def durable_then_fail(path: Path) -> None:
        original_fsync(path)
        raise OSError("injected post-rename fsync crash")

    monkeypatch.setattr(adapter, "_fsync_directory", durable_then_fail)
    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._recover(payload),
        "workspace.recover-outcome-unknown",
    )
    assert fixture["bundle"].is_dir()
    assert not exact_stage.exists()

    monkeypatch.setattr(adapter, "_fsync_directory", original_fsync)
    replay = adapter._recover(payload)
    assert replay["recovery"]["resolution"] == "ready"
    assert adapter._recover(payload) == replay


def test_recover_prior_destroy_post_rename_error_is_unknown_then_replayable(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-destroy-rename"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    os.replace(fixture["bundle"], exact_stage)
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-rename",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )
    original_rename = adapter._rename_directory_no_replace

    def rename_then_fail(source: Path, target: Path) -> None:
        original_rename(source, target)
        raise OSError("injected post-rename crash")

    monkeypatch.setattr(adapter, "_rename_directory_no_replace", rename_then_fail)
    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._recover(payload),
        "workspace.recover-outcome-unknown",
    )
    assert fixture["bundle"].is_dir()
    assert not exact_stage.exists()

    monkeypatch.setattr(adapter, "_rename_directory_no_replace", original_rename)
    replay = adapter._recover(payload)
    assert replay["recovery"]["resolution"] == "ready"


def test_recover_prior_destroy_missing_is_exact_and_idempotent(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    shutil.rmtree(fixture["bundle"])
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-missing",
        prior_operation_id="wsop-prior-destroy-missing",
        prior_operation_kind="destroy",
        force=True,
    )

    first = adapter._recover(payload)
    second = adapter._recover(payload)

    assert second == first
    assert first["receipt"] is None
    assert first["review_snapshot"] is None
    assert first["observation"]["state"] == "missing"
    assert first["observation"]["wip_summary_digest"] == fixture["wip_digest"]
    assert first["recovery"]["resolution"] == "missing"


@pytest.mark.parametrize("prior_kind", ["repair", "destroy"])
def test_recover_rejects_foreign_destroy_stage_without_touching_it(
    adapter_unit_workspace: dict[str, Any], prior_kind: str
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    foreign = fixture["parent"] / ".destroying-wsop-foreign-recover"
    foreign.mkdir(mode=0o700)
    sentinel = foreign / "foreign.txt"
    sentinel.write_text("foreign\n", encoding="utf-8")
    payload = _recover_payload(
        fixture,
        f"wsop-recover-{prior_kind}-foreign",
        prior_operation_id=f"wsop-prior-{prior_kind}-foreign",
        prior_operation_kind=prior_kind,
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert fixture["bundle"].is_dir()
    assert sentinel.read_text(encoding="utf-8") == "foreign\n"


@pytest.mark.parametrize("prior_kind", ["repair", "destroy"])
def test_recover_rejects_any_unowned_parent_inventory_entry(
    adapter_unit_workspace: dict[str, Any], prior_kind: str
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    extra = fixture["parent"] / "opaque-owner-inventory"
    extra.mkdir(mode=0o700)
    sentinel = extra / "preserve.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    payload = _recover_payload(
        fixture,
        f"wsop-recover-{prior_kind}-opaque-inventory",
        prior_operation_id=f"wsop-prior-{prior_kind}-opaque-inventory",
        prior_operation_kind=prior_kind,
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert fixture["bundle"].is_dir()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_recover_prior_destroy_rejects_bundle_and_exact_stage_conflict(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = "wsop-prior-destroy-conflict"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    exact_stage.mkdir(mode=0o700)
    exact_sentinel = exact_stage / "exact.txt"
    exact_sentinel.write_text("exact\n", encoding="utf-8")
    payload = _recover_payload(
        fixture,
        "wsop-recover-destroy-conflict",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert fixture["bundle"].is_dir()
    assert exact_sentinel.read_text(encoding="utf-8") == "exact\n"


@pytest.mark.parametrize("target_kind", ["partial", "symlink"])
def test_recover_prior_destroy_rejects_partial_or_symlink_stage_without_touching_it(
    adapter_unit_workspace: dict[str, Any], target_kind: str
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    prior_operation_id = f"wsop-prior-destroy-{target_kind}"
    exact_stage = fixture["parent"] / f".destroying-{prior_operation_id}"
    shutil.rmtree(fixture["bundle"])
    if target_kind == "partial":
        exact_stage.mkdir(mode=0o700)
        sentinel = exact_stage / "employee-wip.txt"
    else:
        outside = fixture["parent"].parent / "outside-recover"
        outside.mkdir(mode=0o700)
        exact_stage.symlink_to(outside, target_is_directory=True)
        sentinel = outside / "employee-wip.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    payload = _recover_payload(
        fixture,
        f"wsop-recover-destroy-{target_kind}",
        prior_operation_id=prior_operation_id,
        prior_operation_kind="destroy",
    )

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    if target_kind == "symlink":
        assert exact_stage.is_symlink()


@pytest.mark.parametrize(
    ("prior_kind", "force"),
    [("repair", True), ("status", False)],
)
def test_recover_rejects_invalid_prior_identity_without_mutation(
    adapter_unit_workspace: dict[str, Any], prior_kind: str, force: bool
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _recover_payload(
        fixture,
        "wsop-recover-invalid-prior",
        prior_operation_id="wsop-prior-invalid",
        prior_operation_kind=prior_kind,
        force=force,
    )
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker_before = marker_path.read_bytes()

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert marker_path.read_bytes() == marker_before


@pytest.mark.parametrize("prior_kind", ["repair", "destroy"])
def test_recover_rejects_simulated_unowned_container_without_mutation(
    adapter_unit_workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    prior_kind: str,
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _recover_payload(
        fixture,
        f"wsop-recover-{prior_kind}-unowned",
        prior_operation_id=f"wsop-prior-{prior_kind}-unowned",
        prior_operation_kind=prior_kind,
    )
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker_before = marker_path.read_bytes()
    actual_uid = os.getuid()
    monkeypatch.setattr(adapter.os, "getuid", lambda: actual_uid + 1)

    _assert_not_started_adapter_error(adapter, lambda: adapter._recover(payload))

    assert marker_path.read_bytes() == marker_before


def test_repair_exact_double_execution_replays_the_published_marker(
    adapter_unit_workspace: dict[str, Any],
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _repair_payload(fixture, "wsop-repair-double")

    first = adapter._repair(payload)
    second = adapter._repair(payload)

    assert second == first
    assert first["journal"]["operation_id"] == "wsop-repair-double"
    marker = json.loads(
        (fixture["bundle"] / ".ai-collab-harness-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["receipt"] == first["receipt"]


@pytest.mark.parametrize("force", [False, True])
def test_destroy_exact_double_execution_is_idempotent_and_preserves_force(
    adapter_unit_workspace: dict[str, Any], force: bool
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    if force:
        fixture["observation_state"]["state"] = "degraded"
    payload = _destroy_payload(
        fixture, "wsop-destroy-double", force=force
    )

    first = adapter._destroy(payload)
    second = adapter._destroy(payload)

    assert second == first
    assert first["journal"]["operation_id"] == "wsop-destroy-double"
    assert first["observation"]["state"] == "missing"
    assert not fixture["bundle"].exists()
    assert not (fixture["parent"] / ".destroying-wsop-destroy-double").exists()


def test_concurrent_exact_destroy_never_reports_a_failed_execution_as_no_effect(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _destroy_payload(fixture, "wsop-destroy-concurrent")
    original_replace = adapter.os.replace
    barrier = threading.Barrier(2)

    def racing_replace(source: Path, target: Path) -> None:
        if Path(source) == fixture["bundle"]:
            barrier.wait(timeout=3)
        original_replace(source, target)

    monkeypatch.setattr(adapter.os, "replace", racing_replace)
    outcomes: list[tuple[str, Any]] = []

    def execute() -> None:
        try:
            outcomes.append(("completed", adapter._destroy(payload)))
        except adapter.AdapterError as exc:
            outcomes.append(("failed", exc))

    threads = [threading.Thread(target=execute) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert [kind for kind, _ in outcomes].count("completed") == 1
    failures = [value for kind, value in outcomes if kind == "failed"]
    assert len(failures) == 1
    assert failures[0].mutation_state == "unknown"
    assert failures[0].code == "workspace.destroy-outcome-unknown"
    monkeypatch.setattr(adapter.os, "replace", original_replace)
    assert adapter._destroy(payload)["observation"]["state"] == "missing"


def test_repair_post_marker_fsync_failure_is_unknown_and_exactly_replayable(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _repair_payload(fixture, "wsop-repair-post-marker")
    original_write = adapter._write_binding_marker

    def publish_then_fail(path: Path, value: dict[str, Any]) -> None:
        original_write(path, value)
        raise OSError("injected post-marker fsync failure")

    monkeypatch.setattr(adapter, "_write_binding_marker", publish_then_fail)
    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._repair(payload),
        "workspace.repair-outcome-unknown",
    )
    marker = json.loads(
        (fixture["bundle"] / ".ai-collab-harness-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["journal"]["operation_id"] == "wsop-repair-post-marker"

    monkeypatch.setattr(adapter, "_write_binding_marker", original_write)
    replay = adapter._repair(payload)
    assert replay["journal"]["operation_id"] == "wsop-repair-post-marker"


def test_repair_replay_rejects_a_marker_changed_during_status_revalidation(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _repair_payload(fixture, "wsop-repair-replay-race")
    adapter._repair(payload)
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    original_status = adapter._status

    def status_then_change_marker(status_payload: dict[str, Any]) -> dict[str, Any]:
        result = original_status(status_payload)
        if status_payload["operation_id"].endswith("-replay-status"):
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["review_snapshot"]["snapshot_digest"] = "f" * 64
            marker_path.write_text(
                json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
            )
        return result

    monkeypatch.setattr(adapter, "_status", status_then_change_marker)
    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._repair(payload),
        "workspace.repair-outcome-unknown",
    )


def test_destroy_pre_rename_failure_is_not_started_only_after_exact_reproof(
    adapter_unit_workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _destroy_payload(fixture, "wsop-destroy-pre-effect")

    def refuse_replace(source: Path, target: Path) -> None:
        raise PermissionError("injected pre-rename refusal")

    monkeypatch.setattr(adapter.os, "replace", refuse_replace)
    with pytest.raises(adapter.AdapterError) as raised:
        adapter._destroy(payload)

    assert raised.value.code == "workspace.destroy-outcome-unknown"
    assert raised.value.mutation_state == "not_started"
    assert raised.value.retryable is True
    assert fixture["bundle"].is_dir()
    assert not (fixture["parent"] / ".destroying-wsop-destroy-pre-effect").exists()


@pytest.mark.parametrize("failure_point", ["rename", "cleanup"])
def test_destroy_directory_fsync_failure_is_unknown_and_exactly_replayable(
    adapter_unit_workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    payload = _destroy_payload(fixture, f"wsop-destroy-fsync-{failure_point}")
    original_fsync = adapter._fsync_directory
    call_count = 0

    def injected_fsync(path: Path) -> None:
        nonlocal call_count
        call_count += 1
        if (failure_point == "rename" and call_count == 1) or (
            failure_point == "cleanup" and call_count == 2
        ):
            raise OSError("injected directory fsync failure")
        original_fsync(path)

    monkeypatch.setattr(adapter, "_fsync_directory", injected_fsync)
    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._destroy(payload),
        "workspace.destroy-outcome-unknown",
    )
    monkeypatch.setattr(adapter, "_fsync_directory", original_fsync)
    replay = adapter._destroy(payload)
    assert replay["observation"]["state"] == "missing"


@pytest.mark.parametrize("operation", ["repair", "destroy"])
def test_high_risk_operations_reject_foreign_destroy_staging_without_touching_wip(
    adapter_unit_workspace: dict[str, Any], operation: str
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    foreign = fixture["parent"] / ".destroying-wsop-foreign"
    foreign.mkdir(mode=0o700)
    payload_file = foreign / "employee-wip.txt"
    payload_file.write_text("preserve\n", encoding="utf-8")

    if operation == "repair":
        call = lambda: adapter._repair(
            _repair_payload(fixture, "wsop-repair-foreign")
        )
        code = "workspace.repair-outcome-unknown"
    else:
        call = lambda: adapter._destroy(
            _destroy_payload(fixture, "wsop-destroy-foreign")
        )
        code = "workspace.destroy-outcome-unknown"
    _assert_unknown_adapter_error(adapter, call, code)

    assert fixture["bundle"].is_dir()
    assert payload_file.read_text(encoding="utf-8") == "preserve\n"


def test_destroy_exact_staging_and_bundle_conflict_is_unknown_and_preserved(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    exact = fixture["parent"] / ".destroying-wsop-destroy-conflict"
    exact.mkdir(mode=0o700)
    sentinel = exact / "preserve.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")

    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._destroy(
            _destroy_payload(fixture, "wsop-destroy-conflict")
        ),
        "workspace.destroy-outcome-unknown",
    )

    assert fixture["bundle"].is_dir()
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_force_destroy_does_not_bypass_binding_marker_provenance(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    fixture["observation_state"]["state"] = "degraded"
    marker_path = fixture["bundle"] / ".ai-collab-harness-binding.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["review_snapshot"]["snapshot_digest"] = "f" * 64
    marker_path.write_text(
        json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8"
    )

    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._destroy(
            _destroy_payload(
                fixture, "wsop-force-destroy-marker-drift", force=True
            )
        ),
        "workspace.destroy-outcome-unknown",
    )

    assert fixture["bundle"].is_dir()


def test_destroy_rejects_an_exact_staging_symlink_as_unknown(
    adapter_unit_workspace: dict[str, Any]
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    outside = fixture["parent"].parent / "outside-wip"
    outside.mkdir(mode=0o700)
    payload_file = outside / "preserve.txt"
    payload_file.write_text("preserve\n", encoding="utf-8")
    shutil.rmtree(fixture["bundle"])
    exact = fixture["parent"] / ".destroying-wsop-destroy-symlink"
    exact.symlink_to(outside, target_is_directory=True)

    _assert_unknown_adapter_error(
        adapter,
        lambda: adapter._destroy(
            _destroy_payload(fixture, "wsop-destroy-symlink")
        ),
        "workspace.destroy-outcome-unknown",
    )

    assert exact.is_symlink()
    assert payload_file.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize("operation", ["repair", "destroy"])
def test_high_risk_operations_reject_component_symlink_ownership(
    adapter_unit_workspace: dict[str, Any], operation: str
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    outside = fixture["parent"].parent / "outside-component"
    outside.mkdir(mode=0o700)
    shutil.rmtree(fixture["component"])
    fixture["component"].symlink_to(outside, target_is_directory=True)

    if operation == "repair":
        call = lambda: adapter._repair(
            _repair_payload(fixture, "wsop-repair-link")
        )
        expected_code = "workspace.repair-outcome-unknown"
    else:
        call = lambda: adapter._destroy(
            _destroy_payload(fixture, "wsop-destroy-link")
        )
        expected_code = "workspace.destroy-outcome-unknown"
    _assert_unknown_adapter_error(adapter, call, expected_code)
    assert fixture["component"].is_symlink()
    assert outside.is_dir()


@pytest.mark.parametrize("operation", ["repair", "destroy"])
def test_high_risk_operations_reject_simulated_uid_mismatch(
    adapter_unit_workspace: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    fixture = adapter_unit_workspace
    adapter = fixture["adapter"]
    actual_uid = os.getuid()
    monkeypatch.setattr(adapter.os, "getuid", lambda: actual_uid + 1)
    if operation == "repair":
        call = lambda: adapter._repair(
            _repair_payload(fixture, "wsop-repair-owner")
        )
        code = "workspace.repair-outcome-unknown"
    else:
        call = lambda: adapter._destroy(
            _destroy_payload(fixture, "wsop-destroy-owner")
        )
        code = "workspace.destroy-outcome-unknown"
    _assert_unknown_adapter_error(adapter, call, code)
    assert fixture["bundle"].is_dir()


def test_register_returns_declared_project_identity(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    index_snapshot = _stale_index_snapshot(project, project / "requirements.lock")
    code, reply, stderr = _call(
        project,
        "register",
        {"canonical_project_path": str(project)},
        extra_environment={"GIT_OPTIONAL_LOCKS": "1"},
    )
    assert code == 0, stderr
    _assert_index_unchanged(index_snapshot)
    assert reply is not None and reply["adapter_id"] == ADAPTER_ID
    observed = reply["result"]["project"]
    assert observed["project_key"] == "sampleproject"
    assert observed["workspace_adapter_id"] == "ai-collab-workspace-v1"
    assert observed["environment_adapter_id"] == "ai-collab-environment-v1"
    assert set(observed) == {
        "project_key",
        "project_binding_digest",
        "product_contract_version",
        "workspace_adapter_id",
        "environment_adapter_id",
        "participant_driver_contract",
        "collaboration_policy_schema",
        "repo_manifest_digest",
        "adapter_capability_digest",
    }


def test_register_serves_a_second_project_key_without_any_code_change(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path, project_key="otherproj")
    code, reply, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["project"]["project_key"] == "otherproj"


def test_register_migrates_a_legacy_descriptor_adapter_pin_in_memory(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    descriptor = project / "project_descriptor.yaml"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8").replace(
            "ai-collab-workspace-v1", "someone-elses-workspace-v1"
        ),
        encoding="utf-8",
    )
    code, reply, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["project"]["workspace_adapter_id"] == "ai-collab-workspace-v1"


def test_register_rejects_descriptor_manifest_project_key_mismatch(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    manifest = project / "repo_manifest.yaml"
    text = manifest.read_text(encoding="utf-8").replace(
        "project_key: sampleproject", "project_key: renamedproj", 1
    )
    manifest.write_text(text, encoding="utf-8")
    _assert_failed(
        _call(project, "register", {"canonical_project_path": str(project)}),
        "project.manifest-invalid",
    )


def test_adapter_rejects_a_request_for_another_adapter_id(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    _assert_failed(
        _call(
            project,
            "register",
            {"canonical_project_path": str(project)},
            adapter_id="ai-collab-edgestudio-bundle-v1",
        ),
        "adapter.rejected",
    )


def test_collaboration_templates_come_from_the_frozen_project_render(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    code, registered, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert registered is not None
    frozen_render = registered["result"]["render"]
    (project / "ai_collab_team_policies.json").unlink()

    code, reply, stderr = _call(
        project,
        "collaboration_templates",
        {},
        extra_environment={
            "AI_COLLAB_PROJECT_RENDER": json.dumps(frozen_render)
        },
    )
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["templates"] == [{"template_id": "team.solo"}]


@pytest.mark.slow
def test_plan_and_provision_clone_a_missing_declared_repo_from_remote(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    _remote, git_environment = _remote_only_helper(project, tmp_path)
    code, plan_reply, stderr = _call(
        project,
        "plan",
        {
            "operation_id": "wsop-remote-1",
            "scenario": {"scenario_id": "scenario-remote"},
            "scenario_state_revision": 1,
            "workspace_id": "workspace:remote",
            "requested_component_ids": [],
            "project_payload": {},
        },
        extra_environment=git_environment,
    )
    assert code == 0, stderr
    assert plan_reply is not None and plan_reply["outcome"] == "completed"
    plan = plan_reply["result"]["plan"]
    helper = next(
        item for item in plan["components"] if item["component_id"] == "helper-lib"
    )
    assert helper["materialization_mode"] == "workspace.remote-clone"

    staging = tmp_path / "scenario-remote" / "bundle"
    staging.parent.mkdir(mode=0o700)
    code, reply, stderr = _call(
        project,
        "provision",
        {
            "workspace_id": "workspace:remote",
            "staging_path": str(staging),
            "plan": plan,
            "descriptors": plan_reply["result"]["descriptors"],
        },
        extra_environment=git_environment,
    )
    assert code == 0, stderr
    assert reply is not None and reply["outcome"] == "completed"
    assert (staging / "helper-lib" / ".git").is_dir()
    assert _git_output(staging / "helper-lib", "rev-parse", "HEAD") == helper[
        "planned_revision"
    ]


def test_missing_repo_auth_failure_is_typed_and_actionable(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    _remote, git_environment = _remote_only_helper(project, tmp_path)
    ssh = tmp_path / "deny-ssh.py"
    ssh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '-G' in sys.argv: raise SystemExit(0)\n"
        "sys.stderr.write('Permission denied (publickey).\\n')\n"
        "raise SystemExit(255)\n",
        encoding="utf-8",
    )
    ssh.chmod(0o700)
    git_environment["GIT_SSH_COMMAND"] = str(ssh)
    error = _assert_failed(
        _call(
            project,
            "plan",
            {
                "operation_id": "wsop-auth-1",
                "scenario": {"scenario_id": "scenario-auth"},
                "scenario_state_revision": 1,
                "workspace_id": "workspace:auth",
                "requested_component_ids": [],
                "project_payload": {},
            },
            extra_environment=git_environment,
        ),
        "workspace.git-auth-required",
    )
    assert error["retryable"] is False
    assert "Sign in" in error["message"]


def test_plan_does_not_refresh_canonical_git_index(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    index_snapshot = _stale_index_snapshot(project, project / "requirements.lock")

    code, reply, stderr = _call(
        project,
        "plan",
        {
            "operation_id": "wsop-index-readonly-1",
            "scenario": {"scenario_id": "scenario-index-readonly"},
            "scenario_state_revision": 1,
            "workspace_id": "workspace:index-readonly",
            "requested_component_ids": [],
            "project_payload": {},
        },
        # Prove the adapter overrides, rather than mutates or trusts, ambient Git
        # configuration when it observes the employee's canonical checkout.
        extra_environment={"GIT_OPTIONAL_LOCKS": "1"},
    )

    assert code == 0, stderr
    assert reply is not None and reply["outcome"] == "completed"
    _assert_index_unchanged(index_snapshot)


def test_scan_era_wrong_branch_fails_typed_without_mutating_canonical_source(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    remote, git_environment = _remote_only_helper(project, tmp_path)
    manifest = project / "repo_manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            f"remote: {remote}\n    base_branch: main",
            f"remote: {remote}\n    base_branch: guessed-main",
        ),
        encoding="utf-8",
    )
    before = _git_output(project, "status", "--porcelain=v1", "--untracked-files=all")

    error = _assert_failed(
        _call(
            project,
            "plan",
            {
                "operation_id": "wsop-wrong-branch-1",
                "scenario": {"scenario_id": "scenario-wrong-branch"},
                "scenario_state_revision": 1,
                "workspace_id": "workspace:wrong-branch",
                "requested_component_ids": [],
                "project_payload": {},
            },
            extra_environment=git_environment,
        ),
        "workspace.branch-unavailable",
    )

    assert error["retryable"] is False
    assert error["mutation_state"] == "not_started"
    assert _git_output(
        project, "status", "--porcelain=v1", "--untracked-files=all"
    ) == before


def test_present_checkouts_use_local_head_on_arbitrary_and_detached_branches(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    helper = project.parent / "helper-lib"
    _git(project, "checkout", "-b", "employee/work")
    _git(helper, "checkout", "--detach")
    (project / "local-wip.txt").write_text("not committed\n", encoding="utf-8")

    code, reply, stderr = _call(
        project,
        "plan",
        {
            "operation_id": "wsop-local-head-1",
            "scenario": {"scenario_id": "scenario-local-head"},
            "scenario_state_revision": 1,
            "workspace_id": "workspace:local-head",
            "requested_component_ids": [],
            "project_payload": {},
        },
    )

    assert code == 0, stderr
    assert reply is not None and reply["outcome"] == "completed"
    components = {
        item["component_id"]: item for item in reply["result"]["plan"]["components"]
    }
    assert components["sampleproject"]["planned_revision"] == _git_output(
        project, "rev-parse", "HEAD"
    )
    assert components["helper-lib"]["planned_revision"] == _git_output(
        helper, "rev-parse", "HEAD"
    )


def test_shallow_checkout_failure_is_typed_and_actionable(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    shallow = Path(_git_output(project, "rev-parse", "--git-path", "shallow"))
    if not shallow.is_absolute():
        shallow = project / shallow
    shallow.write_text(_git_output(project, "rev-parse", "HEAD") + "\n", encoding="utf-8")
    index_snapshot = _stale_index_snapshot(project, project / "requirements.lock")

    error = _assert_failed(
        _call(
            project,
            "plan",
            {
                "operation_id": "wsop-shallow-1",
                "scenario": {"scenario_id": "scenario-shallow"},
                "scenario_state_revision": 1,
                "workspace_id": "workspace:shallow",
                "requested_component_ids": [],
                "project_payload": {},
            },
            extra_environment={"GIT_OPTIONAL_LOCKS": "1"},
        ),
        "workspace.shallow-source",
    )
    _assert_index_unchanged(index_snapshot)
    assert error["retryable"] is False
    assert "git fetch --unshallow" in error["message"]


@pytest.mark.parametrize(
    ("fixture", "expected_code"),
    [
        ("origin", "workspace.source-origin-mismatch"),
        ("partial", "workspace.partial-source"),
    ],
)
def test_unsupported_present_checkout_storage_is_typed(
    tmp_path: Path, fixture: str, expected_code: str
) -> None:
    project = _build_project(tmp_path)
    if fixture == "origin":
        _git(project, "remote", "set-url", "origin", "https://example.invalid/other.git")
    else:
        _git(project, "config", "extensions.partialClone", "origin")

    error = _assert_failed(
        _call(
            project,
            "plan",
            {
                "operation_id": f"wsop-{fixture}-1",
                "scenario": {"scenario_id": f"scenario-{fixture}"},
                "scenario_state_revision": 1,
                "workspace_id": f"workspace:{fixture}",
                "requested_component_ids": [],
                "project_payload": {},
            },
        ),
        expected_code,
    )
    assert error["mutation_state"] == "not_started"


@pytest.mark.slow
def test_plan_provision_status_destroy_full_cycle(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    code, plan_reply, stderr = _call(
        project,
        "plan",
        {
            "operation_id": "wsop-e2e-1",
            "scenario": {"scenario_id": "scenario-e2e"},
            "scenario_state_revision": 1,
            "workspace_id": "workspace:e2e",
            "requested_component_ids": [],
            "project_payload": {},
        },
    )
    assert code == 0, stderr
    assert plan_reply is not None
    plan = plan_reply["result"]["plan"]
    descriptors = plan_reply["result"]["descriptors"]
    assert plan["project_key"] == "sampleproject"
    assert [item["component_id"] for item in plan["components"]] == [
        "sampleproject",
        "helper-lib",
    ]
    # The declared dependency lock is digested from the project's own file.
    lock = project / "requirements.lock"
    import hashlib

    assert (
        plan["environment"]["dependency_lock_digest"]
        == hashlib.sha256(lock.read_bytes()).hexdigest()
    )

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(mode=0o700)
    staging = scenario_root / "bundle"
    code, provision_reply, stderr = _call(
        project,
        "provision",
        {
            "workspace_id": "workspace:e2e",
            "staging_path": str(staging),
            "plan": plan,
            "descriptors": descriptors,
        },
    )
    assert code == 0, stderr
    assert provision_reply is not None
    receipt = provision_reply["result"]["receipt"]
    journal = provision_reply["result"]["journal"]
    snapshot = provision_reply["result"]["review_snapshot"]

    # The receipt declares where participants launch: the provisioned
    # project root checkout, derived from the canonical directory name.
    assert receipt["participant_working_directory"] == f"bundle/{project.name}"

    # Workspace layout mirrors the canonical directory name, not any
    # hard-coded project name.
    assert (staging / project.name / "src" / "samplepkg" / "__init__.py").is_file()
    assert (staging / "helper-lib").is_dir()
    binding = list((staging / ".venv").rglob("ai_collab_scenario_sources.pth"))
    assert len(binding) == 1
    assert "src" in binding[0].read_text(encoding="utf-8")
    marker = json.loads(
        (staging / ".venv" / ".ai-collab-environment.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["source_bindings"] == [
        {"component_id": "sampleproject", "python_import_name": "samplepkg"}
    ]

    # The Host persists the binding marker after a successful provision; the
    # test stands in for it so status can prove full alignment.
    marker_path = staging / ".ai-collab-harness-binding.json"
    marker_path.write_text(
        json.dumps(
            {"journal": journal, "receipt": receipt, "review_snapshot": snapshot},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(marker_path, 0o600)

    code, status_reply, stderr = _call(
        project,
        "status",
        {
            "operation_id": "status-e2e-1",
            "bundle_path": str(staging),
            "plan": plan,
            "receipt": receipt,
        },
    )
    assert code == 0, stderr
    assert status_reply is not None
    observation = status_reply["result"]["observation"]
    assert observation["state"] == "aligned", observation["drift_codes"]
    assert all(not item["dirty"] for item in observation["components"])

    recover_payload = {
        "operation_id": "recover-e2e-repair-base",
        "bundle_path": str(staging),
        "plan": plan,
        "receipt": receipt,
        "expected_wip_summary_digest": observation["wip_summary_digest"],
        "prior_operation": {
            "operation_id": "repair-e2e-prior",
            "operation_kind": "repair",
            "force": False,
            "claim_digest": "a" * 64,
        },
    }
    code, recover_reply, stderr = _call(project, "recover", recover_payload)
    assert code == 0, stderr
    assert recover_reply is not None
    assert recover_reply["result"]["recovery"]["resolution"] == "ready"
    code, replay_reply, stderr = _call(project, "recover", recover_payload)
    assert code == 0, stderr
    assert replay_reply == recover_reply

    prior_destroy_id = "destroy-e2e-prior-stage"
    destroy_stage = scenario_root / f".destroying-{prior_destroy_id}"
    os.replace(staging, destroy_stage)
    destroy_recover_payload = {
        **recover_payload,
        "operation_id": "recover-e2e-destroy-stage",
        "prior_operation": {
            "operation_id": prior_destroy_id,
            "operation_kind": "destroy",
            "force": False,
            "claim_digest": "b" * 64,
        },
    }
    code, recover_reply, stderr = _call(
        project, "recover", destroy_recover_payload
    )
    assert code == 0, stderr
    assert recover_reply is not None
    assert recover_reply["result"]["recovery"]["resolution"] == "ready"
    assert staging.is_dir()
    assert not destroy_stage.exists()

    # Scenario-local edits surface as WIP, and the WIP fence then blocks a
    # destroy that presents the stale digest.
    (staging / project.name / "notes.txt").write_text("wip\n", encoding="utf-8")
    code, dirty_reply, stderr = _call(
        project,
        "status",
        {
            "operation_id": "status-e2e-2",
            "bundle_path": str(staging),
            "plan": plan,
            "receipt": receipt,
        },
    )
    assert code == 0, stderr
    assert dirty_reply is not None
    dirty_observation = dirty_reply["result"]["observation"]
    dirty_flags = {
        item["component_id"]: item["dirty"]
        for item in dirty_observation["components"]
    }
    assert dirty_flags["sampleproject"] is True
    _assert_failed(
        _call(
            project,
            "destroy",
            {
                "operation_id": "destroy-e2e-blocked",
                "bundle_path": str(staging),
                "plan": plan,
                "receipt": receipt,
                "expected_wip_summary_digest": observation["wip_summary_digest"],
                "force": False,
            },
        ),
        "adapter.rejected",
    )
    assert staging.is_dir()

    code, destroy_reply, stderr = _call(
        project,
        "destroy",
        {
            "operation_id": "destroy-e2e-1",
            "bundle_path": str(staging),
            "plan": plan,
            "receipt": receipt,
            "expected_wip_summary_digest": dirty_observation["wip_summary_digest"],
            "force": False,
        },
    )
    assert code == 0, stderr
    assert destroy_reply is not None
    assert destroy_reply["result"]["observation"]["state"] == "missing"
    assert not staging.exists()


@pytest.mark.slow
def test_environment_without_declared_bindings_is_a_plain_venv(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    manifest = project / "repo_manifest.yaml"
    text = manifest.read_text(encoding="utf-8")
    for line in (
        "    dependency_lock: requirements.lock\n",
        "    python_source_path: src\n",
        "    python_import_name: samplepkg\n",
    ):
        assert line in text
        text = text.replace(line, "")
    manifest.write_text(text, encoding="utf-8")

    code, plan_reply, stderr = _call(
        project,
        "plan",
        {
            "operation_id": "wsop-plain-1",
            "scenario": {"scenario_id": "scenario-plain"},
            "scenario_state_revision": 1,
            "workspace_id": "workspace:plain",
            "requested_component_ids": [],
            "project_payload": {},
        },
    )
    assert code == 0, stderr
    assert plan_reply is not None
    plan = plan_reply["result"]["plan"]
    assert plan["environment"]["dependency_lock_digest"] is None

    scenario_root = tmp_path / "scenario-plain"
    scenario_root.mkdir(mode=0o700)
    staging = scenario_root / "bundle"
    code, provision_reply, stderr = _call(
        project,
        "provision",
        {
            "workspace_id": "workspace:plain",
            "staging_path": str(staging),
            "plan": plan,
            "descriptors": plan_reply["result"]["descriptors"],
        },
    )
    assert code == 0, stderr
    assert provision_reply is not None
    marker = json.loads(
        (staging / ".venv" / ".ai-collab-environment.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["source_bindings"] == []
    assert not list((staging / ".venv").rglob("ai_collab_scenario_sources.pth"))


def test_bootstrap_drafts_a_registrable_project_from_a_bare_git_directory(
    tmp_path: Path,
) -> None:
    """The cold-start path: a directory with nothing but Git repositories
    becomes a registrable project in one bootstrap call."""
    bare = tmp_path / "canonical" / "freshproject"
    nested = bare / "helper-lib"
    bare.mkdir(parents=True)
    (bare / "README.md").write_text("hello\n", encoding="utf-8")
    _init_repo(bare, "git@github.com:example/freshproject.git")
    (nested / "lib.txt").parent.mkdir()
    (nested / "lib.txt").write_text("lib\n", encoding="utf-8")
    _init_repo(nested, "https://github.com/example/helper-lib.git")
    project = bare.resolve()

    code, reply, stderr = _call(
        project, "bootstrap", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    outcome = reply["result"]["bootstrap"]
    assert outcome["already_configured"] is False
    assert outcome["project_key"] == "freshproject"
    assert outcome["created"] == []
    assert not (project / "project_descriptor.yaml").exists()
    assert not (project / "repo_manifest.yaml").exists()
    assert not (project / "gates.yaml").exists()
    assert not (project / "ai_collab_team_policies.json").exists()

    # Fileless registration accepts the observed project without canonical
    # source mutation.
    code, reply, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    observed = reply["result"]["project"]
    assert observed["project_key"] == "freshproject"

    assert any(
        row["repo_key"] == "helper-lib"
        for row in reply["result"]["render"]["repo_manifest"]["repos"]
    )

    # A second bootstrap remains a side-effect-free proposal.
    code, reply, stderr = _call(
        project, "bootstrap", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["bootstrap"]["already_configured"] is False
    assert reply["result"]["bootstrap"]["created"] == []


def test_bootstrap_leaves_no_residue_when_the_draft_cannot_validate(
    tmp_path: Path,
) -> None:
    bare = tmp_path / "canonical" / "no-remote"
    bare.mkdir(parents=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(bare)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # No origin remote: fileless registration still succeeds and exposes the
    # missing remote as availability instead of failing registration.
    project = bare.resolve()
    code, reply, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["render"]["availability"]["status"] == "attention"
    _assert_failed(
        _call(project, "bootstrap", {"canonical_project_path": str(project)}),
        "project.intent-proposal-incomplete",
    )
    assert not (project / "project_descriptor.yaml").exists()
    assert not (project / "repo_manifest.yaml").exists()
    assert not (project / "gates.yaml").exists()
    assert not (project / "ai_collab_team_policies.json").exists()
