#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-AtomGradient-Proprietary
# Copyright (c) 2026 AtomGradient. All rights reserved.
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司。保留所有权利。
# Unauthorized copying, distribution, or use is strictly prohibited.
# 未经授权，禁止复制、分发或使用本文件。
"""Shared fail-closed evidence helpers for new AI Collab bootstrap spikes.

The already-issued Stage 0, Immediate, and Gate 0 producers remain frozen so
their producer digests and receipts do not become stale during Phase -1.
New Phase -1 verifiers use this module to avoid multiplying receipt, checkout,
timeout, and dependency-validation implementations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


COMMAND_TIMEOUT_SECONDS = 30.0
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
GATE_ID_RE = re.compile(r"^[A-Z0-9-]+$")
PROJECT_DESCRIPTOR_FIELDS = (
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
PROJECT_DESCRIPTOR_SCALARS = {
    "schema_version": 1,
    "project_key": "edgestudio",
    "product_contract_version": "3.2",
    "participant_driver_contract": 2,
    "collaboration_policy_schema": 1,
}
_DESCRIPTOR_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DESCRIPTOR_BARE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class EvidenceError(RuntimeError):
    """A fail-closed bootstrap evidence operation."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def project_descriptor_digest(repo_root: Path) -> str:
    """Return the validated canonical digest of the tracked project descriptor."""

    root = repo_root.resolve()
    descriptor_path = root / "project_descriptor.yaml"
    if descriptor_path.is_symlink() or not descriptor_path.is_file():
        raise EvidenceError("project descriptor is unavailable or not a regular file")
    try:
        descriptor_path.resolve(strict=True).relative_to(root)
        text = descriptor_path.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeError) as exc:
        raise EvidenceError("project descriptor is unavailable") from exc
    lowered = text.lower()
    if "placeholder" in lowered or "not_available" in lowered or "<" in text:
        raise EvidenceError("project descriptor contains an unresolved placeholder")
    if "\t" in text or "\r" in text:
        raise EvidenceError("project descriptor must use LF and spaces only")

    descriptor: dict[str, str | int] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        if line[0].isspace() or line.count(":") != 1:
            raise EvidenceError(
                f"project descriptor line {line_number} is not a top-level scalar"
            )
        key, raw = line.split(":", 1)
        if _DESCRIPTOR_KEY_RE.fullmatch(key) is None or key in descriptor:
            raise EvidenceError("project descriptor contains an invalid or duplicate key")
        raw = raw.strip()
        if not raw:
            raise EvidenceError(f"project descriptor field {key} is empty")
        if raw.startswith('"'):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise EvidenceError(
                    f"project descriptor field {key} has invalid quoted syntax"
                ) from exc
            if not isinstance(value, str) or not value:
                raise EvidenceError(f"project descriptor field {key} is invalid")
        elif re.fullmatch(r"[0-9]+", raw):
            value = int(raw)
        elif re.fullmatch(r"[0-9]+\.[0-9]+", raw):
            raise EvidenceError(
                f"project descriptor field {key} version must be quoted"
            )
        elif _DESCRIPTOR_BARE_RE.fullmatch(raw) is not None:
            value = raw
        else:
            raise EvidenceError(
                f"project descriptor field {key} uses unsupported syntax"
            )
        descriptor[key] = value

    if set(descriptor) != set(PROJECT_DESCRIPTOR_FIELDS):
        raise EvidenceError("project descriptor fields do not match contract")
    if any(descriptor.get(key) != value for key, value in PROJECT_DESCRIPTOR_SCALARS.items()):
        raise EvidenceError("project descriptor is incompatible with product contract")
    for field in ("repo_manifest", "gate_registry"):
        reference = descriptor[field]
        if not isinstance(reference, str):
            raise EvidenceError(f"project descriptor {field} reference is invalid")
        pure = PurePosixPath(reference)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise EvidenceError(f"project descriptor {field} reference is unsafe")
        candidate = root.joinpath(*pure.parts)
        if candidate.is_symlink() or not candidate.is_file():
            raise EvidenceError(f"project descriptor {field} reference is unavailable")
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise EvidenceError(
                f"project descriptor {field} reference escapes project root"
            ) from exc
    return canonical_json_sha256(descriptor)


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise EvidenceError(
            f"{Path(argv[0]).name} timed out after {timeout_seconds:g}s"
        ) from exc
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def git_output(repo: Path, *args: str) -> str:
    completed = run_command(("git", *args), cwd=repo)
    if completed.returncode != 0:
        raise EvidenceError(
            f"git {' '.join(args)} failed for {repo.name}: exit={completed.returncode}"
        )
    return completed.stdout.strip()


def verify_pushed_checkout(
    repo: Path,
    *,
    expected_sha: str,
    expected_branch: str,
    expected_upstream: str,
) -> dict[str, str]:
    repo = repo.resolve()
    if not SHA1_RE.fullmatch(expected_sha):
        raise EvidenceError(f"invalid expected SHA for {repo.name}")
    if not repo.is_dir():
        raise EvidenceError(f"repository directory is missing: {repo.name}")
    top_level = Path(git_output(repo, "rev-parse", "--show-toplevel")).resolve()
    if top_level != repo:
        raise EvidenceError(f"repository root mismatch for {repo.name}")

    head = git_output(repo, "rev-parse", "HEAD")
    tree = git_output(repo, "rev-parse", "HEAD^{tree}")
    branch = git_output(repo, "branch", "--show-current")
    upstream = git_output(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    upstream_head = git_output(repo, "rev-parse", "@{upstream}")
    status_output = git_output(
        repo, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if head != expected_sha:
        raise EvidenceError(f"{repo.name} HEAD does not match expected SHA")
    if branch != expected_branch:
        raise EvidenceError(f"{repo.name} branch mismatch")
    if upstream != expected_upstream:
        raise EvidenceError(f"{repo.name} upstream mismatch")
    if upstream_head != expected_sha:
        raise EvidenceError(f"{repo.name} expected SHA is not pushed to upstream")
    if status_output:
        raise EvidenceError(f"{repo.name} working tree is not clean")
    if "/" not in expected_upstream:
        raise EvidenceError(f"invalid upstream format for {repo.name}")

    remote, remote_branch = expected_upstream.split("/", 1)
    remote_line = git_output(
        repo,
        "ls-remote",
        "--exit-code",
        remote,
        f"refs/heads/{remote_branch}",
    )
    fields = remote_line.split()
    if len(fields) != 2 or fields[0] != expected_sha:
        raise EvidenceError(f"{repo.name} expected SHA is not current on remote")
    return {
        "branch": branch,
        "upstream": upstream,
        "commit": head,
        "tree": tree,
        "remote_commit": fields[0],
    }


def resolve_logical_path(state_root: Path, logical_path: Any) -> Path:
    if not isinstance(logical_path, str):
        raise EvidenceError("evidence_path must be a string")
    pure = PurePosixPath(logical_path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise EvidenceError("evidence_path is unsafe")
    state_root = state_root.expanduser().resolve()
    resolved = (state_root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(state_root)
    except ValueError as exc:
        raise EvidenceError("evidence_path escapes state root") from exc
    return resolved


def load_evidence_pair(state_root: Path, gate_id: str) -> dict[str, Any]:
    if not GATE_ID_RE.fullmatch(gate_id):
        raise EvidenceError("gate_id contains unsupported characters")
    state_root = state_root.expanduser().resolve()
    if not state_root.is_dir() or stat.S_IMODE(state_root.stat().st_mode) != 0o700:
        raise EvidenceError("state root is unavailable or not mode 0700")
    current_path = state_root / "receipts/gates" / f"{gate_id}.json"
    try:
        current = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{gate_id} current view is unavailable") from exc
    if not isinstance(current, dict) or current.get("gate_id") != gate_id:
        raise EvidenceError(f"{gate_id} current view identity mismatch")
    expected_digest = current.get("evidence_sha256")
    if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest):
        raise EvidenceError(f"{gate_id} evidence digest is invalid")
    evidence_path = resolve_logical_path(state_root, current.get("evidence_path"))
    if not evidence_path.is_file() or sha256_file(evidence_path) != expected_digest:
        raise EvidenceError(f"{gate_id} evidence digest mismatch")
    if stat.S_IMODE(current_path.stat().st_mode) != 0o600:
        raise EvidenceError(f"{gate_id} current view is not mode 0600")
    if stat.S_IMODE(evidence_path.stat().st_mode) != 0o600:
        raise EvidenceError(f"{gate_id} evidence is not mode 0600")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"{gate_id} evidence is invalid JSON") from exc
    if (
        not isinstance(evidence, dict)
        or evidence.get("gate_id") != gate_id
        or evidence.get("status") != current.get("status")
        or evidence.get("run_id") != current.get("run_id")
        or evidence.get("input_fingerprint") != current.get("input_fingerprint")
    ):
        raise EvidenceError(f"{gate_id} current/evidence fields do not match")
    fingerprint_inputs = evidence.get("fingerprint_inputs")
    expected_project_descriptor_digest = project_descriptor_digest(
        Path(__file__).resolve().parents[1]
    )
    if (
        not isinstance(fingerprint_inputs, dict)
        or fingerprint_inputs.get("project_descriptor_digest")
        != expected_project_descriptor_digest
    ):
        raise EvidenceError(f"{gate_id} project descriptor fingerprint is stale")
    return {
        "current": current,
        "evidence": evidence,
        "evidence_sha256": expected_digest,
    }


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_exclusive(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def replace_private(path: Path, content: bytes) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    write_exclusive(temporary, content)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_receipt_pair(
    state_root: Path, gate_id: str, receipt: dict[str, Any]
) -> dict[str, str]:
    if not GATE_ID_RE.fullmatch(gate_id) or receipt.get("gate_id") != gate_id:
        raise EvidenceError("receipt gate identity mismatch")
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise EvidenceError("run_id contains unsupported characters")
    input_fingerprint = receipt.get("input_fingerprint")
    if not isinstance(input_fingerprint, str) or not SHA256_RE.fullmatch(
        input_fingerprint
    ):
        raise EvidenceError("receipt input_fingerprint is invalid")

    state_root = state_root.expanduser().resolve()
    receipts_root = state_root / "receipts"
    run_dir = receipts_root / "runs" / run_id
    gate_dir = receipts_root / "gates"
    for directory in (state_root, receipts_root, run_dir.parent, run_dir, gate_dir):
        ensure_private_directory(directory)
    evidence_path = run_dir / f"{gate_id}.json"
    current_path = gate_dir / f"{gate_id}.json"
    try:
        write_exclusive(evidence_path, json_bytes(receipt))
    except FileExistsError as exc:
        raise EvidenceError("immutable evidence path already exists") from exc
    evidence_digest = sha256_file(evidence_path)
    current = {
        "schema_version": 2,
        "gate_id": gate_id,
        "status": receipt["status"],
        "run_id": run_id,
        "evidence_path": f"receipts/runs/{run_id}/{gate_id}.json",
        "evidence_sha256": evidence_digest,
        "input_fingerprint": input_fingerprint,
        "updated_at": receipt["observed_at"],
    }
    replace_private(current_path, json_bytes(current))
    if stat.S_IMODE(evidence_path.stat().st_mode) != 0o600:
        raise EvidenceError("evidence file mode is not 0600")
    if stat.S_IMODE(current_path.stat().st_mode) != 0o600:
        raise EvidenceError("current-view file mode is not 0600")
    return {
        "evidence_path": current["evidence_path"],
        "evidence_sha256": evidence_digest,
        "current_view_path": f"receipts/gates/{gate_id}.json",
    }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_run_id(prefix: str, observed_at: datetime, input_fingerprint: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", prefix):
        raise EvidenceError("run_id prefix contains unsupported characters")
    if not SHA256_RE.fullmatch(input_fingerprint):
        raise EvidenceError("input fingerprint is invalid")
    return (
        f"{prefix}-{observed_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{input_fingerprint[:12]}"
    )
