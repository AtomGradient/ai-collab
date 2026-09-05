# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
"""Openings a room can give its colleagues.

An opening is text written into each colleague's startup prompt once. It is
not a rule set, not a role, and never a permission: the Host routes every
message kind between every colleague regardless of the opening.
"""

from __future__ import annotations

PLAYBOOK_IDS = ("none", "pairing", "peer-review")
DEFAULT_PLAYBOOK = "none"
MAX_NOTE_CHARACTERS = 500

OPENINGS = {
    "none": "",
    "pairing": (
        "你们在结对工作。一人写、一人逐步审并回推；换手听人指挥，分歧由人裁决。"
        "开工前先用 ai-ping 互相确认谁先做什么，不要各干各的。\n"
        "You are pair programming. One writes; the other reviews each step and "
        "pushes back. Swap when the person says so; the person settles "
        "disagreements. Before starting, agree with ai-ping who does what first; "
        "do not work in isolation."
    ),
    "peer-review": (
        "一人实现并用审核请求交付；另一人审核并用审核回复答复；每一轮以完成通知收尾。\n"
        "One implements and hands over with a review request; the other reviews "
        "and answers with a review response; each round ends with a done notice."
    ),
}


def opening_text(playbook: str) -> str:
    return OPENINGS[playbook]
