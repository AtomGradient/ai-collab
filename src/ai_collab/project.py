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
from typing import Any

from .protocol import canonical_json_bytes, canonical_json_sha256
from .workspace import ProjectAdapterCommand, WorkspaceError


PROJECT_REGISTRY_SCHEMA_VERSION = 1
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
                    != {"canonical_root", "canonical_root_fingerprint", "record"}
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
                    or set(request["result"]) != {"project"}
                ):
                    raise ValueError
                self._validate_record(request["result"]["project"])
        except (KeyError, TypeError, ValueError, ProjectError) as exc:
            raise ProjectError(
                "project.state-invalid", "project registry records differ"
            ) from exc
        return value

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
                "project registration validation failed",
                exc.retryable,
            ) from exc
        public = self._validate_observation(observed)
        root_fingerprint = canonical_json_sha256({"canonical_project_path": str(root)})

        with self._lock:
            state = self._read_state()
            previous = state["requests"].get(request_id)
            if previous is not None:
                if previous["request_digest"] != request_digest:
                    raise ProjectError("ipc.request-reused", "request identity was reused")
                return previous["operation_id"], copy.deepcopy(previous["result"])
            existing_id = next(
                (
                    project_id
                    for project_id, item in state["projects"].items()
                    if item["canonical_root_fingerprint"] == root_fingerprint
                    and item["canonical_root"] == str(root)
                ),
                None,
            )
            if existing_id is None:
                project_instance_id = f"project-{uuid.uuid4().hex}"
                registration_revision = 1
            else:
                project_instance_id = existing_id
                registration_revision = (
                    state["projects"][existing_id]["record"]["registration_revision"]
                    + 1
                )
            record = {
                "project_instance_id": project_instance_id,
                "registration_revision": registration_revision,
                **public,
            }
            state["projects"][project_instance_id] = {
                "canonical_root": str(root),
                "canonical_root_fingerprint": root_fingerprint,
                "record": record,
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

    def collaboration_templates(self, project_instance_id: str) -> dict[str, Any]:
        """Load project-provided team/policy data without exposing its root."""

        if self.adapter is None:
            raise ProjectError(
                "project.adapter-unavailable",
                "project adapter is not configured",
                retryable=True,
            )
        root = self.canonical_root(project_instance_id)
        try:
            if isinstance(self.adapter, ProjectAdapterCommand):
                observed = self.adapter.call(
                    "collaboration_templates", {}, project_root=root
                )
            else:
                observed = self.adapter.call("collaboration_templates", {})
        except WorkspaceError as exc:
            raise ProjectError(
                exc.code,
                "collaboration template discovery failed",
                exc.retryable,
            ) from exc
        templates = observed.get("templates") if isinstance(observed, dict) else None
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
            template = self._validate_collaboration_template(value)
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
        """Require Scenario creation to use the registered project binding."""

        with self._lock:
            state = self._read_state()
            item = state["projects"].get(project_instance_id)
            if item is None:
                raise ProjectError("project.not-found", "project is not registered")
            if item["record"]["project_binding_digest"] != project_binding_digest:
                raise ProjectError(
                    "project.binding-drift",
                    "project binding differs from its registered descriptor",
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
        if not isinstance(value, dict) or set(value) != {"project"}:
            raise ProjectError("project.adapter-invalid", "project adapter reply differs")
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
        return copy.deepcopy(project)
