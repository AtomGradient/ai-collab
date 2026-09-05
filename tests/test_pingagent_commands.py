# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

import ai_collab.pingagent_commands as commands_module
import ai_collab.service as service_module
from ai_collab.pingagent_commands import (
    PINGAGENT_COMMANDS,
    CommandLinkError,
    app_bundle_for,
    install_commands,
    provenance,
    remove_commands,
    verify_product_bundle,
)


def _fake_app(root: Path, name: str = "AI Collab.app") -> Path:
    app = root / name
    bin_dir = app / "Contents" / "Resources" / "PingAgent" / "bin"
    bin_dir.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS:
        path = bin_dir / command
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return app


def _app_bin(app: Path) -> Path:
    return app.resolve() / "Contents" / "Resources" / "PingAgent" / "bin"


def _checkout(root: Path) -> Path:
    bin_dir = root / "PingAgent" / "bin"
    bin_dir.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS:
        path = bin_dir / command
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return bin_dir


def _entries(directory: Path) -> dict[str, Any]:
    return {
        path.name: (
            ("link", os.readlink(path)) if path.is_symlink() else ("file", path.read_bytes())
        )
        for path in directory.iterdir()
    }


def test_fresh_install_links_every_command_and_writes_a_receipt(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    state = tmp_path / "state"
    home = tmp_path / "home with space"
    home.mkdir()
    commands = home / ".local" / "bin"

    result = install_commands(app, state, commands)

    for command in PINGAGENT_COMMANDS:
        link = commands / command
        assert link.is_symlink()
        assert Path(os.readlink(link)) == _app_bin(app) / command
        assert os.access(link, os.X_OK)
    receipt = json.loads(
        (state / "installation" / "pingagent-commands.json").read_text(encoding="utf-8")
    )
    assert receipt["app_bundle_path"] == str(app.resolve())
    assert set(receipt["commands"]) == set(PINGAGENT_COMMANDS)
    assert result["migrated"] == []
    assert result["commands"] == list(PINGAGENT_COMMANDS)
    assert not any(path.name.startswith(".") for path in commands.iterdir())


def test_pingagent_checkout_links_and_dangling_links_are_migrated(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    checkout = _checkout(tmp_path / "Codes")
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS:
        os.symlink(checkout / command, commands / command)
    (commands / "ai-pane-doctor").unlink()
    os.symlink(
        tmp_path / "gone" / "PingAgent" / "bin" / "ai-pane-doctor",
        commands / "ai-pane-doctor",
    )
    assert provenance(commands / "ai-ping") == "pingagent"
    assert provenance(commands / "ai-pane-doctor") == "pingagent"

    result = install_commands(app, tmp_path / "state", commands)

    assert result["migrated"] == sorted(PINGAGENT_COMMANDS)
    for command in PINGAGENT_COMMANDS:
        assert Path(os.readlink(commands / command)) == _app_bin(app) / command


def test_foreign_entries_are_reported_and_nothing_changes(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    state = tmp_path / "state"
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    user_script = commands / "ai-ping"
    user_script.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    user_script.chmod(0o755)
    os.symlink("/usr/bin/true", commands / "ai-collab-watch")
    checkout = _checkout(tmp_path / "Codes")
    os.symlink(checkout / "ai-pane-register", commands / "ai-pane-register")
    before = _entries(commands)

    with pytest.raises(CommandLinkError) as info:
        install_commands(app, state, commands)

    assert info.value.collisions == [
        str(commands / "ai-collab-watch"),
        str(commands / "ai-ping"),
    ]
    assert "move them aside" in str(info.value)
    assert _entries(commands) == before
    assert not (state / "installation" / "pingagent-commands.json").exists()


def test_failed_replacement_restores_previous_links(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _fake_app(tmp_path)
    checkout = _checkout(tmp_path / "Codes")
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS[:4]:
        os.symlink(checkout / command, commands / command)
    before = _entries(commands)
    real_replace = os.replace
    calls = {"count": 0}

    def flaky(source: Any, target: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 3:
            raise OSError("simulated failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="simulated failure"):
        install_commands(app, tmp_path / "state", commands)

    assert _entries(commands) == before
    assert not any(path.name.startswith(".") for path in commands.iterdir())


def test_upgrade_repoints_links_and_repairs_stale_app_targets(tmp_path: Path) -> None:
    previous = _fake_app(tmp_path / "previous")
    current = _fake_app(tmp_path / "current")
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS:
        os.symlink(_app_bin(previous) / command, commands / command)
    shutil.rmtree(previous)  # the archived App is gone: links dangle
    assert provenance(commands / "ai-ping") == "managed"

    result = install_commands(current, tmp_path / "state", commands)

    assert result["migrated"] == sorted(PINGAGENT_COMMANDS)
    for command in PINGAGENT_COMMANDS:
        assert Path(os.readlink(commands / command)) == _app_bin(current) / command
    # Running again is idempotent and reports nothing migrated.
    assert install_commands(current, tmp_path / "state", commands)["migrated"] == []


def test_remove_only_removes_links_into_an_app(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    checkout = _checkout(tmp_path / "Codes")
    state = tmp_path / "state"
    commands = tmp_path / ".local" / "bin"
    install_commands(app, state, commands)
    (commands / "ai-pane-register").unlink()
    os.symlink(checkout / "ai-pane-register", commands / "ai-pane-register")
    (commands / "ai-collab-watch").unlink()
    (commands / "ai-collab-watch").write_text("#!/bin/sh\n", encoding="utf-8")

    # A link retargeted to another App is not ours any more.
    other = _fake_app(tmp_path / "elsewhere", "Other.app")
    (commands / "ai-ping").unlink()
    os.symlink(_app_bin(other) / "ai-ping", commands / "ai-ping")

    removed = remove_commands(state, commands)

    assert removed == sorted(
        set(PINGAGENT_COMMANDS) - {"ai-pane-register", "ai-collab-watch", "ai-ping"}
    )
    assert Path(os.readlink(commands / "ai-pane-register")) == checkout / "ai-pane-register"
    assert (commands / "ai-collab-watch").is_file()
    assert Path(os.readlink(commands / "ai-ping")) == _app_bin(other) / "ai-ping"
    assert not (state / "installation" / "pingagent-commands.json").exists()


def test_remove_without_a_trustworthy_receipt_removes_nothing(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    state = tmp_path / "state"
    commands = tmp_path / ".local" / "bin"
    install_commands(app, state, commands)
    receipt = state / "installation" / "pingagent-commands.json"
    before = _entries(commands)

    receipt.write_text("{not json", encoding="utf-8")
    assert remove_commands(state, commands) == []
    assert _entries(commands) == before

    receipt.unlink()
    assert remove_commands(state, commands) == []
    assert _entries(commands) == before

    install_commands(app, state, commands)
    receipt.chmod(0o644)
    assert remove_commands(state, commands) == []
    assert _entries(commands) == before

    receipt.chmod(0o600)
    assert remove_commands(state, commands, app=tmp_path / "elsewhere" / "Other.app") == []
    assert remove_commands(state, commands, app=app) == sorted(PINGAGENT_COMMANDS)


def test_app_requires_every_command_before_touching_anything(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    (_app_bin(app) / "ai-pane-doctor").unlink()
    commands = tmp_path / ".local" / "bin"
    with pytest.raises(CommandLinkError, match="ai-pane-doctor"):
        install_commands(app, tmp_path / "state", commands)
    assert not commands.exists()


def test_app_bundle_for_finds_the_enclosing_bundle(tmp_path: Path) -> None:
    app = tmp_path / "AI Collab.app"
    executable = app / "Contents" / "Resources" / "HarnessService" / "runtime" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    assert app_bundle_for(executable) == app
    plain = tmp_path / "venv" / "bin" / "python3"
    plain.parent.mkdir(parents=True)
    plain.write_text("", encoding="utf-8")
    assert app_bundle_for(plain) is None


def test_copied_pingagent_scripts_are_migrated_and_other_files_are_not(
    tmp_path: Path,
) -> None:
    app = _fake_app(tmp_path)
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    copied = commands / "ai-ping"
    copied.write_text(
        "#!/usr/bin/env bash\n# SPDX-License-Identifier: MIT\n"
        "# Copyright © 2026 AtomGradient\n# 版权所有 © 2026 质子梯度（北京）科技有限公司\n"
        "# ai-ping <to> [options] [<message>]\necho old\n",
        encoding="utf-8",
    )
    copied.chmod(0o755)
    assert provenance(copied) == "pingagent"
    other = commands / "ai-pane-doctor"
    other.write_text("#!/bin/sh\n# my own doctor\nexit 0\n", encoding="utf-8")
    other.chmod(0o755)
    assert provenance(other) == "foreign"

    with pytest.raises(CommandLinkError) as info:
        install_commands(app, tmp_path / "state", commands)
    assert info.value.collisions == [str(other)]
    assert copied.is_file() and not copied.is_symlink()

    other.unlink()
    result = install_commands(app, tmp_path / "state", commands)
    assert result["migrated"] == ["ai-ping"]
    assert Path(os.readlink(commands / "ai-ping")) == _app_bin(app) / "ai-ping"


def test_copied_script_is_restored_when_a_later_link_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _fake_app(tmp_path)
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    copied = commands / "ai-collab-watch"
    body = (
        "#!/usr/bin/env bash\n# SPDX-License-Identifier: MIT\n"
        "# Copyright © 2025 AtomGradient\n#\n# ai-collab-watch <role> <mailbox-dir>\n"
    )
    copied.write_text(body, encoding="utf-8")
    copied.chmod(0o755)
    real_replace = os.replace
    calls = {"count": 0}

    def flaky(source: Any, target: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="simulated failure"):
        install_commands(app, tmp_path / "state", commands)
    assert copied.is_file() and not copied.is_symlink()
    assert copied.read_text(encoding="utf-8") == body
    assert sorted(path.name for path in commands.iterdir()) == ["ai-collab-watch"]


def test_command_directory_override(tmp_path: Path, monkeypatch: Any) -> None:
    from ai_collab.pingagent_commands import default_command_directory

    monkeypatch.setenv("AI_COLLAB_COMMAND_DIRECTORY", str(tmp_path / "bin"))
    assert default_command_directory() == tmp_path / "bin"
    monkeypatch.delenv("AI_COLLAB_COMMAND_DIRECTORY")
    assert default_command_directory() == Path.home() / ".local" / "bin"


def test_install_is_transactional_across_verification_and_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _fake_app(tmp_path)
    checkout = _checkout(tmp_path / "Codes")
    commands = tmp_path / ".local" / "bin"
    commands.mkdir(parents=True)
    for command in PINGAGENT_COMMANDS[:3]:
        os.symlink(checkout / command, commands / command)
    copied = commands / "ai-ping"
    copied.write_text(
        "#!/usr/bin/env bash\n# SPDX-License-Identifier: MIT\n"
        "# Copyright © 2026 AtomGradient\n#\n# ai-ping <to>\n",
        encoding="utf-8",
    )
    copied.chmod(0o700)
    before = _entries(commands)
    state = tmp_path / "state"

    def failing_receipt(path: Path, value: dict[str, Any]) -> None:
        raise OSError("receipt disk full")

    monkeypatch.setattr(commands_module, "_write_receipt", failing_receipt)
    with pytest.raises(OSError, match="receipt disk full"):
        install_commands(app, state, commands)
    assert _entries(commands) == before
    assert stat.S_IMODE(copied.lstat().st_mode) == 0o700
    assert not (state / "installation" / "pingagent-commands.json").exists()
    monkeypatch.undo()

    def failing_verification(directory: Path, source_bin: Path) -> None:
        raise CommandLinkError("verification mismatch")

    monkeypatch.setattr(commands_module, "_verify_links", failing_verification)
    with pytest.raises(CommandLinkError, match="verification mismatch"):
        install_commands(app, state, commands)
    assert _entries(commands) == before
    assert stat.S_IMODE(copied.lstat().st_mode) == 0o700
    monkeypatch.undo()

    # An unsafe receipt location is refused before any command changes.
    shutil.rmtree(state / "installation", ignore_errors=True)
    state.mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "elsewhere", state / "installation")
    with pytest.raises(CommandLinkError, match="installation state directory"):
        install_commands(app, state, commands)
    assert _entries(commands) == before


def test_unsafe_command_directory_or_parent_is_refused(tmp_path: Path) -> None:
    app = _fake_app(tmp_path)
    state = tmp_path / "state"

    loose = tmp_path / "loose" / "bin"
    loose.mkdir(parents=True)
    loose.chmod(0o777)
    with pytest.raises(CommandLinkError, match="world-writable"):
        install_commands(app, state, loose)
    assert list(loose.iterdir()) == []

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    os.symlink(real_parent, linked_parent)
    with pytest.raises(CommandLinkError, match="parent must be a real directory"):
        install_commands(app, state, linked_parent / "bin")
    assert not (real_parent / "bin").exists()

    loose_parent = tmp_path / "loose-parent"
    loose_parent.mkdir()
    loose_parent.chmod(0o777)
    with pytest.raises(CommandLinkError, match="parent is group- or world-writable"):
        install_commands(app, state, loose_parent / "bin")
    assert not (loose_parent / "bin").exists()


def test_host_repair_links_only_a_verified_product_bundle(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import plistlib

    calls: list[Path] = []
    monkeypatch.setattr(service_module, "install_commands", lambda app, state: calls.append(app))

    # Not inside a bundle at all: nothing happens.
    monkeypatch.setattr(service_module.sys, "executable", str(tmp_path / "venv" / "bin" / "python3"))
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python3").write_text("", encoding="utf-8")
    service_module._refresh_pingagent_commands(tmp_path / "state")  # noqa: SLF001
    assert calls == []

    # A bundle with another identity is refused and reported, never linked.
    other = tmp_path / "Other.app"
    executable = other / "Contents" / "Resources" / "HarnessService" / "runtime" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    with (other / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "com.example.other"}, stream)
    monkeypatch.setattr(service_module.sys, "executable", str(executable))
    service_module._refresh_pingagent_commands(tmp_path / "state")  # noqa: SLF001
    assert calls == []
    assert "pingagent-commands-not-linked" in capsys.readouterr().err

    # The product bundle with a verifying signature is linked.
    with (other / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "com.atomgradient.aicollab"}, stream)
    monkeypatch.setattr(
        commands_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, ""),
    )
    service_module._refresh_pingagent_commands(tmp_path / "state")  # noqa: SLF001
    assert calls == [other]

    # A failing signature check is refused even with the right identity.
    calls.clear()
    monkeypatch.setattr(
        commands_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "invalid signature"),
    )
    service_module._refresh_pingagent_commands(tmp_path / "state")  # noqa: SLF001
    assert calls == []
    with pytest.raises(CommandLinkError, match="does not verify"):
        verify_product_bundle(other)
