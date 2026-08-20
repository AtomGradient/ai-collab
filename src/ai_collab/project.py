# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Owner-private project registration behind the generic Host IPC surface."""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .protocol import canonical_json_bytes, canonical_json_sha256
from .workspace import ProjectAdapterCommand, WorkspaceError


PROJECT_REGISTRY_SCHEMA_VERSION = 2
LEGACY_PROJECT_REGISTRY_SCHEMA_VERSION = 1
NAMESPACED_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass
class ProjectError(ValueError):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class ProjectRegistry:
    """Persist local roots privately while returning only redacted project records."""

    def __init__(
        self,
        state_root: Path,
        adapter: ProjectAdapterCommand | None,
    ):
        self.state_root = Path(state_root).resolve()
        self.state_path = self.state_root / "project-registry.json"
        self.adapter = adapter
        self._lock = threading.RLock()
        with self._lock:
            if not self.state_path.exists():
                self._write_state(self._empty_state())
            else:
                self._migrate_legacy_state()
            self._read_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": PROJECT_REGISTRY_SCHEMA_VERSION,
            "state_revision": 0,
            "projects": {},
            "requests": {},
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise ProjectError("project.state-invalid", "project registry is unavailable")
        details = self.state_path.stat()
        if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
            raise ProjectError("project.state-invalid", "project registry permissions differ")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError("project.state-invalid", "project registry is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "state_revision", "projects", "requests"}
            or value["schema_version"] != PROJECT_REGISTRY_SCHEMA_VERSION
            or not isinstance(value["state_revision"], int)
            or isinstance(value["state_revision"], bool)
            or value["state_revision"] < 0
            or not isinstance(value["projects"], dict)
            or not isinstance(value["requests"], dict)
        ):
            raise ProjectError("project.state-invalid", "project registry schema differs")
        try:
            for project_instance_id, item in value["projects"].items():
                if (
                    not isinstance(project_instance_id, str)
                    or not project_instance_id
                    or not isinstance(item, dict)
                    or set(item)
                    != {
                        "canonical_root",
                        "canonical_root_fingerprint",
                        "record",
                        "render",
                        "pending_reconciliation",
                        "accepted_binding_digests",
                    }
                    or not isinstance(item["canonical_root"], str)
                    or not Path(item["canonical_root"]).is_absolute()
                    or not self._is_sha256(item["canonical_root_fingerprint"])
                    or item["canonical_root_fingerprint"]
                    != canonical_json_sha256(
                        {"canonical_project_path": item["canonical_root"]}
                    )
                ):
                    raise ValueError
                record = self._validate_record(item["record"])
                self._validate_render(item["render"])
                accepted = item["accepted_binding_digests"]
                if (
                    not isinstance(accepted, list)
                    or not accepted
                    or len(accepted) > 256
                    or len(accepted) != len(set(accepted))
                    or any(not self._is_sha256(digest) for digest in accepted)
                    or record["project_binding_digest"] not in accepted
                ):
                    raise ValueError
                pending = item["pending_reconciliation"]
                if pending is not None:
                    if (
                        not isinstance(pending, dict)
                        or set(pending) != {"public", "render", "reconciliation"}
                    ):
                        raise ValueError
                    self._validate_observation(
                        {
                            "project": pending["public"],
                            "render": pending["render"],
                        }
                    )
                    self._validate_reconciliation(pending["reconciliation"])
                if record["project_instance_id"] != project_instance_id:
                    raise ValueError
            for request_id, request in value["requests"].items():
                if (
                    not isinstance(request_id, str)
                    or not request_id
                    or not isinstance(request, dict)
                    or set(request) != {"request_digest", "operation_id", "result"}
                    or not self._is_sha256(request["request_digest"])
                    or not isinstance(request["operation_id"], str)
                    or not request["operation_id"]
                    or not isinstance(request["result"], dict)
                    or set(request["result"])
                    not in (
                        {"project"},
                        {"project", "reconciliation"},
                        {"unregistered"},
                        {"bootstrap"},
                    )
                ):
                    raise ValueError
                if "project" in request["result"]:
                    self._validate_record(request["result"]["project"])
                    if "reconciliation" in request["result"]:
                        self._validate_reconciliation(
                            request["result"]["reconciliation"]
                        )
                elif "bootstrap" in request["result"]:
                    self._validate_bootstrap_result(request["result"]["bootstrap"])
                else:
                    removal = request["result"]["unregistered"]
                    if (
                        not isinstance(removal, dict)
                        or set(removal)
                        != {
                            "project_instance_id",
                            "project_key",
                            "registration_revision",
                        }
                        or not isinstance(removal["project_instance_id"], str)
                        or not removal["project_instance_id"]
                        or not isinstance(removal["project_key"], str)
                        or not removal["project_key"]
                        or not isinstance(removal["registration_revision"], int)
                        or isinstance(removal["registration_revision"], bool)
                        or removal["registration_revision"] < 1
                    ):
                        raise ValueError
        except (KeyError, TypeError, ValueError, ProjectError) as exc:
            raise ProjectError(
                "project.state-invalid", "project registry records differ"
            ) from exc
        return value

    def _migrate_legacy_state(self) -> None:
        """Upgrade v0.1.6.1 project registrations without re-registration."""

        if self.state_path.is_symlink() or not self.state_path.is_file():
            return
        details = self.state_path.stat()
        if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
            return
        try:
            raw = self.state_path.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return
        if (
            not isinstance(value, dict)
            or value.get("schema_version")
            != LEGACY_PROJECT_REGISTRY_SCHEMA_VERSION
        ):
            return
        if set(value) != {"schema_version", "state_revision", "projects", "requests"}:
            raise ProjectError("project.state-invalid", "legacy project registry fields differ")
        projects = value.get("projects")
        state_revision = value.get("state_revision")
        requests = value.get("requests")
        if (
            not isinstance(projects, dict)
            or not isinstance(requests, dict)
            or not isinstance(state_revision, int)
            or isinstance(state_revision, bool)
            or state_revision < 0
        ):
            raise ProjectError("project.state-invalid", "legacy project registry differs")
        for item in projects.values():
            if not isinstance(item, dict) or set(item) != {
                "canonical_root",
                "canonical_root_fingerprint",
                "record",
            }:
                raise ProjectError("project.state-invalid", "legacy project record differs")
            self._validate_record(item["record"])
            item["render"] = None
            item["pending_reconciliation"] = None
            item["accepted_binding_digests"] = [
                item["record"]["project_binding_digest"]
            ]
        backup = self.state_root / "project-registry.v1.last-good.json"
        if backup.exists() or backup.is_symlink():
            if (
                backup.is_symlink()
                or not backup.is_file()
                or backup.stat().st_uid != os.getuid()
                or stat.S_IMODE(backup.stat().st_mode) != 0o600
            ):
                raise ProjectError(
                    "project.state-invalid",
                    "project registry last-good snapshot differs",
                )
        else:
            temporary = self.state_root / (
                f".project-registry.v1.last-good.{os.getpid()}."
                f"{secrets.token_hex(6)}.tmp"
            )
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, backup)
                os.chmod(backup, 0o600)
            finally:
                if temporary.exists():
                    temporary.unlink()
        value["schema_version"] = PROJECT_REGISTRY_SCHEMA_VERSION
        value["state_revision"] += 1
        self._write_validated_migration(value)

    def _write_validated_migration(self, value: Mapping[str, Any]) -> None:
        """Write, validate, then atomically swap an exact migration candidate."""

        temporary = self.state_root / (
            f".project-registry.migration.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json_bytes(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            original = self.state_path
            self.state_path = temporary
            try:
                self._read_state()
            finally:
                self.state_path = original
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

    def _write_state(self, value: dict[str, Any]) -> None:
        temporary = self.state_root / (
            f".project-registry.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
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

    def register(
        self,
        *,
        request_id: str,
        request_digest: str,
        canonical_project_path: str,
    ) -> tuple[str, dict[str, Any]]:
        if self.adapter is None:
            raise ProjectError(
                "project.adapter-unavailable",
                "project adapter is not configured",
                retryable=True,
            )
        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])

        supplied = Path(canonical_project_path).expanduser()
        if supplied.is_symlink():
            raise ProjectError("project.path-invalid", "project root must not be a symlink")
        try:
            root = supplied.resolve(strict=True)
        except OSError as exc:
            raise ProjectError("project.path-invalid", "project root is unavailable") from exc
        if not root.is_dir() or root.stat().st_uid != os.getuid():
            raise ProjectError("project.path-invalid", "project root owner differs")
        root_fingerprint = canonical_json_sha256({"canonical_project_path": str(root)})

        # Registering an already-known canonical root is an idempotent lookup,
        # not an alternate path for accepting a changed project binding.  The
        # explicit reconcile/accept flow owns those changes so that choosing
        # the same folder again cannot silently replace team intent.
        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            existing_id = self._find_project_by_root(
                state,
                root=str(root),
                root_fingerprint=root_fingerprint,
            )
            if existing_id is not None:
                return self._record_existing_registration(
                    state,
                    request_id=request_id,
                    request_digest=request_digest,
                    project_instance_id=existing_id,
                )

        try:
            if isinstance(self.adapter, ProjectAdapterCommand):
                observed = self.adapter.call(
                    "register",
                    {"canonical_project_path": str(root)},
                    project_root=root,
                )
            else:
                observed = self.adapter.call(
                    "register", {"canonical_project_path": str(root)}
                )
        except WorkspaceError as exc:
            raise ProjectError(
                exc.code,
                exc.message,
                exc.retryable,
            ) from exc
        public = self._validate_observation(observed)
        render = copy.deepcopy(observed.get("render"))

        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            existing_id = self._find_project_by_root(
                state,
                root=str(root),
                root_fingerprint=root_fingerprint,
            )
            if existing_id is not None:
                return self._record_existing_registration(
                    state,
                    request_id=request_id,
                    request_digest=request_digest,
                    project_instance_id=existing_id,
                )
            project_instance_id = f"project-{uuid.uuid4().hex}"
            record = {
                "project_instance_id": project_instance_id,
                "registration_revision": 1,
                **public,
            }
            state["projects"][project_instance_id] = {
                "canonical_root": str(root),
                "canonical_root_fingerprint": root_fingerprint,
                "record": record,
                "render": render,
                "pending_reconciliation": None,
                "accepted_binding_digests": [record["project_binding_digest"]],
            }
            operation_id = f"project-op-{uuid.uuid4().hex}"
            result = {"project": copy.deepcopy(record)}
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "result": copy.deepcopy(result),
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    @staticmethod
    def _find_project_by_root(
        state: Mapping[str, Any],
        *,
        root: str,
        root_fingerprint: str,
    ) -> str | None:
        return next(
            (
                project_id
                for project_id, item in state["projects"].items()
                if item["canonical_root_fingerprint"] == root_fingerprint
                and item["canonical_root"] == root
            ),
            None,
        )

    def _record_existing_registration(
        self,
        state: dict[str, Any],
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
    ) -> tuple[str, dict[str, Any]]:
        operation_id = f"project-op-{uuid.uuid4().hex}"
        result = {
            "project": copy.deepcopy(
                state["projects"][project_instance_id]["record"]
            )
        }
        state["requests"][request_id] = {
            "request_digest": request_digest,
            "operation_id": operation_id,
            "result": copy.deepcopy(result),
        }
        state["state_revision"] += 1
        self._write_state(state)
        return operation_id, result

    def list(self) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            return {
                "projects": sorted(
                    (
                        copy.deepcopy(item["record"])
                        for item in state["projects"].values()
                    ),
                    key=lambda value: (
                        value["project_key"], value["project_instance_id"]
                    ),
                )
            }

    def reconcile(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Observe drift without silently changing a pinned project contract."""

        if self.adapter is None:
            raise ProjectError(
                "project.adapter-unavailable",
                "project adapter is not configured",
                retryable=True,
            )
        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            if project_instance_id not in state["projects"]:
                raise ProjectError("project.not-found", "project is not registered")

        root = self.canonical_root(project_instance_id)
        try:
            if isinstance(self.adapter, ProjectAdapterCommand):
                observed = self.adapter.call(
                    "register",
                    {"canonical_project_path": str(root)},
                    project_root=root,
                )
            else:
                observed = self.adapter.call(
                    "register", {"canonical_project_path": str(root)}
                )
        except WorkspaceError as exc:
            raise ProjectError(exc.code, exc.message, exc.retryable) from exc
        public = self._validate_observation(observed)
        render = copy.deepcopy(observed.get("render"))
        availability = (
            render.get("availability") if isinstance(render, dict) else None
        )
        if not isinstance(availability, dict):
            availability = {
                "status": "ready",
                "changes": [],
                "warnings": [],
                "fingerprint": canonical_json_sha256(
                    {"status": "ready", "changes": [], "warnings": []}
                ),
            }
        reconciliation = {
            "status": availability["status"],
            "binding_changed": False,
            # This public token fences the complete candidate the employee is
            # being asked to accept. Availability alone can remain identical
            # while team intent changes between observation and confirmation.
            "availability_fingerprint": canonical_json_sha256(
                {
                    "availability_fingerprint": availability["fingerprint"],
                    "project_binding_digest": public["project_binding_digest"],
                }
            ),
            "changes": copy.deepcopy(availability["changes"]),
            "warnings": copy.deepcopy(availability["warnings"]),
        }

        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            current = item["record"]
            reconciliation["binding_changed"] = any(
                current[field] != value for field, value in public.items()
            )
            if reconciliation["binding_changed"] and render is None:
                raise ProjectError(
                    "project.adapter-invalid",
                    "project adapter changed a binding without a resolved render",
                )
            runtime_refresh = reconciliation[
                "binding_changed"
            ] and self._is_compatible_runtime_refresh(
                current=current,
                previous_render=item["render"],
                public=public,
                render=render,
            )
            if runtime_refresh:
                record = {
                    "project_instance_id": project_instance_id,
                    "registration_revision": current["registration_revision"] + 1,
                    **copy.deepcopy(public),
                }
                item["record"] = record
                item["render"] = render
                item["pending_reconciliation"] = None
                item["accepted_binding_digests"] = self._retain_accepted_bindings(
                    item["accepted_binding_digests"],
                    record["project_binding_digest"],
                )
                reconciliation["binding_changed"] = False
            elif reconciliation["binding_changed"]:
                reconciliation["status"] = "attention"
                item["pending_reconciliation"] = {
                    "public": copy.deepcopy(public),
                    "render": copy.deepcopy(render),
                    "reconciliation": copy.deepcopy(reconciliation),
                }
                record = current
            else:
                record = current
                item["render"] = render
                item["pending_reconciliation"] = None
            operation_id = f"project-op-{uuid.uuid4().hex}"
            result = {
                "project": copy.deepcopy(record),
                "reconciliation": copy.deepcopy(reconciliation),
            }
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "result": copy.deepcopy(result),
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    @staticmethod
    def _is_compatible_runtime_refresh(
        *,
        current: Mapping[str, Any],
        previous_render: Mapping[str, Any] | None,
        public: Mapping[str, Any],
        render: Mapping[str, Any] | None,
    ) -> bool:
        """Recognize tool-owned upgrades that must not require employee action."""

        if render is None:
            return False
        stable_public = {
            "project_key",
            "repo_manifest_digest",
            "participant_driver_contract",
            "collaboration_policy_schema",
        }
        if any(current[field] != public[field] for field in stable_public):
            return False
        if previous_render is None:
            return (
                current["product_contract_version"] in {"1.0", "3.2"}
                and current["workspace_adapter_id"]
                in {"ai-collab-workspace-v1", "ai-collab-edgestudio-workspace-v1"}
                and current["environment_adapter_id"]
                in {
                    "ai-collab-environment-v1",
                    "ai-collab-edgestudio-environment-v1",
                }
            )

        def same_source(left: Any, right: Any) -> bool:
            if left == right:
                return True
            return (
                isinstance(left, Mapping)
                and isinstance(right, Mapping)
                and left.get("kind") == right.get("kind") == "builtin"
                and left.get("profile_id") == right.get("profile_id")
            )

        return (
            previous_render.get("source") == render.get("source")
            and previous_render.get("repo_manifest_digest")
            == render.get("repo_manifest_digest")
            and same_source(previous_render.get("gate"), render.get("gate"))
            and same_source(
                previous_render.get("collaboration"), render.get("collaboration")
            )
        )

    def accept_reconciliation(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        availability_fingerprint: str,
    ) -> tuple[str, dict[str, Any]]:
        """Apply the exact pending private render after an explicit user action."""

        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            pending = item["pending_reconciliation"]
            if pending is None:
                raise ProjectError(
                    "project.reconciliation-unavailable",
                    "project has no pending configuration update",
                )
            reconciliation = pending["reconciliation"]
            if reconciliation["availability_fingerprint"] != availability_fingerprint:
                raise ProjectError(
                    "project.reconciliation-stale",
                    "project reconciliation changed; check for updates again",
                    retryable=True,
                )
            current = item["record"]
            record = {
                "project_instance_id": project_instance_id,
                "registration_revision": current["registration_revision"] + 1,
                **copy.deepcopy(pending["public"]),
            }
            item["record"] = record
            item["render"] = copy.deepcopy(pending["render"])
            item["pending_reconciliation"] = None
            item["accepted_binding_digests"] = self._retain_accepted_bindings(
                item["accepted_binding_digests"],
                record["project_binding_digest"],
            )
            operation_id = f"project-op-{uuid.uuid4().hex}"
            result = {
                "project": copy.deepcopy(record),
                "reconciliation": {
                    **copy.deepcopy(reconciliation),
                    "binding_changed": False,
                    "status": "ready" if not reconciliation["changes"] else "attention",
                },
            }
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "result": copy.deepcopy(result),
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    @staticmethod
    def _retain_accepted_bindings(
        existing: list[str], binding_digest: str
    ) -> list[str]:
        """Keep the migrated binding plus the newest bounded audit history."""

        accepted = list(dict.fromkeys([*existing, binding_digest]))
        if len(accepted) <= 256:
            return accepted
        return [accepted[0], *accepted[-255:]]

    @classmethod
    def _validate_bootstrap_result(cls, value: Any) -> None:
        legacy_fields = {"created", "already_configured", "project_key"}
        if isinstance(value, dict) and set(value) == legacy_fields:
            if (
                isinstance(value["created"], list)
                and all(
                    isinstance(item, str)
                    and item
                    and "/" not in item
                    and ".." not in item
                    for item in value["created"]
                )
                and isinstance(value["already_configured"], bool)
                and (
                    value["project_key"] is None
                    or isinstance(value["project_key"], str)
                )
            ):
                return
            raise ValueError
        if (
            not isinstance(value, dict)
            or set(value)
            != {"created", "already_configured", "project_key", "proposal"}
            or not isinstance(value["created"], list)
            or any(
                not isinstance(item, str) or not item or "/" in item or ".." in item
                for item in value["created"]
            )
            or not isinstance(value["already_configured"], bool)
            or not (
                value["project_key"] is None or isinstance(value["project_key"], str)
            )
            or not isinstance(value["proposal"], dict)
            or set(value["proposal"]) != {"intent_digest", "yaml"}
            or not cls._is_sha256(value["proposal"]["intent_digest"])
            or not isinstance(value["proposal"]["yaml"], str)
            or not value["proposal"]["yaml"]
            or len(value["proposal"]["yaml"].encode("utf-8")) > 256 * 1024
        ):
            raise ValueError

    @classmethod
    def _validate_reconciliation(cls, value: Any) -> None:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "status",
                "binding_changed",
                "availability_fingerprint",
                "changes",
                "warnings",
            }
            or value["status"] not in {"ready", "attention"}
            or not isinstance(value["binding_changed"], bool)
            or not cls._is_sha256(value["availability_fingerprint"])
            or not isinstance(value["changes"], list)
            or len(value["changes"]) > 256
            or not isinstance(value["warnings"], list)
            or len(value["warnings"]) > 256
            or any(not isinstance(item, str) or not item for item in value["warnings"])
        ):
            raise ValueError
        for change in value["changes"]:
            if (
                not isinstance(change, dict)
                or not {"repo_key", "path", "classification", "status"}.issubset(change)
                or set(change) - {"repo_key", "path", "classification", "status", "reasons"}
                or not all(
                    isinstance(change[field], str) and change[field]
                    for field in {"repo_key", "path", "classification", "status"}
                )
                or (
                    "reasons" in change
                    and (
                        not isinstance(change["reasons"], list)
                        or any(not isinstance(item, str) or not item for item in change["reasons"])
                    )
                )
            ):
                raise ValueError

    def bootstrap(
        self,
        *,
        request_id: str,
        request_digest: str,
        canonical_project_path: str,
    ) -> tuple[str, dict[str, Any]]:
        """Return an owner-private intent draft without writing canonical source."""
        if self.adapter is None:
            raise ProjectError(
                "project.adapter-unavailable",
                "project adapter is not configured",
                retryable=True,
            )
        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])

        supplied = Path(canonical_project_path).expanduser()
        if supplied.is_symlink():
            raise ProjectError("project.path-invalid", "project root must not be a symlink")
        try:
            root = supplied.resolve(strict=True)
        except OSError as exc:
            raise ProjectError("project.path-invalid", "project root is unavailable") from exc
        if not root.is_dir() or root.stat().st_uid != os.getuid():
            raise ProjectError("project.path-invalid", "project root owner differs")

        try:
            if isinstance(self.adapter, ProjectAdapterCommand):
                observed = self.adapter.call(
                    "bootstrap",
                    {"canonical_project_path": str(root)},
                    project_root=root,
                )
            else:
                observed = self.adapter.call(
                    "bootstrap", {"canonical_project_path": str(root)}
                )
        except WorkspaceError as exc:
            raise ProjectError(
                exc.code,
                exc.message,
                exc.retryable,
            ) from exc
        if not isinstance(observed, dict) or set(observed) != {"bootstrap"}:
            raise ProjectError("project.binding-drift", "bootstrap reply differs")
        try:
            self._validate_bootstrap_result(observed["bootstrap"])
        except ValueError as exc:
            raise ProjectError(
                "project.binding-drift", "bootstrap reply differs"
            ) from exc

        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            operation_id = f"project-op-{uuid.uuid4().hex}"
            result = {"bootstrap": copy.deepcopy(observed["bootstrap"])}
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "result": copy.deepcopy(result),
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    def unregister(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Remove one registration record.

        The Host has already proven the project owns no durable Scenarios;
        this only forgets the redacted record and the private canonical-root
        binding. Nothing on disk outside the registry is touched, and the
        project can simply be registered again.
        """
        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            record = item["record"]
            result = {
                "unregistered": {
                    "project_instance_id": project_instance_id,
                    "project_key": record["project_key"],
                    "registration_revision": record["registration_revision"],
                }
            }
            del state["projects"][project_instance_id]
            operation_id = f"project-op-{uuid.uuid4().hex}"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "result": copy.deepcopy(result),
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    def collaboration_templates(self, project_instance_id: str) -> dict[str, Any]:
        """Return the current project catalog, preferring its frozen render."""

        render = self.resolved_render(project_instance_id)
        collaboration = (
            render.get("collaboration") if isinstance(render, dict) else None
        )
        if isinstance(collaboration, dict) and (
            "registry_snapshot" in collaboration
            or "registry_snapshot_digest" in collaboration
        ):
            return self.collaboration_templates_from_render(render)

        # v0.1.6.1 registrations, plus prerelease v0.1.7 state created before
        # embedded catalogs existed, have only a source pointer. Keep that
        # compatibility path until a successful reconciliation refreshes the
        # current render. Every newly resolved render is self-contained.
        if self.adapter is None:
            raise ProjectError(
                "project.adapter-unavailable",
                "project adapter is not configured",
                retryable=True,
            )
        root = self.canonical_root(project_instance_id)
        render = self.resolved_render(project_instance_id)
        try:
            if isinstance(self.adapter, ProjectAdapterCommand):
                observed = self.adapter.call(
                    "collaboration_templates",
                    {},
                    project_root=root,
                    project_render=render,
                )
            else:
                observed = self.adapter.call("collaboration_templates", {})
        except WorkspaceError as exc:
            raise ProjectError(
                exc.code,
                exc.message,
                exc.retryable,
            ) from exc
        templates = observed.get("templates") if isinstance(observed, dict) else None
        return self._validated_collaboration_templates(templates)

    @classmethod
    def collaboration_templates_from_render(
        cls, render: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Read a bounded path-free catalog from one immutable project render."""

        cls._validate_render(render)
        collaboration = render.get("collaboration")
        if not isinstance(collaboration, dict):
            raise ProjectError(
                "project.state-invalid", "collaboration snapshot differs"
            )
        registry = collaboration.get("registry_snapshot")
        registry_digest = collaboration.get("registry_snapshot_digest")
        if (
            not isinstance(registry, dict)
            or set(registry) != {"schema_version", "templates"}
            or registry.get("schema_version") != 1
            or not cls._is_sha256(registry_digest)
            or canonical_json_sha256(registry) != registry_digest
            or len(canonical_json_bytes(registry)) > 512 * 1024
        ):
            raise ProjectError(
                "project.state-invalid", "collaboration snapshot differs"
            )
        return cls._validated_collaboration_templates(registry.get("templates"))

    @classmethod
    def _validated_collaboration_templates(
        cls, templates: Any
    ) -> dict[str, Any]:
        if (
            not isinstance(templates, list)
            or not templates
            or len(templates) > 64
        ):
            raise ProjectError(
                "project.adapter-invalid", "collaboration template registry differs"
            )
        validated: list[dict[str, Any]] = []
        template_ids: set[str] = set()
        for value in templates:
            template = cls._validate_collaboration_template(value)
            if template["template_id"] in template_ids:
                raise ProjectError(
                    "project.adapter-invalid", "collaboration template identity differs"
                )
            template_ids.add(template["template_id"])
            validated.append(template)
        return {
            "templates": sorted(validated, key=lambda value: value["template_id"])
        }

    def validate_binding(
        self, project_instance_id: str, project_binding_digest: str
    ) -> None:
        """Require new Scenarios to use the current registered binding.

        Historical digests remain private audit evidence only. Existing
        Scenarios recover from their own pinned project snapshot (or the
        v0.1.6.1 Workspace evidence), so accepting an old digest here would
        create a new Scenario without a matching self-contained render.
        """

        with self._lock:
            state = self._read_state()
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            if project_binding_digest != item["record"]["project_binding_digest"]:
                raise ProjectError(
                    "project.binding-drift",
                    "project binding is not the current project snapshot",
                )

    def validate_existing_binding(
        self, project_instance_id: str, project_binding_digest: str
    ) -> None:
        """Validate a binding already pinned by a migrated Scenario.

        v0.1.6.1 Scenarios do not contain a self-contained project render. The
        registry's accepted set is retained only for validating those existing
        Scenario bindings; new Scenarios must use :meth:`validate_binding` and
        therefore cannot select historical configuration.
        """

        with self._lock:
            state = self._read_state()
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            if project_binding_digest not in item["accepted_binding_digests"]:
                raise ProjectError(
                    "project.binding-drift",
                    "existing Scenario project binding is not accepted",
                )

    def canonical_root(self, project_instance_id: str) -> Path:
        """Resolve one registered root for private Host-to-adapter dispatch only."""

        with self._lock:
            state = self._read_state()
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            root = Path(item["canonical_root"])
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise ProjectError(
                "project.path-invalid", "registered project root is unavailable"
            ) from exc
        if (
            resolved != root
            or not resolved.is_dir()
            or resolved.stat().st_uid != os.getuid()
            or canonical_json_sha256({"canonical_project_path": str(resolved)})
            != item["canonical_root_fingerprint"]
        ):
            raise ProjectError(
                "project.path-invalid", "registered project root identity differs"
            )
        return resolved

    def resolved_render(
        self,
        project_instance_id: str,
        project_binding_digest: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the Host-private deterministic render for adapter dispatch."""

        with self._lock:
            state = self._read_state()
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            if (
                project_binding_digest is None
                or project_binding_digest
                == item["record"]["project_binding_digest"]
            ):
                return copy.deepcopy(item["render"])
            return None

    @classmethod
    def _validate_collaboration_template(cls, value: Any) -> dict[str, Any]:
        fields = {
            "template_contract_version",
            "template_id",
            "display_name",
            "policy_id",
            "participant_ids",
            "assignments",
            "retry_profiles",
            "route_rules",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["template_contract_version"] != 1
            or not cls._is_namespaced(value["template_id"])
            or not isinstance(value["display_name"], str)
            or not value["display_name"].strip()
            or len(value["display_name"]) > 128
            or not cls._is_namespaced(value["policy_id"])
            or not isinstance(value["participant_ids"], list)
            or not value["participant_ids"]
            or len(value["participant_ids"]) > 64
            or any(not cls._is_opaque(item) for item in value["participant_ids"])
            or len(set(value["participant_ids"])) != len(value["participant_ids"])
            or not all(
                isinstance(value[field], list)
                for field in ("assignments", "retry_profiles", "route_rules")
            )
        ):
            raise ProjectError(
                "project.adapter-invalid", "collaboration template differs"
            )
        # Detailed policy semantics and current-generation resolution remain in
        # DeliveryCoordinator.  The project boundary only accepts a closed,
        # bounded, path-free data shape.
        for collection in ("assignments", "retry_profiles", "route_rules"):
            if len(value[collection]) > 256 or any(
                not isinstance(item, dict) for item in value[collection]
            ):
                raise ProjectError(
                    "project.adapter-invalid", "collaboration template values differ"
                )
        return copy.deepcopy(value)

    @staticmethod
    def _is_opaque(value: Any) -> bool:
        return isinstance(value, str) and OPAQUE_RE.fullmatch(value) is not None

    @staticmethod
    def _is_namespaced(value: Any) -> bool:
        return isinstance(value, str) and NAMESPACED_RE.fullmatch(value) is not None

    @staticmethod
    def _is_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and set(value).issubset(set("0123456789abcdef"))
        )

    @classmethod
    def _validate_render(cls, value: Any) -> dict[str, Any] | None:
        # A migrated v0.1.6.1 registration has no render until the next
        # successful reconciliation.  The generic adapter can still resolve
        # its legacy canonical declarations during that compatibility window.
        if value is None:
            return None
        if not isinstance(value, dict) or len(canonical_json_bytes(value)) > 8 * 1024 * 1024:
            raise ProjectError("project.state-invalid", "project render differs")
        digest = value.get("render_digest")
        material = {
            key: item
            for key, item in value.items()
            if key not in {"render_digest", "availability"}
        }
        if not cls._is_sha256(digest) or canonical_json_sha256(material) != digest:
            raise ProjectError("project.state-invalid", "project render digest differs")
        required = {
            "render_contract_version",
            "source",
            "project",
            "repo_manifest",
            "repo_manifest_digest",
            "gate",
            "collaboration",
            "availability",
            "render_digest",
        }
        if set(value) != required or value["render_contract_version"] != 1:
            raise ProjectError("project.state-invalid", "project render fields differ")
        availability = value["availability"]
        if (
            not isinstance(availability, dict)
            or set(availability)
            != {"status", "observations", "changes", "warnings", "fingerprint"}
            or not isinstance(availability["observations"], list)
            or len(availability["observations"]) > 256
            or canonical_json_sha256(
                {
                    key: item
                    for key, item in availability.items()
                    if key != "fingerprint"
                }
            )
            != availability["fingerprint"]
        ):
            raise ProjectError(
                "project.state-invalid", "project availability differs"
            )
        try:
            cls._validate_reconciliation(
                {
                    "status": availability["status"],
                    "binding_changed": False,
                    "availability_fingerprint": availability["fingerprint"],
                    "changes": availability["changes"],
                    "warnings": availability["warnings"],
                }
            )
            cls._validate_reconciliation(
                {
                    "status": availability["status"],
                    "binding_changed": False,
                    "availability_fingerprint": availability["fingerprint"],
                    "changes": availability["observations"],
                    "warnings": availability["warnings"],
                }
            )
        except ValueError as exc:
            raise ProjectError(
                "project.state-invalid", "project availability values differ"
            ) from exc
        return value

    @classmethod
    def _validate_record(cls, value: Any) -> dict[str, Any]:
        public_fields = {
            "project_key",
            "project_binding_digest",
            "product_contract_version",
            "workspace_adapter_id",
            "environment_adapter_id",
            "participant_driver_contract",
            "collaboration_policy_schema",
            "repo_manifest_digest",
            "adapter_capability_digest",
        }
        if (
            not isinstance(value, dict)
            or set(value)
            != public_fields | {"project_instance_id", "registration_revision"}
            or not isinstance(value["project_instance_id"], str)
            or not value["project_instance_id"]
            or not isinstance(value["registration_revision"], int)
            or isinstance(value["registration_revision"], bool)
            or value["registration_revision"] < 1
        ):
            raise ProjectError("project.state-invalid", "project record differs")
        cls._validate_observation(
            {"project": {field: value[field] for field in public_fields}}
        )
        return value

    @staticmethod
    def _validate_observation(value: Any) -> dict[str, Any]:
        fields = {
            "project_key",
            "project_binding_digest",
            "product_contract_version",
            "workspace_adapter_id",
            "environment_adapter_id",
            "participant_driver_contract",
            "collaboration_policy_schema",
            "repo_manifest_digest",
            "adapter_capability_digest",
        }
        if not isinstance(value, dict) or set(value) not in ({"project"}, {"project", "render"}):
            raise ProjectError("project.adapter-invalid", "project adapter reply differs")
        if "render" in value:
            render = ProjectRegistry._validate_render(value["render"])
        project = value["project"]
        if not isinstance(project, dict) or set(project) != fields:
            raise ProjectError("project.adapter-invalid", "project adapter record differs")
        sha_fields = {
            "project_binding_digest",
            "repo_manifest_digest",
            "adapter_capability_digest",
        }
        if (
            any(
                not ProjectRegistry._is_sha256(project[field])
                for field in sha_fields
            )
            or any(
                not isinstance(project[field], str) or not project[field]
                for field in {
                    "project_key",
                    "product_contract_version",
                    "workspace_adapter_id",
                    "environment_adapter_id",
                }
            )
            or any(
                not isinstance(project[field], int)
                or isinstance(project[field], bool)
                or project[field] < 1
                for field in {
                    "participant_driver_contract",
                    "collaboration_policy_schema",
                }
            )
        ):
            raise ProjectError("project.adapter-invalid", "project adapter values differ")
        if "render" in value:
            assert render is not None
            render_project = render.get("project")
            if (
                project["project_binding_digest"] != render["render_digest"]
                or project["repo_manifest_digest"]
                != render["repo_manifest_digest"]
                or not isinstance(render_project, dict)
                or any(
                    project[field] != render_project.get(field)
                    for field in {
                        "project_key",
                        "product_contract_version",
                        "workspace_adapter_id",
                        "environment_adapter_id",
                        "participant_driver_contract",
                        "collaboration_policy_schema",
                    }
                )
            ):
                raise ProjectError(
                    "project.adapter-invalid",
                    "project adapter render binding differs",
                )
        return copy.deepcopy(project)
