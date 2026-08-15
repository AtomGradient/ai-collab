# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import copy
import json
import shutil
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from ai_collab.protocol import canonical_json_sha256
from ai_collab.client import HarnessClient, HarnessClientError
from ai_collab.host import HarnessHost
from ai_collab.security import SecurityCoordinator
from ai_collab.store import ScenarioStore
from ai_collab.workspace import (
    ADAPTER_ENVIRONMENT_KEYS,
    ProjectAdapterCommand,
    WorkspaceCoordinator,
    WorkspaceError,
)


def test_adapter_environment_allows_standard_proxy_without_vendor_identity() -> None:
    assert {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    }.issubset(ADAPTER_ENVIRONMENT_KEYS)
    assert "PYTHONDONTWRITEBYTECODE" in ADAPTER_ENVIRONMENT_KEYS
    assert "CLAUDE_CODE_SESSION_ID" not in ADAPTER_ENVIRONMENT_KEYS
    assert "CODEX_THREAD_ID" not in ADAPTER_ENVIRONMENT_KEYS


def test_external_workspace_root_preserves_legacy_bindings_and_hosts_new_ones(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    legacy_store = ScenarioStore(state_root)
    _, legacy_result = legacy_store.create_scenario(
        request_id="legacy-create",
        request_digest="a" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="legacy",
        project_binding_digest="b" * 64,
    )
    legacy_binding = legacy_result["scenario"]["workspace_binding_id"]
    legacy_path = state_root / "workspaces" / legacy_binding
    assert legacy_path.is_dir()

    external_root = tmp_path / "Documents" / "Scenarios"
    external_root.parent.mkdir()
    store = ScenarioStore(state_root, workspace_root=external_root)
    _, resolved_legacy_path = store.scenario_workspace("project", "legacy")
    assert resolved_legacy_path == legacy_path

    _, new_result = store.create_scenario(
        request_id="new-create",
        request_digest="c" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="new",
        project_binding_digest="b" * 64,
    )
    new_binding = new_result["scenario"]["workspace_binding_id"]
    assert (external_root / new_binding).is_dir()
    assert not (state_root / "workspaces" / new_binding).exists()


class FakeAdapter:
    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "plan":
            plan = {
                "operation_id": payload["operation_id"],
                "scenario": payload["scenario"],
                "project_descriptor_digest": "b" * 64,
                "components": [],
            }
            return {"descriptors": [], "plan": plan}
        if operation == "provision":
            staging = Path(payload["staging_path"])
            staging.mkdir(mode=0o700)
            (staging / "component.txt").write_text("ready\n", encoding="utf-8")
            plan = payload["plan"]
            journal = {
                "operation_id": plan["operation_id"],
                "plan_digest": canonical_json_sha256(plan),
            }
            receipt = {
                "operation_id": plan["operation_id"],
                "plan_digest": canonical_json_sha256(plan),
                "journal_digest": canonical_json_sha256(journal),
                "workspace_id": payload["workspace_id"],
                "workspace_binding_digest": canonical_json_sha256(
                    {"workspace_id": payload["workspace_id"]}
                ),
                "scenario": plan["scenario"],
                "state": "ready",
                "residual_owned_resources": 0,
            }
            return {
                "journal": journal,
                "receipt": receipt,
                "review_snapshot": {"snapshot_contract_version": 1},
            }
        if operation == "status":
            plan = payload["plan"]
            receipt = payload["receipt"]
            journal = {
                "operation_id": payload["operation_id"],
                "plan_digest": canonical_json_sha256(plan),
            }
            return {
                "journal": journal,
                "observation": {
                    "operation_id": payload["operation_id"],
                    "receipt_digest": canonical_json_sha256(receipt),
                    "journal_digest": canonical_json_sha256(journal),
                    "state": "aligned",
                    "drift_codes": [],
                    "wip_summary_digest": canonical_json_sha256({"wip": "stable"}),
                },
            }
        if operation == "repair":
            base = payload["receipt"]
            journal = {
                "operation_id": payload["operation_id"],
                "operation_kind": "repair",
                "plan_digest": canonical_json_sha256(payload["plan"]),
            }
            receipt = {
                **base,
                "operation_id": payload["operation_id"],
                "base_receipt_digest": canonical_json_sha256(base),
                "journal_digest": canonical_json_sha256(journal),
            }
            return {
                "journal": journal,
                "receipt": receipt,
                "observation": {
                    "operation_id": payload["operation_id"],
                    "operation_kind": "repair",
                    "journal_digest": canonical_json_sha256(journal),
                    "receipt_digest": canonical_json_sha256(receipt),
                    "state": "aligned",
                    "drift_codes": [],
                    "wip_summary_digest": canonical_json_sha256({"wip": "stable"}),
                },
                "review_snapshot": {"snapshot_contract_version": 1},
            }
        if operation == "destroy":
            bundle = Path(payload["bundle_path"])
            if bundle.exists():
                shutil.rmtree(bundle)
            journal = {
                "operation_id": payload["operation_id"],
                "operation_kind": "destroy",
                "plan_digest": canonical_json_sha256(payload["plan"]),
            }
            return {
                "journal": journal,
                "observation": {
                    "operation_id": payload["operation_id"],
                    "operation_kind": "destroy",
                    "journal_digest": canonical_json_sha256(journal),
                    "receipt_digest": canonical_json_sha256(payload["receipt"]),
                    "state": "missing",
                    "drift_codes": ["workspace.destroyed"],
                    "wip_summary_digest": canonical_json_sha256({"wip": "stable"}),
                },
            }
        raise AssertionError(operation)


class FakeSecurityAdapter:
    def __init__(self) -> None:
        self.present_calls = 0

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        del timeout_seconds
        if operation == "observe":
            return {
                "observations": [
                    {
                        "permission_id": permission_id,
                        "subject_digest": "a" * 64,
                        "status": "granted",
                        "observed_at_epoch_ms": payload["captured_at_epoch_ms"],
                        "valid_until_epoch_ms": payload["captured_at_epoch_ms"]
                        + 2_000,
                        "evidence_digest": "b" * 64,
                        "provider_error_code": None,
                        "remediation_ref": None,
                    }
                    for permission_id in payload["permission_ids"]
                ]
            }
        challenge = payload["challenge"]
        self.present_calls += 1
        return {
            "challenge_digest": canonical_json_sha256(challenge),
            "outcome": "approved",
            "decided_at_epoch_ms": challenge["issued_at_epoch_ms"],
            "presenter_instance_digest": "c" * 64,
            "decision_evidence_digest": "d" * 64,
            "reason_code": None,
        }


class EmptyForceCloseCoordinator:
    def force_close_scenario_participants(
        self, executions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        assert executions == []
        return []


@contextmanager
def running_high_risk_host(
    state_root: Path,
) -> Iterator[tuple[HarnessHost, HarnessClient]]:
    with tempfile.TemporaryDirectory(prefix="harness-high-risk-") as runtime:
        socket_path = Path(runtime) / "host.sock"
        host = HarnessHost(state_root, socket_path)
        host.projects.validate_binding = lambda _project, _digest: None  # type: ignore[method-assign]
        host.workspace = WorkspaceCoordinator(state_root, FakeAdapter())  # type: ignore[arg-type]
        host.security = SecurityCoordinator(state_root, FakeSecurityAdapter())  # type: ignore[arg-type]
        errors: list[BaseException] = []

        def run() -> None:
            try:
                host.serve_forever()
            except BaseException as exc:  # pragma: no cover - fixture surfaces it
                errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while not socket_path.exists() and not errors and time.monotonic() < deadline:
            time.sleep(0.01)
        if errors:
            raise errors[0]
        try:
            yield host, HarnessClient(state_root, socket_path)
        finally:
            host.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert not errors


def _coordinator(tmp_path: Path) -> tuple[ScenarioStore, WorkspaceCoordinator, dict[str, Any], Path]:
    store = ScenarioStore(tmp_path / "state")
    _, created = store.create_scenario(
        request_id="create",
        request_digest="a" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="scenario",
        project_binding_digest="b" * 64,
    )
    record, workspace_path = store.scenario_workspace("project", "scenario")
    coordinator = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    return store, coordinator, record, workspace_path


def test_coordinator_publishes_replays_and_observes(tmp_path: Path) -> None:
    _, coordinator, record, workspace_path = _coordinator(tmp_path)
    operation_id, planned = coordinator.plan(
        request_id="plan-request",
        request_digest="c" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    )
    assert planned["workspace"]["state"] == "planned"
    assert coordinator.plan(
        request_id="plan-request",
        request_digest="c" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    ) == (operation_id, planned)

    plan_digest = planned["workspace"]["plan_digest"]
    provision_operation, ready = coordinator.provision(
        request_id="provision-request",
        request_digest="d" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=plan_digest,
        workspace_path=workspace_path,
    )
    assert provision_operation == operation_id
    assert ready["workspace"]["state"] == "ready"
    assert (workspace_path / "bundle" / "component.txt").read_text() == "ready\n"
    assert not any(path.name.startswith(".stage-") for path in workspace_path.iterdir())
    assert coordinator.is_ready("project", "scenario")

    receipt = ready["workspace"]["receipt"]
    _, observed = coordinator.status(
        request_id="status-request",
        request_digest="e" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        # The Host fences this request against the live Scenario revision.  A
        # workspace planned while closed must remain observable after open.
        scenario_state_revision=record["state_revision"] + 1,
        receipt_digest=canonical_json_sha256(receipt),
        workspace_path=workspace_path,
    )
    assert observed["workspace"]["state"] == "aligned"
    assert stat.S_IMODE(coordinator.state_path.stat().st_mode) == 0o600


def test_adapter_command_rejects_absolute_public_path(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'completed','result':{'leak':'/private/value'}},sys.stdout)\n",
        encoding="utf-8",
    )
    config = tmp_path / "adapter.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_id": "test-adapter",
                "command": ["python3", "adapter.py"],
                "working_directory": ".",
            }
        ),
        encoding="utf-8",
    )
    adapter = ProjectAdapterCommand(config)
    with pytest.raises(WorkspaceError) as exc:
        adapter.call("plan", {})
    assert exc.value.code == "adapter.private-data-leak"


def test_adapter_command_receives_project_root_only_in_private_environment(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "selected-project"
    project_root.mkdir()
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,os,sys\n"
        "request=json.load(sys.stdin)\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'completed','result':{'root_matches':"
        f"os.environ.get('AI_COLLAB_PROJECT_ROOT')=={str(project_root)!r}}}}},sys.stdout)\n",
        encoding="utf-8",
    )
    config = tmp_path / "adapter.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_id": "test-adapter",
                "command": ["python3", "adapter.py"],
                "working_directory": ".",
            }
        ),
        encoding="utf-8",
    )
    adapter = ProjectAdapterCommand(config)
    result = adapter.call("register", {}, project_root=project_root)
    assert result == {"root_matches": True}


def test_restart_reconciles_publish_before_final_state_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="plan-unknown",
        request_digest="1" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    )
    write_state = coordinator._write_state  # noqa: SLF001 - exact crash window injection

    def fail_final_ready(value: Mapping[str, Any]) -> None:
        if any(item["state"] == "ready" for item in value["bindings"].values()):
            raise OSError("injected final state failure")
        write_state(value)

    monkeypatch.setattr(coordinator, "_write_state", fail_final_ready)
    with pytest.raises(WorkspaceError) as exc:
        coordinator.provision(
            request_id="provision-unknown",
            request_digest="2" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            scenario_state_revision=record["state_revision"],
            plan_digest=planned["workspace"]["plan_digest"],
            workspace_path=workspace_path,
        )
    assert exc.value.code == "workspace.publish-outcome-unknown"
    assert exc.value.mutation_state == "unknown"
    assert (workspace_path / "bundle").is_dir()

    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    _, replay = recovered.provision(
        request_id="provision-unknown",
        request_digest="2" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    assert replay["workspace"]["state"] == "ready"
    assert recovered.is_ready("project", "scenario")


def test_mutable_exception_types_preserve_traceback_assignment() -> None:
    error = WorkspaceError("workspace.test", "test")
    error.__traceback__ = None
    assert error.__traceback__ is None


def test_high_risk_workspace_context_repair_and_destroy_preserve_audit(
    tmp_path: Path,
) -> None:
    _, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="high-risk-plan",
        request_digest="6" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    )
    coordinator.provision(
        request_id="high-risk-provision",
        request_digest="7" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    preview, subject = coordinator.high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        operation="scenario.repair",
    )
    assert preview["state"] == "aligned"
    assert preview["canonical_source_wip_mutation"] is False
    assert "bundle_path" not in json.dumps(preview)
    assert subject["bundle_path"] == str(workspace_path / "bundle")

    _, repaired = coordinator.repair(
        request_id="high-risk-repair",
        request_digest="8" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        expected_wip_summary_digest=preview["wip_summary_digest"],
    )
    assert repaired["workspace"]["state"] == "ready"

    destroy_preview, _ = coordinator.high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        operation="scenario.destroy",
    )
    _, destroyed = coordinator.destroy(
        request_id="high-risk-destroy",
        request_digest="9" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        expected_wip_summary_digest=destroy_preview["wip_summary_digest"],
    )
    assert destroyed["workspace"]["state"] == "missing"
    assert not (workspace_path / "bundle").exists()
    durable = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    assert durable["bindings"] == {}
    assert len(durable["history"]) == 1


def _provision_host_workspace(client: HarnessClient) -> tuple[dict[str, Any], Path]:
    created = client.create_scenario(
        project_instance_id="project",
        scenario_id="scenario",
        project_binding_digest="b" * 64,
        request_id="host-create",
    )["scenario"]
    planned = client.plan_workspace(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=created["scenario_generation"],
        scenario_state_revision=created["state_revision"],
        requested_component_ids=[],
        project_payload={},
        request_id="host-plan",
    )["workspace"]
    client.provision_workspace(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=created["scenario_generation"],
        scenario_state_revision=created["state_revision"],
        plan_digest=planned["plan_digest"],
        request_id="host-provision",
    )
    return created, Path()


def test_host_repair_is_conservative_and_destroy_unregisters_with_audit(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        workspace_path = state_root / "workspaces" / created["workspace_binding_id"]
        local_wip = workspace_path / "bundle" / "local-wip.txt"
        local_wip.write_text("preserve during repair\n", encoding="utf-8")

        with host.store._lock:  # noqa: SLF001 - durable fault fixture
            durable = host.store._read_state()  # noqa: SLF001
            item = next(iter(durable["scenarios"].values()))
            record = item["record"]
            record["observed_state"] = "degraded"
            record["degraded"] = {
                "reason": "cleanup_pending",
                "cleanup_pending": True,
                "owned_resource_evidence_sha256": "e" * 64,
                "repair_action": "scenario.repair",
            }
            record["state_revision"] += 1
            durable["state_revision"] += 1
            host.store._write_state(durable)  # noqa: SLF001
            degraded = dict(record)

        repaired = client.repair_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
            request_id="host-repair",
        )["scenario"]
        assert repaired["observed_state"] == "closed"
        assert repaired["degraded"] is None
        assert local_wip.read_text(encoding="utf-8") == "preserve during repair\n"

        preview = client.preview_destroy_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
        )["effect_preview"]
        assert preview["eligible"] is True
        assert preview["canonical_wip_mutation"] is False
        destroyed = client.destroy_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
            request_id="host-destroy",
        )
        assert destroyed["unregistered"] is True
        assert not (workspace_path / "bundle").exists()
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }
        with pytest.raises(HarnessClientError) as caught:
            client.scenario_status(
                project_instance_id="project", scenario_id="scenario"
            )
        assert caught.value.code == "target.scenario-not-found"

        host_state = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        workspace_state = json.loads(
            (state_root / "workspace-execution.json").read_text(encoding="utf-8")
        )
        assert host_state["scenarios"] == {}
        assert len(host_state["scenario_history"]) == 1
        assert workspace_state["bindings"] == {}
        assert len(workspace_state["history"]) == 1


def test_force_destroy_closes_running_scenario_with_one_authorization(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        opened = client.open_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-destroy-open",
        )["scenario"]
        host.participants = EmptyForceCloseCoordinator()  # type: ignore[assignment]
        assert host.security is not None
        adapter = host.security.adapter

        result = client.force_destroy_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="force-destroy-running",
        )

        assert result["unregistered"] is True
        assert result["scenario"]["desired_state"] == "destroyed"
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }
        assert adapter.present_calls == 1  # type: ignore[attr-defined]
        durable = json.loads((state_root / "host-state.json").read_text())
        operation_kinds = {
            value["operation_kind"] for value in durable["operations"].values()
        }
        assert "scenario.force-close" in operation_kinds
        assert "scenario.force-destroy" in operation_kinds


def test_restart_joins_completed_workspace_repair_and_destroy_outcomes(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        workspace_path = state_root / "workspaces" / created["workspace_binding_id"]
        local_wip = workspace_path / "bundle" / "crash-window-wip.txt"
        local_wip.write_text("survives repair and destroy\n", encoding="utf-8")
        with host.store._lock:  # noqa: SLF001 - durable crash fixture
            durable = host.store._read_state()  # noqa: SLF001
            record = next(iter(durable["scenarios"].values()))["record"]
            record["observed_state"] = "degraded"
            record["degraded"] = {
                "reason": "cleanup_pending",
                "cleanup_pending": True,
                "owned_resource_evidence_sha256": "e" * 64,
                "repair_action": "scenario.repair",
            }
            record["state_revision"] += 1
            durable["state_revision"] += 1
            host.store._write_state(durable)  # noqa: SLF001
            degraded = copy.deepcopy(record)
        preview, _ = host.workspace.high_risk_context(  # type: ignore[union-attr]
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.repair",
        )
        request_digest = "8" * 64
        operation_id, replay, pending_path = host.store.begin_scenario_repair(
            request_id="repair-crash-window",
            request_digest=request_digest,
            host_generation=host.host_generation,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
        )
        assert replay is None
        assert pending_path == workspace_path
        host.workspace.repair(  # type: ignore[union-attr]
            request_id="repair-crash-window",
            request_digest=request_digest,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=preview["wip_summary_digest"],
        )
        pending = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert pending["operations"][operation_id]["state"] == "executing_external"

    with running_high_risk_host(state_root) as (host, client):
        repaired = client.scenario_status(
            project_instance_id="project", scenario_id="scenario"
        )["scenario"]
        assert repaired["observed_state"] == "closed"
        assert local_wip.read_text(encoding="utf-8") == (
            "survives repair and destroy\n"
        )
        destroy_preview, _ = host.workspace.high_risk_context(  # type: ignore[union-attr]
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.destroy",
        )
        request_digest = "9" * 64
        operation_id, replay, pending_path = host.store.begin_scenario_destroy(
            request_id="destroy-crash-window",
            request_digest=request_digest,
            host_generation=host.host_generation,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
        )
        assert replay is None
        assert pending_path == workspace_path
        host.workspace.destroy(  # type: ignore[union-attr]
            request_id="destroy-crash-window",
            request_digest=request_digest,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=destroy_preview["wip_summary_digest"],
        )
        assert not (workspace_path / "bundle").exists()
        pending = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert pending["operations"][operation_id]["state"] == "executing_external"

    with running_high_risk_host(state_root) as (_, client):
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }
        host_state = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        workspace_state = json.loads(
            (state_root / "workspace-execution.json").read_text(encoding="utf-8")
        )
        assert host_state["scenarios"] == {}
        assert len(host_state["scenario_history"]) == 1
        assert workspace_state["bindings"] == {}
        assert len(workspace_state["history"]) == 1


def test_workspace_restart_replays_external_repair_and_destroy_before_commit(
    tmp_path: Path,
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="recovery-plan",
        request_digest="1" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    )
    coordinator.provision(
        request_id="recovery-provision",
        request_digest="2" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    local_wip = workspace_path / "bundle" / "crash-window-wip.txt"
    local_wip.write_text("must survive repair\n", encoding="utf-8")
    preview, _ = coordinator.high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        operation="scenario.repair",
    )

    original_write = coordinator._write_state  # noqa: SLF001

    def crash_before_repair_commit(value: Mapping[str, Any]) -> None:
        request = value["requests"].get("recovery-repair")
        if request is not None and request["status"] == "completed":
            raise SystemExit("crash after repair external action")
        original_write(value)

    coordinator._write_state = crash_before_repair_commit  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(SystemExit, match="after repair external action"):
        coordinator.repair(
            request_id="recovery-repair",
            request_digest="3" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=preview["wip_summary_digest"],
        )
    durable = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    assert next(iter(durable["bindings"].values()))["state"] == "repairing"

    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    repair_replay = recovered.completed_request("recovery-repair", "3" * 64)
    assert repair_replay is not None
    assert repair_replay[1]["workspace"]["state"] == "ready"
    assert local_wip.read_text(encoding="utf-8") == "must survive repair\n"

    destroy_preview, _ = recovered.high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        operation="scenario.destroy",
    )
    recovered_write = recovered._write_state  # noqa: SLF001

    def crash_before_destroy_commit(value: Mapping[str, Any]) -> None:
        request = value["requests"].get("recovery-destroy")
        if request is not None and request["status"] == "completed":
            raise SystemExit("crash after destroy external action")
        recovered_write(value)

    recovered._write_state = crash_before_destroy_commit  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(SystemExit, match="after destroy external action"):
        recovered.destroy(
            request_id="recovery-destroy",
            request_digest="4" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=destroy_preview["wip_summary_digest"],
        )
    assert not (workspace_path / "bundle").exists()
    durable = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    assert next(iter(durable["bindings"].values()))["state"] == "destroying"

    restarted = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    restarted.start_host(store.workspace_root)
    destroy_replay = restarted.completed_request("recovery-destroy", "4" * 64)
    assert destroy_replay is not None
    assert destroy_replay[1]["workspace"]["state"] == "missing"
    durable = json.loads(restarted.state_path.read_text(encoding="utf-8"))
    assert durable["bindings"] == {}
    assert len(durable["history"]) == 1


def test_workspace_restart_fails_closed_when_pending_high_risk_fence_differs(
    tmp_path: Path,
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="mismatch-plan",
        request_digest="5" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest="b" * 64,
        requested_component_ids=[],
        project_payload={},
    )
    coordinator.provision(
        request_id="mismatch-provision",
        request_digest="6" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    with coordinator._lock:  # noqa: SLF001 - exact corrupt crash fixture
        durable = coordinator._read_state()  # noqa: SLF001
        binding = next(iter(durable["bindings"].values()))
        binding["state"] = "repairing"
        binding["pending_request_id"] = "mismatch-repair"
        binding["pending_operation_id"] = "wsop-mismatch"
        binding["pending_expected_wip_summary_digest"] = "0" * 64
        durable["requests"]["mismatch-repair"] = {
            "request_digest": "7" * 64,
            "operation_id": "wsop-mismatch",
            "status": "pending",
            "result": None,
        }
        durable["state_revision"] += 1
        coordinator._write_state(durable)  # noqa: SLF001

    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    durable = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    binding = next(iter(durable["bindings"].values()))
    assert binding["state"] == "repair_failed"
    assert binding["error_code"] == "workspace.repair-outcome-unknown"
    assert durable["requests"]["mismatch-repair"]["status"] == "failed"
    assert recovered.completed_request("mismatch-repair", "7" * 64) is None
