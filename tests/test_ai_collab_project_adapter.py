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
import os
import subprocess
import sys
from pathlib import Path
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
        env={**os.environ, "AI_COLLAB_PROJECT_ROOT": str(project_root)},
    )
    reply = None
    if completed.returncode == 0:
        reply = json.loads(completed.stdout)
    return completed.returncode, reply, completed.stderr


def test_register_returns_declared_project_identity(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    code, reply, stderr = _call(
        project, "register", {"canonical_project_path": str(project)}
    )
    assert code == 0, stderr
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


def test_register_rejects_descriptor_naming_a_different_adapter(
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
    code, _, _ = _call(project, "register", {"canonical_project_path": str(project)})
    assert code == 1


def test_register_rejects_descriptor_manifest_project_key_mismatch(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)
    manifest = project / "repo_manifest.yaml"
    text = manifest.read_text(encoding="utf-8").replace(
        "project_key: sampleproject", "project_key: renamedproj", 1
    )
    manifest.write_text(text, encoding="utf-8")
    code, _, _ = _call(project, "register", {"canonical_project_path": str(project)})
    assert code == 1


def test_adapter_rejects_a_request_for_another_adapter_id(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    code, _, _ = _call(
        project,
        "register",
        {"canonical_project_path": str(project)},
        adapter_id="ai-collab-edgestudio-bundle-v1",
    )
    assert code == 1


def test_collaboration_templates_come_from_the_project_root(tmp_path: Path) -> None:
    project = _build_project(tmp_path)
    code, reply, stderr = _call(project, "collaboration_templates", {})
    assert code == 0, stderr
    assert reply is not None
    assert reply["result"]["templates"] == [{"template_id": "team.solo"}]


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
    code, _, _ = _call(
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
    )
    assert code == 1
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
