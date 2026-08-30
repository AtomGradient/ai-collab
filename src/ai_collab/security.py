# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Provider-neutral permission and high-risk confirmation runtime.

The product core owns the frozen artifact chain and its durable replay fence.
Project/platform plugins own permission observation and the trusted local
presenter.  A consumed authorization is deliberately not operation success.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .protocol import (
    OPERATION_DESCRIPTORS,
    OPERATION_REGISTRY_DIGEST,
    canonical_json_bytes,
    canonical_json_sha256,
    operation_intent_digest,
)


SECURITY_STATE_SCHEMA_VERSION = 1
SECURITY_ADAPTER_PROTOCOL_VERSION = 1
MAX_SECURITY_REPLY_BYTES = 1024 * 1024
CONFIRMATION_DURATION_MS = 5 * 60 * 1000
FROZEN_HOST_IPC_CONTRACT_DIGEST = (
    "1036d9dfc4c61ac2f9d6f229d34b22ff804b5da1a4c6332d19f97003c5f146e4"
)
HIGH_RISK_OPERATIONS = {
    "participant.force-stop": "permission.local-process-control",
    "resource.break": "permission.local-resource-control",
    "scenario.repair": "permission.project-storage",
    "scenario.destroy": "permission.project-storage",
    "scenario.force-destroy": "permission.project-storage",
}
EFFECT_PREVIEW_SCHEMA_DIGESTS = {
    operation: canonical_json_sha256(
        {
            "schema_version": 1,
            "operation_id": operation,
            "type": "redacted-effect-preview",
        }
    )
    for operation in HIGH_RISK_OPERATIONS
}


@dataclass
class SecurityError(ValueError):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class SecurityAdapterCommand:
    """Owner-controlled command adapter for probes and trusted presentation."""

    def __init__(self, config_path: Path):
        supplied = Path(config_path).expanduser()
        if supplied.is_symlink():
            raise SecurityError("security.config-invalid", "security adapter config is invalid")
        path = supplied.resolve(strict=True)
        if not path.is_file() or not self._owner_controlled(path):
            raise SecurityError("security.config-invalid", "security adapter config is invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecurityError(
                "security.config-invalid", "security adapter config is invalid"
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "adapter_id",
            "command",
            "working_directory",
        }:
            raise SecurityError(
                "security.config-invalid", "security adapter config fields differ"
            )
        command = value["command"]
        if (
            value["schema_version"] != 1
            or not isinstance(value["adapter_id"], str)
            or not value["adapter_id"]
            or not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise SecurityError(
                "security.config-invalid", "security adapter config values are invalid"
            )
        base = path.parent
        work = self._resolve_relative(base, value["working_directory"], directory=True)
        executable = command[0]
        if "/" in executable:
            executable = str(self._resolve_relative(base, executable, directory=False))
        arguments = [executable]
        for item in command[1:]:
            arguments.append(
                item
                if item.startswith("-")
                else str(self._resolve_relative(base, item, directory=False))
            )
        self.config_path = path
        self.adapter_id = value["adapter_id"]
        self.command = tuple(arguments)
        self.working_directory = work

    @staticmethod
    def _resolve_relative(base: Path, raw: Any, *, directory: bool) -> Path:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise SecurityError("security.config-invalid", "security adapter path is invalid")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or any(part in {"", ".."} for part in relative.parts):
            raise SecurityError("security.config-invalid", "security adapter path escapes config")
        lexical = base.joinpath(*relative.parts)
        if directory:
            candidate = lexical.resolve(strict=True)
            if (
                not candidate.is_relative_to(base)
                or not candidate.is_dir()
                or not SecurityAdapterCommand._owner_controlled(candidate)
            ):
                raise SecurityError(
                    "security.config-invalid", "security adapter working directory is invalid"
                )
            return candidate
        parent = lexical.parent.resolve(strict=True)
        if (
            not parent.is_relative_to(base)
            or not lexical.exists()
            or not lexical.is_file()
            or not SecurityAdapterCommand._owner_controlled(lexical)
        ):
            raise SecurityError("security.config-invalid", "security adapter command is invalid")
        return lexical.absolute()

    @staticmethod
    def _owner_controlled(path: Path) -> bool:
        details = path.stat()
        return details.st_uid == os.getuid() and stat.S_IMODE(details.st_mode) & 0o022 == 0

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        project_root: Path | None = None,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        request = {
            "security_adapter_protocol_version": SECURITY_ADAPTER_PROTOCOL_VERSION,
            "adapter_id": self.adapter_id,
            "operation": operation,
            "payload": payload,
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "SYSTEMROOT",
                "PYTHONDONTWRITEBYTECODE",
            }
        }
        if project_root is not None:
            environment["AI_COLLAB_PROJECT_ROOT"] = str(
                Path(project_root).resolve(strict=True)
            )
        try:
            completed = subprocess.run(
                self.command,
                cwd=self.working_directory,
                env=environment,
                input=canonical_json_bytes(request) + b"\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            if operation == "present":
                raise SecurityError(
                    "auth.confirmation-timeout",
                    "high-risk operation confirmation timed out",
                    retryable=True,
                ) from exc
            raise SecurityError(
                "security.adapter-unavailable",
                "security adapter is unavailable",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise SecurityError(
                "security.adapter-unavailable",
                "security adapter is unavailable",
                retryable=True,
            ) from exc
        if completed.returncode != 0 or completed.stderr:
            raise SecurityError(
                "security.adapter-failed",
                "security adapter failed closed",
                retryable=True,
            )
        if not completed.stdout or len(completed.stdout) > MAX_SECURITY_REPLY_BYTES:
            raise SecurityError("security.invalid-reply", "security adapter reply is invalid")
        try:
            value = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecurityError("security.invalid-reply", "security adapter reply is invalid") from exc
        if not isinstance(value, dict) or set(value) != {
            "security_adapter_protocol_version",
            "adapter_id",
            "outcome",
            "result",
        }:
            raise SecurityError("security.invalid-reply", "security adapter reply fields differ")
        if (
            value["security_adapter_protocol_version"] != SECURITY_ADAPTER_PROTOCOL_VERSION
            or value["adapter_id"] != self.adapter_id
            or value["outcome"] != "completed"
            or not isinstance(value["result"], dict)
        ):
            raise SecurityError("security.invalid-reply", "security adapter rejected the operation")
        return value["result"]


class SecurityCoordinator:
    """Build and durably consume exact high-risk authorization chains."""

    def __init__(
        self,
        state_root: Path,
        adapter: SecurityAdapterCommand,
        *,
        project_root_resolver: Callable[[str], Path] | None = None,
    ):
        supplied_root = Path(state_root).expanduser()
        if supplied_root.is_symlink():
            raise SecurityError("security.state-invalid", "security state root is invalid")
        supplied_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state_root = supplied_root.resolve()
        details = self.state_root.stat()
        if not self.state_root.is_dir() or details.st_uid != os.getuid():
            raise SecurityError("security.state-invalid", "security state root owner differs")
        os.chmod(self.state_root, 0o700)
        self.state_path = self.state_root / "security-confirmations.json"
        self.adapter = adapter
        self.project_root_resolver = project_root_resolver
        self._lock = threading.RLock()
        self.matrix = self._matrix()
        self.matrix_digest = canonical_json_sha256(self.matrix)
        with self._lock:
            if not self.state_path.exists():
                self._write_state(self._empty_state())
            self._read_state()

    @staticmethod
    def _permission_catalog() -> dict[str, Any]:
        permissions = []
        values = (
            (
                "permission.local-process-control",
                "presentation_control",
                "platform_plugin",
                "platform.local-process-control",
                "driver",
                "system_managed",
            ),
            (
                "permission.local-resource-control",
                "local_sensitive_storage",
                "platform_plugin",
                "platform.local-resource-control",
                "host",
                "system_managed",
            ),
            (
                "permission.project-storage",
                "project_storage",
                "project_adapter",
                "adapter.project-permission",
                "project",
                "user_initiated",
            ),
        )
        for (
            permission_id,
            category,
            provider_kind,
            provider_ref,
            subject_scope,
            user_mediation,
        ) in values:
            permissions.append(
                {
                    "permission_id": permission_id,
                    "category": category,
                    "provider_kind": provider_kind,
                    "provider_ref": provider_ref,
                    "subject_scope": subject_scope,
                    "probe_schema_digest": canonical_json_sha256(
                        {"schema_version": 1, "permission_id": permission_id}
                    ),
                    "user_mediation": user_mediation,
                    "secret_handling": "none",
                    "revocable": True,
                    "max_observation_age_ms": 60_000,
                }
            )
        return {
            "catalog_contract_version": 1,
            "catalog_id": "permission.harness-runtime-catalog",
            "permissions": permissions,
        }

    @classmethod
    def _matrix(cls) -> dict[str, Any]:
        catalog = cls._permission_catalog()
        policies = [
            {
                "policy_id": "confirmation.destructive-once",
                "authorization_scope": "exact_request",
                "presenter_requirement": "trusted_local_app",
                "interaction_mode": "explicit_user_action",
                "max_uses": 1,
                "max_duration_ms": CONFIRMATION_DURATION_MS,
                "requires_effect_preview": True,
                "requires_current_permission_snapshot_per_use": True,
                "scope_constraint_schema_digest": None,
            }
        ]
        bindings = []
        for descriptor in OPERATION_DESCRIPTORS:
            operation = descriptor["operation_id"]
            high_risk = operation in HIGH_RISK_OPERATIONS
            bindings.append(
                {
                    "operation_id": operation,
                    "operation_schema_version": descriptor["operation_schema_version"],
                    "operation_descriptor_digest": canonical_json_sha256(descriptor),
                    "mutation_class": descriptor["mutation_class"],
                    "target_scope": descriptor["target_scope"],
                    "required_capability": descriptor["required_capability"],
                    "required_permission_ids": (
                        [HIGH_RISK_OPERATIONS[operation]] if high_risk else []
                    ),
                    "risk_class": "high" if high_risk else (
                        "routine"
                        if descriptor["mutation_class"] in {"read_only", "durable_state"}
                        else "elevated"
                    ),
                    "confirmation_policy_ref": descriptor["confirmation_policy_ref"],
                    "effect_preview_schema_digest": (
                        EFFECT_PREVIEW_SCHEMA_DIGESTS[operation] if high_risk else None
                    ),
                }
            )
        return {
            "matrix_contract_version": 1,
            "matrix_id": "permission.harness-runtime-matrix",
            "matrix_revision": 1,
            "host_ipc_contract_digest": FROZEN_HOST_IPC_CONTRACT_DIGEST,
            "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
            "permission_catalog": catalog,
            "permission_catalog_digest": canonical_json_sha256(catalog),
            "confirmation_policies": policies,
            "confirmation_policy_set_digest": canonical_json_sha256(policies),
            "operation_bindings": bindings,
        }

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": SECURITY_STATE_SCHEMA_VERSION,
            "state_revision": 0,
            "chains": {},
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise SecurityError("security.state-invalid", "security state is unavailable")
        details = self.state_path.stat()
        if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
            raise SecurityError("security.state-invalid", "security state permissions differ")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecurityError("security.state-invalid", "security state is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "state_revision", "chains"}
            or value["schema_version"] != SECURITY_STATE_SCHEMA_VERSION
            or not isinstance(value["state_revision"], int)
            or not isinstance(value["chains"], dict)
        ):
            raise SecurityError("security.state-invalid", "security state schema differs")
        return value

    def _write_state(self, value: Mapping[str, Any]) -> None:
        temporary = self.state_root / f".security-state.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json_bytes(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            os.chmod(self.state_path, 0o600)
            directory = os.open(self.state_root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()

    def start_host(self) -> None:
        """A consumed chain without a recorded outcome remains spent/unknown."""

        unknown = self._unknown_operation_outcome()
        with self._lock:
            state = self._read_state()
            changed = False
            for chain in state["chains"].values():
                if chain["status"] == "consumed" and chain["operation_outcome"] is None:
                    chain["operation_outcome"] = copy.deepcopy(unknown)
                    changed = True
            if changed:
                state["state_revision"] += 1
                self._write_state(state)

    def authorize(
        self,
        request: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        *,
        effect_preview: Mapping[str, Any],
        private_subject: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        project_root = (
            self.project_root_resolver(request["target"]["project_instance_id"])
            if self.project_root_resolver is not None
            else None
        )
        operation = request["operation"]
        if descriptor["confirmation_policy_ref"] is None:
            return None
        if operation not in HIGH_RISK_OPERATIONS:
            raise SecurityError("security.matrix-invalid", "confirmation binding differs")
        request_digest = operation_intent_digest(request)
        with self._lock:
            state = self._read_state()
            existing = state["chains"].get(request_digest)
            if existing is not None and existing.get("status") != "denied":
                raise SecurityError(
                    "auth.authorization-replayed",
                    "high-risk authorization was already consumed",
                )

        binding = next(
            value
            for value in self.matrix["operation_bindings"]
            if value["operation_id"] == operation
        )
        policy = self.matrix["confirmation_policies"][0]
        target_digest = canonical_json_sha256(request["target"])
        fence_digest = canonical_json_sha256(request["fence"])
        preview = {
            "schema_digest": binding["effect_preview_schema_digest"],
            "value_digest": canonical_json_sha256(effect_preview),
        }
        first_snapshot = self._permission_snapshot(
            binding=binding,
            request_digest=request_digest,
            target_digest=target_digest,
            fence_digest=fence_digest,
            private_subject=private_subject,
            project_root=project_root,
        )
        now_ms = int(time.time() * 1000)
        challenge = {
            "challenge_contract_version": 1,
            "challenge_id": f"challenge-{uuid.uuid4().hex}",
            "matrix_digest": self.matrix_digest,
            "policy_id": policy["policy_id"],
            "policy_digest": canonical_json_sha256(policy),
            "authorization_scope": "exact_request",
            "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
            "operation_descriptor_digest": binding["operation_descriptor_digest"],
            "operation_request_digest": request_digest,
            "target_digest": target_digest,
            "fence_digest": fence_digest,
            "permission_snapshot_digest": canonical_json_sha256(first_snapshot),
            "effect_preview": preview,
            "scope_constraint_digest": None,
            "issued_at_epoch_ms": now_ms,
            "expires_at_epoch_ms": now_ms + CONFIRMATION_DURATION_MS,
        }
        presented = self.adapter.call(
            "present",
            {
                "challenge": challenge,
                "effect_preview": copy.deepcopy(effect_preview),
            },
        )
        required_presented = {
            "challenge_digest",
            "outcome",
            "decided_at_epoch_ms",
            "presenter_instance_digest",
            "decision_evidence_digest",
            "reason_code",
        }
        if (
            set(presented) != required_presented
            or presented["challenge_digest"] != canonical_json_sha256(challenge)
            or presented["outcome"] not in {"approved", "denied"}
            or not self._sha256(presented["presenter_instance_digest"])
            or not self._sha256(presented["decision_evidence_digest"])
            or not isinstance(presented["decided_at_epoch_ms"], int)
            or isinstance(presented["decided_at_epoch_ms"], bool)
            or presented["decided_at_epoch_ms"] < challenge["issued_at_epoch_ms"]
            or presented["decided_at_epoch_ms"] > challenge["expires_at_epoch_ms"]
            or (
                presented["reason_code"] is not None
                and not isinstance(presented["reason_code"], str)
            )
        ):
            raise SecurityError("security.invalid-reply", "confirmation decision is invalid")
        decision = {
            "decision_contract_version": 1,
            "decision_id": f"decision-{uuid.uuid4().hex}",
            "challenge_digest": canonical_json_sha256(challenge),
            "outcome": presented["outcome"],
            "decided_at_epoch_ms": presented["decided_at_epoch_ms"],
            "actor_scope": "current_user",
            "presenter_instance_digest": presented["presenter_instance_digest"],
            "reason_code": presented["reason_code"],
        }
        if decision["outcome"] != "approved":
            self._record_denied(request_digest, first_snapshot, challenge, decision)
            if decision["reason_code"] == "confirmation.timeout":
                raise SecurityError(
                    "auth.confirmation-timeout",
                    "high-risk operation confirmation timed out",
                    retryable=True,
                )
            raise SecurityError(
                "auth.confirmation-denied", "high-risk operation was denied"
            )

        current_snapshot = self._permission_snapshot(
            binding=binding,
            request_digest=request_digest,
            target_digest=target_digest,
            fence_digest=fence_digest,
            private_subject=private_subject,
            project_root=project_root,
        )
        first_subjects = {
            value["permission_id"]: value["subject_digest"]
            for value in first_snapshot["observations"]
        }
        current_subjects = {
            value["permission_id"]: value["subject_digest"]
            for value in current_snapshot["observations"]
        }
        if first_subjects != current_subjects:
            raise SecurityError(
                "auth.permission-denied", "permission subject changed during confirmation"
            )
        authorized_scope_digest = canonical_json_sha256(
            {
                "authorization_scope": "exact_request",
                "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
                "operation_descriptor_digest": binding["operation_descriptor_digest"],
                "operation_request_digest": request_digest,
                "target_digest": target_digest,
                "fence_digest": fence_digest,
                "permission_snapshot_digest": canonical_json_sha256(first_snapshot),
                "effect_preview": preview,
            }
        )
        authorization = {
            "authorization_contract_version": 1,
            "authorization_id": f"authorization-{uuid.uuid4().hex}",
            "matrix_digest": self.matrix_digest,
            "policy_id": policy["policy_id"],
            "policy_digest": canonical_json_sha256(policy),
            "challenge_digest": canonical_json_sha256(challenge),
            "decision_digest": canonical_json_sha256(decision),
            "authorization_scope": "exact_request",
            "authorized_scope_digest": authorized_scope_digest,
            "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
            "operation_descriptor_digest": binding["operation_descriptor_digest"],
            "target_digest": target_digest,
            "issued_at_epoch_ms": decision["decided_at_epoch_ms"],
            "expires_at_epoch_ms": challenge["expires_at_epoch_ms"],
            "max_uses": 1,
        }
        consumed_at = int(time.time() * 1000)
        if consumed_at < authorization["issued_at_epoch_ms"]:
            raise SecurityError(
                "security.invalid-reply",
                "confirmation decision timestamp is invalid",
            )
        if consumed_at > authorization["expires_at_epoch_ms"]:
            raise SecurityError("auth.confirmation-expired", "confirmation expired")
        permission_descriptors = {
            value["permission_id"]: value
            for value in self.matrix["permission_catalog"]["permissions"]
        }
        for observation in first_snapshot["observations"]:
            permission_descriptor = permission_descriptors[
                observation["permission_id"]
            ]
            valid_until = observation["valid_until_epoch_ms"]
            if (
                consumed_at - observation["observed_at_epoch_ms"]
                > permission_descriptor["max_observation_age_ms"]
                or (valid_until is not None and valid_until < consumed_at)
            ):
                raise SecurityError(
                    "auth.permission-denied",
                    "permission snapshot expired during confirmation",
                )
        consumption = {
            "consumption_contract_version": 1,
            "consumption_id": f"consumption-{uuid.uuid4().hex}",
            "matrix_digest": self.matrix_digest,
            "authorization_digest": canonical_json_sha256(authorization),
            "use_index": 1,
            "previous_consumption_digest": None,
            "operation_registry_digest": OPERATION_REGISTRY_DIGEST,
            "operation_descriptor_digest": binding["operation_descriptor_digest"],
            "operation_request_digest": request_digest,
            "target_digest": target_digest,
            "fence_digest": fence_digest,
            "permission_snapshot_digest": canonical_json_sha256(first_snapshot),
            "effect_preview": preview,
            "scope_membership_evidence": None,
            "consumed_at_epoch_ms": consumed_at,
        }
        with self._lock:
            state = self._read_state()
            existing = state["chains"].get(request_digest)
            if existing is not None and existing.get("status") != "denied":
                raise SecurityError(
                    "auth.authorization-replayed",
                    "high-risk authorization was already consumed",
                )
            chain = {
                "status": "consumed",
                "operation": operation,
                "request_id": request["request_id"],
                "permission_snapshot": first_snapshot,
                "permission_revalidation_snapshot": current_snapshot,
                "challenge": challenge,
                "decision": decision,
                "authorization": authorization,
                "consumption": consumption,
                "effect_preview": copy.deepcopy(effect_preview),
                "operation_outcome": None,
            }
            if existing is not None:
                chain["denied_history"] = self._denied_history(existing)
            state["chains"][request_digest] = chain
            state["state_revision"] += 1
            self._write_state(state)
        return copy.deepcopy(consumption)

    def mark_outcome(
        self,
        consumption: Mapping[str, Any] | None,
        *,
        outcome: str,
        operation_id: str | None,
        result: Mapping[str, Any] | None,
    ) -> None:
        if consumption is None:
            return
        request_digest = consumption["operation_request_digest"]
        with self._lock:
            state = self._read_state()
            chain = state["chains"].get(request_digest)
            if (
                chain is None
                or chain["consumption"]["consumption_id"]
                != consumption["consumption_id"]
                or chain["operation_outcome"] is not None
            ):
                raise SecurityError("security.outcome-invalid", "security outcome fence differs")
            chain["operation_outcome"] = {
                "outcome": outcome,
                "operation_id": operation_id,
                "result_digest": (
                    canonical_json_sha256(result) if result is not None else None
                ),
            }
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _unknown_operation_outcome() -> dict[str, Any]:
        return {
            "outcome": "unknown",
            "operation_id": None,
            "result_digest": None,
        }

    def reconcile_unknown_outcome(
        self,
        request_digest: str,
        *,
        allow_missing: bool = False,
    ) -> None:
        """Persist an exact unresolved outcome without replacing a terminal one."""

        unknown = self._unknown_operation_outcome()
        with self._lock:
            state = self._read_state()
            chain = state["chains"].get(request_digest)
            if chain is None and allow_missing:
                return
            if chain is None or chain.get("status") != "consumed":
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            previous = chain.get("operation_outcome")
            if previous == unknown:
                return
            if previous is not None and (
                not isinstance(previous, dict)
                or previous.get("outcome") != "unknown"
            ):
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            chain["operation_outcome"] = unknown
            state["state_revision"] += 1
            self._write_state(state)

    def reconcile_completed_outcome(
        self,
        request_digest: str,
        *,
        operation_id: str,
        result: Mapping[str, Any],
        allow_missing: bool = False,
    ) -> None:
        """Join an exact durable success after an unknown process outcome."""

        completed = {
            "outcome": "completed",
            "operation_id": operation_id,
            "result_digest": canonical_json_sha256(result),
        }
        with self._lock:
            state = self._read_state()
            chain = state["chains"].get(request_digest)
            if chain is None and allow_missing:
                return
            if chain is None or chain.get("status") != "consumed":
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            previous = chain.get("operation_outcome")
            if previous == completed:
                return
            if previous is not None and previous.get("outcome") != "unknown":
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            chain["operation_outcome"] = completed
            state["state_revision"] += 1
            self._write_state(state)

    def reconcile_failed_outcome(
        self,
        request_digest: str,
        *,
        allow_missing: bool = False,
    ) -> None:
        """Close an unknown consumed chain after a proven no-effect abort."""

        failed = {
            "outcome": "failed",
            "operation_id": None,
            "result_digest": None,
        }
        with self._lock:
            state = self._read_state()
            chain = state["chains"].get(request_digest)
            if chain is None and allow_missing:
                return
            if chain is None or chain.get("status") != "consumed":
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            previous = chain.get("operation_outcome")
            if previous == failed:
                return
            if previous is not None and previous.get("outcome") != "unknown":
                raise SecurityError(
                    "security.outcome-invalid", "security outcome fence differs"
                )
            chain["operation_outcome"] = failed
            state["state_revision"] += 1
            self._write_state(state)

    def _permission_snapshot(
        self,
        *,
        binding: Mapping[str, Any],
        request_digest: str,
        target_digest: str,
        fence_digest: str,
        private_subject: Mapping[str, Any],
        project_root: Path | None,
    ) -> dict[str, Any]:
        captured_at = int(time.time() * 1000)
        payload = {
            "permission_ids": binding["required_permission_ids"],
            "private_subject": private_subject,
            "captured_at_epoch_ms": captured_at,
        }
        if project_root is None:
            observed = self.adapter.call("observe", payload)
        else:
            observed = self.adapter.call(
                "observe", payload, project_root=project_root
            )
        if set(observed) != {"observations"} or not isinstance(
            observed["observations"], list
        ):
            raise SecurityError("security.invalid-reply", "permission observation is invalid")
        descriptors = {
            value["permission_id"]: value
            for value in self.matrix["permission_catalog"]["permissions"]
        }
        values = observed["observations"]
        if {value.get("permission_id") for value in values} != set(
            binding["required_permission_ids"]
        ):
            raise SecurityError("auth.permission-denied", "permission observation set differs")
        normalized = []
        for value in values:
            permission_id = value["permission_id"]
            descriptor = descriptors[permission_id]
            fields = {
                "permission_id",
                "subject_digest",
                "status",
                "observed_at_epoch_ms",
                "valid_until_epoch_ms",
                "evidence_digest",
                "provider_error_code",
                "remediation_ref",
            }
            if (
                not isinstance(value, dict)
                or set(value) != fields
                or value["status"] != "granted"
                or not self._sha256(value["subject_digest"])
                or not self._sha256(value["evidence_digest"])
                or not isinstance(value["observed_at_epoch_ms"], int)
                or isinstance(value["observed_at_epoch_ms"], bool)
                or value["observed_at_epoch_ms"] > captured_at
                or captured_at - value["observed_at_epoch_ms"]
                > descriptor["max_observation_age_ms"]
                or (
                    value["valid_until_epoch_ms"] is not None
                    and value["valid_until_epoch_ms"] < captured_at
                )
                or value["provider_error_code"] is not None
            ):
                raise SecurityError("auth.permission-denied", "permission is not currently granted")
            normalized.append(
                {
                    "observation_id": f"observation-{uuid.uuid4().hex}",
                    "permission_id": permission_id,
                    "permission_descriptor_digest": canonical_json_sha256(descriptor),
                    **copy.deepcopy(value),
                }
            )
        return {
            "snapshot_contract_version": 1,
            "snapshot_id": f"snapshot-{uuid.uuid4().hex}",
            "matrix_digest": self.matrix_digest,
            "operation_descriptor_digest": binding["operation_descriptor_digest"],
            "operation_request_digest": request_digest,
            "target_digest": target_digest,
            "fence_digest": fence_digest,
            "captured_at_epoch_ms": captured_at,
            "observations": normalized,
        }

    def _record_denied(
        self,
        request_digest: str,
        snapshot: Mapping[str, Any],
        challenge: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> None:
        with self._lock:
            state = self._read_state()
            existing = state["chains"].get(request_digest)
            if existing is not None and existing.get("status") != "denied":
                raise SecurityError("auth.authorization-replayed", "confirmation already exists")
            denied_history = (
                self._denied_history(existing) if existing is not None else []
            )
            denied_history.append(
                {
                    "permission_snapshot": copy.deepcopy(snapshot),
                    "challenge": copy.deepcopy(challenge),
                    "decision": copy.deepcopy(decision),
                }
            )
            state["chains"][request_digest] = {
                "status": "denied",
                "operation": None,
                "request_id": None,
                "permission_snapshot": copy.deepcopy(snapshot),
                "challenge": copy.deepcopy(challenge),
                "decision": copy.deepcopy(decision),
                "authorization": None,
                "consumption": None,
                "effect_preview": None,
                "operation_outcome": None,
                "denied_history": denied_history,
            }
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _denied_history(chain: Mapping[str, Any]) -> list[dict[str, Any]]:
        history = chain.get("denied_history")
        if isinstance(history, list):
            return copy.deepcopy(history)
        return [
            {
                "permission_snapshot": copy.deepcopy(chain["permission_snapshot"]),
                "challenge": copy.deepcopy(chain["challenge"]),
                "decision": copy.deepcopy(chain["decision"]),
            }
        ]

    @staticmethod
    def _sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )
