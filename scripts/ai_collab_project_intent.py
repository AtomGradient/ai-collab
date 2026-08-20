#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Resolve stable team intent, legacy declarations, or a fileless Git root.

The returned render is machine-independent and contains no absolute path.  It
is safe for the Host to persist privately and pass back to the generic project
adapter.  Tool-version pins live in this render, never in team intent.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from ai_collab_project_support import canonical_json_sha256, sha256_file
import ai_collab_project_descriptor as descriptor_validator
import ai_collab_repo_manifest as manifest_validator


INTENT_RELATIVE_PATH = ".aicollab/project.yaml"
INTENT_SCHEMA_VERSION = 1
READER_VERSION = "0.1.7"
MAX_INTENT_BYTES = 256 * 1024
BUILTIN_GATE_PROFILE = "builtin.standard-v1"
BUILTIN_POLICY_PROFILE = "builtin.standard-v1"
PRODUCT_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_POLICY_PATH = PRODUCT_ROOT / "ai_collab_team_policies.json"
WORKSPACE_ADAPTER_ID = "ai-collab-workspace-v1"
ENVIRONMENT_ADAPTER_ID = "ai-collab-environment-v1"
PRODUCT_CONTRACT_VERSION = "1.0"
PARTICIPANT_DRIVER_CONTRACT = 2
COLLABORATION_POLICY_SCHEMA = 1
PROJECT_KEY_SANITIZE_RE = re.compile(r"[^a-z0-9-]+")
SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class IntentError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise IntentError("project.intent-invalid", "project intent keys must be strings")
        if key in result:
            raise IntentError(
                "project.intent-invalid",
                f"project intent duplicates field {key} at line {key_node.start_mark.line + 1}",
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _parse_yaml_mapping(text: str, *, label: str) -> dict[str, Any]:
    if "\t" in text or "\r" in text:
        raise IntentError("project.intent-invalid", f"{label} must use LF and spaces only")
    try:
        for token in yaml.scan(text):
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise IntentError(
                    "project.intent-invalid",
                    f"{label} aliases, anchors, and tags are unsupported",
                )
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except IntentError:
        raise
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}" if mark is not None else ""
        raise IntentError(
            "project.intent-invalid", f"{label} is invalid YAML{location}"
        ) from exc
    if not isinstance(value, dict):
        raise IntentError("project.intent-invalid", f"{label} must be a mapping")
    return value


def _version_tuple(value: Any, *, field: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise IntentError("project.intent-invalid", f"{field} must be a version string")
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise IntentError("project.intent-invalid", f"{field} is invalid")
    return tuple(int(item) for item in match.groups())


def _safe_reference(root: Path, raw: Any, *, field: str) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise IntentError("project.intent-invalid", f"{field} must be a relative path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise IntentError("project.intent-invalid", f"{field} escapes the project root")
    lexical = root.joinpath(*relative.parts)
    if lexical.is_symlink():
        raise IntentError("project.intent-invalid", f"{field} must not be a symlink")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise IntentError("project.intent-invalid", f"{field} is unavailable") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise IntentError("project.intent-invalid", f"{field} escapes the project root")
    return resolved, relative.as_posix()


def _validate_manifest_mapping(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse the frozen manifest validator without writing canonical source."""

    text = yaml.safe_dump(
        dict(manifest), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    with tempfile.TemporaryDirectory(prefix="ai-collab-manifest-validate.") as temporary:
        root = Path(temporary)
        (root / manifest_validator.MANIFEST_RELATIVE_PATH).write_text(
            text, encoding="utf-8"
        )
        return manifest_validator.validate_manifest(repo_root=root)


def _load_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntentError("project.manifest-invalid", "repository manifest is unavailable") from exc
    try:
        manifest = manifest_validator._parse_manifest(text)  # noqa: SLF001
        result = _validate_manifest_mapping(manifest)
    except manifest_validator.ManifestError as exc:
        raise IntentError("project.manifest-invalid", str(exc)) from exc
    return manifest, result


def _git(root: Path, *arguments: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return 127, ""
    return completed.returncode, completed.stdout.decode("utf-8", errors="replace").strip()


def _is_git_root(path: Path) -> bool:
    status, top = _git(path, "rev-parse", "--show-toplevel")
    if status != 0:
        return False
    try:
        return Path(top).resolve(strict=True) == path.resolve(strict=True)
    except OSError:
        return False


def project_key_from_root(root: Path) -> str:
    candidate = PROJECT_KEY_SANITIZE_RE.sub("-", root.name.lower()).strip("-")
    if not candidate or not candidate[0].isalpha():
        candidate = f"project-{candidate}".strip("-")
    candidate = candidate[:64].rstrip("-")
    if descriptor_validator.PROJECT_KEY_RE.fullmatch(candidate) is None:
        raise IntentError("project.intent-invalid", "project name cannot become a project key")
    return candidate


def _observed_repo(path: Path, *, key: str, placement: str, logical_path: str) -> dict[str, Any]:
    remote_status, remote = _git(path, "config", "--get", "remote.origin.url")
    branch_status, branch = _git(path, "symbolic-ref", "--short", "HEAD")
    head_status, head = _git(path, "rev-parse", "HEAD")
    return {
        "repo_key": key,
        "placement": placement,
        "path": logical_path,
        "remote": remote if remote_status == 0 and remote else None,
        "branch": branch if branch_status == 0 and branch else "main",
        "head": head if head_status == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head) else None,
    }


def discover_repositories(
    root: Path, *, root_project_key: str | None = None
) -> list[dict[str, Any]]:
    if not _is_git_root(root):
        raise IntentError("project.not-git", "selected project is not a Git repository")
    project_key = root_project_key or project_key_from_root(root)
    observed = [
        _observed_repo(
            root, key=project_key, placement="project_root", logical_path="."
        )
    ]
    seen = {project_key}
    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if child.is_symlink() or not child.is_dir() or not _is_git_root(child):
            continue
        if manifest_validator.PATH_PART_RE.fullmatch(child.name) is None:
            continue
        key = PROJECT_KEY_SANITIZE_RE.sub("-", child.name.lower()).strip("-")
        if not key or key in seen or manifest_validator.REPO_KEY_RE.fullmatch(key) is None:
            continue
        observed.append(
            _observed_repo(
                child, key=key, placement="project_child", logical_path=child.name
            )
        )
        seen.add(key)
    return observed


def _fileless_manifest(
    root: Path, *, project_key: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    observed = discover_repositories(root, root_project_key=project_key)
    project_key = observed[0]["repo_key"]
    rows: list[dict[str, Any]] = []
    order = 0
    for item in observed:
        row = {
            "repo_key": item["repo_key"],
            "classification": "required",
            "placement": item["placement"],
            "path": item["path"],
            "remote": item["remote"],
            "base_branch": item["branch"],
            "provision_order": order,
            "provision_after": [] if order == 0 else [project_key],
            "acceptance_layer": "base",
            "smoke_policy": "required" if order == 0 else "optional",
        }
        rows.append(row)
        order += 10
    return {"schema_version": 1, "project_key": project_key, "repos": rows}, observed


def _builtin_profile(*, field: str, profile: str) -> dict[str, Any]:
    expected = BUILTIN_GATE_PROFILE if field == "gates" else BUILTIN_POLICY_PROFILE
    if profile != expected:
        raise IntentError(
            "project.intent-invalid", f"{field}.profile is not shipped by this AICollab"
        )
    digest = (
        canonical_json_sha256(
            {"profile_id": BUILTIN_GATE_PROFILE, "semantics": "no-project-gates-v1"}
        )
        if field == "gates"
        else sha256_file(BUILTIN_POLICY_PATH)
    )
    return {"kind": "builtin", "profile_id": profile, "digest": digest}


def _profile_or_registry(
    root: Path, value: Any, *, field: str, builtin: str
) -> dict[str, Any]:
    if value is None:
        return _builtin_profile(field=field, profile=builtin)
    if not isinstance(value, dict) or len(value) != 1:
        raise IntentError("project.intent-invalid", f"{field} must select one source")
    if set(value) == {"profile"}:
        profile = value["profile"]
        if not isinstance(profile, str) or not profile:
            raise IntentError("project.intent-invalid", f"{field}.profile is invalid")
        return _builtin_profile(field=field, profile=profile)
    if set(value) == {"registry"}:
        path, relative = _safe_reference(root, value["registry"], field=f"{field}.registry")
        return {
            "kind": "project-registry",
            "relative_path": relative,
            "digest": sha256_file(path),
        }
    raise IntentError("project.intent-invalid", f"{field} source is unsupported")


def _load_intent(root: Path, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        if path.stat().st_size > MAX_INTENT_BYTES or path.is_symlink():
            raise IntentError("project.intent-invalid", "project intent is not a regular bounded file")
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntentError("project.intent-invalid", "project intent is unavailable") from exc
    value = _parse_yaml_mapping(text, label="project intent")
    fields = {"schema_version", "min_reader", "project_key", "repos", "gates", "collaboration"}
    missing = fields - set(value)
    if missing:
        raise IntentError("project.intent-invalid", "project intent fields differ")
    unknown = sorted(set(value) - fields)
    if len(unknown) > 32 or any(len(field) > 128 for field in unknown):
        raise IntentError("project.intent-invalid", "project intent extensions are invalid")
    value = {field: value[field] for field in fields}
    schema_version = value["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise IntentError("project.intent-invalid", "project intent schema_version is invalid")
    if schema_version > INTENT_SCHEMA_VERSION:
        raise IntentError("project.intent-too-new", "project intent requires a newer AICollab")
    if schema_version != INTENT_SCHEMA_VERSION:
        raise IntentError("project.intent-invalid", "project intent schema is unsupported")
    if _version_tuple(value["min_reader"], field="min_reader") > _version_tuple(
        READER_VERSION, field="reader version"
    ):
        raise IntentError(
            "project.intent-too-new",
            f"project requires AICollab {value['min_reader']} or newer",
        )
    project_key = value["project_key"]
    if not isinstance(project_key, str) or manifest_validator.REPO_KEY_RE.fullmatch(project_key) is None:
        raise IntentError("project.intent-invalid", "project_key is invalid")
    manifest = {"schema_version": 1, "project_key": project_key, "repos": value["repos"]}
    try:
        manifest_result = _validate_manifest_mapping(manifest)
    except manifest_validator.ManifestError as exc:
        raise IntentError("project.intent-invalid", str(exc)) from exc
    return value, {
        "manifest": manifest,
        "manifest_result": manifest_result,
        "gate": _profile_or_registry(
            root, value["gates"], field="gates", builtin=BUILTIN_GATE_PROFILE
        ),
        "collaboration": _profile_or_registry(
            root,
            value["collaboration"],
            field="collaboration",
            builtin=BUILTIN_POLICY_PROFILE,
        ),
        "intent_digest": canonical_json_sha256(value),
        "warnings": [f"intent.unknown-field:{field}" for field in unknown],
    }


def _legacy(root: Path) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    descriptor_path = root / descriptor_validator.DESCRIPTOR_RELATIVE_PATH
    default_manifest = root / manifest_validator.MANIFEST_RELATIVE_PATH
    warnings: list[str] = []
    descriptor: dict[str, Any] | None = None
    manifest_path = default_manifest
    if descriptor_path.is_file() and not descriptor_path.is_symlink():
        try:
            descriptor = descriptor_validator.parse_descriptor(
                descriptor_path.read_text(encoding="utf-8")
            )
        except (OSError, descriptor_validator.DescriptorError) as exc:
            raise IntentError("project.descriptor-invalid", str(exc)) from exc
        raw_manifest = descriptor.get("repo_manifest")
        if isinstance(raw_manifest, str):
            try:
                manifest_path, _ = _safe_reference(
                    root, raw_manifest, field="legacy repo_manifest"
                )
            except IntentError:
                warnings.append("legacy.manifest-reference-unavailable")
                manifest_path = default_manifest
    else:
        warnings.append("legacy.descriptor-missing")

    if manifest_path.is_file() and not manifest_path.is_symlink():
        manifest, _ = _load_manifest(manifest_path)
    else:
        warnings.append("legacy.manifest-missing")
        manifest, _observed = _fileless_manifest(
            root,
            project_key=(
                descriptor["project_key"] if descriptor is not None else None
            ),
        )

    if descriptor is not None and descriptor["project_key"] != manifest["project_key"]:
        raise IntentError(
            "project.partial-configuration",
            "legacy descriptor and manifest project keys differ",
        )
    gate = _builtin_profile(field="gates", profile=BUILTIN_GATE_PROFILE)
    if descriptor is not None:
        raw_gate = descriptor.get("gate_registry")
        try:
            gate_path, gate_ref = _safe_reference(root, raw_gate, field="legacy gate_registry")
        except IntentError:
            warnings.append("legacy.gate-registry-unavailable")
        else:
            gate = {
                "kind": "project-registry",
                "relative_path": gate_ref,
                "digest": sha256_file(gate_path),
            }
    policy_path = root / "ai_collab_team_policies.json"
    collaboration = _builtin_profile(
        field="collaboration", profile=BUILTIN_POLICY_PROFILE
    )
    if policy_path.is_file() and not policy_path.is_symlink():
        collaboration = {
            "kind": "project-registry",
            "relative_path": "ai_collab_team_policies.json",
            "digest": sha256_file(policy_path),
        }
    source_kind = "legacy" if not warnings else "legacy-partial"
    return source_kind, manifest, gate, collaboration, warnings


def _observe_against_manifest(
    root: Path, manifest: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actual = discover_repositories(root)
    actual_by_location = {(item["placement"], item["path"]): item for item in actual}
    declared_locations: set[tuple[str, str]] = set()
    observations: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for row in manifest["repos"]:
        location = (row["placement"], row["path"])
        declared_locations.add(location)
        item = actual_by_location.get(location)
        if item is None and row["placement"] == "bundle_sibling":
            sibling = root.parent / row["path"]
            if (
                not sibling.is_symlink()
                and sibling.is_dir()
                and _is_git_root(sibling)
            ):
                item = _observed_repo(
                    sibling,
                    key=row["repo_key"],
                    placement="bundle_sibling",
                    logical_path=row["path"],
                )
        if item is None:
            status = "missing" if row["classification"] != "unmanaged" else "absent"
            observation = {
                "repo_key": row["repo_key"],
                "path": row["path"],
                "classification": row["classification"],
                "status": status,
            }
            observations.append(observation)
            if status == "missing":
                changes.append(observation)
            continue
        status = "present"
        reasons: list[str] = []
        if row["classification"] != "unmanaged":
            if not row.get("remote"):
                reasons.append("remote-unavailable")
            if item["remote"] != row.get("remote"):
                reasons.append("remote")
            # A present checkout belongs to the employee: feature branches,
            # detached HEADs, and ahead/behind state are normal WIP, not team
            # intent drift.  ``base_branch`` is consulted only when the
            # Workspace adapter must clone a repository that is absent.
        if reasons:
            status = "drift"
        observation = {
            "repo_key": row["repo_key"],
            "path": row["path"],
            "classification": row["classification"],
            "status": status,
        }
        if reasons:
            observation["reasons"] = reasons
            changes.append(observation)
        observations.append(observation)
    for item in actual:
        location = (item["placement"], item["path"])
        if location in declared_locations:
            continue
        change = {
            "repo_key": item["repo_key"],
            "path": item["path"],
            "classification": "undeclared",
            "status": "undeclared",
        }
        observations.append(change)
        changes.append(change)
    observations.sort(key=lambda item: (item["path"].casefold(), item["repo_key"]))
    changes.sort(key=lambda item: (item["status"], item["path"].casefold()))
    return observations, changes


def resolve_project(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    intent_path = root / INTENT_RELATIVE_PATH
    warnings: list[str] = []
    if intent_path.exists() or intent_path.is_symlink():
        intent, loaded = _load_intent(root, intent_path)
        source_kind = "team-intent"
        source_digest = loaded["intent_digest"]
        intent_schema_version: int | None = intent["schema_version"]
        manifest = loaded["manifest"]
        manifest_result = loaded["manifest_result"]
        gate = loaded["gate"]
        collaboration = loaded["collaboration"]
        warnings.extend(loaded["warnings"])
    elif (
        (root / descriptor_validator.DESCRIPTOR_RELATIVE_PATH).exists()
        or (root / manifest_validator.MANIFEST_RELATIVE_PATH).exists()
    ):
        source_kind, manifest, gate, collaboration, warnings = _legacy(root)
        source_digest = canonical_json_sha256(
            {"kind": source_kind, "manifest": manifest, "gate": gate, "collaboration": collaboration}
        )
        intent_schema_version = None
        manifest_result = {"manifest_digest": canonical_json_sha256(manifest)}
    else:
        manifest, _actual = _fileless_manifest(root)
        source_kind = "fileless"
        source_digest = canonical_json_sha256(
            {"kind": source_kind, "project_key": manifest["project_key"], "repos": manifest["repos"]}
        )
        intent_schema_version = None
        manifest_result = {"manifest_digest": canonical_json_sha256(manifest)}
        gate = _builtin_profile(field="gates", profile=BUILTIN_GATE_PROFILE)
        collaboration = _builtin_profile(
            field="collaboration", profile=BUILTIN_POLICY_PROFILE
        )

    observations, changes = _observe_against_manifest(root, manifest)
    availability = {
        "status": "attention" if changes or warnings else "ready",
        "observations": observations,
        "changes": changes,
        "warnings": sorted(warnings),
    }
    availability["fingerprint"] = canonical_json_sha256(availability)
    render = {
        "render_contract_version": 1,
        "source": {
            "kind": source_kind,
            "intent_schema_version": intent_schema_version,
            "source_digest": source_digest,
        },
        "project": {
            "project_key": manifest["project_key"],
            "product_contract_version": PRODUCT_CONTRACT_VERSION,
            "workspace_adapter_id": WORKSPACE_ADAPTER_ID,
            "environment_adapter_id": ENVIRONMENT_ADAPTER_ID,
            "participant_driver_contract": PARTICIPANT_DRIVER_CONTRACT,
            "collaboration_policy_schema": COLLABORATION_POLICY_SCHEMA,
        },
        "repo_manifest": manifest,
        "repo_manifest_digest": manifest_result["manifest_digest"],
        "gate": gate,
        "collaboration": collaboration,
        "availability": availability,
    }
    render["render_digest"] = canonical_json_sha256(
        {key: value for key, value in render.items() if key != "availability"}
    )
    return render


def draft_intent(root: Path) -> dict[str, Any]:
    """Return a deterministic owner-private proposal; never write the project."""

    root = root.resolve(strict=True)
    resolved = resolve_project(root)
    manifest = resolved["repo_manifest"]
    incomplete = [
        row["repo_key"]
        for row in manifest["repos"]
        if row["classification"] != "unmanaged" and not row.get("remote")
    ]
    if incomplete:
        raise IntentError(
            "project.intent-proposal-incomplete",
            "Team intent needs a canonical Git remote for: " + ", ".join(incomplete),
        )
    gate = resolved["gate"]
    collaboration = resolved["collaboration"]

    def source(value: Mapping[str, Any]) -> dict[str, str]:
        if value["kind"] == "project-registry":
            return {"registry": str(value["relative_path"])}
        return {"profile": str(value["profile_id"])}

    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "min_reader": READER_VERSION,
        "project_key": manifest["project_key"],
        "repos": manifest["repos"],
        "gates": source(gate),
        "collaboration": source(collaboration),
    }
    return {
        "intent": intent,
        "intent_digest": canonical_json_sha256(intent),
        "yaml": yaml.safe_dump(intent, sort_keys=False, allow_unicode=True),
    }
