# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_ai_collab_app", ROOT / "scripts/install_ai_collab_app.py"
)
assert SPEC is not None and SPEC.loader is not None
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
BUILD_SPEC = importlib.util.spec_from_file_location(
    "build_ai_collab_app", ROOT / "scripts/build_ai_collab_app.py"
)
assert BUILD_SPEC is not None and BUILD_SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(BUILDER)


class _Process:
    def poll(self) -> int | None:
        return 0


def _app(path: Path, marker: str) -> Path:
    path.mkdir(parents=True)
    (path / "marker").write_text(marker, encoding="utf-8")
    return path


def test_atomic_swap_keeps_both_app_directories(tmp_path: Path) -> None:
    old = _app(tmp_path / "old.app", "old")
    new = _app(tmp_path / "new.app", "new")

    INSTALLER._atomic_swap(old, new)

    assert (old / "marker").read_text(encoding="utf-8") == "new"
    assert (new / "marker").read_text(encoding="utf-8") == "old"


def test_existing_app_quarantines_only_unsealed_python_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "AI Collab.app"
    cache = (
        app
        / "Contents/Resources/HarnessService/python/ai_collab/__pycache__"
    )
    cache.mkdir(parents=True)
    bytecode = cache / "host.cpython-312.pyc"
    bytecode.write_bytes(b"cache")
    calls = 0

    def verify(_argv: list[str], *, check: bool = True) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise INSTALLER.InstallError("a sealed resource is invalid")
        return object()

    monkeypatch.setattr(INSTALLER, "_run", verify)
    quarantine = INSTALLER._repair_unsealed_bytecode(app, tmp_path / "state")

    assert quarantine is not None
    assert not cache.exists()
    assert (
        quarantine
        / "Contents/Resources/HarnessService/python/ai_collab/__pycache__/host.cpython-312.pyc"
    ).read_bytes() == b"cache"
    assert calls == 2


def test_existing_app_restores_bytecode_when_signature_still_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "AI Collab.app"
    cache = app / "Contents/Resources/HarnessService/runtime/__pycache__"
    cache.mkdir(parents=True)
    bytecode = cache / "pathlib.cpython-312.pyc"
    bytecode.write_bytes(b"cache")

    def reject(_argv: list[str], *, check: bool = True) -> object:
        raise INSTALLER.InstallError("a signed resource differs")

    monkeypatch.setattr(INSTALLER, "_run", reject)
    with pytest.raises(INSTALLER.InstallError, match="signed resource differs"):
        INSTALLER._repair_unsealed_bytecode(app, tmp_path / "state")

    assert bytecode.read_bytes() == b"cache"
    assert not list((tmp_path / "state/installation").glob("bytecode-quarantine-*"))


def test_existing_app_does_not_quarantine_non_bytecode_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "AI Collab.app"
    cache = app / "Contents/Resources/HarnessService/python/__pycache__"
    cache.mkdir(parents=True)
    unexpected = cache / "operator-note.txt"
    unexpected.write_text("keep", encoding="utf-8")

    def reject(_argv: list[str], *, check: bool = True) -> object:
        raise INSTALLER.InstallError("a sealed resource is invalid")

    monkeypatch.setattr(INSTALLER, "_run", reject)
    with pytest.raises(INSTALLER.InstallError, match="sealed resource is invalid"):
        INSTALLER._repair_unsealed_bytecode(app, tmp_path / "state")

    assert unexpected.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "state/installation").exists()


def test_service_build_digest_tracks_bundle_inputs_but_not_info_plist(
    tmp_path: Path,
) -> None:
    app = tmp_path / "AI Collab.app"
    info = app / "Contents/Info.plist"
    service = app / "Contents/Resources/HarnessService/host.py"
    ui = app / "Contents/MacOS/AICollab"
    service.parent.mkdir(parents=True)
    ui.parent.mkdir(parents=True)
    info.write_text("first", encoding="utf-8")
    service.write_text("v1", encoding="utf-8")
    ui.write_text("ui-v1", encoding="utf-8")
    first = BUILDER._unsigned_bundle_digest(app)

    info.write_text("second", encoding="utf-8")
    ui.write_text("ui-v2", encoding="utf-8")
    assert BUILDER._unsigned_bundle_digest(app) == first

    service.write_text("v2", encoding="utf-8")
    assert BUILDER._unsigned_bundle_digest(app) != first


def test_upgrade_health_failure_restores_previous_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _app(tmp_path / "candidate.app", "new")
    target = _app(tmp_path / "install" / "AI Collab.app", "old")
    state_root = tmp_path / "state"
    monkeypatch.setattr(INSTALLER, "DEFAULT_STATE_ROOT", state_root)
    monkeypatch.setattr(
        INSTALLER,
        "verify_candidate",
        lambda _app, existing=None: {
            "bundle_id": INSTALLER.BUNDLE_ID,
            "service_build_digest": "b" * 64,
            "team_identifier": "TEAM",
        },
    )
    monkeypatch.setattr(
        INSTALLER,
        "_copy_to_stage",
        lambda source, destination: shutil.copytree(source, destination),
    )
    monkeypatch.setattr(INSTALLER, "_ensure_app_not_running", lambda _target: None)
    monkeypatch.setattr(
        INSTALLER,
        "_repair_unsealed_bytecode",
        lambda _target, _state_root: None,
    )
    monkeypatch.setattr(INSTALLER, "_launch_app", lambda _app: _Process())
    monkeypatch.setattr(INSTALLER, "_stop_app", lambda _process: None)
    monkeypatch.setattr(
        INSTALLER,
        "_metadata",
        lambda _app: {
            "CFBundleIdentifier": INSTALLER.BUNDLE_ID,
            INSTALLER.SERVICE_BUILD_DIGEST_KEY: "a" * 64,
        },
    )
    calls = iter(
        [
            INSTALLER.InstallError("new Host did not become ready"),
            {"status": "ready", "host_generation": 7},
        ]
    )

    def health(*_args: object, **_kwargs: object) -> dict[str, object]:
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(INSTALLER, "_health_check", health)

    with pytest.raises(INSTALLER.InstallError, match="previous App and Host recovered"):
        INSTALLER.install(candidate, target, state_root, 1.0)

    assert (target / "marker").read_text(encoding="utf-8") == "old"
    failed = list(target.parent.glob(".AI Collab.failed-*.app"))
    assert len(failed) == 1
    assert (failed[0] / "marker").read_text(encoding="utf-8") == "new"


def test_health_requires_registration_from_installed_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "AI Collab.app"
    registration = tmp_path / "state/installation/service-registration.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(
        '{"service_build_digest":"' + "a" * 64 + '",'
        '"app_bundle_path":"/another/AI Collab.app"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(INSTALLER.time, "sleep", lambda _seconds: None)

    with pytest.raises(INSTALLER.InstallError, match="another App bundle"):
        INSTALLER._health_check(app, tmp_path / "state", "a" * 64, 0.01)


def test_first_install_failure_unregisters_bad_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _app(tmp_path / "candidate.app", "new")
    target = tmp_path / "install/AI Collab.app"
    state_root = tmp_path / "state"
    monkeypatch.setattr(INSTALLER, "DEFAULT_STATE_ROOT", state_root)
    monkeypatch.setattr(
        INSTALLER,
        "verify_candidate",
        lambda _app, existing=None: {
            "bundle_id": INSTALLER.BUNDLE_ID,
            "service_build_digest": "b" * 64,
            "team_identifier": "TEAM",
        },
    )
    monkeypatch.setattr(
        INSTALLER,
        "_copy_to_stage",
        lambda source, destination: shutil.copytree(source, destination),
    )
    monkeypatch.setattr(INSTALLER, "_launch_app", lambda _app: _Process())
    monkeypatch.setattr(INSTALLER, "_stop_app", lambda _process: None)
    monkeypatch.setattr(
        INSTALLER,
        "_health_check",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            INSTALLER.InstallError("Host unavailable")
        ),
    )
    unregistered: list[Path] = []
    monkeypatch.setattr(INSTALLER, "unregister", unregistered.append)

    with pytest.raises(INSTALLER.InstallError, match="first installation failed"):
        INSTALLER.install(candidate, target, state_root, 1.0)

    assert unregistered == [target]
    assert (target / "marker").read_text(encoding="utf-8") == "new"
