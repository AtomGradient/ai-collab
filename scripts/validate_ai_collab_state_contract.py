#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司
"""Validate the Phase 0 Scenario/Participant state and lifecycle contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_collab_bootstrap_evidence import canonical_json_sha256, sha256_file


VALIDATION_ID = "PHASE0-SCENARIO-PARTICIPANT-STATE-CONFORMANCE"
CONTRACT_RELATIVE_PATH = (
    "contracts/scenario_participant_state_v1.schema.json"
)
DRIVER_CONTRACT_RELATIVE_PATH = (
    "contracts/participant_drivers_v2.schema.json"
)
MAX_CONTRACT_BYTES = 512 * 1024
TOP_LEVEL_FIELDS = {"$schema", "$id", "title", "oneOf", "$defs", "x-ai-collab"}
ROOT_ARTIFACT_DEFS = (
    "scenario_record",
    "participant_record",
    "lifecycle_operation",
    "operation_journal_entry",
)
EXPECTED_DEFS = {
    "opaque_id",
    "namespaced_id",
    "sha256",
    "generation",
    "revision",
    "nullable_revision",
    "nullable_opaque_id",
    "nullable_namespaced_id",
    "nullable_sha256",
    "nullable_generation",
    "scenario_desired_state",
    "scenario_observed_state",
    "participant_desired_state",
    "participant_observed_state",
    "nullable_scenario_state",
    "nullable_participant_state",
    "degraded_observation",
    "nullable_degraded_observation",
    "scenario_record",
    "participant_record",
    "operation_target_scenario",
    "operation_target_participant",
    "operation_target",
    "operation_fence",
    "lifecycle_operation",
    "operation_journal_entry",
}
SCENARIO_TRANSITIONS = (
    ("create", ("absent",), "provisioning", "closed"),
    ("provision_succeeded", ("provisioning",), "closed", "preserve"),
    ("provision_failed", ("provisioning",), "provision_failed", "preserve"),
    ("open", ("closed",), "opening", "running"),
    ("open_succeeded", ("opening",), "running", "preserve"),
    ("open_failed", ("opening",), "degraded", "preserve"),
    (
        "participant_or_host_fault",
        ("opening", "running"),
        "degraded",
        "preserve",
    ),
    ("repair", ("provision_failed", "degraded"), "repairing", "preserve"),
    ("repair_to_closed", ("repairing",), "closed", "preserve"),
    ("repair_to_running", ("repairing",), "running", "preserve"),
    ("repair_to_destroying", ("repairing",), "destroying", "preserve"),
    ("repair_failed", ("repairing",), "degraded", "preserve"),
    ("close", ("opening", "running", "degraded"), "closing", "closed"),
    ("close_succeeded", ("closing",), "closed", "preserve"),
    ("close_failed", ("closing",), "degraded", "preserve"),
    ("destroy", ("closed",), "destroying", "destroyed"),
    ("destroy_aborted_no_effect", ("destroying",), "closed", "closed"),
    ("destroy_failed", ("destroying",), "degraded", "preserve"),
    ("destroy_succeeded", ("destroying",), "absent", "record_absent"),
)
PARTICIPANT_TRANSITIONS = (
    ("add", ("absent",), "stopped", "stopped"),
    ("start", ("stopped",), "starting", "running"),
    ("ready", ("starting",), "ready", "preserve"),
    ("start_failed", ("starting",), "degraded", "preserve"),
    ("stop", ("ready", "degraded"), "stopping", "stopped"),
    ("detach", ("ready", "degraded"), "stopping", "detached"),
    ("stop_succeeded", ("stopping",), "stopped", "preserve"),
    ("stop_failed", ("stopping",), "degraded", "preserve"),
    ("recover", ("degraded",), "recovering", "stopped"),
    ("recover_succeeded", ("recovering",), "stopped", "preserve"),
    ("recover_failed", ("recovering",), "degraded", "preserve"),
    ("detach_stopped", ("stopped",), "detached", "detached"),
    (
        "detach_cleanup_succeeded",
        ("stopping", "degraded"),
        "detached",
        "preserve",
    ),
    (
        "replace",
        ("stopped", "ready", "degraded"),
        "replacing",
        "operation_desired",
    ),
    ("replace_to_stopped", ("replacing",), "stopped", "preserve"),
    ("replace_to_starting", ("replacing",), "starting", "preserve"),
    ("replace_failed_after_cas", ("replacing",), "degraded", "preserve"),
    ("destroy", ("stopped",), "destroying", "destroyed"),
    ("destroy_succeeded", ("destroying",), "absent", "record_absent"),
)
SCENARIO_OPERATION_DESIRED = {
    "scenario.create": {"closed"},
    "scenario.open": {"running"},
    "scenario.close": {"closed"},
    "scenario.repair": {"closed", "running", "destroyed"},
    "scenario.destroy": {"destroyed"},
}
PARTICIPANT_OPERATION_DESIRED = {
    "participant.add": {"stopped"},
    "participant.start": {"running"},
    "participant.stop": {"stopped"},
    "participant.recover": {"stopped"},
    "participant.replace": {"stopped", "running"},
    "participant.detach": {"detached"},
    "participant.destroy": {"destroyed"},
}
EXPECTED_OPERATION_PROTOCOL = {
    "phases": [
        "validate_request_and_capability",
        "cas_desired_state_with_generation_and_revision_fence",
        "release_lock_and_execute_external_action",
        "reacquire_lock_and_finalize_if_fence_matches",
    ],
    "request_id_is_idempotency_key": True,
    "plan_is_immutable_and_digest_bound": True,
    "external_action_never_holds_state_lock": True,
    "callback_requires_exact_operation_resulting_generation_and_committed_revision": True,
    "stale_callback_is_journaled_and_rejected": True,
    "proven_no_external_effect_destroy_may_abort_to_closed": True,
    "external_outcome_unknown_policy": {
        "source": "user_decision",
        "decision_date": "2026-08-20",
        "decision_summary": (
            "Durably joinable unknown outcomes remain transitional for at most "
            "three exact joins; missing proof or exhaustion requires degraded repair."
        ),
        "journal_intent_before_external_effect": True,
        "durably_joinable_unknown_remains_transitional": True,
        "join_requires": [
            "exact_operation_identity",
            "fence_verified_at_join",
            "adapter_idempotent_join_declared",
            "ownership_reproven_at_join",
            "no_concurrent_conflicting_operation",
        ],
        "join_attempts": {
            "max_attempts": 3,
            "persisted_before_each_attempt": True,
            "exhaustion_requires_degraded": True,
        },
        "unjoinable_unknown_requires_degraded": True,
        "forbid_completion_claim_without_evidence": True,
    },
}
EXPECTED_REPLACE_PROTOCOL = {
    "validate_new_launch_spec_before_old_binding_mutation": True,
    "pre_cas_failure_preserves_old_generation_and_binding": True,
    "post_cas_failure_is_degraded_and_repairable": True,
    "new_generation_is_exactly_previous_plus_one": True,
    "prior_desired_running_restarts_new_generation": True,
    "history_is_never_rewritten": True,
}
EXPECTED_RECOVER_PROTOCOL = {
    "source": "user_decision",
    "decision_date": "2026-08-14",
    "exact_degraded_generation_is_fenced": True,
    "cleanup_or_absence_proof_precedes_rotation": True,
    "ambiguous_external_resources_fail_closed": True,
    "new_generation_is_exactly_previous_plus_one": True,
    "failed_generation_history_is_retained": True,
    "recovery_finishes_stopped_before_explicit_restart": True,
}
EXPECTED_DETACH_PROTOCOL = {
    "desired_detached_committed_before_cleanup": True,
    "new_delivery_disabled_after_desired_commit": True,
    "cleanup_pending_is_degraded_not_deleted": True,
    "stop_detach_and_repair_remain_retryable": True,
    "history_retained_until_separate_gated_destroy": True,
}
EXPECTED_DESTROY_PROTOCOL = {
    "source": "user_decision",
    "decision_date": "2026-08-20",
    "stopped_only": True,
    "live_bindings_and_unreleased_resources_forbidden": True,
    "inbound_nonconsumed_deliveries_become_recipient_deleted": True,
    "destroyed_generation_history_is_retained": True,
    "same_name_readd_uses_a_fresh_generation": True,
    "workspace_and_canonical_source_are_untouched": True,
}
EXPECTED_INVARIANTS = {
    "participant_identity_is_scenario_and_participant_id",
    "desired_and_observed_state_are_distinct",
    "generation_and_state_revision_are_distinct",
    "operation_fence_is_immutable_precondition_snapshot",
    "desired_commit_revision_is_callback_finalize_fence",
    "stable_callbacks_require_exact_resulting_generation_and_committed_revision",
    "absent_target_uses_null_generation_and_revision_fence",
    "ready_requires_exact_runtime_and_optional_presentation_binding",
    "tui_ready_requires_presentation_binding",
    "headless_forbids_presentation_binding",
    "detached_and_stopped_have_no_live_binding",
    "degraded_state_preserves_owned_resource_evidence",
    "recover_never_reuses_a_failed_generation",
    "one_participant_fault_does_not_rebind_other_participants",
    "scenario_degraded_is_an_aggregate_projection",
    "unregistered_is_absence_not_a_persisted_record",
    "destroy_removes_current_state_but_retains_audit_history",
    "no_operation_changes_model_binding_in_place",
    "operation_errors_use_non_reserved_driver_or_lifecycle_namespace",
}
EXPECTED_DEFERRED = {
    "collaboration-policy-routing",
    "workspace-environment-operation-payloads",
    "permission-and-high-risk-confirmation-matrix",
    "host-storage-and-transaction-implementation",
    "phase4-force-stop-and-destroy-mechanics",
    "host-ipc-operation-registry-population",
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
RESERVED_ERROR_NAMESPACES = {
    "ipc",
    "identity",
    "target",
    "auth",
    "fence",
    "availability",
    "operation",
}
TRANSITIONAL_SCENARIO_STATES = {
    "provisioning",
    "opening",
    "repairing",
    "closing",
    "destroying",
}
TRANSITIONAL_PARTICIPANT_STATES = {
    "starting",
    "stopping",
    "recovering",
    "replacing",
}


class StateContractError(ValueError):
    """The state contract or one state value is invalid."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StateContractError(f"JSON object contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json(text: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    except StateContractError:
        raise
    except json.JSONDecodeError as exc:
        raise StateContractError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise StateContractError(f"{label} root must be an object")
    return value


def _read_regular_artifact(root: Path, relative_path: str) -> tuple[Path, str]:
    path = root / relative_path
    try:
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
            raise StateContractError(f"{relative_path} must be a regular project artifact")
        if path.stat().st_size > MAX_CONTRACT_BYTES:
            raise StateContractError(f"{relative_path} exceeds the size limit")
        return path, path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StateContractError(f"{relative_path} is unavailable") from exc


def _resolve_ref(contract: Mapping[str, Any], ref: Any) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        raise StateContractError("only local $defs references are allowed")
    name = ref.removeprefix("#/$defs/")
    if "/" in name:
        raise StateContractError("nested or escaped schema references are unsupported")
    schema = contract.get("$defs", {}).get(name)
    if not isinstance(schema, dict):
        raise StateContractError(f"schema reference is unresolved: {ref}")
    return schema


def _validate_schema_node(
    schema: Any, *, contract: Mapping[str, Any], path: str
) -> None:
    if not isinstance(schema, dict):
        raise StateContractError(f"{path} must be a schema object")
    unknown = set(schema) - ALLOWED_SCHEMA_KEYWORDS
    if unknown:
        raise StateContractError(
            f"{path} uses unsupported schema keywords: {sorted(unknown)}"
        )
    if "$ref" in schema:
        if set(schema) != {"$ref"}:
            raise StateContractError(f"{path} mixes $ref with sibling keywords")
        _resolve_ref(contract, schema["$ref"])
        return
    if "oneOf" in schema:
        variants = schema["oneOf"]
        if not isinstance(variants, list) or len(variants) < 2:
            raise StateContractError(f"{path}.oneOf must contain at least two variants")
        for index, variant in enumerate(variants):
            _validate_schema_node(
                variant, contract=contract, path=f"{path}.oneOf[{index}]"
            )
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in {
        "object",
        "array",
        "string",
        "integer",
        "boolean",
        "null",
    }:
        raise StateContractError(f"{path}.type is unsupported")
    keyword_types = {
        "properties": "object",
        "required": "object",
        "additionalProperties": "object",
        "items": "array",
        "uniqueItems": "array",
        "pattern": "string",
        "minimum": "integer",
    }
    for keyword, required_type in keyword_types.items():
        if keyword in schema and schema_type != required_type:
            raise StateContractError(
                f"{path}.{keyword} requires schema type {required_type}"
            )
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise StateContractError(f"{path}.properties must be an object")
        if schema.get("additionalProperties") is not False:
            raise StateContractError(f"{path} must reject unknown fields")
        required = schema.get("required")
        if not isinstance(required, list) or len(required) != len(set(required)):
            raise StateContractError(f"{path}.required must be a unique list")
        if set(required) != set(properties):
            raise StateContractError(f"{path} must require every declared field")
        for key, child in properties.items():
            _validate_schema_node(
                child, contract=contract, path=f"{path}.properties.{key}"
            )
    if schema_type == "array":
        if "items" not in schema:
            raise StateContractError(f"{path} array has no item schema")
        _validate_schema_node(schema["items"], contract=contract, path=f"{path}.items")
    if "enum" in schema:
        encoded = [json.dumps(item, sort_keys=True) for item in schema["enum"]]
        if not encoded or len(encoded) != len(set(encoded)):
            raise StateContractError(f"{path}.enum must be non-empty and unique")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            raise StateContractError(f"{path}.pattern is invalid") from exc


def _matches_schema(
    value: Any, schema: Mapping[str, Any], *, contract: Mapping[str, Any], path: str
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
        matched = 0
        for variant in schema["oneOf"]:
            try:
                _matches_schema(value, variant, contract=contract, path=path)
            except StateContractError:
                continue
            matched += 1
        if matched != 1:
            raise StateContractError(f"{path} must match exactly one schema variant")
    if "const" in schema and value != schema["const"]:
        raise StateContractError(f"{path} does not match its constant value")
    if "enum" in schema and value not in schema["enum"]:
        raise StateContractError(f"{path} is not in the allowed enum")
    schema_type = schema.get("type")
    type_matches = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if schema_type is not None and not type_matches[schema_type](value):
        raise StateContractError(f"{path} has the wrong type")
    if schema_type == "object":
        properties = schema["properties"]
        missing = set(schema["required"]) - set(value)
        if missing:
            raise StateContractError(f"{path} is missing fields: {sorted(missing)}")
        unknown = set(value) - set(properties)
        if unknown:
            raise StateContractError(f"{path} has unknown fields: {sorted(unknown)}")
        for key, child_value in value.items():
            _matches_schema(
                child_value,
                properties[key],
                contract=contract,
                path=f"{path}.{key}",
            )
    if schema_type == "array":
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise StateContractError(f"{path} items must be unique")
        for index, item in enumerate(value):
            _matches_schema(
                item,
                schema["items"],
                contract=contract,
                path=f"{path}[{index}]",
            )
    if schema_type == "string" and "pattern" in schema:
        if re.fullmatch(schema["pattern"], value) is None:
            raise StateContractError(f"{path} does not match its pattern")
    if schema_type == "integer" and value < schema.get("minimum", value):
        raise StateContractError(f"{path} is below its minimum")


def _transition_rows(
    rows: Any, *, label: str
) -> tuple[tuple[str, tuple[str, ...], str, str], ...]:
    if not isinstance(rows, list):
        raise StateContractError(f"{label} must be a list")
    parsed: list[tuple[str, tuple[str, ...], str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "trigger",
            "from",
            "to",
            "desired_after",
        }:
            raise StateContractError(f"{label} row fields do not match")
        if (
            not isinstance(row["trigger"], str)
            or not isinstance(row["to"], str)
            or not isinstance(row["desired_after"], str)
        ):
            raise StateContractError(f"{label} row contains a non-string")
        sources = row["from"]
        if (
            not isinstance(sources, list)
            or not sources
            or not all(isinstance(item, str) for item in sources)
            or len(sources) != len(set(sources))
        ):
            raise StateContractError(f"{label} source set is invalid")
        parsed.append(
            (row["trigger"], tuple(sources), row["to"], row["desired_after"])
        )
    return tuple(parsed)


def validate_contract(*, repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the tracked contract and its participant-driver dependency."""

    root = repo_root.resolve()
    contract_path, text = _read_regular_artifact(root, CONTRACT_RELATIVE_PATH)
    driver_path, driver_text = _read_regular_artifact(
        root, DRIVER_CONTRACT_RELATIVE_PATH
    )
    contract = _load_json(text, label="state contract")
    driver_contract = _load_json(driver_text, label="driver contract")
    if set(contract) != TOP_LEVEL_FIELDS:
        raise StateContractError("state contract top-level fields do not match")
    if contract["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise StateContractError("state contract schema dialect is incompatible")
    if contract["$id"] != "urn:ai-collab:scenario-participant-state:v1":
        raise StateContractError("state contract identity is incompatible")
    definitions = contract.get("$defs")
    if not isinstance(definitions, dict) or set(definitions) != EXPECTED_DEFS:
        raise StateContractError("state contract definition set does not match")
    expected_root = [
        {"$ref": f"#/$defs/{name}"} for name in ROOT_ARTIFACT_DEFS
    ]
    if contract["oneOf"] != expected_root:
        raise StateContractError("state contract root artifacts do not match")
    for name, schema in definitions.items():
        _validate_schema_node(schema, contract=contract, path=f"$defs.{name}")

    metadata = contract.get("x-ai-collab")
    expected_metadata_fields = {
        "schema_version",
        "contract_id",
        "product_contract_version",
        "driver_contract",
        "scenario_transitions",
        "participant_transitions",
        "operation_protocol",
        "replace_protocol",
        "recover_protocol",
        "detach_protocol",
        "destroy_protocol",
        "invariants",
        "deferred_surfaces",
    }
    if not isinstance(metadata, dict) or set(metadata) != expected_metadata_fields:
        raise StateContractError("state contract metadata fields do not match")
    if metadata["schema_version"] != 1 or isinstance(
        metadata["schema_version"], bool
    ):
        raise StateContractError("state metadata schema version is incompatible")
    if metadata["contract_id"] != "ai-collab-scenario-participant-state-v1":
        raise StateContractError("state metadata contract id is incompatible")
    if metadata["product_contract_version"] != "1.0":
        raise StateContractError("state product contract version is incompatible")
    expected_driver = {
        "contract_id": "ai-collab-participant-drivers-v2",
        "participant_driver_contract_version": 2,
        "runtime_binding_join_field": "runtime_binding_id",
        "launch_spec_reference": "sha256-canonical-json",
    }
    if metadata["driver_contract"] != expected_driver:
        raise StateContractError("state driver dependency metadata is incompatible")
    driver_metadata = driver_contract.get("x-ai-collab", {})
    if (
        driver_metadata.get("contract_id") != expected_driver["contract_id"]
        or driver_metadata.get("participant_driver_contract_version") != 2
        or "runtime_binding_id"
        not in driver_contract.get("$defs", {})
        .get("runtime_process_binding", {})
        .get("properties", {})
    ):
        raise StateContractError("tracked participant driver contract is incompatible")
    if _transition_rows(
        metadata["scenario_transitions"], label="scenario transitions"
    ) != SCENARIO_TRANSITIONS:
        raise StateContractError("scenario transition table is incompatible")
    if _transition_rows(
        metadata["participant_transitions"], label="participant transitions"
    ) != PARTICIPANT_TRANSITIONS:
        raise StateContractError("participant transition table is incompatible")
    if metadata["operation_protocol"] != EXPECTED_OPERATION_PROTOCOL:
        raise StateContractError("operation protocol is incompatible")
    if metadata["replace_protocol"] != EXPECTED_REPLACE_PROTOCOL:
        raise StateContractError("replace protocol is incompatible")
    if metadata["recover_protocol"] != EXPECTED_RECOVER_PROTOCOL:
        raise StateContractError("recover protocol is incompatible")
    if metadata["detach_protocol"] != EXPECTED_DETACH_PROTOCOL:
        raise StateContractError("detach protocol is incompatible")
    if metadata["destroy_protocol"] != EXPECTED_DESTROY_PROTOCOL:
        raise StateContractError("destroy protocol is incompatible")
    invariants = metadata["invariants"]
    if not isinstance(invariants, dict) or set(invariants) != EXPECTED_INVARIANTS:
        raise StateContractError("state invariant set does not match")
    if any(value is not True for value in invariants.values()):
        raise StateContractError("every state invariant must be active")
    deferred = metadata["deferred_surfaces"]
    if (
        not isinstance(deferred, list)
        or set(deferred) != EXPECTED_DEFERRED
        or len(deferred) != len(EXPECTED_DEFERRED)
    ):
        raise StateContractError("deferred state surfaces do not match")

    result = {
        "schema_version": 1,
        "validation_id": VALIDATION_ID,
        "status": "valid",
        "contract_relative_path": CONTRACT_RELATIVE_PATH,
        "contract_digest": canonical_json_sha256(contract),
        "raw_sha256": sha256_file(contract_path),
        "contract_id": metadata["contract_id"],
        "product_contract_version": metadata["product_contract_version"],
        "driver_contract_digest": canonical_json_sha256(driver_contract),
        "scenario_transition_count": len(SCENARIO_TRANSITIONS),
        "participant_transition_count": len(PARTICIPANT_TRANSITIONS),
        "lifecycle_operation_count": len(SCENARIO_OPERATION_DESIRED)
        + len(PARTICIPANT_OPERATION_DESIRED),
        "state_mutated": False,
    }
    return contract, result


def validate_scenario_record(record: Any, *, contract: Mapping[str, Any]) -> None:
    _matches_schema(
        record,
        _resolve_ref(contract, "#/$defs/scenario_record"),
        contract=contract,
        path="scenario_record",
    )
    desired = record["desired_state"]
    observed = record["observed_state"]
    if record["state_revision"] < 1:
        raise StateContractError("persisted scenario state revision is not positive")
    allowed_desired = {
        "provisioning": {"closed"},
        "provision_failed": {"closed"},
        "closed": {"closed"},
        "opening": {"running"},
        "running": {"running"},
        "degraded": {"closed", "running", "destroyed"},
        "repairing": {"closed", "running", "destroyed"},
        "closing": {"closed"},
        "destroying": {"destroyed"},
    }
    if desired not in allowed_desired[observed]:
        raise StateContractError("scenario desired/observed state combination is invalid")
    degraded_required = observed in {"degraded", "provision_failed"}
    if degraded_required != (record["degraded"] is not None):
        raise StateContractError("scenario degraded observation is inconsistent")
    if observed in TRANSITIONAL_SCENARIO_STATES and record["active_operation_id"] is None:
        raise StateContractError("transitional scenario lacks active operation")
    if observed not in {"provisioning", "provision_failed"} and record[
        "workspace_binding_id"
    ] is None:
        raise StateContractError("provisioned scenario lacks workspace binding")


def validate_participant_record(record: Any, *, contract: Mapping[str, Any]) -> None:
    _matches_schema(
        record,
        _resolve_ref(contract, "#/$defs/participant_record"),
        contract=contract,
        path="participant_record",
    )
    desired = record["desired_state"]
    observed = record["observed_state"]
    if record["state_revision"] < 1:
        raise StateContractError("persisted participant state revision is not positive")
    allowed_desired = {
        "detached": {"detached"},
        "stopped": {"stopped"},
        "starting": {"running"},
        "ready": {"running"},
        "stopping": {"stopped", "detached"},
        "recovering": {"stopped"},
        "replacing": {"stopped", "running"},
        "degraded": {"detached", "stopped", "running"},
    }
    if desired not in allowed_desired[observed]:
        raise StateContractError(
            "participant desired/observed state combination is invalid"
        )
    if (observed == "degraded") != (record["degraded"] is not None):
        raise StateContractError("participant degraded observation is inconsistent")
    if observed in TRANSITIONAL_PARTICIPANT_STATES and record[
        "active_operation_id"
    ] is None:
        raise StateContractError("transitional participant lacks active operation")
    if observed in {"detached", "stopped"} and (
        record["runtime_binding_id"] is not None
        or record["presentation_binding_id"] is not None
    ):
        raise StateContractError("inactive participant retains a live binding")
    if observed == "replacing" and (
        record["runtime_binding_id"] is not None
        or record["presentation_binding_id"] is not None
    ):
        raise StateContractError("replacing participant retains the old live binding")
    if observed == "ready":
        if record["runtime_binding_id"] is None:
            raise StateContractError("ready participant lacks runtime binding")
        if record["interaction_mode"] == "tui" and record[
            "presentation_binding_id"
        ] is None:
            raise StateContractError("ready TUI participant lacks presentation binding")
        if record["interaction_mode"] == "headless" and record[
            "presentation_binding_id"
        ] is not None:
            raise StateContractError("headless participant has presentation binding")
    if record["interaction_mode"] == "headless" and record[
        "presentation_binding_id"
    ] is not None:
        raise StateContractError("headless participant has presentation binding")
    if observed != "detached" and record["launch_spec_digest"] is None:
        raise StateContractError("attached participant lacks launch spec digest")
    degraded = record["degraded"]
    if degraded is not None:
        if degraded["reason"] == "cleanup_pending" and not degraded["cleanup_pending"]:
            raise StateContractError("cleanup-pending degradation lacks cleanup flag")
        if desired == "detached" and not degraded["cleanup_pending"]:
            raise StateContractError("degraded detach is not marked cleanup pending")


def _validate_error_namespace(value: str | None) -> None:
    if value is not None and value.split(".", 1)[0] in RESERVED_ERROR_NAMESPACES:
        raise StateContractError("lifecycle error code uses a reserved IPC namespace")


def validate_lifecycle_operation(
    operation: Any, *, contract: Mapping[str, Any]
) -> None:
    _matches_schema(
        operation,
        _resolve_ref(contract, "#/$defs/lifecycle_operation"),
        contract=contract,
        path="lifecycle_operation",
    )
    kind = operation["operation_kind"]
    target = operation["target"]
    fence = operation["fence"]
    scenario_fence_is_absent = (
        fence["scenario_generation"] is None
        and fence["scenario_state_revision"] is None
    )
    scenario_fence_is_concrete = (
        fence["scenario_generation"] is not None
        and fence["scenario_state_revision"] is not None
    )
    if kind in SCENARIO_OPERATION_DESIRED:
        if (
            target["scope"] != "scenario"
            or fence["participant_generation"] is not None
            or fence["participant_state_revision"] is not None
        ):
            raise StateContractError("scenario operation target or fence is invalid")
        if kind == "scenario.create":
            if not scenario_fence_is_absent:
                raise StateContractError(
                    "scenario create does not fence the expected absent record"
                )
        elif not scenario_fence_is_concrete:
            raise StateContractError(
                "scenario operation lacks generation or state revision fence"
            )
        desired = SCENARIO_OPERATION_DESIRED[kind]
    else:
        if target["scope"] != "participant":
            raise StateContractError("participant operation target is invalid")
        if not scenario_fence_is_concrete:
            raise StateContractError(
                "participant operation lacks scenario generation or state revision fence"
            )
        if kind == "participant.add":
            if (
                fence["participant_generation"] is not None
                or fence["participant_state_revision"] is not None
            ):
                raise StateContractError(
                    "participant add fences a nonexistent participant record"
                )
        elif (
            fence["participant_generation"] is None
            or fence["participant_state_revision"] is None
        ):
            raise StateContractError(
                "participant operation lacks generation or state revision fence"
            )
        desired = PARTICIPANT_OPERATION_DESIRED[kind]
    if operation["desired_state_after"] not in desired:
        raise StateContractError("lifecycle operation desired state does not match kind")
    replacement = operation["replacement_launch_spec_digest"]
    if (kind == "participant.replace") != (replacement is not None):
        raise StateContractError("replace launch spec digest is inconsistent")
    continuity = operation["requested_continuity_mode"]
    continuity_operations = {"participant.start", "participant.replace"}
    if continuity is not None and kind not in continuity_operations:
        raise StateContractError("continuity mode is attached to an invalid operation")
    continuity_required = kind == "participant.start" or (
        kind == "participant.replace" and operation["desired_state_after"] == "running"
    )
    if continuity_required and continuity is None:
        raise StateContractError("runtime-starting operation lacks continuity mode")
    if (
        kind == "participant.replace"
        and operation["desired_state_after"] == "stopped"
        and continuity is not None
    ):
        raise StateContractError("stopped replacement carries an unused continuity mode")
    resulting_scenario = operation["resulting_scenario_generation"]
    expected_scenario = fence["scenario_generation"]
    if kind == "scenario.create":
        if resulting_scenario not in {None, 1}:
            raise StateContractError("scenario create does not create generation one")
    elif resulting_scenario not in {None, expected_scenario}:
        raise StateContractError("lifecycle operation changed scenario generation")
    resulting_participant = operation["resulting_participant_generation"]
    expected_participant = fence["participant_generation"]
    if (
        kind in {"participant.replace", "participant.recover"}
        and resulting_participant is not None
    ):
        if resulting_participant != expected_participant + 1:
            raise StateContractError(
                "participant rotation generation is not previous plus one"
            )
    if kind == "participant.add" and (
        resulting_participant is not None
        and (
            not isinstance(resulting_participant, int)
            or isinstance(resulting_participant, bool)
            or resulting_participant < 1
        )
    ):
        raise StateContractError("participant add generation is invalid")
    if kind == "participant.destroy" and resulting_participant is not None:
        raise StateContractError("participant destroy reports a retained generation")
    if kind in set(PARTICIPANT_OPERATION_DESIRED) - {
        "participant.add",
        "participant.replace",
        "participant.recover",
        "participant.destroy",
    } and resulting_participant not in {None, expected_participant}:
        raise StateContractError("participant operation changed participant generation")
    if kind.startswith("scenario.") and resulting_participant is not None:
        raise StateContractError("scenario operation reports participant generation")
    if operation["created_sequence"] > operation["last_journal_sequence"]:
        raise StateContractError("operation journal sequence moved backwards")
    state = operation["state"]
    mutation = operation["mutation_state"]
    if state == "planned" and mutation != "not_started":
        raise StateContractError("planned operation reports mutation")
    if state in {"desired_committed", "executing_external", "finalizing", "succeeded"} and mutation != "committed":
        raise StateContractError("committed operation phase lacks committed mutation")
    if mutation == "unknown" and state != "repair_required":
        raise StateContractError("unknown mutation outcome is not repair-required")
    failure_code = operation["failure_code"]
    if (state in {"failed", "repair_required"}) != (failure_code is not None):
        raise StateContractError("operation failure code is inconsistent")
    _validate_error_namespace(failure_code)


def validate_journal_entry(entry: Any, *, contract: Mapping[str, Any]) -> None:
    _matches_schema(
        entry,
        _resolve_ref(contract, "#/$defs/operation_journal_entry"),
        contract=contract,
        path="operation_journal_entry",
    )
    before = entry["target_state_revision_before"]
    after = entry["target_state_revision_after"]
    event = entry["event"]
    committing = event in {"desired_state_committed", "finalize_committed"}
    if committing:
        if after != before + 1 or entry["mutation_state"] != "committed":
            raise StateContractError("committing journal event lacks exact CAS revision")
    elif after != before:
        raise StateContractError("non-committing journal event changed state revision")
    error_code = entry["error_code"]
    if (event in {"external_failed", "repair_required"}) != (
        error_code is not None
    ):
        raise StateContractError("journal error code is inconsistent")
    if event == "stale_callback_rejected" and entry["mutation_state"] not in {
        "not_started",
        "not_committed",
    }:
        raise StateContractError("stale callback reports committed mutation")
    _validate_error_namespace(error_code)


def validate_operation_journal(
    operation: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
) -> None:
    validate_lifecycle_operation(operation, contract=contract)
    if not entries:
        raise StateContractError("operation journal is empty")
    previous_sequence = 0
    fence = operation["fence"]
    expected_target_revision = (
        fence["scenario_state_revision"]
        if operation["target"]["scope"] == "scenario"
        else fence["participant_state_revision"]
    )
    if expected_target_revision is None:
        expected_target_revision = 0
    desired_commit_seen = False
    external_started_seen = False
    external_succeeded_seen = False
    events: list[str] = []
    for index, entry in enumerate(entries):
        validate_journal_entry(entry, contract=contract)
        if entry["sequence"] <= previous_sequence:
            raise StateContractError("operation journal sequence is not monotonic")
        previous_sequence = entry["sequence"]
        event = entry["event"]
        events.append(event)
        if index == 0 and event != "planned":
            raise StateContractError("operation journal does not begin with planned")
        if index > 0 and event == "planned":
            raise StateContractError("operation journal repeats planned")
        if event == "external_started":
            if not desired_commit_seen:
                raise StateContractError("external action started before desired-state commit")
            external_started_seen = True
        if event in {"external_succeeded", "external_failed"}:
            if not external_started_seen:
                raise StateContractError("external outcome lacks external-start evidence")
            external_succeeded_seen = event == "external_succeeded"
        if event == "finalize_committed" and not desired_commit_seen:
            raise StateContractError("operation finalized before desired-state commit")
        if entry["operation_id"] != operation["operation_id"]:
            raise StateContractError("journal operation identity mismatch")
        if entry["operation_generation"] != operation["operation_generation"]:
            raise StateContractError("journal operation generation mismatch")
        if entry["target"] != operation["target"]:
            raise StateContractError("journal operation target mismatch")
        if entry["fence"] != operation["fence"]:
            raise StateContractError("journal operation fence mismatch")
        if entry["target_state_revision_before"] != expected_target_revision:
            raise StateContractError("operation journal target revision chain is broken")
        expected_target_revision = entry["target_state_revision_after"]
        if event == "desired_state_committed":
            if desired_commit_seen:
                raise StateContractError("operation journal repeats desired-state commit")
            desired_commit_seen = True
    if entries[0]["sequence"] != operation["created_sequence"]:
        raise StateContractError("journal does not start at operation creation")
    if entries[-1]["sequence"] != operation["last_journal_sequence"]:
        raise StateContractError("journal head does not match operation")
    mutation = operation["mutation_state"]
    if mutation == "committed" and not desired_commit_seen:
        raise StateContractError("committed operation journal lacks desired-state commit")
    if mutation in {"not_started", "not_committed"} and desired_commit_seen:
        raise StateContractError("uncommitted operation journal contains desired-state commit")
    state = operation["state"]
    if state == "executing_external" and not external_started_seen:
        raise StateContractError("executing operation lacks external-start evidence")
    if state == "finalizing" and not external_succeeded_seen:
        raise StateContractError("finalizing operation lacks external-success evidence")
    if state == "succeeded" and "finalize_committed" not in events:
        raise StateContractError("succeeded operation lacks finalize commit")
    if state == "repair_required" and "repair_required" not in events:
        raise StateContractError("repair-required operation lacks journal evidence")


def _transition_allowed(
    trigger: str,
    before: str,
    after: str,
    transitions: Sequence[tuple[str, tuple[str, ...], str, str]],
) -> bool:
    return any(
        trigger == row_trigger and before in sources and after == target
        for row_trigger, sources, target, _ in transitions
    )


def _validate_transition_desired_state(
    *,
    trigger: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    transitions: Sequence[tuple[str, tuple[str, ...], str, str]],
) -> None:
    desired_rule = next(
        desired_after
        for row_trigger, _, _, desired_after in transitions
        if row_trigger == trigger
    )
    if desired_rule == "record_absent":
        if after is not None:
            raise StateContractError("record-absence transition retained desired state")
    elif desired_rule == "preserve":
        if before is None or after is None or before["desired_state"] != after["desired_state"]:
            raise StateContractError("transition changed desired state instead of preserving it")
    elif desired_rule == "operation_desired":
        if after is None:
            raise StateContractError("operation transition removed its desired state")
    elif after is None or after["desired_state"] != desired_rule:
        raise StateContractError("transition desired state does not match its trigger")


def validate_scenario_transition(
    *,
    trigger: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
) -> None:
    before_state = "absent" if before is None else before["observed_state"]
    after_state = "absent" if after is None else after["observed_state"]
    if not _transition_allowed(trigger, before_state, after_state, SCENARIO_TRANSITIONS):
        raise StateContractError("scenario transition is not allowed")
    if before is not None:
        validate_scenario_record(before, contract=contract)
    if after is not None:
        validate_scenario_record(after, contract=contract)
    _validate_transition_desired_state(
        trigger=trigger,
        before=before,
        after=after,
        transitions=SCENARIO_TRANSITIONS,
    )
    if before is None:
        if (
            after is None
            or after["scenario_generation"] != 1
            or after["state_revision"] != 1
        ):
            raise StateContractError(
                "scenario create does not establish generation and revision one"
            )
        return
    if after is None:
        return
    if before["scenario_id"] != after["scenario_id"]:
        raise StateContractError("scenario identity changed across transition")
    if after["state_revision"] != before["state_revision"] + 1:
        raise StateContractError("scenario transition lacks exact revision increment")
    if after["scenario_generation"] != before["scenario_generation"]:
        raise StateContractError("scenario generation changed outside create")


def validate_participant_transition(
    *,
    trigger: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
) -> None:
    before_state = "absent" if before is None else before["observed_state"]
    after_state = "absent" if after is None else after["observed_state"]
    if not _transition_allowed(
        trigger,
        before_state,
        after_state,
        PARTICIPANT_TRANSITIONS,
    ):
        raise StateContractError("participant transition is not allowed")
    if before is not None:
        validate_participant_record(before, contract=contract)
    if after is not None:
        validate_participant_record(after, contract=contract)
    _validate_transition_desired_state(
        trigger=trigger,
        before=before,
        after=after,
        transitions=PARTICIPANT_TRANSITIONS,
    )
    if before is None:
        if (
            after is None
            or after["participant_generation"] != 1
            or after["state_revision"] != 1
        ):
            raise StateContractError(
                "participant add does not establish generation and revision one"
            )
        return
    if after is None:
        return
    for field in ("scenario_id", "participant_id"):
        if before[field] != after[field]:
            raise StateContractError(f"participant {field} changed across transition")
    if after["state_revision"] != before["state_revision"] + 1:
        raise StateContractError("participant transition lacks exact revision increment")
    expected_generation = (
        before["participant_generation"] + 1
        if trigger in {"replace", "recover_succeeded"}
        else before["participant_generation"]
    )
    if after["participant_generation"] != expected_generation:
        raise StateContractError(
            "participant generation changed outside exact rotation increment"
        )


def validate_scenario_aggregate(
    scenario: Mapping[str, Any],
    participants: Sequence[Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
) -> None:
    validate_scenario_record(scenario, contract=contract)
    participant_ids: set[str] = set()
    any_degraded = False
    any_nonready_desired = False
    for participant in participants:
        validate_participant_record(participant, contract=contract)
        if participant["scenario_id"] != scenario["scenario_id"]:
            raise StateContractError("aggregate contains a cross-scenario participant")
        participant_id = participant["participant_id"]
        if participant_id in participant_ids:
            raise StateContractError("aggregate contains duplicate participant identity")
        participant_ids.add(participant_id)
        any_degraded = any_degraded or participant["observed_state"] == "degraded"
        any_nonready_desired = any_nonready_desired or (
            participant["desired_state"] == "running"
            and participant["observed_state"] != "ready"
        )
    if set(scenario["participant_ids"]) != participant_ids:
        raise StateContractError("scenario participant set does not match records")
    if any_degraded and scenario["observed_state"] not in {
        "degraded",
        "repairing",
        "closing",
        "destroying",
    }:
        raise StateContractError("participant fault is missing from scenario projection")
    if scenario["observed_state"] == "running" and any_nonready_desired:
        raise StateContractError("running scenario has a non-ready desired participant")


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
        _, result = validate_contract(repo_root=args.repo_root)
    except (StateContractError, OSError, ValueError) as exc:
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
