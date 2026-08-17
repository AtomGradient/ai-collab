#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Validate any project's ``project_descriptor.yaml`` without mutation.

The descriptor intentionally uses a constrained, top-level YAML mapping so
validation does not add a runtime package dependency. This validator checks
the shape of the contract only; it carries no knowledge of any particular
project. The project identity (``project_key``) and product contract version
are whatever the descriptor declares, provided they satisfy the shape rules
below and agree with the gate registry the descriptor references.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ai_collab_project_support import canonical_json_sha256, sha256_file


VALIDATION_ID = "AI-COLLAB-PROJECT-DESCRIPTOR-CONFORMANCE"
DESCRIPTOR_RELATIVE_PATH = "project_descriptor.yaml"
EXPECTED_FIELDS = (
    "schema_version",
    "project_key",
    "product_contract_version",
    "workspace_adapter",
    "repo_manifest",
    "environment_adapter",
    "gate_registry",
    "participant_driver_contract",
    "collaboration_policy_schema",
)
EXPECTED_SCHEMA_VERSION = 1
EXPECTED_PARTICIPANT_DRIVER_CONTRACT = 2
EXPECTED_COLLABORATION_POLICY_SCHEMA = 1
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
BARE_STRING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
PROJECT_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^[1-9][0-9]*\.[0-9]+$")
REGISTRY_ID_RE = re.compile(
    r"^ai-collab-scenario-harness-"
    r"(?P<project>[a-z][a-z0-9-]{0,63})-"
    r"v(?P<version>[1-9][0-9]*\.[0-9]+)-(?P<date>[0-9]{8})$"
)


class DescriptorError(ValueError):
    """The project descriptor or one of its references is invalid."""


def _parse_scalar(raw: str, *, field: str) -> str | int:
    if not raw:
        raise DescriptorError(f"{field} must not be empty")
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DescriptorError(f"{field} has an invalid quoted scalar") from exc
        if not isinstance(value, str) or not value:
            raise DescriptorError(f"{field} must be a non-empty string")
        return value
    if re.fullmatch(r"[0-9]+", raw):
        return int(raw)
    if re.fullmatch(r"[0-9]+\.[0-9]+", raw):
        raise DescriptorError(f"{field} version-like strings must be quoted")
    if BARE_STRING_RE.fullmatch(raw) is None:
        raise DescriptorError(f"{field} uses unsupported YAML scalar syntax")
    return raw


def parse_descriptor(text: str) -> dict[str, str | int]:
    """Parse the deliberately constrained root descriptor mapping."""

    if "\t" in text or "\r" in text:
        raise DescriptorError("descriptor must use LF and spaces only")
    result: dict[str, str | int] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        if line[0].isspace() or line.count(":") != 1:
            raise DescriptorError(
                f"descriptor line {line_number} is not a top-level scalar"
            )
        key, raw = line.split(":", 1)
        if KEY_RE.fullmatch(key) is None:
            raise DescriptorError(f"descriptor line {line_number} has an invalid key")
        if key in result:
            raise DescriptorError(f"descriptor contains duplicate field: {key}")
        result[key] = _parse_scalar(raw.strip(), field=key)
    missing = sorted(set(EXPECTED_FIELDS) - set(result))
    unknown = sorted(set(result) - set(EXPECTED_FIELDS))
    if missing or unknown:
        raise DescriptorError(
            f"descriptor field mismatch: missing={missing}, unknown={unknown}"
        )
    return result


def _require_relative_reference(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DescriptorError(f"{field} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DescriptorError(f"{field} must stay within the canonical project root")
    return path


def _resolve_reference(
    repo_root: Path, value: Any, *, field: str
) -> tuple[Path, str]:
    relative = _require_relative_reference(value, field=field)
    candidate = repo_root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DescriptorError(f"{field} reference is unavailable") from exc
    root = repo_root.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise DescriptorError(f"{field} reference escapes the canonical project root")
    return resolved, relative.as_posix()


def _parse_registry_identity(text: str) -> tuple[int, str]:
    values: dict[str, str | int] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line[0].isspace() or line.endswith(":"):
            break
        if line.count(":") != 1:
            raise DescriptorError("gate registry top-level identity is malformed")
        key, raw = line.split(":", 1)
        if key in values:
            raise DescriptorError(f"gate registry duplicates top-level field: {key}")
        values[key] = _parse_scalar(raw.strip(), field=f"registry.{key}")
    if not isinstance(values.get("schema_version"), int):
        raise DescriptorError("gate registry schema_version is missing or invalid")
    registry_id = values.get("registry_id")
    if not isinstance(registry_id, str):
        raise DescriptorError("gate registry registry_id is missing or invalid")
    return values["schema_version"], registry_id


def validate_descriptor(*, repo_root: Path) -> dict[str, Any]:
    """Return a deterministic, path-redacted conformance result."""

    root = repo_root.resolve()
    descriptor_path = root / DESCRIPTOR_RELATIVE_PATH
    try:
        descriptor_text = descriptor_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DescriptorError("project descriptor is unavailable") from exc
    lowered = descriptor_text.lower()
    if "placeholder" in lowered or "not_available" in lowered or "<" in descriptor_text:
        raise DescriptorError("project descriptor contains an unresolved placeholder")
    descriptor = parse_descriptor(descriptor_text)

    expected_scalars: Mapping[str, str | int] = {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "participant_driver_contract": EXPECTED_PARTICIPANT_DRIVER_CONTRACT,
        "collaboration_policy_schema": EXPECTED_COLLABORATION_POLICY_SCHEMA,
    }
    for field, expected in expected_scalars.items():
        if descriptor[field] != expected:
            raise DescriptorError(f"{field} is incompatible with product contract")
    if PROJECT_KEY_RE.fullmatch(str(descriptor["project_key"])) is None:
        raise DescriptorError("project_key is invalid")
    if VERSION_RE.fullmatch(str(descriptor["product_contract_version"])) is None:
        raise DescriptorError("product_contract_version is invalid")
    for field in ("workspace_adapter", "environment_adapter"):
        value = descriptor[field]
        if not isinstance(value, str) or COMMAND_RE.fullmatch(value) is None:
            raise DescriptorError(f"{field} must be a versioned command reference")

    manifest_path, manifest_ref = _resolve_reference(
        root, descriptor["repo_manifest"], field="repo_manifest"
    )
    registry_path, registry_ref = _resolve_reference(
        root, descriptor["gate_registry"], field="gate_registry"
    )
    registry_schema, registry_id = _parse_registry_identity(
        registry_path.read_text(encoding="utf-8")
    )
    match = REGISTRY_ID_RE.fullmatch(registry_id)
    if match is None:
        raise DescriptorError("gate registry identity is unsupported")
    if match.group("project") != descriptor["project_key"]:
        raise DescriptorError("gate registry project identity does not match descriptor")
    if match.group("version") != descriptor["product_contract_version"]:
        raise DescriptorError("gate registry product contract version does not match")

    return {
        "schema_version": 1,
        "validation_id": VALIDATION_ID,
        "status": "valid",
        "descriptor_relative_path": DESCRIPTOR_RELATIVE_PATH,
        "descriptor_digest": canonical_json_sha256(descriptor),
        "project_key": descriptor["project_key"],
        "product_contract_version": descriptor["product_contract_version"],
        "references": {
            "gate_registry": {
                "relative_path": registry_ref,
                "sha256": sha256_file(registry_path),
                "schema_version": registry_schema,
                "registry_id": registry_id,
            },
            "repo_manifest": {
                "relative_path": manifest_ref,
                "sha256": sha256_file(manifest_path),
            },
        },
        "state_mutated": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_descriptor(repo_root=args.repo_root)
    except (DescriptorError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "validation_id": VALIDATION_ID,
                    "status": "failed",
                    "reason": str(exc),
                    "state_mutated": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
