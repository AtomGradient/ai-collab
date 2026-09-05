# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Current-user service entry point for the embedded Harness Host."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from .delivery import DeliveryError
from .host import HarnessHost
from .participant import ParticipantError
from .project import ProjectError
from .security import SecurityError
from .store import StoreError
from .workspace import WorkspaceError


def default_state_root() -> Path:
    override = os.environ.get("AI_COLLAB_STATE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path.home() / "Library" / "Application Support" / "AI Collab"
    ).resolve()


def default_workspace_root(state_root: Path) -> Path:
    override = os.environ.get("AI_COLLAB_WORKSPACE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    default_control_root = (
        Path.home() / "Library" / "Application Support" / "AI Collab"
    ).resolve()
    if state_root != default_control_root:
        return state_root / "workspaces"
    return (Path.home() / "Documents" / "Scenarios").resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-collab-host-service")
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--socket-path", type=Path, default=None)
    # The project and security adapters are optional, exactly as they are for
    # the underlying Host: without them the Host still serves, and project
    # registration or confirmation-gated operations answer with their typed
    # refusals instead of the service refusing to start. The participant
    # driver stays required because every build ships it; its absence means
    # the installation itself is broken.
    parser.add_argument("--adapter-config", type=Path, default=None)
    parser.add_argument("--participant-driver-config", type=Path, required=True)
    parser.add_argument("--security-adapter-config", type=Path, default=None)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    state_root = (arguments.state_root or default_state_root()).expanduser().resolve()
    host = HarnessHost(
        state_root,
        arguments.socket_path,
        arguments.adapter_config,
        arguments.participant_driver_config,
        arguments.security_adapter_config,
        arguments.workspace_root or default_workspace_root(state_root),
    )
    stopping = threading.Event()

    def stop(_signal: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(
            target=host.shutdown,
            name="ai-collab-host-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    host.bind()
    print(
        json.dumps(
            {
                "status": "ready",
                "host_generation": host.host_generation,
                "socket_path": str(host.socket_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    host.serve_forever()
    return 0


def main() -> int:
    try:
        return run()
    except (
        StoreError,
        WorkspaceError,
        ParticipantError,
        DeliveryError,
        SecurityError,
        ProjectError,
        OSError,
    ) as exc:
        print(
            json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
