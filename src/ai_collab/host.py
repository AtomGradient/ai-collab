# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Current-user local Host for the first runnable Harness vertical slice."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import socketserver
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .protocol import (
    CONTRACT_VERSION,
    HOST_CAPABILITIES,
    MAX_MESSAGE_BYTES,
    OPERATION_REGISTRY_DIGEST,
    ProtocolError,
    capability_proof,
    cancel_capability_proof,
    cancel_reply,
    canonical_json_bytes,
    canonical_json_sha256,
    failed_reply,
    handshake_rejected,
    operation_intent_digest,
    progress_event,
    rejected_reply,
    validate_handshake_request,
    validate_cancel_request,
    validate_operation_request,
)
from .participant import (
    ParticipantCoordinator,
    ParticipantDriverCommand,
    ParticipantError,
    SUPERVISION_TIMEOUT_SECONDS,
)
from .participant_auth import ParticipantAuthError, ParticipantAuthStore
from .delivery import DeliveryCoordinator, DeliveryError
from .project import ProjectError, ProjectRegistry
from .store import OperationFailed, ScenarioStore, StoreError
from .security import SecurityAdapterCommand, SecurityCoordinator, SecurityError
from .workspace import ProjectAdapterCommand, WorkspaceCoordinator, WorkspaceError


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 32


@dataclass
class _ActiveOperation:
    cancel_event: threading.Event
    mutation_state: str
    cancellable: bool = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        host: HarnessHost = self.server.harness_host  # type: ignore[attr-defined]
        try:
            peer_uid, peer_pid = host.peer_credentials(self.request)
        except ProtocolError as exc:
            host.write_message(self.wfile, handshake_rejected("unauthorized-peer", exc))
            return
        if peer_uid != os.getuid():
            host.write_message(
                self.wfile,
                handshake_rejected(
                    "unauthorized-peer",
                    ProtocolError(
                        "identity.owner-mismatch",
                        "identity",
                        "IPC peer is not the current owner",
                    ),
                ),
            )
            return
        try:
            handshake = host.read_message(self.rfile)
            accepted = host.accept_handshake(handshake)
            host.write_message(self.wfile, accepted)
            if accepted["outcome"] != "accepted":
                return
            request = host.read_message(self.rfile)
            if request.get("message_type") == "cancel_request":
                reply = host.handle_cancel(request)
            else:
                reply = host.handle_operation(
                    request,
                    peer_pid=peer_pid,
                    progress_callback=lambda value: host.write_message(
                        self.wfile, value
                    ),
                )
        except ProtocolError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            if (
                isinstance(locals().get("request"), dict)
                and locals()["request"].get("message_type") == "cancel_request"
            ):
                reply = cancel_reply(
                    request_id,
                    locals()["request"].get("operation_id", "invalid-operation"),
                    outcome="rejected",
                    host_generation=host.host_generation,
                    mutation_state="not_started",
                    error=exc,
                )
            else:
                reply = rejected_reply(request_id, exc)
        except StoreError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = rejected_reply(request_id, host.store_error(exc))
        except OperationFailed as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = failed_reply(
                request_id,
                exc.operation_id,
                host.host_generation,
                exc.mutation_state,
                ProtocolError(
                    exc.code,
                    "operation",
                    exc.message,
                    exc.retryable,
                    repair_action=(
                        "scenario.refresh"
                        if exc.code == "operation.cancelled"
                        else None
                    ),
                ),
            )
        except WorkspaceError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            if exc.operation_id is None:
                reply = rejected_reply(request_id, host.workspace_error(exc))
            else:
                reply = failed_reply(
                    request_id,
                    exc.operation_id,
                    host.host_generation,
                    exc.mutation_state,
                    host.workspace_error(exc),
                )
        except ParticipantError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            if exc.operation_id is None:
                reply = rejected_reply(request_id, host.participant_error(exc))
            else:
                reply = failed_reply(
                    request_id,
                    exc.operation_id,
                    host.host_generation,
                    exc.mutation_state,
                    host.participant_error(exc),
                )
        except ParticipantAuthError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = rejected_reply(
                request_id,
                ProtocolError(
                    "identity.context-invalid",
                    "identity",
                    "participant identity context is unavailable",
                ),
            )
        except DeliveryError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = rejected_reply(request_id, host.delivery_error(exc))
        except SecurityError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = rejected_reply(request_id, host.security_error(exc))
        except ProjectError as exc:
            request_id = "invalid-request"
            if isinstance(locals().get("request"), dict):
                request_id = locals()["request"].get("request_id", request_id)
            reply = rejected_reply(request_id, host.project_error(exc))
        try:
            host.write_message(self.wfile, reply)
        except OSError:
            # The durable operation result is authoritative even when its
            # observer disconnects before the final reply is delivered.
            return


class HarnessHost:
    """Synchronous Unix-socket Host with typed request/reply and durable state."""

    def __init__(
        self,
        state_root: Path,
        socket_path: Path | None = None,
        adapter_config: Path | None = None,
        participant_driver_config: Path | None = None,
        security_adapter_config: Path | None = None,
        workspace_root: Path | None = None,
    ):
        self.store = ScenarioStore(state_root, workspace_root=workspace_root)
        self.state_root = self.store.state_root
        self.socket_path = (
            Path(socket_path).expanduser().resolve()
            if socket_path is not None
            else self.state_root / "host.sock"
        )
        self.capability = self.store.ensure_capability()
        self._active_operation_lock = threading.Lock()
        self._active_operations: dict[str, _ActiveOperation] = {}
        # Store intent, Workspace claim binding, and adapter execution form one
        # Host-owned critical section.  Workspace also serializes internally,
        # but this lock covers the cross-coordinator handoff.
        self._workspace_operation_lock = threading.RLock()
        self._participant_destroy_lock = threading.RLock()
        self._workspace_join_attempted_this_host: set[str] = set()
        self.host_generation = 0
        self.host_instance_fingerprint = "0" * 64
        runtime_identity = os.stat(sys.prefix)
        self.host_runtime_identity = {"dev": runtime_identity.st_dev, "ino": runtime_identity.st_ino}
        self.participant_auth = ParticipantAuthStore(
            self.state_root, self.socket_path
        )
        project_adapter = (
            ProjectAdapterCommand(adapter_config)
            if adapter_config is not None
            else None
        )
        self.projects = ProjectRegistry(self.state_root, project_adapter)
        self.workspace = (
            WorkspaceCoordinator(
                self.state_root,
                project_adapter,
                project_root_resolver=self.projects.canonical_root,
                project_render_resolver=self._scenario_project_render,
            )
            if project_adapter is not None
            else None
        )
        self.participants = (
            ParticipantCoordinator(
                self.store,
                ParticipantDriverCommand(participant_driver_config),
                workspace_summary=(
                    self.workspace.summary if self.workspace is not None else None
                ),
            )
            if participant_driver_config is not None
            else None
        )
        self.delivery = (
            DeliveryCoordinator(self.state_root, self.store, self.participants)
            if self.participants is not None
            else None
        )
        self.security = (
            SecurityCoordinator(
                self.state_root,
                SecurityAdapterCommand(security_adapter_config),
                project_root_resolver=self.projects.canonical_root,
            )
            if security_adapter_config is not None
            else None
        )
        self._server: _UnixServer | None = None
        self._supervision_stop = threading.Event()
        self._supervision_thread: threading.Thread | None = None
        self.supervision_interval_seconds = 5.0

    def _scenario_project_render(
        self,
        project_instance_id: str,
        scenario_id: str | None,
        project_binding_digest: str | None,
    ) -> dict[str, Any] | None:
        """Prefer the Scenario's self-contained contract over mutable registry state."""

        if scenario_id is not None:
            snapshot = self.store.scenario_project_contract(
                project_instance_id, scenario_id
            )
            if snapshot is not None:
                return snapshot
        return self.projects.resolved_render(
            project_instance_id, project_binding_digest
        )

    def _scenario_collaboration_templates(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        """Offer refreshed built-ins; keep project-authored scenario rules frozen."""

        snapshot = self.store.scenario_project_contract(
            project_instance_id, scenario_id
        )
        collaboration = (
            snapshot.get("collaboration") if isinstance(snapshot, dict) else None
        )
        if isinstance(collaboration, dict) and collaboration.get("kind") == "builtin":
            current = self.projects.resolved_render(project_instance_id)
            if (
                isinstance(current, dict)
                and current.get("collaboration", {}).get("kind") == "builtin"
            ):
                # Reconciliation updates the catalog, not the applied policy.
                # The operator still explicitly previews/applies this template.
                return self.projects.collaboration_templates(project_instance_id)
        if isinstance(collaboration, dict) and (
            "registry_snapshot" in collaboration
            or "registry_snapshot_digest" in collaboration
        ):
            return self.projects.collaboration_templates_from_render(snapshot)
        # Migrated v0.1.6.1 Scenarios have no project snapshot. Prerelease
        # v0.1.7 snapshots may predate the embedded template catalog. Their
        # accepted current registry remains the only available compatibility
        # source; new Scenarios never take this branch.
        return self.projects.collaboration_templates(project_instance_id)

    def bind(self) -> None:
        if self._server is not None:
            return
        self._prepare_socket_path()
        server = _UnixServer(str(self.socket_path), _Handler)
        server.harness_host = self  # type: ignore[attr-defined]
        try:
            os.chmod(self.socket_path, 0o600)
            if self.security is not None:
                self.security.start_host()
            if self.workspace is not None:
                self.workspace.start_host(self.store.workspace_path)
                self._reconcile_workspace_operations()
            else:
                for pending in self.store.pending_workspace_operations():
                    durable_claim_bound = all(
                        isinstance(pending.get(field), str)
                        for field in {
                            "workspace_operation_id",
                            "workspace_join_claim_digest",
                            "workspace_adapter_capability_digest",
                        }
                    )
                    if (
                        pending.get("workspace_operation_kind") != "recover"
                        and durable_claim_bound
                    ):
                        try:
                            self._degrade_workspace_join(
                                pending,
                                workspace_claim=None,
                                reason="workspace.adapter-unavailable",
                                unjoinable=True,
                            )
                        except (StoreError, OSError):
                            self._mark_workspace_join_unknown(
                                pending["request_digest"]
                            )
                        continue
                    # Without a fully bound claim there is no exact capsule
                    # from which manual recovery can proceed.  Preserve the
                    # transitional intent until the coordinator returns.
                    self._mark_workspace_join_unknown(pending["request_digest"])
            if self.delivery is not None:
                self._reconcile_participant_destroy_operations()
            self.store.reconcile_recorded_outcomes()
            preserved_workspace_operations = {
                pending["operation_id"]
                for pending in self.store.pending_workspace_operations()
            }
            preserved_participant_destroy_operations = {
                pending["operation_id"]
                for pending in self.store.pending_participant_destroy_operations()
            }
            started = self.store.start_host(
                preserve_workspace_operation_ids=preserved_workspace_operations,
                preserve_participant_destroy_operation_ids=(
                    preserved_participant_destroy_operations
                ),
            )
        except BaseException:
            server.server_close()
            self._remove_owned_socket()
            raise
        self.host_generation = started["host_generation"]
        self.host_instance_fingerprint = hashlib.sha256(
            started["host_instance_id"].encode("utf-8")
        ).hexdigest()
        try:
            self._reconcile_scenario_resumes()
        except BaseException:
            server.server_close()
            self._remove_owned_socket()
            raise
        self._server = server
        self._start_resource_supervisor()

    def _complete_participant_destroy(
        self, pending: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if self.delivery is None:
            raise DeliveryError(
                "availability.driver-unavailable",
                "Delivery coordinator is not configured",
                True,
            )
        with self._participant_destroy_lock:
            _, delivery_result = self.delivery.settle_deleted_recipient(
                request_id=pending["delivery_request_id"],
                request_digest=pending["delivery_request_digest"],
                project_instance_id=pending["project_instance_id"],
                scenario_id=pending["scenario_id"],
                participant_id=pending["participant_id"],
                participant_generation=pending["participant_generation"],
            )
            self.store.record_participant_destroy_evidence(
                project_instance_id=pending["project_instance_id"],
                scenario_id=pending["scenario_id"],
                participant_id=pending["participant_id"],
                request_id=pending["request_id"],
                operation_id=pending["operation_id"],
                delivery_settlement=delivery_result["delivery_settlement"],
            )
            self.participant_auth.revoke(
                project_instance_id=pending["project_instance_id"],
                scenario_id=pending["scenario_id"],
                participant_id=pending["participant_id"],
                participant_generation=pending["participant_generation"],
            )
            result = self.store.finalize_participant_destroy(
                project_instance_id=pending["project_instance_id"],
                scenario_id=pending["scenario_id"],
                participant_id=pending["participant_id"],
                request_id=pending["request_id"],
                operation_id=pending["operation_id"],
            )
            self._refresh_participant_collaboration_contexts(
                pending["project_instance_id"], pending["scenario_id"]
            )
            return pending["operation_id"], result

    def _reconcile_participant_destroy_operations(self) -> None:
        if self.delivery is None:
            return
        for pending in self.store.pending_participant_destroy_operations():
            try:
                self._complete_participant_destroy(pending)
            except (DeliveryError, ParticipantAuthError, StoreError, OSError):
                # Store keeps the exact destroying intent.  A later exact
                # retry or Host restart rejoins the idempotent settlement.
                continue

    def _refresh_participant_collaboration_contexts(
        self, project_instance_id: str, scenario_id: str
    ) -> None:
        try:
            participants = self.store.list_participants(
                project_instance_id, scenario_id
            )["participants"]
        except (StoreError, OSError):
            return
        for participant in participants:
            if participant["observed_state"] != "ready":
                continue
            try:
                self._participant_launch_material(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant["participant_id"],
                    participant_generation=participant[
                        "participant_generation"
                    ],
                    participant_state_revision=participant["state_revision"],
                    mark_objective_issued=False,
                )
            except (
                DeliveryError,
                ParticipantAuthError,
                ParticipantError,
                StoreError,
                OSError,
            ):
                # Context is derived owner-private guidance; live Host routing
                # already rejects the deleted generation.  A later refresh or
                # participant restart regenerates it from current authority.
                continue

    def serve_forever(self) -> None:
        self.bind()
        server = self._server
        assert server is not None
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            self._stop_resource_supervisor()
            server.server_close()
            self._server = None
            self._remove_owned_socket()

    def shutdown(self) -> None:
        self._supervision_stop.set()
        if self._server is not None:
            self._server.shutdown()

    def run_resource_supervision_once(self) -> dict[str, int]:
        """Run one deterministic bounded observation pass for tests and diagnostics."""

        if self.participants is None:
            return {"observed": 0, "stale": 0}
        return self.participants.supervise_once(self.host_generation)

    def _participant_launch_material(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        participant_state_revision: int,
        mark_objective_issued: bool = True,
    ) -> dict[str, str]:
        if self.delivery is None:
            scenario, participants = self.store.delivery_snapshot(
                project_instance_id, scenario_id
            )
            unsigned: dict[str, Any] = {
                "schema_version": 1,
                "context_revision": scenario["state_revision"],
                "scenario": {
                    "project_instance_id": project_instance_id,
                    "scenario_id": scenario_id,
                    "scenario_generation": scenario["scenario_generation"],
                    "objective": {
                        "revision": len(scenario["objective_history"]),
                        "objective": scenario["objective"],
                        "acceptance_criteria": (
                            scenario["objective_history"][-1][
                                "acceptance_criteria"
                            ]
                            if scenario["objective_history"]
                            else ""
                        ),
                    },
                },
                "participant": {
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "assignments": [],
                },
                "peers": [
                    {
                        "participant_id": value["participant_id"],
                        "participant_generation": value[
                            "participant_generation"
                        ],
                        "assignments": [],
                    }
                    for value in participants
                    if value["participant_id"] != participant_id
                ],
                "policy": None,
                "allowed_outbound": [],
                "reply_semantics": {
                    "reply_expected_kinds": [
                        "collaboration.request",
                        "collaboration.question",
                        "collaboration.review-request",
                        "collaboration.pushback",
                    ],
                    "terminal_kinds": [
                        "collaboration.response",
                        "collaboration.review-response",
                        "collaboration.notice",
                        "collaboration.done",
                    ],
                    "preserve_reply_to": True,
                    "machine_ack_is_silent": True,
                },
            }
            collaboration_context = {
                **unsigned,
                "context_digest": canonical_json_sha256(unsigned),
            }
        else:
            collaboration_context = (
                self.delivery.participant_collaboration_context(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    participant_generation=participant_generation,
                )
            )
        return self.participant_auth.ensure(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
            collaboration_context=collaboration_context,
            issued_objective_revision=(
                collaboration_context["scenario"]["objective"]["revision"]
                if mark_objective_issued
                else None
            ),
        )

    def _project_participant_objective_issuance(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant: Mapping[str, Any],
    ) -> dict[str, Any]:
        projected = dict(participant)
        try:
            projected["issued_objective_revision"] = (
                self.participant_auth.issued_objective_revision(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant["participant_id"],
                    participant_generation=participant["participant_generation"],
                )
            )
        except (ParticipantAuthError, OSError):
            projected["issued_objective_revision"] = 0
        return projected

    def _reconcile_scenario_resumes(self) -> None:
        for pending in self.store.pending_scenario_resume_requests():
            project_binding_digest = pending.pop("project_binding_digest")
            restore_started = False
            try:
                snapshot = self.store.scenario_project_contract(
                    pending["project_instance_id"], pending["scenario_id"]
                )
                if snapshot is None:
                    # Only v0.1.6.1 migrations lack a self-contained render.
                    # Their accepted registry digest and frozen Workspace
                    # evidence validate the existing Scenario without making
                    # Host startup depend on canonical-root availability.
                    self.projects.validate_existing_binding(
                        pending["project_instance_id"], project_binding_digest
                    )
                elif snapshot["render_digest"] != project_binding_digest:
                    raise StoreError(
                        "scenario.restore-plan-invalid",
                        "Scenario project snapshot binding differs",
                    )
                if self.workspace is not None and not self.workspace.is_ready(
                    pending["project_instance_id"], pending["scenario_id"]
                ):
                    raise StoreError(
                        "scenario.restore-plan-invalid",
                        "Scenario workspace and environment are not ready",
                    )
                restore_started = True
                self._resume_scenario_participants(**pending)
            except (
                ProjectError,
                StoreError,
                WorkspaceError,
                ParticipantAuthError,
                ParticipantError,
                DeliveryError,
                OperationFailed,
            ) as exc:
                self.store.fail_scenario_open_resume(
                    **pending,
                    failure_code=getattr(
                        exc, "code", "scenario.restore-plan-invalid"
                    ),
                    retryable=getattr(exc, "retryable", False),
                    cleanup_pending=restore_started,
                )

    def _resume_scenario_participants(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        request_digest: str,
    ) -> dict[str, Any]:
        plan = self.store.scenario_restore_plan(project_instance_id, scenario_id)
        reports: list[dict[str, Any]] = []
        for planned in plan:
            participant_id = planned["participant_id"]
            current_scenario = self.store.scenario_status(
                project_instance_id, scenario_id
            )["scenario"]
            current_participants = {
                item["participant_id"]: item
                for item in self.store.list_participants(
                    project_instance_id, scenario_id
                )["participants"]
            }
            participant = current_participants.get(participant_id)
            base_report = {
                "participant_id": participant_id,
                "participant_generation": planned["participant_generation"],
                "continuity_mode": planned["continuity_mode"],
            }
            if participant is None or participant.get(
                "participant_generation"
            ) != planned["participant_generation"]:
                reports.append(
                    {
                        **base_report,
                        "outcome": "failed",
                        "reason_code": "participant.restore-target-drift",
                        "repair_required": True,
                    }
                )
                continue
            if participant["observed_state"] == "ready":
                if self.participants is None:
                    reports.append(
                        {
                            **base_report,
                            "outcome": "unsupported",
                            "reason_code": "availability.driver-unavailable",
                            "repair_required": False,
                        }
                    )
                    continue
                try:
                    self.participants.status(
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        participant_id=participant_id,
                        scenario_generation=current_scenario[
                            "scenario_generation"
                        ],
                        scenario_state_revision=current_scenario[
                            "state_revision"
                        ],
                        participant_generation=participant[
                            "participant_generation"
                        ],
                        participant_state_revision=participant["state_revision"],
                    )
                    reports.append(
                        {
                            **base_report,
                            "outcome": "already_ready",
                            "reason_code": None,
                            "repair_required": False,
                        }
                    )
                except (ParticipantError, StoreError, OperationFailed) as exc:
                    reports.append(
                        {
                            **base_report,
                            "outcome": "failed",
                            "reason_code": getattr(
                                exc, "code", "participant.restore-health-unverified"
                            ),
                            "repair_required": True,
                        }
                    )
                continue
            if participant["observed_state"] != "stopped":
                reports.append(
                    {
                        **base_report,
                        "outcome": "failed",
                        "reason_code": "participant.repair-required",
                        "repair_required": True,
                    }
                )
                continue
            if self.participants is None:
                reports.append(
                    {
                        **base_report,
                        "outcome": "unsupported",
                        "reason_code": "availability.driver-unavailable",
                        "repair_required": False,
                    }
                )
                continue
            child_request_id = "restore-" + hashlib.sha256(
                (
                    request_id
                    + "\0"
                    + participant_id
                    + "\0"
                    + str(planned["participant_generation"])
                ).encode("utf-8")
            ).hexdigest()[:32]
            child_request_digest = canonical_json_sha256(
                {
                    "parent_request_id": request_id,
                    "scenario_generation": current_scenario[
                        "scenario_generation"
                    ],
                    "participant_id": participant_id,
                    "participant_generation": participant[
                        "participant_generation"
                    ],
                    "continuity_mode": planned["continuity_mode"],
                }
            )
            participant_client: dict[str, str] | None = None
            try:
                participant_client = self._participant_launch_material(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    participant_generation=participant["participant_generation"],
                    participant_state_revision=participant["state_revision"],
                )
                _, started = self.participants.start(
                    request_id=child_request_id,
                    request_digest=child_request_digest,
                    host_generation=self.host_generation,
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    scenario_generation=current_scenario["scenario_generation"],
                    scenario_state_revision=current_scenario["state_revision"],
                    participant_generation=participant["participant_generation"],
                    participant_state_revision=participant["state_revision"],
                    participant_client=participant_client,
                    start_continuity_mode=planned["continuity_mode"],
                    require_bound_vendor_identity=(
                        planned["continuity_mode"] == "exact_resume"
                    ),
                )
                started_participant = started["participant"]
                self._participant_launch_material(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    participant_generation=started_participant[
                        "participant_generation"
                    ],
                    participant_state_revision=started_participant[
                        "state_revision"
                    ],
                )
                reports.append(
                    {
                        **base_report,
                        "outcome": (
                            "exact_resumed"
                            if planned["continuity_mode"] == "exact_resume"
                            else "recreated"
                        ),
                        "reason_code": None,
                        "repair_required": False,
                        "resulting_state_revision": started_participant[
                            "state_revision"
                        ],
                    }
                )
            except (
                ParticipantAuthError,
                ParticipantError,
                DeliveryError,
                StoreError,
                OperationFailed,
            ) as exc:
                if participant_client is not None:
                    self.participant_auth.revoke(
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        participant_id=participant_id,
                        participant_generation=participant[
                            "participant_generation"
                        ],
                    )
                reports.append(
                    {
                        **base_report,
                        "outcome": "failed",
                        "reason_code": getattr(
                            exc, "code", "participant.restore-failed"
                        ),
                        "repair_required": True,
                    }
                )
        return self.store.record_scenario_open_resume_summary(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            request_id=request_id,
            request_digest=request_digest,
            reports=reports,
        )

    def _start_scenario_participants(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        request_id: str,
        operation_id: str,
        active: _ActiveOperation,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        assert self.participants is not None
        progress_sequence = 0

        def emit_progress(
            state: str,
            *,
            completed_units: int,
            total_units: int,
            participant_id: str | None = None,
        ) -> None:
            nonlocal progress_sequence
            if progress_callback is not None:
                try:
                    progress_callback(
                        progress_event(
                            operation_id,
                            progress_sequence,
                            state,
                            self.host_generation,
                            {
                                "phase": "starting_participants",
                                "completed_units": completed_units,
                                "total_units": total_units,
                                "participant_id": participant_id,
                                "cancellable": active.cancellable,
                            },
                        )
                    )
                except OSError:
                    # Losing an observation client cannot cancel or corrupt
                    # the already-started durable per-unit operations.
                    pass
            progress_sequence += 1

        roster = self.store.list_participants(project_instance_id, scenario_id)[
            "participants"
        ]
        planned = [
            {
                "participant_id": item["participant_id"],
                "participant_generation": item["participant_generation"],
            }
            for item in roster
        ]
        total_units = len(planned)
        reports: list[dict[str, Any]] = []
        cancelled = False
        emit_progress("running", completed_units=0, total_units=total_units)
        for index, planned_item in enumerate(planned):
            participant_id = planned_item["participant_id"]
            if active.cancel_event.is_set():
                cancelled = True
                reports.append(
                    {
                        "participant_id": participant_id,
                        "participant_generation": planned_item[
                            "participant_generation"
                        ],
                        "observed_state": None,
                        "outcome": "cancelled",
                        "reason_code": "operation.cancelled",
                        "resulting_state_revision": None,
                    }
                )
                continue
            current_scenario = self.store.scenario_status(
                project_instance_id, scenario_id
            )["scenario"]
            current = next(
                (
                    item
                    for item in self.store.list_participants(
                        project_instance_id, scenario_id
                    )["participants"]
                    if item["participant_id"] == participant_id
                ),
                None,
            )
            if current is None:
                reports.append(
                    {
                        "participant_id": participant_id,
                        "participant_generation": planned_item[
                            "participant_generation"
                        ],
                        "observed_state": None,
                        "outcome": "skipped",
                        "reason_code": "participant.not-found",
                        "resulting_state_revision": None,
                    }
                )
                emit_progress(
                    "running",
                    completed_units=index + 1,
                    total_units=total_units,
                    participant_id=participant_id,
                )
                continue
            base_report = {
                "participant_id": participant_id,
                "participant_generation": current["participant_generation"],
                "observed_state": current["observed_state"],
            }
            if current["observed_state"] == "ready":
                # A durable "ready" is a claim, not evidence. Mirror the
                # resume loop: prove liveness or report the unit as failed.
                try:
                    self.participants.status(
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        participant_id=participant_id,
                        scenario_generation=current_scenario[
                            "scenario_generation"
                        ],
                        scenario_state_revision=current_scenario[
                            "state_revision"
                        ],
                        participant_generation=current[
                            "participant_generation"
                        ],
                        participant_state_revision=current["state_revision"],
                    )
                    reports.append(
                        {
                            **base_report,
                            "outcome": "already_running",
                            "reason_code": None,
                            "resulting_state_revision": None,
                        }
                    )
                except (ParticipantError, StoreError, OperationFailed) as exc:
                    reports.append(
                        {
                            **base_report,
                            "outcome": "failed",
                            "reason_code": getattr(
                                exc, "code", "participant.health-unverified"
                            ),
                            "resulting_state_revision": None,
                        }
                    )
                emit_progress(
                    "running",
                    completed_units=index + 1,
                    total_units=total_units,
                    participant_id=participant_id,
                )
                continue
            if current["observed_state"] not in {"stopped", "detached"}:
                # The batch never waits on transitional or degraded units;
                # the per-row lifecycle actions stay the repair surface.
                reports.append(
                    {
                        **base_report,
                        "outcome": "skipped",
                        "reason_code": "participant.not-startable",
                        "resulting_state_revision": None,
                    }
                )
                emit_progress(
                    "running",
                    completed_units=index + 1,
                    total_units=total_units,
                    participant_id=participant_id,
                )
                continue
            # The exact state revision is part of the child identity: a
            # transport-level retry of the same parent request after the
            # participant moved (started then stopped again) must run a
            # fresh child start, never replay the stale journal entry as a
            # false "started".
            child_request_id = "start-all-" + hashlib.sha256(
                (
                    request_id
                    + "\0"
                    + participant_id
                    + "\0"
                    + str(current["participant_generation"])
                    + "\0"
                    + str(current["state_revision"])
                ).encode("utf-8")
            ).hexdigest()[:32]
            child_request_digest = canonical_json_sha256(
                {
                    "parent_request_id": request_id,
                    "scenario_generation": current_scenario[
                        "scenario_generation"
                    ],
                    "participant_id": participant_id,
                    "participant_generation": current[
                        "participant_generation"
                    ],
                    "participant_state_revision": current["state_revision"],
                }
            )
            participant_client: dict[str, str] | None = None
            try:
                participant_client = self._participant_launch_material(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    participant_generation=current["participant_generation"],
                    participant_state_revision=current["state_revision"],
                )
                _, started = self.participants.start(
                    request_id=child_request_id,
                    request_digest=child_request_digest,
                    host_generation=self.host_generation,
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    scenario_generation=current_scenario["scenario_generation"],
                    scenario_state_revision=current_scenario["state_revision"],
                    participant_generation=current["participant_generation"],
                    participant_state_revision=current["state_revision"],
                    participant_client=participant_client,
                )
                started_participant = started["participant"]
                self._participant_launch_material(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=participant_id,
                    participant_generation=started_participant[
                        "participant_generation"
                    ],
                    participant_state_revision=started_participant[
                        "state_revision"
                    ],
                )
                reports.append(
                    {
                        **base_report,
                        "outcome": "started",
                        "reason_code": None,
                        "resulting_state_revision": started_participant[
                            "state_revision"
                        ],
                    }
                )
            except (
                ParticipantAuthError,
                ParticipantError,
                DeliveryError,
                StoreError,
                OperationFailed,
            ) as exc:
                failure_code = getattr(exc, "code", "participant.start-failed")
                # A pure precondition loss means this unit's external start
                # never began — a concurrent starter may have won the fence
                # and its in-flight launch owns the material files, so
                # revoking here would delete the winner's identity mid-launch.
                lost_before_external = isinstance(exc, StoreError) and (
                    failure_code
                    in {
                        "scenario.stale-fence",
                        "participant.stale-fence",
                        "participant.invalid-transition",
                        "participant.not-found",
                    }
                )
                if participant_client is not None and not lost_before_external:
                    self.participant_auth.revoke(
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        participant_id=participant_id,
                        participant_generation=current[
                            "participant_generation"
                        ],
                    )
                reports.append(
                    {
                        **base_report,
                        "outcome": "failed",
                        "reason_code": failure_code,
                        "resulting_state_revision": None,
                    }
                )
            emit_progress(
                "cancelling" if active.cancel_event.is_set() else "running",
                completed_units=index + 1,
                total_units=total_units,
                participant_id=participant_id,
            )
        with self._active_operation_lock:
            active.cancellable = False
        counts = {"total": total_units}
        for outcome in (
            "started",
            "already_running",
            "skipped",
            "failed",
            "cancelled",
        ):
            counts[outcome] = sum(
                report["outcome"] == outcome for report in reports
            )
        if cancelled or active.cancel_event.is_set():
            emit_progress(
                "cancelled",
                completed_units=sum(
                    report["outcome"] != "cancelled" for report in reports
                ),
                total_units=total_units,
            )
            raise OperationFailed(
                operation_id,
                "operation.cancelled",
                "Scenario participant batch start was cooperatively cancelled; refresh before deciding the next action",
                "committed",
                False,
            )
        scenario = self.store.scenario_status(project_instance_id, scenario_id)[
            "scenario"
        ]
        result = {
            "scenario": scenario,
            "start_summary": {
                "schema_version": 1,
                "cancelled": cancelled,
                "counts": counts,
                "reports": reports,
            },
        }
        emit_progress(
            "completed", completed_units=total_units, total_units=total_units
        )
        return result

    def _start_resource_supervisor(self) -> None:
        if self.participants is None:
            return
        if self.delivery is not None:
            self.delivery.start_supervision()
        if self._supervision_thread is not None:
            if self._supervision_thread.is_alive():
                return
            self._supervision_thread = None
        self._supervision_stop.clear()

        def supervise() -> None:
            while not self._supervision_stop.wait(
                self.supervision_interval_seconds
            ):
                try:
                    self.participants.supervise_once(
                        self.host_generation,
                        should_stop=self._supervision_stop.is_set,
                    )
                except Exception:
                    # A failed cycle never publishes health or mutates ownership.
                    pass
                if self.delivery is not None:
                    try:
                        self.delivery.run_supervision_once()
                    except Exception:
                        # Delivery failures remain in their own durable state and
                        # cannot disable independent resource supervision.
                        pass

        self._supervision_thread = threading.Thread(
            target=supervise,
            name="ai-collab-resource-supervisor",
            daemon=True,
        )
        self._supervision_thread.start()

    def _stop_resource_supervisor(self) -> None:
        self._supervision_stop.set()
        if self.delivery is not None:
            self.delivery.stop_supervision()
        thread = self._supervision_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=SUPERVISION_TIMEOUT_SECONDS + 1.0)
        if thread is None or not thread.is_alive():
            self._supervision_thread = None

    def _prepare_socket_path(self) -> None:
        if len(os.fsencode(self.socket_path)) >= 100:
            raise StoreError("host.socket-path-too-long", "Unix socket path is too long")
        parent = self.socket_path.parent
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if parent.stat().st_uid != os.getuid():
            raise StoreError("host.socket-owner", "socket directory owner differs")
        if self.socket_path.exists() or self.socket_path.is_symlink():
            details = self.socket_path.lstat()
            if not stat.S_ISSOCK(details.st_mode) or details.st_uid != os.getuid():
                raise StoreError("host.socket-invalid", "socket path is not an owned socket")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(self.socket_path))
            except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
                self.socket_path.unlink()
            else:
                raise StoreError("host.already-running", "Harness Host is already running")
            finally:
                probe.close()

    def _remove_owned_socket(self) -> None:
        try:
            details = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(details.st_mode) and details.st_uid == os.getuid():
            self.socket_path.unlink()

    @staticmethod
    def peer_credentials(connection: socket.socket) -> tuple[int, int]:
        if sys.platform == "darwin":
            if hasattr(connection, "getpeereid"):
                uid, _ = connection.getpeereid()  # type: ignore[attr-defined]
            elif hasattr(socket, "LOCAL_PEERCRED"):
                owner_credentials = connection.getsockopt(
                    0, socket.LOCAL_PEERCRED, 256
                )
                if len(owner_credentials) < 8:
                    raise ProtocolError(
                        "identity.peer-rejected",
                        "identity",
                        "platform returned incomplete local peer credentials",
                    )
                version, uid = struct.unpack_from("=II", owner_credentials)
                if version != 0:
                    raise ProtocolError(
                        "identity.peer-rejected",
                        "identity",
                        "platform returned unsupported local peer credentials",
                    )
            else:
                raise ProtocolError(
                    "identity.peer-rejected",
                    "identity",
                    "platform cannot authenticate the local IPC owner",
                )
            # LOCAL_PEERPID is 0x002 in Darwin sys/un.h.  Python does not
            # currently expose the constant, so keep the SDK value local.
            credentials = connection.getsockopt(0, 0x002, 4)
            if len(credentials) != 4:
                raise ProtocolError(
                    "identity.peer-rejected",
                    "identity",
                    "platform returned an incomplete local peer process",
                )
            (pid,) = struct.unpack("=i", credentials)
            if pid < 2:
                raise ProtocolError(
                    "identity.peer-rejected",
                    "identity",
                    "platform returned an invalid local peer process",
                )
            return int(uid), int(pid)
        if hasattr(socket, "SO_PEERCRED"):
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            pid, uid, _ = struct.unpack("3i", credentials)
            if pid < 2:
                raise ProtocolError(
                    "identity.peer-rejected",
                    "identity",
                    "platform returned an invalid local peer process",
                )
            return int(uid), int(pid)
        raise ProtocolError(
            "identity.peer-rejected",
            "identity",
            "platform cannot authenticate the local IPC peer",
        )

    @staticmethod
    def read_message(stream: Any) -> dict[str, Any]:
        raw = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            raise ProtocolError("ipc.invalid-message", "protocol", "IPC frame is invalid")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("ipc.invalid-message", "protocol", "IPC JSON is invalid") from exc
        if not isinstance(value, dict):
            raise ProtocolError("ipc.invalid-message", "protocol", "IPC value is not an object")
        return value

    @staticmethod
    def write_message(stream: Any, value: dict[str, Any]) -> None:
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()

    def accept_handshake(self, value: Any) -> dict[str, Any]:
        try:
            request = validate_handshake_request(value)
        except ProtocolError as exc:
            request_id = value.get("request_id", "invalid-request") if isinstance(value, dict) else "invalid-request"
            return handshake_rejected(request_id, exc)
        return {
            "message_type": "handshake_reply",
            "request_id": request["request_id"],
            "outcome": "accepted",
            "contract_version": CONTRACT_VERSION,
            "host_instance_fingerprint": self.host_instance_fingerprint,
            "host_generation": self.host_generation,
            "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
            "host_capabilities": HOST_CAPABILITIES,
        }

    def handle_cancel(self, value: Any) -> dict[str, Any]:
        request = validate_cancel_request(value)
        if request["host_generation"] != self.host_generation:
            raise ProtocolError(
                "fence.stale-host-generation",
                "fencing",
                "Host generation fence differs",
                retryable=True,
            )
        expected = cancel_capability_proof(
            self.capability,
            operation_id=request["operation_id"],
            host_generation=self.host_generation,
        )
        if not secrets.compare_digest(request["capability_proof"], expected):
            raise ProtocolError(
                "auth.capability-denied",
                "authorization",
                "cancel capability proof was rejected",
            )
        with self._active_operation_lock:
            active = self._active_operations.get(request["operation_id"])
            if active is None or not active.cancellable:
                raise ProtocolError(
                    "operation.precondition-failed",
                    "operation",
                    "operation is no longer cancellable",
                )
            active.cancel_event.set()
            mutation_state = active.mutation_state
        return cancel_reply(
            request["request_id"],
            request["operation_id"],
            outcome="accepted",
            host_generation=self.host_generation,
            mutation_state=mutation_state,
        )

    def handle_operation(
        self,
        value: Any,
        *,
        peer_pid: int | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        request, descriptor = validate_operation_request(value)
        participant_self_operation = request["operation"] in {
            "message.send-self",
            "message.reply-self",
        }
        capability = self.capability
        if participant_self_operation:
            target = request["target"]
            try:
                context = self.participant_auth.read(
                    {
                        "project_instance_id": target["project_instance_id"],
                        "scenario_id": target["scenario_id"],
                        "participant_id": target["participant_id"],
                        "participant_generation": request["fence"][
                            "participant_generation"
                        ],
                    }
                )
            except ParticipantAuthError as exc:
                raise ProtocolError(
                    "auth.capability-denied",
                    "authorization",
                    "participant capability was rejected",
                ) from exc
            capability = context["participant_capability"]
        expected_proof = capability_proof(
            capability,
            operation=request["operation"],
            required_capability=descriptor["required_capability"],
            target=request["target"],
            host_generation=request["fence"]["host_generation"],
        )
        if not secrets.compare_digest(request["capability_proof"], expected_proof):
            raise ProtocolError(
                "auth.capability-denied",
                "authorization",
                "capability proof was rejected",
            )
        if request["fence"]["host_generation"] != self.host_generation:
            raise ProtocolError(
                "fence.stale-host-generation",
                "fencing",
                "Host generation fence differs",
                retryable=True,
            )
        request_digest = operation_intent_digest(request)
        operation = request["operation"]
        target = request["target"]
        security_consumption = None
        security_effect_preview: dict[str, Any] | None = None
        if descriptor["confirmation_policy_ref"] is not None:
            self._validate_high_risk_preflight(request)
            if (
                operation
                in {
                    "scenario.repair",
                    "scenario.destroy",
                    "scenario.force-destroy",
                }
                and self.workspace is not None
            ):
                # An exact retry first joins any Workspace result that was
                # durably published before Store finalization became unknown.
                # This happens before request replay and never re-prompts.
                self._reconcile_workspace_operations()
            try:
                previous = self.store.replay_request(
                    request["request_id"], request_digest
                )
            except OperationFailed as failure:
                if failure.mutation_state == "unknown":
                    self._mark_workspace_join_unknown(request_digest)
                elif self.security is not None:
                    self.security.reconcile_failed_outcome(
                        request_digest, allow_missing=True
                    )
                raise
            if previous is not None:
                if self.security is not None:
                    self.security.reconcile_completed_outcome(
                        request_digest,
                        operation_id=previous[0],
                        result=previous[1],
                    )
                return self._completed_reply(
                    request=request,
                    descriptor=descriptor,
                    operation_id=previous[0],
                    result=previous[1],
                )
            if self.security is None:
                raise ProtocolError(
                    "auth.confirmation-required",
                    "authorization",
                    "high-risk operation requires a trusted local confirmation adapter",
                    retryable=True,
                )
            effect_preview, private_subject = self._security_context(request)
            security_effect_preview = effect_preview
            security_consumption = self.security.authorize(
                request,
                descriptor,
                effect_preview=effect_preview,
                private_subject=private_subject,
            )
        if participant_self_operation:
            if peer_pid is None or self.participants is None or self.delivery is None:
                raise ProtocolError(
                    "identity.sender-rejected",
                    "identity",
                    "participant sender proof is unavailable",
                )
            scenario, participant_records = self.store.delivery_snapshot(
                target["project_instance_id"], target["scenario_id"]
            )
            sender = next(
                (
                    record
                    for record in participant_records
                    if record["participant_id"] == target["participant_id"]
                    and record["participant_generation"]
                    == request["fence"]["participant_generation"]
                ),
                None,
            )
            if sender is None:
                raise ProtocolError(
                    "identity.sender-rejected",
                    "identity",
                    "participant sender binding differs",
                )
            if request["fence"]["operation_generation"] != sender[
                "state_revision"
            ]:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "participant sender revision differs",
                    retryable=True,
                )
            try:
                self.participants.authorize_sender(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant_id=target["participant_id"],
                    participant_generation=sender["participant_generation"],
                    runtime_binding_id=sender["runtime_binding_id"],
                    presentation_binding_id=sender["presentation_binding_id"],
                    peer_pid=peer_pid,
                )
            except (ParticipantError, StoreError) as exc:
                raise ProtocolError(
                    "identity.sender-rejected",
                    "identity",
                    "participant sender process was rejected",
                ) from exc
            payload = request["payload"]
            if operation == "message.reply-self":
                with self._participant_destroy_lock:
                    operation_id, result = self.delivery.reply_message(
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        scenario=scenario,
                        sender=sender,
                        receiver_participant_id=payload[
                            "receiver_participant_id"
                        ],
                        reply_to_delivery_id=payload["reply_to_delivery_id"],
                        message_id=payload["message_id"],
                        message_kind=payload["message_kind"],
                        message=payload["message"],
                    )
            else:
                with self._participant_destroy_lock:
                    operation_id, result = self.delivery.send_self_message(
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        scenario=scenario,
                        sender=sender,
                        receiver_participant_id=payload[
                            "receiver_participant_id"
                        ],
                        message_id=payload["message_id"],
                        message_kind=payload["message_kind"],
                        message=payload["message"],
                    )
        elif operation == "host.status":
            result = self.store.host_status()
            result["host_runtime_identity"] = self.host_runtime_identity
            operation_id = f"read-{request['request_id']}"
        elif operation in (
            "presentation.permission-probe",
            "presentation.permission-request",
        ):
            # permission-probe is a pure observation; permission-request is an
            # explicit user gesture that lets the platform show its consent
            # prompt. Neither mutates durable Harness state.
            if self.participants is None:
                raise ProtocolError(
                    "presentation.driver-unavailable",
                    "availability",
                    "presentation permission driver is not configured",
                    False,
                    "participant.driver-configure",
                )
            try:
                if operation == "presentation.permission-request":
                    result = self.participants.permission_request()
                else:
                    result = self.participants.permission_probe()
            except ParticipantError as exc:
                raise ProtocolError(
                    "presentation.observation-failed",
                    "operation",
                    "presentation permission observation failed",
                    True,
                ) from exc
            operation_id = f"read-{request['request_id']}"
        elif operation == "environment.probe":
            # A pure machine-environment observation: which registry-declared
            # runtime executables, presentation target, and shell exist right
            # now. Nothing durable moves and no vendor name reaches this file.
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "participant driver is not configured",
                    False,
                    "participant.driver-configure",
                )
            try:
                result = self.participants.environment_probe()
            except ParticipantError as exc:
                raise ProtocolError(
                    "environment.observation-failed",
                    "operation",
                    "machine environment observation failed",
                    True,
                ) from exc
            operation_id = f"read-{request['request_id']}"
        elif operation == "project.register":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "project registration requires an absent-request fence",
                    retryable=True,
                )
            operation_id, result = self.projects.register(
                request_id=request["request_id"],
                request_digest=request_digest,
                canonical_project_path=request["payload"]["canonical_project_path"],
            )
        elif operation == "project.list":
            result = self.projects.list()
            operation_id = f"read-{request['request_id']}"
        elif operation == "project.reconcile":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "project reconciliation requires an absent-request fence",
                    retryable=True,
                )
            operation_id, result = self.projects.reconcile(
                request_id=request["request_id"],
                request_digest=request_digest,
                project_instance_id=request["target"]["project_instance_id"],
            )
        elif operation == "project.accept-reconciliation":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "project reconciliation acceptance requires an absent-request fence",
                    retryable=True,
                )
            operation_id, result = self.projects.accept_reconciliation(
                request_id=request["request_id"],
                request_digest=request_digest,
                project_instance_id=request["target"]["project_instance_id"],
                availability_fingerprint=request["payload"]["availability_fingerprint"],
            )
        elif operation == "project.bootstrap":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "project bootstrap requires an absent-request fence",
                    retryable=True,
                )
            operation_id, result = self.projects.bootstrap(
                request_id=request["request_id"],
                request_digest=request_digest,
                canonical_project_path=request["payload"]["canonical_project_path"],
            )
        elif operation == "project.unregister":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "project unregistration requires an absent-request fence",
                    retryable=True,
                )
            project_instance_id = request["payload"]["project_instance_id"]
            # Unregistering never disposes of anything: a project that still
            # owns durable Scenarios keeps its registration until the owner
            # has explicitly destroyed every one of them.
            if self.store.list_scenarios(project_instance_id)["scenarios"]:
                raise ProtocolError(
                    "project.scenarios-exist",
                    "operation",
                    "project still owns durable Scenarios; destroy them first",
                )
            operation_id, result = self.projects.unregister(
                request_id=request["request_id"],
                request_digest=request_digest,
                project_instance_id=project_instance_id,
            )
        elif operation == "scenario.list":
            result = self.store.list_scenarios(target["project_instance_id"])
            operation_id = f"read-{request['request_id']}"
        elif operation == "scenario.status":
            result = self.store.scenario_status(
                target["project_instance_id"], target["scenario_id"]
            )
            operation_id = f"read-{request['request_id']}"
        elif operation == "scenario.objective.append":
            payload = request["payload"]
            if (
                request["fence"]["operation_generation"]
                != payload["scenario_state_revision"]
            ):
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "Scenario objective operation generation differs from revision",
                    retryable=True,
                )
            operation_id, result = self.store.append_scenario_objective(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                scenario_generation=payload["scenario_generation"],
                scenario_state_revision=payload["scenario_state_revision"],
                objective=payload["objective"],
                acceptance_criteria=payload["acceptance_criteria"],
            )
            self._refresh_participant_collaboration_contexts(
                target["project_instance_id"], target["scenario_id"]
            )
        elif operation == "scenario.diagnostic":
            result = self.store.scenario_diagnostic(
                target["project_instance_id"], target["scenario_id"]
            )
            diagnostic = result["diagnostic"]
            diagnostic["schema_version"] = 3
            diagnostic["workspace"] = (
                self.workspace.summary(
                    target["project_instance_id"], target["scenario_id"]
                )
                if self.workspace is not None
                else None
            )
            diagnostic.pop("diagnostic_digest", None)
            diagnostic["diagnostic_digest"] = canonical_json_sha256(diagnostic)
            operation_id = f"read-{request['request_id']}"
        elif operation == "scenario.preflight":
            result = self._scenario_preflight(
                target["project_instance_id"], target["scenario_id"]
            )
            operation_id = f"read-{request['request_id']}"
        elif operation in {"scenario.topology", "scenario.focus"}:
            if self.participants is None and operation == "scenario.focus":
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Presentation driver is not configured",
                    retryable=True,
                    repair_action="participant.driver-configure",
                )
            current = self.store.scenario_status(
                target["project_instance_id"], target["scenario_id"]
            )["scenario"]
            if operation == "scenario.focus":
                payload = request["payload"]
                if (
                    request["fence"]["operation_generation"]
                    != payload["scenario_state_revision"]
                    or payload["scenario_generation"]
                    != current["scenario_generation"]
                    or payload["scenario_state_revision"]
                    != current["state_revision"]
                ):
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "Scenario focus fence differs",
                        retryable=True,
                        repair_action="scenario.refresh",
                    )
            if self.participants is None:
                result = self._unavailable_scenario_topology(
                    self.store.list_participants(
                        target["project_instance_id"], target["scenario_id"]
                    )["participants"]
                )
            else:
                result = self.participants.scenario_presentation(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    scenario_generation=current["scenario_generation"],
                    scenario_state_revision=current["state_revision"],
                    action="focus" if operation == "scenario.focus" else "inspect",
                )
            operation_id = (
                f"focus-{request['request_id']}"
                if operation == "scenario.focus"
                else f"read-{request['request_id']}"
            )
        elif operation == "scenario.destroy.preview":
            result = {
                "effect_preview": self._scenario_high_risk_context(
                    request, operation="scenario.destroy", require_eligible=False
                )[0]
            }
            operation_id = f"read-{request['request_id']}"
        elif operation == "resource.list":
            result = self.store.list_resources(
                target["project_instance_id"], target["scenario_id"]
            )
            operation_id = f"read-{request['request_id']}"
        elif operation == "resource.break":
            payload = request["payload"]
            if request["fence"]["operation_generation"] != payload[
                "scenario_state_revision"
            ]:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "resource break generation differs from Scenario revision",
                    retryable=True,
                )
            try:
                operation_id, result = self.store.break_resource(
                    request_id=request["request_id"],
                    request_digest=request_digest,
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    scenario_generation=payload["scenario_generation"],
                    scenario_state_revision=payload["scenario_state_revision"],
                    lease_id=payload["lease_id"],
                    lease_revision=payload["lease_revision"],
                    consumption_evidence_sha256=canonical_json_sha256(
                        security_consumption
                    ),
                )
            except BaseException:
                self._mark_security_failure(security_consumption)
                raise
        elif operation == "scenario.create":
            if request["fence"]["operation_generation"] != 0:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "scenario creation requires an absent-record fence",
                    retryable=True,
                )
            replayed = self.store.replay_request(
                request["request_id"], request_digest
            )
            if replayed is not None:
                operation_id, result = replayed
            else:
                self.projects.validate_binding(
                    target["project_instance_id"],
                    request["payload"]["project_binding_digest"],
                )
                project_contract_snapshot = self.projects.resolved_render(
                    target["project_instance_id"],
                    request["payload"]["project_binding_digest"],
                )
                if project_contract_snapshot is None:
                    raise ProjectError(
                        "project.reconciliation-required",
                        "project upgrade must finish before creating a new Scenario",
                    )
                # New Scenarios must be self-contained. Compatibility lookup
                # of a mutable project registry is reserved for already
                # migrated Scenarios whose historical state lacks a snapshot.
                self.projects.collaboration_templates_from_render(
                    project_contract_snapshot
                )
                operation_id, result = self.store.create_scenario(
                    request_id=request["request_id"],
                    request_digest=request_digest,
                    host_generation=self.host_generation,
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    project_binding_digest=request["payload"]["project_binding_digest"],
                    objective=request["payload"]["objective"],
                    acceptance_criteria=request["payload"]["acceptance_criteria"],
                    project_contract_snapshot=project_contract_snapshot,
                )
        elif operation == "scenario.open":
            if request["fence"]["operation_generation"] != request["payload"]["scenario_state_revision"]:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "operation generation differs from scenario revision",
                    retryable=True,
                )
            if self.workspace is not None and not self.workspace.is_ready(
                target["project_instance_id"], target["scenario_id"]
            ):
                raise ProtocolError(
                    "operation.precondition-failed",
                    "operation",
                    "Scenario workspace and environment are not ready",
                )
            operation_id, result = self.store.open_scenario(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                scenario_generation=request["payload"]["scenario_generation"],
                scenario_state_revision=request["payload"]["scenario_state_revision"],
            )
            if "resume_summary" not in result:
                try:
                    result = self._resume_scenario_participants(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request["request_id"],
                        request_digest=request_digest,
                    )
                except (
                    ProjectError,
                    StoreError,
                    WorkspaceError,
                    ParticipantAuthError,
                    ParticipantError,
                    DeliveryError,
                    OperationFailed,
                ) as exc:
                    self.store.fail_scenario_open_resume(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        failure_code=getattr(
                            exc, "code", "scenario.restore-plan-invalid"
                        ),
                        retryable=getattr(exc, "retryable", False),
                        cleanup_pending=True,
                    )
                    # Raise the failure exactly as persisted so the client and
                    # subsequent idempotent replays observe one outcome.
                    self.store.replay_request(
                        request["request_id"], request_digest
                    )
                    raise
        elif operation == "scenario.force-destroy":
            if self.workspace is None or security_effect_preview is None:
                self._mark_security_failure(security_consumption)
                raise ProtocolError(
                    "availability.adapter-unavailable",
                    "availability",
                    "Workspace/Environment adapter is not configured",
                    retryable=True,
                )
            try:
                operation_id, result = self._force_destroy_scenario(
                    request=request,
                    request_digest=request_digest,
                    effect_preview=security_effect_preview,
                )
            except BaseException as exc:
                if not (
                    isinstance(exc, OperationFailed)
                    and exc.mutation_state == "unknown"
                ):
                    self._mark_security_failure(security_consumption)
                if isinstance(exc, (StoreError, ProtocolError, OperationFailed)):
                    raise
                raise OperationFailed(
                    f"op-{request['request_id']}",
                    "operation.external-failure",
                    "Scenario force destroy failed",
                    "committed",
                    False,
                ) from exc
        elif operation in {"scenario.repair", "scenario.destroy"}:
            if self.workspace is None or security_effect_preview is None:
                self._mark_security_failure(security_consumption)
                raise ProtocolError(
                    "availability.adapter-unavailable",
                    "availability",
                    "Workspace/Environment adapter is not configured",
                    retryable=True,
                )
            payload = request["payload"]
            if (
                request["fence"]["operation_generation"]
                != payload["scenario_state_revision"]
            ):
                self._mark_security_failure(security_consumption)
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "scenario operation generation differs from revision",
                    retryable=True,
                )
            common = {
                "request_id": request["request_id"],
                "request_digest": request_digest,
                "host_generation": self.host_generation,
                "project_instance_id": target["project_instance_id"],
                "scenario_id": target["scenario_id"],
                "scenario_generation": payload["scenario_generation"],
                "scenario_state_revision": payload["scenario_state_revision"],
            }
            workspace_request_id = self.store.workspace_join_request_id(
                operation,
                request["request_id"],
                request_digest,
            )
            expected_binding_state = security_effect_preview["workspace"].get(
                "binding_state"
            )
            expected_wip_summary_digest = security_effect_preview["workspace"][
                "wip_summary_digest"
            ]
            recovery_preview = security_effect_preview["workspace"].get(
                "recovery"
            )
            workspace_operation_kind = (
                "recover"
                if operation == "scenario.repair"
                and isinstance(recovery_preview, dict)
                else "repair"
            )
            operation_id: str | None = None
            preserve_workspace_recovery = False
            try:
                if operation == "scenario.repair":
                    operation_id, replay, workspace_path = (
                        self.store.begin_scenario_repair(
                            expected_wip_summary_digest=(
                                expected_wip_summary_digest
                            ),
                            workspace_operation_kind=workspace_operation_kind,
                            expected_recovery_claim_digest=(
                                recovery_preview.get(
                                    "prior_operation_claim_digest"
                                )
                                if isinstance(recovery_preview, dict)
                                else None
                            ),
                            expected_recovery_inventory_digest=(
                                recovery_preview.get("inventory_digest")
                                if isinstance(recovery_preview, dict)
                                else None
                            ),
                            expected_recovery_prior_operation_kind=(
                                recovery_preview.get(
                                    "prior_operation_kind"
                                )
                                if isinstance(recovery_preview, dict)
                                else None
                            ),
                            **common,
                        )
                    )
                    if replay is not None:
                        result = replay
                    else:
                        assert workspace_path is not None
                        bind_claim = lambda claim: (
                            self.store.bind_workspace_execution_claim(
                                project_instance_id=target[
                                    "project_instance_id"
                                ],
                                scenario_id=target["scenario_id"],
                                request_id=request["request_id"],
                                request_digest=request_digest,
                                operation_id=operation_id,
                                workspace_request_id=workspace_request_id,
                                operation_kind="scenario.repair",
                                scenario_generation=payload[
                                    "scenario_generation"
                                ],
                                workspace_claim=dict(claim),
                            )
                        )
                        with self._workspace_operation_lock:
                            if workspace_operation_kind == "recover":
                                assert isinstance(recovery_preview, dict)
                                _, workspace_result = self.workspace.recover(
                                    request_id=workspace_request_id,
                                    request_digest=request_digest,
                                    project_instance_id=target[
                                        "project_instance_id"
                                    ],
                                    scenario_id=target["scenario_id"],
                                    scenario_generation=payload[
                                        "scenario_generation"
                                    ],
                                    workspace_path=workspace_path,
                                    expected_wip_summary_digest=(
                                        expected_wip_summary_digest
                                    ),
                                    expected_prior_claim_digest=(
                                        recovery_preview[
                                            "prior_operation_claim_digest"
                                        ]
                                    ),
                                    expected_inventory_digest=(
                                        recovery_preview["inventory_digest"]
                                    ),
                                    before_external=bind_claim,
                                )
                            else:
                                _, workspace_result = self.workspace.repair(
                                    request_id=workspace_request_id,
                                    request_digest=request_digest,
                                    project_instance_id=target[
                                        "project_instance_id"
                                    ],
                                    scenario_id=target["scenario_id"],
                                    scenario_generation=payload[
                                        "scenario_generation"
                                    ],
                                    workspace_path=workspace_path,
                                    expected_wip_summary_digest=(
                                        expected_wip_summary_digest
                                    ),
                                    before_external=bind_claim,
                                )
                        # Workspace has a durable external outcome from here.
                        # A Store publication error is therefore unknown and
                        # must be joined, never converted to a repair failure.
                        preserve_workspace_recovery = True
                        result = self.store.finalize_scenario_repair(
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            request_id=request["request_id"],
                            operation_id=operation_id,
                            workspace_evidence_sha256=canonical_json_sha256(
                                workspace_result
                            ),
                        )
                else:
                    operation_id, replay, workspace_path = (
                        self.store.begin_scenario_destroy(
                            expected_workspace_binding_state=(
                                expected_binding_state
                            ),
                            expected_wip_summary_digest=(
                                expected_wip_summary_digest
                            ),
                            **common,
                        )
                    )
                    if replay is not None:
                        result = replay
                    else:
                        assert workspace_path is not None
                        try:
                            with self._workspace_operation_lock:
                                _, workspace_result = self.workspace.destroy(
                                    request_id=workspace_request_id,
                                    request_digest=request_digest,
                                    project_instance_id=target[
                                        "project_instance_id"
                                    ],
                                    scenario_id=target["scenario_id"],
                                    scenario_generation=payload[
                                        "scenario_generation"
                                    ],
                                    workspace_path=workspace_path,
                                    expected_wip_summary_digest=(
                                        expected_wip_summary_digest
                                    ),
                                    expected_binding_state=(
                                        expected_binding_state
                                    ),
                                    before_external=lambda claim: (
                                        self.store.bind_workspace_execution_claim(
                                            project_instance_id=target[
                                                "project_instance_id"
                                            ],
                                            scenario_id=target["scenario_id"],
                                            request_id=request["request_id"],
                                            request_digest=request_digest,
                                            operation_id=operation_id,
                                            workspace_request_id=(
                                                workspace_request_id
                                            ),
                                            operation_kind="scenario.destroy",
                                            scenario_generation=payload[
                                                "scenario_generation"
                                            ],
                                            workspace_claim=dict(claim),
                                        )
                                    ),
                                )
                        except BaseException as destroy_error:
                            preserve_workspace_recovery = True
                            completed = self.workspace.completed_request(
                                workspace_request_id, request_digest
                            )
                            if completed is not None:
                                _, workspace_result = completed
                            elif (
                                expected_binding_state
                                in {"absent", "planned", "provision_failed"}
                                or (
                                    isinstance(destroy_error, WorkspaceError)
                                    and destroy_error.mutation_state
                                    == "not_started"
                                )
                            ):
                                self.store.abort_scenario_destroy_no_effect(
                                    project_instance_id=target[
                                        "project_instance_id"
                                    ],
                                    scenario_id=target["scenario_id"],
                                    request_id=request["request_id"],
                                    operation_id=operation_id,
                                    reason=(
                                        destroy_error.code
                                        if isinstance(
                                            destroy_error, WorkspaceError
                                        )
                                        else "workspace.destroy-failed"
                                    ),
                                )
                                raise OperationFailed(
                                    operation_id,
                                    "operation.precondition-failed",
                                    (
                                        "Scenario destroy evidence changed; "
                                        "confirm a new request"
                                    ),
                                    "committed",
                                    True,
                                ) from destroy_error
                            else:
                                raise
                        preserve_workspace_recovery = True
                        result = self.store.finalize_scenario_destroy(
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            request_id=request["request_id"],
                            operation_id=operation_id,
                            workspace_evidence_sha256=canonical_json_sha256(
                                workspace_result
                            ),
                        )
            except BaseException as exc:
                if operation_id is None and isinstance(exc, OSError):
                    # Resolve the atomic-write ambiguity against the durable
                    # Store ledger.  A published pending intent must be joined;
                    # exact absence proves the adapter can never have run and
                    # requires a newly confirmed request.
                    try:
                        durable_status, durable_operation_id = (
                            self.store.inspect_request_status(
                                request["request_id"], request_digest
                            )
                        )
                    except (StoreError, OSError):
                        durable_status, durable_operation_id = "unknown", None
                    if durable_status == "completed":
                        replayed = self.store.replay_request(
                            request["request_id"], request_digest
                        )
                        assert replayed is not None
                        if self.security is not None:
                            self.security.reconcile_completed_outcome(
                                request_digest,
                                operation_id=replayed[0],
                                result=replayed[1],
                                allow_missing=True,
                            )
                        return self._completed_reply(
                            request=request,
                            descriptor=descriptor,
                            operation_id=replayed[0],
                            result=replayed[1],
                        )
                    if durable_status == "failed":
                        try:
                            self.store.replay_request(
                                request["request_id"], request_digest
                            )
                        except OperationFailed as persisted_failure:
                            if persisted_failure.mutation_state == "unknown":
                                self._mark_workspace_join_unknown(request_digest)
                            elif self.security is not None:
                                self.security.reconcile_failed_outcome(
                                    request_digest, allow_missing=True
                                )
                            raise
                        raise StoreError(
                            "host.state-invalid",
                            "failed request replay did not fail",
                        )
                    if durable_status == "pending":
                        stable_status, stable_operation_id = (
                            self._stabilize_store_begin_pending(
                                request["request_id"], request_digest
                            )
                        )
                        if stable_status == "completed":
                            replayed = self.store.replay_request(
                                request["request_id"], request_digest
                            )
                            assert replayed is not None
                            if self.security is not None:
                                self.security.reconcile_completed_outcome(
                                    request_digest,
                                    operation_id=replayed[0],
                                    result=replayed[1],
                                    allow_missing=True,
                                )
                            return self._completed_reply(
                                request=request,
                                descriptor=descriptor,
                                operation_id=replayed[0],
                                result=replayed[1],
                            )
                        if stable_status == "failed":
                            self.store.replay_request(
                                request["request_id"], request_digest
                            )
                        raise OperationFailed(
                            stable_operation_id
                            or durable_operation_id
                            or f"op-{request['request_id']}",
                            "operation.internal-failure",
                            "Scenario operation intent is pending reconciliation",
                            "unknown",
                            True,
                        ) from exc
                    if durable_status == "unknown":
                        self._mark_workspace_join_unknown(request_digest)
                        raise OperationFailed(
                            f"op-{request['request_id']}",
                            "operation.internal-failure",
                            "Scenario operation intent outcome is unknown",
                            "unknown",
                            True,
                        ) from exc
                    self._mark_security_failure(security_consumption)
                    raise OperationFailed(
                        f"op-{request['request_id']}",
                        "operation.precondition-failed",
                        "Scenario operation intent was not committed; confirm a new request",
                        "not_started",
                        True,
                    ) from exc
                if (
                    operation == "scenario.repair"
                    and workspace_operation_kind == "recover"
                    and operation_id is not None
                    and isinstance(exc, WorkspaceError)
                    and exc.mutation_state == "not_started"
                ):
                    try:
                        persisted_abort = (
                            self.store.abort_scenario_recovery_no_effect(
                                project_instance_id=target[
                                    "project_instance_id"
                                ],
                                scenario_id=target["scenario_id"],
                                request_id=request["request_id"],
                                operation_id=operation_id,
                                reason=exc.code,
                            )
                        )
                    except (StoreError, OSError) as abort_error:
                        try:
                            replayed = self.store.replay_request(
                                request["request_id"], request_digest
                            )
                        except OperationFailed as persisted_failure:
                            if not self._is_recovery_no_effect_failure(
                                persisted_failure,
                                operation_id=operation_id,
                            ):
                                self._mark_workspace_join_unknown(
                                    request_digest
                                )
                                raise OperationFailed(
                                    operation_id,
                                    "operation.internal-failure",
                                    (
                                        "Scenario recovery abort outcome "
                                        "is unknown"
                                    ),
                                    "unknown",
                                    True,
                                ) from persisted_failure
                            self._mark_security_failure(security_consumption)
                            raise persisted_failure from exc
                        except StoreError:
                            self._mark_workspace_join_unknown(request_digest)
                            raise OperationFailed(
                                operation_id,
                                "operation.internal-failure",
                                (
                                    "Scenario recovery abort is pending "
                                    "reconciliation"
                                ),
                                "unknown",
                                True,
                            ) from abort_error
                        if replayed is not None:
                            if self.security is not None:
                                self.security.reconcile_completed_outcome(
                                    request_digest,
                                    operation_id=replayed[0],
                                    result=replayed[1],
                                    allow_missing=True,
                                )
                            return self._completed_reply(
                                request=request,
                                descriptor=descriptor,
                                operation_id=replayed[0],
                                result=replayed[1],
                            )
                        self._mark_workspace_join_unknown(request_digest)
                        raise OperationFailed(
                            operation_id,
                            "operation.internal-failure",
                            "Scenario recovery abort outcome is unknown",
                            "unknown",
                            True,
                        ) from abort_error
                    self._mark_security_failure(security_consumption)
                    raise persisted_abort from exc
                if (
                    operation in {"scenario.repair", "scenario.destroy"}
                    and (
                        preserve_workspace_recovery
                        or (
                            isinstance(exc, WorkspaceError)
                            and exc.mutation_state == "unknown"
                        )
                    )
                ):
                    if isinstance(exc, OperationFailed):
                        if exc.mutation_state != "unknown":
                            self._mark_security_failure(security_consumption)
                        else:
                            self._mark_workspace_join_unknown(request_digest)
                        raise
                    self._mark_workspace_join_unknown(request_digest)
                    raise OperationFailed(
                        operation_id or f"op-{request['request_id']}",
                        "operation.internal-failure",
                        "Scenario Workspace outcome is pending reconciliation",
                        "unknown",
                        True,
                    ) from exc
                if operation_id is not None:
                    persisted_failure = self.store.fail_scenario_repair_or_destroy(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request["request_id"],
                        operation_id=operation_id,
                        reason=(
                            "lifecycle.repair-failed"
                            if operation == "scenario.repair"
                            else "lifecycle.destroy-failed"
                        ),
                    )
                else:
                    persisted_failure = None
                self._mark_security_failure(security_consumption)
                if persisted_failure is not None:
                    raise persisted_failure from exc
                if isinstance(exc, (StoreError, ProtocolError, OperationFailed)):
                    raise
                raise OperationFailed(
                    operation_id or f"op-{request['request_id']}",
                    "operation.external-failure",
                    "Scenario high-risk operation failed",
                    "committed" if operation_id is not None else "not_started",
                    False,
                ) from exc
        elif operation == "scenario.close":
            payload = request["payload"]
            if (
                request["fence"]["operation_generation"]
                != payload["scenario_state_revision"]
            ):
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "operation generation differs from scenario revision",
                    retryable=True,
                )
            operation_id, replay, executions = self.store.begin_scenario_close(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                scenario_generation=payload["scenario_generation"],
                scenario_state_revision=payload["scenario_state_revision"],
                drain_timeout_ms=payload["drain_timeout_ms"],
            )
            if replay is not None:
                result = replay
            else:
                assert executions is not None
                active = _ActiveOperation(threading.Event(), "committed")
                with self._active_operation_lock:
                    self._active_operations[operation_id] = active
                progress_sequence = 0

                def emit_progress(
                    state: str,
                    *,
                    completed_units: int,
                    total_units: int,
                    participant_id: str | None = None,
                ) -> None:
                    nonlocal progress_sequence
                    if progress_callback is not None:
                        try:
                            progress_callback(
                                progress_event(
                                    operation_id,
                                    progress_sequence,
                                    state,
                                    self.host_generation,
                                    {
                                        "phase": "closing_participants",
                                        "completed_units": completed_units,
                                        "total_units": total_units,
                                        "participant_id": participant_id,
                                        "cancellable": active.cancellable,
                                    },
                                )
                            )
                        except OSError:
                            # Losing an observation client cannot cancel or
                            # corrupt the already-started durable operation.
                            pass
                    progress_sequence += 1

                total_units = len(executions)
                emit_progress(
                    "running", completed_units=0, total_units=total_units
                )
                try:
                    if self.participants is None:
                        reports = [
                            self._driver_unavailable_close_report(entry)
                            if entry["kind"] == "driver"
                            else self._non_driver_close_report(entry)
                            for entry in executions
                        ]
                        cancelled = False
                    else:
                        reports, cancelled = (
                            self.participants.close_scenario_participants(
                                executions,
                                should_cancel=active.cancel_event.is_set,
                                progress_callback=lambda completed, total, participant: emit_progress(
                                    "cancelling"
                                    if active.cancel_event.is_set()
                                    else "running",
                                    completed_units=completed,
                                    total_units=total,
                                    participant_id=participant,
                                ),
                            )
                        )
                    with self._active_operation_lock:
                        active.cancellable = False
                    self.store.record_scenario_close_reports(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request["request_id"],
                        operation_id=operation_id,
                        reports=reports,
                        cancelled=cancelled,
                    )
                    if cancelled:
                        emit_progress(
                            "cancelled",
                            completed_units=sum(
                                report["command"] != "cancelled-before-action"
                                for report in reports
                            ),
                            total_units=total_units,
                        )
                    result = self.store.finalize_scenario_close(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        request_id=request["request_id"],
                        operation_id=operation_id,
                        reports=reports,
                        cancelled=cancelled,
                    )
                    emit_progress(
                        "completed",
                        completed_units=total_units,
                        total_units=total_units,
                    )
                finally:
                    with self._active_operation_lock:
                        self._active_operations.pop(operation_id, None)
            if result["scenario"]["observed_state"] == "closed":
                self.participant_auth.revoke_scenario(
                    target["project_instance_id"], target["scenario_id"]
                )
        elif operation == "scenario.start-participants":
            payload = request["payload"]
            if (
                request["fence"]["operation_generation"]
                != payload["scenario_state_revision"]
            ):
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "operation generation differs from scenario revision",
                    retryable=True,
                )
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "participant driver is not configured",
                    False,
                    "participant.driver-configure",
                )
            entry_scenario = self.store.scenario_status(
                target["project_instance_id"], target["scenario_id"]
            )["scenario"]
            if (
                entry_scenario["scenario_generation"]
                != payload["scenario_generation"]
                or entry_scenario["state_revision"]
                != payload["scenario_state_revision"]
            ):
                raise ProtocolError(
                    "scenario.stale-fence",
                    "fencing",
                    "scenario state fence differs",
                    retryable=True,
                )
            operation_id = f"op-{request['request_id']}"
            active = _ActiveOperation(threading.Event(), "committed")
            with self._active_operation_lock:
                if operation_id in self._active_operations:
                    # The same request is still executing (a timeout retry
                    # landing mid-flight). Running it twice would race the
                    # per-unit fences and leave cancel pointing at only one
                    # of the two loops.
                    raise ProtocolError(
                        "scenario.operation-in-progress",
                        "operation",
                        "this batch start request is still executing",
                        retryable=True,
                    )
                self._active_operations[operation_id] = active
            try:
                result = self._start_scenario_participants(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    request_id=request["request_id"],
                    operation_id=operation_id,
                    active=active,
                    progress_callback=progress_callback,
                )
            finally:
                with self._active_operation_lock:
                    self._active_operations.pop(operation_id, None)
        elif operation == "policy.template.list":
            result = self.projects.collaboration_templates(
                target["project_instance_id"]
            )
            # Validate detailed policy semantics before exposing project data.
            for template in result["templates"]:
                DeliveryCoordinator.validate_template(
                    template, "template-validation-scenario"
                )
            operation_id = f"read-{request['request_id']}"
        elif operation.startswith("policy.") or operation.startswith("delivery.") or operation == "message.send":
            if self.delivery is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Delivery driver is not configured",
                    retryable=True,
                )
            payload = request["payload"]
            common = {
                "project_instance_id": target["project_instance_id"],
                "scenario_id": target["scenario_id"],
            }
            if operation == "policy.show":
                operation_id, result = self.delivery.show_policy(**common)
            elif operation in {"policy.plan", "policy.apply-plan"}:
                replayed = (
                    self.delivery.replay_request(
                        request["request_id"], request_digest
                    )
                    if operation == "policy.apply-plan"
                    else None
                )
                if replayed is not None:
                    operation_id, result = replayed
                elif request["fence"]["operation_generation"] != payload[
                    "scenario_state_revision"
                ]:
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "policy plan generation differs from Scenario revision",
                        retryable=True,
                    )
                else:
                    templates = self._scenario_collaboration_templates(
                        target["project_instance_id"], target["scenario_id"]
                    )["templates"]
                    template = next(
                        (
                            value
                            for value in templates
                            if value["template_id"] == payload["template_id"]
                        ),
                        None,
                    )
                    if template is None:
                        raise ProtocolError(
                            "operation.precondition-failed",
                            "operation",
                            "collaboration template is unavailable",
                        )
                    if operation == "policy.plan":
                        operation_id, result = self.delivery.plan_policy(
                            scenario_generation=payload["scenario_generation"],
                            scenario_state_revision=payload[
                                "scenario_state_revision"
                            ],
                            template=template,
                            **common,
                        )
                    else:
                        operation_id, result = self.delivery.apply_policy_plan(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            scenario_generation=payload["scenario_generation"],
                            scenario_state_revision=payload[
                                "scenario_state_revision"
                            ],
                            template=template,
                            plan_digest=payload["plan_digest"],
                            **common,
                        )
                if operation == "policy.apply-plan":
                    self._refresh_participant_collaboration_contexts(
                        target["project_instance_id"], target["scenario_id"]
                    )
            elif operation == "policy.apply":
                if request["fence"]["operation_generation"] != payload["scenario_state_revision"]:
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "policy operation generation differs from Scenario revision",
                        retryable=True,
                    )
                operation_id, result = self.delivery.apply_policy(
                    request_id=request["request_id"],
                    request_digest=request_digest,
                    scenario_generation=payload["scenario_generation"],
                    scenario_state_revision=payload["scenario_state_revision"],
                    policy_pack=payload["policy_pack"],
                    **common,
                )
                self._refresh_participant_collaboration_contexts(
                    target["project_instance_id"], target["scenario_id"]
                )
            elif operation == "message.send":
                if (
                    request["fence"]["operation_generation"]
                    != payload["sender_participant_state_revision"]
                    or request["fence"]["participant_generation"]
                    != payload["sender_participant_generation"]
                ):
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "message sender fence differs",
                        retryable=True,
                    )
                with self._participant_destroy_lock:
                    operation_id, result = self.delivery.send_message(
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        scenario_generation=payload["scenario_generation"],
                        scenario_state_revision=payload[
                            "scenario_state_revision"
                        ],
                        sender_participant_id=payload[
                            "sender_participant_id"
                        ],
                        sender_participant_generation=payload[
                            "sender_participant_generation"
                        ],
                        sender_participant_state_revision=payload[
                            "sender_participant_state_revision"
                        ],
                        receiver_intent=payload["receiver_intent"],
                        message_id=payload["message_id"],
                        message_kind=payload["message_kind"],
                        message=payload["message"],
                        **common,
                    )
            elif operation == "delivery.list":
                operation_id, result = self.delivery.list_deliveries(
                    limit=payload["limit"],
                    after_delivery_id=payload.get("after_delivery_id"),
                    collection_digest=payload.get("collection_digest"),
                    thread_root_delivery_id=payload.get(
                        "thread_root_delivery_id"
                    ),
                    **common,
                )
            elif operation == "delivery.status":
                operation_id, result = self.delivery.status(
                    delivery_id=payload["delivery_id"], **common
                )
            elif operation == "delivery.consume":
                if request["fence"]["operation_generation"] != payload["event_sequence"]:
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "delivery event fence differs",
                        retryable=True,
                    )
                operation_id, result = self.delivery.consume(
                    delivery_id=payload["delivery_id"],
                    event_sequence=payload["event_sequence"],
                    consumption_ack=payload["consumption_ack"],
                    **common,
                )
            else:
                if request["fence"]["operation_generation"] != payload["event_sequence"]:
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "delivery event fence differs",
                        retryable=True,
                    )
                operation_id, result = self.delivery.retry(
                    delivery_id=payload["delivery_id"],
                    event_sequence=payload["event_sequence"],
                    **common,
                )
        elif operation == "participant.list":
            result = self.store.list_participants(
                target["project_instance_id"], target["scenario_id"]
            )
            result["participants"] = [
                self._project_participant_objective_issuance(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant=participant,
                )
                for participant in result["participants"]
            ]
            operation_id = f"read-{request['request_id']}"
        elif operation == "participant.template.list":
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Participant driver is not configured",
                    retryable=True,
                )
            result = self.participants.list_templates()
            operation_id = f"read-{request['request_id']}"
        elif operation == "participant.destroy":
            payload = request["payload"]
            participant_generation = request["fence"]["participant_generation"]
            if (
                request["fence"]["operation_generation"]
                != payload["participant_state_revision"]
            ):
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "participant deletion generation differs from revision",
                    retryable=True,
                )
            if payload["confirmed"] is not True:
                raise ProtocolError(
                    "auth.confirmation-required",
                    "authorization",
                    "participant deletion requires confirmation",
                )
            if self.delivery is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Delivery coordinator is not configured",
                    retryable=True,
                )
            self._reconcile_participant_destroy_operations()
            try:
                replayed = self.store.replay_request(
                    request["request_id"], request_digest
                )
            except StoreError as exc:
                if exc.code != "scenario.operation-in-progress":
                    raise
                pending = next(
                    (
                        value
                        for value in self.store.pending_participant_destroy_operations()
                        if value["request_id"] == request["request_id"]
                        and value["request_digest"] == request_digest
                    ),
                    None,
                )
                if pending is None:
                    raise
                raise OperationFailed(
                    pending["operation_id"],
                    "operation.external-failure",
                    "participant deletion is awaiting delivery settlement",
                    "committed",
                    True,
                ) from exc
            if replayed is not None:
                operation_id, result = replayed
            else:
                try:
                    operation_id, result, settlement = (
                        self.store.begin_participant_destroy(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            host_generation=self.host_generation,
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            participant_id=target["participant_id"],
                            scenario_generation=payload["scenario_generation"],
                            scenario_state_revision=payload[
                                "scenario_state_revision"
                            ],
                            participant_generation=participant_generation,
                            participant_state_revision=payload[
                                "participant_state_revision"
                            ],
                        )
                    )
                except OSError as exc:
                    try:
                        durable_status, durable_operation_id = (
                            self.store.inspect_request_status(
                                request["request_id"], request_digest
                            )
                        )
                    except (StoreError, OSError) as inspect_error:
                        raise ProtocolError(
                            "availability.host-degraded",
                            "availability",
                            "participant deletion intent outcome is unavailable",
                            retryable=True,
                        ) from inspect_error
                    if durable_status == "completed":
                        replayed = self.store.replay_request(
                            request["request_id"], request_digest
                        )
                        assert replayed is not None
                        operation_id, result = replayed
                        settlement = None
                    elif durable_status == "pending":
                        pending = next(
                            (
                                value
                                for value in self.store.pending_participant_destroy_operations()
                                if value["request_id"] == request["request_id"]
                                and value["request_digest"] == request_digest
                            ),
                            None,
                        )
                        if pending is None or durable_operation_id != pending[
                            "operation_id"
                        ]:
                            raise ProtocolError(
                                "availability.host-degraded",
                                "availability",
                                "participant deletion intent differs",
                                retryable=True,
                            ) from exc
                        try:
                            operation_id, result = (
                                self._complete_participant_destroy(pending)
                            )
                        except (
                            DeliveryError,
                            ParticipantAuthError,
                            StoreError,
                            OSError,
                        ) as completion_error:
                            raise OperationFailed(
                                pending["operation_id"],
                                "operation.external-failure",
                                (
                                    "participant deletion is awaiting "
                                    "delivery settlement"
                                ),
                                "committed",
                                True,
                            ) from completion_error
                        settlement = None
                    else:
                        raise ProtocolError(
                            "operation.external-failure",
                            "operation",
                            "participant deletion did not start; retry the request",
                            retryable=True,
                        ) from exc
                if result is None:
                    assert settlement is not None
                    pending = {
                        "project_instance_id": target["project_instance_id"],
                        "scenario_id": target["scenario_id"],
                        "participant_id": target["participant_id"],
                        "participant_generation": participant_generation,
                        "request_id": request["request_id"],
                        "request_digest": request_digest,
                        "operation_id": operation_id,
                        "delivery_request_id": settlement[
                            "delivery_request_id"
                        ],
                        "delivery_request_digest": settlement[
                            "delivery_request_digest"
                        ],
                    }
                    try:
                        operation_id, result = self._complete_participant_destroy(
                            pending
                        )
                    except (
                        DeliveryError,
                        ParticipantAuthError,
                        StoreError,
                        OSError,
                    ) as exc:
                        raise OperationFailed(
                            operation_id,
                            "operation.external-failure",
                            "participant deletion is awaiting delivery settlement",
                            "committed",
                            True,
                        ) from exc
                else:
                    assert result is not None
        elif operation.startswith("participant."):
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Participant driver is not configured",
                    retryable=True,
                )
            payload = request["payload"]
            participant_generation = request["fence"]["participant_generation"]
            if operation == "participant.add":
                if (
                    request["fence"]["operation_generation"] != 0
                    or participant_generation != 0
                ):
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "participant add requires an absent-record fence",
                        retryable=True,
                    )
                operation_id, result = self.participants.add(
                    request_id=request["request_id"],
                    request_digest=request_digest,
                    host_generation=self.host_generation,
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    participant_id=target["participant_id"],
                    scenario_generation=payload["scenario_generation"],
                    scenario_state_revision=payload["scenario_state_revision"],
                    launch_spec=payload["launch_spec"],
                    presentation_driver_id=payload["presentation_driver_id"],
                )
            else:
                if (
                    operation != "participant.status"
                    and request["fence"]["operation_generation"]
                    != payload["participant_state_revision"]
                ):
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "participant operation generation differs from revision",
                        retryable=True,
                    )
                common = {
                    "project_instance_id": target["project_instance_id"],
                    "scenario_id": target["scenario_id"],
                    "participant_id": target["participant_id"],
                    "scenario_generation": payload["scenario_generation"],
                    "scenario_state_revision": payload["scenario_state_revision"],
                    "participant_generation": participant_generation,
                    "participant_state_revision": payload[
                        "participant_state_revision"
                    ],
                }
                if operation == "participant.status":
                    operation_id, result = self.participants.status(**common)
                elif operation == "participant.start":
                    participant_client = self._participant_launch_material(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        participant_id=target["participant_id"],
                        participant_generation=participant_generation,
                        participant_state_revision=payload[
                            "participant_state_revision"
                        ],
                    )
                    try:
                        operation_id, result = self.participants.start(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            host_generation=self.host_generation,
                            participant_client=participant_client,
                            **common,
                        )
                        started_participant = result["participant"]
                        self._participant_launch_material(
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            participant_id=target["participant_id"],
                            participant_generation=started_participant[
                                "participant_generation"
                            ],
                            participant_state_revision=started_participant[
                                "state_revision"
                            ],
                        )
                    except BaseException:
                        self.participant_auth.revoke(
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            participant_id=target["participant_id"],
                            participant_generation=participant_generation,
                        )
                        raise
                elif operation == "participant.recover":
                    operation_id, result = self.participants.recover(
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        host_generation=self.host_generation,
                        **common,
                    )
                    self.participant_auth.revoke(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        participant_id=target["participant_id"],
                        participant_generation=participant_generation,
                    )
                elif operation == "participant.replace":
                    try:
                        operation_id, result = self.participants.replace(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            host_generation=self.host_generation,
                            launch_spec=payload["launch_spec"],
                            presentation_driver_id=payload[
                                "presentation_driver_id"
                            ],
                            participant_client_factory=(
                                lambda generation, revision: (
                                    self._participant_launch_material(
                                        project_instance_id=target[
                                            "project_instance_id"
                                        ],
                                        scenario_id=target["scenario_id"],
                                        participant_id=target[
                                            "participant_id"
                                        ],
                                        participant_generation=generation,
                                        participant_state_revision=revision,
                                    )
                                )
                            ),
                            **common,
                        )
                    except BaseException:
                        current = next(
                            (
                                item
                                for item in self.store.list_participants(
                                    target["project_instance_id"],
                                    target["scenario_id"],
                                )["participants"]
                                if item["participant_id"]
                                == target["participant_id"]
                            ),
                            None,
                        )
                        if (
                            current is not None
                            and current["participant_generation"]
                            != participant_generation
                        ):
                            self.participant_auth.revoke(
                                project_instance_id=target[
                                    "project_instance_id"
                                ],
                                scenario_id=target["scenario_id"],
                                participant_id=target["participant_id"],
                                participant_generation=participant_generation,
                            )
                            self.participant_auth.revoke(
                                project_instance_id=target[
                                    "project_instance_id"
                                ],
                                scenario_id=target["scenario_id"],
                                participant_id=target["participant_id"],
                                participant_generation=current[
                                    "participant_generation"
                                ],
                            )
                        raise
                    replaced = result["participant"]
                    self.participant_auth.revoke(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        participant_id=target["participant_id"],
                        participant_generation=participant_generation,
                    )
                    if replaced["observed_state"] == "ready":
                        self._participant_launch_material(
                            project_instance_id=target[
                                "project_instance_id"
                            ],
                            scenario_id=target["scenario_id"],
                            participant_id=target["participant_id"],
                            participant_generation=replaced[
                                "participant_generation"
                            ],
                            participant_state_revision=replaced[
                                "state_revision"
                            ],
                        )
                elif operation == "participant.force-stop":
                    try:
                        operation_id, result = self.participants.force_stop(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            host_generation=self.host_generation,
                            **common,
                        )
                    except BaseException:
                        self._mark_security_failure(security_consumption)
                        raise
                    self.participant_auth.revoke(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        participant_id=target["participant_id"],
                        participant_generation=participant_generation,
                    )
                else:
                    operation_id, result = self.participants.stop(
                        request_id=request["request_id"],
                        request_digest=request_digest,
                        host_generation=self.host_generation,
                        **common,
                    )
                    self.participant_auth.revoke(
                        project_instance_id=target["project_instance_id"],
                        scenario_id=target["scenario_id"],
                        participant_id=target["participant_id"],
                        participant_generation=participant_generation,
                    )
        else:
            if self.workspace is None:
                raise ProtocolError(
                    "availability.adapter-unavailable",
                    "availability",
                    "Workspace/Environment adapter is not configured",
                    retryable=True,
                )
            payload = request["payload"]
            if request["fence"]["operation_generation"] != payload["scenario_state_revision"]:
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "workspace operation generation differs from Scenario revision",
                    retryable=True,
                )
            record, workspace_path = self.store.scenario_workspace(
                target["project_instance_id"],
                target["scenario_id"],
                allow_missing=operation
                in {"scenario.destroy", "scenario.force-destroy"},
            )
            if (
                record["scenario_generation"] != payload["scenario_generation"]
                or record["state_revision"] != payload["scenario_state_revision"]
            ):
                raise ProtocolError(
                    "fence.stale-operation-generation",
                    "fencing",
                    "workspace operation Scenario fence differs",
                    retryable=True,
                )
            common = {
                "request_id": request["request_id"],
                "request_digest": request_digest,
                "project_instance_id": target["project_instance_id"],
                "scenario_id": target["scenario_id"],
                "scenario_generation": payload["scenario_generation"],
                "scenario_state_revision": payload["scenario_state_revision"],
            }
            if operation == "workspace.plan":
                if record["observed_state"] != "closed":
                    raise ProtocolError(
                        "operation.precondition-failed",
                        "operation",
                        "workspace planning requires a closed Scenario",
                    )
                operation_id, result = self.workspace.plan(
                    **common,
                    workspace_id=record["workspace_binding_id"],
                    project_binding_digest=record["project_binding_digest"],
                    requested_component_ids=payload["requested_component_ids"],
                    project_payload=payload["project_payload"],
                )
            elif operation == "workspace.provision":
                if record["observed_state"] != "closed":
                    raise ProtocolError(
                        "operation.precondition-failed",
                        "operation",
                        "workspace provisioning requires a closed Scenario",
                    )
                workspace_progress_sequence = 0
                ready_component_ids: set[str] = set()
                workspace_progress_seen = False
                workspace_progress_total = 0

                def emit_workspace_progress(
                    workspace_operation_id: str,
                    event: dict[str, Any],
                ) -> None:
                    nonlocal workspace_progress_sequence
                    nonlocal workspace_progress_seen
                    nonlocal workspace_progress_total
                    workspace_progress_seen = True
                    workspace_progress_total = event["total"]
                    if event["state"] == "ready":
                        ready_component_ids.add(event["component_id"])
                    if progress_callback is not None:
                        try:
                            progress_callback(
                                progress_event(
                                    workspace_operation_id,
                                    workspace_progress_sequence,
                                    {
                                        "waiting": "waiting",
                                        "cloning": "running",
                                        "building": "running",
                                        "ready": "running",
                                        "failed": "failed",
                                    }[event["state"]],
                                    self.host_generation,
                                    {
                                        "progress_kind": "workspace-component-v1",
                                        "phase": (
                                            "workspace.environment"
                                            if event["component_kind"] == "environment"
                                            else "workspace.repositories"
                                        ),
                                        "completed_units": len(ready_component_ids),
                                        "total_units": event["total"],
                                        "participant_id": None,
                                        "cancellable": False,
                                        "component_id": event["component_id"],
                                        "component_kind": event["component_kind"],
                                        "component_index": event["index"],
                                        "component_state": event["state"],
                                    },
                                )
                            )
                        except OSError:
                            pass
                    workspace_progress_sequence += 1

                operation_id, result = self.workspace.provision(
                    **common,
                    plan_digest=payload["plan_digest"],
                    workspace_path=workspace_path,
                    progress_callback=emit_workspace_progress,
                )
                if workspace_progress_seen and progress_callback is not None:
                    try:
                        progress_callback(
                            progress_event(
                                operation_id,
                                workspace_progress_sequence,
                                "completed",
                                self.host_generation,
                                {
                                    "progress_kind": "workspace-component-v1",
                                    "phase": "workspace.prepare",
                                    "completed_units": workspace_progress_total,
                                    "total_units": workspace_progress_total,
                                    "participant_id": None,
                                    "cancellable": False,
                                    "component_id": None,
                                    "component_kind": None,
                                    "component_index": None,
                                    "component_state": "complete",
                                },
                            )
                        )
                    except OSError:
                        pass
            else:
                operation_id, result = self.workspace.status(
                    **common,
                    receipt_digest=payload["receipt_digest"],
                    workspace_path=workspace_path,
                )
        reply = {
            "message_type": "operation_reply",
            "contract_version": CONTRACT_VERSION,
            "request_id": request["request_id"],
            "outcome": "completed",
            "operation_id": operation_id,
            "host_generation": self.host_generation,
            "mutation_state": (
                "not_started" if descriptor["mutation_class"] == "read_only" else "committed"
            ),
            "result": result,
        }
        if security_consumption is not None:
            assert self.security is not None
            self.security.mark_outcome(
                security_consumption,
                outcome="completed",
                operation_id=operation_id,
                result=result,
            )
        return reply

    def _force_destroy_scenario(
        self,
        *,
        request: dict[str, Any],
        request_digest: str,
        effect_preview: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Force-clean exact owned Participants, then destroy one fenced Workspace."""

        assert self.workspace is not None
        target = request["target"]
        payload = request["payload"]
        project_instance_id = target["project_instance_id"]
        scenario_id = target["scenario_id"]
        current = self.store.scenario_status(project_instance_id, scenario_id)[
            "scenario"
        ]
        normal_preview = self.store.scenario_high_risk_preview(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=current["scenario_generation"],
            scenario_state_revision=current["state_revision"],
            operation="scenario.destroy",
        )
        force_close_committed = False
        if not normal_preview["eligible"]:
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Participant driver is not configured",
                    retryable=True,
                )
            cleanup_request_id = "force-close-" + canonical_json_sha256(
                {
                    "request_id": request["request_id"],
                    "request_digest": request_digest,
                }
            )[:32]
            cleanup_digest = canonical_json_sha256(
                {
                    "operation": "scenario.force-close",
                    "outer_request_digest": request_digest,
                }
            )
            cleanup_operation_id, replay, executions = (
                self.store.begin_scenario_close(
                    request_id=cleanup_request_id,
                    request_digest=cleanup_digest,
                    host_generation=self.host_generation,
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    scenario_generation=current["scenario_generation"],
                    scenario_state_revision=current["state_revision"],
                    drain_timeout_ms=1,
                    force=True,
                )
            )
            if replay is None:
                assert executions is not None
                reports = self.participants.force_close_scenario_participants(
                    executions
                )
                self.store.record_scenario_close_reports(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    request_id=cleanup_request_id,
                    operation_id=cleanup_operation_id,
                    reports=reports,
                    force_stop_used=True,
                )
                self.store.finalize_scenario_close(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    request_id=cleanup_request_id,
                    operation_id=cleanup_operation_id,
                    reports=reports,
                    force_stop_used=True,
                )
            self.participant_auth.revoke_scenario(project_instance_id, scenario_id)
            force_close_committed = True
            current = self.store.scenario_status(project_instance_id, scenario_id)[
                "scenario"
            ]

        operation_id: str | None = None
        workspace_destroy_completed = False
        preserve_workspace_recovery = False
        workspace_request_id = self.store.workspace_join_request_id(
            "scenario.force-destroy",
            request["request_id"],
            request_digest,
        )
        try:
            operation_id, replay, workspace_path = self.store.begin_scenario_destroy(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=current["scenario_generation"],
                scenario_state_revision=current["state_revision"],
                expected_workspace_binding_state=effect_preview["workspace"][
                    "binding_state"
                ],
                expected_wip_summary_digest=effect_preview["workspace"][
                    "wip_summary_digest"
                ],
                operation_kind="scenario.force-destroy",
            )
            if replay is not None:
                return operation_id, replay
            assert workspace_path is not None
            try:
                with self._workspace_operation_lock:
                    _, workspace_result = self.workspace.destroy(
                        request_id=workspace_request_id,
                        request_digest=request_digest,
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        scenario_generation=payload["scenario_generation"],
                        workspace_path=workspace_path,
                        expected_wip_summary_digest=effect_preview["workspace"][
                            "wip_summary_digest"
                        ],
                        expected_binding_state=effect_preview["workspace"][
                            "binding_state"
                        ],
                        force=True,
                        before_external=lambda claim: (
                            self.store.bind_workspace_execution_claim(
                                project_instance_id=project_instance_id,
                                scenario_id=scenario_id,
                                request_id=request["request_id"],
                                request_digest=request_digest,
                                operation_id=operation_id,
                                workspace_request_id=workspace_request_id,
                                operation_kind="scenario.force-destroy",
                                scenario_generation=payload[
                                    "scenario_generation"
                                ],
                                workspace_claim=dict(claim),
                            )
                        ),
                    )
            except BaseException as destroy_error:
                completed = self.workspace.completed_request(
                    workspace_request_id, request_digest
                )
                if completed is not None:
                    _, workspace_result = completed
                elif (
                    isinstance(destroy_error, WorkspaceError)
                    and destroy_error.mutation_state == "not_started"
                ):
                    self.store.abort_scenario_destroy_no_effect(
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        request_id=request["request_id"],
                        operation_id=operation_id,
                        reason=destroy_error.code,
                    )
                    raise OperationFailed(
                        operation_id,
                        "operation.precondition-failed",
                        "Scenario destroy evidence changed; confirm a new request",
                        "committed",
                        True,
                    ) from destroy_error
                else:
                    preserve_workspace_recovery = True
                    raise
            workspace_destroy_completed = True
            result = self.store.finalize_scenario_destroy(
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                request_id=request["request_id"],
                operation_id=operation_id,
                workspace_evidence_sha256=canonical_json_sha256(workspace_result),
            )
            self.participant_auth.revoke_scenario(project_instance_id, scenario_id)
            return operation_id, result
        except BaseException as exc:
            if operation_id is None and isinstance(exc, OSError):
                try:
                    durable_status, durable_operation_id = (
                        self.store.inspect_request_status(
                            request["request_id"], request_digest
                        )
                    )
                except (StoreError, OSError):
                    durable_status, durable_operation_id = "unknown", None
                if durable_status == "completed":
                    replayed = self.store.replay_request(
                        request["request_id"], request_digest
                    )
                    assert replayed is not None
                    return replayed
                if durable_status == "failed":
                    self.store.replay_request(
                        request["request_id"], request_digest
                    )
                    raise StoreError(
                        "host.state-invalid", "failed request replay did not fail"
                    )
                if durable_status == "pending":
                    stable_status, stable_operation_id = (
                        self._stabilize_store_begin_pending(
                            request["request_id"], request_digest
                        )
                    )
                    if stable_status == "completed":
                        replayed = self.store.replay_request(
                            request["request_id"], request_digest
                        )
                        assert replayed is not None
                        return replayed
                    if stable_status == "failed":
                        self.store.replay_request(
                            request["request_id"], request_digest
                        )
                    raise OperationFailed(
                        stable_operation_id
                        or durable_operation_id
                        or f"op-{request['request_id']}",
                        "operation.internal-failure",
                        "Scenario force destroy intent outcome is unknown",
                        "unknown",
                        True,
                    ) from exc
                if durable_status == "unknown":
                    self._mark_workspace_join_unknown(request_digest)
                    raise OperationFailed(
                        f"op-{request['request_id']}",
                        "operation.internal-failure",
                        "Scenario force destroy intent outcome is unknown",
                        "unknown",
                        True,
                    ) from exc
                raise OperationFailed(
                    f"op-{request['request_id']}",
                    "operation.precondition-failed",
                    "Scenario force destroy intent was not committed; confirm a new request",
                    "committed" if force_close_committed else "not_started",
                    True,
                ) from exc
            if isinstance(exc, OperationFailed):
                raise
            if workspace_destroy_completed or preserve_workspace_recovery:
                self._mark_workspace_join_unknown(request_digest)
                raise OperationFailed(
                    operation_id or f"op-{request['request_id']}",
                    "operation.internal-failure",
                    "Scenario destroy outcome is pending reconciliation",
                    "unknown",
                    True,
                ) from exc
            if operation_id is not None:
                persisted_failure = self.store.fail_scenario_repair_or_destroy(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    request_id=request["request_id"],
                    operation_id=operation_id,
                    reason="lifecycle.force-destroy-failed",
                )
                if persisted_failure is not None:
                    raise persisted_failure from exc
            raise

    def _scenario_preflight(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        """Aggregate fresh, redacted readiness and permission observations."""

        diagnostic = self.store.scenario_diagnostic(
            project_instance_id, scenario_id
        )["diagnostic"]
        scenario = diagnostic["scenario"]
        participants = diagnostic["participants"]
        checks: list[dict[str, Any]] = []
        repair_actions = set(diagnostic["repair_actions"])

        def add_check(
            check_id: str,
            status: str,
            summary: str,
            repair_action: str | None = None,
        ) -> None:
            checks.append(
                {
                    "check_id": check_id,
                    "status": status,
                    "summary": summary,
                    "repair_action": repair_action,
                }
            )
            if repair_action is not None:
                repair_actions.add(repair_action)

        try:
            self.projects.canonical_root(project_instance_id)
        except ProjectError:
            add_check(
                "project.access",
                "blocked",
                "Registered project access is unavailable.",
                "project.register",
            )
        else:
            add_check(
                "project.access", "ready", "Registered project access is current."
            )

        scenario_repair = (
            scenario.get("degraded", {}).get("repair_action")
            if isinstance(scenario.get("degraded"), dict)
            else None
        )
        if scenario["observed_state"] in {"degraded", "provision_failed"}:
            add_check(
                "scenario.state",
                "blocked",
                "Scenario durable state requires repair.",
                scenario_repair or "scenario.repair",
            )
        elif diagnostic["active_operations"]:
            add_check(
                "scenario.state",
                "blocked",
                "A Scenario operation is still active; refresh before continuing.",
                "scenario.refresh",
            )
        else:
            add_check(
                "scenario.state", "ready", "Scenario durable state is operable."
            )

        workspace = (
            self.workspace.summary(project_instance_id, scenario_id)
            if self.workspace is not None
            else None
        )
        if workspace is not None and workspace.get("state") == "ready":
            add_check(
                "workspace.state", "ready", "Isolated Workspace is prepared."
            )
        elif workspace is not None and workspace.get("state") in {
            "failed",
            "degraded",
        }:
            add_check(
                "workspace.state",
                "blocked",
                "Workspace state requires Scenario repair.",
                "scenario.repair",
            )
        else:
            add_check(
                "workspace.state",
                "blocked",
                "Isolated Workspace has not been prepared.",
                "workspace.prepare",
            )

        degraded_participants = [
            value
            for value in participants
            if value["observed_state"] == "degraded"
        ]
        if degraded_participants:
            participant_actions = sorted(
                {
                    value["degraded"].get("repair_action")
                    for value in degraded_participants
                    if isinstance(value.get("degraded"), dict)
                    and isinstance(value["degraded"].get("repair_action"), str)
                }
            )
            action = participant_actions[0] if len(participant_actions) == 1 else None
            add_check(
                "participant.state",
                "blocked",
                "One or more participants require recovery.",
                action,
            )
            repair_actions.update(participant_actions)
        else:
            add_check(
                "participant.state", "ready", "Participant generations are operable."
            )

        interactive_required = any(
            value.get("interaction_mode") == "tui"
            and value.get("observed_state") != "detached"
            for value in participants
        )
        permission_observations: list[dict[str, Any]] = []
        if not interactive_required:
            add_check(
                "presentation.permission",
                "not_required",
                "No attached interactive participant requires presentation control.",
            )
        elif self.participants is None:
            add_check(
                "presentation.permission",
                "blocked",
                "The configured presentation driver is unavailable.",
                "participant.driver-configure",
            )
        else:
            try:
                permission_observations = self.participants.permission_probe()[
                    "permission_observations"
                ]
            except (ParticipantError, OSError):
                add_check(
                    "presentation.permission",
                    "blocked",
                    "Presentation permission could not be observed.",
                    "scenario.preflight",
                )
            else:
                blocked_permissions = [
                    value
                    for value in permission_observations
                    if value["status"] != "granted"
                ]
                if blocked_permissions:
                    actions = sorted(
                        {
                            value["remediation_ref"]
                            for value in blocked_permissions
                            if value["remediation_ref"] is not None
                        }
                    )
                    add_check(
                        "presentation.permission",
                        "blocked",
                        "Interactive presentation permission needs attention.",
                        actions[0] if len(actions) == 1 else "scenario.preflight",
                    )
                    repair_actions.update(actions)
                else:
                    add_check(
                        "presentation.permission",
                        "ready",
                        "Interactive presentation permission is granted.",
                    )

        preflight = {
            "schema_version": 1,
            "scope": {
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
                "scenario_generation": scenario["scenario_generation"],
                "scenario_state_revision": scenario["state_revision"],
            },
            "status": (
                "blocked"
                if any(value["status"] == "blocked" for value in checks)
                else "ready"
            ),
            "captured_at_epoch_ms": int(time.time() * 1000),
            "checks": checks,
            "permission_observations": permission_observations,
            "repair_actions": sorted(repair_actions),
        }
        preflight["preflight_digest"] = canonical_json_sha256(preflight)
        return {"preflight": preflight}

    def _reconcile_workspace_operations(self) -> None:
        assert self.workspace is not None
        for pending in self.store.pending_workspace_operations():
            with self._workspace_operation_lock:
                # Another startup/retry caller may have finalized the stale
                # projection while this caller waited for the process-local
                # join lock.  Refresh under that lock so one reconciliation
                # wave cannot finalize twice or consume two bounded attempts.
                current = next(
                    (
                        candidate
                        for candidate in self.store.pending_workspace_operations()
                        if candidate.get("operation_id")
                        == pending.get("operation_id")
                    ),
                    None,
                )
                if current is None:
                    continue
                self._reconcile_workspace_operation(current)

    @staticmethod
    def _workspace_join_kwargs(pending: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "project_instance_id": pending["project_instance_id"],
            "scenario_id": pending["scenario_id"],
            "request_id": pending["request_id"],
            "request_digest": pending["request_digest"],
            "operation_id": pending["operation_id"],
            "workspace_request_id": pending["workspace_request_id"],
            "operation_kind": pending["operation_kind"],
            "scenario_generation": pending["scenario_generation"],
        }

    def _mark_workspace_join_unknown(self, request_digest: str) -> None:
        if self.security is not None:
            self.security.reconcile_unknown_outcome(
                request_digest, allow_missing=True
            )

    def _stabilize_store_begin_pending(
        self, request_id: str, request_digest: str
    ) -> tuple[str, str | None]:
        """Publish Security unknown or observe a concurrent terminal Store result."""

        try:
            self._mark_workspace_join_unknown(request_digest)
        except SecurityError as security_error:
            try:
                status = self.store.inspect_request_status(
                    request_id, request_digest
                )
            except (StoreError, OSError):
                raise security_error
            if status[0] not in {"completed", "failed"}:
                raise security_error
            return status
        try:
            return self.store.inspect_request_status(request_id, request_digest)
        except (StoreError, OSError):
            return "unknown", None

    @staticmethod
    def _is_recovery_no_effect_failure(
        failure: OperationFailed, *, operation_id: str
    ) -> bool:
        return (
            failure.operation_id == operation_id
            and failure.code == "operation.precondition-failed"
            and failure.mutation_state == "committed"
            and failure.retryable is True
        )

    def _abort_reconciled_recovery_no_effect(
        self, pending: Mapping[str, Any], *, reason: str
    ) -> bool:
        """Finish a proven no-adapter recovery abort without risking Host bind."""

        try:
            self.store.abort_scenario_recovery_no_effect(
                project_instance_id=pending["project_instance_id"],
                scenario_id=pending["scenario_id"],
                request_id=pending["request_id"],
                operation_id=pending["operation_id"],
                reason=reason,
            )
        except (StoreError, OSError):
            try:
                replay = self.store.replay_request(
                    pending["request_id"], pending["request_digest"]
                )
            except OperationFailed as failure:
                if not self._is_recovery_no_effect_failure(
                    failure, operation_id=pending["operation_id"]
                ):
                    self._mark_workspace_join_unknown(
                        pending["request_digest"]
                    )
                    return False
                if self.security is not None:
                    self.security.reconcile_failed_outcome(
                        pending["request_digest"], allow_missing=True
                    )
                return True
            except StoreError:
                self._mark_workspace_join_unknown(pending["request_digest"])
                return False
            if replay is not None:
                if self.security is not None:
                    self.security.reconcile_completed_outcome(
                        pending["request_digest"],
                        operation_id=replay[0],
                        result=replay[1],
                        allow_missing=True,
                    )
                return True
            self._mark_workspace_join_unknown(pending["request_digest"])
            return False
        if self.security is not None:
            self.security.reconcile_failed_outcome(
                pending["request_digest"], allow_missing=True
            )
        return True

    def _degrade_workspace_join(
        self,
        pending: Mapping[str, Any],
        *,
        workspace_claim: Mapping[str, Any] | None,
        reason: str,
        unjoinable: bool,
    ) -> None:
        self.store.degrade_unknown_workspace_join(
            **self._workspace_join_kwargs(pending),
            workspace_claim=(
                dict(workspace_claim) if workspace_claim is not None else None
            ),
            reason=reason,
            unjoinable=unjoinable,
        )
        self._mark_workspace_join_unknown(pending["request_digest"])

    def _flatten_unjoinable_workspace_recovery(
        self,
        pending: Mapping[str, Any],
        *,
        workspace_path: Path,
        reason: str,
    ) -> bool:
        """Make every recover degradation leave a real manual-recovery exit."""

        if (
            self.workspace is None
            or pending.get("workspace_operation_kind") != "recover"
        ):
            return False
        try:
            resolution, frozen_claim = (
                self.workspace.retire_unjoinable_recovery(
                    workspace_request_id=pending["workspace_request_id"],
                    request_digest=pending["request_digest"],
                    workspace_path=workspace_path,
                    reason=reason,
                )
            )
        except (WorkspaceError, OSError):
            self._mark_workspace_join_unknown(pending["request_digest"])
            return True
        if resolution == "not_started":
            bound_values = (
                pending.get("workspace_operation_id"),
                pending.get("workspace_join_claim_digest"),
                pending.get("workspace_adapter_capability_digest"),
            )
            if any(value is not None for value in bound_values) and (
                bound_values[0] != frozen_claim.get("workspace_operation_id")
                or bound_values[1] != frozen_claim.get("claim_digest")
                or bound_values[2]
                != frozen_claim.get("adapter_capability_digest")
            ):
                # Workspace proved that *its* prepared recovery never called
                # the adapter, but Store is bound to a different execution
                # claim.  Do not use that proof to abort another operation.
                self._mark_workspace_join_unknown(pending["request_digest"])
                return True
            self._abort_reconciled_recovery_no_effect(
                pending, reason="workspace.recovery-not-started"
            )
            return True
        store_bound_claim = (
            frozen_claim
            if pending.get("workspace_operation_id")
            == frozen_claim.get("workspace_operation_id")
            and pending.get("workspace_join_claim_digest")
            == frozen_claim.get("claim_digest")
            and pending.get("workspace_adapter_capability_digest")
            == frozen_claim.get("adapter_capability_digest")
            else None
        )
        try:
            self._degrade_workspace_join(
                pending,
                workspace_claim=store_bound_claim,
                reason=reason,
                unjoinable=True,
            )
        except (StoreError, OSError):
            self._mark_workspace_join_unknown(pending["request_digest"])
        return True

    def _degrade_with_frozen_workspace_claim(
        self,
        pending: Mapping[str, Any],
        *,
        workspace_path: Path,
        reason: str,
    ) -> bool:
        """Preserve a manual-recovery capsule when live capability changed."""

        if (
            self.workspace is None
            or pending.get("workspace_operation_kind") == "recover"
        ):
            return False
        bound_values = (
            pending.get("workspace_operation_id"),
            pending.get("workspace_join_claim_digest"),
            pending.get("workspace_adapter_capability_digest"),
        )
        if all(isinstance(value, str) for value in bound_values):
            try:
                self._degrade_workspace_join(
                    pending,
                    workspace_claim=None,
                    reason=reason,
                    unjoinable=True,
                )
            except (StoreError, OSError):
                self._mark_workspace_join_unknown(pending["request_digest"])
            return True
        if any(value is not None for value in bound_values):
            self._mark_workspace_join_unknown(pending["request_digest"])
            return True
        try:
            frozen = self.workspace.inspect_frozen_pending_high_risk_join(
                workspace_request_id=pending["workspace_request_id"],
                request_digest=pending["request_digest"],
                workspace_path=workspace_path,
            )
            if frozen is None:
                return False
            self.store.bind_workspace_execution_claim(
                **self._workspace_join_kwargs(pending),
                workspace_claim=frozen,
            )
            self._degrade_workspace_join(
                pending,
                workspace_claim=frozen,
                reason=reason,
                unjoinable=True,
            )
        except (WorkspaceError, StoreError, OSError):
            self._mark_workspace_join_unknown(pending["request_digest"])
        return True

    def _finalize_reconciled_workspace_operation(
        self,
        pending: Mapping[str, Any],
        completed: tuple[str, dict[str, Any]],
    ) -> None:
        workspace_operation_id, workspace_result = completed
        expected_workspace_operation_id = pending.get("workspace_operation_id")
        expected_binding_state = pending.get("expected_workspace_binding_state")
        unprovisioned_destroy = (
            pending["operation_kind"]
            in {"scenario.destroy", "scenario.force-destroy"}
            and expected_binding_state
            in {"absent", "planned", "provision_failed"}
        )
        coordinator_proven_force_destroy = False
        if (
            pending["operation_kind"] == "scenario.force-destroy"
            and expected_binding_state == "ready"
            and isinstance(workspace_result, dict)
        ):
            workspace = workspace_result.get("workspace")
            evidence = (
                workspace.get("unprovisioned_destroy_evidence")
                if isinstance(workspace, dict)
                else None
            )
            coordinator_proven_force_destroy = (
                isinstance(evidence, dict)
                and evidence.get("operation_kind") == "destroy-unprovisioned"
                and evidence.get("binding_state_before") == "ready"
            )
        if (
            not (unprovisioned_destroy or coordinator_proven_force_destroy)
            and workspace_operation_id != expected_workspace_operation_id
        ):
            self._degrade_workspace_join(
                pending,
                workspace_claim=None,
                reason="workspace.join-unprovable",
                unjoinable=True,
            )
            return
        evidence = canonical_json_sha256(workspace_result)
        try:
            if pending["operation_kind"] == "scenario.repair":
                result = self.store.finalize_scenario_repair(
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    request_id=pending["request_id"],
                    operation_id=pending["operation_id"],
                    workspace_evidence_sha256=evidence,
                )
            else:
                result = self.store.finalize_scenario_destroy(
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    request_id=pending["request_id"],
                    operation_id=pending["operation_id"],
                    workspace_evidence_sha256=evidence,
                )
        except (StoreError, OSError) as publication_error:
            try:
                replay = self.store.replay_request(
                    pending["request_id"], pending["request_digest"]
                )
            except OperationFailed as failure:
                if failure.mutation_state == "unknown":
                    self._mark_workspace_join_unknown(
                        pending["request_digest"]
                    )
                elif self.security is not None:
                    self.security.reconcile_failed_outcome(
                        pending["request_digest"], allow_missing=True
                    )
                return
            except StoreError:
                raise publication_error
            if replay is None:
                raise publication_error
            _, result = replay
        if self.security is not None:
            self.security.reconcile_completed_outcome(
                pending["request_digest"],
                operation_id=pending["operation_id"],
                result=result,
                allow_missing=True,
            )

    def _reconcile_workspace_operation(self, pending: dict[str, Any]) -> None:
        """Join one exact Store/Workspace intersection without autonomous replay."""

        assert self.workspace is not None
        claim_box: dict[str, dict[str, Any]] = {}
        if pending.get("workspace_operation_kind") == "recover":
            try:
                recovery_request_exists = self.workspace.has_exact_request(
                    pending["workspace_request_id"],
                    pending["request_digest"],
                )
            except (WorkspaceError, OSError):
                self._mark_workspace_join_unknown(pending["request_digest"])
                return
            if not recovery_request_exists:
                # Store committed the manual-recovery intent, but Workspace
                # never published even the prepared request.  The recovery
                # adapter therefore cannot have run; restore the prior
                # degraded authority instead of manufacturing a first call
                # during startup.
                self._abort_reconciled_recovery_no_effect(
                    pending, reason="workspace.recovery-not-started"
                )
                return
            try:
                terminal_recovery = self.workspace.inspect_terminal_recovery(
                    workspace_request_id=pending["workspace_request_id"],
                    request_digest=pending["request_digest"],
                    workspace_path=self.store.workspace_path(
                        pending["workspace_binding_id"]
                    ),
                )
            except (WorkspaceError, StoreError, OSError):
                self._mark_workspace_join_unknown(pending["request_digest"])
                return
            if terminal_recovery is not None:
                if (
                    terminal_recovery.get("project_instance_id")
                    != pending["project_instance_id"]
                    or terminal_recovery.get("scenario_id")
                    != pending["scenario_id"]
                    or terminal_recovery.get("scenario_generation")
                    != pending["scenario_generation"]
                    or terminal_recovery.get("workspace_id")
                    != pending["workspace_binding_id"]
                    or terminal_recovery.get("prior_operation_kind")
                    != pending.get("expected_recovery_prior_operation_kind")
                    or terminal_recovery.get("prior_claim_digest")
                    != pending.get("expected_recovery_claim_digest")
                    or (
                        pending.get("workspace_operation_id") is not None
                        and pending.get("workspace_operation_id")
                        != terminal_recovery.get("workspace_operation_id")
                    )
                    or (
                        pending.get("workspace_join_claim_digest") is not None
                        and pending.get("workspace_join_claim_digest")
                        != terminal_recovery.get(
                            "last_recovery_claim_digest"
                        )
                    )
                ):
                    self._mark_workspace_join_unknown(
                        pending["request_digest"]
                    )
                    return
                if terminal_recovery["resolution"] == "not_started":
                    self._abort_reconciled_recovery_no_effect(
                        pending, reason=terminal_recovery["reason"]
                    )
                else:
                    terminal_claim = terminal_recovery["workspace_claim"]
                    store_bound_claim = (
                        terminal_claim
                        if isinstance(terminal_claim, dict)
                        and pending.get("workspace_operation_id")
                        == terminal_claim.get("workspace_operation_id")
                        and pending.get("workspace_join_claim_digest")
                        == terminal_claim.get("claim_digest")
                        and pending.get(
                            "workspace_adapter_capability_digest"
                        )
                        == terminal_claim.get("adapter_capability_digest")
                        else None
                    )
                    if (
                        terminal_recovery["unjoinable"] is False
                        and store_bound_claim is None
                    ):
                        self._mark_workspace_join_unknown(
                            pending["request_digest"]
                        )
                        return
                    try:
                        self._degrade_workspace_join(
                            pending,
                            workspace_claim=store_bound_claim,
                            reason=terminal_recovery["reason"],
                            unjoinable=terminal_recovery["unjoinable"],
                        )
                    except (StoreError, OSError):
                        self._mark_workspace_join_unknown(
                            pending["request_digest"]
                        )
                return
        workspace_operation_id = pending.get("workspace_operation_id")
        if (
            pending.get("workspace_operation_kind") == "recover"
            and isinstance(workspace_operation_id, str)
        ):
            try:
                recovery_not_started = self.workspace.failed_no_effect_request(
                    pending["workspace_request_id"],
                    pending["request_digest"],
                    workspace_operation_id,
                )
            except WorkspaceError:
                recovery_not_started = False
            if recovery_not_started:
                self._abort_reconciled_recovery_no_effect(
                    pending, reason="workspace.recovery-not-started"
                )
                return
        try:
            completed = self.workspace.completed_request(
                pending["workspace_request_id"], pending["request_digest"]
            )
        except WorkspaceError:
            try:
                fallback_workspace_path = self.store.workspace_path(
                    pending["workspace_binding_id"]
                )
            except StoreError:
                self._mark_workspace_join_unknown(pending["request_digest"])
                return
            if self._flatten_unjoinable_workspace_recovery(
                pending,
                workspace_path=fallback_workspace_path,
                reason="workspace.join-unprovable",
            ):
                return
            if self._degrade_with_frozen_workspace_claim(
                pending,
                workspace_path=fallback_workspace_path,
                reason="workspace.join-unprovable",
            ):
                return
            self._degrade_workspace_join(
                pending,
                workspace_claim=None,
                reason="workspace.join-unprovable",
                unjoinable=True,
            )
            return
        if completed is not None:
            try:
                self._finalize_reconciled_workspace_operation(pending, completed)
            except (StoreError, OSError):
                # Workspace has a durable completed outcome.  Keep Store
                # transitional and security unknown so a later startup can
                # finish the exact publication without another adapter call.
                self._mark_workspace_join_unknown(pending["request_digest"])
            return

        expected_binding_state = pending.get("expected_workspace_binding_state")
        expected_wip = pending.get("expected_wip_summary_digest")
        try:
            _, workspace_path = self.store.scenario_workspace(
                pending["project_instance_id"],
                pending["scenario_id"],
                allow_missing=pending["operation_kind"]
                in {"scenario.destroy", "scenario.force-destroy"},
            )
        except StoreError:
            if (
                pending["operation_kind"]
                in {"scenario.destroy", "scenario.force-destroy"}
                and expected_binding_state
                in {"absent", "planned", "provision_failed"}
            ):
                try:
                    self.store.restore_missing_workspace_container(
                        pending["project_instance_id"], pending["scenario_id"]
                    )
                    _, workspace_path = self.store.scenario_workspace(
                        pending["project_instance_id"], pending["scenario_id"]
                    )
                except StoreError:
                    try:
                        fallback_workspace_path = self.store.workspace_path(
                            pending["workspace_binding_id"]
                        )
                    except StoreError:
                        self._mark_workspace_join_unknown(
                            pending["request_digest"]
                        )
                        return
                    if self._flatten_unjoinable_workspace_recovery(
                        pending,
                        workspace_path=fallback_workspace_path,
                        reason="workspace.join-unprovable",
                    ):
                        return
                    self._mark_workspace_join_unknown(
                        pending["request_digest"]
                    )
                    return
            else:
                try:
                    fallback_workspace_path = self.store.workspace_path(
                        pending["workspace_binding_id"]
                    )
                except StoreError:
                    self._mark_workspace_join_unknown(
                        pending["request_digest"]
                    )
                    return
                self._mark_workspace_join_unknown(pending["request_digest"])
                return

        def retire_exhausted_recovery(claim: Mapping[str, Any]) -> str:
            if claim.get("operation_kind") != "recover":
                return "not_recover"
            try:
                resolution = self.workspace.retire_exhausted_recovery(
                    workspace_claim=claim,
                    workspace_path=workspace_path,
                    reason="workspace.join-attempts-exhausted",
                )
            except (WorkspaceError, OSError):
                # The checkpoint write may itself have crossed its atomic
                # publication boundary.  Keep Store transitional until a
                # later Host can observe the exact Workspace side.
                self._mark_workspace_join_unknown(pending["request_digest"])
                return "unknown"
            if resolution == "not_started":
                if not self._abort_reconciled_recovery_no_effect(
                    pending, reason="workspace.recovery-not-started"
                ):
                    return "unknown"
                return "aborted"
            return "retired"

        def authorize_join(raw_claim: Mapping[str, Any]) -> None:
            claim = dict(raw_claim)
            claim_box["claim"] = claim
            workspace_operation_id = claim["workspace_operation_id"]
            if workspace_operation_id in self._workspace_join_attempted_this_host:
                raise OperationFailed(
                    pending["operation_id"],
                    "operation.internal-failure",
                    "Scenario Workspace recovery is already pending this Host run",
                    "unknown",
                    True,
                )
            # Mark before the Store write/external call.  A crash or uncertain
            # write therefore cannot cause a second invocation in this Host
            # process; a later Host generation may consume the next durable
            # bounded attempt.
            self._workspace_join_attempted_this_host.add(
                workspace_operation_id
            )
            kwargs = self._workspace_join_kwargs(pending)
            self.store.bind_workspace_execution_claim(
                **kwargs, workspace_claim=claim
            )
            joined = self.store.claim_workspace_join(
                **kwargs, workspace_claim=claim
            )
            if joined["status"] == "exhausted":
                retirement = retire_exhausted_recovery(claim)
                if retirement == "aborted":
                    raise OperationFailed(
                        pending["operation_id"],
                        "operation.precondition-failed",
                        "Scenario recovery did not reach the adapter",
                        "committed",
                        True,
                    )
                if retirement == "unknown":
                    raise OperationFailed(
                        pending["operation_id"],
                        "operation.internal-failure",
                        "Scenario Workspace recovery remains pending",
                        "unknown",
                        True,
                    )
                self._degrade_workspace_join(
                    pending,
                    workspace_claim=claim,
                    reason="workspace.join-attempts-exhausted",
                    unjoinable=False,
                )
                raise OperationFailed(
                    pending["operation_id"],
                    "operation.internal-failure",
                    "Scenario Workspace recovery attempts are exhausted",
                    "unknown",
                    False,
                )

        try:
            pending_claim = self.workspace.inspect_pending_high_risk_join(
                workspace_request_id=pending["workspace_request_id"],
                request_digest=pending["request_digest"],
                workspace_path=workspace_path,
            )
            if pending_claim is not None:
                claim_box["claim"] = pending_claim
                completed = self.workspace.resume_exact_high_risk_join(
                    workspace_claim=pending_claim,
                    workspace_path=workspace_path,
                    before_external=authorize_join,
                )
            elif (
                pending["operation_kind"] == "scenario.repair"
                and pending.get("workspace_operation_kind") == "recover"
            ):
                recovery_claim_digest = pending.get(
                    "expected_recovery_claim_digest"
                )
                recovery_inventory_digest = pending.get(
                    "expected_recovery_inventory_digest"
                )
                if (
                    not isinstance(expected_wip, str)
                    or not isinstance(recovery_claim_digest, str)
                    or not isinstance(recovery_inventory_digest, str)
                ):
                    raise WorkspaceError(
                        "workspace.join-unprovable",
                        "workspace recovery evidence is unavailable",
                        mutation_state="unknown",
                    )
                completed = self.workspace.recover(
                    request_id=pending["workspace_request_id"],
                    request_digest=pending["request_digest"],
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    scenario_generation=pending["scenario_generation"],
                    workspace_path=workspace_path,
                    expected_wip_summary_digest=expected_wip,
                    expected_prior_claim_digest=recovery_claim_digest,
                    expected_inventory_digest=recovery_inventory_digest,
                    before_external=authorize_join,
                )
            elif pending["operation_kind"] == "scenario.repair":
                if not isinstance(expected_wip, str):
                    raise WorkspaceError(
                        "workspace.join-unprovable",
                        "workspace repair evidence is unavailable",
                        mutation_state="unknown",
                    )
                completed = self.workspace.repair(
                    request_id=pending["workspace_request_id"],
                    request_digest=pending["request_digest"],
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    scenario_generation=pending["scenario_generation"],
                    workspace_path=workspace_path,
                    expected_wip_summary_digest=expected_wip,
                    before_external=authorize_join,
                )
            else:
                if (
                    expected_binding_state
                    not in {"absent", "planned", "provision_failed", "ready"}
                    or not isinstance(expected_wip, str)
                ):
                    raise WorkspaceError(
                        "workspace.join-unprovable",
                        "workspace destroy evidence is unavailable",
                        mutation_state="unknown",
                    )
                completed = self.workspace.destroy(
                    request_id=pending["workspace_request_id"],
                    request_digest=pending["request_digest"],
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    scenario_generation=pending["scenario_generation"],
                    workspace_path=workspace_path,
                    expected_wip_summary_digest=expected_wip,
                    expected_binding_state=expected_binding_state,
                    force=(
                        pending["operation_kind"] == "scenario.force-destroy"
                    ),
                    before_external=authorize_join,
                )
        except OperationFailed:
            return
        except WorkspaceError as exc:
            claim = claim_box.get("claim")
            if exc.mutation_state == "not_started":
                coordinator_no_effect = False
                if claim is not None:
                    try:
                        coordinator_no_effect = (
                            self.workspace.failed_no_effect_request(
                                pending["workspace_request_id"],
                                pending["request_digest"],
                                claim["workspace_operation_id"],
                            )
                        )
                    except WorkspaceError:
                        coordinator_no_effect = False
                unprovisioned_no_adapter = (
                    pending["operation_kind"]
                    in {"scenario.destroy", "scenario.force-destroy"}
                    and expected_binding_state
                    in {"absent", "planned", "provision_failed"}
                )
                if not coordinator_no_effect and not unprovisioned_no_adapter:
                    self._degrade_workspace_join(
                        pending,
                        workspace_claim=None,
                        reason="workspace.join-unprovable",
                        unjoinable=True,
                    )
                    return
                if pending["operation_kind"] in {
                    "scenario.destroy",
                    "scenario.force-destroy",
                }:
                    try:
                        self.store.abort_scenario_destroy_no_effect(
                            project_instance_id=pending["project_instance_id"],
                            scenario_id=pending["scenario_id"],
                            request_id=pending["request_id"],
                            operation_id=pending["operation_id"],
                            reason=exc.code,
                        )
                    except (StoreError, OSError):
                        self._mark_workspace_join_unknown(
                            pending["request_digest"]
                        )
                        return
                elif pending.get("workspace_operation_kind") == "recover":
                    if not self._abort_reconciled_recovery_no_effect(
                        pending, reason=exc.code
                    ):
                        return
                    return
                else:
                    self.store.fail_scenario_repair_or_destroy(
                        project_instance_id=pending["project_instance_id"],
                        scenario_id=pending["scenario_id"],
                        request_id=pending["request_id"],
                        operation_id=pending["operation_id"],
                        reason="lifecycle.repair-failed",
                    )
                if self.security is not None:
                    self.security.reconcile_failed_outcome(
                        pending["request_digest"], allow_missing=True
                    )
                return
            latest = next(
                (
                    value
                    for value in self.store.pending_workspace_operations()
                    if value["operation_id"] == pending["operation_id"]
                ),
                None,
            )
            attempts = (
                latest.get("workspace_join_attempts")
                if isinstance(latest, dict)
                else None
            )
            exhausted = (
                isinstance(attempts, dict)
                and attempts.get("count") == attempts.get("max_attempts") == 3
            )
            if exhausted and claim is not None:
                retirement = retire_exhausted_recovery(claim)
                if retirement in {"aborted", "unknown"}:
                    return
                self._degrade_workspace_join(
                    pending,
                    workspace_claim=claim,
                    reason="workspace.join-attempts-exhausted",
                    unjoinable=False,
                )
            elif claim is None or exc.code in {
                "workspace.join-unprovable",
                "ipc.request-reused",
            }:
                if self._flatten_unjoinable_workspace_recovery(
                    pending,
                    workspace_path=workspace_path,
                    reason="workspace.join-unprovable",
                ):
                    return
                if self._degrade_with_frozen_workspace_claim(
                    pending,
                    workspace_path=workspace_path,
                    reason="workspace.join-unprovable",
                ):
                    return
                self._degrade_workspace_join(
                    pending,
                    workspace_claim=None,
                    reason="workspace.join-unprovable",
                    unjoinable=True,
                )
            else:
                self._mark_workspace_join_unknown(pending["request_digest"])
            return
        except (StoreError, OSError, KeyError, TypeError):
            if self._flatten_unjoinable_workspace_recovery(
                pending,
                workspace_path=workspace_path,
                reason="workspace.join-unprovable",
            ):
                return
            if self._degrade_with_frozen_workspace_claim(
                pending,
                workspace_path=workspace_path,
                reason="workspace.join-unprovable",
            ):
                return
            try:
                self._degrade_workspace_join(
                    pending,
                    workspace_claim=None,
                    reason="workspace.join-unprovable",
                    unjoinable=True,
                )
            except (StoreError, OSError):
                self._mark_workspace_join_unknown(pending["request_digest"])
            return

        refreshed = next(
            (
                value
                for value in self.store.pending_workspace_operations()
                if value.get("operation_id") == pending["operation_id"]
            ),
            None,
        )
        if refreshed is None:
            # Another exact caller finalized while this one was executing.
            return
        pending = refreshed
        try:
            self._finalize_reconciled_workspace_operation(pending, completed)
        except (StoreError, OSError):
            self._mark_workspace_join_unknown(pending["request_digest"])

    def _security_context(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        operation = request["operation"]
        target = request["target"]
        payload = request["payload"]
        if operation == "participant.force-stop":
            return self.store.participant_security_context(
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                participant_id=target["participant_id"],
                scenario_generation=payload["scenario_generation"],
                scenario_state_revision=payload["scenario_state_revision"],
                participant_generation=request["fence"]["participant_generation"],
                participant_state_revision=payload["participant_state_revision"],
            )
        if operation == "resource.break":
            return self.store.resource_break_context(
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                scenario_generation=payload["scenario_generation"],
                scenario_state_revision=payload["scenario_state_revision"],
                lease_id=payload["lease_id"],
                lease_revision=payload["lease_revision"],
            )
        if operation in {
            "scenario.repair",
            "scenario.destroy",
            "scenario.force-destroy",
        }:
            return self._scenario_high_risk_context(
                request, operation=operation, require_eligible=True
            )
        raise ProtocolError(
            "ipc.operation-not-allowed",
            "protocol",
            "high-risk operation is not implemented",
        )

    def _scenario_high_risk_context(
        self,
        request: dict[str, Any],
        *,
        operation: str,
        require_eligible: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = request["target"]
        payload = request["payload"]
        store_preview = self.store.scenario_high_risk_preview(
            project_instance_id=target["project_instance_id"],
            scenario_id=target["scenario_id"],
            scenario_generation=payload["scenario_generation"],
            scenario_state_revision=payload["scenario_state_revision"],
            operation=operation,
        )
        workspace_preview: dict[str, Any]
        subject: dict[str, Any]
        if self.workspace is None:
            workspace_preview = {
                "state": "adapter_unavailable",
                "drift_codes": ["workspace.adapter-unavailable"],
                "wip_summary_digest": None,
                "canonical_source_wip_mutation": False,
            }
            subject = {}
        else:
            record, workspace_path = self.store.scenario_workspace(
                target["project_instance_id"],
                target["scenario_id"],
                allow_missing=operation
                in {"scenario.destroy", "scenario.force-destroy"},
            )
            if record["workspace_binding_id"] is None:
                workspace_preview = {
                    "state": "binding_unavailable",
                    "drift_codes": ["workspace.binding-unavailable"],
                    "wip_summary_digest": None,
                    "canonical_source_wip_mutation": False,
                }
                subject = {}
            else:
                workspace_preview, subject = self.workspace.high_risk_context(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    scenario_generation=payload["scenario_generation"],
                    workspace_path=workspace_path,
                    operation=operation,
                )
                if workspace_preview.get("state") == "recovery-required":
                    recovery = workspace_preview.get("recovery")
                    try:
                        authority = self.store.scenario_workspace_recovery_authority(
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            scenario_generation=payload[
                                "scenario_generation"
                            ],
                            scenario_state_revision=payload[
                                "scenario_state_revision"
                            ],
                        )
                    except StoreError:
                        authority = None
                    if (
                        not isinstance(recovery, dict)
                        or not isinstance(authority, dict)
                        or authority.get("workspace_binding_id")
                        != workspace_preview.get("workspace_id")
                        or authority.get("prior_operation_kind")
                        != recovery.get("prior_operation_kind")
                        or authority.get("prior_operation_claim_digest")
                        != recovery.get("prior_operation_claim_digest")
                    ):
                        workspace_preview = {
                            **workspace_preview,
                            "state": "recovery-unprovable",
                            "drift_codes": sorted(
                                set(workspace_preview.get("drift_codes", []))
                                | {"workspace.recovery-authority-missing"}
                            ),
                        }
                        subject = {}
        # force-destroy exists to remove a Scenario that is already broken, so a
        # workspace that no longer observes as aligned cannot be a prerequisite
        # for it without closing the only exit. The drift stays visible in the
        # workspace observation the owner confirms; it just stops blocking.
        alignment_blocks = (
            operation != "scenario.force-destroy"
            and workspace_preview["state"]
            not in {
                "aligned",
                "unprovisioned",
                *(
                    {"recovery-required"}
                    if operation == "scenario.repair"
                    else set()
                ),
            }
        )
        eligible = store_preview["eligible"] and not alignment_blocks
        effect_preview = {
            **store_preview,
            "eligible": eligible,
            "blockers": sorted(
                set(store_preview["blockers"])
                | ({"workspace.not-aligned"} if alignment_blocks else set())
            ),
            "workspace": workspace_preview,
        }
        if require_eligible and not eligible:
            # Name the blockers. Without them the caller cannot tell an
            # unaligned workspace from an open Scenario, and the two need
            # completely different recovery steps.
            detail = ", ".join(effect_preview["blockers"])
            raise ProtocolError(
                "operation.precondition-failed",
                "operation",
                "Scenario high-risk operation prerequisites differ"
                + (f": {detail}" if detail else ""),
            )
        return effect_preview, subject

    def _validate_high_risk_preflight(self, request: dict[str, Any]) -> None:
        """Reject impossible high-risk calls before displaying confirmation UI."""

        operation = request["operation"]
        payload = request["payload"]
        if operation == "participant.force-stop":
            if self.participants is None:
                raise ProtocolError(
                    "availability.driver-unavailable",
                    "availability",
                    "Participant driver is not configured",
                    retryable=True,
                )
            expected_revision = payload["participant_state_revision"]
        else:
            expected_revision = payload["scenario_state_revision"]
            if operation in {
                "scenario.repair",
                "scenario.destroy",
                "scenario.force-destroy",
            } and self.workspace is None:
                raise ProtocolError(
                    "availability.adapter-unavailable",
                    "availability",
                    "Workspace/Environment adapter is not configured",
                    retryable=True,
                )
        if request["fence"]["operation_generation"] != expected_revision:
            raise ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                "high-risk operation fence differs from target revision",
                retryable=True,
            )

    def _mark_security_failure(
        self, consumption: dict[str, Any] | None
    ) -> None:
        if consumption is None or self.security is None:
            return
        self.security.mark_outcome(
            consumption,
            outcome="failed",
            operation_id=None,
            result=None,
        )

    def _completed_reply(
        self,
        *,
        request: dict[str, Any],
        descriptor: dict[str, Any],
        operation_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "message_type": "operation_reply",
            "contract_version": CONTRACT_VERSION,
            "request_id": request["request_id"],
            "outcome": "completed",
            "operation_id": operation_id,
            "host_generation": self.host_generation,
            "mutation_state": (
                "not_started"
                if descriptor["mutation_class"] == "read_only"
                else "committed"
            ),
            "result": result,
        }

    @staticmethod
    def _unavailable_scenario_topology(
        participants: list[dict[str, Any]],
    ) -> dict[str, Any]:
        observations: list[dict[str, Any]] = []
        for participant in participants:
            interaction_mode = participant["interaction_mode"]
            if interaction_mode != "tui":
                health = "not_required"
                error_code = None
            elif participant["observed_state"] != "ready":
                health = "not_running"
                error_code = None
            else:
                health = "degraded"
                error_code = "availability.driver-unavailable"
            observations.append(
                {
                    "participant_id": participant["participant_id"],
                    "participant_generation": participant[
                        "participant_generation"
                    ],
                    "interaction_mode": interaction_mode,
                    "health": health,
                    "focused": False,
                    "restore_outcome": "not_requested",
                    "geometry": None,
                    "display_topology_fingerprint": None,
                    "error_code": error_code,
                }
            )
        topology = {
            "schema_version": 1,
            "action": "inspect",
            "participants": observations,
        }
        topology["summary_digest"] = canonical_json_sha256(topology)
        return {"topology": topology}

    @staticmethod
    def _non_driver_close_report(execution: dict[str, Any]) -> dict[str, Any]:
        base = {
            "participant_id": execution["participant_id"],
            "participant_generation": execution["participant_generation"],
            "participant_state_revision": execution["participant_state_revision"],
            "desired_state_before_close": execution["desired_state"],
            "continuity_mode": execution["continuity_mode"],
        }
        inactive = execution["kind"] == "inactive"
        settled = execution["kind"] == "settled"
        return {
            **base,
            "classification": (
                "idle"
                if inactive
                else "settled_cleanup_pending"
                if settled
                else "unknown"
            ),
            "closed": inactive or settled,
            "action_outcome_known": True,
            "drain_requested": False,
            "progress_event_count": 0,
            "runtime_binding_id": None,
            "presentation_binding_id": None,
            "owned_resource_evidence_sha256": canonical_json_sha256(
                {
                    **base,
                    "inactive": inactive,
                    "settled": settled,
                    "observation_available": inactive,
                }
            ),
            "owner": execution["participant_id"],
            "command": (
                "inactive"
                if inactive
                else "settled-cleanup-pending"
                if settled
                else "unknown"
            ),
            "started_at_unix_ms": None,
        }

    @classmethod
    def _driver_unavailable_close_report(
        cls, execution: dict[str, Any]
    ) -> dict[str, Any]:
        report = cls._non_driver_close_report({**execution, "kind": "unknown"})
        payload = execution["driver_payload"]
        report["runtime_binding_id"] = payload["runtime_ready_ack"]["binding"][
            "runtime_binding_id"
        ]
        presentation = payload["presentation_create_ack"]
        report["presentation_binding_id"] = (
            None
            if presentation is None
            else presentation["binding"]["presentation_instance_id"]
        )
        report["command"] = payload["launch_spec"]["runtime_profile_ref"]
        return report

    @staticmethod
    def store_error(error: StoreError) -> ProtocolError:
        if error.code == "scenario.not-found":
            return ProtocolError("target.scenario-not-found", "identity", error.message)
        if error.code == "scenario.stale-fence":
            return ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                error.message,
                retryable=True,
                repair_action="scenario.refresh",
            )
        if error.code == "participant.not-found":
            return ProtocolError(
                "target.participant-not-found", "identity", error.message
            )
        if error.code == "participant.stale-fence":
            return ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                error.message,
                retryable=True,
                repair_action="scenario.refresh",
            )
        if error.code == "resource.not-found":
            return ProtocolError("target.resource-not-found", "identity", error.message)
        if error.code == "resource.stale-fence":
            return ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                error.message,
                retryable=True,
                repair_action="scenario.refresh",
            )
        if error.code in {
            "ipc.request-reused",
            "scenario.already-exists",
            "scenario.invalid-transition",
            "scenario.operation-in-progress",
            "participant.already-exists",
            "participant.invalid-transition",
            "participant.binding-drift",
            "resource.invalid-transition",
            "resource.release-invalid",
            "resource.release-pending",
        }:
            return ProtocolError(
                "operation.precondition-failed",
                "operation",
                error.message,
                repair_action=(
                    "participant.recover"
                    if error.code == "participant.binding-drift"
                    else "scenario.refresh"
                ),
            )
        return ProtocolError(
            "availability.host-degraded",
            "availability",
            "Harness Host durable state is unavailable",
            retryable=True,
            repair_action="host.retry",
        )

    @staticmethod
    def project_error(error: ProjectError) -> ProtocolError:
        if error.code == "project.not-found":
            return ProtocolError(
                "target.project-not-found",
                "identity",
                error.message,
                repair_action="project.register",
            )
        if error.code == "ipc.request-reused":
            return ProtocolError("operation.precondition-failed", "operation", error.message)
        if error.code in {"project.binding-drift", "project.path-invalid"}:
            return ProtocolError(
                "operation.precondition-failed",
                "operation",
                error.message,
                repair_action="project.register",
            )
        if error.code in {"project.adapter-unavailable", "adapter.unavailable"}:
            return ProtocolError(
                "availability.adapter-unavailable",
                "availability",
                error.message,
                error.retryable,
                "project.register",
            )
        if error.code == "adapter.crashed":
            return ProtocolError(
                "operation.adapter-crashed",
                "operation",
                error.message,
                error.retryable,
                "project.register",
            )
        if error.code in {
            "project.descriptor-invalid",
            "project.manifest-invalid",
            "project.intent-invalid",
            "project.partial-configuration",
        }:
            return ProtocolError(
                error.code,
                "operation",
                error.message,
                error.retryable,
                "project.fix-configuration",
            )
        if error.code == "project.intent-too-new":
            return ProtocolError(
                error.code,
                "operation",
                error.message,
                error.retryable,
                "host.update",
            )
        if error.code == "project.intent-proposal-incomplete":
            return ProtocolError(
                error.code,
                "operation",
                error.message,
                error.retryable,
                "project.resolve-remote",
            )
        if error.code in {
            "project.reconciliation-unavailable",
            "project.reconciliation-required",
            "project.reconciliation-stale",
        }:
            return ProtocolError(
                error.code,
                "operation",
                error.message,
                error.retryable,
                "project.reconcile",
            )
        if error.code.startswith("adapter."):
            return ProtocolError(
                error.code,
                "operation",
                error.message,
                error.retryable,
                "project.register",
            )
        return ProtocolError(
            "availability.host-degraded",
            "availability",
            error.message,
            error.retryable,
            "host.retry",
        )

    @staticmethod
    def participant_error(error: ParticipantError) -> ProtocolError:
        if error.code in {
            "driver.unavailable",
            "driver.execution-failed",
            "driver.capability-unavailable",
        }:
            return ProtocolError(
                "availability.driver-unavailable",
                "availability",
                error.message,
                error.retryable,
                "scenario.preflight",
            )
        if error.operation_id is not None:
            return ProtocolError(
                "operation.external-failure",
                "operation",
                error.message,
                error.retryable,
                "participant.recover",
            )
        if error.code == "participant.binding-drift":
            return ProtocolError(
                "operation.precondition-failed",
                "operation",
                error.message,
                error.retryable,
                "participant.recover",
            )
        return ProtocolError(
            "operation.precondition-failed",
            "operation",
            error.message,
            False,
            "scenario.refresh",
        )

    @staticmethod
    def delivery_error(error: DeliveryError) -> ProtocolError:
        if error.code in {
            "delivery.stale-fence",
            "delivery.stale-sender",
            "delivery.collection-stale",
            "policy.plan-stale",
            "policy.generation-drift",
        }:
            return ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                error.message,
                True,
                "scenario.refresh",
            )
        if error.code in {"delivery.not-found", "policy.not-found"}:
            return ProtocolError("target.delivery-not-found", "identity", error.message)
        if error.code == "policy.denied":
            return ProtocolError("auth.capability-denied", "authorization", error.message)
        return ProtocolError(
            "operation.precondition-failed", "operation", error.message, error.retryable
        )

    @staticmethod
    def workspace_error(error: WorkspaceError) -> ProtocolError:
        if "stale-fence" in error.code:
            return ProtocolError(
                "fence.stale-operation-generation",
                "fencing",
                error.message,
                True,
                "scenario.refresh",
            )
        if error.code.startswith("adapter."):
            return ProtocolError(
                "availability.adapter-unavailable",
                "availability",
                error.message,
                True,
                "workspace.prepare",
            )
        workspace_repairs = {
            "workspace.git-auth-required": "git.authenticate",
            "workspace.network-unavailable": "workspace.prepare",
            "workspace.branch-unavailable": "project.resolve-branch",
            "workspace.remote-unavailable": "project.resolve-remote",
            "workspace.remote-download-failed": "project.resolve-remote",
            "workspace.disk-full": "disk.free-space",
            "workspace.source-origin-mismatch": "project.resolve-origin",
            "workspace.shallow-source": "git.fetch-full-history",
            "workspace.partial-source-invalid": "git.materialize-full-clone",
            "workspace.partial-source": "git.materialize-full-clone",
            "workspace.alternate-object-source": "git.materialize-full-clone",
        }
        if error.code in workspace_repairs:
            return ProtocolError(
                error.code,
                "availability",
                error.message,
                error.retryable,
                workspace_repairs[error.code],
            )
        if error.code in {
            "workspace.concurrent-change",
            "workspace.operation-in-progress",
            "workspace.path-conflict",
        }:
            return ProtocolError(
                "availability.resource-busy",
                "availability",
                error.message,
                True,
                "scenario.refresh",
            )
        if error.operation_id is not None:
            if error.code == "workspace.publish-outcome-unknown":
                return ProtocolError(
                    "operation.internal-failure",
                    "operation",
                    error.message,
                    False,
                    "scenario.repair",
                )
            return ProtocolError(
                "operation.external-failure",
                "operation",
                error.message,
                error.retryable,
                "scenario.repair",
            )
        return ProtocolError(
            "operation.precondition-failed",
            "operation",
            error.message,
            False,
            "workspace.prepare",
        )

    @staticmethod
    def security_error(error: SecurityError) -> ProtocolError:
        if error.code.startswith("auth."):
            return ProtocolError(
                error.code,
                "authorization",
                error.message,
                error.retryable,
                "scenario.preflight",
            )
        if error.code in {"security.adapter-unavailable", "security.adapter-failed"}:
            return ProtocolError(
                "auth.permission-denied",
                "authorization",
                error.message,
                True,
                "scenario.preflight",
            )
        return ProtocolError(
            "availability.host-degraded",
            "availability",
            "Harness security state is unavailable",
            True,
            "host.retry",
        )
