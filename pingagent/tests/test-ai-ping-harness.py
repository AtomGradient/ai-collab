#!/usr/bin/env python3

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_PING = ROOT / "bin" / "ai-ping"


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    context = tmp_path / "participant-context.json"
    context.write_text("{}\n", encoding="utf-8")
    context.chmod(0o600)
    pythonpath = tmp_path / "client-pythonpath"
    pythonpath.mkdir(mode=0o700)
    log = tmp_path / "client.log"
    client = tmp_path / "participant-client"
    client.write_text(
        "#!/bin/sh\n"
        "{\n"
        "  printf 'PYTHONPATH=%s\\n' \"$PYTHONPATH\"\n"
        "  for value in \"$@\"; do printf 'ARG=%s\\n' \"$value\"; done\n"
        "} > \"$HARNESS_TEST_LOG\"\n"
        "cat > \"$HARNESS_TEST_LOG.stdin\"\n"
        "printf '{}\\n'\n",
        encoding="utf-8",
    )
    client.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    environment = dict(os.environ)
    environment.update(
        {
            "AI_COLLAB_HARNESS_CONTEXT": str(context),
            "AI_COLLAB_HARNESS_CLIENT_EXECUTABLE": str(client),
            "AI_COLLAB_HARNESS_CLIENT_PYTHONPATH": str(pythonpath),
            "HARNESS_TEST_LOG": str(log),
        }
    )
    return environment, context, log


def _record(log: Path) -> tuple[str, list[str], str]:
    lines = log.read_text(encoding="utf-8").splitlines()
    return (
        lines[0].removeprefix("PYTHONPATH="),
        [line.removeprefix("ARG=") for line in lines[1:]],
        Path(f"{log}.stdin").read_text(encoding="utf-8"),
    )


def test_harness_mode_routes_send_and_reply_to_scoped_product_client(
    tmp_path: Path,
) -> None:
    environment, context, log = _environment(tmp_path)
    send = subprocess.run(
        (
            str(AI_PING),
            "reviewer",
            "--kind",
            "review-request",
            "A message longer than legacy notification limits remains intact.",
        ),
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert send.returncode == 0, send.stderr
    pythonpath, arguments, stdin = _record(log)
    assert pythonpath == environment["AI_COLLAB_HARNESS_CLIENT_PYTHONPATH"]
    assert arguments == [
        "-m",
        "edgestudio.harness.participant_client",
        "send",
        "--context",
        str(context),
        "--to",
        "reviewer",
        "--kind",
        "review-request",
        "--message",
        "A message longer than legacy notification limits remains intact.",
    ]
    assert stdin == ""
    assert not (tmp_path / ".ai-mailbox").exists()

    reply = subprocess.run(
        (
            str(AI_PING),
            "analyst",
            "--reply-to",
            "delivery-original",
            "--kind",
            "review-response",
        ),
        cwd=tmp_path,
        env=environment,
        input="P0=0 P1=0\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert reply.returncode == 0, reply.stderr
    _, arguments, stdin = _record(log)
    assert arguments == [
        "-m",
        "edgestudio.harness.participant_client",
        "reply",
        "--context",
        str(context),
        "--to",
        "analyst",
        "--kind",
        "review-response",
        "--reply-to",
        "delivery-original",
    ]
    assert stdin == "P0=0 P1=0\n"


def test_harness_mode_rejects_sender_spoof_and_legacy_wait(
    tmp_path: Path,
) -> None:
    environment, _, log = _environment(tmp_path)
    for forbidden in (
        ("reviewer", "--from", "another-agent", "message"),
        ("reviewer", "--wait", "message"),
    ):
        completed = subprocess.run(
            (str(AI_PING), *forbidden),
            cwd=tmp_path,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode != 0
    assert not log.exists()
    assert not (tmp_path / ".ai-mailbox").exists()


def test_harness_mode_fails_closed_on_invalid_context(tmp_path: Path) -> None:
    cases = (
        "symlink-context",
        "relative-client",
        "relative-pythonpath",
        "missing-client",
    )
    for case in cases:
        case_root = tmp_path / case
        case_root.mkdir()
        environment, context, log = _environment(case_root)
        if case == "symlink-context":
            symlink = case_root / "participant-context-link.json"
            symlink.symlink_to(context)
            environment["AI_COLLAB_HARNESS_CONTEXT"] = str(symlink)
        elif case == "relative-client":
            environment["AI_COLLAB_HARNESS_CLIENT_EXECUTABLE"] = "participant-client"
        elif case == "relative-pythonpath":
            environment["AI_COLLAB_HARNESS_CLIENT_PYTHONPATH"] = "client-pythonpath"
        else:
            environment["AI_COLLAB_HARNESS_CLIENT_EXECUTABLE"] = str(
                case_root / "missing-participant-client"
            )

        completed = subprocess.run(
            (str(AI_PING), "reviewer", "message"),
            cwd=case_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        assert completed.returncode != 0, case
        assert not log.exists(), case
        assert not (case_root / ".ai-mailbox").exists(), case
