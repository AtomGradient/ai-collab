# SPDX-License-Identifier: LicenseRef-AtomGradient-Proprietary
# Copyright (c) 2026 AtomGradient. All rights reserved.
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司。保留所有权利。
# Unauthorized copying, distribution, or use is strictly prohibited.
# 未经授权，禁止复制、分发或使用本文件。

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_ai_collab_driver_contract as validator  # noqa: E402
from ai_collab_bootstrap_evidence import sha256_file  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _contract() -> dict[str, Any]:
    contract, _ = validator.validate_contract(repo_root=REPO_ROOT)
    return copy.deepcopy(contract)


def _install_contract(tmp_path: Path, contract: dict[str, Any] | None = None) -> Path:
    root = tmp_path / "project"
    path = root / validator.CONTRACT_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(contract or _contract(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    transient_parts = {".git", ".pytest_cache", ".build", "__pycache__"}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mode,
            path.stat().st_mtime_ns,
            sha256_file(path),
        )
        for path in root.rglob("*")
        if path.is_file()
        and path.name != ".DS_Store"
        and transient_parts.isdisjoint(path.relative_to(root).parts)
    }


def _runtime_descriptor(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "driver_kind": "runtime",
        "driver_id": "runtime.opaque-a",
        "contract_version": 2,
        "implementation_ref": "plugin.runtime-opaque-a.v1",
        "interaction_modes": ["tui", "headless"],
        "lifecycle_operations": [
            "create",
            "start",
            "stop",
            "health",
            "delivery_ack",
            "session_drift",
            "repair",
        ],
        "continuity_modes": ["explicit_recreate"],
        "supports_harness_process_binding": True,
        "supports_ready_ack": True,
        "supports_delivery_ack": True,
        "supports_session_drift_signal": True,
        "supports_vendor_session_identity": False,
        "vendor_lifecycle_surface": None,
        "optional_vendor_lifecycle_operations": [],
        "retention_modes": ["none", "harness_context"],
        "repair_modes": ["recreate_generation", "rebind_owned_process"],
        "error_namespace": "runtime-opaque-a.error",
        "redaction_profile_ref": "redaction.runtime-default",
    }
    value.update(overrides)
    return value


def _exact_runtime_descriptor(**overrides: Any) -> dict[str, Any]:
    value = _runtime_descriptor(
        continuity_modes=["explicit_recreate", "exact_resume"],
        supports_vendor_session_identity=True,
        vendor_lifecycle_surface="runtime-lifecycle.opaque-v1",
        optional_vendor_lifecycle_operations=["vendor_resume", "vendor_bind"],
        retention_modes=["none", "harness_context", "vendor_binding"],
        repair_modes=[
            "recreate_generation",
            "rebind_owned_process",
            "refresh_vendor_binding",
        ],
    )
    value.update(overrides)
    return value


def _presentation_descriptor(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "driver_kind": "presentation",
        "driver_id": "presentation.opaque-a",
        "contract_version": 1,
        "implementation_ref": "plugin.presentation-opaque-a.v1",
        "interaction_modes": ["tui"],
        "lifecycle_operations": [
            "permission_probe",
            "create_top_level",
            "focus",
            "close_exact",
            "health",
            "capture_geometry",
            "restore_geometry",
        ],
        "supports_stable_window_identity": True,
        "supports_stable_session_identity": True,
        "supports_exact_close": True,
        "supports_geometry": True,
        "supports_display_topology": True,
        "permission_model": "platform-plugin",
        "error_namespace": "presentation-opaque-a.error",
        "redaction_profile_ref": "redaction.presentation-default",
    }
    value.update(overrides)
    return value


def _registry(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": 1,
        "participant_driver_contract_version": 2,
        "runtime_drivers": [_runtime_descriptor()],
        "presentation_drivers": [_presentation_descriptor()],
    }
    value.update(overrides)
    return value


def _launch_spec(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "driver_id": "runtime.opaque-a",
        "driver_contract_version": 2,
        "interaction_mode": "tui",
        "continuity_mode": "explicit_recreate",
        "runtime_profile_ref": "runtime-profile.local-default",
        "model_binding": {
            "provider_profile_ref": "provider-profile.local-default",
            "model_ref": "model.opaque-a",
            "inference_profile_ref": None,
        },
        "continuity_binding_ref": None,
    }
    value.update(overrides)
    return value


def _context(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "scenario_id": "scenario:01",
        "participant_id": "participant:01",
        "participant_generation": 3,
        "operation_id": "operation:07",
        "operation_generation": 2,
        "driver_registry_digest": DIGEST_A,
        "capability_snapshot_digest": DIGEST_B,
    }
    value.update(overrides)
    return value


def _runtime_ack(**binding_overrides: Any) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "scenario_id": "scenario:01",
        "participant_id": "participant:01",
        "participant_generation": 3,
        "driver_id": "runtime.opaque-a",
        "runtime_instance_id": "runtime:01",
        "runtime_binding_id": "binding:runtime-01",
        "process_instance_id": "process:01",
        "process_identity_sha256": DIGEST_C,
        "continuity_mode": "explicit_recreate",
        "vendor_session_identity_sha256": None,
        "private_driver_binding_ref": "binding:runtime-private-01",
        "capability_snapshot_digest": DIGEST_B,
    }
    binding.update(binding_overrides)
    return {"context": _context(), "binding": binding, "ready": True}


def _presentation_ack(**binding_overrides: Any) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "scenario_id": "scenario:01",
        "participant_id": "participant:01",
        "participant_generation": 3,
        "driver_id": "presentation.opaque-a",
        "presentation_instance_id": "presentation:01",
        "runtime_binding_id": "binding:runtime-01",
        "window_identity_sha256": DIGEST_C,
        "session_identity_sha256": DIGEST_D,
        "private_driver_binding_ref": "binding:presentation-private-01",
        "geometry": {"x": 10, "y": 20, "width": 1200, "height": 800},
        "display_topology_fingerprint": DIGEST_A,
        "capability_snapshot_digest": DIGEST_B,
    }
    binding.update(binding_overrides)
    return {
        "context": _context(),
        "binding": binding,
        "geometry_restore_outcome": "not_requested",
        "created": True,
    }


def _runtime_create_request(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "context": _context(),
        "launch_spec": _launch_spec(),
    }
    value.update(overrides)
    return value


def _prepared_runtime_launch(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "context": _context(),
        "driver_id": "runtime.opaque-a",
        "runtime_instance_id": "runtime:01",
        "private_launch_handle_ref": "launch:private-01",
    }
    value.update(overrides)
    return value


def _presentation_create_request(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "context": _context(),
        "presentation_driver_id": "presentation.opaque-a",
        "runtime_binding_id": "binding:runtime-01",
        "restore_geometry": None,
        "display_topology_fingerprint": DIGEST_A,
    }
    value.update(overrides)
    return value


def test_contract_validates_without_mutating_project_tree() -> None:
    before = _snapshot(REPO_ROOT)
    _, result = validator.validate_contract(repo_root=REPO_ROOT)
    after = _snapshot(REPO_ROOT)

    assert before == after
    assert result["status"] == "valid"
    assert result["participant_driver_contract_version"] == 2
    assert result["runtime_operation_count"] == 7
    assert result["presentation_operation_count"] == 7
    assert result["root_artifact_count"] == 6
    assert result["registry_population"] == "implementation-owned"
    assert result["state_mutated"] is False
    assert "/Users/" not in json.dumps(result)


def test_contract_rejects_duplicate_json_key(tmp_path: Path) -> None:
    root = _install_contract(tmp_path)
    path = root / validator.CONTRACT_RELATIVE_PATH
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("{", '{"title":"duplicate",', 1), encoding="utf-8")

    with pytest.raises(validator.DriverContractError, match="duplicate key"):
        validator.validate_contract(repo_root=root)


def test_contract_rejects_symlink(tmp_path: Path) -> None:
    target_root = _install_contract(tmp_path / "target")
    target = target_root / validator.CONTRACT_RELATIVE_PATH
    root = tmp_path / "project"
    path = root / validator.CONTRACT_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    with pytest.raises(validator.DriverContractError, match="regular project artifact"):
        validator.validate_contract(repo_root=root)


def test_contract_rejects_oversized_artifact(tmp_path: Path) -> None:
    root = _install_contract(tmp_path)
    path = root / validator.CONTRACT_RELATIVE_PATH
    with path.open("ab") as handle:
        handle.write(b" " * validator.MAX_CONTRACT_BYTES)

    with pytest.raises(validator.DriverContractError, match="size limit"):
        validator.validate_contract(repo_root=root)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda c: c.update({"extra": True}), "top-level fields"),
        (
            lambda c: c["$defs"]["model_binding"].update({"description": "open"}),
            "unsupported schema keywords",
        ),
        (
            lambda c: c["$defs"]["nullable_sha256"]["oneOf"][1].update(
                {"$ref": "https://example.invalid/schema"}
            ),
            "only local",
        ),
        (
            lambda c: c["x-ai-collab"]["invariants"].update(
                {"model_change_requires_new_participant_generation": False}
            ),
            "every driver invariant",
        ),
        (
            lambda c: c["x-ai-collab"]["runtime_interface"].update(
                {"vendor_magic": ["opaque"]}
            ),
            "runtime interface map",
        ),
        (
            lambda c: c["x-ai-collab"].update({"contract_id": "codex-driver"}),
            "contract id",
        ),
    ],
)
def test_contract_rejects_semantic_drift(
    tmp_path: Path, mutate: Any, match: str
) -> None:
    contract = _contract()
    mutate(contract)
    root = _install_contract(tmp_path, contract)

    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_contract(repo_root=root)


def test_runtime_descriptor_accepts_required_baseline() -> None:
    validator.validate_runtime_descriptor(_runtime_descriptor(), contract=_contract())


def test_runtime_descriptor_accepts_optional_exact_resume() -> None:
    validator.validate_runtime_descriptor(
        _exact_runtime_descriptor(), contract=_contract()
    )


@pytest.mark.parametrize(
    ("descriptor", "match"),
    [
        (
            _runtime_descriptor(continuity_modes=["exact_resume"]),
            "explicit recreate baseline",
        ),
        (
            _runtime_descriptor(continuity_modes=["explicit_recreate", "exact_resume"]),
            "exact resume lacks vendor",
        ),
        (
            _runtime_descriptor(vendor_lifecycle_surface="runtime-lifecycle.opaque-v1"),
            "capability and surface disagree",
        ),
        (
            _runtime_descriptor(retention_modes=["none", "vendor_binding"]),
            "vendor-only modes",
        ),
        (
            _runtime_descriptor(error_namespace="operation.driver"),
            "collides with Host IPC",
        ),
        (
            _runtime_descriptor(lifecycle_operations=["start"] * 7),
            "unique",
        ),
    ],
)
def test_runtime_descriptor_rejects_invalid_capability_combinations(
    descriptor: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_runtime_descriptor(descriptor, contract=_contract())


def test_presentation_descriptor_accepts_required_interface() -> None:
    validator.validate_presentation_descriptor(
        _presentation_descriptor(), contract=_contract()
    )


@pytest.mark.parametrize(
    ("descriptor", "match"),
    [
        (
            _presentation_descriptor(lifecycle_operations=[
                "permission_probe",
                "create_top_level",
                "focus",
                "close_exact",
                "health",
                "capture_geometry",
                "capture_geometry",
            ]),
            "unique",
        ),
        (
            _presentation_descriptor(interaction_modes=["headless"]),
            "constant value",
        ),
        (
            _presentation_descriptor(error_namespace="availability.presentation"),
            "collides with Host IPC",
        ),
    ],
)
def test_presentation_descriptor_rejects_invalid_capabilities(
    descriptor: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_presentation_descriptor(descriptor, contract=_contract())


def test_registry_accepts_empty_population() -> None:
    registry = _registry(runtime_drivers=[], presentation_drivers=[])
    digest = validator.validate_registry(registry, contract=_contract())

    assert digest == validator.canonical_json_sha256(registry)


def test_registry_accepts_opaque_drivers_and_is_order_sensitive() -> None:
    registry = _registry()
    digest = validator.validate_registry(registry, contract=_contract())
    reversed_registry = copy.deepcopy(registry)
    reversed_registry["runtime_drivers"].append(
        _runtime_descriptor(
            driver_id="runtime.opaque-b",
            implementation_ref="plugin.runtime-opaque-b.v1",
            error_namespace="runtime-opaque-b.error",
        )
    )
    reverse_digest = validator.validate_registry(
        reversed_registry, contract=_contract()
    )

    assert digest != reverse_digest


@pytest.mark.parametrize(
    ("registry", "match"),
    [
        (
            _registry(
                runtime_drivers=[
                    _runtime_descriptor(),
                    _runtime_descriptor(
                        implementation_ref="plugin.runtime-opaque-b.v1",
                        error_namespace="runtime-opaque-b.error",
                    ),
                ]
            ),
            "duplicate runtime driver registry key",
        ),
        (
            _registry(
                presentation_drivers=[
                    _presentation_descriptor(
                        implementation_ref="plugin.runtime-opaque-a.v1"
                    )
                ]
            ),
            "duplicate driver implementation ref",
        ),
        (
            _registry(
                presentation_drivers=[
                    _presentation_descriptor(error_namespace="runtime-opaque-a.error")
                ]
            ),
            "duplicate driver error namespace",
        ),
    ],
)
def test_registry_rejects_ambiguous_entries(
    registry: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_registry(registry, contract=_contract())


def test_launch_spec_accepts_explicit_recreate_without_vendor_identity() -> None:
    validator.validate_runtime_launch_spec(
        _launch_spec(), contract=_contract(), descriptor=_runtime_descriptor()
    )


def test_launch_spec_accepts_exact_resume_only_with_capability_and_binding() -> None:
    validator.validate_runtime_launch_spec(
        _launch_spec(
            continuity_mode="exact_resume", continuity_binding_ref="binding:exact-01"
        ),
        contract=_contract(),
        descriptor=_exact_runtime_descriptor(),
    )


@pytest.mark.parametrize(
    ("launch_spec", "descriptor", "match"),
    [
        (
            _launch_spec(driver_id="runtime.opaque-b"),
            _runtime_descriptor(),
            "resolved descriptor",
        ),
        (
            _launch_spec(interaction_mode="headless"),
            _runtime_descriptor(interaction_modes=["tui"]),
            "interaction mode is unsupported",
        ),
        (
            _launch_spec(continuity_mode="exact_resume", continuity_binding_ref="binding:x"),
            _runtime_descriptor(),
            "continuity mode is unsupported",
        ),
        (
            _launch_spec(continuity_mode="exact_resume"),
            _exact_runtime_descriptor(),
            "exact continuity binding",
        ),
        (
            _launch_spec(continuity_binding_ref="binding:x"),
            _runtime_descriptor(),
            "explicit recreate cannot consume",
        ),
    ],
)
def test_launch_spec_rejects_unsupported_or_ambiguous_continuity(
    launch_spec: dict[str, Any], descriptor: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_runtime_launch_spec(
            launch_spec, contract=_contract(), descriptor=descriptor
        )


def test_launch_spec_rejects_secret_or_runtime_rebind_fields() -> None:
    launch_spec = _launch_spec()
    launch_spec["api_key"] = "secret"

    with pytest.raises(validator.DriverContractError, match="unknown fields"):
        validator.validate_runtime_launch_spec(
            launch_spec, contract=_contract(), descriptor=_runtime_descriptor()
        )


def test_runtime_create_exchange_joins_context_registry_driver_and_launch_spec() -> None:
    validator.validate_runtime_create_exchange(
        _runtime_create_request(),
        _prepared_runtime_launch(),
        contract=_contract(),
        descriptor=_runtime_descriptor(),
        current_registry_digest=DIGEST_A,
    )


@pytest.mark.parametrize(
    ("create_request", "prepared", "current_digest", "match"),
    [
        (
            _runtime_create_request(),
            _prepared_runtime_launch(context=_context(operation_generation=3)),
            DIGEST_A,
            "context changed",
        ),
        (
            _runtime_create_request(),
            _prepared_runtime_launch(driver_id="runtime.opaque-b"),
            DIGEST_A,
            "driver differs",
        ),
        (
            _runtime_create_request(),
            _prepared_runtime_launch(),
            DIGEST_D,
            "stale registry digest",
        ),
    ],
)
def test_runtime_create_exchange_rejects_cross_request_values(
    create_request: dict[str, Any],
    prepared: dict[str, Any],
    current_digest: str,
    match: str,
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_runtime_create_exchange(
            create_request,
            prepared,
            contract=_contract(),
            descriptor=_runtime_descriptor(),
            current_registry_digest=current_digest,
        )


def test_runtime_ready_ack_binds_context_process_generation_and_snapshot() -> None:
    validator.validate_runtime_ready_ack(
        _runtime_ack(), contract=_contract(), descriptor=_runtime_descriptor()
    )


@pytest.mark.parametrize(
    ("ack", "descriptor", "match"),
    [
        (
            _runtime_ack(participant_generation=4),
            _runtime_descriptor(),
            "participant_generation mismatch",
        ),
        (
            _runtime_ack(driver_id="runtime.opaque-b"),
            _runtime_descriptor(),
            "driver mismatch",
        ),
        (
            _runtime_ack(capability_snapshot_digest=DIGEST_D),
            _runtime_descriptor(),
            "capability snapshot mismatch",
        ),
    ],
)
def test_runtime_ready_ack_rejects_cross_generation_or_weak_binding(
    ack: dict[str, Any], descriptor: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_runtime_ready_ack(
            ack, contract=_contract(), descriptor=descriptor
        )


def test_exact_resume_ready_ack_accepts_redacted_vendor_binding() -> None:
    validator.validate_runtime_ready_ack(
        _runtime_ack(
            continuity_mode="exact_resume",
            vendor_session_identity_sha256=DIGEST_D,
        ),
        contract=_contract(),
        descriptor=_exact_runtime_descriptor(),
    )


def test_fresh_exact_resume_ready_ack_accepts_pending_first_turn_binding() -> None:
    validator.validate_runtime_ready_ack(
        _runtime_ack(
            continuity_mode="exact_resume",
            vendor_session_identity_sha256=None,
        ),
        contract=_contract(),
        descriptor=_exact_runtime_descriptor(),
    )


def test_runtime_start_exchange_joins_prepared_instance_and_continuity() -> None:
    validator.validate_runtime_start_exchange(
        _prepared_runtime_launch(),
        _runtime_ack(),
        launch_spec=_launch_spec(),
        contract=_contract(),
        descriptor=_runtime_descriptor(),
        current_registry_digest=DIGEST_A,
    )


@pytest.mark.parametrize(
    ("prepared", "ack", "launch_spec", "match"),
    [
        (
            _prepared_runtime_launch(),
            _runtime_ack(runtime_instance_id="runtime:02"),
            _launch_spec(),
            "instance differs",
        ),
        (
            _prepared_runtime_launch(),
            _runtime_ack(
                continuity_mode="explicit_recreate",
                vendor_session_identity_sha256=None,
            ),
            _launch_spec(
                continuity_mode="exact_resume",
                continuity_binding_ref="binding:previous-exact",
            ),
            "silently changed continuity mode",
        ),
        (
            _prepared_runtime_launch(),
            {**_runtime_ack(), "context": _context(operation_generation=3)},
            _launch_spec(),
            "context differs",
        ),
    ],
)
def test_runtime_start_exchange_rejects_instance_context_or_continuity_swap(
    prepared: dict[str, Any],
    ack: dict[str, Any],
    launch_spec: dict[str, Any],
    match: str,
) -> None:
    descriptor = (
        _exact_runtime_descriptor()
        if launch_spec["continuity_mode"] == "exact_resume"
        else _runtime_descriptor()
    )
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_runtime_start_exchange(
            prepared,
            ack,
            launch_spec=launch_spec,
            contract=_contract(),
            descriptor=descriptor,
            current_registry_digest=DIGEST_A,
        )


def test_presentation_create_ack_binds_exact_window_and_generation() -> None:
    validator.validate_presentation_create_ack(
        _presentation_ack(),
        contract=_contract(),
        descriptor=_presentation_descriptor(),
    )


def test_presentation_create_exchange_joins_runtime_topology_and_geometry() -> None:
    request = _presentation_create_request(
        restore_geometry={"x": 10, "y": 20, "width": 1200, "height": 800}
    )
    ack = _presentation_ack()
    ack["geometry_restore_outcome"] = "applied_exact"

    validator.validate_presentation_create_exchange(
        request,
        ack,
        contract=_contract(),
        descriptor=_presentation_descriptor(),
        current_registry_digest=DIGEST_A,
    )


@pytest.mark.parametrize(
    ("create_request", "ack", "match"),
    [
        (
            _presentation_create_request(runtime_binding_id="binding:runtime-02"),
            _presentation_ack(),
            "runtime binding mismatch",
        ),
        (
            _presentation_create_request(display_topology_fingerprint=DIGEST_D),
            _presentation_ack(),
            "topology changed",
        ),
        (
            _presentation_create_request(
                restore_geometry={"x": 5, "y": 20, "width": 1200, "height": 800}
            ),
            {**_presentation_ack(), "geometry_restore_outcome": "applied_exact"},
            "exact geometry restore",
        ),
        (
            _presentation_create_request(),
            {**_presentation_ack(), "geometry_restore_outcome": "applied_adjusted"},
            "unrequested restore",
        ),
        (
            _presentation_create_request(
                restore_geometry={"x": 10, "y": 20, "width": 1200, "height": 800}
            ),
            _presentation_ack(),
            "ignored requested geometry",
        ),
    ],
)
def test_presentation_create_exchange_rejects_cross_request_values(
    create_request: dict[str, Any], ack: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_presentation_create_exchange(
            create_request,
            ack,
            contract=_contract(),
            descriptor=_presentation_descriptor(),
            current_registry_digest=DIGEST_A,
        )


@pytest.mark.parametrize(
    ("ack", "match"),
    [
        (_presentation_ack(participant_id="participant:02"), "participant_id mismatch"),
        (_presentation_ack(driver_id="presentation.opaque-b"), "driver mismatch"),
        (
            _presentation_ack(capability_snapshot_digest=DIGEST_D),
            "capability snapshot mismatch",
        ),
    ],
)
def test_presentation_create_ack_rejects_cross_binding(
    ack: dict[str, Any], match: str
) -> None:
    with pytest.raises(validator.DriverContractError, match=match):
        validator.validate_presentation_create_ack(
            ack,
            contract=_contract(),
            descriptor=_presentation_descriptor(),
        )


def test_tui_projection_requires_one_matching_presentation_binding() -> None:
    validator.validate_participant_projection(
        launch_spec=_launch_spec(),
        runtime_ack=_runtime_ack(),
        presentation_ack=_presentation_ack(),
    )


def test_tui_projection_rejects_missing_presentation_binding() -> None:
    with pytest.raises(validator.DriverContractError, match="TUI participant lacks"):
        validator.validate_participant_projection(
            launch_spec=_launch_spec(),
            runtime_ack=_runtime_ack(),
            presentation_ack=None,
        )


def test_headless_projection_has_no_placeholder_window() -> None:
    validator.validate_participant_projection(
        launch_spec=_launch_spec(interaction_mode="headless"),
        runtime_ack=_runtime_ack(),
        presentation_ack=None,
    )


def test_headless_projection_rejects_presentation_binding() -> None:
    with pytest.raises(validator.DriverContractError, match="headless participant"):
        validator.validate_participant_projection(
            launch_spec=_launch_spec(interaction_mode="headless"),
            runtime_ack=_runtime_ack(),
            presentation_ack=_presentation_ack(),
        )


def test_projection_rejects_cross_participant_presentation() -> None:
    presentation_ack = _presentation_ack()
    presentation_ack["context"]["participant_id"] = "participant:02"
    presentation_ack["binding"]["participant_id"] = "participant:02"

    with pytest.raises(validator.DriverContractError, match="participant_id mismatch"):
        validator.validate_participant_projection(
            launch_spec=_launch_spec(),
            runtime_ack=_runtime_ack(),
            presentation_ack=presentation_ack,
        )


def test_projection_rejects_cross_runtime_presentation_binding() -> None:
    presentation_ack = _presentation_ack(runtime_binding_id="binding:runtime-02")

    with pytest.raises(validator.DriverContractError, match="binding join mismatch"):
        validator.validate_participant_projection(
            launch_spec=_launch_spec(),
            runtime_ack=_runtime_ack(),
            presentation_ack=presentation_ack,
        )


def test_contract_contains_no_vendor_or_platform_product_dependency() -> None:
    text = (
        REPO_ROOT / validator.CONTRACT_RELATIVE_PATH
    ).read_text(encoding="utf-8").casefold()

    assert "codex" not in text
    assert "claude" not in text
    assert "iterm" not in text
    assert "nsxpc" not in text
    assert "machservice" not in text
    assert "explicit_recreate" in text
    assert "exact_resume" in text


def test_validator_cli_is_path_redacted_and_read_only() -> None:
    result = os.popen(
        f"{sys.executable} {SCRIPTS / 'validate_ai_collab_driver_contract.py'}"
    ).read()
    payload = json.loads(result)

    assert payload["status"] == "valid"
    assert payload["state_mutated"] is False
    assert str(REPO_ROOT) not in result
