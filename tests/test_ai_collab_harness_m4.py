# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_collab.delivery import DeliveryCoordinator  # noqa: E402
from ai_collab.protocol import canonical_json_sha256  # noqa: E402
import ai_collab_participant_driver as participant_driver  # noqa: E402
import validate_ai_collab_policy_delivery_contract as frozen  # noqa: E402


@pytest.fixture(autouse=True)
def _ignore_any_real_runtime_profile_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the overlay at a path that does not exist, for every test here.

    Runtime profiles are now the sum of the shipped registry and an operator's
    overlay file. Without this, whether the profile assertions below pass would
    depend on whether the person running the suite happens to have an overlay in
    their own application-support directory. Tests that exercise the overlay set
    this variable again, and the later value wins.
    """
    monkeypatch.setenv(
        participant_driver.PROFILE_OVERLAY_ENVIRONMENT_KEY,
        str(tmp_path / "no-such-overlay.json"),
    )


def _participant(participant_id: str) -> dict[str, Any]:
    return {
        "scenario_id": "scenario-m4",
        "participant_id": participant_id,
        "participant_generation": 1,
        "state_revision": 3,
        "desired_state": "running",
        "observed_state": "ready",
        "interaction_mode": "tui",
        "launch_spec_digest": "a" * 64,
        "runtime_binding_id": f"runtime-{participant_id}",
        "presentation_binding_id": f"presentation-{participant_id}",
        "active_operation_id": None,
        "degraded": None,
        "journal_head_sequence": 10,
        "note": "",
    }


def _ref(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": value["scenario_id"],
        "participant_id": value["participant_id"],
        "participant_generation": value["participant_generation"],
    }


def test_product_m4_values_conform_to_frozen_f_contract() -> None:
    contract, state_contract, _ = frozen.validate_contract(repo_root=ROOT)
    sender = _participant("sender")
    receiver = _participant("receiver")
    participants = [sender, receiver]
    pack = {
        "policy_contract_version": 1,
        "policy_id": "policy.m4-dogfood",
        "policy_version": 1,
        "scenario_id": "scenario-m4",
        "default_effect": "deny",
        "assignments": [],
        "retry_profiles": [
            {"profile_id": "retry-m4", "max_attempts": 2, "backoff_ms": [0, 100]}
        ],
        "route_rules": [
            {
                "rule_id": "m4-exact-route",
                "sender": {"kind": "participant", "participant": _ref(sender)},
                "receiver": {"kind": "participant", "participant": _ref(receiver)},
                "message_kind": "collaboration.request",
                "effect": "allow",
                "retry_profile_id": "retry-m4",
            }
        ],
    }
    request = {
        "request_id": "route-m4",
        "message_id": "message-m4",
        "scenario_id": "scenario-m4",
        "sender": _ref(sender),
        "receiver_intent": {"kind": "participant", "participant": _ref(receiver)},
        "message_kind": "collaboration.request",
        "payload_digest": "b" * 64,
        "policy_snapshot": {
            "policy_id": pack["policy_id"],
            "policy_version": 1,
            "policy_digest": canonical_json_sha256(pack),
        },
    }
    decision, targets, profile = DeliveryCoordinator._resolve_route(  # noqa: SLF001
        pack, request, participants
    )
    frozen.validate_route_decision(
        pack,
        request,
        decision,
        participants,
        contract=contract,
        state_contract=state_contract,
    )
    queued = DeliveryCoordinator._enqueue_record(  # noqa: SLF001
        "delivery-m4", request, decision, sender, targets[0], profile
    )
    frozen.validate_delivery_enqueue(
        queued,
        pack,
        request,
        decision,
        participants,
        contract=contract,
        state_contract=state_contract,
    )
    attempted = copy.deepcopy(queued)
    DeliveryCoordinator._append_event(  # noqa: SLF001
        attempted,
        event="attempt_started",
        attempt_number=1,
        backoff_ms=0,
        transport_attempt_id="transport-m4",
        evidence_digest=None,
        error_code=None,
    )
    frozen.validate_delivery_transition(
        trigger="attempt", before=queued, after=attempted, contract=contract
    )
    delivery_ack = {
        "ack_kind": "delivered",
        "delivery_id": attempted["delivery_id"],
        "message_id": attempted["message_id"],
        "target": attempted["target"],
        "payload_digest": attempted["payload_digest"],
        "attempt_number": 1,
        "transport_attempt_id": "transport-m4",
    }
    delivered = copy.deepcopy(attempted)
    DeliveryCoordinator._accept_delivery(delivered, delivery_ack)  # noqa: SLF001
    frozen.validate_delivery_transition(
        trigger="matching_delivery_ack",
        before=attempted,
        after=delivered,
        contract=contract,
        ack=delivery_ack,
    )
    consumption_ack = {
        "ack_kind": "consumed",
        "delivery_id": delivered["delivery_id"],
        "message_id": delivered["message_id"],
        "target": delivered["target"],
        "payload_digest": delivered["payload_digest"],
        "attempt_number": 1,
        "transport_attempt_id": "transport-m4",
        "delivery_ack_digest": delivered["events"][-1]["evidence_digest"],
    }
    consumed = copy.deepcopy(delivered)
    DeliveryCoordinator._accept_consumption(consumed, consumption_ack)  # noqa: SLF001
    frozen.validate_delivery_transition(
        trigger="matching_consumption_ack",
        before=delivered,
        after=consumed,
        contract=contract,
        ack=consumption_ack,
    )
    assert consumed["state"] == "consumed"


def test_runtime_profiles_keep_generic_baseline_and_enable_vendor_identity_adapters() -> None:
    profiles = participant_driver._runtime_profiles()  # noqa: SLF001
    assert set(profiles) == {
        "runtime-profile.inert",
        "runtime-profile.codex",
        "runtime-profile.claude",
    }
    assert profiles["runtime-profile.inert"]["accepts_typed_delivery"] is False
    assert profiles["runtime-profile.inert"]["vendor_lifecycle"] is None
    for profile_id in (
        "runtime-profile.codex",
        "runtime-profile.claude",
    ):
        resolved = participant_driver.resolve(
            {
                "launch_spec": {
                    "driver_id": "runtime.generic-process",
                    "driver_contract_version": 2,
                    "interaction_mode": "tui",
                    "continuity_mode": "explicit_recreate",
                    "runtime_profile_ref": profile_id,
                    "model_binding": None,
                    "continuity_binding_ref": None,
                },
                "presentation_driver_id": "presentation.iterm2",
            }
        )
        assert resolved["runtime_descriptor"]["supports_vendor_session_identity"] is True
        assert "exact_resume" in resolved["runtime_descriptor"]["continuity_modes"]
        assert profiles[profile_id]["vendor_lifecycle"]["adapter_id"].startswith(
            "vendor-lifecycle."
        )
    product_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "ai_collab").glob("*.py")
    ).lower()
    assert "codex" not in product_source
    assert "claude" not in product_source


def test_cli_startup_gates_allow_slow_vendor_ready_screens() -> None:
    profiles = participant_driver._runtime_profiles()  # noqa: SLF001

    assert (
        profiles["runtime-profile.codex"]["startup_gate"]["timeout_seconds"]
        == 180
    )
    assert (
        profiles["runtime-profile.claude"]["startup_gate"]["timeout_seconds"]
        == 180
    )


def test_participant_templates_are_driver_data_not_host_vendor_logic() -> None:
    result = participant_driver.list_templates({})
    assert {item["template_id"] for item in result["templates"]} == {
        "runtime-profile.inert",
        "runtime-profile.codex",
        "runtime-profile.claude",
    }
    for item in result["templates"]:
        launch_spec = item["launch_spec"]
        assert launch_spec["driver_id"] == "runtime.generic-process"
        assert launch_spec["runtime_profile_ref"] == item["template_id"]
        assert (launch_spec["interaction_mode"] == "tui") == (
            item["presentation_driver_id"] == "presentation.iterm2"
        )


def test_template_display_names_are_registry_data_not_transformed_ids() -> None:
    """The label used to be the profile id with its prefix stripped and title
    cased, so ``runtime-profile.inert`` reached the employee picker as "Inert" —
    indistinguishable from a real agent."""
    names = {
        item["template_id"]: item["display_name"]
        for item in participant_driver.list_templates({})["templates"]
    }
    for template_id, display_name in names.items():
        assert display_name == participant_driver._runtime_profiles()[template_id][
            "display_name"
        ]
        assert "runtime-profile" not in display_name

    # The fixture must announce itself wherever it is shown, because it accepts
    # no delivery, opens no window and can never resume a conversation.
    assert "fixture" in names["runtime-profile.inert"].lower()
    assert names["runtime-profile.claude"] == "Claude"
    assert names["runtime-profile.codex"] == "Codex"


def test_only_the_inert_fixture_is_headless() -> None:
    """The App groups the picker on ``interaction_mode``, so this is the property
    that keeps the fixture out of the list an employee chooses from."""
    headless = {
        item["template_id"]
        for item in participant_driver.list_templates({})["templates"]
        if item["launch_spec"]["interaction_mode"] == "headless"
    }
    assert headless == {"runtime-profile.inert"}


def test_a_runtime_profile_without_a_display_name_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = json.loads(
        participant_driver.PROFILE_PATH.read_text(encoding="utf-8")
    )
    for profile in registry["profiles"]:
        del profile["display_name"]
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(participant_driver, "PROFILE_PATH", path)
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()


def test_a_runtime_profile_with_a_blank_display_name_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = json.loads(
        participant_driver.PROFILE_PATH.read_text(encoding="utf-8")
    )
    registry["profiles"][0]["display_name"] = "   "
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(participant_driver, "PROFILE_PATH", path)
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()


def _overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: Any) -> Path:
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(
        participant_driver.PROFILE_OVERLAY_ENVIRONMENT_KEY, str(path)
    )
    return path


def test_an_overlay_replaces_the_vendor_arguments_the_bundle_ships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason this mechanism exists.

    The shipped Codex profile passes an approval-bypass flag. The registry that
    carries it sits inside the signed bundle, so editing it there invalidates the
    signature; an operator who wants the vendor's own confirmations back has to be
    able to say so from a writable file.
    """
    shipped = participant_driver._runtime_profiles()  # noqa: SLF001
    assert any(
        argument.startswith("--dangerously")
        for argument in shipped["runtime-profile.codex"]["arguments"]
    )
    replacement = copy.deepcopy(shipped["runtime-profile.codex"])
    replacement["arguments"] = [
        argument
        for argument in replacement["arguments"]
        if not argument.startswith("--dangerously")
    ]
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [replacement]})

    profiles = participant_driver._runtime_profiles()  # noqa: SLF001

    assert not any(
        argument.startswith("--dangerously")
        for argument in profiles["runtime-profile.codex"]["arguments"]
    )
    # Replacing one profile leaves the rest of the registry alone.
    assert set(profiles) == set(shipped)
    assert profiles["runtime-profile.claude"] == (
        shipped["runtime-profile.claude"]
    )


def test_an_overlay_can_add_a_profile_the_bundle_does_not_ship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Harness that only ever drives the two CLIs we happen to use is not
    project-neutral. Adding a profile must not require a new build."""
    shipped = participant_driver._runtime_profiles()  # noqa: SLF001
    added = copy.deepcopy(shipped["runtime-profile.inert"])
    added["profile_id"] = "runtime-profile.local-tool"
    added["display_name"] = "Local Tool"
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [added]})

    profiles = participant_driver._runtime_profiles()  # noqa: SLF001

    assert set(profiles) == set(shipped) | {"runtime-profile.local-tool"}
    assert profiles["runtime-profile.local-tool"]["display_name"] == "Local Tool"


def test_a_broken_overlay_fails_instead_of_being_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignoring an unreadable overlay would launch the vendor CLI with the
    shipped approval flags after the operator had already decided otherwise."""
    path = tmp_path / "overlay.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv(
        participant_driver.PROFILE_OVERLAY_ENVIRONMENT_KEY, str(path)
    )
    with pytest.raises(participant_driver.DriverError) as failure:
        participant_driver._runtime_profiles()  # noqa: SLF001
    assert "overlay" in str(failure.value)


@pytest.mark.parametrize(
    "profile_id",
    [
        "MyTool",  # uppercase
        "codex",  # no namespace dot
        "my_tool",  # underscore
        "",  # empty
        "shell.zsh",  # reserved: collides with the fixed probe subject
        "presentation.iterm2",  # reserved: collides with the fixed probe subject
    ],
)
def test_profile_ids_must_live_in_the_namespaced_subject_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile_id: str
) -> None:
    """Profile ids double as environment.probe subject_refs. An id the
    supervisor's namespaced-id rule rejects — or one that collides with the
    fixed shell/presentation subjects — must fail at overlay load, not by
    erroring the entire diagnostics report later."""
    shipped = participant_driver._runtime_profiles()  # noqa: SLF001
    added = copy.deepcopy(shipped["runtime-profile.inert"])
    added["profile_id"] = profile_id
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [added]})
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()  # noqa: SLF001


def test_profile_display_names_stay_inside_the_supervisor_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped = participant_driver._runtime_profiles()  # noqa: SLF001
    added = copy.deepcopy(shipped["runtime-profile.inert"])
    added["profile_id"] = "runtime-profile.long-name"
    added["display_name"] = "x" * 121
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [added]})
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()  # noqa: SLF001


def test_an_overlay_is_held_to_the_registry_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same validation both sides. An operator-supplied profile cannot be looser
    than a shipped one, so a typo cannot quietly produce a half-configured
    participant."""
    shipped = participant_driver._runtime_profiles()  # noqa: SLF001
    loose = copy.deepcopy(shipped["runtime-profile.codex"])
    loose["display_name"] = "   "
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [loose]})
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()  # noqa: SLF001

    missing_key = copy.deepcopy(shipped["runtime-profile.codex"])
    del missing_key["process_match"]
    _overlay(tmp_path, monkeypatch, {"schema_version": 1, "profiles": [missing_key]})
    with pytest.raises(participant_driver.DriverError):
        participant_driver._runtime_profiles()  # noqa: SLF001


def test_no_overlay_means_the_shipped_registry_is_what_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        participant_driver.PROFILE_OVERLAY_ENVIRONMENT_KEY,
        str(tmp_path / "absent.json"),
    )
    profiles = participant_driver._runtime_profiles()  # noqa: SLF001
    registry = json.loads(
        participant_driver.PROFILE_PATH.read_text(encoding="utf-8")
    )
    assert set(profiles) == {row["profile_id"] for row in registry["profiles"]}


def _socket_status(
    *,
    present: bool = True,
    is_unix_socket: bool = True,
    owned_by_current_user: bool = True,
    api_server_configured: bool = True,
    api_server_enabled: bool = True,
) -> dict[str, bool]:
    return {
        "present": present,
        "is_unix_socket": is_unix_socket,
        "owned_by_current_user": owned_by_current_user,
        "local_only_ready": present and is_unix_socket and owned_by_current_user,
        "api_server_configured": api_server_configured,
        "api_server_enabled": api_server_enabled,
        "api_server_explicitly_disabled": (
            api_server_configured and not api_server_enabled
        ),
    }


@pytest.mark.parametrize(
    (
        "automation_status",
        "authorized",
        "target_running",
        "socket_status",
        "expected",
        "provider_error_code",
        "remediation",
    ),
    (
        ("authorized", True, True, _socket_status(), "granted", None, None),
        (
            "denied",
            False,
            True,
            _socket_status(),
            "denied",
            "iterm-presentation.automation-denied",
            "system-settings.automation",
        ),
        (
            "not_determined_no_prompt",
            False,
            True,
            _socket_status(),
            "not_determined",
            "iterm-presentation.automation-not-determined",
            "presentation.permission-request",
        ),
        (
            "authorized",
            True,
            False,
            _socket_status(),
            "unavailable",
            "iterm-presentation.target-not-running",
            "iterm-presentation.launch-target",
        ),
        (
            "authorized",
            True,
            True,
            _socket_status(
                present=False,
                is_unix_socket=False,
                owned_by_current_user=False,
                api_server_configured=False,
                api_server_enabled=False,
            ),
            "unavailable",
            "iterm-presentation.python-api-disabled",
            "iterm-presentation.enable-python-api",
        ),
        (
            "authorized",
            True,
            True,
            _socket_status(
                present=False,
                is_unix_socket=False,
                owned_by_current_user=False,
            ),
            "unavailable",
            "iterm-presentation.private-socket-missing",
            "iterm-presentation.restart-after-python-api",
        ),
        (
            "authorized",
            True,
            True,
            _socket_status(is_unix_socket=False),
            "unavailable",
            "iterm-presentation.private-socket-invalid",
            "iterm-presentation.reset-private-api-socket",
        ),
    ),
)
def test_presentation_permission_probe_is_no_prompt_and_actionable(
    monkeypatch: Any,
    automation_status: str,
    authorized: bool,
    target_running: bool,
    socket_status: dict[str, bool],
    expected: str,
    provider_error_code: str | None,
    remediation: str | None,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "automation_permission_status",
        lambda _bundle, **kwargs: {
            "status": automation_status,
            "authorized": authorized,
            "prompt_requested": kwargs.get("ask_user_if_needed", False),
        },
    )
    monkeypatch.setattr(
        participant_driver,
        "authentication_bypass_status",
        lambda: {"cookie_authentication_required": True},
    )
    monkeypatch.setattr(
        participant_driver,
        "private_unix_socket_status",
        lambda: socket_status,
    )
    monkeypatch.setattr(
        participant_driver,
        "_target_application_running",
        lambda _bundle: target_running,
    )
    observation = participant_driver.permission_probe({})[
        "permission_observations"
    ][0]
    assert observation["status"] == expected
    assert observation["provider_error_code"] == provider_error_code
    assert observation["remediation_ref"] == remediation
    assert observation["prompt_requested"] is False
    assert len(observation["evidence_digest"]) == 64


def test_presentation_permission_probe_rejects_authentication_bypass(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "automation_permission_status",
        lambda _bundle, **kwargs: {
            "status": "authorized",
            "authorized": True,
            "prompt_requested": kwargs.get("ask_user_if_needed", False),
        },
    )
    monkeypatch.setattr(
        participant_driver,
        "authentication_bypass_status",
        lambda: {"cookie_authentication_required": False},
    )
    monkeypatch.setattr(
        participant_driver,
        "private_unix_socket_status",
        _socket_status,
    )
    monkeypatch.setattr(
        participant_driver,
        "_target_application_running",
        lambda _bundle: True,
    )
    observation = participant_driver.permission_probe({})[
        "permission_observations"
    ][0]
    assert observation["status"] == "restricted"
    assert observation["provider_error_code"] == (
        "iterm-presentation.authentication-bypass-present"
    )


def test_tui_launch_preflight_fails_fast_with_actionable_reason(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "private_unix_socket_status",
        lambda: _socket_status(
            present=False,
            is_unix_socket=False,
            owned_by_current_user=False,
            api_server_configured=False,
            api_server_enabled=False,
        ),
    )
    monkeypatch.setattr(
        participant_driver,
        "_target_application_running",
        lambda _bundle: True,
    )

    with pytest.raises(participant_driver.PresentationPreflightError) as caught:
        participant_driver._require_iterm_private_api_ready()

    assert str(caught.value) == "iTerm private API is unavailable"
    assert (
        caught.value.provider_error_code
        == "iterm-presentation.python-api-disabled"
    )
    assert caught.value.remediation_ref == "iterm-presentation.enable-python-api"
    assert (
        participant_driver._launch_reason_code("iterm-preflight", caught.value)
        == "iterm.private-api-unavailable"
    )


def test_process_observation_unavailable_has_specific_reason_code() -> None:
    error = participant_driver.DriverError("owned process observation is unavailable")

    assert (
        participant_driver._launch_reason_code("initial-process", error)  # noqa: SLF001
        == "process.observation-unavailable"
    )
    assert (
        participant_driver._sender_auth_reason_code("send", error)  # noqa: SLF001
        == "process.observation-unavailable"
    )


def test_launch_failure_diagnostic_preserves_preflight_remediation(
    tmp_path: Path,
) -> None:
    error = participant_driver.PresentationPreflightError(
        "iterm-presentation.private-socket-missing",
        "iterm-presentation.restart-after-python-api",
    )

    participant_driver._record_launch_failure(
        tmp_path,
        stage="iterm-preflight",
        exc=error,
        cleanup_outcome="not-required",
    )

    diagnostic = json.loads((tmp_path / "launch-diagnostic.json").read_text())
    assert diagnostic == {
        "schema_version": 1,
        "outcome": "rejected",
        "stage": "iterm-preflight",
        "reason_code": "iterm.private-api-unavailable",
        "cleanup_outcome": "not-required",
        "provider_error_code": "iterm-presentation.private-socket-missing",
        "remediation_ref": "iterm-presentation.restart-after-python-api",
    }


def test_presentation_focus_restores_exact_owned_window_for_current_topology(
    monkeypatch: Any, tmp_path: Path
) -> None:
    topology_fingerprint = "a" * 64
    expected_geometry = {"x": 10, "y": 20, "width": 900, "height": 700}

    class Window:
        window_id = "window-1"

        def __init__(self) -> None:
            self.frame = SimpleNamespace(
                origin=SimpleNamespace(x=1, y=2),
                size=SimpleNamespace(width=600, height=400),
            )
            session = SimpleNamespace(
                session_id="session-1",
                async_get_variable=lambda _name: _async_value("123"),
            )
            self.tabs = [SimpleNamespace(sessions=[session], all_sessions=[session])]
            self.activated = False

        async def async_get_variable(self, _name: str) -> dict[str, Any]:
            return {"owner": "exact"}

        async def async_get_frame(self) -> Any:
            return self.frame

        async def async_set_frame(self, value: Any) -> None:
            self.frame = value

        async def async_activate(self) -> None:
            self.activated = True

    class App:
        def __init__(self, window: Window) -> None:
            self.window = window
            self.activated = False

        def get_window_by_id(self, value: str) -> Window | None:
            return self.window if value == self.window.window_id else None

        async def async_activate(self) -> None:
            self.activated = True

    window = Window()
    app = App(window)

    async def async_create() -> object:
        return object()

    async def async_get_app(_connection: object) -> App:
        return app

    def point(x: int, y: int) -> Any:
        return SimpleNamespace(x=x, y=y)

    def size(width: int, height: int) -> Any:
        return SimpleNamespace(width=width, height=height)

    def frame(origin: Any, dimensions: Any) -> Any:
        return SimpleNamespace(origin=origin, size=dimensions)

    module = SimpleNamespace(
        Connection=SimpleNamespace(async_create=async_create),
        async_get_app=async_get_app,
        util=SimpleNamespace(Point=point, Size=size, Frame=frame),
    )
    state = {
        "schema_version": 1,
        "status": "ready",
        "interaction_mode": "tui",
        "participant_generation": 3,
        "presentation_instance_id": "presentation-1",
        "runtime_binding_id": "runtime-1",
        "window_id": "window-1",
        "session_id": "session-1",
        "owner_marker": {"owner": "exact"},
        "display_topology_fingerprint": topology_fingerprint,
        "geometry": {"x": 1, "y": 2, "width": 600, "height": 400},
        "geometry_by_topology": {topology_fingerprint: expected_geometry},
        "pid": 123,
        "pgid": 123,
    }
    written: dict[str, Any] = {}
    monkeypatch.setattr(
        participant_driver, "_topology", lambda: ({}, topology_fingerprint)
    )
    monkeypatch.setattr(
        participant_driver, "_validate_owned_foreground_job", lambda *_: None
    )
    monkeypatch.setattr(
        participant_driver,
        "_write_private",
        lambda _path, value: written.update(copy.deepcopy(value)),
    )

    result = asyncio.run(
        participant_driver._presentation_action_async(  # noqa: SLF001
            module, tmp_path, state, action="focus"
        )
    )["presentation"]

    assert result["focused"] is True
    assert result["restore_outcome"] == "applied_exact"
    assert result["geometry"] == expected_geometry
    assert result["display_topology_fingerprint"] == topology_fingerprint
    assert window.activated is True
    assert app.activated is True
    assert written["geometry_by_topology"][topology_fingerprint] == expected_geometry

    changed_topology = "b" * 64
    current_geometry = {"x": 30, "y": 40, "width": 1000, "height": 720}
    window.frame = frame(
        point(current_geometry["x"], current_geometry["y"]),
        size(current_geometry["width"], current_geometry["height"]),
    )
    monkeypatch.setattr(
        participant_driver, "_topology", lambda: ({}, changed_topology)
    )
    changed = asyncio.run(
        participant_driver._presentation_action_async(  # noqa: SLF001
            module, tmp_path, state, action="focus"
        )
    )["presentation"]
    assert changed["restore_outcome"] == "not_available"
    assert changed["geometry"] == current_geometry
    assert written["geometry_by_topology"][changed_topology] == current_geometry


async def _async_value(value: Any) -> Any:
    return value


def test_shipped_profiles_match_normal_local_permission_modes() -> None:
    profiles = participant_driver._runtime_profiles()  # noqa: SLF001
    assert profiles["runtime-profile.claude"]["arguments"] == [
        "--dangerously-skip-permissions",
        "--system-prompt",
        ".",
    ]
    assert profiles["runtime-profile.codex"]["arguments"] == [
        "--dangerously-bypass-approvals-and-sandbox",
        "--no-alt-screen",
    ]


def test_foreground_job_accepts_exact_owned_descendant(monkeypatch: Any) -> None:
    relationships = {
        3003: {"parent_pid": 3002, "process_group_id": 3001},
        3002: {"parent_pid": 3001, "process_group_id": 3001},
        3001: {"parent_pid": 100, "process_group_id": 3001},
    }
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: relationships[pid],
    )
    participant_driver._validate_owned_foreground_job(  # noqa: SLF001
        3003, {"pid": 3001, "pgid": 3001}
    )


def test_foreground_job_rejects_same_group_non_descendant(monkeypatch: Any) -> None:
    relationships = {
        4003: {"parent_pid": 4002, "process_group_id": 3001},
        4002: {"parent_pid": 100, "process_group_id": 3001},
        100: {"parent_pid": 1, "process_group_id": 3001},
    }
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: relationships[pid],
    )
    try:
        participant_driver._validate_owned_foreground_job(  # noqa: SLF001
            4003, {"pid": 3001, "pgid": 3001}
        )
    except participant_driver.DriverError as exc:
        assert str(exc) == "iTerm foreground job is not an owned descendant"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("same-group non-descendant foreground job was accepted")


def test_foreground_job_accepts_absent_stale_job_pid_when_owned_group_is_foreground(
    monkeypatch: Any,
) -> None:
    def unavailable(pid: int) -> dict[str, int]:
        raise participant_driver.DriverError("owned process relationship is unavailable")

    def absent(pid: int, signal_number: int) -> None:
        assert pid == 3999
        assert signal_number == 0
        raise ProcessLookupError

    monkeypatch.setattr(participant_driver, "_process_relationship", unavailable)
    monkeypatch.setattr(participant_driver.os, "kill", absent)
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 3001,
            "ps": "/opt/homebrew/bin/claude",
            "identity_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        participant_driver, "_terminal_foreground_process_group", lambda pid: 3001
    )

    participant_driver._validate_owned_foreground_job(  # noqa: SLF001
        3999,
        {
            "pid": 3001,
            "pgid": 3001,
            "process_identity_sha256": "a" * 64,
        },
    )


def test_foreground_job_rejects_unrelated_live_job_pid(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: (_ for _ in ()).throw(
            participant_driver.DriverError(
                "owned process relationship is unavailable"
            )
        ),
    )
    monkeypatch.setattr(participant_driver.os, "kill", lambda pid, signal_number: None)
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("live jobPid must fail before root fallback")
        ),
    )

    with pytest.raises(
        participant_driver.DriverError,
        match="^owned process relationship is unavailable$",
    ):
        participant_driver._validate_owned_foreground_job(  # noqa: SLF001
            3999,
            {
                "pid": 3001,
                "pgid": 3001,
                "process_identity_sha256": "a" * 64,
            },
        )


def test_foreground_job_rejects_stale_job_pid_after_root_identity_drift(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: (_ for _ in ()).throw(
            participant_driver.DriverError(
                "owned process relationship is unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        participant_driver.os,
        "kill",
        lambda pid, signal_number: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 3001,
            "ps": "/opt/homebrew/bin/claude",
            "identity_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        participant_driver, "_terminal_foreground_process_group", lambda pid: 3001
    )

    with pytest.raises(
        participant_driver.DriverError,
        match="^owned foreground process group drifted$",
    ):
        participant_driver._validate_owned_foreground_job(  # noqa: SLF001
            3999,
            {
                "pid": 3001,
                "pgid": 3001,
                "process_identity_sha256": "a" * 64,
            },
        )


def test_foreground_job_rejects_stale_job_pid_after_root_group_drift(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: (_ for _ in ()).throw(
            participant_driver.DriverError(
                "owned process relationship is unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        participant_driver.os,
        "kill",
        lambda pid, signal_number: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 4001,
            "ps": "/opt/homebrew/bin/claude",
            "identity_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        participant_driver, "_terminal_foreground_process_group", lambda pid: 3001
    )

    with pytest.raises(
        participant_driver.DriverError,
        match="^owned foreground process group drifted$",
    ):
        participant_driver._validate_owned_foreground_job(  # noqa: SLF001
            3999,
            {
                "pid": 3001,
                "pgid": 3001,
                "process_identity_sha256": "a" * 64,
            },
        )


def test_foreground_job_rejects_absent_stale_job_pid_for_another_foreground_group(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: (_ for _ in ()).throw(
            participant_driver.DriverError(
                "owned process relationship is unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        participant_driver.os,
        "kill",
        lambda pid, signal_number: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 3001,
            "ps": "/opt/homebrew/bin/claude",
            "identity_sha256": "a" * 64,
        },
    )
    monkeypatch.setattr(
        participant_driver, "_terminal_foreground_process_group", lambda pid: 4001
    )

    with pytest.raises(
        participant_driver.DriverError,
        match="owned foreground process group drifted",
    ):
        participant_driver._validate_owned_foreground_job(  # noqa: SLF001
            3999,
            {
                "pid": 3001,
                "pgid": 3001,
                "process_identity_sha256": "a" * 64,
            },
        )


class _JobPidSession:
    async def async_get_variable(self, name: str) -> str:
        assert name == "jobPid"
        return "9653"


def test_wait_job_pid_prefers_stable_process_group_leader(
    monkeypatch: Any,
) -> None:
    observations = {
        9625: {
            "pid": 9625,
            "pgid": 9625,
            "ps": "/opt/homebrew/bin/claude --session-id existing",
            "identity_sha256": "a" * 64,
        },
        9653: {
            "pid": 9653,
            "pgid": 9625,
            "ps": "/opt/homebrew/bin/claude transient-helper",
            "identity_sha256": "b" * 64,
        },
    }
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: observations[pid],
    )
    monkeypatch.setattr(
        participant_driver,
        "_matching_process_group_observations",
        lambda pgid, process_match: [
            value
            for value in observations.values()
            if value["pgid"] == pgid and process_match in value["ps"]
        ],
    )

    pid = asyncio.run(
        participant_driver._wait_job_pid(  # noqa: SLF001
            _JobPidSession(),
            "claude",
            wait_seconds=0.1,
        )
    )

    assert pid == 9625


def test_process_group_candidate_lookup_degrades_when_ps_has_no_rows(
    monkeypatch: Any,
) -> None:
    def empty_group(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(participant_driver.subprocess, "run", empty_group)

    assert (
        participant_driver._matching_process_group_observations(  # noqa: SLF001
            3001, "codex"
        )
        == []
    )


def test_process_group_candidate_lookup_degrades_when_ps_times_out(
    monkeypatch: Any,
) -> None:
    def timed_out(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(["/bin/ps"], timeout=3)

    monkeypatch.setattr(participant_driver.subprocess, "run", timed_out)

    assert (
        participant_driver._matching_process_group_observations(  # noqa: SLF001
            3001, "codex"
        )
        == []
    )


def test_sender_accepts_owned_descendant_in_new_process_group(
    monkeypatch: Any,
) -> None:
    relationships = {
        5003: {"parent_pid": 5002, "process_group_id": 5003},
        5002: {"parent_pid": 5001, "process_group_id": 5002},
        5001: {"parent_pid": 100, "process_group_id": 5000},
    }
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: relationships[pid],
    )
    participant_driver._validate_owned_descendant_process(  # noqa: SLF001
        5003, {"pid": 5001, "pgid": 5000}
    )


def test_sender_rejects_same_group_non_descendant(monkeypatch: Any) -> None:
    relationships = {
        6003: {"parent_pid": 6002, "process_group_id": 5000},
        6002: {"parent_pid": 100, "process_group_id": 5000},
        100: {"parent_pid": 1, "process_group_id": 5000},
    }
    monkeypatch.setattr(
        participant_driver,
        "_process_relationship",
        lambda pid: relationships[pid],
    )
    try:
        participant_driver._validate_owned_descendant_process(  # noqa: SLF001
            6003, {"pid": 5001, "pgid": 5000}
        )
    except participant_driver.DriverError as exc:
        assert str(exc) == "participant sender process is not an owned descendant"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("same-group non-descendant sender was accepted")


def test_generic_runtime_environment_includes_scoped_client_and_proxy_only(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_runtime_argv",
        lambda launch_spec: ("/owner/runtime/bin/launcher", "--flag"),
    )
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:8080")
    monkeypatch.setenv("CODEX_THREAD_ID", "must-not-cross-boundary")
    context = tmp_path / "participant-context.json"
    context.write_text("{}\n", encoding="utf-8")
    context.chmod(0o600)
    client_pythonpath = tmp_path / "client-pythonpath"
    client_pythonpath.mkdir(mode=0o700)
    collaboration_context = tmp_path / "participant-collaboration.json"
    collaboration_context.write_text("{}\n", encoding="utf-8")
    collaboration_context.chmod(0o600)
    participant_client = {
        "context_path": str(context),
        "client_executable": str(Path(sys.executable).resolve()),
        "client_pythonpath": str(client_pythonpath),
        "collaboration_context_path": str(collaboration_context),
    }
    environment = participant_driver._runtime_environment(  # noqa: SLF001
        {}, participant_client
    )
    search_path = environment["PATH"].split(":")
    assert search_path[0] == str(participant_driver.PINGAGENT_BIN)
    assert search_path[1] == "/owner/runtime/bin"
    assert environment["LANG"] == "en_US.UTF-8"
    assert environment["https_proxy"] == "http://proxy.invalid:8080"
    assert environment["AI_COLLAB_HARNESS_CONTEXT"] == str(context)
    assert (
        environment["AI_COLLAB_HARNESS_CLIENT_EXECUTABLE"]
        == participant_client["client_executable"]
    )
    assert (
        environment["AI_COLLAB_HARNESS_CLIENT_PYTHONPATH"]
        == str(client_pythonpath)
    )
    assert environment["AI_COLLAB_HARNESS_COLLABORATION_CONTEXT"] == str(
        collaboration_context
    )
    assert "CODEX_THREAD_ID" not in environment

def test_runtime_environment_puts_the_generation_wrapper_first(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver,
        "_runtime_argv",
        lambda launch_spec: ("/owner/runtime/bin/launcher", "--flag"),
    )
    context = tmp_path / "participant-context.json"
    context.write_text("{}\n", encoding="utf-8")
    context.chmod(0o600)
    client_pythonpath = tmp_path / "client-pythonpath"
    client_pythonpath.mkdir(mode=0o700)
    collaboration_context = tmp_path / "participant-collaboration.json"
    collaboration_context.write_text("{}\n", encoding="utf-8")
    collaboration_context.chmod(0o600)
    participant_client = {
        "context_path": str(context),
        "client_executable": str(Path(sys.executable).resolve()),
        "client_pythonpath": str(client_pythonpath),
        "collaboration_context_path": str(collaboration_context),
    }
    participants = tmp_path / "Application Support" / "AI Collab" / "participants"
    older = participants / "digest" / "generation-2"
    current = participants / "digest" / "generation-3"
    for root in (older, current):
        root.mkdir(parents=True, mode=0o700)

    environment = participant_driver._runtime_environment(  # noqa: SLF001
        {}, participant_client, current
    )
    search_path = environment["PATH"].split(os.pathsep)
    # This generation's own directory comes first, then the product entry
    # point; no other generation's directory is reachable from this TUI.
    assert search_path[0] == str(current)
    assert search_path[1] == str(participant_driver.PINGAGENT_BIN)
    assert str(older) not in search_path
    assert search_path.count(str(current)) == 1

    previous = participant_driver._runtime_environment(  # noqa: SLF001
        {}, participant_client, older
    )
    assert previous["PATH"].split(os.pathsep)[0] == str(older)
    assert str(current) not in previous["PATH"].split(os.pathsep)

    # Callers without a private root keep the product entry point first.
    generic = participant_driver._runtime_environment(  # noqa: SLF001
        {}, participant_client
    )
    assert generic["PATH"].split(os.pathsep)[0] == str(
        participant_driver.PINGAGENT_BIN
    )

    # The launcher quotes the space-bearing PATH and workspace so the TUI's
    # zsh receives exactly these values.
    workspace = tmp_path / "Scenarios" / "workspace-demo" / "bundle" / "my project"
    script = participant_driver._launcher_script(  # noqa: SLF001
        environment, workspace, "exec-me --flag"
    )
    lines = script.splitlines()
    assert lines[:3] == ["#!/bin/zsh -f", "set -eu", "umask 077"]
    exports: dict[str, str] = {}
    for line in lines:
        if not line.startswith("export "):
            continue
        key, _, quoted = line.removeprefix("export ").partition("=")
        values = shlex.split(quoted)
        assert len(values) == 1
        exports[key] = values[0]
    assert exports["PATH"] == environment["PATH"]
    assert exports["PATH"].startswith(str(current) + os.pathsep)
    assert shlex.split(lines[-2]) == ["cd", "--", str(workspace)]
    assert lines[-1] == "exec exec-me --flag"



def test_authorize_sender_returns_only_redacted_owned_chain_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    state = {
        "status": "ready",
        "interaction_mode": "headless",
        "scenario_id": "scenario-self",
        "participant_id": "participant-self",
        "participant_generation": 2,
        "runtime_binding_id": "runtime-self",
        "presentation_instance_id": None,
        "process_identity_sha256": "a" * 64,
        "pgid": 4100,
        "pid": 4100,
    }
    observed: list[tuple[int, int]] = []
    monkeypatch.setattr(participant_driver, "_read_private", lambda path: state)
    monkeypatch.setattr(
        participant_driver, "_validate_process_state", lambda value: None
    )

    def validate_descendant(peer_pid: int, value: dict[str, Any]) -> None:
        observed.append((peer_pid, value["pid"]))

    monkeypatch.setattr(
        participant_driver, "_validate_owned_descendant_process", validate_descendant
    )
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 4100,
            "ps": "redacted in public result",
            "identity_sha256": "b" * 64,
        },
    )

    result = participant_driver.authorize_sender(
        {
            "peer_pid": 4102,
            "runtime_ready_ack": {
                "binding": {"runtime_binding_id": "runtime-self"}
            },
            "presentation_create_ack": None,
            "private_root": str(private_root),
        }
    )

    assert observed == [(4102, 4100)]
    assert result == {
        "authorized": True,
        "sender": {
            "scenario_id": "scenario-self",
            "participant_id": "participant-self",
            "participant_generation": 2,
        },
        "runtime_binding_id": "runtime-self",
        "process_chain_evidence_sha256": result[
            "process_chain_evidence_sha256"
        ],
    }
    assert len(result["process_chain_evidence_sha256"]) == 64
    assert "pid" not in json.dumps(result)


def test_authorize_tui_sender_checks_exact_session_as_owned_descendant(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    state = {
        "status": "ready",
        "interaction_mode": "tui",
        "scenario_id": "scenario-self",
        "participant_id": "participant-self",
        "participant_generation": 2,
        "runtime_binding_id": "runtime-self",
        "presentation_instance_id": "presentation-self",
        "process_identity_sha256": "a" * 64,
        "pgid": 4100,
        "pid": 4100,
    }
    exact_calls: list[tuple[object, dict[str, Any], bool]] = []
    module = object()
    monkeypatch.setattr(participant_driver, "_read_private", lambda path: state)
    monkeypatch.setattr(
        participant_driver, "_validate_process_state", lambda value: None
    )
    monkeypatch.setattr(
        participant_driver,
        "_validate_owned_descendant_process",
        lambda peer_pid, value: None,
    )
    monkeypatch.setattr(
        participant_driver, "_ensure_iterm_module", lambda root: module
    )

    async def exact_session(
        found_module: object,
        found_state: dict[str, Any],
        *,
        require_foreground_process_group: bool = True,
    ) -> tuple[object, object, object, int]:
        exact_calls.append(
            (found_module, found_state, require_foreground_process_group)
        )
        return object(), object(), object(), 4102

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 4102,
            "ps": "redacted in public result",
            "identity_sha256": "b" * 64,
        },
    )

    result = participant_driver.authorize_sender(
        {
            "peer_pid": 4102,
            "runtime_ready_ack": {
                "binding": {"runtime_binding_id": "runtime-self"}
            },
            "presentation_create_ack": {
                "binding": {"presentation_instance_id": "presentation-self"}
            },
            "private_root": str(private_root),
        }
    )

    assert result["authorized"] is True
    assert exact_calls == [(module, state, False)]


def test_authorize_tui_sender_retries_only_closed_iterm_transport(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    state = {
        "status": "ready",
        "interaction_mode": "tui",
        "scenario_id": "scenario-self",
        "participant_id": "participant-self",
        "participant_generation": 2,
        "runtime_binding_id": "runtime-self",
        "presentation_instance_id": "presentation-self",
        "process_identity_sha256": "a" * 64,
        "pgid": 4100,
        "pid": 4100,
    }
    module = object()
    monkeypatch.setattr(participant_driver, "_read_private", lambda path: state)
    monkeypatch.setattr(
        participant_driver, "_validate_process_state", lambda value: None
    )
    monkeypatch.setattr(
        participant_driver,
        "_validate_owned_descendant_process",
        lambda peer_pid, value: None,
    )
    monkeypatch.setattr(
        participant_driver, "_ensure_iterm_module", lambda root: module
    )
    monkeypatch.setattr(participant_driver, "SENDER_SESSION_RETRY_SECONDS", 0)
    connection_closed = type(
        "ConnectionClosedError",
        (Exception,),
        {"__module__": "websockets.exceptions"},
    )
    exact_calls = 0

    async def exact_session(
        found_module: object,
        found_state: dict[str, Any],
        *,
        require_foreground_process_group: bool = True,
    ) -> tuple[object, object, object, int]:
        nonlocal exact_calls
        assert found_module is module
        assert found_state is state
        assert require_foreground_process_group is False
        exact_calls += 1
        if exact_calls < 3:
            raise connection_closed("injected loopback handoff")
        return object(), object(), object(), 4102

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_process_observation",
        lambda pid: {
            "pid": pid,
            "pgid": 4102,
            "ps": "redacted in public result",
            "identity_sha256": "b" * 64,
        },
    )

    result = participant_driver.authorize_sender(
        {
            "peer_pid": 4102,
            "runtime_ready_ack": {
                "binding": {"runtime_binding_id": "runtime-self"}
            },
            "presentation_create_ack": {
                "binding": {"presentation_instance_id": "presentation-self"}
            },
            "private_root": str(private_root),
        }
    )

    assert result["authorized"] is True
    assert exact_calls == 3


def test_iterm_connection_release_cancels_dispatch_and_closes_websocket() -> None:
    observed: dict[str, bool] = {"closed": False}

    class _WebSocket:
        async def close(self) -> None:
            observed["closed"] = True

    async def exercise() -> bool:
        dispatcher = asyncio.create_task(asyncio.Event().wait())
        connection = SimpleNamespace(websocket=_WebSocket())
        setattr(
            connection,
            "_Connection__dispatch_forever_future",
            dispatcher,
        )
        await participant_driver._close_iterm_connection(  # noqa: SLF001
            connection
        )
        return dispatcher.cancelled()

    assert asyncio.run(exercise()) is True
    assert observed == {"closed": True}


def test_exact_session_failure_releases_created_iterm_connection() -> None:
    observed: dict[str, bool] = {"closed": False}

    class _WebSocket:
        async def close(self) -> None:
            observed["closed"] = True

    async def exercise() -> None:
        dispatcher = asyncio.create_task(asyncio.Event().wait())
        connection = SimpleNamespace(websocket=_WebSocket())
        setattr(
            connection,
            "_Connection__dispatch_forever_future",
            dispatcher,
        )

        async def create_connection() -> Any:
            return connection

        async def get_app(found_connection: Any) -> Any:
            assert found_connection is connection
            return SimpleNamespace(get_window_by_id=lambda window_id: None)

        module = SimpleNamespace(
            Connection=SimpleNamespace(async_create=create_connection),
            async_get_app=get_app,
        )
        with pytest.raises(
            participant_driver.DriverError,
            match="owned iTerm window is absent",
        ):
            await participant_driver._exact_session(  # noqa: SLF001
                module,
                {"window_id": "window-owned"},
            )
        assert dispatcher.cancelled()

    asyncio.run(exercise())
    assert observed == {"closed": True}


def test_sender_exact_session_success_releases_iterm_connection(
    monkeypatch: Any,
) -> None:
    observed: dict[str, bool] = {"closed": False}

    class _WebSocket:
        async def close(self) -> None:
            observed["closed"] = True

    async def exercise() -> None:
        dispatcher = asyncio.create_task(asyncio.Event().wait())
        connection = SimpleNamespace(websocket=_WebSocket())
        setattr(
            connection,
            "_Connection__dispatch_forever_future",
            dispatcher,
        )
        app = SimpleNamespace(connection=connection)

        async def exact_session(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
            return app, object(), object(), 4102

        monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
        await participant_driver._authorize_sender_exact_session(  # noqa: SLF001
            object(), {}
        )
        assert dispatcher.cancelled()

    asyncio.run(exercise())
    assert observed == {"closed": True}


def test_authorize_tui_sender_does_not_retry_binding_drift(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    state = {
        "status": "ready",
        "interaction_mode": "tui",
        "scenario_id": "scenario-self",
        "participant_id": "participant-self",
        "participant_generation": 2,
        "runtime_binding_id": "runtime-self",
        "presentation_instance_id": "presentation-self",
        "process_identity_sha256": "a" * 64,
        "pgid": 4100,
        "pid": 4100,
    }
    monkeypatch.setattr(participant_driver, "_read_private", lambda path: state)
    monkeypatch.setattr(
        participant_driver, "_validate_process_state", lambda value: None
    )
    monkeypatch.setattr(
        participant_driver,
        "_validate_owned_descendant_process",
        lambda peer_pid, value: None,
    )
    monkeypatch.setattr(
        participant_driver, "_ensure_iterm_module", lambda root: object()
    )
    exact_calls = 0

    async def exact_session(*args: Any, **kwargs: Any) -> tuple[object, ...]:
        nonlocal exact_calls
        exact_calls += 1
        raise participant_driver.DriverError("owned iTerm delivery topology drifted")

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)

    with pytest.raises(
        participant_driver.DriverError,
        match="owned iTerm delivery topology drifted",
    ):
        participant_driver.authorize_sender(
            {
                "peer_pid": 4102,
                "runtime_ready_ack": {
                    "binding": {"runtime_binding_id": "runtime-self"}
                },
                "presentation_create_ack": {
                    "binding": {"presentation_instance_id": "presentation-self"}
                },
                "private_root": str(private_root),
            }
        )

    assert exact_calls == 1
    assert json.loads(
        (private_root / "sender-auth-diagnostic.json").read_text(encoding="utf-8")
    ) == {
        "schema_version": 1,
        "outcome": "rejected",
        "stage": "exact-session",
        "reason_code": "iterm.topology-drift",
    }


def test_authorize_sender_records_private_peer_process_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    state = {
        "status": "ready",
        "interaction_mode": "headless",
        "scenario_id": "scenario-self",
        "participant_id": "participant-self",
        "participant_generation": 2,
        "runtime_binding_id": "runtime-self",
        "presentation_instance_id": None,
        "process_identity_sha256": "a" * 64,
        "pgid": 4100,
        "pid": 4100,
    }
    monkeypatch.setattr(participant_driver, "_read_private", lambda path: state)
    monkeypatch.setattr(
        participant_driver, "_validate_process_state", lambda value: None
    )

    def reject_descendant(peer_pid: int, value: dict[str, Any]) -> None:
        raise participant_driver.DriverError(
            "participant sender process is not an owned descendant"
        )

    monkeypatch.setattr(
        participant_driver, "_validate_owned_descendant_process", reject_descendant
    )

    with pytest.raises(
        participant_driver.DriverError,
        match="participant sender process is not an owned descendant",
    ):
        participant_driver.authorize_sender(
            {
                "peer_pid": 4102,
                "runtime_ready_ack": {
                    "binding": {"runtime_binding_id": "runtime-self"}
                },
                "presentation_create_ack": None,
                "private_root": str(private_root),
            }
        )

    diagnostic = private_root / "sender-auth-diagnostic.json"
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    assert json.loads(diagnostic.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "outcome": "rejected",
        "stage": "peer-process",
        "reason_code": "process.not-owned-descendant",
    }
    assert "pid" not in diagnostic.read_text(encoding="utf-8")
    assert str(private_root) not in diagnostic.read_text(encoding="utf-8")


def test_iterm_dependency_env_rebuilds_incomplete_venv_with_symlinks(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    lock_digest = "a" * 64
    environment = (
        participant_driver._iterm_install_root(  # noqa: SLF001
            private_root, lock_digest
        )
        / "venv"
    )
    environment.mkdir(parents=True)
    incomplete = environment / "incomplete"
    incomplete.write_text("failed bootstrap", encoding="utf-8")
    builder_options: list[dict[str, Any]] = []

    class _Builder:
        def __init__(self, **options: Any) -> None:
            builder_options.append(options)

        def create(self, target: Path) -> None:
            assert Path(target) == environment
            assert incomplete.is_file()
            shutil.rmtree(target)
            (
                Path(target)
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            ).mkdir(parents=True)
            (Path(target) / "bin").mkdir()
            (Path(target) / "bin" / "python").write_text("fixture", encoding="utf-8")

    class _Completed:
        returncode = 0

    monkeypatch.setattr(participant_driver, "_load_lock", lambda: ([], lock_digest))
    monkeypatch.setattr(participant_driver.venv, "EnvBuilder", _Builder)
    monkeypatch.setattr(participant_driver.subprocess, "run", lambda *args, **kwargs: _Completed())
    iterm_module = object()
    monkeypatch.setitem(sys.modules, "iterm2", iterm_module)

    site_packages = (
        environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    try:
        assert (
            participant_driver._ensure_iterm_module(private_root)  # noqa: SLF001
            is iterm_module
        )
    finally:
        if str(site_packages) in sys.path:
            sys.path.remove(str(site_packages))

    assert builder_options == [
        {"with_pip": True, "clear": True, "symlinks": True}
    ]
    assert not incomplete.exists()
    assert (environment.parent / "ready.json").is_file()


def test_legacy_untagged_python_cache_is_not_reused(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    lock_digest = "b" * 64
    legacy_root = private_root / f"iterm-python-{lock_digest[:16]}"
    legacy_site_packages = legacy_root / "venv/lib/python3.11/site-packages"
    legacy_site_packages.mkdir(parents=True)
    (legacy_root / "venv/pyvenv.cfg").write_text(
        "version = 3.11.12\n", encoding="utf-8"
    )
    legacy_ready = legacy_root / "ready.json"
    legacy_ready.write_text(
        json.dumps({"schema_version": 1, "lock_digest": lock_digest}),
        encoding="utf-8",
    )
    legacy_ready.chmod(0o600)
    monkeypatch.setattr(participant_driver, "_load_lock", lambda: ([], lock_digest))
    expected_environment = (
        private_root
        / f"iterm-python-{lock_digest[:16]}-{sys.implementation.cache_tag}/venv"
    )

    class _Builder:
        def __init__(self, **_options: Any) -> None:
            pass

        def create(self, target: Path) -> None:
            assert Path(target) == expected_environment
            (
                Path(target)
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            ).mkdir(parents=True)
            (Path(target) / "bin").mkdir()
            (Path(target) / "bin/python").write_text("fixture", encoding="utf-8")

    class _Completed:
        returncode = 0

    monkeypatch.setattr(participant_driver.venv, "EnvBuilder", _Builder)
    monkeypatch.setattr(
        participant_driver.subprocess,
        "run",
        lambda *args, **kwargs: _Completed(),
    )
    iterm_module = object()
    monkeypatch.setitem(sys.modules, "iterm2", iterm_module)
    path_before = list(sys.path)
    site_packages = (
        expected_environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    try:
        assert (
            participant_driver._ensure_iterm_module(private_root)  # noqa: SLF001
            is iterm_module
        )
    finally:
        sys.path[:] = path_before

    assert legacy_site_packages.is_dir()
    assert site_packages.is_dir()
    assert json.loads(
        (expected_environment.parent / "ready.json").read_text(encoding="utf-8")
    )["runtime_cache_tag"] == sys.implementation.cache_tag


def test_startup_process_wait_is_independent_from_declared_gate_timeout() -> None:
    assert (
        participant_driver._startup_process_wait_seconds(  # noqa: SLF001
            {"startup_gate": {"timeout_seconds": 60}}
        )
        == participant_driver.PROCESS_WAIT_SECONDS
    )
    assert (
        participant_driver._startup_process_wait_seconds(  # noqa: SLF001
            {"startup_gate": None}
        )
        == participant_driver.PROCESS_WAIT_SECONDS
    )


def test_startup_gate_schema_budget_stays_within_driver_start_timeout() -> None:
    from ai_collab.participant import PARTICIPANT_START_TIMEOUT_SECONDS

    bounded_driver_overhead = (
        (2 * participant_driver.PROCESS_WAIT_SECONDS)
        + (7 * participant_driver.OPERATION_TIMEOUT_SECONDS)
    )
    assert (
        participant_driver.STARTUP_GATE_MAX_SECONDS
        + bounded_driver_overhead
        < PARTICIPANT_START_TIMEOUT_SECONDS
    )


def test_startup_gate_timeout_validation_allows_slow_vendor_ready_screens() -> None:
    gate = copy.deepcopy(
        participant_driver._runtime_profiles()["runtime-profile.codex"][  # noqa: SLF001
            "startup_gate"
        ]
    )

    assert participant_driver._valid_startup_gate(gate) is True  # noqa: SLF001
    gate["timeout_seconds"] = participant_driver.STARTUP_GATE_MAX_SECONDS + 1
    assert participant_driver._valid_startup_gate(gate) is False  # noqa: SLF001


def test_startup_gate_validation_rejects_unknown_confirmation_keys() -> None:
    gate = copy.deepcopy(
        participant_driver._runtime_profiles()["runtime-profile.claude"][  # noqa: SLF001
            "startup_gate"
        ]
    )

    assert participant_driver._valid_startup_gate(gate) is True  # noqa: SLF001
    gate["prompt_rules"][0]["confirm_sequence"] = ["yes", "\r"]
    assert participant_driver._valid_startup_gate(gate) is False  # noqa: SLF001


def test_startup_gate_validation_rejects_non_object_prompt_rules() -> None:
    gate = copy.deepcopy(
        participant_driver._runtime_profiles()["runtime-profile.codex"][  # noqa: SLF001
            "startup_gate"
        ]
    )
    gate["prompt_rules"] = ["invalid"]

    assert participant_driver._valid_startup_gate(gate) is False  # noqa: SLF001


def test_startup_gate_validation_accepts_legacy_single_prompt_shape() -> None:
    gate = copy.deepcopy(
        participant_driver._runtime_profiles()["runtime-profile.codex"][  # noqa: SLF001
            "startup_gate"
        ]
    )
    rule = gate.pop("prompt_rules")[0]
    gate.update({key: value for key, value in rule.items() if key != "rule_id"})

    assert participant_driver._valid_startup_gate(gate) is True  # noqa: SLF001


def test_launch_failure_diagnostic_is_bounded_and_owner_private(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)

    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="initial-process",
        exc=participant_driver.DriverError(
            "iTerm runtime process did not become ready"
        ),
        cleanup_outcome="close-requested",
    )

    diagnostic = private_root / "launch-diagnostic.json"
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    assert json.loads(diagnostic.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "outcome": "rejected",
        "stage": "initial-process",
        "reason_code": "process.readiness-timeout",
        "cleanup_outcome": "close-requested",
    }
    assert str(private_root) not in diagnostic.read_text(encoding="utf-8")


def test_pre_window_launch_failure_is_diagnosed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)

    async def fail_connect(module: Any) -> tuple[Any, Any]:
        raise participant_driver.DriverError("injected pre-window failure")

    monkeypatch.setattr(
        participant_driver, "_connect_iterm_application", fail_connect
    )
    with pytest.raises(
        participant_driver.DriverError, match="injected pre-window failure"
    ):
        asyncio.run(
            participant_driver._iterm_start_async(  # noqa: SLF001
                object(),
                private_root,
                tmp_path,
                {},
                {},
                {},
                {},
            )
        )

    diagnostic = json.loads(
        (private_root / "launch-diagnostic.json").read_text(encoding="utf-8")
    )
    assert diagnostic["stage"] == "iterm-connect"
    assert diagnostic["cleanup_outcome"] == "not-required"


def test_iterm_pre_window_connection_retries_closed_transport(
    monkeypatch: Any,
) -> None:
    connection_closed = type(
        "ConnectionClosedError",
        (Exception,),
        {"__module__": "websockets.exceptions"},
    )
    attempts = 0
    connections: list[object] = []

    class Connection:
        @staticmethod
        async def async_create() -> object:
            connection = object()
            connections.append(connection)
            return connection

    async def async_get_app(connection: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise connection_closed("injected closed transport")
        return object()

    module = type(
        "ItermModule",
        (),
        {"Connection": Connection, "async_get_app": async_get_app},
    )
    monkeypatch.setattr(participant_driver, "SENDER_SESSION_RETRY_SECONDS", 0)

    connection, app = asyncio.run(
        participant_driver._connect_iterm_application(module)  # noqa: SLF001
    )

    assert connection is connections[-1]
    assert app is not None
    assert attempts == 2


def _repair_payload(private_root: Path) -> dict[str, Any]:
    context = {
        "scenario_id": "scenario-repair",
        "participant_id": "participant-repair",
        "participant_generation": 1,
        "operation_id": "operation-repair",
        "operation_generation": 4,
        "driver_registry_digest": "a" * 64,
        "capability_snapshot_digest": "b" * 64,
    }
    return {
        "context": context,
        "next_participant_generation": 2,
        "launch_spec": {
            "driver_id": "runtime.generic-process",
            "driver_contract_version": 2,
            "interaction_mode": "tui",
            "continuity_mode": "explicit_recreate",
            "runtime_profile_ref": "runtime-profile.inert",
            "model_binding": None,
            "continuity_binding_ref": None,
        },
        "resolved_driver": {
            "driver_registry_digest": context["driver_registry_digest"],
            "capability_snapshot_digest": context[
                "capability_snapshot_digest"
            ],
        },
        "runtime_ready_ack": None,
        "presentation_create_ack": None,
        "degraded": {
            "reason": "launch_failed",
            "cleanup_pending": True,
            "owned_resource_evidence_sha256": "c" * 64,
            "repair_action": "participant.recover",
        },
        "private_root": str(private_root),
    }


def test_repair_rotates_pre_binding_failure_without_deleting_private_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    lock_digest = "c" * 64
    incomplete = (
        participant_driver._iterm_install_root(  # noqa: SLF001
            private_root, lock_digest
        )
        / "venv"
    )
    incomplete.mkdir(parents=True)
    (incomplete / "failure-marker").write_text("retained", encoding="utf-8")
    monkeypatch.setattr(participant_driver, "_load_lock", lambda: ([], lock_digest))

    result = participant_driver.repair(_repair_payload(private_root))

    assert result["recovered"] is True
    assert result["recovery_class"] == "pre_binding_absent"
    assert result["previous_participant_generation"] == 1
    assert result["next_participant_generation"] == 2
    assert result["external_resources_absent"] is True
    assert result["private_generation_retained"] is True
    assert len(result["owned_resource_evidence_sha256"]) == 64
    assert (incomplete / "failure-marker").read_text(encoding="utf-8") == "retained"


def test_repair_fails_closed_when_pre_binding_absence_is_not_provable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    lock_digest = "d" * 64
    ready = (
        participant_driver._iterm_install_root(  # noqa: SLF001
            private_root, lock_digest
        )
        / "ready.json"
    )
    ready.parent.mkdir(parents=True)
    ready.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(participant_driver, "_load_lock", lambda: ([], lock_digest))

    try:
        participant_driver.repair(_repair_payload(private_root))
    except participant_driver.DriverError as exc:
        assert str(exc) == "repair cannot prove pre-binding resource absence"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("ambiguous pre-binding recovery was accepted")


def test_repair_accepts_diagnosed_pre_window_failure_after_dependency_ready(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    lock_digest = "e" * 64
    ready = (
        participant_driver._iterm_install_root(  # noqa: SLF001
            private_root, lock_digest
        )
        / "ready.json"
    )
    ready.parent.mkdir(parents=True)
    ready.write_text("{}", encoding="utf-8")
    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="iterm-connect",
        exc=participant_driver.DriverError("iTerm application state is unavailable"),
        cleanup_outcome="not-required",
    )
    monkeypatch.setattr(participant_driver, "_load_lock", lambda: ([], lock_digest))

    result = participant_driver.repair(_repair_payload(private_root))

    assert result["recovered"] is True
    assert result["recovery_class"] == "pre_binding_absent"


def test_repair_gracefully_stops_only_exact_published_binding(tmp_path: Path) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    state = {
        "schema_version": 1,
        "status": "stopped",
        "scenario_id": payload["context"]["scenario_id"],
        "participant_id": payload["context"]["participant_id"],
        "participant_generation": 1,
        "runtime_binding_id": "runtime-binding-repair",
        "presentation_instance_id": "presentation-repair",
        "stop_evidence_sha256": "e" * 64,
    }
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root), state  # noqa: SLF001
    )
    payload["runtime_ready_ack"] = {
        "binding": {"runtime_binding_id": "runtime-binding-repair"}
    }
    payload["presentation_create_ack"] = {
        "binding": {"presentation_instance_id": "presentation-repair"}
    }

    result = participant_driver.repair(payload)

    assert result["recovery_class"] == "exact_binding_stopped"
    assert result["external_resources_absent"] is True
    assert participant_driver._state_path(private_root).is_file()  # noqa: SLF001


def _ready_published_binding(
    private_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _repair_payload(private_root)
    payload["runtime_ready_ack"] = {
        "binding": {"runtime_binding_id": "runtime-binding-repair"}
    }
    payload["presentation_create_ack"] = {
        "binding": {"presentation_instance_id": "presentation-repair"}
    }
    state = {
        "schema_version": 1,
        "status": "ready",
        "scenario_id": payload["context"]["scenario_id"],
        "participant_id": payload["context"]["participant_id"],
        "participant_generation": payload["context"]["participant_generation"],
        "runtime_binding_id": "runtime-binding-repair",
        "presentation_instance_id": "presentation-repair",
        "runtime_profile_ref": "runtime-profile.inert",
        "interaction_mode": "tui",
        "pid": 43210,
    }
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root), state  # noqa: SLF001
    )
    return payload, state


def test_repair_accepts_exact_published_binding_after_vendor_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: True
    )

    result = participant_driver.repair(payload)

    assert result["recovered"] is True
    assert result["recovery_class"] == "exact_binding_stopped"
    stopped = json.loads(
        participant_driver._state_path(private_root).read_text(encoding="utf-8")  # noqa: SLF001
    )
    assert stopped["status"] == "stopped"
    assert len(stopped["stop_evidence_sha256"]) == 64


def test_close_accepts_exact_published_binding_after_vendor_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    payload["drain_timeout_ms"] = 100
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: True
    )
    monkeypatch.setattr(
        participant_driver,
        "_ensure_iterm_module",
        lambda root: (_ for _ in ()).throw(AssertionError("iTerm was consulted")),
    )

    result = participant_driver.close(payload)

    assert result["classification"] == "idle"
    assert result["closed"] is True
    assert result["action_outcome_known"] is True
    assert result["drain_requested"] is False
    assert result["progress_event_count"] == 0
    stopped = json.loads(
        participant_driver._state_path(private_root).read_text(encoding="utf-8")  # noqa: SLF001
    )
    assert stopped["status"] == "stopped"


def test_close_rejects_reused_pid_after_vendor_exit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    payload["drain_timeout_ms"] = 100
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: False
    )
    monkeypatch.setattr(
        participant_driver,
        "_validate_process_state",
        lambda state: (_ for _ in ()).throw(
            participant_driver.DriverError("process identity differs")
        ),
    )

    with pytest.raises(participant_driver.DriverError, match="process identity differs"):
        participant_driver.close(payload)


def test_close_rejects_mismatched_binding_when_vendor_process_is_absent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    payload["drain_timeout_ms"] = 100
    payload["runtime_ready_ack"]["binding"]["runtime_binding_id"] = "other"
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: True
    )

    with pytest.raises(participant_driver.DriverError, match="close binding differs"):
        participant_driver.close(payload)


def test_process_permission_ambiguity_is_not_absence(monkeypatch: Any) -> None:
    def permission_denied(pid: int, signal_number: int) -> None:
        raise PermissionError

    monkeypatch.setattr(participant_driver.os, "kill", permission_denied)

    absent = participant_driver._owned_process_is_absent(  # noqa: SLF001
        {"pid": 43210}
    )
    assert absent is False


def test_stop_closes_a_live_exact_published_binding(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    validated: list[int] = []
    closed: list[int] = []

    async def close_iterm(module: Any, current: dict[str, Any]) -> int:
        closed.append(current["pid"])
        return 43211

    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: False
    )
    monkeypatch.setattr(
        participant_driver,
        "_validate_process_state",
        lambda state: validated.append(state["pid"]),
    )
    monkeypatch.setattr(
        participant_driver,
        "_ensure_iterm_module",
        lambda root: object(),
    )
    monkeypatch.setattr(participant_driver, "_iterm_close_async", close_iterm)
    monkeypatch.setattr(
        participant_driver, "_wait_process_absent", lambda pid: True
    )
    monkeypatch.setattr(
        participant_driver,
        "_wait_process_absent_bounded",
        lambda pid, timeout: True,
    )

    result = participant_driver.stop(payload)

    assert result["stopped"] is True
    assert validated == [43210]
    assert closed == [43210]
    stopped = json.loads(
        participant_driver._state_path(private_root).read_text(encoding="utf-8")  # noqa: SLF001
    )
    assert stopped["status"] == "stopped"


def test_stop_rejects_mismatched_binding_when_vendor_process_is_absent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    payload["runtime_ready_ack"]["binding"]["runtime_binding_id"] = "other"
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: True
    )

    with pytest.raises(participant_driver.DriverError, match="stop binding differs"):
        participant_driver.stop(payload)


def test_stop_absent_evidence_claims_only_process_absence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload, _state = _ready_published_binding(private_root)
    payload.pop("next_participant_generation")
    payload.pop("degraded")
    monkeypatch.setattr(
        participant_driver, "_owned_process_is_absent", lambda state: True
    )
    original_digest = participant_driver.digest
    evidence_payloads: list[dict[str, Any]] = []

    def capture_digest(value: Any) -> str:
        if isinstance(value, dict) and value.get("termination_mode"):
            evidence_payloads.append(value)
        return original_digest(value)

    monkeypatch.setattr(participant_driver, "digest", capture_digest)

    participant_driver.stop(payload)

    assert evidence_payloads == [
        {
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "process_absent": True,
            "termination_mode": "graceful-stop-process-absent",
        }
    ]


def test_repair_accepts_exact_durable_cleanup_evidence_without_public_binding(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    payload["degraded"].update(
        {
            "cleanup_pending": False,
            "owned_resource_evidence_sha256": "e" * 64,
        }
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "status": "stopped",
            "scenario_id": payload["context"]["scenario_id"],
            "participant_id": payload["context"]["participant_id"],
            "participant_generation": 1,
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "stop_evidence_sha256": "e" * 64,
        },
    )

    result = participant_driver.repair(payload)

    assert result["recovery_class"] == "exact_binding_stopped"
    assert result["private_generation_retained"] is True


def test_repair_accepts_startup_gate_stopped_binding_when_processes_are_absent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "status": "stopped",
            "scenario_id": payload["context"]["scenario_id"],
            "participant_id": payload["context"]["participant_id"],
            "participant_generation": 1,
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "pid": 999_991,
            "pgid": 999_991,
            "process_identity_sha256": "1" * 64,
            "stop_evidence_sha256": "2" * 64,
        },
    )
    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="startup-gate",
        exc=participant_driver.DriverError("runtime TUI did not become input-ready"),
        cleanup_outcome="unconfirmed",
        process_observation={
            "pid": 999_993,
            "pgid": 999_993,
            "identity_sha256": "3" * 64,
        },
    )

    def process_absent(pid: int) -> dict[str, Any]:
        raise participant_driver.DriverError("owned process is absent")

    monkeypatch.setattr(participant_driver, "_process_observation", process_absent)
    monkeypatch.setattr(
        participant_driver, "_exact_process_group_observations", lambda _: []
    )

    result = participant_driver.repair(payload)

    assert result["recovery_class"] == "exact_binding_stopped"
    assert result["external_resources_absent"] is True
    assert len(result["owned_resource_evidence_sha256"]) == 64


def test_repair_rejects_unconfirmed_startup_gate_cleanup_without_process_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "status": "stopped",
            "scenario_id": payload["context"]["scenario_id"],
            "participant_id": payload["context"]["participant_id"],
            "participant_generation": 1,
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "pid": 999_992,
            "pgid": 999_992,
            "process_identity_sha256": "1" * 64,
            "stop_evidence_sha256": "2" * 64,
        },
    )
    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="startup-gate",
        exc=participant_driver.DriverError("runtime TUI did not become input-ready"),
        cleanup_outcome="unconfirmed",
    )

    def process_absent(pid: int) -> dict[str, Any]:
        raise participant_driver.DriverError("owned process is absent")

    monkeypatch.setattr(participant_driver, "_process_observation", process_absent)

    with pytest.raises(
        participant_driver.DriverError, match="repair durable cleanup evidence differs"
    ):
        participant_driver.repair(payload)


def test_repair_rejects_unconfirmed_startup_gate_cleanup_with_live_process(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "status": "stopped",
            "scenario_id": payload["context"]["scenario_id"],
            "participant_id": payload["context"]["participant_id"],
            "participant_generation": 1,
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "pid": 999_994,
            "pgid": 999_994,
            "process_identity_sha256": "1" * 64,
            "stop_evidence_sha256": "2" * 64,
        },
    )
    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="startup-gate",
        exc=participant_driver.DriverError("runtime TUI did not become input-ready"),
        cleanup_outcome="unconfirmed",
        process_observation={
            "pid": 999_995,
            "pgid": 999_995,
            "identity_sha256": "3" * 64,
        },
    )

    def process_observation(pid: int) -> dict[str, Any]:
        if pid == 999_995:
            return {"pid": pid, "pgid": pid, "ps": "claude", "identity_sha256": "3" * 64}
        raise participant_driver.DriverError("owned process is absent")

    monkeypatch.setattr(participant_driver, "_process_observation", process_observation)

    with pytest.raises(
        participant_driver.DriverError, match="repair startup cleanup is unconfirmed"
    ):
        participant_driver.repair(payload)


@pytest.mark.parametrize("cleanup_outcome", ["close-requested", "unconfirmed"])
def test_repair_rejects_failed_launch_with_remaining_process_group_member(
    tmp_path: Path, monkeypatch: Any, cleanup_outcome: str
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    payload = _repair_payload(private_root)
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._state_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "status": "stopped",
            "scenario_id": payload["context"]["scenario_id"],
            "participant_id": payload["context"]["participant_id"],
            "participant_generation": 1,
            "runtime_binding_id": "runtime-binding-repair",
            "presentation_instance_id": "presentation-repair",
            "pid": 999_996,
            "pgid": 999_996,
            "process_identity_sha256": "1" * 64,
            "stop_evidence_sha256": "2" * 64,
        },
    )
    participant_driver._record_launch_failure(  # noqa: SLF001
        private_root,
        stage="startup-gate",
        exc=participant_driver.DriverError("runtime TUI did not become input-ready"),
        cleanup_outcome=cleanup_outcome,
        process_observation={
            "pid": 999_997,
            "pgid": 999_997,
            "identity_sha256": "3" * 64,
        },
    )

    def process_absent(pid: int) -> dict[str, Any]:
        raise participant_driver.DriverError("owned process is absent")

    monkeypatch.setattr(participant_driver, "_process_observation", process_absent)
    monkeypatch.setattr(
        participant_driver,
        "_exact_process_group_observations",
        lambda pgid: [{"pid": pgid + 1, "pgid": pgid}],
    )

    with pytest.raises(
        participant_driver.DriverError, match="repair startup cleanup is unconfirmed"
    ):
        participant_driver.repair(payload)


def test_exact_process_group_observation_fails_closed_on_ps_timeout(
    monkeypatch: Any,
) -> None:
    def timeout(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(args[0], timeout=3)

    monkeypatch.setattr(participant_driver.subprocess, "run", timeout)

    with pytest.raises(
        participant_driver.DriverError,
        match="owned process observation is unavailable",
    ):
        participant_driver._exact_process_group_observations(42)  # noqa: SLF001


class _ScreenLine:
    def __init__(self, value: str) -> None:
        self.string = value


class _Screen:
    def __init__(self, value: str) -> None:
        self._lines = value.splitlines()
        self.number_of_lines = len(self._lines)

    def line(self, index: int) -> _ScreenLine:
        return _ScreenLine(self._lines[index])


class _StartupSession:
    def __init__(self, screens: list[str]) -> None:
        self._screens = list(screens)
        self._last = screens[-1]
        self.sent: list[tuple[str, bool]] = []

    async def async_get_screen_contents(self) -> _Screen:
        if self._screens:
            self._last = self._screens.pop(0)
        return _Screen(self._last)

    async def async_send_text(
        self, value: str, *, suppress_broadcast: bool
    ) -> None:
        self.sent.append((value, suppress_broadcast))


def test_startup_trust_gate_accepts_only_exact_workspace_and_waits_for_ready(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "Harness Workspace"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.claude"
    ]
    prompt = (
        "────────────────────────────────────────────────────────────────────────────────\n"
        " Accessing workspace:\n\n"
        f" {workspace}\n\n"
        " Quick safety check: Is this a project you created or one you trust? (Like your\n"
        " own code, a well-known open source project, or work from your team). If not,\n"
        " take a moment to review what's in this folder first.\n\n"
        " Claude Code'll be able to read, edit, and execute files here.\n\n"
        " Security guide\n\n"
        " ❯ No, exit\n"
        "   Yes, I trust this folder\n\n"
        " Enter to confirm · Esc to cancel"
    )
    ready = "Claude Code v2.1.227\n❯"
    session = _StartupSession([prompt, prompt, ready, ready, ready, ready])
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(participant_driver, "_process_cwd", lambda pid: workspace)
    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )
    assert session.sent == [("\x1b[B", True), ("\r", True)]
    assert evidence["outcome"] == "accepted"
    assert evidence["scope"] == "harness_verified_workspace"
    assert evidence["workspace_identity_sha256"] == participant_driver.digest(
        {"workspace_path": str(workspace.resolve())}
    )


def test_claude_trust_prompt_pattern_requires_current_menu_shape() -> None:
    gate = participant_driver._runtime_profiles()["runtime-profile.claude"][  # noqa: SLF001
        "startup_gate"
    ]
    pattern = gate["prompt_rules"][0]["prompt_pattern"]
    screen = (
        " Accessing workspace:\n\n"
        " /Users/atomgradient/Documents/Scenarios/workspace/bundle/EdgeStudio\n\n"
        " Quick safety check: Is this a project you created or one you trust?\n\n"
        " Security guide\n\n"
        " ❯ No, exit\n"
        "   Yes, I trust this folder\n\n"
        " Enter to confirm · Esc to cancel"
    )

    assert re.search(pattern, screen) is not None
    assert re.search(
        pattern,
        screen.replace("❯ No, exit\n   Yes", "  No, exit\n ❯ Yes"),
    ) is None
    assert re.search(
        pattern,
        screen.replace("❯ No, exit\n   Yes", "❯ Yes\n   No, exit"),
    ) is None
    assert re.search(
        pattern,
        screen.replace(
            "   Yes, I trust this folder",
            "   Yes, I trust this folder\n   Maybe later",
        ),
    ) is None


def test_claude_long_session_prompt_accepts_recommended_summary(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.claude"
    ]
    prompt = (
        "This session is 9h 57m old and 450.1k tokens.\n\n"
        "Resuming the full session will consume a substantial portion of your usage\n"
        "limits. We recommend resuming from a summary.\n\n"
        "❯ 1. Resume from summary (recommended)\n"
        "  2. Resume full session as-is\n"
        "  3. Don't ask me again\n\n"
        "Enter to confirm · Esc to cancel"
    )
    ready = "Claude Code v2.1.258\n❯"
    session = _StartupSession([prompt, prompt, ready, ready, ready, ready])
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(participant_driver, "_process_cwd", lambda pid: workspace)

    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )

    assert session.sent == [("\r", True)]
    assert evidence["outcome"] == "accepted"


def test_claude_long_session_prompt_requires_recommended_menu_shape() -> None:
    gate = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.claude"
    ]["startup_gate"]
    rule = next(
        rule
        for rule in gate["prompt_rules"]
        if rule["rule_id"] == "startup.claude-long-session-summary"
    )
    pattern = rule["prompt_pattern"]
    screen = (
        "This session is 9h 57m old and 450.1k tokens.\n\n"
        "Resuming the full session will consume a substantial portion of your usage\n"
        "limits. We recommend resuming from a summary.\n\n"
        "❯ 1. Resume from summary (recommended)\n"
        "  2. Resume full session as-is\n"
        "  3. Don't ask me again\n\n"
        "Enter to confirm · Esc to cancel"
    )

    assert re.search(pattern, screen) is not None
    assert re.search(pattern, screen.replace("❯ 1.", "  1.")) is None
    assert re.search(
        pattern,
        screen.replace("❯ 1. Resume", "  1. Resume").replace(
            "  2. Resume", "❯ 2. Resume"
        ),
    ) is None
    assert re.search(pattern, screen.replace("  3. Don't ask me again\n", "")) is None


def test_claude_startup_handles_trust_then_long_session_with_residual_text(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.claude"
    ]
    trust_prompt = (
        "Accessing workspace:\n\n"
        f"{workspace}\n\n"
        "Quick safety check: Is this a project you created or one you trust?\n\n"
        "❯ No, exit\n"
        "  Yes, I trust this folder\n\n"
        "Enter to confirm · Esc to cancel"
    )
    long_session_prompt = (
        "This session is 9h 57m old and 450.1k tokens.\n\n"
        "Resuming the full session will consume a substantial portion of your usage\n"
        "limits. We recommend resuming from a summary.\n\n"
        "❯ 1. Resume from summary (recommended)\n"
        "  2. Resume full session as-is\n"
        "  3. Don't ask me again\n\n"
        "Enter to confirm · Esc to cancel"
    )
    ready = "Claude Code v2.1.258\n❯"
    session = _StartupSession(
        [
            trust_prompt,
            f"{trust_prompt}\n\n{long_session_prompt}",
            ready,
            ready,
            ready,
            ready,
        ]
    )
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(participant_driver, "_process_cwd", lambda pid: workspace)

    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )

    assert session.sent == [("\x1b[B", True), ("\r", True), ("\r", True)]
    assert evidence["outcome"] == "accepted"


def test_startup_trust_gate_fails_closed_on_workspace_mismatch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "expected"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.codex"
    ]
    prompt = (
        "You are in /private/tmp/not-the-workspace\n"
        "Do you trust the contents of this directory?\n"
        "1. Yes, continue\n2. No, quit"
    )
    session = _StartupSession([prompt])
    monkeypatch.setattr(
        participant_driver, "_process_cwd", lambda pid: tmp_path / "different"
    )
    try:
        asyncio.run(
            participant_driver._wait_startup_ready(  # noqa: SLF001
                session, profile, workspace, 1234
            )
        )
    except participant_driver.DriverError as exc:
        assert str(exc) == "startup trust gate workspace differs"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("mismatched startup trust workspace was accepted")
    assert session.sent == []


def test_codex_update_prompt_is_skipped_before_waiting_for_ready(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.codex"
    ]
    prompt = (
        "✨ Update available! 0.151.0 -> 0.152.0\n\n"
        "Release notes: https://github.com/openai/codex/releases/latest\n\n"
        "› 1. Update now (runs `npm install -g @openai/codex`)\n"
        "  2. Skip\n"
        "  3. Skip until next version\n\n"
        "Press enter to continue"
    )
    ready = ">_ OpenAI Codex (v0.151.0)\n›"
    session = _StartupSession([prompt, prompt, ready, ready, ready, ready])
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(participant_driver, "_process_cwd", lambda pid: workspace)

    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )

    assert session.sent == [("2", True)]
    assert evidence["outcome"] == "accepted"


def test_codex_startup_handles_update_then_trust_with_residual_text(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.codex"
    ]
    update_prompt = (
        "✨ Update available! 0.151.0 -> 0.152.0\n\n"
        "Release notes: https://github.com/openai/codex/releases/latest\n\n"
        "› 1. Update now (runs `npm install -g @openai/codex`)\n"
        "  2. Skip\n"
        "  3. Skip until next version\n\n"
        "Press enter to continue"
    )
    trust_prompt = (
        "You are in /tmp/trusted\n"
        "Do you trust the contents of this directory?\n"
        "1. Yes, continue\n2. No, quit"
    )
    ready = ">_ OpenAI Codex (v0.151.0)\n›"
    session = _StartupSession(
        [
            update_prompt,
            f"{update_prompt}\n\n{trust_prompt}",
            ready,
            ready,
            ready,
            ready,
        ]
    )
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    monkeypatch.setattr(participant_driver, "_process_cwd", lambda pid: workspace)

    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )

    assert session.sent == [("2", True), ("1", True), ("\r", True)]
    assert evidence["outcome"] == "accepted"


def test_startup_gate_does_not_type_when_workspace_is_already_trusted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[  # noqa: SLF001
        "runtime-profile.codex"
    ]
    ready = ">_ OpenAI Codex (v0.147.0)\n›"
    session = _StartupSession([ready, ready, ready, ready])
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)
    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )
    assert session.sent == []
    assert evidence["outcome"] == "already_satisfied"


@pytest.mark.parametrize(
    ("profile_id", "restored_screen"),
    [
        (
            "runtime-profile.codex",
            "› Earlier employee prompt\n"
            "• Earlier response restored from the exact conversation.\n"
            "› Improve documentation in @filename\n"
            "gpt-5.6-luna medium · ~/workspace/bundle",
        ),
        (
            "runtime-profile.claude",
            "⏺ Earlier response restored from the exact conversation.\n"
            "────────────────────────────────────────────────\n"
            "❯ \n"
            "────────────────────────────────────────────────\n"
            "⏵⏵ bypass permissions on",
        ),
    ],
)
def test_startup_gate_accepts_stable_restored_input_without_transient_banner(
    tmp_path: Path,
    monkeypatch: Any,
    profile_id: str,
    restored_screen: str,
) -> None:
    workspace = tmp_path / "trusted"
    workspace.mkdir()
    profile = participant_driver._runtime_profiles()[profile_id]  # noqa: SLF001
    session = _StartupSession([restored_screen] * 4)
    monkeypatch.setattr(participant_driver, "STARTUP_POLL_SECONDS", 0)

    evidence = asyncio.run(
        participant_driver._wait_startup_ready(  # noqa: SLF001
            session, profile, workspace, 1234
        )
    )

    assert session.sent == []
    assert evidence["outcome"] == "already_satisfied"


@pytest.mark.parametrize(
    ("profile_id", "menu_screen"),
    [
        (
            "runtime-profile.codex",
            "Choose working directory to resume this session\n"
            "› 1. Use session directory (/workspace/old)\n"
            "  2. Use current directory (/workspace/new)",
        ),
        (
            "runtime-profile.claude",
            "Select an option\n❯ 1. Continue\n  2. Exit",
        ),
    ],
)
def test_restored_input_ready_pattern_rejects_numbered_choice_menus(
    profile_id: str,
    menu_screen: str,
) -> None:
    profile = participant_driver._runtime_profiles()[profile_id]  # noqa: SLF001

    assert re.search(profile["startup_gate"]["ready_pattern"], menu_screen) is None


class _CloseWindow:
    def __init__(self) -> None:
        self.close_calls: list[bool] = []

    async def async_close(self, *, force: bool) -> None:
        self.close_calls.append(force)


class _CloseWindowWithLostReply(_CloseWindow):
    async def async_close(self, *, force: bool) -> None:
        self.close_calls.append(force)
        raise OSError("iTerm close reply was lost")


def test_requested_tui_close_does_not_interpret_screen_content(
    monkeypatch: Any,
) -> None:
    window = _CloseWindow()

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        return _AbsentApp(), window, object(), 5678

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    class _AbsentApp:
        def get_window_by_id(self, window_id: str) -> None:
            return None

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_wait_process_absent_bounded",
        lambda pid, timeout: True,
    )
    classification, drain_requested, progress_count, closed = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 150
        )
    )
    assert classification == "requested"
    assert drain_requested is False
    assert progress_count == 0
    assert closed is True
    assert window.close_calls == [True]


def test_requested_tui_close_reports_timeout_when_exact_process_remains(
    monkeypatch: Any,
) -> None:
    window = _CloseWindow()

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        return _AbsentApp(), window, object(), 5678

    class _AbsentApp:
        def get_window_by_id(self, window_id: str) -> None:
            return None

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver, "_wait_process_absent_bounded", lambda pid, timeout: False
    )
    monkeypatch.setattr(
        participant_driver,
        "_terminate_gracefully_exact",
        lambda state: (_ for _ in ()).throw(
            participant_driver.DriverError("owned process remained after graceful stop")
        ),
    )
    classification, drain_requested, _, closed = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 1
        )
    )
    assert classification == "timeout"
    assert drain_requested is False
    assert closed is False
    assert window.close_calls == [True]


def test_requested_tui_close_requires_foreground_process_absence(
    monkeypatch: Any,
) -> None:
    window = _CloseWindow()

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        return _AbsentApp(), window, object(), 5678

    class _AbsentApp:
        def get_window_by_id(self, window_id: str) -> None:
            return None

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    observed: list[int] = []

    def wait_absent(pid: int, timeout: float) -> bool:
        observed.append(pid)
        return pid == 1234

    monkeypatch.setattr(
        participant_driver, "_wait_process_absent_bounded", wait_absent
    )
    monkeypatch.setattr(
        participant_driver, "_terminate_gracefully_exact", lambda state: None
    )

    result = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 1
        )
    )

    assert result == ("timeout", False, 0, False)
    assert 1234 in observed
    assert 5678 in observed


def test_requested_tui_close_ignores_the_stale_app_window_snapshot(
    monkeypatch: Any,
) -> None:
    window = _CloseWindow()

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        return _PresentApp(), window, object(), 5678

    class _PresentApp:
        def get_window_by_id(self, window_id: str) -> object:
            return object()

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_wait_process_absent_bounded",
        lambda pid, timeout: True,
    )
    result = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 1
        )
    )
    assert result == ("requested", False, 0, True)
    assert window.close_calls == [True]


def test_requested_tui_close_accepts_process_absence_after_a_lost_rpc_reply(
    monkeypatch: Any,
) -> None:
    window = _CloseWindowWithLostReply()

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        return _PresentApp(), window, object(), 5678

    class _PresentApp:
        connection = None

        def get_window_by_id(self, window_id: str) -> object:
            return object()

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_wait_process_absent_bounded",
        lambda pid, timeout: True,
    )

    result = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 150
        )
    )

    assert result == ("requested", False, 0, True)
    assert window.close_calls == [True]


def test_requested_tui_close_accepts_absence_after_close_and_reconnect_failures(
    monkeypatch: Any,
) -> None:
    window = _CloseWindowWithLostReply()
    attempts = 0
    absence_checks = iter((False, True, True))

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return _PresentApp(), window, object(), 5678
        raise OSError("iTerm reconnect failed")

    class _PresentApp:
        connection = None

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_wait_processes_absent_bounded",
        lambda pids, timeout: next(absence_checks),
    )

    result = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 150
        )
    )

    assert result == ("requested", False, 0, True)
    assert attempts == 2
    assert window.close_calls == [True]


def test_requested_tui_close_retries_same_exact_binding_once(
    monkeypatch: Any,
) -> None:
    window = _CloseWindow()
    attempts = 0

    async def exact_session(
        module: Any, state: dict[str, Any]
    ) -> tuple[Any, Any, Any, int]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient iTerm RPC failure")
        return _AbsentApp(), window, object(), 5678

    class _AbsentApp:
        def get_window_by_id(self, window_id: str) -> None:
            return None

    class _Module:
        pass

    monkeypatch.setattr(participant_driver, "_exact_session", exact_session)
    monkeypatch.setattr(
        participant_driver,
        "_wait_process_absent_bounded",
        lambda pid, timeout: True,
    )

    result = asyncio.run(
        participant_driver._safe_tui_close_async(  # noqa: SLF001
            _Module(), {"pid": 1234, "window_id": "owned"}, 150
        )
    )

    assert attempts == 2
    assert window.close_calls == [True]
    assert result == ("requested", False, 0, True)


def test_root_driver_accepts_only_exact_pingagent_transport_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    executable = tmp_path / "ai-harness-transport"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(participant_driver, "PINGAGENT_TRANSPORT", executable)
    state = {"session_id": "session-m4"}
    record = {
        "delivery_id": "delivery-m4",
        "payload_digest": "c" * 64,
        "events": [
            {
                "event": "attempt_started",
                "transport_attempt_id": "transport-m4",
            }
        ],
    }
    evidence = {
        "transport_contract_version": 1,
        "delivery_id": "delivery-m4",
        "transport_attempt_id": "transport-m4",
        "payload_digest": "c" * 64,
        "session_identity_sha256": hashlib.sha256(b"session-m4").hexdigest(),
        "injection_confirmed": True,
    }

    def completed(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        request = json.loads(kwargs["input"])
        assert request["session_id"] == "session-m4"
        assert "role" not in request
        result = {
            **evidence,
            "transport_evidence_digest": participant_driver.digest(evidence),
        }
        return subprocess.CompletedProcess(args[0], 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(participant_driver.subprocess, "run", completed)
    assert participant_driver._pingagent_deliver(  # noqa: SLF001
        state, record, "typed notification"
    )["transport_evidence_digest"] == participant_driver.digest(evidence)

    def stale(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        result = {
            **evidence,
            "transport_attempt_id": "wrong-attempt",
            "transport_evidence_digest": participant_driver.digest(evidence),
        }
        return subprocess.CompletedProcess(args[0], 0, json.dumps(result).encode(), b"")

    monkeypatch.setattr(participant_driver.subprocess, "run", stale)
    try:
        participant_driver._pingagent_deliver(state, record, "typed notification")  # noqa: SLF001
    except participant_driver.DriverError:
        pass
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("stale PingAgent evidence was accepted")


def test_delivery_notification_does_not_create_terminal_reply_loops() -> None:
    record = {
        "delivery_id": "delivery-terminal",
        "payload_digest": "c" * 64,
        "target": {
            "sender": {"participant_id": "reviewer"},
            "receiver": {"participant_id": "analyst"},
        },
    }
    message_path = Path(".ai-mailbox/inbox/analyst/delivery-terminal.md")
    for message_kind in (
        "collaboration.request",
        "collaboration.question",
        "collaboration.review-request",
        "collaboration.pushback",
    ):
        request = participant_driver._delivery_notification(  # noqa: SLF001
            record,
            message_kind,
            message_path,
            None,
        )
        assert request.startswith(
            "[ai-collab 收信] from=reviewer "
            f"kind={message_kind.removeprefix('collaboration.')} "
            "id=delivery-terminal | 请 Read "
        )
        assert "Review fixed SHA." not in request
        assert "AI Collaboration Harness typed delivery" not in request
        assert "AI_COLLAB_CONSUMED" not in request
        assert (
            "处理完用 ai-ping reviewer "
            + (
                "--kind review-response "
                if message_kind == "collaboration.review-request"
                else ""
            )
            + "--reply-to delivery-terminal --file <你的回复.md>"
        ) in request
        # The wrapper is found through the TUI's PATH; no absolute path leaks.
        assert "/" not in request.split("处理完用 ")[1]
        assert "Application Support" not in request
        assert "\n" not in request

    for message_kind in (
        "collaboration.response",
        "collaboration.review-response",
        "collaboration.notice",
        "collaboration.done",
    ):
        terminal = participant_driver._delivery_notification(  # noqa: SLF001
            record,
            message_kind,
            message_path,
            "delivery-request",
        )
        assert "reply_to=delivery-request" in terminal
        assert "这是对你之前消息(id=delivery-request)的回复" in terminal
        assert "ai-ping" not in terminal
        assert "\n" not in terminal


def test_delivery_message_moves_long_payload_out_of_tui_notification(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant generation"
    private_root.mkdir(mode=0o700)
    scenario_root = tmp_path / "workspace-delivery"
    scenario_root.mkdir(mode=0o700)
    workspace_path = scenario_root / "bundle" / "project"
    workspace_path.mkdir(parents=True)
    record = {
        "delivery_id": "delivery-long",
        "payload_digest": "d" * 64,
        "target": {
            "sender": {"participant_id": "reviewer"},
            "receiver": {"participant_id": "analyst2"},
        },
    }
    message = "诊断结果语义审核\n" + ("完整正文-0123456789\n" * 2_000)
    token = "e" * 48

    message_path = participant_driver._write_delivery_message(  # noqa: SLF001
        workspace_path,
        record,
        "collaboration.review-request",
        message,
        token,
        None,
    )
    notification = participant_driver._delivery_notification(  # noqa: SLF001
        record,
        "collaboration.review-request",
        message_path.relative_to(workspace_path),
        None,
    )
    persisted = message_path.read_text(encoding="utf-8")

    assert message_path == (
        workspace_path
        / ".ai-mailbox/inbox/analyst2/delivery-long.md"
    )
    assert stat.S_IMODE(message_path.stat().st_mode) == 0o600
    assert message in persisted
    assert "kind: review-request" in persisted
    assert (
        "需要回复时使用：ai-ping reviewer --kind review-response "
        "--reply-to delivery-long --file <你的回复.md>"
    ) in persisted
    assert str(private_root) not in persisted
    assert "处理完用 ai-ping reviewer --kind review-response" in notification
    assert str(private_root) not in notification
    assert f"consumption_token: {token}" in persisted
    assert f"AI_COLLAB_CONSUMED:{token}" not in persisted
    assert "前缀 `AI_COLLAB_CONSUMED:`" in persisted
    assert message not in notification
    assert token not in notification
    assert "Read .ai-mailbox/inbox/analyst2/delivery-long.md" in notification
    assert str(workspace_path) not in notification
    assert len(notification.encode("utf-8")) < 1_024


def test_delivery_command_is_bare_for_every_binding(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``ai-ping`` is the installer-managed command, so no binding — old or
    new, whatever its process PATH — is ever told a private-root path."""

    private_root = tmp_path / "participant generation"
    private_root.mkdir(mode=0o700)
    scenario_root = tmp_path / "workspace-delivery-legacy"
    scenario_root.mkdir(mode=0o700)
    workspace_path = scenario_root / "bundle" / "project"
    workspace_path.mkdir(parents=True)
    record = {
        "delivery_id": "delivery-legacy",
        "message_id": "message-legacy",
        "payload_digest": "f" * 64,
        "target": {
            "sender": {"participant_id": "reviewer"},
            "receiver": {"participant_id": "analyst"},
        },
        "events": [
            {
                "attempt_number": 1,
                "event": "attempt_started",
                "transport_attempt_id": "attempt-legacy",
            }
        ],
    }
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "session-legacy",
        "runtime_profile_ref": "runtime-profile.codex",
        "workspace_path": str(workspace_path),
    }
    notifications: list[str] = []
    monkeypatch.setattr(
        participant_driver,
        "_delivery_state",
        lambda payload, require_delivered: (private_root, state, record, "a" * 48),
    )
    monkeypatch.setattr(participant_driver, "_ensure_iterm_module", lambda _: object())

    async def validate_exact_session(module: Any, current: Any) -> None:
        return None

    monkeypatch.setattr(
        participant_driver, "_validate_exact_session_async", validate_exact_session
    )
    monkeypatch.setattr(
        participant_driver,
        "_pingagent_deliver",
        lambda current, delivery, notification: (
            notifications.append(notification) or {"transport_evidence_digest": "b" * 64}
        ),
    )
    monkeypatch.setattr(participant_driver, "_read_private", lambda _: dict(state))
    monkeypatch.setattr(participant_driver, "_write_private", lambda path, value: None)
    payload = {
        "delivery_record": record,
        "message": "review this",
        "message_kind": "collaboration.review-request",
        "consumption_token": "a" * 48,
        "runtime_ready_ack": {},
        "presentation_create_ack": {},
        "private_root": str(private_root),
        "workspace_path": str(scenario_root),
        "participant_working_directory": "bundle/project",
    }
    message_file = workspace_path / ".ai-mailbox" / "inbox" / "analyst" / "delivery-legacy.md"

    for marker in (None, True):
        if marker is not None:
            state["private_path_first"] = marker
        participant_driver.deliver(payload)
        assert (
            "处理完用 ai-ping reviewer --kind review-response "
            "--reply-to delivery-legacy --file <你的回复.md>"
        ) in notifications[-1]
        assert str(private_root) not in notifications[-1]
        persisted = message_file.read_text(encoding="utf-8")
        assert "需要回复时使用：ai-ping reviewer --kind review-response" in persisted
        assert str(private_root) not in persisted


def test_driver_delivery_accepts_host_message_limit(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant generation"
    private_root.mkdir(mode=0o700)
    scenario_root = tmp_path / "workspace-delivery-limit"
    scenario_root.mkdir(mode=0o700)
    workspace_path = scenario_root / "bundle" / "project"
    workspace_path.mkdir(parents=True)
    record = {
        "delivery_id": "delivery-limit",
        "message_id": "message-limit",
        "payload_digest": "f" * 64,
        "target": {
            "sender": {"participant_id": "reviewer"},
            "receiver": {"participant_id": "analyst"},
        },
        "events": [
            {
                "attempt_number": 1,
                "event": "attempt_started",
                "transport_attempt_id": "attempt-limit",
            }
        ],
    }
    state = {
        "schema_version": 1,
        "session_id": "session-limit",
        "runtime_profile_ref": "runtime-profile.codex",
        "workspace_path": str(workspace_path),
    }
    monkeypatch.setattr(
        participant_driver,
        "_delivery_state",
        lambda payload, require_delivered: (private_root, state, record, "a" * 48),
    )
    monkeypatch.setattr(participant_driver, "_ensure_iterm_module", lambda _: object())

    async def validate_exact_session(module: Any, current: Any) -> None:
        return None

    monkeypatch.setattr(
        participant_driver, "_validate_exact_session_async", validate_exact_session
    )
    monkeypatch.setattr(
        participant_driver,
        "_pingagent_deliver",
        lambda current, delivery, notification: {
            "transport_evidence_digest": "b" * 64
        },
    )
    monkeypatch.setattr(participant_driver, "_read_private", lambda _: dict(state))
    monkeypatch.setattr(participant_driver, "_write_private", lambda path, value: None)
    payload = {
        "delivery_record": record,
        "message": "x" * participant_driver.MAX_DELIVERY_MESSAGE_BYTES,
        "message_kind": "collaboration.review-request",
        "consumption_token": "a" * 48,
        "runtime_ready_ack": {},
        "presentation_create_ack": {},
        "private_root": str(private_root),
        "workspace_path": str(scenario_root),
        "participant_working_directory": "bundle/project",
    }

    result = participant_driver.deliver(payload)

    assert result["delivery_ack"]["delivery_id"] == "delivery-limit"
    assert not (workspace_path / ".gitignore").exists()
    persisted = participant_driver._delivery_message_path(  # noqa: SLF001
        workspace_path, record
    ).read_text(encoding="utf-8")
    assert "x" * participant_driver.MAX_DELIVERY_MESSAGE_BYTES in persisted

    payload["message"] += "x"
    with pytest.raises(participant_driver.DriverError, match="payload is invalid"):
        participant_driver.deliver(payload)


def test_delivery_workspace_migrates_only_the_exact_running_directory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    scenario_root = tmp_path / "workspace-existing"
    scenario_root.mkdir(mode=0o700)
    workspace_path = scenario_root / "bundle" / "project"
    workspace_path.mkdir(parents=True)
    state = {
        "schema_version": 1,
        "pid": 1234,
        "runtime_profile_ref": "runtime-profile.codex",
    }
    writes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        participant_driver, "_process_cwd", lambda pid: workspace_path
    )
    monkeypatch.setattr(
        participant_driver,
        "_write_private",
        lambda path, value: writes.append(dict(value)),
    )

    assert participant_driver._delivery_workspace(  # noqa: SLF001
        str(scenario_root), state, private_root, "bundle/project"
    ) == workspace_path
    assert state["workspace_path"] == str(workspace_path)
    assert writes[-1]["workspace_path"] == str(workspace_path)

    other = scenario_root / "bundle" / "other"
    other.mkdir()
    with pytest.raises(
        participant_driver.DriverError, match="workspace binding differs"
    ):
        participant_driver._delivery_workspace(  # noqa: SLF001
            str(scenario_root), state, private_root, "bundle/other"
        )


def test_delivery_workspace_uses_the_launch_profile_fallback(tmp_path: Path) -> None:
    private_root = tmp_path / "participant-private"
    private_root.mkdir(mode=0o700)
    scenario_root = tmp_path / "workspace-legacy-receipt"
    scenario_root.mkdir(mode=0o700)
    bundle = scenario_root / "bundle"
    bundle.mkdir(mode=0o700)
    state = {
        "schema_version": 1,
        "runtime_profile_ref": "runtime-profile.codex",
    }
    profile = participant_driver._runtime_profile(state)  # noqa: SLF001
    launch_path = participant_driver._workspace_path(  # noqa: SLF001
        str(scenario_root), profile
    )
    state["workspace_path"] = str(launch_path)

    assert participant_driver._delivery_workspace(  # noqa: SLF001
        str(scenario_root), state, private_root
    ) == launch_path == bundle


def test_delivery_mailbox_ignore_skips_a_non_repository_directory(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "bundle"
    workspace_path.mkdir()

    participant_driver._ensure_delivery_mailbox_ignored(workspace_path)  # noqa: SLF001

    assert not (workspace_path / ".gitignore").exists()


def test_delivery_driver_adds_the_mailbox_gitignore_once(tmp_path: Path) -> None:
    scenario_root = tmp_path / "workspace-driver-gitignore"
    scenario_root.mkdir(mode=0o700)
    workspace_path = scenario_root / "bundle" / "project"
    workspace_path.mkdir(parents=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(workspace_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    participant_driver._ensure_delivery_mailbox_ignored(workspace_path)  # noqa: SLF001
    first = (workspace_path / ".gitignore").read_bytes()
    participant_driver._ensure_delivery_mailbox_ignored(workspace_path)  # noqa: SLF001

    assert first == b".ai-mailbox/\n"
    assert (workspace_path / ".gitignore").read_bytes() == first
    assert subprocess.run(
        [
            "git",
            "-C",
            str(workspace_path),
            "check-ignore",
            "-q",
            ".ai-mailbox/message",
        ],
        check=False,
    ).returncode == 0


def test_generation_scoped_ping_survives_fresh_login_shell_environment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    private_root = tmp_path / "participant generation"
    private_root.mkdir(mode=0o700)
    context = tmp_path / "participant-context.json"
    context.write_text("{}\n", encoding="utf-8")
    context.chmod(0o600)
    collaboration_context = tmp_path / "participant-collaboration.json"
    collaboration_context.write_text("{}\n", encoding="utf-8")
    collaboration_context.chmod(0o600)
    client_pythonpath = tmp_path / "client pythonpath"
    client_pythonpath.mkdir(mode=0o700)
    client_executable = tmp_path / "client-python"
    client_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    client_executable.chmod(0o700)
    transport = tmp_path / "product ai-ping"
    transport.write_text(
        "#!/bin/zsh -f\n"
        "printf '%s\\n' \"$AI_COLLAB_HARNESS_CONTEXT\" "
        "\"$AI_COLLAB_HARNESS_CLIENT_EXECUTABLE\" "
        "\"$AI_COLLAB_HARNESS_CLIENT_PYTHONPATH\" "
        "\"$PYTHONDONTWRITEBYTECODE\" \"$PYTHONNOUSERSITE\" \"$@\"\n",
        encoding="utf-8",
    )
    transport.chmod(0o700)
    monkeypatch.setattr(participant_driver, "PINGAGENT_CLIENT", transport)
    participant_client = {
        "context_path": str(context),
        "client_executable": str(client_executable),
        "client_pythonpath": str(client_pythonpath),
        "collaboration_context_path": str(collaboration_context),
    }

    launcher = participant_driver._write_participant_ping(  # noqa: SLF001
        private_root, participant_client
    )
    completed = subprocess.run(
        ("/bin/zsh", "-lc", f"{shlex.quote(str(launcher))} reviewer hello"),
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        str(context),
        str(client_executable),
        str(client_pythonpath),
        "1",
        "1",
        "reviewer",
        "hello",
    ]
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("profile_id", "provider"),
    [
        ("runtime-profile.codex", "codex"),
        ("runtime-profile.claude", "claude"),
    ],
)
def test_vendor_session_hook_captures_and_reuses_exact_identity(
    tmp_path: Path,
    monkeypatch: Any,
    profile_id: str,
    provider: str,
) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    auth_context = tmp_path / "participant-auth.json"
    auth_context.write_text("{}\n", encoding="utf-8")
    auth_context.chmod(0o600)
    client_pythonpath = tmp_path / "client-pythonpath"
    client_pythonpath.mkdir(mode=0o700)
    unsigned = {
        "schema_version": 2,
        "context_revision": 7,
        "opening": "你们在结对工作。",
        "note": "先补测试",
        "scenario": {
            "project_instance_id": "project-one",
            "scenario_id": "scenario-one",
            "scenario_generation": 1,
            "objective": {
                "revision": 2,
                "objective": "Ship the collaboration overview",
                "acceptance_criteria": "Both vendor sessions receive this context.",
            },
        },
        "participant": {
            "participant_id": "analyst",
            "participant_generation": 1,
            "assignments": [
                {"attribute": "collaboration.role", "task_id": "analysis"}
            ],
        },
        "peers": [
            {
                "participant_id": "reviewer",
                "participant_generation": 1,
                "assignments": ["collaboration.role:review"],
            }
        ],
        "policy": {
            "policy_id": "policy.one",
            "policy_version": 1,
            "policy_digest": "c" * 64,
        },
        "allowed_outbound": [
            {
                "message_kind": "collaboration.review-request",
                "receiver_label": "reviewer",
            }
        ],
        "reply_semantics": {
            "reply_expected_kinds": ["collaboration.review-request"],
            "terminal_kinds": ["collaboration.review-response"],
            "preserve_reply_to": True,
            "machine_ack_is_silent": True,
        },
    }
    collaboration_context = tmp_path / "participant-collaboration.json"
    collaboration_context.write_text(
        json.dumps(
            {
                **unsigned,
                "context_digest": participant_driver.digest(unsigned),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    collaboration_context.chmod(0o600)
    participant_client = {
        "context_path": str(auth_context),
        "client_executable": str(Path(sys.executable).resolve()),
        "client_pythonpath": str(client_pythonpath),
        "collaboration_context_path": str(collaboration_context),
    }
    launch_spec = {
        "runtime_profile_ref": profile_id,
        "continuity_mode": "exact_resume",
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    monkeypatch.setattr(
        participant_driver,
        "_runtime_argv",
        lambda value: ("/usr/bin/true",),
    )

    argv, actual_provider, expected_session_id, resumed = (
        participant_driver._prepare_runtime_launch(  # noqa: SLF001
            private_root, launch_spec, participant_client
        )
    )
    assert actual_provider == provider
    assert resumed is False
    collaboration_prompt = participant_driver._collaboration_prompt_path(  # noqa: SLF001
        private_root
    ).read_text(encoding="utf-8")
    assert "reached through Harness ai-ping" in collaboration_prompt
    assert "generation-scoped communication command" in collaboration_prompt
    assert str(private_root / "ai-ping") in collaboration_prompt
    assert "not provider-native agent discovery or messaging" in collaboration_prompt
    assert "A successful ai-ping Host result is authoritative" in collaboration_prompt
    assert (
        'scenario objective (revision 2): "Ship the collaboration overview"'
        in collaboration_prompt
    )
    assert (
        'acceptance criteria: "Both vendor sessions receive this context."'
        in collaboration_prompt
    )
    oversized = copy.deepcopy(unsigned)
    oversized["scenario"]["objective"] = {
        "revision": 3,
        "objective": "x" * participant_driver.COLLABORATION_CONTEXT_LIMIT,
        "acceptance_criteria": "",
    }
    with pytest.raises(participant_driver.DriverError, match="collaboration.context-too-long"):
        participant_driver._render_collaboration_context(  # noqa: SLF001
            oversized, private_root / "ai-ping"
        )
    session_id = expected_session_id or "11111111-1111-4111-8111-111111111111"
    initial_identity_digest = participant_driver._verify_vendor_session(  # noqa: SLF001
        private_root,
        launch_spec,
        provider,
        expected_session_id,
        False,
    )
    assert initial_identity_digest is None
    unproven_argv, _, unproven_session_id, unproven_resumed = (
        participant_driver._prepare_runtime_launch(  # noqa: SLF001
            private_root, launch_spec, participant_client
        )
    )
    assert unproven_resumed is False
    if provider == "codex":
        assert unproven_session_id is None
        assert "resume" not in unproven_argv
        assert any(
            value.startswith("hooks.UserPromptSubmit=")
            for value in unproven_argv
        )
    else:
        assert unproven_session_id is not None
        assert unproven_argv[-2:] == ("--session-id", unproven_session_id)
        assert "--resume" not in unproven_argv
    environment = participant_driver._runtime_environment(  # noqa: SLF001
        launch_spec, participant_client, private_root
    )
    hook = subprocess.run(
        (sys.executable, str(participant_driver._vendor_hook_path(private_root))),  # noqa: SLF001
        input=json.dumps(
            {
                "session_id": session_id,
                "source": "startup",
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    assert hook.returncode == 0, hook.stderr
    if provider == "codex":
        assert "additionalContext" in hook.stdout
        assert "--dangerously-bypass-hook-trust" in argv
    else:
        assert hook.stdout == ""
        assert "--append-system-prompt-file" in argv
    state = {
        "vendor_provider": provider,
        "expected_vendor_session_id": expected_session_id,
        "vendor_resume_requested": False,
        "vendor_session_identity_sha256": None,
    }
    identity_digest = participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, state
    )
    if provider == "claude":
        assert identity_digest is None
        assert state["vendor_session_identity_sha256"] is None
        activity = subprocess.run(
            (
                sys.executable,
                str(participant_driver._vendor_hook_path(private_root)),  # noqa: SLF001
            ),
            input=json.dumps(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                }
            ),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        assert activity.returncode == 0, activity.stderr
        assert activity.stdout == ""
        identity_digest = participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
            private_root, launch_spec, state
        )
    assert identity_digest == hashlib.sha256(session_id.encode()).hexdigest()
    assert state["vendor_session_identity_sha256"] == identity_digest

    resumed_argv, _, resumed_session_id, resumed = (
        participant_driver._prepare_runtime_launch(  # noqa: SLF001
            private_root, launch_spec, participant_client
        )
    )
    assert resumed is True
    assert resumed_session_id == session_id
    if provider == "codex":
        assert resumed_argv[-2:] == ("resume", session_id)
    else:
        assert resumed_argv[-2:] == ("--resume", session_id)
    assert participant_driver._verify_vendor_session(  # noqa: SLF001
        private_root,
        launch_spec,
        provider,
        resumed_session_id,
        True,
    ) == hashlib.sha256(session_id.encode()).hexdigest()

    resumed_hook = subprocess.run(
        (sys.executable, str(participant_driver._vendor_hook_path(private_root))),  # noqa: SLF001
        input=json.dumps(
            {
                "session_id": session_id,
                "source": "resume",
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    assert resumed_hook.returncode == 0, resumed_hook.stderr
    assert participant_driver._verify_vendor_session(  # noqa: SLF001
        private_root,
        launch_spec,
        provider,
        resumed_session_id,
        True,
    ) == hashlib.sha256(session_id.encode()).hexdigest()


def test_claude_adopts_a_prompt_proven_session_selected_inside_the_owned_tui(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant"
    private_root.mkdir()
    launch_spec = {
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    generated_session_id = "11111111-1111-4111-8111-111111111111"
    selected_session_id = "22222222-2222-4222-8222-222222222222"
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_proof_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "claude",
            "session_id": selected_session_id,
            "source": "compact",
        },
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_activity_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "claude",
            "session_id": selected_session_id,
            "source": "UserPromptSubmit",
        },
    )
    state = {
        "vendor_provider": "claude",
        "expected_vendor_session_id": generated_session_id,
        "vendor_resume_requested": False,
        "vendor_session_identity_sha256": None,
    }

    identity = participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, state
    )

    assert identity == hashlib.sha256(selected_session_id.encode()).hexdigest()
    assert state["expected_vendor_session_id"] == selected_session_id
    assert state["vendor_resume_requested"] is True
    binding = participant_driver._stored_vendor_binding(  # noqa: SLF001
        private_root, launch_spec, "claude"
    )
    assert binding is not None
    assert binding["vendor_session_id"] == selected_session_id


def test_codex_adopts_a_prompt_proven_session_selected_after_recreate(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant"
    private_root.mkdir()
    launch_spec = {
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    previous_session_id = "11111111-1111-4111-8111-111111111111"
    selected_session_id = "22222222-2222-4222-8222-222222222222"
    participant_driver._record_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, "codex", previous_session_id
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_proof_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "codex",
            "session_id": selected_session_id,
            "source": "resume",
        },
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_activity_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "codex",
            "session_id": selected_session_id,
            "source": "UserPromptSubmit",
        },
    )
    state = {
        "vendor_provider": "codex",
        "expected_vendor_session_id": None,
        "vendor_resume_requested": False,
        "vendor_session_identity_sha256": None,
    }

    identity = participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, state
    )

    assert identity == hashlib.sha256(selected_session_id.encode()).hexdigest()
    assert state["expected_vendor_session_id"] == selected_session_id
    assert state["vendor_resume_requested"] is True
    binding = participant_driver._stored_vendor_binding(  # noqa: SLF001
        private_root, launch_spec, "codex"
    )
    assert binding is not None
    assert binding["vendor_session_id"] == selected_session_id


def test_codex_refuses_an_unprompted_session_selected_after_recreate(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "participant"
    private_root.mkdir()
    launch_spec = {
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    previous_session_id = "11111111-1111-4111-8111-111111111111"
    selected_session_id = "22222222-2222-4222-8222-222222222222"
    participant_driver._record_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, "codex", previous_session_id
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_proof_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "codex",
            "session_id": selected_session_id,
            "source": "resume",
        },
    )
    state = {
        "vendor_provider": "codex",
        "expected_vendor_session_id": None,
        "vendor_resume_requested": False,
        "vendor_session_identity_sha256": None,
    }

    with pytest.raises(
        participant_driver.DriverError,
        match="vendor session lifecycle proof differs",
    ):
        participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
            private_root, launch_spec, state
        )

    binding = participant_driver._stored_vendor_binding(  # noqa: SLF001
        private_root, launch_spec, "codex"
    )
    assert binding is not None
    assert binding["vendor_session_id"] == previous_session_id


@pytest.mark.parametrize(
    ("resume_requested", "record_activity", "accepted"),
    [(False, True, True), (False, False, False), (True, True, False)],
)
def test_vendor_adopts_startup_rebind_only_after_explicit_recreate(
    tmp_path: Path,
    resume_requested: bool,
    record_activity: bool,
    accepted: bool,
) -> None:
    private_root = tmp_path / "participant"
    private_root.mkdir()
    launch_spec = {
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    previous_session_id = "11111111-1111-4111-8111-111111111111"
    fallback_session_id = "22222222-2222-4222-8222-222222222222"
    participant_driver._record_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, "codex", previous_session_id
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_proof_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "codex",
            "session_id": fallback_session_id,
            "source": "startup",
        },
    )
    if record_activity:
        participant_driver._write_private(  # noqa: SLF001
            participant_driver._vendor_activity_path(private_root),  # noqa: SLF001
            {
                "schema_version": 1,
                "provider": "codex",
                "session_id": fallback_session_id,
                "source": "UserPromptSubmit",
            },
        )
    state = {
        "vendor_provider": "codex",
        "expected_vendor_session_id": (
            previous_session_id if resume_requested else None
        ),
        "vendor_resume_requested": resume_requested,
        "vendor_session_identity_sha256": None,
    }

    if not accepted:
        with pytest.raises(
            participant_driver.DriverError,
            match="vendor session lifecycle proof differs",
        ):
            participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
                private_root, launch_spec, state
            )
        expected_binding = previous_session_id
    else:
        identity = participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
            private_root, launch_spec, state
        )
        assert identity == hashlib.sha256(fallback_session_id.encode()).hexdigest()
        expected_binding = fallback_session_id

    binding = participant_driver._stored_vendor_binding(  # noqa: SLF001
        private_root, launch_spec, "codex"
    )
    assert binding is not None
    assert binding["vendor_session_id"] == expected_binding


@pytest.mark.parametrize(
    "activity_session_id", [None, "11111111-1111-4111-8111-111111111111"]
)
def test_claude_refuses_unproven_session_selected_inside_the_owned_tui(
    tmp_path: Path,
    activity_session_id: str | None,
) -> None:
    private_root = tmp_path / "participant"
    private_root.mkdir()
    launch_spec = {
        "continuity_binding_ref": "vendor-session-slot:primary",
    }
    generated_session_id = "11111111-1111-4111-8111-111111111111"
    selected_session_id = "22222222-2222-4222-8222-222222222222"
    participant_driver._record_vendor_session_binding(  # noqa: SLF001
        private_root, launch_spec, "claude", generated_session_id
    )
    participant_driver._write_private(  # noqa: SLF001
        participant_driver._vendor_proof_path(private_root),  # noqa: SLF001
        {
            "schema_version": 1,
            "provider": "claude",
            "session_id": selected_session_id,
            "source": "compact",
        },
    )
    if activity_session_id is not None:
        participant_driver._write_private(  # noqa: SLF001
            participant_driver._vendor_activity_path(private_root),  # noqa: SLF001
            {
                "schema_version": 1,
                "provider": "claude",
                "session_id": activity_session_id,
                "source": "UserPromptSubmit",
            },
        )
    state = {
        "vendor_provider": "claude",
        "expected_vendor_session_id": generated_session_id,
        "vendor_resume_requested": False,
        "vendor_session_identity_sha256": None,
    }

    with pytest.raises(
        participant_driver.DriverError,
        match="vendor session lifecycle proof differs",
    ):
        participant_driver._refresh_vendor_session_binding(  # noqa: SLF001
            private_root, launch_spec, state
        )

    binding = participant_driver._stored_vendor_binding(  # noqa: SLF001
        private_root, launch_spec, "claude"
    )
    assert binding is not None
    assert binding["vendor_session_id"] == generated_session_id
    assert state["expected_vendor_session_id"] == generated_session_id
    assert state["vendor_session_identity_sha256"] is None


def test_workspace_path_prefers_the_declared_project_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "bundle" / "someproject").mkdir(parents=True)
    profile = {"working_directory": "bundle"}
    chosen = participant_driver._workspace_path(
        str(tmp_path), profile, "bundle/someproject"
    )
    assert chosen == (tmp_path / "bundle" / "someproject").resolve()


def test_workspace_path_falls_back_to_the_profile_without_declaration(
    tmp_path: Path,
) -> None:
    (tmp_path / "bundle").mkdir()
    profile = {"working_directory": "bundle"}
    chosen = participant_driver._workspace_path(str(tmp_path), profile)
    assert chosen == (tmp_path / "bundle").resolve()


def test_workspace_path_rejects_invalid_declared_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "bundle").mkdir()
    profile = {"working_directory": "bundle"}
    for declared in ("", "../outside", "/absolute", 7, "bundle/missing-dir"):
        with pytest.raises(participant_driver.DriverError):
            participant_driver._workspace_path(str(tmp_path), profile, declared)


def test_environment_probe_is_registry_driven_and_reports_missing_tools(
    monkeypatch: Any,
) -> None:
    profiles = {
        "runtime-profile.codex": {
            "display_name": "Codex",
            "executable": "codex",
        },
        "runtime-profile.claude": {
            "display_name": "Claude",
            "executable": "claude",
        },
    }
    monkeypatch.setattr(
        participant_driver, "_runtime_profiles", lambda: profiles
    )
    monkeypatch.setattr(
        participant_driver,
        "_resolve_executable",
        lambda executable: (
            "/opt/tools/codex" if executable == "codex" else None
        ),
    )
    monkeypatch.setattr(
        participant_driver,
        "_observed_tool_version",
        lambda path: {
            "/opt/tools/codex": "codex-cli 9.9.9",
            "/bin/zsh": "zsh 5.9",
        }.get(path),
    )
    monkeypatch.setattr(
        participant_driver, "_target_application_running", lambda _bundle: False
    )
    monkeypatch.setattr(
        participant_driver, "_installed_application_path", lambda _bundle: None
    )

    result = participant_driver.environment_probe({})
    observations = {
        value["subject_ref"]: value
        for value in result["environment_observations"]
    }
    # Every runtime subject comes from the (patched) registry data alone: a
    # new vendor profile is covered with zero code changes here.
    assert set(observations) == {
        "runtime-profile.codex",
        "runtime-profile.claude",
        "presentation.iterm2",
        "shell.zsh",
    }
    codex = observations["runtime-profile.codex"]
    assert codex["status"] == "available"
    assert codex["observed_version"] == "codex-cli 9.9.9"
    assert codex["provider_error_code"] is None
    assert codex["remediation_ref"] is None
    claude = observations["runtime-profile.claude"]
    assert claude["status"] == "missing"
    assert claude["observed_version"] is None
    assert claude["provider_error_code"] == "environment.executable-not-found"
    assert claude["remediation_ref"] == "environment.install-executable"
    iterm = observations["presentation.iterm2"]
    assert iterm["status"] == "missing"
    assert iterm["remediation_ref"] == "environment.install-application"
    zsh = observations["shell.zsh"]
    assert zsh["status"] == "available"
    assert zsh["observed_version"] == "zsh 5.9"
    assert [
        value["subject_ref"] for value in result["environment_observations"]
    ] == sorted(observations)
    for value in observations.values():
        assert len(value["evidence_digest"]) == 64


def test_environment_probe_rejects_payload_fields() -> None:
    with pytest.raises(participant_driver.DriverError):
        participant_driver.environment_probe({"unexpected": True})


def test_presentation_permission_request_guards_absent_target(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        participant_driver, "_target_application_running", lambda _bundle: False
    )
    called: list[bool] = []
    monkeypatch.setattr(
        participant_driver,
        "automation_permission_status",
        lambda _bundle, **kwargs: called.append(True),
    )
    monkeypatch.setattr(
        participant_driver,
        "_provoke_automation_prompt",
        lambda _bundle: called.append(True),
    )
    observation = participant_driver.permission_request({})[
        "permission_observations"
    ][0]
    assert observation["status"] == "unavailable"
    assert observation["provider_error_code"] == (
        "iterm-presentation.target-not-running"
    )
    assert observation["remediation_ref"] == "iterm-presentation.launch-target"
    assert observation["prompt_requested"] is False
    assert called == []


def test_presentation_permission_request_prompts_when_target_running(
    monkeypatch: Any,
) -> None:
    asked: list[bool] = []
    provoked: list[str] = []

    def fake_status(_bundle: str, **kwargs: Any) -> dict[str, Any]:
        asked.append(kwargs.get("ask_user_if_needed") is True)
        return {"status": "authorized", "authorized": True, "prompt_requested": True}

    monkeypatch.setattr(
        participant_driver, "_target_application_running", lambda _bundle: True
    )
    monkeypatch.setattr(
        participant_driver,
        "_provoke_automation_prompt",
        lambda bundle: provoked.append(bundle),
    )
    monkeypatch.setattr(
        participant_driver, "automation_permission_status", fake_status
    )
    monkeypatch.setattr(
        participant_driver,
        "authentication_bypass_status",
        lambda: {"cookie_authentication_required": True},
    )
    monkeypatch.setattr(
        participant_driver,
        "private_unix_socket_status",
        lambda: {
            "present": True,
            "is_unix_socket": True,
            "owned_by_current_user": True,
            "local_only_ready": True,
        },
    )
    observation = participant_driver.permission_request({})[
        "permission_observations"
    ][0]
    # The dialog is summoned by one real harmless AppleEvent; the observation
    # afterwards is a pure read (no second pre-flight ask).
    assert provoked == [participant_driver.EXPECTED_ITERM_BUNDLE_ID]
    assert asked == [False]
    assert observation["status"] == "granted"
    assert observation["prompt_requested"] is True


def test_collaboration_prompt_shows_opening_and_note_without_roles() -> None:
    unsigned = {
        "schema_version": 2,
        "context_revision": 3,
        "opening": "你们在结对工作。\nYou are pair programming.",
        "note": "先补测试",
        "scenario": {
            "project_instance_id": "project-one",
            "scenario_id": "scenario-one",
            "scenario_generation": 1,
            "objective": {"revision": 1, "objective": "修复对账偏差", "acceptance_criteria": "CI 全绿"},
        },
        "participant": {"participant_id": "claude", "participant_generation": 2, "assignments": []},
        "peers": [{"participant_id": "codex", "participant_generation": 1, "assignments": []}],
        "policy": None,
        "allowed_outbound": [],
        "reply_semantics": {
            "reply_expected_kinds": [],
            "terminal_kinds": [],
            "preserve_reply_to": True,
            "machine_ack_is_silent": True,
        },
    }
    value = {**unsigned, "context_digest": participant_driver.digest(unsigned)}
    rendered = participant_driver._render_collaboration_context(  # noqa: SLF001
        value, Path("/private/generation/ai-ping")
    )
    assert "opening from the person you work for:\n你们在结对工作。\nYou are pair programming.\n" in rendered
    assert "note from the person you work for: 先补测试\n" in rendered
    assert "colleagues in this room: codex" in rendered
    assert "your assignments" not in rendered
    assert "allowed outbound routes" not in rendered
