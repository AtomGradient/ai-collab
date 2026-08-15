# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Participant-facing send/reply entry used by PingAgent inside a Scenario."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from .client import HarnessClientError, ParticipantHarnessClient


KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _message_kind(value: str, *, reply: bool) -> str:
    aliases = {
        "msg": "message",
        "review-request": "review-request",
        "review-response": "review-response",
        "question": "question",
        "pushback": "pushback",
        "notice": "notice",
        "done": "done",
    }
    normalized = aliases.get(value, value)
    if not KIND_RE.fullmatch(normalized):
        raise ValueError("message kind is invalid")
    if reply and normalized == "message":
        normalized = "response"
    return f"collaboration.{normalized}"


def _body(arguments: argparse.Namespace) -> str:
    sources = sum(
        value is not None for value in (arguments.file, arguments.message)
    )
    if sources > 1:
        raise ValueError("multiple message sources were provided")
    if arguments.file is not None:
        path = Path(arguments.file)
        if path.is_symlink() or not path.is_file():
            raise ValueError("message file is unavailable")
        value = path.read_text(encoding="utf-8")
    elif arguments.message is not None:
        value = arguments.message
    elif not sys.stdin.isatty():
        value = sys.stdin.read()
    else:
        raise ValueError("message body is unavailable")
    if not value:
        raise ValueError("message body is empty")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(prog="ai-ping (Harness participant mode)")
    parser.add_argument("operation", choices=("send", "reply"))
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--to", required=True)
    parser.add_argument("--kind", default="msg")
    parser.add_argument("--reply-to")
    parser.add_argument("--file")
    parser.add_argument("--message")
    arguments = parser.parse_args()
    try:
        if arguments.operation == "send" and arguments.reply_to is not None:
            raise ValueError("send cannot carry a reply delivery")
        if arguments.operation == "reply" and arguments.reply_to is None:
            raise ValueError("reply delivery is required")
        message = _body(arguments)
        client = ParticipantHarnessClient(arguments.context)
        common = {
            "receiver_participant_id": arguments.to,
            "message_id": f"message-{uuid.uuid4().hex}",
            "message_kind": _message_kind(
                arguments.kind, reply=arguments.operation == "reply"
            ),
            "message": message,
        }
        if arguments.operation == "reply":
            result = client.reply(
                reply_to_delivery_id=arguments.reply_to,
                **common,
            )
        else:
            result = client.send(**common)
        output = {
            "outcome": result["acceptance"]["outcome"],
            "sender": {
                "scenario_id": client.context["scenario_id"],
                "participant_id": client.context["participant_id"],
                "participant_generation": client.context[
                    "participant_generation"
                ],
            },
            "deliveries": [
                {
                    "delivery_id": value["delivery_id"],
                    "state": value["state"],
                    "receiver": value["target"]["receiver"]["participant_id"],
                }
                for value in result["deliveries"]
            ],
        }
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
        return 0
    except (HarnessClientError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "participant.message-invalid")
        print(f"Harness participant send failed [{code}]: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
