#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-AtomGradient-Proprietary
# Copyright (c) 2026 AtomGradient. All rights reserved.
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司。保留所有权利。

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


def _signing_identity() -> str:
    output = _run(
        ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
        cwd=ROOT,
    )
    matches = re.findall(r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"', output, re.M)
    preferred = [value for value, name in matches if name.startswith("Apple Development:")]
    if not preferred:
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


def _sign_nested(app: Path, identity: str) -> None:
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
        _run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                str(path),
            ],
            cwd=ROOT,
        )
    _run(
        [
            "/usr/bin/codesign",
            "--force",
            "--deep",
            "--sign",
            identity,
            "--timestamp=none",
            str(app),
        ],
        cwd=ROOT,
    )
    _run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        cwd=ROOT,
    )


def build(output: Path, integration_root: Path, python_executable: Path) -> None:
    output = output.expanduser().resolve()
    integration_root = integration_root.expanduser().resolve(strict=True)
    python_executable = python_executable.expanduser().resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise SystemExit("output already exists; choose a fresh path")
    if output.suffix != ".app":
        raise SystemExit("output must end in .app")
    identity = _signing_identity()
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
        _run(
            [
                str(python_executable),
                str(ROOT / "scripts/build_harness_service_payload.py"),
                "--destination",
                str(service),
                "--integration-root",
                str(integration_root),
            ],
            cwd=ROOT,
        )
        info = app / "Contents/Info.plist"
        with info.open("rb") as stream:
            metadata = plistlib.load(stream)
        metadata["CFBundleShortVersionString"] = "0.1"
        metadata["CFBundleVersion"] = "1"
        metadata[SERVICE_BUILD_DIGEST_KEY] = _unsigned_bundle_digest(app)
        with info.open("wb") as stream:
            plistlib.dump(metadata, stream, fmt=plistlib.FMT_XML, sort_keys=True)
        _sign_nested(app, identity)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, output, symlinks=True)
        _run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(output)],
            cwd=ROOT,
        )
    print(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, default=Path(sys.executable))
    arguments = parser.parse_args()
    build(arguments.output, arguments.integration_root, arguments.python_executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
