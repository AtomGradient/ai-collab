# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
from __future__ import annotations

import importlib.util
import json
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
                executable.write_text("embedded runtime\n", encoding="utf-8")
        else:
            target.write_text("embedded file\n", encoding="utf-8")

    monkeypatch.setattr(module, "_copy", fake_copy)
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
                executable.write_text("embedded runtime\n", encoding="utf-8")
        else:
            target.write_text("embedded file\n", encoding="utf-8")

    monkeypatch.setattr(module, "_copy", fake_copy)
    return copied


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
