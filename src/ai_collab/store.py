# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Durable Scenario state and lifecycle journal for the first Harness slice."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import (
    ProtocolError,
    canonical_json_bytes,
    canonical_json_sha256,
    validate_runtime_launch_spec,
)


STATE_SCHEMA_VERSION = 1
RESOURCE_LEASE_SCHEMA_VERSION = 1
RESOURCE_CLASSES = {
    "port",
    "device",
    "compute",
    "accelerator",
    "exclusive_runtime",
}


@dataclass
class StoreError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class OperationFailed(ValueError):
    operation_id: str
    code: str
    message: str
    mutation_state: str
    retryable: bool

    def __str__(self) -> str:
        return self.message


class ScenarioStore:
    """Atomic single-process store with persisted request idempotency."""

    def __init__(self, state_root: Path, workspace_root: Path | None = None):
        supplied_root = Path(state_root).expanduser()
        if supplied_root.is_symlink():
            raise StoreError("host.state-root-invalid", "state root must not be a symlink")
        self.state_root = supplied_root.resolve()
        self.state_path = self.state_root / "host-state.json"
        self.capability_path = self.state_root / "owner-capability"
        self.legacy_workspace_root = self.state_root / "workspaces"
        supplied_workspace_root = (
            self.legacy_workspace_root
            if workspace_root is None
            else Path(workspace_root).expanduser()
        )
        if supplied_workspace_root.is_symlink():
            raise StoreError(
                "host.workspace-root-invalid", "workspace root must not be a symlink"
            )
        self.workspace_root = supplied_workspace_root.resolve()
        self.participant_root = self.state_root / "participants"
        self._lock = threading.RLock()
        self._ensure_private_root()
        self._ensure_private_directory(self.workspace_root)
        self._ensure_private_directory(self.participant_root)
        with self._lock:
            if not self.state_path.exists():
                self._write_state(self._empty_state())
            self._read_state()

    def workspace_path(self, binding_id: str) -> Path:
        """Resolve a binding without moving legacy Application Support workspaces."""

        if (
            not isinstance(binding_id, str)
            or not binding_id.startswith("workspace-")
            or Path(binding_id).name != binding_id
        ):
            raise StoreError(
                "scenario.workspace-unavailable", "workspace binding differs"
            )
        candidates = [self.workspace_root / binding_id]
        if self.legacy_workspace_root != self.workspace_root:
            candidates.append(self.legacy_workspace_root / binding_id)
        existing = [path for path in candidates if path.exists() or path.is_symlink()]
        if len(existing) > 1:
            raise StoreError(
                "host.workspace-conflict", "workspace binding exists in multiple roots"
            )
        return existing[0] if existing else candidates[0]

    def _ensure_private_root(self) -> None:
        if self.state_root.is_symlink():
            raise StoreError("host.state-root-invalid", "state root must not be a symlink")
        self.state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.state_root.stat().st_uid != os.getuid():
            raise StoreError("host.state-root-owner", "state root owner differs")
        os.chmod(self.state_root, 0o700)

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        if path.is_symlink():
            raise StoreError("host.workspace-root-invalid", "workspace root must not be a symlink")
        path.mkdir(mode=0o700, exist_ok=True)
        details = path.stat()
        if not path.is_dir() or details.st_uid != os.getuid():
            raise StoreError("host.workspace-root-invalid", "workspace root owner differs")
        os.chmod(path, 0o700)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "host_instance_id": uuid.uuid4().hex,
            "host_generation": 0,
            "state_revision": 0,
            "journal_head_sequence": 0,
            "scenarios": {},
            "scenario_history": {},
            "operations": {},
            "requests": {},
            "journal": [],
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise StoreError("host.state-invalid", "Host state file is unavailable")
        details = self.state_path.stat()
        if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
            raise StoreError("host.state-permission", "Host state permissions are invalid")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("host.state-invalid", "Host state is not valid JSON") from exc
        expected = {
            "schema_version",
            "host_instance_id",
            "host_generation",
            "state_revision",
            "journal_head_sequence",
            "scenarios",
            "scenario_history",
            "operations",
            "requests",
            "journal",
        }
        legacy_expected = expected - {"scenario_history"}
        if isinstance(value, dict) and set(value) == legacy_expected:
            value["scenario_history"] = {}
        if (
            not isinstance(value, dict)
            or set(value) != expected
            or value["schema_version"] != STATE_SCHEMA_VERSION
            or not isinstance(value["host_instance_id"], str)
            or not isinstance(value["host_generation"], int)
            or not isinstance(value["state_revision"], int)
            or not isinstance(value["journal_head_sequence"], int)
            or not isinstance(value["scenarios"], dict)
            or not isinstance(value["scenario_history"], dict)
            or not isinstance(value["operations"], dict)
            or not isinstance(value["requests"], dict)
            or not isinstance(value["journal"], list)
        ):
            raise StoreError("host.state-invalid", "Host state schema differs")
        return value

    def _write_state(self, value: dict[str, Any]) -> None:
        payload = canonical_json_bytes(value) + b"\n"
        temporary = self.state_root / f".host-state.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            os.chmod(self.state_path, 0o600)
            directory_fd = os.open(self.state_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()

    def ensure_capability(self) -> str:
        with self._lock:
            if self.capability_path.exists():
                if self.capability_path.is_symlink() or not self.capability_path.is_file():
                    raise StoreError("host.capability-invalid", "capability file is invalid")
                details = self.capability_path.stat()
                if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
                    raise StoreError("host.capability-invalid", "capability permissions are invalid")
                value = self.capability_path.read_text(encoding="utf-8").strip()
                if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                    raise StoreError("host.capability-invalid", "capability value is invalid")
                return value
            value = secrets.token_hex(32)
            descriptor = os.open(
                self.capability_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return value

    def start_host(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            state["host_generation"] += 1
            self._mark_active_resources_stale_after_restart(state)
            self._reconcile_transitional_scenarios(state)
            self._reconcile_transitional_participants(state)
            self._reconcile_scenario_participant_faults(state)
            state["state_revision"] += 1
            self._write_state(state)
            return {
                "host_instance_id": state["host_instance_id"],
                "host_generation": state["host_generation"],
            }

    def reconcile_recorded_outcomes(self) -> None:
        """Finish exact external outcomes durably recorded before a crash."""

        with self._lock:
            state = self._read_state()
            pending = [
                (
                    request_id,
                    request["operation_id"],
                    copy.deepcopy(request.get("pending_external_result")),
                )
                for request_id, request in state["requests"].items()
                if request["status"] == "pending"
                and request.get("pending_external_result") is not None
            ]
        for request_id, operation_id, external in pending:
            assert isinstance(external, dict)
            if external.get("outcome_kind") == "resource.break":
                self.finalize_resource_break(
                    project_instance_id=external["project_instance_id"],
                    scenario_id=external["scenario_id"],
                    request_id=request_id,
                    operation_id=operation_id,
                )
                continue
            with self._lock:
                state = self._read_state()
                operation = state["operations"].get(operation_id)
                if operation is None:
                    raise StoreError(
                        "host.state-invalid", "recorded operation is unavailable"
                    )
                kind = operation["operation_kind"]
                target = copy.deepcopy(operation["target"])
            if kind in {"scenario.close", "scenario.force-close"}:
                try:
                    self.finalize_scenario_close(
                        project_instance_id=external["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request_id,
                        operation_id=operation_id,
                        reports=external["reports"],
                        cancelled=external.get("cancelled", False),
                        force_stop_used=external.get("force_stop_used", False),
                    )
                except OperationFailed:
                    # An incomplete close is itself a fully reconciled durable
                    # result; start_host must not reclassify it as unknown.
                    pass
            elif kind in {"participant.stop", "participant.force-stop"}:
                self.finalize_participant_stop(
                    project_instance_id=external["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant_id=target["participant_id"],
                    request_id=request_id,
                    operation_id=operation_id,
                    release_evidence_sha256=external[
                        "owned_resource_evidence_sha256"
                    ],
                )
            elif kind == "participant.recover":
                self.finalize_participant_recover(
                    project_instance_id=external["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant_id=target["participant_id"],
                    request_id=request_id,
                    operation_id=operation_id,
                    recovery=external["recovery"],
                )
            elif kind == "participant.replace":
                self.commit_participant_replacement(
                    project_instance_id=external["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant_id=target["participant_id"],
                    request_id=request_id,
                    operation_id=operation_id,
                    launch_spec=external["launch_spec"],
                    resolved_driver=external["resolved_driver"],
                    cleanup_kind=external["cleanup_kind"],
                    owned_resource_evidence_sha256=external[
                        "owned_resource_evidence_sha256"
                    ],
                    recovery=external["recovery"],
                )

    def pending_workspace_operations(self) -> list[dict[str, Any]]:
        """Return private exact repair/destroy joins for Host startup recovery."""

        with self._lock:
            state = self._read_state()
            values: list[dict[str, Any]] = []
            for key, item in state["scenarios"].items():
                record = item["record"]
                operation_id = record.get("active_operation_id")
                if not isinstance(operation_id, str):
                    continue
                operation = state["operations"][operation_id]
                if operation["operation_kind"] not in {
                    "scenario.repair",
                    "scenario.destroy",
                    "scenario.force-destroy",
                }:
                    continue
                request_id = operation["request_id"]
                values.append(
                    {
                        "key": key,
                        "project_instance_id": item["project_instance_id"],
                        "scenario_id": record["scenario_id"],
                        "request_id": request_id,
                        "request_digest": state["requests"][request_id][
                            "request_digest"
                        ],
                        "operation_id": operation_id,
                        "operation_kind": operation["operation_kind"],
                    }
                )
            return values

    def record_scenario_close_reports(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        reports: list[dict[str, Any]],
        cancelled: bool = False,
        force_stop_used: bool = False,
    ) -> None:
        """Persist external close evidence before applying the final state CAS."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            request = state["requests"].get(request_id)
            if (
                item is None
                or request is None
                or request["operation_id"] != operation_id
                or request["status"] != "pending"
                or item["record"]["active_operation_id"] != operation_id
                or item["record"]["observed_state"] != "closing"
            ):
                raise StoreError(
                    "scenario.stale-fence", "scenario close evidence fence differs"
                )
            evidence = {
                "project_instance_id": project_instance_id,
                "reports": copy.deepcopy(reports),
                "cancelled": cancelled,
                "force_stop_used": force_stop_used,
            }
            previous = request.get("pending_external_result")
            if previous is not None and previous != evidence:
                raise StoreError(
                    "scenario.stale-fence", "scenario close evidence changed"
                )
            request["pending_external_result"] = evidence
            state["state_revision"] += 1
            self._write_state(state)

    def record_participant_stop_evidence(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        owned_resource_evidence_sha256: str,
    ) -> None:
        """Persist exact stop/force-stop evidence before clearing the binding."""

        if not self._sha256(owned_resource_evidence_sha256):
            raise StoreError(
                "resource.release-invalid", "participant stop evidence differs"
            )
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            _, _, record, _ = self._participant_state(
                state, key, participant_id
            )
            request = state["requests"].get(request_id)
            if (
                request is None
                or request["operation_id"] != operation_id
                or request["status"] != "pending"
                or record["active_operation_id"] != operation_id
                or record["observed_state"] != "stopping"
            ):
                raise StoreError(
                    "participant.stale-fence", "participant stop evidence fence differs"
                )
            evidence = {
                "project_instance_id": project_instance_id,
                "owned_resource_evidence_sha256": owned_resource_evidence_sha256,
            }
            previous = request.get("pending_external_result")
            if previous is not None and previous != evidence:
                raise StoreError(
                    "participant.stale-fence", "participant stop evidence changed"
                )
            request["pending_external_result"] = evidence
            state["state_revision"] += 1
            self._write_state(state)

    def record_participant_recovery_evidence(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        recovery: dict[str, Any],
    ) -> None:
        """Persist exact recovery evidence before rotating the generation."""

        evidence = recovery.get("owned_resource_evidence_sha256")
        if not self._sha256(evidence):
            raise StoreError(
                "resource.release-invalid", "participant recovery evidence differs"
            )
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            _, _, record, _ = self._participant_state(
                state, key, participant_id
            )
            request = state["requests"].get(request_id)
            if (
                request is None
                or request["operation_id"] != operation_id
                or request["status"] != "pending"
                or record["active_operation_id"] != operation_id
                or record["observed_state"] != "recovering"
            ):
                raise StoreError(
                    "participant.stale-fence",
                    "participant recovery evidence fence differs",
                )
            pending = {
                "project_instance_id": project_instance_id,
                "recovery": copy.deepcopy(recovery),
            }
            previous = request.get("pending_external_result")
            if previous is not None and previous != pending:
                raise StoreError(
                    "participant.stale-fence",
                    "participant recovery evidence changed",
                )
            request["pending_external_result"] = pending
            state["state_revision"] += 1
            self._write_state(state)

    def record_participant_replacement_cleanup(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        launch_spec: dict[str, Any],
        resolved_driver: dict[str, Any],
        cleanup_kind: str,
        owned_resource_evidence_sha256: str,
        recovery: dict[str, Any] | None,
    ) -> None:
        """Persist old-generation cleanup before replacement generation CAS."""

        if cleanup_kind not in {"stop", "repair"} or not self._sha256(
            owned_resource_evidence_sha256
        ):
            raise StoreError(
                "resource.release-invalid", "participant replacement evidence differs"
            )
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            _, _, record, _ = self._participant_state(
                state, key, participant_id
            )
            request = state["requests"].get(request_id)
            operation = state["operations"].get(operation_id)
            if (
                request is None
                or operation is None
                or request["operation_id"] != operation_id
                or request["status"] != "pending"
                or record["active_operation_id"] != operation_id
                or record["observed_state"] != "replacing"
                or operation["operation_kind"] != "participant.replace"
                or operation["replacement_launch_spec_digest"]
                != canonical_json_sha256(launch_spec)
            ):
                raise StoreError(
                    "participant.stale-fence",
                    "participant replacement evidence fence differs",
                )
            pending = {
                "outcome_kind": "participant.replace-cleanup",
                "project_instance_id": project_instance_id,
                "launch_spec": copy.deepcopy(launch_spec),
                "resolved_driver": copy.deepcopy(resolved_driver),
                "cleanup_kind": cleanup_kind,
                "owned_resource_evidence_sha256": (
                    owned_resource_evidence_sha256
                ),
                "recovery": copy.deepcopy(recovery),
            }
            previous = request.get("pending_external_result")
            if previous is not None and previous != pending:
                raise StoreError(
                    "participant.stale-fence",
                    "participant replacement evidence changed",
                )
            request["pending_external_result"] = pending
            state["state_revision"] += 1
            self._write_state(state)

    def host_status(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            return {
                "status": "ready",
                "host_generation": state["host_generation"],
                "scenario_count": len(state["scenarios"]),
            }

    def create_scenario(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        project_binding_digest: str,
    ) -> tuple[str, dict[str, Any]]:
        key = self._scenario_key(project_instance_id, scenario_id)
        binding_digest = hashlib.sha256(
            f"{request_id}\0{request_digest}".encode("utf-8")
        ).hexdigest()
        workspace_binding_id = f"workspace-{binding_digest[:24]}"
        workspace_path = self.workspace_path(workspace_binding_id)

        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous
            if key in state["scenarios"]:
                raise StoreError("scenario.already-exists", "scenario already exists")
            if workspace_path.exists() or workspace_path.is_symlink():
                raise StoreError(
                    "host.workspace-conflict",
                    "workspace binding path already exists without durable state",
                )
            operation = self._new_scenario_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="scenario.create",
                scenario_id=scenario_id,
                scenario_generation=None,
                scenario_state_revision=None,
                desired_state_after="closed",
                resulting_scenario_generation=1,
            )
            record = {
                "scenario_id": scenario_id,
                "scenario_generation": 1,
                "state_revision": 1,
                "desired_state": "closed",
                "observed_state": "provisioning",
                "project_binding_digest": project_binding_digest,
                "workspace_binding_id": None,
                "participant_ids": [],
                "active_operation_id": operation["operation_id"],
                "degraded": None,
                "journal_head_sequence": 0,
            }
            state["scenarios"][key] = {
                "project_instance_id": project_instance_id,
                "record": record,
                "participants": {},
                "participant_artifacts": {},
                "resource_leases": {},
                "resource_break_history": [],
            }
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=0,
                after_revision=1,
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=1,
                after_revision=1,
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "workspace_binding_id": workspace_binding_id,
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise StoreError("host.state-unavailable", "Host state could not be committed") from exc

        try:
            workspace_path.mkdir(mode=0o700)
        except OSError as exc:
            self._finalize_create_failure(
                key=key,
                request_id=request_id,
                operation_id=operation["operation_id"],
                workspace_path=workspace_path,
            )
            raise OperationFailed(
                operation["operation_id"],
                "operation.external-failure",
                "Scenario workspace provisioning failed",
                "committed",
                True,
            ) from exc

        with self._lock:
            state = self._read_state()
            result = self._finalize_scenario_success(
                state,
                key=key,
                request_id=request_id,
                operation_id=operation["operation_id"],
                trigger="provision_succeeded",
                workspace_binding_id=workspace_binding_id,
            )
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise OperationFailed(
                    operation["operation_id"],
                    "operation.internal-failure",
                    "Scenario provisioning outcome is unknown",
                    "unknown",
                    False,
                ) from exc
            return operation["operation_id"], result

    def open_scenario(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
    ) -> tuple[str, dict[str, Any]]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            record = item["record"]
            if (
                record["scenario_generation"] != scenario_generation
                or record["state_revision"] != scenario_state_revision
            ):
                raise StoreError("scenario.stale-fence", "scenario state fence differs")
            if record.get("active_operation_id") is not None:
                raise StoreError(
                    "scenario.operation-in-progress",
                    "scenario lifecycle operation is still pending",
                )
            resumable_degraded = (
                record["observed_state"] == "degraded"
                and record["desired_state"] == "running"
                and isinstance(record.get("degraded"), dict)
                and record["degraded"].get("reason")
                in {"participant_fault", "participant_restore_incomplete"}
            )
            if record["observed_state"] != "closed" and not resumable_degraded:
                raise StoreError(
                    "scenario.invalid-transition",
                    "scenario cannot be opened or resumed",
                )
            operation = self._new_scenario_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="scenario.open",
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                desired_state_after="running",
                resulting_scenario_generation=scenario_generation,
            )
            record["desired_state"] = "running"
            record["observed_state"] = "opening"
            record["active_operation_id"] = operation["operation_id"]
            record["degraded"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=scenario_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "workspace_binding_id": None,
                "result": None,
                "error": None,
                "pending_resume_summary": {
                    "project_instance_id": project_instance_id,
                    "scenario_id": scenario_id,
                },
            }
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise StoreError("host.state-unavailable", "Host state could not be committed") from exc

        return operation["operation_id"], {"scenario": copy.deepcopy(record)}

    def begin_scenario_close(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        drain_timeout_ms: int,
        force: bool = False,
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]] | None]:
        """Commit the frozen close intent and return driver work outside the lock."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            record = item["record"]
            self._check_scenario_fence(
                record, scenario_generation, scenario_state_revision
            )
            if record.get("active_operation_id") is not None:
                raise StoreError(
                    "scenario.operation-in-progress",
                    "scenario lifecycle operation is still pending",
                )
            allowed_states = (
                {"opening", "running", "degraded", "closed", "provision_failed"}
                if force
                else {"opening", "running", "degraded"}
            )
            if record["observed_state"] not in allowed_states:
                raise StoreError(
                    "scenario.invalid-transition", "scenario cannot be closed"
                )
            participants, _ = self._participant_maps(item)
            if any(
                participant.get("active_operation_id") is not None
                or participant.get("observed_state")
                in {"starting", "stopping", "replacing"}
                for participant in participants.values()
            ):
                raise StoreError(
                    "scenario.operation-in-progress",
                    "participant lifecycle operation is still pending",
                )
            operation = self._new_scenario_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind=("scenario.force-close" if force else "scenario.close"),
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                desired_state_after="closed",
                resulting_scenario_generation=scenario_generation,
            )
            record["desired_state"] = "closed"
            record["observed_state"] = "closing"
            record["active_operation_id"] = operation["operation_id"]
            record["degraded"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=scenario_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            participants, artifacts = self._participant_maps(item)
            executions: list[dict[str, Any]] = []
            for participant_id in sorted(participants):
                participant = participants[participant_id]
                artifact = artifacts[participant_id]
                common = {
                    "participant_id": participant_id,
                    "participant_generation": participant["participant_generation"],
                    "participant_state_revision": participant["state_revision"],
                    "desired_state": participant["desired_state"],
                    "continuity_mode": artifact["launch_spec"]["continuity_mode"],
                    "runtime_binding_id": participant["runtime_binding_id"],
                    "presentation_binding_id": participant[
                        "presentation_binding_id"
                    ],
                }
                if participant["observed_state"] in {"stopped", "detached"}:
                    executions.append({**common, "kind": "inactive"})
                    continue
                runtime_ack = artifact.get("runtime_ready_ack")
                if (
                    participant["observed_state"] not in {"ready", "degraded"}
                    or not isinstance(runtime_ack, dict)
                    or participant.get("runtime_binding_id") is None
                ):
                    executions.append({**common, "kind": "unknown"})
                    continue
                executions.append(
                    {
                        **common,
                        "kind": "driver",
                        "driver_payload": {
                            "context": {
                                "scenario_id": scenario_id,
                                "participant_id": participant_id,
                                "participant_generation": participant[
                                    "participant_generation"
                                ],
                                "operation_id": operation["operation_id"],
                                "operation_generation": operation[
                                    "operation_generation"
                                ],
                                "driver_registry_digest": artifact[
                                    "resolved_driver"
                                ]["driver_registry_digest"],
                                "capability_snapshot_digest": artifact[
                                    "resolved_driver"
                                ]["capability_snapshot_digest"],
                            },
                            "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                            "resolved_driver": copy.deepcopy(
                                artifact["resolved_driver"]
                            ),
                            "runtime_ready_ack": copy.deepcopy(runtime_ack),
                            "presentation_create_ack": copy.deepcopy(
                                artifact.get("presentation_create_ack")
                            ),
                            "private_root": str(
                                self.participant_private_path(
                                    project_instance_id,
                                    scenario_id,
                                    participant_id,
                                    participant["participant_generation"],
                                )
                            ),
                            "drain_timeout_ms": drain_timeout_ms,
                        },
                    }
                )
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "workspace_binding_id": None,
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise StoreError(
                    "host.state-unavailable", "Host state could not be committed"
                ) from exc
            return operation["operation_id"], None, executions

    def finalize_scenario_close(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        reports: list[dict[str, Any]],
        cancelled: bool = False,
        force_stop_used: bool = False,
    ) -> dict[str, Any]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            record = item["record"]
            operation = state["operations"][operation_id]
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "closing"
            ):
                raise StoreError(
                    "scenario.stale-fence", "scenario close callback fence differs"
                )
            participants, artifacts = self._participant_maps(item)
            expected_ids = sorted(participants)
            if [entry.get("participant_id") for entry in reports] != expected_ids:
                raise StoreError(
                    "scenario.stale-fence", "scenario close target set differs"
                )
            reports = copy.deepcopy(reports)
            revision = record["state_revision"]
            all_closed = all(entry["closed"] is True for entry in reports)
            # A safe close must still fail closed when a participant cannot be
            # proven closed. A forced close is the owner-confirmed teardown of a
            # Scenario that is already broken, so an unprovable participant is
            # recorded honestly in the reports but does not abort the teardown;
            # otherwise the only exit closes exactly when it is needed.
            close_completed = all_closed or (force_stop_used and not cancelled)
            event = "external_succeeded" if close_completed else "external_failed"
            failure_code = (
                None
                if close_completed
                else "operation.cancelled"
                if cancelled
                else "lifecycle.close-incomplete"
            )
            self._append_operation_event(
                state,
                operation,
                event=event,
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
                error_code=failure_code,
            )
            for report in reports:
                participant = participants[report["participant_id"]]
                artifact = artifacts[report["participant_id"]]
                report.setdefault(
                    "desired_state_before_close", participant["desired_state"]
                )
                report.setdefault(
                    "continuity_mode", artifact["launch_spec"]["continuity_mode"]
                )
                if (
                    participant["participant_generation"]
                    != report["participant_generation"]
                    or participant["state_revision"]
                    != report["participant_state_revision"]
                    or participant["desired_state"]
                    != report["desired_state_before_close"]
                    or artifact["launch_spec"]["continuity_mode"]
                    != report["continuity_mode"]
                ):
                    raise StoreError(
                        "participant.stale-fence",
                        "scenario close participant fence differs",
                    )
                participant_revision = participant["state_revision"]
                if report["closed"] is True:
                    self._release_participant_resources(
                        item,
                        participant,
                        report["owned_resource_evidence_sha256"],
                    )
                    if participant["observed_state"] not in {"stopped", "detached"}:
                        participant.update(
                            {
                                "desired_state": "stopped",
                                "observed_state": "stopped",
                                "runtime_binding_id": None,
                                "presentation_binding_id": None,
                                "active_operation_id": None,
                                "degraded": None,
                                "state_revision": participant_revision + 1,
                            }
                        )
                        for field in (
                            "runtime_create_request",
                            "prepared_runtime_launch",
                            "runtime_ready_ack",
                            "presentation_create_request",
                            "presentation_create_ack",
                        ):
                            artifact[field] = None
                elif participant["observed_state"] not in {"stopped", "detached"}:
                    self._stale_participant_resources(
                        item, participant, "close_incomplete"
                    )
                    participant["observed_state"] = "degraded"
                    participant["active_operation_id"] = None
                    participant["state_revision"] = participant_revision + 1
                    participant["degraded"] = {
                        "reason": "cleanup_pending",
                        "cleanup_pending": True,
                        "owned_resource_evidence_sha256": canonical_json_sha256(
                            report
                        ),
                        "repair_action": "participant.recover",
                    }
                if participant["state_revision"] != participant_revision:
                    participant["journal_head_sequence"] = state[
                        "journal_head_sequence"
                    ]
            restore_target_participant_ids = sorted(
                report["participant_id"]
                for report in reports
                if report["desired_state_before_close"] == "running"
            )
            restore_targets = [
                {
                    "participant_id": report["participant_id"],
                    "participant_generation": report["participant_generation"],
                    "continuity_mode": report["continuity_mode"],
                }
                for report in reports
                if report["desired_state_before_close"] == "running"
            ]
            close_summary = {
                "schema_version": 1,
                "operation_id": operation_id,
                "all_closed": all_closed,
                "auto_force_stop_used": force_stop_used,
                "restore_target_participant_ids": restore_target_participant_ids,
                "restore_targets": restore_targets,
                "reports": copy.deepcopy(reports),
                "summary_digest": canonical_json_sha256(reports),
            }
            item.setdefault("close_history", []).append(copy.deepcopy(close_summary))
            # close_summary above keeps all_closed and the per-participant
            # reports verbatim, so the record of what could not be proven
            # survives even when a forced close is allowed to complete.
            record["observed_state"] = "closed" if close_completed else "degraded"
            record["active_operation_id"] = None
            record["state_revision"] += 1
            record["degraded"] = (
                None
                if close_completed
                else {
                    "reason": "cleanup_pending",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": close_summary[
                        "summary_digest"
                    ],
                    "repair_action": "scenario.repair",
                }
            )
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            request = state["requests"][request_id]
            request.pop("pending_external_result", None)
            if close_completed:
                operation["state"] = "succeeded"
                operation["mutation_state"] = "committed"
                result = {
                    "scenario": copy.deepcopy(record),
                    "close_summary": copy.deepcopy(close_summary),
                }
                request["status"] = "completed"
                request["result"] = result
                state["state_revision"] += 1
                try:
                    self._write_state(state)
                except OSError as exc:
                    raise OperationFailed(
                        operation_id,
                        "operation.internal-failure",
                        "Scenario close outcome is unknown",
                        "unknown",
                        False,
                    ) from exc
                return result
            operation["state"] = "failed"
            operation["mutation_state"] = "committed"
            operation["failure_code"] = failure_code
            request["status"] = "failed"
            request["error"] = {
                "code": (
                    "operation.cancelled"
                    if cancelled
                    else "operation.external-failure"
                ),
                "message": (
                    "Scenario close was cooperatively cancelled; refresh before deciding the next action"
                    if cancelled
                    else "Scenario close requires repair or explicit high-risk action"
                ),
                "mutation_state": "committed",
                "retryable": False,
            }
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise OperationFailed(
                    operation_id,
                    "operation.internal-failure",
                    "Scenario close outcome is unknown",
                    "unknown",
                    False,
                ) from exc
            raise OperationFailed(
                operation_id,
                "operation.cancelled" if cancelled else "operation.external-failure",
                (
                    "Scenario close was cooperatively cancelled; refresh before deciding the next action"
                    if cancelled
                    else "Scenario close requires repair or explicit high-risk action"
                ),
                "committed",
                False,
            )

    def scenario_restore_plan(
        self, project_instance_id: str, scenario_id: str
    ) -> list[dict[str, Any]]:
        """Return the last successful close's exact participant restore targets."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            participants, artifacts = self._participant_maps(item)
            close_history = item.get("close_history", [])
            if not close_history:
                return []
            latest = close_history[-1]
            reports = latest.get("reports", [])
            declared = latest.get("restore_targets")
            if declared is None:
                declared_target_ids = latest.get("restore_target_participant_ids")
                if declared_target_ids is None:
                    # Compatibility for pre-resume close summaries: an inactive
                    # close report proves that participant was already stopped;
                    # every successful driver close proves it was active.
                    declared_target_ids = sorted(
                        report["participant_id"]
                        for report in reports
                        if report.get("closed") is True
                        and report.get("command") != "inactive"
                    )
                reports_by_id = {
                    report.get("participant_id"): report for report in reports
                }
                declared = []
                if isinstance(declared_target_ids, list):
                    for participant_id in declared_target_ids:
                        report = reports_by_id.get(participant_id, {})
                        artifact = artifacts.get(participant_id, {})
                        launch_spec = artifact.get("launch_spec", {})
                        declared.append(
                            {
                                "participant_id": participant_id,
                                "participant_generation": report.get(
                                    "participant_generation"
                                ),
                                "continuity_mode": report.get(
                                    "continuity_mode",
                                    launch_spec.get("continuity_mode"),
                                ),
                            }
                        )
            declared_ids = (
                [
                    value.get("participant_id")
                    for value in declared
                    if isinstance(value, dict)
                ]
                if isinstance(declared, list)
                else []
            )
            if (
                latest.get("all_closed") is not True
                or not isinstance(declared, list)
                or len(declared_ids) != len(declared)
                or len(declared_ids) != len(set(declared_ids))
                or any(
                    not isinstance(value, dict)
                    or not isinstance(value.get("participant_id"), str)
                    or not self._positive_int(value.get("participant_generation"))
                    or value.get("continuity_mode")
                    not in {"explicit_recreate", "exact_resume"}
                    for value in declared
                )
            ):
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario restore plan is unavailable",
                )
            return sorted(
                copy.deepcopy(declared), key=lambda value: value["participant_id"]
            )

    def pending_scenario_resume_requests(self) -> list[dict[str, Any]]:
        """Return compound opens whose participant restore summary is unfinished."""

        with self._lock:
            state = self._read_state()
            pending: list[dict[str, Any]] = []
            for item in state["scenarios"].values():
                project_instance_id = item["project_instance_id"]
                scenario_id = item["record"]["scenario_id"]
                for request_id, request in state["requests"].items():
                    resume_context = request.get("pending_resume_summary")
                    if resume_context != {
                        "project_instance_id": project_instance_id,
                        "scenario_id": scenario_id,
                    }:
                        continue
                    operation = state["operations"].get(request["operation_id"])
                    if (
                        not isinstance(operation, dict)
                        or operation["operation_kind"] != "scenario.open"
                        or operation["target"].get("scenario_id") != scenario_id
                    ):
                        continue
                    pending.append(
                        {
                            "project_instance_id": project_instance_id,
                            "scenario_id": scenario_id,
                            "project_binding_digest": item["record"][
                                "project_binding_digest"
                            ],
                            "request_id": request_id,
                            "request_digest": request["request_digest"],
                        }
                    )
            return sorted(
                pending,
                key=lambda value: (
                    value["project_instance_id"],
                    value["scenario_id"],
                    value["request_id"],
                ),
            )

    def record_scenario_open_resume_summary(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        request_digest: str,
        reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Complete a scenario.open result with its participant restore summary."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            request = state["requests"].get(request_id)
            if item is None or request is None:
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario resume request is unavailable",
                )
            operation = state["operations"].get(request["operation_id"])
            if (
                request["request_digest"] != request_digest
                or request["status"] not in {"pending", "completed"}
                or not isinstance(operation, dict)
                or operation["operation_kind"] != "scenario.open"
            ):
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario resume request differs",
                )
            request_result = request.get("result")
            existing = (
                request_result.get("resume_summary")
                if isinstance(request_result, dict)
                else None
            )
            if existing is not None:
                return copy.deepcopy(request_result)
            restore_plan = self.scenario_restore_plan(
                project_instance_id, scenario_id
            )
            expected_by_id = {
                value["participant_id"]: value for value in restore_plan
            }
            if (
                not isinstance(reports, list)
                or len(reports) != len(expected_by_id)
                or {value.get("participant_id") for value in reports}
                != set(expected_by_id)
                or any(
                    not isinstance(value, dict)
                    or value.get("participant_generation")
                    != expected_by_id[value.get("participant_id")][
                        "participant_generation"
                    ]
                    or value.get("continuity_mode")
                    != expected_by_id[value.get("participant_id")][
                        "continuity_mode"
                    ]
                    or value.get("outcome")
                    not in {
                        "already_ready",
                        "recreated",
                        "exact_resumed",
                        "failed",
                        "unsupported",
                    }
                    or not isinstance(value.get("repair_required"), bool)
                    for value in reports
                )
            ):
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario resume result target set differs",
                )
            failed = [
                value
                for value in reports
                if value.get("outcome") in {"failed", "unsupported"}
            ]
            participants, _ = self._participant_maps(item)
            unresolved_participants = sorted(
                participant_id
                for participant_id, participant in participants.items()
                if participant["observed_state"] == "degraded"
            )
            summary = {
                "schema_version": 1,
                "all_targets_ready": not failed and not unresolved_participants,
                "target_count": len(reports),
                "recreated_count": sum(
                    value.get("outcome") == "recreated" for value in reports
                ),
                "exact_resumed_count": sum(
                    value.get("outcome") == "exact_resumed" for value in reports
                ),
                "reports": copy.deepcopy(reports),
                "vendor_session_identity_required": any(
                    value["continuity_mode"] == "exact_resume"
                    for value in restore_plan
                ),
                "explicit_recreate_is_not_exact_resume": True,
            }
            record = item["record"]
            if (
                request.get("pending_resume_summary")
                != {
                    "project_instance_id": project_instance_id,
                    "scenario_id": scenario_id,
                }
                or record["active_operation_id"] != operation["operation_id"]
                or record["observed_state"] not in {"opening", "degraded"}
                or (
                    record["observed_state"] == "degraded"
                    and record.get("degraded", {}).get("reason")
                    not in {"participant_fault", "participant_restore_incomplete"}
                )
            ):
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario resume callback fence differs",
                )
            before_revision = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=before_revision,
                after_revision=before_revision,
                mutation_state="committed",
            )
            if failed or unresolved_participants:
                cleanup_pending = any(
                    value.get("repair_required") is True for value in failed
                ) or any(
                    participants[participant_id].get("degraded", {}).get(
                        "cleanup_pending"
                    )
                    is True
                    for participant_id in unresolved_participants
                )
                record["observed_state"] = "degraded"
                record["degraded"] = {
                    "reason": (
                        "participant_restore_incomplete"
                        if failed
                        else "participant_fault"
                    ),
                    "cleanup_pending": cleanup_pending,
                    "owned_resource_evidence_sha256": canonical_json_sha256(
                        {
                            "resume_summary": summary,
                            "unresolved_participant_ids": unresolved_participants,
                        }
                    ),
                    "repair_action": "scenario.repair",
                }
            else:
                record["observed_state"] = "running"
                record["degraded"] = None
            record["active_operation_id"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=before_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            request["result"] = {
                "scenario": copy.deepcopy(record),
                "resume_summary": summary,
            }
            request["status"] = "completed"
            request.pop("pending_resume_summary", None)
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise OperationFailed(
                    operation["operation_id"],
                    "operation.internal-failure",
                    "Scenario resume outcome is unknown",
                    "unknown",
                    False,
                ) from exc
            return copy.deepcopy(request["result"])

    def scenario_diagnostic(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        """Return redacted durable state suitable for CLI JSON diagnostics."""

        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(
                self._scenario_key(project_instance_id, scenario_id)
            )
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            participants, _ = self._participant_maps(item)
            operation_ids = {
                value["active_operation_id"]
                for value in [item["record"], *participants.values()]
                if value.get("active_operation_id") is not None
            }
            operations = [
                {
                    "operation_id": operation_id,
                    "operation_kind": state["operations"][operation_id][
                        "operation_kind"
                    ],
                    "state": state["operations"][operation_id]["state"],
                    "mutation_state": state["operations"][operation_id][
                        "mutation_state"
                    ],
                }
                for operation_id in sorted(operation_ids)
            ]
            latest_close = None
            close_history = item.get("close_history", [])
            if close_history:
                latest_close = copy.deepcopy(close_history[-1])
            diagnostic = {
                "schema_version": 2,
                "scenario": copy.deepcopy(item["record"]),
                "participants": [
                    copy.deepcopy(participants[key]) for key in sorted(participants)
                ],
                "resources": self._public_resource_leases(item),
                "active_operations": operations,
                "latest_close": latest_close,
                "repair_actions": sorted(
                    {
                        value["degraded"]["repair_action"]
                        for value in [item["record"], *participants.values()]
                        if isinstance(value.get("degraded"), dict)
                    }
                    | (
                        {"resource.inspect"}
                        if any(
                            lease["status"] == "stale"
                            for lease in self._resource_leases(item).values()
                        )
                        else set()
                    )
                ),
            }
            diagnostic["diagnostic_digest"] = canonical_json_sha256(diagnostic)
            return {"diagnostic": diagnostic}

    def list_resources(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        """Return the redacted resource-lease ledger for one Scenario."""

        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(
                self._scenario_key(project_instance_id, scenario_id)
            )
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            return {"resources": self._public_resource_leases(item)}

    def replay_request(
        self, request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return an exact completed replay before a new high-risk prompt."""

        with self._lock:
            state = self._read_state()
            return self._previous_request(state, request_id, request_digest)

    def participant_security_context(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Freeze redacted preview and owner-private probe subject."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, participant, artifact = self._participant_state(
                state, key, participant_id
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                participant, participant_generation, participant_state_revision
            )
            if participant["observed_state"] not in {"ready", "degraded"}:
                raise StoreError(
                    "participant.invalid-transition", "participant is not active"
                )
            if participant["active_operation_id"] is not None:
                raise StoreError(
                    "scenario.operation-in-progress", "participant operation is active"
                )
            runtime_ack = artifact.get("runtime_ready_ack")
            binding = (
                runtime_ack.get("binding")
                if isinstance(runtime_ack, dict)
                else None
            )
            if (
                not isinstance(binding, dict)
                or binding.get("runtime_binding_id")
                != participant["runtime_binding_id"]
                or not self._sha256(binding.get("process_identity_sha256"))
            ):
                raise StoreError(
                    "participant.binding-drift", "participant binding evidence differs"
                )
            leases = [
                copy.deepcopy(lease)
                for lease in self._resource_leases(item).values()
                if lease["holder"]["participant_id"] == participant_id
                and lease["holder"]["participant_generation"]
                == participant_generation
                and lease["status"] != "released"
            ]
            preview = {
                "schema_version": 1,
                "operation": "participant.force-stop",
                "participant": {
                    "scenario_id": scenario_id,
                    "scenario_generation": scenario_generation,
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "participant_state_revision": participant_state_revision,
                    "runtime_binding_id": participant["runtime_binding_id"],
                    "process_identity_sha256": binding[
                        "process_identity_sha256"
                    ],
                },
                "affected_resource_lease_ids": sorted(
                    lease["lease_id"] for lease in leases
                ),
                "canonical_wip_mutation": False,
            }
            private_root = self.participant_private_path(
                project_instance_id,
                scenario_id,
                participant_id,
                participant_generation,
            )
            subject = {
                "subject_kind": "harness-owned-process",
                "private_root": str(private_root),
                "runtime_binding_id": participant["runtime_binding_id"],
                "process_identity_sha256": binding[
                    "process_identity_sha256"
                ],
                "expected_process_state": "present",
            }
            return preview, subject

    def resource_break_context(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        lease_id: str,
        lease_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Require an exact stale lease and prepare an absent-process probe."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            scenario = item["record"]
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            lease = self._resource_leases(item).get(lease_id)
            if lease is None:
                raise StoreError("resource.not-found", "resource lease does not exist")
            if lease["lease_revision"] != lease_revision:
                raise StoreError("resource.stale-fence", "resource lease fence differs")
            if lease["status"] != "stale":
                raise StoreError(
                    "resource.invalid-transition", "only an exact stale lease can be broken"
                )
            holder = lease["holder"]
            if (
                holder["scenario_id"] != scenario_id
                or holder["scenario_generation"] != scenario_generation
            ):
                raise StoreError("resource.stale-fence", "resource holder fence differs")
            preview = {
                "schema_version": 1,
                "operation": "resource.break",
                "lease": {
                    "lease_id": lease_id,
                    "lease_revision": lease_revision,
                    "resource_class": lease["resource_class"],
                    "resource_identity_sha256": lease[
                        "resource_identity_sha256"
                    ],
                    "holder": copy.deepcopy(holder),
                    "stale_reason": lease["stale_reason"],
                },
                "allows_future_reuse": True,
                "terminates_process": False,
            }
            private_root = self.participant_private_path(
                project_instance_id,
                scenario_id,
                holder["participant_id"],
                holder["participant_generation"],
            )
            subject = {
                "subject_kind": "harness-owned-process",
                "private_root": str(private_root),
                "runtime_binding_id": holder["runtime_binding_id"],
                "process_identity_sha256": lease[
                    "process_start_identity_sha256"
                ],
                "expected_process_state": "absent",
            }
            return preview, subject

    def break_resource(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        lease_id: str,
        lease_revision: int,
        consumption_evidence_sha256: str,
    ) -> tuple[str, dict[str, Any]]:
        """Release only the exact stale lease after authorization consumption."""

        if not self._sha256(consumption_evidence_sha256):
            raise StoreError("resource.release-invalid", "break evidence differs")
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            scenario = item["record"]
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            lease = self._resource_leases(item).get(lease_id)
            if lease is None:
                raise StoreError("resource.not-found", "resource lease does not exist")
            if lease["lease_revision"] != lease_revision:
                raise StoreError("resource.stale-fence", "resource lease fence differs")
            if lease["status"] != "stale":
                raise StoreError(
                    "resource.invalid-transition", "only an exact stale lease can be broken"
                )
            operation_id = f"resource-op-{uuid.uuid4().hex}"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "status": "pending",
                "workspace_binding_id": None,
                "result": None,
                "error": None,
                "pending_external_result": {
                    "outcome_kind": "resource.break",
                    "project_instance_id": project_instance_id,
                    "scenario_id": scenario_id,
                    "scenario_generation": scenario_generation,
                    "scenario_state_revision": scenario_state_revision,
                    "lease_id": lease_id,
                    "lease_revision": lease_revision,
                    "consumption_evidence_sha256": consumption_evidence_sha256,
                },
            }
            state["state_revision"] += 1
            self._write_state(state)
        return self.finalize_resource_break(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            request_id=request_id,
            operation_id=operation_id,
        )

    def finalize_resource_break(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Publish an exact authorized stale-lease release after a crash-safe join."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            request = state["requests"].get(request_id)
            if request is None or request["operation_id"] != operation_id:
                raise StoreError("resource.stale-fence", "resource break request differs")
            if request["status"] == "completed":
                return operation_id, copy.deepcopy(request["result"])
            external = request.get("pending_external_result")
            if (
                request["status"] != "pending"
                or not isinstance(external, dict)
                or external.get("outcome_kind") != "resource.break"
                or external.get("project_instance_id") != project_instance_id
                or external.get("scenario_id") != scenario_id
                or not self._sha256(external.get("consumption_evidence_sha256"))
            ):
                raise StoreError("resource.stale-fence", "resource break evidence differs")
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            scenario = item["record"]
            self._check_scenario_fence(
                scenario,
                external["scenario_generation"],
                external["scenario_state_revision"],
            )
            lease_id = external["lease_id"]
            lease_revision = external["lease_revision"]
            consumption_evidence_sha256 = external[
                "consumption_evidence_sha256"
            ]
            lease = self._resource_leases(item).get(lease_id)
            if lease is None:
                raise StoreError("resource.not-found", "resource lease does not exist")
            if lease["lease_revision"] != lease_revision:
                raise StoreError("resource.stale-fence", "resource lease fence differs")
            if lease["status"] != "stale":
                raise StoreError(
                    "resource.invalid-transition",
                    "only an exact stale lease can be broken",
                )
            lease.update(
                {
                    "lease_revision": lease["lease_revision"] + 1,
                    "status": "released",
                    "stale_reason": None,
                    "release_evidence_sha256": consumption_evidence_sha256,
                }
            )
            scenario["state_revision"] += 1
            history = item.setdefault("resource_break_history", [])
            if not isinstance(history, list):
                raise StoreError("host.state-invalid", "resource break history differs")
            history.append(
                {
                    "operation_id": operation_id,
                    "request_digest": request["request_digest"],
                    "lease_id": lease_id,
                    "lease_revision_before": lease_revision,
                    "lease_revision_after": lease["lease_revision"],
                    "consumption_evidence_sha256": consumption_evidence_sha256,
                }
            )
            result = {"resource": copy.deepcopy(lease)}
            request.update({"status": "completed", "result": result})
            request.pop("pending_external_result", None)
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    def scenario_high_risk_preview(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        operation: str,
    ) -> dict[str, Any]:
        """Build an exact, redacted high-risk Scenario effect preview."""

        if operation not in {
            "scenario.repair",
            "scenario.destroy",
            "scenario.force-destroy",
        }:
            raise StoreError("scenario.invalid-transition", "scenario operation differs")
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            scenario = item["record"]
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            participants, _ = self._participant_maps(item)
            leases = list(self._resource_leases(item).values())
            blockers: list[str] = []
            if scenario["active_operation_id"] is not None:
                blockers.append("scenario.operation-active")
            if operation == "scenario.repair":
                if scenario["observed_state"] not in {"provision_failed", "degraded"}:
                    blockers.append("scenario.not-degraded")
                if any(
                    participant["active_operation_id"] is not None
                    or participant["observed_state"] in {"starting", "stopping", "replacing"}
                    for participant in participants.values()
                ):
                    blockers.append("participant.operation-active")
                if scenario["desired_state"] in {"closed", "destroyed"}:
                    if any(
                        participant["observed_state"] not in {"stopped", "detached"}
                        or participant["runtime_binding_id"] is not None
                        or participant["presentation_binding_id"] is not None
                        for participant in participants.values()
                    ):
                        blockers.append("participant.cleanup-pending")
                    if any(lease["status"] != "released" for lease in leases):
                        blockers.append("resource.release-pending")
            elif operation == "scenario.destroy":
                resumable = (
                    scenario["observed_state"] == "destroying"
                    and scenario["desired_state"] == "destroyed"
                )
                if scenario["observed_state"] != "closed" and not resumable:
                    blockers.append("scenario.not-closed")
                if any(
                    participant["observed_state"] not in {"stopped", "detached"}
                    or participant["runtime_binding_id"] is not None
                    or participant["presentation_binding_id"] is not None
                    or participant["active_operation_id"] is not None
                    for participant in participants.values()
                ):
                    blockers.append("participant.not-detached-or-stopped")
                if any(lease["status"] != "released" for lease in leases):
                    blockers.append("resource.not-released")
            else:
                if scenario["observed_state"] == "destroying":
                    blockers.append("scenario.destroy-in-progress")
                if any(
                    participant["active_operation_id"] is not None
                    or participant["observed_state"]
                    in {"starting", "stopping", "recovering", "replacing"}
                    for participant in participants.values()
                ):
                    blockers.append("participant.operation-active")
            return {
                "schema_version": 1,
                "operation": operation,
                "scenario": {
                    "scenario_id": scenario_id,
                    "scenario_generation": scenario_generation,
                    "scenario_state_revision": scenario_state_revision,
                    "desired_state": scenario["desired_state"],
                    "observed_state": scenario["observed_state"],
                    "workspace_binding_id": scenario["workspace_binding_id"],
                },
                "participant_summary": {
                    "count": len(participants),
                    "stopped_or_detached": sum(
                        value["observed_state"] in {"stopped", "detached"}
                        for value in participants.values()
                    ),
                    "live_binding_count": sum(
                        value["runtime_binding_id"] is not None
                        or value["presentation_binding_id"] is not None
                        for value in participants.values()
                    ),
                    "force_cleanup_count": sum(
                        value["observed_state"] not in {"stopped", "detached"}
                        or value["runtime_binding_id"] is not None
                        or value["presentation_binding_id"] is not None
                        for value in participants.values()
                    ),
                },
                "resource_summary": {
                    "count": len(leases),
                    "unreleased_count": sum(
                        value["status"] != "released" for value in leases
                    ),
                },
                "eligible": not blockers,
                "blockers": sorted(set(blockers)),
                "canonical_wip_mutation": False,
            }

    def begin_scenario_repair(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
    ) -> tuple[str, dict[str, Any] | None, Path | None]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            preview = self.scenario_high_risk_preview(
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                operation="scenario.repair",
            )
            if not preview["eligible"]:
                raise StoreError(
                    "scenario.invalid-transition", "scenario repair prerequisites differ"
                )
            state = self._read_state()
            item = state["scenarios"][key]
            record = item["record"]
            operation = self._new_scenario_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="scenario.repair",
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                desired_state_after=record["desired_state"],
                resulting_scenario_generation=scenario_generation,
            )
            record["observed_state"] = "repairing"
            record["active_operation_id"] = operation["operation_id"]
            record["state_revision"] += 1
            before = scenario_state_revision
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=before,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "workspace_binding_id": record["workspace_binding_id"],
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return (
                operation["operation_id"],
                None,
                self.workspace_path(record["workspace_binding_id"]),
            )

    def finalize_scenario_repair(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        workspace_evidence_sha256: str,
    ) -> dict[str, Any]:
        if not self._sha256(workspace_evidence_sha256):
            raise StoreError("scenario.repair-invalid", "repair evidence differs")
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"][key]
            record = item["record"]
            operation = state["operations"][operation_id]
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "repairing"
            ):
                raise StoreError("scenario.stale-fence", "repair callback fence differs")
            before = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=before,
                after_revision=before,
                mutation_state="committed",
            )
            target = {
                "closed": "closed",
                "running": "running",
                "destroyed": "destroying",
            }[record["desired_state"]]
            record["observed_state"] = target
            record["active_operation_id"] = None
            record["degraded"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=before,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            result = {"scenario": copy.deepcopy(record)}
            state["requests"][request_id].update(
                {"status": "completed", "result": result}
            )
            state["state_revision"] += 1
            self._write_state(state)
            return result

    def fail_scenario_repair_or_destroy(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        reason: str,
    ) -> None:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(key)
            if item is None:
                return
            record = item["record"]
            if record["active_operation_id"] != operation_id:
                return
            operation = state["operations"][operation_id]
            before = record["state_revision"]
            evidence = canonical_json_sha256(
                {"operation_id": operation_id, "reason": reason}
            )
            self._append_operation_event(
                state,
                operation,
                event="repair_required",
                before_revision=before,
                after_revision=before,
                mutation_state="committed",
                error_code=reason,
            )
            record["observed_state"] = "degraded"
            record["active_operation_id"] = None
            record["degraded"] = {
                "reason": "cleanup_pending",
                "cleanup_pending": True,
                "owned_resource_evidence_sha256": evidence,
                "repair_action": "scenario.repair",
            }
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=before,
                after_revision=record["state_revision"],
                mutation_state="committed",
                error_code=reason,
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "repair_required"
            operation["mutation_state"] = "committed"
            operation["failure_code"] = reason
            state["requests"][request_id].update(
                {
                    "status": "failed",
                    "error": {
                        "code": "operation.external-failure",
                        "message": "Scenario operation requires repair",
                        "mutation_state": "committed",
                        "retryable": False,
                    },
                }
            )
            state["state_revision"] += 1
            self._write_state(state)

    def begin_scenario_destroy(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        operation_kind: str = "scenario.destroy",
    ) -> tuple[str, dict[str, Any] | None, Path | None]:
        if operation_kind not in {"scenario.destroy", "scenario.force-destroy"}:
            raise StoreError(
                "scenario.invalid-transition", "scenario destroy operation differs"
            )
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            # Re-check against the operation actually being performed. Checking
            # a force destroy against the conservative prerequisites reimposed
            # exactly the conditions a force destroy exists to bypass.
            preview = self.scenario_high_risk_preview(
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                operation=operation_kind,
            )
            if not preview["eligible"]:
                raise StoreError(
                    "scenario.invalid-transition", "scenario destroy prerequisites differ"
                )
            state = self._read_state()
            item = state["scenarios"][key]
            record = item["record"]
            operation = self._new_scenario_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind=operation_kind,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                desired_state_after="destroyed",
                resulting_scenario_generation=scenario_generation,
            )
            record["desired_state"] = "destroyed"
            record["observed_state"] = "destroying"
            record["active_operation_id"] = operation["operation_id"]
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=scenario_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "workspace_binding_id": record["workspace_binding_id"],
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return (
                operation["operation_id"],
                None,
                self.workspace_path(record["workspace_binding_id"]),
            )

    def finalize_scenario_destroy(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        workspace_evidence_sha256: str,
    ) -> dict[str, Any]:
        if not self._sha256(workspace_evidence_sha256):
            raise StoreError("scenario.destroy-invalid", "destroy evidence differs")
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item = state["scenarios"][key]
            record = item["record"]
            operation = state["operations"][operation_id]
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "destroying"
            ):
                raise StoreError("scenario.stale-fence", "destroy callback fence differs")
            before = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=before,
                after_revision=before,
                mutation_state="committed",
            )
            record["active_operation_id"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=before,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            tombstone = copy.deepcopy(record)
            result = {
                "scenario": tombstone,
                "unregistered": True,
                "destroy_evidence_sha256": workspace_evidence_sha256,
            }
            state["scenario_history"][key] = {
                **copy.deepcopy(item),
                "destroy_operation_id": operation_id,
                "destroy_evidence_sha256": workspace_evidence_sha256,
            }
            del state["scenarios"][key]
            state["requests"][request_id].update(
                {"status": "completed", "result": result}
            )
            state["state_revision"] += 1
            self._write_state(state)
            self._remove_workspace_husk(record.get("workspace_binding_id"))
            return result

    def _remove_workspace_husk(self, binding_id: Any) -> bool:
        """Remove a destroyed Scenario's now-empty workspace directory.

        The adapter has already proven the workspace bundle is absent; what
        remains is only the container directory the Harness itself created.
        Removal stays fail-closed: only the exact owned, non-symlink directory
        resolved from the binding is removed, and only while it contains
        nothing beyond Finder's ``.DS_Store`` metadata. Anything else means
        the directory is not provably an empty husk and it is left in place
        rather than disposed of. The durable destroy has already committed,
        so a husk that cannot be removed never fails the operation.
        """
        if not isinstance(binding_id, str):
            return False
        try:
            path = self.workspace_path(binding_id)
        except StoreError:
            return False
        try:
            details = path.lstat()
        except OSError:
            return False
        if (
            path.is_symlink()
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.getuid()
        ):
            return False
        try:
            leftovers = [
                entry for entry in path.iterdir() if entry.name != ".DS_Store"
            ]
            if leftovers:
                return False
            metadata = path / ".DS_Store"
            if metadata.is_file() and not metadata.is_symlink():
                metadata.unlink()
            path.rmdir()
        except OSError:
            return False
        return True

    def scenario_status(self, project_instance_id: str, scenario_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(self._scenario_key(project_instance_id, scenario_id))
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            return {"scenario": copy.deepcopy(item["record"])}

    def scenario_workspace(
        self, project_instance_id: str, scenario_id: str
    ) -> tuple[dict[str, Any], Path]:
        """Return the fenced Scenario record and its owned private workspace."""

        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(
                self._scenario_key(project_instance_id, scenario_id)
            )
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            record = copy.deepcopy(item["record"])
            binding_id = record.get("workspace_binding_id")
            if not isinstance(binding_id, str):
                raise StoreError("scenario.workspace-unavailable", "scenario has no workspace")
            workspace_path = self.workspace_path(binding_id)
            if (
                workspace_path.is_symlink()
                or not workspace_path.is_dir()
                or workspace_path.parent
                not in {self.workspace_root, self.legacy_workspace_root}
                or workspace_path.stat().st_uid != os.getuid()
            ):
                raise StoreError(
                    "scenario.workspace-unavailable", "scenario workspace is unavailable"
                )
            return record, workspace_path

    def list_scenarios(self, project_instance_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            values = [
                copy.deepcopy(item["record"])
                for item in state["scenarios"].values()
                if item["project_instance_id"] == project_instance_id
            ]
            return {
                "scenarios": sorted(
                    values,
                    key=lambda value: (
                        value["journal_head_sequence"],
                        value["scenario_id"],
                    ),
                    reverse=True,
                )
            }

    def list_participants(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(
                self._scenario_key(project_instance_id, scenario_id)
            )
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            participants, artifacts = self._participant_maps(item)
            configurations = []
            for participant_id in sorted(participants):
                participant = participants[participant_id]
                artifact = artifacts.get(participant_id)
                launch_spec = (
                    artifact.get("launch_spec")
                    if isinstance(artifact, dict)
                    else None
                )
                try:
                    validate_runtime_launch_spec(launch_spec)
                except ProtocolError as exc:
                    raise StoreError(
                        "host.state-invalid",
                        "participant launch configuration is invalid",
                    ) from exc
                configurations.append(
                    {
                        "participant_id": participant_id,
                        "participant_generation": participant[
                            "participant_generation"
                        ],
                        "runtime_profile_ref": launch_spec["runtime_profile_ref"],
                        "continuity_mode": launch_spec["continuity_mode"],
                        "model_binding": copy.deepcopy(launch_spec["model_binding"]),
                    }
                )
            return {
                "participants": [
                    copy.deepcopy(participants[key]) for key in sorted(participants)
                ],
                "participant_configurations": configurations,
            }

    def delivery_snapshot(
        self, project_instance_id: str, scenario_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return public Scenario/Participant facts used by policy routing."""

        with self._lock:
            state = self._read_state()
            item = state["scenarios"].get(
                self._scenario_key(project_instance_id, scenario_id)
            )
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            return (
                copy.deepcopy(item["record"]),
                [
                    copy.deepcopy(item["participants"][participant_id])
                    for participant_id in sorted(item["participants"])
                ],
            )

    def participant_delivery_input(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        runtime_binding_id: str,
        presentation_binding_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        """Resolve one exact ready receiver without exposing its private binding."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            _, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            if scenario["observed_state"] not in {"running", "degraded"}:
                raise StoreError(
                    "participant.invalid-transition",
                    "delivery requires an active Scenario",
                )
            if (
                record["participant_generation"] != participant_generation
                or record["desired_state"] != "running"
                or record["observed_state"] != "ready"
                or record["runtime_binding_id"] != runtime_binding_id
                or record["presentation_binding_id"] != presentation_binding_id
            ):
                raise StoreError(
                    "participant.stale-fence", "delivery receiver binding differs"
                )
            return (
                copy.deepcopy(record),
                copy.deepcopy(artifact),
                self.participant_private_path(
                    project_instance_id,
                    scenario_id,
                    participant_id,
                    participant_generation,
                ),
            )

    def add_participant(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        launch_spec: dict[str, Any],
        resolved_driver: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous
            item = state["scenarios"].get(key)
            if item is None:
                raise StoreError("scenario.not-found", "scenario does not exist")
            scenario = item["record"]
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            if scenario["observed_state"] not in {"closed", "running"}:
                raise StoreError(
                    "participant.invalid-transition",
                    "participant add requires a closed or running Scenario",
                )
            participants, artifacts = self._participant_maps(item)
            if participant_id in participants:
                raise StoreError(
                    "participant.already-exists", "participant already exists"
                )
            operation = self._new_participant_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="participant.add",
                scenario_id=scenario_id,
                participant_id=participant_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                participant_generation=None,
                participant_state_revision=None,
                desired_state_after="stopped",
                requested_continuity_mode=None,
                resulting_participant_generation=1,
            )
            record = {
                "scenario_id": scenario_id,
                "participant_id": participant_id,
                "participant_generation": 1,
                "state_revision": 1,
                "desired_state": "stopped",
                "observed_state": "stopped",
                "interaction_mode": launch_spec["interaction_mode"],
                "launch_spec_digest": canonical_json_sha256(launch_spec),
                "runtime_binding_id": None,
                "presentation_binding_id": None,
                "active_operation_id": None,
                "degraded": None,
                "journal_head_sequence": 0,
            }
            participants[participant_id] = record
            artifacts[participant_id] = {
                "launch_spec": copy.deepcopy(launch_spec),
                "resolved_driver": copy.deepcopy(resolved_driver),
                "runtime_create_request": None,
                "prepared_runtime_launch": None,
                "runtime_ready_ack": None,
                "presentation_create_request": None,
                "presentation_create_ack": None,
                "history": [],
            }
            scenario["participant_ids"] = sorted(participants)
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=0,
                after_revision=1,
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "desired_committed"
            operation["mutation_state"] = "committed"
            result = {"participant": copy.deepcopy(record)}
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "completed",
                "result": result,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation["operation_id"], result

    def begin_participant_start(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            item, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                record, participant_generation, participant_state_revision
            )
            if (
                scenario["desired_state"] != "running"
                or scenario["observed_state"]
                not in {"opening", "running", "degraded"}
                or (
                    scenario["observed_state"] == "degraded"
                    and scenario.get("degraded", {}).get("reason")
                    not in {"participant_fault", "participant_restore_incomplete"}
                )
            ):
                raise StoreError(
                    "participant.invalid-transition",
                    "participant start requires a resumable running Scenario",
                )
            # A detached record is inert in exactly the way a stopped one is:
            # no binding, no owned process. Its launch_spec survives, and start
            # builds fresh runtime artifacts anyway, so treating the two alike
            # is what keeps a detached participant from becoming a dead end.
            if record["observed_state"] not in {"stopped", "detached"}:
                raise StoreError(
                    "participant.invalid-transition", "participant is not stopped"
                )
            operation = self._new_participant_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="participant.start",
                scenario_id=scenario_id,
                participant_id=participant_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                participant_generation=participant_generation,
                participant_state_revision=participant_state_revision,
                desired_state_after="running",
                requested_continuity_mode=artifact["launch_spec"]["continuity_mode"],
                resulting_participant_generation=participant_generation,
            )
            record["desired_state"] = "running"
            record["observed_state"] = "starting"
            record["active_operation_id"] = operation["operation_id"]
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=participant_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            execution = {
                "context": {
                    "scenario_id": scenario_id,
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "operation_id": operation["operation_id"],
                    "operation_generation": operation["operation_generation"],
                    "driver_registry_digest": artifact["resolved_driver"][
                        "driver_registry_digest"
                    ],
                    "capability_snapshot_digest": artifact["resolved_driver"][
                        "capability_snapshot_digest"
                    ],
                },
                "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                "resolved_driver": copy.deepcopy(artifact["resolved_driver"]),
                "private_root": str(
                    self.participant_private_path(
                        project_instance_id,
                        scenario_id,
                        participant_id,
                        participant_generation,
                    )
                ),
                "workspace_path": str(
                    self.workspace_path(scenario["workspace_binding_id"])
                ),
            }
            return operation["operation_id"], None, execution

    def finalize_participant_start(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        artifacts: dict[str, Any],
        supervision_observation: dict[str, Any],
    ) -> dict[str, Any]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, record, stored = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            expected_generation = operation["fence"]["participant_generation"]
            expected_revision = (
                operation["fence"]["participant_state_revision"] + 1
            )
            if operation["operation_kind"] == "participant.replace":
                expected_generation = operation[
                    "resulting_participant_generation"
                ]
                expected_revision += 1
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "starting"
                or record["participant_generation"] != expected_generation
                or record["state_revision"] != expected_revision
            ):
                raise StoreError(
                    "participant.stale-fence", "participant callback fence differs"
                )
            revision = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
            )
            runtime_ack = artifacts["runtime_ready_ack"]
            presentation_ack = artifacts["presentation_create_ack"]
            record["observed_state"] = "ready"
            record["runtime_binding_id"] = runtime_ack["binding"][
                "runtime_binding_id"
            ]
            record["presentation_binding_id"] = (
                None
                if presentation_ack is None
                else presentation_ack["binding"]["presentation_instance_id"]
            )
            record["active_operation_id"] = None
            record["degraded"] = None
            record["state_revision"] += 1
            stored.setdefault("history", []).append(
                {
                    "participant_generation": record["participant_generation"],
                    "runtime_create_request": copy.deepcopy(
                        artifacts["runtime_create_request"]
                    ),
                    "prepared_runtime_launch": copy.deepcopy(
                        artifacts["prepared_runtime_launch"]
                    ),
                    "runtime_ready_ack": copy.deepcopy(
                        artifacts["runtime_ready_ack"]
                    ),
                    "presentation_create_request": copy.deepcopy(
                        artifacts["presentation_create_request"]
                    ),
                    "presentation_create_ack": copy.deepcopy(
                        artifacts["presentation_create_ack"]
                    ),
                }
            )
            stored.update(copy.deepcopy(artifacts))
            self._activate_resource_leases(
                state,
                item,
                record,
                supervision_observation,
            )
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            participants, _ = self._participant_maps(item)
            if (
                scenario["desired_state"] == "running"
                and scenario["observed_state"] == "degraded"
                and scenario.get("degraded", {}).get("reason")
                in {"participant_fault", "participant_restore_incomplete"}
                and not any(
                    participant["observed_state"] == "degraded"
                    for participant in participants.values()
                )
            ):
                scenario["observed_state"] = "running"
                scenario["degraded"] = None
                scenario["state_revision"] += 1
                scenario["journal_head_sequence"] = state[
                    "journal_head_sequence"
                ]
            result = {"participant": copy.deepcopy(record)}
            request = state["requests"][request_id]
            request.pop("pending_external_result", None)
            request["status"] = "completed"
            request["result"] = result
            state["state_revision"] += 1
            self._write_state(state)
            return result

    def begin_participant_stop(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        operation_kind: str = "participant.stop",
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            _, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                record, participant_generation, participant_state_revision
            )
            if record["observed_state"] not in {"ready", "degraded"}:
                raise StoreError(
                    "participant.invalid-transition", "participant is not active"
                )
            operation = self._new_participant_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind=operation_kind,
                scenario_id=scenario_id,
                participant_id=participant_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                participant_generation=participant_generation,
                participant_state_revision=participant_state_revision,
                desired_state_after="stopped",
                requested_continuity_mode=None,
                resulting_participant_generation=participant_generation,
            )
            record["desired_state"] = "stopped"
            record["observed_state"] = "stopping"
            record["active_operation_id"] = operation["operation_id"]
            record["degraded"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=participant_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation["operation_id"], None, {
                "context": {
                    "scenario_id": scenario_id,
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "operation_id": operation["operation_id"],
                    "operation_generation": operation["operation_generation"],
                    "driver_registry_digest": artifact["resolved_driver"][
                        "driver_registry_digest"
                    ],
                    "capability_snapshot_digest": artifact["resolved_driver"][
                        "capability_snapshot_digest"
                    ],
                },
                "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                "resolved_driver": copy.deepcopy(artifact["resolved_driver"]),
                "runtime_ready_ack": copy.deepcopy(artifact["runtime_ready_ack"]),
                "presentation_create_ack": copy.deepcopy(
                    artifact["presentation_create_ack"]
                ),
                "private_root": str(
                    self.participant_private_path(
                        project_instance_id,
                        scenario_id,
                        participant_id,
                        participant_generation,
                    )
                ),
            }

    def finalize_participant_stop(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        release_evidence_sha256: str,
    ) -> dict[str, Any]:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, _, record, artifact = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "stopping"
            ):
                raise StoreError(
                    "participant.stale-fence", "participant callback fence differs"
                )
            revision = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
            )
            self._release_participant_resources(
                item, record, release_evidence_sha256
            )
            record.update(
                {
                    "observed_state": "stopped",
                    "runtime_binding_id": None,
                    "presentation_binding_id": None,
                    "active_operation_id": None,
                    "degraded": None,
                    "state_revision": revision + 1,
                }
            )
            for field in (
                "runtime_create_request",
                "prepared_runtime_launch",
                "runtime_ready_ack",
                "presentation_create_request",
                "presentation_create_ack",
            ):
                artifact[field] = None
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            result = {"participant": copy.deepcopy(record)}
            request = state["requests"][request_id]
            request["status"] = "completed"
            request["result"] = result
            state["state_revision"] += 1
            self._write_state(state)
            return result

    def begin_participant_recover(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Fence one degraded generation before bounded driver recovery."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            _, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                record, participant_generation, participant_state_revision
            )
            if (
                scenario["desired_state"] != "running"
                or scenario["observed_state"] != "degraded"
                or scenario.get("degraded", {}).get("reason")
                not in {"participant_fault", "participant_restore_incomplete"}
                or scenario.get("active_operation_id") is not None
            ):
                raise StoreError(
                    "participant.invalid-transition",
                    "participant recovery requires a resumable degraded Scenario",
                )
            if (
                record["observed_state"] != "degraded"
                or record.get("active_operation_id") is not None
                or record.get("degraded", {}).get("repair_action")
                != "participant.recover"
            ):
                raise StoreError(
                    "participant.invalid-transition",
                    "participant recovery requires an exact degraded participant",
                )
            next_generation = participant_generation + 1
            operation = self._new_participant_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="participant.recover",
                scenario_id=scenario_id,
                participant_id=participant_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                participant_generation=participant_generation,
                participant_state_revision=participant_state_revision,
                desired_state_after="stopped",
                requested_continuity_mode=None,
                resulting_participant_generation=next_generation,
            )
            record["desired_state"] = "stopped"
            record["observed_state"] = "recovering"
            record["active_operation_id"] = operation["operation_id"]
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=participant_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            self._append_operation_event(
                state,
                operation,
                event="external_started",
                before_revision=record["state_revision"],
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "executing_external"
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation["operation_id"], None, {
                "context": {
                    "scenario_id": scenario_id,
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "operation_id": operation["operation_id"],
                    "operation_generation": operation["operation_generation"],
                    "driver_registry_digest": artifact["resolved_driver"][
                        "driver_registry_digest"
                    ],
                    "capability_snapshot_digest": artifact["resolved_driver"][
                        "capability_snapshot_digest"
                    ],
                },
                "next_participant_generation": next_generation,
                "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                "resolved_driver": copy.deepcopy(artifact["resolved_driver"]),
                "runtime_ready_ack": copy.deepcopy(artifact["runtime_ready_ack"]),
                "presentation_create_ack": copy.deepcopy(
                    artifact["presentation_create_ack"]
                ),
                "degraded": copy.deepcopy(record["degraded"]),
                "private_root": str(
                    self.participant_private_path(
                        project_instance_id,
                        scenario_id,
                        participant_id,
                        participant_generation,
                    )
                ),
            }

    def finalize_participant_recover(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        recovery: dict[str, Any],
    ) -> dict[str, Any]:
        """Rotate only the recovered participant to a fresh stopped generation."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            previous_generation = operation["fence"]["participant_generation"]
            next_generation = operation["resulting_participant_generation"]
            if (
                record["active_operation_id"] != operation_id
                or record["observed_state"] != "recovering"
                or record["participant_generation"] != previous_generation
                or recovery.get("previous_participant_generation")
                != previous_generation
                or recovery.get("next_participant_generation") != next_generation
            ):
                raise StoreError(
                    "participant.stale-fence",
                    "participant recovery callback fence differs",
                )
            revision = record["state_revision"]
            evidence = recovery["owned_resource_evidence_sha256"]
            self._append_operation_event(
                state,
                operation,
                event="external_succeeded",
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
            )
            self._release_participant_resources(item, record, evidence)
            artifact.setdefault("history", []).append(
                {
                    "participant_generation": previous_generation,
                    "recovery": copy.deepcopy(recovery),
                }
            )
            record.update(
                {
                    "participant_generation": next_generation,
                    "observed_state": "stopped",
                    "runtime_binding_id": None,
                    "presentation_binding_id": None,
                    "active_operation_id": None,
                    "degraded": None,
                    "state_revision": revision + 1,
                }
            )
            for field in (
                "runtime_create_request",
                "prepared_runtime_launch",
                "runtime_ready_ack",
                "presentation_create_request",
                "presentation_create_ack",
            ):
                artifact[field] = None
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "succeeded"
            operation["mutation_state"] = "committed"
            participants, _ = self._participant_maps(item)
            if (
                scenario["desired_state"] == "running"
                and scenario["observed_state"] == "degraded"
                and scenario.get("degraded", {}).get("reason")
                in {"participant_fault", "participant_restore_incomplete"}
                and not any(
                    participant["observed_state"] == "degraded"
                    for participant in participants.values()
                )
            ):
                scenario["observed_state"] = "running"
                scenario["degraded"] = None
                scenario["state_revision"] += 1
                scenario["journal_head_sequence"] = state[
                    "journal_head_sequence"
                ]
            result = {"participant": copy.deepcopy(record)}
            request = state["requests"][request_id]
            request.pop("pending_external_result", None)
            request["status"] = "completed"
            request["result"] = result
            state["state_revision"] += 1
            self._write_state(state)
            return result

    def begin_participant_replace(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        launch_spec: dict[str, Any],
        resolved_driver: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """Fence an exact old generation after the replacement was validated."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            previous = self._previous_request(state, request_id, request_digest)
            if previous is not None:
                return previous[0], previous[1], None
            _, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                record, participant_generation, participant_state_revision
            )
            if (
                scenario["observed_state"] not in {"closed", "running", "degraded"}
                or scenario.get("active_operation_id") is not None
                or record["observed_state"]
                not in {"stopped", "detached", "ready", "degraded"}
                or record["desired_state"] not in {"stopped", "detached", "running"}
            ):
                raise StoreError(
                    "participant.invalid-transition",
                    "participant replacement requires a stable replaceable generation",
                )
            # A replacement generation is never born detached: that state only
            # describes a record whose binding was already released.
            desired_after = (
                "stopped"
                if record["desired_state"] == "detached"
                else record["desired_state"]
            )
            next_generation = participant_generation + 1
            operation = self._new_participant_operation(
                state,
                request_id=request_id,
                request_digest=request_digest,
                host_generation=host_generation,
                operation_kind="participant.replace",
                scenario_id=scenario_id,
                participant_id=participant_id,
                scenario_generation=scenario_generation,
                scenario_state_revision=scenario_state_revision,
                participant_generation=participant_generation,
                participant_state_revision=participant_state_revision,
                desired_state_after=desired_after,
                requested_continuity_mode=(
                    launch_spec["continuity_mode"]
                    if desired_after == "running"
                    else None
                ),
                resulting_participant_generation=next_generation,
                replacement_launch_spec_digest=canonical_json_sha256(
                    launch_spec
                ),
            )
            source_state = record["observed_state"]
            cleanup_kind = {
                "stopped": "none",
                "ready": "stop",
                "degraded": "repair",
            }[source_state]
            source_degraded = copy.deepcopy(record["degraded"])
            record["observed_state"] = "replacing"
            record["active_operation_id"] = operation["operation_id"]
            record["degraded"] = None
            record["state_revision"] += 1
            self._append_operation_event(
                state,
                operation,
                event="desired_state_committed",
                before_revision=participant_state_revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            if cleanup_kind != "none":
                self._append_operation_event(
                    state,
                    operation,
                    event="external_started",
                    before_revision=record["state_revision"],
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                operation["state"] = "executing_external"
            else:
                operation["state"] = "desired_committed"
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["mutation_state"] = "committed"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation["operation_id"],
                "status": "pending",
                "result": None,
                "error": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation["operation_id"], None, {
                "context": {
                    "scenario_id": scenario_id,
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "operation_id": operation["operation_id"],
                    "operation_generation": operation["operation_generation"],
                    "driver_registry_digest": artifact["resolved_driver"][
                        "driver_registry_digest"
                    ],
                    "capability_snapshot_digest": artifact["resolved_driver"][
                        "capability_snapshot_digest"
                    ],
                },
                "cleanup_kind": cleanup_kind,
                "next_participant_generation": next_generation,
                "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                "resolved_driver": copy.deepcopy(artifact["resolved_driver"]),
                "runtime_ready_ack": copy.deepcopy(
                    artifact["runtime_ready_ack"]
                ),
                "presentation_create_ack": copy.deepcopy(
                    artifact["presentation_create_ack"]
                ),
                "degraded": source_degraded,
                "private_root": str(
                    self.participant_private_path(
                        project_instance_id,
                        scenario_id,
                        participant_id,
                        participant_generation,
                    )
                ),
                "replacement_launch_spec": copy.deepcopy(launch_spec),
                "replacement_resolved_driver": copy.deepcopy(
                    resolved_driver
                ),
            }

    def commit_participant_replacement(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        launch_spec: dict[str, Any],
        resolved_driver: dict[str, Any],
        cleanup_kind: str,
        owned_resource_evidence_sha256: str | None,
        recovery: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """CAS the validated new launch binding after old-generation cleanup."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, record, artifact = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            previous_generation = operation["fence"]["participant_generation"]
            next_generation = operation["resulting_participant_generation"]
            if (
                operation["operation_kind"] != "participant.replace"
                or operation["replacement_launch_spec_digest"]
                != canonical_json_sha256(launch_spec)
                or record["active_operation_id"] != operation_id
                or record["observed_state"] != "replacing"
                or record["participant_generation"] != previous_generation
                or record["state_revision"]
                != operation["fence"]["participant_state_revision"] + 1
                or cleanup_kind not in {"none", "stop", "repair"}
                or (cleanup_kind == "none")
                != (owned_resource_evidence_sha256 is None)
                or (
                    cleanup_kind != "none"
                    and not self._sha256(owned_resource_evidence_sha256)
                )
                or (cleanup_kind == "repair") != (recovery is not None)
            ):
                raise StoreError(
                    "participant.stale-fence",
                    "participant replacement CAS fence differs",
                )
            revision = record["state_revision"]
            if cleanup_kind != "none":
                assert owned_resource_evidence_sha256 is not None
                self._append_operation_event(
                    state,
                    operation,
                    event="external_succeeded",
                    before_revision=revision,
                    after_revision=revision,
                    mutation_state="committed",
                )
                self._release_participant_resources(
                    item, record, owned_resource_evidence_sha256
                )
            artifact.setdefault("history", []).append(
                {
                    "participant_generation": previous_generation,
                    "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                    "resolved_driver": copy.deepcopy(
                        artifact["resolved_driver"]
                    ),
                    "runtime_create_request": copy.deepcopy(
                        artifact["runtime_create_request"]
                    ),
                    "prepared_runtime_launch": copy.deepcopy(
                        artifact["prepared_runtime_launch"]
                    ),
                    "runtime_ready_ack": copy.deepcopy(
                        artifact["runtime_ready_ack"]
                    ),
                    "presentation_create_request": copy.deepcopy(
                        artifact["presentation_create_request"]
                    ),
                    "presentation_create_ack": copy.deepcopy(
                        artifact["presentation_create_ack"]
                    ),
                    "replacement_cleanup": {
                        "cleanup_kind": cleanup_kind,
                        "owned_resource_evidence_sha256": (
                            owned_resource_evidence_sha256
                        ),
                        "recovery": copy.deepcopy(recovery),
                    },
                }
            )
            desired_after = operation["desired_state_after"]
            record.update(
                {
                    "participant_generation": next_generation,
                    "desired_state": desired_after,
                    "observed_state": (
                        "starting" if desired_after == "running" else "stopped"
                    ),
                    "interaction_mode": launch_spec["interaction_mode"],
                    "launch_spec_digest": canonical_json_sha256(launch_spec),
                    "runtime_binding_id": None,
                    "presentation_binding_id": None,
                    "active_operation_id": (
                        operation_id if desired_after == "running" else None
                    ),
                    "degraded": None,
                    "state_revision": revision + 1,
                }
            )
            artifact["launch_spec"] = copy.deepcopy(launch_spec)
            artifact["resolved_driver"] = copy.deepcopy(resolved_driver)
            for field in (
                "runtime_create_request",
                "prepared_runtime_launch",
                "runtime_ready_ack",
                "presentation_create_request",
                "presentation_create_ack",
            ):
                artifact[field] = None
            request = state["requests"][request_id]
            request.pop("pending_external_result", None)
            execution = None
            if desired_after == "running":
                self._append_operation_event(
                    state,
                    operation,
                    event="external_started",
                    before_revision=record["state_revision"],
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                operation["state"] = "executing_external"
                execution = {
                    "context": {
                        "scenario_id": scenario_id,
                        "participant_id": participant_id,
                        "participant_generation": next_generation,
                        "operation_id": operation_id,
                        "operation_generation": operation[
                            "operation_generation"
                        ],
                        "driver_registry_digest": resolved_driver[
                            "driver_registry_digest"
                        ],
                        "capability_snapshot_digest": resolved_driver[
                            "capability_snapshot_digest"
                        ],
                    },
                    "launch_spec": copy.deepcopy(launch_spec),
                    "resolved_driver": copy.deepcopy(resolved_driver),
                    "private_root": str(
                        self.participant_private_path(
                            project_instance_id,
                            scenario_id,
                            participant_id,
                            next_generation,
                        )
                    ),
                    "workspace_path": str(
                        self.workspace_path(scenario["workspace_binding_id"])
                    ),
                }
            else:
                self._append_operation_event(
                    state,
                    operation,
                    event="finalize_committed",
                    before_revision=revision,
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                operation["state"] = "succeeded"
                request["status"] = "completed"
                participants, _ = self._participant_maps(item)
                if (
                    scenario["desired_state"] == "running"
                    and scenario["observed_state"] == "degraded"
                    and scenario.get("degraded", {}).get("reason")
                    in {"participant_fault", "participant_restore_incomplete"}
                    and not any(
                        participant["observed_state"] == "degraded"
                        for participant in participants.values()
                    )
                ):
                    scenario["observed_state"] = "running"
                    scenario["degraded"] = None
                    scenario["state_revision"] += 1
                    scenario["journal_head_sequence"] = state[
                        "journal_head_sequence"
                    ]
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["mutation_state"] = "committed"
            result = {"participant": copy.deepcopy(record)}
            if desired_after == "stopped":
                request["result"] = result
            state["state_revision"] += 1
            self._write_state(state)
            return result, execution

    def fail_participant_replace_before_cas(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        cleanup_pending: bool,
        owned_resource_evidence_sha256: str | None = None,
    ) -> None:
        """Fail closed while retaining the old generation and launch binding."""

        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, record, _ = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            if (
                operation["operation_kind"] != "participant.replace"
                or record["active_operation_id"] != operation_id
                or record["observed_state"] != "replacing"
                or record["participant_generation"]
                != operation["fence"]["participant_generation"]
            ):
                raise StoreError(
                    "participant.stale-fence",
                    "participant replacement failure fence differs",
                )
            if (
                owned_resource_evidence_sha256 is not None
                and not self._sha256(owned_resource_evidence_sha256)
            ):
                raise StoreError(
                    "resource.release-invalid",
                    "participant replacement cleanup evidence differs",
                )
            revision = record["state_revision"]
            failure_code = "lifecycle.replace-cleanup-failed"
            self._append_operation_event(
                state,
                operation,
                event="external_failed",
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
                error_code=failure_code,
            )
            evidence = owned_resource_evidence_sha256 or canonical_json_sha256(
                {
                    "operation_id": operation_id,
                    "participant_generation": record[
                        "participant_generation"
                    ],
                    "cleanup_pending": cleanup_pending,
                }
            )
            record["observed_state"] = "degraded"
            record["active_operation_id"] = None
            record["state_revision"] += 1
            record["degraded"] = {
                "reason": "cleanup_pending",
                "cleanup_pending": cleanup_pending,
                "owned_resource_evidence_sha256": evidence,
                "repair_action": "participant.recover",
            }
            self._stale_participant_resources(
                item, record, "lifecycle_failed"
            )
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "failed"
            operation["mutation_state"] = "committed"
            operation["failure_code"] = failure_code
            request = state["requests"][request_id]
            request.pop("pending_external_result", None)
            request["status"] = "failed"
            request["error"] = {
                "code": "operation.external-failure",
                "message": "participant replacement cleanup failed",
                "mutation_state": "committed",
                "retryable": True,
            }
            if scenario["observed_state"] in {"opening", "running"}:
                scenario["observed_state"] = "degraded"
                scenario["state_revision"] += 1
                scenario["degraded"] = {
                    "reason": "participant_fault",
                    "cleanup_pending": cleanup_pending,
                    "owned_resource_evidence_sha256": evidence,
                    "repair_action": "scenario.repair",
                }
            state["state_revision"] += 1
            self._write_state(state)

    def fail_participant_operation(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        request_id: str,
        operation_id: str,
        reason: str,
        failure_code: str,
        cleanup_pending: bool,
        failure_artifacts: dict[str, Any] | None = None,
        owned_resource_evidence_sha256: str | None = None,
    ) -> None:
        key = self._scenario_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            item, scenario, record, stored = self._participant_state(
                state, key, participant_id
            )
            operation = state["operations"][operation_id]
            revision = record["state_revision"]
            self._append_operation_event(
                state,
                operation,
                event="external_failed",
                before_revision=revision,
                after_revision=revision,
                mutation_state="committed",
                error_code=failure_code,
            )
            record["observed_state"] = "degraded"
            record["active_operation_id"] = None
            record["state_revision"] += 1
            if failure_artifacts is not None:
                runtime_ack = failure_artifacts["runtime_ready_ack"]
                presentation_ack = failure_artifacts["presentation_create_ack"]
                record["runtime_binding_id"] = runtime_ack["binding"][
                    "runtime_binding_id"
                ]
                record["presentation_binding_id"] = (
                    None
                    if presentation_ack is None
                    else presentation_ack["binding"]["presentation_instance_id"]
                )
                stored.setdefault("history", []).append(
                    {
                        "participant_generation": record[
                            "participant_generation"
                        ],
                        **copy.deepcopy(failure_artifacts),
                    }
                )
                stored.update(copy.deepcopy(failure_artifacts))
            if (
                owned_resource_evidence_sha256 is not None
                and not self._sha256(owned_resource_evidence_sha256)
            ):
                raise StoreError(
                    "resource.release-invalid",
                    "resource cleanup evidence differs",
                )
            record["degraded"] = {
                "reason": reason,
                "cleanup_pending": cleanup_pending,
                "owned_resource_evidence_sha256": (
                    owned_resource_evidence_sha256
                    or canonical_json_sha256(
                        {
                            "operation_id": operation_id,
                            "participant_generation": record[
                                "participant_generation"
                            ],
                            "cleanup_pending": cleanup_pending,
                        }
                    )
                ),
                "repair_action": "participant.recover",
            }
            self._stale_participant_resources(
                item, record, "lifecycle_failed"
            )
            self._append_operation_event(
                state,
                operation,
                event="finalize_committed",
                before_revision=revision,
                after_revision=record["state_revision"],
                mutation_state="committed",
            )
            record["journal_head_sequence"] = state["journal_head_sequence"]
            operation["state"] = "failed"
            operation["mutation_state"] = "committed"
            operation["failure_code"] = failure_code
            request = state["requests"][request_id]
            request["status"] = "failed"
            request["error"] = {
                "code": "operation.external-failure",
                "message": "participant lifecycle operation failed",
                "mutation_state": "committed",
                "retryable": True,
            }
            if scenario["observed_state"] in {"opening", "running"}:
                scenario["observed_state"] = "degraded"
                scenario["state_revision"] += 1
                scenario["degraded"] = {
                    "reason": "participant_fault",
                    "cleanup_pending": cleanup_pending,
                    "owned_resource_evidence_sha256": record["degraded"][
                        "owned_resource_evidence_sha256"
                    ],
                    "repair_action": "scenario.repair",
                }
            state["state_revision"] += 1
            self._write_state(state)

    def participant_status_input(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            _, scenario, record, artifact = self._participant_state(
                state,
                self._scenario_key(project_instance_id, scenario_id),
                participant_id,
            )
            self._check_scenario_fence(
                scenario, scenario_generation, scenario_state_revision
            )
            self._check_participant_fence(
                record, participant_generation, participant_state_revision
            )
            return copy.deepcopy(record), copy.deepcopy(artifact)

    def participant_private_path(
        self,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> Path:
        digest = hashlib.sha256(
            f"{project_instance_id}\0{scenario_id}\0{participant_id}".encode("utf-8")
        ).hexdigest()
        return self.participant_root / digest[:32] / f"generation-{participant_generation}"

    def resource_supervision_inputs(
        self, host_generation: int
    ) -> list[dict[str, Any]]:
        """Freeze exact active bindings for one bounded supervision pass."""

        with self._lock:
            state = self._read_state()
            if state["host_generation"] != host_generation:
                return []
            result: list[dict[str, Any]] = []
            for item in state["scenarios"].values():
                scenario = item["record"]
                if scenario["desired_state"] != "running":
                    continue
                participants, artifacts = self._participant_maps(item)
                for participant_id in sorted(participants):
                    record = participants[participant_id]
                    artifact = artifacts[participant_id]
                    if (
                        record["desired_state"] != "running"
                        or record["observed_state"] not in {"ready", "degraded"}
                        or not isinstance(record.get("runtime_binding_id"), str)
                        or not isinstance(artifact.get("runtime_ready_ack"), dict)
                    ):
                        continue
                    driver_payload = {
                        "launch_spec": copy.deepcopy(artifact["launch_spec"]),
                        "resolved_driver": copy.deepcopy(
                            artifact["resolved_driver"]
                        ),
                        "runtime_ready_ack": copy.deepcopy(
                            artifact["runtime_ready_ack"]
                        ),
                        "presentation_create_ack": copy.deepcopy(
                            artifact.get("presentation_create_ack")
                        ),
                        "private_root": str(
                            self.participant_private_path(
                                item["project_instance_id"],
                                scenario["scenario_id"],
                                participant_id,
                                record["participant_generation"],
                            )
                        ),
                    }
                    result.append(
                        {
                            "project_instance_id": item["project_instance_id"],
                            "scenario_id": scenario["scenario_id"],
                            "participant_id": participant_id,
                            "participant_generation": record[
                                "participant_generation"
                            ],
                            "runtime_binding_id": record["runtime_binding_id"],
                            "driver_payload": driver_payload,
                            "artifacts": {
                                "runtime_ready_ack": copy.deepcopy(
                                    artifact["runtime_ready_ack"]
                                ),
                                "presentation_create_ack": copy.deepcopy(
                                    artifact.get("presentation_create_ack")
                                ),
                            },
                        }
                    )
            return result

    def commit_resource_supervision(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        runtime_binding_id: str,
        observation: dict[str, Any],
    ) -> bool:
        """Commit only an exact same-holder observation; never take over."""

        with self._lock:
            state = self._read_state()
            try:
                item, scenario, record, _ = self._participant_state(
                    state,
                    self._scenario_key(project_instance_id, scenario_id),
                    participant_id,
                )
            except StoreError as exc:
                if exc.code in {"scenario.not-found", "participant.not-found"}:
                    return False
                raise
            if (
                scenario["desired_state"] != "running"
                or record["desired_state"] != "running"
                or record["observed_state"] not in {"ready", "degraded"}
                or record["participant_generation"] != participant_generation
                or record.get("runtime_binding_id") != runtime_binding_id
                or observation.get("runtime_binding_id") != runtime_binding_id
            ):
                return False
            changed = self._activate_resource_leases(
                state, item, record, observation
            )
            if changed:
                state["state_revision"] += 1
                self._write_state(state)
            return changed

    def mark_resource_supervision_stale(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        runtime_binding_id: str,
        reason: str,
    ) -> bool:
        """Preserve ownership evidence when a binding cannot be re-observed."""

        with self._lock:
            state = self._read_state()
            try:
                item, _, record, _ = self._participant_state(
                    state,
                    self._scenario_key(project_instance_id, scenario_id),
                    participant_id,
                )
            except StoreError as exc:
                if exc.code in {"scenario.not-found", "participant.not-found"}:
                    return False
                raise
            if (
                record["participant_generation"] != participant_generation
                or record.get("runtime_binding_id") != runtime_binding_id
            ):
                return False
            changed = self._stale_participant_resources(item, record, reason)
            if changed:
                state["state_revision"] += 1
                self._write_state(state)
            return changed

    @staticmethod
    def _resource_holder(
        item: dict[str, Any], participant: dict[str, Any]
    ) -> dict[str, Any]:
        scenario = item["record"]
        return {
            "project_instance_id": item["project_instance_id"],
            "scenario_id": scenario["scenario_id"],
            "scenario_generation": scenario["scenario_generation"],
            "participant_id": participant["participant_id"],
            "participant_generation": participant["participant_generation"],
            "runtime_binding_id": participant["runtime_binding_id"],
        }

    @classmethod
    def _resource_leases(cls, item: dict[str, Any]) -> dict[str, Any]:
        leases = item.setdefault("resource_leases", {})
        if not isinstance(leases, dict):
            raise StoreError("host.state-invalid", "resource lease ledger differs")
        for lease_id, lease in leases.items():
            if lease_id != lease.get("lease_id"):
                raise StoreError("host.state-invalid", "resource lease identity differs")
            cls._validate_resource_lease(lease)
        return leases

    @classmethod
    def _public_resource_leases(cls, item: dict[str, Any]) -> list[dict[str, Any]]:
        leases = cls._resource_leases(item)
        return [copy.deepcopy(leases[key]) for key in sorted(leases)]

    @classmethod
    def _validate_resource_lease(cls, lease: Any) -> None:
        fields = {
            "schema_version",
            "lease_id",
            "lease_revision",
            "resource_class",
            "resource_identity_sha256",
            "holder",
            "process_start_identity_sha256",
            "boot_id_sha256",
            "heartbeat_sequence",
            "heartbeat_at_unix_ms",
            "fencing_token_sha256",
            "status",
            "stale_reason",
            "observation_evidence_sha256",
            "release_evidence_sha256",
        }
        holder_fields = {
            "project_instance_id",
            "scenario_id",
            "scenario_generation",
            "participant_id",
            "participant_generation",
            "runtime_binding_id",
        }
        holder = lease.get("holder") if isinstance(lease, dict) else None
        if (
            not isinstance(lease, dict)
            or set(lease) != fields
            or lease["schema_version"] != RESOURCE_LEASE_SCHEMA_VERSION
            or not isinstance(lease["lease_id"], str)
            or not lease["lease_id"].startswith("lease-")
            or not cls._positive_int(lease["lease_revision"])
            or lease["resource_class"] not in RESOURCE_CLASSES
            or not cls._sha256(lease["resource_identity_sha256"])
            or not isinstance(holder, dict)
            or set(holder) != holder_fields
            or any(
                not isinstance(holder[field], str) or not holder[field]
                for field in (
                    "project_instance_id",
                    "scenario_id",
                    "participant_id",
                    "runtime_binding_id",
                )
            )
            or not cls._positive_int(holder["scenario_generation"])
            or not cls._positive_int(holder["participant_generation"])
            or not cls._sha256(lease["process_start_identity_sha256"])
            or not cls._sha256(lease["boot_id_sha256"])
            or not cls._positive_int(lease["heartbeat_sequence"])
            or not cls._nonnegative_int(lease["heartbeat_at_unix_ms"])
            or not cls._sha256(lease["fencing_token_sha256"])
            or lease["status"] not in {"active", "stale", "released"}
            or not cls._sha256(lease["observation_evidence_sha256"])
        ):
            raise StoreError("host.state-invalid", "resource lease schema differs")
        if (
            (lease["status"] == "stale")
            != isinstance(lease["stale_reason"], str)
            or (
                lease["status"] == "released"
                and not cls._sha256(lease["release_evidence_sha256"])
            )
            or (
                lease["status"] != "released"
                and lease["release_evidence_sha256"] is not None
            )
            or (lease["status"] != "stale" and lease["stale_reason"] is not None)
        ):
            raise StoreError("host.state-invalid", "resource lease lifecycle differs")

    @classmethod
    def _validate_supervision_observation(cls, observation: Any) -> None:
        fields = {
            "schema_version",
            "runtime_binding_id",
            "process_start_identity_sha256",
            "boot_id_sha256",
            "heartbeat_sequence",
            "heartbeat_at_unix_ms",
            "fencing_token_sha256",
            "resources",
            "observation_evidence_sha256",
        }
        if (
            not isinstance(observation, dict)
            or set(observation) != fields
            or observation["schema_version"] != RESOURCE_LEASE_SCHEMA_VERSION
            or not isinstance(observation["runtime_binding_id"], str)
            or not observation["runtime_binding_id"]
            or not cls._sha256(observation["process_start_identity_sha256"])
            or not cls._sha256(observation["boot_id_sha256"])
            or not cls._positive_int(observation["heartbeat_sequence"])
            or not cls._nonnegative_int(observation["heartbeat_at_unix_ms"])
            or not cls._sha256(observation["fencing_token_sha256"])
            or not isinstance(observation["resources"], list)
            or not observation["resources"]
            or not cls._sha256(observation["observation_evidence_sha256"])
        ):
            raise StoreError("resource.observation-invalid", "resource observation differs")
        identities: set[tuple[str, str]] = set()
        for resource in observation["resources"]:
            if (
                not isinstance(resource, dict)
                or set(resource)
                != {"resource_class", "resource_identity_sha256", "state"}
                or resource["resource_class"] not in RESOURCE_CLASSES
                or not cls._sha256(resource["resource_identity_sha256"])
                or resource["state"] != "held"
            ):
                raise StoreError(
                    "resource.observation-invalid", "resource observation differs"
                )
            identities.add(
                (resource["resource_class"], resource["resource_identity_sha256"])
            )
        if len(identities) != len(observation["resources"]):
            raise StoreError(
                "resource.observation-invalid", "resource observation differs"
            )
        evidence = {
            key: copy.deepcopy(value)
            for key, value in observation.items()
            if key != "observation_evidence_sha256"
        }
        if canonical_json_sha256(evidence) != observation["observation_evidence_sha256"]:
            raise StoreError(
                "resource.observation-invalid", "resource observation evidence differs"
            )

    @classmethod
    def _activate_resource_leases(
        cls,
        state: dict[str, Any],
        item: dict[str, Any],
        participant: dict[str, Any],
        observation: dict[str, Any],
    ) -> bool:
        cls._validate_supervision_observation(observation)
        holder = cls._resource_holder(item, participant)
        if observation["runtime_binding_id"] != holder["runtime_binding_id"]:
            raise StoreError(
                "resource.observation-invalid", "resource binding differs"
            )
        leases = cls._resource_leases(item)
        changed = False
        observed_ids: set[str] = set()
        for resource in observation["resources"]:
            conflicting = cls._resource_conflict(
                state, holder=holder, resource=resource
            )
            if conflicting is not None:
                raise StoreError(
                    "resource.conflict",
                    "resource remains held or release is unproven",
                )
            lease_id = "lease-" + canonical_json_sha256(
                {"holder": holder, **resource}
            )[:32]
            observed_ids.add(lease_id)
            existing = leases.get(lease_id)
            immutable = {
                "resource_class": resource["resource_class"],
                "resource_identity_sha256": resource[
                    "resource_identity_sha256"
                ],
                "holder": holder,
                "process_start_identity_sha256": observation[
                    "process_start_identity_sha256"
                ],
                "boot_id_sha256": observation["boot_id_sha256"],
                "fencing_token_sha256": observation["fencing_token_sha256"],
            }
            if existing is None:
                leases[lease_id] = {
                    "schema_version": RESOURCE_LEASE_SCHEMA_VERSION,
                    "lease_id": lease_id,
                    "lease_revision": 1,
                    **copy.deepcopy(immutable),
                    "heartbeat_sequence": observation["heartbeat_sequence"],
                    "heartbeat_at_unix_ms": observation[
                        "heartbeat_at_unix_ms"
                    ],
                    "status": "active",
                    "stale_reason": None,
                    "observation_evidence_sha256": observation[
                        "observation_evidence_sha256"
                    ],
                    "release_evidence_sha256": None,
                }
                changed = True
                continue
            if existing["status"] == "released":
                continue
            if any(existing[key] != value for key, value in immutable.items()):
                changed |= cls._mark_lease_stale(existing, "binding_changed")
                continue
            if observation["heartbeat_sequence"] < existing["heartbeat_sequence"]:
                changed |= cls._mark_lease_stale(existing, "observation_failed")
                continue
            if (
                observation["heartbeat_sequence"]
                == existing["heartbeat_sequence"]
                and observation["observation_evidence_sha256"]
                == existing["observation_evidence_sha256"]
                and existing["status"] == "active"
            ):
                continue
            existing.update(
                {
                    "lease_revision": existing["lease_revision"] + 1,
                    "heartbeat_sequence": observation["heartbeat_sequence"],
                    "heartbeat_at_unix_ms": observation[
                        "heartbeat_at_unix_ms"
                    ],
                    "status": "active",
                    "stale_reason": None,
                    "observation_evidence_sha256": observation[
                        "observation_evidence_sha256"
                    ],
                    "release_evidence_sha256": None,
                }
            )
            changed = True
        for lease_id, lease in leases.items():
            if (
                lease_id not in observed_ids
                and lease["holder"] == holder
                and lease["status"] != "released"
            ):
                changed |= cls._mark_lease_stale(lease, "observation_failed")
        return changed

    @classmethod
    def _resource_conflict(
        cls,
        state: dict[str, Any],
        *,
        holder: dict[str, Any],
        resource: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Find an unreleased holder across the Host, not only one Scenario."""

        for scenario_item in state["scenarios"].values():
            for lease in cls._resource_leases(scenario_item).values():
                if (
                    lease["resource_class"] == resource["resource_class"]
                    and lease["resource_identity_sha256"]
                    == resource["resource_identity_sha256"]
                    and lease["holder"] != holder
                    and lease["status"] != "released"
                ):
                    return lease
        return None

    @classmethod
    def _release_participant_resources(
        cls,
        item: dict[str, Any],
        participant: dict[str, Any],
        evidence_sha256: str,
    ) -> bool:
        if not cls._sha256(evidence_sha256):
            raise StoreError("resource.release-invalid", "resource release evidence differs")
        holder = cls._resource_holder(item, participant)
        changed = False
        for lease in cls._resource_leases(item).values():
            if lease["holder"] == holder and lease["status"] != "released":
                lease.update(
                    {
                        "lease_revision": lease["lease_revision"] + 1,
                        "status": "released",
                        "stale_reason": None,
                        "release_evidence_sha256": evidence_sha256,
                    }
                )
                changed = True
        return changed

    @classmethod
    def _stale_participant_resources(
        cls,
        item: dict[str, Any],
        participant: dict[str, Any],
        reason: str,
    ) -> bool:
        holder = cls._resource_holder(item, participant)
        changed = False
        for lease in cls._resource_leases(item).values():
            if lease["holder"] == holder and lease["status"] != "released":
                changed |= cls._mark_lease_stale(lease, reason)
        return changed

    @staticmethod
    def _mark_lease_stale(lease: dict[str, Any], reason: str) -> bool:
        if lease["status"] == "stale" and lease["stale_reason"] == reason:
            return False
        lease.update(
            {
                "lease_revision": lease["lease_revision"] + 1,
                "status": "stale",
                "stale_reason": reason,
                "release_evidence_sha256": None,
            }
        )
        return True

    @classmethod
    def _mark_active_resources_stale_after_restart(
        cls, state: dict[str, Any]
    ) -> None:
        for item in state["scenarios"].values():
            for lease in cls._resource_leases(item).values():
                if lease["status"] == "active":
                    cls._mark_lease_stale(lease, "host_restarted_unobserved")

    @staticmethod
    def _sha256(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    @staticmethod
    def _positive_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _nonnegative_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _participant_maps(
        item: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        participants = item.setdefault("participants", {})
        artifacts = item.setdefault("participant_artifacts", {})
        if not isinstance(participants, dict) or not isinstance(artifacts, dict):
            raise StoreError("host.state-invalid", "participant state schema differs")
        return participants, artifacts

    def _participant_state(
        self, state: dict[str, Any], key: str, participant_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        item = state["scenarios"].get(key)
        if item is None:
            raise StoreError("scenario.not-found", "scenario does not exist")
        participants, artifacts = self._participant_maps(item)
        record = participants.get(participant_id)
        artifact = artifacts.get(participant_id)
        if record is None or artifact is None:
            raise StoreError("participant.not-found", "participant does not exist")
        return item, item["record"], record, artifact

    @staticmethod
    def _check_scenario_fence(
        scenario: dict[str, Any], generation: int, revision: int
    ) -> None:
        if (
            scenario["scenario_generation"] != generation
            or scenario["state_revision"] != revision
        ):
            raise StoreError("scenario.stale-fence", "scenario state fence differs")

    @staticmethod
    def _check_participant_fence(
        participant: dict[str, Any], generation: int, revision: int
    ) -> None:
        if (
            participant["participant_generation"] != generation
            or participant["state_revision"] != revision
        ):
            raise StoreError(
                "participant.stale-fence", "participant state fence differs"
            )

    def _new_participant_operation(
        self,
        state: dict[str, Any],
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        operation_kind: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int | None,
        participant_state_revision: int | None,
        desired_state_after: str,
        requested_continuity_mode: str | None,
        resulting_participant_generation: int,
        replacement_launch_spec_digest: str | None = None,
    ) -> dict[str, Any]:
        operation_id = f"op-{uuid.uuid4().hex}"
        operation = {
            "operation_id": operation_id,
            "request_id": request_id,
            "operation_generation": state["state_revision"] + 1,
            "operation_kind": operation_kind,
            "target": {
                "scope": "participant",
                "scenario_id": scenario_id,
                "participant_id": participant_id,
            },
            "fence": {
                "host_generation": host_generation,
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "participant_generation": participant_generation,
                "participant_state_revision": participant_state_revision,
            },
            "state": "planned",
            "mutation_state": "not_started",
            "desired_state_after": desired_state_after,
            "replacement_launch_spec_digest": replacement_launch_spec_digest,
            "requested_continuity_mode": requested_continuity_mode,
            "plan_digest": request_digest,
            "resulting_scenario_generation": scenario_generation,
            "resulting_participant_generation": resulting_participant_generation,
            "failure_code": None,
            "created_sequence": state["journal_head_sequence"] + 1,
            "last_journal_sequence": state["journal_head_sequence"] + 1,
        }
        state["operations"][operation_id] = operation
        before_revision = participant_state_revision or 0
        self._append_operation_event(
            state,
            operation,
            event="planned",
            before_revision=before_revision,
            after_revision=before_revision,
            mutation_state="not_started",
        )
        return operation

    def _new_scenario_operation(
        self,
        state: dict[str, Any],
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        operation_kind: str,
        scenario_id: str,
        scenario_generation: int | None,
        scenario_state_revision: int | None,
        desired_state_after: str,
        resulting_scenario_generation: int,
    ) -> dict[str, Any]:
        operation_id = f"op-{uuid.uuid4().hex}"
        operation = {
            "operation_id": operation_id,
            "request_id": request_id,
            "operation_generation": state["state_revision"] + 1,
            "operation_kind": operation_kind,
            "target": {"scope": "scenario", "scenario_id": scenario_id},
            "fence": {
                "host_generation": host_generation,
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "participant_generation": None,
                "participant_state_revision": None,
            },
            "state": "planned",
            "mutation_state": "not_started",
            "desired_state_after": desired_state_after,
            "replacement_launch_spec_digest": None,
            "requested_continuity_mode": None,
            "plan_digest": request_digest,
            "resulting_scenario_generation": resulting_scenario_generation,
            "resulting_participant_generation": None,
            "failure_code": None,
            "created_sequence": state["journal_head_sequence"] + 1,
            "last_journal_sequence": state["journal_head_sequence"] + 1,
        }
        state["operations"][operation_id] = operation
        before_revision = scenario_state_revision or 0
        self._append_operation_event(
            state,
            operation,
            event="planned",
            before_revision=before_revision,
            after_revision=before_revision,
            mutation_state="not_started",
        )
        return operation

    @staticmethod
    def _previous_request(
        state: dict[str, Any], request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        previous = state["requests"].get(request_id)
        if previous is None:
            return None
        if previous["request_digest"] != request_digest:
            raise StoreError("ipc.request-reused", "request identity was reused")
        if previous["status"] == "completed":
            return previous["operation_id"], copy.deepcopy(previous["result"])
        if previous["status"] == "failed":
            error = previous["error"]
            raise OperationFailed(
                previous["operation_id"],
                error["code"],
                error["message"],
                error["mutation_state"],
                error["retryable"],
            )
        raise StoreError("scenario.operation-in-progress", "scenario operation is still pending")

    def _finalize_scenario_success(
        self,
        state: dict[str, Any],
        *,
        key: str,
        request_id: str,
        operation_id: str,
        trigger: str,
        workspace_binding_id: str | None,
    ) -> dict[str, Any]:
        record = state["scenarios"][key]["record"]
        operation = state["operations"][operation_id]
        if record["active_operation_id"] != operation_id:
            raise StoreError("scenario.stale-fence", "scenario operation fence differs")
        revision = record["state_revision"]
        self._append_operation_event(
            state,
            operation,
            event="external_succeeded",
            before_revision=revision,
            after_revision=revision,
            mutation_state="committed",
        )
        if trigger == "provision_succeeded":
            if record["observed_state"] != "provisioning" or workspace_binding_id is None:
                raise StoreError("scenario.stale-fence", "scenario provisioning fence differs")
            record["workspace_binding_id"] = workspace_binding_id
            record["observed_state"] = "closed"
        else:
            if record["observed_state"] != "opening":
                raise StoreError("scenario.stale-fence", "scenario opening fence differs")
            record["observed_state"] = "running"
        record["active_operation_id"] = None
        record["state_revision"] += 1
        self._append_operation_event(
            state,
            operation,
            event="finalize_committed",
            before_revision=revision,
            after_revision=record["state_revision"],
            mutation_state="committed",
        )
        record["journal_head_sequence"] = state["journal_head_sequence"]
        operation["state"] = "succeeded"
        operation["mutation_state"] = "committed"
        result = {"scenario": copy.deepcopy(record)}
        request = state["requests"][request_id]
        request["status"] = "completed"
        request["result"] = result
        return result

    def _finalize_create_failure(
        self,
        *,
        key: str,
        request_id: str,
        operation_id: str,
        workspace_path: Path,
    ) -> None:
        with self._lock:
            state = self._read_state()
            self._finalize_create_failure_in_state(
                state,
                key=key,
                request_id=request_id,
                operation_id=operation_id,
                workspace_path=workspace_path,
            )
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _append_operation_event(
        state: dict[str, Any],
        operation: dict[str, Any],
        *,
        event: str,
        before_revision: int,
        after_revision: int,
        mutation_state: str,
        error_code: str | None = None,
    ) -> None:
        state["journal_head_sequence"] += 1
        sequence = state["journal_head_sequence"]
        state["journal"].append(
            {
                "sequence": sequence,
                "operation_id": operation["operation_id"],
                "operation_generation": operation["operation_generation"],
                "event": event,
                "target": copy.deepcopy(operation["target"]),
                "fence": copy.deepcopy(operation["fence"]),
                "target_state_revision_before": before_revision,
                "target_state_revision_after": after_revision,
                "mutation_state": mutation_state,
                "payload_digest": operation["plan_digest"],
                "error_code": error_code,
            }
        )
        operation["last_journal_sequence"] = sequence

    def _reconcile_transitional_scenarios(self, state: dict[str, Any]) -> None:
        for key, item in state["scenarios"].items():
            record = item["record"]
            operation_id = record.get("active_operation_id")
            if operation_id is None:
                continue
            request_id = state["operations"][operation_id]["request_id"]
            if record["observed_state"] == "closing":
                operation = state["operations"][operation_id]
                request = state["requests"][request_id]
                revision = record["state_revision"]
                failure_code = "lifecycle.close-outcome-unknown"
                self._append_operation_event(
                    state,
                    operation,
                    event="repair_required",
                    before_revision=revision,
                    after_revision=revision,
                    mutation_state="unknown",
                    error_code=failure_code,
                )
                evidence = canonical_json_sha256(
                    {
                        "operation_id": operation_id,
                        "scenario_generation": record["scenario_generation"],
                        "host_restart": True,
                    }
                )
                record["observed_state"] = "degraded"
                record["active_operation_id"] = None
                record["state_revision"] += 1
                record["degraded"] = {
                    "reason": "operation_unknown",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": evidence,
                    "repair_action": "scenario.repair",
                }
                self._append_operation_event(
                    state,
                    operation,
                    event="finalize_committed",
                    before_revision=revision,
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                record["journal_head_sequence"] = state["journal_head_sequence"]
                operation["state"] = "repair_required"
                operation["mutation_state"] = "unknown"
                operation["failure_code"] = failure_code
                request["status"] = "failed"
                request["error"] = {
                    "code": "operation.internal-failure",
                    "message": "Scenario close outcome requires repair",
                    "mutation_state": "unknown",
                    "retryable": False,
                }
                continue
            if record["observed_state"] in {"repairing", "destroying"}:
                operation = state["operations"][operation_id]
                request = state["requests"][request_id]
                revision = record["state_revision"]
                failure_code = (
                    "lifecycle.repair-outcome-unknown"
                    if record["observed_state"] == "repairing"
                    else "lifecycle.destroy-outcome-unknown"
                )
                self._append_operation_event(
                    state,
                    operation,
                    event="repair_required",
                    before_revision=revision,
                    after_revision=revision,
                    mutation_state="unknown",
                    error_code=failure_code,
                )
                evidence = canonical_json_sha256(
                    {
                        "operation_id": operation_id,
                        "scenario_generation": record["scenario_generation"],
                        "host_restart": True,
                    }
                )
                record["observed_state"] = "degraded"
                record["active_operation_id"] = None
                record["state_revision"] += 1
                record["degraded"] = {
                    "reason": "operation_unknown",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": evidence,
                    "repair_action": "scenario.repair",
                }
                self._append_operation_event(
                    state,
                    operation,
                    event="finalize_committed",
                    before_revision=revision,
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                record["journal_head_sequence"] = state["journal_head_sequence"]
                operation["state"] = "repair_required"
                operation["mutation_state"] = "unknown"
                operation["failure_code"] = failure_code
                request["status"] = "failed"
                request["error"] = {
                    "code": "operation.internal-failure",
                    "message": "Scenario operation outcome requires repair",
                    "mutation_state": "unknown",
                    "retryable": False,
                }
                continue
            if record["observed_state"] == "opening":
                request = state["requests"][request_id]
                if isinstance(request.get("pending_resume_summary"), dict):
                    # The compound restore is resumed by HarnessHost after the
                    # store-level restart reconciliation commits this state.
                    continue
                self._finalize_scenario_success(
                    state,
                    key=key,
                    request_id=request_id,
                    operation_id=operation_id,
                    trigger="open_succeeded",
                    workspace_binding_id=None,
                )
                continue
            if record["observed_state"] != "provisioning":
                continue
            binding_id = state["requests"][request_id]["workspace_binding_id"]
            workspace_path = self.workspace_path(binding_id)
            if self._workspace_is_ready(workspace_path):
                self._finalize_scenario_success(
                    state,
                    key=key,
                    request_id=request_id,
                    operation_id=operation_id,
                    trigger="provision_succeeded",
                    workspace_binding_id=binding_id,
                )
            else:
                self._finalize_create_failure_in_state(
                    state,
                    key=key,
                    request_id=request_id,
                    operation_id=operation_id,
                    workspace_path=workspace_path,
                )

    def _reconcile_transitional_participants(self, state: dict[str, Any]) -> None:
        """Fail closed when a Host restart loses an external callback."""

        for item in state["scenarios"].values():
            participants, _ = self._participant_maps(item)
            scenario = item["record"]
            for record in participants.values():
                if record["observed_state"] not in {
                    "starting",
                    "stopping",
                    "recovering",
                    "replacing",
                }:
                    continue
                operation_id = record.get("active_operation_id")
                if not isinstance(operation_id, str):
                    continue
                operation = state["operations"][operation_id]
                request = state["requests"][operation["request_id"]]
                revision = record["state_revision"]
                failure_code = "lifecycle.operation-outcome-unknown"
                self._append_operation_event(
                    state,
                    operation,
                    event="repair_required",
                    before_revision=revision,
                    after_revision=revision,
                    mutation_state="unknown",
                    error_code=failure_code,
                )
                evidence = canonical_json_sha256(
                    {
                        "operation_id": operation_id,
                        "participant_generation": record["participant_generation"],
                        "host_restart": True,
                    }
                )
                record["observed_state"] = "degraded"
                record["active_operation_id"] = None
                record["state_revision"] += 1
                record["degraded"] = {
                    "reason": "operation_unknown",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": evidence,
                    "repair_action": "participant.recover",
                }
                self._stale_participant_resources(
                    item, record, "lifecycle_failed"
                )
                self._append_operation_event(
                    state,
                    operation,
                    event="finalize_committed",
                    before_revision=revision,
                    after_revision=record["state_revision"],
                    mutation_state="committed",
                )
                record["journal_head_sequence"] = state["journal_head_sequence"]
                operation["state"] = "repair_required"
                operation["mutation_state"] = "unknown"
                operation["failure_code"] = failure_code
                request["status"] = "failed"
                request["error"] = {
                    "code": "operation.internal-failure",
                    "message": "participant lifecycle outcome requires repair",
                    "mutation_state": "unknown",
                    "retryable": False,
                }
                if scenario["observed_state"] in {"opening", "running"}:
                    scenario["observed_state"] = "degraded"
                    scenario["state_revision"] += 1
                    scenario["degraded"] = {
                        "reason": "participant_fault",
                        "cleanup_pending": True,
                        "owned_resource_evidence_sha256": evidence,
                        "repair_action": "scenario.repair",
                    }

    def _reconcile_scenario_participant_faults(self, state: dict[str, Any]) -> None:
        """Restore the aggregate invariant after older empty-resume results."""

        for item in state["scenarios"].values():
            scenario = item["record"]
            participants, _ = self._participant_maps(item)
            degraded = sorted(
                (
                    participant_id,
                    participant,
                )
                for participant_id, participant in participants.items()
                if participant["observed_state"] == "degraded"
            )
            if (
                not degraded
                or scenario["desired_state"] != "running"
                or scenario["observed_state"] != "running"
                or scenario.get("active_operation_id") is not None
            ):
                continue
            evidence = canonical_json_sha256(
                {
                    "scenario_id": scenario["scenario_id"],
                    "scenario_generation": scenario["scenario_generation"],
                    "host_restart": True,
                    "degraded_participants": [
                        {
                            "participant_id": participant_id,
                            "participant_generation": participant[
                                "participant_generation"
                            ],
                            "owned_resource_evidence_sha256": participant.get(
                                "degraded", {}
                            ).get("owned_resource_evidence_sha256"),
                        }
                        for participant_id, participant in degraded
                    ],
                }
            )
            scenario["observed_state"] = "degraded"
            scenario["state_revision"] += 1
            scenario["degraded"] = {
                "reason": "participant_fault",
                "cleanup_pending": any(
                    participant.get("degraded", {}).get("cleanup_pending") is True
                    for _, participant in degraded
                ),
                "owned_resource_evidence_sha256": evidence,
                "repair_action": "scenario.repair",
            }

    @staticmethod
    def _workspace_is_ready(path: Path) -> bool:
        if path.is_symlink() or not path.is_dir():
            return False
        details = path.stat()
        return (
            details.st_uid == os.getuid()
            and stat.S_IMODE(details.st_mode) == 0o700
            and next(path.iterdir(), None) is None
        )

    def _finalize_create_failure_in_state(
        self,
        state: dict[str, Any],
        *,
        key: str,
        request_id: str,
        operation_id: str,
        workspace_path: Path,
    ) -> None:
        record = state["scenarios"][key]["record"]
        operation = state["operations"][operation_id]
        revision = record["state_revision"]
        failure_code = "workspace.provision-failed"
        self._append_operation_event(
            state,
            operation,
            event="external_failed",
            before_revision=revision,
            after_revision=revision,
            mutation_state="committed",
            error_code=failure_code,
        )
        record["observed_state"] = "provision_failed"
        record["active_operation_id"] = None
        record["state_revision"] += 1
        record["degraded"] = {
            "reason": "provision_failed",
            "cleanup_pending": workspace_path.exists(),
            "owned_resource_evidence_sha256": canonical_json_sha256(
                {"workspace_binding_id": workspace_path.name, "exists": workspace_path.exists()}
            ),
            "repair_action": "scenario.repair",
        }
        self._append_operation_event(
            state,
            operation,
            event="finalize_committed",
            before_revision=revision,
            after_revision=record["state_revision"],
            mutation_state="committed",
        )
        record["journal_head_sequence"] = state["journal_head_sequence"]
        operation["state"] = "failed"
        operation["mutation_state"] = "committed"
        operation["failure_code"] = failure_code
        request = state["requests"][request_id]
        request["status"] = "failed"
        request["error"] = {
            "code": "operation.external-failure",
            "message": "Scenario workspace provisioning failed",
            "mutation_state": "committed",
            "retryable": True,
        }

    @staticmethod
    def _scenario_key(project_instance_id: str, scenario_id: str) -> str:
        return f"{project_instance_id}\u0000{scenario_id}"
