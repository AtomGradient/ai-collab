# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ai_collab import security as security_module
from ai_collab.protocol import (
    CONTRACT_VERSION,
    OPERATION_BY_ID,
    OPERATION_DESCRIPTORS,
    OPERATION_REGISTRY_DIGEST,
    canonical_json_sha256,
)
from ai_collab.security import (
    HIGH_RISK_OPERATIONS,
    SecurityCoordinator,
    SecurityError,
)


class FakeSecurityAdapter:
    def __init__(self) -> None:
        self.outcome = "approved"
        self.status = "granted"
        self.observe_calls = 0
        self.change_subject = False
        self.decision_offset_ms = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        del timeout_seconds
        self.calls.append((operation, dict(payload)))
        if operation == "observe":
            self.observe_calls += 1
            suffix = "2" if self.change_subject and self.observe_calls > 1 else "1"
            return {
                "observations": [
                    {
                        "permission_id": permission_id,
                        "subject_digest": suffix * 64,
                        "status": self.status,
                        "observed_at_epoch_ms": payload["captured_at_epoch_ms"],
                        "valid_until_epoch_ms": payload["captured_at_epoch_ms"]
                        + 10_000,
                        "evidence_digest": "3" * 64,
                        "provider_error_code": None,
                        "remediation_ref": None,
                    }
                    for permission_id in payload["permission_ids"]
                ]
            }
        challenge = payload["challenge"]
        return {
            "challenge_digest": canonical_json_sha256(challenge),
            "outcome": self.outcome,
            "decided_at_epoch_ms": (
                challenge["issued_at_epoch_ms"] + self.decision_offset_ms
            ),
            "presenter_instance_digest": "4" * 64,
            "decision_evidence_digest": "5" * 64,
            "reason_code": None if self.outcome == "approved" else "user.denied",
        }


def _request(operation: str, *, request_id: str = "request-high-risk") -> dict[str, Any]:
    descriptor = OPERATION_BY_ID[operation]
    target = (
        {
            "scope": "participant",
            "project_instance_id": "project-one",
            "scenario_id": "scenario-one",
            "participant_id": "participant-one",
        }
        if descriptor["target_scope"] == "participant"
        else {
            "scope": "scenario",
            "project_instance_id": "project-one",
            "scenario_id": "scenario-one",
        }
    )
    payload: dict[str, Any] = {
        "scenario_generation": 1,
        "scenario_state_revision": 7,
    }
    if operation == "participant.force-stop":
        payload["participant_state_revision"] = 5
    elif operation == "resource.break":
        payload.update({"lease_id": "lease-one", "lease_revision": 3})
    fence = {"host_generation": 2, "operation_generation": 7}
    if descriptor["target_scope"] == "participant":
        fence["participant_generation"] = 1
        fence["operation_generation"] = 5
    return {
        "message_type": "operation_request",
        "contract_version": CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation,
        "operation_schema_version": 1,
        "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
        "capability_proof": "private-proof",
        "target": target,
        "fence": fence,
        "payload": payload,
    }


def _authorize(
    coordinator: SecurityCoordinator,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    value = coordinator.authorize(
        request,
        OPERATION_BY_ID[request["operation"]],
        effect_preview={
            "schema_version": 1,
            "operation": request["operation"],
            "target_digest": canonical_json_sha256(request["target"]),
        },
        private_subject={"state_root": "/private/never-persist-this"},
    )
    assert value is not None
    return value


def test_runtime_matrix_exactly_covers_registry_and_high_risk_bindings(
    tmp_path: Path,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    matrix = coordinator.matrix
    assert matrix["operation_registry_digest"] == OPERATION_REGISTRY_DIGEST
    assert [
        value["operation_id"] for value in matrix["operation_bindings"]
    ] == [value["operation_id"] for value in OPERATION_DESCRIPTORS]
    protected = {
        value["operation_id"]
        for value in matrix["operation_bindings"]
        if value["confirmation_policy_ref"] is not None
    }
    assert protected == set(HIGH_RISK_OPERATIONS)
    for value in matrix["operation_bindings"]:
        if value["operation_id"] in protected:
            assert value["risk_class"] == "high"
            assert value["required_permission_ids"]
            assert value["effect_preview_schema_digest"] is not None


@pytest.mark.parametrize("operation", sorted(HIGH_RISK_OPERATIONS))
def test_high_risk_chain_is_consumed_before_operation_outcome(
    tmp_path: Path, operation: str
) -> None:
    adapter = FakeSecurityAdapter()
    coordinator = SecurityCoordinator(tmp_path, adapter)
    request = _request(operation, request_id=f"request-{operation}")
    consumption = _authorize(coordinator, request)

    state = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    chain = next(iter(state["chains"].values()))
    assert [value[0] for value in adapter.calls] == ["observe", "present", "observe"]
    assert chain["status"] == "consumed"
    assert chain["decision"]["outcome"] == "approved"
    assert chain["authorization"]["max_uses"] == 1
    assert chain["consumption"] == consumption
    assert chain["operation_outcome"] is None
    assert b"/private/never-persist-this" not in coordinator.state_path.read_bytes()

    coordinator.mark_outcome(
        consumption,
        outcome="completed",
        operation_id="operation-one",
        result={"ok": True},
    )
    state = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    chain = next(iter(state["chains"].values()))
    assert chain["operation_outcome"]["outcome"] == "completed"
    assert chain["operation_outcome"]["result_digest"] == canonical_json_sha256(
        {"ok": True}
    )


def test_denial_never_creates_authorization_or_consumption(tmp_path: Path) -> None:
    adapter = FakeSecurityAdapter()
    adapter.outcome = "denied"
    coordinator = SecurityCoordinator(tmp_path, adapter)
    request = _request("scenario.destroy")
    with pytest.raises(SecurityError, match="denied") as caught:
        _authorize(coordinator, request)
    assert caught.value.code == "auth.confirmation-denied"
    chain = next(
        iter(json.loads(coordinator.state_path.read_text(encoding="utf-8"))["chains"].values())
    )
    assert chain["status"] == "denied"
    assert chain["authorization"] is None
    assert chain["consumption"] is None


def test_non_granted_or_changed_permission_subject_fails_closed(tmp_path: Path) -> None:
    denied_adapter = FakeSecurityAdapter()
    denied_adapter.status = "denied"
    denied = SecurityCoordinator(tmp_path / "denied", denied_adapter)
    with pytest.raises(SecurityError, match="not currently granted") as caught:
        _authorize(denied, _request("resource.break", request_id="permission-denied"))
    assert caught.value.code == "auth.permission-denied"
    assert json.loads(denied.state_path.read_text(encoding="utf-8"))["chains"] == {}

    drift_adapter = FakeSecurityAdapter()
    drift_adapter.change_subject = True
    drift = SecurityCoordinator(tmp_path / "drift", drift_adapter)
    with pytest.raises(SecurityError, match="subject changed") as caught:
        _authorize(drift, _request("scenario.repair", request_id="subject-drift"))
    assert caught.value.code == "auth.permission-denied"
    assert json.loads(drift.state_path.read_text(encoding="utf-8"))["chains"] == {}


def test_initial_permission_snapshot_must_remain_current_at_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    moments = iter((1.0, 1.0, 62.0, 62.0))
    monkeypatch.setattr(security_module.time, "time", lambda: next(moments))
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())

    with pytest.raises(SecurityError, match="expired during confirmation") as caught:
        _authorize(coordinator, _request("scenario.destroy"))

    assert caught.value.code == "auth.permission-denied"
    assert json.loads(coordinator.state_path.read_text(encoding="utf-8"))["chains"] == {}


def test_presenter_cannot_issue_authorization_from_a_future_timestamp(
    tmp_path: Path,
) -> None:
    adapter = FakeSecurityAdapter()
    adapter.decision_offset_ms = 10_000
    coordinator = SecurityCoordinator(tmp_path, adapter)

    with pytest.raises(SecurityError, match="timestamp is invalid") as caught:
        _authorize(coordinator, _request("scenario.destroy"))

    assert caught.value.code == "security.invalid-reply"
    assert json.loads(coordinator.state_path.read_text(encoding="utf-8"))["chains"] == {}


def test_consumption_replay_fails_and_restart_marks_unknown_outcome(
    tmp_path: Path,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    request = _request("participant.force-stop")
    _authorize(coordinator, request)
    with pytest.raises(SecurityError, match="already consumed") as caught:
        _authorize(coordinator, request)
    assert caught.value.code == "auth.authorization-replayed"

    coordinator.start_host()
    chain = next(
        iter(json.loads(coordinator.state_path.read_text(encoding="utf-8"))["chains"].values())
    )
    assert chain["operation_outcome"] == {
        "outcome": "unknown",
        "operation_id": None,
        "result_digest": None,
    }
