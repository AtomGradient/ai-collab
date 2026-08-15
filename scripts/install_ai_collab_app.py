#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-AtomGradient-Proprietary
# Copyright (c) 2026 AtomGradient. All rights reserved.
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司。保留所有权利。

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
import time
import uuid
from pathlib import Path
from typing import Any


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
    bundle.  The repair is accepted only when removing exactly those caches
    makes the original App signature valid again.
    """

    try:
        _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
        return None
    except InstallError as initial_failure:
        service = app / "Contents/Resources/HarnessService"
        if service.is_symlink() or not service.is_dir():
            raise initial_failure
        service_root = service.resolve(strict=True)
        caches = sorted(service.rglob("__pycache__"), key=lambda path: len(path.parts))
        if not caches:
            raise initial_failure
        for cache in caches:
            if (
                cache.is_symlink()
                or not cache.is_dir()
                or cache.stat().st_uid != os.getuid()
                or not cache.resolve(strict=True).is_relative_to(service_root)
            ):
                raise initial_failure
            for child in cache.iterdir():
                if (
                    child.is_symlink()
                    or not child.is_file()
                    or child.suffix != ".pyc"
                    or child.stat().st_uid != os.getuid()
                ):
                    raise initial_failure

        quarantine = _private_installation_directory(state_root) / (
            f"bytecode-quarantine-{uuid.uuid4().hex}"
        )
        quarantine.mkdir(mode=0o700)
        moved: list[tuple[Path, Path]] = []
        try:
            for cache in caches:
                destination = quarantine / cache.relative_to(app)
                destination.parent.mkdir(parents=True, mode=0o700)
                os.replace(cache, destination)
                moved.append((cache, destination))
            _run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
            )
        except BaseException:
            for cache, destination in reversed(moved):
                cache.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, cache)
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


def install(candidate: Path, target: Path, state_root: Path, health_timeout: float) -> dict[str, Any]:
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
    return {
        "status": "installed",
        "target": str(target),
        "service_build_digest": identity["service_build_digest"],
        "team_identifier": identity["team_identifier"],
        "host_generation": health["host_generation"],
        "previous_version": previous_path,
        "recovered": recovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--candidate", type=Path)
    action.add_argument("--unregister", action="store_true")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--health-timeout", type=float, default=20.0)
    arguments = parser.parse_args()
    try:
        if arguments.unregister:
            unregister(arguments.target)
            result = {"status": "unregistered", "target": str(arguments.target)}
        else:
            assert arguments.candidate is not None
            result = install(
                arguments.candidate,
                arguments.target,
                DEFAULT_STATE_ROOT,
                arguments.health_timeout,
            )
    except (InstallError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
