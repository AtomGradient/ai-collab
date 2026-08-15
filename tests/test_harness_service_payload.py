from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


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
