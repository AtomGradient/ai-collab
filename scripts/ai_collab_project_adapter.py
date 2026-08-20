#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""Generic Workspace/Environment adapter for the Harness Host.

This adapter serves fileless Git roots, stable ``.aicollab/project.yaml`` team
intent, and every legacy preview declaration. The Host supplies a pinned
resolved render for Scenario work; nothing project-specific lives in this code.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import venv
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ai_collab_project_support import canonical_json_sha256, sha256_file
import ai_collab_project_descriptor as descriptor_validator
import ai_collab_project_intent as intent_resolver
import ai_collab_repo_manifest as manifest_validator


ROOT = Path(
    os.environ.get("AI_COLLAB_PROJECT_ROOT", Path(__file__).resolve().parents[1])
).resolve()
ADAPTER_ID = "ai-collab-project-adapter-v1"
WORKSPACE_ADAPTER_ID = "ai-collab-workspace-v1"
ENVIRONMENT_ADAPTER_ID = "ai-collab-environment-v1"
IMPORT_NAME_RE = manifest_validator.IMPORT_NAME_RE
PLAN_PAYLOAD_SCHEMA = {
    "type": "object",
    "properties": {"environment_mode": {"const": "minimal-editable"}},
    "additionalProperties": False,
}
PRIVATE_WORKSPACE_FORMAT = {
    "version": 1,
    "layout": "bundle/<canonical project directory> plus bundle siblings",
    "publish": "owned staging rename",
}
PRIVATE_ENVIRONMENT_FORMAT = {
    "version": 1,
    "kind": "python venv with scenario-local source path bindings",
}
ZERO_SHA1 = "0" * 40
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
SAFE_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DESTROY_STAGING_PREFIX = ".destroying-"
DARWIN_RENAME_EXCL = 0x00000004
LINUX_RENAME_NOREPLACE = 0x00000001
NO_REPLACE_CONFLICT_ERRNOS = frozenset({errno.EEXIST, errno.ENOTEMPTY})
NO_REPLACE_UNAVAILABLE_ERRNOS = frozenset({errno.ENOSYS, errno.ENOTSUP})
NO_REPLACE_PROVEN_NO_EFFECT_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
        errno.EXDEV,
        errno.EINVAL,
    }
)


class AdapterError(ValueError):
    """A safe, typed project-adapter refusal returned to the Host."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "adapter.rejected",
        retryable: bool = False,
        mutation_state: str = "not_started",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.mutation_state = mutation_state


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _emit_progress(
    component_id: str,
    index: int,
    total: int,
    state: str,
) -> None:
    """Write one bounded logical progress event to the declared inherited FD."""

    raw_descriptor = os.environ.get("AI_COLLAB_PROGRESS_FD")
    if raw_descriptor is None:
        return
    if not raw_descriptor.isascii() or not raw_descriptor.isdecimal():
        raise AdapterError(
            "progress side channel is invalid",
            code="adapter.progress-invalid",
        )
    descriptor = int(raw_descriptor)
    if descriptor < 3:
        raise AdapterError(
            "progress side channel is invalid",
            code="adapter.progress-invalid",
        )
    encoded = _canonical_bytes(
        {
            "component_id": component_id,
            "index": index,
            "total": total,
            "state": state,
        }
    ) + b"\n"
    if len(encoded) > 2048:
        raise AdapterError(
            "progress event exceeds its bound",
            code="adapter.progress-invalid",
        )
    try:
        written = os.write(descriptor, encoded)
    except OSError:
        # The progress observer is not operation authority. Losing it cannot
        # cancel repository materialization or alter the final JSON reply.
        return
    if written != len(encoded):
        raise AdapterError(
            "progress event could not be written atomically",
            code="adapter.progress-invalid",
        )


def _git_environment(**overrides: str) -> dict[str, str]:
    """Return a private Git environment with optional metadata writes disabled."""

    environment = dict(os.environ)
    environment.update(overrides)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _run_git(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if completed.returncode != 0:
        raise AdapterError("Git operation failed")
    return completed.stdout


def _probe_git(repo: Path, *arguments: str) -> tuple[int, bytes]:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    return completed.returncode, completed.stdout


def _descriptor(kind: str, adapter_id: str) -> dict[str, Any]:
    return {
        "adapter_contract_version": 1,
        "adapter": {
            "adapter_kind": kind,
            "adapter_id": adapter_id,
            "contract_version": 1,
        },
        "operations": [
            "plan",
            "provision",
            "status",
            "repair",
            "destroy",
            "recover",
        ],
        "project_payload_schema_digest": canonical_json_sha256(PLAN_PAYLOAD_SCHEMA),
        "private_binding_format_digest": canonical_json_sha256(
            PRIVATE_WORKSPACE_FORMAT if kind == "workspace" else PRIVATE_ENVIRONMENT_FORMAT
        ),
        "supports_resume": True,
        "supports_repair": True,
        "supports_destroy": True,
    }


def _descriptors() -> list[dict[str, Any]]:
    return [
        _descriptor("workspace", WORKSPACE_ADAPTER_ID),
        _descriptor("environment", ENVIRONMENT_ADAPTER_ID),
    ]


def _resolved_render() -> dict[str, Any]:
    encoded = os.environ.get("AI_COLLAB_PROJECT_RENDER")
    if encoded is None:
        return intent_resolver.resolve_project(ROOT)
    try:
        value = json.loads(encoded)
        expected = value["render_digest"]
        material = {
            key: item
            for key, item in value.items()
            if key not in {"render_digest", "availability"}
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AdapterError(
            "Host project render is invalid", code="project.render-invalid"
        ) from exc
    if not isinstance(expected, str) or canonical_json_sha256(material) != expected:
        raise AdapterError(
            "Host project render digest differs", code="project.render-invalid"
        )
    return value


def _project_inputs(
    render: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    render = dict(render) if render is not None else _resolved_render()
    project = render["project"]
    descriptor = {
        "schema_version": 1,
        "project_key": project["project_key"],
        "product_contract_version": project["product_contract_version"],
        "workspace_adapter": project["workspace_adapter_id"],
        "repo_manifest": "repo_manifest.yaml",
        "environment_adapter": project["environment_adapter_id"],
        "gate_registry": render["gate"],
        "participant_driver_contract": project["participant_driver_contract"],
        "collaboration_policy_schema": project["collaboration_policy_schema"],
    }
    return (
        descriptor,
        {"descriptor_digest": render["render_digest"]},
        render["repo_manifest"],
        {"manifest_digest": render["repo_manifest_digest"]},
    )


def _register(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"canonical_project_path"}:
        raise AdapterError("register payload fields differ")
    supplied = payload["canonical_project_path"]
    if not isinstance(supplied, str) or not supplied:
        raise AdapterError("canonical project path is invalid")
    try:
        project_root = Path(supplied).resolve(strict=True)
    except OSError as exc:
        raise AdapterError("canonical project path is unavailable") from exc
    if project_root != ROOT or not project_root.is_dir():
        raise AdapterError("canonical project root differs")
    render = _resolved_render()
    descriptor, descriptor_result, _, manifest_result = _project_inputs(render)
    if (
        descriptor["workspace_adapter"] != WORKSPACE_ADAPTER_ID
        or descriptor["environment_adapter"] != ENVIRONMENT_ADAPTER_ID
    ):
        raise AdapterError("descriptor names a different adapter implementation")
    descriptors = _descriptors()
    return {
        "project": {
            "project_key": descriptor["project_key"],
            "project_binding_digest": descriptor_result["descriptor_digest"],
            "product_contract_version": descriptor["product_contract_version"],
            "workspace_adapter_id": descriptor["workspace_adapter"],
            "environment_adapter_id": descriptor["environment_adapter"],
            "participant_driver_contract": descriptor[
                "participant_driver_contract"
            ],
            "collaboration_policy_schema": descriptor[
                "collaboration_policy_schema"
            ],
            "repo_manifest_digest": manifest_result["manifest_digest"],
            "adapter_capability_digest": canonical_json_sha256(descriptors),
        },
        "render": render,
    }


PRODUCT_ROOT = Path(__file__).resolve().parents[1]


def _bootstrap(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build an owner-private intent proposal without writing canonical source."""
    if set(payload) != {"canonical_project_path"}:
        raise AdapterError("bootstrap payload fields differ")
    supplied = payload["canonical_project_path"]
    if not isinstance(supplied, str) or not supplied:
        raise AdapterError("canonical project path is invalid")
    try:
        root = Path(supplied).resolve(strict=True)
    except OSError as exc:
        raise AdapterError("canonical project path is unavailable") from exc
    if root != ROOT or not root.is_dir():
        raise AdapterError("canonical project root differs")

    proposal = intent_resolver.draft_intent(root)
    return {
        "bootstrap": {
            "created": [],
            "already_configured": (root / intent_resolver.INTENT_RELATIVE_PATH).is_file(),
            "project_key": proposal["intent"]["project_key"],
            "proposal": {
                "intent_digest": proposal["intent_digest"],
                "yaml": proposal["yaml"],
            },
        }
    }


def _collaboration_templates(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload:
        raise AdapterError("collaboration template payload fields differ")
    source = _resolved_render()["collaboration"]
    has_snapshot = (
        "registry_snapshot" in source
        or "registry_snapshot_digest" in source
    )
    if has_snapshot:
        value = source.get("registry_snapshot")
        if source.get("registry_snapshot_digest") != canonical_json_sha256(value):
            raise AdapterError("collaboration template registry digest differs")
    else:
        # Compatibility for v0.1.6.1 and prerelease registrations whose
        # accepted render predates embedded catalogs. Reconciliation replaces
        # this pointer with a self-contained snapshot for every new Scenario.
        path = (
            ROOT / source["relative_path"]
            if source["kind"] == "project-registry"
            else PRODUCT_ROOT / "ai_collab_team_policies.json"
        )
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_uid != os.getuid()
            or source.get("digest") != sha256_file(path)
        ):
            raise AdapterError("collaboration template registry is unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdapterError("collaboration template registry is invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "templates"}
        or value["schema_version"] != 1
        or not isinstance(value["templates"], list)
        or not value["templates"]
        or len(value["templates"]) > 64
        or any(not isinstance(item, dict) for item in value["templates"])
        or len(_canonical_bytes(value)) > 512 * 1024
    ):
        raise AdapterError("collaboration template registry differs")
    return {"templates": value["templates"]}


def _source_path(row: Mapping[str, Any]) -> Path:
    placement = row["placement"]
    logical_path = Path(row["path"])
    if logical_path.is_absolute():
        raise AdapterError("declared source path is not relative")
    if placement == "project_root":
        path = ROOT
        expected_base = ROOT
    elif placement == "project_child":
        path = ROOT / logical_path
        expected_base = ROOT
    elif placement == "bundle_sibling":
        path = ROOT.parent / logical_path
        expected_base = ROOT.parent
    else:
        raise AdapterError("declared source placement is unavailable")
    current = expected_base
    if placement != "project_root":
        for part in logical_path.parts:
            current /= part
            if current.is_symlink():
                raise AdapterError("declared source path traverses a symlink")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise AdapterError("declared source repository is unavailable")
    if placement == "project_root" and resolved != ROOT:
        raise AdapterError("declared project root differs")
    if placement == "project_child" and not resolved.is_relative_to(ROOT):
        raise AdapterError("declared project child escapes project root")
    if placement == "bundle_sibling" and (
        not resolved.is_relative_to(ROOT.parent) or resolved.is_relative_to(ROOT)
    ):
        raise AdapterError("declared bundle sibling is misplaced")
    _run_git(resolved, "rev-parse", "--git-dir")
    return resolved


def _source_path_if_present(row: Mapping[str, Any]) -> Path | None:
    """Resolve a declared checkout, while treating an absent repo as availability."""

    placement = row["placement"]
    logical_path = Path(row["path"])
    if logical_path.is_absolute():
        raise AdapterError("declared source path is not relative")
    if placement == "project_root":
        path = ROOT
    elif placement == "project_child":
        path = ROOT / logical_path
    elif placement == "bundle_sibling":
        path = ROOT.parent / logical_path
    else:
        raise AdapterError("declared source placement is unavailable")
    if path.is_symlink():
        raise AdapterError("declared source repository traverses a symlink")
    if not path.exists():
        return None
    return _source_path(row)


def _selected_rows(
    manifest: Mapping[str, Any], requested: Sequence[str]
) -> list[dict[str, Any]]:
    """Resolve the component rows a workspace plan provisions.

    An empty ``requested`` selects the full managed workspace: every manifest
    row whose classification is not ``unmanaged``. A non-empty ``requested``
    keeps the narrower historical semantics, where the required rows form a
    floor and the request is additive. Both forms are then closed transitively
    over ``provision_after`` so a plan never omits a declared prerequisite.
    """
    rows = {row["repo_key"]: row for row in manifest["repos"]}
    if requested:
        selected = {
            row["repo_key"]
            for row in manifest["repos"]
            if row["classification"] == "required"
        }
        selected.update(requested)
    else:
        selected = {
            row["repo_key"]
            for row in manifest["repos"]
            if row["classification"] != "unmanaged"
        }
    while True:
        before = set(selected)
        for repo_key in tuple(selected):
            row = rows.get(repo_key)
            if row is None or row["classification"] == "unmanaged":
                raise AdapterError("requested component is unmanaged or undeclared")
            selected.update(row["provision_after"])
        if selected == before:
            break
    result = [row for row in manifest["repos"] if row["repo_key"] in selected]
    if not result:
        raise AdapterError("workspace plan has no managed components")
    return result


def _git_identity(path: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    head = _run_git(path, "rev-parse", "HEAD").decode().strip()
    object_format = _run_git(path, "rev-parse", "--show-object-format").decode().strip()
    if object_format not in {"sha1", "sha256"}:
        raise AdapterError("canonical source object format is unsupported")
    _run_git(path, "cat-file", "-e", f"{head}^{{commit}}")
    status_bytes = _run_git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    # The raw configured value, not `remote get-url`: get-url applies the
    # machine's url.<base>.insteadOf rewrites, so on a machine with such a
    # rule the manifest's declared remote would never match even though the
    # repository is configured exactly as declared.
    remote = row.get("remote")
    if not isinstance(remote, str) or not remote:
        raise AdapterError(
            "This repository has no canonical remote. Add an origin before preparing the Workspace.",
            code="workspace.remote-unavailable",
        )
    origin = _run_git(path, "config", "--get", "remote.origin.url").decode().strip()
    if origin != remote:
        raise AdapterError(
            "This checkout's origin does not match the team project definition.",
            code="workspace.source-origin-mismatch",
        )
    if _run_git(path, "rev-parse", "--is-shallow-repository").decode().strip() != "false":
        raise AdapterError(
            "This checkout has partial history. Use your Git client to fetch complete history (or run git fetch --unshallow), then retry Prepare Workspace.",
            code="workspace.shallow-source",
        )
    config_status, promisor_config = _probe_git(
        path,
        "config",
        "--get-regexp",
        r"^(extensions\.partial[Cc]lone|remote\..*\.promisor)$",
    )
    if config_status not in {0, 1}:
        raise AdapterError(
            "This checkout's partial-clone configuration cannot be read.",
            code="workspace.partial-source-invalid",
        )
    if config_status == 0 or promisor_config.strip():
        raise AdapterError(
            "This checkout uses partial Git object storage. Download all repository objects, then retry Prepare Workspace.",
            code="workspace.partial-source",
        )
    common_dir_raw = _run_git(path, "rev-parse", "--git-common-dir").decode().strip()
    common_dir = Path(common_dir_raw)
    if not common_dir.is_absolute():
        common_dir = path / common_dir
    common_dir = common_dir.resolve(strict=True)
    alternates = common_dir / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise AdapterError(
            "This checkout uses shared alternate Git object storage and cannot be isolated safely.",
            code="workspace.alternate-object-source",
        )
    return {
        "head": head,
        "object_format": object_format,
        "status_digest": hashlib.sha256(status_bytes).hexdigest(),
        "origin_digest": hashlib.sha256(origin.encode("utf-8")).hexdigest(),
    }


def _remote_failure(stderr: bytes, returncode: int, *, mutation_state: str) -> AdapterError:
    detail = stderr.decode("utf-8", errors="replace").lower()
    if "no space left on device" in detail or "disk quota exceeded" in detail:
        return AdapterError(
            "The Scenario Workspace does not have enough free disk space.",
            code="workspace.disk-full",
            mutation_state=mutation_state,
        )
    authentication_markers = (
        "permission denied",
        "authentication failed",
        "could not read username",
        "terminal prompts disabled",
        "publickey",
        "access denied",
        "repository not found",
    )
    if any(marker in detail for marker in authentication_markers):
        return AdapterError(
            "Git credentials are unavailable to AICollab. Sign in to the repository provider, then retry Prepare Workspace.",
            code="workspace.git-auth-required",
            mutation_state=mutation_state,
        )
    network_markers = (
        "could not resolve host",
        "connection timed out",
        "network is unreachable",
        "connection refused",
        "failed to connect",
        "operation timed out",
        "no route to host",
        "connection closed",
    )
    if any(marker in detail for marker in network_markers):
        return AdapterError(
            "The repository network is unavailable. Check the network or VPN, then retry Prepare Workspace.",
            code="workspace.network-unavailable",
            retryable=True,
            mutation_state=mutation_state,
        )
    if returncode == 2:
        return AdapterError(
            "The declared repository branch is unavailable.",
            code="workspace.branch-unavailable",
            mutation_state=mutation_state,
        )
    return AdapterError(
        "The repository could not be downloaded.",
        code="workspace.remote-download-failed",
        mutation_state=mutation_state,
    )


def _remote_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    remote = row.get("remote")
    branch = row.get("base_branch")
    if not isinstance(remote, str) or not remote:
        raise AdapterError(
            "This repository has no canonical remote. Add an origin before preparing the Workspace.",
            code="workspace.remote-unavailable",
        )
    try:
        completed = subprocess.run(
            [
                "git",
                "ls-remote",
                "--exit-code",
                "--refs",
                remote,
                f"refs/heads/{branch}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(GIT_TERMINAL_PROMPT="0"),
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(
            "The repository network timed out. Check the network or VPN, then retry Prepare Workspace.",
            code="workspace.network-unavailable",
            retryable=True,
        ) from exc
    if completed.returncode != 0:
        raise _remote_failure(completed.stderr, completed.returncode, mutation_state="not_started")
    fields = completed.stdout.decode("utf-8", errors="replace").strip().split()
    if len(fields) != 2 or fields[1] != f"refs/heads/{branch}" or re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", fields[0]
    ) is None:
        raise AdapterError("The repository returned an invalid branch identity.")
    object_format = "sha1" if len(fields[0]) == 40 else "sha256"
    return {"head": fields[0], "object_format": object_format}


def _source_wip_digest(
    rows: Sequence[Mapping[str, Any]], components: Sequence[Mapping[str, Any]]
) -> str:
    components_by_id = {item["component_id"]: item for item in components}
    values: list[dict[str, Any]] = []
    for row in rows:
        component = components_by_id[row["repo_key"]]
        source = _source_path_if_present(row)
        if source is None:
            values.append(
                {
                    "component_id": row["repo_key"],
                    "source_kind": "remote",
                    "planned_revision": component["planned_revision"],
                    "source_identity_digest": component["source_identity_digest"],
                }
            )
        else:
            values.append(
                {
                    "component_id": row["repo_key"],
                    "source_kind": "canonical-checkout",
                    **_git_identity(source, row),
                }
            )
    return canonical_json_sha256(
        values
    )


def _estimated_git_bytes(path: Path) -> int:
    values: dict[str, int] = {}
    for line in _run_git(path, "count-objects", "-v").decode().splitlines():
        if ": " not in line:
            continue
        key, raw = line.split(": ", 1)
        if raw.isdigit():
            values[key] = int(raw)
    return 1024 * (values.get("size", 0) + values.get("size-pack", 0))


def _safe_target_ref(scenario_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", scenario_id).strip("-.") or "scenario"
    digest = hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()[:10]
    value = f"ai-collab/{slug[:80]}-{digest}"
    if SAFE_REF_RE.fullmatch(value) is None:
        raise AdapterError("scenario ref cannot be represented safely")
    return value


def _environment_spec(
    rows: Sequence[Mapping[str, Any]], components: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    lock_rows = [row for row in rows if "dependency_lock" in row]
    if len(lock_rows) > 1:
        raise AdapterError("multiple dependency lock declarations are unsupported")
    lock_digest = None
    if lock_rows:
        row = lock_rows[0]
        relative = PurePosixPath(row["dependency_lock"])
        source = _source_path_if_present(row)
        lock_path = source.joinpath(*relative.parts) if source is not None else None
        if lock_path is not None and lock_path.is_file():
            lock_digest = sha256_file(lock_path)
    components_by_id = {item["component_id"]: item for item in components}
    source_bindings = [
        {
            "component_id": row["repo_key"],
            "revision": components_by_id[row["repo_key"]]["planned_revision"],
        }
        for row in rows
    ]
    spec = {
        "environment_kind": "environment.python-venv",
        "python_implementation": sys.implementation.name,
        "python_version": list(sys.version_info[:3]),
        "install_mode": "scenario-source-paths-no-shared-site-packages",
    }
    return {
        "spec": spec,
        "dependency_lock_digest": lock_digest,
        "source_bindings_digest": canonical_json_sha256(source_bindings),
    }


def _plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "operation_id",
        "scenario",
        "scenario_state_revision",
        "workspace_id",
        "requested_component_ids",
        "project_payload",
    }
    if set(payload) != expected:
        raise AdapterError("plan payload fields differ")
    project_payload = payload["project_payload"]
    if not isinstance(project_payload, dict) or set(project_payload) - {"environment_mode"}:
        raise AdapterError("project payload fields differ")
    if project_payload.get("environment_mode", "minimal-editable") != "minimal-editable":
        raise AdapterError("environment mode is unsupported")
    descriptor, descriptor_result, manifest, manifest_result = _project_inputs()
    requested = payload["requested_component_ids"]
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        raise AdapterError("requested component list is invalid")
    rows = _selected_rows(manifest, requested)
    scenario = payload["scenario"]
    target_ref = _safe_target_ref(scenario["scenario_id"])
    components: list[dict[str, Any]] = []
    for row in rows:
        source = _source_path_if_present(row)
        identity = _git_identity(source, row) if source is not None else _remote_identity(row)
        revision_kind = f"scm.git-{identity['object_format']}"
        component = {
            "component_id": row["repo_key"],
            "component_kind": "scm.git-repository",
            "classification": row["classification"],
            "placement": row["placement"],
            "logical_path": row["path"],
            "provision_order": row["provision_order"],
            "dependency_component_ids": list(row["provision_after"]),
            "source_identity_digest": canonical_json_sha256(
                {"remote": row["remote"], "base_branch": row["base_branch"]}
            ),
            "revision_kind": revision_kind,
            "planned_revision": identity["head"],
            "target_ref": target_ref,
            "materialization_mode": (
                "workspace.no-local-clone"
                if source is not None
                else "workspace.remote-clone"
            ),
            "source_mutation_allowed": False,
            "isolated_writable": True,
            "shared_mutable_storage": False,
            "adapter_plan_digest": "",
            "estimated_bytes": _estimated_git_bytes(source) if source is not None else 0,
        }
        component["adapter_plan_digest"] = canonical_json_sha256(
            {
                "adapter": WORKSPACE_ADAPTER_ID,
                "component_id": component["component_id"],
                "planned_revision": component["planned_revision"],
                "target_ref": component["target_ref"],
            }
        )
        components.append(component)
    environment_details = _environment_spec(rows, components)
    environment_id = f"environment:{str(payload['operation_id']).removeprefix('wsop-')}"
    environment = {
        "environment_id": environment_id,
        "environment_kind": environment_details["spec"]["environment_kind"],
        "environment_spec_digest": canonical_json_sha256(environment_details["spec"]),
        "dependency_lock_digest": environment_details["dependency_lock_digest"],
        "source_bindings_digest": environment_details["source_bindings_digest"],
        "writable_scope": "scenario",
        "shared_mutable_dependencies": False,
        "immutable_cache_reuse": True,
        "adapter_plan_digest": canonical_json_sha256(
            {"adapter": ENVIRONMENT_ADAPTER_ID, **environment_details["spec"]}
        ),
        "estimated_bytes": 8 * 1024 * 1024,
    }
    plan = {
        "plan_contract_version": 1,
        "plan_id": f"plan:{str(payload['operation_id']).removeprefix('wsop-')}",
        "plan_generation": 1,
        "operation_id": payload["operation_id"],
        "scenario": scenario,
        "project_key": descriptor["project_key"],
        "project_descriptor_digest": descriptor_result["descriptor_digest"],
        "repo_manifest_digest": manifest_result["manifest_digest"],
        "workspace_adapter": _descriptors()[0]["adapter"],
        "environment_adapter": _descriptors()[1]["adapter"],
        "requested_component_ids": [item["component_id"] for item in components],
        "components": components,
        "environment": environment,
        "source_wip_snapshot_digest": _source_wip_digest(rows, components),
        "total_estimated_bytes": sum(item["estimated_bytes"] for item in components)
        + environment["estimated_bytes"],
        "project_payload_digest": canonical_json_sha256(project_payload),
    }
    return {"descriptors": _descriptors(), "plan": plan}


def _target_path(staging: Path, component: Mapping[str, Any]) -> Path:
    placement = component["placement"]
    logical = component["logical_path"]
    if placement == "project_root":
        return staging / ROOT.name
    if placement == "project_child":
        return staging / ROOT.name / logical
    return staging / logical


def _guard_sources(remote: str, target_ref: str) -> tuple[str, str]:
    provider = f'''#!/usr/bin/env python3
import sys

payload = sys.stdin.buffer.read()
for raw in payload.splitlines():
    fields = raw.decode("utf-8").split()
    if len(fields) != 4:
        raise SystemExit(1)
    local_ref, local_sha, remote_ref, _remote_sha = fields
    if local_ref != "refs/heads/{target_ref}" or remote_ref != "refs/heads/{target_ref}":
        raise SystemExit(1)
    if set(local_sha) == {{"0"}} or remote_ref in {{"refs/heads/main", "refs/heads/master"}}:
        raise SystemExit(1)
'''
    dispatcher = f'''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

payload = sys.stdin.buffer.read()
if len(sys.argv) != 3 or sys.argv[1] != "origin" or sys.argv[2] != {remote!r}:
    raise SystemExit(1)
for raw in payload.splitlines():
    fields = raw.decode("utf-8").split()
    if len(fields) != 4:
        raise SystemExit(1)
    local_ref, local_sha, remote_ref, remote_sha = fields
    if local_ref != "refs/heads/{target_ref}" or remote_ref != "refs/heads/{target_ref}":
        raise SystemExit(1)
    if set(local_sha) == {{"0"}} or remote_ref.startswith("refs/tags/"):
        raise SystemExit(1)
    if set(remote_sha) != {{"0"}}:
        check = subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote_sha, local_sha],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode != 0:
            raise SystemExit(1)
provider = Path(__file__).with_name("ai-collab-provider-pre-push")
result = subprocess.run([str(provider)], input=payload, check=False)
raise SystemExit(result.returncode)
'''
    return dispatcher, provider


def _install_guard(repo: Path, remote: str, target_ref: str) -> str:
    git_dir_raw = _run_git(repo, "rev-parse", "--git-dir").decode().strip()
    git_dir = (repo / git_dir_raw).resolve(strict=True)
    if not git_dir.is_relative_to(repo.resolve()):
        raise AdapterError("scenario Git directory escapes its repository")
    hooks = git_dir / "hooks"
    hooks.mkdir(mode=0o700, exist_ok=True)
    dispatcher, provider = _guard_sources(remote, target_ref)
    values = {
        hooks / "pre-push": dispatcher,
        hooks / "ai-collab-provider-pre-push": provider,
    }
    for path, text in values.items():
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o700)
    hooks_path = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        check=False,
    )
    if hooks_path.returncode not in {0, 1} or hooks_path.stdout.strip():
        raise AdapterError("scenario clone unexpectedly configures core.hooksPath")
    return canonical_json_sha256(
        {"dispatcher": dispatcher, "provider": provider, "remote": remote, "target_ref": target_ref}
    )


def _component_content(repo: Path) -> str:
    head = _run_git(repo, "rev-parse", "HEAD").decode().strip()
    tree = _run_git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    status = _run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    return canonical_json_sha256(
        {"head": head, "tree": tree, "status_digest": hashlib.sha256(status).hexdigest()}
    )


def _materialize_components_unobserved(
    staging: Path,
    plan: Mapping[str, Any],
    rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for component in plan["components"]:
        row = rows[component["component_id"]]
        target = _target_path(staging, component)
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        remote_clone = component["materialization_mode"] == "workspace.remote-clone"
        if remote_clone:
            clone = [
                "git", "clone", "--no-checkout", "--origin", "origin",
                row["remote"], str(target),
            ]
        else:
            source = _source_path(row)
            clone = [
                "git", "clone", "--no-local", "--no-checkout", "--origin",
                "canonical-source", str(source), str(target),
            ]
        try:
            completed = subprocess.run(
                clone,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_environment(GIT_TERMINAL_PROMPT="0"),
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(
                "The repository download timed out. Check the network or VPN, then retry Prepare Workspace.",
                code="workspace.network-unavailable",
                retryable=True,
                mutation_state="started",
            ) from exc
        if completed.returncode != 0:
            if remote_clone:
                raise _remote_failure(
                    completed.stderr, completed.returncode, mutation_state="started"
                )
            raise AdapterError(
                "The canonical checkout could not be copied into the Scenario Workspace.",
                code="workspace.provision-failed",
                retryable=True,
                mutation_state="started",
            )
        revision = component["planned_revision"]
        _run_git(target, "cat-file", "-e", f"{revision}^{{commit}}")
        _run_git(target, "switch", "--create", component["target_ref"], revision)
        if not remote_clone:
            _run_git(target, "remote", "remove", "canonical-source")
            _run_git(target, "remote", "add", "origin", row["remote"])
        if _run_git(target, "remote").decode().splitlines() != ["origin"]:
            raise AdapterError("scenario remote set differs")
        configured = _run_git(
            target, "config", "--get", "remote.origin.url"
        ).decode().strip()
        if configured != row["remote"]:
            raise AdapterError("scenario origin differs")
        if _run_git(target, "rev-parse", "HEAD").decode().strip() != revision:
            raise AdapterError("scenario exact revision differs")
        guard_digest = _install_guard(target, row["remote"], component["target_ref"])
        if _run_git(target, "status", "--porcelain=v1", "-z"):
            raise AdapterError("scenario clone is not clean")
        content_digest = _component_content(target)
        binding_digest = canonical_json_sha256(
            {
                "component_id": component["component_id"],
                "revision": revision,
                "target_ref": component["target_ref"],
                "guard_digest": guard_digest,
            }
        )
        receipts.append(
            {
                "component_id": component["component_id"],
                "component_kind": component["component_kind"],
                "placement": component["placement"],
                "logical_path": component["logical_path"],
                "source_identity_digest": component["source_identity_digest"],
                "revision_kind": component["revision_kind"],
                "planned_revision": revision,
                "realized_revision": revision,
                "target_ref": component["target_ref"],
                "component_binding_digest": binding_digest,
                "content_digest": content_digest,
                "guard_digest": guard_digest,
                "isolated_writable": True,
                "shared_mutable_storage": False,
                "clean_at_provision": True,
            }
        )
    return receipts


def _materialize_components(
    staging: Path,
    plan: Mapping[str, Any],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    progress_total: int,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for index, component in enumerate(plan["components"]):
        component_id = component["component_id"]
        _emit_progress(component_id, index, progress_total, "cloning")
        try:
            receipts.extend(
                _materialize_components_unobserved(
                    staging,
                    {**plan, "components": [component]},
                    rows,
                )
            )
        except Exception:
            _emit_progress(component_id, index, progress_total, "failed")
            raise
        _emit_progress(component_id, index, progress_total, "ready")
    return receipts


def _directory_bytes(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            candidate = Path(root) / name
            try:
                if not candidate.is_symlink():
                    total += candidate.stat().st_size
            except OSError:
                continue
    return total


def _environment_content(environment: Path) -> str:
    marker = environment / ".ai-collab-environment.json"
    if not marker.is_file():
        raise AdapterError("environment marker is unavailable")
    python = environment / "bin" / "python"
    if not python.exists():
        raise AdapterError("environment interpreter is unavailable")
    try:
        marker_value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError("environment marker is invalid") from exc
    if (
        not isinstance(marker_value, dict)
        or set(marker_value)
        != {
            "schema_version",
            "environment_id",
            "source_bindings",
            "source_bindings_digest",
        }
        or marker_value["schema_version"] != 1
        or not isinstance(marker_value["source_bindings"], list)
    ):
        raise AdapterError("environment marker fields differ")
    import_names: list[str] = []
    for binding in marker_value["source_bindings"]:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"component_id", "python_import_name"}
            or not isinstance(binding["component_id"], str)
            or not isinstance(binding["python_import_name"], str)
            or IMPORT_NAME_RE.fullmatch(binding["python_import_name"]) is None
        ):
            raise AdapterError("environment source bindings differ")
        import_names.append(binding["python_import_name"])
    import_probe = (
        "import importlib\n"
        + "".join(f"importlib.import_module({name!r})\n" for name in import_names)
        + "print('ok')"
    )
    # -B keeps the probe observational: without it the imports write
    # __pycache__ bytecode into the just-provisioned workspace sources, which
    # immediately shows up as scenario WIP that nobody created.
    completed = subprocess.run(
        [str(python), "-B", "-c", import_probe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip() != b"ok":
        raise AdapterError("scenario source import verification failed")
    return canonical_json_sha256(
        {
            "marker_digest": sha256_file(marker),
            "python_version": subprocess.check_output(
                [str(python), "-B", "-c", "import platform; print(platform.python_version())"]
            ).decode().strip(),
        }
    )


def _materialize_environment(
    staging: Path,
    plan: Mapping[str, Any],
    component_receipts: Sequence[Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    environment = staging / ".venv"
    venv.EnvBuilder(with_pip=False, clear=False, symlinks=True).create(environment)
    python = environment / "bin" / "python"
    purelib = subprocess.check_output(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).decode().strip()
    purelib_path = Path(purelib).resolve(strict=True)
    if not purelib_path.is_relative_to(environment.resolve()):
        raise AdapterError("environment site-packages escapes Scenario")
    bindings: list[dict[str, str]] = []
    source_paths: list[Path] = []
    for component in plan["components"]:
        row = rows.get(component["component_id"])
        if row is None or "python_import_name" not in row:
            continue
        declared = row["python_source_path"]
        try:
            target = _target_path(staging, component).resolve(strict=True)
            if declared == ".":
                source = target
            else:
                source = target.joinpath(*PurePosixPath(declared).parts).resolve(
                    strict=True
                )
        except OSError as exc:
            raise AdapterError("environment source binding is unavailable") from exc
        if not source.is_dir() or not source.is_relative_to(staging.resolve()):
            raise AdapterError("environment source binding escapes Scenario")
        bindings.append(
            {
                "component_id": component["component_id"],
                "python_import_name": row["python_import_name"],
            }
        )
        source_paths.append(source)
    if source_paths:
        binding_file = purelib_path / "ai_collab_scenario_sources.pth"
        # The complete staging tree is atomically renamed by the Host.  A relative
        # .pth entry preserves the binding across that rename; an absolute staging
        # path would become stale immediately after successful publication.
        binding_file.write_text(
            "".join(
                os.path.relpath(source, start=purelib_path) + "\n"
                for source in source_paths
            ),
            encoding="utf-8",
        )
    marker_value = {
        "schema_version": 1,
        "environment_id": plan["environment"]["environment_id"],
        "source_bindings": bindings,
        "source_bindings_digest": plan["environment"]["source_bindings_digest"],
    }
    marker = environment / ".ai-collab-environment.json"
    marker.write_bytes(_canonical_bytes(marker_value) + b"\n")
    os.chmod(marker, 0o600)
    content_digest = _environment_content(environment)
    binding_digest = canonical_json_sha256(
        {
            "environment_id": plan["environment"]["environment_id"],
            "component_bindings": [
                item["component_binding_digest"] for item in component_receipts
            ],
            "content_digest": content_digest,
        }
    )
    environment_plan = plan["environment"]
    return {
        "environment_id": environment_plan["environment_id"],
        "environment_kind": environment_plan["environment_kind"],
        "environment_binding_digest": binding_digest,
        "environment_spec_digest": environment_plan["environment_spec_digest"],
        "dependency_lock_digest": environment_plan["dependency_lock_digest"],
        "source_bindings_digest": environment_plan["source_bindings_digest"],
        "content_digest": content_digest,
        "isolated_writable": True,
        "shared_mutable_dependencies": False,
        "immutable_cache_only": True,
        "realized_bytes": _directory_bytes(environment),
    }


def _event(
    sequence: int,
    phase: str,
    adapter_kind: str,
    step_id: str,
    target_id: str,
    state: str,
    evidence_digest: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "phase": phase,
        "adapter_kind": adapter_kind,
        "step_id": step_id,
        "target_id": target_id,
        "state": state,
        "evidence_digest": evidence_digest,
        "error_code": error_code,
    }


def _operation_intent(journal: Mapping[str, Any]) -> str:
    return canonical_json_sha256(
        {
            "operation_id": journal["operation_id"],
            "operation_kind": journal["operation_kind"],
            "plan_digest": journal["plan_digest"],
            "scenario": journal["scenario"],
            "operation_fence": journal["operation_fence"],
        }
    )


def _provision(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"workspace_id", "staging_path", "plan", "descriptors"}:
        raise AdapterError("provision payload fields differ")
    staging = Path(payload["staging_path"])
    if not staging.is_absolute() or staging.exists() or staging.is_symlink():
        raise AdapterError("staging path is invalid")
    staging.mkdir(mode=0o700)
    plan = payload["plan"]
    descriptor, _, manifest, _ = _project_inputs()
    del descriptor
    rows = {row["repo_key"]: row for row in manifest["repos"]}
    planned_rows = [rows[item["component_id"]] for item in plan["components"]]
    progress_total = len(plan["components"]) + 1
    progress_items = [item["component_id"] for item in plan["components"]] + [
        plan["environment"]["environment_id"]
    ]
    for progress_index, component_id in enumerate(progress_items):
        _emit_progress(component_id, progress_index, progress_total, "waiting")
    if _source_wip_digest(planned_rows, plan["components"]) != plan["source_wip_snapshot_digest"]:
        raise AdapterError("canonical source changed after planning")
    components = _materialize_components(
        staging,
        plan,
        rows,
        progress_total=progress_total,
    )
    environment_index = progress_total - 1
    environment_id = plan["environment"]["environment_id"]
    _emit_progress(environment_id, environment_index, progress_total, "building")
    try:
        environment = _materialize_environment(staging, plan, components, rows)
    except Exception:
        _emit_progress(environment_id, environment_index, progress_total, "failed")
        raise
    _emit_progress(environment_id, environment_index, progress_total, "ready")
    if _source_wip_digest(planned_rows, plan["components"]) != plan["source_wip_snapshot_digest"]:
        raise AdapterError("canonical source WIP changed during provisioning")
    workspace_binding_digest = canonical_json_sha256(
        {
            "workspace_id": payload["workspace_id"],
            "component_bindings": [item["component_binding_digest"] for item in components],
            "environment_binding": environment["environment_binding_digest"],
        }
    )
    staging_binding_digest = canonical_json_sha256(
        {
            "workspace_binding_digest": workspace_binding_digest,
            "source_wip_digest": plan["source_wip_snapshot_digest"],
            "atomic_publish_target": "bundle",
        }
    )
    journal = {
        "journal_contract_version": 1,
        "operation_id": plan["operation_id"],
        "operation_kind": "provision",
        "plan_digest": canonical_json_sha256(plan),
        "scenario": plan["scenario"],
        "operation_fence": None,
        "events": [],
    }
    journal["events"] = [
        _event(1, "planned", "coordinator", "workspace.plan-frozen", plan["plan_id"], "committed", _operation_intent(journal)),
        _event(2, "workspace", "workspace", "workspace.materialize", payload["workspace_id"], "started"),
        _event(3, "workspace", "workspace", "workspace.materialize", payload["workspace_id"], "committed", canonical_json_sha256(components)),
        _event(4, "environment", "environment", "environment.materialize", environment["environment_id"], "started"),
        _event(5, "environment", "environment", "environment.materialize", environment["environment_id"], "committed", canonical_json_sha256(environment)),
        _event(6, "verify", "workspace", "workspace.components-verified", plan["plan_id"], "committed", canonical_json_sha256(plan["components"])),
        _event(7, "verify", "environment", "environment.binding-verified", environment["environment_id"], "committed", canonical_json_sha256(plan["environment"])),
        _event(8, "finalize", "coordinator", "workspace.atomic-publish", payload["workspace_id"], "committed", staging_binding_digest),
    ]
    receipt = {
        "receipt_contract_version": 1,
        "receipt_id": f"receipt:{str(plan['operation_id']).removeprefix('wsop-')}",
        "plan_digest": canonical_json_sha256(plan),
        "operation_id": plan["operation_id"],
        "base_receipt_digest": None,
        "scenario": plan["scenario"],
        # Where participants belong: the provisioned project root checkout.
        # The staging tree is published as "bundle", so this is the
        # workspace-root-relative path the driver launches vendor CLIs in.
        "participant_working_directory": f"bundle/{ROOT.name}",
        "project_key": plan["project_key"],
        "workspace_adapter": plan["workspace_adapter"],
        "environment_adapter": plan["environment_adapter"],
        "workspace_id": payload["workspace_id"],
        "workspace_binding_digest": workspace_binding_digest,
        "components": components,
        "environment": environment,
        "journal_digest": canonical_json_sha256(journal),
        "source_wip_before_digest": plan["source_wip_snapshot_digest"],
        "source_wip_after_digest": plan["source_wip_snapshot_digest"],
        "finalization": {
            "staging_binding_digest": staging_binding_digest,
            "atomic_publish": True,
            "expected_registry_revision": 0,
            "committed_ready_revision": 1,
        },
        "state": "ready",
        "residual_owned_resources": 0,
        "project_payload_digest": plan["project_payload_digest"],
    }
    review_snapshot = {
        "snapshot_contract_version": 1,
        "scenario": plan["scenario"],
        "plan_digest": canonical_json_sha256(plan),
        "receipt_digest": canonical_json_sha256(receipt),
        "components": [
            {
                "component_id": item["component_id"],
                "revision_kind": item["revision_kind"],
                "exact_revision": item["realized_revision"],
                "target_ref": item["target_ref"],
                "content_digest": item["content_digest"],
            }
            for item in components
        ],
    }
    review_snapshot["snapshot_digest"] = canonical_json_sha256(review_snapshot)
    return {
        "journal": journal,
        "receipt": receipt,
        "review_snapshot": review_snapshot,
    }


def _status_component(bundle: Path, receipt: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    target = _target_path(bundle, receipt)
    if not target.is_dir():
        return None, ["workspace.component-missing"]
    try:
        head = _run_git(target, "rev-parse", "HEAD").decode().strip()
        status = _run_git(target, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        content = _component_content(target)
    except AdapterError:
        return None, ["workspace.component-invalid"]
    drift: list[str] = []
    if head != receipt["realized_revision"]:
        drift.append("workspace.revision-drift")
    observation = {
        "component_id": receipt["component_id"],
        "component_binding_digest": receipt["component_binding_digest"],
        "realized_revision": head,
        "content_digest": content,
        "dirty": bool(status),
        "ownership_digest": canonical_json_sha256(
            {
                "component_binding_digest": receipt["component_binding_digest"],
                "logical_path": receipt["logical_path"],
            }
        ),
    }
    return observation, drift


def _load_binding_marker(bundle: Path) -> tuple[Path, dict[str, Any]]:
    marker = bundle / ".ai-collab-harness-binding.json"
    details = marker.lstat()
    if (
        marker.is_symlink()
        or not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o600
        or details.st_uid != os.getuid()
    ):
        raise AdapterError("workspace binding marker ownership differs")
    value = json.loads(marker.read_bytes())
    if not isinstance(value, dict) or set(value) != {
        "journal",
        "receipt",
        "review_snapshot",
    }:
        raise AdapterError("workspace binding marker fields differ")
    if not all(
        isinstance(value[field], dict)
        for field in ("journal", "receipt", "review_snapshot")
    ):
        raise AdapterError("workspace binding marker artifacts are invalid")
    return marker, value


def _require_binding_marker_provenance(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    """Prove that an owned marker is the exact receipt/plan binding."""

    try:
        journal = value["journal"]
        marker_receipt = value["receipt"]
        snapshot = value["review_snapshot"]
        if not all(
            isinstance(item, Mapping)
            for item in (journal, marker_receipt, snapshot)
        ):
            raise AdapterError("workspace binding marker artifacts are invalid")
        snapshot_without_digest = dict(snapshot)
        snapshot_digest = snapshot_without_digest.pop("snapshot_digest", None)
        receipt_digest = canonical_json_sha256(receipt)
        if (
            canonical_json_sha256(marker_receipt) != receipt_digest
            or canonical_json_sha256(journal) != receipt["journal_digest"]
            or marker_receipt.get("journal_digest") != receipt["journal_digest"]
            or marker_receipt.get("workspace_binding_digest")
            != receipt["workspace_binding_digest"]
            or marker_receipt.get("plan_digest") != canonical_json_sha256(plan)
            or snapshot.get("receipt_digest") != receipt_digest
            or snapshot.get("plan_digest") != canonical_json_sha256(plan)
            or snapshot_digest != canonical_json_sha256(snapshot_without_digest)
        ):
            raise AdapterError("workspace binding marker provenance differs")
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("workspace binding marker provenance differs") from exc


def _require_owned_directory(
    path: Path,
    *,
    require_private_mode: bool = False,
) -> None:
    """Prove one exact directory without following a final-component link."""

    try:
        details = path.lstat()
    except OSError as exc:
        raise AdapterError("workspace owned directory is unavailable") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.getuid()
        or (
            require_private_mode
            and stat.S_IMODE(details.st_mode) != 0o700
        )
    ):
        raise AdapterError("workspace owned directory differs")


def _require_owned_workspace_tree(
    bundle: Path,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-prove the private container, binding marker, and owned roots.

    A status result alone is insufficient for a high-risk mutation: Git and
    ``Path.is_dir`` both follow links, and neither proves that the object still
    belongs to the current Host user.  Only the declared component roots and
    environment root are checked here; repository-internal symlinks remain
    ordinary Scenario content and are covered by the WIP digest.
    """

    _require_owned_directory(bundle.parent, require_private_mode=True)
    _require_owned_directory(bundle, require_private_mode=True)
    _, marker_value = _load_binding_marker(bundle)
    try:
        components = receipt["components"]
        environment = receipt["environment"]
    except (KeyError, TypeError) as exc:
        raise AdapterError("workspace receipt ownership fields differ") from exc
    if not isinstance(components, list) or not isinstance(environment, Mapping):
        raise AdapterError("workspace receipt ownership fields differ")
    bundle_root = bundle.resolve(strict=True)
    for component in components:
        if not isinstance(component, Mapping):
            raise AdapterError("workspace component ownership differs")
        target = _target_path(bundle, component)
        _require_owned_directory(target)
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise AdapterError("workspace component ownership differs") from exc
        if resolved != target or not resolved.is_relative_to(bundle_root):
            raise AdapterError("workspace component ownership differs")
    environment_path = bundle / ".venv"
    _require_owned_directory(environment_path)
    try:
        resolved_environment = environment_path.resolve(strict=True)
    except OSError as exc:
        raise AdapterError("workspace environment ownership differs") from exc
    if (
        resolved_environment != environment_path
        or not resolved_environment.is_relative_to(bundle_root)
    ):
        raise AdapterError("workspace environment ownership differs")
    return marker_value


def _destroy_staging_entries(parent: Path) -> list[Path]:
    try:
        return sorted(
            (
                entry
                for entry in parent.iterdir()
                if entry.name.startswith(DESTROY_STAGING_PREFIX)
            ),
            key=lambda entry: entry.name,
        )
    except OSError as exc:
        raise AdapterError(
            "workspace destroy staging cannot be inspected",
            code="workspace.destroy-outcome-unknown",
            retryable=True,
            mutation_state="unknown",
        ) from exc


def _reject_foreign_destroy_staging(
    parent: Path,
    *,
    exact: Path | None,
) -> None:
    foreign = [
        entry
        for entry in _destroy_staging_entries(parent)
        if exact is None or entry != exact
    ]
    if foreign:
        raise AdapterError(
            "workspace has a conflicting destroy operation",
            code="workspace.destroy-outcome-unknown",
            retryable=True,
            mutation_state="unknown",
        )


def _unknown_repair(error: BaseException, message: str) -> AdapterError:
    if (
        isinstance(error, AdapterError)
        and error.mutation_state == "unknown"
        and error.code == "workspace.repair-outcome-unknown"
    ):
        return error
    return AdapterError(
        message,
        code="workspace.repair-outcome-unknown",
        retryable=True,
        mutation_state="unknown",
    )


def _unknown_destroy(error: BaseException, message: str) -> AdapterError:
    if (
        isinstance(error, AdapterError)
        and error.mutation_state == "unknown"
        and error.code == "workspace.destroy-outcome-unknown"
    ):
        return error
    return AdapterError(
        message,
        code="workspace.destroy-outcome-unknown",
        retryable=True,
        mutation_state="unknown",
    )


def _unknown_recover(error: BaseException, message: str) -> AdapterError:
    if (
        isinstance(error, AdapterError)
        and error.mutation_state == "unknown"
        and error.code == "workspace.recover-outcome-unknown"
    ):
        return error
    return AdapterError(
        message,
        code="workspace.recover-outcome-unknown",
        retryable=True,
        mutation_state="unknown",
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically rename one directory while refusing an existing target.

    Plain POSIX ``rename``/``os.replace`` may replace an empty directory that
    appears after recovery's final inventory check.  Darwin exposes the exact
    primitive through ``renameatx_np(..., RENAME_EXCL)``; Linux exposes the
    equivalent ``renameat2(..., RENAME_NOREPLACE)``.  Platforms without an
    equivalent fail before touching either path.
    """

    if (
        not source.is_absolute()
        or not target.is_absolute()
        or source.parent != target.parent
        or source.name in {"", ".", ".."}
        or target.name in {"", ".", ".."}
    ):
        raise OSError(errno.EINVAL, "no-replace rename identity differs")
    if os.name == "nt":
        # Windows rename fails when the destination already exists.
        os.rename(source, target)
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            function = libc.renameatx_np
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int
            directory_fd = -2  # Darwin AT_FDCWD.
            flags = DARWIN_RENAME_EXCL
        elif sys.platform.startswith("linux"):
            function = libc.renameat2
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int
            directory_fd = -100  # Linux AT_FDCWD.
            flags = LINUX_RENAME_NOREPLACE
        else:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-replace directory rename is unavailable",
            )
    except AttributeError as exc:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory rename is unavailable",
        ) from exc
    ctypes.set_errno(0)
    result = function(
        directory_fd,
        os.fsencode(str(source)),
        directory_fd,
        os.fsencode(str(target)),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(
            error_number,
            os.strerror(error_number),
            str(target),
        )


def _status_binding_marker(
    bundle: Path,
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> tuple[str | None, list[str]]:
    try:
        marker, value = _load_binding_marker(bundle)
        _require_binding_marker_provenance(value, plan, receipt)
    except (AdapterError, OSError, TypeError, ValueError, KeyError):
        return None, ["workspace.binding-evidence-drift"]
    return sha256_file(marker), []


def _status(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"operation_id", "bundle_path", "plan", "receipt"}:
        raise AdapterError("status payload fields differ")
    bundle = Path(payload["bundle_path"])
    plan = payload["plan"]
    receipt = payload["receipt"]
    components: list[dict[str, Any]] = []
    drift: list[str] = []
    binding_marker_digest = None
    if not bundle.is_absolute() or not bundle.is_dir() or bundle.is_symlink():
        drift.append("workspace.binding-missing")
    else:
        binding_marker_digest, marker_drift = _status_binding_marker(bundle, plan, receipt)
        drift.extend(marker_drift)
        for component_receipt in receipt["components"]:
            observation, component_drift = _status_component(bundle, component_receipt)
            drift.extend(component_drift)
            if observation is not None:
                components.append(observation)
    environment_observation = None
    environment_path = bundle / ".venv"
    if environment_path.is_dir() and not environment_path.is_symlink():
        try:
            environment_content = _environment_content(environment_path)
            environment_receipt = receipt["environment"]
            environment_observation = {
                "environment_id": environment_receipt["environment_id"],
                "environment_binding_digest": environment_receipt["environment_binding_digest"],
                "environment_spec_digest": environment_receipt["environment_spec_digest"],
                "source_bindings_digest": environment_receipt["source_bindings_digest"],
                "content_digest": environment_content,
            }
            if environment_content != environment_receipt["content_digest"]:
                drift.append("environment.content-drift")
        except AdapterError:
            drift.append("environment.binding-invalid")
    else:
        drift.append("environment.binding-missing")
    state = "aligned" if not drift else "degraded"
    operation_id = payload["operation_id"]
    fence = {
        "base_receipt_digest": canonical_json_sha256(receipt),
        "expected_ready_revision": receipt["finalization"]["committed_ready_revision"],
        "workspace_id": receipt["workspace_id"],
        "workspace_binding_digest": receipt["workspace_binding_digest"],
        "environment_id": receipt["environment"]["environment_id"],
        "environment_binding_digest": receipt["environment"]["environment_binding_digest"],
    }
    journal = {
        "journal_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "status",
        "plan_digest": canonical_json_sha256(plan),
        "scenario": receipt["scenario"],
        "operation_fence": fence,
        "events": [],
    }
    current_evidence = canonical_json_sha256(
        {
            "binding_marker_digest": binding_marker_digest,
            "components": components,
            "environment": environment_observation,
            "drift_codes": sorted(set(drift)),
        }
    )
    journal["events"] = [
        _event(1, "planned", "coordinator", "workspace.status-planned", receipt["workspace_id"], "committed", _operation_intent(journal)),
        _event(2, "verify", "coordinator", "workspace.status-observed", receipt["workspace_id"], "committed", current_evidence),
    ]
    observation = {
        "observation_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "status",
        "journal_digest": canonical_json_sha256(journal),
        "plan_digest": canonical_json_sha256(plan),
        "receipt_digest": canonical_json_sha256(receipt),
        "scenario": receipt["scenario"],
        "workspace_id": receipt["workspace_id"],
        "workspace_binding_digest": receipt["workspace_binding_digest"],
        "components": components,
        "environment": environment_observation,
        "state": state,
        "drift_codes": sorted(set(drift)),
        "wip_summary_digest": canonical_json_sha256(
            [
                {"component_id": item["component_id"], "dirty": item["dirty"], "content_digest": item["content_digest"]}
                for item in components
            ]
        ),
        "ownership_summary_digest": canonical_json_sha256(
            {
                "binding_marker_digest": binding_marker_digest,
                "component_ownership_digests": [
                    item["ownership_digest"] for item in components
                ],
                "environment_binding_digest": (
                    environment_observation["environment_binding_digest"]
                    if environment_observation is not None
                    else None
                ),
            }
        ),
    }
    return {"journal": journal, "observation": observation}


def _write_binding_marker(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _repair(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "operation_id",
        "bundle_path",
        "plan",
        "receipt",
        "expected_wip_summary_digest",
    }
    if set(payload) != required:
        raise AdapterError("repair payload fields differ")
    operation_id = payload["operation_id"]
    bundle = Path(payload["bundle_path"])
    plan = payload["plan"]
    base_receipt = payload["receipt"]
    expected_wip = payload["expected_wip_summary_digest"]
    if (
        not isinstance(operation_id, str)
        or SAFE_OPERATION_ID_RE.fullmatch(operation_id) is None
        or not isinstance(expected_wip, str)
        or SHA256_RE.fullmatch(expected_wip) is None
        or not bundle.is_absolute()
        or bundle.name != "bundle"
    ):
        raise AdapterError("repair operation identity is invalid")
    try:
        marker_value = _require_owned_workspace_tree(bundle, base_receipt)
        _reject_foreign_destroy_staging(bundle.parent, exact=None)
    except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
        raise _unknown_repair(
            exc, "workspace repair ownership could not be proved"
        ) from exc
    marker_journal = marker_value["journal"]
    marker_receipt = marker_value["receipt"]
    marker_snapshot = marker_value["review_snapshot"]
    if (
        marker_journal.get("operation_id") == operation_id
        or marker_receipt.get("operation_id") == operation_id
    ):
        snapshot_without_digest = dict(marker_snapshot)
        snapshot_digest = snapshot_without_digest.pop("snapshot_digest", None)
        marker_receipt_digest = canonical_json_sha256(marker_receipt)
        if (
            marker_journal.get("operation_id") != operation_id
            or marker_journal.get("operation_kind") != "repair"
            or marker_journal.get("plan_digest") != canonical_json_sha256(plan)
            or marker_receipt.get("operation_id") != operation_id
            or marker_receipt.get("base_receipt_digest")
            != canonical_json_sha256(base_receipt)
            or marker_receipt.get("journal_digest")
            != canonical_json_sha256(marker_journal)
            or marker_receipt.get("workspace_id") != base_receipt.get("workspace_id")
            or marker_receipt.get("workspace_binding_digest")
            != base_receipt.get("workspace_binding_digest")
            or marker_snapshot.get("plan_digest") != canonical_json_sha256(plan)
            or marker_snapshot.get("receipt_digest") != marker_receipt_digest
            or snapshot_digest != canonical_json_sha256(snapshot_without_digest)
        ):
            raise AdapterError(
                "repair replay provenance differs",
                code="workspace.repair-outcome-unknown",
                retryable=True,
                mutation_state="unknown",
            )
        try:
            _require_binding_marker_provenance(
                marker_value, plan, marker_receipt
            )
        except (AdapterError, KeyError, TypeError, ValueError) as exc:
            raise _unknown_repair(
                exc, "workspace repair replay provenance could not be proved"
            ) from exc
        try:
            _reject_foreign_destroy_staging(bundle.parent, exact=None)
            replay_observation = _status(
                {
                    "operation_id": f"{operation_id}-replay-status",
                    "bundle_path": str(bundle),
                    "plan": plan,
                    "receipt": marker_receipt,
                }
            )["observation"]
            replay_marker = _require_owned_workspace_tree(bundle, marker_receipt)
            if (
                canonical_json_sha256(replay_marker)
                != canonical_json_sha256(marker_value)
                or replay_observation["state"] != "aligned"
                or replay_observation["wip_summary_digest"] != expected_wip
            ):
                raise AdapterError("repair replay workspace differs")
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _unknown_repair(
                exc, "workspace repair replay could not be proved"
            ) from exc
        replay_observation.update(
            {
                "operation_id": operation_id,
                "operation_kind": "repair",
                "journal_digest": canonical_json_sha256(marker_journal),
                "receipt_digest": marker_receipt_digest,
            }
        )
        return {
            "journal": marker_journal,
            "receipt": marker_receipt,
            "observation": replay_observation,
            "review_snapshot": marker_snapshot,
        }
    try:
        _require_binding_marker_provenance(marker_value, plan, base_receipt)
    except (AdapterError, KeyError, TypeError, ValueError) as exc:
        raise _unknown_repair(
            exc, "workspace repair base provenance could not be proved"
        ) from exc
    preflight = _status(
        {
            "operation_id": f"{operation_id}-preflight",
            "bundle_path": str(bundle),
            "plan": plan,
            "receipt": base_receipt,
        }
    )["observation"]
    if preflight["state"] != "aligned":
        # Repair never overwrites or cleans Scenario-local WIP.  Drift that
        # cannot be proved harmless remains explicit and requires a narrower
        # project-specific repair implementation.
        raise AdapterError("workspace repair would overwrite or mask drift")
    if preflight["wip_summary_digest"] != expected_wip:
        raise AdapterError("workspace repair WIP fence differs")
    if base_receipt["source_wip_before_digest"] != base_receipt["source_wip_after_digest"]:
        raise AdapterError("canonical source WIP fence differs")
    fence = {
        "base_receipt_digest": canonical_json_sha256(base_receipt),
        "expected_ready_revision": base_receipt["finalization"][
            "committed_ready_revision"
        ],
        "workspace_id": base_receipt["workspace_id"],
        "workspace_binding_digest": base_receipt["workspace_binding_digest"],
        "environment_id": base_receipt["environment"]["environment_id"],
        "environment_binding_digest": base_receipt["environment"][
            "environment_binding_digest"
        ],
    }
    journal = {
        "journal_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "repair",
        "plan_digest": canonical_json_sha256(plan),
        "scenario": base_receipt["scenario"],
        "operation_fence": fence,
        "events": [],
    }
    journal["events"] = [
        _event(1, "planned", "coordinator", "workspace.plan-frozen", plan["plan_id"], "committed", _operation_intent(journal)),
        _event(2, "repair", "workspace", "workspace.repair", base_receipt["workspace_id"], "committed", preflight["ownership_summary_digest"]),
        _event(3, "verify", "workspace", "workspace.components-verified", plan["plan_id"], "committed", canonical_json_sha256(plan["components"])),
        _event(4, "verify", "environment", "environment.binding-verified", plan["environment"]["environment_id"], "committed", canonical_json_sha256(plan["environment"])),
        _event(5, "finalize", "coordinator", "workspace.atomic-publish", base_receipt["workspace_id"], "committed", preflight["ownership_summary_digest"]),
    ]
    receipt = dict(base_receipt)
    receipt.update(
        {
            "receipt_id": f"receipt:{operation_id}",
            "operation_id": operation_id,
            "base_receipt_digest": canonical_json_sha256(base_receipt),
            "journal_digest": canonical_json_sha256(journal),
            "source_wip_before_digest": base_receipt["source_wip_before_digest"],
            "source_wip_after_digest": base_receipt["source_wip_before_digest"],
        }
    )
    receipt["finalization"] = {
        **base_receipt["finalization"],
        "staging_binding_digest": preflight["ownership_summary_digest"],
        "expected_registry_revision": base_receipt["finalization"][
            "committed_ready_revision"
        ],
        "committed_ready_revision": base_receipt["finalization"][
            "committed_ready_revision"
        ]
        + 1,
    }
    review_snapshot = {
        "snapshot_contract_version": 1,
        "scenario": plan["scenario"],
        "plan_digest": canonical_json_sha256(plan),
        "receipt_digest": canonical_json_sha256(receipt),
        "components": [
            {
                "component_id": item["component_id"],
                "revision_kind": item["revision_kind"],
                "exact_revision": item["realized_revision"],
                "target_ref": item["target_ref"],
                "content_digest": item["content_digest"],
            }
            for item in receipt["components"]
        ],
    }
    review_snapshot["snapshot_digest"] = canonical_json_sha256(review_snapshot)
    marker_payload = {
        "journal": journal,
        "receipt": receipt,
        "review_snapshot": review_snapshot,
    }
    try:
        # Close the observation-to-publication window. A different marker or a
        # newly introduced destroy staging tree means another operation raced
        # this repair, so this invocation must not publish over it.
        current = _status(
            {
                "operation_id": f"{operation_id}-commit-preflight",
                "bundle_path": str(bundle),
                "plan": plan,
                "receipt": base_receipt,
            }
        )["observation"]
        _reject_foreign_destroy_staging(bundle.parent, exact=None)
        current_marker = _require_owned_workspace_tree(bundle, base_receipt)
        if canonical_json_sha256(current_marker) != canonical_json_sha256(marker_value):
            raise AdapterError("workspace repair binding changed concurrently")
        if (
            current["state"] != "aligned"
            or current["wip_summary_digest"] != expected_wip
        ):
            raise AdapterError("workspace repair changed before publication")
    except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AdapterError) and exc.mutation_state == "unknown":
            raise
        try:
            # Only classify this as no-effect when the receipt-owned base
            # marker is still exact and no destroy operation appeared while
            # the commit preflight was running.
            refused_marker = _require_owned_workspace_tree(bundle, base_receipt)
            _reject_foreign_destroy_staging(bundle.parent, exact=None)
            if canonical_json_sha256(refused_marker) != canonical_json_sha256(
                marker_value
            ):
                raise AdapterError("workspace repair binding changed concurrently")
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as proof_error:
            raise _unknown_repair(
                proof_error,
                "workspace repair pre-effect refusal could not be proved",
            ) from exc
        raise AdapterError(str(exc)) from exc
    try:
        _write_binding_marker(
            bundle / ".ai-collab-harness-binding.json",
            marker_payload,
        )
    except (AdapterError, OSError) as exc:
        # ``os.replace`` may already have published the new marker before a
        # chmod/fsync error became observable.
        raise _unknown_repair(
            exc, "workspace repair publication outcome is unknown"
        ) from exc
    try:
        postflight = _status(
            {
                "operation_id": f"{operation_id}-postflight",
                "bundle_path": str(bundle),
                "plan": plan,
                "receipt": receipt,
            }
        )["observation"]
        _reject_foreign_destroy_staging(bundle.parent, exact=None)
        published_marker = _require_owned_workspace_tree(bundle, receipt)
        if canonical_json_sha256(published_marker) != canonical_json_sha256(
            marker_payload
        ):
            raise AdapterError("workspace repair marker publication differs")
        if (
            postflight["state"] != "aligned"
            or postflight["wip_summary_digest"] != expected_wip
        ):
            raise AdapterError("workspace repair postflight differs")
    except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
        raise _unknown_repair(
            exc, "workspace repair publication could not be proved"
        ) from exc
    observation = {
        **postflight,
        "operation_id": operation_id,
        "operation_kind": "repair",
        "journal_digest": canonical_json_sha256(journal),
        "receipt_digest": canonical_json_sha256(receipt),
    }
    return {
        "journal": journal,
        "receipt": receipt,
        "observation": observation,
        "review_snapshot": review_snapshot,
    }


def _destroy(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "operation_id",
        "bundle_path",
        "plan",
        "receipt",
        "expected_wip_summary_digest",
        "force",
    }
    if set(payload) != required:
        raise AdapterError("destroy payload fields differ")
    force = payload["force"]
    if not isinstance(force, bool):
        raise AdapterError("destroy force flag is invalid")
    operation_id = payload["operation_id"]
    bundle = Path(payload["bundle_path"])
    plan = payload["plan"]
    receipt = payload["receipt"]
    expected_wip = payload["expected_wip_summary_digest"]
    if (
        not isinstance(operation_id, str)
        or SAFE_OPERATION_ID_RE.fullmatch(operation_id) is None
        or not isinstance(expected_wip, str)
        or SHA256_RE.fullmatch(expected_wip) is None
        or not bundle.is_absolute()
        or bundle.name != "bundle"
    ):
        raise AdapterError("destroy target is invalid")
    parent = bundle.parent
    try:
        _require_owned_directory(parent, require_private_mode=True)
    except (AdapterError, OSError) as exc:
        # Without the exact private container there is no trustworthy basis
        # for declaring that an earlier execution did not already mutate it.
        raise _unknown_destroy(
            exc, "workspace destroy target ownership could not be proved"
        ) from exc
    fence = {
        "base_receipt_digest": canonical_json_sha256(receipt),
        "expected_ready_revision": receipt["finalization"][
            "committed_ready_revision"
        ],
        "workspace_id": receipt["workspace_id"],
        "workspace_binding_digest": receipt["workspace_binding_digest"],
        "environment_id": receipt["environment"]["environment_id"],
        "environment_binding_digest": receipt["environment"][
            "environment_binding_digest"
        ],
    }
    destroying = parent / f".destroying-{operation_id}"
    try:
        _reject_foreign_destroy_staging(parent, exact=destroying)
        bundle_exists = bundle.exists() or bundle.is_symlink()
        destroying_exists = destroying.exists() or destroying.is_symlink()
    except (AdapterError, OSError) as exc:
        raise _unknown_destroy(
            exc, "workspace destroy targets could not be inspected"
        ) from exc
    if bundle_exists and destroying_exists:
        raise AdapterError(
            "destroy targets conflict",
            code="workspace.destroy-outcome-unknown",
            retryable=True,
            mutation_state="unknown",
        )
    if bundle_exists:
        try:
            base_marker = _require_owned_workspace_tree(bundle, receipt)
            _require_binding_marker_provenance(base_marker, plan, receipt)
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            # A path that merely exists is not proof that it is still the
            # receipt-owned Workspace.  Treat a failed ownership proof as an
            # ambiguous replay instead of a fresh, no-effect refusal.
            raise _unknown_destroy(
                exc, "workspace destroy ownership could not be proved"
            ) from exc
        try:
            preflight = _status(
                {
                    "operation_id": f"{operation_id}-preflight",
                    "bundle_path": str(bundle),
                    "plan": plan,
                    "receipt": receipt,
                }
            )["observation"]
            # A forced destroy is the owner-confirmed teardown of a workspace
            # already known to have drifted. It may bypass alignment, but never
            # the exact WIP fence or ownership proof.
            if not force and preflight["state"] != "aligned":
                raise AdapterError("workspace destroy binding is not aligned")
            if preflight["wip_summary_digest"] != expected_wip:
                raise AdapterError("workspace destroy WIP fence differs")
            _reject_foreign_destroy_staging(parent, exact=destroying)
            if destroying.exists() or destroying.is_symlink():
                raise AdapterError(
                    "destroy targets conflict",
                    code="workspace.destroy-outcome-unknown",
                    retryable=True,
                    mutation_state="unknown",
                )
            current_marker = _require_owned_workspace_tree(bundle, receipt)
            if canonical_json_sha256(current_marker) != canonical_json_sha256(
                base_marker
            ):
                raise AdapterError(
                    "workspace destroy binding changed concurrently",
                    code="workspace.destroy-outcome-unknown",
                    retryable=True,
                    mutation_state="unknown",
                )
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, AdapterError) and exc.mutation_state == "unknown":
                raise
            try:
                # A semantic preflight refusal is only ``not_started`` when
                # the unchanged receipt-owned marker and absence of this
                # operation's staging path can still be re-proved.
                refused_marker = _require_owned_workspace_tree(bundle, receipt)
                _reject_foreign_destroy_staging(parent, exact=destroying)
                if destroying.exists() or destroying.is_symlink():
                    raise AdapterError("destroy staging appeared concurrently")
                if canonical_json_sha256(refused_marker) != canonical_json_sha256(
                    base_marker
                ):
                    raise AdapterError("workspace destroy binding changed concurrently")
            except (AdapterError, OSError, KeyError, TypeError, ValueError) as proof_error:
                raise _unknown_destroy(
                    proof_error,
                    "workspace destroy pre-effect refusal could not be proved",
                ) from exc
            raise AdapterError(str(exc)) from exc
        try:
            os.replace(bundle, destroying)
            _fsync_directory(parent)
        except OSError as exc:
            mutation_state = "unknown"
            try:
                refused_marker = _require_owned_workspace_tree(bundle, receipt)
                _reject_foreign_destroy_staging(parent, exact=destroying)
                if destroying.exists() or destroying.is_symlink():
                    raise AdapterError("destroy staging appeared concurrently")
                if canonical_json_sha256(refused_marker) != canonical_json_sha256(
                    base_marker
                ):
                    raise AdapterError("workspace destroy binding changed concurrently")
                mutation_state = "not_started"
            except (AdapterError, OSError, KeyError, TypeError, ValueError):
                pass
            raise AdapterError(
                "workspace destroy publication outcome is unknown",
                code="workspace.destroy-outcome-unknown",
                retryable=True,
                mutation_state=mutation_state,
            ) from exc
        destroying_exists = True
    if destroying_exists:
        try:
            _reject_foreign_destroy_staging(parent, exact=destroying)
            if bundle.exists() or bundle.is_symlink():
                raise AdapterError("destroy targets conflict")
            staging_preflight_marker = _require_owned_workspace_tree(
                destroying, receipt
            )
            _require_binding_marker_provenance(
                staging_preflight_marker, plan, receipt
            )
            staging_preflight = _status(
                {
                    "operation_id": f"{operation_id}-staging-preflight",
                    "bundle_path": str(destroying),
                    "plan": plan,
                    "receipt": receipt,
                }
            )["observation"]
            if not force and staging_preflight["state"] != "aligned":
                raise AdapterError("workspace destroy staging is not aligned")
            if staging_preflight["wip_summary_digest"] != expected_wip:
                raise AdapterError("workspace destroy staging WIP fence differs")
            _reject_foreign_destroy_staging(parent, exact=destroying)
            if bundle.exists() or bundle.is_symlink():
                raise AdapterError("destroy targets conflict")
            current_staging_marker = _require_owned_workspace_tree(
                destroying, receipt
            )
            if canonical_json_sha256(
                current_staging_marker
            ) != canonical_json_sha256(staging_preflight_marker):
                raise AdapterError("workspace destroy staging changed concurrently")
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            # The exact staging name proves an earlier execution crossed the
            # rename boundary. No failure from this point can be no-effect.
            raise _unknown_destroy(
                exc, "workspace destroy staging could not be proved"
            ) from exc
        try:
            shutil.rmtree(destroying)
            _fsync_directory(parent)
        except OSError as exc:
            raise AdapterError(
                "workspace destroy cleanup outcome is unknown",
                code="workspace.destroy-outcome-unknown",
                retryable=True,
                mutation_state="unknown",
            ) from exc
    try:
        _reject_foreign_destroy_staging(parent, exact=destroying)
        if (
            bundle.exists()
            or bundle.is_symlink()
            or destroying.exists()
            or destroying.is_symlink()
        ):
            raise AdapterError("workspace absence could not be proved")
    except (AdapterError, OSError) as exc:
        raise _unknown_destroy(
            exc, "workspace destroy absence could not be proved"
        ) from exc
    destroy_evidence_digest = canonical_json_sha256(
        {
            "receipt_digest": canonical_json_sha256(receipt),
            "expected_wip_summary_digest": expected_wip,
        }
    )
    journal = {
        "journal_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "destroy",
        "plan_digest": canonical_json_sha256(plan),
        "scenario": receipt["scenario"],
        "operation_fence": fence,
        "events": [],
    }
    journal["events"] = [
        _event(1, "planned", "coordinator", "workspace.plan-frozen", plan["plan_id"], "committed", _operation_intent(journal)),
        _event(2, "destroy", "workspace", "workspace.destroy", receipt["workspace_id"], "committed", destroy_evidence_digest),
        _event(3, "verify", "coordinator", "workspace.absence-verified", receipt["workspace_id"], "committed", canonical_json_sha256({"bundle_missing": True})),
        _event(4, "finalize", "coordinator", "workspace.destroy-finalized", receipt["workspace_id"], "committed", canonical_json_sha256({"workspace_unregistered": True})),
    ]
    observation = {
        "observation_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "destroy",
        "journal_digest": canonical_json_sha256(journal),
        "plan_digest": canonical_json_sha256(plan),
        "receipt_digest": canonical_json_sha256(receipt),
        "scenario": receipt["scenario"],
        "workspace_id": None,
        "workspace_binding_digest": None,
        "components": [],
        "environment": None,
        "state": "missing",
        "drift_codes": ["workspace.destroyed"],
        "wip_summary_digest": expected_wip,
        "ownership_summary_digest": canonical_json_sha256(
            {"bundle_missing": True, "workspace_id": receipt["workspace_id"]}
        ),
    }
    return {"journal": journal, "observation": observation}


def _recovery_fence(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "base_receipt_digest": canonical_json_sha256(receipt),
        "expected_ready_revision": receipt["finalization"][
            "committed_ready_revision"
        ],
        "workspace_id": receipt["workspace_id"],
        "workspace_binding_digest": receipt["workspace_binding_digest"],
        "environment_id": receipt["environment"]["environment_id"],
        "environment_binding_digest": receipt["environment"][
            "environment_binding_digest"
        ],
    }


def _expected_review_snapshot(
    plan: Mapping[str, Any], receipt: Mapping[str, Any]
) -> dict[str, Any]:
    snapshot = {
        "snapshot_contract_version": 1,
        "scenario": plan["scenario"],
        "plan_digest": canonical_json_sha256(plan),
        "receipt_digest": canonical_json_sha256(receipt),
        "components": [
            {
                "component_id": item["component_id"],
                "revision_kind": item["revision_kind"],
                "exact_revision": item["realized_revision"],
                "target_ref": item["target_ref"],
                "content_digest": item["content_digest"],
            }
            for item in receipt["components"]
        ],
    }
    snapshot["snapshot_digest"] = canonical_json_sha256(snapshot)
    return snapshot


def _recovery_parent_entries(parent: Path) -> set[Path]:
    try:
        return set(parent.iterdir())
    except OSError as exc:
        raise AdapterError("workspace recovery inventory is unavailable") from exc


def _require_recovery_marker(
    marker_value: Mapping[str, Any],
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    _require_binding_marker_provenance(marker_value, plan, receipt)
    if marker_value["review_snapshot"] != _expected_review_snapshot(plan, receipt):
        raise AdapterError("workspace recovery review snapshot differs")


def _require_prior_repair_receipt(
    marker_value: Mapping[str, Any],
    plan: Mapping[str, Any],
    base_receipt: Mapping[str, Any],
    prior_operation: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select only the exact base or the exact completed prior repair marker."""

    marker_journal = marker_value["journal"]
    marker_receipt = marker_value["receipt"]
    marker_snapshot = marker_value["review_snapshot"]
    if canonical_json_sha256(marker_receipt) == canonical_json_sha256(base_receipt):
        _require_recovery_marker(marker_value, plan, base_receipt)
        return dict(base_receipt), dict(marker_snapshot)

    prior_operation_id = prior_operation["operation_id"]
    try:
        finalization = marker_receipt["finalization"]
        base_finalization = base_receipt["finalization"]
        if (
            set(marker_receipt) != set(base_receipt)
            or marker_receipt.get("receipt_id")
            != f"receipt:{prior_operation_id}"
            or marker_receipt.get("operation_id") != prior_operation_id
            or marker_receipt.get("base_receipt_digest")
            != canonical_json_sha256(base_receipt)
            or marker_receipt.get("journal_digest")
            != canonical_json_sha256(marker_journal)
            or marker_receipt.get("source_wip_before_digest")
            != base_receipt["source_wip_before_digest"]
            or marker_receipt.get("source_wip_after_digest")
            != base_receipt["source_wip_before_digest"]
            or not isinstance(finalization, Mapping)
            or set(finalization) != set(base_finalization)
            or finalization.get("atomic_publish") is not True
            or finalization.get("expected_registry_revision")
            != base_finalization["committed_ready_revision"]
            or finalization.get("committed_ready_revision")
            != base_finalization["committed_ready_revision"] + 1
            or not isinstance(finalization.get("staging_binding_digest"), str)
            or SHA256_RE.fullmatch(finalization["staging_binding_digest"]) is None
        ):
            raise AdapterError("workspace prior repair receipt differs")
        mutable_receipt_fields = {
            "receipt_id",
            "operation_id",
            "base_receipt_digest",
            "journal_digest",
            "source_wip_before_digest",
            "source_wip_after_digest",
            "finalization",
        }
        if any(
            marker_receipt[field] != base_receipt[field]
            for field in set(base_receipt) - mutable_receipt_fields
        ):
            raise AdapterError("workspace prior repair binding differs")
        if (
            set(marker_journal)
            != {
                "journal_contract_version",
                "operation_id",
                "operation_kind",
                "plan_digest",
                "scenario",
                "operation_fence",
                "events",
            }
            or marker_journal.get("journal_contract_version") != 1
            or marker_journal.get("operation_id") != prior_operation_id
            or marker_journal.get("operation_kind") != "repair"
            or marker_journal.get("plan_digest") != canonical_json_sha256(plan)
            or marker_journal.get("scenario") != base_receipt["scenario"]
            or marker_journal.get("operation_fence")
            != _recovery_fence(base_receipt)
        ):
            raise AdapterError("workspace prior repair journal differs")
        expected_events = [
            _event(
                1,
                "planned",
                "coordinator",
                "workspace.plan-frozen",
                plan["plan_id"],
                "committed",
                _operation_intent(marker_journal),
            ),
            _event(
                2,
                "repair",
                "workspace",
                "workspace.repair",
                base_receipt["workspace_id"],
                "committed",
                finalization["staging_binding_digest"],
            ),
            _event(
                3,
                "verify",
                "workspace",
                "workspace.components-verified",
                plan["plan_id"],
                "committed",
                canonical_json_sha256(plan["components"]),
            ),
            _event(
                4,
                "verify",
                "environment",
                "environment.binding-verified",
                plan["environment"]["environment_id"],
                "committed",
                canonical_json_sha256(plan["environment"]),
            ),
            _event(
                5,
                "finalize",
                "coordinator",
                "workspace.atomic-publish",
                base_receipt["workspace_id"],
                "committed",
                finalization["staging_binding_digest"],
            ),
        ]
        if marker_journal.get("events") != expected_events:
            raise AdapterError("workspace prior repair journal events differ")
        _require_recovery_marker(marker_value, plan, marker_receipt)
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("workspace prior repair provenance differs") from exc
    return dict(marker_receipt), dict(marker_snapshot)


def _recover_observation(
    *,
    operation_id: str,
    bundle: Path,
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
    marker_value: Mapping[str, Any],
    expected_wip: str,
    require_aligned: bool,
) -> dict[str, Any]:
    status = _status(
        {
            "operation_id": operation_id,
            "bundle_path": str(bundle),
            "plan": plan,
            "receipt": receipt,
        }
    )["observation"]
    current_marker = _require_owned_workspace_tree(bundle, receipt)
    _require_recovery_marker(current_marker, plan, receipt)
    if (
        canonical_json_sha256(current_marker)
        != canonical_json_sha256(marker_value)
        or (require_aligned and status["state"] != "aligned")
        or status["wip_summary_digest"] != expected_wip
    ):
        raise AdapterError("workspace recovery ready proof differs")
    return status


def _recover_result(
    *,
    operation_id: str,
    plan: Mapping[str, Any],
    base_receipt: Mapping[str, Any],
    expected_wip: str,
    prior_operation: Mapping[str, Any],
    resolution: str,
    current_receipt: Mapping[str, Any] | None,
    review_snapshot: Mapping[str, Any] | None,
    ready_observation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    recovery = {
        "prior_operation_id": prior_operation["operation_id"],
        "prior_operation_kind": prior_operation["operation_kind"],
        "prior_claim_digest": prior_operation["claim_digest"],
        "resolution": resolution,
    }
    missing_ownership_digest = canonical_json_sha256(
        {
            "bundle_missing": True,
            "workspace_id": base_receipt["workspace_id"],
        }
    )
    proof = {
        "recovery": recovery,
        "base_receipt_digest": canonical_json_sha256(base_receipt),
        "current_receipt_digest": (
            canonical_json_sha256(current_receipt)
            if current_receipt is not None
            else None
        ),
        "expected_wip_summary_digest": expected_wip,
        "observed_state": (
            ready_observation.get("state")
            if ready_observation is not None
            else "missing"
        ),
        "ownership_summary_digest": (
            ready_observation.get("ownership_summary_digest")
            if ready_observation is not None
            else missing_ownership_digest
        ),
    }
    journal = {
        "journal_contract_version": 1,
        "operation_id": operation_id,
        "operation_kind": "recover",
        "plan_digest": canonical_json_sha256(plan),
        "scenario": base_receipt["scenario"],
        "operation_fence": _recovery_fence(base_receipt),
        "events": [],
    }
    journal["events"] = [
        _event(
            1,
            "planned",
            "coordinator",
            "workspace.recovery-planned",
            prior_operation["operation_id"],
            "committed",
            canonical_json_sha256(
                {
                    "operation_intent": _operation_intent(journal),
                    "prior_operation": prior_operation,
                }
            ),
        ),
        _event(
            2,
            "verify",
            "workspace",
            "workspace.recovery-resolved",
            base_receipt["workspace_id"],
            "committed",
            canonical_json_sha256(proof),
        ),
        _event(
            3,
            "finalize",
            "coordinator",
            "workspace.recovery-finalized",
            base_receipt["workspace_id"],
            "committed",
            canonical_json_sha256(recovery),
        ),
    ]
    journal_digest = canonical_json_sha256(journal)
    if resolution == "ready":
        if (
            current_receipt is None
            or review_snapshot is None
            or ready_observation is None
        ):
            raise AdapterError("workspace recovery ready result is incomplete")
        observation = {
            **ready_observation,
            "operation_id": operation_id,
            "operation_kind": "recover",
            "journal_digest": journal_digest,
            "plan_digest": canonical_json_sha256(plan),
            "receipt_digest": canonical_json_sha256(current_receipt),
        }
        receipt_result: dict[str, Any] | None = dict(current_receipt)
        snapshot_result: dict[str, Any] | None = dict(review_snapshot)
    else:
        observation = {
            "observation_contract_version": 1,
            "operation_id": operation_id,
            "operation_kind": "recover",
            "journal_digest": journal_digest,
            "plan_digest": canonical_json_sha256(plan),
            "receipt_digest": canonical_json_sha256(base_receipt),
            "scenario": base_receipt["scenario"],
            "workspace_id": None,
            "workspace_binding_digest": None,
            "components": [],
            "environment": None,
            "state": "missing",
            "drift_codes": ["workspace.destroyed"],
            "wip_summary_digest": expected_wip,
            "ownership_summary_digest": missing_ownership_digest,
        }
        receipt_result = None
        snapshot_result = None
    return {
        "journal": journal,
        "receipt": receipt_result,
        "observation": observation,
        "review_snapshot": snapshot_result,
        "recovery": recovery,
    }


def _recover(payload: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "operation_id",
        "bundle_path",
        "plan",
        "receipt",
        "expected_wip_summary_digest",
        "prior_operation",
    }
    if set(payload) != required:
        raise AdapterError("recover payload fields differ")
    operation_id = payload["operation_id"]
    bundle_value = payload["bundle_path"]
    plan = payload["plan"]
    base_receipt = payload["receipt"]
    expected_wip = payload["expected_wip_summary_digest"]
    prior_operation = payload["prior_operation"]
    if (
        not isinstance(operation_id, str)
        or SAFE_OPERATION_ID_RE.fullmatch(operation_id) is None
        or len(operation_id) > 127
        or not isinstance(bundle_value, str)
        or not bundle_value
        or not isinstance(plan, Mapping)
        or not isinstance(base_receipt, Mapping)
        or not isinstance(expected_wip, str)
        or SHA256_RE.fullmatch(expected_wip) is None
        or not isinstance(prior_operation, Mapping)
        or set(prior_operation)
        != {"operation_id", "operation_kind", "force", "claim_digest"}
    ):
        raise AdapterError("recover operation identity is invalid")
    bundle = Path(bundle_value)
    if not bundle.is_absolute() or bundle.name != "bundle":
        raise AdapterError("recover bundle identity is invalid")
    prior_operation_id = prior_operation["operation_id"]
    prior_kind = prior_operation["operation_kind"]
    prior_force = prior_operation["force"]
    prior_claim_digest = prior_operation["claim_digest"]
    if (
        not isinstance(prior_operation_id, str)
        or SAFE_OPERATION_ID_RE.fullmatch(prior_operation_id) is None
        or len(prior_operation_id) > 127
        or prior_operation_id == operation_id
        or not isinstance(prior_kind, str)
        or prior_kind not in {"repair", "destroy"}
        or not isinstance(prior_force, bool)
        or (prior_kind == "repair" and prior_force)
        or not isinstance(prior_claim_digest, str)
        or SHA256_RE.fullmatch(prior_claim_digest) is None
    ):
        raise AdapterError("recover prior operation identity is invalid")
    try:
        _recovery_fence(base_receipt)
        _expected_review_snapshot(plan, base_receipt)
        if (
            base_receipt.get("plan_digest") != canonical_json_sha256(plan)
            or base_receipt.get("scenario") != plan.get("scenario")
            or base_receipt.get("state") != "ready"
        ):
            raise AdapterError("recover base receipt provenance differs")
    except (AdapterError, KeyError, TypeError, ValueError) as exc:
        raise AdapterError("recover base receipt is invalid") from exc

    parent = bundle.parent
    exact_staging = parent / f"{DESTROY_STAGING_PREFIX}{prior_operation_id}"
    try:
        _require_owned_directory(parent, require_private_mode=True)
        parent_entries = _recovery_parent_entries(parent)
    except (AdapterError, OSError) as exc:
        raise AdapterError("workspace recovery container differs") from exc

    if prior_kind == "repair":
        if parent_entries != {bundle}:
            raise AdapterError("workspace repair recovery inventory differs")
        if not bundle.exists() or bundle.is_symlink():
            raise AdapterError("workspace repair recovery bundle differs")
        try:
            marker_value = _require_owned_workspace_tree(bundle, base_receipt)
            current_receipt, review_snapshot = _require_prior_repair_receipt(
                marker_value, plan, base_receipt, prior_operation
            )
            _require_owned_workspace_tree(bundle, current_receipt)
            observation = _recover_observation(
                operation_id=operation_id,
                bundle=bundle,
                plan=plan,
                receipt=current_receipt,
                marker_value=marker_value,
                expected_wip=expected_wip,
                require_aligned=True,
            )
            if _recovery_parent_entries(parent) != {bundle}:
                raise AdapterError("workspace recovery changed concurrently")
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            raise AdapterError("workspace repair recovery proof differs") from exc
        return _recover_result(
            operation_id=operation_id,
            plan=plan,
            base_receipt=base_receipt,
            expected_wip=expected_wip,
            prior_operation=prior_operation,
            resolution="ready",
            current_receipt=current_receipt,
            review_snapshot=review_snapshot,
            ready_observation=observation,
        )

    unexpected_entries = parent_entries - {bundle, exact_staging}
    if unexpected_entries:
        raise AdapterError("workspace destroy recovery inventory differs")
    bundle_exists = bundle.exists() or bundle.is_symlink()
    staging_exists = exact_staging.exists() or exact_staging.is_symlink()
    if bundle_exists and staging_exists:
        raise AdapterError("workspace destroy recovery targets conflict")
    if not bundle_exists and not staging_exists:
        return _recover_result(
            operation_id=operation_id,
            plan=plan,
            base_receipt=base_receipt,
            expected_wip=expected_wip,
            prior_operation=prior_operation,
            resolution="missing",
            current_receipt=None,
            review_snapshot=None,
            ready_observation=None,
        )

    current_path = bundle if bundle_exists else exact_staging
    if current_path.is_symlink() or not current_path.is_dir():
        raise AdapterError("workspace destroy recovery target differs")
    try:
        marker_value = _require_owned_workspace_tree(current_path, base_receipt)
        _require_recovery_marker(marker_value, plan, base_receipt)
        observation = _recover_observation(
            operation_id=operation_id,
            bundle=current_path,
            plan=plan,
            receipt=base_receipt,
            marker_value=marker_value,
            expected_wip=expected_wip,
            # A force-destroy may have started from a degraded but exactly
            # owned/WIP-fenced tree.  After an exact stage rollback, replay
            # observes the indistinguishable bundle-only form, so the same
            # force proof must apply to both forms for deterministic replay.
            require_aligned=not prior_force,
        )
        expected_entries = {bundle} if bundle_exists else {exact_staging}
        if _recovery_parent_entries(parent) != expected_entries:
            raise AdapterError("workspace recovery changed concurrently")
    except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
        raise AdapterError("workspace destroy recovery proof differs") from exc

    if staging_exists:
        # Exercise the complete deterministic result construction before the
        # sole recovery effect.  Malformed provenance must fail no-effect,
        # never after the stage has been moved back into place.
        _recover_result(
            operation_id=operation_id,
            plan=plan,
            base_receipt=base_receipt,
            expected_wip=expected_wip,
            prior_operation=prior_operation,
            resolution="ready",
            current_receipt=base_receipt,
            review_snapshot=marker_value["review_snapshot"],
            ready_observation=observation,
        )
        try:
            _rename_directory_no_replace(exact_staging, bundle)
        except OSError as exc:
            if exc.errno in NO_REPLACE_CONFLICT_ERRNOS:
                # RENAME_EXCL/NOREPLACE makes this a proof about this exact
                # invocation: neither path was changed.  The Host's durable
                # pending_recover_external_attempted bit still promotes a
                # later/replayed not_started response to unknown.
                raise AdapterError(
                    "workspace recovery target appeared concurrently",
                    code="workspace.recover-target-conflict",
                    retryable=False,
                    mutation_state="not_started",
                ) from exc
            if exc.errno in NO_REPLACE_UNAVAILABLE_ERRNOS:
                raise AdapterError(
                    "atomic no-replace recovery rename is unavailable",
                    code="workspace.recover-no-replace-unavailable",
                    retryable=False,
                    mutation_state="not_started",
                ) from exc
            if exc.errno in NO_REPLACE_PROVEN_NO_EFFECT_ERRNOS:
                raise AdapterError(
                    "workspace recovery rollback could not start",
                    code="workspace.recover-rename-not-started",
                    retryable=False,
                    mutation_state="not_started",
                ) from exc
            raise _unknown_recover(
                exc, "workspace recovery rollback outcome is unknown"
            ) from exc
        try:
            _fsync_directory(parent)
        except OSError as exc:
            raise _unknown_recover(
                exc, "workspace recovery rollback outcome is unknown"
            ) from exc
        try:
            if exact_staging.exists() or exact_staging.is_symlink():
                raise AdapterError("workspace recovery staging remains")
            if _recovery_parent_entries(parent) != {bundle}:
                raise AdapterError("workspace recovery inventory changed")
            published_marker = _require_owned_workspace_tree(bundle, base_receipt)
            _require_recovery_marker(published_marker, plan, base_receipt)
            if canonical_json_sha256(published_marker) != canonical_json_sha256(
                marker_value
            ):
                raise AdapterError("workspace recovery marker changed")
            observation = _recover_observation(
                operation_id=operation_id,
                bundle=bundle,
                plan=plan,
                receipt=base_receipt,
                marker_value=published_marker,
                expected_wip=expected_wip,
                require_aligned=not prior_force,
            )
            result = _recover_result(
                operation_id=operation_id,
                plan=plan,
                base_receipt=base_receipt,
                expected_wip=expected_wip,
                prior_operation=prior_operation,
                resolution="ready",
                current_receipt=base_receipt,
                review_snapshot=published_marker["review_snapshot"],
                ready_observation=observation,
            )
        except (AdapterError, OSError, KeyError, TypeError, ValueError) as exc:
            raise _unknown_recover(
                exc, "workspace recovery rollback could not be proved"
            ) from exc
        return result

    review_snapshot = marker_value["review_snapshot"]
    return _recover_result(
        operation_id=operation_id,
        plan=plan,
        base_receipt=base_receipt,
        expected_wip=expected_wip,
        prior_operation=prior_operation,
        resolution="ready",
        current_receipt=base_receipt,
        review_snapshot=review_snapshot,
        ready_observation=observation,
    )


def _handle(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict) or set(request) != {
        "adapter_protocol_version",
        "adapter_id",
        "operation",
        "payload",
    }:
        raise AdapterError("adapter request fields differ")
    if request["adapter_protocol_version"] != 1 or request["adapter_id"] != ADAPTER_ID:
        raise AdapterError("adapter request identity differs")
    operation = request["operation"]
    payload = request["payload"]
    if not isinstance(payload, dict):
        raise AdapterError("adapter payload is invalid")
    if operation == "register":
        result = _register(payload)
    elif operation == "bootstrap":
        result = _bootstrap(payload)
    elif operation == "collaboration_templates":
        result = _collaboration_templates(payload)
    elif operation == "plan":
        result = _plan(payload)
    elif operation == "provision":
        result = _provision(payload)
    elif operation == "status":
        result = _status(payload)
    elif operation == "repair":
        result = _repair(payload)
    elif operation == "destroy":
        result = _destroy(payload)
    elif operation == "recover":
        result = _recover(payload)
    else:
        raise AdapterError("adapter operation is unavailable")
    return {
        "adapter_protocol_version": 1,
        "adapter_id": ADAPTER_ID,
        "outcome": "completed",
        "result": result,
    }


def _failed_response(error: AdapterError) -> dict[str, Any]:
    return {
        "adapter_protocol_version": 1,
        "adapter_id": ADAPTER_ID,
        "outcome": "failed",
        "result": {
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "mutation_state": error.mutation_state,
            }
        },
    }


def main() -> int:
    operation: Any = None
    try:
        request = json.loads(sys.stdin.buffer.read())
        if isinstance(request, Mapping):
            operation = request.get("operation")
        response = _handle(request)
    except descriptor_validator.DescriptorError as exc:
        error = AdapterError(str(exc), code="project.descriptor-invalid")
        response = _failed_response(error)
    except manifest_validator.ManifestError as exc:
        error = AdapterError(str(exc), code="project.manifest-invalid")
        response = _failed_response(error)
    except intent_resolver.IntentError as exc:
        response = _failed_response(
            AdapterError(str(exc), code=exc.code, retryable=exc.retryable)
        )
    except AdapterError as exc:
        response = _failed_response(exc)
    except (OSError, subprocess.SubprocessError) as exc:
        is_disk_full = isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}
        high_risk = operation in {"repair", "destroy", "recover"}
        response = _failed_response(
            AdapterError(
                (
                    "The Scenario Workspace does not have enough free disk space."
                    if is_disk_full
                    else "project adapter could not access a required local resource"
                ),
                code="workspace.disk-full" if is_disk_full else "adapter.io-failed",
                retryable=high_risk,
                mutation_state="unknown" if high_risk else "not_started",
            )
        )
    except (json.JSONDecodeError, ValueError):
        response = _failed_response(
            AdapterError("project adapter request is invalid", code="adapter.request-invalid")
        )
    sys.stdout.buffer.write(_canonical_bytes(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
