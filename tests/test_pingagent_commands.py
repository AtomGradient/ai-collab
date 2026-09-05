# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import ai_collab.pingagent_commands as commands

VERIFY_PRODUCT = commands.verify_product_bundle


def fake_app(root: Path) -> Path:
    app = root / "AI Collab.app"
    source = app / commands.PINGAGENT_BIN_RELATIVE
    source.mkdir(parents=True)
    for name in commands.PINGAGENT_COMMANDS:
        path = source / name
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": commands.PRODUCT_BUNDLE_IDENTIFIER,
    }))
    return app.resolve()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.setattr(commands, "verify_product_bundle", lambda app: {"bundle": "verified"})
    return fake_app(tmp_path), tmp_path / "state", tmp_path / ".local/bin"


def entries(directory: Path) -> dict:
    return {path.name: (os.readlink(path) if path.is_symlink() else path.read_bytes(),
                        path.lstat().st_ino, stat.S_IMODE(path.lstat().st_mode))
            for path in directory.iterdir()}


def test_fresh_status_is_read_only_and_reconcile_is_idempotent(setup) -> None:
    app, state, directory = setup
    assert commands.command_status(app, state, directory)["status"] == "needs_repair"
    assert not state.exists() and not directory.exists()
    result = commands.reconcile_commands(app, state, directory)
    assert result["status"] == "ready"
    assert result["changed"] == list(commands.PINGAGENT_COMMANDS)
    before = entries(directory)
    receipt = state / "installation" / commands.RECEIPT_NAME
    receipt_stat = receipt.stat()
    assert commands.reconcile_commands(app, state, directory)["changed"] == []
    assert entries(directory) == before
    assert receipt.stat() == receipt_stat


def test_headerless_scripts_need_explicit_replace_and_originals_are_preserved(setup) -> None:
    app, state, directory = setup
    directory.mkdir(parents=True)
    originals = {}
    for name in ("ai-ping", "ai-pane-register", "ai-pane-unregister", "ai-collab-watch"):
        path = directory / name
        path.write_text(f"#!/usr/bin/env bash\n# {name}\necho legacy\n")
        path.chmod(0o700)
        originals[name] = (path.read_bytes(), path.stat().st_ino)
    other = directory / "ai-watch-service"
    other.write_text("unrelated")
    before = entries(directory)
    result = commands.reconcile_commands(app, state, directory)
    assert result["status"] == "conflict"
    assert {item["name"] for item in result["conflicts"]} == set(originals)
    assert entries(directory) == before
    result = commands.reconcile_commands(app, state, directory,
        replace=[directory / name for name in originals])
    assert result["status"] == "ready"
    backup = Path(result["backup_directory"])
    for name, (body, inode) in originals.items():
        assert (backup / name).read_bytes() == body
        assert (backup / name).stat().st_ino == inode
        assert stat.S_IMODE((backup / name).stat().st_mode) == 0o700
    assert (other.read_bytes(), other.lstat().st_ino, stat.S_IMODE(other.stat().st_mode)) == before[other.name]
    assert sorted(path.name for path in backup.iterdir()) == sorted(originals)


@pytest.mark.parametrize("kind", ["foreign-app", "checkout", "dangling", "header"])
def test_unrecorded_ownership_is_never_guessed(setup, tmp_path, kind) -> None:
    app, state, directory = setup
    directory.mkdir(parents=True)
    path = directory / "ai-ping"
    if kind == "header":
        path.write_text("#!/bin/sh\n# Copyright \u00a9 2026 AtomGradient\n# ai-ping\necho custom\n")
    else:
        parent = tmp_path / ("Other.app/Contents/Resources/PingAgent/bin" if kind == "foreign-app" else "PingAgent/bin")
        if kind != "dangling":
            parent.mkdir(parents=True)
            for name in ("ai-ping", "ai-harness-transport"):
                (parent / name).write_text("custom")
        path.symlink_to(parent / "ai-ping")
    before = entries(directory)
    assert commands.reconcile_commands(app, state, directory)["status"] == "conflict"
    assert entries(directory) == before


def test_recorded_upgrade_and_verified_current_target_are_migratable(setup, tmp_path) -> None:
    app, state, directory = setup
    commands.reconcile_commands(app, state, directory)
    new_app = fake_app(tmp_path / "new")
    shutil.rmtree(app)
    assert commands.reconcile_commands(new_app, state, directory)["status"] == "ready"
    (state / "installation" / commands.RECEIPT_NAME).unlink()
    before = entries(directory)
    assert commands.reconcile_commands(new_app, state, directory)["changed"] == []
    assert entries(directory) == before


@pytest.mark.parametrize("bad", ["missing", "malformed", "mode", "target"])
def test_invalid_receipts_grant_no_uninstall_authority(setup, bad) -> None:
    app, state, directory = setup
    commands.reconcile_commands(app, state, directory)
    receipt = state / "installation" / commands.RECEIPT_NAME
    if bad == "missing":
        receipt.unlink()
    elif bad == "malformed":
        receipt.write_text("{broken")
    elif bad == "mode":
        receipt.chmod(0o644)
    else:
        value = json.loads(receipt.read_text())
        value["commands"]["ai-ping"] = "/usr/bin/true"
        receipt.write_text(json.dumps(value))
    before = entries(directory)
    assert commands.remove_commands(state, directory, app=app) == []
    assert entries(directory) == before


def test_uninstall_keeps_retargeted_links_and_wrong_app_does_nothing(setup, tmp_path) -> None:
    app, state, directory = setup
    commands.reconcile_commands(app, state, directory)
    other = fake_app(tmp_path / "other")
    assert commands.remove_commands(state, directory, app=other) == []
    link = directory / "ai-ping"
    link.unlink()
    link.symlink_to(other / commands.PINGAGENT_BIN_RELATIVE / "ai-ping")
    assert "ai-ping" not in commands.remove_commands(state, directory, app=app)
    assert link.resolve() == other / commands.PINGAGENT_BIN_RELATIVE / "ai-ping"


@pytest.mark.parametrize("bad", ["directory-mode", "parent-mode", "parent-symlink", "state-symlink"])
def test_unsafe_directories_are_refused(setup, tmp_path, bad) -> None:
    app, state, directory = setup
    directory.mkdir(parents=True)
    if bad == "directory-mode":
        directory.chmod(0o777)
    elif bad == "parent-mode":
        directory.parent.chmod(0o777)
    elif bad == "parent-symlink":
        directory.rmdir()
        directory.parent.rmdir()
        target = tmp_path / "elsewhere"
        target.mkdir()
        directory.parent.symlink_to(target)
    else:
        state.mkdir()
        (state / "installation").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(commands.CommandLinkError):
        commands.reconcile_commands(app, state, directory)
    assert not (directory / "ai-ping").exists()


@pytest.mark.parametrize("phase", ["apply", "verify", "receipt"])
def test_failed_transaction_restores_links_original_inode_mode_and_receipt(setup, tmp_path, monkeypatch, phase) -> None:
    app, state, directory = setup
    commands.reconcile_commands(app, state, directory)
    receipt = state / "installation" / commands.RECEIPT_NAME
    old_receipt = receipt.read_bytes()
    copied = directory / "ai-collab-watch"
    copied.unlink()
    copied.write_text("#!/bin/sh\necho legacy\n")
    copied.chmod(0o700)
    before = entries(directory)
    new_app = fake_app(tmp_path / "new")
    real_replace = commands._replace_link
    def fail(*args):
        raise OSError("simulated disk failure")
    def fail_once(path, target):
        if path.name == "ai-ping" and str(target).startswith(str(new_app)):
            raise OSError("simulated disk failure")
        real_replace(path, target)
    monkeypatch.setattr(commands, {"apply": "_replace_link", "verify": "_verify_links", "receipt": "_write_receipt"}[phase], fail_once if phase == "apply" else fail)
    with pytest.raises(commands.CommandLinkError) as result:
        commands.reconcile_commands(new_app, state, directory, replace=[copied])
    assert result.value.backup_directory is not None
    after = entries(directory)
    assert {name: (v[0], v[2]) for name, v in after.items()} == {name: (v[0], v[2]) for name, v in before.items()}
    assert copied.stat().st_ino == before[copied.name][1]
    assert receipt.read_bytes() == old_receipt


def test_replace_rejects_unapproved_paths_and_unsafe_entries(setup) -> None:
    app, state, directory = setup
    directory.mkdir(parents=True)
    path = directory / "ai-ping"
    path.write_text("original")
    for wrong in (directory / "ai-watch-service", Path("ai-ping")):
        with pytest.raises(commands.CommandLinkError, match="six command"):
            commands.reconcile_commands(app, state, directory, replace=[wrong])
    path.chmod(0o666)
    with pytest.raises(commands.CommandLinkError, match="cannot be explicitly replaced"):
        commands.reconcile_commands(app, state, directory, replace=[path])
    assert path.read_text() == "original"


@pytest.mark.parametrize("kind", ["unsigned", "adhoc", "invalid", "valid", "foreign"])
def test_product_verification_distinguishes_dev_from_broken_release(tmp_path, monkeypatch, kind) -> None:
    app = fake_app(tmp_path)
    if kind == "foreign":
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))
    def run(argv, **kwargs):
        if "--display" in argv:
            return subprocess.CompletedProcess(argv, 1 if kind == "unsigned" else 0, "",
                "code object is not signed at all" if kind == "unsigned" else
                "Signature=adhoc" if kind == "adhoc" else "Authority=Developer ID Application")
        return subprocess.CompletedProcess(argv, 1 if kind == "invalid" else 0, "", "invalid signature")
    monkeypatch.setattr(commands.subprocess, "run", run)
    if kind in {"invalid", "foreign"}:
        with pytest.raises(commands.CommandLinkError):
            VERIFY_PRODUCT(app)
    else:
        assert VERIFY_PRODUCT(app)["bundle"] == ("verified" if kind == "valid" else "unverified")
    if kind in {"unsigned", "adhoc"}:
        state, directory = tmp_path / "state", tmp_path / ".local/bin"
        assert commands.command_status(app, state, directory)["status"] == "skipped"
        assert not state.exists() and not directory.exists()
