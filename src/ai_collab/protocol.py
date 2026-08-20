# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Minimal executable subset of the frozen Host IPC v1 contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


CONTRACT_VERSION = 1
MAX_MESSAGE_BYTES = 1_048_576
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def capability_proof(
    secret: str,
    *,
    operation: str,
    required_capability: str,
    target: Mapping[str, Any],
    host_generation: int,
) -> str:
    binding = {
        "contract_version": CONTRACT_VERSION,
        "host_generation": host_generation,
        "operation": operation,
        "required_capability": required_capability,
        "target": target,
    }
    return hmac.new(
        bytes.fromhex(secret),
        canonical_json_bytes(binding),
        hashlib.sha256,
    ).hexdigest()


def cancel_capability_proof(
    secret: str, *, operation_id: str, host_generation: int
) -> str:
    return hmac.new(
        bytes.fromhex(secret),
        canonical_json_bytes(
            {
                "contract_version": CONTRACT_VERSION,
                "host_generation": host_generation,
                "operation_id": operation_id,
                "purpose": "cancel",
            }
        ),
        hashlib.sha256,
    ).hexdigest()


def operation_intent_digest(request: Mapping[str, Any]) -> str:
    """Hash durable operation intent without ephemeral auth/Host fencing."""

    fence = dict(request["fence"])
    fence.pop("host_generation", None)
    return canonical_json_sha256(
        {
            "contract_version": request["contract_version"],
            "operation": request["operation"],
            "operation_schema_version": request["operation_schema_version"],
            "operation_registry_digest": request["operation_registry_digest"],
            "target": request["target"],
            "fence": fence,
            "payload": request["payload"],
        }
    )


EMPTY_OBJECT_SCHEMA = {"type": "object", "required": [], "properties": {}}
PROJECT_REGISTER_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["canonical_project_path"],
    "properties": {"canonical_project_path": {"type": "string"}},
}
PROJECT_RESULT_SCHEMA = {
    "type": "object",
    "required": ["project"],
    "properties": {"project": {"type": "object"}},
}
PROJECT_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["projects"],
    "properties": {"projects": {"type": "array"}},
}
PROJECT_RECONCILE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["project", "reconciliation"],
    "properties": {
        "project": {"type": "object"},
        "reconciliation": {"type": "object"},
    },
}
PROJECT_ACCEPT_RECONCILIATION_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["availability_fingerprint"],
    "properties": {"availability_fingerprint": {"type": "sha256"}},
}
PROJECT_UNREGISTER_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["project_instance_id"],
    "properties": {"project_instance_id": {"type": "string"}},
}
PROJECT_BOOTSTRAP_RESULT_SCHEMA = {
    "type": "object",
    "required": ["bootstrap"],
    "properties": {"bootstrap": {"type": "object"}},
}
PROJECT_UNREGISTER_RESULT_SCHEMA = {
    "type": "object",
    "required": ["unregistered"],
    "properties": {"unregistered": {"type": "object"}},
}
CREATE_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["project_binding_digest"],
    "properties": {"project_binding_digest": {"type": "sha256"}},
}
OPEN_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["scenario_generation", "scenario_state_revision"],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
    },
}
CLOSE_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "drain_timeout_ms",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "drain_timeout_ms": {"type": "positive_integer"},
    },
}
SCENARIO_FENCED_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["scenario_generation", "scenario_state_revision"],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
    },
}
SCENARIO_RESULT_SCHEMA = {
    "type": "object",
    "required": ["scenario"],
    "properties": {"scenario": {"type": "scenario_record"}},
}
SCENARIO_OPEN_RESULT_SCHEMA = {
    "type": "object",
    "required": ["scenario", "resume_summary"],
    "properties": {
        "scenario": {"type": "scenario_record"},
        "resume_summary": {"type": "object"},
    },
}
SCENARIO_CLOSE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["scenario", "close_summary"],
    "properties": {
        "scenario": {"type": "scenario_record"},
        "close_summary": {"type": "object"},
    },
}
SCENARIO_START_PARTICIPANTS_RESULT_SCHEMA = {
    "type": "object",
    "required": ["scenario", "start_summary"],
    "properties": {
        "scenario": {"type": "scenario_record"},
        "start_summary": {"type": "object"},
    },
}
SCENARIO_DIAGNOSTIC_RESULT_SCHEMA = {
    "type": "object",
    "required": ["diagnostic"],
    "properties": {"diagnostic": {"type": "object"}},
}
SCENARIO_PREFLIGHT_RESULT_SCHEMA = {
    "type": "object",
    "required": ["preflight"],
    "properties": {"preflight": {"type": "object"}},
}
PRESENTATION_PERMISSION_RESULT_SCHEMA = {
    "type": "object",
    "required": ["permission_observations"],
    "properties": {"permission_observations": {"type": "array"}},
}
ENVIRONMENT_PROBE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["environment_observations"],
    "properties": {"environment_observations": {"type": "array"}},
}
SCENARIO_TOPOLOGY_RESULT_SCHEMA = {
    "type": "object",
    "required": ["topology"],
    "properties": {"topology": {"type": "object"}},
}
RESOURCE_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["resources"],
    "properties": {"resources": {"type": "array"}},
}
RESOURCE_BREAK_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "lease_id",
        "lease_revision",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "lease_id": {"type": "opaque_id"},
        "lease_revision": {"type": "positive_integer"},
    },
}
RESOURCE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["resource"],
    "properties": {"resource": {"type": "object"}},
}
SCENARIO_PREVIEW_RESULT_SCHEMA = {
    "type": "object",
    "required": ["effect_preview"],
    "properties": {"effect_preview": {"type": "object"}},
}
SCENARIO_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["scenarios"],
    "properties": {"scenarios": {"type": "scenario_record_list"}},
}
HOST_STATUS_RESULT_SCHEMA = {
    "type": "object",
    "required": ["status", "host_generation", "scenario_count"],
    "properties": {
        "status": {"const": "ready"},
        "host_generation": {"type": "integer"},
        "scenario_count": {"type": "integer"},
    },
}
WORKSPACE_PLAN_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "requested_component_ids",
        "project_payload",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "requested_component_ids": {"type": "opaque_id_list"},
        "project_payload": {"type": "object"},
    },
}
WORKSPACE_PROVISION_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["scenario_generation", "scenario_state_revision", "plan_digest"],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "plan_digest": {"type": "sha256"},
    },
}
WORKSPACE_STATUS_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["scenario_generation", "scenario_state_revision", "receipt_digest"],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "receipt_digest": {"type": "sha256"},
    },
}
WORKSPACE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["workspace"],
    "properties": {"workspace": {"type": "object"}},
}
PARTICIPANT_ADD_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "launch_spec",
        "presentation_driver_id",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "launch_spec": {"type": "object"},
        "presentation_driver_id": {"type": "nullable_opaque_id"},
    },
}
PARTICIPANT_EXISTING_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "participant_state_revision",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "participant_state_revision": {"type": "positive_integer"},
    },
}
PARTICIPANT_REPLACE_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "participant_state_revision",
        "launch_spec",
        "presentation_driver_id",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "participant_state_revision": {"type": "positive_integer"},
        "launch_spec": {"type": "object"},
        "presentation_driver_id": {"type": "nullable_opaque_id"},
    },
}
PARTICIPANT_RESULT_SCHEMA = {
    "type": "object",
    "required": ["participant"],
    "properties": {"participant": {"type": "participant_record"}},
}
PARTICIPANT_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["participants", "participant_configurations"],
    "properties": {
        "participants": {"type": "array"},
        "participant_configurations": {"type": "array"},
    },
}
PARTICIPANT_TEMPLATE_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["templates"],
    "properties": {"templates": {"type": "array"}},
}
POLICY_APPLY_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["scenario_generation", "scenario_state_revision", "policy_pack"],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "policy_pack": {"type": "object"},
    },
}
POLICY_PLAN_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "template_id",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "template_id": {"type": "opaque_id"},
    },
}
POLICY_APPLY_PLAN_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "template_id",
        "plan_digest",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "template_id": {"type": "opaque_id"},
        "plan_digest": {"type": "sha256"},
    },
}
POLICY_TEMPLATE_LIST_RESULT_SCHEMA = {
    "type": "object",
    "required": ["templates"],
    "properties": {"templates": {"type": "array"}},
}
POLICY_PLAN_RESULT_SCHEMA = {
    "type": "object",
    "required": ["policy_plan"],
    "properties": {"policy_plan": {"type": "object"}},
}
MESSAGE_SEND_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "scenario_generation",
        "scenario_state_revision",
        "sender_participant_id",
        "sender_participant_generation",
        "sender_participant_state_revision",
        "receiver_intent",
        "message_id",
        "message_kind",
        "message",
    ],
    "properties": {
        "scenario_generation": {"type": "positive_integer"},
        "scenario_state_revision": {"type": "positive_integer"},
        "sender_participant_id": {"type": "opaque_id"},
        "sender_participant_generation": {"type": "positive_integer"},
        "sender_participant_state_revision": {"type": "positive_integer"},
        "receiver_intent": {"type": "object"},
        "message_id": {"type": "opaque_id"},
        "message_kind": {"type": "opaque_id"},
        "message": {"type": "string"},
    },
}
MESSAGE_SEND_SELF_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "receiver_participant_id",
        "message_id",
        "message_kind",
        "message",
    ],
    "properties": {
        "receiver_participant_id": {"type": "opaque_id"},
        "message_id": {"type": "opaque_id"},
        "message_kind": {"type": "opaque_id"},
        "message": {"type": "string"},
    },
}
MESSAGE_REPLY_SELF_REQUEST_SCHEMA = {
    "type": "object",
    "required": [
        "reply_to_delivery_id",
        "receiver_participant_id",
        "message_id",
        "message_kind",
        "message",
    ],
    "properties": {
        "reply_to_delivery_id": {"type": "opaque_id"},
        "receiver_participant_id": {"type": "opaque_id"},
        "message_id": {"type": "opaque_id"},
        "message_kind": {"type": "opaque_id"},
        "message": {"type": "string"},
    },
}
DELIVERY_STATUS_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["delivery_id"],
    "properties": {"delivery_id": {"type": "opaque_id"}},
}
DELIVERY_LIST_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["limit"],
    "properties": {
        "limit": {"type": "positive_integer"},
        "after_delivery_id": {"type": "opaque_id"},
        "collection_digest": {"type": "sha256"},
        "thread_root_delivery_id": {"type": "opaque_id"},
    },
}
DELIVERY_MUTATION_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["delivery_id", "event_sequence"],
    "properties": {
        "delivery_id": {"type": "opaque_id"},
        "event_sequence": {"type": "integer"},
    },
}
DELIVERY_CONSUME_REQUEST_SCHEMA = {
    "type": "object",
    "required": ["delivery_id", "event_sequence", "consumption_ack"],
    "properties": {
        "delivery_id": {"type": "opaque_id"},
        "event_sequence": {"type": "integer"},
        "consumption_ack": {"type": "object"},
    },
}
POLICY_RESULT_SCHEMA = {
    "type": "object",
    "required": ["policy", "policy_snapshot"],
    "properties": {"policy": {"type": "object"}, "policy_snapshot": {"type": "object"}},
}
MESSAGE_RESULT_SCHEMA = {
    "type": "object",
    "required": ["acceptance", "route_decision", "deliveries"],
    "properties": {
        "acceptance": {"type": "object"},
        "route_decision": {"type": "object"},
        "deliveries": {"type": "array"},
    },
}
DELIVERY_RESULT_SCHEMA = {
    "type": "object",
    "required": ["delivery"],
    "properties": {"delivery": {"type": "object"}},
}
DELIVERY_COLLECTION_RESULT_SCHEMA = {
    "type": "object",
    "required": ["delivery_collection"],
    "properties": {"delivery_collection": {"type": "object"}},
}


def _descriptor(
    operation_id: str,
    *,
    capability: str,
    target_scope: str,
    required_fences: list[str],
    mutation_class: str,
    request_schema: Mapping[str, Any],
    result_schema: Mapping[str, Any],
    confirmation_policy_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "operation_schema_version": 1,
        "request_schema_digest": canonical_json_sha256(request_schema),
        "result_schema_digest": canonical_json_sha256(result_schema),
        "required_capability": capability,
        "target_scope": target_scope,
        "required_fences": required_fences,
        "mutation_class": mutation_class,
        "confirmation_policy_ref": confirmation_policy_ref,
    }


OPERATION_DESCRIPTORS = (
    _descriptor(
        "host.status",
        capability="host.read",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=HOST_STATUS_RESULT_SCHEMA,
    ),
    _descriptor(
        "presentation.permission-probe",
        capability="participant.read",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PRESENTATION_PERMISSION_RESULT_SCHEMA,
    ),
    _descriptor(
        "presentation.permission-request",
        capability="participant.manage",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="external_effect",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PRESENTATION_PERMISSION_RESULT_SCHEMA,
    ),
    _descriptor(
        "environment.probe",
        capability="participant.read",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=ENVIRONMENT_PROBE_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.register",
        capability="project.manage",
        target_scope="host",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=PROJECT_REGISTER_REQUEST_SCHEMA,
        result_schema=PROJECT_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.list",
        capability="project.read",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PROJECT_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.reconcile",
        capability="project.manage",
        target_scope="project",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PROJECT_RECONCILE_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.accept-reconciliation",
        capability="project.manage",
        target_scope="project",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=PROJECT_ACCEPT_RECONCILIATION_REQUEST_SCHEMA,
        result_schema=PROJECT_RECONCILE_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.unregister",
        capability="project.manage",
        target_scope="host",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=PROJECT_UNREGISTER_REQUEST_SCHEMA,
        result_schema=PROJECT_UNREGISTER_RESULT_SCHEMA,
    ),
    _descriptor(
        "project.bootstrap",
        capability="project.manage",
        target_scope="host",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=PROJECT_REGISTER_REQUEST_SCHEMA,
        result_schema=PROJECT_BOOTSTRAP_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.create",
        capability="scenario.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=CREATE_REQUEST_SCHEMA,
        result_schema=SCENARIO_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.list",
        capability="scenario.read",
        target_scope="project",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=SCENARIO_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.open",
        capability="scenario.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=OPEN_REQUEST_SCHEMA,
        result_schema=SCENARIO_OPEN_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.close",
        capability="scenario.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=CLOSE_REQUEST_SCHEMA,
        result_schema=SCENARIO_CLOSE_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.start-participants",
        capability="participant.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_START_PARTICIPANTS_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.repair",
        capability="scenario.repair",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_RESULT_SCHEMA,
        confirmation_policy_ref="confirmation.destructive-once",
    ),
    _descriptor(
        "scenario.destroy.preview",
        capability="scenario.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_PREVIEW_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.destroy",
        capability="scenario.destroy",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="destructive",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_RESULT_SCHEMA,
        confirmation_policy_ref="confirmation.destructive-once",
    ),
    _descriptor(
        "scenario.force-destroy",
        capability="scenario.destroy",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="destructive",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_RESULT_SCHEMA,
        confirmation_policy_ref="confirmation.destructive-once",
    ),
    _descriptor(
        "scenario.diagnostic",
        capability="scenario.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=SCENARIO_DIAGNOSTIC_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.preflight",
        capability="scenario.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=SCENARIO_PREFLIGHT_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.topology",
        capability="scenario.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=SCENARIO_TOPOLOGY_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.focus",
        capability="scenario.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=SCENARIO_FENCED_REQUEST_SCHEMA,
        result_schema=SCENARIO_TOPOLOGY_RESULT_SCHEMA,
    ),
    _descriptor(
        "scenario.status",
        capability="scenario.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=SCENARIO_RESULT_SCHEMA,
    ),
    _descriptor(
        "resource.list",
        capability="resource.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=RESOURCE_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "resource.break",
        capability="resource.break",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="destructive",
        request_schema=RESOURCE_BREAK_REQUEST_SCHEMA,
        result_schema=RESOURCE_RESULT_SCHEMA,
        confirmation_policy_ref="confirmation.destructive-once",
    ),
    _descriptor(
        "workspace.plan",
        capability="workspace.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=WORKSPACE_PLAN_REQUEST_SCHEMA,
        result_schema=WORKSPACE_RESULT_SCHEMA,
    ),
    _descriptor(
        "workspace.provision",
        capability="workspace.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=WORKSPACE_PROVISION_REQUEST_SCHEMA,
        result_schema=WORKSPACE_RESULT_SCHEMA,
    ),
    _descriptor(
        "workspace.status",
        capability="workspace.read",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=WORKSPACE_STATUS_REQUEST_SCHEMA,
        result_schema=WORKSPACE_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.list",
        capability="participant.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PARTICIPANT_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.template.list",
        capability="participant.read",
        target_scope="host",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=PARTICIPANT_TEMPLATE_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.add",
        capability="participant.manage",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="durable_state",
        request_schema=PARTICIPANT_ADD_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.start",
        capability="participant.manage",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=PARTICIPANT_EXISTING_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.status",
        capability="participant.read",
        target_scope="participant",
        required_fences=["host_generation", "participant_generation"],
        mutation_class="read_only",
        request_schema=PARTICIPANT_EXISTING_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.stop",
        capability="participant.manage",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=PARTICIPANT_EXISTING_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.recover",
        capability="participant.manage",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=PARTICIPANT_EXISTING_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.replace",
        capability="participant.manage",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=PARTICIPANT_REPLACE_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
    ),
    _descriptor(
        "participant.force-stop",
        capability="participant.force-stop",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=PARTICIPANT_EXISTING_REQUEST_SCHEMA,
        result_schema=PARTICIPANT_RESULT_SCHEMA,
        confirmation_policy_ref="confirmation.destructive-once",
    ),
    _descriptor(
        "policy.template.list",
        capability="policy.read",
        target_scope="project",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=POLICY_TEMPLATE_LIST_RESULT_SCHEMA,
    ),
    _descriptor(
        "policy.plan",
        capability="policy.read",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="read_only",
        request_schema=POLICY_PLAN_REQUEST_SCHEMA,
        result_schema=POLICY_PLAN_RESULT_SCHEMA,
    ),
    _descriptor(
        "policy.apply-plan",
        capability="policy.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=POLICY_APPLY_PLAN_REQUEST_SCHEMA,
        result_schema=POLICY_RESULT_SCHEMA,
    ),
    _descriptor(
        "policy.apply",
        capability="policy.manage",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=POLICY_APPLY_REQUEST_SCHEMA,
        result_schema=POLICY_RESULT_SCHEMA,
    ),
    _descriptor(
        "policy.show",
        capability="policy.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=EMPTY_OBJECT_SCHEMA,
        result_schema=POLICY_RESULT_SCHEMA,
    ),
    _descriptor(
        "message.send",
        capability="delivery.send",
        target_scope="scenario",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=MESSAGE_SEND_REQUEST_SCHEMA,
        result_schema=MESSAGE_RESULT_SCHEMA,
    ),
    _descriptor(
        "message.send-self",
        capability="delivery.send-self",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=MESSAGE_SEND_SELF_REQUEST_SCHEMA,
        result_schema=MESSAGE_RESULT_SCHEMA,
    ),
    _descriptor(
        "message.reply-self",
        capability="delivery.send-self",
        target_scope="participant",
        required_fences=[
            "host_generation",
            "operation_generation",
            "participant_generation",
        ],
        mutation_class="external_effect",
        request_schema=MESSAGE_REPLY_SELF_REQUEST_SCHEMA,
        result_schema=MESSAGE_RESULT_SCHEMA,
    ),
    _descriptor(
        "delivery.list",
        capability="delivery.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=DELIVERY_LIST_REQUEST_SCHEMA,
        result_schema=DELIVERY_COLLECTION_RESULT_SCHEMA,
    ),
    _descriptor(
        "delivery.status",
        capability="delivery.read",
        target_scope="scenario",
        required_fences=["host_generation"],
        mutation_class="read_only",
        request_schema=DELIVERY_STATUS_REQUEST_SCHEMA,
        result_schema=DELIVERY_RESULT_SCHEMA,
    ),
    _descriptor(
        "delivery.consume",
        capability="delivery.consume",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="durable_state",
        request_schema=DELIVERY_CONSUME_REQUEST_SCHEMA,
        result_schema=DELIVERY_RESULT_SCHEMA,
    ),
    _descriptor(
        "delivery.retry",
        capability="delivery.send",
        target_scope="scenario",
        required_fences=["host_generation", "operation_generation"],
        mutation_class="external_effect",
        request_schema=DELIVERY_MUTATION_REQUEST_SCHEMA,
        result_schema=DELIVERY_RESULT_SCHEMA,
    ),
)
OPERATION_REGISTRY_DIGEST = canonical_json_sha256(list(OPERATION_DESCRIPTORS))
OPERATION_BY_ID = {value["operation_id"]: value for value in OPERATION_DESCRIPTORS}
HOST_CAPABILITIES = sorted(
    {value["required_capability"] for value in OPERATION_DESCRIPTORS}
)


@dataclass
class ProtocolError(ValueError):
    code: str
    category: str
    redacted_message: str
    retryable: bool = False
    repair_action: str | None = None

    def __str__(self) -> str:
        return self.redacted_message


def error_value(error: ProtocolError) -> dict[str, Any]:
    value = {
        "category": error.category,
        "code": error.code,
        "retryable": error.retryable,
        "redacted_message": error.redacted_message,
    }
    if error.repair_action is not None:
        value["repair_action"] = error.repair_action
    return value


def rejected_reply(request_id: str, error: ProtocolError) -> dict[str, Any]:
    return {
        "message_type": "operation_reply",
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id if _is_opaque_id(request_id) else "invalid-request",
        "outcome": "rejected",
        "mutation_state": "not_started",
        "error": error_value(error),
    }


def failed_reply(
    request_id: str,
    operation_id: str,
    host_generation: int,
    mutation_state: str,
    error: ProtocolError,
) -> dict[str, Any]:
    return {
        "message_type": "operation_reply",
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id if _is_opaque_id(request_id) else "invalid-request",
        "outcome": "failed",
        "operation_id": operation_id,
        "host_generation": host_generation,
        "mutation_state": mutation_state,
        "error": error_value(error),
    }


def progress_event(
    operation_id: str,
    sequence: int,
    state: str,
    host_generation: int,
    progress: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "message_type": "progress_event",
        "contract_version": CONTRACT_VERSION,
        "operation_id": operation_id,
        "sequence": sequence,
        "state": state,
        "host_generation": host_generation,
        "progress": dict(progress),
    }


def cancel_reply(
    request_id: str,
    operation_id: str,
    *,
    outcome: str,
    host_generation: int,
    mutation_state: str,
    error: ProtocolError | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "message_type": "cancel_reply",
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id if _is_opaque_id(request_id) else "invalid-request",
        "outcome": outcome,
        "operation_id": (
            operation_id if _is_opaque_id(operation_id) else "invalid-operation"
        ),
        "mutation_state": mutation_state,
    }
    if outcome == "accepted":
        value["host_generation"] = host_generation
    if error is not None:
        value["error"] = error_value(error)
    return value


def handshake_rejected(request_id: str, error: ProtocolError) -> dict[str, Any]:
    return {
        "message_type": "handshake_reply",
        "request_id": request_id if _is_opaque_id(request_id) else "invalid-request",
        "outcome": "rejected",
        "error": error_value(error),
    }


def _is_opaque_id(value: Any) -> bool:
    return isinstance(value, str) and OPAQUE_ID_RE.fullmatch(value) is not None


def _require_exact_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProtocolError(
            "ipc.invalid-message",
            "protocol",
            f"{label} fields are invalid",
        )
    return value


def validate_handshake_request(value: Any) -> dict[str, Any]:
    request = _require_exact_fields(
        value,
        {
            "message_type",
            "request_id",
            "client_instance_id",
            "supported_contract_versions",
            "client_capabilities",
        },
        label="handshake request",
    )
    versions = request["supported_contract_versions"]
    capabilities = request["client_capabilities"]
    if (
        request["message_type"] != "handshake_request"
        or not _is_opaque_id(request["request_id"])
        or not _is_opaque_id(request["client_instance_id"])
        or not isinstance(versions, list)
        or not versions
        or any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in versions)
        or len(versions) != len(set(versions))
        or not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or len(capabilities) != len(set(capabilities))
    ):
        raise ProtocolError("ipc.invalid-message", "protocol", "handshake request is invalid")
    if CONTRACT_VERSION not in versions:
        raise ProtocolError(
            "ipc.unsupported-contract",
            "protocol",
            "no compatible Host IPC contract version",
        )
    return request


def validate_operation_request(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _require_exact_fields(
        value,
        {
            "message_type",
            "contract_version",
            "request_id",
            "operation",
            "operation_schema_version",
            "operation_registry_digest",
            "capability_proof",
            "target",
            "fence",
            "payload",
        },
        label="operation request",
    )
    if request["message_type"] != "operation_request" or request["contract_version"] != 1:
        raise ProtocolError("ipc.invalid-message", "protocol", "operation envelope is invalid")
    if not _is_opaque_id(request["request_id"]):
        raise ProtocolError("ipc.invalid-message", "protocol", "request identity is invalid")
    descriptor = OPERATION_BY_ID.get(request["operation"])
    if descriptor is None:
        raise ProtocolError("ipc.operation-not-allowed", "protocol", "operation is not allowlisted")
    if request["operation_schema_version"] != descriptor["operation_schema_version"]:
        raise ProtocolError(
            "ipc.operation-schema-mismatch",
            "protocol",
            "operation schema version differs",
        )
    if request["operation_registry_digest"] != OPERATION_REGISTRY_DIGEST:
        raise ProtocolError(
            "ipc.operation-registry-mismatch",
            "protocol",
            "operation registry binding differs",
            retryable=True,
        )
    if not isinstance(request["capability_proof"], str) or not request["capability_proof"]:
        raise ProtocolError("auth.capability-denied", "authorization", "capability proof is unavailable")
    _validate_target(request["target"], descriptor["target_scope"])
    _validate_fence(request["fence"], descriptor["required_fences"])
    _validate_payload(request["operation"], request["payload"])
    return request, descriptor


def validate_cancel_request(value: Any) -> dict[str, Any]:
    request = _require_exact_fields(
        value,
        {
            "message_type",
            "contract_version",
            "request_id",
            "operation_id",
            "host_generation",
            "capability_proof",
        },
        label="cancel request",
    )
    if (
        request["message_type"] != "cancel_request"
        or request["contract_version"] != CONTRACT_VERSION
        or not _is_opaque_id(request["request_id"])
        or not _is_opaque_id(request["operation_id"])
        or not isinstance(request["host_generation"], int)
        or isinstance(request["host_generation"], bool)
        or request["host_generation"] < 1
        or not isinstance(request["capability_proof"], str)
        or SHA256_RE.fullmatch(request["capability_proof"]) is None
    ):
        raise ProtocolError(
            "ipc.invalid-message", "protocol", "cancel request is invalid"
        )
    return request


def _validate_target(value: Any, expected_scope: str) -> None:
    fields = {
        "host": {"scope"},
        "project": {"scope", "project_instance_id"},
        "scenario": {"scope", "project_instance_id", "scenario_id"},
        "participant": {
            "scope",
            "project_instance_id",
            "scenario_id",
            "participant_id",
        },
    }[expected_scope]
    target = _require_exact_fields(value, fields, label="operation target")
    if target["scope"] != expected_scope:
        raise ProtocolError("ipc.invalid-message", "protocol", "operation target differs")
    for key in fields - {"scope"}:
        if not _is_opaque_id(target[key]):
            raise ProtocolError("ipc.invalid-message", "protocol", "operation target identity is invalid")


def _validate_fence(value: Any, required: list[str]) -> None:
    allowed = {"host_generation", "operation_generation", "participant_generation"}
    if not isinstance(value, dict) or not set(value).issubset(allowed) or not set(required).issubset(value):
        raise ProtocolError("ipc.invalid-message", "protocol", "required generation fence is unavailable")
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value.values()):
        raise ProtocolError("ipc.invalid-message", "protocol", "generation fence is invalid")


def _validate_payload(operation: str, value: Any) -> None:
    if operation in {
        "host.status",
        "presentation.permission-probe",
        "presentation.permission-request",
        "environment.probe",
        "project.list",
        "scenario.list",
        "scenario.status",
        "scenario.diagnostic",
        "scenario.preflight",
        "scenario.topology",
        "resource.list",
        "policy.show",
        "policy.template.list",
        "participant.list",
        "participant.template.list",
        "project.reconcile",
    }:
        _require_exact_fields(value, set(), label="operation payload")
        return
    if operation == "project.accept-reconciliation":
        payload = _require_exact_fields(
            value,
            {"availability_fingerprint"},
            label="project reconciliation payload",
        )
        if (
            not isinstance(payload["availability_fingerprint"], str)
            or SHA256_RE.fullmatch(payload["availability_fingerprint"]) is None
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "project reconciliation fingerprint is invalid",
            )
        return
    if operation in {"project.register", "project.bootstrap"}:
        payload = _require_exact_fields(
            value,
            {"canonical_project_path"},
            label="project register payload",
        )
        path = payload["canonical_project_path"]
        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or len(path.encode("utf-8")) > 4096
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "project registration path is invalid",
            )
        return
    if operation == "project.unregister":
        payload = _require_exact_fields(
            value,
            {"project_instance_id"},
            label="project unregister payload",
        )
        identity = payload["project_instance_id"]
        if (
            not isinstance(identity, str)
            or not identity
            or "\x00" in identity
            or len(identity) > 256
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "project instance identity is invalid",
            )
        return
    if operation == "scenario.create":
        payload = _require_exact_fields(value, {"project_binding_digest"}, label="scenario create payload")
        if not isinstance(payload["project_binding_digest"], str) or SHA256_RE.fullmatch(payload["project_binding_digest"]) is None:
            raise ProtocolError("ipc.operation-schema-mismatch", "protocol", "project binding digest is invalid")
        return
    if operation == "scenario.open":
        payload = _require_exact_fields(
            value,
            {"scenario_generation", "scenario_state_revision"},
            label="scenario open payload",
        )
        if any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in payload.values()):
            raise ProtocolError("ipc.operation-schema-mismatch", "protocol", "scenario precondition is invalid")
        return
    if operation in {
        "scenario.repair",
        "scenario.destroy.preview",
        "scenario.destroy",
        "scenario.force-destroy",
        "scenario.focus",
        "scenario.start-participants",
    }:
        payload = _require_exact_fields(
            value,
            {"scenario_generation", "scenario_state_revision"},
            label="scenario fenced payload",
        )
        _validate_scenario_revision(payload)
        return
    if operation == "scenario.close":
        payload = _require_exact_fields(
            value,
            {
                "scenario_generation",
                "scenario_state_revision",
                "drain_timeout_ms",
            },
            label="scenario close payload",
        )
        if any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or item < 1
            for item in payload.values()
        ) or payload["drain_timeout_ms"] > 300_000:
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "scenario close precondition is invalid",
            )
        return
    if operation in {"participant.add", "participant.replace"}:
        fields = {
            "scenario_generation",
            "scenario_state_revision",
            "launch_spec",
            "presentation_driver_id",
        }
        if operation == "participant.replace":
            fields.add("participant_state_revision")
        payload = _require_exact_fields(
            value,
            fields,
            label=f"{operation} payload",
        )
        _validate_scenario_revision(payload)
        if operation == "participant.replace" and (
            not isinstance(payload["participant_state_revision"], int)
            or isinstance(payload["participant_state_revision"], bool)
            or payload["participant_state_revision"] < 1
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "participant revision is invalid",
            )
        validate_runtime_launch_spec(payload["launch_spec"])
        presentation_driver_id = payload["presentation_driver_id"]
        if presentation_driver_id is not None and not _is_opaque_id(
            presentation_driver_id
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "presentation driver identity is invalid",
            )
        if (
            payload["launch_spec"]["interaction_mode"] == "tui"
        ) != (presentation_driver_id is not None):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "participant presentation does not match interaction mode",
            )
        return
    if operation.startswith("participant."):
        payload = _require_exact_fields(
            value,
            {
                "scenario_generation",
                "scenario_state_revision",
                "participant_state_revision",
            },
            label="participant operation payload",
        )
        _validate_scenario_revision(payload)
        if (
            not isinstance(payload["participant_state_revision"], int)
            or isinstance(payload["participant_state_revision"], bool)
            or payload["participant_state_revision"] < 1
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "participant revision is invalid",
            )
        return
    if operation == "resource.break":
        payload = _require_exact_fields(
            value,
            {
                "scenario_generation",
                "scenario_state_revision",
                "lease_id",
                "lease_revision",
            },
            label="resource break payload",
        )
        _validate_scenario_revision(payload)
        if (
            not _is_opaque_id(payload["lease_id"])
            or not isinstance(payload["lease_revision"], int)
            or isinstance(payload["lease_revision"], bool)
            or payload["lease_revision"] < 1
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "resource break fence is invalid",
            )
        return
    if operation in {"policy.plan", "policy.apply-plan"}:
        fields = {
            "scenario_generation",
            "scenario_state_revision",
            "template_id",
        }
        if operation == "policy.apply-plan":
            fields.add("plan_digest")
        payload = _require_exact_fields(
            value,
            fields,
            label="policy plan payload",
        )
        _validate_scenario_revision(payload)
        if (
            not _is_opaque_id(payload["template_id"])
            or (
                operation == "policy.apply-plan"
                and (
                    not isinstance(payload["plan_digest"], str)
                    or SHA256_RE.fullmatch(payload["plan_digest"]) is None
                )
            )
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "policy plan payload is invalid",
            )
        return
    if operation == "policy.apply":
        payload = _require_exact_fields(
            value,
            {"scenario_generation", "scenario_state_revision", "policy_pack"},
            label="policy apply payload",
        )
        _validate_scenario_revision(payload)
        if not isinstance(payload["policy_pack"], dict):
            raise ProtocolError(
                "ipc.operation-schema-mismatch", "protocol", "policy pack is invalid"
            )
        return
    if operation == "message.send":
        payload = _require_exact_fields(
            value,
            {
                "scenario_generation",
                "scenario_state_revision",
                "sender_participant_id",
                "sender_participant_generation",
                "sender_participant_state_revision",
                "receiver_intent",
                "message_id",
                "message_kind",
                "message",
            },
            label="message send payload",
        )
        _validate_scenario_revision(payload)
        if (
            not _is_opaque_id(payload["sender_participant_id"])
            or not _is_opaque_id(payload["message_id"])
            or not _is_opaque_id(payload["message_kind"])
            or any(
                not isinstance(payload[field], int)
                or isinstance(payload[field], bool)
                or payload[field] < 1
                for field in (
                    "sender_participant_generation",
                    "sender_participant_state_revision",
                )
            )
            or not isinstance(payload["receiver_intent"], dict)
            or not isinstance(payload["message"], str)
            or not payload["message"]
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch", "protocol", "message send payload is invalid"
            )
        return
    if operation in {"message.send-self", "message.reply-self"}:
        fields = {
            "receiver_participant_id",
            "message_id",
            "message_kind",
            "message",
        }
        if operation == "message.reply-self":
            fields.add("reply_to_delivery_id")
        payload = _require_exact_fields(value, fields, label="participant message payload")
        if (
            any(
                not _is_opaque_id(payload[field])
                for field in fields - {"message"}
            )
            or not isinstance(payload["message"], str)
            or not payload["message"]
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "participant message payload is invalid",
            )
        return
    if operation == "delivery.list":
        allowed = {
            "limit",
            "after_delivery_id",
            "collection_digest",
            "thread_root_delivery_id",
        }
        if not isinstance(value, dict) or set(value) - allowed or "limit" not in value:
            raise ProtocolError(
                "ipc.invalid-message",
                "protocol",
                "delivery list payload fields are invalid",
            )
        if (
            not isinstance(value["limit"], int)
            or isinstance(value["limit"], bool)
            or not 1 <= value["limit"] <= 256
            or any(
                not _is_opaque_id(value[field])
                for field in ("after_delivery_id", "thread_root_delivery_id")
                if field in value
            )
            or (
                "collection_digest" in value
                and (
                    not isinstance(value["collection_digest"], str)
                    or SHA256_RE.fullmatch(value["collection_digest"]) is None
                )
            )
            or (("after_delivery_id" in value) != ("collection_digest" in value))
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "delivery list payload is invalid",
            )
        return
    if operation == "delivery.status":
        payload = _require_exact_fields(
            value, {"delivery_id"}, label="delivery status payload"
        )
        if not _is_opaque_id(payload["delivery_id"]):
            raise ProtocolError(
                "ipc.operation-schema-mismatch", "protocol", "delivery identity is invalid"
            )
        return
    if operation in {"delivery.consume", "delivery.retry"}:
        fields = {"delivery_id", "event_sequence"}
        if operation == "delivery.consume":
            fields.add("consumption_ack")
        payload = _require_exact_fields(value, fields, label="delivery mutation payload")
        if (
            not _is_opaque_id(payload["delivery_id"])
            or not isinstance(payload["event_sequence"], int)
            or isinstance(payload["event_sequence"], bool)
            or payload["event_sequence"] < 0
            or (
                operation == "delivery.consume"
                and not isinstance(payload["consumption_ack"], dict)
            )
        ):
            raise ProtocolError(
                "ipc.operation-schema-mismatch", "protocol", "delivery mutation payload is invalid"
            )
        return
    if operation == "workspace.plan":
        payload = _require_exact_fields(
            value,
            {
                "scenario_generation",
                "scenario_state_revision",
                "requested_component_ids",
                "project_payload",
            },
            label="workspace plan payload",
        )
        _validate_workspace_revision(payload)
        identifiers = payload["requested_component_ids"]
        if (
            not isinstance(identifiers, list)
            or any(not _is_opaque_id(item) for item in identifiers)
            or len(identifiers) != len(set(identifiers))
            or not isinstance(payload["project_payload"], dict)
        ):
            raise ProtocolError("ipc.operation-schema-mismatch", "protocol", "workspace plan payload is invalid")
        return
    digest_field = "plan_digest" if operation == "workspace.provision" else "receipt_digest"
    payload = _require_exact_fields(
        value,
        {"scenario_generation", "scenario_state_revision", digest_field},
        label="workspace operation payload",
    )
    _validate_workspace_revision(payload)
    if not isinstance(payload[digest_field], str) or SHA256_RE.fullmatch(payload[digest_field]) is None:
        raise ProtocolError("ipc.operation-schema-mismatch", "protocol", "workspace digest fence is invalid")


def _validate_workspace_revision(payload: Mapping[str, Any]) -> None:
    _validate_scenario_revision(payload)


def _validate_scenario_revision(payload: Mapping[str, Any]) -> None:
    revisions = (payload["scenario_generation"], payload["scenario_state_revision"])
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in revisions):
        raise ProtocolError("ipc.operation-schema-mismatch", "protocol", "workspace revision is invalid")


def validate_runtime_launch_spec(value: Any) -> None:
    launch = _require_exact_fields(
        value,
        {
            "driver_id",
            "driver_contract_version",
            "interaction_mode",
            "continuity_mode",
            "runtime_profile_ref",
            "model_binding",
            "continuity_binding_ref",
        },
        label="runtime launch spec",
    )
    if (
        not _is_opaque_id(launch["driver_id"])
        or launch["driver_contract_version"] != 2
        or launch["interaction_mode"] not in {"tui", "headless"}
        or launch["continuity_mode"] not in {"explicit_recreate", "exact_resume"}
    ):
        raise ProtocolError(
            "ipc.operation-schema-mismatch", "protocol", "runtime launch spec is invalid"
        )
    for field in ("runtime_profile_ref", "continuity_binding_ref"):
        if launch[field] is not None and not _is_opaque_id(launch[field]):
            raise ProtocolError(
                "ipc.operation-schema-mismatch",
                "protocol",
                "runtime launch binding is invalid",
            )
    model = launch["model_binding"]
    if model is None:
        return
    model = _require_exact_fields(
        model,
        {"provider_profile_ref", "model_ref", "inference_profile_ref"},
        label="model binding",
    )
    if not _is_opaque_id(model["provider_profile_ref"]) or not _is_opaque_id(
        model["model_ref"]
    ):
        raise ProtocolError(
            "ipc.operation-schema-mismatch", "protocol", "model binding is invalid"
        )
    inference = model["inference_profile_ref"]
    if inference is not None and not _is_opaque_id(inference):
        raise ProtocolError(
            "ipc.operation-schema-mismatch", "protocol", "model binding is invalid"
        )
