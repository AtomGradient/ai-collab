# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from ai_collab import cli as cli_main, participant_auth
from ai_collab.client import HarnessClient, HarnessClientError
from ai_collab.host import HarnessHost
from ai_collab.participant import (
    PARTICIPANT_START_TIMEOUT_SECONDS,
    ParticipantCoordinator,
    ParticipantError,
)
from ai_collab.participant_auth import ParticipantAuthStore
from ai_collab.project import ProjectError
from ai_collab.protocol import canonical_json_sha256
from ai_collab.security import SecurityCoordinator
from ai_collab.store import ScenarioStore, StoreError


PROJECT_ID = "project-one"
SCENARIO_ID = "scenario-one"
PARTICIPANT_ID = "participant-one"
_BUILTIN_COLLABORATION_REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "ai_collab_team_policies.json").read_text(
        encoding="utf-8"
    )
)
PROJECT_RENDER: dict[str, Any] = {
    "render_contract_version": 1,
    "source": {
        "kind": "fileless",
        "intent_schema_version": None,
        "source_digest": "1" * 64,
    },
    "project": {
        "project_key": PROJECT_ID,
        "product_contract_version": "1.0",
        "workspace_adapter_id": "workspace.test-v1",
        "environment_adapter_id": "environment.test-v1",
        "participant_driver_contract": 2,
        "collaboration_policy_schema": 1,
    },
    "repo_manifest": {"schema_version": 1, "project_key": PROJECT_ID, "repos": []},
    "repo_manifest_digest": "2" * 64,
    "gate": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
    "collaboration": {
        "kind": "builtin",
        "profile_id": "builtin.standard-v1",
        "registry_snapshot": _BUILTIN_COLLABORATION_REGISTRY,
        "registry_snapshot_digest": canonical_json_sha256(
            _BUILTIN_COLLABORATION_REGISTRY
        ),
    },
    "availability": {
        "status": "ready",
        "observations": [],
        "changes": [],
        "warnings": [],
    },
}
PROJECT_RENDER["availability"]["fingerprint"] = canonical_json_sha256(
    PROJECT_RENDER["availability"]
)
PROJECT_RENDER["render_digest"] = canonical_json_sha256(
    {key: value for key, value in PROJECT_RENDER.items() if key != "availability"}
)
PROJECT_DIGEST = PROJECT_RENDER["render_digest"]
CAPABILITY_DIGEST = "b" * 64
PROCESS_DIGEST = "c" * 64
WINDOW_DIGEST = "d" * 64
SESSION_DIGEST = "e" * 64
TOPOLOGY_DIGEST = "f" * 64
BOOT_DIGEST = "1" * 64
FENCE_DIGEST = "2" * 64
RESOURCE_DIGEST = "3" * 64


def test_participant_client_pythonpath_imports_product_module(
    tmp_path: Path,
) -> None:
    store = ParticipantAuthStore(tmp_path / "state", tmp_path / "host.sock")
    material = store.ensure(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        participant_generation=1,
        participant_state_revision=1,
    )

    module_root = Path(material["client_pythonpath"])
    assert module_root == Path(participant_auth.__file__).resolve().parents[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    environment["PYTHONPATH"] = str(module_root)
    completed = subprocess.run(
        [
            material["client_executable"],
            "-c",
            "import ai_collab.participant_client",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_collaboration_context_revision_tracks_semantic_changes_only(
    tmp_path: Path,
) -> None:
    store = ParticipantAuthStore(tmp_path / "state", tmp_path / "host.sock")

    def context(peers: list[dict[str, Any]]) -> dict[str, Any]:
        unsigned = {
            "schema_version": 1,
            "context_revision": 99,
            "scenario": {
                "project_instance_id": PROJECT_ID,
                "scenario_id": SCENARIO_ID,
                "scenario_generation": 1,
            },
            "participant": {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "assignments": [],
            },
            "peers": peers,
            "policy": None,
            "allowed_outbound": [],
            "reply_semantics": {
                "reply_expected_kinds": [],
                "terminal_kinds": [],
                "preserve_reply_to": True,
                "machine_ack_is_silent": True,
            },
        }
        return {**unsigned, "context_digest": canonical_json_sha256(unsigned)}

    first = store.ensure(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        participant_generation=1,
        participant_state_revision=1,
        collaboration_context=context([]),
    )
    path = Path(first["collaboration_context_path"])
    initial = json.loads(path.read_text(encoding="utf-8"))
    assert initial["context_revision"] == 1

    store.ensure(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        participant_generation=1,
        participant_state_revision=2,
        collaboration_context=context([]),
    )
    same_semantics = json.loads(path.read_text(encoding="utf-8"))
    assert same_semantics["context_revision"] == 1
    assert same_semantics["context_digest"] == initial["context_digest"]

    store.ensure(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        participant_generation=1,
        participant_state_revision=2,
        collaboration_context=context(
            [
                {
                    "participant_id": "reviewer",
                    "participant_generation": 1,
                    "assignments": ["collaboration.role:review"],
                }
            ]
        ),
    )
    changed = json.loads(path.read_text(encoding="utf-8"))
    assert changed["context_revision"] == 2
    assert changed["context_digest"] != initial["context_digest"]


def test_collaboration_context_rejects_cross_project_identity(
    tmp_path: Path,
) -> None:
    store = ParticipantAuthStore(tmp_path / "state", tmp_path / "host.sock")
    unsigned = {
        "schema_version": 1,
        "context_revision": 1,
        "scenario": {
            "project_instance_id": "project-other",
            "scenario_id": SCENARIO_ID,
            "scenario_generation": 1,
        },
        "participant": {
            "participant_id": PARTICIPANT_ID,
            "participant_generation": 1,
            "assignments": [],
        },
        "peers": [],
        "policy": None,
        "allowed_outbound": [],
        "reply_semantics": {
            "reply_expected_kinds": [],
            "terminal_kinds": [],
            "preserve_reply_to": True,
            "machine_ack_is_silent": True,
        },
    }
    with pytest.raises(
        ValueError, match="participant collaboration context differs"
    ):
        store.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            participant_generation=1,
            participant_state_revision=1,
            collaboration_context={
                **unsigned,
                "context_digest": canonical_json_sha256(unsigned),
            },
        )


def _runtime_descriptor() -> dict[str, Any]:
    return {
        "driver_kind": "runtime",
        "driver_id": "runtime.generic-process",
        "contract_version": 2,
        "implementation_ref": "implementation.test-process",
        "interaction_modes": ["tui", "headless"],
        "lifecycle_operations": [
            "create",
            "start",
            "stop",
            "health",
            "delivery_ack",
            "session_drift",
            "repair",
        ],
        "continuity_modes": ["explicit_recreate"],
        "supports_harness_process_binding": True,
        "supports_ready_ack": True,
        "supports_delivery_ack": True,
        "supports_session_drift_signal": True,
        "supports_vendor_session_identity": False,
        "vendor_lifecycle_surface": None,
        "optional_vendor_lifecycle_operations": [],
        "retention_modes": ["none", "harness_context"],
        "repair_modes": ["recreate_generation", "rebind_owned_process"],
        "error_namespace": "generic-runtime.error",
        "redaction_profile_ref": "redaction.local-private",
    }


def _presentation_descriptor() -> dict[str, Any]:
    return {
        "driver_kind": "presentation",
        "driver_id": "presentation.iterm2",
        "contract_version": 1,
        "implementation_ref": "implementation.test-iterm",
        "interaction_modes": ["tui"],
        "lifecycle_operations": [
            "permission_probe",
            "create_top_level",
            "focus",
            "close_exact",
            "health",
            "capture_geometry",
            "restore_geometry",
        ],
        "supports_stable_window_identity": True,
        "supports_stable_session_identity": True,
        "supports_exact_close": True,
        "supports_geometry": True,
        "supports_display_topology": True,
        "permission_model": "platform-plugin",
        "error_namespace": "iterm-presentation.error",
        "redaction_profile_ref": "redaction.local-private",
    }


class FakeDriver:
    def __init__(self) -> None:
        self.running = False
        self.running_bindings: set[str] = set()
        self.fail_start = False
        self.fail_start_participant_ids: set[str] = set()
        self.support_exact_resume = False
        self.pending_vendor_binding = False
        self.always_pending_vendor_binding = False
        self.close_vendor_session_identity_sha256: str | None = None
        self.start_calls = 0
        self.start_generations: list[int] = []
        self.start_participant_clients: list[dict[str, str]] = []
        self.start_working_directories: list[object] = []
        self.stop_calls: list[str] = []
        self.force_stop_calls: list[str] = []
        self.repair_calls: list[int] = []
        self.fail_stop = False
        self.fail_repair = False
        self.close_mode = "idle"
        self.close_calls = 0
        self.close_started = threading.Event()
        self.close_release: threading.Event | None = None
        self.start_started = threading.Event()
        self.start_release: threading.Event | None = None
        self.per_participant_resource_digests = False
        self.supervision_sequence = 0
        self.fail_supervision = False
        self.call_timeouts: list[tuple[str, float]] = []
        self.boot_digest = BOOT_DIGEST
        self.fence_digest = FENCE_DIGEST
        self.resource_digest = RESOURCE_DIGEST

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        self.call_timeouts.append((operation, timeout_seconds))
        runtime = _runtime_descriptor()
        if self.support_exact_resume:
            runtime.update(
                {
                    "continuity_modes": ["explicit_recreate", "exact_resume"],
                    "supports_vendor_session_identity": True,
                    "vendor_lifecycle_surface": "runtime-lifecycle.test-v1",
                    "optional_vendor_lifecycle_operations": [
                        "vendor_resume",
                        "vendor_bind",
                    ],
                    "retention_modes": [
                        "none",
                        "harness_context",
                        "vendor_binding",
                    ],
                }
            )
        presentation = _presentation_descriptor()
        registry = {
            "schema_version": 1,
            "participant_driver_contract_version": 2,
            "runtime_drivers": [runtime],
            "presentation_drivers": [presentation],
        }
        if operation == "list_templates":
            return {
                "templates": [
                    {
                        "template_id": "test-tui",
                        "display_name": "Test TUI",
                        "launch_spec": _launch_spec(),
                        "presentation_driver_id": "presentation.iterm2",
                    }
                ]
            }
        if operation == "environment_probe":
            return {
                "environment_observations": [
                    {
                        "subject_ref": "runtime-profile.inert",
                        "display_name": "Inert (test fixture)",
                        "status": "available",
                        "observed_version": "1.0",
                        "evidence_digest": "8" * 64,
                        "provider_error_code": None,
                        "remediation_ref": None,
                    }
                ]
            }
        if operation == "permission_probe":
            return {
                "permission_observations": [
                    {
                        "permission_id": "permission.presentation-control",
                        "provider_ref": "platform.test-presentation",
                        "subject_ref": "presentation.test-iterm",
                        "status": "granted",
                        "evidence_digest": "9" * 64,
                        "provider_error_code": None,
                        "remediation_ref": None,
                        "prompt_requested": False,
                    }
                ]
            }
        if operation == "resolve":
            return {
                "driver_registry": registry,
                "driver_registry_digest": canonical_json_sha256(registry),
                "runtime_descriptor": runtime,
                "presentation_descriptor": presentation,
                "capability_snapshot_digest": CAPABILITY_DIGEST,
            }
        if operation == "start":
            self.start_calls += 1
            self.start_started.set()
            if self.start_release is not None:
                assert self.start_release.wait(timeout=10)
            self.start_generations.append(payload["context"]["participant_generation"])
            self.start_participant_clients.append(
                dict(payload["participant_client"])
            )
            self.start_working_directories.append(
                payload.get("participant_working_directory")
            )
            if (
                self.fail_start
                or payload["context"]["participant_id"]
                in self.fail_start_participant_ids
            ):
                raise ParticipantError(
                    "driver.execution-failed", "injected participant start failure"
                )
            context = payload["context"]
            launch_spec = payload["launch_spec"]
            participant_id = context["participant_id"]
            runtime_binding_id = (
                "runtime-binding-one"
                if participant_id == PARTICIPANT_ID
                else f"runtime-binding-{participant_id}"
            )
            presentation_instance_id = (
                "presentation-instance-one"
                if participant_id == PARTICIPANT_ID
                else f"presentation-instance-{participant_id}"
            )
            runtime_binding = {
                "scenario_id": context["scenario_id"],
                "participant_id": context["participant_id"],
                "participant_generation": context["participant_generation"],
                "driver_id": launch_spec["driver_id"],
                "runtime_instance_id": "runtime-instance-one",
                "runtime_binding_id": runtime_binding_id,
                "process_instance_id": "process-instance-one",
                "process_identity_sha256": PROCESS_DIGEST,
                "continuity_mode": launch_spec["continuity_mode"],
                "vendor_session_identity_sha256": (
                    SESSION_DIGEST
                    if launch_spec["continuity_mode"] == "exact_resume"
                    and not (
                        self.always_pending_vendor_binding
                        or (
                            self.pending_vendor_binding and self.start_calls == 1
                        )
                    )
                    else None
                ),
                "private_driver_binding_ref": "runtime-private-one",
                "capability_snapshot_digest": context[
                    "capability_snapshot_digest"
                ],
            }
            presentation_binding = {
                "scenario_id": context["scenario_id"],
                "participant_id": context["participant_id"],
                "participant_generation": context["participant_generation"],
                "driver_id": "presentation.iterm2",
                "presentation_instance_id": presentation_instance_id,
                "runtime_binding_id": runtime_binding_id,
                "window_identity_sha256": WINDOW_DIGEST,
                "session_identity_sha256": SESSION_DIGEST,
                "private_driver_binding_ref": "presentation-private-one",
                "geometry": {"x": 10, "y": 20, "width": 1200, "height": 800},
                "display_topology_fingerprint": TOPOLOGY_DIGEST,
                "capability_snapshot_digest": context[
                    "capability_snapshot_digest"
                ],
            }
            self.running_bindings.add(runtime_binding_id)
            self.running = True
            return {
                "runtime_create_request": {
                    "context": context,
                    "launch_spec": launch_spec,
                },
                "prepared_runtime_launch": {
                    "context": context,
                    "driver_id": launch_spec["driver_id"],
                    "runtime_instance_id": "runtime-instance-one",
                    "private_launch_handle_ref": "launch-private-one",
                },
                "runtime_ready_ack": {
                    "context": context,
                    "binding": runtime_binding,
                    "ready": True,
                },
                "presentation_create_request": {
                    "context": context,
                    "presentation_driver_id": "presentation.iterm2",
                    "runtime_binding_id": runtime_binding_id,
                    "restore_geometry": None,
                    "display_topology_fingerprint": TOPOLOGY_DIGEST,
                },
                "presentation_create_ack": {
                    "context": context,
                    "binding": presentation_binding,
                    "geometry_restore_outcome": "not_requested",
                    "created": True,
                },
            }
        if operation == "repair":
            context = payload["context"]
            generation = context["participant_generation"]
            self.repair_calls.append(generation)
            if self.fail_repair:
                raise ParticipantError(
                    "driver.execution-failed", "injected participant repair failure"
                )
            runtime_ack = payload["runtime_ready_ack"]
            recovery_class = "pre_binding_absent"
            if runtime_ack is not None:
                binding_id = runtime_ack["binding"]["runtime_binding_id"]
                self.running_bindings.discard(binding_id)
                self.running = bool(self.running_bindings)
                recovery_class = "exact_binding_stopped"
            evidence = {
                "scenario_id": context["scenario_id"],
                "participant_id": context["participant_id"],
                "previous_participant_generation": generation,
                "next_participant_generation": payload[
                    "next_participant_generation"
                ],
                "recovery_class": recovery_class,
            }
            return {
                "recovered": True,
                "recovery_class": recovery_class,
                "previous_participant_generation": generation,
                "next_participant_generation": payload[
                    "next_participant_generation"
                ],
                "external_resources_absent": True,
                "private_generation_retained": True,
                "owned_resource_evidence_sha256": canonical_json_sha256(
                    evidence
                ),
            }
        runtime_ack = payload["runtime_ready_ack"]
        presentation_ack = payload["presentation_create_ack"]
        if operation == "supervise":
            binding_id = runtime_ack["binding"]["runtime_binding_id"]
            if self.fail_supervision or binding_id not in self.running_bindings:
                raise ParticipantError(
                    "driver.execution-failed", "injected supervision failure"
                )
            self.supervision_sequence += 1
            observation = {
                "schema_version": 1,
                "runtime_binding_id": runtime_ack["binding"][
                    "runtime_binding_id"
                ],
                "process_start_identity_sha256": runtime_ack["binding"][
                    "process_identity_sha256"
                ],
                "boot_id_sha256": self.boot_digest,
                "heartbeat_sequence": self.supervision_sequence,
                "heartbeat_at_unix_ms": 1_786_435_200_000
                + self.supervision_sequence,
                "fencing_token_sha256": self.fence_digest,
                "resources": [
                    {
                        "resource_class": "exclusive_runtime",
                        "resource_identity_sha256": (
                            hashlib.sha256(
                                runtime_ack["binding"]["participant_id"].encode(
                                    "utf-8"
                                )
                            ).hexdigest()
                            if self.per_participant_resource_digests
                            else self.resource_digest
                        ),
                        "state": "held",
                    }
                ],
            }
            return {
                **observation,
                "observation_evidence_sha256": canonical_json_sha256(
                    observation
                ),
            }
        if operation == "status":
            return {
                "healthy": runtime_ack["binding"]["runtime_binding_id"]
                in self.running_bindings,
                "runtime_binding_id": runtime_ack["binding"]["runtime_binding_id"],
                "presentation_binding_id": presentation_ack["binding"][
                    "presentation_instance_id"
                ],
            }
        if operation == "presentation_action":
            return {
                "presentation": {
                    "participant_generation": payload[
                        "presentation_create_ack"
                    ]["context"]["participant_generation"],
                    "presentation_instance_id": presentation_ack["binding"][
                        "presentation_instance_id"
                    ],
                    "health": "ready",
                    "focused": payload["action"] == "focus",
                    "restore_outcome": (
                        "applied_exact"
                        if payload["action"] == "focus"
                        else "not_requested"
                    ),
                    "geometry": {
                        "x": 10,
                        "y": 20,
                        "width": 1200,
                        "height": 800,
                    },
                    "display_topology_fingerprint": TOPOLOGY_DIGEST,
                }
            }
        if operation in {"stop", "force_stop"}:
            binding_id = runtime_ack["binding"]["runtime_binding_id"]
            (
                self.force_stop_calls
                if operation == "force_stop"
                else self.stop_calls
            ).append(binding_id)
            if self.fail_stop:
                raise ParticipantError(
                    "driver.execution-failed", "injected participant stop failure"
                )
            self.running_bindings.discard(binding_id)
            self.running = bool(self.running_bindings)
            result = {
                "stopped": True,
                "owned_resource_evidence_sha256": canonical_json_sha256(
                    {"stopped": True}
                ),
            }
            if self.close_vendor_session_identity_sha256 is not None:
                result["vendor_session_identity_sha256"] = (
                    self.close_vendor_session_identity_sha256
                )
            return result
        if operation == "close":
            self.close_calls += 1
            self.close_started.set()
            if self.close_release is not None:
                assert self.close_release.wait(timeout=5)
            mode = self.close_mode
            closed = mode in {"idle", "busy", "requested"}
            if closed:
                self.running_bindings.discard(
                    runtime_ack["binding"]["runtime_binding_id"]
                )
                self.running = bool(self.running_bindings)
            result = {
                "classification": mode,
                "closed": closed,
                "action_outcome_known": True,
                "drain_requested": mode in {"busy", "timeout"},
                "progress_event_count": 2 if mode == "busy" else (1 if mode == "timeout" else 0),
                "runtime_binding_id": runtime_ack["binding"]["runtime_binding_id"],
                "presentation_binding_id": presentation_ack["binding"][
                    "presentation_instance_id"
                ],
                "owned_resource_evidence_sha256": canonical_json_sha256(
                    {"mode": mode, "closed": closed}
                ),
                "owner": payload["context"]["participant_id"],
                "command": payload["launch_spec"]["runtime_profile_ref"],
                "started_at_unix_ms": 1_786_435_200_000,
            }
            if self.close_vendor_session_identity_sha256 is not None:
                result["vendor_session_identity_sha256"] = (
                    self.close_vendor_session_identity_sha256
                )
            return result
        raise AssertionError(operation)


class FakeSecurityAdapter:
    def __init__(self) -> None:
        self.present_calls = 0

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        del timeout_seconds
        if operation == "observe":
            return {
                "observations": [
                    {
                        "permission_id": permission_id,
                        "subject_digest": "6" * 64,
                        "status": "granted",
                        "observed_at_epoch_ms": payload["captured_at_epoch_ms"],
                        "valid_until_epoch_ms": payload["captured_at_epoch_ms"]
                        + 10_000,
                        "evidence_digest": "7" * 64,
                        "provider_error_code": None,
                        "remediation_ref": None,
                    }
                    for permission_id in payload["permission_ids"]
                ]
            }
        self.present_calls += 1
        challenge = payload["challenge"]
        return {
            "challenge_digest": canonical_json_sha256(challenge),
            "outcome": "approved",
            "decided_at_epoch_ms": challenge["issued_at_epoch_ms"],
            "presenter_instance_digest": "8" * 64,
            "decision_evidence_digest": "9" * 64,
            "reason_code": None,
        }


@contextmanager
def running_host(
    state_root: Path,
    *,
    with_security: bool = False,
    existing_binding_error: ProjectError | None = None,
) -> Iterator[tuple[HarnessHost, HarnessClient, FakeDriver]]:
    with tempfile.TemporaryDirectory(prefix="ai-collab-m3-") as runtime:
        socket_path = Path(runtime) / "host.sock"
        host = HarnessHost(state_root, socket_path)
        host.projects.validate_binding = lambda _project, _digest: None  # type: ignore[method-assign]
        if existing_binding_error is None:
            host.projects.validate_existing_binding = (  # type: ignore[method-assign]
                lambda _project, _digest: None
            )
        else:
            def fail_existing_binding(_project: str, _digest: str) -> None:
                raise existing_binding_error

            host.projects.validate_existing_binding = fail_existing_binding  # type: ignore[method-assign]
        host.projects.resolved_render = (  # type: ignore[method-assign]
            lambda _project, digest=None: PROJECT_RENDER
            if digest in {None, PROJECT_DIGEST}
            else None
        )
        driver = FakeDriver()
        host.participants = ParticipantCoordinator(host.store, driver)  # type: ignore[arg-type]
        if with_security:
            host.security = SecurityCoordinator(  # type: ignore[arg-type]
                state_root, FakeSecurityAdapter()
            )
        host.bind()
        thread = threading.Thread(target=host.serve_forever, daemon=True)
        thread.start()
        try:
            yield host, HarnessClient(state_root, socket_path), driver
        finally:
            host.shutdown()
            thread.join(timeout=3)


def _launch_spec(*, continuity_mode: str = "explicit_recreate") -> dict[str, Any]:
    return {
        "driver_id": "runtime.generic-process",
        "driver_contract_version": 2,
        "interaction_mode": "tui",
        "continuity_mode": continuity_mode,
        "runtime_profile_ref": "runtime-profile.inert",
        "model_binding": None,
        "continuity_binding_ref": (
            "binding:exact-test" if continuity_mode == "exact_resume" else None
        ),
    }


def _add_test_participant(
    client: HarnessClient,
    *,
    scenario: Mapping[str, Any],
    participant_id: str,
    request_prefix: str,
    launch_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return client.add_participant(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=participant_id,
        scenario_generation=scenario["scenario_generation"],
        scenario_state_revision=scenario["state_revision"],
        launch_spec=launch_spec or _launch_spec(),
        presentation_driver_id="presentation.iterm2",
        request_id=f"{request_prefix}-add",
    )["participant"]


def _start_test_participant(
    client: HarnessClient,
    *,
    scenario: Mapping[str, Any],
    participant: Mapping[str, Any],
    participant_id: str,
    request_prefix: str,
) -> dict[str, Any]:
    return client.start_participant(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=participant_id,
        scenario_generation=scenario["scenario_generation"],
        scenario_state_revision=scenario["state_revision"],
        participant_generation=participant["participant_generation"],
        participant_state_revision=participant["state_revision"],
        request_id=f"{request_prefix}-start",
    )["participant"]


def test_a_detached_record_is_not_a_dead_end(tmp_path: Path) -> None:
    """A record left detached by an older Host must still be startable.

    Detach used to end a participant permanently: start required "stopped",
    replace and recover rejected it, and the name stayed taken so it could not
    be added again. The operation is gone, but records written by earlier
    versions remain, and they have to lead somewhere.
    """
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="detached-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="detached",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="detached-open",
        )["scenario"]

        # Stand in for a record an older Host left behind: no binding, no
        # runtime artifacts, but the declaration and its launch_spec intact.
        state = json.loads((state_root / "host-state.json").read_text())
        key = next(iter(state["scenarios"]))
        record = state["scenarios"][key]["participants"][PARTICIPANT_ID]
        record["observed_state"] = "detached"
        record["desired_state"] = "detached"
        record["runtime_binding_id"] = None
        record["presentation_binding_id"] = None
        artifact = state["scenarios"][key]["participant_artifacts"][PARTICIPANT_ID]
        for field in (
            "runtime_create_request",
            "prepared_runtime_launch",
            "runtime_ready_ack",
            "presentation_create_request",
            "presentation_create_ack",
        ):
            artifact[field] = None
        (state_root / "host-state.json").write_text(json.dumps(state))

        assert record["observed_state"] == "detached"
        assert artifact["launch_spec"] is not None

        started = _start_test_participant(
            client,
            scenario=opened,
            participant=record,
            participant_id=PARTICIPANT_ID,
            request_prefix="detached-revive",
        )
        assert started["observed_state"] == "ready"
        assert started["runtime_binding_id"] is not None


def test_real_ipc_participant_add_start_status_stop(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="create",
        )["scenario"]
        added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="add",
        )["participant"]
        assert added["participant_generation"] == 1
        assert added["state_revision"] == 1
        assert added["observed_state"] == "stopped"
        assert client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        ) == {
            "participants": [{**added, "issued_objective_revision": 0}],
            "participant_configurations": [
                {
                    "participant_id": PARTICIPANT_ID,
                    "participant_generation": 1,
                    "runtime_profile_ref": "runtime-profile.inert",
                    "continuity_mode": "explicit_recreate",
                    "model_binding": None,
                }
            ],
        }
        assert client.list_participant_templates()["templates"][0][
            "template_id"
        ] == "test-tui"
        preflight = client.scenario_preflight(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["preflight"]
        assert preflight["permission_observations"] == [
            {
                "permission_id": "permission.presentation-control",
                "provider_ref": "platform.test-presentation",
                "subject_ref": "presentation.test-iterm",
                "status": "granted",
                "evidence_digest": "9" * 64,
                "provider_error_code": None,
                "remediation_ref": None,
                "prompt_requested": False,
            }
        ]
        assert next(
            value
            for value in preflight["checks"]
            if value["check_id"] == "presentation.permission"
        )["status"] == "ready"

        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="open",
        )["scenario"]
        ready = client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=added["participant_generation"],
            participant_state_revision=added["state_revision"],
            request_id="start",
        )["participant"]
        assert ready["observed_state"] == "ready"
        assert ready["state_revision"] == 3
        assert ready["runtime_binding_id"] == "runtime-binding-one"
        assert ready["presentation_binding_id"] == "presentation-instance-one"
        assert driver.running
        assert ("start", PARTICIPANT_START_TIMEOUT_SECONDS) in driver.call_timeouts
        contexts = list((state_root / "participant-contexts").glob("*.json"))
        assert contexts == [
            Path(driver.start_participant_clients[-1]["context_path"])
        ]
        assert stat.S_IMODE(contexts[0].stat().st_mode) == 0o600
        scoped_context = json.loads(contexts[0].read_text(encoding="utf-8"))
        assert scoped_context["participant_generation"] == 1
        assert scoped_context["participant_state_revision"] == ready[
            "state_revision"
        ]
        collaboration_path = Path(
            driver.start_participant_clients[-1]["collaboration_context_path"]
        )
        assert collaboration_path.parent == state_root / "participant-collaboration"
        assert stat.S_IMODE(collaboration_path.stat().st_mode) == 0o600
        collaboration = json.loads(
            collaboration_path.read_text(encoding="utf-8")
        )
        assert collaboration["scenario"]["scenario_id"] == SCENARIO_ID
        assert collaboration["participant"] == {
            "participant_id": PARTICIPANT_ID,
            "participant_generation": 1,
            "assignments": [],
        }
        assert collaboration["reply_semantics"]["machine_ack_is_silent"] is True
        assert collaboration["context_digest"] == canonical_json_sha256(
            {
                key: value
                for key, value in collaboration.items()
                if key != "context_digest"
            }
        )
        active_lease = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert active_lease["status"] == "active"
        assert active_lease["resource_class"] == "exclusive_runtime"
        assert active_lease["heartbeat_sequence"] == 1
        assert active_lease["holder"]["runtime_binding_id"] == ready[
            "runtime_binding_id"
        ]
        assert client.participant_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=ready["participant_generation"],
            participant_state_revision=ready["state_revision"],
        )["participant"] == ready
        topology = client.scenario_topology(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["topology"]
        assert topology["action"] == "inspect"
        assert topology["participants"] == [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": ready["participant_generation"],
                "interaction_mode": "tui",
                "health": "ready",
                "focused": False,
                "restore_outcome": "not_requested",
                "geometry": {
                    "x": 10,
                    "y": 20,
                    "width": 1200,
                    "height": 800,
                },
                "display_topology_fingerprint": TOPOLOGY_DIGEST,
                "error_code": None,
            }
        ]
        current = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        focused = client.focus_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=current["scenario_generation"],
            scenario_state_revision=current["state_revision"],
            request_id="focus",
        )["topology"]
        assert focused["action"] == "focus"
        assert focused["participants"][0]["focused"] is True
        assert focused["participants"][0]["restore_outcome"] == "applied_exact"
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=ready["participant_generation"],
            participant_state_revision=ready["state_revision"],
            request_id="stop",
        )["participant"]
        assert stopped["observed_state"] == "stopped"
        assert stopped["state_revision"] == 5
        assert stopped["runtime_binding_id"] is None
        assert stopped["presentation_binding_id"] is None
        assert not driver.running
        assert list((state_root / "participant-contexts").glob("*.json")) == []
        released_lease = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert released_lease["status"] == "released"
        assert released_lease["release_evidence_sha256"] is not None

        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        participant = next(iter(durable["scenarios"].values()))["participants"][
            PARTICIPANT_ID
        ]
        assert participant == stopped
        operations = {
            item["operation_kind"]: item for item in durable["operations"].values()
        }
        assert operations["participant.add"]["state"] == "desired_committed"
        assert operations["participant.start"]["state"] == "succeeded"
        assert operations["participant.stop"]["state"] == "succeeded"


def test_objective_issuance_stays_pending_until_participant_restart(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, _):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            objective="Ship the first objective",
            request_id="objective-issuance-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="objective-issuance",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="objective-issuance-open",
        )["scenario"]
        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="objective-issuance",
        )
        listed = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["participants"][0]
        assert listed["issued_objective_revision"] == 1

        revised = client.append_scenario_objective(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            objective="Ship the revised objective",
            acceptance_criteria="The participant receives revision two",
            request_id="objective-issuance-revise",
        )["scenario"]
        listed = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["participants"][0]
        assert len(revised["objective_history"]) == 2
        assert listed["issued_objective_revision"] == 1

        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=revised["scenario_generation"],
            scenario_state_revision=revised["state_revision"],
            participant_generation=ready["participant_generation"],
            participant_state_revision=ready["state_revision"],
            request_id="objective-issuance-stop",
        )["participant"]
        restarted = _start_test_participant(
            client,
            scenario=revised,
            participant=stopped,
            participant_id=PARTICIPANT_ID,
            request_prefix="objective-issuance-restart",
        )
        assert restarted["observed_state"] == "ready"
        listed = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["participants"][0]
        assert listed["issued_objective_revision"] == 2


def test_participant_list_projects_current_nonsecret_model_binding(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, _):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="create-model-view",
        )["scenario"]
        launch_spec = _launch_spec()
        launch_spec["model_binding"] = {
            "provider_profile_ref": "provider.test-local",
            "model_ref": "model.test-current",
            "inference_profile_ref": "inference.research",
        }
        client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            launch_spec=launch_spec,
            presentation_driver_id="presentation.iterm2",
            request_id="add-model-view",
        )
        result = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )
        assert result["participant_configurations"] == [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "runtime_profile_ref": "runtime-profile.inert",
                "continuity_mode": "explicit_recreate",
                "model_binding": launch_spec["model_binding"],
            }
        ]


def test_preflight_fails_closed_on_malformed_permission_observation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="preflight-malformed-create",
        )["scenario"]
        client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="preflight-malformed-add",
        )
        original_call = driver.call

        def malformed(
            operation: str,
            payload: Mapping[str, Any],
            *,
            timeout_seconds: float = 300,
        ) -> dict[str, Any]:
            if operation == "permission_probe":
                return {"permission_observations": [{"status": []}]}
            return original_call(
                operation, payload, timeout_seconds=timeout_seconds
            )

        driver.call = malformed  # type: ignore[method-assign]
        preflight = client.scenario_preflight(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["preflight"]
        presentation = next(
            value
            for value in preflight["checks"]
            if value["check_id"] == "presentation.permission"
        )
        assert presentation == {
            "check_id": "presentation.permission",
            "status": "blocked",
            "summary": "Presentation permission could not be observed.",
            "repair_action": "scenario.preflight",
        }
        assert preflight["permission_observations"] == []


def test_participant_replace_rotates_exactly_once_and_preserves_run_intent(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, ready = _start_ready_participant(client)
        replacement_spec = {
            **_launch_spec(),
            "runtime_profile_ref": "runtime-profile.replacement",
        }
        replaced = client.replace_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=ready["participant_generation"],
            participant_state_revision=ready["state_revision"],
            launch_spec=replacement_spec,
            presentation_driver_id="presentation.iterm2",
            request_id="replace-running",
        )["participant"]

        assert replaced["participant_generation"] == 2
        assert replaced["desired_state"] == "running"
        assert replaced["observed_state"] == "ready"
        assert replaced["state_revision"] == ready["state_revision"] + 3
        assert driver.stop_calls == [ready["runtime_binding_id"]]
        assert driver.start_generations[-1] == 2
        assert driver.call_timeouts.count(
            ("start", PARTICIPANT_START_TIMEOUT_SECONDS)
        ) == 2
        contexts = list((state_root / "participant-contexts").glob("*.json"))
        assert len(contexts) == 1
        context = json.loads(contexts[0].read_text(encoding="utf-8"))
        assert context["participant_generation"] == 2
        assert context["participant_state_revision"] == replaced[
            "state_revision"
        ]

        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        artifact = item["participant_artifacts"][PARTICIPANT_ID]
        assert artifact["launch_spec"] == replacement_spec
        replacement_history = [
            value
            for value in artifact["history"]
            if "replacement_cleanup" in value
        ]
        assert replacement_history[0]["participant_generation"] == 1
        operation = next(
            value
            for value in durable["operations"].values()
            if value["operation_kind"] == "participant.replace"
        )
        assert operation["state"] == "succeeded"
        assert operation["resulting_participant_generation"] == 2
        assert operation["replacement_launch_spec_digest"] == (
            canonical_json_sha256(replacement_spec)
        )


def test_participant_replace_validates_before_mutation_and_retains_old_generation_on_cleanup_failure(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, ready = _start_ready_participant(client)
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError) as unsupported:
            client.replace_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=ready["participant_generation"],
                participant_state_revision=ready["state_revision"],
                launch_spec=_launch_spec(continuity_mode="exact_resume"),
                presentation_driver_id="presentation.iterm2",
                request_id="replace-unsupported",
            )
        assert unsupported.value.code == "operation.precondition-failed"
        assert (state_root / "host-state.json").read_bytes() == before
        assert driver.stop_calls == []

        driver.fail_stop = True
        replacement_spec = {
            **_launch_spec(),
            "runtime_profile_ref": "runtime-profile.replacement",
        }
        with pytest.raises(HarnessClientError) as failed:
            client.replace_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=ready["participant_generation"],
                participant_state_revision=ready["state_revision"],
                launch_spec=replacement_spec,
                presentation_driver_id="presentation.iterm2",
                request_id="replace-cleanup-failed",
            )
        assert failed.value.code == "operation.external-failure"
        assert failed.value.category == "operation"
        assert failed.value.mutation_state == "committed"
        assert failed.value.repair_action == "participant.recover"
        after = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert after["participant_generation"] == 1
        assert after["launch_spec_digest"] == ready["launch_spec_digest"]
        assert after["runtime_binding_id"] == ready["runtime_binding_id"]
        assert after["desired_state"] == "running"
        assert after["observed_state"] == "degraded"
        assert after["degraded"]["cleanup_pending"] is True
        assert driver.start_generations == [1]


def test_participant_replace_keeps_stopped_intent_and_post_cas_failure_is_new_generation_degraded(
    tmp_path: Path,
) -> None:
    stopped_root = tmp_path / "stopped-state"
    with running_host(stopped_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="replace-stopped-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="replace-stopped",
        )
        replacement_spec = {
            **_launch_spec(),
            "runtime_profile_ref": "runtime-profile.replacement",
        }
        replaced = client.replace_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            participant_generation=added["participant_generation"],
            participant_state_revision=added["state_revision"],
            launch_spec=replacement_spec,
            presentation_driver_id="presentation.iterm2",
            request_id="replace-stopped",
        )["participant"]
        assert replaced["participant_generation"] == 2
        assert replaced["desired_state"] == "stopped"
        assert replaced["observed_state"] == "stopped"
        assert driver.stop_calls == []
        assert driver.start_calls == 0

    failed_root = tmp_path / "post-cas-state"
    with running_host(failed_root) as (_, client, driver):
        opened, ready = _start_ready_participant(client)
        driver.fail_start = True
        with pytest.raises(HarnessClientError) as failed:
            client.replace_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=ready["participant_generation"],
                participant_state_revision=ready["state_revision"],
                launch_spec={
                    **_launch_spec(),
                    "runtime_profile_ref": "runtime-profile.replacement",
                },
                presentation_driver_id="presentation.iterm2",
                request_id="replace-post-cas-failed",
            )
        assert failed.value.code == "operation.external-failure"
        after = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert after["participant_generation"] == 2
        assert after["desired_state"] == "running"
        assert after["observed_state"] == "degraded"
        assert after["runtime_binding_id"] is None
        assert driver.stop_calls == [ready["runtime_binding_id"]]
        assert driver.start_generations == [1, 2]
        assert list((failed_root / "participant-contexts").glob("*.json")) == []


def test_real_ipc_participant_can_be_added_and_started_while_scenario_runs(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        dynamic_id = "participant-two"
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="dynamic-create",
        )["scenario"]
        initial_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="dynamic-initial",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="dynamic-open",
        )["scenario"]
        initial_ready = _start_test_participant(
            client,
            scenario=opened,
            participant=initial_added,
            participant_id=PARTICIPANT_ID,
            request_prefix="dynamic-initial",
        )
        driver.resource_digest = "4" * 64

        added = _add_test_participant(
            client,
            scenario=opened,
            participant_id=dynamic_id,
            request_prefix="dynamic",
        )
        assert added["observed_state"] == "stopped"
        assert added["desired_state"] == "stopped"

        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=dynamic_id,
            request_prefix="dynamic",
        )
        assert ready["observed_state"] == "ready"
        assert ready["runtime_binding_id"] == "runtime-binding-participant-two"
        assert driver.running

        initial_after = client.participant_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=initial_ready["participant_generation"],
            participant_state_revision=initial_ready["state_revision"],
        )["participant"]
        assert initial_after == initial_ready

        status = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert status["observed_state"] == "running"
        assert status["participant_ids"] == [PARTICIPANT_ID, dynamic_id]


def test_participant_identity_is_scoped_by_scenario_not_runtime_or_role(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    scenario_ids = ("identity-scenario-a", "identity-scenario-b")
    with running_host(state_root) as (_, client, _):
        added: list[dict[str, Any]] = []
        for scenario_id in scenario_ids:
            created = client.create_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=scenario_id,
                project_binding_digest=PROJECT_DIGEST,
                request_id=f"identity-create-{scenario_id}",
            )["scenario"]
            participant = client.add_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=scenario_id,
                participant_id=PARTICIPANT_ID,
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                launch_spec=_launch_spec(),
                presentation_driver_id="presentation.iterm2",
                request_id=f"identity-add-{scenario_id}",
            )["participant"]
            added.append(participant)

        assert {
            (value["scenario_id"], value["participant_id"]) for value in added
        } == {(scenario_id, PARTICIPANT_ID) for scenario_id in scenario_ids}
        assert all(value["participant_generation"] == 1 for value in added)
        assert all("role" not in value and "product" not in value for value in added)
        assert all(
            client.scenario_status(
                project_instance_id=PROJECT_ID,
                scenario_id=scenario_id,
            )["scenario"]["participant_ids"]
            == [PARTICIPANT_ID]
            for scenario_id in scenario_ids
        )


def test_participant_start_failure_does_not_rebind_ready_sibling(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    sibling_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="fault-create",
        )["scenario"]
        first_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="fault-first",
        )
        sibling_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=sibling_id,
            request_prefix="fault-sibling",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="fault-open",
        )["scenario"]
        first_ready = _start_test_participant(
            client,
            scenario=opened,
            participant=first_added,
            participant_id=PARTICIPANT_ID,
            request_prefix="fault-first",
        )
        driver.fail_start = True

        with pytest.raises(HarnessClientError) as exc:
            _start_test_participant(
                client,
                scenario=opened,
                participant=sibling_added,
                participant_id=sibling_id,
                request_prefix="fault-sibling",
            )
        assert exc.value.code == "operation.external-failure"

        participants = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["diagnostic"]["participants"]
        first_after = next(
            value
            for value in participants
            if value["participant_id"] == PARTICIPANT_ID
        )
        assert first_after == first_ready
        assert first_after["observed_state"] == "ready"
        assert first_after["runtime_binding_id"] in driver.running_bindings
        sibling = next(
            value
            for value in participants
            if value["participant_id"] == sibling_id
        )
        assert sibling["observed_state"] == "degraded"
        assert sibling["runtime_binding_id"] is None
        assert list((state_root / "participant-contexts").glob("*.json")) == [
            Path(driver.start_participant_clients[0]["context_path"])
        ]


def test_resource_supervision_renews_stales_and_reactivates_only_exact_holder(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        _, ready = _start_ready_participant(client)
        first = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert host.run_resource_supervision_once() == {
            "observed": 1,
            "stale": 0,
        }
        renewed = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert renewed["status"] == "active"
        assert renewed["lease_revision"] == first["lease_revision"] + 1
        assert renewed["heartbeat_sequence"] == first["heartbeat_sequence"] + 1

        driver.fail_supervision = True
        assert host.run_resource_supervision_once() == {
            "observed": 0,
            "stale": 1,
        }
        stale = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert stale["status"] == "stale"
        assert stale["stale_reason"] == "observation_failed"
        assert driver.running is True

        driver.fail_supervision = False
        host.run_resource_supervision_once()
        recovered = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert recovered["status"] == "active"
        assert recovered["holder"]["runtime_binding_id"] == ready[
            "runtime_binding_id"
        ]

        driver.boot_digest = "4" * 64
        host.run_resource_supervision_once()
        drifted = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert drifted["status"] == "stale"
        assert drifted["stale_reason"] == "binding_changed"
        assert drifted["boot_id_sha256"] == BOOT_DIGEST
        assert driver.running is True

        driver.boot_digest = BOOT_DIGEST
        host.run_resource_supervision_once()
        exact = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert exact["status"] == "active"
        rendered = json.dumps(exact, sort_keys=True)
        assert '"pid":' not in rendered
        assert "private_root" not in rendered
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["resources"] == [exact]
        assert diagnostic["repair_actions"] == []


def test_resource_supervision_never_takes_over_unreleased_other_holder(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="conflict-create",
        )["scenario"]
        participants = {}
        for participant_id in (PARTICIPANT_ID, second_id):
            participants[participant_id] = client.add_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=participant_id,
                scenario_generation=1,
                scenario_state_revision=created["state_revision"],
                launch_spec=_launch_spec(),
                presentation_driver_id="presentation.iterm2",
                request_id=f"conflict-add-{participant_id}",
            )["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            request_id="conflict-open",
        )["scenario"]
        first = client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=1,
            participant_state_revision=participants[PARTICIPANT_ID][
                "state_revision"
            ],
            request_id="conflict-start-first",
        )["participant"]
        with pytest.raises(HarnessClientError) as exc:
            client.start_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=second_id,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                participant_generation=1,
                participant_state_revision=participants[second_id][
                    "state_revision"
                ],
                request_id="conflict-start-second",
            )
        assert exc.value.code == "operation.external-failure"
        leases = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"]
        assert len(leases) == 1
        assert leases[0]["status"] == "active"
        assert leases[0]["holder"]["participant_id"] == PARTICIPANT_ID
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        records = {
            record["participant_id"]: record
            for record in diagnostic["participants"]
        }
        assert records[PARTICIPANT_ID]["runtime_binding_id"] == first[
            "runtime_binding_id"
        ]
        assert records[PARTICIPANT_ID]["observed_state"] == "ready"
        assert records[second_id]["observed_state"] == "degraded"
        assert records[second_id]["degraded"]["cleanup_pending"] is False
        assert records[second_id]["runtime_binding_id"] is None
        assert "runtime-binding-participant-two" in driver.stop_calls


def test_resource_conflict_retains_exact_binding_when_cleanup_stop_fails(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="retained-create",
        )["scenario"]
        participants = {
            participant_id: client.add_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=participant_id,
                scenario_generation=1,
                scenario_state_revision=created["state_revision"],
                launch_spec=_launch_spec(),
                presentation_driver_id="presentation.iterm2",
                request_id=f"retained-add-{participant_id}",
            )["participant"]
            for participant_id in (PARTICIPANT_ID, second_id)
        }
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            request_id="retained-open",
        )["scenario"]
        client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=1,
            participant_state_revision=participants[PARTICIPANT_ID][
                "state_revision"
            ],
            request_id="retained-start-first",
        )
        driver.fail_stop = True
        with pytest.raises(HarnessClientError):
            client.start_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=second_id,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                participant_generation=1,
                participant_state_revision=participants[second_id][
                    "state_revision"
                ],
                request_id="retained-start-second",
            )
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        records = {
            record["participant_id"]: record
            for record in diagnostic["participants"]
        }
        retained = records[second_id]
        assert retained["observed_state"] == "degraded"
        assert retained["degraded"]["cleanup_pending"] is True
        assert retained["runtime_binding_id"] == "runtime-binding-participant-two"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        artifact = item["participant_artifacts"][second_id]
        assert artifact["runtime_ready_ack"]["binding"][
            "runtime_binding_id"
        ] == retained["runtime_binding_id"]

        driver.fail_stop = False
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=second_id,
            scenario_generation=1,
            scenario_state_revision=diagnostic["scenario"]["state_revision"],
            participant_generation=1,
            participant_state_revision=retained["state_revision"],
            request_id="retained-cleanup-second",
        )["participant"]
        assert stopped["observed_state"] == "stopped"
        assert stopped["runtime_binding_id"] is None


def test_machine_resource_conflict_is_enforced_across_scenarios(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_scenario = "scenario-two"
    second_participant = "participant-two"
    with running_host(state_root) as (_, client, driver):
        first_created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="global-first-create",
        )["scenario"]
        first_added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=first_created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="global-first-add",
        )["participant"]
        first_opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=first_created["state_revision"],
            request_id="global-first-open",
        )["scenario"]
        client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=first_opened["state_revision"],
            participant_generation=1,
            participant_state_revision=first_added["state_revision"],
            request_id="global-first-start",
        )

        second_created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=second_scenario,
            project_binding_digest=PROJECT_DIGEST,
            request_id="global-second-create",
        )["scenario"]
        second_added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=second_scenario,
            participant_id=second_participant,
            scenario_generation=1,
            scenario_state_revision=second_created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="global-second-add",
        )["participant"]
        second_opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=second_scenario,
            scenario_generation=1,
            scenario_state_revision=second_created["state_revision"],
            request_id="global-second-open",
        )["scenario"]
        with pytest.raises(HarnessClientError):
            client.start_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=second_scenario,
                participant_id=second_participant,
                scenario_generation=1,
                scenario_state_revision=second_opened["state_revision"],
                participant_generation=1,
                participant_state_revision=second_added["state_revision"],
                request_id="global-second-start",
            )
        first_leases = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"]
        second_leases = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=second_scenario
        )["resources"]
        assert len(first_leases) == 1
        assert first_leases[0]["status"] == "active"
        assert first_leases[0]["holder"]["scenario_id"] == SCENARIO_ID
        assert second_leases == []
        assert "runtime-binding-participant-two" in driver.stop_calls
        assert "runtime-binding-one" in driver.running_bindings
        assert "runtime-binding-participant-two" not in driver.running_bindings


def _start_ready_participant(client: HarnessClient) -> tuple[dict[str, Any], dict[str, Any]]:
    created = client.create_scenario(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        project_binding_digest=PROJECT_DIGEST,
        request_id="close-create",
    )["scenario"]
    added = client.add_participant(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        scenario_generation=created["scenario_generation"],
        scenario_state_revision=created["state_revision"],
        launch_spec=_launch_spec(),
        presentation_driver_id="presentation.iterm2",
        request_id="close-add",
    )["participant"]
    opened = client.open_scenario(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        scenario_generation=created["scenario_generation"],
        scenario_state_revision=created["state_revision"],
        request_id="close-open",
    )["scenario"]
    ready = client.start_participant(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        participant_id=PARTICIPANT_ID,
        scenario_generation=opened["scenario_generation"],
        scenario_state_revision=opened["state_revision"],
        participant_generation=added["participant_generation"],
        participant_state_revision=added["state_revision"],
        request_id="close-start",
    )["participant"]
    return opened, ready


@pytest.mark.parametrize("mode", ["idle", "busy", "requested"])
def test_scenario_safe_close_completes_without_force_stop(
    tmp_path: Path, mode: str
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, ready = _start_ready_participant(client)
        driver.close_mode = mode
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id=f"close-{mode}",
        )
        scenario = closed["scenario"]
        summary = closed["close_summary"]
        assert scenario["desired_state"] == "closed"
        assert scenario["observed_state"] == "closed"
        assert summary["all_closed"] is True
        assert summary["auto_force_stop_used"] is False
        assert summary["reports"][0]["classification"] == mode
        assert summary["reports"][0]["closed"] is True
        assert not driver.running
        participant = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]["participants"][0]
        assert ready["desired_state"] == "running"
        assert participant["desired_state"] == "stopped"
        assert participant["observed_state"] == "stopped"
        assert participant["runtime_binding_id"] is None
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["latest_close"] == summary
        assert diagnostic["repair_actions"] == []
        assert len(diagnostic["diagnostic_digest"]) == 64

        replay = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id=f"close-{mode}",
        )
        assert replay == closed
        assert driver.close_calls == 1


def test_scenario_close_progress_and_cooperative_cancel_preserve_partial_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="cancel-create",
        )["scenario"]
        first = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="cancel-first",
        )
        second = _add_test_participant(
            client,
            scenario=created,
            participant_id=second_id,
            request_prefix="cancel-second",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="cancel-open",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=first,
            participant_id=PARTICIPANT_ID,
            request_prefix="cancel-first",
        )
        assert second["observed_state"] == "stopped"
        driver.close_release = threading.Event()
        progress: list[dict[str, Any]] = []
        progress_ready = threading.Event()
        failures: list[HarnessClientError] = []

        def observe_progress(event: dict[str, Any]) -> None:
            progress.append(event)
            progress_ready.set()

        def close() -> None:
            try:
                client.close_scenario(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    scenario_generation=opened["scenario_generation"],
                    scenario_state_revision=opened["state_revision"],
                    drain_timeout_ms=2_000,
                    request_id="cancel-close",
                    progress_callback=observe_progress,
                )
            except HarnessClientError as exc:
                failures.append(exc)

        thread = threading.Thread(target=close)
        thread.start()
        assert driver.close_started.wait(timeout=3)
        assert progress_ready.wait(timeout=3)
        assert progress
        operation_id = progress[0]["operation_id"]
        cancelled = client.cancel_operation(operation_id)
        assert cancelled == {
            "operation_id": operation_id,
            "outcome": "accepted",
            "mutation_state": "committed",
        }
        driver.close_release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert failures[0].code == "operation.cancelled"
        assert failures[0].mutation_state == "committed"
        assert failures[0].repair_action == "scenario.refresh"
        assert [event["sequence"] for event in progress] == list(range(len(progress)))
        assert progress[-1]["state"] == "cancelled"
        assert all(event["operation_id"] == operation_id for event in progress)
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["scenario"]["observed_state"] == "degraded"
        participants = {value["participant_id"]: value for value in diagnostic["participants"]}
        assert participants[PARTICIPANT_ID]["observed_state"] == "stopped"
        assert participants[second_id]["observed_state"] == "stopped"
        assert driver.close_calls == 1
        assert driver.force_stop_calls == []
        with pytest.raises(HarnessClientError) as late_cancel:
            client.cancel_operation(operation_id)
        assert late_cancel.value.code == "operation.precondition-failed"
        assert late_cancel.value.mutation_state == "not_started"


def test_scenario_close_survives_progress_observer_disconnect(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, _):
        opened, _ = _start_ready_participant(client)

        def disconnect(_event: dict[str, Any]) -> None:
            raise OSError("observer disconnected")

        with pytest.raises(HarnessClientError) as unavailable:
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                drain_timeout_ms=2_000,
                request_id="observer-disconnect-close",
                progress_callback=disconnect,
            )
        assert unavailable.value.code == "host.unavailable"
        assert unavailable.value.mutation_state == "unknown"

        deadline = time.monotonic() + 3
        observed = ""
        while time.monotonic() < deadline:
            observed = client.scenario_status(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["scenario"]["observed_state"]
            if observed == "closed":
                break
            time.sleep(0.01)
        assert observed == "closed"


def test_scenario_resume_recreates_only_previous_running_participants(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    stopped_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="resume-create",
        )["scenario"]
        running = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="resume-running",
        )
        _add_test_participant(
            client,
            scenario=created,
            participant_id=stopped_id,
            request_prefix="resume-stopped",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=running,
            participant_id=PARTICIPANT_ID,
            request_prefix="resume-running",
        )
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="resume-close",
        )
        assert closed["close_summary"]["restore_target_participant_ids"] == [
            PARTICIPANT_ID
        ]
        assert closed["close_summary"]["restore_targets"] == [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "continuity_mode": "explicit_recreate",
            }
        ]
        reports = {
            value["participant_id"]: value
            for value in closed["close_summary"]["reports"]
        }
        assert reports[PARTICIPANT_ID]["desired_state_before_close"] == "running"
        assert reports[stopped_id]["desired_state_before_close"] == "stopped"

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario"]["scenario_generation"],
            scenario_state_revision=closed["scenario"]["state_revision"],
            request_id="resume-open",
        )
        summary = resumed["resume_summary"]
        assert summary["all_targets_ready"] is True
        assert summary["target_count"] == 1
        assert summary["recreated_count"] == 1
        assert summary["exact_resumed_count"] == 0
        assert summary["vendor_session_identity_required"] is False
        assert summary["explicit_recreate_is_not_exact_resume"] is True
        assert summary["reports"][0]["outcome"] == "recreated"
        participants = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert participants[PARTICIPANT_ID]["observed_state"] == "ready"
        assert participants[stopped_id]["observed_state"] == "stopped"
        assert driver.start_calls == 2

        replay = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario"]["scenario_generation"],
            scenario_state_revision=closed["scenario"]["state_revision"],
            request_id="resume-open",
        )
        assert replay == resumed
        assert driver.start_calls == 2


def test_scenario_resume_isolates_one_participant_failure_and_starts_sibling(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    sibling_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="partial-resume-create",
        )["scenario"]
        participants = {
            participant_id: _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"partial-resume-{participant_id}",
            )
            for participant_id in (PARTICIPANT_ID, sibling_id)
        }
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="partial-resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=participants[PARTICIPANT_ID],
            participant_id=PARTICIPANT_ID,
            request_prefix=f"partial-resume-{PARTICIPANT_ID}",
        )
        driver.resource_digest = "4" * 64
        _start_test_participant(
            client,
            scenario=opened,
            participant=participants[sibling_id],
            participant_id=sibling_id,
            request_prefix=f"partial-resume-{sibling_id}",
        )
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="partial-resume-close",
        )
        driver.fail_start_participant_ids = {PARTICIPANT_ID}

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario"]["scenario_generation"],
            scenario_state_revision=closed["scenario"]["state_revision"],
            request_id="partial-resume-open",
        )
        summary = resumed["resume_summary"]
        by_participant = {
            value["participant_id"]: value for value in summary["reports"]
        }
        assert summary["all_targets_ready"] is False
        assert by_participant[PARTICIPANT_ID]["outcome"] == "failed"
        assert by_participant[PARTICIPANT_ID]["repair_required"] is True
        assert by_participant[sibling_id]["outcome"] == "recreated"
        current = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert current[PARTICIPANT_ID]["observed_state"] == "degraded"
        assert current[sibling_id]["observed_state"] == "ready"
        assert current[sibling_id]["runtime_binding_id"] in driver.running_bindings
        assert resumed["scenario"]["observed_state"] == "degraded"


def test_scenario_resume_projects_pre_resume_close_history_without_guessing(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    stopped_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="legacy-resume-create",
        )["scenario"]
        running = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="legacy-resume-running",
        )
        _add_test_participant(
            client,
            scenario=created,
            participant_id=stopped_id,
            request_prefix="legacy-resume-stopped",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="legacy-resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=running,
            participant_id=PARTICIPANT_ID,
            request_prefix="legacy-resume-running",
        )
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="legacy-resume-close",
        )["scenario"]

        state_path = state_root / "host-state.json"
        durable = json.loads(state_path.read_text(encoding="utf-8"))
        item = next(iter(durable["scenarios"].values()))
        latest = item["close_history"][-1]
        latest.pop("restore_target_participant_ids")
        latest.pop("restore_targets")
        for report in latest["reports"]:
            report.pop("desired_state_before_close")
            report.pop("continuity_mode")
        latest["summary_digest"] = canonical_json_sha256(latest["reports"])
        state_path.write_text(
            json.dumps(durable, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
            request_id="legacy-resume-open",
        )
        assert resumed["resume_summary"]["target_count"] == 1
        assert resumed["resume_summary"]["reports"][0]["participant_id"] == (
            PARTICIPANT_ID
        )
        current = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert current[PARTICIPANT_ID]["observed_state"] == "ready"
        assert current[stopped_id]["observed_state"] == "stopped"
        assert driver.start_calls == 2


def test_scenario_resume_rejects_target_generation_drift(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="drift-resume-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="drift-resume",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="drift-resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="drift-resume",
        )
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="drift-resume-close",
        )["scenario"]

        state_path = state_root / "host-state.json"
        durable = json.loads(state_path.read_text(encoding="utf-8"))
        item = next(iter(durable["scenarios"].values()))
        participant = item["participants"][PARTICIPANT_ID]
        participant["participant_generation"] = 2
        state_path.write_text(
            json.dumps(durable, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
            request_id="drift-resume-open",
        )
        report = resumed["resume_summary"]["reports"][0]
        assert report["outcome"] == "failed"
        assert report["reason_code"] == "participant.restore-target-drift"
        assert resumed["scenario"]["observed_state"] == "degraded"
        assert driver.start_calls == 1


@pytest.mark.parametrize(
    ("close_binding_present", "resume_binding_present"),
    [(True, True), (True, False), (False, True)],
)
def test_scenario_resume_reports_optional_exact_resume_capability(
    tmp_path: Path,
    close_binding_present: bool,
    resume_binding_present: bool,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        driver.support_exact_resume = True
        driver.pending_vendor_binding = not close_binding_present
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="exact-resume-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="exact-resume",
            launch_spec=_launch_spec(continuity_mode="exact_resume"),
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="exact-resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="exact-resume",
        )
        closed_result = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="exact-resume-close",
        )
        closed = closed_result["scenario"]
        restore_target = closed_result["close_summary"]["restore_targets"][0]
        if close_binding_present:
            assert restore_target == {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "continuity_mode": "exact_resume",
                "vendor_session_identity_sha256": SESSION_DIGEST,
            }
        else:
            assert restore_target == {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "continuity_mode": "explicit_recreate",
            }

        driver.always_pending_vendor_binding = not resume_binding_present

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
            request_id="exact-resume-open",
        )
        summary = resumed["resume_summary"]
        assert summary["vendor_session_identity_required"] is close_binding_present
        assert summary["exact_resumed_count"] == int(
            close_binding_present and resume_binding_present
        )
        assert summary["recreated_count"] == int(not close_binding_present)
        expected_outcome = (
            "recreated"
            if not close_binding_present
            else "exact_resumed"
            if resume_binding_present
            else "failed"
        )
        assert summary["reports"][0]["outcome"] == expected_outcome
        assert summary["reports"][0]["repair_required"] is (
            close_binding_present and not resume_binding_present
        )


def test_scenario_resume_uses_vendor_identity_discovered_during_close(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        driver.support_exact_resume = True
        driver.pending_vendor_binding = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="close-discovered-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="close-discovered",
            launch_spec=_launch_spec(continuity_mode="exact_resume"),
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="close-discovered-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="close-discovered",
        )

        driver.close_vendor_session_identity_sha256 = SESSION_DIGEST
        closed_result = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="close-discovered-close",
        )
        closed = closed_result["scenario"]
        assert closed_result["close_summary"]["restore_targets"] == [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "continuity_mode": "exact_resume",
                "vendor_session_identity_sha256": SESSION_DIGEST,
            }
        ]

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
            request_id="close-discovered-open",
        )
        summary = resumed["resume_summary"]
        assert summary["vendor_session_identity_required"] is True
        assert summary["exact_resumed_count"] == 1
        assert summary["recreated_count"] == 0
        assert summary["reports"][0]["outcome"] == "exact_resumed"
        assert summary["reports"][0]["repair_required"] is False


def test_environment_probe_round_trips_validated_observations(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, _driver):
        result = client.environment_probe()
        assert result == {
            "environment_observations": [
                {
                    "subject_ref": "runtime-profile.inert",
                    "display_name": "Inert (test fixture)",
                    "status": "available",
                    "observed_version": "1.0",
                    "evidence_digest": "8" * 64,
                    "provider_error_code": None,
                    "remediation_ref": None,
                }
            ]
        }


def test_environment_probe_fails_closed_on_malformed_reply(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        original_call = driver.call

        def malformed(
            operation: str,
            payload: Mapping[str, Any],
            *,
            timeout_seconds: float = 300,
        ) -> dict[str, Any]:
            if operation == "environment_probe":
                return {
                    "environment_observations": [
                        {
                            "subject_ref": "runtime-profile.inert",
                            "display_name": "Inert (test fixture)",
                            "status": "available",
                            # Wrong type must be rejected, not coerced.
                            "observed_version": 1.0,
                            "evidence_digest": "8" * 64,
                            "provider_error_code": None,
                            "remediation_ref": None,
                        }
                    ]
                }
            return original_call(
                operation, payload, timeout_seconds=timeout_seconds
            )

        driver.call = malformed  # type: ignore[method-assign]
        with pytest.raises(HarnessClientError) as failed:
            client.environment_probe()
        assert failed.value.code == "environment.observation-failed"


def test_scenario_start_participants_starts_every_startable_unit(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-create",
        )["scenario"]
        for participant_id in (PARTICIPANT_ID, second_id):
            _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"start-all-{participant_id}",
            )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-open",
        )["scenario"]

        result = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-first",
        )
        summary = result["start_summary"]
        assert summary["schema_version"] == 1
        assert summary["cancelled"] is False
        assert summary["counts"] == {
            "total": 2,
            "started": 2,
            "already_running": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": 0,
        }
        outcomes = {
            report["participant_id"]: report for report in summary["reports"]
        }
        assert outcomes[PARTICIPANT_ID]["outcome"] == "started"
        assert outcomes[second_id]["outcome"] == "started"
        assert outcomes[PARTICIPANT_ID]["resulting_state_revision"] is not None
        assert result["scenario"]["observed_state"] == "running"
        assert driver.start_calls == 2
        participants = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert participants[PARTICIPANT_ID]["observed_state"] == "ready"
        assert participants[second_id]["observed_state"] == "ready"

        # Pressing the batch again is safe: every unit is already running
        # and no new driver start is issued.
        repeat = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=result["scenario"]["scenario_generation"],
            scenario_state_revision=result["scenario"]["state_revision"],
            request_id="start-all-second",
        )
        assert repeat["start_summary"]["counts"] == {
            "total": 2,
            "started": 0,
            "already_running": 2,
            "skipped": 0,
            "failed": 0,
            "cancelled": 0,
        }
        assert driver.start_calls == 2


def test_scenario_start_participants_isolates_failure_and_continues(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-fail-create",
        )["scenario"]
        for participant_id in (PARTICIPANT_ID, second_id):
            _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"start-all-fail-{participant_id}",
            )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-fail-open",
        )["scenario"]
        driver.fail_start_participant_ids = {PARTICIPANT_ID}

        # The first unit fails and degrades the Scenario, which bumps the
        # scenario state revision mid-batch; the sibling must still start
        # because every unit re-reads fresh fences.
        result = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-fail",
        )
        summary = result["start_summary"]
        assert summary["counts"]["failed"] == 1
        assert summary["counts"]["started"] == 1
        outcomes = {
            report["participant_id"]: report for report in summary["reports"]
        }
        assert outcomes[PARTICIPANT_ID]["outcome"] == "failed"
        assert isinstance(outcomes[PARTICIPANT_ID]["reason_code"], str)
        assert outcomes[PARTICIPANT_ID]["reason_code"]
        assert outcomes[second_id]["outcome"] == "started"
        participants = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert participants[PARTICIPANT_ID]["observed_state"] == "degraded"
        assert participants[second_id]["observed_state"] == "ready"
        assert result["scenario"]["observed_state"] == "degraded"


def test_scenario_start_participants_reports_ready_units_without_restarting(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-mixed-create",
        )["scenario"]
        first = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-mixed-first",
        )
        _add_test_participant(
            client,
            scenario=created,
            participant_id=second_id,
            request_prefix="start-all-mixed-second",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-mixed-open",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=first,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-mixed-first",
        )
        assert driver.start_calls == 1

        result = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-mixed",
        )
        summary = result["start_summary"]
        outcomes = {
            report["participant_id"]: report for report in summary["reports"]
        }
        assert outcomes[PARTICIPANT_ID]["outcome"] == "already_running"
        assert outcomes[second_id]["outcome"] == "started"
        assert summary["counts"]["already_running"] == 1
        assert summary["counts"]["started"] == 1
        assert driver.start_calls == 2


def test_scenario_start_participants_retry_after_stop_starts_for_real(
    tmp_path: Path,
) -> None:
    """A transport retry of the same request id must never replay a stale
    child journal entry as a false "started" after the unit was stopped."""

    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-retry-create",
        )["scenario"]
        _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-retry",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-retry-open",
        )["scenario"]
        first = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-retry",
        )
        assert first["start_summary"]["counts"]["started"] == 1
        assert driver.start_calls == 1
        running = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }[PARTICIPANT_ID]
        client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=running["participant_generation"],
            participant_state_revision=running["state_revision"],
            request_id="start-all-retry-stop",
        )

        retried = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-retry",
        )
        assert retried["start_summary"]["counts"]["started"] == 1
        assert driver.start_calls == 2
        after = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }[PARTICIPANT_ID]
        assert after["observed_state"] == "ready"


def test_scenario_start_participants_refuses_concurrent_identical_request(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-dup-create",
        )["scenario"]
        _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-dup",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-dup-open",
        )["scenario"]
        driver.start_release = threading.Event()
        outcomes: list[object] = []

        def start_all() -> None:
            try:
                outcomes.append(
                    client.start_scenario_participants(
                        project_instance_id=PROJECT_ID,
                        scenario_id=SCENARIO_ID,
                        scenario_generation=opened["scenario_generation"],
                        scenario_state_revision=opened["state_revision"],
                        request_id="start-all-dup",
                    )
                )
            except HarnessClientError as exc:
                outcomes.append(exc)

        thread = threading.Thread(target=start_all)
        thread.start()
        assert driver.start_started.wait(timeout=3)
        with pytest.raises(HarnessClientError) as duplicate:
            client.start_scenario_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                request_id="start-all-dup",
            )
        assert duplicate.value.code == "scenario.operation-in-progress"
        driver.start_release.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], dict)
        assert outcomes[0]["start_summary"]["counts"]["started"] == 1
        assert driver.start_calls == 1


def test_scenario_start_participants_probes_ready_units_instead_of_trusting_state(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-dead-create",
        )["scenario"]
        first = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-dead-first",
        )
        _add_test_participant(
            client,
            scenario=created,
            participant_id=second_id,
            request_prefix="start-all-dead-second",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-dead-open",
        )["scenario"]
        started = _start_test_participant(
            client,
            scenario=opened,
            participant=first,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-dead-first",
        )
        # The process dies externally: the durable record still says ready,
        # but the driver can no longer prove the binding.
        driver.running_bindings.discard(started["runtime_binding_id"])

        result = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="start-all-dead",
        )
        outcomes = {
            report["participant_id"]: report
            for report in result["start_summary"]["reports"]
        }
        assert outcomes[PARTICIPANT_ID]["outcome"] == "failed"
        assert outcomes[PARTICIPANT_ID]["reason_code"] == (
            "participant.binding-drift"
        )
        assert outcomes[second_id]["outcome"] == "started"
        assert result["start_summary"]["counts"]["already_running"] == 0


def test_scenario_start_participants_rejects_stale_scenario_fence(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-stale-create",
        )["scenario"]
        _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="start-all-stale",
        )
        client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-stale-open",
        )
        with pytest.raises(HarnessClientError) as stale:
            client.start_scenario_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=created["scenario_generation"],
                scenario_state_revision=created["state_revision"],
                request_id="start-all-stale",
            )
        assert stale.value.code == "scenario.stale-fence"
        assert driver.start_calls == 0


def test_scenario_start_participants_progress_and_cooperative_cancel(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-cancel-create",
        )["scenario"]
        for participant_id in (PARTICIPANT_ID, second_id):
            _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"start-all-cancel-{participant_id}",
            )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-cancel-open",
        )["scenario"]
        driver.start_release = threading.Event()
        progress: list[dict[str, Any]] = []
        progress_ready = threading.Event()
        failures: list[HarnessClientError] = []

        def observe_progress(event: dict[str, Any]) -> None:
            progress.append(event)
            progress_ready.set()

        def start_all() -> None:
            try:
                client.start_scenario_participants(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    scenario_generation=opened["scenario_generation"],
                    scenario_state_revision=opened["state_revision"],
                    request_id="start-all-cancel",
                    progress_callback=observe_progress,
                )
            except HarnessClientError as exc:
                failures.append(exc)

        thread = threading.Thread(target=start_all)
        thread.start()
        assert driver.start_started.wait(timeout=3)
        assert progress_ready.wait(timeout=3)
        operation_id = progress[0]["operation_id"]
        cancelled = client.cancel_operation(operation_id)
        assert cancelled == {
            "operation_id": operation_id,
            "outcome": "accepted",
            "mutation_state": "committed",
        }
        driver.start_release.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert failures[0].code == "operation.cancelled"
        assert failures[0].mutation_state == "committed"
        assert [event["sequence"] for event in progress] == list(
            range(len(progress))
        )
        assert progress[-1]["state"] == "cancelled"
        assert all(
            event["operation_id"] == operation_id for event in progress
        )
        # The unit that already began keeps its durable outcome; the unit
        # behind the cancel point was never driven.
        participants = {
            value["participant_id"]: value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
        }
        assert participants[PARTICIPANT_ID]["observed_state"] == "ready"
        assert participants[second_id]["observed_state"] == "stopped"
        assert driver.start_calls == 1


def test_scenario_start_participants_reports_cancel_accepted_during_last_unit(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="start-all-last-cancel-create",
        )["scenario"]
        for participant_id in (PARTICIPANT_ID, second_id):
            _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"start-all-last-cancel-{participant_id}",
            )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="start-all-last-cancel-open",
        )["scenario"]
        original_call = driver.call
        last_started = threading.Event()
        release_last = threading.Event()

        def block_last_start(
            operation: str,
            payload: Mapping[str, Any],
            *,
            timeout_seconds: float = 300,
        ) -> dict[str, Any]:
            if (
                operation == "start"
                and payload["context"]["participant_id"] == second_id
            ):
                last_started.set()
                assert release_last.wait(timeout=10)
            return original_call(
                operation, payload, timeout_seconds=timeout_seconds
            )

        driver.call = block_last_start  # type: ignore[method-assign]
        progress: list[dict[str, Any]] = []
        failures: list[HarnessClientError] = []

        def start_all() -> None:
            try:
                client.start_scenario_participants(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    scenario_generation=opened["scenario_generation"],
                    scenario_state_revision=opened["state_revision"],
                    request_id="start-all-last-cancel",
                    progress_callback=progress.append,
                )
            except HarnessClientError as exc:
                failures.append(exc)

        thread = threading.Thread(target=start_all)
        thread.start()
        assert last_started.wait(timeout=3)
        operation_id = progress[0]["operation_id"]
        assert client.cancel_operation(operation_id)["outcome"] == "accepted"
        release_last.set()
        thread.join(timeout=10)

        assert not thread.is_alive()
        assert len(failures) == 1
        assert failures[0].code == "operation.cancelled"
        assert failures[0].mutation_state == "committed"
        assert progress[-1]["state"] == "cancelled"
        participants = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"]
        assert {value["observed_state"] for value in participants} == {"ready"}
        assert driver.start_calls == 2


def test_host_restart_finishes_migrated_pending_scenario_resume(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="restart-resume-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="restart-resume",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="restart-resume-open-initial",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="restart-resume",
        )
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="restart-resume-close",
        )["scenario"]
        _, pending = host.store.open_scenario(
            request_id="restart-resume-open",
            request_digest="d" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
        )
        assert pending["scenario"]["observed_state"] == "opening"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert durable["requests"]["restart-resume-open"]["status"] == "pending"
        item = next(iter(durable["scenarios"].values()))
        item["project_contract_snapshot"] = None
        (state_root / "host-state.json").write_text(
            json.dumps(durable), encoding="utf-8"
        )
        (state_root / "host-state.json").chmod(0o600)

    with running_host(state_root) as (_, client, driver):
        resumed = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        participant = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["participants"][0]
        assert resumed["observed_state"] == "running"
        assert participant["observed_state"] == "ready"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        request = durable["requests"]["restart-resume-open"]
        assert request["status"] == "completed"
        assert request["result"]["resume_summary"]["all_targets_ready"] is True
        assert driver.start_calls == 1


def test_live_scenario_open_failure_clears_pending_resume(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _driver):
        opened, _ready = _start_ready_participant(client)
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="live-resume-failure-close",
        )["scenario"]

        def fail_during_live_resume(
            project_instance_id: str, scenario_id: str
        ) -> list[dict[str, Any]]:
            raise StoreError(
                "scenario.restore-plan-invalid",
                "injected live resume failure",
            )

        host.store.scenario_restore_plan = (  # type: ignore[method-assign]
            fail_during_live_resume
        )
        with pytest.raises(HarnessClientError):
            client.open_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=closed["scenario_generation"],
                scenario_state_revision=closed["state_revision"],
                request_id="live-resume-failure-open",
            )

        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        assert scenario["observed_state"] == "degraded"
        assert scenario["active_operation_id"] is None
        assert scenario["degraded"]["reason"] == "participant_restore_incomplete"
        assert scenario["degraded"]["repair_action"] == "scenario.repair"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        request = durable["requests"]["live-resume-failure-open"]
        assert request["status"] == "failed"
        assert "pending_resume_summary" not in request


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [
        ("legacy-binding", "project.not-found"),
        ("snapshot-mismatch", "host.state-invalid"),
    ],
)
def test_host_restart_contains_one_resume_precondition_failure(
    tmp_path: Path, failure_kind: str, expected_code: str
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="contained-resume-create",
        )["scenario"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="contained-resume-open-initial",
        )["scenario"]
        closed = client.close_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            drain_timeout_ms=2_000,
            request_id="contained-resume-close",
        )["scenario"]
        host.store.open_scenario(
            request_id="contained-resume-open",
            request_digest="e" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=closed["scenario_generation"],
            scenario_state_revision=closed["state_revision"],
        )
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        if failure_kind == "legacy-binding":
            item["project_contract_snapshot"] = None
        else:
            item["record"]["project_binding_digest"] = "f" * 64
        (state_root / "host-state.json").write_text(
            json.dumps(durable), encoding="utf-8"
        )
        (state_root / "host-state.json").chmod(0o600)

    existing_binding_error = (
        ProjectError("project.not-found", "project is not registered")
        if failure_kind == "legacy-binding"
        else None
    )
    with running_host(
        state_root, existing_binding_error=existing_binding_error
    ) as (_, client, _):
        assert client.host_status()["status"] == "ready"
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert scenario["observed_state"] == "degraded"
        assert scenario["active_operation_id"] is None
        assert scenario["degraded"]["reason"] == "participant_restore_incomplete"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        request = durable["requests"]["contained-resume-open"]
        assert request["status"] == "failed"
        assert request["error"]["code"] == expected_code
        assert "pending_resume_summary" not in request


def test_force_stop_requires_confirmation_and_preserves_sibling(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root, with_security=True) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="force-create",
        )["scenario"]
        first_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-first",
        )
        sibling_added = _add_test_participant(
            client,
            scenario=created,
            participant_id="participant-two",
            request_prefix="force-second",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-open",
        )["scenario"]
        first = _start_test_participant(
            client,
            scenario=opened,
            participant=first_added,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-first",
        )
        driver.resource_digest = "4" * 64
        sibling = _start_test_participant(
            client,
            scenario=opened,
            participant=sibling_added,
            participant_id="participant-two",
            request_prefix="force-second",
        )

        forced = client.force_stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=first["participant_generation"],
            participant_state_revision=first["state_revision"],
            request_id="force-exact",
        )["participant"]
        assert forced["observed_state"] == "stopped"
        assert driver.force_stop_calls == ["runtime-binding-one"]
        assert sibling["runtime_binding_id"] in driver.running_bindings
        assert client.participant_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id="participant-two",
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=sibling["participant_generation"],
            participant_state_revision=sibling["state_revision"],
        )["participant"]["observed_state"] == "ready"
        security = host.security
        assert security is not None
        assert security.adapter.present_calls == 1  # type: ignore[attr-defined]
        durable = json.loads(security.state_path.read_text(encoding="utf-8"))
        chain = next(iter(durable["chains"].values()))
        assert chain["operation"] == "participant.force-stop"
        assert chain["operation_outcome"]["outcome"] == "completed"
        assert "private_root" not in security.state_path.read_text(encoding="utf-8")

        replay = client.force_stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=first["participant_generation"],
            participant_state_revision=first["state_revision"],
            request_id="force-exact",
        )["participant"]
        assert replay == forced
        assert security.adapter.present_calls == 1  # type: ignore[attr-defined]


def test_force_stop_without_security_adapter_fails_before_driver(
    tmp_path: Path,
) -> None:
    with running_host(tmp_path / "state") as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="no-security-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="no-security",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="no-security-open",
        )["scenario"]
        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="no-security",
        )
        with pytest.raises(HarnessClientError) as caught:
            client.force_stop_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=ready["participant_generation"],
                participant_state_revision=ready["state_revision"],
                request_id="no-security-force",
            )
        assert caught.value.code == "auth.confirmation-required"
        assert driver.force_stop_calls == []
        assert ready["runtime_binding_id"] in driver.running_bindings


def test_force_destroy_cleanup_uses_exact_frozen_participant_binding(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="force-delete-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-delete",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-delete-open",
        )["scenario"]
        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-delete",
        )
        current = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        operation_id, replay, executions = host.store.begin_scenario_close(
            request_id="force-delete-cleanup",
            request_digest="a" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=current["scenario_generation"],
            scenario_state_revision=current["state_revision"],
            drain_timeout_ms=1,
            force=True,
        )
        assert replay is None
        assert executions is not None
        assert executions[0]["runtime_binding_id"] == ready["runtime_binding_id"]
        assert host.participants is not None

        reports = host.participants.force_close_scenario_participants(executions)
        host.store.record_scenario_close_reports(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="force-delete-cleanup",
            operation_id=operation_id,
            reports=reports,
            force_stop_used=True,
        )
        result = host.store.finalize_scenario_close(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="force-delete-cleanup",
            operation_id=operation_id,
            reports=reports,
            force_stop_used=True,
        )

        assert driver.force_stop_calls == [ready["runtime_binding_id"]]
        assert result["scenario"]["observed_state"] == "closed"
        assert result["close_summary"]["auto_force_stop_used"] is True
        stopped = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert stopped["observed_state"] == "stopped"
        assert stopped["runtime_binding_id"] is None


def test_forced_close_completes_when_a_participant_cannot_be_proven_closed(
    tmp_path: Path,
) -> None:
    """A forced teardown must not be blocked by an unprovable participant.

    The owner has already confirmed the destruction of a Scenario that is
    already broken. Requiring proof of a clean close at that point left the
    Scenario impossible to remove from any entry point. The report still
    records honestly that the outcome was unknown; only the gate is relaxed,
    and only for the forced path.
    """
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="unprovable-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="unprovable",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="unprovable-open",
        )["scenario"]
        _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="unprovable",
        )
        current = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        operation_id, _replay, executions = host.store.begin_scenario_close(
            request_id="unprovable-cleanup",
            request_digest="b" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=current["scenario_generation"],
            scenario_state_revision=current["state_revision"],
            drain_timeout_ms=1,
            force=True,
        )
        assert executions is not None
        assert host.participants is not None

        # The owned process can no longer be acted on, so the driver cannot
        # prove the outcome either way.
        driver.fail_stop = True
        reports = host.participants.force_close_scenario_participants(executions)
        assert reports[0]["closed"] is False
        assert reports[0]["action_outcome_known"] is False

        host.store.record_scenario_close_reports(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="unprovable-cleanup",
            operation_id=operation_id,
            reports=reports,
            force_stop_used=True,
        )
        result = host.store.finalize_scenario_close(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="unprovable-cleanup",
            operation_id=operation_id,
            reports=reports,
            force_stop_used=True,
        )

        assert result["scenario"]["observed_state"] == "closed"
        # What could not be proven is still on the record.
        assert result["close_summary"]["all_closed"] is False
        assert result["close_summary"]["auto_force_stop_used"] is True


def _mark_closed_cleanup_pending_without_external_resources(
    host: HarnessHost,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with host.store._lock:  # noqa: SLF001 - durable edge fixture
        durable = host.store._read_state()  # noqa: SLF001
        item = next(iter(durable["scenarios"].values()))
        scenario = item["record"]
        participant = item["participants"][PARTICIPANT_ID]
        artifact = item["participant_artifacts"][PARTICIPANT_ID]
        for lease in item["resource_leases"].values():
            lease.update(
                {
                    "lease_revision": lease["lease_revision"] + 1,
                    "status": "released",
                    "stale_reason": None,
                    "release_evidence_sha256": "d" * 64,
                }
            )
        for field in (
            "runtime_create_request",
            "prepared_runtime_launch",
            "runtime_ready_ack",
            "presentation_create_request",
            "presentation_create_ack",
        ):
            artifact[field] = None
        participant.update(
            {
                "desired_state": "stopped",
                "observed_state": "degraded",
                "runtime_binding_id": None,
                "presentation_binding_id": None,
                "active_operation_id": None,
                "degraded": {
                    "reason": "cleanup_pending",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": "e" * 64,
                    "repair_action": "participant.recover",
                },
                "state_revision": participant["state_revision"] + 1,
            }
        )
        scenario.update(
            {
                "desired_state": "closed",
                "observed_state": "degraded",
                "active_operation_id": None,
                "degraded": {
                    "reason": "cleanup_pending",
                    "cleanup_pending": True,
                    "owned_resource_evidence_sha256": "f" * 64,
                    "repair_action": "scenario.repair",
                },
                "state_revision": scenario["state_revision"] + 1,
            }
        )
        durable["state_revision"] += 1
        host.store._write_state(durable)  # noqa: SLF001
        return json.loads(json.dumps(scenario)), json.loads(json.dumps(participant))


def test_scenario_repair_settles_closed_cleanup_pending_without_resources(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _driver):
        _start_ready_participant(client)
        scenario, _participant = _mark_closed_cleanup_pending_without_external_resources(
            host
        )

        preview = host.store.scenario_high_risk_preview(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            operation="scenario.repair",
        )
        assert preview["eligible"] is True
        assert "participant.cleanup-pending" not in preview["blockers"]
        operation_id, replay, _ = host.store.begin_scenario_repair(
            request_id="settled-cleanup-repair",
            request_digest="1" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            expected_wip_summary_digest="2" * 64,
        )
        assert replay is None

        repaired = host.store.finalize_scenario_repair(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="settled-cleanup-repair",
            operation_id=operation_id,
            workspace_evidence_sha256="3" * 64,
        )["scenario"]

        assert repaired["observed_state"] == "closed"
        assert repaired["degraded"] is None
        participant = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert participant["observed_state"] == "stopped"
        assert participant["degraded"] is None
        assert participant["journal_head_sequence"] == repaired["journal_head_sequence"]


def test_scenario_close_records_settled_cleanup_pending_without_observation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        _start_ready_participant(client)
        scenario, _participant = _mark_closed_cleanup_pending_without_external_resources(
            host
        )

        operation_id, replay, executions = host.store.begin_scenario_close(
            request_id="settled-cleanup-close",
            request_digest="4" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            drain_timeout_ms=25,
        )
        assert replay is None
        assert executions is not None
        assert executions[0]["kind"] == "settled"
        assert host.participants is not None

        reports, cancelled = host.participants.close_scenario_participants(executions)
        result = host.store.finalize_scenario_close(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="settled-cleanup-close",
            operation_id=operation_id,
            reports=reports,
            cancelled=cancelled,
        )

        assert driver.close_calls == 0
        assert result["scenario"]["observed_state"] == "closed"
        assert result["scenario"]["degraded"] is None
        assert result["close_summary"]["all_closed"] is True
        report = result["close_summary"]["reports"][0]
        assert report["classification"] == "settled_cleanup_pending"
        assert report["command"] == "settled-cleanup-pending"
        participant = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert participant["journal_head_sequence"] == result["scenario"][
            "journal_head_sequence"
        ]


def test_force_close_records_settled_cleanup_pending_without_observation(
    tmp_path: Path,
) -> None:
    store = ScenarioStore(tmp_path / "state")
    driver = FakeDriver()
    coordinator = ParticipantCoordinator(store, driver)  # type: ignore[arg-type]

    reports = coordinator.force_close_scenario_participants(
        [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "participant_state_revision": 3,
                "desired_state": "stopped",
                "continuity_mode": "explicit_recreate",
                "runtime_binding_id": None,
                "presentation_binding_id": None,
                "kind": "settled",
            }
        ]
    )

    assert reports[0]["classification"] == "settled_cleanup_pending"
    assert reports[0]["closed"] is True
    assert reports[0]["command"] == "settled-cleanup-pending"
    assert driver.force_stop_calls == []


def test_force_destroy_cleanup_rejects_unproven_live_binding(tmp_path: Path) -> None:
    store = ScenarioStore(tmp_path / "state")
    driver = FakeDriver()
    coordinator = ParticipantCoordinator(store, driver)  # type: ignore[arg-type]
    reports = coordinator.force_close_scenario_participants(
        [
            {
                "participant_id": PARTICIPANT_ID,
                "participant_generation": 1,
                "participant_state_revision": 3,
                "desired_state": "running",
                "continuity_mode": "explicit_recreate",
                "runtime_binding_id": "runtime-binding-unproven",
                "presentation_binding_id": None,
                "kind": "unknown",
            }
        ]
    )

    assert reports[0]["closed"] is False
    assert reports[0]["command"] == "ownership-unproven"
    assert driver.force_stop_calls == []


def test_restart_finalizes_force_stop_from_durable_external_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root, with_security=True) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="force-crash-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-crash",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="force-crash-open",
        )["scenario"]
        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="force-crash",
        )

        def crash_before_finalize(**_: object) -> dict[str, object]:
            raise SystemExit("simulated Host crash after force-stop evidence")

        monkeypatch.setattr(
            host.store, "finalize_participant_stop", crash_before_finalize
        )
        assert host.participants is not None
        with pytest.raises(SystemExit, match="simulated Host crash"):
            host.participants.force_stop(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=ready["participant_generation"],
                participant_state_revision=ready["state_revision"],
                request_id="force-crash-request",
                request_digest="d" * 64,
                host_generation=host.host_generation,
            )
        assert driver.force_stop_calls == ["runtime-binding-one"]
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        request = durable["requests"]["force-crash-request"]
        assert request["status"] == "pending"
        assert request["pending_external_result"][
            "owned_resource_evidence_sha256"
        ]

    with running_host(state_root, with_security=True) as (_, client, driver):
        participant = client.participant_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=ready["participant_generation"],
            participant_state_revision=ready["state_revision"] + 2,
        )["participant"]
        assert participant["observed_state"] == "stopped"
        assert participant["runtime_binding_id"] is None
        assert driver.force_stop_calls == []


def test_resource_break_requires_exact_stale_absent_lease(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root, with_security=True) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="break-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="break",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="break-open",
        )["scenario"]
        ready = _start_test_participant(
            client,
            scenario=opened,
            participant=added,
            participant_id=PARTICIPANT_ID,
            request_prefix="break",
        )
        active = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        with pytest.raises(HarnessClientError) as caught:
            client.break_resource(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                lease_id=active["lease_id"],
                lease_revision=active["lease_revision"],
                request_id="break-active",
            )
        assert caught.value.code == "operation.precondition-failed"
        security = host.security
        assert security is not None
        assert security.adapter.present_calls == 0  # type: ignore[attr-defined]

        driver.running_bindings.discard(ready["runtime_binding_id"])
        assert host.run_resource_supervision_once() == {"observed": 0, "stale": 1}
        stale = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert stale["status"] == "stale"
        released = client.break_resource(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            lease_id=stale["lease_id"],
            lease_revision=stale["lease_revision"],
            request_id="break-stale",
        )["resource"]
        assert released["status"] == "released"
        assert released["release_evidence_sha256"] is not None
        assert security.adapter.present_calls == 1  # type: ignore[attr-defined]


def test_restart_finishes_durably_authorized_resource_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root, with_security=True) as (host, client, driver):
        opened, ready = _start_ready_participant(client)
        driver.running_bindings.discard(ready["runtime_binding_id"])
        assert host.run_resource_supervision_once() == {"observed": 0, "stale": 1}
        stale = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        original_finalize = host.store.finalize_resource_break

        def crash_before_finalize(**kwargs: Any) -> tuple[str, dict[str, Any]]:
            raise SystemExit("simulated resource break crash")

        monkeypatch.setattr(
            host.store, "finalize_resource_break", crash_before_finalize
        )
        with pytest.raises(SystemExit, match="resource break crash"):
            host.store.break_resource(
                request_id="break-crash",
                request_digest="f" * 64,
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                lease_id=stale["lease_id"],
                lease_revision=stale["lease_revision"],
                consumption_evidence_sha256="e" * 64,
            )
        monkeypatch.setattr(host.store, "finalize_resource_break", original_finalize)
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert durable["requests"]["break-crash"]["status"] == "pending"
        durable_lease = next(
            iter(next(iter(durable["scenarios"].values()))["resource_leases"].values())
        )
        assert durable_lease["status"] == "stale"

    with running_host(state_root, with_security=True) as (_, client, _):
        released = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"][0]
        assert released["status"] == "released"
        assert released["release_evidence_sha256"] == "e" * 64
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        assert durable["requests"]["break-crash"]["status"] == "completed"
        assert len(
            next(iter(durable["scenarios"].values()))["resource_break_history"]
        ) == 1


def test_clean_participant_close_failure_allows_scenario_repair(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="clean-close-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="clean-close",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="clean-close-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="clean-close",
            )
        degraded = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        with pytest.raises(HarnessClientError) as failed:
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=degraded["scenario_generation"],
                scenario_state_revision=degraded["state_revision"],
                drain_timeout_ms=25,
                request_id="clean-close-failed",
            )
        assert failed.value.code == "operation.external-failure"

        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        participant = diagnostic["participants"][0]
        assert participant["desired_state"] == "stopped"
        assert participant["observed_state"] == "degraded"
        assert participant["runtime_binding_id"] is None
        assert participant["presentation_binding_id"] is None
        assert client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"] == []

        common = {
            "project_instance_id": PROJECT_ID,
            "scenario_id": SCENARIO_ID,
            "scenario_generation": diagnostic["scenario"]["scenario_generation"],
            "scenario_state_revision": diagnostic["scenario"]["state_revision"],
        }
        repair = host.store.scenario_high_risk_preview(
            **common, operation="scenario.repair"
        )
        destroy = host.store.scenario_high_risk_preview(
            **common, operation="scenario.destroy"
        )
        assert repair["blockers"] == []
        assert repair["eligible"] is True
        assert destroy["blockers"] == [
            "participant.not-detached-or-stopped",
            "scenario.not-closed",
        ]


def test_repaired_incomplete_close_preserves_resume_targets(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    second_id = "participant-two"
    with running_host(state_root) as (host, client, driver):
        driver.per_participant_resource_digests = True
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="repair-resume-create",
        )["scenario"]
        for participant_id in (PARTICIPANT_ID, second_id):
            _add_test_participant(
                client,
                scenario=created,
                participant_id=participant_id,
                request_prefix=f"repair-resume-{participant_id}",
            )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="repair-resume-open-initial",
        )["scenario"]
        driver.fail_start_participant_ids = {PARTICIPANT_ID}
        started = client.start_scenario_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            request_id="repair-resume-start-all",
        )
        assert started["start_summary"]["counts"]["failed"] == 1
        assert started["start_summary"]["counts"]["started"] == 1

        degraded = started["scenario"]
        with pytest.raises(HarnessClientError):
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=degraded["scenario_generation"],
                scenario_state_revision=degraded["state_revision"],
                drain_timeout_ms=25,
                request_id="repair-resume-close",
            )
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["latest_close"]["all_closed"] is False
        assert {
            target["participant_id"]
            for target in diagnostic["latest_close"]["restore_targets"]
        } == {PARTICIPANT_ID, second_id}

        scenario = diagnostic["scenario"]
        repair_id, replay, _ = host.store.begin_scenario_repair(
            request_id="repair-resume-repair",
            request_digest="a" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            expected_wip_summary_digest="b" * 64,
        )
        assert replay is None
        repaired = host.store.finalize_scenario_repair(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="repair-resume-repair",
            operation_id=repair_id,
            workspace_evidence_sha256="c" * 64,
        )["scenario"]
        assert repaired["observed_state"] == "closed"

        driver.fail_start_participant_ids.clear()
        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
            request_id="repair-resume-open-final",
        )
        assert resumed["scenario"]["observed_state"] == "running"
        reports = resumed["resume_summary"]["reports"]
        assert {report["participant_id"] for report in reports} == {
            PARTICIPANT_ID,
            second_id,
        }
        assert {report["outcome"] for report in reports} == {"recreated"}
        assert all(report["repair_required"] is False for report in reports)
        assert driver.start_calls == 4


@pytest.mark.parametrize("mode", ["timeout", "unknown"])
def test_scenario_safe_close_fails_closed_and_preserves_owned_binding(
    tmp_path: Path, mode: str
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        opened, ready = _start_ready_participant(client)
        driver.close_mode = mode
        for _ in range(2):
            with pytest.raises(
                HarnessClientError,
                match="requires repair or explicit high-risk action",
            ) as exc:
                client.close_scenario(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    scenario_generation=opened["scenario_generation"],
                    scenario_state_revision=opened["state_revision"],
                    drain_timeout_ms=25,
                    request_id=f"close-{mode}",
                )
            assert exc.value.code == "operation.external-failure"
            assert exc.value.retryable is False
        assert driver.close_calls == 1
        assert driver.running
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        assert diagnostic["scenario"]["desired_state"] == "closed"
        assert diagnostic["scenario"]["observed_state"] == "degraded"
        assert diagnostic["repair_actions"] == [
            "participant.recover",
            "resource.inspect",
            "scenario.repair",
        ]
        participant = diagnostic["participants"][0]
        assert participant["desired_state"] == "stopped"
        assert participant["observed_state"] == "degraded"
        assert participant["runtime_binding_id"] == ready["runtime_binding_id"]
        preview = host.store.scenario_high_risk_preview(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=diagnostic["scenario"]["scenario_generation"],
            scenario_state_revision=diagnostic["scenario"]["state_revision"],
            operation="scenario.repair",
        )
        assert "participant.cleanup-pending" in preview["blockers"]
        report = diagnostic["latest_close"]["reports"][0]
        assert report["classification"] == mode
        assert report["closed"] is False
        assert diagnostic["latest_close"]["auto_force_stop_used"] is False
        with pytest.raises(StoreError) as restore_blocked:
            host.store.scenario_restore_plan(PROJECT_ID, SCENARIO_ID)
        assert restore_blocked.value.code == "scenario.restore-plan-invalid"
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        close_operation = next(
            value
            for value in durable["operations"].values()
            if value["operation_kind"] == "scenario.close"
        )
        assert close_operation["state"] == "failed"
        assert close_operation["failure_code"] == "lifecycle.close-incomplete"


def test_scenario_close_stale_fence_fails_before_driver_or_state_mutation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, _ = _start_ready_participant(client)
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError) as exc:
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"] + 1,
                drain_timeout_ms=25,
                request_id="close-stale-fence",
            )
        assert exc.value.code == "fence.stale-operation-generation"
        assert driver.close_calls == 0
        assert driver.running
        assert (state_root / "host-state.json").read_bytes() == before


def test_invalid_driver_close_reply_is_reported_as_unknown_action_outcome(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, _ = _start_ready_participant(client)
        driver.close_mode = "invalid"
        with pytest.raises(HarnessClientError) as exc:
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                drain_timeout_ms=25,
                request_id="close-invalid-driver",
            )
        assert exc.value.code == "operation.external-failure"
        diagnostic = client.scenario_diagnostic(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["diagnostic"]
        report = diagnostic["latest_close"]["reports"][0]
        assert report["classification"] == "unknown"
        assert report["closed"] is False
        assert report["action_outcome_known"] is False
        assert report["drain_requested"] is None
        assert report["progress_event_count"] is None
        assert diagnostic["latest_close"]["auto_force_stop_used"] is False
        assert driver.running


def test_participant_fences_fail_before_driver_call(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
        )["scenario"]
        added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
        )["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
        )["scenario"]
        before = (state_root / "host-state.json").read_bytes()
        with pytest.raises(HarnessClientError) as exc:
            client.start_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                participant_generation=added["participant_generation"] + 1,
                participant_state_revision=added["state_revision"],
            )
        assert exc.value.code == "fence.stale-operation-generation"
        assert not driver.running
        assert (state_root / "host-state.json").read_bytes() == before


def test_participant_start_failure_is_durable_and_replayed(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
        )["scenario"]
        added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
        )["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
        )["scenario"]
        driver.fail_start = True
        for _ in range(2):
            with pytest.raises(HarnessClientError) as exc:
                client.start_participant(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    participant_id=PARTICIPANT_ID,
                    scenario_generation=1,
                    scenario_state_revision=opened["state_revision"],
                    participant_generation=added["participant_generation"],
                    participant_state_revision=added["state_revision"],
                    request_id="failed-start",
                )
            assert exc.value.code == "operation.external-failure"
        assert driver.start_calls == 1
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        assert item["record"]["observed_state"] == "degraded"
        participant = item["participants"][PARTICIPANT_ID]
        assert participant["observed_state"] == "degraded"
        assert participant["degraded"]["reason"] == "launch_failed"
        operation = next(
            value
            for value in durable["operations"].values()
            if value["operation_kind"] == "participant.start"
        )
        assert operation["state"] == "failed"
        assert operation["failure_code"] == "lifecycle.launch-failed"


def test_participant_recover_rotates_only_failed_generation_then_allows_start(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    sibling_id = "participant-two"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="recover-create",
        )["scenario"]
        failed_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="recover-failed",
        )
        sibling_added = _add_test_participant(
            client,
            scenario=created,
            participant_id=sibling_id,
            request_prefix="recover-sibling",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="recover-open",
        )["scenario"]
        sibling_ready = _start_test_participant(
            client,
            scenario=opened,
            participant=sibling_added,
            participant_id=sibling_id,
            request_prefix="recover-sibling",
        )
        driver.fail_start_participant_ids.add(PARTICIPANT_ID)
        with pytest.raises(HarnessClientError) as caught:
            _start_test_participant(
                client,
                scenario=opened,
                participant=failed_added,
                participant_id=PARTICIPANT_ID,
                request_prefix="recover-failed",
            )
        assert caught.value.code == "operation.external-failure"

        degraded_scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        resumed_without_close = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=degraded_scenario["scenario_generation"],
            scenario_state_revision=degraded_scenario["state_revision"],
            request_id="recover-empty-resume",
        )
        assert resumed_without_close["resume_summary"]["target_count"] == 0
        assert resumed_without_close["resume_summary"]["all_targets_ready"] is False
        degraded_scenario = resumed_without_close["scenario"]
        assert degraded_scenario["observed_state"] == "degraded"

        degraded = next(
            value
            for value in client.list_participants(
                project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
            )["participants"]
            if value["participant_id"] == PARTICIPANT_ID
        )
        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=degraded_scenario["scenario_generation"],
            scenario_state_revision=degraded_scenario["state_revision"],
            participant_generation=degraded["participant_generation"],
            participant_state_revision=degraded["state_revision"],
            request_id="recover-exact",
        )["participant"]
        assert recovered["participant_generation"] == 2
        assert recovered["observed_state"] == "stopped"
        assert recovered["desired_state"] == "stopped"
        assert recovered["runtime_binding_id"] is None
        assert recovered["presentation_binding_id"] is None
        assert driver.repair_calls == [1]
        assert sibling_ready["runtime_binding_id"] in driver.running_bindings

        running_scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert running_scenario["observed_state"] == "running"
        driver.fail_start_participant_ids.clear()
        driver.resource_digest = "4" * 64
        restarted = _start_test_participant(
            client,
            scenario=running_scenario,
            participant=recovered,
            participant_id=PARTICIPANT_ID,
            request_prefix="recover-new-generation",
        )
        assert restarted["participant_generation"] == 2
        assert restarted["observed_state"] == "ready"
        assert driver.start_generations[-1] == 2

        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        history = item["participant_artifacts"][PARTICIPANT_ID]["history"]
        recovery_entries = [value for value in history if "recovery" in value]
        assert recovery_entries[0]["participant_generation"] == 1
        assert recovery_entries[0]["recovery"]["private_generation_retained"] is True


def test_host_restart_reconciles_legacy_running_scenario_participant_fault(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="legacy-fault-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="legacy-fault",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="legacy-fault-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="legacy-fault",
            )

    state_path = state_root / "host-state.json"
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    item = next(iter(durable["scenarios"].values()))
    item["record"]["observed_state"] = "running"
    item["record"]["degraded"] = None
    item["record"]["state_revision"] += 1
    durable["state_revision"] += 1
    state_path.write_text(
        json.dumps(durable, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with running_host(state_root) as (_, client, driver):
        reconciled = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        assert reconciled["observed_state"] == "degraded"
        assert reconciled["degraded"]["reason"] == "participant_fault"
        assert reconciled["degraded"]["repair_action"] == "participant.recover"
        participant = client.list_participants(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["participants"][0]
        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=reconciled["scenario_generation"],
            scenario_state_revision=reconciled["state_revision"],
            participant_generation=participant["participant_generation"],
            participant_state_revision=participant["state_revision"],
            request_id="legacy-fault-recover",
        )["participant"]
        assert recovered["participant_generation"] == 2
        assert recovered["observed_state"] == "stopped"
        assert driver.repair_calls == [1]


def test_scenario_repair_keeps_unrecovered_participant_fault_repairable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="repair-participant-fault-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="repair-participant-fault",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="repair-participant-fault-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="repair-participant-fault",
            )

        degraded = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        participant = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert degraded["observed_state"] == "degraded"
        assert degraded["degraded"]["reason"] == "participant_fault"
        assert degraded["degraded"]["repair_action"] == "participant.recover"

        operation_id, replay, _ = host.store.begin_scenario_repair(
            request_id="repair-participant-fault-scenario-repair",
            request_digest="4" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
            expected_wip_summary_digest="5" * 64,
        )
        assert replay is None
        repaired = host.store.finalize_scenario_repair(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="repair-participant-fault-scenario-repair",
            operation_id=operation_id,
            workspace_evidence_sha256="6" * 64,
        )["scenario"]
        assert repaired["observed_state"] == "degraded"
        assert repaired["degraded"]["reason"] == "participant_fault"
        assert repaired["degraded"]["cleanup_pending"] is True
        assert repaired["degraded"]["repair_action"] == "participant.recover"

        driver.fail_start = False
        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
            participant_generation=participant["participant_generation"],
            participant_state_revision=participant["state_revision"],
            request_id="repair-participant-fault-recover",
        )["participant"]
        assert recovered["observed_state"] == "stopped"
        assert driver.repair_calls == [1]
        final = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        assert final["observed_state"] == "running"
        assert final["degraded"] is None


def test_scenario_repair_keeps_closed_participant_fault_resumable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="closed-participant-fault-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="closed-participant-fault",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="closed-participant-fault-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="closed-participant-fault",
            )

        degraded = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        operation_id, replay, _ = host.store.begin_scenario_repair(
            request_id="closed-participant-fault-scenario-repair",
            request_digest="7" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
            expected_wip_summary_digest="8" * 64,
        )
        assert replay is None
        with host.store._lock:  # noqa: SLF001 - legacy durable edge fixture
            durable = host.store._read_state()  # noqa: SLF001
            item = next(iter(durable["scenarios"].values()))
            item["record"]["desired_state"] = "closed"
            durable["state_revision"] += 1
            host.store._write_state(durable)  # noqa: SLF001

        repaired = host.store.finalize_scenario_repair(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="closed-participant-fault-scenario-repair",
            operation_id=operation_id,
            workspace_evidence_sha256="9" * 64,
        )["scenario"]

        assert repaired["desired_state"] == "closed"
        assert repaired["observed_state"] == "degraded"
        assert repaired["degraded"]["reason"] == "participant_fault"
        assert repaired["degraded"]["repair_action"] == "scenario.open"

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=repaired["scenario_generation"],
            scenario_state_revision=repaired["state_revision"],
            request_id="closed-participant-fault-resume",
        )["scenario"]

        assert resumed["desired_state"] == "running"
        assert resumed["observed_state"] == "degraded"
        assert resumed["degraded"]["reason"] == "participant_fault"
        assert resumed["degraded"]["repair_action"] == "participant.recover"


def test_closed_incomplete_close_recovers_participant_in_one_step(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        opened, ready = _start_ready_participant(client)
        driver.close_mode = "timeout"
        with pytest.raises(HarnessClientError):
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                drain_timeout_ms=25,
                request_id="closed-recover-close",
            )

        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        participant = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert scenario["desired_state"] == "closed"
        assert scenario["degraded"]["reason"] == "cleanup_pending"
        assert participant["degraded"]["repair_action"] == "participant.recover"

        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            participant_generation=participant["participant_generation"],
            participant_state_revision=participant["state_revision"],
            request_id="closed-recover-participant",
        )["participant"]

        assert (
            recovered["participant_generation"]
            == ready["participant_generation"] + 1
        )
        assert recovered["observed_state"] == "stopped"
        final = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        assert final["observed_state"] == "closed"
        assert final["degraded"] is None
        assert driver.repair_calls == [ready["participant_generation"]]
        resources = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"]
        assert resources
        assert all(resource["status"] == "released" for resource in resources)

        resumed = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=final["scenario_generation"],
            scenario_state_revision=final["state_revision"],
            request_id="closed-recover-resume",
        )
        assert resumed["scenario"]["observed_state"] == "running"
        assert resumed["scenario"]["degraded"] is None
        assert resumed["resume_summary"]["target_count"] == 0
        assert resumed["resume_summary"]["reports"] == []


def test_closed_participant_recovery_does_not_hide_an_unreleased_lease(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        opened, _ = _start_ready_participant(client)
        driver.close_mode = "timeout"
        with pytest.raises(HarnessClientError):
            client.close_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                drain_timeout_ms=25,
                request_id="closed-recover-stale-lease-close",
            )

        with host.store._lock:  # noqa: SLF001 - durable edge fixture
            durable = host.store._read_state()  # noqa: SLF001
            item = next(iter(durable["scenarios"].values()))
            lease = next(iter(item["resource_leases"].values()))
            stale_lease = json.loads(json.dumps(lease))
            stale_lease["lease_id"] = "lease-stale-previous-binding"
            stale_lease["lease_revision"] += 1
            stale_lease["holder"]["runtime_binding_id"] = "runtime-binding-previous"
            stale_lease["status"] = "stale"
            stale_lease["stale_reason"] = "binding_changed"
            stale_lease["release_evidence_sha256"] = None
            item["resource_leases"][stale_lease["lease_id"]] = stale_lease
            durable["state_revision"] += 1
            host.store._write_state(durable)  # noqa: SLF001

        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        participant = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            participant_generation=participant["participant_generation"],
            participant_state_revision=participant["state_revision"],
            request_id="closed-recover-stale-lease-participant",
        )["participant"]

        assert recovered["observed_state"] == "stopped"
        final = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        assert final["observed_state"] == "degraded"
        assert final["degraded"]["reason"] == "cleanup_pending"
        resources = client.list_resources(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["resources"]
        assert any(resource["status"] != "released" for resource in resources)


def test_scenario_repair_keeps_destroyed_participant_fault_non_resumable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="destroyed-participant-fault-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="destroyed-participant-fault",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="destroyed-participant-fault-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="destroyed-participant-fault",
            )

        degraded = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        operation_id, replay, _ = host.store.begin_scenario_repair(
            request_id="destroyed-participant-fault-scenario-repair",
            request_digest="a" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=degraded["scenario_generation"],
            scenario_state_revision=degraded["state_revision"],
            expected_wip_summary_digest="b" * 64,
        )
        assert replay is None
        with host.store._lock:  # noqa: SLF001 - legacy durable edge fixture
            durable = host.store._read_state()  # noqa: SLF001
            item = next(iter(durable["scenarios"].values()))
            item["record"]["desired_state"] = "destroyed"
            durable["state_revision"] += 1
            host.store._write_state(durable)  # noqa: SLF001

        repaired = host.store.finalize_scenario_repair(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            request_id="destroyed-participant-fault-scenario-repair",
            operation_id=operation_id,
            workspace_evidence_sha256="c" * 64,
        )["scenario"]

        assert repaired["desired_state"] == "destroyed"
        assert repaired["observed_state"] == "degraded"
        assert repaired["degraded"]["reason"] == "participant_fault"
        assert repaired["degraded"]["repair_action"] == "scenario.force-destroy"
        with pytest.raises(HarnessClientError):
            client.open_scenario(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=repaired["scenario_generation"],
                scenario_state_revision=repaired["state_revision"],
                request_id="destroyed-participant-fault-resume",
            )


def test_participant_recover_failure_stays_degraded_without_generation_rotation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="recover-failure-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="recover-failure",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            request_id="recover-failure-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="recover-failure",
            )
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        degraded = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        driver.fail_repair = True
        with pytest.raises(HarnessClientError) as caught:
            client.recover_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=scenario["scenario_generation"],
                scenario_state_revision=scenario["state_revision"],
                participant_generation=degraded["participant_generation"],
                participant_state_revision=degraded["state_revision"],
                request_id="recover-failure-exact",
            )
        assert caught.value.code == "operation.external-failure"
        after = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert after["participant_generation"] == 1
        assert after["observed_state"] == "degraded"
        assert after["degraded"]["repair_action"] == "participant.recover"


def test_recover_retains_and_cleans_post_launch_binding_after_supervision_failure(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="recover-supervision-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="recover-supervision",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            request_id="recover-supervision-open",
        )["scenario"]
        driver.fail_supervision = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="recover-supervision",
            )
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        degraded = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert degraded["runtime_binding_id"] == "runtime-binding-one"
        assert degraded["presentation_binding_id"] == "presentation-instance-one"
        assert degraded["runtime_binding_id"] in driver.running_bindings

        driver.fail_supervision = False
        recovered = client.recover_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            participant_generation=degraded["participant_generation"],
            participant_state_revision=degraded["state_revision"],
            request_id="recover-supervision-exact",
        )["participant"]
        assert recovered["participant_generation"] == 2
        assert recovered["observed_state"] == "stopped"
        assert "runtime-binding-one" not in driver.running_bindings


def test_restart_finalizes_recovery_from_durable_external_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="recover-crash-create",
        )["scenario"]
        added = _add_test_participant(
            client,
            scenario=created,
            participant_id=PARTICIPANT_ID,
            request_prefix="recover-crash",
        )
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            request_id="recover-crash-open",
        )["scenario"]
        driver.fail_start = True
        with pytest.raises(HarnessClientError):
            _start_test_participant(
                client,
                scenario=opened,
                participant=added,
                participant_id=PARTICIPANT_ID,
                request_prefix="recover-crash",
            )
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]
        degraded = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        driver.fail_start = False

        def crash_before_finalize(**_: object) -> dict[str, object]:
            raise SystemExit("simulated Host crash after recovery evidence")

        monkeypatch.setattr(
            host.store, "finalize_participant_recover", crash_before_finalize
        )
        assert host.participants is not None
        with pytest.raises(SystemExit, match="simulated Host crash"):
            host.participants.recover(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=PARTICIPANT_ID,
                scenario_generation=scenario["scenario_generation"],
                scenario_state_revision=scenario["state_revision"],
                participant_generation=degraded["participant_generation"],
                participant_state_revision=degraded["state_revision"],
                request_id="recover-crash-request",
                request_digest="d" * 64,
                host_generation=host.host_generation,
            )
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        pending = durable["requests"]["recover-crash-request"]
        assert pending["status"] == "pending"
        assert pending["pending_external_result"]["recovery"]["recovered"] is True

    with running_host(state_root) as (_, client, driver):
        recovered = client.list_participants(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["participants"][0]
        assert recovered["participant_generation"] == 2
        assert recovered["observed_state"] == "stopped"
        assert recovered["degraded"] is None
        assert driver.repair_calls == []
        assert client.scenario_status(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["scenario"]["observed_state"] == "running"


def test_restart_marks_unknown_external_participant_operation_repair_required(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
        )["scenario"]
        added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
        )["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
        )["scenario"]
        operation_id, replay, execution = host.store.begin_participant_start(
            request_id="unknown-start",
            request_digest="9" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=added["participant_generation"],
            participant_state_revision=added["state_revision"],
        )
        assert replay is None
        assert execution is not None

    with running_host(state_root) as (_, client, _):
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        item = next(iter(durable["scenarios"].values()))
        participant = item["participants"][PARTICIPANT_ID]
        assert participant["observed_state"] == "degraded"
        assert participant["degraded"]["reason"] == "operation_unknown"
        assert item["record"]["observed_state"] == "degraded"
        operation = durable["operations"][operation_id]
        assert operation["state"] == "repair_required"
        assert operation["mutation_state"] == "unknown"
        assert operation["failure_code"] == "lifecycle.operation-outcome-unknown"
        assert client.participant_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=1,
            scenario_state_revision=item["record"]["state_revision"],
            participant_generation=participant["participant_generation"],
            participant_state_revision=participant["state_revision"],
        )["participant"] == participant


def test_cli_controls_generic_participant_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
        )["scenario"]
        connection = [
            "--state-root",
            str(state_root),
            "--socket-path",
            str(host.socket_path),
            "--json",
        ]
        assert cli_main.main(
            [
                "harness",
                "participant",
                "add",
                PARTICIPANT_ID,
                "--scenario-id",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--scenario-generation",
                "1",
                "--scenario-state-revision",
                str(created["state_revision"]),
                "--launch-spec-json",
                json.dumps(_launch_spec()),
                "--presentation-driver-id",
                "presentation.iterm2",
                *connection,
            ]
        ) == 0
        added = json.loads(capsys.readouterr().out)["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
        )["scenario"]

        common = [
            PARTICIPANT_ID,
            "--scenario-id",
            SCENARIO_ID,
            "--project-instance-id",
            PROJECT_ID,
            "--scenario-generation",
            "1",
            "--scenario-state-revision",
            str(opened["state_revision"]),
            "--participant-generation",
            str(added["participant_generation"]),
        ]
        assert cli_main.main(
            [
                "harness",
                "participant",
                "start",
                *common,
                "--participant-state-revision",
                str(added["state_revision"]),
                *connection,
            ]
        ) == 0
        ready = json.loads(capsys.readouterr().out)["participant"]
        assert cli_main.main(
            [
                "harness",
                "participant",
                "status",
                *common,
                "--participant-state-revision",
                str(ready["state_revision"]),
                *connection,
            ]
        ) == 0
        assert json.loads(capsys.readouterr().out)["participant"] == ready
        assert cli_main.main(
            [
                "harness",
                "scenario",
                "topology",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                *connection,
            ]
        ) == 0
        assert json.loads(capsys.readouterr().out)["topology"]["action"] == "inspect"
        assert cli_main.main(
            [
                "harness",
                "scenario",
                "focus",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--scenario-generation",
                "1",
                "--state-revision",
                str(opened["state_revision"]),
                *connection,
            ]
        ) == 0
        assert json.loads(capsys.readouterr().out)["topology"]["action"] == "focus"
        assert cli_main.main(
            [
                "harness",
                "resource",
                "list",
                "--scenario-id",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                *connection,
            ]
        ) == 0
        assert json.loads(capsys.readouterr().out)["resources"][0][
            "status"
        ] == "active"
        assert cli_main.main(
            [
                "harness",
                "participant",
                "stop",
                *common,
                "--participant-state-revision",
                str(ready["state_revision"]),
                *connection,
            ]
        ) == 0
        stopped = json.loads(capsys.readouterr().out)["participant"]
        assert stopped["observed_state"] == "stopped"
        assert cli_main.main(
            [
                "harness",
                "participant",
                "replace",
                *common,
                "--participant-state-revision",
                str(stopped["state_revision"]),
                "--launch-spec-json",
                json.dumps(
                    {
                        **_launch_spec(),
                        "runtime_profile_ref": "runtime-profile.replacement",
                    }
                ),
                "--presentation-driver-id",
                "presentation.iterm2",
                *connection,
            ]
        ) == 0
        replaced = json.loads(capsys.readouterr().out)["participant"]
        assert replaced["participant_generation"] == 2
        assert replaced["observed_state"] == "stopped"
        assert cli_main.main(
            [
                "harness",
                "participant",
                "status",
                PARTICIPANT_ID,
                "--scenario-id",
                SCENARIO_ID,
                "--project-instance-id",
                PROJECT_ID,
                "--scenario-generation",
                "1",
                "--scenario-state-revision",
                str(opened["state_revision"]),
                "--participant-generation",
                str(replaced["participant_generation"]),
                "--participant-state-revision",
                str(replaced["state_revision"]),
                *connection,
            ]
        ) == 0
        observed = json.loads(capsys.readouterr().out)["participant"]
        assert observed["participant_generation"] == 2
        assert observed["observed_state"] == "stopped"


def test_bind_workspace_directory_prefers_the_receipt_declaration() -> None:
    """The provisioned receipt, not the runtime profile, decides where a
    participant launches; without a declaration the execution is untouched."""

    def summary(_project: str, _scenario: str) -> dict[str, object]:
        return {"receipt": {"participant_working_directory": "bundle/someproject"}}

    coordinator = ParticipantCoordinator(
        None, None, workspace_summary=summary  # type: ignore[arg-type]
    )
    execution: dict[str, object] = {"workspace_path": "/tmp/ws"}
    coordinator._bind_workspace_directory(execution, "project-1", "scenario-1")
    assert execution["participant_working_directory"] == "bundle/someproject"


def test_bind_workspace_directory_leaves_execution_alone_without_declaration() -> None:
    cases = [
        lambda _p, _s: None,  # no workspace binding at all
        lambda _p, _s: {"receipt": None},  # planned but never provisioned
        lambda _p, _s: {"receipt": {}},  # receipt predates the field
        lambda _p, _s: {"receipt": {"participant_working_directory": 7}},
        lambda _p, _s: {"receipt": {"participant_working_directory": ""}},
    ]
    for summary in cases:
        coordinator = ParticipantCoordinator(
            None, None, workspace_summary=summary  # type: ignore[arg-type]
        )
        execution: dict[str, object] = {"workspace_path": "/tmp/ws"}
        coordinator._bind_workspace_directory(execution, "project-1", "scenario-1")
        assert "participant_working_directory" not in execution
    coordinator = ParticipantCoordinator(None, None)  # type: ignore[arg-type]
    execution = {"workspace_path": "/tmp/ws"}
    coordinator._bind_workspace_directory(execution, "project-1", "scenario-1")
    assert "participant_working_directory" not in execution


def test_start_execution_carries_the_provisioned_working_directory(
    tmp_path: Path,
) -> None:
    """The coordinator injects the receipt-declared project directory into the
    driver's start payload. The driver-side preference for that value over the
    profile's static working directory is covered by the driver tests."""
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        assert host.participants is not None
        host.participants._workspace_summary = lambda _project, _scenario: {
            "receipt": {"participant_working_directory": "bundle/someproject"}
        }
        created = client.create_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            project_binding_digest=PROJECT_DIGEST,
            request_id="create",
        )["scenario"]
        added = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="add",
        )["participant"]
        opened = client.open_scenario(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=created["scenario_generation"],
            scenario_state_revision=created["state_revision"],
            request_id="open",
        )["scenario"]
        client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=PARTICIPANT_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=added["participant_generation"],
            participant_state_revision=added["state_revision"],
            request_id="start",
        )
        assert driver.start_working_directories == ["bundle/someproject"]
