#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Assemble the minimal relocatable Python payload embedded by AI Collab.app."""

from __future__ import annotations

import argparse
import compileall
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PRODUCT_FILES = (
    "ai_collab_runtime_profiles.json",
    "ai_collab_team_policies.json",
    "scripts/ai_collab_default_security_adapter.py",
    "scripts/ai_collab_iterm_adapter_lock.json",
    "scripts/ai_collab_macos_automation_preflight.py",
    "scripts/ai_collab_participant_driver.py",
    "scripts/ai_collab_project_adapter.py",
    "scripts/ai_collab_project_descriptor.py",
    "scripts/ai_collab_project_intent.py",
    "scripts/ai_collab_project_support.py",
    "scripts/ai_collab_repo_manifest.py",
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


# Install prefixes that must never be referenced by the shipped runtime; a
# reference would make the payload depend on the build machine's package
# manager and fail hardened-runtime library validation on end-user machines.
_FOREIGN_PREFIXES = ("/opt/homebrew/", "/usr/local/")
# The embedded interpreter is pinned so every build ships the same runtime
# regardless of which machine produced it.
EMBEDDED_PYTHON_VERSION = (3, 14)
# Executed by ``site`` on every interpreter start, including ``-I`` and
# sanitized environments, so no launch path can write into the signed bundle.
BYTECODE_GUARD_NAME = "aicollab-no-bytecode.pth"
PRECOMPILE_EXCLUDE = re.compile(r"/(test|tests|idlelib)(/|$)")
IMMUTABILITY_PROBE_IMPORTS = (
    "asyncio, ensurepip, hashlib, json, pathlib, plistlib, re, subprocess, "
    "urllib.request, uuid, venv"
)
# Every optimization level a launch may request looks for its own bytecode
# variant before ``site`` runs, so all of them ship.
PRECOMPILE_OPTIMIZATION_LEVELS = [0, 1, 2]
IMMUTABILITY_PROBE_FLAGS = (("-I",), ("-I", "-O"), ("-I", "-OO"))
# ``co_filename`` in shipped bytecode is relative to this name, never the
# build machine's output directory.
SHIPPED_PATH_PREFIX = "HarnessService"
_MACHO_MAGICS = (
    b"\xcf\xfa\xed\xfe",
    b"\xce\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)


def _is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in _MACHO_MAGICS
    except OSError:
        return False


def _macho_files(runtime: Path) -> list[Path]:
    return [
        path
        for path in sorted(runtime.rglob("*"))
        if path.is_file() and not path.is_symlink() and _is_macho(path)
    ]


def _install_id(path: Path) -> str | None:
    """LC_ID_DYLIB install name, or None for executables and bundles."""

    output = subprocess.run(
        ["/usr/bin/otool", "-D", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    ).stdout
    lines = [line.strip() for line in output.splitlines()[1:] if line.strip()]
    return lines[0] if lines else None


def _load_commands(path: Path) -> list[str]:
    """Dependency install names of one Mach-O (LC_LOAD_DYLIB, not its own id)."""

    output = subprocess.run(
        ["/usr/bin/otool", "-L", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    ).stdout
    names = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if line.endswith(")") and " (compatibility" in line:
            names.append(line.split(" (compatibility", 1)[0])
    identity = _install_id(path)
    if identity is not None and identity in names:
        names.remove(identity)
    return names


def _set_install_name(path: Path, old: str, new: str) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IWUSR)
    subprocess.run(
        ["/usr/bin/install_name_tool", "-change", old, new, str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    path.chmod(mode)


def _is_python_framework_binary(dependency: str) -> bool:
    return (
        "/Python.framework/Versions/" in dependency
        and dependency.endswith("/Python")
    )


def _relocate_runtime(runtime: Path) -> None:
    """Make the copied interpreter tree self-contained.

    The runtime is copied verbatim from the build interpreter, so its Mach-O
    files still reference absolute install paths (Homebrew and friends). Every
    foreign dylib is bundled into runtime/lib and every reference is rewritten
    to an @loader_path-relative one; the pass fails closed if any foreign
    reference survives.
    """

    lib_dir = runtime / "lib"
    framework_binary = runtime / "Python"
    bundled: dict[str, Path] = {}
    modified: set[Path] = set()
    queue = _macho_files(runtime)
    seen = {path for path in queue}
    while queue:
        macho = queue.pop()
        identity = _install_id(macho)
        if identity is not None and identity.startswith(_FOREIGN_PREFIXES):
            mode = macho.stat().st_mode
            macho.chmod(mode | stat.S_IWUSR)
            subprocess.run(
                [
                    "/usr/bin/install_name_tool",
                    "-id",
                    f"@rpath/{macho.name}",
                    str(macho),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            macho.chmod(mode)
            modified.add(macho)
        for dependency in _load_commands(macho):
            if not dependency.startswith(_FOREIGN_PREFIXES):
                continue
            if _is_python_framework_binary(dependency) and framework_binary.is_file():
                target = framework_binary
            else:
                if dependency not in bundled:
                    source = Path(dependency).resolve(strict=True)
                    local = lib_dir / source.name
                    if not local.exists():
                        shutil.copy2(source, local, follow_symlinks=True)
                        local.chmod(0o755)
                    bundled[dependency] = local
                    if local not in seen:
                        seen.add(local)
                        queue.append(local)
                target = bundled[dependency]
            relative = os.path.relpath(target, macho.parent)
            _set_install_name(macho, dependency, f"@loader_path/{relative}")
            modified.add(macho)
    for macho in sorted(modified):
        # install_name_tool invalidates the existing code signature and the
        # kernel refuses to execute unsigned arm64 code; restore an ad-hoc
        # signature (the App build re-signs with its real identity later).
        mode = macho.stat().st_mode
        macho.chmod(mode | stat.S_IWUSR)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", str(macho)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        macho.chmod(mode)
    remaining = [
        (path, name)
        for path in _macho_files(runtime)
        for name in (*_load_commands(path), _install_id(path) or "")
        if name.startswith(_FOREIGN_PREFIXES)
    ]
    if remaining:
        listing = "; ".join(f"{p.name} -> {d}" for p, d in remaining[:5])
        raise SystemExit(f"embedded runtime still references foreign libraries: {listing}")


def _embedded_lib(runtime: Path) -> Path:
    return runtime / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"


def _write_bytecode_guard(runtime: Path) -> Path:
    """Refuse bytecode writes on every interpreter start, ``-I`` included."""

    site_packages = _embedded_lib(runtime) / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    guard = site_packages / BYTECODE_GUARD_NAME
    guard.write_text("import sys; sys.dont_write_bytecode = True\n", encoding="utf-8")
    return guard


def _precompile_tree(root: Path, destination: Path) -> None:
    """Ship hash-validated bytecode so the interpreter never needs to write any.

    Modules imported before ``site`` runs (encodings, io, os, ...) are not
    covered by the guard; shipping their bytecode is what keeps the first
    interpreter start from writing into the bundle.  Unchecked-hash bytecode
    is used regardless of source mtimes, which copying changes.  All
    optimization levels ship because ``-O``/``-OO`` look for their own
    variant, and ``co_filename`` is recorded relative to the payload so the
    output is identical wherever it was built.
    """

    if not root.is_dir():
        return
    if not compileall.compile_dir(
        str(root),
        quiet=1,
        rx=PRECOMPILE_EXCLUDE,
        optimize=PRECOMPILE_OPTIMIZATION_LEVELS,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        stripdir=str(destination),
        prependdir=SHIPPED_PATH_PREFIX,
    ):
        raise SystemExit(f"embedded Python precompilation failed under {root}")


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            details = path.lstat()
            snapshot[str(path.relative_to(root))] = (details.st_size, details.st_mtime_ns)
    return snapshot


def _assert_embedded_python_leaves_payload_untouched(destination: Path) -> None:
    """Start the embedded interpreter the worst way and prove nothing changed."""

    executable = destination / "runtime/bin/python3"
    roots = [destination / "runtime", destination / "python", destination / "scripts"]
    before = {str(root): _tree_snapshot(root) for root in roots}
    for flags in IMMUTABILITY_PROBE_FLAGS:
        completed = subprocess.run(
            [str(executable), *flags, "-c", f"import {IMMUTABILITY_PROBE_IMPORTS}"],
            cwd=destination,
            env={},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=60,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "embedded Python immutability probe failed:\n"
                + completed.stdout.strip()
            )
    changed: list[str] = []
    for root in roots:
        after = _tree_snapshot(root)
        previous = before[str(root)]
        for key in sorted(set(previous) | set(after)):
            if previous.get(key) != after.get(key):
                changed.append(str(root.relative_to(destination) / key))
    if changed:
        raise SystemExit(
            "embedded Python wrote into the payload:\n" + "\n".join(changed[:40])
        )


def _assert_embedded_python_runs(destination: Path) -> None:
    runtime = destination / "runtime"
    python_root = destination / "python"
    executable = destination / "runtime/bin/python3"

    checks = [
        (
            [
                str(executable),
                "-I",
                "-c",
                "import sys; raise SystemExit(0 if sys.prefix else 1)",
            ],
            None,
        ),
        (
            [
                str(executable),
                "-c",
                "import ai_collab.service, yaml; import sys; "
                "raise SystemExit(0 if sys.prefix else 1)",
            ],
            {
                **os.environ,
                "PYTHONHOME": str(runtime),
                "PYTHONPATH": str(python_root),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
        ),
    ]
    for argv, env in checks:
        try:
            completed = subprocess.run(
                argv,
                cwd=destination,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            raise SystemExit(
                "embedded Python smoke test timed out:\n" + output.strip()
            ) from exc
        if completed.returncode != 0:
            raise SystemExit(
                "embedded Python smoke test failed:\n" + completed.stdout.strip()
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


def build(destination: Path, integration_root: Path | None) -> None:
    destination = destination.resolve()
    if sys.version_info[:2] != EMBEDDED_PYTHON_VERSION:
        pinned = ".".join(str(part) for part in EMBEDDED_PYTHON_VERSION)
        running = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise SystemExit(
            f"the embedded runtime is pinned to Python {pinned}; "
            f"build with that interpreter (running {running})"
        )
    if destination.exists():
        raise SystemExit("destination already exists; use a fresh build directory")
    if integration_root is not None:
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
    if integration_root is not None:
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
    if integration_root is not None:
        for relative in REQUIRED_INTEGRATION_FILES:
            _copy(integration_root / relative, destination / relative)

    ping_bin = PRODUCT_ROOT / "pingagent/bin"
    for command in ("ai-harness-transport", "ai-ping"):
        source = ping_bin / command
        if source.is_symlink() or not source.is_file():
            raise SystemExit(f"PingAgent command is unavailable: {command}")
        _copy(source, destination.parent / f"PingAgent/bin/{command}")

    # Without an integration payload the bundle points at the generic,
    # config-driven adapters that ship as product files, so a public build is
    # usable out of the box against any project that provides the descriptor,
    # manifest, and collaboration template files at its canonical root. An
    # integration build replaces both configs with the integration project's
    # own adapters.
    if integration_root is not None:
        project_adapter_config = {
            "adapter_id": "ai-collab-edgestudio-bundle-v1",
            "script": "scripts/ai_collab_edgestudio_adapter.py",
        }
        security_adapter_config = {
            "adapter_id": "edgestudio-security-adapter",
            "script": "scripts/ai_collab_security_adapter.py",
        }
    else:
        project_adapter_config = {
            "adapter_id": "ai-collab-project-adapter-v1",
            "script": "scripts/ai_collab_project_adapter.py",
        }
        security_adapter_config = {
            "adapter_id": "ai-collab-security-adapter",
            "script": "scripts/ai_collab_default_security_adapter.py",
        }
    embedded_configs = {
        "ai_collab_participant_driver.json": {
            "schema_version": 1,
            "adapter_id": "ai-collab-participant-driver",
            "command": [
                "runtime/bin/python3",
                "scripts/ai_collab_participant_driver.py",
            ],
            "working_directory": ".",
        },
        "ai_collab_harness_adapter.json": {
            "schema_version": 1,
            "adapter_id": project_adapter_config["adapter_id"],
            "command": [
                "runtime/bin/python3",
                project_adapter_config["script"],
            ],
            "idempotent_join_operations": ["destroy", "recover", "repair"],
            "progress_side_channel": "v1",
            "working_directory": ".",
        },
        "ai_collab_security_adapter.json": {
            "schema_version": 1,
            "adapter_id": security_adapter_config["adapter_id"],
            "command": [
                "runtime/bin/python3",
                security_adapter_config["script"],
            ],
            "working_directory": ".",
        },
    }
    for name, value in embedded_configs.items():
        (destination / name).write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    _relocate_runtime(destination / "runtime")
    executable = destination / "runtime/bin/python3"
    versioned_executable = destination / (
        f"runtime/bin/python{sys.version_info.major}.{sys.version_info.minor}"
    )
    if not versioned_executable.is_file():
        raise SystemExit("embedded Python executable is unavailable")
    if not executable.exists() and not executable.is_symlink():
        executable.symlink_to(versioned_executable.name)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _write_bytecode_guard(destination / "runtime")
    for root in (
        _embedded_lib(destination / "runtime"),
        python_root,
        destination / "scripts",
    ):
        _precompile_tree(root, destination)
    _assert_embedded_python_runs(destination)
    _assert_embedded_python_leaves_payload_untouched(destination)
    manifest = {
        "schema_version": 1,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "host_module": "ai_collab.service",
        "integration_embedded": integration_root is not None,
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
    if integration_root is None:
        _assert_no_integration_content(destination)


def _assert_no_integration_content(destination: Path) -> None:
    """Prove a public payload carries nothing from the integration project.

    A payload built without ``--integration-root`` is what ships to the
    public. Construction above already skips the integration files and points
    the adapter configs at the generic product adapters, but a future edit
    could silently regress either, so both properties are asserted from the
    finished tree rather than trusted from the code path that produced it.
    """
    unexpected = [
        relative
        for relative in REQUIRED_INTEGRATION_FILES
        if (destination / relative).exists() or (destination / relative).is_symlink()
    ]
    if unexpected:
        raise SystemExit(
            "public payload contains integration files: " + ", ".join(unexpected)
        )
    for name in (
        "ai_collab_harness_adapter.json",
        "ai_collab_participant_driver.json",
        "ai_collab_security_adapter.json",
        "ai_collab_runtime_profiles.json",
        "payload-manifest.json",
    ):
        text = (destination / name).read_text(encoding="utf-8").lower()
        for marker in ("edgestudio", "edge-studio", "dogfood"):
            if marker in text:
                raise SystemExit(
                    f"public payload leaks integration content: {name} contains {marker!r}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--integration-root", type=Path, default=None)
    arguments = parser.parse_args()
    build(arguments.destination, arguments.integration_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
