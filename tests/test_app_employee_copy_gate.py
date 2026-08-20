"""Employee-copy gate: presentation sources carry no raw user-visible copy.

Every user-visible string in the macOS App flows through the bilingual `S`
catalog (Strings.swift), where English and Simplified Chinese are declared
together and a missing translation cannot compile. This gate closes the
bypass routes codex's review identified: raw literals in any App Swift file's
user-visible constructors, panel prompts, English-sentence literals in the
local error surfaces, missing localized system-dialog strings, and English
catalog copy that drifts from the approved employee glossary.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "macos" / "AI-Collab" / "App"

# User-visible SwiftUI constructors taking a leading string literal, plus
# presentation-property prompts. Scanned across every App source.
RAW_LITERAL = re.compile(
    r"(?:Text|Button|Label|GroupBox|TextField|Toggle|Picker|Menu|"
    r"ContentUnavailableView)\(\s*\"[A-Za-z][^\"]"
)
RAW_PROMPT = re.compile(r"\.(?:prompt|title|help)\s*=?\(?\s*\"[A-Za-z][^\"]")

# English-sentence heuristic for the local error surfaces: a quoted string
# containing two consecutive capitalized/lowercase words reads as copy, not a
# machine token.
SENTENCE = re.compile(r"\"[A-Z][A-Za-z]*\s[a-z]+\s[a-z]+")
SENTENCE_FILES = ["HarnessIPC.swift", "HarnessServiceController.swift"]

# Exact stripped lines allowed to keep a literal (reviewed exceptions only).
ALLOWLIST: set[str] = set()


def _swift_sources() -> list[Path]:
    return sorted(APP.glob("*.swift"))


def test_app_sources_have_no_raw_user_visible_literals() -> None:
    offenders: list[str] = []
    for path in _swift_sources():
        if path.name == "Strings.swift":
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped in ALLOWLIST or stripped.startswith("//"):
                continue
            if RAW_LITERAL.search(line) or RAW_PROMPT.search(line):
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert offenders == [], (
        "raw user-visible copy must go through the bilingual S catalog:\n"
        + "\n".join(offenders)
    )


def test_local_error_surfaces_carry_no_english_sentences() -> None:
    offenders: list[str] = []
    for name in SENTENCE_FILES:
        for number, line in enumerate(
            (APP / name).read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if stripped.startswith("//") or stripped in ALLOWLIST:
                continue
            if SENTENCE.search(line):
                offenders.append(f"{name}:{number}: {stripped}")
    assert offenders == [], (
        "local error copy must render through S (language-reactive):\n"
        + "\n".join(offenders)
    )


def test_strings_catalog_pairs_english_and_chinese() -> None:
    source = (APP / "Strings.swift").read_text(encoding="utf-8")
    calls = re.findall(r"\bt\(\s*\"(.*?)\",\s*\"(.*?)\"", source, re.S)
    assert len(calls) > 150, "catalog scan looks wrong; expected many entries"
    for english, chinese in calls:
        assert english.strip(), "empty English copy in catalog"
        assert chinese.strip(), "empty Chinese copy in catalog"


def test_english_catalog_respects_the_employee_glossary() -> None:
    """Approved glossary: Task Room / AI Colleague — in English too."""

    source = (APP / "Strings.swift").read_text(encoding="utf-8")
    english_halves = re.findall(r"\bt\(\s*\"((?:[^\"\\]|\\.)*)\"", source)
    banned = re.compile(r"\b(Scenario|Participant)\b")
    offenders = [text for text in english_halves if banned.search(text)]
    assert offenders == [], (
        "English copy must use the approved employee glossary:\n"
        + "\n".join(offenders)
    )


def test_system_dialog_strings_ship_in_both_languages() -> None:
    for folder in ("en.lproj", "zh-Hans.lproj"):
        strings = (APP / folder / "InfoPlist.strings").read_text(encoding="utf-8")
        assert "NSAppleEventsUsageDescription" in strings
        assert len(strings.strip()) > 40
