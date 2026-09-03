#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""AI Collab generic process runtime + official iTerm presentation driver."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import plistlib
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import time
import urllib.request
import uuid
import venv
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_collab_macos_automation_preflight import (
    AutomationPreflightError,
    authentication_bypass_status,
    automation_permission_status,
    private_unix_socket_status,
    target_application_running,
)

try:
    from platformdirs import user_config_dir
except ModuleNotFoundError:  # pragma: no cover - both shipping runtimes have it
    # The bundled interpreter and the project venv both provide platformdirs.
    # A bare `python3` on some machines does not, and this driver must not stop
    # launching participants over the location of an optional overlay file. The
    # fallback repeats what platformdirs returns on macOS, which is the only
    # platform this driver supports (it drives iTerm2).
    def user_config_dir(app_name: str) -> str:
        return str(Path.home() / "Library" / "Application Support" / app_name)


ADAPTER_PROTOCOL_VERSION = 1
STATE_SCHEMA_VERSION = 1
OWNER_VARIABLE = "user.ai_collab_harness_owner"
OPERATION_TIMEOUT_SECONDS = 15.0
PROCESS_WAIT_SECONDS = 8.0
ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "scripts" / "ai_collab_iterm_adapter_lock.json"
TOPOLOGY_HELPER = ROOT / "scripts" / "ai_collab_window_topology_screens.swift"
PROFILE_PATH = ROOT / "ai_collab_runtime_profiles.json"
# The shipped registry above lives inside the signed application bundle, where
# editing it breaks the code signature. Operators change runtime profiles here
# instead: arguments (including the vendor approval flags), working directory,
# or an entirely new profile for a vendor CLI we do not ship.
PROFILE_OVERLAY_ENVIRONMENT_KEY = "AI_COLLAB_RUNTIME_PROFILES_OVERLAY"
PROFILE_OVERLAY_PATH = (
    Path(user_config_dir("AI Collab")).expanduser() / "runtime_profiles.overlay.json"
)
_SOURCE_TRANSPORT = ROOT / "pingagent" / "bin" / "ai-harness-transport"
_EMBEDDED_TRANSPORT = ROOT.parent / "PingAgent" / "bin" / "ai-harness-transport"
PINGAGENT_TRANSPORT = (
    _SOURCE_TRANSPORT if _SOURCE_TRANSPORT.is_file() else _EMBEDDED_TRANSPORT
)
PINGAGENT_BIN = PINGAGENT_TRANSPORT.parent
PINGAGENT_CLIENT = PINGAGENT_BIN / "ai-ping"
CONSUMPTION_TIMEOUT_SECONDS = 240.0
STARTUP_POLL_SECONDS = 0.25
STARTUP_STABLE_OBSERVATIONS = 4
STARTUP_GATE_MAX_SECONDS = 240
COLLABORATION_CONTEXT_LIMIT = 5_000
STARTUP_CONFIRM_KEYS = {"1", "2", "\r", "\x1b[A", "\x1b[B"}
SENDER_SESSION_CONNECT_ATTEMPTS = 3
SENDER_SESSION_RETRY_SECONDS = 0.1
PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
EXPECTED_ITERM_BUNDLE_ID = "com.googlecode.iterm2"


class DriverError(RuntimeError):
    pass


class PresentationPreflightError(DriverError):
    def __init__(self, provider_error_code: str, remediation_ref: str):
        super().__init__("iTerm private API is unavailable")
        self.provider_error_code = provider_error_code
        self.remediation_ref = remediation_ref


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def runtime_descriptor() -> dict[str, Any]:
    return {
        "driver_kind": "runtime",
        "driver_id": "runtime.generic-process",
        "contract_version": 2,
        "implementation_ref": "implementation.edgestudio-generic-process-v1",
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
        "continuity_modes": ["explicit_recreate", "exact_resume"],
        "supports_harness_process_binding": True,
        "supports_ready_ack": True,
        "supports_delivery_ack": True,
        "supports_session_drift_signal": True,
        "supports_vendor_session_identity": True,
        "vendor_lifecycle_surface": "runtime-lifecycle.vendor-cli-v1",
        "optional_vendor_lifecycle_operations": ["vendor_resume", "vendor_bind"],
        "retention_modes": ["none", "harness_context", "vendor_binding"],
        "repair_modes": ["recreate_generation", "rebind_owned_process"],
        "error_namespace": "generic-runtime.error",
        "redaction_profile_ref": "redaction.owner-private-v1",
    }


def presentation_descriptor() -> dict[str, Any]:
    return {
        "driver_kind": "presentation",
        "driver_id": "presentation.iterm2",
        "contract_version": 1,
        "implementation_ref": "implementation.edgestudio-iterm2-v1",
        "interaction_modes": ["tui"],
        "lifecycle_operations": [
            "permission_probe",
            "permission_request",
            "environment_probe",
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
        "error_namespace": "iterm-presentation.error",
        "redaction_profile_ref": "redaction.owner-private-v1",
    }


def registry() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "participant_driver_contract_version": 2,
        "runtime_drivers": [runtime_descriptor()],
        "presentation_drivers": [presentation_descriptor()],
    }


def capability_snapshot() -> str:
    profiles = _runtime_profiles()
    value = {
        "registry": registry(),
        "platform": "macos" if sys.platform == "darwin" else sys.platform,
        "runtime_profiles": sorted(profiles),
        "runtime_profiles_digest": digest({"profiles": list(profiles.values())}),
        "vendor_session_identity": True,
    }
    return digest(value)


def resolve(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"launch_spec", "presentation_driver_id"}:
        raise DriverError("resolve payload differs")
    launch = payload["launch_spec"]
    if (
        not isinstance(launch, dict)
        or launch.get("driver_id") != "runtime.generic-process"
        or launch.get("driver_contract_version") != 2
        or launch.get("continuity_mode")
        not in {"explicit_recreate", "exact_resume"}
        or launch.get("runtime_profile_ref") not in _runtime_profiles()
    ):
        raise DriverError("runtime launch capability is unavailable")
    if (
        launch.get("continuity_mode") == "exact_resume"
        and _runtime_profiles()[launch["runtime_profile_ref"]]["vendor_lifecycle"]
        is None
    ):
        raise DriverError("runtime exact resume capability is unavailable")
    interaction = launch.get("interaction_mode")
    requested_presentation = payload["presentation_driver_id"]
    if interaction == "tui":
        if requested_presentation != "presentation.iterm2" or sys.platform != "darwin":
            raise DriverError("TUI presentation capability is unavailable")
        presentation: dict[str, Any] | None = presentation_descriptor()
    elif interaction == "headless":
        if requested_presentation is not None:
            raise DriverError("headless runtime cannot have presentation")
        presentation = None
    else:
        raise DriverError("interaction mode is unavailable")
    composed = registry()
    return {
        "driver_registry": composed,
        "driver_registry_digest": digest(composed),
        "runtime_descriptor": runtime_descriptor(),
        "presentation_descriptor": presentation,
        "capability_snapshot_digest": capability_snapshot(),
    }


def list_templates(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload:
        raise DriverError("template list payload differs")
    templates = []
    for profile_id, profile in sorted(_runtime_profiles().items()):
        interaction_mode = "headless" if profile["startup_gate"] is None else "tui"
        vendor_lifecycle = profile["vendor_lifecycle"]
        exact_resume = vendor_lifecycle is not None
        templates.append(
            {
                "template_id": profile_id,
                # Registry data, not a transformed identifier. Deriving the label
                # from the profile id meant "runtime-profile.inert" reached the
                # employee-facing picker as "Inert", reading like a role rather
                # than the do-nothing fixture it is.
                "display_name": profile["display_name"],
                "launch_spec": {
                    "driver_id": "runtime.generic-process",
                    "driver_contract_version": 2,
                    "interaction_mode": interaction_mode,
                    "continuity_mode": (
                        "exact_resume" if exact_resume else "explicit_recreate"
                    ),
                    "runtime_profile_ref": profile_id,
                    "model_binding": None,
                    "continuity_binding_ref": (
                        vendor_lifecycle["continuity_binding_ref"]
                        if exact_resume
                        else None
                    ),
                },
                "presentation_driver_id": (
                    "presentation.iterm2" if interaction_mode == "tui" else None
                ),
            }
        )
    return {"templates": templates}


def _presentation_observation(
    *, prompt_requested: bool, ask_user_if_needed: bool | None = None
) -> dict[str, Any]:
    """Build one provider-neutral presentation observation from live platform state.

    ``prompt_requested`` records whether this operation asked the platform to
    show its consent UI; ``ask_user_if_needed`` controls the underlying
    determine call and defaults to the same value. permission_request passes
    False explicitly because it summons the dialog with a real AppleEvent
    first and then only reads the resulting verdict here.
    """

    if ask_user_if_needed is None:
        ask_user_if_needed = prompt_requested
    try:
        automation = automation_permission_status(
            EXPECTED_ITERM_BUNDLE_ID, ask_user_if_needed=ask_user_if_needed
        )
        authentication = authentication_bypass_status()
        private_socket = private_unix_socket_status()
        target_running = _target_application_running(EXPECTED_ITERM_BUNDLE_ID)
    except AutomationPreflightError as exc:
        raise DriverError("presentation permission observation is unavailable") from exc

    provider_error_code: str | None = None
    remediation_ref: str | None = None
    automation_status = automation.get("status")
    if authentication.get("cookie_authentication_required") is not True:
        status = "restricted"
        provider_error_code = "iterm-presentation.authentication-bypass-present"
        remediation_ref = "iterm-presentation.remove-authentication-bypass"
    elif automation_status == "denied":
        status = "denied"
        provider_error_code = "iterm-presentation.automation-denied"
        remediation_ref = "system-settings.automation"
    elif automation_status == "not_determined_no_prompt":
        status = "not_determined"
        provider_error_code = "iterm-presentation.automation-not-determined"
        remediation_ref = "presentation.permission-request"
    elif automation.get("authorized") is not True:
        status = "unknown"
        provider_error_code = "iterm-presentation.automation-unknown"
        remediation_ref = "system-settings.automation"
    elif not target_running:
        status = "unavailable"
        provider_error_code, remediation_ref = _iterm_private_api_failure(
            private_socket,
            target_running=target_running,
        )
    elif private_socket.get("local_only_ready") is not True:
        status = "unavailable"
        provider_error_code, remediation_ref = _iterm_private_api_failure(
            private_socket,
            target_running=target_running,
        )
    else:
        status = "granted"

    evidence = {
        "automation_status": automation_status,
        "target_running": target_running,
        "prompt_requested": automation.get("prompt_requested") is True,
        "cookie_authentication_required": authentication.get(
            "cookie_authentication_required"
        )
        is True,
        "api_server_configured": private_socket.get("api_server_configured") is True,
        "api_server_enabled": private_socket.get("api_server_enabled") is True,
        "api_server_explicitly_disabled": (
            private_socket.get("api_server_explicitly_disabled") is True
        ),
        "private_socket_present": private_socket.get("present") is True,
        "private_socket_is_unix": private_socket.get("is_unix_socket") is True,
        "private_socket_owned": private_socket.get("owned_by_current_user") is True,
    }
    return {
        "permission_id": "permission.presentation-control",
        "provider_ref": "platform.macos-automation",
        "subject_ref": "presentation.iterm2",
        "status": status,
        "evidence_digest": digest(evidence),
        "provider_error_code": provider_error_code,
        "remediation_ref": remediation_ref,
        "prompt_requested": prompt_requested,
    }


def _target_application_running(bundle_identifier: str) -> bool:
    """Observe whether the automation target is running, without TCC side effects."""

    return target_application_running(bundle_identifier)


def _iterm_private_api_failure(
    private_socket: Mapping[str, Any],
    *,
    target_running: bool,
) -> tuple[str, str]:
    if not target_running:
        return (
            "iterm-presentation.target-not-running",
            "iterm-presentation.launch-target",
        )
    if private_socket.get("api_server_enabled") is not True:
        return (
            "iterm-presentation.python-api-disabled",
            "iterm-presentation.enable-python-api",
        )
    if private_socket.get("present") is not True:
        return (
            "iterm-presentation.private-socket-missing",
            "iterm-presentation.restart-after-python-api",
        )
    if (
        private_socket.get("is_unix_socket") is not True
        or private_socket.get("owned_by_current_user") is not True
    ):
        return (
            "iterm-presentation.private-socket-invalid",
            "iterm-presentation.reset-private-api-socket",
        )
    return (
        "iterm-presentation.private-api-unavailable",
        "iterm-presentation.enable-python-api",
    )


def _require_iterm_private_api_ready() -> None:
    try:
        private_socket = private_unix_socket_status()
        target_running = _target_application_running(EXPECTED_ITERM_BUNDLE_ID)
    except AutomationPreflightError as exc:
        raise DriverError("presentation permission observation is unavailable") from exc
    if target_running and private_socket.get("local_only_ready") is True:
        return
    provider_error_code, remediation_ref = _iterm_private_api_failure(
        private_socket,
        target_running=target_running,
    )
    raise PresentationPreflightError(provider_error_code, remediation_ref)


def permission_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Observe the current presentation permission without prompting or launching."""

    if payload:
        raise DriverError("permission probe payload differs")
    return {
        "permission_observations": [_presentation_observation(prompt_requested=False)]
    }


def permission_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Let macOS show its consent prompt for presentation control.

    Reserved for an explicit user gesture. The target application must already
    be running: the driver never launches it on the user's behalf, and the
    system consent dialog lacks context when the target is absent.
    """

    if payload:
        raise DriverError("permission request payload differs")
    if not _target_application_running(EXPECTED_ITERM_BUNDLE_ID):
        return {
            "permission_observations": [
                {
                    "permission_id": "permission.presentation-control",
                    "provider_ref": "platform.macos-automation",
                    "subject_ref": "presentation.iterm2",
                    "status": "unavailable",
                    "evidence_digest": digest(
                        {"target_running": False, "prompt_requested": False}
                    ),
                    "provider_error_code": "iterm-presentation.target-not-running",
                    "remediation_ref": "iterm-presentation.launch-target",
                    "prompt_requested": False,
                }
            ]
        }
    _provoke_automation_prompt(EXPECTED_ITERM_BUNDLE_ID)
    return {
        "permission_observations": [
            _presentation_observation(
                prompt_requested=True, ask_user_if_needed=False
            )
        ]
    }


def _provoke_automation_prompt(bundle_identifier: str) -> None:
    """Send one harmless real AppleEvent so macOS shows its consent dialog.

    A pre-flight ask (AEDeterminePermissionToAutomateTarget with
    askUserIfNeeded) is silently auto-denied when the responsible process is a
    background service, verified live: the request returned denied, no dialog
    appeared, and nothing was persisted. The dialog only appears for an
    actual send — the same mechanism that summoned it during a real
    participant start. The command is read-only, and the verdict is observed
    afterwards, never inferred from this call's outcome.
    """

    script = f'tell application id "{bundle_identifier}" to count windows'
    try:
        subprocess.run(
            ("/usr/bin/osascript", "-e", script),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        # The dialog may still be on screen; the observation below simply
        # reports the still-unanswered state and the owner can ask again.
        pass


def _observed_tool_version(executable_path: str) -> str | None:
    """Best-effort `<tool> --version` first line; None instead of guesses."""

    try:
        completed = subprocess.run(
            (executable_path, "--version"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Cold-starting Node/Deno vendor CLIs can exceed 5s on first run;
            # stay bounded but generous, and fail to None rather than guess.
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    lines = completed.stdout.strip().splitlines()
    if not lines:
        return None
    version = lines[0].strip()[:200]
    return version or None


def _installed_application_path(bundle_identifier: str) -> Path | None:
    """Locate an installed app bundle by identifier without launching it."""

    # Standard install locations first: deterministic, and never outvoted by
    # a stale Spotlight-indexed copy (Downloads, backup volumes).
    for root in (Path("/Applications"), Path.home() / "Applications"):
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*.app")):
            info_path = candidate / "Contents" / "Info.plist"
            try:
                with open(info_path, "rb") as handle:
                    info = plistlib.load(handle)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            if info.get("CFBundleIdentifier") == bundle_identifier:
                return candidate
    try:
        completed = subprocess.run(
            (
                "/usr/bin/mdfind",
                f"kMDItemCFBundleIdentifier == '{bundle_identifier}'",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            candidate = Path(line.strip())
            if candidate.suffix == ".app" and candidate.is_dir():
                return candidate
    return None


def _application_bundle_version(app_path: Path) -> str | None:
    try:
        with open(app_path / "Contents" / "Info.plist", "rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    raw = info.get("CFBundleShortVersionString")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:200]
    return None


def _runtime_environment_observation(
    profile_id: str, profile: Mapping[str, Any]
) -> dict[str, Any]:
    executable = profile["executable"]
    resolved = _resolve_executable(executable)
    version = _observed_tool_version(resolved) if resolved is not None else None
    evidence = {
        "profile_id": profile_id,
        "executable": executable,
        "resolved_path": resolved,
        "observed_version": version,
    }
    return {
        "subject_ref": profile_id,
        "display_name": profile["display_name"],
        "status": "available" if resolved is not None else "missing",
        "observed_version": version,
        "evidence_digest": digest(evidence),
        "provider_error_code": (
            None if resolved is not None else "environment.executable-not-found"
        ),
        "remediation_ref": (
            None if resolved is not None else "environment.install-executable"
        ),
    }


def _presentation_environment_observation() -> dict[str, Any]:
    running = _target_application_running(EXPECTED_ITERM_BUNDLE_ID)
    app_path = _installed_application_path(EXPECTED_ITERM_BUNDLE_ID)
    installed = running or app_path is not None
    version = (
        _application_bundle_version(app_path) if app_path is not None else None
    )
    evidence = {
        "bundle_identifier": EXPECTED_ITERM_BUNDLE_ID,
        "running": running,
        "installed": installed,
        "bundle_path": str(app_path) if app_path is not None else None,
        "observed_version": version,
    }
    return {
        "subject_ref": "presentation.iterm2",
        "display_name": "iTerm2",
        "status": "available" if installed else "missing",
        "observed_version": version,
        "evidence_digest": digest(evidence),
        "provider_error_code": (
            None if installed else "environment.application-not-found"
        ),
        "remediation_ref": (
            None if installed else "environment.install-application"
        ),
    }


def _shell_environment_observation() -> dict[str, Any]:
    shell_path = "/bin/zsh"
    available = Path(shell_path).is_file()
    version = _observed_tool_version(shell_path) if available else None
    evidence = {
        "shell_path": shell_path,
        "available": available,
        "observed_version": version,
    }
    return {
        "subject_ref": "shell.zsh",
        "display_name": "zsh login shell",
        "status": "available" if available else "missing",
        "observed_version": version,
        "evidence_digest": digest(evidence),
        "provider_error_code": (
            None if available else "environment.shell-not-found"
        ),
        "remediation_ref": None if available else "environment.install-shell",
    }


def environment_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Observe machine readiness for every registry-declared dependency.

    Data-driven: the runtime executables come from the runtime-profile
    registry (bundle plus operator overlay), never from names in code, so a
    new vendor profile is covered without touching this operation.
    """

    if payload:
        raise DriverError("environment probe payload differs")
    observations = [
        _runtime_environment_observation(profile_id, profile)
        for profile_id, profile in sorted(_runtime_profiles().items())
    ]
    observations.append(_presentation_environment_observation())
    observations.append(_shell_environment_observation())
    if len(observations) > 64:
        raise DriverError("environment probe observation count differs")
    return {
        "environment_observations": sorted(
            observations, key=lambda value: value["subject_ref"]
        )
    }


def _private_root(raw: Any) -> Path:
    if not isinstance(raw, str):
        raise DriverError("private root is invalid")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise DriverError("private root is invalid")
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise DriverError("private root is invalid")
    return path


def _write_private(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(5)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_private(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DriverError("private binding state is unavailable")
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
        raise DriverError("private binding state is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriverError("private binding state is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise DriverError("private binding state is invalid")
    return value


def _state_path(private_root: Path) -> Path:
    return private_root / "driver-binding.json"


def _sender_auth_diagnostic_path(private_root: Path) -> Path:
    return private_root / "sender-auth-diagnostic.json"


def _launch_diagnostic_path(private_root: Path) -> Path:
    return private_root / "launch-diagnostic.json"


def _launch_reason_code(stage: str, exc: BaseException) -> str:
    known = {
        "iTerm runtime process did not become ready": "process.readiness-timeout",
        "runtime TUI did not become input-ready": "tui.readiness-timeout",
        "runtime TUI startup prompt needs attention": "tui.startup-prompt-unknown",
        "iTerm application state is unavailable": "iterm.application-unavailable",
        "iTerm private API is unavailable": "iterm.private-api-unavailable",
        "iTerm top-level window was not created": "iterm.window-not-created",
        "iTerm create returned an ambiguous window identity": (
            "iterm.window-identity-ambiguous"
        ),
        "iTerm owner marker round trip differed": "iterm.owner-marker-drift",
        "owned process observation is unavailable": "process.observation-unavailable",
    }
    if _retryable_iterm_connection_error(exc):
        return "iterm.connection-closed"
    if isinstance(exc, asyncio.TimeoutError):
        return f"{stage}.timeout"
    if isinstance(exc, DriverError):
        return known.get(str(exc), f"{stage}.driver-rejected")
    return f"{stage}.unexpected-failure"


def _record_launch_failure(
    private_root: Path,
    *,
    stage: str,
    exc: BaseException,
    cleanup_outcome: str,
    process_observation: Mapping[str, Any] | None = None,
) -> None:
    """Persist a bounded owner-private launch reason without raw TUI or paths."""

    try:
        value = {
            "schema_version": 1,
            "outcome": "rejected",
            "stage": stage,
            "reason_code": _launch_reason_code(stage, exc),
            "cleanup_outcome": cleanup_outcome,
        }
        provider_error_code = getattr(exc, "provider_error_code", None)
        remediation_ref = getattr(exc, "remediation_ref", None)
        if isinstance(provider_error_code, str) and isinstance(remediation_ref, str):
            value["provider_error_code"] = provider_error_code
            value["remediation_ref"] = remediation_ref
        if isinstance(process_observation, Mapping):
            pid = process_observation.get("pid")
            pgid = process_observation.get("pgid")
            identity = process_observation.get("identity_sha256")
            if (
                isinstance(pid, int)
                and not isinstance(pid, bool)
                and pid > 1
                and isinstance(pgid, int)
                and not isinstance(pgid, bool)
                and pgid > 1
                and isinstance(identity, str)
                and re.fullmatch(r"[0-9a-f]{64}", identity) is not None
            ):
                value["process_observation"] = {
                    "pid": pid,
                    "pgid": pgid,
                    "identity_sha256": identity,
                }
        _write_private(
            _launch_diagnostic_path(private_root),
            value,
        )
    except OSError:
        pass


def _sender_auth_reason_code(stage: str, exc: BaseException) -> str:
    if _retryable_iterm_connection_error(exc):
        return "iterm.connection-closed"
    known = {
        "owned process is absent": "process.absent",
        "owned process binding drifted": "process.binding-drift",
        "owned process observation is unavailable": "process.observation-unavailable",
        "owned process relationship is unavailable": "process.relationship-unavailable",
        "owned process relationship is invalid": "process.relationship-invalid",
        "participant sender process is not an owned descendant": (
            "process.not-owned-descendant"
        ),
        "owned iTerm window is absent": "iterm.window-absent",
        "owned iTerm delivery topology drifted": "iterm.topology-drift",
        "owned iTerm delivery marker drifted": "iterm.owner-marker-drift",
        "owned iTerm delivery job identity is invalid": "iterm.job-identity-invalid",
    }
    if isinstance(exc, DriverError):
        return known.get(str(exc), f"{stage}.driver-rejected")
    return f"{stage}.unexpected-failure"


def _record_sender_auth_failure(
    private_root: Path, *, stage: str, exc: BaseException
) -> None:
    """Persist an owner-private bounded reason without exposing PID/path details."""

    try:
        _write_private(
            _sender_auth_diagnostic_path(private_root),
            {
                "schema_version": 1,
                "outcome": "rejected",
                "stage": stage,
                "reason_code": _sender_auth_reason_code(stage, exc),
            },
        )
    except OSError:
        pass


def _runtime_profile_overlay_path() -> Path:
    override = os.environ.get(PROFILE_OVERLAY_ENVIRONMENT_KEY, "").strip()
    if override:
        return Path(override).expanduser()
    return PROFILE_OVERLAY_PATH


def _runtime_profiles() -> dict[str, dict[str, Any]]:
    result = _validated_runtime_profiles(PROFILE_PATH, "registry")
    overlay_path = _runtime_profile_overlay_path()
    if overlay_path.is_file():
        # A present-but-broken overlay fails the operation. Ignoring it would
        # silently launch a vendor CLI with the shipped approval flags after the
        # operator had already decided otherwise.
        result.update(_validated_runtime_profiles(overlay_path, "overlay"))
    if "runtime-profile.inert" not in result:
        raise DriverError("runtime profile registry lacks inert baseline")
    return result


# Profile ids double as environment.probe subject_refs, so they must live in
# the supervisor's namespaced-id domain, and the fixed observation subjects
# stay reserved. Enforced at registry load so an operator learns immediately,
# not by losing the entire diagnostics surface later.
_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_RESERVED_PROFILE_PREFIXES = ("shell.", "presentation.")


def _validated_runtime_profiles(
    path: Path, source: str
) -> dict[str, dict[str, Any]]:
    """Read one profile document. The overlay is held to the registry's rules.

    Both documents go through this single function so an operator-supplied
    profile cannot be looser than a shipped one.
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriverError(f"runtime profile {source} is unavailable") from exc
    rows = value.get("profiles") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(rows, list)
    ):
        raise DriverError(f"runtime profile {source} is invalid")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "profile_id",
                "display_name",
                "executable",
                "arguments",
                "working_directory",
                "process_match",
                "accepts_typed_delivery",
                "vendor_lifecycle",
                "safe_close",
                "startup_gate",
            }
            or not isinstance(row["profile_id"], str)
            or _PROFILE_ID_RE.fullmatch(row["profile_id"]) is None
            or row["profile_id"].startswith(_RESERVED_PROFILE_PREFIXES)
            or not isinstance(row["display_name"], str)
            or not row["display_name"].strip()
            or len(row["display_name"]) > 120
            or not isinstance(row["executable"], str)
            or not row["executable"]
            or not isinstance(row["arguments"], list)
            or any(not isinstance(item, str) or not item for item in row["arguments"])
            or not isinstance(row["working_directory"], str)
            or not row["working_directory"]
            or not isinstance(row["process_match"], str)
            or not row["process_match"]
            or not isinstance(row["accepts_typed_delivery"], bool)
            or not _valid_vendor_lifecycle(row["vendor_lifecycle"])
            or not _valid_safe_close(row["safe_close"], row["startup_gate"])
            or not _valid_startup_gate(row["startup_gate"])
            or row["profile_id"] in result
        ):
            raise DriverError(f"runtime profile {source} is invalid")
        result[row["profile_id"]] = row
    return result


def _valid_vendor_lifecycle(value: Any) -> bool:
    return value is None or (
        isinstance(value, dict)
        and set(value) == {"adapter_id", "continuity_binding_ref"}
        and value["adapter_id"]
        in {
            "vendor-lifecycle.codex-cli-v1",
            "vendor-lifecycle.claude-cli-v1",
        }
        and isinstance(value["continuity_binding_ref"], str)
        and value["continuity_binding_ref"]
    )


def _valid_safe_close(value: Any, startup_gate: Any) -> bool:
    if not isinstance(value, dict):
        return False
    idle_detection = value.get("idle_detection")
    expected = (
        {"idle_detection", "drain_sequence", "ready_pattern"}
        if idle_detection == "input_ready_pattern"
        else {"idle_detection", "drain_sequence"}
    )
    if (
        set(value) != expected
        or idle_detection not in {"always", "startup_ready_pattern", "input_ready_pattern"}
        or not isinstance(value.get("drain_sequence"), list)
        or len(value["drain_sequence"]) > 4
        or any(
            not isinstance(item, str) or not item or len(item) > 8
            for item in value["drain_sequence"]
        )
        or (
            value["idle_detection"] == "startup_ready_pattern"
            and startup_gate is None
        )
        or (
            idle_detection == "input_ready_pattern"
            and (
                not isinstance(value.get("ready_pattern"), str)
                or not value["ready_pattern"]
                or len(value["ready_pattern"]) > 512
            )
        )
    ):
        return False
    if idle_detection == "input_ready_pattern":
        try:
            re.compile(value["ready_pattern"])
        except re.error:
            return False
    return True


def _valid_startup_gate(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    legacy_fields = {
        "scope",
        "prompt_pattern",
        "ready_pattern",
        "confirm_sequence",
        "requires_workspace_path",
        "timeout_seconds",
    }
    rules_fields = {
        "scope",
        "prompt_rules",
        "ready_pattern",
        "timeout_seconds",
    }
    if (
        frozenset(value) not in {frozenset(legacy_fields), frozenset(rules_fields)}
        or value.get("scope") != "harness_verified_workspace"
        or not isinstance(value.get("ready_pattern"), str)
        or not value["ready_pattern"]
        or len(value["ready_pattern"]) > 512
        or not isinstance(value.get("timeout_seconds"), int)
        or not 5 <= value["timeout_seconds"] <= STARTUP_GATE_MAX_SECONDS
    ):
        return False
    rules = _startup_prompt_rules(value)
    if any(not isinstance(rule, dict) for rule in rules):
        return False
    if (
        not 1 <= len(rules) <= 8
        or len({rule.get("rule_id") for rule in rules}) != len(rules)
        or any(
            set(rule)
            != {
                "rule_id",
                "prompt_pattern",
                "confirm_sequence",
                "requires_workspace_path",
            }
            or not isinstance(rule.get("rule_id"), str)
            or _PROFILE_ID_RE.fullmatch(rule["rule_id"]) is None
            or not isinstance(rule.get("prompt_pattern"), str)
            or not rule["prompt_pattern"]
            or len(rule["prompt_pattern"]) > 1024
            or not isinstance(rule.get("confirm_sequence"), list)
            or not 1 <= len(rule["confirm_sequence"]) <= 4
            or any(
                key not in STARTUP_CONFIRM_KEYS
                for key in rule["confirm_sequence"]
            )
            or rule.get("requires_workspace_path") is not True
            for rule in rules
        )
    ):
        return False
    try:
        re.compile(value["ready_pattern"])
        for rule in rules:
            re.compile(rule["prompt_pattern"])
    except re.error:
        return False
    return True


def _startup_prompt_rules(gate: Mapping[str, Any]) -> list[dict[str, Any]]:
    rules = gate.get("prompt_rules")
    if isinstance(rules, list):
        return rules
    return [
        {
            "rule_id": "startup.legacy-prompt",
            "prompt_pattern": gate.get("prompt_pattern"),
            "confirm_sequence": gate.get("confirm_sequence"),
            "requires_workspace_path": gate.get("requires_workspace_path"),
        }
    ]


def _runtime_profile(launch_spec: Mapping[str, Any]) -> dict[str, Any]:
    profile = _runtime_profiles().get(launch_spec.get("runtime_profile_ref"))
    if profile is None:
        raise DriverError("runtime profile is unavailable")
    return profile


def _resolve_executable(executable: str) -> str | None:
    """Resolve a runtime-profile executable exactly the way launch does.

    PATH first, then the user's login zsh (`whence -p`) so App-launched Hosts
    see the same tools an interactive shell would. Returns None when absent.
    """

    if "/" in executable:
        return executable if Path(executable).is_file() else None
    resolved = shutil.which(executable)
    if resolved is not None:
        return resolved
    try:
        discovered = subprocess.run(
            ("/bin/zsh", "-lic", f"whence -p {shlex.quote(executable)}"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    candidate = Path(discovered.stdout.strip())
    if discovered.returncode == 0 and candidate.is_absolute() and candidate.is_file():
        return str(candidate)
    return None


def _runtime_argv(launch_spec: Mapping[str, Any]) -> tuple[str, ...]:
    profile = _runtime_profile(launch_spec)
    resolved = _resolve_executable(profile["executable"])
    if resolved is None:
        raise DriverError("runtime profile executable is unavailable")
    return (resolved, *profile["arguments"])


def _vendor_adapter(launch_spec: Mapping[str, Any]) -> str | None:
    profile = _runtime_profiles().get(launch_spec.get("runtime_profile_ref"))
    if profile is None:
        return None
    lifecycle = profile["vendor_lifecycle"]
    if lifecycle is None:
        return None
    return {
        "vendor-lifecycle.codex-cli-v1": "codex",
        "vendor-lifecycle.claude-cli-v1": "claude",
    }[lifecycle["adapter_id"]]


def _vendor_binding_path(private_root: Path) -> Path:
    return private_root / "vendor-session-binding.json"


def _vendor_proof_path(private_root: Path) -> Path:
    return private_root / "vendor-session-proof.json"


def _vendor_activity_path(private_root: Path) -> Path:
    return private_root / "vendor-session-activity.json"


def _collaboration_prompt_path(private_root: Path) -> Path:
    return private_root / "participant-collaboration-context.txt"


def _participant_ping_path(private_root: Path) -> Path:
    return private_root / "ai-ping"


def _vendor_hook_path(private_root: Path) -> Path:
    return private_root / "vendor-session-start-hook.py"


def _claude_settings_path(private_root: Path) -> Path:
    return private_root / "claude-harness-settings.json"


def _read_collaboration_context(participant_client: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(participant_client["collaboration_context_path"])
    value = _read_private(path)
    if set(value) != {
        "schema_version",
        "context_revision",
        "context_digest",
        "scenario",
        "participant",
        "peers",
        "policy",
        "allowed_outbound",
        "reply_semantics",
    }:
        raise DriverError("participant collaboration context differs")
    unsigned = {key: item for key, item in value.items() if key != "context_digest"}
    if (
        value["schema_version"] != 1
        or not isinstance(value["context_revision"], int)
        or isinstance(value["context_revision"], bool)
        or value["context_revision"] < 1
        or value["context_digest"] != digest(unsigned)
    ):
        raise DriverError("participant collaboration context differs")
    scenario = value.get("scenario")
    if isinstance(scenario, dict) and "objective" not in scenario:
        scenario["objective"] = {
            "revision": 0,
            "objective": "",
            "acceptance_criteria": "",
        }
        unsigned = {key: item for key, item in value.items() if key != "context_digest"}
        value["context_digest"] = digest(unsigned)
    return value


def _render_collaboration_context(
    value: Mapping[str, Any], participant_ping: Path
) -> str:
    participant = value["participant"]
    scenario = value["scenario"]
    policy = value["policy"]
    assignments = participant.get("assignments", [])
    peers = value["peers"]
    routes = value["allowed_outbound"]
    assignment_text = ", ".join(
        (
            item["attribute"]
            if item["task_id"] is None
            else f"{item['attribute']}:{item['task_id']}"
        )
        for item in assignments
    ) or "none"
    peer_text = ", ".join(
        f"{item['participant_id']} ({', '.join(item.get('assignments', [])) or 'unassigned'})"
        for item in peers
    ) or "none"
    route_text = ", ".join(
        f"{item['message_kind']} -> {item['receiver_label']}" for item in routes
    ) or "none"
    policy_text = (
        "not configured"
        if policy is None
        else f"{policy['policy_id']} v{policy['policy_version']} digest={policy['policy_digest']}"
    )
    objective = scenario.get("objective")
    if (
        not isinstance(objective, dict)
        or set(objective) != {"revision", "objective", "acceptance_criteria"}
        or not isinstance(objective["revision"], int)
        or isinstance(objective["revision"], bool)
        or objective["revision"] < 0
        or not isinstance(objective["objective"], str)
        or not isinstance(objective["acceptance_criteria"], str)
        or (objective["revision"] == 0) != (objective["objective"] == "")
    ):
        raise DriverError("collaboration.objective-invalid")
    objective_text = (
        "not set"
        if objective["revision"] == 0
        else json.dumps(objective["objective"], ensure_ascii=False)
    )
    acceptance_text = (
        "not set"
        if not objective["acceptance_criteria"]
        else json.dumps(objective["acceptance_criteria"], ensure_ascii=False)
    )
    ping_command = shlex.quote(str(participant_ping))
    rendered = (
        "AI Collaboration Harness participant context\n"
        f"context revision: {value['context_revision']}\n"
        f"scenario: {scenario['scenario_id']}\n"
        f"scenario objective (revision {objective['revision']}): {objective_text}\n"
        f"acceptance criteria: {acceptance_text}\n"
        f"your Harness identity: {participant['participant_id']} generation "
        f"{participant['participant_generation']}\n"
        f"your assignments: {assignment_text}\n"
        f"scenario peers: {peer_text}\n"
        f"current policy: {policy_text}\n"
        f"allowed outbound routes: {route_text}\n"
        f"your generation-scoped communication command: {ping_command}\n"
        "Collaboration rules:\n"
        "- Treat this context as identity/routing information, not authorization; the live Host policy is authoritative.\n"
        "- Scenario peers listed here are reached through Harness ai-ping, not provider-native agent discovery or messaging.\n"
        f"- When the employee asks you to contact a peer, use exactly {ping_command} with that peer's Harness participant identity. Do not substitute a global ai-ping or a provider-native tool.\n"
        "- A successful ai-ping Host result is authoritative; do not report that a peer is unreachable based on provider-native discovery.\n"
        "- Reply to request, question, review-request, or pushback deliveries when work or an answer is required, preserving --reply-to.\n"
        "- Response, review-response, notice, and done deliveries are terminal/informational unless their payload explicitly requests new work; do not send receipt-only replies.\n"
        "- Accepted/delivered/consumed acknowledgements are machine state and should remain silent in the conversation.\n"
    )
    if len(rendered) > COLLABORATION_CONTEXT_LIMIT:
        raise DriverError("collaboration.context-too-long")
    return rendered


def _write_participant_ping(
    private_root: Path, participant_client: Mapping[str, Any]
) -> Path:
    """Create the exact generation-scoped Agent entrypoint.

    Vendor tool runners may start a fresh login shell instead of inheriting the
    TUI process environment.  The private entrypoint therefore carries only the
    Host-issued client locations needed to reach the authoritative Host.  It
    contains no sender identity or authority; the context capability, live
    process ancestry, generation fence, and Host policy remain authoritative.
    """

    _validate_participant_client(participant_client)
    path = _participant_ping_path(private_root)
    environment = {
        "AI_COLLAB_HARNESS_CONTEXT": participant_client["context_path"],
        "AI_COLLAB_HARNESS_CLIENT_EXECUTABLE": participant_client[
            "client_executable"
        ],
        "AI_COLLAB_HARNESS_CLIENT_PYTHONPATH": participant_client[
            "client_pythonpath"
        ],
        "AI_COLLAB_HARNESS_COLLABORATION_CONTEXT": participant_client[
            "collaboration_context_path"
        ],
    }
    path.write_text(
        "#!/bin/zsh -f\nset -eu\numask 077\n"
        + "".join(
            f"export {key}={shlex.quote(value)}\n"
            for key, value in environment.items()
        )
        + f"exec {shlex.quote(str(PINGAGENT_CLIENT))} \"$@\"\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o700)
    return path


def _write_vendor_hook(private_root: Path) -> Path:
    path = _vendor_hook_path(private_root)
    path.write_text(
        """#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

payload = json.load(sys.stdin)
provider = os.environ["AI_COLLAB_HARNESS_VENDOR_PROVIDER"]
proof_path = Path(os.environ["AI_COLLAB_HARNESS_VENDOR_PROOF"])
activity_path = Path(os.environ["AI_COLLAB_HARNESS_VENDOR_ACTIVITY"])
context_path = Path(os.environ["AI_COLLAB_HARNESS_COLLABORATION_PROMPT"])
event_name = (
    payload.get("hook_event_name")
    or payload.get("hookEventName")
    or ("SessionStart" if payload.get("source") is not None else None)
)
if event_name == "SessionStart":
    proof = {
        "schema_version": 1,
        "provider": provider,
        "session_id": payload.get("session_id"),
        "source": payload.get("source"),
    }
    temporary = proof_path.with_name(
        "." + proof_path.name + "." + str(os.getpid()) + ".hook.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(proof, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, proof_path)
    os.chmod(proof_path, 0o600)
elif event_name == "UserPromptSubmit":
    activity = {
        "schema_version": 1,
        "provider": provider,
        "session_id": payload.get("session_id"),
        "source": event_name,
    }
    temporary = activity_path.with_name(
        "." + activity_path.name + "." + str(os.getpid()) + ".hook.tmp"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(activity, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, activity_path)
    os.chmod(activity_path, 0o600)
if provider == "codex" and event_name == "SessionStart":
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context_path.read_text(encoding="utf-8"),
        }
    }))
""",
        encoding="utf-8",
    )
    os.chmod(path, 0o700)
    return path


def _stored_vendor_binding(
    private_root: Path, launch_spec: Mapping[str, Any], provider: str
) -> dict[str, Any] | None:
    path = _vendor_binding_path(private_root)
    if not path.exists():
        return None
    value = _read_private(path)
    if (
        set(value)
        != {
            "schema_version",
            "provider",
            "continuity_binding_ref",
            "vendor_session_id",
        }
        or value["provider"] != provider
        or value["continuity_binding_ref"]
        != launch_spec.get("continuity_binding_ref")
        or not isinstance(value["vendor_session_id"], str)
        or not value["vendor_session_id"]
    ):
        raise DriverError("vendor session binding differs")
    return value


def _vendor_session_activity_matches(
    private_root: Path, provider: str, session_id: str
) -> bool:
    path = _vendor_activity_path(private_root)
    if not path.exists():
        return False
    value = _read_private(path)
    if (
        set(value) != {"schema_version", "provider", "session_id", "source"}
        or value["schema_version"] != STATE_SCHEMA_VERSION
        or value["provider"] != provider
        or value["source"] != "UserPromptSubmit"
    ):
        raise DriverError("vendor session activity differs")
    try:
        activity_session_id = str(uuid.UUID(value["session_id"]))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DriverError("vendor session activity is invalid") from exc
    return activity_session_id == session_id


def _record_vendor_session_binding(
    private_root: Path,
    launch_spec: Mapping[str, Any],
    provider: str,
    session_id: str,
    *,
    allow_rebind: bool = False,
) -> None:
    continuity_binding_ref = launch_spec.get("continuity_binding_ref")
    if not isinstance(continuity_binding_ref, str) or not continuity_binding_ref:
        return
    binding = {
        "schema_version": STATE_SCHEMA_VERSION,
        "provider": provider,
        "continuity_binding_ref": continuity_binding_ref,
        "vendor_session_id": session_id,
    }
    existing = _stored_vendor_binding(private_root, launch_spec, provider)
    if existing is not None and existing != binding and not allow_rebind:
        raise DriverError("vendor session binding differs")
    if existing != binding:
        _write_private(_vendor_binding_path(private_root), binding)


def _prepare_runtime_launch(
    private_root: Path,
    launch_spec: Mapping[str, Any],
    participant_client: Mapping[str, Any],
) -> tuple[tuple[str, ...], str | None, str | None, bool]:
    argv = list(_runtime_argv(launch_spec))
    provider = _vendor_adapter(launch_spec)
    if provider is None:
        if launch_spec.get("continuity_mode") == "exact_resume":
            raise DriverError("runtime exact resume capability is unavailable")
        return tuple(argv), None, None, False

    context = _read_collaboration_context(participant_client)
    participant_ping = _write_participant_ping(private_root, participant_client)
    prompt_path = _collaboration_prompt_path(private_root)
    prompt_path.write_text(
        _render_collaboration_context(context, participant_ping), encoding="utf-8"
    )
    os.chmod(prompt_path, 0o600)
    hook_path = _write_vendor_hook(private_root)
    proof_path = _vendor_proof_path(private_root)
    proof_path.unlink(missing_ok=True)

    binding = None
    if launch_spec.get("continuity_mode") == "exact_resume":
        binding = _stored_vendor_binding(private_root, launch_spec, provider)
    expected_session_id = (
        None if binding is None else binding["vendor_session_id"]
    )
    resume_requested = binding is not None

    if provider == "codex":
        hook_command = f"/usr/bin/python3 {shlex.quote(str(hook_path))}"
        inline_hooks = (
            '[{matcher="startup|resume|compact",hooks=['
            "{type=\"command\",command="
            + json.dumps(hook_command)
            + ",timeout=10,additionalContextLimit=5000}]}]"
        )
        argv.extend(
            (
                "--dangerously-bypass-hook-trust",
                "-c",
                f"hooks.SessionStart={inline_hooks}",
            )
        )
        if expected_session_id is not None:
            argv.extend(("resume", expected_session_id))
    else:
        settings_path = _claude_settings_path(private_root)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup|resume|compact",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"/usr/bin/python3 {shlex.quote(str(hook_path))}",
                                "timeout": 10,
                            }
                        ],
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"/usr/bin/python3 {shlex.quote(str(hook_path))}",
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            }
        }
        _write_private(settings_path, settings)
        argv.extend(
            (
                "--settings",
                str(settings_path),
                "--append-system-prompt-file",
                str(prompt_path),
            )
        )
        if expected_session_id is None:
            expected_session_id = str(uuid.uuid4())
            argv.extend(("--session-id", expected_session_id))
        else:
            argv.extend(("--resume", expected_session_id))
    return tuple(argv), provider, expected_session_id, resume_requested


def _verify_vendor_session(
    private_root: Path,
    launch_spec: Mapping[str, Any],
    provider: str | None,
    expected_session_id: str | None,
    resume_requested: bool,
) -> str | None:
    if provider is None:
        return None
    proof_path = _vendor_proof_path(private_root)
    if not proof_path.exists():
        # A ready but untouched vendor TUI may not have materialized a
        # conversation yet.  Do not persist a generated Claude --session-id as
        # resumable until the SessionStart hook proves the vendor accepted it.
        # For an existing binding, a successful --resume launch and input-ready
        # TUI prove the already recorded session is loadable even if no new hook
        # event fires until the first prompt.
        if expected_session_id is not None and resume_requested:
            normalized_session_id = str(uuid.UUID(expected_session_id))
            _record_vendor_session_binding(
                private_root, launch_spec, provider, normalized_session_id
            )
            return hashlib.sha256(
                normalized_session_id.encode("utf-8")
            ).hexdigest()
        return None
    proof = _read_private(proof_path)
    session_id = proof.get("session_id")
    try:
        normalized_session_id = str(uuid.UUID(session_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DriverError("vendor session identity is invalid") from exc
    expected_sources = (
        {"resume", "compact"} if resume_requested else {"startup", "compact"}
    )
    expected_identity_differs = False
    if expected_session_id is not None:
        try:
            expected_identity_differs = (
                normalized_session_id != str(uuid.UUID(expected_session_id))
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise DriverError("expected vendor session identity is invalid") from exc
    proven_in_tui_rebind = (
        expected_identity_differs
        and proof.get("source") in {"resume", "compact"}
        and _vendor_session_activity_matches(
            private_root, provider, normalized_session_id
        )
    )
    if (
        set(proof) != {"schema_version", "provider", "session_id", "source"}
        or proof["provider"] != provider
        or proof["source"] not in expected_sources
        or (expected_identity_differs and not proven_in_tui_rebind)
    ):
        raise DriverError("vendor session lifecycle proof differs")
    source = proof["source"]
    materialized = source in {"resume", "compact"} or _vendor_session_activity_matches(
        private_root, provider, normalized_session_id
    )
    if provider == "codex" and source == "startup":
        # Codex currently emits SessionStart only after the first real prompt,
        # unlike Claude which emits it at TUI startup before a transcript exists.
        materialized = True
    if not materialized:
        return None
    _record_vendor_session_binding(
        private_root,
        launch_spec,
        provider,
        normalized_session_id,
        allow_rebind=proven_in_tui_rebind,
    )
    return hashlib.sha256(normalized_session_id.encode("utf-8")).hexdigest()


def _refresh_vendor_session_binding(
    private_root: Path,
    launch_spec: Mapping[str, Any],
    state: dict[str, Any],
) -> str | None:
    provider = state.get("vendor_provider")
    expected_session_id = state.get("expected_vendor_session_id")
    resume_requested = state.get("vendor_resume_requested")
    if provider is None:
        return None
    if (
        not isinstance(provider, str)
        or (
            expected_session_id is not None
            and not isinstance(expected_session_id, str)
        )
        or not isinstance(resume_requested, bool)
    ):
        raise DriverError("stored vendor session launch state differs")
    identity_digest = _verify_vendor_session(
        private_root,
        launch_spec,
        provider,
        expected_session_id,
        resume_requested,
    )
    if identity_digest is not None:
        binding = _stored_vendor_binding(private_root, launch_spec, provider)
        if binding is None:  # pragma: no cover - verification records it
            raise DriverError("vendor session binding is unavailable")
        rebound = binding["vendor_session_id"] != expected_session_id
        state["expected_vendor_session_id"] = binding["vendor_session_id"]
        state["vendor_resume_requested"] = resume_requested or rebound
        state["vendor_session_identity_sha256"] = identity_digest
        _write_private(_state_path(private_root), state)
    return identity_digest


def _runtime_environment(
    launch_spec: Mapping[str, Any],
    participant_client: Mapping[str, Any],
    private_root: Path | None = None,
) -> dict[str, str]:
    _validate_participant_client(participant_client)
    executable = Path(_runtime_argv(launch_spec)[0])
    search_path = os.pathsep.join(
        dict.fromkeys(
            (
                str(PINGAGENT_BIN),
                str(executable.parent),
                "/usr/local/bin",
                "/opt/homebrew/bin",
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
    )
    environment = {"PATH": search_path, "LANG": "en_US.UTF-8"}
    environment.update(
        {
            "AI_COLLAB_HARNESS_CONTEXT": participant_client["context_path"],
            "AI_COLLAB_HARNESS_CLIENT_EXECUTABLE": participant_client[
                "client_executable"
            ],
            "AI_COLLAB_HARNESS_CLIENT_PYTHONPATH": participant_client[
                "client_pythonpath"
            ],
            "AI_COLLAB_HARNESS_COLLABORATION_CONTEXT": participant_client[
                "collaboration_context_path"
            ],
        }
    )
    provider = _vendor_adapter(launch_spec)
    if provider is not None:
        if private_root is None:
            raise DriverError("vendor launch private root is unavailable")
        environment.update(
            {
                "AI_COLLAB_HARNESS_VENDOR_PROVIDER": provider,
                "AI_COLLAB_HARNESS_VENDOR_PROOF": str(
                    _vendor_proof_path(private_root)
                ),
                "AI_COLLAB_HARNESS_VENDOR_ACTIVITY": str(
                    _vendor_activity_path(private_root)
                ),
                "AI_COLLAB_HARNESS_COLLABORATION_PROMPT": str(
                    _collaboration_prompt_path(private_root)
                ),
            }
        )
    environment.update(
        {
            key: os.environ[key]
            for key in PROXY_ENVIRONMENT_KEYS
            if key in os.environ
        }
    )
    return environment


def _validate_participant_client(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "context_path",
        "client_executable",
        "client_pythonpath",
        "collaboration_context_path",
    }:
        raise DriverError("participant client context differs")
    context_path = Path(value["context_path"])
    client_executable = Path(value["client_executable"])
    client_pythonpath = Path(value["client_pythonpath"])
    collaboration_context_path = Path(value["collaboration_context_path"])
    if (
        PINGAGENT_CLIENT.is_symlink()
        or not PINGAGENT_CLIENT.is_file()
        or PINGAGENT_CLIENT.stat().st_uid not in {0, os.getuid()}
        or stat.S_IMODE(PINGAGENT_CLIENT.stat().st_mode) & 0o022
        or any(
            not path.is_absolute() or path.is_symlink()
            for path in (
                context_path,
                client_executable,
                client_pythonpath,
                collaboration_context_path,
            )
        )
        or not context_path.is_file()
        or context_path.stat().st_uid != os.getuid()
        or stat.S_IMODE(context_path.stat().st_mode) != 0o600
        or not collaboration_context_path.is_file()
        or collaboration_context_path.stat().st_uid != os.getuid()
        or stat.S_IMODE(collaboration_context_path.stat().st_mode) != 0o600
        or not client_executable.is_file()
        or client_executable.stat().st_uid not in {0, os.getuid()}
        or stat.S_IMODE(client_executable.stat().st_mode) & 0o022
        or not client_pythonpath.is_dir()
        or client_pythonpath.stat().st_uid not in {0, os.getuid()}
        or stat.S_IMODE(client_pythonpath.stat().st_mode) & 0o022
    ):
        raise DriverError("participant client context is unsafe")


def _workspace_path(
    raw: Any, profile: Mapping[str, Any], declared: Any = None
) -> Path:
    if not isinstance(raw, str):
        raise DriverError("workspace path is invalid")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise DriverError("workspace path is invalid")
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o022:
        raise DriverError("workspace path is invalid")
    # The workspace receipt's declared project directory wins over the
    # profile's static working directory: the registry is product-generic and
    # cannot know where a given project materializes inside the bundle, while
    # the adapter that provisioned the workspace knows exactly.
    if declared is None:
        relative = profile["working_directory"]
    elif isinstance(declared, str) and declared:
        relative = declared
    else:
        raise DriverError("declared participant working directory is invalid")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(part in {"", ".."} for part in parts):
        raise DriverError("runtime working directory is invalid")
    try:
        candidate = path.joinpath(*parts).resolve(strict=True)
    except OSError as exc:
        raise DriverError("runtime working directory is invalid") from exc
    if (
        not candidate.is_relative_to(path)
        or not candidate.is_dir()
        or candidate.stat().st_uid != os.getuid()
    ):
        raise DriverError("runtime working directory is invalid")
    return candidate


def _process_observation(pid: int) -> dict[str, Any]:
    if not isinstance(pid, int) or pid <= 1:
        raise DriverError("process identity is invalid")
    try:
        completed = subprocess.run(
            ("/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DriverError("owned process observation is unavailable") from exc
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line:
        raise DriverError("owned process is absent")
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError as exc:
        raise DriverError("owned process is absent") from exc
    value = {"pid": pid, "pgid": pgid, "ps": line}
    return {**value, "identity_sha256": digest(value)}


def _matching_process_group_observations(
    process_group_id: int, process_match: str
) -> list[dict[str, Any]]:
    if (
        not isinstance(process_group_id, int)
        or isinstance(process_group_id, bool)
        or process_group_id <= 1
    ):
        raise DriverError("process identity is invalid")
    try:
        completed = subprocess.run(
            ("/bin/ps", "-g", str(process_group_id), "-o", "pid="),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return []
    if completed.returncode != 0:
        return []
    observations: list[dict[str, Any]] = []
    for field in completed.stdout.split():
        try:
            pid = int(field)
            observation = _process_observation(pid)
        except (ValueError, DriverError):
            continue
        if (
            observation["pgid"] == process_group_id
            and process_match in observation["ps"]
        ):
            observations.append(observation)
    return sorted(observations, key=lambda value: value["pid"])


def _exact_process_group_observations(
    process_group_id: int,
) -> list[dict[str, int]]:
    """Observe every member of one exact process group, or fail closed."""

    if (
        not isinstance(process_group_id, int)
        or isinstance(process_group_id, bool)
        or process_group_id <= 1
    ):
        raise DriverError("process identity is invalid")
    try:
        completed = subprocess.run(
            ("/bin/ps", "-axo", "pid=", "-o", "pgid="),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DriverError("owned process observation is unavailable") from exc
    if completed.returncode != 0:
        raise DriverError("owned process observation is unavailable")
    observations: list[dict[str, int]] = []
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise DriverError("owned process observation is unavailable")
        try:
            pid, pgid = (int(field) for field in fields)
        except ValueError as exc:
            raise DriverError("owned process observation is unavailable") from exc
        if pgid == process_group_id:
            observations.append({"pid": pid, "pgid": pgid})
    return sorted(observations, key=lambda value: value["pid"])


def _stable_job_observation(
    observation: Mapping[str, Any], process_match: str
) -> Mapping[str, Any]:
    candidates = _matching_process_group_observations(
        observation["pgid"], process_match
    )
    for candidate in candidates:
        if candidate["pid"] == candidate["pgid"]:
            return candidate
    if candidates:
        return candidates[0]
    return observation


def _process_relationship(pid: int) -> dict[str, int]:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 2:
        raise DriverError("owned process relationship is invalid")
    completed = subprocess.run(
        ("/bin/ps", "-p", str(pid), "-o", "ppid=", "-o", "pgid="),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=3,
        check=False,
    )
    fields = completed.stdout.split()
    if completed.returncode != 0 or len(fields) != 2:
        raise DriverError("owned process relationship is unavailable")
    try:
        parent_pid, process_group_id = (int(item) for item in fields)
    except ValueError as exc:
        raise DriverError("owned process relationship is invalid") from exc
    if parent_pid < 1 or process_group_id < 2:
        raise DriverError("owned process relationship is invalid")
    return {"parent_pid": parent_pid, "process_group_id": process_group_id}


def _terminal_foreground_process_group(pid: int) -> int:
    try:
        completed = subprocess.run(
            ("/bin/ps", "-p", str(pid), "-o", "tpgid="),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DriverError("terminal foreground process group is unavailable") from exc
    fields = completed.stdout.split()
    if completed.returncode != 0 or len(fields) != 1:
        raise DriverError("terminal foreground process group is unavailable")
    try:
        process_group_id = int(fields[0])
    except ValueError as exc:
        raise DriverError("terminal foreground process group is invalid") from exc
    if process_group_id < 2:
        raise DriverError("terminal foreground process group is invalid")
    return process_group_id


def _validate_owned_foreground_job(job_pid: int, state: Mapping[str, Any]) -> None:
    """Require the live iTerm foreground job to belong to the bound process chain."""

    root_pid = state.get("pid")
    owned_pgid = state.get("pgid")
    if (
        not isinstance(root_pid, int)
        or isinstance(root_pid, bool)
        or not isinstance(owned_pgid, int)
        or isinstance(owned_pgid, bool)
    ):
        raise DriverError("owned process chain is invalid")
    current_pid = job_pid
    seen: set[int] = set()
    for _ in range(64):
        if current_pid < 2 or current_pid in seen:
            break
        seen.add(current_pid)
        try:
            relationship = _process_relationship(current_pid)
        except DriverError as exc:
            if str(exc) != "owned process relationship is unavailable":
                raise
            try:
                os.kill(current_pid, 0)
            except ProcessLookupError:
                pass
            except OSError:
                raise exc
            else:
                raise exc
            root = _process_observation(root_pid)
            if (
                root["identity_sha256"] != state.get("process_identity_sha256")
                or root["pgid"] != owned_pgid
                or _terminal_foreground_process_group(root_pid) != owned_pgid
            ):
                raise DriverError("owned foreground process group drifted") from exc
            return
        if relationship["process_group_id"] != owned_pgid:
            raise DriverError("owned foreground process group drifted")
        if current_pid == root_pid:
            return
        current_pid = relationship["parent_pid"]
    raise DriverError("iTerm foreground job is not an owned descendant")


def _validate_owned_descendant_process(
    descendant_pid: int, state: Mapping[str, Any]
) -> None:
    """Require a live process to descend from the exact bound runtime root.

    Agent tool executors may create their own process groups.  Sender identity
    therefore follows the kernel parent chain, while interactive foreground,
    delivery, status, and close checks retain their stricter same-PGID fence.
    """

    root_pid = state.get("pid")
    if not isinstance(root_pid, int) or isinstance(root_pid, bool):
        raise DriverError("owned process chain is invalid")
    current_pid = descendant_pid
    seen: set[int] = set()
    for _ in range(64):
        if current_pid < 2 or current_pid in seen:
            break
        seen.add(current_pid)
        relationship = _process_relationship(current_pid)
        if current_pid == root_pid:
            return
        current_pid = relationship["parent_pid"]
    raise DriverError("participant sender process is not an owned descendant")


def _boot_id_sha256() -> str:
    """Return a portable redacted boot identity, never a raw platform value."""

    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    raw: str | None = None
    if boot_id_path.is_file():
        try:
            raw = boot_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            raw = None
    elif sys.platform == "darwin":
        completed = subprocess.run(
            ("/usr/sbin/sysctl", "-n", "kern.boottime"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode == 0:
            raw = completed.stdout.strip()
    if not raw:
        completed = subprocess.run(
            ("/bin/ps", "-p", "1", "-o", "lstart="),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode == 0:
            raw = completed.stdout.strip()
    if not raw:
        raise DriverError("boot identity is unavailable")
    return digest({"platform": sys.platform, "boot_identity": raw})


def _wait_process_absent(pid: int) -> bool:
    deadline = time.monotonic() + PROCESS_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def _terminate_exact(state: Mapping[str, Any]) -> None:
    pid = state.get("pid")
    if not isinstance(pid, int):
        raise DriverError("private process identity is invalid")
    try:
        observed = _process_observation(pid)
    except DriverError:
        return
    if observed["identity_sha256"] != state.get("process_identity_sha256"):
        raise DriverError("owned process identity drifted")
    pgid = state.get("pgid")
    if not isinstance(pgid, int) or observed["pgid"] != pgid:
        raise DriverError("owned process group drifted")
    os.killpg(pgid, signal.SIGTERM)
    if not _wait_process_absent(pid):
        observed = _process_observation(pid)
        if observed["identity_sha256"] != state.get("process_identity_sha256"):
            raise DriverError("owned process changed during stop")
        os.killpg(pgid, signal.SIGKILL)
        if not _wait_process_absent(pid):
            raise DriverError("owned process remained after stop")


def _terminate_gracefully_exact(state: Mapping[str, Any]) -> None:
    """Send only SIGTERM to the exact owned process group.

    A normal lifecycle stop must never silently cross the force-stop boundary.
    If the process remains, the caller retains the binding and reports cleanup
    pending so a separately confirmed force-stop can be requested.
    """

    pid = state.get("pid")
    if not isinstance(pid, int):
        raise DriverError("private process identity is invalid")
    try:
        observed = _process_observation(pid)
    except DriverError:
        return
    if observed["identity_sha256"] != state.get("process_identity_sha256"):
        raise DriverError("owned process identity drifted")
    pgid = state.get("pgid")
    if not isinstance(pgid, int) or observed["pgid"] != pgid:
        raise DriverError("owned process group drifted")
    os.killpg(pgid, signal.SIGTERM)
    if not _wait_process_absent(pid):
        observed = _process_observation(pid)
        if observed["identity_sha256"] != state.get("process_identity_sha256"):
            raise DriverError("owned process changed during graceful stop")
        raise DriverError("owned process remained after graceful stop")


def _headless_start(
    private_root: Path,
    workspace_path: Path,
    context: Mapping[str, Any],
    launch_spec: Mapping[str, Any],
    identifiers: Mapping[str, str],
    participant_client: Mapping[str, Any],
) -> dict[str, Any]:
    argv = _runtime_argv(launch_spec)
    process = subprocess.Popen(
        argv,
        cwd=workspace_path,
        env=_runtime_environment(launch_spec, participant_client, private_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    observation = _process_observation(process.pid)
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "status": "ready",
        "interaction_mode": "headless",
        "started_at_unix_ms": int(time.time() * 1000),
        "scenario_id": context["scenario_id"],
        "participant_id": context["participant_id"],
        "participant_generation": context["participant_generation"],
        "owner_token": identifiers["owner_token"],
        "pid": process.pid,
        "pgid": observation["pgid"],
        "process_identity_sha256": observation["identity_sha256"],
        "boot_id_sha256": _boot_id_sha256(),
        "supervision_fencing_token": secrets.token_hex(32),
        "supervision_heartbeat_sequence": 0,
        "runtime_binding_id": identifiers["runtime_binding_id"],
        "presentation_instance_id": None,
        "window_id": None,
        "session_id": None,
        "owner_marker": None,
        "runtime_profile_ref": launch_spec["runtime_profile_ref"],
        "accepts_typed_delivery": _runtime_profile(launch_spec)[
            "accepts_typed_delivery"
        ],
    }
    _write_private(_state_path(private_root), state)
    return _artifacts(
        context=context,
        launch_spec=launch_spec,
        identifiers=identifiers,
        process_observation=observation,
        presentation=None,
        vendor_session_identity_sha256=None,
    )


def _load_lock() -> tuple[list[dict[str, Any]], str]:
    try:
        value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DriverError("iTerm dependency lock is unavailable") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("adapter") != "iterm2_official_python_api"
        or not isinstance(value.get("artifacts"), list)
    ):
        raise DriverError("iTerm dependency lock is invalid")
    return value["artifacts"], digest(value)


def _iterm_runtime_cache_tag() -> str:
    tag = getattr(sys.implementation, "cache_tag", None)
    if not isinstance(tag, str) or re.fullmatch(r"[a-z0-9_-]{1,40}", tag) is None:
        raise DriverError("Python runtime cache tag is unavailable")
    return tag


def _iterm_install_root(private_root: Path, lock_digest: str) -> Path:
    return private_root / (
        f"iterm-python-{lock_digest[:16]}-{_iterm_runtime_cache_tag()}"
    )


def _file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _ensure_iterm_module(private_root: Path) -> Any:
    artifacts, lock_digest = _load_lock()
    runtime_cache_tag = _iterm_runtime_cache_tag()
    install_root = _iterm_install_root(private_root, lock_digest)
    environment = install_root / "venv"
    ready = install_root / "ready.json"
    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = environment / "lib" / python_version / "site-packages"
    expected_ready = {
        "schema_version": 1,
        "lock_digest": lock_digest,
        "runtime_cache_tag": runtime_cache_tag,
        "runtime_platform": sysconfig.get_platform(),
    }
    try:
        ready_details = ready.stat() if not ready.is_symlink() else None
        ready_value = (
            json.loads(ready.read_text(encoding="utf-8"))
            if ready_details is not None
            and ready.is_file()
            and ready_details.st_uid == os.getuid()
            and stat.S_IMODE(ready_details.st_mode) == 0o600
            else None
        )
    except (OSError, json.JSONDecodeError):
        ready_value = None
    if ready_value != expected_ready or not site_packages.is_dir():
        install_root.mkdir(mode=0o700, exist_ok=True)
        downloads = install_root / "downloads"
        downloads.mkdir(mode=0o700, exist_ok=True)
        wheel_paths: list[Path] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise DriverError("iTerm dependency lock is invalid")
            filename = artifact.get("filename")
            url = artifact.get("url")
            expected = artifact.get("sha256")
            if not all(isinstance(item, str) and item for item in (filename, url, expected)):
                raise DriverError("iTerm dependency lock is invalid")
            wheel = downloads / filename
            if not wheel.exists() or _file_sha256(wheel) != expected:
                temporary = downloads / f".{filename}.{secrets.token_hex(5)}.tmp"
                try:
                    urllib.request.urlretrieve(url, temporary)
                    if _file_sha256(temporary) != expected:
                        raise DriverError("iTerm dependency digest differs")
                    os.replace(temporary, wheel)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            wheel_paths.append(wheel)
        venv.EnvBuilder(with_pip=True, clear=True, symlinks=True).create(environment)
        python = environment / "bin" / "python"
        completed = subprocess.run(
            (
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--no-deps",
                *[str(path) for path in wheel_paths],
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise DriverError("iTerm dependency installation failed")
        _write_private(ready, expected_ready)
    if not site_packages.is_dir():
        raise DriverError("iTerm dependency environment is unavailable")
    sys.path.insert(0, str(site_packages))
    try:
        import iterm2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DriverError("iTerm Python API is unavailable") from exc
    return iterm2


def _topology() -> tuple[dict[str, Any], str]:
    completed = subprocess.run(
        ("/usr/bin/xcrun", "swift", str(TOPOLOGY_HELPER)),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise DriverError("display topology is unavailable")
    try:
        raw = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError("display topology is invalid") from exc
    displays = raw.get("displays") if isinstance(raw, dict) else None
    if not isinstance(displays, list) or not displays:
        raise DriverError("display topology is invalid")
    primary = [item for item in displays if item.get("is_primary") is True]
    if len(primary) != 1:
        raise DriverError("display topology is ambiguous")
    origin = primary[0]["frame"]
    normalized: list[dict[str, Any]] = []
    for item in displays:
        value: dict[str, Any] = {"is_primary": item.get("is_primary") is True}
        for field in ("frame", "visible_frame"):
            frame = item.get(field)
            if (
                not isinstance(frame, dict)
                or not all(isinstance(frame.get(key), int) for key in ("x", "y", "width", "height"))
                or frame["width"] < 1
                or frame["height"] < 1
            ):
                raise DriverError("display topology is invalid")
            value[field] = {
                "x": frame["x"] - origin["x"],
                "y": frame["y"] - origin["y"],
                "width": frame["width"],
                "height": frame["height"],
            }
        normalized.append(value)
    normalized.sort(key=lambda item: canonical_json_bytes(item))
    return raw, hashlib.sha256(
        b"ai-collab-display-topology-v1\0" + canonical_json_bytes(normalized)
    ).hexdigest()


def _topology_identity(window: Any) -> tuple[str, str, Any]:
    window_id = getattr(window, "window_id", None)
    tabs = getattr(window, "tabs", None)
    if not isinstance(window_id, str) or not window_id or not isinstance(tabs, list) or len(tabs) != 1:
        raise DriverError("iTerm window topology differs")
    tab = tabs[0]
    sessions = getattr(tab, "sessions", None)
    all_sessions = getattr(tab, "all_sessions", sessions)
    if (
        not isinstance(sessions, list)
        or len(sessions) != 1
        or not isinstance(all_sessions, list)
        or len(all_sessions) != 1
    ):
        raise DriverError("iTerm session topology differs")
    session = sessions[0]
    session_id = getattr(session, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        raise DriverError("iTerm session identity is invalid")
    return window_id, session_id, session


async def _read_window_geometry(window: Any) -> dict[str, int]:
    frame = await _bounded(window.async_get_frame())
    values = (
        frame.origin.x,
        frame.origin.y,
        frame.size.width,
        frame.size.height,
    )
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value).is_integer() is False
        for value in values
    ):
        raise DriverError("iTerm window geometry is non-integral")
    geometry = {
        "x": int(values[0]),
        "y": int(values[1]),
        "width": int(values[2]),
        "height": int(values[3]),
    }
    if geometry["width"] < 1 or geometry["height"] < 1:
        raise DriverError("iTerm window geometry is invalid")
    return geometry


async def _restore_window_geometry(
    module: Any, window: Any, geometry: Mapping[str, Any]
) -> None:
    if (
        set(geometry) != {"x", "y", "width", "height"}
        or any(
            not isinstance(geometry[key], int)
            or isinstance(geometry[key], bool)
            for key in geometry
        )
        or geometry["width"] < 1
        or geometry["height"] < 1
    ):
        raise DriverError("stored iTerm geometry is invalid")
    frame = module.util.Frame(
        module.util.Point(geometry["x"], geometry["y"]),
        module.util.Size(geometry["width"], geometry["height"]),
    )
    await _bounded(window.async_set_frame(frame))


async def _bounded(awaitable: Any) -> Any:
    return await asyncio.wait_for(awaitable, timeout=OPERATION_TIMEOUT_SECONDS)


async def _close_iterm_connection(connection: Any | None) -> None:
    """Release one short-lived iTerm API connection before its loop exits.

    The official client starts a background dispatch task but exposes no public
    connection close helper.  Letting ``asyncio.run`` tear the loop down leaves
    iTerm's Unix-socket peer open long enough for repeated Harness operations to
    exhaust the server.  Cancel the dispatcher first so it cannot race the
    WebSocket close handshake, then close the socket explicitly.
    """

    if connection is None:
        return
    dispatcher = getattr(
        connection, "_Connection__dispatch_forever_future", None
    )
    if dispatcher is not None and not dispatcher.done():
        dispatcher.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await dispatcher
    websocket = getattr(connection, "websocket", None)
    if websocket is not None:
        with suppress(asyncio.CancelledError, Exception):
            await _bounded(websocket.close())


async def _connect_iterm_application(module: Any) -> tuple[Any, Any]:
    """Reconnect only before launch has crossed the external-effect boundary."""

    for attempt in range(SENDER_SESSION_CONNECT_ATTEMPTS):
        connection = None
        try:
            connection = await _bounded(module.Connection.async_create())
            app = await _bounded(module.async_get_app(connection))
            if app is None:
                raise DriverError("iTerm application state is unavailable")
            return connection, app
        except Exception as exc:
            await _close_iterm_connection(connection)
            if (
                not _retryable_iterm_connection_error(exc)
                or attempt + 1 == SENDER_SESSION_CONNECT_ATTEMPTS
            ):
                raise
            await asyncio.sleep(SENDER_SESSION_RETRY_SECONDS)
    raise AssertionError("iTerm connection attempts exhausted")  # pragma: no cover


def _startup_process_wait_seconds(_profile: Mapping[str, Any]) -> float:
    return PROCESS_WAIT_SECONDS


async def _wait_job_pid(
    session: Any,
    process_match: str,
    *,
    wait_seconds: float = PROCESS_WAIT_SECONDS,
) -> int:
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        value = await _bounded(session.async_get_variable("jobPid"))
        try:
            pid = int(value)
            observation = _process_observation(pid)
        except (TypeError, ValueError, DriverError):
            pid = 0
        else:
            if process_match in observation["ps"]:
                stable = _stable_job_observation(observation, process_match)
                return int(stable["pid"])
        if asyncio.get_running_loop().time() >= deadline:
            raise DriverError("iTerm runtime process did not become ready")
        await asyncio.sleep(0.05)


def _screen_text(contents: Any) -> str:
    count = getattr(contents, "number_of_lines", None)
    if not isinstance(count, int) or count < 0:
        raise DriverError("iTerm screen contents are invalid")
    values: list[str] = []
    for index in range(count):
        line = contents.line(index)
        value = getattr(line, "string", None)
        if not isinstance(value, str):
            raise DriverError("iTerm screen line is invalid")
        values.append(value)
    return "\n".join(values)


def _process_cwd(pid: int) -> Path:
    completed = subprocess.run(
        ("/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=3,
        check=False,
    )
    values = [line[1:] for line in completed.stdout.splitlines() if line.startswith("n")]
    if completed.returncode != 0 or len(values) != 1:
        raise DriverError("runtime working directory observation is unavailable")
    try:
        return Path(values[0]).resolve(strict=True)
    except OSError as exc:
        raise DriverError("runtime working directory observation is invalid") from exc


async def _wait_startup_ready(
    session: Any,
    profile: Mapping[str, Any],
    workspace_path: Path,
    process_pid: int,
) -> dict[str, Any]:
    gate = profile["startup_gate"]
    if gate is None:
        return {
            "scope": None,
            "outcome": "not_declared",
            "workspace_identity_sha256": None,
            "ready_screen_sha256": None,
        }
    prompt_rules = [
        (rule, re.compile(rule["prompt_pattern"]))
        for rule in _startup_prompt_rules(gate)
    ]
    ready_pattern = re.compile(gate["ready_pattern"])
    deadline = asyncio.get_running_loop().time() + gate["timeout_seconds"]
    handled_rule_ids: set[str] = set()
    stable_digest: str | None = None
    stable_count = 0
    while True:
        screen = _screen_text(
            await _bounded(session.async_get_screen_contents())
        ).replace("\x00", " ")
        matched_rules = [
            rule for rule, pattern in prompt_rules if pattern.search(screen) is not None
        ]
        unhandled_rules = [
            rule
            for rule in matched_rules
            if rule["rule_id"] not in handled_rule_ids
        ]
        if len(unhandled_rules) > 1:
            raise DriverError("runtime TUI startup prompt needs attention")
        if matched_rules:
            rule = unhandled_rules[0] if unhandled_rules else matched_rules[0]
            if rule["rule_id"] not in handled_rule_ids:
                if (
                    rule["requires_workspace_path"]
                    and _process_cwd(process_pid)
                    != workspace_path.resolve(strict=True)
                ):
                    raise DriverError("startup trust gate workspace differs")
                for value in rule["confirm_sequence"]:
                    await _bounded(
                        session.async_send_text(value, suppress_broadcast=True)
                    )
                handled_rule_ids.add(rule["rule_id"])
            stable_digest = None
            stable_count = 0
        elif ready_pattern.search(screen) is not None:
            screen_digest = hashlib.sha256(screen.encode("utf-8")).hexdigest()
            if screen_digest == stable_digest:
                stable_count += 1
            else:
                stable_digest = screen_digest
                stable_count = 1
            if stable_count >= STARTUP_STABLE_OBSERVATIONS:
                return {
                    "scope": gate["scope"],
                    "outcome": (
                        "accepted" if handled_rule_ids else "already_satisfied"
                    ),
                    "workspace_identity_sha256": digest(
                        {"workspace_path": str(workspace_path.resolve(strict=True))}
                    ),
                    "ready_screen_sha256": screen_digest,
                }
        else:
            stable_digest = None
            stable_count = 0
        if asyncio.get_running_loop().time() >= deadline:
            raise DriverError("runtime TUI did not become input-ready")
        await asyncio.sleep(STARTUP_POLL_SECONDS)


async def _iterm_start_async(
    module: Any,
    private_root: Path,
    workspace_path: Path,
    context: Mapping[str, Any],
    launch_spec: Mapping[str, Any],
    identifiers: Mapping[str, str],
    participant_client: Mapping[str, Any],
) -> dict[str, Any]:
    stage = "iterm-connect"
    launcher = private_root / "runtime-launcher.zsh"
    connection: Any | None = None
    window: Any | None = None
    marker_verified = False
    marker: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    pid: int | None = None
    try:
        connection, app = await _connect_iterm_application(module)
        before_ids = {
            item.window_id
            for item in app.windows
            if isinstance(item.window_id, str)
        }
        stage = "display-topology"
        _, topology_fingerprint = _topology()
        stage = "launch-material"
        profile = _runtime_profile(launch_spec)
        (
            runtime_argv,
            vendor_provider,
            expected_vendor_session_id,
            resume_requested,
        ) = _prepare_runtime_launch(private_root, launch_spec, participant_client)
        command = shlex.join(runtime_argv)
        environment = _runtime_environment(
            launch_spec, participant_client, private_root
        )
        launcher.write_text(
            "#!/bin/zsh -f\nset -eu\numask 077\n"
            + "".join(
                f"export {key}={shlex.quote(value)}\n"
                for key, value in environment.items()
            )
            + f"cd -- {shlex.quote(str(workspace_path))}\n"
            + f"exec {command}\n",
            encoding="utf-8",
        )
        os.chmod(launcher, 0o700)
        stage = "window-create"
        window = await _bounded(
            module.Window.async_create(
                connection,
                command=f"/bin/zsh -f {shlex.quote(str(launcher))}",
            )
        )
        if window is None:
            raise DriverError("iTerm top-level window was not created")
        window_id, session_id, session = _topology_identity(window)
        if window_id in before_ids:
            raise DriverError("iTerm create returned an ambiguous window identity")
        stage = "owner-marker"
        marker = {
            "schema_version": 1,
            "owner_token": identifiers["owner_token"],
            "scenario_id": context["scenario_id"],
            "participant_id": context["participant_id"],
            "participant_generation": context["participant_generation"],
        }
        await _bounded(window.async_set_variable(OWNER_VARIABLE, marker))
        if await _bounded(window.async_get_variable(OWNER_VARIABLE)) != marker:
            raise DriverError("iTerm owner marker round trip differed")
        marker_verified = True
        process_wait_seconds = _startup_process_wait_seconds(profile)
        stage = "initial-process"
        pid = await _wait_job_pid(
            session,
            profile["process_match"],
            wait_seconds=process_wait_seconds,
        )
        stage = "startup-gate"
        startup_gate_evidence = await _wait_startup_ready(
            session, profile, workspace_path, pid
        )
        stage = "vendor-session-proof"
        vendor_session_identity_sha256 = _verify_vendor_session(
            private_root,
            launch_spec,
            vendor_provider,
            expected_vendor_session_id,
            resume_requested,
        )
        # Script-based vendor launchers may replace the shell-visible job with a
        # native child while their TUI initializes.  Bind only the input-ready
        # terminal job; the earlier PID proves launch progress, not final identity.
        stage = "final-process"
        pid = await _wait_job_pid(
            session,
            profile["process_match"],
            wait_seconds=process_wait_seconds,
        )
        observation = _process_observation(pid)
        stage = "window-geometry"
        geometry = await _read_window_geometry(window)
        window_hash = digest({"window_id": window_id, "marker": marker})
        session_hash = digest({"session_id": session_id, "marker": marker})
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "ready",
            "interaction_mode": "tui",
            "started_at_unix_ms": int(time.time() * 1000),
            "scenario_id": context["scenario_id"],
            "participant_id": context["participant_id"],
            "participant_generation": context["participant_generation"],
            "owner_token": identifiers["owner_token"],
            "pid": pid,
            "pgid": observation["pgid"],
            "process_identity_sha256": observation["identity_sha256"],
            "boot_id_sha256": _boot_id_sha256(),
            "supervision_fencing_token": secrets.token_hex(32),
            "supervision_heartbeat_sequence": 0,
            "runtime_binding_id": identifiers["runtime_binding_id"],
            "presentation_instance_id": identifiers["presentation_instance_id"],
            "window_id": window_id,
            "session_id": session_id,
            "owner_marker": marker,
            "window_identity_sha256": window_hash,
            "session_identity_sha256": session_hash,
            "display_topology_fingerprint": topology_fingerprint,
            "geometry": geometry,
            "geometry_by_topology": {topology_fingerprint: geometry},
            "runtime_profile_ref": launch_spec["runtime_profile_ref"],
            "accepts_typed_delivery": profile["accepts_typed_delivery"],
            "startup_gate_evidence": startup_gate_evidence,
            "vendor_session_identity_sha256": vendor_session_identity_sha256,
            "vendor_provider": vendor_provider,
            "expected_vendor_session_id": expected_vendor_session_id,
            "vendor_resume_requested": resume_requested,
        }
        _write_private(_state_path(private_root), state)
        stage = "activation"
        await _bounded(app.async_activate())
        await _bounded(window.async_activate())
        presentation = {
            "presentation_instance_id": identifiers["presentation_instance_id"],
            "window_identity_sha256": window_hash,
            "session_identity_sha256": session_hash,
            "geometry": geometry,
            "display_topology_fingerprint": topology_fingerprint,
        }
        return _artifacts(
            context=context,
            launch_spec=launch_spec,
            identifiers=identifiers,
            process_observation=observation,
            presentation=presentation,
            vendor_session_identity_sha256=vendor_session_identity_sha256,
        )
    except BaseException as exc:
        cleanup_outcome = (
            "unconfirmed" if stage == "window-create" else "not-required"
        )
        failure_process_observation = None
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1:
            try:
                failure_process_observation = _process_observation(pid)
            except DriverError:
                failure_process_observation = None
        if window is not None:
            cleanup_outcome = "unconfirmed"
            try:
                if (
                    marker_verified
                    and marker is not None
                    and await _bounded(window.async_get_variable(OWNER_VARIABLE))
                    != marker
                ):
                    raise DriverError("iTerm cleanup owner marker differed")
                await _bounded(window.async_close(force=True))
                cleanup_outcome = "close-requested"
            except Exception:
                pass
        if state is not None:
            try:
                _terminate_exact(state)
            except DriverError:
                pass
        _record_launch_failure(
            private_root,
            stage=stage,
            exc=exc,
            cleanup_outcome=cleanup_outcome,
            process_observation=failure_process_observation,
        )
        raise
    finally:
        await _close_iterm_connection(connection)
        launcher.unlink(missing_ok=True)


def _artifacts(
    *,
    context: Mapping[str, Any],
    launch_spec: Mapping[str, Any],
    identifiers: Mapping[str, str],
    process_observation: Mapping[str, Any],
    presentation: Mapping[str, Any] | None,
    vendor_session_identity_sha256: str | None,
) -> dict[str, Any]:
    runtime_create_request = {"context": context, "launch_spec": launch_spec}
    prepared = {
        "context": context,
        "driver_id": launch_spec["driver_id"],
        "runtime_instance_id": identifiers["runtime_instance_id"],
        "private_launch_handle_ref": identifiers["private_launch_handle_ref"],
    }
    runtime_ack = {
        "context": context,
        "binding": {
            "scenario_id": context["scenario_id"],
            "participant_id": context["participant_id"],
            "participant_generation": context["participant_generation"],
            "driver_id": launch_spec["driver_id"],
            "runtime_instance_id": identifiers["runtime_instance_id"],
            "runtime_binding_id": identifiers["runtime_binding_id"],
            "process_instance_id": identifiers["process_instance_id"],
            "process_identity_sha256": process_observation["identity_sha256"],
            "continuity_mode": launch_spec["continuity_mode"],
            "vendor_session_identity_sha256": vendor_session_identity_sha256,
            "private_driver_binding_ref": identifiers[
                "private_runtime_binding_ref"
            ],
            "capability_snapshot_digest": context[
                "capability_snapshot_digest"
            ],
        },
        "ready": True,
    }
    if presentation is None:
        presentation_request = None
        presentation_ack = None
    else:
        presentation_request = {
            "context": context,
            "presentation_driver_id": "presentation.iterm2",
            "runtime_binding_id": identifiers["runtime_binding_id"],
            "restore_geometry": None,
            "display_topology_fingerprint": presentation[
                "display_topology_fingerprint"
            ],
        }
        presentation_ack = {
            "context": context,
            "binding": {
                "scenario_id": context["scenario_id"],
                "participant_id": context["participant_id"],
                "participant_generation": context["participant_generation"],
                "driver_id": "presentation.iterm2",
                "presentation_instance_id": presentation[
                    "presentation_instance_id"
                ],
                "runtime_binding_id": identifiers["runtime_binding_id"],
                "window_identity_sha256": presentation[
                    "window_identity_sha256"
                ],
                "session_identity_sha256": presentation[
                    "session_identity_sha256"
                ],
                "private_driver_binding_ref": identifiers[
                    "private_presentation_binding_ref"
                ],
                "geometry": presentation["geometry"],
                "display_topology_fingerprint": presentation[
                    "display_topology_fingerprint"
                ],
                "capability_snapshot_digest": context[
                    "capability_snapshot_digest"
                ],
            },
            "geometry_restore_outcome": "not_requested",
            "created": True,
        }
    return {
        "runtime_create_request": runtime_create_request,
        "prepared_runtime_launch": prepared,
        "runtime_ready_ack": runtime_ack,
        "presentation_create_request": presentation_request,
        "presentation_create_ack": presentation_ack,
    }


def start(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "context",
        "launch_spec",
        "resolved_driver",
        "private_root",
        "workspace_path",
        "participant_client",
    }
    if not required <= set(payload) or set(payload) - required - {
        "participant_working_directory"
    }:
        raise DriverError("start payload differs")
    private_root = _private_root(payload["private_root"])
    context = payload["context"]
    launch_spec = payload["launch_spec"]
    workspace_path = _workspace_path(
        payload["workspace_path"],
        _runtime_profile(launch_spec),
        payload.get("participant_working_directory"),
    )
    resolved = payload["resolved_driver"]
    participant_client = payload["participant_client"]
    if (
        not isinstance(context, dict)
        or not isinstance(launch_spec, dict)
        or not isinstance(resolved, dict)
        or resolved.get("driver_registry_digest") != context.get("driver_registry_digest")
        or resolved.get("capability_snapshot_digest")
        != context.get("capability_snapshot_digest")
    ):
        raise DriverError("start binding differs")
    token = secrets.token_hex(16)
    identifiers = {
        "owner_token": secrets.token_hex(32),
        "runtime_instance_id": f"runtime-{token}",
        "runtime_binding_id": f"runtime-binding-{token}",
        "process_instance_id": f"process-{token}",
        "presentation_instance_id": f"presentation-{token}",
        "private_launch_handle_ref": f"private-launch-{token}",
        "private_runtime_binding_ref": f"private-runtime-{token}",
        "private_presentation_binding_ref": f"private-presentation-{token}",
    }
    if launch_spec.get("interaction_mode") == "headless":
        return _headless_start(
            private_root,
            workspace_path,
            context,
            launch_spec,
            identifiers,
            participant_client,
        )
    try:
        _require_iterm_private_api_ready()
    except BaseException as exc:
        _record_launch_failure(
            private_root,
            stage="iterm-preflight",
            exc=exc,
            cleanup_outcome="not-required",
        )
        raise
    try:
        module = _ensure_iterm_module(private_root)
    except BaseException as exc:
        _record_launch_failure(
            private_root,
            stage="iterm-dependency",
            exc=exc,
            cleanup_outcome="not-required",
        )
        raise
    return asyncio.run(
        _iterm_start_async(
            module,
            private_root,
            workspace_path,
            context,
            launch_spec,
            identifiers,
            participant_client,
        )
    )


def authorize_sender(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {
        "peer_pid",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }:
        raise DriverError("participant sender payload differs")
    peer_pid = payload["peer_pid"]
    if not isinstance(peer_pid, int) or isinstance(peer_pid, bool) or peer_pid < 2:
        raise DriverError("participant sender process is invalid")
    private_root = _private_root(payload["private_root"])
    state = _read_private(_state_path(private_root))
    runtime_ack = payload["runtime_ready_ack"]
    presentation_ack = payload["presentation_create_ack"]
    if (
        state.get("status") != "ready"
        or not isinstance(runtime_ack, dict)
        or runtime_ack.get("binding", {}).get("runtime_binding_id")
        != state.get("runtime_binding_id")
        or (
            presentation_ack is None
            and state.get("presentation_instance_id") is not None
        )
        or (
            presentation_ack is not None
            and presentation_ack.get("binding", {}).get(
                "presentation_instance_id"
            )
            != state.get("presentation_instance_id")
        )
    ):
        exc = DriverError("participant sender binding differs")
        _record_sender_auth_failure(private_root, stage="binding", exc=exc)
        raise exc
    try:
        _validate_process_state(state)
    except Exception as exc:
        _record_sender_auth_failure(private_root, stage="root-process", exc=exc)
        raise
    try:
        _validate_owned_descendant_process(peer_pid, state)
    except Exception as exc:
        _record_sender_auth_failure(private_root, stage="peer-process", exc=exc)
        raise
    if state.get("interaction_mode") == "tui":
        module = _ensure_iterm_module(private_root)
        try:
            asyncio.run(_authorize_sender_exact_session(module, state))
        except Exception as exc:
            _record_sender_auth_failure(private_root, stage="exact-session", exc=exc)
            raise
    peer = _process_observation(peer_pid)
    evidence = {
        "sender": {
            "scenario_id": state["scenario_id"],
            "participant_id": state["participant_id"],
            "participant_generation": state["participant_generation"],
        },
        "runtime_binding_id": state["runtime_binding_id"],
        "root_process_identity_sha256": state["process_identity_sha256"],
        "peer_process_identity_sha256": peer["identity_sha256"],
        "same_process_group": peer["pgid"] == state["pgid"],
    }
    return {
        "authorized": True,
        "sender": evidence["sender"],
        "runtime_binding_id": state["runtime_binding_id"],
        "process_chain_evidence_sha256": digest(evidence),
    }


def _validate_process_state(state: Mapping[str, Any]) -> None:
    observation = _process_observation(state.get("pid"))
    if (
        observation["identity_sha256"] != state.get("process_identity_sha256")
        or observation["pgid"] != state.get("pgid")
    ):
        raise DriverError("owned process binding drifted")


async def _iterm_status_async(module: Any, state: Mapping[str, Any]) -> None:
    connection = await _bounded(module.Connection.async_create())
    try:
        app = await _bounded(module.async_get_app(connection))
        window = (
            app.get_window_by_id(state.get("window_id"))
            if app is not None
            else None
        )
        if window is None:
            raise DriverError("owned iTerm window is absent")
        window_id, session_id, session = _topology_identity(window)
        if window_id != state.get("window_id") or session_id != state.get(
            "session_id"
        ):
            raise DriverError("owned iTerm topology drifted")
        if await _bounded(window.async_get_variable(OWNER_VARIABLE)) != state.get(
            "owner_marker"
        ):
            raise DriverError("owned iTerm marker drifted")
        try:
            job_pid = int(await _bounded(session.async_get_variable("jobPid")))
        except (TypeError, ValueError) as exc:
            raise DriverError("owned iTerm job identity is invalid") from exc
        _validate_owned_foreground_job(job_pid, state)
    finally:
        await _close_iterm_connection(connection)


def status(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }:
        raise DriverError("status payload differs")
    private_root = _private_root(payload["private_root"])
    state = _read_private(_state_path(private_root))
    if state.get("status") != "ready":
        raise DriverError("participant is not ready")
    _validate_process_state(state)
    if state.get("interaction_mode") == "tui":
        module = _ensure_iterm_module(private_root)
        asyncio.run(_iterm_status_async(module, state))
    _refresh_vendor_session_binding(private_root, payload["launch_spec"], state)
    return {
        "healthy": True,
        "runtime_binding_id": state["runtime_binding_id"],
        "presentation_binding_id": state["presentation_instance_id"],
    }


async def _presentation_action_async(
    module: Any,
    private_root: Path,
    state: dict[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    connection = await _bounded(module.Connection.async_create())
    try:
        return await _presentation_action_connected(
            module,
            connection,
            private_root,
            state,
            action=action,
        )
    finally:
        await _close_iterm_connection(connection)


async def _presentation_action_connected(
    module: Any,
    connection: Any,
    private_root: Path,
    state: dict[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    app = await _bounded(module.async_get_app(connection))
    window = app.get_window_by_id(state.get("window_id")) if app is not None else None
    if window is None:
        raise DriverError("owned iTerm window is absent")
    window_id, session_id, session = _topology_identity(window)
    if window_id != state.get("window_id") or session_id != state.get("session_id"):
        raise DriverError("owned iTerm topology drifted")
    if await _bounded(window.async_get_variable(OWNER_VARIABLE)) != state.get(
        "owner_marker"
    ):
        raise DriverError("owned iTerm marker drifted")
    try:
        job_pid = int(await _bounded(session.async_get_variable("jobPid")))
    except (TypeError, ValueError) as exc:
        raise DriverError("owned iTerm job identity is invalid") from exc
    _validate_owned_foreground_job(job_pid, state)

    _, topology_fingerprint = _topology()
    stored = state.get("geometry_by_topology")
    if stored is None:
        stored = {
            state["display_topology_fingerprint"]: state["geometry"],
        }
    if not isinstance(stored, dict):
        raise DriverError("stored iTerm topology geometry is invalid")

    restore_outcome = "not_requested"
    if action == "focus":
        restore_geometry = stored.get(topology_fingerprint)
        if restore_geometry is None:
            restore_outcome = "not_available"
        else:
            await _restore_window_geometry(module, window, restore_geometry)
            restore_outcome = "applied_exact"
        await _bounded(app.async_activate())
        await _bounded(window.async_activate())

    geometry = await _read_window_geometry(window)
    if restore_outcome == "applied_exact" and geometry != restore_geometry:
        restore_outcome = "applied_adjusted"
    if action == "focus":
        stored[topology_fingerprint] = geometry
        state["geometry_by_topology"] = stored
        state["display_topology_fingerprint"] = topology_fingerprint
        state["geometry"] = geometry
        _write_private(_state_path(private_root), state)
    return {
        "presentation": {
            "participant_generation": state["participant_generation"],
            "presentation_instance_id": state["presentation_instance_id"],
            "health": "ready",
            "focused": action == "focus",
            "restore_outcome": restore_outcome,
            "geometry": geometry,
            "display_topology_fingerprint": topology_fingerprint,
        }
    }


def presentation_action(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
        "action",
    }:
        raise DriverError("presentation action payload differs")
    if payload["action"] not in {"inspect", "focus"}:
        raise DriverError("presentation action differs")
    private_root = _private_root(payload["private_root"])
    state = _read_private(_state_path(private_root))
    runtime_ack = payload["runtime_ready_ack"]
    presentation_ack = payload["presentation_create_ack"]
    if (
        state.get("status") != "ready"
        or state.get("interaction_mode") != "tui"
        or not isinstance(runtime_ack, dict)
        or runtime_ack.get("binding", {}).get("runtime_binding_id")
        != state.get("runtime_binding_id")
        or not isinstance(presentation_ack, dict)
        or presentation_ack.get("binding", {}).get("presentation_instance_id")
        != state.get("presentation_instance_id")
    ):
        raise DriverError("presentation action binding differs")
    _validate_process_state(state)
    module = _ensure_iterm_module(private_root)
    return asyncio.run(
        _presentation_action_async(
            module,
            private_root,
            state,
            action=payload["action"],
        )
    )


def supervise(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Observe the exact Harness-owned process without vendor lifecycle APIs."""

    if set(payload) != {
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }:
        raise DriverError("supervision payload differs")
    private_root = _private_root(payload["private_root"])
    state_path = _state_path(private_root)
    state = _read_private(state_path)
    runtime_ack = payload["runtime_ready_ack"]
    binding = runtime_ack.get("binding") if isinstance(runtime_ack, dict) else None
    if (
        state.get("status") != "ready"
        or not isinstance(binding, dict)
        or binding.get("scenario_id") != state.get("scenario_id")
        or binding.get("participant_id") != state.get("participant_id")
        or binding.get("participant_generation")
        != state.get("participant_generation")
        or binding.get("runtime_binding_id") != state.get("runtime_binding_id")
        or binding.get("process_identity_sha256")
        != state.get("process_identity_sha256")
        or _boot_id_sha256() != state.get("boot_id_sha256")
        or not isinstance(state.get("supervision_fencing_token"), str)
        or not state["supervision_fencing_token"]
        or not isinstance(state.get("supervision_heartbeat_sequence"), int)
        or isinstance(state.get("supervision_heartbeat_sequence"), bool)
        or state["supervision_heartbeat_sequence"] < 0
    ):
        raise DriverError("supervision binding differs")
    _validate_process_state(state)
    _refresh_vendor_session_binding(private_root, payload["launch_spec"], state)
    state["supervision_heartbeat_sequence"] += 1
    heartbeat_at = int(time.time() * 1000)
    _write_private(state_path, state)
    resources = [
        {
            "resource_class": "exclusive_runtime",
            "resource_identity_sha256": digest(
                {
                    "resource_class": "exclusive_runtime",
                    "runtime_binding_id": state["runtime_binding_id"],
                    "process_start_identity_sha256": state[
                        "process_identity_sha256"
                    ],
                }
            ),
            "state": "held",
        }
    ]
    observation = {
        "schema_version": 1,
        "runtime_binding_id": state["runtime_binding_id"],
        "process_start_identity_sha256": state["process_identity_sha256"],
        "boot_id_sha256": state["boot_id_sha256"],
        "heartbeat_sequence": state["supervision_heartbeat_sequence"],
        "heartbeat_at_unix_ms": heartbeat_at,
        "fencing_token_sha256": digest(
            {"fencing_token": state["supervision_fencing_token"]}
        ),
        "resources": resources,
    }
    return {
        **observation,
        "observation_evidence_sha256": digest(observation),
    }


def _delivery_state(
    payload: Mapping[str, Any], *, require_delivered: bool
) -> tuple[Path, dict[str, Any], dict[str, Any], str]:
    private_root = _private_root(payload["private_root"])
    state = _read_private(_state_path(private_root))
    record = payload["delivery_record"]
    token = payload["consumption_token"]
    if (
        state.get("status") != "ready"
        or state.get("interaction_mode") != "tui"
        or state.get("accepts_typed_delivery") is not True
        or not isinstance(record, dict)
        or not isinstance(token, str)
        or re.fullmatch(r"[0-9a-f]{48}", token) is None
        or record.get("state")
        != ("delivered" if require_delivered else "delivery_attempted")
        or not isinstance(record.get("events"), list)
        or not record["events"]
    ):
        raise DriverError("typed delivery binding is invalid")
    target = record.get("target")
    runtime_ack = payload["runtime_ready_ack"]
    presentation_ack = payload["presentation_create_ack"]
    if (
        not isinstance(target, dict)
        or not isinstance(runtime_ack, dict)
        or not isinstance(presentation_ack, dict)
        or target.get("runtime_binding_id") != state.get("runtime_binding_id")
        or target.get("presentation_binding_id")
        != state.get("presentation_instance_id")
        or runtime_ack.get("binding", {}).get("runtime_binding_id")
        != state.get("runtime_binding_id")
        or presentation_ack.get("binding", {}).get("presentation_instance_id")
        != state.get("presentation_instance_id")
        or target.get("receiver", {}).get("participant_id")
        != state.get("owner_marker", {}).get("participant_id")
        or target.get("receiver", {}).get("participant_generation")
        != state.get("owner_marker", {}).get("participant_generation")
    ):
        raise DriverError("typed delivery target differs")
    _validate_process_state(state)
    return private_root, state, record, token


async def _exact_session(
    module: Any,
    state: Mapping[str, Any],
    *,
    require_foreground_process_group: bool = True,
) -> tuple[Any, Any, Any, int]:
    connection = await _bounded(module.Connection.async_create())
    try:
        return await _exact_session_connected(
            module,
            connection,
            state,
            require_foreground_process_group=require_foreground_process_group,
        )
    except BaseException:
        await _close_iterm_connection(connection)
        raise


async def _exact_session_connected(
    module: Any,
    connection: Any,
    state: Mapping[str, Any],
    *,
    require_foreground_process_group: bool,
) -> tuple[Any, Any, Any, int]:
    app = await _bounded(module.async_get_app(connection))
    window = app.get_window_by_id(state.get("window_id")) if app is not None else None
    if window is None:
        raise DriverError("owned iTerm window is absent")
    window_id, session_id, session = _topology_identity(window)
    if window_id != state.get("window_id") or session_id != state.get("session_id"):
        raise DriverError("owned iTerm delivery topology drifted")
    if await _bounded(window.async_get_variable(OWNER_VARIABLE)) != state.get(
        "owner_marker"
    ):
        raise DriverError("owned iTerm delivery marker drifted")
    try:
        job_pid = int(await _bounded(session.async_get_variable("jobPid")))
    except (TypeError, ValueError) as exc:
        raise DriverError("owned iTerm delivery job identity is invalid") from exc
    if require_foreground_process_group:
        _validate_owned_foreground_job(job_pid, state)
    else:
        _validate_owned_descendant_process(job_pid, state)
    return app, window, session, job_pid


def _retryable_iterm_connection_error(exc: BaseException) -> bool:
    error_type = type(exc)
    return (
        error_type.__name__ == "ConnectionClosedError"
        and error_type.__module__.startswith("websockets.")
    )


async def _authorize_sender_exact_session(
    module: Any, state: Mapping[str, Any]
) -> tuple[Any, Any, Any, int]:
    """Recheck the exact TUI binding, retrying only a closed API transport.

    Concurrent participant authorization can race an iTerm loopback connection
    handoff.  No message has been accepted yet, so reconnecting this read-only
    proof is safe.  Topology, owner-marker, process-chain, and all other errors
    remain terminal on their first observation.
    """

    for attempt in range(SENDER_SESSION_CONNECT_ATTEMPTS):
        try:
            result = await _exact_session(
                module,
                state,
                require_foreground_process_group=False,
            )
            await _close_iterm_connection(
                getattr(result[0], "connection", None)
            )
            return result
        except Exception as exc:
            if (
                not _retryable_iterm_connection_error(exc)
                or attempt + 1 == SENDER_SESSION_CONNECT_ATTEMPTS
            ):
                raise
            await asyncio.sleep(SENDER_SESSION_RETRY_SECONDS)
    raise AssertionError("sender exact-session attempts exhausted")  # pragma: no cover


def _delivery_notification(
    record: Mapping[str, Any],
    message_kind: str,
    message: str,
    token: str,
    participant_ping: Path,
) -> str:
    sender = record["target"]["sender"]["participant_id"]
    terminal_kinds = {
        "collaboration.response",
        "collaboration.review-response",
        "collaboration.notice",
        "collaboration.done",
    }
    if message_kind in terminal_kinds:
        handling = (
            "This delivery is terminal/informational. Consume the payload, but do not "
            "send a Harness-tracked receipt or acknowledgement. Do not add receipt-only "
            "prose; if no new work is explicitly requested, emit only the required "
            "consumption marker.\n"
        )
        reply_instruction = ""
    else:
        handling = (
            "Treat the payload as a peer request. Complete or answer it, and send a "
            "Harness-tracked reply when the work requires one.\n"
        )
        ping_command = shlex.quote(str(participant_ping))
        reply_kind = (
            "review-response"
            if message_kind == "collaboration.review-request"
            else "msg"
        )
        reply_instruction = (
            f"To send that reply, use: {ping_command} {sender} "
            f"--kind {reply_kind} --reply-to {record['delivery_id']} <message>."
        )
    return (
        "[AI Collaboration Harness typed delivery]\n"
        f"delivery_id: {record['delivery_id']}\n"
        f"from_participant: {sender}\n"
        f"message_kind: {message_kind}\n"
        f"{handling}"
        "--- payload ---\n"
        f"{message}\n"
        "--- end payload ---\n"
        f"CONSUMPTION TOKEN: {token}\n"
        "After completing the request, end your response with the exact prefix "
        "AI_COLLAB_CONSUMED: immediately followed by the token above. "
        "Do not put whitespace between the prefix and token. "
        f"{reply_instruction}"
    )


def _pingagent_deliver(
    state: Mapping[str, Any], record: Mapping[str, Any], notification: str
) -> dict[str, Any]:
    if (
        PINGAGENT_TRANSPORT.is_symlink()
        or not PINGAGENT_TRANSPORT.is_file()
        or PINGAGENT_TRANSPORT.stat().st_uid != os.getuid()
        or stat.S_IMODE(PINGAGENT_TRANSPORT.stat().st_mode) & 0o022
    ):
        raise DriverError("PingAgent typed transport is unavailable")
    attempt = record["events"][-1]
    request = {
        "transport_contract_version": 1,
        "operation": "deliver_exact_session",
        "delivery_id": record["delivery_id"],
        "transport_attempt_id": attempt["transport_attempt_id"],
        "session_id": state["session_id"],
        "notification": notification,
        "payload_digest": record["payload_digest"],
    }
    completed = subprocess.run(
        (str(PINGAGENT_TRANSPORT),),
        cwd=ROOT,
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "TMPDIR", "LANG", "LC_ALL"}
        },
        input=canonical_json_bytes(request) + b"\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or completed.stderr:
        raise DriverError("PingAgent typed transport failed")
    try:
        result = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError("PingAgent typed transport reply is invalid") from exc
    evidence = {
        "transport_contract_version": 1,
        "delivery_id": record["delivery_id"],
        "transport_attempt_id": attempt["transport_attempt_id"],
        "payload_digest": record["payload_digest"],
        "session_identity_sha256": hashlib.sha256(
            state["session_id"].encode("utf-8")
        ).hexdigest(),
        "injection_confirmed": True,
    }
    if (
        not isinstance(result, dict)
        or result
        != {
            **evidence,
            "transport_evidence_digest": digest(evidence),
        }
    ):
        raise DriverError("PingAgent typed transport evidence differs")
    return result


async def _validate_exact_session_async(
    module: Any, state: Mapping[str, Any]
) -> None:
    result = await _exact_session(module, state)
    try:
        return None
    finally:
        await _close_iterm_connection(
            getattr(result[0], "connection", None)
        )


def deliver(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "delivery_record",
        "message",
        "message_kind",
        "consumption_token",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }
    if set(payload) != required:
        raise DriverError("deliver payload differs")
    private_root, state, record, token = _delivery_state(
        payload, require_delivered=False
    )
    message = payload["message"]
    message_kind = payload["message_kind"]
    attempt = record["events"][-1]
    if (
        not isinstance(message, str)
        or not message
        or len(message.encode("utf-8")) > 64 * 1024
        or not isinstance(message_kind, str)
        or re.fullmatch(
            r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+", message_kind
        )
        is None
        or attempt.get("event") != "attempt_started"
    ):
        raise DriverError("typed delivery payload is invalid")
    module = _ensure_iterm_module(private_root)
    asyncio.run(_validate_exact_session_async(module, state))
    transport = _pingagent_deliver(
        state,
        record,
        _delivery_notification(
            record,
            message_kind,
            message,
            token,
            _participant_ping_path(private_root),
        ),
    )
    private_state = _read_private(_state_path(private_root))
    private_state.setdefault("delivery_transport_history", []).append(
        {
            "delivery_id": record["delivery_id"],
            "transport_attempt_id": attempt["transport_attempt_id"],
            "transport_evidence_digest": transport["transport_evidence_digest"],
        }
    )
    _write_private(_state_path(private_root), private_state)
    delivery_ack = {
        "ack_kind": "delivered",
        "delivery_id": record["delivery_id"],
        "message_id": record["message_id"],
        "target": record["target"],
        "payload_digest": record["payload_digest"],
        "attempt_number": attempt["attempt_number"],
        "transport_attempt_id": attempt["transport_attempt_id"],
    }
    return {"delivery_ack": delivery_ack, "consumption_ack": None}


async def _await_consumption_async(
    module: Any, state: Mapping[str, Any], expected_marker: str
) -> None:
    app, _, session, _ = await _exact_session(module, state)
    try:
        deadline = asyncio.get_running_loop().time() + CONSUMPTION_TIMEOUT_SECONDS
        while True:
            contents = await _bounded(session.async_get_screen_contents())
            if expected_marker in _screen_text(contents):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise DriverError("agent consumption signal timed out")
            await asyncio.sleep(0.5)
    finally:
        await _close_iterm_connection(getattr(app, "connection", None))


def await_consumption(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "delivery_record",
        "consumption_token",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }
    if set(payload) != required:
        raise DriverError("consumption payload differs")
    private_root, state, record, token = _delivery_state(
        payload, require_delivered=True
    )
    delivered = record["events"][-1]
    if delivered.get("event") != "ack_accepted":
        raise DriverError("delivery ACK evidence is unavailable")
    module = _ensure_iterm_module(private_root)
    asyncio.run(
        _await_consumption_async(
            module, state, f"AI_COLLAB_CONSUMED:{token}"
        )
    )
    return {
        "consumption_ack": {
            "ack_kind": "consumed",
            "delivery_id": record["delivery_id"],
            "message_id": record["message_id"],
            "target": record["target"],
            "payload_digest": record["payload_digest"],
            "attempt_number": delivered["attempt_number"],
            "transport_attempt_id": delivered["transport_attempt_id"],
            "delivery_ack_digest": delivered["evidence_digest"],
        }
    }


async def _iterm_close_async(module: Any, state: Mapping[str, Any]) -> int:
    connection = await _bounded(module.Connection.async_create())
    try:
        return await _iterm_close_connected(module, connection, state)
    finally:
        await _close_iterm_connection(connection)


async def _iterm_close_connected(
    module: Any, connection: Any, state: Mapping[str, Any]
) -> int:
    app = await _bounded(module.async_get_app(connection))
    window = app.get_window_by_id(state.get("window_id")) if app is not None else None
    if window is None:
        raise DriverError("owned iTerm window is absent before exact close")
    window_id, session_id, session = _topology_identity(window)
    if window_id != state.get("window_id") or session_id != state.get("session_id"):
        raise DriverError("owned iTerm topology drifted before close")
    if await _bounded(window.async_get_variable(OWNER_VARIABLE)) != state.get(
        "owner_marker"
    ):
        raise DriverError("owned iTerm marker drifted before close")
    try:
        job_pid = int(await _bounded(session.async_get_variable("jobPid")))
    except (TypeError, ValueError) as exc:
        raise DriverError("owned iTerm job identity is invalid") from exc
    _validate_owned_foreground_job(job_pid, state)
    await _bounded(window.async_close(force=True))
    # The App object is a topology snapshot. Depending on iTerm notification
    # timing it can retain the just-closed window for several seconds, even
    # though async_close accepted the exact request. The caller proves the
    # owned foreground/root processes are absent before publishing cleanup.
    return job_pid


def _wait_process_absent_bounded(pid: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _wait_processes_absent_bounded(
    pids: Sequence[int], timeout_seconds: float
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    for pid in dict.fromkeys(pids):
        remaining = max(0.0, deadline - time.monotonic())
        if not _wait_process_absent_bounded(pid, remaining):
            return False
    return True


async def _safe_tui_close_async(
    module: Any,
    state: Mapping[str, Any],
    drain_timeout_ms: int,
) -> tuple[str, bool, int, bool]:
    app = None
    accepted_process_pids = [state["pid"]]
    for attempt in range(2):
        app = None
        try:
            app, window, _, foreground_pid = await _exact_session(module, state)
            accepted_process_pids.append(foreground_pid)
            await _bounded(window.async_close(force=True))
            break
        except Exception:
            if len(accepted_process_pids) > 1 and await asyncio.to_thread(
                _wait_processes_absent_bounded, accepted_process_pids, 0.25
            ):
                break
            await _close_iterm_connection(
                getattr(app, "connection", None)
            )
            app = None
            if attempt == 1:
                raise
            await asyncio.sleep(0.1)
    try:
        return await _safe_tui_close_connected(
            state,
            accepted_process_pids,
            drain_timeout_ms,
        )
    finally:
        await _close_iterm_connection(getattr(app, "connection", None))


async def _safe_tui_close_connected(
    state: Mapping[str, Any],
    accepted_process_pids: list[int],
    drain_timeout_ms: int,
) -> tuple[str, bool, int, bool]:
    processes_absent = await asyncio.to_thread(
        _wait_processes_absent_bounded, accepted_process_pids, 1.0
    )
    if not processes_absent:
        try:
            await asyncio.to_thread(_terminate_gracefully_exact, state)
        except DriverError as exc:
            if str(exc) == "owned process remained after graceful stop":
                return "timeout", False, 0, False
            raise
        processes_absent = await asyncio.to_thread(
            _wait_processes_absent_bounded,
            accepted_process_pids,
            drain_timeout_ms / 1000,
        )
    if not processes_absent:
        return "timeout", False, 0, False
    return "requested", False, 0, True


def _close_binding(
    payload: Mapping[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    required = {
        "context",
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
        "drain_timeout_ms",
    }
    if set(payload) != required:
        raise DriverError("close payload differs")
    timeout = payload["drain_timeout_ms"]
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= 300_000
    ):
        raise DriverError("close timeout is invalid")
    private_root = _private_root(payload["private_root"])
    state = _read_private(_state_path(private_root))
    context = payload["context"]
    runtime_ack = payload["runtime_ready_ack"]
    presentation_ack = payload["presentation_create_ack"]
    if (
        state.get("status") != "ready"
        or not isinstance(context, dict)
        or any(
            state.get(field) != context.get(field)
            for field in (
                "scenario_id",
                "participant_id",
                "participant_generation",
            )
        )
        or not isinstance(runtime_ack, dict)
        or runtime_ack.get("binding", {}).get("runtime_binding_id")
        != state.get("runtime_binding_id")
        or (
            presentation_ack is None
            and state.get("presentation_instance_id") is not None
        )
        or (
            presentation_ack is not None
            and presentation_ack.get("binding", {}).get(
                "presentation_instance_id"
            )
            != state.get("presentation_instance_id")
        )
    ):
        raise DriverError("close binding differs")
    _validate_process_state(state)
    return private_root, state, _runtime_profile(payload["launch_spec"])


def close(payload: Mapping[str, Any]) -> dict[str, Any]:
    private_root, state, profile = _close_binding(payload)
    try:
        vendor_session_identity_sha256 = _refresh_vendor_session_binding(
            private_root, payload["launch_spec"], state
        )
    except DriverError:
        vendor_session_identity_sha256 = None
    drain_timeout_ms = payload["drain_timeout_ms"]
    classification = "idle"
    drain_requested = False
    progress_count = 0
    closed = False
    if state["interaction_mode"] == "tui":
        module = _ensure_iterm_module(private_root)
        classification, drain_requested, progress_count, closed = asyncio.run(
            _safe_tui_close_async(module, state, drain_timeout_ms)
        )
    elif profile["safe_close"]["idle_detection"] == "always":
        os.killpg(state["pgid"], signal.SIGTERM)
        closed = _wait_process_absent_bounded(
            state["pid"], drain_timeout_ms / 1000
        )
        if not closed:
            classification = "timeout"
            drain_requested = True
    else:
        classification = "unknown"
    evidence = digest(
        {
            "runtime_binding_id": state["runtime_binding_id"],
            "presentation_instance_id": state["presentation_instance_id"],
            "classification": classification,
            "closed": closed,
            "drain_requested": drain_requested,
            "progress_event_count": progress_count,
        }
    )
    if closed:
        state["status"] = "stopped"
        state["stop_evidence_sha256"] = evidence
        _write_private(_state_path(private_root), state)
    return {
        "classification": classification,
        "closed": closed,
        "action_outcome_known": True,
        "drain_requested": drain_requested,
        "progress_event_count": progress_count,
        "runtime_binding_id": state["runtime_binding_id"],
        "presentation_binding_id": state["presentation_instance_id"],
        "owned_resource_evidence_sha256": evidence,
        "vendor_session_identity_sha256": vendor_session_identity_sha256,
        "owner": payload["context"]["participant_id"],
        "command": state["runtime_profile_ref"],
        "started_at_unix_ms": state.get("started_at_unix_ms"),
    }


def stop(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "context",
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }
    if set(payload) != required:
        raise DriverError("stop payload differs")
    private_root = _private_root(payload["private_root"])
    state_path = _state_path(private_root)
    state = _read_private(state_path)
    if state.get("status") == "stopped":
        return {
            "stopped": True,
            "owned_resource_evidence_sha256": state["stop_evidence_sha256"],
            "vendor_session_identity_sha256": state.get(
                "vendor_session_identity_sha256"
            ),
        }
    _validate_process_state(state)
    try:
        vendor_session_identity_sha256 = _refresh_vendor_session_binding(
            private_root, payload["launch_spec"], state
        )
    except DriverError:
        vendor_session_identity_sha256 = None
    if state.get("interaction_mode") == "tui":
        module = _ensure_iterm_module(private_root)
        foreground_pid = asyncio.run(_iterm_close_async(module, state))
        if not _wait_process_absent(state["pid"]):
            _terminate_gracefully_exact(state)
        if not _wait_process_absent_bounded(foreground_pid, PROCESS_WAIT_SECONDS):
            raise DriverError("owned foreground process remained after graceful stop")
    else:
        _terminate_gracefully_exact(state)
    evidence = digest(
        {
            "runtime_binding_id": state["runtime_binding_id"],
            "presentation_instance_id": state["presentation_instance_id"],
            "process_absent": True,
        }
    )
    state["status"] = "stopped"
    state["stop_evidence_sha256"] = evidence
    _write_private(state_path, state)
    return {
        "stopped": True,
        "owned_resource_evidence_sha256": evidence,
        "vendor_session_identity_sha256": vendor_session_identity_sha256,
    }


def _owned_process_is_absent(state: Mapping[str, Any]) -> bool:
    """Report whether the owned process is provably gone.

    Only a definite ProcessLookupError counts. If any process still holds the
    recorded pid the answer is False, even when it is a different process that
    reused the number, so identity validation still refuses to act on it.
    """
    pid = state.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def force_stop(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Force-stop only the exact Harness-owned binding supplied by Host."""

    required = {
        "context",
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "private_root",
    }
    if set(payload) != required:
        raise DriverError("force-stop payload differs")
    private_root = _private_root(payload["private_root"])
    state_path = _state_path(private_root)
    state = _read_private(state_path)
    if state.get("status") == "stopped":
        return {
            "stopped": True,
            "owned_resource_evidence_sha256": state["stop_evidence_sha256"],
        }
    if _owned_process_is_absent(state):
        # A force stop has nothing left to terminate. Recording the binding as
        # stopped is evidence that the process is gone, not an assumption that
        # it was closed, so the evidence claims only what was observed. Without
        # this the binding can never be released: identity validation rejects a
        # vanished process, the close reports back as an unknown outcome, and
        # the Scenario becomes impossible to destroy from any entry point.
        evidence = digest(
            {
                "runtime_binding_id": state["runtime_binding_id"],
                "presentation_instance_id": state["presentation_instance_id"],
                "process_absent": True,
                "termination_mode": "force-stop-process-absent",
            }
        )
        state["status"] = "stopped"
        state["stop_evidence_sha256"] = evidence
        _write_private(state_path, state)
        return {"stopped": True, "owned_resource_evidence_sha256": evidence}
    _validate_process_state(state)
    if state.get("interaction_mode") == "tui":
        module = _ensure_iterm_module(private_root)
        foreground_pid = asyncio.run(_iterm_close_async(module, state))
        if not _wait_process_absent(state["pid"]):
            _terminate_exact(state)
        if not _wait_process_absent_bounded(foreground_pid, PROCESS_WAIT_SECONDS):
            raise DriverError("owned foreground process remained after force stop")
    else:
        _terminate_exact(state)
    evidence = digest(
        {
            "runtime_binding_id": state["runtime_binding_id"],
            "presentation_instance_id": state["presentation_instance_id"],
            "process_absent": True,
            "termination_mode": "force-stop",
        }
    )
    state["status"] = "stopped"
    state["stop_evidence_sha256"] = evidence
    _write_private(state_path, state)
    return {"stopped": True, "owned_resource_evidence_sha256": evidence}


def _recovery_result(
    payload: Mapping[str, Any], *, recovery_class: str, stop_evidence: str | None
) -> dict[str, Any]:
    context = payload["context"]
    evidence = digest(
        {
            "scenario_id": context["scenario_id"],
            "participant_id": context["participant_id"],
            "previous_participant_generation": context[
                "participant_generation"
            ],
            "next_participant_generation": payload[
                "next_participant_generation"
            ],
            "recovery_class": recovery_class,
            "stop_evidence_sha256": stop_evidence,
            "external_resources_absent": True,
            "private_generation_retained": True,
        }
    )
    return {
        "recovered": True,
        "recovery_class": recovery_class,
        "previous_participant_generation": context["participant_generation"],
        "next_participant_generation": payload["next_participant_generation"],
        "external_resources_absent": True,
        "private_generation_retained": True,
        "owned_resource_evidence_sha256": evidence,
    }


def _startup_gate_recovery_evidence(
    private_root: Path,
    payload: Mapping[str, Any],
    state: Mapping[str, Any],
    degraded: Mapping[str, Any],
) -> str | None:
    diagnostic_path = _launch_diagnostic_path(private_root)
    if not diagnostic_path.is_file() or diagnostic_path.is_symlink():
        return None
    diagnostic = _read_private(diagnostic_path)
    required = {
        "schema_version",
        "outcome",
        "stage",
        "reason_code",
        "cleanup_outcome",
    }
    if not required <= set(diagnostic) or set(diagnostic) - required - {
        "provider_error_code",
        "remediation_ref",
        "process_observation",
    }:
        return None
    if (
        diagnostic["schema_version"] != 1
        or diagnostic["outcome"] != "rejected"
        or diagnostic["stage"]
        not in {
            "startup-gate",
            "vendor-session-proof",
            "final-process",
            "window-geometry",
            "activation",
        }
        or diagnostic["cleanup_outcome"] not in {"close-requested", "unconfirmed"}
    ):
        return None
    pid = state.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise DriverError("repair stopped binding lacks process identity")
    exact_process_absent = False
    try:
        observation = _process_observation(pid)
    except DriverError as exc:
        if str(exc) != "owned process is absent":
            raise
        exact_process_absent = True
    else:
        if observation["identity_sha256"] == state.get("process_identity_sha256"):
            raise DriverError("repair stopped binding still has live process")
        exact_process_absent = True
    launch_process = diagnostic.get("process_observation")
    if not isinstance(launch_process, dict):
        return None
    launch_pid = launch_process.get("pid")
    launch_pgid = launch_process.get("pgid")
    launch_identity = launch_process.get("identity_sha256")
    if (
        not isinstance(launch_pid, int)
        or isinstance(launch_pid, bool)
        or launch_pid <= 1
        or not isinstance(launch_pgid, int)
        or isinstance(launch_pgid, bool)
        or launch_pgid <= 1
        or not isinstance(launch_identity, str)
        or re.fullmatch(r"[0-9a-f]{64}", launch_identity) is None
    ):
        return None
    try:
        observation = _process_observation(launch_pid)
    except DriverError as exc:
        if str(exc) != "owned process is absent":
            raise
    else:
        if observation["identity_sha256"] == launch_identity:
            raise DriverError("repair startup cleanup is unconfirmed")
    if _exact_process_group_observations(launch_pgid):
        raise DriverError("repair startup cleanup is unconfirmed")
    return digest(
        {
            "recovery": "startup_gate_stopped_binding",
            "diagnostic": diagnostic,
            "degraded_evidence_sha256": degraded[
                "owned_resource_evidence_sha256"
            ],
            "state_runtime_binding_id": state.get("runtime_binding_id"),
            "state_presentation_instance_id": state.get(
                "presentation_instance_id"
            ),
            "state_stop_evidence_sha256": state.get("stop_evidence_sha256"),
            "exact_process_absent": exact_process_absent,
            "launch_process": diagnostic.get("process_observation"),
        }
    )


def repair(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recover only an exact owned binding or a provably pre-binding failure."""

    required = {
        "context",
        "next_participant_generation",
        "launch_spec",
        "resolved_driver",
        "runtime_ready_ack",
        "presentation_create_ack",
        "degraded",
        "private_root",
    }
    if set(payload) != required:
        raise DriverError("repair payload differs")
    context = payload["context"]
    resolved = payload["resolved_driver"]
    next_generation = payload["next_participant_generation"]
    if (
        not isinstance(context, dict)
        or not isinstance(context.get("participant_generation"), int)
        or isinstance(context.get("participant_generation"), bool)
        or context["participant_generation"] < 1
        or next_generation != context["participant_generation"] + 1
        or not isinstance(resolved, dict)
        or resolved.get("driver_registry_digest")
        != context.get("driver_registry_digest")
        or resolved.get("capability_snapshot_digest")
        != context.get("capability_snapshot_digest")
    ):
        raise DriverError("repair binding differs")
    private_root = _private_root(payload["private_root"])
    state_path = _state_path(private_root)
    runtime_ack = payload["runtime_ready_ack"]
    presentation_ack = payload["presentation_create_ack"]
    degraded = payload["degraded"]
    if (
        not isinstance(degraded, dict)
        or degraded.get("repair_action") != "participant.recover"
        or not isinstance(degraded.get("cleanup_pending"), bool)
        or re.fullmatch(
            r"[0-9a-f]{64}",
            degraded.get("owned_resource_evidence_sha256", ""),
        )
        is None
    ):
        raise DriverError("repair degradation evidence differs")
    if state_path.is_file() and not state_path.is_symlink():
        state = _read_private(state_path)
        runtime_binding = (
            runtime_ack.get("binding") if isinstance(runtime_ack, dict) else None
        )
        presentation_binding = (
            presentation_ack.get("binding")
            if isinstance(presentation_ack, dict)
            else None
        )
        if any(
            state.get(field) != context.get(field)
            for field in (
                "scenario_id",
                "participant_id",
                "participant_generation",
            )
        ):
            raise DriverError("repair owned binding differs")
        if state.get("status") == "stopped" and runtime_ack is None:
            if presentation_ack is not None:
                raise DriverError("repair durable cleanup evidence differs")
            if (
                degraded["cleanup_pending"] is False
                and state.get("stop_evidence_sha256")
                == degraded["owned_resource_evidence_sha256"]
            ):
                stop_evidence = state["stop_evidence_sha256"]
            elif degraded["cleanup_pending"] is True:
                stop_evidence = _startup_gate_recovery_evidence(
                    private_root, payload, state, degraded
                )
                if stop_evidence is None:
                    raise DriverError("repair durable cleanup evidence differs")
            else:  # pragma: no cover - validated above
                raise DriverError("repair durable cleanup evidence differs")
            return _recovery_result(
                payload,
                recovery_class="exact_binding_stopped",
                stop_evidence=stop_evidence,
            )
        if (
            not isinstance(runtime_binding, dict)
            or runtime_binding.get("runtime_binding_id")
            != state.get("runtime_binding_id")
            or (
                state.get("presentation_instance_id") is None
                and presentation_ack is not None
            )
            or (
                state.get("presentation_instance_id") is not None
                and (
                    not isinstance(presentation_binding, dict)
                    or presentation_binding.get("presentation_instance_id")
                    != state.get("presentation_instance_id")
                )
            )
        ):
            raise DriverError("repair published binding differs")
        stopped = stop(
            {
                key: value
                for key, value in payload.items()
                if key not in {"next_participant_generation", "degraded"}
            }
        )
        return _recovery_result(
            payload,
            recovery_class="exact_binding_stopped",
            stop_evidence=stopped["owned_resource_evidence_sha256"],
        )
    if state_path.exists() or state_path.is_symlink():
        raise DriverError("repair private binding state is ambiguous")
    if runtime_ack is not None or presentation_ack is not None:
        raise DriverError("repair lacks private state for published binding")
    diagnostic_path = _launch_diagnostic_path(private_root)
    pre_window_failure = False
    if diagnostic_path.is_file() and not diagnostic_path.is_symlink():
        diagnostic = _read_private(diagnostic_path)
        pre_window_failure = (
            set(diagnostic)
            == {
                "schema_version",
                "outcome",
                "stage",
                "reason_code",
                "cleanup_outcome",
            }
            and diagnostic["outcome"] == "rejected"
            and diagnostic["stage"]
            in {
                "iterm-dependency",
                "iterm-connect",
                "display-topology",
                "launch-material",
            }
            and diagnostic["cleanup_outcome"] == "not-required"
        )
    _, lock_digest = _load_lock()
    ready = _iterm_install_root(private_root, lock_digest) / "ready.json"
    launcher = private_root / "runtime-launcher.zsh"
    if not pre_window_failure and (
        ready.exists()
        or ready.is_symlink()
        or launcher.exists()
        or launcher.is_symlink()
    ):
        raise DriverError("repair cannot prove pre-binding resource absence")
    # runtime-launcher.zsh is durably written before window creation and is
    # independent of the interpreter-specific dependency cache. Its absence
    # therefore proves this generation never entered the external-effect path.
    return _recovery_result(
        payload,
        recovery_class="pre_binding_absent",
        stop_evidence=None,
    )


OPERATIONS = {
    "list_templates": list_templates,
    "permission_probe": permission_probe,
    "permission_request": permission_request,
    "environment_probe": environment_probe,
    "resolve": resolve,
    "start": start,
    "status": status,
    "presentation_action": presentation_action,
    "supervise": supervise,
    "deliver": deliver,
    "await_consumption": await_consumption,
    "authorize_sender": authorize_sender,
    "close": close,
    "stop": stop,
    "force_stop": force_stop,
    "repair": repair,
}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or set(request) != {
            "adapter_protocol_version",
            "adapter_id",
            "operation",
            "payload",
        }:
            raise DriverError("adapter request differs")
        if (
            request["adapter_protocol_version"] != ADAPTER_PROTOCOL_VERSION
            or request["adapter_id"] != "ai-collab-participant-driver"
            or request["operation"] not in OPERATIONS
            or not isinstance(request["payload"], dict)
        ):
            raise DriverError("adapter request is invalid")
        result = OPERATIONS[request["operation"]](request["payload"])
        json.dump(
            {
                "adapter_protocol_version": ADAPTER_PROTOCOL_VERSION,
                "adapter_id": "ai-collab-participant-driver",
                "outcome": "completed",
                "result": result,
            },
            sys.stdout,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except Exception:
        print("participant driver operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
