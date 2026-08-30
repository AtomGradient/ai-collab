# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from ai_collab import cli as cli_main
from ai_collab.protocol import canonical_json_sha256
from ai_collab.client import HarnessClient, HarnessClientError
from ai_collab.host import HarnessHost
from ai_collab.security import SecurityCoordinator
from ai_collab.store import OperationFailed, ScenarioStore, StoreError
from ai_collab.workspace import (
    ADAPTER_ENVIRONMENT_KEYS,
    ProjectAdapterCommand,
    WorkspaceCoordinator,
    WorkspaceError,
)


_BUILTIN_COLLABORATION_REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "ai_collab_team_policies.json").read_text(
        encoding="utf-8"
    )
)

HOST_PROJECT_RENDER = {
    "render_contract_version": 1,
    "source": {"kind": "fileless", "intent_schema_version": None, "source_digest": "1" * 64},
    "project": {
        "project_key": "project",
        "product_contract_version": "1.0",
        "workspace_adapter_id": "workspace.test-v1",
        "environment_adapter_id": "environment.test-v1",
        "participant_driver_contract": 2,
        "collaboration_policy_schema": 1,
    },
    "repo_manifest": {"schema_version": 1, "project_key": "project", "repos": []},
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
HOST_PROJECT_RENDER["availability"]["fingerprint"] = canonical_json_sha256(  # type: ignore[index]
    HOST_PROJECT_RENDER["availability"]
)
HOST_PROJECT_RENDER["render_digest"] = canonical_json_sha256(
    {
        key: value
        for key, value in HOST_PROJECT_RENDER.items()
        if key != "availability"
    }
)
HOST_PROJECT_DIGEST = HOST_PROJECT_RENDER["render_digest"]


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
    assert {"HOME", "SSH_AUTH_SOCK"}.issubset(ADAPTER_ENVIRONMENT_KEYS)
    assert "PYTHONDONTWRITEBYTECODE" in ADAPTER_ENVIRONMENT_KEYS
    assert "CLAUDE_CODE_SESSION_ID" not in ADAPTER_ENVIRONMENT_KEYS
    assert "CODEX_THREAD_ID" not in ADAPTER_ENVIRONMENT_KEYS


def test_host_preserves_actionable_workspace_availability_error() -> None:
    error = HarnessHost.workspace_error(
        WorkspaceError(
            "workspace.git-auth-required",
            "Git credentials are unavailable to AICollab.",
        )
    )
    assert error.code == "workspace.git-auth-required"
    assert error.category == "availability"
    assert error.retryable is False
    assert error.repair_action == "git.authenticate"

    transient = HarnessHost.workspace_error(
        WorkspaceError(
            "workspace.network-unavailable",
            "The repository network is unavailable.",
            retryable=True,
        )
    )
    assert transient.retryable is True
    assert transient.repair_action == "workspace.prepare"

    shallow = HarnessHost.workspace_error(
        WorkspaceError(
            "workspace.shallow-source",
            "Fetch complete Git history.",
        )
    )
    assert shallow.retryable is False
    assert shallow.repair_action == "git.fetch-full-history"


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
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
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
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
    )
    new_binding = new_result["scenario"]["workspace_binding_id"]
    assert (external_root / new_binding).is_dir()
    assert not (state_root / "workspaces" / new_binding).exists()


def test_v0161_host_state_migrates_atomically_with_scenario_last_good(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    store = ScenarioStore(state_root)
    store.create_scenario(
        request_id="legacy-create",
        request_digest="a" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="legacy",
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
    )
    state_path = state_root / "host-state.json"
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy["state_revision"] -= 1
    for collection in (legacy["scenarios"], legacy["scenario_history"]):
        for item in collection.values():
            del item["project_contract_snapshot"]
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    state_path.chmod(0o600)

    ScenarioStore(state_root)
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert next(iter(migrated["scenarios"].values()))[
        "project_contract_snapshot"
    ] is None
    backup = state_root / "host-state.v1.last-good.json"
    assert backup.is_file()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == 1


class FakeAdapter:
    def __init__(
        self,
        *,
        observed_state: str = "aligned",
        drift_codes: tuple[str, ...] = (),
    ) -> None:
        # Injectable so a test can observe a workspace that is not aligned.
        # Without this every high-risk test saw a perfectly aligned workspace,
        # which is how the force-destroy gate stayed uncovered.
        self.observed_state = observed_state
        self.drift_codes = list(drift_codes)
        self.project_binding_digest = HOST_PROJECT_DIGEST
        self.adapter_id = "workspace.test-v1"
        self.idempotent_join_operations = frozenset(
            {"destroy", "recover", "repair"}
        )

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "plan":
            plan = {
                "plan_id": f"plan:{payload['operation_id']}",
                "operation_id": payload["operation_id"],
                "scenario": payload["scenario"],
                "project_descriptor_digest": getattr(
                    self, "project_binding_digest", "b" * 64
                ),
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
                    "state": self.observed_state,
                    "drift_codes": list(self.drift_codes),
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
        if operation == "recover":
            plan = payload["plan"]
            receipt = payload["receipt"]
            prior = payload["prior_operation"]
            bundle = Path(payload["bundle_path"])
            resolution = "ready" if bundle.exists() else "missing"
            journal = {
                "operation_id": payload["operation_id"],
                "operation_kind": "recover",
                "plan_digest": canonical_json_sha256(plan),
            }
            recovery = {
                "prior_operation_id": prior["operation_id"],
                "prior_operation_kind": prior["operation_kind"],
                "prior_claim_digest": prior["claim_digest"],
                "resolution": resolution,
            }
            observation = {
                "operation_id": payload["operation_id"],
                "operation_kind": "recover",
                "journal_digest": canonical_json_sha256(journal),
                "receipt_digest": canonical_json_sha256(receipt),
                "state": "aligned" if resolution == "ready" else "missing",
                "drift_codes": [],
                "wip_summary_digest": payload[
                    "expected_wip_summary_digest"
                ],
            }
            if resolution == "missing":
                snapshot = None
                resolved_receipt = None
            else:
                resolved_receipt = receipt
                snapshot = {
                    "snapshot_contract_version": 1,
                    "plan_digest": canonical_json_sha256(plan),
                    "receipt_digest": canonical_json_sha256(receipt),
                }
                snapshot["snapshot_digest"] = canonical_json_sha256(snapshot)
            return {
                "journal": journal,
                "receipt": resolved_receipt,
                "observation": observation,
                "review_snapshot": snapshot,
                "recovery": recovery,
            }
        raise AssertionError(operation)


class ProgressFakeAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.progress_side_channel = "v1"

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        if operation == "plan":
            result = super().call(operation, payload)
            result["plan"]["components"] = [
                {"component_id": "project"},
                {"component_id": "helper-lib"},
            ]
            result["plan"]["environment"] = {
                "environment_id": "environment:test"
            }
            return result
        if operation == "provision" and progress_callback is not None:
            events = [
                ("project", 0, "waiting"),
                ("helper-lib", 1, "waiting"),
                ("environment:test", 2, "waiting"),
                ("project", 0, "cloning"),
                ("project", 0, "ready"),
                ("helper-lib", 1, "cloning"),
                ("helper-lib", 1, "ready"),
                ("environment:test", 2, "building"),
                ("environment:test", 2, "ready"),
            ]
            for component_id, index, state in events:
                progress_callback(
                    {
                        "component_id": component_id,
                        "index": index,
                        "total": 3,
                        "state": state,
                    }
                )
        return super().call(operation, payload)


class FailFastProgressAdapter(ProgressFakeAdapter):
    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        if operation == "plan":
            result = super().call(operation, payload)
            result["plan"]["components"] = [
                {"component_id": "repo-a"},
                {"component_id": "repo-b"},
                {"component_id": "repo-c"},
            ]
            result["plan"]["environment"] = {
                "environment_id": "environment:fail-fast"
            }
            return result
        if operation == "provision":
            assert progress_callback is not None
            items = ["repo-a", "repo-b", "repo-c", "environment:fail-fast"]
            for index, component_id in enumerate(items):
                progress_callback(
                    {
                        "component_id": component_id,
                        "index": index,
                        "total": 4,
                        "state": "waiting",
                    }
                )
            for component_id, index, state in (
                ("repo-a", 0, "cloning"),
                ("repo-a", 0, "ready"),
                ("repo-b", 1, "cloning"),
                ("repo-b", 1, "failed"),
            ):
                progress_callback(
                    {
                        "component_id": component_id,
                        "index": index,
                        "total": 4,
                        "state": state,
                    }
                )
            staging = Path(payload["staging_path"])
            staging.mkdir(mode=0o700)
            (staging / "partial-clone").write_text("scratch\n", encoding="utf-8")
            raise WorkspaceError(
                "workspace.git-auth-required",
                "repository authentication is required",
                retryable=False,
                mutation_state="started",
            )
        return FakeAdapter.call(self, operation, payload)


class FailProvisionOnceAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "provision" and not self.failed:
            self.failed = True
            raise WorkspaceError(
                "workspace.git-auth-required",
                "Git credentials are unavailable to AICollab.",
            )
        return super().call(operation, payload)


class UnknownDestroyAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.destroy_calls = 0

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "destroy":
            self.destroy_calls += 1
            raise WorkspaceError(
                "workspace.destroy-outcome-unknown",
                "injected unknown destroy outcome",
                retryable=True,
                mutation_state="unknown",
                operation_id=payload["operation_id"],
            )
        return super().call(operation, payload)


class UnknownThenNotStartedRecoverAdapter(UnknownDestroyAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.recover_calls = 0

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "recover":
            self.recover_calls += 1
            if self.recover_calls == 1:
                raise WorkspaceError(
                    "workspace.recover-outcome-unknown",
                    "injected unknown recover outcome",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=payload["operation_id"],
                )
            raise WorkspaceError(
                "workspace.concurrent-change",
                "injected no-effect recover refusal",
                retryable=True,
                mutation_state="not_started",
                operation_id=payload["operation_id"],
            )
        return super().call(operation, payload)


class ObservingDestroyAdapter(FakeAdapter):
    def __init__(self, state_path: Path, store_request_id: str) -> None:
        super().__init__()
        self.state_path = state_path
        self.store_request_id = store_request_id
        self.destroy_calls = 0
        self.destroy_operation_id: str | None = None
        self.store_request_before_destroy: dict[str, Any] | None = None

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "destroy":
            self.destroy_calls += 1
            self.destroy_operation_id = payload["operation_id"]
            durable = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.store_request_before_destroy = copy.deepcopy(
                durable["requests"][self.store_request_id]
            )
        return super().call(operation, payload)


class BlockingDestroyAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.destroy_calls = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self._calls_lock = threading.Lock()

    def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if operation != "destroy":
            return super().call(operation, payload)
        with self._calls_lock:
            self.destroy_calls += 1
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        self.entered.set()
        try:
            if not self.release.wait(timeout=3):
                raise AssertionError("timed out waiting to release destroy adapter")
            return super().call(operation, payload)
        finally:
            with self._calls_lock:
                self.active_calls -= 1


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
    *,
    adapter: FakeAdapter | None = None,
    install_workspace_adapter: bool = True,
) -> Iterator[tuple[HarnessHost, HarnessClient]]:
    with tempfile.TemporaryDirectory(prefix="harness-high-risk-") as runtime:
        socket_path = Path(runtime) / "host.sock"
        host = HarnessHost(state_root, socket_path)
        host.projects.validate_binding = lambda _project, _digest: None  # type: ignore[method-assign]
        host.projects.resolved_render = (  # type: ignore[method-assign]
            lambda _project, digest=None: HOST_PROJECT_RENDER
            if digest in {None, HOST_PROJECT_DIGEST}
            else None
        )
        if install_workspace_adapter:
            workspace_adapter = adapter or FakeAdapter()
            workspace_adapter.project_binding_digest = HOST_PROJECT_DIGEST
            host.workspace = WorkspaceCoordinator(  # type: ignore[arg-type]
                state_root, workspace_adapter
            )
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
        while host._server is None and not errors and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        if errors:
            raise errors[0]
        client = HarnessClient(state_root, socket_path)
        # A successful round trip proves serve_forever is actually running;
        # socket creation alone races BaseServer.shutdown and caused flaky
        # teardown deadlocks under the full suite.
        client.host_status()
        try:
            yield host, client
        finally:
            host.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert not errors


def _coordinator(tmp_path: Path) -> tuple[ScenarioStore, WorkspaceCoordinator, dict[str, Any], Path]:
    store = ScenarioStore(tmp_path / "state")
    store.create_scenario(
        request_id="create",
        request_digest="a" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="scenario",
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
    )
    record, workspace_path = store.scenario_workspace("project", "scenario")
    coordinator = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    return store, coordinator, record, workspace_path


def _begin_ready_destroy_join(
    state_root: Path, adapter: FakeAdapter
) -> dict[str, Any]:
    store = ScenarioStore(state_root)
    _, created = store.create_scenario(
        request_id="join-create",
        request_digest="1" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="scenario",
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
    )
    record = created["scenario"]
    _, workspace_path = store.scenario_workspace("project", "scenario")
    coordinator = WorkspaceCoordinator(state_root, adapter)  # type: ignore[arg-type]
    _, planned = coordinator.plan(
        request_id="join-plan",
        request_digest="2" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    coordinator.provision(
        request_id="join-provision",
        request_digest="3" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    preview, _ = coordinator.high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=workspace_path,
        operation="scenario.destroy",
    )
    store_request_id = "join-destroy"
    request_digest = "4" * 64
    store_operation_id, replay, pending_path = store.begin_scenario_destroy(
        request_id=store_request_id,
        request_digest=request_digest,
        host_generation=1,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        expected_workspace_binding_state=preview["binding_state"],
        expected_wip_summary_digest=preview["wip_summary_digest"],
    )
    assert replay is None
    assert pending_path == workspace_path
    workspace_request_id = store.workspace_join_request_id(
        "scenario.destroy", store_request_id, request_digest
    )
    return {
        "store": store,
        "coordinator": coordinator,
        "record": record,
        "workspace_path": workspace_path,
        "preview": preview,
        "store_request_id": store_request_id,
        "request_digest": request_digest,
        "store_operation_id": store_operation_id,
        "workspace_request_id": workspace_request_id,
    }


def _leave_unknown_destroy_join(
    state_root: Path, adapter: UnknownDestroyAdapter
) -> dict[str, Any]:
    fixture = _begin_ready_destroy_join(state_root, adapter)

    with pytest.raises(WorkspaceError) as unknown:
        fixture["coordinator"].destroy(
            request_id=fixture["workspace_request_id"],
            request_digest=fixture["request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=fixture["preview"][
                "wip_summary_digest"
            ],
            expected_binding_state=fixture["preview"]["binding_state"],
            before_external=lambda claim: _bind_store_destroy_claim(
                fixture, claim
            ),
        )
    assert unknown.value.mutation_state == "unknown"
    assert adapter.destroy_calls == 1
    return fixture


def _bind_store_destroy_claim(
    fixture: Mapping[str, Any], claim: Mapping[str, Any]
) -> None:
    fixture["store"].bind_workspace_execution_claim(
        project_instance_id="project",
        scenario_id="scenario",
        request_id=fixture["store_request_id"],
        request_digest=fixture["request_digest"],
        operation_id=fixture["store_operation_id"],
        workspace_request_id=fixture["workspace_request_id"],
        operation_kind="scenario.destroy",
        scenario_generation=fixture["record"]["scenario_generation"],
        workspace_claim=dict(claim),
    )


def _assert_destroy_join_degraded(fixture: Mapping[str, Any]) -> None:
    durable = json.loads(
        (fixture["store"].state_root / "host-state.json").read_text(
            encoding="utf-8"
        )
    )
    scenario = next(iter(durable["scenarios"].values()))["record"]
    assert scenario["observed_state"] == "degraded"
    assert scenario["active_operation_id"] is None
    assert scenario["degraded"]["reason"] == "operation_unknown"
    assert scenario["degraded"]["repair_action"] == "scenario.repair"
    request = durable["requests"][fixture["store_request_id"]]
    assert request["status"] == "failed"
    assert request["error"]["mutation_state"] == "unknown"


def _manual_destroy_recovery_fixture(
    state_root: Path,
    adapter: UnknownDestroyAdapter,
) -> dict[str, Any]:
    fixture = _leave_unknown_destroy_join(state_root, adapter)
    claim = fixture["coordinator"].inspect_pending_high_risk_join(
        workspace_request_id=fixture["workspace_request_id"],
        request_digest=fixture["request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert claim is not None
    fixture["store"].degrade_unknown_workspace_join(
        project_instance_id="project",
        scenario_id="scenario",
        request_id=fixture["store_request_id"],
        request_digest=fixture["request_digest"],
        operation_id=fixture["store_operation_id"],
        workspace_request_id=fixture["workspace_request_id"],
        operation_kind="scenario.destroy",
        scenario_generation=fixture["record"]["scenario_generation"],
        workspace_claim=claim,
        reason="workspace.join-unprovable",
        unjoinable=True,
    )
    record = fixture["store"].scenario_status("project", "scenario")[
        "scenario"
    ]
    preview, _ = fixture["coordinator"].high_risk_context(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        workspace_path=fixture["workspace_path"],
        operation="scenario.repair",
    )
    recovery = preview["recovery"]
    request_id = "manual-recover"
    request_digest = "9" * 64
    operation_id, replay, pending_path = fixture[
        "store"
    ].begin_scenario_repair(
        request_id=request_id,
        request_digest=request_digest,
        host_generation=1,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        expected_wip_summary_digest=preview["wip_summary_digest"],
        workspace_operation_kind="recover",
        expected_recovery_claim_digest=recovery[
            "prior_operation_claim_digest"
        ],
        expected_recovery_inventory_digest=recovery["inventory_digest"],
        expected_recovery_prior_operation_kind=recovery[
            "prior_operation_kind"
        ],
    )
    assert replay is None
    assert pending_path == fixture["workspace_path"]
    workspace_request_id = fixture["store"].workspace_join_request_id(
        "scenario.repair", request_id, request_digest
    )
    return {
        **fixture,
        "record": record,
        "recovery_preview": preview,
        "recovery": recovery,
        "recovery_request_id": request_id,
        "recovery_request_digest": request_digest,
        "recovery_operation_id": operation_id,
        "recovery_workspace_request_id": workspace_request_id,
    }


def _bind_store_recovery_claim(
    fixture: Mapping[str, Any], claim: Mapping[str, Any]
) -> None:
    fixture["store"].bind_workspace_execution_claim(
        project_instance_id="project",
        scenario_id="scenario",
        request_id=fixture["recovery_request_id"],
        request_digest=fixture["recovery_request_digest"],
        operation_id=fixture["recovery_operation_id"],
        workspace_request_id=fixture["recovery_workspace_request_id"],
        operation_kind="scenario.repair",
        scenario_generation=fixture["record"]["scenario_generation"],
        workspace_claim=dict(claim),
    )


def test_recover_not_started_after_unknown_never_rolls_back(
    tmp_path: Path,
) -> None:
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(tmp_path / "state", adapter)
    preview = fixture["recovery_preview"]
    with pytest.raises(WorkspaceError) as first:
        fixture["coordinator"].recover(
            request_id=fixture["recovery_workspace_request_id"],
            request_digest=fixture["recovery_request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_prior_claim_digest=fixture["recovery"][
                "prior_operation_claim_digest"
            ],
            expected_inventory_digest=fixture["recovery"][
                "inventory_digest"
            ],
            before_external=lambda claim: _bind_store_recovery_claim(
                fixture, claim
            ),
        )
    assert first.value.mutation_state == "unknown"
    claim = fixture["coordinator"].inspect_pending_high_risk_join(
        workspace_request_id=fixture["recovery_workspace_request_id"],
        request_digest=fixture["recovery_request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert claim is not None
    with pytest.raises(WorkspaceError) as replay:
        fixture["coordinator"].resume_exact_high_risk_join(
            workspace_claim=claim,
            workspace_path=fixture["workspace_path"],
            before_external=lambda _claim: None,
        )
    assert replay.value.code == "workspace.recover-outcome-unknown"
    assert replay.value.mutation_state == "unknown"
    assert adapter.recover_calls == 2
    durable = json.loads(
        fixture["coordinator"].state_path.read_text(encoding="utf-8")
    )
    binding = next(iter(durable["bindings"].values()))
    assert binding["state"] == "recovering"
    assert binding["pending_recover_external_attempted"] is True
    assert durable["requests"][fixture["recovery_workspace_request_id"]][
        "status"
    ] == "pending"


def test_recover_bind_failure_is_durable_no_effect_and_contract_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(tmp_path / "state", adapter)
    preview = fixture["recovery_preview"]

    def reject_store_bind(_claim: Mapping[str, Any]) -> None:
        raise StoreError("host.state-invalid", "injected bind failure")

    with pytest.raises(WorkspaceError) as refused:
        fixture["coordinator"].recover(
            request_id=fixture["recovery_workspace_request_id"],
            request_digest=fixture["recovery_request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_prior_claim_digest=fixture["recovery"][
                "prior_operation_claim_digest"
            ],
            expected_inventory_digest=fixture["recovery"][
                "inventory_digest"
            ],
            before_external=reject_store_bind,
        )
    assert refused.value.mutation_state == "not_started"
    assert adapter.recover_calls == 0
    terminal = fixture["coordinator"].inspect_terminal_recovery(
        workspace_request_id=fixture["recovery_workspace_request_id"],
        request_digest=fixture["recovery_request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert terminal is not None
    assert terminal["resolution"] == "not_started"
    fixture["store"].abort_scenario_recovery_no_effect(
        project_instance_id="project",
        scenario_id="scenario",
        request_id=fixture["recovery_request_id"],
        operation_id=fixture["recovery_operation_id"],
        reason="workspace.recovery-bind-failed",
    )
    with pytest.raises(OperationFailed) as replayed:
        fixture["store"].replay_request(
            fixture["recovery_request_id"],
            fixture["recovery_request_digest"],
        )
    assert replayed.value.code == "operation.precondition-failed"
    assert replayed.value.mutation_state == "committed"
    assert replayed.value.retryable is True

    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repository_root / "scripts"))
    from validate_ai_collab_state_contract import validate_operation_journal

    contract = json.loads(
        (
            repository_root
            / "contracts"
            / "scenario_participant_state_v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    durable = json.loads(
        (fixture["store"].state_root / "host-state.json").read_text(
            encoding="utf-8"
        )
    )
    operation = durable["operations"][fixture["recovery_operation_id"]]
    journal = [
        entry
        for entry in durable["journal"]
        if entry["operation_id"] == fixture["recovery_operation_id"]
    ]
    validate_operation_journal(operation, journal, contract=contract)


def test_exhausted_recover_before_adapter_restores_prior_authority(
    tmp_path: Path,
) -> None:
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(tmp_path / "state", adapter)
    preview = fixture["recovery_preview"]

    def bind_then_crash(claim: Mapping[str, Any]) -> None:
        _bind_store_recovery_claim(fixture, claim)
        raise SystemExit("crash before recover adapter")

    with pytest.raises(SystemExit, match="before recover adapter"):
        fixture["coordinator"].recover(
            request_id=fixture["recovery_workspace_request_id"],
            request_digest=fixture["recovery_request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_prior_claim_digest=fixture["recovery"][
                "prior_operation_claim_digest"
            ],
            expected_inventory_digest=fixture["recovery"][
                "inventory_digest"
            ],
            before_external=bind_then_crash,
        )
    claim = fixture["coordinator"].inspect_pending_high_risk_join(
        workspace_request_id=fixture["recovery_workspace_request_id"],
        request_digest=fixture["recovery_request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert claim is not None
    for expected_attempt in range(1, 4):
        joined = fixture["store"].claim_workspace_join(
            project_instance_id="project",
            scenario_id="scenario",
            request_id=fixture["recovery_request_id"],
            request_digest=fixture["recovery_request_digest"],
            operation_id=fixture["recovery_operation_id"],
            workspace_request_id=fixture[
                "recovery_workspace_request_id"
            ],
            operation_kind="scenario.repair",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_claim=claim,
        )
        assert joined["attempt"] == expected_attempt
    assert (
        fixture["coordinator"].retire_exhausted_recovery(
            workspace_claim=claim,
            workspace_path=fixture["workspace_path"],
            reason="workspace.join-attempts-exhausted",
        )
        == "not_started"
    )
    fixture["store"].abort_scenario_recovery_no_effect(
        project_instance_id="project",
        scenario_id="scenario",
        request_id=fixture["recovery_request_id"],
        operation_id=fixture["recovery_operation_id"],
        reason="workspace.recovery-not-started",
    )
    assert adapter.recover_calls == 0
    scenario = fixture["store"].scenario_status("project", "scenario")[
        "scenario"
    ]
    assert scenario["observed_state"] == "degraded"
    workspace_state = json.loads(
        fixture["coordinator"].state_path.read_text(encoding="utf-8")
    )
    assert next(iter(workspace_state["bindings"].values()))["state"] == (
        "destroying"
    )


def test_restart_aborts_recovery_missing_workspace_request(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(state_root, adapter)
    assert not fixture["coordinator"].has_exact_request(
        fixture["recovery_workspace_request_id"],
        fixture["recovery_request_digest"],
    )

    host = HarnessHost(state_root, tmp_path / "host.sock")
    host.workspace = fixture["coordinator"]
    host._reconcile_workspace_operations()

    assert adapter.recover_calls == 0
    assert fixture["store"].pending_workspace_operations() == []
    with pytest.raises(OperationFailed) as replayed:
        fixture["store"].replay_request(
            fixture["recovery_request_id"],
            fixture["recovery_request_digest"],
        )
    assert replayed.value.code == "operation.precondition-failed"
    authority = fixture["store"].scenario_workspace_recovery_authority(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=fixture["record"]["scenario_generation"],
        scenario_state_revision=fixture["record"]["state_revision"] + 2,
    )
    assert authority["prior_operation_claim_digest"] == fixture["recovery"][
        "prior_operation_claim_digest"
    ]


def test_restart_joins_recovery_no_effect_terminal_before_store_abort(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(state_root, adapter)
    preview = fixture["recovery_preview"]

    with pytest.raises(WorkspaceError) as refused:
        fixture["coordinator"].recover(
            request_id=fixture["recovery_workspace_request_id"],
            request_digest=fixture["recovery_request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_prior_claim_digest=fixture["recovery"][
                "prior_operation_claim_digest"
            ],
            expected_inventory_digest=fixture["recovery"][
                "inventory_digest"
            ],
            before_external=lambda _claim: (_ for _ in ()).throw(
                StoreError("host.state-invalid", "injected bind failure")
            ),
        )
    assert refused.value.mutation_state == "not_started"

    host = HarnessHost(state_root, tmp_path / "host.sock")
    host.workspace = fixture["coordinator"]
    host._reconcile_workspace_operations()

    assert adapter.recover_calls == 0
    assert fixture["store"].pending_workspace_operations() == []
    with pytest.raises(OperationFailed) as replayed:
        fixture["store"].replay_request(
            fixture["recovery_request_id"],
            fixture["recovery_request_digest"],
        )
    assert replayed.value.code == "operation.precondition-failed"


def test_restart_joins_retired_recovery_before_store_degrade(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownThenNotStartedRecoverAdapter()
    fixture = _manual_destroy_recovery_fixture(state_root, adapter)
    preview = fixture["recovery_preview"]

    with pytest.raises(WorkspaceError) as unknown:
        fixture["coordinator"].recover(
            request_id=fixture["recovery_workspace_request_id"],
            request_digest=fixture["recovery_request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_prior_claim_digest=fixture["recovery"][
                "prior_operation_claim_digest"
            ],
            expected_inventory_digest=fixture["recovery"][
                "inventory_digest"
            ],
            before_external=lambda claim: _bind_store_recovery_claim(
                fixture, claim
            ),
        )
    assert unknown.value.mutation_state == "unknown"
    claim = fixture["coordinator"].inspect_pending_high_risk_join(
        workspace_request_id=fixture["recovery_workspace_request_id"],
        request_digest=fixture["recovery_request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert claim is not None
    for expected_attempt in range(1, 4):
        joined = fixture["store"].claim_workspace_join(
            project_instance_id="project",
            scenario_id="scenario",
            request_id=fixture["recovery_request_id"],
            request_digest=fixture["recovery_request_digest"],
            operation_id=fixture["recovery_operation_id"],
            workspace_request_id=fixture[
                "recovery_workspace_request_id"
            ],
            operation_kind="scenario.repair",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_claim=claim,
        )
        assert joined["attempt"] == expected_attempt
    assert fixture["coordinator"].retire_exhausted_recovery(
        workspace_claim=claim,
        workspace_path=fixture["workspace_path"],
        reason="workspace.join-attempts-exhausted",
    ) == "retired"

    host = HarnessHost(state_root, tmp_path / "host.sock")
    host.workspace = fixture["coordinator"]
    host._reconcile_workspace_operations()

    assert adapter.recover_calls == 1
    assert fixture["store"].pending_workspace_operations() == []
    with pytest.raises(OperationFailed) as replayed:
        fixture["store"].replay_request(
            fixture["recovery_request_id"],
            fixture["recovery_request_digest"],
        )
    assert replayed.value.code == "operation.internal-failure"
    assert replayed.value.mutation_state == "unknown"


def test_upgrade_capability_change_preserves_unbound_destroy_recovery(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownDestroyAdapter()
    fixture = _begin_ready_destroy_join(state_root, adapter)

    with pytest.raises(SystemExit, match="before Store bind"):
        fixture["coordinator"].destroy(
            request_id=fixture["workspace_request_id"],
            request_digest=fixture["request_digest"],
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=fixture["record"]["scenario_generation"],
            workspace_path=fixture["workspace_path"],
            expected_wip_summary_digest=fixture["preview"][
                "wip_summary_digest"
            ],
            expected_binding_state=fixture["preview"]["binding_state"],
            before_external=lambda _claim: (_ for _ in ()).throw(
                SystemExit("crash before Store bind")
            ),
        )
    frozen = fixture["coordinator"].inspect_frozen_pending_high_risk_join(
        workspace_request_id=fixture["workspace_request_id"],
        request_digest=fixture["request_digest"],
        workspace_path=fixture["workspace_path"],
    )
    assert frozen is not None
    pending = fixture["store"].pending_workspace_operations()[0]
    assert pending["workspace_operation_id"] is None
    assert pending["workspace_join_claim_digest"] is None

    with running_high_risk_host(
        state_root, install_workspace_adapter=False
    ) as (unavailable_host, _client):
        assert unavailable_host.workspace is None
        preserved = fixture["store"].pending_workspace_operations()
        assert len(preserved) == 1
        assert preserved[0]["workspace_operation_id"] is None

    adapter.idempotent_join_operations = frozenset({"recover", "repair"})

    host = HarnessHost(state_root, tmp_path / "host.sock")
    host.workspace = fixture["coordinator"]
    host._reconcile_workspace_operations()

    assert adapter.destroy_calls == 0
    assert fixture["store"].pending_workspace_operations() == []
    authority = fixture["store"].scenario_workspace_recovery_authority(
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=fixture["record"]["scenario_generation"],
        scenario_state_revision=fixture["record"]["state_revision"] + 2,
    )
    assert authority["prior_operation_kind"] == "destroy"
    assert authority["prior_operation_claim_digest"] == frozen["claim_digest"]


def test_start_host_without_workspace_adapter_degrades_pending_exact_join(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownDestroyAdapter()
    fixture = _leave_unknown_destroy_join(state_root, adapter)
    adapter.destroy_calls = 0

    with running_high_risk_host(
        state_root, install_workspace_adapter=False
    ) as (host, _client):
        assert host.workspace is None
        _assert_destroy_join_degraded(fixture)

    assert adapter.destroy_calls == 0
    assert fixture["store"].pending_workspace_operations() == []


def test_unknown_exact_join_retries_three_times_and_never_calls_a_fourth(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownDestroyAdapter()
    fixture = _leave_unknown_destroy_join(state_root, adapter)
    adapter.destroy_calls = 0
    initial = json.loads(
        (state_root / "host-state.json").read_text(encoding="utf-8")
    )["requests"][fixture["store_request_id"]]
    exact_join = (
        initial["workspace_operation_id"],
        initial["workspace_join_claim_digest"],
        initial["workspace_adapter_capability_digest"],
    )

    for expected_attempt in range(1, 4):
        with running_high_risk_host(state_root, adapter=adapter) as (_host, _client):
            durable = json.loads(
                (state_root / "host-state.json").read_text(encoding="utf-8")
            )
            request = durable["requests"][fixture["store_request_id"]]
            assert request["workspace_join_attempts"]["count"] == expected_attempt
            assert request["workspace_join_attempts"]["max_attempts"] == 3
            assert (
                request["workspace_operation_id"],
                request["workspace_join_claim_digest"],
                request["workspace_adapter_capability_digest"],
            ) == exact_join
            scenario = next(iter(durable["scenarios"].values()))["record"]
            if expected_attempt < 3:
                assert request["status"] == "pending"
                assert scenario["observed_state"] == "destroying"
            else:
                assert request["status"] == "failed"
                assert scenario["observed_state"] == "degraded"
        assert adapter.destroy_calls == expected_attempt

    with running_high_risk_host(state_root, adapter=adapter):
        _assert_destroy_join_degraded(fixture)

    assert adapter.destroy_calls == 3
    assert fixture["store"].pending_workspace_operations() == []


@pytest.mark.parametrize(
    "missing_proof", ["wrong-mode", "capability", "claim-digest"]
)
def test_unprovable_exact_join_degrades_without_adapter_call(
    tmp_path: Path, missing_proof: str
) -> None:
    state_root = tmp_path / "state"
    adapter = UnknownDestroyAdapter()
    fixture = _leave_unknown_destroy_join(state_root, adapter)
    adapter.destroy_calls = 0

    if missing_proof == "wrong-mode":
        fixture["workspace_path"].chmod(0o755)
    elif missing_proof == "capability":
        adapter.idempotent_join_operations = frozenset({"recover", "repair"})
    else:
        coordinator = fixture["coordinator"]
        with coordinator._lock:  # noqa: SLF001 - exact missing-proof fixture
            durable = coordinator._read_state()  # noqa: SLF001
            binding = next(iter(durable["bindings"].values()))
            binding.pop("pending_join_claim_digest")
            durable["state_revision"] += 1
            coordinator._write_state(durable)  # noqa: SLF001

    with running_high_risk_host(state_root, adapter=adapter):
        _assert_destroy_join_degraded(fixture)

    assert adapter.destroy_calls == 0
    durable = json.loads(
        (state_root / "host-state.json").read_text(encoding="utf-8")
    )
    attempts = durable["requests"][fixture["store_request_id"]][
        "workspace_join_attempts"
    ]
    assert attempts["count"] == 0


def test_initial_destroy_binds_store_claim_before_adapter_call(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    adapter = ObservingDestroyAdapter(
        state_root / "host-state.json", "join-destroy"
    )
    fixture = _begin_ready_destroy_join(state_root, adapter)
    claim_box: dict[str, Any] = {}

    def bind_claim(claim: Mapping[str, Any]) -> None:
        claim_box.update(claim)
        _bind_store_destroy_claim(fixture, claim)

    fixture["coordinator"].destroy(
        request_id=fixture["workspace_request_id"],
        request_digest=fixture["request_digest"],
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=fixture["record"]["scenario_generation"],
        workspace_path=fixture["workspace_path"],
        expected_wip_summary_digest=fixture["preview"]["wip_summary_digest"],
        expected_binding_state=fixture["preview"]["binding_state"],
        before_external=bind_claim,
    )

    assert adapter.destroy_calls == 1
    assert adapter.store_request_before_destroy is not None
    observed = adapter.store_request_before_destroy
    assert observed["status"] == "pending"
    assert observed["workspace_operation_id"] == adapter.destroy_operation_id
    assert observed["workspace_operation_id"] == claim_box["workspace_operation_id"]
    assert observed["workspace_join_claim_digest"] == claim_box["claim_digest"]
    assert observed["workspace_adapter_capability_digest"] == claim_box[
        "adapter_capability_digest"
    ]
    assert observed["workspace_join_attempts"] == {
        "operation_id": fixture["store_operation_id"],
        "count": 0,
        "max_attempts": 3,
    }


def test_concurrent_exact_join_reconcile_invokes_destroy_and_finalizes_once(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    fixture = _leave_unknown_destroy_join(state_root, UnknownDestroyAdapter())
    adapter = BlockingDestroyAdapter()
    host = HarnessHost(state_root, tmp_path / "unused.sock")
    host.workspace = WorkspaceCoordinator(state_root, adapter)  # type: ignore[arg-type]
    host.workspace.start_host(host.store.workspace_path)

    original_pending = host.store.pending_workspace_operations
    snapshots = 0
    snapshots_lock = threading.Lock()
    second_snapshot = threading.Event()

    def observed_pending() -> list[dict[str, Any]]:
        nonlocal snapshots
        result = original_pending()
        with snapshots_lock:
            snapshots += 1
            if snapshots >= 2:
                second_snapshot.set()
        return result

    host.store.pending_workspace_operations = observed_pending  # type: ignore[method-assign]
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def reconcile() -> None:
        try:
            start.wait(timeout=3)
            host._reconcile_workspace_operations()  # noqa: SLF001
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=reconcile) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=3)
    assert adapter.entered.wait(timeout=3)
    # On the pre-fix implementation both callers capture the same stale pending
    # row before the first finalization. A serialization fix may intentionally
    # defer the second enumeration, so this wait is advisory rather than a
    # required implementation detail.
    second_snapshot.wait(timeout=0.5)
    adapter.release.set()
    for thread in threads:
        thread.join(timeout=3)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert adapter.destroy_calls == 1
    assert adapter.max_active_calls == 1
    durable = json.loads(
        (state_root / "host-state.json").read_text(encoding="utf-8")
    )
    assert durable["scenarios"] == {}
    request = durable["requests"][fixture["store_request_id"]]
    assert request["status"] == "completed"
    assert request["workspace_join_attempts"]["count"] == 1


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
        project_binding_digest=HOST_PROJECT_DIGEST,
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
        project_binding_digest=HOST_PROJECT_DIGEST,
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


def test_retryable_prepare_failure_reuses_the_frozen_plan(tmp_path: Path) -> None:
    store = ScenarioStore(tmp_path / "state")
    store.create_scenario(
        request_id="retry-create",
        request_digest="1" * 64,
        host_generation=1,
        project_instance_id="project",
        scenario_id="retry-scenario",
        project_binding_digest=HOST_PROJECT_DIGEST,
        project_contract_snapshot=HOST_PROJECT_RENDER,
    )
    record, workspace_path = store.scenario_workspace(
        "project", "retry-scenario"
    )
    coordinator = WorkspaceCoordinator(
        store.state_root, FailProvisionOnceAdapter()
    )  # type: ignore[arg-type]
    operation_id, planned = coordinator.plan(
        request_id="retry-plan-1",
        request_digest="2" * 64,
        project_instance_id="project",
        scenario_id="retry-scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    with pytest.raises(WorkspaceError) as failed:
        coordinator.provision(
            request_id="retry-provision-1",
            request_digest="3" * 64,
            project_instance_id="project",
            scenario_id="retry-scenario",
            scenario_generation=record["scenario_generation"],
            scenario_state_revision=record["state_revision"],
            plan_digest=planned["workspace"]["plan_digest"],
            workspace_path=workspace_path,
        )
    assert failed.value.code == "workspace.git-auth-required"
    assert failed.value.retryable is False

    replay_operation, replay_plan = coordinator.plan(
        request_id="retry-plan-2",
        request_digest="4" * 64,
        project_instance_id="project",
        scenario_id="retry-scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    assert replay_operation == operation_id
    assert replay_plan == planned
    _, ready = coordinator.provision(
        request_id="retry-provision-2",
        request_digest="5" * 64,
        project_instance_id="project",
        scenario_id="retry-scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    assert ready["workspace"]["state"] == "ready"


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


def test_adapter_command_preserves_a_typed_adapter_refusal(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,sys\n"
        "json.load(sys.stdin)\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'failed','result':{'error':{"
        "'code':'project.intent-invalid','message':'project intent is invalid',"
        "'retryable':False,'mutation_state':'not_started'}}},sys.stdout)\n",
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
        adapter.call("register", {})
    assert exc.value.code == "project.intent-invalid"
    assert exc.value.message == "project intent is invalid"
    assert exc.value.retryable is False


def test_adapter_command_distinguishes_a_process_crash(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text("raise SystemExit(9)\n", encoding="utf-8")
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
        adapter.call("register", {})
    assert exc.value.code == "adapter.crashed"
    assert exc.value.retryable is True


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


def test_adapter_command_rejects_render_too_large_for_process_environment(
    tmp_path: Path,
) -> None:
    script = tmp_path / "adapter.py"
    script.write_text("raise AssertionError('must not spawn')\n", encoding="utf-8")
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
        adapter.call(
            "register",
            {},
            project_render={"padding": "x" * (600 * 1024)},
        )

    assert exc.value.code == "project.render-invalid"


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
        project_binding_digest=HOST_PROJECT_DIGEST,
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


def test_restart_discards_unpublished_partial_stage_and_allows_retry(
    tmp_path: Path,
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="plan-partial-stage",
        request_digest="1" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )

    class CrashBeforeMarker(FakeAdapter):
        def call(self, operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
            if operation == "provision":
                stage = Path(payload["staging_path"])
                stage.mkdir(mode=0o700)
                (stage / "partial-clone.txt").write_text(
                    "unpublished scratch\n", encoding="utf-8"
                )
                raise SystemExit("crash before binding marker")
            return super().call(operation, payload)

    coordinator.adapter = CrashBeforeMarker()  # type: ignore[assignment]
    with pytest.raises(SystemExit, match="before binding marker"):
        coordinator.provision(
            request_id="provision-partial-stage",
            request_digest="2" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            scenario_state_revision=record["state_revision"],
            plan_digest=planned["workspace"]["plan_digest"],
            workspace_path=workspace_path,
        )
    assert any(path.name.startswith(".stage-") for path in workspace_path.iterdir())

    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    assert list(workspace_path.iterdir()) == []
    durable = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    assert next(iter(durable["bindings"].values()))["state"] == "provision_failed"

    _, ready = recovered.provision(
        request_id="provision-after-partial-stage",
        request_digest="3" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        plan_digest=planned["workspace"]["plan_digest"],
        workspace_path=workspace_path,
    )
    assert ready["workspace"]["state"] == "ready"


def test_restart_publishes_exact_stage_after_pending_digest_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="plan-pending-stage",
        request_digest="4" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    original_replace = os.replace

    def crash_before_stage_publish(source: Any, target: Any) -> None:
        if Path(source).name.startswith(".stage-") and Path(target).name == "bundle":
            raise SystemExit("crash before stage publish")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", crash_before_stage_publish)
    with pytest.raises(SystemExit, match="before stage publish"):
        coordinator.provision(
            request_id="provision-pending-stage",
            request_digest="5" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            scenario_state_revision=record["state_revision"],
            plan_digest=planned["workspace"]["plan_digest"],
            workspace_path=workspace_path,
        )
    monkeypatch.setattr(os, "replace", original_replace)

    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    assert (workspace_path / "bundle" / "component.txt").read_text(
        encoding="utf-8"
    ) == "ready\n"
    replay = recovered.completed_request("provision-pending-stage", "5" * 64)
    assert replay is not None
    assert replay[1]["workspace"]["state"] == "ready"


def test_post_rename_error_remains_pending_and_restart_proves_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, coordinator, record, workspace_path = _coordinator(tmp_path)
    _, planned = coordinator.plan(
        request_id="plan-post-rename",
        request_digest="6" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    original_replace = os.replace

    def publish_then_report_error(source: Any, target: Any) -> None:
        original_replace(source, target)
        if Path(source).name.startswith(".stage-") and Path(target).name == "bundle":
            raise OSError("injected post-rename directory failure")

    monkeypatch.setattr(os, "replace", publish_then_report_error)
    with pytest.raises(WorkspaceError) as unknown:
        coordinator.provision(
            request_id="provision-post-rename",
            request_digest="7" * 64,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            scenario_state_revision=record["state_revision"],
            plan_digest=planned["workspace"]["plan_digest"],
            workspace_path=workspace_path,
        )
    assert unknown.value.code == "workspace.publish-outcome-unknown"
    assert unknown.value.mutation_state == "unknown"
    durable = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    assert next(iter(durable["bindings"].values()))["state"] == "provisioning"
    assert (workspace_path / "bundle").is_dir()

    monkeypatch.setattr(os, "replace", original_replace)
    recovered = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    recovered.start_host(store.workspace_root)
    replay = recovered.completed_request("provision-post-rename", "7" * 64)
    assert replay is not None
    assert replay[1]["workspace"]["state"] == "ready"


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
        project_binding_digest=HOST_PROJECT_DIGEST,
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
        expected_binding_state=destroy_preview["binding_state"],
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
        project_binding_digest=HOST_PROJECT_DIGEST,
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


def test_workspace_provision_streams_validated_component_progress(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(
        state_root, adapter=ProgressFakeAdapter()
    ) as (_host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="progress-room",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="progress-create",
        )["scenario"]
        planned = client.plan_workspace(
            project_instance_id="project",
            scenario_id="progress-room",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            requested_component_ids=[],
            project_payload={},
            request_id="progress-plan",
        )["workspace"]
        observed: list[dict[str, Any]] = []
        ready = client.provision_workspace(
            project_instance_id="project",
            scenario_id="progress-room",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            plan_digest=planned["plan_digest"],
            request_id="progress-provision",
            progress_callback=observed.append,
        )
        assert ready["workspace"]["state"] == "ready"

    assert [event["sequence"] for event in observed] == list(range(10))
    assert [event["progress"]["component_state"] for event in observed] == [
        "waiting",
        "waiting",
        "waiting",
        "cloning",
        "ready",
        "cloning",
        "ready",
        "building",
        "ready",
        "complete",
    ]
    assert [
        event["progress"]["component_kind"] for event in observed[:3]
    ] == ["repository", "repository", "environment"]
    assert observed[-1]["state"] == "completed"
    assert observed[-1]["progress"]["completed_units"] == 3
    assert all(
        event["progress"]["progress_kind"] == "workspace-component-v1"
        for event in observed
    )
    serialized = json.dumps(observed)
    assert str(tmp_path) not in serialized
    assert "remote" not in serialized


def test_workspace_prepare_cli_emits_component_progress_to_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(
        state_root, adapter=ProgressFakeAdapter()
    ) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="cli-progress-room",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="cli-progress-create",
        )["scenario"]
        assert (
            cli_main.main(
                [
                    "harness",
                    "workspace",
                    "prepare",
                    "cli-progress-room",
                    "--project-instance-id",
                    "project",
                    "--scenario-generation",
                    str(created["scenario_generation"]),
                    "--state-revision",
                    str(created["state_revision"]),
                    "--progress",
                    "--state-root",
                    str(state_root),
                    "--socket-path",
                    str(host.socket_path),
                    "--json",
                ]
            )
            == 0
        )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    progress = [json.loads(line) for line in captured.err.splitlines()]
    assert result["workspace"]["state"] == "ready"
    assert len(progress) == 10
    assert progress[0]["progress"]["component_state"] == "waiting"
    assert progress[-1]["state"] == "completed"


def test_repository_failure_stops_provision_and_preserves_waiting_rows(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(
        state_root, adapter=FailFastProgressAdapter()
    ) as (_host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="fail-fast-room",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="fail-fast-create",
        )["scenario"]
        planned = client.plan_workspace(
            project_instance_id="project",
            scenario_id="fail-fast-room",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            requested_component_ids=[],
            project_payload={},
            request_id="fail-fast-plan",
        )["workspace"]
        observed: list[dict[str, Any]] = []
        with pytest.raises(HarnessClientError) as raised:
            client.provision_workspace(
                project_instance_id="project",
                scenario_id="fail-fast-room",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                plan_digest=planned["plan_digest"],
                request_id="fail-fast-provision",
                progress_callback=observed.append,
            )
        assert raised.value.code == "workspace.git-auth-required"
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        assert list(workspace_path.iterdir()) == []

    assert [event["progress"]["component_state"] for event in observed] == [
        "waiting",
        "waiting",
        "waiting",
        "waiting",
        "cloning",
        "ready",
        "cloning",
        "failed",
    ]
    assert not any(
        event["progress"]["component_id"] == "repo-c"
        and event["progress"]["component_state"] != "waiting"
        for event in observed
    )
    assert not any(
        event["progress"]["component_kind"] == "environment"
        and event["progress"]["component_state"] == "building"
        for event in observed
    )


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
        # The workspace container itself is removed too: after a committed
        # destroy nothing of the Scenario remains on disk, not even the empty
        # directory husk.
        assert not workspace_path.exists()
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


def test_closed_unprovisioned_scenario_has_a_confirmed_destroy_exit(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (_host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="never-planned",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-never-planned",
        )["scenario"]
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        assert list(workspace_path.iterdir()) == []

        preview = client.preview_destroy_scenario(
            project_instance_id="project",
            scenario_id="never-planned",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
        )["effect_preview"]
        assert preview["eligible"] is True
        assert preview["workspace"]["state"] == "unprovisioned"
        assert preview["workspace"]["binding_state"] == "absent"
        assert preview["workspace"]["receipt_digest"] is None
        assert preview["workspace"]["canonical_source_wip_mutation"] is False

        destroyed = client.destroy_scenario(
            project_instance_id="project",
            scenario_id="never-planned",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="destroy-never-planned",
        )
        assert destroyed["unregistered"] is True
        assert not workspace_path.exists()
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }

    workspace_state = json.loads(
        (state_root / "workspace-execution.json").read_text(encoding="utf-8")
    )
    assert workspace_state["bindings"] == {}
    history = next(iter(workspace_state["history"].values()))
    assert history["binding_state_before_destroy"] == "absent"
    evidence = history["unprovisioned_destroy_evidence"]
    assert evidence["operation_kind"] == "destroy-unprovisioned"
    assert evidence["binding_state_before"] == "absent"


def test_empty_provision_failure_can_be_destroyed_without_adapter_cleanup(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(
        state_root, adapter=FailProvisionOnceAdapter()
    ) as (_host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="failed-before-publish",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-failed-before-publish",
        )["scenario"]
        planned = client.plan_workspace(
            project_instance_id="project",
            scenario_id="failed-before-publish",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            requested_component_ids=[],
            project_payload={},
            request_id="plan-failed-before-publish",
        )["workspace"]
        with pytest.raises(HarnessClientError) as failed:
            client.provision_workspace(
                project_instance_id="project",
                scenario_id="failed-before-publish",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                plan_digest=planned["plan_digest"],
                request_id="provision-failed-before-publish",
            )
        assert failed.value.code == "workspace.git-auth-required"
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        assert list(workspace_path.iterdir()) == []

        preview = client.preview_destroy_scenario(
            project_instance_id="project",
            scenario_id="failed-before-publish",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
        )["effect_preview"]
        assert preview["eligible"] is True
        assert preview["workspace"]["binding_state"] == "provision_failed"
        client.destroy_scenario(
            project_instance_id="project",
            scenario_id="failed-before-publish",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="destroy-failed-before-publish",
        )
        assert not workspace_path.exists()


def test_unprovisioned_destroy_resumes_after_store_intent_restart(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="restart-before-workspace-destroy",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-restart-before-destroy",
        )["scenario"]
        record, workspace_path = host.store.scenario_workspace(
            "project", "restart-before-workspace-destroy"
        )
        preview, _ = host.workspace.high_risk_context(  # type: ignore[union-attr]
            project_instance_id="project",
            scenario_id="restart-before-workspace-destroy",
            scenario_generation=record["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.destroy",
        )
        operation_id, replay, _ = host.store.begin_scenario_destroy(
            request_id="destroy-restart-before-workspace",
            request_digest="a" * 64,
            host_generation=host.host_generation,
            project_instance_id="project",
            scenario_id="restart-before-workspace-destroy",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            expected_workspace_binding_state=preview["binding_state"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
        )
        assert replay is None
        pending = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert pending["operations"][operation_id]["state"] == (
            "executing_external"
        )
        assert workspace_path.exists()

    with running_high_risk_host(state_root) as (_host, client):
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }
        assert not workspace_path.exists()


def test_unprovisioned_destroy_finalize_failure_stays_pending_for_exact_retry(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="finalize-retry",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-finalize-retry",
        )["scenario"]
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        original_write = host.store._write_state  # noqa: SLF001
        failed_once = False

        def fail_before_finalize_publish(value: dict[str, Any]) -> None:
            nonlocal failed_once
            request = value["requests"].get("destroy-finalize-retry")
            if (
                not failed_once
                and request is not None
                and request["status"] == "completed"
            ):
                failed_once = True
                raise OSError("injected finalize publication failure")
            original_write(value)

        host.store._write_state = fail_before_finalize_publish  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as pending:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="finalize-retry",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="destroy-finalize-retry",
            )
        assert pending.value.code == "operation.internal-failure"
        assert pending.value.retryable is True
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        scenario = next(iter(durable["scenarios"].values()))["record"]
        assert scenario["observed_state"] == "destroying"
        assert scenario["active_operation_id"] is not None

        host.store._write_state = original_write  # type: ignore[method-assign]  # noqa: SLF001
        replayed = client.destroy_scenario(
            project_instance_id="project",
            scenario_id="finalize-retry",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="destroy-finalize-retry",
        )
        assert replayed["unregistered"] is True
        assert not workspace_path.exists()


def test_unprovisioned_destroy_binding_race_aborts_closed_and_requires_reconfirm(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="binding-race",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-binding-race",
        )["scenario"]
        assert host.workspace is not None
        original_destroy = host.workspace.destroy

        def plan_before_destroy(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            host.workspace.plan(
                request_id="racing-plan",
                request_digest="b" * 64,
                project_instance_id="project",
                scenario_id="binding-race",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                workspace_id=created["workspace_binding_id"],
                project_binding_digest=HOST_PROJECT_DIGEST,
                requested_component_ids=[],
                project_payload={},
            )
            return original_destroy(**kwargs)

        host.workspace.destroy = plan_before_destroy  # type: ignore[method-assign]
        with pytest.raises(HarnessClientError) as raced:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="binding-race",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="destroy-binding-race",
            )
        assert raced.value.code == "operation.precondition-failed"
        assert raced.value.retryable is True
        restored = client.scenario_status(
            project_instance_id="project", scenario_id="binding-race"
        )["scenario"]
        assert restored["desired_state"] == "closed"
        assert restored["observed_state"] == "closed"
        assert restored["active_operation_id"] is None

        with pytest.raises(HarnessClientError) as replayed:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="binding-race",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="destroy-binding-race",
            )
        assert replayed.value.code == raced.value.code
        assert replayed.value.retryable == raced.value.retryable

        host.workspace.destroy = original_destroy  # type: ignore[method-assign]
        client.destroy_scenario(
            project_instance_id="project",
            scenario_id="binding-race",
            scenario_generation=restored["scenario_generation"],
            scenario_state_revision=restored["state_revision"],
            request_id="destroy-binding-race-reconfirmed",
        )
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }


def test_unprovisioned_destroy_content_race_preserves_bytes_and_replays_error(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="content-race",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-content-race",
        )["scenario"]
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        assert host.workspace is not None
        original_destroy = host.workspace.destroy
        unexpected = workspace_path / "arrived-after-confirmation.txt"

        def write_before_destroy(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            unexpected.write_text("never delete\n", encoding="utf-8")
            return original_destroy(**kwargs)

        host.workspace.destroy = write_before_destroy  # type: ignore[method-assign]
        with pytest.raises(HarnessClientError) as first:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="content-race",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="destroy-content-race",
            )
        assert first.value.code == "operation.precondition-failed"
        assert first.value.retryable is True
        assert unexpected.read_text(encoding="utf-8") == "never delete\n"
        restored = client.scenario_status(
            project_instance_id="project", scenario_id="content-race"
        )["scenario"]
        assert restored["observed_state"] == "closed"

        with pytest.raises(HarnessClientError) as replayed:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="content-race",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="destroy-content-race",
            )
        assert replayed.value.code == first.value.code
        assert replayed.value.retryable == first.value.retryable
        assert unexpected.read_text(encoding="utf-8") == "never delete\n"


def test_unprovisioned_finalize_never_removes_post_confirmation_metadata(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="metadata-race",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-metadata-race",
        )["scenario"]
        record, workspace_path = host.store.scenario_workspace(
            "project", "metadata-race"
        )
        preview, _ = host.workspace.high_risk_context(  # type: ignore[union-attr]
            project_instance_id="project",
            scenario_id="metadata-race",
            scenario_generation=record["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.destroy",
        )
        operation_id, _, _ = host.store.begin_scenario_destroy(
            request_id="destroy-metadata-race",
            request_digest="c" * 64,
            host_generation=host.host_generation,
            project_instance_id="project",
            scenario_id="metadata-race",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            expected_workspace_binding_state=preview["binding_state"],
            expected_wip_summary_digest=preview["wip_summary_digest"],
        )
        assert host.workspace is not None
        _, workspace_result = host.workspace.destroy(
            request_id="destroy-metadata-race",
            request_digest="c" * 64,
            project_instance_id="project",
            scenario_id="metadata-race",
            scenario_generation=created["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=preview["wip_summary_digest"],
            expected_binding_state=preview["binding_state"],
        )
        metadata = workspace_path / ".DS_Store"
        metadata.write_bytes(b"preserve exact post-confirmation bytes")
        host.store.finalize_scenario_destroy(
            project_instance_id="project",
            scenario_id="metadata-race",
            request_id="destroy-metadata-race",
            operation_id=operation_id,
            workspace_evidence_sha256=canonical_json_sha256(workspace_result),
        )
        assert metadata.read_bytes() == b"preserve exact post-confirmation bytes"


def test_unprovisioned_destroy_preserves_any_unexpected_entry(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (_host, client):
        created = client.create_scenario(
            project_instance_id="project",
            scenario_id="unexpected-entry",
            project_binding_digest=HOST_PROJECT_DIGEST,
            request_id="create-unexpected-entry",
        )["scenario"]
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        unexpected = workspace_path / "keep-me.txt"
        unexpected.write_text("never delete\n", encoding="utf-8")

        with pytest.raises(HarnessClientError) as preview_failed:
            client.preview_destroy_scenario(
                project_instance_id="project",
                scenario_id="unexpected-entry",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
            )
        assert preview_failed.value.code == "operation.precondition-failed"
        with pytest.raises(HarnessClientError) as force_failed:
            client.force_destroy_scenario(
                project_instance_id="project",
                scenario_id="unexpected-entry",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="force-unexpected-entry",
            )
        assert force_failed.value.code == "operation.precondition-failed"
        assert unexpected.read_text(encoding="utf-8") == "never delete\n"
        assert client.list_scenarios(project_instance_id="project")["scenarios"]


@pytest.mark.parametrize("binding_state", ["provisioning", "repairing", "destroying"])
def test_unprovisioned_destroy_state_allowlist_fails_closed(
    tmp_path: Path, binding_state: str
) -> None:
    _store, coordinator, record, workspace_path = _coordinator(tmp_path)
    coordinator.plan(
        request_id=f"plan-{binding_state}",
        request_digest="1" * 64,
        project_instance_id="project",
        scenario_id="scenario",
        scenario_generation=record["scenario_generation"],
        scenario_state_revision=record["state_revision"],
        workspace_id=record["workspace_binding_id"],
        project_binding_digest=HOST_PROJECT_DIGEST,
        requested_component_ids=[],
        project_payload={},
    )
    with coordinator._lock:  # noqa: SLF001 - exact invalid-state witness
        durable = coordinator._read_state()  # noqa: SLF001
        next(iter(durable["bindings"].values()))["state"] = binding_state
        durable["state_revision"] += 1
        coordinator._write_state(durable)  # noqa: SLF001

    with pytest.raises(WorkspaceError) as rejected:
        coordinator.high_risk_context(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=record["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.destroy",
        )
    assert rejected.value.code == "workspace.not-ready"


def test_high_risk_precondition_failure_names_its_blockers(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (_host, client):
        created, _ = _provision_host_workspace(client)
        opened = client.open_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="blocker-detail-open",
        )["scenario"]

        with pytest.raises(HarnessClientError) as rejected:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                request_id="blocker-detail-destroy",
            )

    assert rejected.value.code == "operation.precondition-failed"
    # The caller must be able to tell which prerequisite failed; an unaligned
    # workspace and an open Scenario need different recovery steps.
    assert "scenario.not-closed" in str(rejected.value)


def test_force_destroy_removes_a_scenario_whose_workspace_drifted(
    tmp_path: Path,
) -> None:
    """The escape hatch must stay open exactly when the Scenario is broken.

    A workspace whose environment fingerprint drifted - for example because an
    App update swapped the embedded interpreter every Scenario venv links to -
    observes as degraded. Requiring alignment before a force destroy closed the
    only exit at the moment it was needed, leaving a Scenario that could not be
    removed from any entry point.
    """
    state_root = tmp_path / "state"
    adapter = FakeAdapter()
    with running_high_risk_host(state_root, adapter=adapter) as (host, client):
        created, _ = _provision_host_workspace(client)
        opened = client.open_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="drifted-open",
        )["scenario"]
        host.participants = EmptyForceCloseCoordinator()  # type: ignore[assignment]

        # The workspace drifts after it was provisioned.
        adapter.observed_state = "degraded"
        adapter.drift_codes = ["environment.content-drift"]

        client.force_destroy_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="drifted-force-destroy",
        )

        assert client.list_scenarios(project_instance_id="project")["scenarios"] == []


def test_force_destroy_removes_ready_scenario_after_workspace_container_disappeared(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        assert host.workspace is not None
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        shutil.rmtree(workspace_path)

        preview, subject = host.workspace.high_risk_context(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            workspace_path=workspace_path,
            operation="scenario.force-destroy",
        )
        assert preview["state"] == "missing"
        assert preview["binding_state"] == "ready"
        assert subject["subject_kind"] == "empty-project-storage"

        result = client.force_destroy_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-missing-ready",
        )

        assert result["unregistered"] is True
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }
        workspace_state = json.loads(
            (state_root / "workspace-execution.json").read_text(encoding="utf-8")
        )
        history = next(iter(workspace_state["history"].values()))
        evidence = history["unprovisioned_destroy_evidence"]
        assert evidence["binding_state_before"] == "ready"
        assert len(evidence["husk_digest"]) == 64


def test_force_destroy_missing_ready_workspace_restarts_after_finalize_failure(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (_host, client):
        created, _ = _provision_host_workspace(client)
        workspace_path = (
            state_root / "workspaces" / created["workspace_binding_id"]
        )
        shutil.rmtree(workspace_path)
        host = _host
        original_write = host.store._write_state  # noqa: SLF001
        failed_once = False

        def fail_before_finalize_publish(value: dict[str, Any]) -> None:
            nonlocal failed_once
            request = value["requests"].get("force-missing-finalize")
            if (
                not failed_once
                and request is not None
                and request["status"] == "completed"
            ):
                failed_once = True
                raise OSError("injected force missing finalize failure")
            original_write(value)

        host.store._write_state = fail_before_finalize_publish  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as pending:
            client.force_destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="force-missing-finalize",
            )
        assert pending.value.code == "operation.internal-failure"
        assert pending.value.retryable is True
        host.store._write_state = original_write  # type: ignore[method-assign]  # noqa: SLF001

    with running_high_risk_host(state_root) as (_host, client):
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }


def test_force_destroy_begin_prepublication_failure_is_no_effect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        original_write = host.store._write_state  # noqa: SLF001

        def fail_before_publish(value: dict[str, Any]) -> None:
            if value["requests"].get("force-begin-not-published") is not None:
                raise OSError("injected force begin pre-publication failure")
            original_write(value)

        host.store._write_state = fail_before_publish  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as refused:
            client.force_destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="force-begin-not-published",
            )
        assert refused.value.code == "operation.precondition-failed"
        assert refused.value.mutation_state == "not_started"
        assert host.store.pending_workspace_operations() == []
        remaining = client.scenario_status(
            project_instance_id="project", scenario_id="scenario"
        )["scenario"]
        assert remaining["observed_state"] == "closed"


def test_force_destroy_begin_postpublication_failure_restarts_exactly(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        original_write = host.store._write_state  # noqa: SLF001
        failed_once = False

        def publish_then_fail(value: dict[str, Any]) -> None:
            nonlocal failed_once
            request = value["requests"].get("force-begin-published")
            if not failed_once and request is not None:
                failed_once = True
                original_write(value)
                raise OSError("injected force begin post-publication failure")
            original_write(value)

        host.store._write_state = publish_then_fail  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as unknown:
            client.force_destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="force-begin-published",
            )
        assert unknown.value.code == "operation.internal-failure"
        assert unknown.value.mutation_state == "unknown"
        pending = host.store.pending_workspace_operations()
        assert len(pending) == 1
        assert unknown.value.retryable is True
        host.store._write_state = original_write  # type: ignore[method-assign]  # noqa: SLF001

    with running_high_risk_host(state_root) as (restarted, client):
        assert restarted.store.pending_workspace_operations() == []
        assert client.list_scenarios(project_instance_id="project") == {
            "scenarios": []
        }


def test_conservative_destroy_still_refuses_a_drifted_workspace(
    tmp_path: Path,
) -> None:
    """Only the forced path is relaxed; the safe path still fails closed."""
    state_root = tmp_path / "state"
    adapter = FakeAdapter()
    with running_high_risk_host(state_root, adapter=adapter) as (host, client):
        created, _ = _provision_host_workspace(client)
        opened = client.open_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="safe-drifted-open",
        )["scenario"]
        host.participants = EmptyForceCloseCoordinator()  # type: ignore[assignment]
        adapter.observed_state = "degraded"
        adapter.drift_codes = ["environment.content-drift"]

        with pytest.raises(HarnessClientError) as rejected:
            client.destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                request_id="safe-drifted-destroy",
            )

        assert rejected.value.code == "operation.precondition-failed"
        assert "workspace.not-aligned" in str(rejected.value)
        remaining = client.list_scenarios(project_instance_id="project")["scenarios"]
        assert [item["scenario_id"] for item in remaining] == ["scenario"]


def test_force_destroy_with_a_stale_revision_is_a_distinct_fence_failure(
    tmp_path: Path,
) -> None:
    """A caller holding an out-of-date revision must not look like a blocked one.

    The two failures need opposite responses: refresh and retry, versus repair
    the Scenario. They are only distinguishable by their error code, so pin
    that a stale revision never reports itself as a precondition failure.
    """
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        opened = client.open_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="stale-open",
        )["scenario"]
        host.participants = EmptyForceCloseCoordinator()  # type: ignore[assignment]

        stale_revision = created["state_revision"]
        assert stale_revision != opened["state_revision"]

        with pytest.raises(HarnessClientError) as rejected:
            client.force_destroy_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=stale_revision,
                request_id="stale-force-destroy",
            )

    assert rejected.value.code != "operation.precondition-failed"
    assert "fence" in rejected.value.code or "stale" in rejected.value.code


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


def test_repair_store_begin_prepublication_failure_is_proven_no_effect(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        with host.store._lock:  # noqa: SLF001 - durable fault fixture
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

        original_write = host.store._write_state  # noqa: SLF001

        def fail_before_publish(value: dict[str, Any]) -> None:
            if value["requests"].get("repair-intent-not-published") is not None:
                raise OSError("injected Store pre-publication failure")
            original_write(value)

        host.store._write_state = fail_before_publish  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as refused:
            client.repair_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=degraded["scenario_generation"],
                scenario_state_revision=degraded["state_revision"],
                request_id="repair-intent-not-published",
            )
        assert refused.value.code == "operation.precondition-failed"
        assert refused.value.mutation_state == "not_started"
        assert host.store.pending_workspace_operations() == []
        assert host.security is not None
        security_state = json.loads(
            host.security.state_path.read_text(encoding="utf-8")
        )
        chain = next(iter(security_state["chains"].values()))
        assert chain["operation_outcome"] == {
            "outcome": "failed",
            "operation_id": None,
            "result_digest": None,
        }


def test_repair_store_begin_publication_ambiguity_replays_as_unknown(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        with host.store._lock:  # noqa: SLF001 - durable fault fixture
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

        original_write = host.store._write_state  # noqa: SLF001
        failed_once = False

        def publish_then_fail(value: dict[str, Any]) -> None:
            nonlocal failed_once
            request = value["requests"].get("repair-intent-ambiguous")
            if not failed_once and request is not None:
                failed_once = True
                original_write(value)
                raise OSError("injected Store post-publication failure")
            original_write(value)

        host.store._write_state = publish_then_fail  # type: ignore[method-assign]  # noqa: SLF001
        with pytest.raises(HarnessClientError) as unknown:
            client.repair_scenario(
                project_instance_id="project",
                scenario_id="scenario",
                scenario_generation=degraded["scenario_generation"],
                scenario_state_revision=degraded["state_revision"],
                request_id="repair-intent-ambiguous",
            )
        assert unknown.value.code == "operation.internal-failure"
        assert unknown.value.retryable is True
        pending = host.store.pending_workspace_operations()
        assert len(pending) == 1
        assert pending[0]["workspace_operation_id"] is None

        host.store._write_state = original_write  # type: ignore[method-assign]  # noqa: SLF001

    with running_high_risk_host(state_root) as (restarted, client):
        repaired = client.scenario_status(
            project_instance_id="project", scenario_id="scenario"
        )["scenario"]
        assert repaired["observed_state"] == "closed"
        assert repaired["degraded"] is None
        assert restarted.store.pending_workspace_operations() == []


def test_repair_begin_ambiguity_adopts_concurrent_terminal_result(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_high_risk_host(state_root) as (host, client):
        created, _ = _provision_host_workspace(client)
        with host.store._lock:  # noqa: SLF001 - durable fault fixture
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

        original_write = host.store._write_state  # noqa: SLF001
        original_inspect = host.store.inspect_request_status
        write_failed = False
        reconciled = False

        def publish_then_fail(value: dict[str, Any]) -> None:
            nonlocal write_failed
            request = value["requests"].get("repair-intent-concurrent")
            if not write_failed and request is not None:
                write_failed = True
                original_write(value)
                raise OSError("injected Store post-publication failure")
            original_write(value)

        def inspect_then_reconcile(
            request_id: str, request_digest: str
        ) -> tuple[str, str | None]:
            nonlocal reconciled
            stale = original_inspect(request_id, request_digest)
            if not reconciled and stale[0] == "pending":
                reconciled = True
                host._reconcile_workspace_operations()
            return stale

        host.store._write_state = publish_then_fail  # type: ignore[method-assign]  # noqa: SLF001
        host.store.inspect_request_status = inspect_then_reconcile  # type: ignore[method-assign]
        repaired = client.repair_scenario(
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
            request_id="repair-intent-concurrent",
        )["scenario"]
        assert reconciled is True
        assert repaired["observed_state"] == "closed"
        assert host.store.pending_workspace_operations() == []


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
            expected_wip_summary_digest=preview["wip_summary_digest"],
        )
        assert replay is None
        assert pending_path == workspace_path
        workspace_request_id = host.store.workspace_join_request_id(
            "scenario.repair",
            "repair-crash-window",
            request_digest,
        )
        host.workspace.repair(  # type: ignore[union-attr]
            request_id=workspace_request_id,
            request_digest=request_digest,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=degraded["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=preview["wip_summary_digest"],
            before_external=lambda claim: host.store.bind_workspace_execution_claim(
                project_instance_id="project",
                scenario_id="scenario",
                request_id="repair-crash-window",
                request_digest=request_digest,
                operation_id=operation_id,
                workspace_request_id=workspace_request_id,
                operation_kind="scenario.repair",
                scenario_generation=degraded["scenario_generation"],
                workspace_claim=dict(claim),
            ),
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
            expected_workspace_binding_state=destroy_preview[
                "binding_state"
            ],
            expected_wip_summary_digest=destroy_preview[
                "wip_summary_digest"
            ],
        )
        assert replay is None
        assert pending_path == workspace_path
        workspace_request_id = host.store.workspace_join_request_id(
            "scenario.destroy",
            "destroy-crash-window",
            request_digest,
        )
        host.workspace.destroy(  # type: ignore[union-attr]
            request_id=workspace_request_id,
            request_digest=request_digest,
            project_instance_id="project",
            scenario_id="scenario",
            scenario_generation=repaired["scenario_generation"],
            workspace_path=workspace_path,
            expected_wip_summary_digest=destroy_preview["wip_summary_digest"],
            expected_binding_state=destroy_preview["binding_state"],
            before_external=lambda claim: host.store.bind_workspace_execution_claim(
                project_instance_id="project",
                scenario_id="scenario",
                request_id="destroy-crash-window",
                request_digest=request_digest,
                operation_id=operation_id,
                workspace_request_id=workspace_request_id,
                operation_kind="scenario.destroy",
                scenario_generation=repaired["scenario_generation"],
                workspace_claim=dict(claim),
            ),
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
        project_binding_digest=HOST_PROJECT_DIGEST,
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
    # Workspace startup never replays a mutation from its ledger alone.  A
    # Host-authorized exact join is required even in this coordinator-level
    # crash fixture.
    repair_claim = recovered.inspect_pending_high_risk_join(
        workspace_request_id="recovery-repair",
        request_digest="3" * 64,
        workspace_path=workspace_path,
    )
    assert repair_claim is not None
    recovered.resume_exact_high_risk_join(
        workspace_claim=repair_claim,
        workspace_path=workspace_path,
        before_external=lambda _claim: None,
    )
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
            expected_binding_state=destroy_preview["binding_state"],
        )
    assert not (workspace_path / "bundle").exists()
    durable = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    assert next(iter(durable["bindings"].values()))["state"] == "destroying"

    restarted = WorkspaceCoordinator(store.state_root, FakeAdapter())  # type: ignore[arg-type]
    restarted.start_host(store.workspace_root)
    destroy_claim = restarted.inspect_pending_high_risk_join(
        workspace_request_id="recovery-destroy",
        request_digest="4" * 64,
        workspace_path=workspace_path,
    )
    assert destroy_claim is not None
    restarted.resume_exact_high_risk_join(
        workspace_claim=destroy_claim,
        workspace_path=workspace_path,
        before_external=lambda _claim: None,
    )
    destroy_replay = restarted.completed_request("recovery-destroy", "4" * 64)
    assert destroy_replay is not None
    assert destroy_replay[1]["workspace"]["state"] == "missing"
    durable = json.loads(restarted.state_path.read_text(encoding="utf-8"))
    assert durable["bindings"] == {}
    assert len(durable["history"]) == 1


def test_workspace_restart_preserves_unknown_high_risk_fence_for_exact_retry(
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
        project_binding_digest=HOST_PROJECT_DIGEST,
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
    assert binding["state"] == "repairing"
    assert binding["pending_operation_id"] == "wsop-mismatch"
    assert durable["requests"]["mismatch-repair"]["status"] == "pending"
    assert recovered.completed_request("mismatch-repair", "7" * 64) is None


def test_workspace_husk_removal_is_fail_closed(tmp_path: Path) -> None:
    """Only a provably empty workspace container is removed; Finder's
    .DS_Store metadata does not count as content, anything else does."""
    store = ScenarioStore(tmp_path / "state", workspace_root=tmp_path / "ws")

    empty = store.workspace_path("workspace-husk-empty")
    empty.mkdir(parents=True, mode=0o700)
    (empty / ".DS_Store").write_bytes(b"finder metadata")
    assert store._remove_workspace_husk("workspace-husk-empty") is True
    assert not empty.exists()

    occupied = store.workspace_path("workspace-husk-occupied")
    occupied.mkdir(parents=True, mode=0o700)
    (occupied / "real-content.txt").write_text("keep me\n", encoding="utf-8")
    assert store._remove_workspace_husk("workspace-husk-occupied") is False
    assert (occupied / "real-content.txt").is_file()

    assert store._remove_workspace_husk("workspace-husk-absent") is False
    assert store._remove_workspace_husk(None) is False
    assert store._remove_workspace_husk("../escape") is False


def test_declared_adapter_progress_fd_is_bounded_and_ordered(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,os,sys\n"
        "request=json.load(sys.stdin)\n"
        "fd=int(os.environ['AI_COLLAB_PROGRESS_FD'])\n"
        "events=["
        "{'component_id':'repo-a','index':0,'total':2,'state':'waiting'},"
        "{'component_id':'environment:1','index':1,'total':2,'state':'waiting'},"
        "{'component_id':'repo-a','index':0,'total':2,'state':'cloning'},"
        "{'component_id':'repo-a','index':0,'total':2,'state':'ready'},"
        "{'component_id':'environment:1','index':1,'total':2,'state':'building'},"
        "{'component_id':'environment:1','index':1,'total':2,'state':'ready'}]\n"
        "for event in events: os.write(fd,(json.dumps(event,sort_keys=True,separators=(',',':'))+'\\n').encode())\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'completed','result':{}},sys.stdout)\n",
        encoding="utf-8",
    )
    config = tmp_path / "adapter.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_id": "test-adapter",
                "command": ["python3", "adapter.py"],
                "progress_side_channel": "v1",
                "working_directory": ".",
            }
        ),
        encoding="utf-8",
    )
    adapter = ProjectAdapterCommand(config)
    observed: list[dict[str, Any]] = []
    result = adapter.call(
        "provision",
        {
            "plan": {
                "components": [{"component_id": "repo-a"}],
                "environment": {"environment_id": "environment:1"},
            }
        },
        progress_callback=observed.append,
    )
    assert result == {}
    assert [(event["component_id"], event["state"]) for event in observed] == [
        ("repo-a", "waiting"),
        ("environment:1", "waiting"),
        ("repo-a", "cloning"),
        ("repo-a", "ready"),
        ("environment:1", "building"),
        ("environment:1", "ready"),
    ]


def test_adapter_progress_fd_requires_an_explicit_v1_capability(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,os,sys\n"
        "json.load(sys.stdin)\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'completed','result':{'progress_fd_present':"
        "'AI_COLLAB_PROGRESS_FD' in os.environ}},sys.stdout)\n",
        encoding="utf-8",
    )
    config = tmp_path / "adapter.json"
    base = {
        "schema_version": 1,
        "adapter_id": "test-adapter",
        "command": ["python3", "adapter.py"],
        "working_directory": ".",
    }
    config.write_text(json.dumps(base), encoding="utf-8")
    adapter = ProjectAdapterCommand(config)
    observed: list[dict[str, Any]] = []
    assert adapter.call("provision", {}, progress_callback=observed.append) == {
        "progress_fd_present": False
    }
    assert observed == []

    config.write_text(
        json.dumps({**base, "progress_side_channel": "v2"}), encoding="utf-8"
    )
    with pytest.raises(WorkspaceError) as raised:
        ProjectAdapterCommand(config)
    assert raised.value.code == "adapter.config-invalid"


def test_completed_adapter_reply_rejects_invalid_progress(tmp_path: Path) -> None:
    script = tmp_path / "adapter.py"
    script.write_text(
        "import json,os,sys\n"
        "json.load(sys.stdin)\n"
        "event={'component_id':'repo-a','index':0,'total':2,'state':'ready','path':'/private/leak'}\n"
        "os.write(int(os.environ['AI_COLLAB_PROGRESS_FD']),(json.dumps(event)+'\\n').encode())\n"
        "json.dump({'adapter_protocol_version':1,'adapter_id':'test-adapter',"
        "'outcome':'completed','result':{}},sys.stdout)\n",
        encoding="utf-8",
    )
    config = tmp_path / "adapter.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_id": "test-adapter",
                "command": ["python3", "adapter.py"],
                "progress_side_channel": "v1",
                "working_directory": ".",
            }
        ),
        encoding="utf-8",
    )
    adapter = ProjectAdapterCommand(config)
    observed: list[dict[str, Any]] = []
    with pytest.raises(WorkspaceError) as raised:
        adapter.call(
            "provision",
            {
                "plan": {
                    "components": [{"component_id": "repo-a"}],
                    "environment": {"environment_id": "environment:1"},
                }
            },
            progress_callback=observed.append,
        )
    assert raised.value.code == "adapter.progress-invalid"
    assert raised.value.mutation_state == "started"
    assert observed == []
