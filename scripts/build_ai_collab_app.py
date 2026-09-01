#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Build one signed AI Collab.app with an embedded real Harness Host."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "macos/AI-Collab"
APP_RELATIVE = Path("Build/Products/Release/AICollab.app")
SERVICE_BUILD_DIGEST_KEY = "AICollabServiceBuildDigest"


def _run(argv: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stdout)
    return completed.stdout


def _signing_identity(*, notarize: bool = False) -> str:
    output = _run(
        ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
        cwd=ROOT,
    )
    matches = re.findall(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"', output, re.M)
    prefix = "Developer ID Application:" if notarize else "Apple Development:"
    preferred = [value for value, name in matches if name.startswith(prefix)]
    if not preferred:
        if notarize:
            raise SystemExit(
                "notarized distribution requires a Developer ID Application identity"
            )
        raise SystemExit("a stable Apple Development signing identity is required")
    return preferred[0]


def _unsigned_bundle_digest(app: Path) -> str:
    """Identify all service-bearing bundle inputs before code signing."""

    digest = hashlib.sha256()
    service_roots = (
        app / "Contents/Library/LaunchAgents",
        app / "Contents/Library/LaunchServices",
        app / "Contents/Resources/HarnessService",
    )
    paths = (
        path
        for root in service_roots
        if root.exists()
        for path in (root, *root.rglob("*"))
    )
    for path in sorted(paths, key=lambda value: value.relative_to(app).as_posix()):
        relative = path.relative_to(app)
        encoded = relative.as_posix().encode("utf-8")
        details = path.lstat()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update((details.st_mode & 0o777).to_bytes(2, "big"))
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L" + len(target).to_bytes(4, "big") + target)
        elif path.is_file():
            digest.update(b"F")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


def _codesign(argv: list[str]) -> None:
    """Run codesign, retrying only transient timestamp-service outages.

    Hardened-runtime signing sends one request per Mach-O to Apple's
    timestamp service; a brief network or proxy hiccup must not fail the
    whole build. Any other codesign failure still fails closed immediately.
    """

    attempts = 3
    for attempt in range(1, attempts + 1):
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode == 0:
            return
        transient = "timestamp service is not available" in completed.stdout
        if not transient or attempt == attempts:
            raise SystemExit(completed.stdout)
        time.sleep(2 * attempt)


def _hardened_flags(entitlements: Path) -> list[str]:
    """codesign flags required by Apple notarization (hardened runtime)."""

    if not entitlements.is_file():
        raise SystemExit(f"entitlements file is missing: {entitlements}")
    return [
        "--timestamp",
        "--options",
        "runtime",
        "--entitlements",
        str(entitlements),
    ]


def _sign_nested(app: Path, identity: str, *, hardened: bool = False) -> None:
    flags = (
        _hardened_flags(APP_SOURCE / "AICollab.entitlements")
        if hardened
        else ["--timestamp=none"]
    )
    candidates: list[Path] = []
    for path in app.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        inspected = subprocess.run(
            ["/usr/bin/file", "-b", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if "Mach-O" in inspected.stdout:
            candidates.append(path)
    candidates.sort(key=lambda value: len(value.parts), reverse=True)
    for path in candidates:
        _codesign(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                identity,
                *flags,
                str(path),
            ]
        )
    _codesign(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            identity,
            *flags,
            str(app),
        ]
    )
    _run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        cwd=ROOT,
    )


def _notarize(target: Path, keychain_profile: str) -> None:
    """Submit signed code to the Apple notary service and staple the ticket.

    A .app bundle is zipped for upload; a .dmg is submitted as-is. Gatekeeper
    assesses the disk image itself on download, so the image needs its own
    notarization on top of the bundle inside it.
    """

    with tempfile.TemporaryDirectory(prefix="ai-collab-notarize.") as temporary:
        if target.suffix == ".dmg":
            upload = target
        else:
            upload = Path(temporary) / f"{target.stem}-notarize.zip"
            _run(
                [
                    "/usr/bin/ditto",
                    "-c",
                    "-k",
                    "--keepParent",
                    str(target),
                    str(upload),
                ],
                cwd=ROOT,
            )
        _run(
            [
                "/usr/bin/xcrun",
                "notarytool",
                "submit",
                str(upload),
                "--keychain-profile",
                keychain_profile,
                "--wait",
                "--timeout",
                "30m",
            ],
            cwd=ROOT,
        )
    _run(["/usr/bin/xcrun", "stapler", "staple", str(target)], cwd=ROOT)
    _run(["/usr/bin/xcrun", "stapler", "validate", str(target)], cwd=ROOT)


def _selected_interpreter(python_executable: Path) -> Path:
    """Normalise the build interpreter without leaving its environment.

    Only the containing directory is resolved. Resolving the executable itself
    would follow a virtual environment's ``bin/python`` symlink out to the base
    interpreter, whose ``site-packages`` does not carry the environment's
    dependencies, so the payload would be built against the wrong packages.
    """
    candidate = python_executable.expanduser()
    candidate = candidate.parent.resolve(strict=True) / candidate.name
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise SystemExit("the selected Python executable is unavailable")
    return candidate


def build(
    output: Path,
    integration_root: Path | None,
    python_executable: Path,
    disk_image: Path | None,
    *,
    notarize: bool = False,
    keychain_profile: str = "AICollab",
    app_version: str = "0.1",
    build_number: str = "1",
) -> None:
    output = output.expanduser().resolve()
    if integration_root is not None:
        integration_root = integration_root.expanduser().resolve(strict=True)
    python_executable = _selected_interpreter(python_executable)
    if output.exists() or output.is_symlink():
        raise SystemExit("output already exists; choose a fresh path")
    if output.suffix != ".app":
        raise SystemExit("output must end in .app")
    if disk_image is not None:
        disk_image = disk_image.expanduser().resolve()
        if disk_image.exists() or disk_image.is_symlink():
            raise SystemExit("disk image already exists; choose a fresh path")
        if disk_image.suffix != ".dmg":
            raise SystemExit("disk image must end in .dmg")
    identity = _signing_identity(notarize=notarize)
    with tempfile.TemporaryDirectory(prefix="ai-collab-app-build.") as temporary:
        derived_data = Path(temporary) / "DerivedData"
        _run(["xcodegen", "generate"], cwd=APP_SOURCE)
        _run(
            [
                "xcodebuild",
                "-project",
                "AICollab.xcodeproj",
                "-scheme",
                "AICollab",
                "-configuration",
                "Release",
                "-derivedDataPath",
                str(derived_data),
                "CODE_SIGNING_ALLOWED=NO",
                "build",
            ],
            cwd=APP_SOURCE,
        )
        app = derived_data / APP_RELATIVE
        if not app.is_dir():
            raise SystemExit("Xcode did not produce AI Collab.app")
        service = app / "Contents/Resources/HarnessService"
        payload_argv = [
            str(python_executable),
            str(ROOT / "scripts/build_harness_service_payload.py"),
            "--destination",
            str(service),
        ]
        if integration_root is not None:
            payload_argv += ["--integration-root", str(integration_root)]
        _run(payload_argv, cwd=ROOT)
        info = app / "Contents/Info.plist"
        with info.open("rb") as stream:
            metadata = plistlib.load(stream)
        metadata["CFBundleShortVersionString"] = app_version
        metadata["CFBundleVersion"] = build_number
        metadata[SERVICE_BUILD_DIGEST_KEY] = _unsigned_bundle_digest(app)
        with info.open("wb") as stream:
            plistlib.dump(metadata, stream, fmt=plistlib.FMT_XML, sort_keys=True)
        _sign_nested(app, identity, hardened=notarize)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, output, symlinks=True)
        _run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(output)],
            cwd=ROOT,
        )
    if notarize:
        _notarize(output, keychain_profile)
    print(output)
    if disk_image is not None:
        disk_image.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ai-collab-dmg-") as staging:
            dmg_root = Path(staging)
            _run(["/usr/bin/ditto", str(output), str(dmg_root / "AICollab.app")], cwd=ROOT)
            (dmg_root / "Applications").symlink_to("/Applications")
            dmg_argv = ["/usr/bin/hdiutil", "create", "-volname", "AI Collab",
                        "-srcfolder", str(dmg_root), "-format", "UDZO", str(disk_image)]
            _run(dmg_argv, cwd=ROOT)
        if notarize:
            _codesign(
                [
                    "/usr/bin/codesign",
                    "--force",
                    "--sign",
                    identity,
                    "--timestamp",
                    str(disk_image),
                ]
            )
            _notarize(disk_image, keychain_profile)
        print(disk_image)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, default=None)
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    parser.add_argument("--dmg", type=Path, default=None)
    parser.add_argument(
        "--notarize",
        action="store_true",
        help="sign with Developer ID + hardened runtime, then notarize and staple",
    )
    parser.add_argument(
        "--keychain-profile",
        default="AICollab",
        help="notarytool keychain profile created via store-credentials",
    )
    parser.add_argument(
        "--app-version",
        default="0.1",
        help="CFBundleShortVersionString; keep in sync with the release tag",
    )
    parser.add_argument(
        "--build-number",
        default="1",
        help="CFBundleVersion; increment for every shipped build",
    )
    arguments = parser.parse_args()
    build(
        arguments.output,
        arguments.integration_root,
        arguments.python_executable,
        arguments.dmg,
        notarize=arguments.notarize,
        keychain_profile=arguments.keychain_profile,
        app_version=arguments.app_version,
        build_number=arguments.build_number,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
