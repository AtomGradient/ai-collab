#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司
"""Validate the vendor-neutral Phase 0 participant driver contract suite."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_collab_bootstrap_evidence import canonical_json_sha256, sha256_file


VALIDATION_ID = "PHASE0-PARTICIPANT-DRIVER-CONTRACT-CONFORMANCE"
CONTRACT_RELATIVE_PATH = (
    "contracts/participant_drivers_v2.schema.json"
)
MAX_CONTRACT_BYTES = 512 * 1024
TOP_LEVEL_FIELDS = {"$schema", "$id", "title", "oneOf", "$defs", "x-ai-collab"}
ROOT_ARTIFACT_DEFS = (
    "driver_registry",
    "runtime_driver_descriptor",
    "presentation_driver_descriptor",
    "runtime_launch_spec",
    "runtime_ready_ack",
    "presentation_create_ack",
)
EXPECTED_DEFS = {
    "opaque_id",
    "namespaced_id",
    "sha256",
    "generation",
    "nullable_namespaced_id",
    "nullable_opaque_id",
    "nullable_sha256",
    "model_binding",
    "nullable_model_binding",
    "runtime_driver_descriptor",
    "presentation_driver_descriptor",
    "runtime_launch_spec",
    "driver_call_context",
    "runtime_create_request",
    "prepared_runtime_launch",
    "runtime_process_binding",
    "runtime_ready_ack",
    "runtime_binding_request",
    "runtime_health_observation",
    "runtime_delivery_ack",
    "runtime_session_drift_signal",
    "runtime_repair_request",
    "geometry",
    "nullable_geometry",
    "presentation_create_request",
    "presentation_binding",
    "presentation_create_ack",
    "presentation_action_request",
    "presentation_health_observation",
    "driver_registry",
}
RUNTIME_OPERATIONS = {
    "create",
    "start",
    "stop",
    "health",
    "delivery_ack",
    "session_drift",
    "repair",
}
PRESENTATION_OPERATIONS = {
    "permission_probe",
    "create_top_level",
    "focus",
    "close_exact",
    "health",
    "capture_geometry",
    "restore_geometry",
}
EXPECTED_INVARIANTS = {
    "driver_dispatch_has_no_product_name_branch",
    "explicit_recreate_is_required_runtime_baseline",
    "exact_resume_requires_declared_vendor_session_identity",
    "unsupported_continuity_rejected_before_desired_state_mutation",
    "vendor_session_identity_is_optional_and_never_participant_identity",
    "runtime_binding_is_harness_process_and_generation_scoped",
    "model_binding_is_non_secret_and_immutable_per_generation",
    "model_change_requires_new_participant_generation",
    "ready_ack_matches_context_driver_runtime_and_process_binding",
    "runtime_create_start_exchange_is_exactly_joined",
    "driver_failure_isolated_to_bound_participants",
    "tui_has_one_exact_top_level_window_per_participant",
    "headless_has_no_presentation_binding",
    "presentation_is_optional_projection_not_identity",
    "presentation_actions_are_operation_and_participant_generation_fenced",
    "presentation_create_exchange_is_exactly_joined",
    "presentation_identity_is_stable_not_position_based",
    "accessibility_ui_scripting_is_not_a_required_capability",
    "private_bindings_never_enter_logs_receipts_mailbox_or_normal_ui",
    "registry_population_does_not_change_core_contract",
}
EXPECTED_DEFERRED_SURFACES = {
    "scenario-participant-durable-state-machine",
    "participant-lifecycle-operation-registry",
    "collaboration-policy-routing",
    "platform-driver-implementation",
    "vendor-driver-population",
    "permission-and-high-risk-confirmation-matrix",
}
EXPECTED_DATA_CLASSES = {
    "public_redacted": {
        "process_identity_sha256",
        "vendor_session_identity_sha256",
        "window_identity_sha256",
        "session_identity_sha256",
        "display_topology_fingerprint",
    },
    "host_private_non_loggable": {
        "private_launch_handle_ref",
        "private_driver_binding_ref",
        "runtime_binding_id",
        "continuity_binding_ref",
    },
    "forbidden": {
        "credential",
        "credential_ref",
        "api_key",
        "token",
        "raw_vendor_session_id",
        "raw_window_id",
        "raw_session_id",
    },
}
EXPECTED_RUNTIME_INTERFACE = {
    "create": [
        "#/$defs/runtime_create_request",
        "#/$defs/prepared_runtime_launch",
    ],
    "start": ["#/$defs/prepared_runtime_launch", "#/$defs/runtime_ready_ack"],
    "stop": [
        "#/$defs/runtime_binding_request",
        "#/$defs/runtime_health_observation",
    ],
    "health": [
        "#/$defs/runtime_binding_request",
        "#/$defs/runtime_health_observation",
    ],
    "delivery_ack": ["#/$defs/runtime_delivery_ack"],
    "session_drift": ["#/$defs/runtime_session_drift_signal"],
    "repair": [
        "#/$defs/runtime_repair_request",
        "#/$defs/runtime_health_observation",
    ],
}
EXPECTED_PRESENTATION_INTERFACE = {
    "permission_probe": ["platform-plugin", "structured-error"],
    "create_top_level": [
        "#/$defs/presentation_create_request",
        "#/$defs/presentation_create_ack",
    ],
    "focus": [
        "#/$defs/presentation_action_request",
        "#/$defs/presentation_health_observation",
    ],
    "close_exact": [
        "#/$defs/presentation_action_request",
        "#/$defs/presentation_health_observation",
    ],
    "health": [
        "#/$defs/presentation_action_request",
        "#/$defs/presentation_health_observation",
    ],
    "capture_geometry": [
        "#/$defs/presentation_action_request",
        "#/$defs/presentation_health_observation",
    ],
    "restore_geometry": [
        "#/$defs/presentation_action_request",
        "#/$defs/presentation_health_observation",
    ],
}
ALLOWED_SCHEMA_KEYWORDS = {
    "$ref",
    "type",
    "const",
    "enum",
    "pattern",
    "minimum",
    "minItems",
    "uniqueItems",
    "items",
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
FORBIDDEN_CORE_PRODUCT_TOKENS = ("codex", "claude", "iterm", "nsxpc", "machservice")


class DriverContractError(ValueError):
    """The participant driver contract or one contract value is invalid."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DriverContractError(f"JSON object contains duplicate key: {key}")
        result[key] = value
    return result


def _load_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_unique_json_object)
    except DriverContractError:
        raise
    except json.JSONDecodeError as exc:
        raise DriverContractError("driver contract is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DriverContractError("driver contract root must be an object")
    return value


def _resolve_ref(contract: Mapping[str, Any], ref: Any) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        raise DriverContractError("only local $defs references are allowed")
    name = ref.removeprefix("#/$defs/")
    if "/" in name:
        raise DriverContractError("nested or escaped schema references are unsupported")
    schema = contract.get("$defs", {}).get(name)
    if not isinstance(schema, dict):
        raise DriverContractError(f"schema reference is unresolved: {ref}")
    return schema


def _validate_schema_node(
    schema: Any, *, contract: Mapping[str, Any], path: str
) -> None:
    if not isinstance(schema, dict):
        raise DriverContractError(f"{path} must be a schema object")
    unknown = set(schema) - ALLOWED_SCHEMA_KEYWORDS
    if unknown:
        raise DriverContractError(
            f"{path} uses unsupported schema keywords: {sorted(unknown)}"
        )
    if "$ref" in schema:
        if set(schema) != {"$ref"}:
            raise DriverContractError(f"{path} mixes $ref with sibling keywords")
        _resolve_ref(contract, schema["$ref"])
        return
    if "oneOf" in schema:
        variants = schema["oneOf"]
        if not isinstance(variants, list) or len(variants) < 2:
            raise DriverContractError(f"{path}.oneOf must contain at least two variants")
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
        raise DriverContractError(f"{path}.type is unsupported")
    keyword_types = {
        "properties": "object",
        "required": "object",
        "additionalProperties": "object",
        "items": "array",
        "minItems": "array",
        "uniqueItems": "array",
        "pattern": "string",
        "minimum": "integer",
    }
    for keyword, required_type in keyword_types.items():
        if keyword in schema and schema_type != required_type:
            raise DriverContractError(
                f"{path}.{keyword} requires schema type {required_type}"
            )
    if schema_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise DriverContractError(f"{path}.properties must be an object")
        if schema.get("additionalProperties") is not False:
            raise DriverContractError(f"{path} must reject unknown fields")
        required = schema.get("required")
        if not isinstance(required, list) or len(required) != len(set(required)):
            raise DriverContractError(f"{path}.required must be a unique list")
        if not all(isinstance(item, str) for item in required):
            raise DriverContractError(f"{path}.required contains a non-string")
        if set(required) != set(properties):
            raise DriverContractError(f"{path} must require every declared field")
        for key, child in properties.items():
            _validate_schema_node(
                child, contract=contract, path=f"{path}.properties.{key}"
            )
    if schema_type == "array":
        if "items" not in schema:
            raise DriverContractError(f"{path} array has no item schema")
        _validate_schema_node(schema["items"], contract=contract, path=f"{path}.items")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise DriverContractError(f"{path}.enum must be non-empty")
        encoded = [json.dumps(item, sort_keys=True) for item in enum]
        if len(encoded) != len(set(encoded)):
            raise DriverContractError(f"{path}.enum must be unique")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as exc:
            raise DriverContractError(f"{path}.pattern is invalid") from exc
    for keyword in ("minimum", "minItems"):
        if keyword in schema and (
            not isinstance(schema[keyword], int)
            or isinstance(schema[keyword], bool)
            or schema[keyword] < 0
        ):
            raise DriverContractError(f"{path}.{keyword} must be non-negative")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise DriverContractError(f"{path}.uniqueItems must be boolean")


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
            except DriverContractError:
                continue
            matched += 1
        if matched != 1:
            raise DriverContractError(f"{path} must match exactly one schema variant")
    if "const" in schema and value != schema["const"]:
        raise DriverContractError(f"{path} does not match its constant value")
    if "enum" in schema and value not in schema["enum"]:
        raise DriverContractError(f"{path} is not in the allowed enum")
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
        raise DriverContractError(f"{path} has the wrong type")
    if schema_type == "object":
        properties = schema["properties"]
        missing = set(schema["required"]) - set(value)
        if missing:
            raise DriverContractError(f"{path} is missing fields: {sorted(missing)}")
        unknown = set(value) - set(properties)
        if unknown:
            raise DriverContractError(f"{path} has unknown fields: {sorted(unknown)}")
        for key, child_value in value.items():
            _matches_schema(
                child_value,
                properties[key],
                contract=contract,
                path=f"{path}.{key}",
            )
    if schema_type == "array":
        if len(value) < schema.get("minItems", 0):
            raise DriverContractError(f"{path} has too few items")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise DriverContractError(f"{path} items must be unique")
        for index, item in enumerate(value):
            _matches_schema(
                item,
                schema["items"],
                contract=contract,
                path=f"{path}[{index}]",
            )
    if schema_type == "string" and "pattern" in schema:
        if re.fullmatch(schema["pattern"], value) is None:
            raise DriverContractError(f"{path} does not match its pattern")
    if schema_type == "integer" and value < schema.get("minimum", value):
        raise DriverContractError(f"{path} is below its minimum")


def _metadata(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = contract.get("x-ai-collab")
    if not isinstance(metadata, dict):
        raise DriverContractError("driver contract metadata must be an object")
    expected_fields = {
        "schema_version",
        "contract_id",
        "product_contract_version",
        "participant_driver_contract_version",
        "registry",
        "runtime_interface",
        "presentation_interface",
        "data_classes",
        "invariants",
        "deferred_surfaces",
    }
    if set(metadata) != expected_fields:
        raise DriverContractError("driver contract metadata fields do not match")
    return metadata


def validate_contract(*, repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the tracked suite and return it with a redacted result."""

    root = repo_root.resolve()
    contract_path = root / CONTRACT_RELATIVE_PATH
    try:
        resolved = contract_path.resolve(strict=True)
        if (
            contract_path.is_symlink()
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise DriverContractError(
                "driver contract must be a regular project artifact"
            )
        if contract_path.stat().st_size > MAX_CONTRACT_BYTES:
            raise DriverContractError("driver contract exceeds the size limit")
        text = contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DriverContractError("driver contract is unavailable") from exc
    contract = _load_json(text)
    if set(contract) != TOP_LEVEL_FIELDS:
        raise DriverContractError("driver contract top-level fields do not match")
    if contract["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise DriverContractError("driver contract schema dialect is incompatible")
    if contract["$id"] != "urn:ai-collab:participant-drivers:v2":
        raise DriverContractError("driver contract identity is incompatible")
    definitions = contract.get("$defs")
    if not isinstance(definitions, dict) or set(definitions) != EXPECTED_DEFS:
        raise DriverContractError("driver contract definition set does not match")
    expected_root = [
        {"$ref": f"#/$defs/{name}"} for name in ROOT_ARTIFACT_DEFS
    ]
    if contract["oneOf"] != expected_root:
        raise DriverContractError("driver contract root artifacts do not match")
    for name, schema in definitions.items():
        _validate_schema_node(schema, contract=contract, path=f"$defs.{name}")

    metadata = _metadata(contract)
    if metadata["schema_version"] != 1 or isinstance(
        metadata["schema_version"], bool
    ):
        raise DriverContractError("driver metadata schema version is incompatible")
    if metadata["contract_id"] != "ai-collab-participant-drivers-v2":
        raise DriverContractError("driver metadata contract id is incompatible")
    if metadata["product_contract_version"] != "1.0":
        raise DriverContractError("driver product contract version is incompatible")
    if metadata["participant_driver_contract_version"] != 2:
        raise DriverContractError("participant driver version is incompatible")
    if metadata["registry"] != {
        "schema_ref": "#/$defs/driver_registry",
        "digest": "sha256-canonical-json",
        "runtime_lookup_key": ["driver_id", "contract_version"],
        "presentation_lookup_key": ["driver_id", "contract_version"],
        "dispatch": "registry-and-capability-only",
        "empty_registry_is_valid": True,
        "population_is_implementation-owned": True,
    }:
        raise DriverContractError("driver registry metadata is incompatible")
    if metadata["runtime_interface"] != EXPECTED_RUNTIME_INTERFACE:
        raise DriverContractError("runtime interface map is incompatible")
    if metadata["presentation_interface"] != EXPECTED_PRESENTATION_INTERFACE:
        raise DriverContractError("presentation interface map is incompatible")
    data_classes = metadata["data_classes"]
    if not isinstance(data_classes, dict) or set(data_classes) != set(
        EXPECTED_DATA_CLASSES
    ):
        raise DriverContractError("driver data classes do not match")
    for name, expected in EXPECTED_DATA_CLASSES.items():
        values = data_classes[name]
        if not isinstance(values, list) or set(values) != expected or len(values) != len(expected):
            raise DriverContractError(f"driver data class {name} does not match")
    invariants = metadata["invariants"]
    if not isinstance(invariants, dict) or set(invariants) != EXPECTED_INVARIANTS:
        raise DriverContractError("driver invariant set does not match")
    if any(value is not True for value in invariants.values()):
        raise DriverContractError("every driver invariant must be active")
    deferred = metadata["deferred_surfaces"]
    if (
        not isinstance(deferred, list)
        or set(deferred) != EXPECTED_DEFERRED_SURFACES
        or len(deferred) != len(EXPECTED_DEFERRED_SURFACES)
    ):
        raise DriverContractError("deferred driver surfaces do not match")
    lowered = text.casefold()
    for token in FORBIDDEN_CORE_PRODUCT_TOKENS:
        if token in lowered:
            raise DriverContractError(
                "driver core contract contains a product or platform-specific token"
            )

    empty_registry = {
        "schema_version": 1,
        "participant_driver_contract_version": 2,
        "runtime_drivers": [],
        "presentation_drivers": [],
    }
    validate_registry(empty_registry, contract=contract)
    result = {
        "schema_version": 1,
        "validation_id": VALIDATION_ID,
        "status": "valid",
        "contract_relative_path": CONTRACT_RELATIVE_PATH,
        "contract_digest": canonical_json_sha256(contract),
        "raw_sha256": sha256_file(contract_path),
        "contract_id": metadata["contract_id"],
        "product_contract_version": metadata["product_contract_version"],
        "participant_driver_contract_version": metadata[
            "participant_driver_contract_version"
        ],
        "runtime_operation_count": len(RUNTIME_OPERATIONS),
        "presentation_operation_count": len(PRESENTATION_OPERATIONS),
        "root_artifact_count": len(ROOT_ARTIFACT_DEFS),
        "registry_population": "implementation-owned",
        "state_mutated": False,
    }
    return contract, result


def _validate_error_namespace(value: str) -> None:
    if value.split(".", 1)[0] in RESERVED_ERROR_NAMESPACES:
        raise DriverContractError("driver error namespace collides with Host IPC")


def validate_runtime_descriptor(
    descriptor: Any, *, contract: Mapping[str, Any]
) -> None:
    _matches_schema(
        descriptor,
        _resolve_ref(contract, "#/$defs/runtime_driver_descriptor"),
        contract=contract,
        path="runtime_driver_descriptor",
    )
    if set(descriptor["lifecycle_operations"]) != RUNTIME_OPERATIONS:
        raise DriverContractError("runtime lifecycle operation set does not match")
    continuity = set(descriptor["continuity_modes"])
    if "explicit_recreate" not in continuity:
        raise DriverContractError("runtime driver omits explicit recreate baseline")
    vendor_identity = descriptor["supports_vendor_session_identity"]
    surface = descriptor["vendor_lifecycle_surface"]
    vendor_operations = set(descriptor["optional_vendor_lifecycle_operations"])
    if vendor_identity != (surface is not None):
        raise DriverContractError("vendor session capability and surface disagree")
    if "exact_resume" in continuity and (
        not vendor_identity
        or not {"vendor_resume", "vendor_bind"}.issubset(vendor_operations)
    ):
        raise DriverContractError(
            "exact resume lacks vendor session identity, resume, or bind capability"
        )
    if not vendor_identity and (
        vendor_operations
        or "vendor_binding" in descriptor["retention_modes"]
        or "refresh_vendor_binding" in descriptor["repair_modes"]
    ):
        raise DriverContractError("vendor-only modes lack vendor session capability")
    _validate_error_namespace(descriptor["error_namespace"])


def validate_presentation_descriptor(
    descriptor: Any, *, contract: Mapping[str, Any]
) -> None:
    _matches_schema(
        descriptor,
        _resolve_ref(contract, "#/$defs/presentation_driver_descriptor"),
        contract=contract,
        path="presentation_driver_descriptor",
    )
    if set(descriptor["lifecycle_operations"]) != PRESENTATION_OPERATIONS:
        raise DriverContractError("presentation lifecycle operation set does not match")
    if descriptor["interaction_modes"] != ["tui"]:
        raise DriverContractError("presentation driver is not TUI-only")
    _validate_error_namespace(descriptor["error_namespace"])


def validate_registry(
    registry: Any, *, contract: Mapping[str, Any]
) -> str:
    """Validate one composed registry and return its canonical digest."""

    _matches_schema(
        registry,
        _resolve_ref(contract, "#/$defs/driver_registry"),
        contract=contract,
        path="driver_registry",
    )
    runtime_keys: set[tuple[str, int]] = set()
    presentation_keys: set[tuple[str, int]] = set()
    implementation_refs: set[str] = set()
    error_namespaces: set[str] = set()
    for descriptor in registry["runtime_drivers"]:
        validate_runtime_descriptor(descriptor, contract=contract)
        key = (descriptor["driver_id"], descriptor["contract_version"])
        if key in runtime_keys:
            raise DriverContractError("duplicate runtime driver registry key")
        runtime_keys.add(key)
        for value, label in (
            (descriptor["implementation_ref"], "implementation ref"),
            (descriptor["error_namespace"], "error namespace"),
        ):
            target = implementation_refs if label == "implementation ref" else error_namespaces
            if value in target:
                raise DriverContractError(f"duplicate driver {label}")
            target.add(value)
    for descriptor in registry["presentation_drivers"]:
        validate_presentation_descriptor(descriptor, contract=contract)
        key = (descriptor["driver_id"], descriptor["contract_version"])
        if key in presentation_keys:
            raise DriverContractError("duplicate presentation driver registry key")
        presentation_keys.add(key)
        for value, label in (
            (descriptor["implementation_ref"], "implementation ref"),
            (descriptor["error_namespace"], "error namespace"),
        ):
            target = implementation_refs if label == "implementation ref" else error_namespaces
            if value in target:
                raise DriverContractError(f"duplicate driver {label}")
            target.add(value)
    return canonical_json_sha256(registry)


def validate_runtime_launch_spec(
    launch_spec: Any,
    *,
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
) -> None:
    """Validate immutable generation input before desired-state mutation."""

    _matches_schema(
        launch_spec,
        _resolve_ref(contract, "#/$defs/runtime_launch_spec"),
        contract=contract,
        path="runtime_launch_spec",
    )
    validate_runtime_descriptor(descriptor, contract=contract)
    if launch_spec["driver_id"] != descriptor["driver_id"]:
        raise DriverContractError("launch spec driver is not the resolved descriptor")
    if launch_spec["driver_contract_version"] != descriptor["contract_version"]:
        raise DriverContractError("launch spec driver version mismatch")
    if launch_spec["interaction_mode"] not in descriptor["interaction_modes"]:
        raise DriverContractError("runtime interaction mode is unsupported")
    continuity = launch_spec["continuity_mode"]
    if continuity not in descriptor["continuity_modes"]:
        raise DriverContractError("runtime continuity mode is unsupported")
    binding_ref = launch_spec["continuity_binding_ref"]
    if continuity == "exact_resume":
        if not descriptor["supports_vendor_session_identity"] or binding_ref is None:
            raise DriverContractError("exact resume lacks an exact continuity binding")
    elif binding_ref is not None:
        raise DriverContractError("explicit recreate cannot consume a continuity binding")


def validate_runtime_ready_ack(
    ack: Any,
    *,
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
) -> None:
    _matches_schema(
        ack,
        _resolve_ref(contract, "#/$defs/runtime_ready_ack"),
        contract=contract,
        path="runtime_ready_ack",
    )
    validate_runtime_descriptor(descriptor, contract=contract)
    context = ack["context"]
    binding = ack["binding"]
    for field in ("scenario_id", "participant_id", "participant_generation"):
        if context[field] != binding[field]:
            raise DriverContractError(f"runtime ready ACK {field} mismatch")
    if binding["driver_id"] != descriptor["driver_id"]:
        raise DriverContractError("runtime ready ACK driver mismatch")
    if binding["capability_snapshot_digest"] != context["capability_snapshot_digest"]:
        raise DriverContractError("runtime ready ACK capability snapshot mismatch")
    if binding["continuity_mode"] == "exact_resume":
        if not descriptor["supports_vendor_session_identity"]:
            raise DriverContractError("exact resume ACK lacks vendor identity capability")
        # A newly opened interactive TUI can be input-ready before the vendor
        # materializes a conversation identity.  Null means pending first-turn
        # materialization, not a fallback to recent/role/cwd session lookup.


def _validate_current_registry_digest(
    context: Mapping[str, Any], current_registry_digest: str
) -> None:
    if (
        not isinstance(current_registry_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", current_registry_digest) is None
    ):
        raise DriverContractError("current driver registry digest is invalid")
    if context["driver_registry_digest"] != current_registry_digest:
        raise DriverContractError("driver call uses a stale registry digest")


def validate_runtime_create_exchange(
    request: Any,
    prepared: Any,
    *,
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    current_registry_digest: str,
) -> None:
    """Bind create input to the exact prepared launch before driver start."""

    _matches_schema(
        request,
        _resolve_ref(contract, "#/$defs/runtime_create_request"),
        contract=contract,
        path="runtime_create_request",
    )
    _matches_schema(
        prepared,
        _resolve_ref(contract, "#/$defs/prepared_runtime_launch"),
        contract=contract,
        path="prepared_runtime_launch",
    )
    validate_runtime_launch_spec(
        request["launch_spec"], contract=contract, descriptor=descriptor
    )
    _validate_current_registry_digest(request["context"], current_registry_digest)
    if prepared["context"] != request["context"]:
        raise DriverContractError("runtime create context changed before start")
    if prepared["driver_id"] != request["launch_spec"]["driver_id"]:
        raise DriverContractError("prepared runtime driver differs from launch spec")


def validate_runtime_start_exchange(
    prepared: Any,
    ack: Any,
    *,
    launch_spec: Mapping[str, Any],
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    current_registry_digest: str,
) -> None:
    """Bind prepared launch and requested continuity to the exact ready ACK."""

    _matches_schema(
        prepared,
        _resolve_ref(contract, "#/$defs/prepared_runtime_launch"),
        contract=contract,
        path="prepared_runtime_launch",
    )
    validate_runtime_launch_spec(
        launch_spec, contract=contract, descriptor=descriptor
    )
    validate_runtime_ready_ack(ack, contract=contract, descriptor=descriptor)
    _validate_current_registry_digest(prepared["context"], current_registry_digest)
    if ack["context"] != prepared["context"]:
        raise DriverContractError("runtime start context differs from prepared launch")
    binding = ack["binding"]
    if prepared["driver_id"] != launch_spec["driver_id"]:
        raise DriverContractError("prepared runtime driver differs from launch spec")
    if binding["runtime_instance_id"] != prepared["runtime_instance_id"]:
        raise DriverContractError("runtime ready ACK instance differs from prepared launch")
    if binding["continuity_mode"] != launch_spec["continuity_mode"]:
        raise DriverContractError("runtime ready ACK silently changed continuity mode")


def validate_presentation_create_ack(
    ack: Any,
    *,
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
) -> None:
    _matches_schema(
        ack,
        _resolve_ref(contract, "#/$defs/presentation_create_ack"),
        contract=contract,
        path="presentation_create_ack",
    )
    validate_presentation_descriptor(descriptor, contract=contract)
    context = ack["context"]
    binding = ack["binding"]
    for field in ("scenario_id", "participant_id", "participant_generation"):
        if context[field] != binding[field]:
            raise DriverContractError(f"presentation create ACK {field} mismatch")
    if binding["driver_id"] != descriptor["driver_id"]:
        raise DriverContractError("presentation create ACK driver mismatch")
    if binding["capability_snapshot_digest"] != context["capability_snapshot_digest"]:
        raise DriverContractError("presentation ACK capability snapshot mismatch")


def validate_presentation_create_exchange(
    request: Any,
    ack: Any,
    *,
    contract: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    current_registry_digest: str,
) -> None:
    """Bind create input to the exact runtime, topology, and presentation ACK."""

    _matches_schema(
        request,
        _resolve_ref(contract, "#/$defs/presentation_create_request"),
        contract=contract,
        path="presentation_create_request",
    )
    validate_presentation_create_ack(
        ack, contract=contract, descriptor=descriptor
    )
    _validate_current_registry_digest(request["context"], current_registry_digest)
    if ack["context"] != request["context"]:
        raise DriverContractError("presentation create context changed before ACK")
    binding = ack["binding"]
    if request["presentation_driver_id"] != binding["driver_id"]:
        raise DriverContractError("presentation create ACK driver differs from request")
    if request["runtime_binding_id"] != binding["runtime_binding_id"]:
        raise DriverContractError("presentation create ACK runtime binding mismatch")
    if (
        request["display_topology_fingerprint"]
        != binding["display_topology_fingerprint"]
    ):
        raise DriverContractError("presentation topology changed during create")
    requested_geometry = request["restore_geometry"]
    outcome = ack["geometry_restore_outcome"]
    if requested_geometry is None:
        if outcome != "not_requested":
            raise DriverContractError("presentation reported an unrequested restore")
    elif outcome == "not_requested":
        raise DriverContractError("presentation ignored requested geometry restore")
    elif outcome == "applied_exact" and binding["geometry"] != requested_geometry:
        raise DriverContractError("presentation exact geometry restore does not match")


def validate_participant_projection(
    *,
    launch_spec: Mapping[str, Any],
    runtime_ack: Mapping[str, Any],
    presentation_ack: Mapping[str, Any] | None,
) -> None:
    """Enforce the one-window TUI / no-window headless composition rule."""

    runtime_context = runtime_ack["context"]
    if launch_spec["interaction_mode"] == "headless":
        if presentation_ack is not None:
            raise DriverContractError("headless participant has a presentation binding")
        return
    if presentation_ack is None:
        raise DriverContractError("TUI participant lacks an exact presentation binding")
    presentation_context = presentation_ack["context"]
    for field in ("scenario_id", "participant_id", "participant_generation"):
        if runtime_context[field] != presentation_context[field]:
            raise DriverContractError(f"runtime/presentation {field} mismatch")
    if runtime_context["capability_snapshot_digest"] != presentation_context[
        "capability_snapshot_digest"
    ]:
        raise DriverContractError("runtime/presentation capability snapshot mismatch")
    if (
        runtime_ack["binding"]["runtime_binding_id"]
        != presentation_ack["binding"]["runtime_binding_id"]
    ):
        raise DriverContractError("runtime/presentation binding join mismatch")


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
    except (DriverContractError, OSError, ValueError) as exc:
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
