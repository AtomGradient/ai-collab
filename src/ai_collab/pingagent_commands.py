# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
"""PingAgent commands installed and kept current by AI Collab.

The App bundle ships the whole PingAgent tool set under
``Contents/Resources/PingAgent/bin``.  Every shell a colleague's CLI opens —
including vendor tool shells that rebuild ``PATH`` from the login profile —
finds ``ai-ping`` and its siblings through one place, ``~/.local/bin``, and
that place always points at the installed App.  The links are created at
install, repaired at every Host start and upgrade, and removed at uninstall.

Only entries that provably belong to PingAgent or to an AI Collab App are ever
replaced: a symlink into a directory that holds both ``ai-ping`` and
``ai-harness-transport`` (a PingAgent checkout or an App bundle), or a dangling
symlink into a removed ``PingAgent/bin``.  Anything else is a collision and
nothing is changed.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any

PINGAGENT_COMMANDS = (
    "ai-collab-watch",
    "ai-harness-transport",
    "ai-pane-doctor",
    "ai-pane-register",
    "ai-pane-unregister",
    "ai-ping",
)
PINGAGENT_BIN_RELATIVE = Path("Contents/Resources/PingAgent/bin")
PINGAGENT_SIGNATURE = ("ai-ping", "ai-harness-transport")
RECEIPT_NAME = "pingagent-commands.json"
COMMAND_DIRECTORY_ENVIRONMENT_KEY = "AI_COLLAB_COMMAND_DIRECTORY"
# A copied (not linked) PingAgent script carries this header; only such a
# file is replaced by a link to the App's copy.
_PINGAGENT_HEADER = re.compile(r"Copyright © \d{4} AtomGradient")


class CommandLinkError(ValueError):
    def __init__(self, message: str, collisions: list[str] | None = None) -> None:
        super().__init__(message)
        self.collisions = list(collisions or [])


def default_command_directory() -> Path:
    override = os.environ.get(COMMAND_DIRECTORY_ENVIRONMENT_KEY, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "bin"


def _is_pingagent_script(path: Path) -> bool:
    """A regular file that is a copy of PingAgent's own script of that name."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            head = [stream.readline() for _ in range(8)]
    except OSError:
        return False
    return any(_PINGAGENT_HEADER.search(line) for line in head[:4]) and any(
        path.name in line for line in head
    )


def app_bundle_for(executable: Path) -> Path | None:
    """The ``.app`` an interpreter runs from, or None outside a bundle."""

    for candidate in (executable, *executable.parents):
        if candidate.suffix == ".app" and candidate.is_dir():
            return candidate
    return None


def _link_target(link: Path) -> Path:
    raw = Path(os.readlink(link))
    return raw if raw.is_absolute() else (link.parent / raw)


def _is_app_pingagent_bin(directory: Path) -> bool:
    parts = directory.parts
    return (
        len(parts) >= 4
        and parts[-4:-1] == ("Contents", "Resources", "PingAgent")
        and parts[-1] == "bin"
        and any(part.endswith(".app") for part in parts[:-4])
    )


def provenance(link: Path) -> str:
    """Classify an entry: absent, managed (an App), pingagent (migratable), foreign."""

    try:
        details = link.lstat()
    except FileNotFoundError:
        return "absent"
    if details.st_uid != os.getuid():
        return "foreign"
    if stat.S_ISREG(details.st_mode):
        return "pingagent" if _is_pingagent_script(link) else "foreign"
    if not stat.S_ISLNK(details.st_mode):
        return "foreign"
    target = _link_target(link)
    if target.name != link.name:
        return "foreign"
    parent = target.parent
    if _is_app_pingagent_bin(parent):
        return "managed"
    if all((parent / name).is_file() for name in PINGAGENT_SIGNATURE):
        return "pingagent"
    if (
        not target.exists()
        and parent.name == "bin"
        and parent.parent.name.lower() == "pingagent"
    ):
        return "pingagent"
    return "foreign"


def _receipt_path(state_root: Path) -> Path:
    directory = state_root / "installation"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise CommandLinkError("installation state directory is unsafe")
    os.chmod(directory, 0o700)
    return directory / RECEIPT_NAME


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_link(link: Path, target: Path) -> None:
    temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}.tmp")
    os.symlink(target, temporary)
    try:
        os.replace(temporary, link)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def install_commands(
    app: Path, state_root: Path, command_directory: Path | None = None
) -> dict[str, Any]:
    """Point every PingAgent command in the command directory at ``app``.

    Plans first and changes nothing when any entry is foreign; applies one
    atomic link replacement per command and restores the previous entries if
    a replacement fails; verifies every link afterwards.
    """

    app = app.resolve(strict=True)
    source_bin = app / PINGAGENT_BIN_RELATIVE
    for name in PINGAGENT_COMMANDS:
        source = source_bin / name
        if source.is_symlink() or not source.is_file() or not os.access(source, os.X_OK):
            raise CommandLinkError(f"the App does not ship an executable PingAgent command: {source}")
    directory = command_directory or default_command_directory()
    directory.mkdir(parents=True, exist_ok=True)
    details = directory.lstat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise CommandLinkError(f"command directory is unsafe: {directory}")

    previous: dict[str, Path | None] = {}
    collisions: list[str] = []
    for name in PINGAGENT_COMMANDS:
        link = directory / name
        kind = provenance(link)
        if kind == "foreign":
            collisions.append(str(link))
        elif kind == "absent":
            previous[name] = None
        elif link.is_symlink():
            previous[name] = Path(os.readlink(link))
        else:
            previous[name] = link  # a copied script; restored from a backup on failure
    if collisions:
        raise CommandLinkError(
            "refusing to replace commands that were not installed by PingAgent or "
            "AI Collab: " + ", ".join(collisions) + "; move them aside and rerun",
            collisions,
        )

    backups: dict[str, bytes] = {}
    for name, earlier in previous.items():
        if earlier == directory / name:
            backups[name] = (directory / name).read_bytes()
    replaced: list[str] = []
    try:
        for name in PINGAGENT_COMMANDS:
            _replace_link(directory / name, source_bin / name)
            replaced.append(name)
    except BaseException:
        for name in reversed(replaced):
            link = directory / name
            earlier = previous[name]
            if earlier is None:
                link.unlink(missing_ok=True)
            elif name in backups:
                link.unlink(missing_ok=True)
                link.write_bytes(backups[name])
                link.chmod(0o755)
            else:
                _replace_link(link, earlier)
        raise
    for name in PINGAGENT_COMMANDS:
        link = directory / name
        if not link.is_symlink() or Path(os.readlink(link)) != source_bin / name:
            raise CommandLinkError(f"command link differs after installation: {link}")

    migrated = sorted(
        name
        for name, earlier in previous.items()
        if earlier is not None and earlier != source_bin / name
    )
    receipt = {
        "schema_version": 1,
        "app_bundle_path": str(app),
        "command_directory": str(directory),
        "commands": {name: str(source_bin / name) for name in PINGAGENT_COMMANDS},
    }
    _write_receipt(_receipt_path(state_root), receipt)
    on_path = str(directory) in os.environ.get("PATH", "").split(os.pathsep)
    return {
        "command_directory": str(directory),
        "commands": list(PINGAGENT_COMMANDS),
        "migrated": migrated,
        "path_hint": None if on_path else f"add {directory} to PATH",
    }


def remove_commands(
    state_root: Path, command_directory: Path | None = None
) -> list[str]:
    """Remove only links that point into an AI Collab App; leave everything else."""

    directory = command_directory or default_command_directory()
    removed: list[str] = []
    for name in PINGAGENT_COMMANDS:
        link = directory / name
        if provenance(link) == "managed":
            link.unlink()
            removed.append(name)
    receipt = state_root / "installation" / RECEIPT_NAME
    if receipt.is_file() and not receipt.is_symlink():
        receipt.unlink()
    return removed
