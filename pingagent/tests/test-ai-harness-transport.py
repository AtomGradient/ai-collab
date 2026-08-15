#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "bin" / "ai-harness-transport"


def request() -> dict[str, object]:
    return {
        "transport_contract_version": 1,
        "operation": "deliver_exact_session",
        "delivery_id": "delivery-one",
        "transport_attempt_id": "attempt-one",
        "session_id": "iterm-session-one",
        "notification": "typed message",
        "payload_digest": "a" * 64,
    }


def run(value: object, *, response: str = "ok", exit_code: int = 0) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryDirectory(prefix="pingagent-harness-") as directory:
        fake = Path(directory) / "osascript"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$AI_HARNESS_TEST_RESPONSE\"\n"
            "exit \"$AI_HARNESS_TEST_EXIT\"\n",
            encoding="utf-8",
        )
        fake.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        environment = dict(os.environ)
        environment.update(
            {
                "AI_HARNESS_OSASCRIPT": str(fake),
                "AI_HARNESS_TEST_RESPONSE": response,
                "AI_HARNESS_TEST_EXIT": str(exit_code),
            }
        )
        return subprocess.run(
            (str(TRANSPORT),),
            input=json.dumps(value).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=5,
            check=False,
        )


def test_exact_success_returns_redacted_typed_evidence() -> None:
    completed = run(request())
    assert completed.returncode == 0
    assert completed.stderr == b""
    result = json.loads(completed.stdout)
    assert result["delivery_id"] == "delivery-one"
    assert result["transport_attempt_id"] == "attempt-one"
    assert result["injection_confirmed"] is True
    assert result["session_identity_sha256"] == hashlib.sha256(
        b"iterm-session-one"
    ).hexdigest()
    assert "session_id" not in result
    assert "notification" not in result


def test_transport_submits_tui_input_with_separate_carriage_return() -> None:
    source = TRANSPORT.read_text(encoding="utf-8")
    body_write = "write text notificationText newline false"
    submit_write = "write text carriageReturn newline false"
    assert body_write in source
    assert submit_write in source
    assert "delay 0.5" in source
    assert source.index(body_write) < source.index(submit_write)


def test_missing_session_or_unconfirmed_output_returns_no_ack() -> None:
    completed = run(request(), response="session_not_found")
    assert completed.returncode == 1
    assert completed.stdout == b""


def test_invalid_request_fails_closed_before_injection() -> None:
    value = request()
    value["payload_digest"] = "not-a-digest"
    completed = run(value)
    assert completed.returncode == 1
    assert completed.stdout == b""
