# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import json
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import pytest

from ai_collab import cli as cli_main
from ai_collab.client import HarnessClient, HarnessClientError
from ai_collab.host import HarnessHost
from ai_collab.protocol import canonical_json_sha256
from ai_collab.security import SecurityCoordinator
from ai_collab.workspace import WorkspaceCoordinator


PROJECT_ID = "edgestudio-local"
SCENARIO_ID = "m1-happy-path"
_BUILTIN_COLLABORATION_REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "ai_collab_team_policies.json").read_text(
        encoding="utf-8"
    )
)
PROJECT_RENDER = {
    "render_contract_version": 1,
    "source": {"kind": "fileless", "intent_schema_version": None, "source_digest": "1" * 64},
    "project": {
        "project_key": PROJECT_ID,
        "product_contract_version": "1.0",
        "workspace_adapter_id": "workspace.test-v1",
        "environment_adapter_id": "environment.test-v1",
        "participant_driver_contract": 2,
        "collaboration_policy_schema": 1,
    },
    "repo_manifest": {"schema_version": 1, "project_key": PROJECT_ID, "repos": []},
    "repo_manifest_digest": "2" * 64,
    "gate": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
    "collaboration": {
        "kind": "builtin",
        "profile_id": "builtin.standard-v1",
        "registry_snapshot": _BUILTIN_COLLABORATION_REGISTRY,
        "registry_snapshot_digest": canonical_json_sha256(
            _BUILTIN_COLLABORATION_REGISTRY
        ),
    },
    "availability": {"status": "ready", "observations": [], "changes": [], "warnings": []},
}
PROJECT_RENDER["availability"]["fingerprint"] = canonical_json_sha256(  # type: ignore[index]
    PROJECT_RENDER["availability"]
)
PROJECT_RENDER["render_digest"] = canonical_json_sha256(
    {key: value for key, value in PROJECT_RENDER.items() if key != "availability"}
)
PROJECT_DIGEST = PROJECT_RENDER["render_digest"]


@contextmanager
def running_host(
    state_root: Path,
    *,
    configure: Callable[[HarnessHost], None] | None = None,
) -> Iterator[tuple[HarnessHost, HarnessClient]]:
    with tempfile.TemporaryDirectory(prefix="ai-collab-m1-") as runtime_directory:
        socket_path = Path(runtime_directory) / "host.sock"
        host = HarnessHost(state_root, socket_path)
        host.projects.validate_binding = lambda _project, _digest: None  # type: ignore[method-assign]
        host.projects.resolved_render = (  # type: ignore[method-assign]
            lambda _project, digest=None: PROJECT_RENDER
            if digest in {None, PROJECT_DIGEST}
            else None
        )
        if configure is not None:
            configure(host)
        errors: list[BaseException] = []

        def run() -> None:
            try:
                host.serve_forever()
            except BaseException as exc:  # pragma: no cover - surfaced by fixture
                errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3.0
        while (
            (not host.socket_path.exists() or host.host_generation == 0)
            and not errors
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if errors:
            raise errors[0]
        assert host.socket_path.exists()
        assert host.host_generation > 0
        client = HarnessClient(state_root, socket_path)
        try:
            yield host, client
        finally:
            host.shutdown()
            thread.join(timeout=3.0)
            assert not thread.is_alive()
            assert not errors


def test_host_status_keeps_startup_runtime_identity_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    startup_details = runtime.stat()
    monkeypatch.setattr("ai_collab.host.sys.prefix", str(runtime))

    with running_host(tmp_path / "state") as (_, client):
        runtime.rename(tmp_path / "old-runtime")
        runtime.mkdir()

        identity = client.host_status()["host_runtime_identity"]
        assert identity == {
            "dev": startup_details.st_dev,
            "ino": startup_details.st_ino,
        }
        current_details = runtime.stat()
        assert identity != {
            "dev": current_details.st_dev,
            "ino": current_details.st_ino,
        }


def test_real_host_create_open_status_list_and_restart(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (first_host, client):
        assert client.host_status() == {
            "status": "ready",
            "host_generation": first_host.host_generation,
            "scenario_count": 0,
            "host_runtime_identity": first_host.host_runtime_identity,
        }
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="request-create",
        )["scenario"]
        assert created["scenario_generation"] == 1
        assert created["state_revision"] == 2
        assert created["desired_state"] == "closed"
        assert created["observed_state"] == "closed"
        assert created["workspace_binding_id"].startswith("workspace-")
        workspace = state_root / "workspaces" / created["workspace_binding_id"]
        assert workspace.is_dir()
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        assert list(workspace.iterdir()) == []

        replayed = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="request-create",
        )["scenario"]
        assert replayed == created

        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="request-open",
        )["scenario"]
        assert opened["desired_state"] == "running"
        assert opened["observed_state"] == "running"
        assert opened["state_revision"] == 4
        assert client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"] == opened
        assert client.list_scenarios(project_instance_id=PROJECT_ID) == {
            "scenarios": [opened]
        }
        durable = json.loads((state_root / "host-state.json").read_text(encoding="utf-8"))
        operations = sorted(
            durable["operations"].values(), key=lambda value: value["created_sequence"]
        )
        assert [value["operation_kind"] for value in operations] == [
            "scenario.create",
            "scenario.open",
        ]
        for operation in operations:
            assert [
                entry["event"]
                for entry in durable["journal"]
                if entry["operation_id"] == operation["operation_id"]
            ] == [
                "planned",
                "desired_state_committed",
                "external_started",
                "external_succeeded",
                "finalize_committed",
            ]

    with running_host(state_root) as (second_host, client):
        assert second_host.host_generation == first_host.host_generation + 1
        assert client.host_status()["scenario_count"] == 1
        assert client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="request-create",
        )["scenario"] == created
        assert client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]["observed_state"] == "running"

    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((state_root / "host-state.json").stat().st_mode) == 0o600


def test_scenario_topology_remains_available_without_participant_driver(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client):
        client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="topology-no-driver-create",
        )
        topology = client.scenario_topology(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["topology"]
        assert topology["action"] == "inspect"
        assert topology["participants"] == []
        assert len(topology["summary_digest"]) == 64
    assert stat.S_IMODE((state_root / "owner-capability").stat().st_mode) == 0o600


def test_registry_concurrent_scenario_creates_preserve_all_declared_records(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    scenario_ids = ("parallel-scenario-a", "parallel-scenario-b")
    with running_host(state_root) as (host, client):
        barrier = threading.Barrier(len(scenario_ids))

        def create(scenario_id: str) -> dict[str, object]:
            barrier.wait(timeout=3)
            _, result = host.store.create_scenario(
                request_id=f"concurrent-create-{scenario_id}",
                request_digest=("a" if scenario_id.endswith("a") else "b") * 64,
                host_generation=host.host_generation,
                project_instance_id=PROJECT_ID,
                scenario_id=scenario_id,
                project_binding_digest=PROJECT_DIGEST,
                project_contract_snapshot=PROJECT_RENDER,
            )
            return result["scenario"]

        with ThreadPoolExecutor(max_workers=len(scenario_ids)) as executor:
            created = list(executor.map(create, scenario_ids))

        assert {value["scenario_id"] for value in created} == set(scenario_ids)
        assert len({value["workspace_binding_id"] for value in created}) == len(
            scenario_ids
        )
        listed = client.list_scenarios(project_instance_id=PROJECT_ID)["scenarios"]
        assert [value["journal_head_sequence"] for value in listed] == sorted(
            (value["journal_head_sequence"] for value in listed), reverse=True
        )
        assert {value["scenario_id"] for value in listed} == set(scenario_ids)
        assert all(value["observed_state"] == "closed" for value in listed)
        assert client.host_status()["scenario_count"] == len(scenario_ids)

        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert len(durable["scenarios"]) == len(scenario_ids)
        assert len(durable["requests"]) == len(scenario_ids)


def test_empty_scenario_close_and_diagnostic_are_real_host_operations(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="empty-create",
        )["scenario"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="empty-open",
        )["scenario"]
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=1,
            request_id="empty-close",
        )
        assert closed["scenario"]["observed_state"] == "closed"
        assert closed["close_summary"]["reports"] == []
        assert closed["close_summary"]["all_closed"] is True
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["diagnostic"]
        assert diagnostic["scenario"] == closed["scenario"]
        assert diagnostic["participants"] == []
        assert diagnostic["latest_close"] == closed["close_summary"]
        assert diagnostic["active_operations"] == []
        assert diagnostic["repair_actions"] == []
        preflight = client.scenario_preflight(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["preflight"]
        assert preflight["status"] == "blocked"
        assert preflight["permission_observations"] == []
        assert {
            value["check_id"]: value["status"] for value in preflight["checks"]
        } == {
            "project.access": "blocked",
            "scenario.state": "ready",
            "workspace.state": "blocked",
            "participant.state": "ready",
            "presentation.permission": "not_required",
        }
        assert preflight["repair_actions"] == [
            "project.register",
            "workspace.prepare",
        ]
        assert len(preflight["preflight_digest"]) == 64


def test_scenario_close_does_not_overwrite_an_active_scenario_operation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="active-operation-create",
        )["scenario"]
        with host.store._lock:  # noqa: SLF001 - exact durable race fixture
            state = host.store._read_state()  # noqa: SLF001
            item = next(iter(state["scenarios"].values()))
            item["record"]["desired_state"] = "running"
            item["record"]["observed_state"] = "opening"
            item["record"]["active_operation_id"] = "operation:existing"
            item["record"]["state_revision"] += 1
            state["state_revision"] += 1
            host.store._write_state(state)  # noqa: SLF001
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError) as exc:
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"] + 1,
                drain_timeout_ms=25,
                request_id="close-over-active-operation",
            )
        assert exc.value.code == "operation.precondition-failed"
        assert (state_root / "host-state.json").read_bytes() == before


def test_reused_request_with_different_payload_fails_without_mutation(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client):
        client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="same-request",
        )
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError, match="request identity was reused") as exc:
            client.create_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                project_binding_digest="b" * 64,
                request_id="same-request",
            )
        assert exc.value.code == "operation.precondition-failed"
        assert (state_root / "host-state.json").read_bytes() == before


def test_scenario_destroy_namespaces_its_workspace_join_request(
    tmp_path: Path,
) -> None:
    class PlanOnlyWorkspaceAdapter:
        def call(
            self, operation: str, payload: Mapping[str, Any]
        ) -> dict[str, Any]:
            assert operation == "plan"
            plan = {
                "plan_id": f"plan:{payload['operation_id']}",
                "operation_id": payload["operation_id"],
                "scenario": payload["scenario"],
                "project_descriptor_digest": PROJECT_DIGEST,
                "components": [],
            }
            return {"descriptors": [], "plan": plan}

    class ApprovingSecurityAdapter:
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
                            "observed_at_epoch_ms": payload[
                                "captured_at_epoch_ms"
                            ],
                            "valid_until_epoch_ms": payload[
                                "captured_at_epoch_ms"
                            ]
                            + 2_000,
                            "evidence_digest": "b" * 64,
                            "provider_error_code": None,
                            "remediation_ref": None,
                        }
                        for permission_id in payload["permission_ids"]
                    ]
                }
            assert operation == "present"
            challenge = payload["challenge"]
            return {
                "challenge_digest": canonical_json_sha256(challenge),
                "outcome": "approved",
                "decided_at_epoch_ms": challenge["issued_at_epoch_ms"],
                "presenter_instance_digest": "c" * 64,
                "decision_evidence_digest": "d" * 64,
                "reason_code": None,
            }

    state_root = tmp_path / "state"

    def configure(host: HarnessHost) -> None:
        host.workspace = WorkspaceCoordinator(  # type: ignore[arg-type]
            state_root, PlanOnlyWorkspaceAdapter()
        )
        host.security = SecurityCoordinator(  # type: ignore[arg-type]
            state_root, ApprovingSecurityAdapter()
        )

    public_request_id = "shared-public-request"
    with running_host(state_root, configure=configure) as (first_host, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="namespace-create",
        )["scenario"]
        planned = client.plan_workspace(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            requested_component_ids=[],
            project_payload={},
            request_id=public_request_id,
        )["workspace"]
        assert planned["state"] == "planned"

        destroyed = client.destroy_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id=public_request_id,
        )
        assert destroyed["unregistered"] is True

        workspace_state = json.loads(
            (state_root / "workspace-execution.json").read_text(encoding="utf-8")
        )
        request_ids = set(workspace_state["requests"])
        workspace_join_ids = {
            request_id
            for request_id in request_ids
            if request_id.startswith("!store-workspace:")
        }
        assert public_request_id in request_ids
        assert len(workspace_join_ids) == 1
        assert public_request_id not in workspace_join_ids
        assert workspace_state["requests"][public_request_id]["status"] == (
            "completed"
        )
        assert workspace_state["requests"][workspace_join_ids.pop()][
            "status"
        ] == "completed"

    with running_host(state_root, configure=configure) as (second_host, client):
        assert second_host.host_generation == first_host.host_generation + 1
        assert client.host_status()["scenario_count"] == 0
        assert client.list_scenarios(project_instance_id=PROJECT_ID) == {
            "scenarios": []
        }


def test_wrong_capability_is_rejected_without_state_mutation(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client):
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError, match="capability proof was rejected") as exc:
            client._call(  # noqa: SLF001 - explicit security-boundary test
                "scenario.create",
                {
                    "scope": "scenario",
                    "project_instance_id": PROJECT_ID,
                    "scenario_id": SCENARIO_ID,
                },
                {"operation_generation": 0},
                {"project_binding_digest": PROJECT_DIGEST},
                capability_override="wrong-capability",
            )
        assert exc.value.code == "auth.capability-denied"
        assert (state_root / "host-state.json").read_bytes() == before


def test_workspace_failure_is_durable_and_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client):
        real_mkdir = Path.mkdir

        def fail_workspace(path: Path, *args: object, **kwargs: object) -> None:
            if path.parent == state_root / "workspaces" and path.name.startswith("workspace-"):
                raise OSError("injected workspace failure")
            real_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_workspace)
        for _ in range(2):
            with pytest.raises(HarnessClientError, match="workspace provisioning failed") as exc:
                client.create_scenario(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    project_binding_digest=PROJECT_DIGEST,
                    request_id="failed-create",
                )
            assert exc.value.code == "operation.external-failure"
        failed = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert failed["desired_state"] == "closed"
        assert failed["observed_state"] == "provision_failed"
        assert failed["state_revision"] == 2
        durable = json.loads((state_root / "host-state.json").read_text(encoding="utf-8"))
        operation = next(iter(durable["operations"].values()))
        assert operation["state"] == "failed"
        assert operation["failure_code"] == "workspace.provision-failed"


def test_restart_reconciles_unknown_provisioning_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client):
        write_state = host.store._write_state  # noqa: SLF001 - crash-window injection

        def fail_finalize(value: dict[str, object]) -> None:
            scenarios = value["scenarios"]
            assert isinstance(scenarios, dict)
            if any(
                item["record"]["observed_state"] == "closed"
                for item in scenarios.values()
            ):
                raise OSError("injected final commit failure")
            write_state(value)

        monkeypatch.setattr(host.store, "_write_state", fail_finalize)
        with pytest.raises(HarnessClientError, match="outcome is unknown") as exc:
            client.create_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                project_binding_digest=PROJECT_DIGEST,
                request_id="crash-window-create",
            )
        assert exc.value.code == "operation.internal-failure"
        pending = json.loads((state_root / "host-state.json").read_text(encoding="utf-8"))
        record = next(iter(pending["scenarios"].values()))["record"]
        assert record["observed_state"] == "provisioning"
        assert record["active_operation_id"] is not None
        monkeypatch.setattr(host.store, "_write_state", write_state)

    with running_host(state_root) as (_, client):
        reconciled = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert reconciled["observed_state"] == "closed"
        assert reconciled["active_operation_id"] is None
        assert client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="crash-window-create",
        )["scenario"] == reconciled


def test_restart_marks_interrupted_close_repair_required(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="close-restart-create",
        )["scenario"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="close-restart-open",
        )["scenario"]
        operation_id, replay, executions = host.store.begin_scenario_close(
            request_id="close-restart-interrupted",
            request_digest="f" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=1_000,
        )
        assert replay is None
        assert executions == []
        closing = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        assert closing["observed_state"] == "closing"
        assert closing["active_operation_id"] == operation_id

    with running_host(state_root) as (_, client):
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["scenario"]["desired_state"] == "closed"
        assert diagnostic["scenario"]["observed_state"] == "degraded"
        assert diagnostic["scenario"]["active_operation_id"] is None
        assert diagnostic["repair_actions"] == ["scenario.repair"]
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        operation = durable["operations"][operation_id]
        assert operation["state"] == "repair_required"
        assert operation["mutation_state"] == "unknown"
        assert operation["failure_code"] == "lifecycle.close-outcome-unknown"
        close_events = [
            entry for entry in durable["journal"] if entry["operation_id"] == operation_id
        ]
        assert close_events[-2]["event"] == "repair_required"
        assert close_events[-2]["mutation_state"] == "unknown"
        assert close_events[-2]["error_code"] == "lifecycle.close-outcome-unknown"
        assert close_events[-1]["event"] == "finalize_committed"
        assert close_events[-1]["mutation_state"] == "committed"
        assert close_events[-1]["error_code"] is None


def test_restart_finalizes_close_when_external_reports_were_durable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="close-recorded-create",
        )["scenario"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="close-recorded-open",
        )["scenario"]
        operation_id, replay, executions = host.store.begin_scenario_close(
            request_id="close-recorded-crash",
            request_digest="e" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=1_000,
        )
        assert replay is None
        assert executions == []
        host.store.record_scenario_close_reports(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="close-recorded-crash",
            operation_id=operation_id,
            reports=[],
        )

    with running_host(state_root) as (_, client):
        reconciled = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert reconciled["observed_state"] == "closed"
        assert reconciled["active_operation_id"] is None
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert durable["operations"][operation_id]["state"] == "succeeded"
        assert durable["requests"]["close-recorded-crash"]["status"] == "completed"


def test_restart_finalizes_force_close_when_external_reports_were_durable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="force-close-recorded-create",
        )["scenario"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-close-recorded-open",
        )["scenario"]
        operation_id, replay, executions = host.store.begin_scenario_close(
            request_id="force-close-recorded-crash",
            request_digest="d" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=1,
            force=True,
        )
        assert replay is None
        assert executions == []
        host.store.record_scenario_close_reports(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="force-close-recorded-crash",
            operation_id=operation_id,
            reports=[],
            force_stop_used=True,
        )

    with running_host(state_root) as (_, client):
        reconciled = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert reconciled["observed_state"] == "closed"
        assert reconciled["active_operation_id"] is None
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert durable["operations"][operation_id]["state"] == "succeeded"
        assert (
            durable["requests"]["force-close-recorded-crash"]["status"]
            == "completed"
        )
        close_history = next(iter(durable["scenarios"].values()))["close_history"]
        assert close_history[-1]["auto_force_stop_used"] is True


def test_cli_controls_a_real_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, _):
        assert cli_main.main(
            [
                "harness",
                "status",
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ready"

        assert cli_main.main(
            [
                "harness",
                "scenario",
                "create",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--project-binding-digest",
                PROJECT_DIGEST,
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        created = json.loads(capsys.readouterr().out)["scenario"]
        assert created["observed_state"] == "closed"

        assert cli_main.main(
            [
                "harness",
                "scenario",
                "open",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--scenario-generation",
                str(created["scenario_generation"]),
                "--state-revision",
                str(created["state_revision"]),
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        opened = json.loads(capsys.readouterr().out)["scenario"]
        assert opened["observed_state"] == "running"

        assert cli_main.main(
            [
                "harness",
                "scenario",
                "diagnostic",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        before_close = json.loads(capsys.readouterr().out)["diagnostic"]
        assert before_close["scenario"] == opened
        assert before_close["latest_close"] is None

        assert cli_main.main(
            [
                "harness",
                "scenario",
                "preflight",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        preflight = json.loads(capsys.readouterr().out)["preflight"]
        assert preflight["status"] == "blocked"
        assert "workspace.prepare" in preflight["repair_actions"]

        assert cli_main.main(
            [
                "harness",
                "scenario",
                "close",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--scenario-generation",
                str(opened["scenario_generation"]),
                "--state-revision",
                str(opened["state_revision"]),
                "--drain-timeout-ms",
                "10",
                "--progress",
                "--state-root",
                str(state_root),
                "--socket-path",
                str(host.socket_path),
                "--json",
            ]
        ) == 0
        captured = capsys.readouterr()
        closed = json.loads(captured.out)
        progress = [json.loads(line) for line in captured.err.splitlines()]
        assert [event["sequence"] for event in progress] == [0, 1]
        assert [event["state"] for event in progress] == ["running", "completed"]
        assert closed["scenario"]["observed_state"] == "closed"
        assert closed["close_summary"]["auto_force_stop_used"] is False
