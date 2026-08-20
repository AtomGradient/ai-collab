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
MAX_ADAPTER_PROGRESS_BYTES = 256 * 1024
MAX_ADAPTER_PROGRESS_LINE_BYTES = 2 * 1024
MAX_ADAPTER_PROGRESS_EVENTS = 4096
ADAPTER_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
PROGRESS_COMPONENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
        base_fields = {
            "schema_version",
            "adapter_id",
            "command",
            "working_directory",
        }
        optional_fields = {"idempotent_join_operations", "progress_side_channel"}
        if (
            not isinstance(value, dict)
            or not base_fields.issubset(value)
            or set(value) - base_fields - optional_fields
        ):
            raise WorkspaceError("adapter.config-invalid", "adapter config fields differ")
        command = value["command"]
        idempotent_join_operations = value.get("idempotent_join_operations", [])
        progress_side_channel = value.get("progress_side_channel")
        if (
            value["schema_version"] != 1
            or not isinstance(value["adapter_id"], str)
            or not value["adapter_id"]
            or not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
            or not isinstance(idempotent_join_operations, list)
            or idempotent_join_operations
            != sorted(set(idempotent_join_operations))
            or any(
                item not in {"destroy", "recover", "repair"}
                for item in idempotent_join_operations
            )
            or progress_side_channel not in {None, "v1"}
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
        self.idempotent_join_operations = frozenset(idempotent_join_operations)
        self.progress_side_channel = progress_side_channel
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
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
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
        mutation_may_have_started = operation in {"repair", "destroy", "recover"}
        progress_reader: _AdapterProgressReader | None = None
        progress_write_descriptor: int | None = None
        pass_fds: tuple[int, ...] = ()
        if (
            operation == "provision"
            and self.progress_side_channel == "v1"
            and progress_callback is not None
        ):
            progress_reader = _AdapterProgressReader(payload, progress_callback)
            read_descriptor, progress_write_descriptor = os.pipe()
            environment["AI_COLLAB_PROGRESS_FD"] = str(progress_write_descriptor)
            pass_fds = (progress_write_descriptor,)
            progress_reader.start(read_descriptor)
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
                pass_fds=pass_fds,
            )
        except OSError as exc:
            raise WorkspaceError(
                "adapter.unavailable",
                "project adapter is unavailable",
                retryable=True,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(
                "adapter.timeout",
                "project adapter outcome is unknown",
                retryable=True,
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            ) from exc
        finally:
            if progress_write_descriptor is not None:
                os.close(progress_write_descriptor)
            if progress_reader is not None:
                progress_reader.join()
        if completed.returncode != 0:
            raise WorkspaceError(
                "adapter.crashed",
                "project adapter process exited without a typed reply",
                retryable=True,
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            )
        if completed.stderr:
            raise WorkspaceError(
                "adapter.protocol-invalid",
                "project adapter wrote unexpected diagnostic output",
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            )
        if not completed.stdout or len(completed.stdout) > MAX_ADAPTER_REPLY_BYTES:
            raise WorkspaceError(
                "adapter.invalid-reply",
                "project adapter reply is invalid",
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            )
        try:
            value = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                "adapter.invalid-reply",
                "project adapter reply is invalid",
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "adapter_protocol_version",
            "adapter_id",
            "outcome",
            "result",
        }:
            raise WorkspaceError(
                "adapter.invalid-reply",
                "project adapter reply fields differ",
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            )
        if (
            value["adapter_protocol_version"] != ADAPTER_PROTOCOL_VERSION
            or value["adapter_id"] != self.adapter_id
            or value["outcome"] not in {"completed", "failed"}
            or not isinstance(value["result"], dict)
        ):
            raise WorkspaceError(
                "adapter.invalid-reply",
                "project adapter rejected the operation",
                mutation_state=(
                    "unknown" if mutation_may_have_started else "not_started"
                ),
            )
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
                    "adapter.invalid-reply",
                    "project adapter error reply differs",
                    mutation_state=(
                        "unknown" if mutation_may_have_started else "not_started"
                    ),
                )
            _reject_public_absolute_paths(error)
            raise WorkspaceError(
                error["code"],
                error["message"],
                retryable=error["retryable"],
                mutation_state=error["mutation_state"],
            )
        if progress_reader is not None:
            progress_reader.require_complete()
        _reject_public_absolute_paths(value["result"])
        return value["result"]


class _AdapterProgressReader:
    """Drain and validate one declared, observation-only adapter FD."""

    def __init__(
        self,
        payload: Mapping[str, Any],
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        plan = payload.get("plan")
        components = plan.get("components") if isinstance(plan, Mapping) else None
        environment = plan.get("environment") if isinstance(plan, Mapping) else None
        if not isinstance(components, list) or not isinstance(environment, Mapping):
            raise WorkspaceError(
                "adapter.progress-invalid", "adapter progress membership is invalid"
            )
        expected: list[tuple[str, str]] = []
        for component in components:
            component_id = (
                component.get("component_id")
                if isinstance(component, Mapping)
                else None
            )
            if (
                not isinstance(component_id, str)
                or PROGRESS_COMPONENT_ID_RE.fullmatch(component_id) is None
            ):
                raise WorkspaceError(
                    "adapter.progress-invalid", "adapter progress membership is invalid"
                )
            expected.append((component_id, "cloning"))
        environment_id = environment.get("environment_id")
        if (
            not isinstance(environment_id, str)
            or PROGRESS_COMPONENT_ID_RE.fullmatch(environment_id) is None
        ):
            raise WorkspaceError(
                "adapter.progress-invalid", "adapter progress membership is invalid"
            )
        expected.append((environment_id, "building"))
        if len(expected) > 1024 or len({item[0] for item in expected}) != len(expected):
            raise WorkspaceError(
                "adapter.progress-invalid", "adapter progress membership is invalid"
            )
        self.expected = expected
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._states = ["new"] * len(expected)
        self._waiting_index = 0
        self._active_index = 0
        self._event_count = 0
        self._byte_count = 0

    def start(self, descriptor: int) -> None:
        self._thread = threading.Thread(
            target=self._read,
            args=(descriptor,),
            name="ai-collab-adapter-progress",
            daemon=True,
        )
        self._thread.start()

    def join(self) -> None:
        assert self._thread is not None
        self._thread.join()

    def _read(self, descriptor: int) -> None:
        with os.fdopen(descriptor, "rb") as stream:
            while True:
                raw = stream.readline(MAX_ADAPTER_PROGRESS_LINE_BYTES + 1)
                if not raw:
                    return
                self._event_count += 1
                self._byte_count += len(raw)
                if self._error is not None:
                    continue
                if (
                    len(raw) > MAX_ADAPTER_PROGRESS_LINE_BYTES
                    or self._event_count > MAX_ADAPTER_PROGRESS_EVENTS
                    or self._byte_count > MAX_ADAPTER_PROGRESS_BYTES
                ):
                    self._error = "adapter progress exceeds its bound"
                    continue
                try:
                    event = json.loads(raw)
                    self._accept(event)
                except (UnicodeDecodeError, json.JSONDecodeError, WorkspaceError):
                    self._error = "adapter progress event is invalid"

    def _accept(self, event: Any) -> None:
        if not isinstance(event, dict) or set(event) != {
            "component_id",
            "index",
            "total",
            "state",
        }:
            raise WorkspaceError(
                "adapter.progress-invalid", "adapter progress fields differ"
            )
        index = event["index"]
        total = event["total"]
        state = event["state"]
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total != len(self.expected)
            or index < 0
            or index >= total
            or event["component_id"] != self.expected[index][0]
            or state not in {"waiting", "cloning", "building", "ready", "failed"}
        ):
            raise WorkspaceError(
                "adapter.progress-invalid", "adapter progress values differ"
            )
        current = self._states[index]
        if state == "waiting":
            if index != self._waiting_index or current != "new":
                raise WorkspaceError(
                    "adapter.progress-invalid", "adapter progress order differs"
                )
            self._states[index] = "waiting"
            self._waiting_index += 1
        elif state in {"cloning", "building"}:
            if (
                self._waiting_index != total
                or index != self._active_index
                or current != "waiting"
                or state != self.expected[index][1]
            ):
                raise WorkspaceError(
                    "adapter.progress-invalid", "adapter progress order differs"
                )
            self._states[index] = state
        else:
            if (
                self._waiting_index != total
                or index != self._active_index
                or current != self.expected[index][1]
            ):
                raise WorkspaceError(
                    "adapter.progress-invalid", "adapter progress order differs"
                )
            self._states[index] = state
            if state == "ready":
                self._active_index += 1
        try:
            self.callback(copy.deepcopy(event))
        except Exception:
            # Progress is an observation. A disconnected or faulty observer
            # cannot cancel or alter the durable Workspace operation.
            pass

    def require_complete(self) -> None:
        if self._error is not None or any(state != "ready" for state in self._states):
            raise WorkspaceError(
                "adapter.progress-invalid",
                self._error or "adapter progress ended before completion",
                retryable=False,
                mutation_state="started",
            )


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
        # Initial high-risk execution and every exact recovery join share one
        # lock.  This prevents two Host threads from invoking the same
        # idempotent adapter operation concurrently while Store persists the
        # corresponding join claim.
        self._recovery_lock = threading.RLock()
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
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        adapter_progress = (
            progress_callback
            if getattr(self.adapter, "progress_side_channel", None) == "v1"
            else None
        )
        if self.project_root_resolver is None:
            if adapter_progress is None:
                return self.adapter.call(operation, payload)
            return self.adapter.call(
                operation,
                payload,
                progress_callback=adapter_progress,
            )
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
        adapter_arguments = {
            "project_root": self.project_root_resolver(project_instance_id),
            "project_render": (
                self.project_render_resolver(
                    project_instance_id, scenario_id, project_binding_digest
                )
                if self.project_render_resolver is not None
                else None
            ),
        }
        if adapter_progress is not None:
            adapter_arguments["progress_callback"] = adapter_progress
        return self.adapter.call(operation, payload, **adapter_arguments)

    def _adapter_join_capability(self, operation_kind: str) -> dict[str, Any]:
        adapter_id = getattr(self.adapter, "adapter_id", None)
        declared = getattr(self.adapter, "idempotent_join_operations", None)
        if (
            not isinstance(adapter_id, str)
            or not adapter_id
            or not isinstance(declared, (set, frozenset))
            or operation_kind not in declared
            or any(item not in {"destroy", "recover", "repair"} for item in declared)
        ):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace adapter does not declare exact idempotent join",
                retryable=False,
                mutation_state="unknown",
            )
        capability = {
            "join_capability_version": 1,
            "adapter_id": adapter_id,
            "operation_kind": operation_kind,
            "idempotent_join_declared": True,
        }
        return {**capability, "capability_digest": canonical_json_sha256(capability)}

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
    ) -> str:
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
                if binding["state"] not in {"provisioning", "provision_failed"}:
                    continue
                root = workspace_path(binding["workspace_id"])
                operation_id = binding["plan"]["operation_id"]
                stage = root / f".stage-{operation_id}"
                bundle = root / "bundle"
                provision_request = state["requests"].get(
                    binding.get("provision_request_id")
                )
                result = self._load_pending_publish_result(
                    binding,
                    provision_request,
                    bundle,
                )
                if result is None and not bundle.exists() and not bundle.is_symlink():
                    staged = self._load_pending_publish_result(
                        binding,
                        provision_request,
                        stage,
                    )
                    if staged is not None and binding["state"] == "provisioning":
                        try:
                            os.replace(stage, bundle)
                            self._fsync_directory(root)
                        except OSError:
                            # Resolve the rename outcome from the exact marker.
                            # If it did not publish, the unpublished scratch is
                            # disposable and a new provision request may retry.
                            result = self._load_pending_publish_result(
                                binding,
                                provision_request,
                                bundle,
                            )
                            if result is None:
                                self._discard_owned_stage(
                                    stage,
                                    root,
                                    operation_id=operation_id,
                                )
                        else:
                            result = staged
                    elif stage.exists() or stage.is_symlink():
                        # The exact stage is adapter scratch which was never
                        # published to Participants. Existing live-error paths
                        # already discard it; startup does the same after a
                        # process crash so retry/delete cannot be stranded.
                        self._discard_owned_stage(
                            stage,
                            root,
                            operation_id=operation_id,
                        )
                if result is None:
                    if binding["state"] == "provisioning":
                        error_code = (
                            "workspace.publish-evidence-mismatch"
                            if bundle.exists() or bundle.is_symlink()
                            else "workspace.publish-outcome-unknown"
                        )
                        self._fail_pending_binding(state, binding, error_code)
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
        # Repair/destroy recovery is deliberately Host-driven.  Workspace
        # state alone is not authority to replay an external mutation: the
        # Host must first intersect it with the exact pending Store operation,
        # persist a bounded join attempt, and only then call the adapter.

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
            historical = state["history"].get(key)
            if (
                isinstance(historical, dict)
                and historical.get("workspace_id") == workspace_id
            ):
                raise WorkspaceError(
                    "workspace.destroyed", "workspace binding was already destroyed"
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
            historical = state["history"].get(key)
            if key in state["bindings"] or (
                isinstance(historical, dict)
                and historical.get("workspace_id") == workspace_id
            ):
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
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
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
            environment_id = (
                plan.get("environment", {}).get("environment_id")
                if isinstance(plan.get("environment"), Mapping)
                else None
            )
        publish_evidence: dict[str, Any] | None = None
        result_digest: str | None = None
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
                progress_callback=(
                    (
                        lambda event: progress_callback(
                            operation_id,
                            {
                                **event,
                                "component_kind": (
                                    "environment"
                                    if event["component_id"] == environment_id
                                    else "repository"
                                ),
                            },
                        )
                    )
                    if progress_callback is not None
                    else None
                ),
            )
            if set(external) != {"journal", "receipt", "review_snapshot"}:
                raise WorkspaceError("adapter.invalid-reply", "provision result fields differ")
            self._validate_ready_result(binding, external)
            self._ensure_private_stage(staging_path)
            _write_private_json(staging_path / ".ai-collab-harness-binding.json", external)
            result_digest = canonical_json_sha256(external)
            publish_evidence = external
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
            self._discard_owned_stage(
                staging_path,
                workspace_path,
                operation_id=operation_id,
            )
            self._record_provision_failure(key, request_id, exc.code)
            raise WorkspaceError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc
        except OSError as exc:
            # A failed rename/fsync can occur after the exact marker digest was
            # durably committed. Preserve that pending state so startup can
            # resolve stage-vs-bundle rather than misclassifying a published
            # bundle as a retryable failure with no exit.
            pending_publish = False
            if publish_evidence is not None and result_digest is not None:
                try:
                    with self._lock:
                        durable = self._read_state()["bindings"].get(key)
                    pending_publish = bool(
                        durable is not None
                        and durable.get("state") == "provisioning"
                        and durable.get("pending_result_digest") == result_digest
                    )
                except (OSError, WorkspaceError):
                    pending_publish = True
            if pending_publish:
                raise WorkspaceError(
                    "workspace.publish-outcome-unknown",
                    "workspace publication outcome is unknown",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=operation_id,
                ) from exc
            self._discard_owned_stage(
                staging_path,
                workspace_path,
                operation_id=operation_id,
            )
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
            binding_state = "absent" if binding is None else binding["state"]
            if binding_state != "ready":
                if operation == "scenario.repair" and binding_state in {
                    "repairing",
                    "destroying",
                    "recovering",
                    "repair_failed",
                    "destroy_failed",
                    "recovery_failed",
                }:
                    assert binding is not None
                    if binding.get("scenario_generation") != scenario_generation:
                        raise WorkspaceError(
                            "workspace.stale-fence", "workspace fence differs"
                        )
                    return self._manual_recovery_context(
                        state=state,
                        binding=binding,
                        workspace_path=workspace_path,
                    )
                if operation != "scenario.destroy" or binding_state not in {
                    "absent",
                    "planned",
                    "provision_failed",
                }:
                    raise WorkspaceError("workspace.not-ready", "workspace is not ready")
                if (
                    binding is not None
                    and binding["scenario_generation"] != scenario_generation
                ):
                    raise WorkspaceError(
                        "workspace.stale-fence", "workspace fence differs"
                    )
                workspace_id = (
                    binding["workspace_id"]
                    if binding is not None
                    else workspace_path.name
                )
                husk_digest = self._empty_husk_digest(
                    workspace_path, workspace_id=workspace_id
                )
                return (
                    {
                        "workspace_id": workspace_id,
                        "workspace_binding_digest": None,
                        "receipt_digest": None,
                        "state": "unprovisioned",
                        "binding_state": binding_state,
                        "drift_codes": [],
                        "wip_summary_digest": husk_digest,
                        "canonical_source_wip_mutation": False,
                    },
                    {
                        "subject_kind": "empty-project-storage",
                        "workspace_path": str(workspace_path),
                        "expected_binding_state": binding_state,
                        "expected_husk_digest": husk_digest,
                    },
                )
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
            "binding_state": "ready",
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

    def _manual_recovery_context(
        self,
        *,
        state: Mapping[str, Any],
        binding: Mapping[str, Any],
        workspace_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Freeze a new, user-confirmed recovery without replaying the old op."""

        checkpoint = binding.get("recovery_checkpoint")
        source = binding.get("pending_recovery_source")
        if binding.get("state") == "recovery_failed" and isinstance(
            checkpoint, dict
        ):
            prior_claim = checkpoint.get("prior_claim")
        elif isinstance(source, dict):
            prior_claim = source.get("prior_claim")
        else:
            request_id = binding.get("pending_request_id")
            operation_id = binding.get("pending_operation_id")
            request = (
                state["requests"].get(request_id)
                if isinstance(request_id, str)
                else None
            )
            if not isinstance(request, dict) or not isinstance(operation_id, str):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace recovery evidence is unavailable",
                    mutation_state="unknown",
                )
            prior_claim = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_current_capability=False,
            )
        prior_claim = self._validated_frozen_prior_claim(
            binding=binding,
            workspace_path=workspace_path,
            value=prior_claim,
        )
        inventory = self._recovery_inventory(
            workspace_path=workspace_path,
            workspace_id=binding["workspace_id"],
        )
        recovery = {
            "recovery_contract_version": 1,
            "prior_operation_kind": prior_claim["operation_kind"],
            "prior_operation_claim_digest": prior_claim["claim_digest"],
            "inventory_digest": inventory["inventory_digest"],
        }
        preview = {
            "workspace_id": binding["workspace_id"],
            "workspace_binding_digest": binding["receipt"].get(
                "workspace_binding_digest"
            ),
            "receipt_digest": canonical_json_sha256(binding["receipt"]),
            "state": "recovery-required",
            "binding_state": binding["state"],
            "drift_codes": ["workspace.external-outcome-unknown"],
            "wip_summary_digest": prior_claim[
                "expected_wip_summary_digest"
            ],
            "canonical_source_wip_mutation": False,
            "recovery": recovery,
        }
        subject = {
            "subject_kind": "project-storage-recovery",
            "workspace_path": str(workspace_path),
            "workspace_id": binding["workspace_id"],
            "expected_inventory_digest": inventory["inventory_digest"],
            "allowed_entry_names": inventory["entry_names"],
            "prior_operation_kind": prior_claim["operation_kind"],
            "prior_operation_claim_digest": prior_claim["claim_digest"],
        }
        return preview, subject

    def _validated_frozen_prior_claim(
        self,
        *,
        binding: Mapping[str, Any],
        workspace_path: Path,
        value: Any,
    ) -> dict[str, Any]:
        """Validate a frozen repair/destroy claim without mutable capability lookup."""

        fields = {
            "join_claim_version",
            "workspace_operation_id",
            "workspace_request_id",
            "request_digest",
            "operation_kind",
            "project_instance_id",
            "scenario_id",
            "scenario_generation",
            "workspace_id",
            "plan_digest",
            "receipt_digest",
            "expected_wip_summary_digest",
            "force",
            "workspace_path_identity_digest",
            "pending_fence_digest",
            "adapter_capability_digest",
            "recovery_source_digest",
            "claim_digest",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise WorkspaceError(
                "workspace.recovery-unprovable",
                "workspace prior claim fields differ",
                mutation_state="unknown",
            )
        claim = copy.deepcopy(value)
        digest = claim.pop("claim_digest")
        sha_fields = {
            "request_digest",
            "plan_digest",
            "receipt_digest",
            "expected_wip_summary_digest",
            "workspace_path_identity_digest",
            "pending_fence_digest",
            "adapter_capability_digest",
            "recovery_source_digest",
        }
        if (
            value.get("join_claim_version") != 1
            or value.get("operation_kind") not in {"destroy", "repair"}
            or not isinstance(value.get("workspace_operation_id"), str)
            or not value["workspace_operation_id"].startswith("wsop-")
            or not isinstance(value.get("workspace_request_id"), str)
            or not value["workspace_request_id"]
            or any(
                not isinstance(value.get(field), str)
                or re.fullmatch(r"[0-9a-f]{64}", value[field]) is None
                for field in sha_fields
            )
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or canonical_json_sha256(claim) != digest
            or not isinstance(value.get("force"), bool)
            or (
                value.get("operation_kind") == "repair"
                and value.get("force") is not False
            )
            or value.get("project_instance_id")
            != binding.get("project_instance_id")
            or value.get("scenario_id") != binding.get("scenario_id")
            or value.get("scenario_generation")
            != binding.get("scenario_generation")
            or value.get("workspace_id") != binding.get("workspace_id")
            or value.get("plan_digest")
            != canonical_json_sha256(binding.get("plan"))
            or value.get("receipt_digest")
            != canonical_json_sha256(binding.get("receipt"))
            or value.get("workspace_path_identity_digest")
            != self._workspace_path_identity_digest(
                workspace_path, workspace_id=binding["workspace_id"]
            )
            or value.get("recovery_source_digest")
            != canonical_json_sha256(None)
        ):
            raise WorkspaceError(
                "workspace.recovery-unprovable",
                "workspace prior claim provenance differs",
                mutation_state="unknown",
            )
        return copy.deepcopy(value)

    @classmethod
    def _recovery_inventory(
        cls,
        *,
        workspace_path: Path,
        workspace_id: str,
    ) -> dict[str, Any]:
        root_digest = cls._workspace_path_identity_digest(
            workspace_path, workspace_id=workspace_id
        )
        try:
            entries = sorted(workspace_path.iterdir(), key=lambda item: item.name)
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
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace recovery inventory contains an unknown entry",
                        mutation_state="unknown",
                    )
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
        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceError(
                "workspace.recovery-unprovable",
                "workspace recovery inventory is unavailable",
                mutation_state="unknown",
            ) from exc
        material = {
            "workspace_id": workspace_id,
            "workspace_path_identity_digest": root_digest,
            "entries": observed,
        }
        return {
            "entry_names": [item["name"] for item in observed],
            "inventory_digest": canonical_json_sha256(material),
        }

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
        before_external: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with self._recovery_lock:
            prepared = self._prepare_ready_high_risk(
                request_id=request_id,
                request_digest=request_digest,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                workspace_path=workspace_path,
                expected_wip_summary_digest=expected_wip_summary_digest,
                operation_kind="repair",
                expected_binding_state="ready",
                force=False,
            )
            if isinstance(prepared, tuple):
                return prepared
            if before_external is not None:
                try:
                    before_external(copy.deepcopy(prepared))
                except Exception as exc:
                    no_effect = WorkspaceError(
                        "workspace.repair-bind-failed",
                        "workspace repair was not started",
                        retryable=True,
                        mutation_state="not_started",
                        operation_id=prepared["workspace_operation_id"],
                    )
                    try:
                        self._restore_ready_after_no_effect(
                            key=_binding_key(project_instance_id, scenario_id),
                            request_id=request_id,
                            operation_id=prepared["workspace_operation_id"],
                            operation_kind="repair",
                            error=no_effect,
                        )
                    except (WorkspaceError, OSError) as rollback_error:
                        raise WorkspaceError(
                            "workspace.repair-outcome-unknown",
                            "workspace repair rollback outcome is unknown",
                            retryable=True,
                            mutation_state="unknown",
                            operation_id=prepared["workspace_operation_id"],
                        ) from rollback_error
                    raise no_effect from exc
            return self._execute_pending_high_risk(prepared, workspace_path)

    def recover(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_wip_summary_digest: str,
        expected_prior_claim_digest: str,
        expected_inventory_digest: str,
        before_external: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Run a new confirmed recovery; never replay the exhausted old op."""

        with self._recovery_lock:
            prepared = self._prepare_recovery(
                request_id=request_id,
                request_digest=request_digest,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                workspace_path=workspace_path,
                expected_wip_summary_digest=expected_wip_summary_digest,
                expected_prior_claim_digest=expected_prior_claim_digest,
                expected_inventory_digest=expected_inventory_digest,
            )
            if isinstance(prepared, tuple):
                return prepared
            if before_external is not None:
                try:
                    before_external(copy.deepcopy(prepared))
                except Exception as exc:
                    no_effect = WorkspaceError(
                        "workspace.recovery-bind-failed",
                        "workspace recovery was not started",
                        retryable=True,
                        mutation_state="not_started",
                        operation_id=prepared["workspace_operation_id"],
                    )
                    try:
                        self._restore_ready_after_no_effect(
                            key=_binding_key(project_instance_id, scenario_id),
                            request_id=request_id,
                            operation_id=prepared["workspace_operation_id"],
                            operation_kind="recover",
                            error=no_effect,
                        )
                    except (WorkspaceError, OSError) as rollback_error:
                        raise WorkspaceError(
                            "workspace.recover-outcome-unknown",
                            "workspace recovery rollback outcome is unknown",
                            retryable=True,
                            mutation_state="unknown",
                            operation_id=prepared["workspace_operation_id"],
                        ) from rollback_error
                    raise no_effect from exc
            return self._execute_pending_high_risk(prepared, workspace_path)

    def _prepare_recovery(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_wip_summary_digest: str,
        expected_prior_claim_digest: str,
        expected_inventory_digest: str,
    ) -> dict[str, Any] | tuple[str, dict[str, Any]]:
        capability = self._adapter_join_capability("recover")
        key = _binding_key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding.get("state") not in {
                "repairing",
                "destroying",
                "repair_failed",
                "destroy_failed",
                "recovery_failed",
            }:
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace does not have a recoverable pending outcome",
                    mutation_state="unknown",
                )
            if binding.get("scenario_generation") != scenario_generation:
                raise WorkspaceError("workspace.stale-fence", "workspace fence differs")
            checkpoint = binding.get("recovery_checkpoint")
            if binding.get("state") == "recovery_failed" and isinstance(
                checkpoint, dict
            ):
                prior_claim = checkpoint.get("prior_claim")
                binding_before = copy.deepcopy(binding)
            else:
                prior_request_id = binding.get("pending_request_id")
                prior_operation_id = binding.get("pending_operation_id")
                prior_request = (
                    state["requests"].get(prior_request_id)
                    if isinstance(prior_request_id, str)
                    else None
                )
                if not isinstance(prior_request, dict) or not isinstance(
                    prior_operation_id, str
                ):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace prior claim is unavailable",
                        mutation_state="unknown",
                    )
                prior_claim = self._pending_join_claim(
                    binding=binding,
                    request=prior_request,
                    workspace_path=workspace_path,
                    require_current_capability=False,
                )
                binding_before = copy.deepcopy(binding)
            prior_claim = self._validated_frozen_prior_claim(
                binding=binding,
                workspace_path=workspace_path,
                value=prior_claim,
            )
            if (
                prior_claim.get("claim_digest")
                != expected_prior_claim_digest
                or prior_claim.get("expected_wip_summary_digest")
                != expected_wip_summary_digest
                or not isinstance(binding_before, dict)
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace prior claim differs",
                    mutation_state="unknown",
                )
            inventory = self._recovery_inventory(
                workspace_path=workspace_path,
                workspace_id=binding["workspace_id"],
            )
            if inventory["inventory_digest"] != expected_inventory_digest:
                raise WorkspaceError(
                    "workspace.concurrent-change",
                    "workspace recovery inventory changed",
                    mutation_state="not_started",
                )
            operation_id = f"wsop-{uuid.uuid4().hex}"
            recovery_source = {
                "recovery_source_version": 1,
                "prior_claim": copy.deepcopy(prior_claim),
                "binding_before": copy.deepcopy(binding_before),
                "expected_inventory_digest": expected_inventory_digest,
                "bundle_path": str(workspace_path / "bundle"),
            }
            binding.pop("recovery_checkpoint", None)
            binding.update(
                {
                    "state": "recovering",
                    "pending_request_id": request_id,
                    "pending_operation_id": operation_id,
                    "pending_expected_wip_summary_digest": (
                        expected_wip_summary_digest
                    ),
                    "pending_force": False,
                    "pending_workspace_path_identity_digest": (
                        self._workspace_path_identity_digest(
                            workspace_path, workspace_id=binding["workspace_id"]
                        )
                    ),
                    "pending_adapter_capability_digest": capability[
                        "capability_digest"
                    ],
                    "pending_recovery_source": recovery_source,
                    # This bit is deliberately outside the stable join claim:
                    # it records that this exact recovery operation may have
                    # crossed the adapter boundary.  Once true, a later
                    # not_started reply cannot safely roll the binding back.
                    "pending_recover_external_attempted": False,
                }
            )
            binding["pending_fence_digest"] = self._pending_fence_digest(binding)
            request = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "operation_kind": "recover",
                "status": "pending",
                "result": None,
            }
            state["requests"][request_id] = request
            claim = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_persisted_claim=False,
            )
            binding["pending_join_claim_digest"] = claim["claim_digest"]
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                # Atomic replace may already have published the prepared
                # request even when the directory durability step fails.
                # Keep the Store intent transitional so startup can inspect
                # the exact request (or prove its absence) before deciding.
                raise WorkspaceError(
                    "workspace.recover-outcome-unknown",
                    "workspace recovery preparation outcome is unknown",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=operation_id,
                ) from exc
            return claim

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
        expected_binding_state: str,
        force: bool = False,
        before_external: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with self._recovery_lock:
            key = _binding_key(project_instance_id, scenario_id)
            with self._lock:
                state = self._read_state()
                replay = self._replay(state, request_id, request_digest)
                if replay is not None:
                    return replay
                binding = state["bindings"].get(key)
                binding_state = "absent" if binding is None else binding["state"]
                if binding_state != expected_binding_state:
                    raise WorkspaceError(
                        "workspace.concurrent-change",
                        "workspace binding changed during destroy",
                    )
                if binding_state != "ready":
                    if force or binding_state not in {
                        "absent",
                        "planned",
                        "provision_failed",
                    }:
                        raise WorkspaceError(
                            "workspace.not-ready", "workspace is not destroyable"
                        )
                    if (
                        binding is not None
                        and binding["scenario_generation"] != scenario_generation
                    ):
                        raise WorkspaceError(
                            "workspace.stale-fence", "workspace fence differs"
                        )
                    return self._destroy_empty_husk(
                        state=state,
                        key=key,
                        binding=binding,
                        binding_state=binding_state,
                        request_id=request_id,
                        request_digest=request_digest,
                        project_instance_id=project_instance_id,
                        scenario_id=scenario_id,
                        scenario_generation=scenario_generation,
                        workspace_path=workspace_path,
                        expected_husk_digest=expected_wip_summary_digest,
                    )
            prepared = self._prepare_ready_high_risk(
                request_id=request_id,
                request_digest=request_digest,
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                scenario_generation=scenario_generation,
                workspace_path=workspace_path,
                expected_wip_summary_digest=expected_wip_summary_digest,
                operation_kind="destroy",
                expected_binding_state=expected_binding_state,
                force=force,
            )
            if isinstance(prepared, tuple):
                return prepared
            if before_external is not None:
                before_external(copy.deepcopy(prepared))
            return self._execute_pending_high_risk(prepared, workspace_path)

    def inspect_pending_high_risk_join(
        self,
        *,
        workspace_request_id: str,
        request_digest: str,
        workspace_path: Path,
    ) -> dict[str, Any] | None:
        """Return a stable proof for one pending adapter mutation, without replay."""

        with self._recovery_lock, self._lock:
            state = self._read_state()
            request = state["requests"].get(workspace_request_id)
            if request is None:
                return None
            if request.get("request_digest") != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            if request.get("status") == "completed":
                return None
            if request.get("status") != "pending":
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace request is not durably pending",
                    mutation_state="unknown",
                )
            binding = self._pending_binding_for_request(
                state, workspace_request_id, request["operation_id"]
            )
            if binding is None:
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace pending binding is unavailable",
                    mutation_state="unknown",
                )
            return self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
            )

    def inspect_frozen_pending_high_risk_join(
        self,
        *,
        workspace_request_id: str,
        request_digest: str,
        workspace_path: Path,
    ) -> dict[str, Any] | None:
        """Read a durably frozen claim without trusting mutable capability config.

        This is only evidence for fail-closed degradation/manual recovery; it
        never authorizes replay of the old adapter operation.
        """

        with self._recovery_lock, self._lock:
            state = self._read_state()
            request = state["requests"].get(workspace_request_id)
            if request is None:
                return None
            if request.get("request_digest") != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            if request.get("status") != "pending":
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace frozen request is not durably pending",
                    mutation_state="unknown",
                )
            binding = self._pending_binding_for_request(
                state, workspace_request_id, request.get("operation_id")
            )
            if binding is None:
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace frozen binding is unavailable",
                    mutation_state="unknown",
                )
            return self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_current_capability=False,
            )

    def resume_exact_high_risk_join(
        self,
        *,
        workspace_claim: Mapping[str, Any],
        workspace_path: Path,
        before_external: Callable[[Mapping[str, Any]], Any],
    ) -> tuple[str, dict[str, Any]]:
        """Replay only an exact Store-authorized, durably pending claim."""

        supplied = dict(workspace_claim)
        with self._recovery_lock:
            with self._lock:
                state = self._read_state()
                request_id = supplied.get("workspace_request_id")
                operation_id = supplied.get("workspace_operation_id")
                if not isinstance(request_id, str) or not isinstance(
                    operation_id, str
                ):
                    raise WorkspaceError(
                        "workspace.join-unprovable",
                        "workspace join identity differs",
                        mutation_state="unknown",
                    )
                request = state["requests"].get(request_id)
                binding = self._pending_binding_for_request(
                    state, request_id, operation_id
                )
                if request is None or binding is None:
                    raise WorkspaceError(
                        "workspace.join-unprovable",
                        "workspace join evidence is unavailable",
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                current = self._pending_join_claim(
                    binding=binding,
                    request=request,
                    workspace_path=workspace_path,
                )
                if current != supplied:
                    raise WorkspaceError(
                        "workspace.concurrent-change",
                        "workspace join claim changed",
                        retryable=False,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
            # The Store callback persists the bounded attempt while this lock
            # prevents an initial execution or another join from racing it.
            before_external(copy.deepcopy(current))
            return self._execute_pending_high_risk(current, workspace_path)

    def retire_exhausted_recovery(
        self,
        *,
        workspace_claim: Mapping[str, Any],
        workspace_path: Path,
        reason: str,
        require_persisted_claim: bool = True,
        require_current_capability: bool = True,
    ) -> str:
        """Seal an exhausted recover op into a flat manual-recovery checkpoint."""

        supplied = dict(workspace_claim)
        with self._recovery_lock, self._lock:
            state = self._read_state()
            request_id = supplied.get("workspace_request_id")
            operation_id = supplied.get("workspace_operation_id")
            if not isinstance(request_id, str) or not isinstance(
                operation_id, str
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace recovery retirement identity differs",
                    mutation_state="unknown",
                )
            request = state["requests"].get(request_id)
            binding = self._pending_binding_for_request(
                state, request_id, operation_id
            )
            if request is None or binding is None:
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace recovery retirement evidence is unavailable",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            current = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_persisted_claim=require_persisted_claim,
                require_current_capability=require_current_capability,
            )
            source = binding.get("pending_recovery_source")
            prior = source.get("prior_claim") if isinstance(source, dict) else None
            prior = self._validated_frozen_prior_claim(
                binding=binding,
                workspace_path=workspace_path,
                value=prior,
            )
            attempted = binding.get("pending_recover_external_attempted")
            if (
                current != supplied
                or supplied.get("operation_kind") != "recover"
                or binding.get("state") != "recovering"
                or not isinstance(attempted, bool)
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace exhausted recovery fence differs",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            if not attempted:
                binding_before = (
                    source.get("binding_before")
                    if isinstance(source, dict)
                    else None
                )
                if not isinstance(binding_before, dict):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace no-effect recovery checkpoint differs",
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                terminal_recovery = {
                    "terminal_recovery_version": 1,
                    "resolution": "not_started",
                    "workspace_operation_id": operation_id,
                    "project_instance_id": binding["project_instance_id"],
                    "scenario_id": binding["scenario_id"],
                    "scenario_generation": binding["scenario_generation"],
                    "workspace_id": binding["workspace_id"],
                    "prior_operation_kind": prior["operation_kind"],
                    "prior_claim_digest": prior["claim_digest"],
                    "last_recovery_claim_digest": supplied["claim_digest"],
                    "reason": "workspace.recovery-not-started",
                    "unjoinable": False,
                    "workspace_claim": None,
                }
                binding.clear()
                binding.update(copy.deepcopy(binding_before))
                request.update(
                    {
                        "status": "failed",
                        "error": {
                            "code": "workspace.recovery-not-started",
                            "message": "workspace recovery adapter did not run",
                            "retryable": False,
                            "mutation_state": "not_started",
                        },
                        "terminal_recovery": terminal_recovery,
                    }
                )
                state["state_revision"] += 1
                try:
                    self._write_state(state)
                except OSError as exc:
                    raise WorkspaceError(
                        "workspace.recovery-retire-outcome-unknown",
                        "workspace recovery retirement outcome is unknown",
                        retryable=True,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    ) from exc
                return "not_started"
            checkpoint = {
                "recovery_checkpoint_version": 1,
                "prior_claim": copy.deepcopy(prior),
                "last_recovery_request_id": request_id,
                "last_recovery_operation_id": operation_id,
                "last_recovery_claim_digest": supplied["claim_digest"],
                "failure_code": reason,
            }
            binding["state"] = "recovery_failed"
            binding["error_code"] = reason
            self._clear_pending_high_risk(binding)
            binding["recovery_checkpoint"] = checkpoint
            request.update(
                {
                    "status": "failed",
                    "error": {
                        "code": reason,
                        "message": "workspace recovery requires new confirmation",
                        "retryable": False,
                        "mutation_state": "unknown",
                    },
                    "terminal_recovery": {
                        "terminal_recovery_version": 1,
                        "resolution": "retired",
                        "workspace_operation_id": operation_id,
                        "project_instance_id": binding[
                            "project_instance_id"
                        ],
                        "scenario_id": binding["scenario_id"],
                        "scenario_generation": binding[
                            "scenario_generation"
                        ],
                        "workspace_id": binding["workspace_id"],
                        "prior_operation_kind": prior["operation_kind"],
                        "prior_claim_digest": prior["claim_digest"],
                        "last_recovery_claim_digest": supplied[
                            "claim_digest"
                        ],
                        "reason": reason,
                        "unjoinable": (
                            reason != "workspace.join-attempts-exhausted"
                        ),
                        "workspace_claim": copy.deepcopy(supplied),
                    },
                }
            )
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise WorkspaceError(
                    "workspace.recovery-retire-outcome-unknown",
                    "workspace recovery retirement outcome is unknown",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=operation_id,
                ) from exc
            return "retired"

    def retire_unjoinable_recovery(
        self,
        *,
        workspace_request_id: str,
        request_digest: str,
        workspace_path: Path,
        reason: str,
    ) -> tuple[str, dict[str, Any]]:
        """Flatten a recover op whose mutable join declaration is unavailable."""

        with self._recovery_lock, self._lock:
            state = self._read_state()
            request = state["requests"].get(workspace_request_id)
            if (
                not isinstance(request, dict)
                or request.get("request_digest") != request_digest
                or request.get("status") != "pending"
                or request.get("operation_kind") != "recover"
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace unjoinable recovery request differs",
                    mutation_state="unknown",
                )
            operation_id = request.get("operation_id")
            if not isinstance(operation_id, str):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace unjoinable recovery operation differs",
                    mutation_state="unknown",
                )
            binding = self._pending_binding_for_request(
                state, workspace_request_id, operation_id
            )
            if binding is None:
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace unjoinable recovery binding is unavailable",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            frozen = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_persisted_claim=False,
                require_current_capability=False,
            )
        resolution = self.retire_exhausted_recovery(
            workspace_claim=frozen,
            workspace_path=workspace_path,
            reason=reason,
            require_persisted_claim=False,
            require_current_capability=False,
        )
        return resolution, frozen

    def _prepare_ready_high_risk(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_wip_summary_digest: str,
        operation_kind: str,
        expected_binding_state: str,
        force: bool,
    ) -> dict[str, Any] | tuple[str, dict[str, Any]]:
        if operation_kind not in {"repair", "destroy"}:
            raise WorkspaceError(
                "workspace.operation-invalid", "workspace operation differs"
            )
        key = _binding_key(project_instance_id, scenario_id)
        capability = self._adapter_join_capability(operation_kind)
        with self._lock:
            state = self._read_state()
            replay = self._replay(state, request_id, request_digest)
            if replay is not None:
                return replay
            binding = state["bindings"].get(key)
            if binding is None or binding.get("state") != expected_binding_state:
                raise WorkspaceError(
                    "workspace.not-ready", "workspace binding is not ready"
                )
            if expected_binding_state != "ready":
                raise WorkspaceError(
                    "workspace.concurrent-change", "workspace binding changed"
                )
            if binding.get("scenario_generation") != scenario_generation:
                raise WorkspaceError("workspace.stale-fence", "workspace fence differs")
            workspace_id = binding.get("workspace_id")
            if not isinstance(workspace_id, str):
                raise WorkspaceError(
                    "workspace.state-invalid", "workspace identity differs"
                )
            path_digest = self._workspace_path_identity_digest(
                workspace_path, workspace_id=workspace_id
            )
            operation_id = f"wsop-{uuid.uuid4().hex}"
            binding.update(
                {
                    "state": f"{operation_kind}ing",
                    "pending_request_id": request_id,
                    "pending_operation_id": operation_id,
                    "pending_expected_wip_summary_digest": (
                        expected_wip_summary_digest
                    ),
                    "pending_force": bool(force),
                    "pending_workspace_path_identity_digest": path_digest,
                    "pending_adapter_capability_digest": capability[
                        "capability_digest"
                    ],
                }
            )
            binding["pending_fence_digest"] = self._pending_fence_digest(binding)
            request = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "status": "pending",
                "result": None,
            }
            state["requests"][request_id] = request
            claim = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
                require_persisted_claim=False,
            )
            binding["pending_join_claim_digest"] = claim["claim_digest"]
            state["state_revision"] += 1
            try:
                self._write_state(state)
            except OSError as exc:
                raise WorkspaceError(
                    f"workspace.{operation_kind}-outcome-unknown",
                    f"workspace {operation_kind} preparation outcome is unknown",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=operation_id,
                ) from exc
            return claim

    def _execute_pending_high_risk(
        self, workspace_claim: Mapping[str, Any], workspace_path: Path
    ) -> tuple[str, dict[str, Any]]:
        claim = dict(workspace_claim)
        operation_id = claim["workspace_operation_id"]
        request_id = claim["workspace_request_id"]
        operation_kind = claim["operation_kind"]
        key = _binding_key(claim["project_instance_id"], claim["scenario_id"])
        recover_previously_attempted = False
        with self._lock:
            state = self._read_state()
            request = state["requests"].get(request_id)
            binding = self._pending_binding_for_request(
                state, request_id, operation_id
            )
            if request is None or binding is None:
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace join evidence is unavailable",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            current_claim = self._pending_join_claim(
                binding=binding,
                request=request,
                workspace_path=workspace_path,
            )
            if current_claim != claim:
                raise WorkspaceError(
                    "workspace.concurrent-change",
                    "workspace join claim changed",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            if operation_kind == "recover":
                attempted = binding.get("pending_recover_external_attempted")
                if not isinstance(attempted, bool):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace recovery attempt evidence differs",
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                recover_previously_attempted = attempted
                if not attempted:
                    binding["pending_recover_external_attempted"] = True
                    state["state_revision"] += 1
                    try:
                        self._write_state(state)
                    except OSError as exc:
                        raise WorkspaceError(
                            "workspace.recover-outcome-unknown",
                            "workspace recovery boundary could not be persisted",
                            retryable=True,
                            mutation_state="unknown",
                            operation_id=operation_id,
                        ) from exc
            snapshot = copy.deepcopy(binding)
        payload = {
            "operation_id": operation_id,
            "bundle_path": str(workspace_path / "bundle"),
            "plan": copy.deepcopy(snapshot["plan"]),
            "receipt": copy.deepcopy(snapshot["receipt"]),
            "expected_wip_summary_digest": claim[
                "expected_wip_summary_digest"
            ],
        }
        if operation_kind == "destroy":
            payload["force"] = bool(claim["force"])
        elif operation_kind == "recover":
            source = snapshot.get("pending_recovery_source")
            prior = source.get("prior_claim") if isinstance(source, dict) else None
            if not isinstance(prior, dict):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace recovery source is unavailable",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            payload["prior_operation"] = {
                "operation_id": prior["workspace_operation_id"],
                "operation_kind": prior["operation_kind"],
                "force": bool(prior["force"]),
                "claim_digest": prior["claim_digest"],
            }
        try:
            external = self._call_adapter(
                claim["project_instance_id"], operation_kind, payload
            )
        except WorkspaceError as exc:
            if exc.mutation_state == "not_started":
                if operation_kind == "recover" and recover_previously_attempted:
                    raise WorkspaceError(
                        "workspace.recover-outcome-unknown",
                        "workspace recovery outcome remains unknown",
                        retryable=True,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    ) from exc
                try:
                    self._restore_ready_after_no_effect(
                        key=key,
                        request_id=request_id,
                        operation_id=operation_id,
                        operation_kind=operation_kind,
                        error=exc,
                    )
                except (WorkspaceError, OSError) as rollback_error:
                    raise WorkspaceError(
                        f"workspace.{operation_kind}-outcome-unknown",
                        f"workspace {operation_kind} rollback outcome is unknown",
                        retryable=True,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    ) from rollback_error
                raise
            raise WorkspaceError(
                exc.code,
                exc.message,
                retryable=True,
                mutation_state="unknown",
                operation_id=operation_id,
            ) from exc
        except (OSError, KeyError, TypeError) as exc:
            raise WorkspaceError(
                f"workspace.{operation_kind}-outcome-unknown",
                f"workspace {operation_kind} outcome is unknown",
                retryable=True,
                mutation_state="unknown",
                operation_id=operation_id,
            ) from exc
        try:
            if operation_kind == "repair":
                self._validate_repair_result(
                    snapshot,
                    operation_id,
                    claim["expected_wip_summary_digest"],
                    external,
                )
                response = {
                    "workspace": {"state": "ready", **copy.deepcopy(external)}
                }
            elif operation_kind == "destroy":
                self._validate_destroy_result(
                    snapshot,
                    operation_id,
                    claim["expected_wip_summary_digest"],
                    external,
                    workspace_path,
                )
                response = {
                    "workspace": {"state": "missing", **copy.deepcopy(external)}
                }
            else:
                recovery_state = self._validate_recover_result(
                    snapshot,
                    operation_id,
                    claim["expected_wip_summary_digest"],
                    external,
                )
                response = {
                    "workspace": {
                        "state": recovery_state,
                        **copy.deepcopy(external),
                    }
                }
        except (WorkspaceError, OSError, KeyError, TypeError) as exc:
            raise WorkspaceError(
                f"workspace.{operation_kind}-outcome-unknown",
                f"workspace {operation_kind} result could not be proven",
                retryable=True,
                mutation_state="unknown",
                operation_id=operation_id,
            ) from exc
        try:
            with self._lock:
                state = self._read_state()
                request = state["requests"].get(request_id)
                current = self._pending_binding_for_request(
                    state, request_id, operation_id
                )
                if request is None or current is None:
                    raise WorkspaceError(
                        "workspace.concurrent-change",
                        "workspace changed before outcome publication",
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                if self._pending_join_claim(
                    binding=current,
                    request=request,
                    workspace_path=workspace_path,
                ) != claim:
                    raise WorkspaceError(
                        "workspace.concurrent-change",
                        "workspace changed before outcome publication",
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                if operation_kind == "repair" or (
                    operation_kind == "recover"
                    and response["workspace"]["state"] == "ready"
                ):
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
                else:
                    del state["bindings"][key]
                    self._clear_pending_high_risk(current)
                    state["history"][key] = {
                        **current,
                        "state": "destroyed",
                        (
                            "destroy_journal"
                            if operation_kind == "destroy"
                            else "recovery_journal"
                        ): copy.deepcopy(external["journal"]),
                        (
                            "destroy_observation"
                            if operation_kind == "destroy"
                            else "recovery_observation"
                        ): copy.deepcopy(external["observation"]),
                    }
                request.update(
                    {"status": "completed", "result": copy.deepcopy(response)}
                )
                state["state_revision"] += 1
                self._write_state(state)
        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceError(
                f"workspace.{operation_kind}-outcome-unknown",
                f"workspace {operation_kind} outcome is pending reconciliation",
                retryable=True,
                mutation_state="unknown",
                operation_id=operation_id,
            ) from exc
        return operation_id, response

    @staticmethod
    def _pending_binding_for_request(
        state: Mapping[str, Any], request_id: str, operation_id: str
    ) -> dict[str, Any] | None:
        matches = [
            binding
            for binding in state["bindings"].values()
            if binding.get("pending_request_id") == request_id
            and binding.get("pending_operation_id") == operation_id
            and binding.get("state") in {"repairing", "destroying", "recovering"}
        ]
        if len(matches) > 1:
            raise WorkspaceError(
                "workspace.state-invalid",
                "workspace pending operation is duplicated",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        return matches[0] if matches else None

    @staticmethod
    def _workspace_path_identity_digest(
        workspace_path: Path, *, workspace_id: str
    ) -> str:
        path = Path(workspace_path)
        lexical = Path(os.path.abspath(os.fspath(path)))
        if (
            not isinstance(workspace_id, str)
            or not workspace_id.startswith("workspace-")
            or Path(workspace_id).name != workspace_id
            or lexical.name != workspace_id
        ):
            raise WorkspaceError(
                "workspace.path-invalid", "workspace container identity differs"
            )
        try:
            details = lexical.lstat()
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError(
                "workspace.path-invalid", "workspace container is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or resolved != lexical
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise WorkspaceError(
                "workspace.path-invalid", "workspace container ownership differs"
            )
        return canonical_json_sha256(
            {
                "workspace_id": workspace_id,
                "device": details.st_dev,
                "inode": details.st_ino,
                "uid": details.st_uid,
                "mode": stat.S_IMODE(details.st_mode),
            }
        )

    @staticmethod
    def _pending_fence_digest(binding: Mapping[str, Any]) -> str:
        operation_kind = {
            "repairing": "repair",
            "destroying": "destroy",
            "recovering": "recover",
        }.get(binding.get("state"))
        if operation_kind is None:
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace pending state differs",
                mutation_state="unknown",
            )
        try:
            material = {
                "project_instance_id": binding["project_instance_id"],
                "scenario_id": binding["scenario_id"],
                "scenario_generation": binding["scenario_generation"],
                "workspace_id": binding["workspace_id"],
                "operation_kind": operation_kind,
                "workspace_operation_id": binding["pending_operation_id"],
                "workspace_request_id": binding["pending_request_id"],
                "plan_digest": canonical_json_sha256(binding["plan"]),
                "receipt_digest": canonical_json_sha256(binding["receipt"]),
                "expected_wip_summary_digest": binding[
                    "pending_expected_wip_summary_digest"
                ],
                "force": bool(binding.get("pending_force", False)),
                "workspace_path_identity_digest": binding[
                    "pending_workspace_path_identity_digest"
                ],
                "adapter_capability_digest": binding[
                    "pending_adapter_capability_digest"
                ],
                "recovery_source_digest": (
                    canonical_json_sha256(binding["pending_recovery_source"])
                    if operation_kind == "recover"
                    else None
                ),
            }
        except (KeyError, TypeError) as exc:
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace pending proof is incomplete",
                mutation_state="unknown",
            ) from exc
        return canonical_json_sha256(material)

    def _pending_join_claim(
        self,
        *,
        binding: Mapping[str, Any],
        request: Mapping[str, Any],
        workspace_path: Path,
        require_persisted_claim: bool = True,
        require_current_capability: bool = True,
    ) -> dict[str, Any]:
        state_to_kind = {
            "repairing": "repair",
            "destroying": "destroy",
            "recovering": "recover",
        }
        operation_kind = state_to_kind.get(binding.get("state"))
        operation_id = binding.get("pending_operation_id")
        request_id = binding.get("pending_request_id")
        expected_wip = binding.get("pending_expected_wip_summary_digest")
        workspace_id = binding.get("workspace_id")
        if (
            operation_kind is None
            or not isinstance(operation_id, str)
            or not isinstance(request_id, str)
            or not isinstance(expected_wip, str)
            or not isinstance(workspace_id, str)
            or request.get("operation_id") != operation_id
            or request.get("operation_kind") != operation_kind
            or request.get("status") != "pending"
            or not isinstance(request.get("request_digest"), str)
        ):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace pending proof is incomplete",
                mutation_state="unknown",
                operation_id=(operation_id if isinstance(operation_id, str) else None),
            )
        capability_digest = binding.get("pending_adapter_capability_digest")
        if not isinstance(capability_digest, str):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace adapter capability proof is unavailable",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        if require_current_capability:
            capability = self._adapter_join_capability(operation_kind)
            if capability_digest != capability["capability_digest"]:
                raise WorkspaceError(
                    "workspace.join-unprovable",
                    "workspace adapter capability changed",
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
        path_digest = self._workspace_path_identity_digest(
            workspace_path, workspace_id=workspace_id
        )
        if (
            binding.get("pending_workspace_path_identity_digest") != path_digest
        ):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace ownership or adapter capability changed",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        fence_digest = self._pending_fence_digest(binding)
        if binding.get("pending_fence_digest") != fence_digest:
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace pending fence differs",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        claim = {
            "join_claim_version": 1,
            "workspace_operation_id": operation_id,
            "workspace_request_id": request_id,
            "request_digest": request["request_digest"],
            "operation_kind": operation_kind,
            "project_instance_id": binding.get("project_instance_id"),
            "scenario_id": binding.get("scenario_id"),
            "scenario_generation": binding.get("scenario_generation"),
            "workspace_id": workspace_id,
            "plan_digest": canonical_json_sha256(binding.get("plan")),
            "receipt_digest": canonical_json_sha256(binding.get("receipt")),
            "expected_wip_summary_digest": expected_wip,
            "force": bool(binding.get("pending_force", False)),
            "workspace_path_identity_digest": path_digest,
            "pending_fence_digest": fence_digest,
            "adapter_capability_digest": capability_digest,
            "recovery_source_digest": (
                canonical_json_sha256(
                    {
                        "prior_claim_digest": binding[
                            "pending_recovery_source"
                        ]["prior_claim"]["claim_digest"],
                        "expected_inventory_digest": binding[
                            "pending_recovery_source"
                        ]["expected_inventory_digest"],
                    }
                )
                if operation_kind == "recover"
                else canonical_json_sha256(None)
            ),
        }
        if (
            not isinstance(claim["project_instance_id"], str)
            or not isinstance(claim["scenario_id"], str)
            or not isinstance(claim["scenario_generation"], int)
            or isinstance(claim["scenario_generation"], bool)
        ):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace scenario fence differs",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        claim["claim_digest"] = canonical_json_sha256(claim)
        if require_persisted_claim and (
            binding.get("pending_join_claim_digest") != claim["claim_digest"]
        ):
            raise WorkspaceError(
                "workspace.join-unprovable",
                "workspace join claim is not durably bound",
                mutation_state="unknown",
                operation_id=operation_id,
            )
        return claim

    def _destroy_empty_husk(
        self,
        *,
        state: dict[str, Any],
        key: str,
        binding: dict[str, Any] | None,
        binding_state: str,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        workspace_path: Path,
        expected_husk_digest: str,
    ) -> tuple[str, dict[str, Any]]:
        """Tombstone an exact empty Scenario workspace without touching source/WIP."""

        workspace_id = (
            binding["workspace_id"] if binding is not None else workspace_path.name
        )
        observed_husk_digest = self._empty_husk_digest(
            workspace_path, workspace_id=workspace_id
        )
        if observed_husk_digest != expected_husk_digest:
            raise WorkspaceError(
                "workspace.stale-fence", "empty workspace evidence differs"
            )
        # The caller holds the coordinator lock. Re-read the exact record from
        # that same durable snapshot rather than treating an absent receipt as
        # proof that every non-ready state is disposable.
        current = state["bindings"].get(key)
        current_state = "absent" if current is None else current["state"]
        if current_state != binding_state or current_state not in {
            "absent",
            "planned",
            "provision_failed",
        }:
            raise WorkspaceError(
                "workspace.concurrent-change", "workspace changed during destroy"
            )
        if current is not None and (
            current["scenario_generation"] != scenario_generation
            or current["workspace_id"] != workspace_id
        ):
            raise WorkspaceError("workspace.stale-fence", "workspace fence differs")

        operation_id = f"wsop-{uuid.uuid4().hex}"
        evidence = {
            "unprovisioned_destroy_evidence_version": 1,
            "operation_id": operation_id,
            "operation_kind": "destroy-unprovisioned",
            "scenario": {
                "scenario_id": scenario_id,
                "scenario_generation": scenario_generation,
            },
            "workspace_id": workspace_id,
            "binding_state_before": binding_state,
            "husk_digest": observed_husk_digest,
            "events": [
                {
                    "sequence": 1,
                    "phase": "finalize",
                    "adapter_kind": "coordinator",
                    "step_id": "workspace.empty-husk-proven",
                    "target_id": workspace_id,
                    "state": "committed",
                    "evidence_digest": observed_husk_digest,
                    "error_code": None,
                }
            ],
        }
        evidence["evidence_digest"] = canonical_json_sha256(evidence)
        response = {
            "workspace": {
                "state": "missing",
                "unprovisioned_destroy_evidence": copy.deepcopy(evidence),
            }
        }
        if current is not None:
            del state["bindings"][key]
            historical = copy.deepcopy(current)
        else:
            historical = {
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
                "scenario_generation": scenario_generation,
                "workspace_id": workspace_id,
            }
        state["history"][key] = {
            **historical,
            "state": "destroyed",
            "binding_state_before_destroy": binding_state,
            "unprovisioned_destroy_evidence": copy.deepcopy(evidence),
        }
        self._record_request(
            state, request_id, request_digest, operation_id, response
        )
        state["state_revision"] += 1
        self._write_state(state)
        return operation_id, response

    @staticmethod
    def _empty_husk_digest(workspace_path: Path, *, workspace_id: str) -> str:
        """Prove a Host-owned Scenario container contains no deletable data."""

        if not isinstance(workspace_id, str) or (
            not workspace_id.startswith("workspace-")
            or Path(workspace_id).name != workspace_id
            or workspace_path.name != workspace_id
        ):
            raise WorkspaceError(
                "workspace.husk-invalid", "empty workspace container differs"
            )
        try:
            details = workspace_path.lstat()
            resolved = workspace_path.resolve(strict=True)
            entries = list(workspace_path.iterdir())
        except OSError as exc:
            raise WorkspaceError(
                "workspace.husk-invalid", "empty workspace cannot be inspected"
            ) from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or resolved != workspace_path
            or details.st_uid != os.getuid()
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise WorkspaceError(
                "workspace.husk-invalid", "empty workspace ownership differs"
            )
        if entries:
            raise WorkspaceError(
                "workspace.husk-not-empty",
                "unprovisioned workspace contains files and cannot be deleted",
            )
        return canonical_json_sha256(
            {
                "path_identity": {
                    "workspace_id": workspace_id,
                    "device": details.st_dev,
                    "inode": details.st_ino,
                    "uid": details.st_uid,
                    "mode": stat.S_IMODE(details.st_mode),
                },
                "entries": [],
            }
        )

    def _restore_ready_after_no_effect(
        self,
        *,
        key: str,
        request_id: str,
        operation_id: str,
        operation_kind: str,
        error: WorkspaceError,
    ) -> None:
        """Roll back only a coordinator claim proven not to have run."""

        expected_state = {
            "repair": "repairing",
            "destroy": "destroying",
            "recover": "recovering",
        }[operation_kind]
        with self._lock:
            state = self._read_state()
            current = state["bindings"].get(key)
            request = state["requests"].get(request_id)
            if (
                current is None
                or current.get("state") != expected_state
                or current.get("pending_request_id") != request_id
                or current.get("pending_operation_id") != operation_id
                or request is None
                or request.get("status") != "pending"
            ):
                raise WorkspaceError(
                    "workspace.concurrent-change",
                    "workspace no-effect recovery fence differs",
                    retryable=True,
                    mutation_state="unknown",
                    operation_id=operation_id,
                )
            terminal_recovery = None
            if operation_kind == "recover":
                source = current.get("pending_recovery_source")
                binding_before = (
                    source.get("binding_before")
                    if isinstance(source, dict)
                    else None
                )
                if not isinstance(binding_before, dict):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace recovery rollback evidence differs",
                        retryable=False,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                prior = source.get("prior_claim")
                last_claim_digest = current.get("pending_join_claim_digest")
                if (
                    not isinstance(prior, dict)
                    or prior.get("operation_kind") not in {"destroy", "repair"}
                    or not isinstance(prior.get("claim_digest"), str)
                    or not isinstance(last_claim_digest, str)
                ):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace recovery terminal evidence differs",
                        retryable=False,
                        mutation_state="unknown",
                        operation_id=operation_id,
                    )
                terminal_recovery = {
                    "terminal_recovery_version": 1,
                    "resolution": "not_started",
                    "workspace_operation_id": operation_id,
                    "project_instance_id": current["project_instance_id"],
                    "scenario_id": current["scenario_id"],
                    "scenario_generation": current["scenario_generation"],
                    "workspace_id": current["workspace_id"],
                    "prior_operation_kind": prior["operation_kind"],
                    "prior_claim_digest": prior["claim_digest"],
                    "last_recovery_claim_digest": last_claim_digest,
                    "reason": error.code,
                    "unjoinable": False,
                    "workspace_claim": None,
                }
                current.clear()
                current.update(copy.deepcopy(binding_before))
            else:
                current["state"] = "ready"
                current["error_code"] = None
                self._clear_pending_high_risk(current)
            request.update(
                {
                    "status": "failed",
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "retryable": error.retryable,
                        "mutation_state": "not_started",
                    },
                }
            )
            if terminal_recovery is not None:
                request["terminal_recovery"] = terminal_recovery
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _clear_pending_high_risk(binding: dict[str, Any]) -> None:
        for field in (
            "pending_request_id",
            "pending_operation_id",
            "pending_expected_wip_summary_digest",
            "pending_force",
            "pending_workspace_path_identity_digest",
            "pending_adapter_capability_digest",
            "pending_fence_digest",
            "pending_join_claim_digest",
            "pending_recovery_source",
            "pending_recover_external_attempted",
            "recovery_checkpoint",
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

    @staticmethod
    def _validate_recover_result(
        binding: Mapping[str, Any],
        operation_id: str,
        expected_wip_summary_digest: str,
        external: Mapping[str, Any],
    ) -> str:
        if set(external) != {
            "journal",
            "receipt",
            "observation",
            "review_snapshot",
            "recovery",
        }:
            raise WorkspaceError(
                "adapter.invalid-reply", "recovery result fields differ"
            )
        source = binding.get("pending_recovery_source")
        prior = source.get("prior_claim") if isinstance(source, Mapping) else None
        journal = external["journal"]
        observation = external["observation"]
        recovery = external["recovery"]
        if not all(
            isinstance(value, Mapping)
            for value in (prior, journal, observation, recovery)
        ):
            raise WorkspaceError(
                "adapter.invalid-reply", "recovery provenance differs"
            )
        resolution = recovery.get("resolution")
        if (
            set(recovery)
            != {
                "prior_operation_id",
                "prior_operation_kind",
                "prior_claim_digest",
                "resolution",
            }
            or recovery.get("prior_operation_id")
            != prior.get("workspace_operation_id")
            or recovery.get("prior_operation_kind")
            != prior.get("operation_kind")
            or recovery.get("prior_claim_digest") != prior.get("claim_digest")
            or resolution not in {"ready", "missing"}
            or journal.get("operation_id") != operation_id
            or journal.get("operation_kind") != "recover"
            or journal.get("plan_digest")
            != canonical_json_sha256(binding["plan"])
            or observation.get("operation_id") != operation_id
            or observation.get("operation_kind") != "recover"
            or observation.get("journal_digest")
            != canonical_json_sha256(journal)
            or observation.get("wip_summary_digest")
            != expected_wip_summary_digest
        ):
            raise WorkspaceError(
                "adapter.invalid-reply", "recovery provenance differs"
            )
        receipt = external["receipt"]
        snapshot = external["review_snapshot"]
        if resolution == "missing":
            if (
                prior.get("operation_kind") != "destroy"
                or
                receipt is not None
                or snapshot is not None
                or observation.get("state") != "missing"
                or observation.get("receipt_digest")
                != canonical_json_sha256(binding["receipt"])
            ):
                raise WorkspaceError(
                    "adapter.invalid-reply", "missing recovery proof differs"
                )
            try:
                bundle = Path(binding["pending_recovery_source"]["bundle_path"])
            except (KeyError, TypeError):
                bundle = None
            if bundle is not None and (bundle.exists() or bundle.is_symlink()):
                raise WorkspaceError(
                    "adapter.invalid-reply", "missing recovery target still exists"
                )
            return "missing"
        if not isinstance(receipt, Mapping) or not isinstance(snapshot, Mapping):
            raise WorkspaceError(
                "adapter.invalid-reply", "ready recovery artifacts differ"
            )
        snapshot_without_digest = dict(snapshot)
        snapshot_digest = snapshot_without_digest.pop("snapshot_digest", None)
        if (
            receipt.get("workspace_id") != binding["workspace_id"]
            or receipt.get("workspace_binding_digest")
            != binding["receipt"].get("workspace_binding_digest")
            or receipt.get("plan_digest")
            != canonical_json_sha256(binding["plan"])
            or observation.get("state")
            not in (
                {"aligned", "degraded"}
                if prior.get("operation_kind") == "destroy"
                and prior.get("force") is True
                else {"aligned"}
            )
            or observation.get("receipt_digest")
            != canonical_json_sha256(receipt)
            or snapshot.get("plan_digest")
            != canonical_json_sha256(binding["plan"])
            or snapshot.get("receipt_digest")
            != canonical_json_sha256(receipt)
            or snapshot_digest != canonical_json_sha256(snapshot_without_digest)
        ):
            raise WorkspaceError(
                "adapter.invalid-reply", "ready recovery proof differs"
            )
        return "ready"

    def is_ready(self, project_instance_id: str, scenario_id: str) -> bool:
        with self._lock:
            state = self._read_state()
            binding = state["bindings"].get(_binding_key(project_instance_id, scenario_id))
            return bool(binding is not None and binding["state"] == "ready")

    def inspect_terminal_recovery(
        self,
        *,
        workspace_request_id: str,
        request_digest: str,
        workspace_path: Path,
    ) -> dict[str, Any] | None:
        """Return a durable no-effect/retired recover terminal for Store join."""

        fields = {
            "terminal_recovery_version",
            "resolution",
            "workspace_operation_id",
            "project_instance_id",
            "scenario_id",
            "scenario_generation",
            "workspace_id",
            "prior_operation_kind",
            "prior_claim_digest",
            "last_recovery_claim_digest",
            "reason",
            "unjoinable",
            "workspace_claim",
        }
        with self._recovery_lock, self._lock:
            state = self._read_state()
            request = state["requests"].get(workspace_request_id)
            if request is None:
                return None
            if request.get("request_digest") != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            terminal = request.get("terminal_recovery")
            error = request.get("error")
            if terminal is None:
                return None
            if (
                request.get("status") != "failed"
                or request.get("operation_kind") != "recover"
                or not isinstance(error, dict)
                or not isinstance(terminal, dict)
                or set(terminal) != fields
                or terminal.get("terminal_recovery_version") != 1
                or terminal.get("resolution")
                not in {"not_started", "retired"}
                or terminal.get("workspace_operation_id")
                != request.get("operation_id")
                or terminal.get("resolution") == "not_started"
                and error.get("mutation_state") != "not_started"
                or terminal.get("resolution") == "retired"
                and error.get("mutation_state") != "unknown"
                or any(
                    not isinstance(terminal.get(field), str)
                    or not terminal[field]
                    for field in {
                        "workspace_operation_id",
                        "project_instance_id",
                        "scenario_id",
                        "workspace_id",
                    }
                )
                or terminal.get("prior_operation_kind")
                not in {"destroy", "repair"}
                or not isinstance(terminal.get("reason"), str)
                or ADAPTER_ERROR_CODE_RE.fullmatch(terminal["reason"]) is None
                or not isinstance(terminal.get("unjoinable"), bool)
                or any(
                    not isinstance(terminal.get(field), str)
                    or re.fullmatch(r"[0-9a-f]{64}", terminal[field]) is None
                    for field in {
                        "prior_claim_digest",
                        "last_recovery_claim_digest",
                    }
                )
                or not isinstance(terminal.get("scenario_generation"), int)
                or isinstance(terminal.get("scenario_generation"), bool)
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace terminal recovery evidence differs",
                    mutation_state="unknown",
                )
            terminal_claim = terminal.get("workspace_claim")
            if terminal["resolution"] == "not_started":
                if terminal_claim is not None or terminal["unjoinable"]:
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace no-effect terminal differs",
                        mutation_state="unknown",
                    )
            else:
                if not isinstance(terminal_claim, dict):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace retired claim is unavailable",
                        mutation_state="unknown",
                    )
                unsigned_claim = dict(terminal_claim)
                claim_digest = unsigned_claim.pop("claim_digest", None)
                if (
                    terminal_claim.get("operation_kind") != "recover"
                    or claim_digest
                    != terminal["last_recovery_claim_digest"]
                    or canonical_json_sha256(unsigned_claim) != claim_digest
                    or terminal_claim.get("workspace_operation_id")
                    != terminal["workspace_operation_id"]
                    or terminal_claim.get("project_instance_id")
                    != terminal["project_instance_id"]
                    or terminal_claim.get("scenario_id")
                    != terminal["scenario_id"]
                    or terminal_claim.get("scenario_generation")
                    != terminal["scenario_generation"]
                    or terminal_claim.get("workspace_id")
                    != terminal["workspace_id"]
                ):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace retired claim provenance differs",
                        mutation_state="unknown",
                    )
            key = _binding_key(
                terminal["project_instance_id"], terminal["scenario_id"]
            )
            binding = state["bindings"].get(key)
            if (
                not isinstance(binding, dict)
                or binding.get("scenario_generation")
                != terminal["scenario_generation"]
                or binding.get("workspace_id") != terminal["workspace_id"]
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace terminal recovery binding differs",
                    mutation_state="unknown",
                )
            checkpoint = binding.get("recovery_checkpoint")
            if binding.get("state") == "recovery_failed" and isinstance(
                checkpoint, dict
            ):
                prior = checkpoint.get("prior_claim")
                if (
                    terminal["resolution"] == "retired"
                    and (
                        checkpoint.get("last_recovery_request_id")
                        != workspace_request_id
                        or checkpoint.get("last_recovery_operation_id")
                        != terminal["workspace_operation_id"]
                        or checkpoint.get("last_recovery_claim_digest")
                        != terminal["last_recovery_claim_digest"]
                    )
                ):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace recovery checkpoint differs",
                        mutation_state="unknown",
                    )
            else:
                prior_request_id = binding.get("pending_request_id")
                prior_operation_id = binding.get("pending_operation_id")
                prior_request = (
                    state["requests"].get(prior_request_id)
                    if isinstance(prior_request_id, str)
                    else None
                )
                if not isinstance(prior_request, dict) or not isinstance(
                    prior_operation_id, str
                ):
                    raise WorkspaceError(
                        "workspace.recovery-unprovable",
                        "workspace prior terminal claim is unavailable",
                        mutation_state="unknown",
                    )
                prior = self._pending_join_claim(
                    binding=binding,
                    request=prior_request,
                    workspace_path=workspace_path,
                    require_current_capability=False,
                )
            prior = self._validated_frozen_prior_claim(
                binding=binding,
                workspace_path=workspace_path,
                value=prior,
            )
            if (
                prior["operation_kind"] != terminal["prior_operation_kind"]
                or prior["claim_digest"] != terminal["prior_claim_digest"]
            ):
                raise WorkspaceError(
                    "workspace.recovery-unprovable",
                    "workspace terminal prior claim differs",
                    mutation_state="unknown",
                )
            return copy.deepcopy(terminal)

    def has_exact_request(self, request_id: str, request_digest: str) -> bool:
        """Report whether this coordinator durably knows an exact request id."""

        with self._lock:
            state = self._read_state()
            request = state["requests"].get(request_id)
            if request is None:
                return False
            if request.get("request_digest") != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            return True

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

    def failed_no_effect_request(
        self,
        request_id: str,
        request_digest: str,
        operation_id: str,
    ) -> bool:
        """Prove this coordinator durably rolled back a typed no-effect call."""

        with self._lock:
            state = self._read_state()
            request = state["requests"].get(request_id)
            if request is None:
                return False
            if request.get("request_digest") != request_digest:
                raise WorkspaceError("ipc.request-reused", "request id was reused")
            error = request.get("error")
            if (
                request.get("operation_id") != operation_id
                or request.get("status") != "failed"
                or not isinstance(error, dict)
                or error.get("mutation_state") != "not_started"
            ):
                return False
            return not any(
                binding.get("pending_request_id") == request_id
                or binding.get("pending_operation_id") == operation_id
                for binding in state["bindings"].values()
            )

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
    def _discard_owned_stage(
        path: Path,
        workspace_path: Path,
        *,
        operation_id: str,
    ) -> bool:
        """Remove only the Host-owned failed staging tree; never follow links."""

        if (
            path.parent != workspace_path
            or path.name != f".stage-{operation_id}"
            or workspace_path.is_symlink()
            or not workspace_path.is_dir()
        ):
            return False
        if not path.exists() and not path.is_symlink():
            return True
        if path.is_symlink():
            return False
        try:
            details = path.stat()
            workspace_details = workspace_path.stat()
            if (
                path.is_dir()
                and details.st_uid == os.getuid()
                and workspace_details.st_uid == os.getuid()
            ):
                shutil.rmtree(path)
                WorkspaceCoordinator._fsync_directory(workspace_path)
        except OSError:
            return False
        return not path.exists() and not path.is_symlink()

    @classmethod
    def _load_pending_publish_result(
        cls,
        binding: Mapping[str, Any],
        request: Mapping[str, Any] | None,
        root: Path,
    ) -> dict[str, Any] | None:
        """Load only an exact private provision marker bound by durable state."""

        marker = root / ".ai-collab-harness-binding.json"
        try:
            operation_id = binding["plan"]["operation_id"]
            expected_request_status = (
                "pending" if binding.get("state") == "provisioning" else "failed"
            )
            if (
                not isinstance(binding.get("provision_request_id"), str)
                or not isinstance(request, Mapping)
                or request.get("operation_id") != operation_id
                or request.get("status") != expected_request_status
                or not isinstance(binding.get("pending_result_digest"), str)
            ):
                return None
            root_details = root.lstat()
            marker_details = marker.lstat()
            if (
                stat.S_ISLNK(root_details.st_mode)
                or not stat.S_ISDIR(root_details.st_mode)
                or root_details.st_uid != os.getuid()
                or stat.S_ISLNK(marker_details.st_mode)
                or not stat.S_ISREG(marker_details.st_mode)
                or marker_details.st_uid != os.getuid()
                or stat.S_IMODE(marker_details.st_mode) != 0o600
                or marker_details.st_size > MAX_ADAPTER_REPLY_BYTES
            ):
                return None
            raw = marker.read_bytes()
            if len(raw) > MAX_ADAPTER_REPLY_BYTES:
                return None
            result = json.loads(raw)
            if (
                not isinstance(result, dict)
                or canonical_json_sha256(result)
                != binding.get("pending_result_digest")
            ):
                return None
            cls._validate_ready_result(binding, result)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, WorkspaceError):
            return None
        return result

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
                # A durable pending claim may already have crossed the
                # adapter boundary. Treating it as not-started lets a caller
                # roll back Store state while an external destroy/repair is
                # still in flight.
                mutation_state="unknown",
                operation_id=request["operation_id"],
            )
        failure = request.get("error")
        if (
            isinstance(failure, dict)
            and isinstance(failure.get("code"), str)
            and isinstance(failure.get("message"), str)
            and isinstance(failure.get("retryable"), bool)
            and failure.get("mutation_state")
            in {"not_started", "started", "committed", "unknown"}
        ):
            raise WorkspaceError(
                failure["code"],
                failure["message"],
                retryable=failure["retryable"],
                mutation_state=failure["mutation_state"],
                operation_id=request["operation_id"],
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
