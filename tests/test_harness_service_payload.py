# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_harness_service_payload.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_harness_service_payload", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_embedded_payload_copies_pingagent_client_and_transport(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    destination = tmp_path / "bundle" / "HarnessService"
    integration_root = tmp_path / "integration"
    for relative in module.REQUIRED_INTEGRATION_FILES:
        source = integration_root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("integration file\n", encoding="utf-8")
    copied: list[tuple[Path, Path]] = []

    def fake_copy(source: Path, target: Path) -> None:
        copied.append((Path(source), Path(target)))
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            if target.name == "runtime":
                executable = target / (
                    f"bin/python{module.sys.version_info.major}.{module.sys.version_info.minor}"
                )
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        else:
            target.write_text("embedded file\n", encoding="utf-8")

    monkeypatch.setattr(module, "_copy", fake_copy)
    monkeypatch.setattr(module, "_precompile_tree", lambda root: None)
    module.build(destination, integration_root)

    targets = {target.relative_to(destination.parent) for _, target in copied}
    assert Path("PingAgent/bin/ai-harness-transport") in targets
    assert Path("PingAgent/bin/ai-ping") in targets
    assert Path("HarnessService/ai_collab_team_policies.json") in targets
    assert Path("HarnessService/scripts/ai_collab_macos_automation_preflight.py") in targets
    assert Path("HarnessService/python/ai_collab") in targets

    # Every declared runtime dependency must land in the embedded interpreter,
    # otherwise ai_collab.cli fails at import time inside the App payload.
    vendored = {
        target.name for _, target in copied if "site-packages" in target.parts
    }
    assert vendored == set(module.VENDORED_SITE_PACKAGES)
    assert "platformdirs" in vendored


def _install_fake_copy(module: Any, monkeypatch: Any) -> list[tuple[Path, Path]]:
    copied: list[tuple[Path, Path]] = []

    def fake_copy(source: Path, target: Path) -> None:
        copied.append((Path(source), Path(target)))
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            if target.name == "runtime":
                executable = target / (
                    f"bin/python{module.sys.version_info.major}.{module.sys.version_info.minor}"
                )
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        else:
            target.write_text("embedded file\n", encoding="utf-8")

    monkeypatch.setattr(module, "_copy", fake_copy)
    monkeypatch.setattr(module, "_precompile_tree", lambda root: None)
    return copied


def test_python_framework_binary_detection_is_version_independent() -> None:
    module = _module()

    assert module._is_python_framework_binary(  # noqa: SLF001
        "/opt/homebrew/Frameworks/Python.framework/Versions/3.14/Python"
    )
    assert module._is_python_framework_binary(  # noqa: SLF001
        "/Library/Frameworks/Python.framework/Versions/3.11/Python"
    )
    assert not module._is_python_framework_binary(  # noqa: SLF001
        "/opt/homebrew/lib/Python"
    )


def test_embedded_python_smoke_test_fails_closed(tmp_path: Path) -> None:
    module = _module()
    executable = tmp_path / "HarnessService/runtime/bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(SystemExit, match="embedded Python smoke test failed"):
        module._assert_embedded_python_runs(tmp_path / "HarnessService")  # noqa: SLF001


def test_embedded_python_smoke_test_uses_host_agent_environment(
    tmp_path: Path,
) -> None:
    module = _module()
    service = tmp_path / "HarnessService"
    executable = service / "runtime/bin/python3"
    log = tmp_path / "smoke-calls.txt"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        "#!/bin/sh\n"
        f"printf '%s|%s|%s|%s|%s\\n' \"$PYTHONHOME\" \"$PYTHONPATH\" "
        f"\"$PYTHONDONTWRITEBYTECODE\" \"$PYTHONNOUSERSITE\" \"$*\" >> {log}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    module._assert_embedded_python_runs(service)  # noqa: SLF001

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("|-I -c import sys; raise SystemExit(0 if sys.prefix else 1)")
    expected = (
        f"{service / 'runtime'}|{service / 'python'}|1|1|-c import ai_collab.service, "
        "yaml; import sys; raise SystemExit(0 if sys.prefix else 1)"
    )
    assert lines[1] == expected


def test_public_payload_embeds_generic_adapters_and_no_integration_content(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    destination = tmp_path / "bundle" / "HarnessService"
    copied = _install_fake_copy(module, monkeypatch)
    module.build(destination, None)

    targets = {target.relative_to(destination.parent) for _, target in copied}
    assert Path("HarnessService/scripts/ai_collab_project_adapter.py") in targets
    assert (
        Path("HarnessService/scripts/ai_collab_default_security_adapter.py")
        in targets
    )
    for relative in module.REQUIRED_INTEGRATION_FILES:
        assert Path("HarnessService") / relative not in targets
        assert not (destination / relative).exists()

    adapter = json.loads(
        (destination / "ai_collab_harness_adapter.json").read_text(encoding="utf-8")
    )
    assert adapter == {
        "adapter_id": "ai-collab-project-adapter-v1",
        "command": [
            "runtime/bin/python3",
            "scripts/ai_collab_project_adapter.py",
        ],
        "idempotent_join_operations": ["destroy", "recover", "repair"],
        "progress_side_channel": "v1",
        "schema_version": 1,
        "working_directory": ".",
    }
    security = json.loads(
        (destination / "ai_collab_security_adapter.json").read_text(encoding="utf-8")
    )
    assert security["adapter_id"] == "ai-collab-security-adapter"
    assert security["command"][1] == "scripts/ai_collab_default_security_adapter.py"
    manifest = json.loads(
        (destination / "payload-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["integration_embedded"] is False


def test_integration_payload_embeds_the_integration_adapters(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    destination = tmp_path / "bundle" / "HarnessService"
    integration_root = tmp_path / "integration"
    for relative in module.REQUIRED_INTEGRATION_FILES:
        source = integration_root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("integration file\n", encoding="utf-8")
    _install_fake_copy(module, monkeypatch)
    module.build(destination, integration_root)

    adapter = json.loads(
        (destination / "ai_collab_harness_adapter.json").read_text(encoding="utf-8")
    )
    assert adapter == {
        "adapter_id": "ai-collab-edgestudio-bundle-v1",
        "command": [
            "runtime/bin/python3",
            "scripts/ai_collab_edgestudio_adapter.py",
        ],
        "idempotent_join_operations": ["destroy", "recover", "repair"],
        "progress_side_channel": "v1",
        "schema_version": 1,
        "working_directory": ".",
    }
    security = json.loads(
        (destination / "ai_collab_security_adapter.json").read_text(encoding="utf-8")
    )
    assert security["adapter_id"] == "edgestudio-security-adapter"
    manifest = json.loads(
        (destination / "payload-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["integration_embedded"] is True


def test_public_leak_assertion_rejects_planted_integration_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    destination = tmp_path / "bundle" / "HarnessService"
    _install_fake_copy(module, monkeypatch)
    module.build(destination, None)

    planted = destination / "scripts" / "ai_collab_edgestudio_adapter.py"
    planted.write_text("leaked\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        module._assert_no_integration_content(destination)


def test_public_leak_assertion_rejects_internal_words_in_shipped_files(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    destination = tmp_path / "bundle" / "HarnessService"
    _install_fake_copy(module, monkeypatch)
    module.build(destination, None)

    profiles = destination / "ai_collab_runtime_profiles.json"
    profiles.write_text(
        '{"working_directory": "bundle/EdgeStudio"}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        module._assert_no_integration_content(destination)


def test_bytecode_guard_blocks_writes_under_isolated_mode_with_empty_environment(
    tmp_path: Path,
) -> None:
    module = _module()
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(runtime)
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir()
    (probe_dir / "probe_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    argv = [
        str(runtime / "bin" / "python"),
        "-I",
        "-c",
        f"import sys; sys.path.insert(0, {str(probe_dir)!r}); "
        "import probe_module; print(sys.dont_write_bytecode, probe_module.VALUE)",
    ]

    # Control: without the guard an isolated start with an empty environment
    # writes bytecode next to the module.
    control = subprocess.run(argv, env={}, capture_output=True, text=True, check=False)
    assert control.returncode == 0, control.stderr
    assert control.stdout.split() == ["False", "1"]
    assert (probe_dir / "__pycache__").is_dir()
    for path in (probe_dir / "__pycache__").iterdir():
        path.unlink()
    (probe_dir / "__pycache__").rmdir()

    guard = module._write_bytecode_guard(runtime)  # noqa: SLF001
    assert guard.name == module.BYTECODE_GUARD_NAME
    assert guard.parent.name == "site-packages"
    guarded = subprocess.run(argv, env={}, capture_output=True, text=True, check=False)
    assert guarded.returncode == 0, guarded.stderr
    assert guarded.stdout.split() == ["True", "1"]
    assert not (probe_dir / "__pycache__").exists()


def test_precompiled_bytecode_is_unchecked_hash_and_skips_test_trees(
    tmp_path: Path,
) -> None:
    module = _module()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "t.py").write_text("Y = 2\n", encoding="utf-8")

    module._precompile_tree(tmp_path)  # noqa: SLF001

    pyc = tmp_path / "pkg" / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc"
    assert pyc.is_file()
    flags = int.from_bytes(pyc.read_bytes()[4:8], "little")
    assert flags == 0b01  # hash-based, source not checked at import time
    assert not (tmp_path / "test" / "__pycache__").exists()
    module._precompile_tree(tmp_path / "absent")  # noqa: SLF001


def test_build_requires_the_pinned_embedded_python(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = _module()
    monkeypatch.setattr(module, "EMBEDDED_PYTHON_VERSION", (3, 99))
    with pytest.raises(SystemExit, match="pinned to Python 3.99"):
        module.build(tmp_path / "HarnessService", None)


def test_immutability_probe_runs_isolated_with_an_empty_environment(
    tmp_path: Path,
) -> None:
    module = _module()
    service = tmp_path / "HarnessService"
    executable = service / "runtime/bin/python3"
    executable.parent.mkdir(parents=True)
    (service / "python").mkdir()
    (service / "scripts").mkdir()
    log = tmp_path / "probe-calls.txt"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf '%s|%s|%s\\n' \"${{PYTHONHOME:-unset}}\" \"${{PYTHONDONTWRITEBYTECODE:-unset}}\" \"$*\" >> {log}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    module._assert_embedded_python_leaves_payload_untouched(service)  # noqa: SLF001

    assert log.read_text(encoding="utf-8").splitlines() == [
        f"unset|unset|-I -c import {module.IMMUTABILITY_PROBE_IMPORTS}"
    ]


def test_immutability_probe_fails_closed_when_the_interpreter_writes(
    tmp_path: Path,
) -> None:
    module = _module()
    service = tmp_path / "HarnessService"
    executable = service / "runtime/bin/python3"
    executable.parent.mkdir(parents=True)
    (service / "python").mkdir()
    (service / "scripts").mkdir()
    executable.write_text(
        "#!/bin/sh\n"
        "mkdir -p \"$(dirname \"$0\")/../lib/__pycache__\"\n"
        "printf 'x' > \"$(dirname \"$0\")/../lib/__pycache__/stray.pyc\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    with pytest.raises(SystemExit, match="wrote into the payload"):
        module._assert_embedded_python_leaves_payload_untouched(service)  # noqa: SLF001
