# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
import sys
import time
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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECURITY_ADAPTER = ROOT / "scripts" / "ai_collab_default_security_adapter.py"


class FakeSecurityAdapter:
    def __init__(self) -> None:
        self.outcome = "approved"
        self.status = "granted"
        self.observe_calls = 0
        self.change_subject = False
        self.decision_offset_ms = 0
        self.reason_code_override: str | None = None
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
            "reason_code": (
                None
                if self.outcome == "approved"
                else self.reason_code_override or "user.denied"
            ),
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


def _call_default_security_adapter(
    subject: Mapping[str, Any],
) -> subprocess.CompletedProcess[bytes]:
    request = {
        "security_adapter_protocol_version": 1,
        "adapter_id": "ai-collab-security-adapter",
        "operation": "observe",
        "payload": {
            "permission_ids": ["permission.project-storage"],
            "private_subject": dict(subject),
            "captured_at_epoch_ms": int(time.time() * 1000),
        },
    }
    return subprocess.run(
        [sys.executable, str(DEFAULT_SECURITY_ADAPTER)],
        input=json.dumps(request).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
        check=False,
    )


def _observe_with_default_adapter(subject: Mapping[str, Any]) -> dict[str, Any]:
    completed = _call_default_security_adapter(subject)
    assert completed.returncode == 0, completed.stderr.decode("utf-8")
    reply = json.loads(completed.stdout)
    assert reply["outcome"] == "completed"
    return reply["result"]["observations"][0]


def _recovery_inventory_subject(
    workspace: Path,
    *,
    prior_operation_kind: str = "destroy",
) -> dict[str, Any]:
    root = workspace.lstat()
    observed: list[dict[str, Any]] = []
    for entry in sorted(workspace.iterdir(), key=lambda item: item.name):
        details = entry.lstat()
        observed.append(
            {
                "name": entry.name,
                "device": details.st_dev,
                "inode": details.st_ino,
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
                "kind": (
                    "directory"
                    if stat.S_ISDIR(details.st_mode)
                    else "regular"
                    if stat.S_ISREG(details.st_mode)
                    else "other"
                ),
            }
        )
    path_identity_digest = canonical_json_sha256(
        {
            "workspace_id": workspace.name,
            "device": root.st_dev,
            "inode": root.st_ino,
            "uid": root.st_uid,
            "mode": stat.S_IMODE(root.st_mode),
        }
    )
    return {
        "subject_kind": "project-storage-recovery",
        "workspace_path": str(workspace),
        "workspace_id": workspace.name,
        "expected_inventory_digest": canonical_json_sha256(
            {
                "workspace_id": workspace.name,
                "workspace_path_identity_digest": path_identity_digest,
                "entries": observed,
            }
        ),
        "allowed_entry_names": [item["name"] for item in observed],
        "prior_operation_kind": prior_operation_kind,
        "prior_operation_claim_digest": "c" * 64,
    }


def _recovery_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace-recovery-proof"
    workspace.mkdir(mode=0o700)
    bundle = workspace / "bundle"
    bundle.mkdir(mode=0o700)
    return workspace, bundle


def _assert_default_adapter_fails_closed(subject: Mapping[str, Any]) -> None:
    completed = _call_default_security_adapter(subject)
    if completed.returncode != 0:
        assert completed.stdout == b""
        assert completed.stderr == b""
        return
    reply = json.loads(completed.stdout)
    observation = reply["result"]["observations"][0]
    assert observation["status"] == "denied"
    assert observation["provider_error_code"] == "project-storage.subject-not-proven"


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


def test_default_security_adapter_proves_only_an_exact_empty_workspace_husk(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-empty-proof"
    workspace.mkdir(mode=0o700)
    details = workspace.lstat()
    husk_digest = canonical_json_sha256(
        {
            "path_identity": {
                "workspace_id": workspace.name,
                "device": details.st_dev,
                "inode": details.st_ino,
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
            },
            "entries": [],
        }
    )
    subject = {
        "subject_kind": "empty-project-storage",
        "workspace_path": str(workspace),
        "expected_binding_state": "absent",
        "expected_husk_digest": husk_digest,
    }

    granted = _observe_with_default_adapter(subject)
    assert granted["status"] == "granted"
    assert granted["provider_error_code"] is None

    unexpected = workspace / "never-delete.txt"
    unexpected.write_text("preserve\n", encoding="utf-8")
    denied = _observe_with_default_adapter(subject)
    assert denied["status"] == "denied"
    assert denied["provider_error_code"] == "project-storage.subject-not-proven"
    assert unexpected.read_text(encoding="utf-8") == "preserve\n"

    original = tmp_path / "workspace-original-proof"
    workspace.rename(original)
    workspace.mkdir(mode=0o700)
    replaced = _observe_with_default_adapter(subject)
    assert replaced["status"] == "denied"
    assert replaced["provider_error_code"] == "project-storage.subject-not-proven"
    assert (original / unexpected.name).read_text(encoding="utf-8") == "preserve\n"

    os.chmod(tmp_path, 0o700)
    missing = tmp_path / "workspace-missing-proof"
    parent_details = tmp_path.lstat()
    missing_husk_digest = canonical_json_sha256(
        {
            "path_absence": {
                "workspace_id": missing.name,
                "parent_device": parent_details.st_dev,
                "parent_inode": parent_details.st_ino,
                "parent_uid": parent_details.st_uid,
                "parent_mode": stat.S_IMODE(parent_details.st_mode),
                "missing_name": missing.name,
            },
            "entries": [],
        }
    )
    missing_subject = {
        "subject_kind": "empty-project-storage",
        "workspace_path": str(missing),
        "expected_binding_state": "ready",
        "expected_husk_digest": missing_husk_digest,
    }
    missing_granted = _observe_with_default_adapter(missing_subject)
    assert missing_granted["status"] == "granted"
    assert missing_granted["provider_error_code"] is None

    missing.mkdir(mode=0o700)
    missing_replaced = _observe_with_default_adapter(missing_subject)
    assert missing_replaced["provider_error_code"] == (
        "project-storage.subject-not-proven"
    )


@pytest.mark.parametrize("prior_operation_kind", ["destroy", "repair"])
def test_default_security_adapter_allows_two_exact_recovery_inventory_observations(
    tmp_path: Path,
    prior_operation_kind: str,
) -> None:
    workspace, bundle = _recovery_workspace(tmp_path)
    sentinel = bundle / "employee-wip.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    subject = _recovery_inventory_subject(
        workspace, prior_operation_kind=prior_operation_kind
    )

    first = _observe_with_default_adapter(subject)
    second = _observe_with_default_adapter(subject)

    assert first["status"] == second["status"] == "granted"
    assert first["provider_error_code"] is None
    assert second["provider_error_code"] is None
    assert first["subject_digest"] == second["subject_digest"]
    assert first["evidence_digest"] == second["evidence_digest"]
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize(
    "mutation",
    [
        "path",
        "inventory",
        "root-inode",
        "entry-inode",
        "root-mode",
        "entry-mode",
        "type",
        "symlink",
    ],
)
def test_default_security_adapter_recovery_inventory_drift_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    workspace, bundle = _recovery_workspace(tmp_path)
    sentinel = bundle / "employee-wip.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    subject = _recovery_inventory_subject(workspace)
    assert _observe_with_default_adapter(subject)["status"] == "granted"

    preserved = bundle
    if mutation == "path":
        preserved = tmp_path / "workspace-recovery-moved"
        workspace.rename(preserved)
    elif mutation == "inventory":
        extra = workspace / "opaque-owner-entry"
        extra.mkdir(mode=0o700)
    elif mutation == "root-inode":
        original_root = tmp_path / "original-workspace-inode"
        workspace.rename(original_root)
        workspace.mkdir(mode=0o700)
        (original_root / bundle.name).rename(bundle)
    elif mutation == "entry-inode":
        preserved = tmp_path / "original-bundle-inode"
        bundle.rename(preserved)
        bundle.mkdir(mode=0o700)
    elif mutation == "root-mode":
        workspace.chmod(0o755)
    elif mutation == "entry-mode":
        bundle.chmod(0o755)
    elif mutation == "type":
        preserved = tmp_path / "original-bundle-type"
        bundle.rename(preserved)
        bundle.write_text("replacement\n", encoding="utf-8")
        bundle.chmod(0o600)
    else:
        preserved = tmp_path / "original-bundle-symlink"
        bundle.rename(preserved)
        bundle.symlink_to(preserved, target_is_directory=True)

    _assert_default_adapter_fails_closed(subject)

    if mutation == "path":
        assert (preserved / "bundle" / sentinel.name).read_text(
            encoding="utf-8"
        ) == "preserve\n"
    elif mutation in {"entry-inode", "type", "symlink"}:
        assert (preserved / sentinel.name).read_text(encoding="utf-8") == "preserve\n"
    else:
        assert sentinel.read_text(encoding="utf-8") == "preserve\n"


@pytest.mark.parametrize("ownership_scope", ["root", "entry"])
def test_default_security_adapter_recovery_uid_mismatch_or_unowned_entry_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ownership_scope: str,
) -> None:
    workspace, bundle = _recovery_workspace(tmp_path)
    sentinel = bundle / "employee-wip.txt"
    sentinel.write_text("preserve\n", encoding="utf-8")
    subject = _recovery_inventory_subject(workspace)
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    adapter = importlib.import_module("ai_collab_default_security_adapter")
    assert adapter._project_recovery_subject(subject)[2] is None
    actual_uid = os.getuid()
    if ownership_scope == "root":
        monkeypatch.setattr(adapter.os, "getuid", lambda: actual_uid + 1)
    else:
        original_lstat = adapter.Path.lstat

        def lstat_with_unowned_entry(path: Path) -> os.stat_result:
            details = original_lstat(path)
            if path == bundle:
                values = list(details)
                values[4] = actual_uid + 1
                return os.stat_result(values)
            return details

        monkeypatch.setattr(adapter.Path, "lstat", lstat_with_unowned_entry)

    with pytest.raises(adapter.AdapterError):
        adapter._project_recovery_subject(subject)

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_default_security_adapter_timeout_is_bounded_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    adapter = importlib.import_module("ai_collab_default_security_adapter")
    monkeypatch.setattr(adapter, "CONFIRMATION_TIMEOUT_SECONDS", 0.01)

    returncode, stdout, timed_out = adapter._present_confirmation(("/bin/sleep", "5"))

    assert returncode == 0
    assert stdout == ""
    assert timed_out is True


@pytest.mark.parametrize(
    "field_drift",
    [
        "extra-field",
        "missing-field",
        "subject-kind",
        "workspace-id",
        "inventory-digest",
        "allowed-names",
        "duplicate-names",
        "prior-kind",
        "claim-digest",
    ],
)
def test_default_security_adapter_recovery_subject_field_drift_fails_closed(
    tmp_path: Path,
    field_drift: str,
) -> None:
    workspace, _bundle = _recovery_workspace(tmp_path)
    subject = _recovery_inventory_subject(workspace)
    assert _observe_with_default_adapter(subject)["status"] == "granted"
    drifted = dict(subject)
    if field_drift == "extra-field":
        drifted["unexpected"] = "reject"
    elif field_drift == "missing-field":
        drifted.pop("prior_operation_claim_digest")
    elif field_drift == "subject-kind":
        drifted["subject_kind"] = "project-storage"
    elif field_drift == "workspace-id":
        drifted["workspace_id"] = "workspace-other"
    elif field_drift == "inventory-digest":
        drifted["expected_inventory_digest"] = "d" * 64
    elif field_drift == "allowed-names":
        drifted["allowed_entry_names"] = []
    elif field_drift == "duplicate-names":
        drifted["allowed_entry_names"] = ["bundle", "bundle"]
    elif field_drift == "prior-kind":
        drifted["prior_operation_kind"] = "status"
    else:
        drifted["prior_operation_claim_digest"] = "not-a-digest"

    _assert_default_adapter_fails_closed(drifted)


@pytest.mark.parametrize("field_drift", ["path", "prior-kind", "claim-digest"])
def test_default_security_adapter_recovery_valid_field_drift_changes_subject_digest(
    tmp_path: Path,
    field_drift: str,
) -> None:
    parent = tmp_path / "first-parent"
    parent.mkdir(mode=0o700)
    workspace, _bundle = _recovery_workspace(parent)
    subject = _recovery_inventory_subject(workspace)
    first = _observe_with_default_adapter(subject)
    drifted = dict(subject)
    if field_drift == "path":
        second_parent = tmp_path / "second-parent"
        second_parent.mkdir(mode=0o700)
        relocated = second_parent / workspace.name
        workspace.rename(relocated)
        drifted["workspace_path"] = str(relocated)
    elif field_drift == "prior-kind":
        drifted["prior_operation_kind"] = "repair"
    else:
        drifted["prior_operation_claim_digest"] = "d" * 64

    second = _observe_with_default_adapter(drifted)

    assert first["status"] == second["status"] == "granted"
    assert first["subject_digest"] != second["subject_digest"]


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


def test_confirmation_timeout_has_distinct_authorization_code(tmp_path: Path) -> None:
    adapter = FakeSecurityAdapter()
    adapter.outcome = "denied"
    adapter.reason_code_override = "confirmation.timeout"
    coordinator = SecurityCoordinator(tmp_path, adapter)

    with pytest.raises(SecurityError, match="timed out") as caught:
        _authorize(coordinator, _request("scenario.destroy"))

    assert caught.value.code == "auth.confirmation-timeout"
    assert caught.value.retryable is True
    chain = next(
        iter(json.loads(coordinator.state_path.read_text(encoding="utf-8"))["chains"].values())
    )
    assert chain["status"] == "denied"
    assert chain["decision"]["reason_code"] == "confirmation.timeout"
    assert chain["authorization"] is None
    assert chain["consumption"] is None


def test_denied_confirmation_intent_can_be_retried_and_consumed(
    tmp_path: Path,
) -> None:
    adapter = FakeSecurityAdapter()
    adapter.outcome = "denied"
    adapter.reason_code_override = "confirmation.timeout"
    coordinator = SecurityCoordinator(tmp_path, adapter)

    with pytest.raises(SecurityError, match="timed out") as first:
        _authorize(coordinator, _request("scenario.destroy", request_id="timeout-one"))
    with pytest.raises(SecurityError, match="timed out") as second:
        _authorize(coordinator, _request("scenario.destroy", request_id="timeout-two"))

    assert first.value.code == second.value.code == "auth.confirmation-timeout"
    state = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    request_digest = next(iter(state["chains"]))
    chain = state["chains"][request_digest]
    assert chain["status"] == "denied"
    assert [item["decision"]["reason_code"] for item in chain["denied_history"]] == [
        "confirmation.timeout",
        "confirmation.timeout",
    ]

    adapter.outcome = "approved"
    consumption = _authorize(
        coordinator,
        _request("scenario.destroy", request_id="approved-after-timeout"),
    )

    state = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    chain = state["chains"][request_digest]
    assert chain["status"] == "consumed"
    assert chain["request_id"] == "approved-after-timeout"
    assert chain["authorization"] is not None
    assert chain["consumption"] == consumption
    assert len(chain["denied_history"]) == 2

    with pytest.raises(SecurityError, match="already consumed") as replayed:
        _authorize(
            coordinator,
            _request("scenario.destroy", request_id="consumed-retry"),
        )
    assert replayed.value.code == "auth.authorization-replayed"


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


@pytest.mark.parametrize("restart_before_reconcile", [False, True])
def test_unknown_outcome_reconcile_is_exact_idempotent_and_durable(
    tmp_path: Path,
    restart_before_reconcile: bool,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    consumption = _authorize(
        coordinator,
        _request(
            "scenario.destroy",
            request_id=(
                "reconcile-unknown-existing"
                if restart_before_reconcile
                else "reconcile-unknown-none"
            ),
        ),
    )
    request_digest = consumption["operation_request_digest"]
    if restart_before_reconcile:
        coordinator.start_host()

    coordinator.reconcile_unknown_outcome(request_digest)
    first = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    coordinator.reconcile_unknown_outcome(request_digest)
    second = json.loads(coordinator.state_path.read_text(encoding="utf-8"))

    expected = {
        "outcome": "unknown",
        "operation_id": None,
        "result_digest": None,
    }
    assert next(iter(first["chains"].values()))["operation_outcome"] == expected
    assert second == first

    recovered = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    recovered.start_host()
    assert json.loads(recovered.state_path.read_text(encoding="utf-8")) == second


@pytest.mark.parametrize("restart_before_reconcile", [False, True])
def test_completed_outcome_reconcile_is_exact_idempotent_and_durable(
    tmp_path: Path,
    restart_before_reconcile: bool,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    consumption = _authorize(
        coordinator,
        _request(
            "scenario.destroy",
            request_id=(
                "reconcile-completed-unknown"
                if restart_before_reconcile
                else "reconcile-completed-none"
            ),
        ),
    )
    request_digest = consumption["operation_request_digest"]
    if restart_before_reconcile:
        coordinator.start_host()

    result = {"scenario": {"observed_state": "destroyed"}, "unregistered": True}
    coordinator.reconcile_completed_outcome(
        request_digest,
        operation_id="operation-completed",
        result=result,
    )
    first = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    coordinator.reconcile_completed_outcome(
        request_digest,
        operation_id="operation-completed",
        result=result,
    )
    second = json.loads(coordinator.state_path.read_text(encoding="utf-8"))

    expected = {
        "outcome": "completed",
        "operation_id": "operation-completed",
        "result_digest": canonical_json_sha256(result),
    }
    assert next(iter(first["chains"].values()))["operation_outcome"] == expected
    assert second == first

    recovered = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    recovered.start_host()
    assert json.loads(recovered.state_path.read_text(encoding="utf-8")) == second


@pytest.mark.parametrize("restart_before_reconcile", [False, True])
def test_failed_outcome_reconcile_is_exact_idempotent_and_durable(
    tmp_path: Path,
    restart_before_reconcile: bool,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    consumption = _authorize(
        coordinator,
        _request(
            "scenario.destroy",
            request_id=(
                "reconcile-failed-unknown"
                if restart_before_reconcile
                else "reconcile-failed-none"
            ),
        ),
    )
    request_digest = consumption["operation_request_digest"]
    if restart_before_reconcile:
        coordinator.start_host()

    coordinator.reconcile_failed_outcome(request_digest)
    first = json.loads(coordinator.state_path.read_text(encoding="utf-8"))
    coordinator.reconcile_failed_outcome(request_digest)
    second = json.loads(coordinator.state_path.read_text(encoding="utf-8"))

    expected = {
        "outcome": "failed",
        "operation_id": None,
        "result_digest": None,
    }
    assert next(iter(first["chains"].values()))["operation_outcome"] == expected
    assert second == first

    recovered = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    recovered.start_host()
    assert json.loads(recovered.state_path.read_text(encoding="utf-8")) == second


@pytest.mark.parametrize("terminal_outcome", ["completed", "failed"])
def test_unknown_outcome_cannot_overwrite_a_terminal_reconcile(
    tmp_path: Path,
    terminal_outcome: str,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    consumption = _authorize(
        coordinator,
        _request(
            "scenario.destroy",
            request_id=f"reconcile-{terminal_outcome}-is-terminal",
        ),
    )
    request_digest = consumption["operation_request_digest"]
    if terminal_outcome == "completed":
        coordinator.reconcile_completed_outcome(
            request_digest,
            operation_id="operation-terminal",
            result={"unregistered": True},
        )
    else:
        coordinator.reconcile_failed_outcome(request_digest)
    before = json.loads(coordinator.state_path.read_text(encoding="utf-8"))

    with pytest.raises(SecurityError, match="outcome fence differs") as caught:
        coordinator.reconcile_unknown_outcome(request_digest)
    assert caught.value.code == "security.outcome-invalid"
    assert json.loads(coordinator.state_path.read_text(encoding="utf-8")) == before

    recovered = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    recovered.start_host()
    assert json.loads(recovered.state_path.read_text(encoding="utf-8")) == before


def test_failed_outcome_cannot_be_overwritten_by_completed_reconcile(
    tmp_path: Path,
) -> None:
    coordinator = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    consumption = _authorize(
        coordinator,
        _request("scenario.destroy", request_id="reconcile-failed-is-terminal"),
    )
    request_digest = consumption["operation_request_digest"]
    coordinator.reconcile_failed_outcome(request_digest)
    before = json.loads(coordinator.state_path.read_text(encoding="utf-8"))

    with pytest.raises(SecurityError, match="outcome fence differs") as caught:
        coordinator.reconcile_completed_outcome(
            request_digest,
            operation_id="operation-must-not-replace-failure",
            result={"unregistered": True},
        )
    assert caught.value.code == "security.outcome-invalid"
    assert json.loads(coordinator.state_path.read_text(encoding="utf-8")) == before

    recovered = SecurityCoordinator(tmp_path, FakeSecurityAdapter())
    recovered.start_host()
    assert json.loads(recovered.state_path.read_text(encoding="utf-8")) == before
