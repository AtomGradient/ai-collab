# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Thin typed client for the local Harness Host."""

from __future__ import annotations

import json
import os
import socket
import stat
import uuid
from pathlib import Path
from typing import Any, Callable

from .protocol import (
    CONTRACT_VERSION,
    MAX_MESSAGE_BYTES,
    OPERATION_BY_ID,
    OPERATION_REGISTRY_DIGEST,
    capability_proof,
    cancel_capability_proof,
    canonical_json_bytes,
)


class HarnessClientError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str = "client",
        retryable: bool = False,
        mutation_state: str = "not_started",
        repair_action: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.category = category
        self.retryable = retryable
        self.mutation_state = mutation_state
        self.repair_action = repair_action


class HarnessClient:
    def __init__(
        self,
        state_root: Path,
        socket_path: Path | None = None,
        *,
        timeout_seconds: float = 3.0,
    ):
        self.state_root = Path(state_root).expanduser().resolve()
        self.socket_path = (
            Path(socket_path).expanduser().resolve()
            if socket_path is not None
            else self.state_root / "host.sock"
        )
        self.timeout_seconds = timeout_seconds
        self.client_instance_id = f"cli-{uuid.uuid4().hex}"

    def host_status(self) -> dict[str, Any]:
        return self._call("host.status", {"scope": "host"}, {}, {})

    def register_project(
        self,
        *,
        canonical_project_path: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "project.register",
            {"scope": "host"},
            {"operation_generation": 0},
            {"canonical_project_path": canonical_project_path},
            request_id=request_id,
        )

    def list_projects(self) -> dict[str, Any]:
        return self._call("project.list", {"scope": "host"}, {}, {})

    def bootstrap_project(
        self,
        *,
        canonical_project_path: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "project.bootstrap",
            {"scope": "host"},
            {"operation_generation": 0},
            {"canonical_project_path": canonical_project_path},
            request_id=request_id,
        )

    def unregister_project(
        self,
        *,
        project_instance_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "project.unregister",
            {"scope": "host"},
            {"operation_generation": 0},
            {"project_instance_id": project_instance_id},
            request_id=request_id,
        )

    def create_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        project_binding_digest: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "scenario.create",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {"operation_generation": 0},
            {"project_binding_digest": project_binding_digest},
            request_id=request_id,
        )

    def open_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "scenario.open",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
            },
            request_id=request_id,
        )

    def close_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        drain_timeout_ms: int,
        request_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "scenario.close",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "drain_timeout_ms": drain_timeout_ms,
            },
            request_id=request_id,
            progress_callback=progress_callback,
        )

    def start_scenario_participants(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "scenario.start-participants",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
            },
            request_id=request_id,
            progress_callback=progress_callback,
        )

    def cancel_operation(self, operation_id: str) -> dict[str, Any]:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout_seconds)
        try:
            connection.connect(str(self.socket_path))
            stream = connection.makefile("rwb")
            handshake_id = f"hs-{uuid.uuid4().hex}"
            self._write(
                stream,
                {
                    "message_type": "handshake_request",
                    "request_id": handshake_id,
                    "client_instance_id": self.client_instance_id,
                    "supported_contract_versions": [CONTRACT_VERSION],
                    "client_capabilities": [],
                },
            )
            handshake = self._read(stream)
            self._check_handshake(handshake, handshake_id)
            request_id = f"cancel-{uuid.uuid4().hex}"
            self._write(
                stream,
                {
                    "message_type": "cancel_request",
                    "contract_version": CONTRACT_VERSION,
                    "request_id": request_id,
                    "operation_id": operation_id,
                    "host_generation": handshake["host_generation"],
                    "capability_proof": cancel_capability_proof(
                        self._read_capability(),
                        operation_id=operation_id,
                        host_generation=handshake["host_generation"],
                    ),
                },
            )
            reply = self._read(stream)
            if reply.get("outcome") != "accepted":
                self._raise_reply_error(reply)
            if (
                reply.get("message_type") != "cancel_reply"
                or reply.get("contract_version") != CONTRACT_VERSION
                or reply.get("request_id") != request_id
                or reply.get("operation_id") != operation_id
                or reply.get("host_generation") != handshake["host_generation"]
                or reply.get("mutation_state")
                not in {"not_started", "committed", "unknown"}
            ):
                raise HarnessClientError(
                    "ipc.invalid-message", "Host cancel reply differs"
                )
            return {
                "operation_id": operation_id,
                "outcome": "accepted",
                "mutation_state": reply["mutation_state"],
            }
        except socket.timeout as exc:
            raise HarnessClientError(
                "operation.timeout",
                "Harness cancel request timed out",
                category="availability",
                retryable=True,
                mutation_state="unknown",
                repair_action="scenario.refresh",
            ) from exc
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise HarnessClientError(
                "host.unavailable",
                "Harness Host is unavailable",
                category="availability",
                retryable=True,
                mutation_state="not_started",
                repair_action="host.retry",
            ) from exc
        finally:
            connection.close()

    def scenario_diagnostic(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "scenario.diagnostic",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def scenario_preflight(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "scenario.preflight",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def scenario_topology(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "scenario.topology",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def focus_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._scenario_fenced_operation(
            "scenario.focus",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            request_id=request_id,
        )

    def repair_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._scenario_fenced_operation(
            "scenario.repair",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            request_id=request_id,
        )

    def preview_destroy_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
    ) -> dict[str, Any]:
        return self._scenario_fenced_operation(
            "scenario.destroy.preview",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
        )

    def destroy_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._scenario_fenced_operation(
            "scenario.destroy",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            request_id=request_id,
        )

    def force_destroy_scenario(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._scenario_fenced_operation(
            "scenario.force-destroy",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            request_id=request_id,
        )

    def scenario_status(self, *, project_instance_id: str, scenario_id: str) -> dict[str, Any]:
        return self._call(
            "scenario.status",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {},
            {},
        )

    def list_resources(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "resource.list",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def break_resource(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        lease_id: str,
        lease_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "resource.break",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "lease_id": lease_id,
                "lease_revision": lease_revision,
            },
            request_id=request_id,
        )

    def list_scenarios(self, *, project_instance_id: str) -> dict[str, Any]:
        return self._call(
            "scenario.list",
            {"scope": "project", "project_instance_id": project_instance_id},
            {},
            {},
        )

    def plan_workspace(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        requested_component_ids: list[str],
        project_payload: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "workspace.plan",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "requested_component_ids": requested_component_ids,
                "project_payload": project_payload,
            },
            request_id=request_id,
        )

    def provision_workspace(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        plan_digest: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "workspace.provision",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "plan_digest": plan_digest,
            },
            request_id=request_id,
        )

    def workspace_status(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        receipt_digest: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "workspace.status",
            {
                "scope": "scenario",
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
            },
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "receipt_digest": receipt_digest,
            },
            request_id=request_id,
        )

    def add_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        launch_spec: dict[str, Any],
        presentation_driver_id: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "participant.add",
            self._participant_target(
                project_instance_id, scenario_id, participant_id
            ),
            {"operation_generation": 0, "participant_generation": 0},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "launch_spec": launch_spec,
                "presentation_driver_id": presentation_driver_id,
            },
            request_id=request_id,
        )

    def list_participants(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "participant.list",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def list_participant_templates(self) -> dict[str, Any]:
        return self._call(
            "participant.template.list", {"scope": "host"}, {}, {}
        )

    def start_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._participant_operation(
            "participant.start",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
            request_id=request_id,
        )

    def participant_status(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> dict[str, Any]:
        return self._participant_operation(
            "participant.status",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
        )

    def stop_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._participant_operation(
            "participant.stop",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
            request_id=request_id,
        )

    def force_stop_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._participant_operation(
            "participant.force-stop",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
            request_id=request_id,
        )

    def recover_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._participant_operation(
            "participant.recover",
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
            request_id=request_id,
        )

    def replace_participant(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        launch_spec: dict[str, Any],
        presentation_driver_id: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "participant.replace",
            self._participant_target(
                project_instance_id, scenario_id, participant_id
            ),
            {
                "operation_generation": participant_state_revision,
                "participant_generation": participant_generation,
            },
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "participant_state_revision": participant_state_revision,
                "launch_spec": launch_spec,
                "presentation_driver_id": presentation_driver_id,
            },
            request_id=request_id,
        )

    def apply_policy(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        policy_pack: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "policy.apply",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "policy_pack": policy_pack,
            },
            request_id=request_id,
        )

    def list_policy_templates(self, *, project_instance_id: str) -> dict[str, Any]:
        return self._call(
            "policy.template.list",
            {"scope": "project", "project_instance_id": project_instance_id},
            {},
            {},
        )

    def plan_policy(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        template_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "policy.plan",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "template_id": template_id,
            },
        )

    def apply_policy_plan(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        template_id: str,
        plan_digest: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "policy.apply-plan",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": scenario_state_revision},
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "template_id": template_id,
                "plan_digest": plan_digest,
            },
            request_id=request_id,
        )

    def show_policy(
        self, *, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return self._call(
            "policy.show",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {},
        )

    def send_message(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        sender_participant_id: str,
        sender_participant_generation: int,
        sender_participant_state_revision: int,
        receiver_intent: dict[str, Any],
        message_id: str,
        message_kind: str,
        message: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "message.send",
            self._scenario_target(project_instance_id, scenario_id),
            {
                "operation_generation": sender_participant_state_revision,
                "participant_generation": sender_participant_generation,
            },
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "sender_participant_id": sender_participant_id,
                "sender_participant_generation": sender_participant_generation,
                "sender_participant_state_revision": sender_participant_state_revision,
                "receiver_intent": receiver_intent,
                "message_id": message_id,
                "message_kind": message_kind,
                "message": message,
            },
            request_id=request_id,
        )

    def list_deliveries(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        limit: int = 100,
        after_delivery_id: str | None = None,
        collection_digest: str | None = None,
        thread_root_delivery_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": limit}
        if after_delivery_id is not None:
            payload["after_delivery_id"] = after_delivery_id
        if collection_digest is not None:
            payload["collection_digest"] = collection_digest
        if thread_root_delivery_id is not None:
            payload["thread_root_delivery_id"] = thread_root_delivery_id
        return self._call(
            "delivery.list",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            payload,
        )

    def delivery_status(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
    ) -> dict[str, Any]:
        return self._call(
            "delivery.status",
            self._scenario_target(project_instance_id, scenario_id),
            {},
            {"delivery_id": delivery_id},
        )

    def consume_delivery(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
        event_sequence: int,
        consumption_ack: dict[str, Any],
    ) -> dict[str, Any]:
        return self._call(
            "delivery.consume",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": event_sequence},
            {
                "delivery_id": delivery_id,
                "event_sequence": event_sequence,
                "consumption_ack": consumption_ack,
            },
        )

    def retry_delivery(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
        event_sequence: int,
    ) -> dict[str, Any]:
        return self._call(
            "delivery.retry",
            self._scenario_target(project_instance_id, scenario_id),
            {"operation_generation": event_sequence},
            {"delivery_id": delivery_id, "event_sequence": event_sequence},
        )

    def _participant_operation(
        self,
        operation: str,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        fence = {"participant_generation": participant_generation}
        if operation != "participant.status":
            fence["operation_generation"] = participant_state_revision
        return self._call(
            operation,
            self._participant_target(
                project_instance_id, scenario_id, participant_id
            ),
            fence,
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "participant_state_revision": participant_state_revision,
            },
            request_id=request_id,
        )

    def _scenario_fenced_operation(
        self,
        operation: str,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            operation,
            self._scenario_target(project_instance_id, scenario_id),
            (
                {}
                if operation == "scenario.destroy.preview"
                else {"operation_generation": scenario_state_revision}
            ),
            {
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
            },
            request_id=request_id,
        )

    @staticmethod
    def _participant_target(
        project_instance_id: str, scenario_id: str, participant_id: str
    ) -> dict[str, Any]:
        return {
            "scope": "participant",
            "project_instance_id": project_instance_id,
            "scenario_id": scenario_id,
            "participant_id": participant_id,
        }

    @staticmethod
    def _scenario_target(
        project_instance_id: str, scenario_id: str
    ) -> dict[str, Any]:
        return {
            "scope": "scenario",
            "project_instance_id": project_instance_id,
            "scenario_id": scenario_id,
        }

    def _call(
        self,
        operation: str,
        target: dict[str, Any],
        extra_fence: dict[str, int],
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        capability_override: str | None = None,
        capability_secret_override: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if capability_override is not None and capability_secret_override is not None:
            raise HarnessClientError(
                "auth.capability-denied", "multiple capability sources were provided"
            )
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout_seconds)
        operation_sent = False
        try:
            connection.connect(str(self.socket_path))
            stream = connection.makefile("rwb")
            handshake_id = f"hs-{uuid.uuid4().hex}"
            self._write(
                stream,
                {
                    "message_type": "handshake_request",
                    "request_id": handshake_id,
                    "client_instance_id": self.client_instance_id,
                    "supported_contract_versions": [CONTRACT_VERSION],
                    "client_capabilities": [],
                },
            )
            handshake = self._read(stream)
            self._check_handshake(handshake, handshake_id)
            operation_request_id = request_id or f"req-{uuid.uuid4().hex}"
            if capability_override is not None:
                proof = capability_override
            else:
                proof = capability_proof(
                    (
                        capability_secret_override
                        if capability_secret_override is not None
                        else self._read_capability()
                    ),
                    operation=operation,
                    required_capability=OPERATION_BY_ID[operation]["required_capability"],
                    target=target,
                    host_generation=handshake["host_generation"],
                )
            self._write(
                stream,
                {
                    "message_type": "operation_request",
                    "contract_version": CONTRACT_VERSION,
                    "request_id": operation_request_id,
                    "operation": operation,
                    "operation_schema_version": 1,
                    "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
                    "capability_proof": proof,
                    "target": target,
                    "fence": {
                        "host_generation": handshake["host_generation"],
                        **extra_fence,
                    },
                    "payload": payload,
                },
            )
            operation_sent = True
            reply = self._read(stream)
            expected_progress_sequence = 0
            progress_operation_id: str | None = None
            while reply.get("message_type") == "progress_event":
                operation_id = reply.get("operation_id")
                if (
                    reply.get("contract_version") != CONTRACT_VERSION
                    or not isinstance(operation_id, str)
                    or not operation_id
                    or reply.get("sequence") != expected_progress_sequence
                    or reply.get("state")
                    not in {
                        "queued",
                        "running",
                        "waiting",
                        "cancelling",
                        "completed",
                        "failed",
                        "cancelled",
                    }
                    or reply.get("host_generation") != handshake["host_generation"]
                    or not isinstance(reply.get("progress"), dict)
                    or (
                        progress_operation_id is not None
                        and operation_id != progress_operation_id
                    )
                ):
                    raise HarnessClientError(
                        "ipc.invalid-message", "Host progress event differs"
                    )
                progress_operation_id = operation_id
                expected_progress_sequence += 1
                if progress_callback is not None:
                    progress_callback(reply)
                reply = self._read(stream)
            return self._check_operation_reply(reply, operation_request_id)
        except socket.timeout as exc:
            raise HarnessClientError(
                "operation.timeout",
                "Harness operation timed out",
                category="availability",
                retryable=True,
                mutation_state="unknown" if operation_sent else "not_started",
                repair_action="scenario.refresh" if operation_sent else "host.retry",
            ) from exc
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise HarnessClientError(
                "host.unavailable",
                "Harness Host is unavailable",
                category="availability",
                retryable=True,
                mutation_state="unknown" if operation_sent else "not_started",
                repair_action="scenario.refresh" if operation_sent else "host.retry",
            ) from exc
        finally:
            connection.close()

    def _read_capability(self) -> str:
        path = self.state_root / "owner-capability"
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
            or path.stat().st_uid != self.state_root.stat().st_uid
        ):
            raise HarnessClientError("host.capability-unavailable", "Harness capability is unavailable")
        value = path.read_text(encoding="utf-8").strip()
        if len(value) != 64 or not set(value).issubset(set("0123456789abcdef")):
            raise HarnessClientError("host.capability-unavailable", "Harness capability is invalid")
        return value

    @staticmethod
    def _write(stream: Any, value: dict[str, Any]) -> None:
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()

    @staticmethod
    def _read(stream: Any) -> dict[str, Any]:
        raw = stream.readline(MAX_MESSAGE_BYTES + 1)
        if not raw or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            raise HarnessClientError("ipc.invalid-message", "Host reply frame is invalid")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HarnessClientError("ipc.invalid-message", "Host reply is invalid JSON") from exc
        if not isinstance(value, dict):
            raise HarnessClientError("ipc.invalid-message", "Host reply is not an object")
        return value

    @staticmethod
    def _raise_reply_error(value: dict[str, Any]) -> None:
        error = value.get("error")
        if not isinstance(error, dict):
            raise HarnessClientError("ipc.invalid-message", "Host error reply is invalid")
        raise HarnessClientError(
            str(error.get("code", "operation.failed")),
            str(error.get("redacted_message", "Harness operation failed")),
            category=str(error.get("category", "operation")),
            retryable=error.get("retryable") is True,
            mutation_state=str(value.get("mutation_state", "not_started")),
            repair_action=(
                error.get("repair_action")
                if isinstance(error.get("repair_action"), str)
                else None
            ),
        )

    def _check_handshake(self, value: dict[str, Any], request_id: str) -> None:
        if value.get("outcome") != "accepted":
            self._raise_reply_error(value)
        if (
            value.get("message_type") != "handshake_reply"
            or value.get("request_id") != request_id
            or value.get("contract_version") != CONTRACT_VERSION
            or value.get("operation_registry_digest") != OPERATION_REGISTRY_DIGEST
            or not isinstance(value.get("host_generation"), int)
        ):
            raise HarnessClientError("ipc.invalid-message", "Host handshake reply differs")

    def _check_operation_reply(self, value: dict[str, Any], request_id: str) -> dict[str, Any]:
        if value.get("outcome") != "completed":
            self._raise_reply_error(value)
        if (
            value.get("message_type") != "operation_reply"
            or value.get("contract_version") != CONTRACT_VERSION
            or value.get("request_id") != request_id
            or not isinstance(value.get("result"), dict)
        ):
            raise HarnessClientError("ipc.invalid-message", "Host operation reply differs")
        return value["result"]


class ParticipantHarnessClient:
    """Scoped client that can only submit an exact Participant self intent."""

    CONTEXT_FIELDS = {
        "schema_version",
        "contract_version",
        "project_instance_id",
        "scenario_id",
        "participant_id",
        "participant_generation",
        "participant_state_revision",
        "host_socket_path",
        "participant_capability",
    }

    def __init__(self, context_path: Path, *, timeout_seconds: float = 30.0):
        self.context_path = Path(context_path).resolve(strict=True)
        self.context = self._read_context(self.context_path)
        self.client = HarnessClient(
            self.context_path.parent.parent,
            Path(self.context["host_socket_path"]),
            timeout_seconds=timeout_seconds,
        )

    def send(
        self,
        *,
        receiver_participant_id: str,
        message_id: str,
        message_kind: str,
        message: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "message.send-self",
            {
                "receiver_participant_id": receiver_participant_id,
                "message_id": message_id,
                "message_kind": message_kind,
                "message": message,
            },
            request_id=request_id,
        )

    def reply(
        self,
        *,
        reply_to_delivery_id: str,
        receiver_participant_id: str,
        message_id: str,
        message_kind: str,
        message: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "message.reply-self",
            {
                "reply_to_delivery_id": reply_to_delivery_id,
                "receiver_participant_id": receiver_participant_id,
                "message_id": message_id,
                "message_kind": message_kind,
                "message": message,
            },
            request_id=request_id,
        )

    def _call(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        target = {
            "scope": "participant",
            "project_instance_id": self.context["project_instance_id"],
            "scenario_id": self.context["scenario_id"],
            "participant_id": self.context["participant_id"],
        }
        return self.client._call(
            operation,
            target,
            {
                "operation_generation": self.context[
                    "participant_state_revision"
                ],
                "participant_generation": self.context[
                    "participant_generation"
                ],
            },
            payload,
            request_id=request_id,
            capability_secret_override=self.context["participant_capability"],
        )

    @classmethod
    def _read_context(cls, path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise HarnessClientError(
                "auth.capability-denied", "participant context is unavailable"
            )
        details = path.stat()
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
            raise HarnessClientError(
                "auth.capability-denied", "participant context permissions differ"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessClientError(
                "auth.capability-denied", "participant context is invalid"
            ) from exc
        if (
            not isinstance(value, dict)
            or set(value) != cls.CONTEXT_FIELDS
            or value["schema_version"] != 1
            or value["contract_version"] != CONTRACT_VERSION
            or any(
                not isinstance(value[field], str) or not value[field]
                for field in (
                    "project_instance_id",
                    "scenario_id",
                    "participant_id",
                    "host_socket_path",
                )
            )
            or not isinstance(value["participant_generation"], int)
            or isinstance(value["participant_generation"], bool)
            or value["participant_generation"] < 1
            or not isinstance(value["participant_state_revision"], int)
            or isinstance(value["participant_state_revision"], bool)
            or value["participant_state_revision"] < 1
            or not isinstance(value["participant_capability"], str)
            or len(value["participant_capability"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in value["participant_capability"]
            )
        ):
            raise HarnessClientError(
                "auth.capability-denied", "participant context binding differs"
            )
        return value
