#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司
"""Validate the Phase 0 collaboration-policy and reliable-delivery contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_collab_bootstrap_evidence import canonical_json_sha256, sha256_file
import validate_ai_collab_state_contract as state_validator


VALIDATION_ID = "PHASE0-COLLABORATION-POLICY-DELIVERY-CONFORMANCE"
CONTRACT_RELATIVE_PATH = (
    "docs/ai-collab-harness/contracts/collaboration_policy_delivery_v1.schema.json"
)
STATE_CONTRACT_RELATIVE_PATH = state_validator.CONTRACT_RELATIVE_PATH
IPC_CONTRACT_RELATIVE_PATH = (
    "docs/ai-collab-harness/contracts/host_ipc_v1.schema.json"
)
MAX_CONTRACT_BYTES = 512 * 1024
TOP_LEVEL_FIELDS = {"$schema", "$id", "title", "oneOf", "$defs", "x-ai-collab"}
ROOT_ARTIFACT_DEFS = (
    "policy_pack",
    "policy_snapshot",
    "route_request",
    "route_decision",
    "delivery_record",
    "delivery_ack",
    "consumption_ack",
)
EXPECTED_DEFS = {
    "opaque_id",
    "namespaced_id",
    "sha256",
    "generation",
    "revision",
    "nullable_opaque_id",
    "nullable_namespaced_id",
    "nullable_sha256",
    "participant_ref",
    "selector_participant",
    "selector_assignment",
    "route_selector",
    "assignment_binding",
    "retry_profile",
    "route_rule",
    "policy_pack",
    "policy_snapshot",
    "route_request",
    "route_decision",
    "retry_profile_snapshot",
    "delivery_target",
    "delivery_event",
    "delivery_record",
    "delivery_ack",
    "consumption_ack",
}
DELIVERY_STATES = (
    "queued",
    "delivery_attempted",
    "delivered",
    "consumed",
)
DELIVERY_TRANSITIONS = (
    ("attempt", ("queued", "delivery_attempted"), "delivery_attempted"),
    ("matching_delivery_ack", ("delivery_attempted",), "delivered"),
    ("matching_consumption_ack", ("delivered",), "consumed"),
)
EXPECTED_ROUTING_PROTOCOL = {
    "route_rules_are_ordered_first_match": True,
    "default_effect_is_deny": True,
    "attributes_are_namespaced_policy_data_not_identity": True,
    "route_decision_contains_only_explicit_exact_targets": True,
    "host_does_not_infer_broadcast_quorum_or_escalation": True,
    "every_queued_message_pins_policy_version_and_digest": True,
    "policy_update_never_reroutes_existing_delivery": True,
}
EXPECTED_DELIVERY_PROTOCOL = {
    "file_or_sidecar_never_implies_delivery": True,
    "matching_ack_is_required_for_delivered": True,
    "matching_consumption_ack_is_required_for_consumed": True,
    "target_is_scenario_sender_receiver_generation_and_binding_exact": True,
    "delivery_events_are_append_only": True,
    "retry_profile_is_policy_snapshot_not_global_constant": True,
    "retry_is_bounded_and_backoff_nondecreasing": True,
    "restart_resumes_all_nonterminal_without_redelivering_delivered": True,
    "retry_exhaustion_degrades_only_exact_target": True,
    "no_role_recent_session_or_other_mailbox_fallback": True,
}
EXPECTED_INVARIANTS = {
    "participant_identity_is_never_policy_attribute",
    "sender_and_receiver_are_exact_participant_generations",
    "ready_receiver_binding_is_frozen_at_enqueue",
    "policy_and_route_digests_are_immutable_per_delivery",
    "one_route_target_produces_one_delivery_record",
    "cross_scenario_or_stale_generation_route_fails_closed",
    "payload_is_digest_only_in_contract_values",
    "vendor_session_identity_is_not_required",
    "no_policy_rule_executes_shell_or_vendor_api",
}
EXPECTED_DEFERRED = {
    "project-and-organization-policy-pack-population",
    "host-policy-storage-and-transaction-implementation",
    "runtime-and-presentation-transport-adapters",
    "real-agent-consumption-witness",
    "workspace-environment-operation-payloads",
    "permission-and-high-risk-confirmation-matrix",
    "gate-registry-fingerprint-wiring-and-rebuild",
}
ALLOWED_SCHEMA_KEYWORDS = {
    "$ref",
    "type",
    "const",
    "enum",
    "pattern",
    "minimum",
    "items",
    "uniqueItems",
    "required",
    "properties",
    "additionalProperties",
    "oneOf",
}
IMMUTABLE_DELIVERY_FIELDS = {
    "delivery_id",
    "message_id",
    "route_request_digest",
    "route_decision_digest",
    "policy_snapshot",
    "target",
    "payload_digest",
    "retry_profile",
}


class PolicyDeliveryContractError(ValueError):
    """The policy/delivery contract or a typed value is invalid."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyDeliveryContractError(
                f"JSON object contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _load_json(text: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    except PolicyDeliveryContractError:
        raise
    except json.JSONDecodeError as exc:
        raise PolicyDeliveryContractError(f"{label} is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise PolicyDeliveryContractError(f"{label} root must be an object")
    return value


def _read_regular_artifact(root: Path, relative_path: str) -> tuple[Path, str]:
    path = root / relative_path
    if path.is_symlink() or not path.is_file():
        raise PolicyDeliveryContractError(
            f"{relative_path} must be a regular project artifact"
        )
    if path.stat().st_size > MAX_CONTRACT_BYTES:
        raise PolicyDeliveryContractError(f"{relative_path} exceeds size limit")
    return path, path.read_text(encoding="utf-8")


def _resolve_ref(contract: Mapping[str, Any], ref: Any) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        raise PolicyDeliveryContractError("contract allows only local $defs references")
    name = ref.removeprefix("#/$defs/")
    if "/" in name or name not in contract.get("$defs", {}):
        raise PolicyDeliveryContractError(f"contract reference is unresolved: {ref}")
    value = contract["$defs"][name]
    if not isinstance(value, dict):
        raise PolicyDeliveryContractError(f"contract definition is not an object: {name}")
    return value


def _validate_schema_node(
    schema: Any, *, contract: Mapping[str, Any], path: str
) -> None:
    if not isinstance(schema, dict):
        raise PolicyDeliveryContractError(f"{path} schema node must be an object")
    unsupported = set(schema) - ALLOWED_SCHEMA_KEYWORDS
    if unsupported:
        raise PolicyDeliveryContractError(
            f"{path} uses unsupported schema keywords: {sorted(unsupported)}"
        )
    if "$ref" in schema:
        if len(schema) != 1:
            raise PolicyDeliveryContractError(f"{path} combines $ref with other keywords")
        _resolve_ref(contract, schema["$ref"])
        return
    if "oneOf" in schema:
        choices = schema["oneOf"]
        if not isinstance(choices, list) or not choices:
            raise PolicyDeliveryContractError(f"{path}.oneOf must be non-empty")
        for index, choice in enumerate(choices):
            _validate_schema_node(
                choice, contract=contract, path=f"{path}.oneOf[{index}]"
            )
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in {
        "object",
        "array",
        "string",
        "integer",
        "null",
    }:
        raise PolicyDeliveryContractError(f"{path} has unsupported type")
    if schema_type == "object":
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        if (
            not isinstance(required, list)
            or len(required) != len(set(required))
            or not isinstance(properties, dict)
            or not set(required) <= set(properties)
            or schema.get("additionalProperties") is not False
        ):
            raise PolicyDeliveryContractError(f"{path} object schema is not closed")
        for key, child in properties.items():
            _validate_schema_node(
                child, contract=contract, path=f"{path}.properties.{key}"
            )
    if schema_type == "array":
        if "items" not in schema:
            raise PolicyDeliveryContractError(f"{path} array schema lacks items")
        _validate_schema_node(
            schema["items"], contract=contract, path=f"{path}.items"
        )


def _matches_schema(
    value: Any,
    schema: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    path: str,
) -> None:
    if "$ref" in schema:
        _matches_schema(
            value,
            _resolve_ref(contract, schema["$ref"]),
            contract=contract,
            path=path,
        )
        return
    if "oneOf" in schema:
        errors = 0
        for choice in schema["oneOf"]:
            try:
                _matches_schema(value, choice, contract=contract, path=path)
            except PolicyDeliveryContractError:
                errors += 1
        if errors != len(schema["oneOf"]) - 1:
            raise PolicyDeliveryContractError(f"{path} does not match exactly one schema")
        return
    if "const" in schema and value != schema["const"]:
        raise PolicyDeliveryContractError(f"{path} does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise PolicyDeliveryContractError(f"{path} is outside enum")
    schema_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        None: True,
    }
    if not type_matches[schema_type]:
        raise PolicyDeliveryContractError(f"{path} has invalid type")
    if schema_type == "object":
        properties = schema["properties"]
        if not set(schema["required"]) <= set(value):
            raise PolicyDeliveryContractError(f"{path} lacks required fields")
        if not set(value) <= set(properties):
            raise PolicyDeliveryContractError(f"{path} has additional fields")
        for key, child in value.items():
            _matches_schema(
                child,
                properties[key],
                contract=contract,
                path=f"{path}.{key}",
            )
    if schema_type == "array":
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise PolicyDeliveryContractError(f"{path} items must be unique")
        for index, item in enumerate(value):
            _matches_schema(
                item,
                schema["items"],
                contract=contract,
                path=f"{path}[{index}]",
            )
    if schema_type == "string" and "pattern" in schema:
        if re.fullmatch(schema["pattern"], value) is None:
            raise PolicyDeliveryContractError(f"{path} does not match its pattern")
    if schema_type == "integer" and value < schema.get("minimum", value):
        raise PolicyDeliveryContractError(f"{path} is below its minimum")


def _transition_rows(
    rows: Any,
) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    if not isinstance(rows, list):
        raise PolicyDeliveryContractError("delivery transitions must be a list")
    parsed: list[tuple[str, tuple[str, ...], str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"trigger", "from", "to"}:
            raise PolicyDeliveryContractError("delivery transition fields do not match")
        sources = row["from"]
        if (
            not isinstance(row["trigger"], str)
            or not isinstance(row["to"], str)
            or not isinstance(sources, list)
            or not sources
            or not all(isinstance(item, str) for item in sources)
            or len(sources) != len(set(sources))
        ):
            raise PolicyDeliveryContractError("delivery transition row is invalid")
        parsed.append((row["trigger"], tuple(sources), row["to"]))
    return tuple(parsed)


def validate_contract(
    *, repo_root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate the tracked F contract and its C/D dependencies."""

    root = repo_root.resolve()
    contract_path, text = _read_regular_artifact(root, CONTRACT_RELATIVE_PATH)
    state_path, state_text = _read_regular_artifact(root, STATE_CONTRACT_RELATIVE_PATH)
    _, ipc_text = _read_regular_artifact(root, IPC_CONTRACT_RELATIVE_PATH)
    contract = _load_json(text, label="policy/delivery contract")
    state_contract = _load_json(state_text, label="state contract")
    ipc_contract = _load_json(ipc_text, label="Host IPC contract")
    if set(contract) != TOP_LEVEL_FIELDS:
        raise PolicyDeliveryContractError("policy/delivery top-level fields do not match")
    if contract["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise PolicyDeliveryContractError("policy/delivery schema dialect is incompatible")
    if contract["$id"] != "urn:ai-collab:collaboration-policy-delivery:v1":
        raise PolicyDeliveryContractError("policy/delivery schema identity is incompatible")
    definitions = contract.get("$defs")
    if not isinstance(definitions, dict) or set(definitions) != EXPECTED_DEFS:
        raise PolicyDeliveryContractError("policy/delivery definition set does not match")
    expected_root = [
        {"$ref": f"#/$defs/{name}"} for name in ROOT_ARTIFACT_DEFS
    ]
    if contract["oneOf"] != expected_root:
        raise PolicyDeliveryContractError("policy/delivery root artifacts do not match")
    for name, schema in definitions.items():
        _validate_schema_node(schema, contract=contract, path=f"$defs.{name}")

    metadata = contract.get("x-ai-collab")
    expected_fields = {
        "schema_version",
        "contract_id",
        "product_contract_version",
        "dependencies",
        "canonical_delivery_states",
        "delivery_transitions",
        "routing_protocol",
        "delivery_protocol",
        "invariants",
        "deferred_surfaces",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_fields:
        raise PolicyDeliveryContractError("policy/delivery metadata fields do not match")
    if metadata["schema_version"] != 1 or isinstance(
        metadata["schema_version"], bool
    ):
        raise PolicyDeliveryContractError("policy/delivery schema version is incompatible")
    if metadata["contract_id"] != "ai-collab-collaboration-policy-delivery-v1":
        raise PolicyDeliveryContractError("policy/delivery contract id is incompatible")
    if metadata["product_contract_version"] != "3.2":
        raise PolicyDeliveryContractError("policy/delivery product version is incompatible")
    expected_dependencies = {
        "host_ipc_contract_id": "ai-collab-host-ipc-v1",
        "state_contract_id": "ai-collab-scenario-participant-state-v1",
        "participant_binding_source": "state-contract-runtime-and-presentation-binding",
    }
    if metadata["dependencies"] != expected_dependencies:
        raise PolicyDeliveryContractError("policy/delivery dependency metadata is incompatible")
    state_metadata = state_contract.get("x-ai-collab", {})
    ipc_metadata = ipc_contract.get("x-ai-collab", {})
    participant_properties = (
        state_contract.get("$defs", {})
        .get("participant_record", {})
        .get("properties", {})
    )
    if (
        state_metadata.get("contract_id") != expected_dependencies["state_contract_id"]
        or not {
            "scenario_id",
            "participant_id",
            "participant_generation",
            "interaction_mode",
            "runtime_binding_id",
            "presentation_binding_id",
        }
        <= set(participant_properties)
    ):
        raise PolicyDeliveryContractError("tracked state contract is incompatible")
    if ipc_metadata.get("contract_id") != expected_dependencies["host_ipc_contract_id"]:
        raise PolicyDeliveryContractError("tracked Host IPC contract is incompatible")
    if metadata["canonical_delivery_states"] != list(DELIVERY_STATES):
        raise PolicyDeliveryContractError("canonical delivery states are incompatible")
    if _transition_rows(metadata["delivery_transitions"]) != DELIVERY_TRANSITIONS:
        raise PolicyDeliveryContractError("delivery transition table is incompatible")
    if metadata["routing_protocol"] != EXPECTED_ROUTING_PROTOCOL:
        raise PolicyDeliveryContractError("routing protocol is incompatible")
    if metadata["delivery_protocol"] != EXPECTED_DELIVERY_PROTOCOL:
        raise PolicyDeliveryContractError("delivery protocol is incompatible")
    invariants = metadata["invariants"]
    if not isinstance(invariants, dict) or set(invariants) != EXPECTED_INVARIANTS:
        raise PolicyDeliveryContractError("policy/delivery invariant set does not match")
    if any(value is not True for value in invariants.values()):
        raise PolicyDeliveryContractError("every policy/delivery invariant must be active")
    deferred = metadata["deferred_surfaces"]
    if (
        not isinstance(deferred, list)
        or set(deferred) != EXPECTED_DEFERRED
        or len(deferred) != len(EXPECTED_DEFERRED)
    ):
        raise PolicyDeliveryContractError("deferred policy/delivery surfaces do not match")

    result = {
        "schema_version": 1,
        "validation_id": VALIDATION_ID,
        "status": "valid",
        "contract_relative_path": CONTRACT_RELATIVE_PATH,
        "contract_digest": canonical_json_sha256(contract),
        "raw_sha256": sha256_file(contract_path),
        "contract_id": metadata["contract_id"],
        "product_contract_version": metadata["product_contract_version"],
        "state_contract_digest": canonical_json_sha256(state_contract),
        "state_contract_raw_sha256": sha256_file(state_path),
        "ipc_contract_digest": canonical_json_sha256(ipc_contract),
        "root_artifact_count": len(ROOT_ARTIFACT_DEFS),
        "delivery_transition_count": len(DELIVERY_TRANSITIONS),
        "state_mutated": False,
    }
    return contract, state_contract, result


def _schema(contract: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _resolve_ref(contract, f"#/$defs/{name}")


def _validate_retry_profile(profile: Mapping[str, Any]) -> None:
    schedule = profile["backoff_ms"]
    if len(schedule) != profile["max_attempts"]:
        raise PolicyDeliveryContractError("retry backoff count does not match max attempts")
    if not schedule or schedule[0] != 0:
        raise PolicyDeliveryContractError("retry backoff must begin at zero")
    if any(later < earlier for earlier, later in zip(schedule, schedule[1:])):
        raise PolicyDeliveryContractError("retry backoff is decreasing")


def _validate_selector_scenario(selector: Mapping[str, Any], scenario_id: str) -> None:
    if (
        selector["kind"] == "participant"
        and selector["participant"]["scenario_id"] != scenario_id
    ):
        raise PolicyDeliveryContractError("policy selector crosses scenario")


def validate_policy_pack(pack: Any, *, contract: Mapping[str, Any]) -> None:
    _matches_schema(
        pack, _schema(contract, "policy_pack"), contract=contract, path="policy_pack"
    )
    scenario_id = pack["scenario_id"]
    assignment_ids: set[str] = set()
    assignment_keys: set[tuple[Any, ...]] = set()
    for assignment in pack["assignments"]:
        assignment_id = assignment["assignment_id"]
        if assignment_id in assignment_ids:
            raise PolicyDeliveryContractError("policy assignment id is duplicated")
        assignment_ids.add(assignment_id)
        participant = assignment["participant"]
        if participant["scenario_id"] != scenario_id:
            raise PolicyDeliveryContractError("policy assignment crosses scenario")
        key = (
            assignment["attribute"],
            assignment["task_id"],
            participant["participant_id"],
            participant["participant_generation"],
        )
        if key in assignment_keys:
            raise PolicyDeliveryContractError("policy assignment is duplicated")
        assignment_keys.add(key)

    profiles: dict[str, Mapping[str, Any]] = {}
    for profile in pack["retry_profiles"]:
        profile_id = profile["profile_id"]
        if profile_id in profiles:
            raise PolicyDeliveryContractError("retry profile id is duplicated")
        _validate_retry_profile(profile)
        profiles[profile_id] = profile

    rule_ids: set[str] = set()
    matchers: set[str] = set()
    for rule in pack["route_rules"]:
        rule_id = rule["rule_id"]
        if rule_id in rule_ids:
            raise PolicyDeliveryContractError("route rule id is duplicated")
        rule_ids.add(rule_id)
        for selector in (rule["sender"], rule["receiver"]):
            _validate_selector_scenario(selector, scenario_id)
            if selector["kind"] == "assignment" and not any(
                assignment["attribute"] == selector["attribute"]
                and assignment["task_id"] == selector["task_id"]
                for assignment in pack["assignments"]
            ):
                raise PolicyDeliveryContractError(
                    "route rule references an unresolved assignment selector"
                )
        matcher = canonical_json_sha256(
            {
                "sender": rule["sender"],
                "receiver": rule["receiver"],
                "message_kind": rule["message_kind"],
            }
        )
        if matcher in matchers:
            raise PolicyDeliveryContractError("route rule is shadowed by an earlier matcher")
        matchers.add(matcher)
        retry_profile_id = rule["retry_profile_id"]
        if rule["effect"] == "allow":
            if retry_profile_id is None or retry_profile_id not in profiles:
                raise PolicyDeliveryContractError(
                    "allow route lacks a valid retry profile"
                )
        elif retry_profile_id is not None:
            raise PolicyDeliveryContractError("deny route carries a retry profile")


def policy_snapshot(pack: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": pack["policy_id"],
        "policy_version": pack["policy_version"],
        "policy_digest": canonical_json_sha256(pack),
    }


def validate_policy_update(
    before: Any, after: Any, *, contract: Mapping[str, Any]
) -> None:
    validate_policy_pack(before, contract=contract)
    validate_policy_pack(after, contract=contract)
    for field in ("policy_contract_version", "policy_id", "scenario_id"):
        if before[field] != after[field]:
            raise PolicyDeliveryContractError(f"policy update changed immutable {field}")
    if after["policy_version"] != before["policy_version"] + 1:
        raise PolicyDeliveryContractError("policy update lacks exact version increment")


def _participant_ref(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": record["scenario_id"],
        "participant_id": record["participant_id"],
        "participant_generation": record["participant_generation"],
    }


def _participant_map(
    participants: Sequence[Mapping[str, Any]],
    *,
    state_contract: Mapping[str, Any],
    scenario_id: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for participant in participants:
        try:
            state_validator.validate_participant_record(
                participant, contract=state_contract
            )
        except state_validator.StateContractError as exc:
            raise PolicyDeliveryContractError(
                f"route participant record is invalid: {exc}"
            ) from exc
        if participant["scenario_id"] != scenario_id:
            raise PolicyDeliveryContractError("route participants cross scenario")
        participant_id = participant["participant_id"]
        if participant_id in result:
            raise PolicyDeliveryContractError("route participant id is duplicated")
        result[participant_id] = participant
    return result


def _resolve_selector(
    selector: Mapping[str, Any], pack: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if selector["kind"] == "participant":
        return [dict(selector["participant"])]
    return [
        dict(assignment["participant"])
        for assignment in pack["assignments"]
        if assignment["attribute"] == selector["attribute"]
        and assignment["task_id"] == selector["task_id"]
    ]


def _selector_contains(
    selector: Mapping[str, Any],
    participant: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> bool:
    return participant in _resolve_selector(selector, pack)


def _participant_is_ready(
    participant_ref: Mapping[str, Any], participants: Mapping[str, Mapping[str, Any]]
) -> bool:
    current = participants.get(participant_ref["participant_id"])
    return bool(
        current is not None
        and _participant_ref(current) == participant_ref
        and current["desired_state"] == "running"
        and current["observed_state"] == "ready"
        and current["runtime_binding_id"] is not None
    )


def resolve_route(
    pack: Any,
    request: Any,
    participants: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
    state_contract: Mapping[str, Any],
) -> dict[str, Any]:
    validate_policy_pack(pack, contract=contract)
    _matches_schema(
        request,
        _schema(contract, "route_request"),
        contract=contract,
        path="route_request",
    )
    if request["scenario_id"] != pack["scenario_id"]:
        raise PolicyDeliveryContractError("route request crosses policy scenario")
    if request["sender"]["scenario_id"] != request["scenario_id"]:
        raise PolicyDeliveryContractError("route sender crosses scenario")
    _validate_selector_scenario(request["receiver_intent"], request["scenario_id"])
    snapshot = policy_snapshot(pack)
    if request["policy_snapshot"] != snapshot:
        raise PolicyDeliveryContractError("route request pins a stale policy snapshot")
    current = _participant_map(
        participants,
        state_contract=state_contract,
        scenario_id=request["scenario_id"],
    )
    if not _participant_is_ready(request["sender"], current):
        raise PolicyDeliveryContractError("route sender is not an exact ready participant")

    matched_rule: Mapping[str, Any] | None = None
    for rule in pack["route_rules"]:
        if (
            rule["message_kind"] == request["message_kind"]
            and rule["receiver"] == request["receiver_intent"]
            and _selector_contains(rule["sender"], request["sender"], pack)
        ):
            matched_rule = rule
            break

    decision: dict[str, Any] = {
        "request_id": request["request_id"],
        "request_digest": canonical_json_sha256(request),
        "policy_snapshot": snapshot,
        "outcome": "deny",
        "matched_rule_id": None,
        "target_participants": [],
        "retry_profile_id": None,
        "denial_code": "policy.no-matching-rule",
    }
    if matched_rule is None:
        return decision
    decision["matched_rule_id"] = matched_rule["rule_id"]
    if matched_rule["effect"] == "deny":
        decision["denial_code"] = "policy.rule-denied"
        return decision

    targets = _resolve_selector(request["receiver_intent"], pack)
    if not targets or not all(_participant_is_ready(target, current) for target in targets):
        decision["denial_code"] = "policy.target-unavailable"
        return decision
    encoded = [json.dumps(target, sort_keys=True) for target in targets]
    if len(encoded) != len(set(encoded)):
        raise PolicyDeliveryContractError("route resolution produced duplicate targets")
    decision.update(
        {
            "outcome": "allow",
            "target_participants": targets,
            "retry_profile_id": matched_rule["retry_profile_id"],
            "denial_code": None,
        }
    )
    return decision


def validate_route_decision(
    pack: Any,
    request: Any,
    decision: Any,
    participants: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
    state_contract: Mapping[str, Any],
) -> None:
    _matches_schema(
        decision,
        _schema(contract, "route_decision"),
        contract=contract,
        path="route_decision",
    )
    expected = resolve_route(
        pack,
        request,
        participants,
        contract=contract,
        state_contract=state_contract,
    )
    if decision != expected:
        raise PolicyDeliveryContractError("route decision is not the exact policy result")


def _retry_profile_snapshot(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "profile": dict(profile),
        "profile_digest": canonical_json_sha256(profile),
    }


def _delivery_target(
    sender: Mapping[str, Any], receiver: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "sender": _participant_ref(sender),
        "receiver": _participant_ref(receiver),
        "interaction_mode": receiver["interaction_mode"],
        "runtime_binding_id": receiver["runtime_binding_id"],
        "presentation_binding_id": receiver["presentation_binding_id"],
    }


def _validate_delivery_error(value: str | None) -> None:
    if value is not None and not value.startswith("delivery."):
        raise PolicyDeliveryContractError("delivery event uses a non-delivery error code")


def validate_delivery_record(record: Any, *, contract: Mapping[str, Any]) -> None:
    _matches_schema(
        record,
        _schema(contract, "delivery_record"),
        contract=contract,
        path="delivery_record",
    )
    target = record["target"]
    if target["sender"]["scenario_id"] != target["receiver"]["scenario_id"]:
        raise PolicyDeliveryContractError("delivery target crosses scenario")
    if target["interaction_mode"] == "tui" and target["presentation_binding_id"] is None:
        raise PolicyDeliveryContractError("TUI delivery target lacks presentation binding")
    if (
        target["interaction_mode"] == "headless"
        and target["presentation_binding_id"] is not None
    ):
        raise PolicyDeliveryContractError("headless delivery target has presentation binding")
    profile_snapshot = record["retry_profile"]
    profile = profile_snapshot["profile"]
    _validate_retry_profile(profile)
    if profile_snapshot["profile_digest"] != canonical_json_sha256(profile):
        raise PolicyDeliveryContractError("delivery retry profile digest is stale")

    expected_sequence = 1
    attempt_count = 0
    active_attempt: tuple[int, str, int] | None = None
    delivered_attempt: tuple[int, str, int, str] | None = None
    phase = "queued"
    transport_ids: set[str] = set()
    for event in record["events"]:
        if event["sequence"] != expected_sequence:
            raise PolicyDeliveryContractError("delivery event sequence is not contiguous")
        expected_sequence += 1
        event_type = event["event"]
        _validate_delivery_error(event["error_code"])
        if event_type == "attempt_started":
            if active_attempt is not None or delivered_attempt is not None:
                raise PolicyDeliveryContractError("delivery attempt overlaps an active attempt")
            attempt_count += 1
            if attempt_count > profile["max_attempts"]:
                raise PolicyDeliveryContractError("delivery retry ceiling is exceeded")
            if event["attempt_number"] != attempt_count:
                raise PolicyDeliveryContractError("delivery attempt number is not contiguous")
            if event["backoff_ms"] != profile["backoff_ms"][attempt_count - 1]:
                raise PolicyDeliveryContractError("delivery attempt backoff is incompatible")
            if event["transport_attempt_id"] in transport_ids:
                raise PolicyDeliveryContractError("transport attempt id is reused")
            transport_ids.add(event["transport_attempt_id"])
            if event["evidence_digest"] is not None or event["error_code"] is not None:
                raise PolicyDeliveryContractError("attempt-start event carries an outcome")
            active_attempt = (
                attempt_count,
                event["transport_attempt_id"],
                event["backoff_ms"],
            )
            phase = "delivery_attempted"
        elif event_type == "attempt_failed":
            if active_attempt is None:
                raise PolicyDeliveryContractError("delivery failure lacks an active attempt")
            if (
                event["attempt_number"],
                event["transport_attempt_id"],
                event["backoff_ms"],
            ) != active_attempt:
                raise PolicyDeliveryContractError("delivery failure changed attempt identity")
            if event["evidence_digest"] is not None or event["error_code"] is None:
                raise PolicyDeliveryContractError("delivery failure evidence is inconsistent")
            active_attempt = None
            phase = "delivery_attempted"
        elif event_type == "ack_accepted":
            if active_attempt is None:
                raise PolicyDeliveryContractError("delivery ACK lacks an active attempt")
            if (
                event["attempt_number"],
                event["transport_attempt_id"],
                event["backoff_ms"],
            ) != active_attempt:
                raise PolicyDeliveryContractError("delivery ACK changed attempt identity")
            if event["evidence_digest"] is None or event["error_code"] is not None:
                raise PolicyDeliveryContractError("delivery ACK evidence is inconsistent")
            delivered_attempt = (*active_attempt, event["evidence_digest"])
            active_attempt = None
            phase = "delivered"
        else:
            if delivered_attempt is None or phase != "delivered":
                raise PolicyDeliveryContractError("consumption lacks a delivered ACK")
            if (
                event["attempt_number"],
                event["transport_attempt_id"],
                event["backoff_ms"],
            ) != delivered_attempt[:3]:
                raise PolicyDeliveryContractError("consumption changed attempt identity")
            if event["evidence_digest"] is None or event["error_code"] is not None:
                raise PolicyDeliveryContractError("consumption evidence is inconsistent")
            phase = "consumed"
    if record["state"] != phase:
        raise PolicyDeliveryContractError("delivery state does not match append-only events")
    exhausted = (
        phase == "delivery_attempted"
        and active_attempt is None
        and attempt_count == profile["max_attempts"]
    )
    reason = record["delivery_degraded_reason"]
    if exhausted:
        if reason != "delivery.retry-exhausted":
            raise PolicyDeliveryContractError("exhausted delivery lacks degraded reason")
    elif reason is not None:
        raise PolicyDeliveryContractError("non-exhausted delivery reports degradation")


def validate_delivery_enqueue(
    record: Any,
    pack: Any,
    request: Any,
    decision: Any,
    participants: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
    state_contract: Mapping[str, Any],
) -> None:
    validate_route_decision(
        pack,
        request,
        decision,
        participants,
        contract=contract,
        state_contract=state_contract,
    )
    validate_delivery_record(record, contract=contract)
    if decision["outcome"] != "allow":
        raise PolicyDeliveryContractError("denied route produced a delivery record")
    if record["state"] != "queued" or record["events"]:
        raise PolicyDeliveryContractError("delivery enqueue is not in queued state")
    current = _participant_map(
        participants,
        state_contract=state_contract,
        scenario_id=request["scenario_id"],
    )
    sender = current.get(request["sender"]["participant_id"])
    receiver_ref = record["target"]["receiver"]
    receiver = current.get(receiver_ref["participant_id"])
    if sender is None or receiver is None:
        raise PolicyDeliveryContractError("delivery target participant is unavailable")
    if receiver_ref not in decision["target_participants"]:
        raise PolicyDeliveryContractError("delivery target was not explicitly routed")
    expected_target = _delivery_target(sender, receiver)
    expected_profile = next(
        (
            profile
            for profile in pack["retry_profiles"]
            if profile["profile_id"] == decision["retry_profile_id"]
        ),
        None,
    )
    if expected_profile is None:
        raise PolicyDeliveryContractError("route retry profile is unavailable")
    expected_fields = {
        "message_id": request["message_id"],
        "route_request_digest": canonical_json_sha256(request),
        "route_decision_digest": canonical_json_sha256(decision),
        "policy_snapshot": request["policy_snapshot"],
        "target": expected_target,
        "payload_digest": request["payload_digest"],
        "retry_profile": _retry_profile_snapshot(expected_profile),
    }
    for field, expected in expected_fields.items():
        if record[field] != expected:
            raise PolicyDeliveryContractError(f"delivery enqueue changed exact {field}")


def validate_delivery_batch_enqueue(
    records: Sequence[Mapping[str, Any]],
    pack: Any,
    request: Any,
    decision: Any,
    participants: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
    state_contract: Mapping[str, Any],
) -> None:
    """Validate the one-route-target-to-one-delivery-record invariant."""

    validate_route_decision(
        pack,
        request,
        decision,
        participants,
        contract=contract,
        state_contract=state_contract,
    )
    if decision["outcome"] == "deny":
        if records:
            raise PolicyDeliveryContractError(
                "denied route produced a delivery batch"
            )
        return
    targets = decision["target_participants"]
    if len(records) != len(targets):
        raise PolicyDeliveryContractError(
            "delivery batch does not contain exactly one record per route target"
        )
    delivery_ids: set[str] = set()
    receiver_refs: list[Mapping[str, Any]] = []
    for record in records:
        validate_delivery_enqueue(
            record,
            pack,
            request,
            decision,
            participants,
            contract=contract,
            state_contract=state_contract,
        )
        delivery_id = record["delivery_id"]
        if delivery_id in delivery_ids:
            raise PolicyDeliveryContractError("delivery batch id is duplicated")
        delivery_ids.add(delivery_id)
        receiver_refs.append(record["target"]["receiver"])
    if receiver_refs != targets:
        raise PolicyDeliveryContractError(
            "delivery batch order does not exactly match route targets"
        )


def _active_attempt(record: Mapping[str, Any]) -> Mapping[str, Any]:
    if record["state"] != "delivery_attempted" or not record["events"]:
        raise PolicyDeliveryContractError("delivery has no active attempt")
    event = record["events"][-1]
    if event["event"] != "attempt_started":
        raise PolicyDeliveryContractError("delivery has no pending ACK attempt")
    return event


def validate_delivery_ack(
    record: Any, ack: Any, *, contract: Mapping[str, Any]
) -> str:
    validate_delivery_record(record, contract=contract)
    _matches_schema(
        ack,
        _schema(contract, "delivery_ack"),
        contract=contract,
        path="delivery_ack",
    )
    attempt = _active_attempt(record)
    expected = {
        "ack_kind": "delivered",
        "delivery_id": record["delivery_id"],
        "message_id": record["message_id"],
        "target": record["target"],
        "payload_digest": record["payload_digest"],
        "attempt_number": attempt["attempt_number"],
        "transport_attempt_id": attempt["transport_attempt_id"],
    }
    if ack != expected:
        raise PolicyDeliveryContractError("delivery ACK does not exactly match target")
    return canonical_json_sha256(ack)


def validate_consumption_ack(
    record: Any, ack: Any, *, contract: Mapping[str, Any]
) -> str:
    validate_delivery_record(record, contract=contract)
    _matches_schema(
        ack,
        _schema(contract, "consumption_ack"),
        contract=contract,
        path="consumption_ack",
    )
    if record["state"] != "delivered" or not record["events"]:
        raise PolicyDeliveryContractError("consumption ACK precedes delivery")
    delivered = record["events"][-1]
    if delivered["event"] != "ack_accepted":
        raise PolicyDeliveryContractError("delivery record lacks accepted ACK evidence")
    expected = {
        "ack_kind": "consumed",
        "delivery_id": record["delivery_id"],
        "message_id": record["message_id"],
        "target": record["target"],
        "payload_digest": record["payload_digest"],
        "attempt_number": delivered["attempt_number"],
        "transport_attempt_id": delivered["transport_attempt_id"],
        "delivery_ack_digest": delivered["evidence_digest"],
    }
    if ack != expected:
        raise PolicyDeliveryContractError("consumption ACK does not exactly match delivery")
    return canonical_json_sha256(ack)


def validate_delivery_transition(
    *,
    trigger: str,
    before: Any,
    after: Any,
    contract: Mapping[str, Any],
    ack: Mapping[str, Any] | None = None,
) -> None:
    validate_delivery_record(before, contract=contract)
    validate_delivery_record(after, contract=contract)
    if not any(
        trigger == row_trigger
        and before["state"] in sources
        and after["state"] == target
        for row_trigger, sources, target in DELIVERY_TRANSITIONS
    ):
        raise PolicyDeliveryContractError("delivery transition is not allowed")
    for field in IMMUTABLE_DELIVERY_FIELDS:
        if before[field] != after[field]:
            raise PolicyDeliveryContractError(f"delivery transition changed immutable {field}")
    if (
        len(after["events"]) <= len(before["events"])
        or after["events"][: len(before["events"])] != before["events"]
    ):
        raise PolicyDeliveryContractError("delivery transition rewrote append-only events")
    if trigger == "matching_delivery_ack":
        if ack is None:
            raise PolicyDeliveryContractError("delivered transition lacks matching ACK")
        expected_digest = validate_delivery_ack(before, ack, contract=contract)
        final_event = after["events"][-1]
        if (
            final_event["event"] != "ack_accepted"
            or final_event["evidence_digest"] != expected_digest
        ):
            raise PolicyDeliveryContractError("delivered transition lacks exact ACK evidence")
    elif trigger == "matching_consumption_ack":
        if ack is None:
            raise PolicyDeliveryContractError("consumed transition lacks matching ACK")
        expected_digest = validate_consumption_ack(before, ack, contract=contract)
        final_event = after["events"][-1]
        if (
            final_event["event"] != "consumed"
            or final_event["evidence_digest"] != expected_digest
        ):
            raise PolicyDeliveryContractError(
                "consumed transition lacks exact consumption evidence"
            )
    elif ack is not None:
        raise PolicyDeliveryContractError("attempt transition carries an ACK")


def resumable_delivery_ids(
    records: Sequence[Mapping[str, Any]], *, contract: Mapping[str, Any]
) -> list[str]:
    seen: set[str] = set()
    resumable: list[str] = []
    for record in records:
        validate_delivery_record(record, contract=contract)
        delivery_id = record["delivery_id"]
        if delivery_id in seen:
            raise PolicyDeliveryContractError("delivery recovery contains duplicate id")
        seen.add(delivery_id)
        if record["state"] in {"queued", "delivery_attempted", "delivered"}:
            resumable.append(delivery_id)
    return resumable


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _, _, result = validate_contract(repo_root=args.repo_root)
    except (PolicyDeliveryContractError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "validation_id": VALIDATION_ID,
                    "status": "failed",
                    "reason": str(exc),
                    "state_mutated": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
