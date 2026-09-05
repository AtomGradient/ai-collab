# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
"""One installation transaction for the App's six PingAgent commands.

Upgrades at the same App path keep existing links valid; a correct installation
is a no-op. Only the App lifecycle and installer call this module, never Host
startup. Unknown files require explicit replacement, with originals preserved.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import stat
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PINGAGENT_COMMANDS = (
    "ai-collab-watch", "ai-harness-transport", "ai-pane-doctor",
    "ai-pane-register", "ai-pane-unregister", "ai-ping",
)
PINGAGENT_BIN_RELATIVE = Path("Contents/Resources/PingAgent/bin")
RECEIPT_NAME = "pingagent-commands.json"
PRODUCT_BUNDLE_IDENTIFIER = "com.atomgradient.aicollab"


class CommandLinkError(ValueError):
    def __init__(self, message: str, *, backup_directory: Path | None = None) -> None:
        super().__init__(message)
        self.backup_directory = backup_directory


def default_command_directory() -> Path:
    return Path.home() / ".local/bin"


def _safe_directory(path: Path) -> None:
    details = path.lstat()
    if (not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) & 0o022):
        raise CommandLinkError(f"directory must be real, user-owned and not group/world writable: {path}")


def _command_directory(path: Path, *, create: bool = False) -> Path:
    path = path.expanduser().absolute()
    # Only ~/.local and bin may need creating. Check before following either.
    for directory in (path.parent, path):
        if directory.exists() or directory.is_symlink():
            _safe_directory(directory)
        else:
            if directory == path.parent:
                _safe_directory(directory.parent)
            if create:
                directory.mkdir(mode=0o755, exist_ok=True)
                _safe_directory(directory)
    return path


def _installation_directory(state_root: Path, *, create: bool = False) -> Path:
    for directory in (state_root, state_root / "installation"):
        if directory.exists() or directory.is_symlink():
            _safe_directory(directory)
        elif create:
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            _safe_directory(directory)
    directory = state_root / "installation"
    if directory.exists() and stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise CommandLinkError(f"installation directory must have mode 0700: {directory}")
    return directory


def verify_product_bundle(app: Path) -> dict[str, str]:
    if app.is_symlink() or not app.is_dir() or app.suffix != ".app":
        raise CommandLinkError(f"not a real App bundle: {app}")
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            metadata = plistlib.load(stream)
    except (OSError, ValueError) as exc:
        raise CommandLinkError(f"App metadata is unreadable: {app}") from exc
    if not isinstance(metadata, dict) or metadata.get("CFBundleIdentifier") != PRODUCT_BUNDLE_IDENTIFIER:
        raise CommandLinkError(f"App bundle identifier differs: {app}")
    display = subprocess.run(
        ["/usr/bin/codesign", "--display", "--verbose=4", str(app)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    signing = display.stdout + display.stderr
    if "Signature=adhoc" in signing or "code object is not signed at all" in signing:
        return {"bundle": "unverified"}
    verified = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if display.returncode != 0 or verified.returncode != 0:
        raise CommandLinkError(f"App signature is invalid: {app}: {verified.stderr.strip()}")
    return {"bundle": "verified"}


def _read_receipt(state_root: Path) -> dict[str, Any] | None:
    path = _installation_directory(state_root) / RECEIPT_NAME
    try:
        details = path.lstat()
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o600):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        app = Path(value["app_bundle_path"])
        directory = Path(value["command_directory"])
        if (value["schema_version"] != 1 or not app.is_absolute()
                or not directory.is_absolute()
                or str(app) != os.path.normpath(str(app))
                or str(directory) != os.path.normpath(str(directory))
                or value["commands"] != {
                    name: str(app / PINGAGENT_BIN_RELATIVE / name)
                    for name in PINGAGENT_COMMANDS
                }):
            return None
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _entry(path: Path) -> dict[str, Any]:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    kind = "link" if stat.S_ISLNK(details.st_mode) else "file" if stat.S_ISREG(details.st_mode) else "other"
    value: dict[str, Any] = {
        "kind": kind, "uid": details.st_uid, "mode": stat.S_IMODE(details.st_mode),
    }
    if kind == "link":
        value["target"] = os.readlink(path)
    return value


def command_status(app: Path, state_root: Path, command_directory: Path | None = None) -> dict[str, Any]:
    if verify_product_bundle(app)["bundle"] == "unverified":
        return {"status": "skipped", "bundle": "unverified", "entries": [], "conflicts": []}
    app = app.resolve(strict=True)
    directory = _command_directory(command_directory or default_command_directory())
    source_bin = app / PINGAGENT_BIN_RELATIVE
    for name in PINGAGENT_COMMANDS:
        source = source_bin / name
        details = source.lstat()
        if (not stat.S_ISREG(details.st_mode) or details.st_mode & 0o022
                or not os.access(source, os.X_OK)):
            raise CommandLinkError(f"App command is unavailable or unsafe: {source}")
    receipt = _read_receipt(state_root)
    entries = []
    for name in PINGAGENT_COMMANDS:
        path = directory / name
        value = _entry(path)
        target = source_bin / name
        recorded = receipt is not None and receipt["command_directory"] == str(directory)
        owned = False
        if value["kind"] == "link" and value["uid"] == os.getuid():
            owned = bool(recorded and value["target"] == receipt["commands"][name])
            try:
                owned = owned or path.resolve() == target
            except (OSError, RuntimeError):
                pass
        state = "ready" if owned and value["target"] == str(target) else (
            "repair" if owned or value["kind"] == "absent" else "conflict"
        )
        replaceable = (value["kind"] in {"file", "link"} and value["uid"] == os.getuid()
                       and (value["kind"] == "link" or not value["mode"] & 0o022))
        entries.append({"name": name, "path": str(path), "kind": value["kind"],
                        "target": value.get("target"), "state": state, "replaceable": replaceable})
    conflicts = [entry for entry in entries if entry["state"] == "conflict"]
    receipt_current = receipt is not None and receipt["app_bundle_path"] == str(app) and receipt["command_directory"] == str(directory)
    status = "conflict" if conflicts else "ready" if receipt_current and all(
        entry["state"] == "ready" for entry in entries
    ) else "needs_repair"
    return {"status": status, "bundle": "verified", "command_directory": str(directory),
            "entries": entries, "conflicts": conflicts, "changed": [],
            "backup_directory": None}


def _replace_link(link: Path, target: Path) -> None:
    temporary = link.with_name(f".{link.name}.{uuid.uuid4().hex}.tmp")
    os.symlink(target, temporary)
    try:
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_links(directory: Path, source_bin: Path) -> None:
    for name in PINGAGENT_COMMANDS:
        link = directory / name
        if not link.is_symlink() or os.readlink(link) != str(source_bin / name):
            raise CommandLinkError(f"command link differs after installation: {link}")


def reconcile_commands(app: Path, state_root: Path, command_directory: Path | None = None,
                       *, replace: Sequence[Path] = ()) -> dict[str, Any]:
    observed = command_status(app, state_root, command_directory)
    if observed["status"] == "skipped":
        raise CommandLinkError("only a signed product App can install commands")
    app = app.resolve(strict=True)
    directory = Path(observed["command_directory"])
    allowed = {directory / name for name in PINGAGENT_COMMANDS}
    replacements = set(replace)
    if not replacements <= allowed:
        raise CommandLinkError("replacement must name an absolute path from the six command entries")
    for entry in observed["entries"]:
        if Path(entry["path"]) in replacements and not entry["replaceable"]:
            raise CommandLinkError(f"entry cannot be explicitly replaced: {entry['path']}")
    if (observed["status"] == "ready"
            or any(Path(entry["path"]) not in replacements for entry in observed["conflicts"])):
        return observed
    _command_directory(directory, create=True)
    installation = _installation_directory(state_root, create=True)
    backup = installation / f"replaced-{uuid.uuid4().hex}" if replacements else None
    changed, moved = [], []
    try:
        if backup:
            backup.mkdir(mode=0o700)
        for entry in observed["entries"]:
            path = Path(entry["path"])
            if entry["state"] == "ready":
                continue
            if path in replacements:
                os.replace(path, backup / path.name)
                moved.append(entry)
            _replace_link(path, app / PINGAGENT_BIN_RELATIVE / path.name)
            changed.append(entry)
        _verify_links(directory, app / PINGAGENT_BIN_RELATIVE)
        _write_receipt(installation / RECEIPT_NAME, {
            "schema_version": 1, "app_bundle_path": str(app), "command_directory": str(directory),
            "commands": {name: str(app / PINGAGENT_BIN_RELATIVE / name) for name in PINGAGENT_COMMANDS},
        })
    except Exception as exc:
        failures = [str(exc)]
        for entry in reversed(changed + [item for item in moved if item not in changed]):
            path = Path(entry["path"])
            try:
                if entry in moved:
                    os.replace(backup / entry["name"], path)
                elif entry["kind"] == "absent":
                    path.unlink(missing_ok=True)
                else:
                    _replace_link(path, Path(entry["target"]))
            except OSError as restore_error:
                failures.append(f"restore {path}: {restore_error}")
        raise CommandLinkError("; ".join(failures), backup_directory=backup) from exc
    observed.update(status="ready", conflicts=[], changed=[entry["name"] for entry in changed],
                    backup_directory=str(backup) if backup else None)
    for entry in observed["entries"]:
        entry.update(kind="link", state="ready", replaceable=True,
                     target=str(app / PINGAGENT_BIN_RELATIVE / entry["name"]))
    return observed


def remove_commands(state_root: Path, command_directory: Path | None = None, *, app: Path) -> list[str]:
    receipt = _read_receipt(state_root)
    if receipt is None or receipt["app_bundle_path"] != str(app.expanduser().resolve()):
        return []
    directory = _command_directory(command_directory or Path(receipt["command_directory"]))
    if str(directory) != receipt["command_directory"]:
        return []
    removed = []
    for name in PINGAGENT_COMMANDS:
        path = directory / name
        value = _entry(path)
        if (value["kind"] == "link" and value["uid"] == os.getuid()
                and value["target"] == receipt["commands"][name]):
            path.unlink()
            removed.append(name)
    (state_root / "installation" / RECEIPT_NAME).unlink()
    return removed


def failure_result(exc: Exception) -> dict[str, Any]:
    return {"status": "error", "reason": str(exc), "entries": [], "conflicts": [],
            "backup_directory": str(exc.backup_directory) if getattr(exc, "backup_directory", None) else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "reconcile", "remove"))
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, default=Path.home() / "Library/Application Support/AI Collab")
    parser.add_argument("--replace-command", type=Path, action="append", default=[])
    arguments = parser.parse_args()
    try:
        if arguments.action == "status":
            result = command_status(arguments.app, arguments.state_root)
        elif arguments.action == "reconcile":
            result = reconcile_commands(arguments.app, arguments.state_root, replace=arguments.replace_command)
        else:
            result = {"status": "removed", "removed": remove_commands(arguments.state_root, app=arguments.app)}
    except (CommandLinkError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result = failure_result(exc)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] in {"conflict", "error"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
