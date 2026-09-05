#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Install or upgrade the signed AI Collab App with bounded recovery."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import plistlib
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
for _candidate in (_SCRIPT_DIR.parent / "src", _SCRIPT_DIR.parent / "python"):
    if (_candidate / "ai_collab" / "pingagent_commands.py").is_file():
        sys.path.insert(0, str(_candidate))
        break
from ai_collab.pingagent_commands import (  # noqa: E402
    CommandLinkError,
    failure_result,
    reconcile_commands,
    remove_commands,
)


BUNDLE_ID = "com.atomgradient.aicollab"
SERVICE_BUILD_DIGEST_KEY = "AICollabServiceBuildDigest"
DEFAULT_TARGET = Path.home() / "Applications/AI Collab.app"
DEFAULT_STATE_ROOT = Path.home() / "Library/Application Support/AI Collab"
HEALTH_PROGRAM = """
import json
import sys
from pathlib import Path
from ai_collab.client import HarnessClient
try:
    value = HarnessClient(Path(sys.argv[1]), timeout_seconds=1.0).host_status()
except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
print(json.dumps(value, sort_keys=True))
""".strip()


class InstallError(RuntimeError):
    pass


def _metadata(app: Path) -> dict[str, Any]:
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise InstallError("App metadata is unavailable") from exc
    if value.get("CFBundleIdentifier") != BUNDLE_ID:
        raise InstallError("candidate App bundle identifier differs")
    digest = value.get(SERVICE_BUILD_DIGEST_KEY)
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise InstallError("candidate App service build identity is invalid")
    return value


def _run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and completed.returncode != 0:
        raise InstallError(completed.stdout.strip() or f"command failed: {argv[0]}")
    return completed


def _team_identifier(app: Path) -> str:
    completed = _run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
    )
    for line in completed.stdout.splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.partition("=")[2].strip()
            if value and value != "not set":
                return value
    raise InstallError("signed App has no stable TeamIdentifier")


def verify_candidate(app: Path, *, existing: Path | None = None) -> dict[str, Any]:
    app = app.expanduser().absolute()
    if app.is_symlink():
        raise InstallError("candidate must be a real .app directory")
    app = app.resolve(strict=True)
    if not app.is_dir() or app.suffix != ".app":
        raise InstallError("candidate must be a real .app directory")
    metadata = _metadata(app)
    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    team_identifier = _team_identifier(app)
    if existing is not None:
        if existing.is_symlink() or not existing.is_dir():
            raise InstallError("existing installation is not a real App directory")
        if _metadata(existing).get("CFBundleIdentifier") != BUNDLE_ID:
            raise InstallError("existing App bundle identifier differs")
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(existing)])
        if _team_identifier(existing) != team_identifier:
            raise InstallError("candidate signing team differs from the installed App")
    return {
        "bundle_id": BUNDLE_ID,
        "service_build_digest": metadata[SERVICE_BUILD_DIGEST_KEY],
        "team_identifier": team_identifier,
    }


def _copy_to_stage(candidate: Path, stage: Path) -> None:
    shutil.copytree(candidate, stage, symlinks=True, copy_function=shutil.copy2)
    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(stage)])


def _atomic_swap(first: Path, second: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    renamex = library.renamex_np
    renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex.restype = ctypes.c_int
    if renamex(os.fsencode(first), os.fsencode(second), 0x00000002) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(first), str(second))


def _ensure_app_not_running(target: Path) -> None:
    executable = target / "Contents/MacOS/AICollab"
    completed = _run(
        ["/usr/bin/pgrep", "-f", "-x", str(executable)], check=False
    )
    if completed.returncode == 0:
        raise InstallError("quit the installed AI Collab App before upgrading")
    if completed.returncode != 1:
        raise InstallError("could not determine whether the installed App is running")


def _repair_unsealed_bytecode(app: Path, state_root: Path) -> Path | None:
    """Quarantine only regenerated Python bytecode that invalidates the App seal.

    A normal embedded Host sets PYTHONDONTWRITEBYTECODE.  This narrow repair
    exists for an older installation (or a diagnostic invocation of its
    embedded Python) that already wrote ``__pycache__/*.pyc`` into the signed
    bundle.  The repair is accepted only when removing exactly the files that
    codesign identifies as additions makes the original App signature valid
    again.
    """

    verification = _run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            str(app),
        ],
        check=False,
    )
    if verification.returncode == 0:
        return None
    initial_failure = InstallError(
        verification.stdout.strip() or "installed App signature is invalid"
    )
    service = app / "Contents/Resources/HarnessService"
    if service.is_symlink() or not service.is_dir():
        raise initial_failure
    service_root = service.resolve(strict=True)
    expected_header = f"{app}: a sealed resource is missing or invalid"
    bytecode: list[Path] = []
    for line in verification.stdout.splitlines():
        if not line or line == expected_header:
            continue
        if not line.startswith("file added: "):
            raise initial_failure
        child = Path(line.removeprefix("file added: "))
        try:
            details = child.lstat()
            resolved = child.resolve(strict=True)
        except OSError as exc:
            raise initial_failure from exc
        if (
            not child.is_absolute()
            or stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or child.suffix != ".pyc"
            or child.parent.name != "__pycache__"
            or details.st_uid != os.getuid()
            or not resolved.is_relative_to(service_root)
        ):
            raise initial_failure
        bytecode.append(child)
    if not bytecode or len(bytecode) != len(set(bytecode)):
        raise initial_failure

    quarantine = _private_installation_directory(state_root) / (
        f"bytecode-quarantine-{uuid.uuid4().hex}"
    )
    quarantine.mkdir(mode=0o700)
    moved: list[tuple[Path, Path]] = []
    try:
        for child in sorted(bytecode):
            destination = quarantine / child.relative_to(app)
            destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.replace(child, destination)
            moved.append((child, destination))
        _run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
        )
    except BaseException:
        for child, destination in reversed(moved):
            child.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination, child)
        shutil.rmtree(quarantine, ignore_errors=True)
        raise initial_failure
    return quarantine


def _launch_app(app: Path) -> subprocess.Popen[bytes]:
    executable = app / "Contents/MacOS/AICollab"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise InstallError("installed App executable is unavailable")
    return subprocess.Popen(
        [str(executable)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_app(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def unregister(app: Path, timeout: float = 15.0) -> None:
    app = app.expanduser().absolute().resolve(strict=True)
    verify_candidate(app)
    _ensure_app_not_running(app)
    executable = app / "Contents/MacOS/AICollab"
    completed = subprocess.run(
        [str(executable), "--unregister-host-service"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise InstallError(
            completed.stderr.strip() or "Harness Host service could not be unregistered"
        )


def _health_check(app: Path, state_root: Path, expected_digest: str, timeout: float) -> dict[str, Any]:
    registration = state_root / "installation/service-registration.json"
    runtime = app / "Contents/Resources/HarnessService/runtime"
    python = runtime / "bin/python3"
    python_path = app / "Contents/Resources/HarnessService/python"
    runtime_details = runtime.stat()
    expected_runtime_identity = {"dev": runtime_details.st_dev, "ino": runtime_details.st_ino}
    deadline = time.monotonic() + timeout
    last_detail = "Host did not become ready"
    while time.monotonic() < deadline:
        try:
            receipt = json.loads(registration.read_text(encoding="utf-8"))
            if receipt.get("service_build_digest") != expected_digest:
                raise ValueError("service registration still identifies another build")
            registered_path = receipt.get("app_bundle_path")
            if (
                not isinstance(registered_path, str)
                or Path(registered_path).resolve() != app.resolve()
            ):
                raise ValueError("service registration points to another App bundle")
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONHOME": str(runtime),
                    "PYTHONPATH": str(python_path),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                }
            )
            completed = subprocess.run(
                [str(python), "-c", HEALTH_PROGRAM, str(state_root)],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
            )
            if completed.returncode == 0:
                value = json.loads(completed.stdout)
                if value.get("host_runtime_identity") != expected_runtime_identity:
                    raise ValueError(
                        "Stale Host runtime %r; bundle runtime %r. Run --unregister --target %r, then retry."
                        % (value.get("host_runtime_identity"), expected_runtime_identity, str(app)))
                if value.get("status") == "ready":
                    return value
            last_detail = (completed.stderr or completed.stdout).strip() or last_detail
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            last_detail = str(exc)
        time.sleep(0.25)
    raise InstallError(f"installed Host health check failed: {last_detail}")


def _private_installation_directory(state_root: Path) -> Path:
    directory = state_root / "installation"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = directory.stat()
    if directory.is_symlink() or details.st_uid != os.getuid():
        raise InstallError("installation state directory is unsafe")
    os.chmod(directory, 0o700)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise InstallError("installation state directory permissions differ")
    return directory


def install(candidate: Path, target: Path, state_root: Path, health_timeout: float,
            *, replace: tuple[Path, ...] = ()) -> dict[str, Any]:
    candidate = candidate.expanduser().absolute()
    target_input = target.expanduser().absolute()
    target = target_input.parent.resolve() / target_input.name
    state_root = state_root.expanduser().resolve()
    if target.suffix != ".app" or target == candidate:
        raise InstallError("target must be a different .app path")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.stat().st_uid != os.getuid():
        raise InstallError("installation directory is not owned by the current user")
    existing = target if target.exists() or target.is_symlink() else None
    if state_root != DEFAULT_STATE_ROOT.resolve():
        raise InstallError("SMAppService installation requires the standard current-user state root")
    if existing is not None:
        _ensure_app_not_running(target)
        _repair_unsealed_bytecode(target, state_root)
    identity = verify_candidate(candidate, existing=existing)
    nonce = uuid.uuid4().hex
    stage = target.parent / f".{target.stem}.stage-{nonce}.app"
    failed = target.parent / f".{target.stem}.failed-{nonce}.app"
    _copy_to_stage(candidate, stage)
    verify_candidate(stage, existing=existing)
    if existing is None:
        os.replace(stage, target)
    else:
        _atomic_swap(stage, target)
    app_process: subprocess.Popen[bytes] | None = None
    recovered = False
    try:
        app_process = _launch_app(target)
        health = _health_check(
            target,
            state_root,
            identity["service_build_digest"],
            health_timeout,
        )
    except BaseException as install_failure:
        if app_process is not None:
            _stop_app(app_process)
        if existing is None:
            cleanup_detail = ""
            try:
                unregister(target)
            except (InstallError, OSError, subprocess.TimeoutExpired) as cleanup_error:
                cleanup_detail = f"; service cleanup also failed: {cleanup_error}"
            raise InstallError(
                f"first installation failed; candidate preserved at {target}"
                f"{cleanup_detail}"
            ) from install_failure
        _atomic_swap(stage, target)
        recovery_process = _launch_app(target)
        try:
            previous = _metadata(target)
            health = _health_check(
                target,
                state_root,
                previous[SERVICE_BUILD_DIGEST_KEY],
                health_timeout,
            )
            recovered = True
        except BaseException as recovery_failure:
            raise InstallError(
                "upgrade failed and the restored App did not recover its Host; "
                f"failed candidate preserved at {stage}: {recovery_failure}"
            ) from install_failure
        finally:
            _stop_app(recovery_process)
        failed_path = stage
        try:
            os.replace(stage, failed)
            failed_path = failed
        except OSError:
            pass
        raise InstallError(
            f"upgrade health check failed; previous App and Host recovered; "
            f"failed candidate preserved at {failed_path}"
        ) from install_failure
    finally:
        if app_process is not None:
            _stop_app(app_process)
        if stage.exists() and existing is None:
            raise InstallError("staged App unexpectedly remained after installation")

    if existing is not None:
        archive = _private_installation_directory(state_root) / f"previous-{nonce}.app"
        os.replace(stage, archive)
        previous_path = str(archive)
    else:
        previous_path = None
    commands = _reconcile_command_result(target, state_root, replace)
    return {
        "status": "installed",
        "target": str(target),
        "service_build_digest": identity["service_build_digest"],
        "team_identifier": identity["team_identifier"],
        "host_generation": health["host_generation"],
        "previous_version": previous_path,
        "recovered": recovered,
        "commands": commands,
    }


def _reconcile_command_result(target: Path, state_root: Path,
                              replace: tuple[Path, ...]) -> dict[str, Any]:
    try:
        return reconcile_commands(target, state_root, replace=replace)
    except (CommandLinkError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return failure_result(exc)


def link_commands(target: Path, state_root: Path, *, replace: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Point ``~/.local/bin`` at the installed App without reinstalling it."""

    target = target.expanduser().resolve(strict=True)
    if target.suffix != ".app":
        raise InstallError("target must be an installed .app")
    verify_candidate(target)
    commands = _reconcile_command_result(target, state_root, replace)
    return {"status": "linked" if commands["status"] == "ready" else "commands_need_attention",
            "target": str(target), "commands": commands}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--candidate", type=Path)
    action.add_argument("--unregister", action="store_true")
    action.add_argument(
        "--link-commands",
        action="store_true",
        help="point ~/.local/bin PingAgent commands at the installed App",
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--health-timeout", type=float, default=20.0)
    parser.add_argument("--replace-command", type=Path, action="append", default=[],
                        help="preserve and replace this explicitly approved command file")
    arguments = parser.parse_args()
    if arguments.unregister and arguments.replace_command:
        parser.error("--replace-command cannot be combined with --unregister")
    try:
        if arguments.unregister:
            unregister(arguments.target)
            removed = remove_commands(DEFAULT_STATE_ROOT, app=arguments.target)
            result = {
                "status": "unregistered",
                "target": str(arguments.target),
                "pingagent_commands_removed": removed,
            }
        elif arguments.link_commands:
            result = link_commands(arguments.target, DEFAULT_STATE_ROOT,
                                   replace=tuple(arguments.replace_command))
        else:
            assert arguments.candidate is not None
            result = install(
                arguments.candidate,
                arguments.target,
                DEFAULT_STATE_ROOT,
                arguments.health_timeout,
                replace=tuple(arguments.replace_command),
            )
    except (InstallError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("commands", {}).get("status") in {"conflict", "error"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
