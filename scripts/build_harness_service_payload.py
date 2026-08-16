#!/usr/bin/env python3
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Assemble the minimal relocatable Python payload embedded by AI Collab.app."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import sysconfig
from pathlib import Path


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PRODUCT_FILES = (
    "ai_collab_runtime_profiles.json",
    "ai_collab_team_policies.json",
    "scripts/ai_collab_iterm_adapter_lock.json",
    "scripts/ai_collab_macos_automation_preflight.py",
    "scripts/ai_collab_participant_driver.py",
    "scripts/ai_collab_window_topology_screens.swift",
)
REQUIRED_INTEGRATION_FILES = (
    "scripts/ai_collab_bootstrap_evidence.py",
    "scripts/ai_collab_edgestudio_adapter.py",
    "scripts/ai_collab_security_adapter.py",
    "scripts/validate_ai_collab_project_descriptor.py",
    "scripts/validate_ai_collab_repo_manifest.py",
)
# Third-party packages vendored into the embedded interpreter. This must cover
# every runtime dependency declared in pyproject.toml that product code imports,
# including from ai_collab.cli, which the embedded CLI entry point loads.
VENDORED_SITE_PACKAGES = (
    "yaml",
    "platformdirs",
)


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def build(destination: Path, integration_root: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit("destination already exists; use a fresh build directory")
    integration_root = integration_root.resolve(strict=True)
    runtime_source = Path(sys.base_prefix).resolve(strict=True)
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    site_packages = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    if not runtime_source.is_dir() or not stdlib.is_relative_to(runtime_source):
        raise SystemExit("the selected Python runtime is not relocatable as one tree")
    for relative in REQUIRED_PRODUCT_FILES:
        source = PRODUCT_ROOT / relative
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"AI Collab payload is missing: {relative}")
    for relative in REQUIRED_INTEGRATION_FILES:
        source = integration_root / relative
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"integration payload is missing: {relative}")

    destination.mkdir(parents=True, mode=0o755)
    _copy(runtime_source, destination / "runtime")
    python_root = destination / "python"
    _copy(PRODUCT_ROOT / "src/ai_collab", python_root / "ai_collab")
    embedded_site_packages = (
        destination
        / "runtime"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if embedded_site_packages.is_symlink():
        embedded_site_packages.unlink()
    elif embedded_site_packages.exists() and not embedded_site_packages.is_dir():
        raise SystemExit("embedded Python site-packages path is not a directory")
    embedded_site_packages.mkdir(parents=True, exist_ok=True)
    for package in VENDORED_SITE_PACKAGES:
        source = site_packages / package
        if not source.is_dir():
            raise SystemExit(
                f"the selected Python runtime is missing a vendored dependency: {package}"
            )
        _copy(source, embedded_site_packages / package)
    for relative in REQUIRED_PRODUCT_FILES:
        _copy(PRODUCT_ROOT / relative, destination / relative)
    for relative in REQUIRED_INTEGRATION_FILES:
        _copy(integration_root / relative, destination / relative)

    ping_bin = PRODUCT_ROOT / "pingagent/bin"
    for command in ("ai-harness-transport", "ai-ping"):
        source = ping_bin / command
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"PingAgent command is unavailable: {command}")
        _copy(source, destination.parent / f"PingAgent/bin/{command}")

    embedded_configs = {
        "ai_collab_harness_adapter.json": {
            "schema_version": 1,
            "adapter_id": "ai-collab-edgestudio-bundle-v1",
            "command": [
                "runtime/bin/python3",
                "scripts/ai_collab_edgestudio_adapter.py",
            ],
            "working_directory": ".",
        },
        "ai_collab_participant_driver.json": {
            "schema_version": 1,
            "adapter_id": "ai-collab-participant-driver",
            "command": [
                "runtime/bin/python3",
                "scripts/ai_collab_participant_driver.py",
            ],
            "working_directory": ".",
        },
        "ai_collab_security_adapter.json": {
            "schema_version": 1,
            "adapter_id": "edgestudio-security-adapter",
            "command": [
                "runtime/bin/python3",
                "scripts/ai_collab_security_adapter.py",
            ],
            "working_directory": ".",
        },
    }
    for name, value in embedded_configs.items():
        (destination / name).write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    executable = destination / "runtime/bin/python3"
    versioned_executable = destination / (
        f"runtime/bin/python{sys.version_info.major}.{sys.version_info.minor}"
    )
    if not versioned_executable.is_file():
        raise SystemExit("embedded Python executable is unavailable")
    if not executable.exists() and not executable.is_symlink():
        executable.symlink_to(versioned_executable.name)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    manifest = {
        "schema_version": 1,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "host_module": "ai_collab.service",
        "project_adapter_id": "ai-collab-edgestudio-bundle-v1",
        "participant_driver_id": "ai-collab-participant-driver",
        "security_adapter_id": "edgestudio-security-adapter",
        "source_paths_embedded": False,
    }
    (destination / "payload-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    for root, directories, files in os.walk(destination):
        os.chmod(root, 0o755)
        for name in directories:
            os.chmod(Path(root) / name, 0o755)
        for name in files:
            path = Path(root) / name
            mode = path.stat().st_mode
            os.chmod(path, 0o755 if mode & stat.S_IXUSR else 0o644)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.destination, arguments.integration_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
