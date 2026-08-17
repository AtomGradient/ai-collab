#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Validate any project's machine-independent ``repo_manifest.yaml``.

This validator checks only the committed contract. It deliberately does not
scan local directories, inspect Git state, provision repositories, or turn an
unclassified checkout into a managed repository. It carries no knowledge of
any particular project: the ``project_key`` is whatever the manifest declares,
and the single ``project_root`` row must carry that same key.

Beyond the required row fields, a managed row may declare how the Scenario
environment binds to it:

- ``dependency_lock``: a file inside that repository whose digest identifies
  the dependency set (at most one row in the manifest may declare this);
- ``python_source_path`` + ``python_import_name`` (both or neither): the
  subdirectory placed on the Scenario venv's import path and the module name
  whose successful import proves the binding works.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

from ai_collab_project_support import canonical_json_sha256


VALIDATION_ID = "AI-COLLAB-REPO-MANIFEST-CONFORMANCE"
MANIFEST_RELATIVE_PATH = "repo_manifest.yaml"
MAX_MANIFEST_BYTES = 256 * 1024
TOP_LEVEL_FIELDS = {"schema_version", "project_key", "repos"}
MANAGED_FIELDS = {
    "repo_key",
    "classification",
    "placement",
    "path",
    "remote",
    "base_branch",
    "provision_order",
    "provision_after",
    "acceptance_layer",
    "smoke_policy",
}
MANAGED_OPTIONAL_FIELDS = {
    "dependency_lock",
    "python_source_path",
    "python_import_name",
}
UNMANAGED_FIELDS = {
    "repo_key",
    "classification",
    "placement",
    "path",
    "acceptance_layer",
    "smoke_policy",
}
MANAGED_CLASSIFICATIONS = {"required", "optional"}
PLACEMENTS = {"project_root", "project_child", "bundle_sibling"}
ACCEPTANCE_LAYERS = {"base", "team", "role", "ci_release"}
SMOKE_POLICIES = {"required", "optional"}
REPO_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
REMOTE_RES = (
    re.compile(r"^git@[A-Za-z0-9][A-Za-z0-9.-]*:[A-Za-z0-9._/-]+\.git$"),
    re.compile(
        r"^ssh://git@[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?/[A-Za-z0-9._/-]+\.git$"
    ),
    re.compile(
        r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?/[A-Za-z0-9._/-]+\.git$"
    ),
)
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
PATH_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IMPORT_NAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)


class ManifestError(ValueError):
    """The repository manifest violates its frozen contract."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ManifestError("manifest mapping keys must be strings")
        if key in result:
            raise ManifestError(f"manifest contains duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _parse_manifest(text: str) -> dict[str, Any]:
    if "\t" in text or "\r" in text:
        raise ManifestError("manifest must use LF and spaces only")
    try:
        for token in yaml.scan(text):
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise ManifestError("manifest aliases, anchors, and tags are unsupported")
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)
    except ManifestError:
        raise
    except yaml.YAMLError as exc:
        raise ManifestError("manifest is not valid constrained YAML") from exc
    if not isinstance(parsed, dict):
        raise ManifestError("manifest root must be a mapping")
    if set(parsed) != TOP_LEVEL_FIELDS:
        raise ManifestError("manifest top-level fields do not match the contract")
    return parsed


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _validate_source_path(value: Any, *, placement: str, field: str) -> str:
    raw = _require_string(value, field=field)
    if "\\" in raw:
        raise ManifestError(f"{field} must be a POSIX path")
    if placement == "project_root":
        if raw != ".":
            raise ManifestError("project_root path must be exactly '.'")
        return raw
    path = PurePosixPath(raw)
    if (
        raw == "."
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or raw != path.as_posix()
        or any(PATH_PART_RE.fullmatch(part) is None for part in path.parts)
    ):
        raise ManifestError(f"{field} must not escape its typed placement root")
    return path.as_posix()


def _validate_repo_relative_path(value: Any, *, field: str, allow_dot: bool) -> str:
    raw = _require_string(value, field=field)
    if "\\" in raw:
        raise ManifestError(f"{field} must be a POSIX path")
    if raw == ".":
        if allow_dot:
            return raw
        raise ManifestError(f"{field} must name a file inside the repository")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or raw != path.as_posix()
        or any(PATH_PART_RE.fullmatch(part) is None for part in path.parts)
    ):
        raise ManifestError(f"{field} must stay within its repository")
    return raw


def _validate_branch(value: Any, *, field: str) -> str:
    branch = _require_string(value, field=field)
    invalid = (
        BRANCH_RE.fullmatch(branch) is None
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or branch.endswith(("/", "."))
        or any(part.startswith(".") for part in branch.split("/"))
        or any(part.endswith(".lock") for part in branch.split("/"))
    )
    if invalid:
        raise ManifestError(f"{field} is not a safe per-repository base branch")
    return branch


def _validate_remote(value: Any, *, field: str) -> str:
    remote = _require_string(value, field=field)
    if ".." in remote or not any(
        pattern.fullmatch(remote) for pattern in REMOTE_RES
    ):
        raise ManifestError(f"{field} is not a canonical Git remote")
    return remote


def _validate_exact_fields(
    row: Mapping[str, Any],
    expected: set[str],
    *,
    repo_key: str,
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(expected - set(row))
    unknown = sorted(set(row) - expected - optional)
    if missing or unknown:
        raise ManifestError(
            f"repo {repo_key} field mismatch: missing={missing}, unknown={unknown}"
        )


def _paths_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    left_parts = tuple(part.casefold() for part in left.parts)
    right_parts = tuple(part.casefold() for part in right.parts)
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def validate_manifest(*, repo_root: Path) -> dict[str, Any]:
    """Return a deterministic, path-redacted validation result."""

    root = repo_root.resolve()
    manifest_path = root / MANIFEST_RELATIVE_PATH
    try:
        resolved = manifest_path.resolve(strict=True)
        if (
            manifest_path.is_symlink()
            or not resolved.is_relative_to(root)
            or not resolved.is_file()
        ):
            raise ManifestError("repository manifest must be a regular root artifact")
        size = manifest_path.stat().st_size
        if size > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest exceeds the size limit")
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError("repository manifest is unavailable") from exc
    manifest = _parse_manifest(text)

    if manifest["schema_version"] != 1 or isinstance(
        manifest["schema_version"], bool
    ):
        raise ManifestError("schema_version is incompatible")
    project_key = manifest["project_key"]
    if not isinstance(project_key, str) or REPO_KEY_RE.fullmatch(project_key) is None:
        raise ManifestError("project_key is invalid")
    repos = manifest["repos"]
    if not isinstance(repos, list) or not repos:
        raise ManifestError("repos must be a non-empty list")

    rows_by_key: dict[str, Mapping[str, Any]] = {}
    managed_orders: dict[int, str] = {}
    managed_remotes: dict[str, str] = {}
    typed_paths: dict[str, list[tuple[str, PurePosixPath]]] = {
        placement: [] for placement in PLACEMENTS
    }
    counts = {"required": 0, "optional": 0, "unmanaged": 0}
    project_roots: list[str] = []
    lock_owners: list[str] = []

    for index, raw_row in enumerate(repos):
        if not isinstance(raw_row, dict):
            raise ManifestError(f"repos[{index}] must be a mapping")
        repo_key = _require_string(raw_row.get("repo_key"), field="repo_key")
        if REPO_KEY_RE.fullmatch(repo_key) is None:
            raise ManifestError(f"repo_key is invalid: {repo_key}")
        if repo_key in rows_by_key:
            raise ManifestError(f"duplicate repo_key: {repo_key}")
        rows_by_key[repo_key] = raw_row

        classification = _require_string(
            raw_row.get("classification"), field=f"{repo_key}.classification"
        )
        if classification not in counts:
            raise ManifestError(f"repo {repo_key} has an unsupported classification")
        counts[classification] += 1
        placement = _require_string(
            raw_row.get("placement"), field=f"{repo_key}.placement"
        )
        if placement not in PLACEMENTS:
            raise ManifestError(f"repo {repo_key} has an unsupported placement")
        path = _validate_source_path(
            raw_row.get("path"), placement=placement, field=f"{repo_key}.path"
        )
        typed_path = PurePosixPath(path)
        for other_key, other_path in typed_paths[placement]:
            if _paths_overlap(typed_path, other_path):
                raise ManifestError(
                    f"repo paths overlap within {placement}: {other_key}, {repo_key}"
                )
        typed_paths[placement].append((repo_key, typed_path))
        if placement == "project_root":
            project_roots.append(repo_key)

        if classification == "unmanaged":
            _validate_exact_fields(raw_row, UNMANAGED_FIELDS, repo_key=repo_key)
            if placement == "project_root":
                raise ManifestError("the project root cannot be unmanaged")
            if raw_row["acceptance_layer"] != "none":
                raise ManifestError(f"unmanaged repo {repo_key} has acceptance work")
            if raw_row["smoke_policy"] != "none":
                raise ManifestError(f"unmanaged repo {repo_key} has a smoke policy")
            continue

        _validate_exact_fields(
            raw_row,
            MANAGED_FIELDS,
            repo_key=repo_key,
            optional=frozenset(MANAGED_OPTIONAL_FIELDS),
        )
        _validate_remote(raw_row["remote"], field=f"{repo_key}.remote")
        remote = raw_row["remote"]
        if remote.lower() in managed_remotes:
            raise ManifestError(
                f"duplicate managed remote: {managed_remotes[remote.lower()]}, {repo_key}"
            )
        managed_remotes[remote.lower()] = repo_key
        _validate_branch(raw_row["base_branch"], field=f"{repo_key}.base_branch")
        order = raw_row["provision_order"]
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            raise ManifestError(f"repo {repo_key} provision_order is invalid")
        if order in managed_orders:
            raise ManifestError(
                f"duplicate provision_order: {managed_orders[order]}, {repo_key}"
            )
        managed_orders[order] = repo_key
        if raw_row["acceptance_layer"] not in ACCEPTANCE_LAYERS:
            raise ManifestError(f"repo {repo_key} acceptance_layer is invalid")
        if raw_row["smoke_policy"] not in SMOKE_POLICIES:
            raise ManifestError(f"repo {repo_key} smoke_policy is invalid")
        after = raw_row["provision_after"]
        if not isinstance(after, list) or not all(
            isinstance(item, str) and REPO_KEY_RE.fullmatch(item) for item in after
        ):
            raise ManifestError(f"repo {repo_key} provision_after is invalid")
        if len(after) != len(set(after)) or repo_key in after:
            raise ManifestError(f"repo {repo_key} provision_after is cyclic or duplicate")

        if "dependency_lock" in raw_row:
            _validate_repo_relative_path(
                raw_row["dependency_lock"],
                field=f"{repo_key}.dependency_lock",
                allow_dot=False,
            )
            lock_owners.append(repo_key)
        has_source = "python_source_path" in raw_row
        has_import = "python_import_name" in raw_row
        if has_source != has_import:
            raise ManifestError(
                f"repo {repo_key} must declare python_source_path and "
                "python_import_name together"
            )
        if has_source:
            _validate_repo_relative_path(
                raw_row["python_source_path"],
                field=f"{repo_key}.python_source_path",
                allow_dot=True,
            )
            import_name = _require_string(
                raw_row["python_import_name"],
                field=f"{repo_key}.python_import_name",
            )
            if IMPORT_NAME_RE.fullmatch(import_name) is None:
                raise ManifestError(
                    f"repo {repo_key} python_import_name is invalid"
                )

    if project_roots != [project_key]:
        raise ManifestError(
            "manifest must contain exactly one project_root row whose repo_key "
            "equals project_key"
        )
    if len(lock_owners) > 1:
        raise ManifestError(
            "at most one repository may declare dependency_lock: "
            + ", ".join(sorted(lock_owners))
        )

    for repo_key, row in rows_by_key.items():
        if row["classification"] not in MANAGED_CLASSIFICATIONS:
            continue
        for prerequisite in row["provision_after"]:
            prerequisite_row = rows_by_key.get(prerequisite)
            if (
                prerequisite_row is None
                or prerequisite_row["classification"] not in MANAGED_CLASSIFICATIONS
            ):
                raise ManifestError(
                    f"repo {repo_key} references unmanaged or missing prerequisite"
                )
            if prerequisite_row["provision_order"] >= row["provision_order"]:
                raise ManifestError(
                    f"repo {repo_key} prerequisite is not earlier in provision order"
                )

    ordered_managed = [managed_orders[key] for key in sorted(managed_orders)]
    return {
        "schema_version": 1,
        "validation_id": VALIDATION_ID,
        "status": "valid",
        "manifest_relative_path": MANIFEST_RELATIVE_PATH,
        "manifest_digest": canonical_json_sha256(manifest),
        "project_key": manifest["project_key"],
        "repo_count": len(repos),
        "classification_counts": counts,
        "project_root_repo_key": project_roots[0],
        "ordered_managed_repos": ordered_managed,
        "inventory_observed": False,
        "state_mutated": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_manifest(repo_root=args.repo_root)
    except (ManifestError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "validation_id": VALIDATION_ID,
                    "status": "failed",
                    "reason": str(exc),
                    "inventory_observed": False,
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
