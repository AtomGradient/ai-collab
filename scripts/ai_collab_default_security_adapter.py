#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient
# 版权所有 (c) 2026 质子梯度（北京）科技有限公司

"""AI Collab local permission observer and native confirmation presenter.

The adapter receives private subjects from Harness Host, validates exact local
ownership, and returns only redacted digests.  It has no vendor API or
vendor-session dependency, and it carries no knowledge of any particular
project: workspace observations delegate to the generic project adapter that
ships beside it.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import ai_collab_project_adapter as project_adapter


ADAPTER_PROTOCOL_VERSION = 1
ADAPTER_ID = "ai-collab-security-adapter"
STATE_SCHEMA_VERSION = 1
MAX_PROMPT_CHARACTERS = 3_000
PRODUCT_NAME = "AI Collab"


class AdapterError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _private_root(raw: Any) -> Path:
    if not isinstance(raw, str):
        raise AdapterError("private subject root is invalid")
    supplied = Path(raw)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise AdapterError("private subject root is invalid")
    root = supplied.resolve(strict=True)
    details = root.stat()
    if (
        not root.is_dir()
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise AdapterError("private subject root is invalid")
    return root


def _read_binding(root: Path) -> dict[str, Any]:
    path = root / "driver-binding.json"
    if path.is_symlink() or not path.is_file():
        raise AdapterError("private binding state is unavailable")
    details = path.stat()
    if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
        raise AdapterError("private binding state is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError("private binding state is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise AdapterError("private binding state is invalid")
    return value


def _process_observation(pid: Any) -> dict[str, Any] | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise AdapterError("private process identity is invalid")
    completed = subprocess.run(
        ("/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "command="),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=3,
        check=False,
    )
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line:
        return None
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return None
    value = {"pid": pid, "pgid": pgid, "ps": line}
    return {**value, "identity_sha256": digest(value)}


def _process_subject(subject: Mapping[str, Any]) -> tuple[str, str, str | None]:
    expected_fields = {
        "subject_kind",
        "private_root",
        "runtime_binding_id",
        "process_identity_sha256",
        "expected_process_state",
    }
    if (
        set(subject) != expected_fields
        or subject.get("subject_kind") != "harness-owned-process"
        or not isinstance(subject.get("runtime_binding_id"), str)
        or not subject["runtime_binding_id"]
        or not _sha256(subject.get("process_identity_sha256"))
        or subject.get("expected_process_state") not in {"present", "absent"}
    ):
        raise AdapterError("private process subject differs")
    root = _private_root(subject["private_root"])
    state = _read_binding(root)
    if (
        state.get("runtime_binding_id") != subject["runtime_binding_id"]
        or state.get("process_identity_sha256") != subject["process_identity_sha256"]
        or not isinstance(state.get("pgid"), int)
    ):
        raise AdapterError("private process binding differs")
    observed = _process_observation(state.get("pid"))
    expected = subject["expected_process_state"]
    exact_present = (
        observed is not None
        and observed["identity_sha256"] == subject["process_identity_sha256"]
        and observed["pgid"] == state["pgid"]
    )
    # A live PID with a changed identity is drift, not proof of absence.
    granted = exact_present if expected == "present" else observed is None
    redacted_subject = {
        "subject_kind": "harness-owned-process",
        "private_root_digest": digest({"private_root": str(root)}),
        "runtime_binding_id": subject["runtime_binding_id"],
        "process_identity_sha256": subject["process_identity_sha256"],
        "expected_process_state": expected,
        "stored_pgid_digest": digest({"pgid": state["pgid"]}),
    }
    evidence = {
        "subject_digest": digest(redacted_subject),
        "expected_process_state": expected,
        "observation": (
            "exact_present"
            if exact_present
            else "absent"
            if observed is None
            else "identity_drift"
        ),
    }
    return (
        digest(redacted_subject),
        digest(evidence),
        None if granted else "process.subject-not-proven",
    )


def _project_subject(subject: Mapping[str, Any]) -> tuple[str, str, str | None]:
    expected_fields = {
        "subject_kind",
        "bundle_path",
        "plan",
        "receipt",
        "expected_wip_summary_digest",
        "expected_workspace_state",
    }
    # The expected state is whatever observation the owner is confirming, not
    # a fixed "aligned". A forced teardown of a drifted workspace has to be
    # provable too, and it stays provable because the probe still requires the
    # live observation to equal this exact value.
    expected_state = subject.get("expected_workspace_state")
    if (
        set(subject) != expected_fields
        or subject.get("subject_kind") != "project-storage"
        or not isinstance(expected_state, str)
        or not expected_state
        or not _sha256(subject.get("expected_wip_summary_digest"))
        or not isinstance(subject.get("plan"), dict)
        or not isinstance(subject.get("receipt"), dict)
    ):
        raise AdapterError("private project subject differs")
    raw_bundle = subject["bundle_path"]
    if not isinstance(raw_bundle, str):
        raise AdapterError("private project subject differs")
    supplied = Path(raw_bundle)
    if not supplied.is_absolute() or supplied.is_symlink() or supplied.name != "bundle":
        raise AdapterError("private project binding is invalid")
    bundle = supplied.resolve(strict=True)
    parent = bundle.parent
    details = parent.stat()
    if (
        not bundle.is_dir()
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise AdapterError("private project binding is invalid")
    operation_id = f"security-observe-{int(time.time() * 1_000_000)}"
    external = project_adapter._status(  # noqa: SLF001 - same trusted plugin
        {
            "operation_id": operation_id,
            "bundle_path": str(bundle),
            "plan": subject["plan"],
            "receipt": subject["receipt"],
        }
    )
    observation = external["observation"]
    aligned = (
        observation.get("state") == subject["expected_workspace_state"]
        and observation.get("wip_summary_digest")
        == subject["expected_wip_summary_digest"]
        and observation.get("receipt_digest")
        == digest(subject["receipt"])
    )
    redacted_subject = {
        "subject_kind": "project-storage",
        "bundle_path_digest": digest({"bundle_path": str(bundle)}),
        "plan_digest": digest(subject["plan"]),
        "receipt_digest": digest(subject["receipt"]),
        "wip_summary_digest": observation.get("wip_summary_digest"),
        "expected_workspace_state": subject["expected_workspace_state"],
    }
    evidence = {
        "subject_digest": digest(redacted_subject),
        "workspace_state": observation.get("state"),
        "drift_codes": observation.get("drift_codes"),
        "wip_summary_digest": observation.get("wip_summary_digest"),
    }
    return (
        digest(redacted_subject),
        digest(evidence),
        None if aligned else "project-storage.subject-not-proven",
    )


def _empty_project_subject(
    subject: Mapping[str, Any],
) -> tuple[str, str, str | None]:
    """Prove an unprovisioned Scenario owns an exact empty storage husk."""

    expected_fields = {
        "subject_kind",
        "workspace_path",
        "expected_binding_state",
        "expected_husk_digest",
    }
    binding_state = subject.get("expected_binding_state")
    if (
        set(subject) != expected_fields
        or subject.get("subject_kind") != "empty-project-storage"
        or binding_state not in {"absent", "planned", "provision_failed"}
        or not _sha256(subject.get("expected_husk_digest"))
    ):
        raise AdapterError("private empty project subject differs")
    raw_workspace = subject["workspace_path"]
    if not isinstance(raw_workspace, str):
        raise AdapterError("private empty project subject differs")
    supplied = Path(raw_workspace)
    if not supplied.is_absolute() or not supplied.name.startswith("workspace-"):
        raise AdapterError("private empty project binding is invalid")
    try:
        details = supplied.lstat()
        workspace = supplied.resolve(strict=True)
        entries = list(supplied.iterdir())
    except OSError as exc:
        raise AdapterError("private empty project binding is invalid") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or workspace != supplied
        or details.st_uid != os.getuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise AdapterError("private empty project binding is invalid")
    observed_husk_digest = digest(
        {
            "path_identity": {
                "workspace_id": workspace.name,
                "device": details.st_dev,
                "inode": details.st_ino,
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
            },
            "entries": [],
        }
    )
    exact_empty = (
        not entries
        and observed_husk_digest == subject["expected_husk_digest"]
    )
    redacted_subject = {
        "subject_kind": "empty-project-storage",
        "workspace_path_digest": digest({"workspace_path": str(workspace)}),
        "expected_binding_state": binding_state,
        "husk_digest": observed_husk_digest,
    }
    evidence = {
        "subject_digest": digest(redacted_subject),
        "workspace_state": "unprovisioned",
        "binding_state": binding_state,
        "entry_count": len(entries),
        "husk_digest": observed_husk_digest,
    }
    return (
        digest(redacted_subject),
        digest(evidence),
        None if exact_empty else "project-storage.subject-not-proven",
    )


def _project_recovery_subject(
    subject: Mapping[str, Any],
) -> tuple[str, str, str | None]:
    """Bind confirmation to one exact owned recovery inventory."""

    expected_fields = {
        "subject_kind",
        "workspace_path",
        "workspace_id",
        "expected_inventory_digest",
        "allowed_entry_names",
        "prior_operation_kind",
        "prior_operation_claim_digest",
    }
    allowed_names = subject.get("allowed_entry_names")
    prior_kind = subject.get("prior_operation_kind")
    workspace_id = subject.get("workspace_id")
    if (
        set(subject) != expected_fields
        or subject.get("subject_kind") != "project-storage-recovery"
        or prior_kind not in {"destroy", "repair"}
        or not isinstance(workspace_id, str)
        or not workspace_id.startswith("workspace-")
        or Path(workspace_id).name != workspace_id
        or not _sha256(subject.get("expected_inventory_digest"))
        or not _sha256(subject.get("prior_operation_claim_digest"))
        or not isinstance(allowed_names, list)
        or allowed_names != sorted(set(allowed_names))
        or any(not isinstance(name, str) or Path(name).name != name for name in allowed_names)
    ):
        raise AdapterError("private project recovery subject differs")
    raw_workspace = subject["workspace_path"]
    if not isinstance(raw_workspace, str):
        raise AdapterError("private project recovery subject differs")
    supplied = Path(raw_workspace)
    if not supplied.is_absolute() or supplied.name != workspace_id:
        raise AdapterError("private project recovery binding is invalid")
    try:
        root_details = supplied.lstat()
        workspace = supplied.resolve(strict=True)
        entries = sorted(supplied.iterdir(), key=lambda item: item.name)
        observed: list[dict[str, Any]] = []
        for entry in entries:
            details = entry.lstat()
            if (
                stat.S_ISLNK(details.st_mode)
                or details.st_uid != os.getuid()
                or (
                    stat.S_ISDIR(details.st_mode)
                    and stat.S_IMODE(details.st_mode) != 0o700
                )
            ):
                raise AdapterError("private project recovery inventory differs")
            observed.append(
                {
                    "name": entry.name,
                    "device": details.st_dev,
                    "inode": details.st_ino,
                    "uid": details.st_uid,
                    "mode": stat.S_IMODE(details.st_mode),
                    "kind": (
                        "directory"
                        if stat.S_ISDIR(details.st_mode)
                        else "regular"
                        if stat.S_ISREG(details.st_mode)
                        else "other"
                    ),
                }
            )
    except OSError as exc:
        raise AdapterError("private project recovery binding is invalid") from exc
    if (
        stat.S_ISLNK(root_details.st_mode)
        or not stat.S_ISDIR(root_details.st_mode)
        or workspace != supplied
        or root_details.st_uid != os.getuid()
        or stat.S_IMODE(root_details.st_mode) != 0o700
    ):
        raise AdapterError("private project recovery binding is invalid")
    inventory_digest = digest(
        {
            "workspace_id": workspace_id,
            "workspace_path_identity_digest": digest(
                {
                    "workspace_id": workspace_id,
                    "device": root_details.st_dev,
                    "inode": root_details.st_ino,
                    "uid": root_details.st_uid,
                    "mode": stat.S_IMODE(root_details.st_mode),
                }
            ),
            "entries": observed,
        }
    )
    exact = (
        [item["name"] for item in observed] == allowed_names
        and inventory_digest == subject["expected_inventory_digest"]
    )
    redacted = {
        "subject_kind": "project-storage-recovery",
        "workspace_path_digest": digest({"workspace_path": str(workspace)}),
        "workspace_id": workspace_id,
        "prior_operation_kind": prior_kind,
        "prior_operation_claim_digest": subject[
            "prior_operation_claim_digest"
        ],
        "inventory_digest": inventory_digest,
    }
    evidence = {
        **redacted,
        "entry_count": len(observed),
        "entry_names_digest": digest([item["name"] for item in observed]),
    }
    return (
        digest(redacted),
        digest(evidence),
        None if exact else "project-storage.subject-not-proven",
    )
def observe(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {
        "permission_ids",
        "private_subject",
        "captured_at_epoch_ms",
    }:
        raise AdapterError("permission observation payload differs")
    permission_ids = payload["permission_ids"]
    captured_at = payload["captured_at_epoch_ms"]
    subject = payload["private_subject"]
    if (
        not isinstance(permission_ids, list)
        or len(permission_ids) != 1
        or not isinstance(permission_ids[0], str)
        or not isinstance(captured_at, int)
        or isinstance(captured_at, bool)
        or not isinstance(subject, dict)
    ):
        raise AdapterError("permission observation payload is invalid")
    permission_id = permission_ids[0]
    if permission_id not in {
        "permission.local-process-control",
        "permission.local-resource-control",
        "permission.project-storage",
    }:
        raise AdapterError("permission is unsupported")
    if permission_id == "permission.project-storage":
        if subject.get("subject_kind") == "empty-project-storage":
            subject_digest, evidence_digest, failure = _empty_project_subject(
                subject
            )
        elif subject.get("subject_kind") == "project-storage-recovery":
            subject_digest, evidence_digest, failure = _project_recovery_subject(
                subject
            )
        else:
            subject_digest, evidence_digest, failure = _project_subject(subject)
    else:
        expected = (
            "present"
            if permission_id == "permission.local-process-control"
            else "absent"
        )
        if subject.get("expected_process_state") != expected:
            raise AdapterError("permission subject purpose differs")
        subject_digest, evidence_digest, failure = _process_subject(subject)
    observed_at = min(int(time.time() * 1000), captured_at)
    return {
        "observations": [
            {
                "permission_id": permission_id,
                "subject_digest": subject_digest,
                "status": "granted" if failure is None else "denied",
                "observed_at_epoch_ms": observed_at,
                "valid_until_epoch_ms": observed_at + 60_000,
                "evidence_digest": evidence_digest,
                "provider_error_code": failure,
                "remediation_ref": None,
            }
        ]
    }


def _prompt(effect_preview: Mapping[str, Any]) -> str:
    operation = effect_preview.get("operation", "unknown high-risk operation")
    summary = json.dumps(
        effect_preview, ensure_ascii=False, sort_keys=True, indent=2
    )
    prompt = (
        f"{PRODUCT_NAME} Harness requests one high-risk local operation.\n\n"
        f"Operation: {operation}\n\n"
        "Exact redacted effect preview:\n"
        f"{summary}\n\n"
        "Approve this exact request once?"
    )
    if len(prompt) > MAX_PROMPT_CHARACTERS:
        prompt = (
            f"{PRODUCT_NAME} Harness requests one high-risk local operation.\n\n"
            f"Operation: {operation}\n"
            f"Effect preview digest: {digest(effect_preview)}\n\n"
            "Approve this exact request once?"
        )
    return prompt


CONFIRMATION_TIMEOUT_SECONDS = 300


def _terminate_presenter(process: "subprocess.Popen[str]") -> None:
    if process.poll() is not None:
        return
    for send in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), send)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _present_confirmation(argv: tuple[str, ...]) -> tuple[int, str]:
    """Run the presenter so its dialog never outlives this adapter.

    An abandoned dialog is worse than no dialog. It keeps accepting clicks
    after the only listener is gone, so an operator can approve a request that
    nothing will ever receive and reasonably believe the operation proceeded.
    The presenter therefore runs in its own process group, which is torn down
    on timeout and on any signal that ends this adapter.
    """
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )

    def _reclaim(signal_number: int, _frame: object) -> None:
        _terminate_presenter(process)
        raise SystemExit(128 + signal_number)

    installed: dict[int, Any] = {}
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            installed[number] = signal.signal(number, _reclaim)
        except (OSError, ValueError):
            continue
    try:
        stdout, _ = process.communicate(timeout=CONFIRMATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_presenter(process)
        raise AdapterError("trusted local confirmation was not answered") from None
    finally:
        for number, handler in installed.items():
            try:
                signal.signal(number, handler)
            except (OSError, ValueError):
                continue
        _terminate_presenter(process)
    return process.returncode, stdout


def present(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != {"challenge", "effect_preview"}:
        raise AdapterError("confirmation payload differs")
    challenge = payload["challenge"]
    effect_preview = payload["effect_preview"]
    if not isinstance(challenge, dict) or not isinstance(effect_preview, dict):
        raise AdapterError("confirmation payload is invalid")
    if sys.platform != "darwin" or not Path("/usr/bin/osascript").is_file():
        raise AdapterError("trusted local confirmation presenter is unavailable")
    script = (
        "on run argv\n"
        "set promptText to item 1 of argv\n"
        "try\n"
        f'set answer to display dialog promptText with title "{PRODUCT_NAME} Harness" '
        "buttons {\"Deny\", \"Approve Once\"} default button \"Deny\" "
        "cancel button \"Deny\" with icon caution\n"
        "return button returned of answer\n"
        "on error number -128\n"
        "return \"Deny\"\n"
        "end try\n"
        "end run"
    )
    returncode, stdout = _present_confirmation(
        ("/usr/bin/osascript", "-e", script, _prompt(effect_preview))
    )
    if returncode != 0:
        raise AdapterError("trusted local confirmation presenter failed")
    outcome = "approved" if stdout.strip() == "Approve Once" else "denied"
    decided_at = int(time.time() * 1000)
    presenter_digest = digest(
        {
            "adapter_id": ADAPTER_ID,
            "presenter": "macos.osascript-display-dialog",
            "protocol_version": ADAPTER_PROTOCOL_VERSION,
        }
    )
    return {
        "challenge_digest": digest(challenge),
        "outcome": outcome,
        "decided_at_epoch_ms": decided_at,
        "presenter_instance_digest": presenter_digest,
        "decision_evidence_digest": digest(
            {
                "challenge_digest": digest(challenge),
                "outcome": outcome,
                "decided_at_epoch_ms": decided_at,
                "presenter_instance_digest": presenter_digest,
            }
        ),
        "reason_code": None if outcome == "approved" else "user.denied",
    }


OPERATIONS = {"observe": observe, "present": present}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or set(request) != {
            "security_adapter_protocol_version",
            "adapter_id",
            "operation",
            "payload",
        }:
            raise AdapterError("security adapter request differs")
        operation = request["operation"]
        if (
            request["security_adapter_protocol_version"] != ADAPTER_PROTOCOL_VERSION
            or request["adapter_id"] != ADAPTER_ID
            or operation not in OPERATIONS
            or not isinstance(request["payload"], dict)
        ):
            raise AdapterError("security adapter request is invalid")
        result = OPERATIONS[operation](request["payload"])
        json.dump(
            {
                "security_adapter_protocol_version": ADAPTER_PROTOCOL_VERSION,
                "adapter_id": ADAPTER_ID,
                "outcome": "completed",
                "result": result,
            },
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 0
    except (AdapterError, OSError, subprocess.SubprocessError, ValueError):
        # The Host exposes a typed, redacted error; adapter stderr stays empty.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
