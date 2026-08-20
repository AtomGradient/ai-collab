# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Project-neutral Workspace/Environment adapter orchestration.

The Host owns transaction boundaries and durable publication.  Project plugins
own repository and environment semantics behind a small JSON command protocol;
their private physical paths never enter public Harness artifacts.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .protocol import canonical_json_bytes, canonical_json_sha256


WORKSPACE_STATE_SCHEMA_VERSION = 1
ADAPTER_PROTOCOL_VERSION = 1
MAX_ADAPTER_REPLY_BYTES = 8 * 1024 * 1024
MAX_PROJECT_RENDER_ENV_BYTES = 512 * 1024
ADAPTER_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
ADAPTER_ENVIRONMENT_KEYS = {
    "HOME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "PYTHONDONTWRITEBYTECODE",
    "SSH_AUTH_SOCK",
}


@dataclass
class WorkspaceError(ValueError):
    code: str
    message: str
    retryable: bool = False
    mutation_state: str = "not_started"
    operation_id: str | None = None

    def __str__(self) -> str:
        return self.message


class ProjectAdapterCommand:
    """Versioned external project adapter with a deliberately narrow env."""

    def __init__(self, config_path: Path):
        supplied = Path(config_path).expanduser()
        if supplied.is_symlink():
            raise WorkspaceError("adapter.config-invalid", "adapter config is invalid")
        path = supplied.resolve(strict=True)
        if not path.is_file() or not self._owner_controlled(path):
            raise WorkspaceError("adapter.config-invalid", "adapter config is invalid")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError("adapter.config-invalid", "adapter config is invalid") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "adapter_id",
            "command",
            "working_directory",
        }:
            raise WorkspaceError("adapter.config-invalid", "adapter config fields differ")
        command = value["command"]
        if (
            value["schema_version"] != 1
            or not isinstance(value["adapter_id"], str)
            or not value["adapter_id"]
            or not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
        ):
            raise WorkspaceError("adapter.config-invalid", "adapter config values are invalid")
        base = path.parent
        work = self._resolve_relative(base, value["working_directory"], directory=True)
        executable = command[0]
        if "/" in executable:
            executable = str(self._resolve_relative(base, executable, directory=False))
        arguments = [executable]
        for item in command[1:]:
            if item.startswith("-"):
                arguments.append(item)
            else:
                arguments.append(str(self._resolve_relative(base, item, directory=False)))
        self.config_path = path
        self.adapter_id = value["adapter_id"]
        self.command = tuple(arguments)
        self.working_directory = work

    @staticmethod
    def _resolve_relative(base: Path, raw: Any, *, directory: bool) -> Path:
        if not isinstance(raw, str) or not raw or "\\" in raw:
            raise WorkspaceError("adapter.config-invalid", "adapter path is invalid")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or any(part in {"", ".."} for part in relative.parts):
            raise WorkspaceError("adapter.config-invalid", "adapter path escapes its config")
        lexical = base.joinpath(*relative.parts)
        if directory:
            candidate = lexical.resolve(strict=True)
            if (
                not candidate.is_relative_to(base)
                or not candidate.is_dir()
                or not ProjectAdapterCommand._owner_controlled(candidate)
            ):
                raise WorkspaceError("adapter.config-invalid", "adapter working directory is invalid")
            return candidate
        parent = lexical.parent.resolve(strict=True)
        if (
            not parent.is_relative_to(base)
            or not lexical.exists()
            or not lexical.is_file()
            or not ProjectAdapterCommand._owner_controlled(lexical)
        ):
            raise WorkspaceError("adapter.config-invalid", "adapter command path is invalid")
        # A venv interpreter is normally a symlink to an immutable system
        # interpreter.  Keep the config-owned lexical entry while requiring
        # every parent directory to remain inside the config root.
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
        project_render: Mapping[str, Any] | None = None,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        request = {
            "adapter_protocol_version": ADAPTER_PROTOCOL_VERSION,
            "adapter_id": self.adapter_id,
            "operation": operation,
            "payload": payload,
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in ADAPTER_ENVIRONMENT_KEYS
        }
        if project_root is not None:
            environment["AI_COLLAB_PROJECT_ROOT"] = str(
                Path(project_root).resolve(strict=True)
            )
        if project_render is not None:
            encoded_render = canonical_json_bytes(dict(project_render))
            if len(encoded_render) > MAX_PROJECT_RENDER_ENV_BYTES:
                raise WorkspaceError(
                    "project.render-invalid", "project render exceeds the adapter limit"
                )
            environment["AI_COLLAB_PROJECT_RENDER"] = encoded_render.decode("utf-8")
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
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(
                "adapter.unavailable",
                "project adapter is unavailable",
                retryable=True,
            ) from exc
        if completed.returncode != 0:
            raise WorkspaceError(
                "adapter.crashed",
                "project adapter process exited without a typed reply",
                retryable=True,
            )
        if completed.stderr:
            raise WorkspaceError(
                "adapter.protocol-invalid",
                "project adapter wrote unexpected diagnostic output",
            )
        if not completed.stdout or len(completed.stdout) > MAX_ADAPTER_REPLY_BYTES:
            raise WorkspaceError("adapter.invalid-reply", "project adapter reply is invalid")
        try:
            value = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("adapter.invalid-reply", "project adapter reply is invalid") from exc
        if not isinstance(value, dict) or set(value) != {
            "adapter_protocol_version",
            "adapter_id",
            "outcome",
            "result",
        }:
            raise WorkspaceError("adapter.invalid-reply", "project adapter reply fields differ")
        if (
            value["adapter_protocol_version"] != ADAPTER_PROTOCOL_VERSION
            or value["adapter_id"] != self.adapter_id
            or value["outcome"] not in {"completed", "failed"}
            or not isinstance(value["result"], dict)
        ):
            raise WorkspaceError("adapter.invalid-reply", "project adapter rejected the operation")
        if value["outcome"] == "failed":
            error = value["result"].get("error")
            if (
                not isinstance(error, dict)
                or set(error)
                != {"code", "message", "retryable", "mutation_state"}
                or not isinstance(error["code"], str)
                or ADAPTER_ERROR_CODE_RE.fullmatch(error["code"]) is None
                or not isinstance(error["message"], str)
                or not error["message"]
                or len(error["message"]) > 512
                or not isinstance(error["retryable"], bool)
                or error["mutation_state"] not in {
                    "not_started",
                    "started",
                    "committed",
                    "unknown",
                }
            ):
                raise WorkspaceError(
                    "adapter.invalid-reply", "project adapter error reply differs"
                )
            _reject_public_absolute_paths(error)
            raise WorkspaceError(
                error["code"],
                error["message"],
                retryable=error["retryable"],
                mutation_state=error["mutation_state"],
            )
        _reject_public_absolute_paths(value["result"])
        return value["result"]


class WorkspaceCoordinator:
    """Durable plan/provision/status coordinator for one configured project plugin."""

    def __init__(
        self,
        state_root: Path,
        adapter: ProjectAdapterCommand,
        *,
        project_root_resolver: Callable[[str], Path] | None = None,
        project_render_resolver: Callable[
            [str, str | None, str | None], Mapping[str, Any] | None
        ]
        | None = None,
    ):
        self.state_root = Path(state_root).resolve()
        self.state_path = self.state_root / "workspace-execution.json"
        self.adapter = adapter
        self.project_root_resolver = project_root_resolver
        self.project_render_resolver = project_render_resolver
        self._lock = threading.RLock()
        with self._lock:
            if not self.state_path.exists():
                self._write_state(self._empty_state())
            self._read_state()

    def _call_adapter(
        self,
        project_instance_id: str,
        operation: str,
        payload: Mapping[str, Any],
        *,
        project_binding_digest: str | None = None,
    ) -> dict[str, Any]:
        if self.project_root_resolver is None:
            return self.adapter.call(operation, payload)
        if project_binding_digest is None:
            plan = payload.get("plan")
            if isinstance(plan, Mapping):
                candidate = plan.get("project_descriptor_digest")
                if isinstance(candidate, str):
                    project_binding_digest = candidate
        scenario_id: str | None = None
        candidates = [payload]
        for field in ("plan", "receipt"):
            nested = payload.get(field)
            if isinstance(nested, Mapping):
                candidates.append(nested)
        for candidate in candidates:
            scenario = candidate.get("scenario")
            if isinstance(scenario, Mapping) and isinstance(
                scenario.get("scenario_id"), str
            ):
                scenario_id = scenario["scenario_id"]
                break
        return self.adapter.call(
            operation,
            payload,
            project_root=self.project_root_resolver(project_instance_id),
            project_render=(
                self.project_render_resolver(
                    project_instance_id, scenario_id, project_binding_digest
                )
                if self.project_render_resolver is not None
                else None
            ),
        )

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_STATE_SCHEMA_VERSION,
            "state_revision": 0,
            "bindings": {},
            "history": {},
            "requests": {},
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise WorkspaceError("workspace.state-invalid", "workspace state is unavailable")
        details = self.state_path.stat()
        if stat.S_IMODE(details.st_mode) != 0o600 or details.st_uid != os.getuid():
            raise WorkspaceError("workspace.state-invalid", "workspace state permissions differ")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError("workspace.state-invalid", "workspace state is invalid") from exc
        legacy_fields = {
            "schema_version",
            "state_revision",
            "bindings",
            "requests",
        }
        if isinstance(value, dict) and set(value) == legacy_fields:
            value["history"] = {}
        if not isinstance(value, dict) or set(value) != legacy_fields | {"history"}:
            raise WorkspaceError("workspace.state-invalid", "workspace state schema differs")
        if (
            value["schema_version"] != WORKSPACE_STATE_SCHEMA_VERSION
            or not isinstance(value["state_revision"], int)
            or not isinstance(value["bindings"], dict)
            or not isinstance(value["history"], dict)
            or not isinstance(value["requests"], dict)
        ):
            raise WorkspaceError("workspace.state-invalid", "workspace state values differ")
        return value

    def _write_state(self, value: Mapping[str, Any]) -> None:
        temporary = self.state_root / f".workspace-state.{os.getpid()}.{secrets.token_hex(6)}.tmp"
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

    def start_host(
        self, workspace_root: Path | Callable[[str], Path]
    ) -> None:
        """Resolve only exact owned publish outcomes left pending by a crash."""

        workspace_path = (
            workspace_root
            if callable(workspace_root)
            else lambda binding_id: workspace_root / binding_id
        )

        with self._lock:
            state = self._read_state()
            changed = False
            for binding in state["bindings"].values():
                if binding["state"] != "provisioning":
                    continue
                bundle = workspace_path(binding["workspace_id"]) / "bundle"
                marker = bundle / ".ai-collab-harness-binding.json"
                if marker.is_symlink() or not marker.is_file():
                    self._fail_pending_binding(
                        state, binding, "workspace.publish-outcome-unknown"
                    )
                    changed = True
                    continue
                try:
                    result = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._fail_pending_binding(
                        state, binding, "workspace.publish-outcome-unknown"
                    )
                    changed = True
                    continue
                if canonical_json_sha256(result) != binding["pending_result_digest"]:
                    self._fail_pending_binding(
                        state, binding, "workspace.publish-evidence-mismatch"
                    )
                    changed = True
                    continue
                response = {"workspace": {"state": "ready", **copy.deepcopy(result)}}
                binding.update(
                    {
                        "state": "ready",
                        "journal": result["journal"],
                        "receipt": result["receipt"],
                        "result": copy.deepcopy(response),
                        "error_code": None,
                        "pending_result_digest": None,
                    }
                )
                request = state["requests"].get(binding["provision_request_id"])
                if request is not None:
                    request["status"] = "completed"
                    request["result"] = copy.deepcopy(response)
                changed = True
            if changed:
                state["state_revision"] += 1
                self._write_state(state)
        self._resume_high_risk_operations(workspace_path)

    def plan(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        workspace_id: str,
        project_binding_digest: str,
        requested_component_ids: list[str],
        project_payload: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            key = _binding_key(project_instance_id, scenario_id)
            existing = state["bindings"].get(key)
            if existing is not None:
                if (
                    existing["state"] in {"planned", "provision_failed"}
                    and existing.get("project_binding_digest")
                    == project_binding_digest
                    and existing.get("requested_component_ids_input")
                    == requested_component_ids
                    and existing.get("project_payload_input") == project_payload
                    and existing["scenario_generation"] == scenario_generation
                    and existing["scenario_state_revision"]
                    == scenario_state_revision
                ):
                    response = copy.deepcopy(existing["result"])
                    operation_id = existing["plan"]["operation_id"]
                    self._record_request(
                        state,
                        request_id,
                        request_digest,
                        operation_id,
                        response,
                    )
                    state["state_revision"] += 1
                    self._write_state(state)
                    return operation_id, response
                raise WorkspaceError(
                    "workspace.already-planned", "workspace already has a plan"
                )
        operation_id = f"wsop-{uuid.uuid4().hex}"
        result = self._call_adapter(
            project_instance_id,
            "plan",
            {
                "operation_id": operation_id,
                "scenario": {
                    "scenario_id": scenario_id,
                    "scenario_generation": scenario_generation,
                },
                "scenario_state_revision": scenario_state_revision,
                "workspace_id": workspace_id,
                "requested_component_ids": requested_component_ids,
                "project_payload": project_payload,
            },
            project_binding_digest=project_binding_digest,
        )
        if set(result) != {"descriptors", "plan"}:
            raise WorkspaceError("adapter.invalid-reply", "plan result fields differ")
        plan = result["plan"]
        descriptors = result["descriptors"]
        if (
            not isinstance(plan, dict)
            or not isinstance(descriptors, list)
            or plan.get("operation_id") != operation_id
            or plan.get("scenario")
            != {"scenario_id": scenario_id, "scenario_generation": scenario_generation}
            or plan.get("project_descriptor_digest") != project_binding_digest
        ):
            raise WorkspaceError("adapter.invalid-reply", "plan identity differs")
        response = {
            "workspace": {
                "state": "planned",
                "workspace_id": workspace_id,
                "project_binding_digest": project_binding_digest,
                "plan_digest": canonical_json_sha256(plan),
                "descriptors": descriptors,
                "plan": plan,
            }
        }
        with self._lock:
            state = self._read_state()
            key = _binding_key(project_instance_id, scenario_id)
            if key in state["bindings"]:
                raise WorkspaceError("workspace.concurrent-change", "workspace changed while planning")
            state["bindings"][key] = {
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
                "workspace_id": workspace_id,
                "project_binding_digest": project_binding_digest,
                "requested_component_ids_input": copy.deepcopy(
                    requested_component_ids
                ),
                "project_payload_input": copy.deepcopy(project_payload),
                "state": "planned",
                "descriptors": copy.deepcopy(descriptors),
                "plan": copy.deepcopy(plan),
                "journal": None,
                "receipt": None,
                "last_observation": None,
                "result": copy.deepcopy(response),
                "error_code": None,
                "pending_result_digest": None,
            }
            self._record_request(state, request_id, request_digest, operation_id, response)
            state["state_revision"] += 1
            self._write_state(state)
        return operation_id, response

    def summary(
        self, project_instance_id: str, scenario_id: str
    ) -> dict[str, Any] | None:
        """Return the persisted public Workspace view without private paths."""

        with self._lock:
            state = self._read_state()
            binding = state["bindings"].get(
                _binding_key(project_instance_id, scenario_id)
            )
            if binding is None:
                return None
            plan = binding.get("plan")
            receipt = binding.get("receipt")
            observation = binding.get("last_observation")
            return {
                "state": binding["state"],
                "workspace_id": binding["workspace_id"],
                "project_binding_digest": binding.get("project_binding_digest")
                or (plan.get("project_descriptor_digest") if isinstance(plan, dict) else None),
                "plan_digest": (
                    canonical_json_sha256(plan) if isinstance(plan, dict) else None
                ),
                "receipt_digest": (
                    canonical_json_sha256(receipt)
                    if isinstance(receipt, dict)
                    else None
                ),
                "receipt": copy.deepcopy(receipt),
                "last_observation": copy.deepcopy(observation),
                "error_code": binding.get("error_code"),
            }

    def provision(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        plan_digest: str,
        workspace_path: Path,
    ) -> tuple[str, dict[str, Any]]:
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding["state"] not in {
                "planned",
                "provision_failed",
            }:
                raise WorkspaceError("workspace.not-planned", "workspace has no provisionable plan")
            self._check_binding_fence(
                binding, scenario_generation, scenario_state_revision, plan_digest
            )
            operation_id = binding["plan"]["operation_id"]
            staging_name = f".stage-{operation_id}"
            staging_path = workspace_path / staging_name
            bundle_path = workspace_path / "bundle"
            if staging_path.exists() or staging_path.is_symlink() or bundle_path.exists() or bundle_path.is_symlink():
                raise WorkspaceError("workspace.path-conflict", "workspace publish path is occupied")
            binding["state"] = "provisioning"
            binding["provision_request_id"] = request_id
            binding["pending_result_digest"] = None
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "status": "pending",
                "result": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
            plan = copy.deepcopy(binding["plan"])
            descriptors = copy.deepcopy(binding["descriptors"])
        try:
            external = self._call_adapter(
                project_instance_id,
                "provision",
                {
                    "workspace_id": binding["workspace_id"],
                    "staging_path": str(staging_path),
                    "plan": plan,
                    "descriptors": descriptors,
                },
            )
            if set(external) != {"journal", "receipt", "review_snapshot"}:
                raise WorkspaceError("adapter.invalid-reply", "provision result fields differ")
            self._validate_ready_result(binding, external)
            self._ensure_private_stage(staging_path)
            _write_private_json(staging_path / ".ai-collab-harness-binding.json", external)
            result_digest = canonical_json_sha256(external)
            with self._lock:
                state = self._read_state()
                current = state["bindings"][key]
                if current["state"] != "provisioning":
                    raise WorkspaceError("workspace.concurrent-change", "workspace changed during provision")
                current["pending_result_digest"] = result_digest
                state["state_revision"] += 1
                self._write_state(state)
            os.replace(staging_path, bundle_path)
            directory = os.open(workspace_path, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except WorkspaceError as exc:
            self._discard_owned_stage(staging_path, workspace_path)
            self._record_provision_failure(key, request_id, exc.code)
            raise WorkspaceError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc
        except OSError as exc:
            self._discard_owned_stage(staging_path, workspace_path)
            self._record_provision_failure(key, request_id, "workspace.provision-failed")
            raise WorkspaceError(
                "workspace.provision-failed",
                "workspace provisioning failed",
                retryable=True,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc
        response = {"workspace": {"state": "ready", **copy.deepcopy(external)}}
        try:
            with self._lock:
                state = self._read_state()
                current = state["bindings"][key]
                current.update(
                    {
                        "state": "ready",
                        "journal": copy.deepcopy(external["journal"]),
                        "receipt": copy.deepcopy(external["receipt"]),
                        "result": copy.deepcopy(response),
                        "error_code": None,
                        "pending_result_digest": None,
                    }
                )
                request = state["requests"][request_id]
                request["status"] = "completed"
                request["result"] = copy.deepcopy(response)
                state["state_revision"] += 1
                self._write_state(state)
        except OSError as exc:
            raise WorkspaceError(
                "workspace.publish-outcome-unknown",
                "workspace publication outcome is unknown",
                retryable=False,
                mutation_state="unknown",
                operation_id=operation_id,
            ) from exc
        return operation_id, response

    def status(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        receipt_digest: str,
        workspace_path: Path,
    ) -> tuple[str, dict[str, Any]]:
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding["state"] != "ready":
                raise WorkspaceError("workspace.not-ready", "workspace is not ready")
            if (
                binding["scenario_generation"] != scenario_generation
                or canonical_json_sha256(binding["receipt"]) != receipt_digest
            ):
                raise WorkspaceError("workspace.stale-fence", "workspace status fence differs")
            operation_id = f"wsop-{uuid.uuid4().hex}"
            plan = copy.deepcopy(binding["plan"])
            receipt = copy.deepcopy(binding["receipt"])
        external = self._call_adapter(
            project_instance_id,
            "status",
            {
                "operation_id": operation_id,
                "bundle_path": str(workspace_path / "bundle"),
                "plan": plan,
                "receipt": receipt,
            },
        )
        if set(external) != {"journal", "observation"}:
            raise WorkspaceError("adapter.invalid-reply", "status result fields differ")
        journal = external["journal"]
        observation = external["observation"]
        if (
            not isinstance(journal, dict)
            or not isinstance(observation, dict)
            or journal.get("operation_id") != operation_id
            or observation.get("operation_id") != operation_id
            or journal.get("plan_digest") != canonical_json_sha256(plan)
            or observation.get("receipt_digest") != canonical_json_sha256(receipt)
            or observation.get("journal_digest") != canonical_json_sha256(journal)
        ):
            raise WorkspaceError("adapter.invalid-reply", "status provenance differs")
        response = {"workspace": {"state": observation.get("state"), **copy.deepcopy(external)}}
        with self._lock:
            state = self._read_state()
            binding = state["bindings"][key]
            binding["last_observation"] = copy.deepcopy(observation)
            self._record_request(state, request_id, request_digest, operation_id, response)
            state["state_revision"] += 1
            self._write_state(state)
        return operation_id, response

    def high_risk_context(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        operation: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Observe exact Workspace/WIP state without mutating coordinator state."""

        if operation not in {
            "scenario.repair",
            "scenario.destroy",
            "scenario.force-destroy",
        }:
            raise WorkspaceError("workspace.operation-invalid", "workspace operation differs")
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            binding = state["bindings"].get(key)
            if binding is None or binding["state"] != "ready":
                raise WorkspaceError("workspace.not-ready", "workspace is not ready")
            if binding["scenario_generation"] != scenario_generation:
                raise WorkspaceError("workspace.stale-fence", "workspace fence differs")
            plan = copy.deepcopy(binding["plan"])
            receipt = copy.deepcopy(binding["receipt"])
            workspace_id = binding["workspace_id"]
        operation_id = f"ws-preview-{uuid.uuid4().hex}"
        external = self._call_adapter(
            project_instance_id,
            "status",
            {
                "operation_id": operation_id,
                "bundle_path": str(workspace_path / "bundle"),
                "plan": plan,
                "receipt": receipt,
            },
        )
        if set(external) != {"journal", "observation"}:
            raise WorkspaceError("adapter.invalid-reply", "workspace preview differs")
        journal = external["journal"]
        observation = external["observation"]
        if (
            not isinstance(journal, dict)
            or not isinstance(observation, dict)
            or observation.get("operation_id") != operation_id
            or observation.get("journal_digest") != canonical_json_sha256(journal)
            or observation.get("receipt_digest") != canonical_json_sha256(receipt)
        ):
            raise WorkspaceError("adapter.invalid-reply", "workspace preview provenance differs")
        preview = {
            "workspace_id": workspace_id,
            "workspace_binding_digest": receipt["workspace_binding_digest"],
            "receipt_digest": canonical_json_sha256(receipt),
            "state": observation.get("state"),
            "drift_codes": copy.deepcopy(observation.get("drift_codes")),
            "wip_summary_digest": observation.get("wip_summary_digest"),
            "canonical_source_wip_mutation": False,
        }
        # The probe re-verifies the observation the owner is about to confirm.
        # Binding that to the literal "aligned" made a forced teardown of a
        # drifted workspace unprovable by construction, so the expectation is
        # the observed state itself for the forced path. The WIP digest and the
        # receipt digest remain bound to the same observation either way, so
        # nothing about the WIP fence is weakened.
        expected_state = (
            observation.get("state")
            if operation == "scenario.force-destroy"
            else "aligned"
        )
        subject = {
            "subject_kind": "project-storage",
            "bundle_path": str(workspace_path / "bundle"),
            "plan": plan,
            "receipt": receipt,
            "expected_wip_summary_digest": observation.get("wip_summary_digest"),
            "expected_workspace_state": expected_state,
        }
        return preview, subject

    def repair(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_wip_summary_digest: str,
    ) -> tuple[str, dict[str, Any]]:
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding["state"] != "ready":
                raise WorkspaceError("workspace.not-ready", "workspace is not repairable")
            if binding["scenario_generation"] != scenario_generation:
                raise WorkspaceError("workspace.stale-fence", "workspace fence differs")
            operation_id = f"wsop-{uuid.uuid4().hex}"
            plan = copy.deepcopy(binding["plan"])
            receipt = copy.deepcopy(binding["receipt"])
            binding["state"] = "repairing"
            binding["pending_request_id"] = request_id
            binding["pending_operation_id"] = operation_id
            binding["pending_expected_wip_summary_digest"] = (
                expected_wip_summary_digest
            )
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "status": "pending",
                "result": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
        try:
            external = self._call_adapter(
                project_instance_id,
                "repair",
                {
                    "operation_id": operation_id,
                    "bundle_path": str(workspace_path / "bundle"),
                    "plan": plan,
                    "receipt": receipt,
                    "expected_wip_summary_digest": expected_wip_summary_digest,
                },
            )
            if set(external) != {"journal", "receipt", "observation", "review_snapshot"}:
                raise WorkspaceError("adapter.invalid-reply", "repair result fields differ")
            journal = external["journal"]
            repaired = external["receipt"]
            observation = external["observation"]
            if (
                journal.get("operation_id") != operation_id
                or journal.get("operation_kind") != "repair"
                or repaired.get("base_receipt_digest")
                != canonical_json_sha256(receipt)
                or repaired.get("journal_digest") != canonical_json_sha256(journal)
                or repaired.get("workspace_id") != binding["workspace_id"]
                or repaired.get("state") != "ready"
                or observation.get("receipt_digest")
                != canonical_json_sha256(repaired)
                or observation.get("wip_summary_digest")
                != expected_wip_summary_digest
                or observation.get("state") != "aligned"
            ):
                raise WorkspaceError("adapter.invalid-reply", "repair provenance differs")
        except (WorkspaceError, OSError, KeyError, TypeError) as exc:
            with self._lock:
                state = self._read_state()
                current = state["bindings"].get(key)
                if current is not None:
                    current["state"] = "repair_failed"
                    current["error_code"] = "workspace.repair-failed"
                state["requests"][request_id]["status"] = "failed"
                state["state_revision"] += 1
                self._write_state(state)
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError("workspace.repair-failed", "workspace repair failed") from exc
        response = {"workspace": {"state": "ready", **copy.deepcopy(external)}}
        with self._lock:
            state = self._read_state()
            current = state["bindings"][key]
            current.update(
                {
                    "state": "ready",
                    "journal": copy.deepcopy(journal),
                    "receipt": copy.deepcopy(repaired),
                    "last_observation": copy.deepcopy(observation),
                    "result": copy.deepcopy(response),
                    "error_code": None,
                }
            )
            self._clear_pending_high_risk(current)
            state["requests"][request_id].update(
                {"status": "completed", "result": copy.deepcopy(response)}
            )
            state["state_revision"] += 1
            self._write_state(state)
        return operation_id, response

    def destroy(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_wip_summary_digest: str,
        force: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding["state"] != "ready":
                raise WorkspaceError("workspace.not-ready", "workspace is not destroyable")
            if binding["scenario_generation"] != scenario_generation:
                raise WorkspaceError("workspace.stale-fence", "workspace fence differs")
            operation_id = f"wsop-{uuid.uuid4().hex}"
            plan = copy.deepcopy(binding["plan"])
            receipt = copy.deepcopy(binding["receipt"])
            binding["state"] = "destroying"
            binding["pending_request_id"] = request_id
            binding["pending_operation_id"] = operation_id
            binding["pending_expected_wip_summary_digest"] = (
                expected_wip_summary_digest
            )
            # Persisted so a crash-recovery replay reissues the identical
            # request. A replay that silently dropped force would re-impose the
            # alignment gate on an operation the owner already confirmed.
            binding["pending_force"] = bool(force)
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "status": "pending",
                "result": None,
            }
            state["state_revision"] += 1
            self._write_state(state)
        try:
            external = self._call_adapter(
                project_instance_id,
                "destroy",
                {
                    "operation_id": operation_id,
                    "bundle_path": str(workspace_path / "bundle"),
                    "plan": plan,
                    "receipt": receipt,
                    "expected_wip_summary_digest": expected_wip_summary_digest,
                    "force": bool(force),
                },
            )
            if set(external) != {"journal", "observation"}:
                raise WorkspaceError("adapter.invalid-reply", "destroy result fields differ")
            journal = external["journal"]
            observation = external["observation"]
            if (
                journal.get("operation_id") != operation_id
                or journal.get("operation_kind") != "destroy"
                or observation.get("journal_digest") != canonical_json_sha256(journal)
                or observation.get("receipt_digest") != canonical_json_sha256(receipt)
                or observation.get("wip_summary_digest")
                != expected_wip_summary_digest
                or observation.get("state") != "missing"
                or (workspace_path / "bundle").exists()
                or (workspace_path / "bundle").is_symlink()
            ):
                raise WorkspaceError("adapter.invalid-reply", "destroy provenance differs")
        except (WorkspaceError, OSError, KeyError, TypeError) as exc:
            with self._lock:
                state = self._read_state()
                current = state["bindings"].get(key)
                if current is not None:
                    current["state"] = "destroy_failed"
                    current["error_code"] = "workspace.destroy-failed"
                state["requests"][request_id]["status"] = "failed"
                state["state_revision"] += 1
                self._write_state(state)
            if isinstance(exc, WorkspaceError):
                raise
            raise WorkspaceError("workspace.destroy-failed", "workspace destroy failed") from exc
        response = {"workspace": {"state": "missing", **copy.deepcopy(external)}}
        with self._lock:
            state = self._read_state()
            current = state["bindings"].pop(key)
            self._clear_pending_high_risk(current)
            state["history"][key] = {
                **current,
                "state": "destroyed",
                "destroy_journal": copy.deepcopy(journal),
                "destroy_observation": copy.deepcopy(observation),
            }
            state["requests"][request_id].update(
                {"status": "completed", "result": copy.deepcopy(response)}
            )
            state["state_revision"] += 1
            self._write_state(state)
        return operation_id, response

    def _resume_high_risk_operations(
        self, workspace_path: Callable[[str], Path]
    ) -> None:
        """Replay exact pending adapter calls and publish their durable outcome."""

        with self._lock:
            state = self._read_state()
            pending = [
                (key, copy.deepcopy(binding))
                for key, binding in state["bindings"].items()
                if binding["state"] in {"repairing", "destroying"}
            ]
        for key, binding in pending:
            kind = binding["state"]
            request_id = binding.get("pending_request_id")
            operation_id = binding.get("pending_operation_id")
            expected_wip = binding.get("pending_expected_wip_summary_digest")
            if (
                not isinstance(request_id, str)
                or not isinstance(operation_id, str)
                or not isinstance(expected_wip, str)
            ):
                self._fail_high_risk_recovery(
                    key, request_id, f"workspace.{kind}-outcome-unknown"
                )
                continue
            current_workspace_path = workspace_path(binding["workspace_id"])
            resumed_payload = {
                "operation_id": operation_id,
                "bundle_path": str(current_workspace_path / "bundle"),
                "plan": copy.deepcopy(binding["plan"]),
                "receipt": copy.deepcopy(binding["receipt"]),
                "expected_wip_summary_digest": expected_wip,
            }
            if kind != "repairing":
                # Reissue the destroy exactly as it was first sent. repair has a
                # different payload field set and must not gain this key.
                resumed_payload["force"] = bool(binding.get("pending_force", False))
            try:
                external = self._call_adapter(
                    binding["project_instance_id"],
                    "repair" if kind == "repairing" else "destroy",
                    resumed_payload,
                )
                if kind == "repairing":
                    self._validate_repair_result(
                        binding, operation_id, expected_wip, external
                    )
                    response = {
                        "workspace": {"state": "ready", **copy.deepcopy(external)}
                    }
                    with self._lock:
                        state = self._read_state()
                        current = state["bindings"][key]
                        if (
                            current["state"] != "repairing"
                            or current.get("pending_operation_id") != operation_id
                        ):
                            raise WorkspaceError(
                                "workspace.concurrent-change",
                                "workspace changed during repair recovery",
                            )
                        current.update(
                            {
                                "state": "ready",
                                "journal": copy.deepcopy(external["journal"]),
                                "receipt": copy.deepcopy(external["receipt"]),
                                "last_observation": copy.deepcopy(
                                    external["observation"]
                                ),
                                "result": copy.deepcopy(response),
                                "error_code": None,
                            }
                        )
                        self._clear_pending_high_risk(current)
                        state["requests"][request_id].update(
                            {"status": "completed", "result": copy.deepcopy(response)}
                        )
                        state["state_revision"] += 1
                        self._write_state(state)
                else:
                    self._validate_destroy_result(
                        binding,
                        operation_id,
                        expected_wip,
                        external,
                        current_workspace_path,
                    )
                    response = {
                        "workspace": {"state": "missing", **copy.deepcopy(external)}
                    }
                    with self._lock:
                        state = self._read_state()
                        current = state["bindings"].get(key)
                        if (
                            current is None
                            or current["state"] != "destroying"
                            or current.get("pending_operation_id") != operation_id
                        ):
                            raise WorkspaceError(
                                "workspace.concurrent-change",
                                "workspace changed during destroy recovery",
                            )
                        del state["bindings"][key]
                        self._clear_pending_high_risk(current)
                        state["history"][key] = {
                            **current,
                            "state": "destroyed",
                            "destroy_journal": copy.deepcopy(external["journal"]),
                            "destroy_observation": copy.deepcopy(
                                external["observation"]
                            ),
                        }
                        state["requests"][request_id].update(
                            {"status": "completed", "result": copy.deepcopy(response)}
                        )
                        state["state_revision"] += 1
                        self._write_state(state)
            except (WorkspaceError, OSError, KeyError, TypeError):
                self._fail_high_risk_recovery(
                    key,
                    request_id,
                    (
                        "workspace.repair-outcome-unknown"
                        if kind == "repairing"
                        else "workspace.destroy-outcome-unknown"
                    ),
                )

    def _fail_high_risk_recovery(
        self, key: str, request_id: Any, error_code: str
    ) -> None:
        with self._lock:
            state = self._read_state()
            current = state["bindings"].get(key)
            if current is not None and current["state"] in {
                "repairing",
                "destroying",
            }:
                current["state"] = (
                    "repair_failed"
                    if current["state"] == "repairing"
                    else "destroy_failed"
                )
                current["error_code"] = error_code
            if isinstance(request_id, str) and request_id in state["requests"]:
                state["requests"][request_id]["status"] = "failed"
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _clear_pending_high_risk(binding: dict[str, Any]) -> None:
        for field in (
            "pending_request_id",
            "pending_operation_id",
            "pending_expected_wip_summary_digest",
        ):
            binding.pop(field, None)

    @staticmethod
    def _validate_repair_result(
        binding: Mapping[str, Any],
        operation_id: str,
        expected_wip_summary_digest: str,
        external: Mapping[str, Any],
    ) -> None:
        if set(external) != {"journal", "receipt", "observation", "review_snapshot"}:
            raise WorkspaceError("adapter.invalid-reply", "repair result fields differ")
        journal = external["journal"]
        repaired = external["receipt"]
        observation = external["observation"]
        if (
            journal.get("operation_id") != operation_id
            or journal.get("operation_kind") != "repair"
            or repaired.get("base_receipt_digest")
            != canonical_json_sha256(binding["receipt"])
            or repaired.get("journal_digest") != canonical_json_sha256(journal)
            or repaired.get("workspace_id") != binding["workspace_id"]
            or repaired.get("state") != "ready"
            or observation.get("receipt_digest")
            != canonical_json_sha256(repaired)
            or observation.get("wip_summary_digest")
            != expected_wip_summary_digest
            or observation.get("state") != "aligned"
        ):
            raise WorkspaceError("adapter.invalid-reply", "repair provenance differs")

    @staticmethod
    def _validate_destroy_result(
        binding: Mapping[str, Any],
        operation_id: str,
        expected_wip_summary_digest: str,
        external: Mapping[str, Any],
        workspace_path: Path,
    ) -> None:
        if set(external) != {"journal", "observation"}:
            raise WorkspaceError("adapter.invalid-reply", "destroy result fields differ")
        journal = external["journal"]
        observation = external["observation"]
        if (
            journal.get("operation_id") != operation_id
            or journal.get("operation_kind") != "destroy"
            or observation.get("journal_digest") != canonical_json_sha256(journal)
            or observation.get("receipt_digest")
            != canonical_json_sha256(binding["receipt"])
            or observation.get("wip_summary_digest")
            != expected_wip_summary_digest
            or observation.get("state") != "missing"
            or (workspace_path / "bundle").exists()
            or (workspace_path / "bundle").is_symlink()
        ):
            raise WorkspaceError("adapter.invalid-reply", "destroy provenance differs")

    def is_ready(self, project_instance_id: str, scenario_id: str) -> bool:
        with self._lock:
            state = self._read_state()
            binding = state["bindings"].get(_binding_key(project_instance_id, scenario_id))
            return bool(binding is not None and binding["state"] == "ready")

    def completed_request(
        self, request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return only a durably completed exact workspace request."""

        with self._lock:
            state = self._read_state()
            request = state["requests"].get(request_id)
            if request is None:
                return None
            if request["request_digest"] != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            if request["status"] != "completed":
                return None
            return request["operation_id"], copy.deepcopy(request["result"])

    @staticmethod
    def _check_binding_fence(
        binding: Mapping[str, Any],
        scenario_generation: int,
        scenario_state_revision: int,
        plan_digest: str,
    ) -> None:
        if (
            binding["scenario_generation"] != scenario_generation
            or binding["scenario_state_revision"] != scenario_state_revision
            or canonical_json_sha256(binding["plan"]) != plan_digest
        ):
            raise WorkspaceError("workspace.stale-fence", "workspace provision fence differs")

    @staticmethod
    def _validate_ready_result(binding: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        journal = result["journal"]
        receipt = result["receipt"]
        if not isinstance(journal, dict) or not isinstance(receipt, dict):
            raise WorkspaceError("adapter.invalid-reply", "ready artifacts are invalid")
        if (
            journal.get("operation_id") != binding["plan"]["operation_id"]
            or journal.get("plan_digest") != canonical_json_sha256(binding["plan"])
            or receipt.get("operation_id") != journal.get("operation_id")
            or receipt.get("plan_digest") != journal.get("plan_digest")
            or receipt.get("journal_digest") != canonical_json_sha256(journal)
            or receipt.get("workspace_id") != binding["workspace_id"]
            or receipt.get("scenario") != binding["plan"].get("scenario")
            or receipt.get("state") != "ready"
            or receipt.get("residual_owned_resources") != 0
        ):
            raise WorkspaceError("adapter.invalid-reply", "ready artifact provenance differs")

    @staticmethod
    def _ensure_private_stage(path: Path) -> None:
        if path.is_symlink() or not path.is_dir():
            raise WorkspaceError("workspace.stage-invalid", "adapter staging path is invalid")
        details = path.stat()
        if details.st_uid != os.getuid():
            raise WorkspaceError("workspace.stage-invalid", "adapter staging owner differs")
        os.chmod(path, 0o700)

    @staticmethod
    def _discard_owned_stage(path: Path, workspace_path: Path) -> None:
        """Remove only the Host-owned failed staging tree; never follow links."""

        if path.parent != workspace_path or not path.name.startswith(".stage-wsop-"):
            return
        if path.is_symlink() or not path.exists():
            return
        try:
            details = path.stat()
            if path.is_dir() and details.st_uid == os.getuid():
                shutil.rmtree(path)
        except OSError:
            return

    def _record_provision_failure(
        self, key: str, request_id: str, error_code: str
    ) -> None:
        with self._lock:
            state = self._read_state()
            binding = state["bindings"].get(key)
            if binding is not None and binding["state"] == "provisioning":
                self._fail_pending_binding(
                    state, binding, error_code
                )
            request = state["requests"].get(request_id)
            if request is not None:
                request["status"] = "failed"
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _fail_pending_binding(
        state: dict[str, Any], binding: dict[str, Any], error_code: str
    ) -> None:
        journal = {
            "journal_contract_version": 1,
            "operation_id": binding["plan"]["operation_id"],
            "operation_kind": "provision",
            "plan_digest": canonical_json_sha256(binding["plan"]),
            "scenario": copy.deepcopy(binding["plan"]["scenario"]),
            "operation_fence": None,
            "events": [],
        }
        intent = canonical_json_sha256(
            {
                "operation_id": journal["operation_id"],
                "operation_kind": journal["operation_kind"],
                "plan_digest": journal["plan_digest"],
                "scenario": journal["scenario"],
                "operation_fence": journal["operation_fence"],
            }
        )
        journal["events"] = [
            {
                "sequence": 1,
                "phase": "planned",
                "adapter_kind": "coordinator",
                "step_id": "workspace.plan-frozen",
                "target_id": binding["plan"]["plan_id"],
                "state": "committed",
                "evidence_digest": intent,
                "error_code": None,
            },
            {
                "sequence": 2,
                "phase": "workspace",
                "adapter_kind": "coordinator",
                "step_id": "workspace.adapter-execution",
                "target_id": binding["workspace_id"],
                "state": "started",
                "evidence_digest": None,
                "error_code": None,
            },
            {
                "sequence": 3,
                "phase": "workspace",
                "adapter_kind": "coordinator",
                "step_id": "workspace.adapter-execution",
                "target_id": binding["workspace_id"],
                "state": "failed",
                "evidence_digest": None,
                "error_code": error_code,
            },
        ]
        binding["state"] = "provision_failed"
        binding["error_code"] = error_code
        binding["journal"] = journal
        request = state["requests"].get(binding.get("provision_request_id"))
        if request is not None:
            request["status"] = "failed"

    @staticmethod
    def _replay(
        state: Mapping[str, Any], request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        request = state["requests"].get(request_id)
        if request is None:
            return None
        if request["request_digest"] != request_digest:
            raise WorkspaceError("ipc.request-reused", "request id was reused")
        if request["status"] == "completed":
            return request["operation_id"], copy.deepcopy(request["result"])
        if request["status"] == "pending":
            raise WorkspaceError(
                "workspace.operation-in-progress",
                "workspace operation is still in progress",
                retryable=True,
            )
        raise WorkspaceError(
            "workspace.previous-failure",
            "workspace operation previously failed",
            retryable=True,
            mutation_state="committed",
            operation_id=request["operation_id"],
        )

    @staticmethod
    def _record_request(
        state: dict[str, Any],
        request_id: str,
        request_digest: str,
        operation_id: str,
        result: Mapping[str, Any],
    ) -> None:
        state["requests"][request_id] = {
            "request_digest": request_digest,
            "operation_id": operation_id,
            "status": "completed",
            "result": copy.deepcopy(result),
        }


def _binding_key(project_instance_id: str, scenario_id: str) -> str:
    return f"{project_instance_id}\u0000{scenario_id}"


def _reject_public_absolute_paths(value: Any) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _reject_public_absolute_paths(child)
    elif isinstance(value, list):
        for child in value:
            _reject_public_absolute_paths(child)
    elif isinstance(value, str) and value.startswith("/"):
        raise WorkspaceError(
            "adapter.private-data-leak",
            "project adapter returned a physical path in a public artifact",
        )


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
