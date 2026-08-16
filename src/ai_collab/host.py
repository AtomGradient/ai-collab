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
from typing import Any, Callable

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
        self.host_generation = 0
        self.host_instance_fingerprint = "0" * 64
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
            )
            if project_adapter is not None
            else None
        )
        self.participants = (
            ParticipantCoordinator(
                self.store, ParticipantDriverCommand(participant_driver_config)
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
            self.store.reconcile_recorded_outcomes()
            started = self.store.start_host()
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
        )

    def _reconcile_scenario_resumes(self) -> None:
        for pending in self.store.pending_scenario_resume_requests():
            project_binding_digest = pending.pop("project_binding_digest")
            self.projects.validate_binding(
                pending["project_instance_id"], project_binding_digest
            )
            if self.workspace is not None and not self.workspace.is_ready(
                pending["project_instance_id"], pending["scenario_id"]
            ):
                raise StoreError(
                    "scenario.restore-plan-invalid",
                    "Scenario workspace and environment are not ready",
                )
            self._resume_scenario_participants(**pending)

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
            previous = self.store.replay_request(request["request_id"], request_digest)
            if previous is not None:
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
        elif operation == "scenario.list":
            result = self.store.list_scenarios(target["project_instance_id"])
            operation_id = f"read-{request['request_id']}"
        elif operation == "scenario.status":
            result = self.store.scenario_status(
                target["project_instance_id"], target["scenario_id"]
            )
            operation_id = f"read-{request['request_id']}"
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
            self.projects.validate_binding(
                target["project_instance_id"],
                request["payload"]["project_binding_digest"],
            )
            operation_id, result = self.store.create_scenario(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=target["project_instance_id"],
                scenario_id=target["scenario_id"],
                project_binding_digest=request["payload"]["project_binding_digest"],
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
                result = self._resume_scenario_participants(
                    project_instance_id=target["project_instance_id"],
                    scenario_id=target["scenario_id"],
                    request_id=request["request_id"],
                    request_digest=request_digest,
                )
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
            operation_id: str | None = None
            try:
                if operation == "scenario.repair":
                    operation_id, replay, workspace_path = (
                        self.store.begin_scenario_repair(**common)
                    )
                    if replay is not None:
                        result = replay
                    else:
                        assert workspace_path is not None
                        _, workspace_result = self.workspace.repair(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            scenario_generation=payload["scenario_generation"],
                            workspace_path=workspace_path,
                            expected_wip_summary_digest=security_effect_preview[
                                "workspace"
                            ]["wip_summary_digest"],
                        )
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
                        self.store.begin_scenario_destroy(**common)
                    )
                    if replay is not None:
                        result = replay
                    else:
                        assert workspace_path is not None
                        _, workspace_result = self.workspace.destroy(
                            request_id=request["request_id"],
                            request_digest=request_digest,
                            project_instance_id=target["project_instance_id"],
                            scenario_id=target["scenario_id"],
                            scenario_generation=payload["scenario_generation"],
                            workspace_path=workspace_path,
                            expected_wip_summary_digest=security_effect_preview[
                                "workspace"
                            ]["wip_summary_digest"],
                        )
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
                if operation_id is not None:
                    self.store.fail_scenario_repair_or_destroy(
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
                self._mark_security_failure(security_consumption)
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
                if request["fence"]["operation_generation"] != payload[
                    "scenario_state_revision"
                ]:
                    raise ProtocolError(
                        "fence.stale-operation-generation",
                        "fencing",
                        "policy plan generation differs from Scenario revision",
                        retryable=True,
                    )
                templates = self.projects.collaboration_templates(
                    target["project_instance_id"]
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
                operation_id, result = self.delivery.send_message(
                    request_id=request["request_id"],
                    request_digest=request_digest,
                    scenario_generation=payload["scenario_generation"],
                    scenario_state_revision=payload["scenario_state_revision"],
                    sender_participant_id=payload["sender_participant_id"],
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
                elif operation == "participant.detach":
                    operation_id, result = self.participants.detach(
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
                target["project_instance_id"], target["scenario_id"]
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
                operation_id, result = self.workspace.provision(
                    **common,
                    plan_digest=payload["plan_digest"],
                    workspace_path=workspace_path,
                )
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
            current = self.store.scenario_status(project_instance_id, scenario_id)[
                "scenario"
            ]

        operation_id: str | None = None
        try:
            operation_id, replay, workspace_path = self.store.begin_scenario_destroy(
                request_id=request["request_id"],
                request_digest=request_digest,
                host_generation=self.host_generation,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=current["scenario_generation"],
                scenario_state_revision=current["state_revision"],
                operation_kind="scenario.force-destroy",
            )
            if replay is not None:
                return operation_id, replay
            assert workspace_path is not None
            _, workspace_result = self.workspace.destroy(
                request_id=request["request_id"],
                request_digest=request_digest,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=payload["scenario_generation"],
                workspace_path=workspace_path,
                expected_wip_summary_digest=effect_preview["workspace"][
                    "wip_summary_digest"
                ],
            )
            result = self.store.finalize_scenario_destroy(
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                request_id=request["request_id"],
                operation_id=operation_id,
                workspace_evidence_sha256=canonical_json_sha256(workspace_result),
            )
            self.participant_auth.revoke_scenario(project_instance_id, scenario_id)
            return operation_id, result
        except BaseException:
            if operation_id is not None:
                self.store.fail_scenario_repair_or_destroy(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    request_id=request["request_id"],
                    operation_id=operation_id,
                    reason="lifecycle.force-destroy-failed",
                )
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
            completed = self.workspace.completed_request(
                pending["request_id"], pending["request_digest"]
            )
            if completed is None:
                continue
            _, workspace_result = completed
            evidence = canonical_json_sha256(workspace_result)
            if pending["operation_kind"] == "scenario.repair":
                self.store.finalize_scenario_repair(
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    request_id=pending["request_id"],
                    operation_id=pending["operation_id"],
                    workspace_evidence_sha256=evidence,
                )
            else:
                self.store.finalize_scenario_destroy(
                    project_instance_id=pending["project_instance_id"],
                    scenario_id=pending["scenario_id"],
                    request_id=pending["request_id"],
                    operation_id=pending["operation_id"],
                    workspace_evidence_sha256=evidence,
                )

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
                target["project_instance_id"], target["scenario_id"]
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
        eligible = store_preview["eligible"] and workspace_preview["state"] == "aligned"
        effect_preview = {
            **store_preview,
            "eligible": eligible,
            "blockers": sorted(
                set(store_preview["blockers"])
                | (
                    set()
                    if workspace_preview["state"] == "aligned"
                    else {"workspace.not-aligned"}
                )
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
        return {
            **base,
            "classification": "idle" if inactive else "unknown",
            "closed": inactive,
            "action_outcome_known": True,
            "drain_requested": False,
            "progress_event_count": 0,
            "runtime_binding_id": None,
            "presentation_binding_id": None,
            "owned_resource_evidence_sha256": canonical_json_sha256(
                {**base, "inactive": inactive, "observation_available": inactive}
            ),
            "owner": execution["participant_id"],
            "command": "inactive" if inactive else "unknown",
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
        if error.code in {
            "project.adapter-unavailable",
            "adapter.unavailable",
            "adapter.execution-failed",
        }:
            return ProtocolError(
                "availability.adapter-unavailable",
                "availability",
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
