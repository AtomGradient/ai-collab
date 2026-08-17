# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""First-contact tolerance of the Agent-facing ai-ping command.

A fresh participant's first send is an LLM guess. The two guesses observed
in real Scenario dogfood — ``--message <text>`` for the body and
``--kind message`` for the default kind — must parse instead of costing
every new Agent one failed send, and the usage text a participant sees on
error must teach only Harness vocabulary, never the legacy mailbox flags
that are forbidden inside a Scenario.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AI_PING = ROOT / "pingagent" / "bin" / "ai-ping"


def _run(arguments: list[str], *, harness: bool) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ}
    environment.pop("AI_COLLAB_HARNESS_CONTEXT", None)
    if harness:
        environment["AI_COLLAB_HARNESS_CONTEXT"] = "/nonexistent/context.json"
    return subprocess.run(
        [str(AI_PING), *arguments],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def test_first_contact_guesses_parse_instead_of_failing() -> None:
    completed = _run(
        ["reviewer", "--kind", "message", "--message", "hello world"],
        harness=True,
    )
    # Parsing succeeded: the failure is the (deliberately bogus) client
    # lookup, not the option parser.
    assert completed.returncode == 1
    assert "Unknown option" not in completed.stdout
    assert "client executable is unavailable" in completed.stdout


def test_harness_usage_teaches_only_harness_vocabulary() -> None:
    completed = _run([], harness=True)
    assert completed.returncode == 1
    assert "--message" in completed.stdout
    assert "participant-id" in completed.stdout
    assert "--wait" not in completed.stdout
    assert "--from" not in completed.stdout
    assert ".ai-mailbox" not in completed.stdout


def test_legacy_usage_still_documents_the_mailbox_mode() -> None:
    completed = _run([], harness=False)
    assert completed.returncode == 1
    assert "--wait" in completed.stdout
    assert ".ai-mailbox" in completed.stdout
