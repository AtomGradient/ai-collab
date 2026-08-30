#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司
"""No-prompt macOS Automation and local iTerm API preflight helpers."""

from __future__ import annotations

import ctypes
import os
import platform
import stat
import subprocess
from pathlib import Path
from typing import Any


ITERM_SUPPORT_SUITE = "iTerm2"
ITERM_PREFERENCES_DOMAIN = "com.googlecode.iterm2"
ITERM_AUTH_BYPASS_FILENAME = "disable-automation-auth"
ITERM_PRIVATE_SOCKET_COMPONENTS = ("private", "socket")
ITERM_API_SERVER_KEYS = ("EnableAPIServer", "NoSyncEnableAPIServer")


class AutomationPreflightError(RuntimeError):
    """A fail-closed local platform observation error."""


class AEDesc(ctypes.Structure):
    _fields_ = [
        ("descriptorType", ctypes.c_uint32),
        ("dataHandle", ctypes.c_void_p),
    ]


def _fourcc(value: str) -> int:
    if len(value) != 4 or not value.isascii():
        raise AutomationPreflightError(
            "invalid Apple Event four-character code"
        )
    return int.from_bytes(value.encode("ascii"), "big")


def automation_permission_status(
    bundle_identifier: str,
    *,
    ask_user_if_needed: bool = False,
) -> dict[str, Any]:
    """Read Automation/TCC state; optionally let macOS show its consent prompt.

    The default never prompts (fail-closed observation). Passing
    ``ask_user_if_needed=True`` is reserved for an explicit user gesture —
    the system consent dialog may appear and block until answered.
    """

    if platform.system() != "Darwin":
        raise AutomationPreflightError("Automation preflight requires macOS")
    if not isinstance(bundle_identifier, str) or not bundle_identifier:
        raise AutomationPreflightError(
            "Automation target bundle identifier is invalid"
        )
    framework = ctypes.CDLL(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    framework.AECreateDesc.argtypes = [
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(AEDesc),
    ]
    framework.AECreateDesc.restype = ctypes.c_int32
    framework.AEDeterminePermissionToAutomateTarget.argtypes = [
        ctypes.POINTER(AEDesc),
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_bool,
    ]
    framework.AEDeterminePermissionToAutomateTarget.restype = ctypes.c_int32
    framework.AEDisposeDesc.argtypes = [ctypes.POINTER(AEDesc)]
    framework.AEDisposeDesc.restype = ctypes.c_int32

    encoded = bundle_identifier.encode("utf-8")
    target = AEDesc()
    created = framework.AECreateDesc(
        _fourcc("bund"), encoded, len(encoded), ctypes.byref(target)
    )
    if created != 0:
        raise AutomationPreflightError(
            "cannot construct Automation target"
        )
    try:
        status = framework.AEDeterminePermissionToAutomateTarget(
            ctypes.byref(target),
            _fourcc("****"),
            _fourcc("****"),
            bool(ask_user_if_needed),
        )
    finally:
        disposed = framework.AEDisposeDesc(ctypes.byref(target))
    if disposed != 0:
        raise AutomationPreflightError("cannot dispose Automation target")
    labels = {
        0: "authorized",
        -1743: "denied",
        -1744: "not_determined_no_prompt",
    }
    return {
        "status": labels.get(status, "other_error"),
        "authorized": status == 0,
        "ask_user_if_needed": bool(ask_user_if_needed),
        "prompt_requested": bool(ask_user_if_needed),
    }


def _iterm_support_root() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / ITERM_SUPPORT_SUITE
    )


def authentication_bypass_status(
    support_root: Path | None = None,
) -> dict[str, bool]:
    """Fail closed on any file at iTerm's unauthenticated-API escape hatch."""

    root = support_root if support_root is not None else _iterm_support_root()
    bypass_path = root / ITERM_AUTH_BYPASS_FILENAME
    present = bypass_path.exists() or bypass_path.is_symlink()
    return {
        "bypass_file_present": present,
        "cookie_authentication_required": not present,
    }


def target_application_running(bundle_identifier: str) -> bool:
    """Observe whether an app is running without sending AppleEvents."""

    if not isinstance(bundle_identifier, str) or not bundle_identifier:
        raise AutomationPreflightError("Automation target bundle identifier is invalid")
    try:
        completed = subprocess.run(
            ["/usr/bin/lsappinfo", "info", "-only", "pid", bundle_identifier],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutomationPreflightError("cannot inspect Automation target") from exc
    return completed.returncode == 0 and "pid" in completed.stdout.lower()


def _defaults_bool(domain: str, key: str) -> bool | None:
    try:
        completed = subprocess.run(
            ["/usr/bin/defaults", "read", domain, key],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    return None


def iterm_python_api_status(
    preferences_domain: str = ITERM_PREFERENCES_DOMAIN,
) -> dict[str, bool]:
    """Read iTerm2's Python API preference without returning plist paths."""

    values = {
        key: _defaults_bool(preferences_domain, key)
        for key in ITERM_API_SERVER_KEYS
    }
    configured = any(value is not None for value in values.values())
    enabled = any(value is True for value in values.values())
    explicitly_disabled = configured and not enabled
    return {
        "api_server_configured": configured,
        "api_server_enabled": enabled,
        "api_server_explicitly_disabled": explicitly_disabled,
    }


def private_unix_socket_status(
    support_root: Path | None = None,
) -> dict[str, bool]:
    """Observe iTerm's local API socket without returning its path or owner."""

    root = support_root if support_root is not None else _iterm_support_root()
    api_status = iterm_python_api_status()
    socket_path = root.joinpath(
        *ITERM_PRIVATE_SOCKET_COMPONENTS
    )
    try:
        metadata = socket_path.lstat()
    except FileNotFoundError:
        return {
            "present": False,
            "is_unix_socket": False,
            "owned_by_current_user": False,
            "local_only_ready": False,
            **api_status,
        }
    except OSError as exc:
        raise AutomationPreflightError(
            "cannot inspect iTerm private API socket"
        ) from exc
    is_socket = stat.S_ISSOCK(metadata.st_mode)
    owned = metadata.st_uid == os.geteuid()
    return {
        "present": True,
        "is_unix_socket": is_socket,
        "owned_by_current_user": owned,
        "local_only_ready": is_socket and owned,
        **api_status,
    }
