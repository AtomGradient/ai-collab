# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Generic participant runtime/presentation driver orchestration."""

from __future__ import annotations

import copy
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .protocol import ProtocolError, canonical_json_sha256, validate_runtime_launch_spec
from .store import ScenarioStore, StoreError
from .workspace import ProjectAdapterCommand, WorkspaceError


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAMESPACED_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
PARTICIPANT_START_TIMEOUT_SECONDS = 420.0
SUPERVISION_SCHEMA_VERSION = 1
SUPERVISION_TIMEOUT_SECONDS = 5.0
RESOURCE_CLASSES = {
    "port",
    "device",
    "compute",
    "accelerator",
    "exclusive_runtime",
}


@dataclass
class ParticipantError(ValueError):
    code: str
    message: str
    retryable: bool = False
    mutation_state: str = "not_started"
    operation_id: str | None = None

    def __str__(self) -> str:
        return self.message


class ParticipantDriverCommand:
    """Use the project-neutral external adapter envelope for participant drivers."""

    def __init__(self, config_path: Path):
        try:
            self._command = ProjectAdapterCommand(config_path)
        except WorkspaceError as exc:
            raise ParticipantError(
                exc.code.replace("adapter.", "driver."),
                "participant driver config is invalid",
                exc.retryable,
            ) from exc
        self.adapter_id = self._command.adapter_id

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        try:
            return self._command.call(
                operation, payload, timeout_seconds=timeout_seconds
            )
        except WorkspaceError as exc:
            raise ParticipantError(
                exc.code.replace("adapter.", "driver."),
                str(exc).replace("project adapter", "participant driver"),
                exc.retryable,
            ) from exc


class ParticipantCoordinator:
    """Compose a generic versioned driver with durable participant lifecycle state."""

    def __init__(
        self,
        store: ScenarioStore,
        driver: ParticipantDriverCommand,
        *,
        workspace_summary: Callable[[str, str], dict[str, Any] | None] | None = None,
    ):
        self.store = store
        self.driver = driver
        self._workspace_summary = workspace_summary

    def _bind_workspace_directory(
        self,
        execution: dict[str, Any],
        project_instance_id: str,
        scenario_id: str,
    ) -> None:
        """Launch the participant inside the provisioned project checkout.

        The workspace receipt is where the adapter declares which directory
        inside the published bundle is the project root; the runtime profile
        registry is product-generic and cannot know it. Receipts provisioned
        before the field existed simply leave the profile's own working
        directory in effect.
        """
        if self._workspace_summary is None:
            return
        summary = self._workspace_summary(project_instance_id, scenario_id)
        receipt = summary.get("receipt") if isinstance(summary, dict) else None
        value = (
            receipt.get("participant_working_directory")
            if isinstance(receipt, dict)
            else None
        )
        if isinstance(value, str) and value:
            execution["participant_working_directory"] = value

    def add(
        self,
        *,
        request_id: str,
        request_digest: str,
        host_generation: int,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        launch_spec: dict[str, Any],
        note: str = "",
        presentation_driver_id: str | None,
    ) -> tuple[str, dict[str, Any]]:
        resolved = self.driver.call(
            "resolve",
            {
                "launch_spec": launch_spec,
                "presentation_driver_id": presentation_driver_id,
            },
        )
        self._validate_resolved(
            resolved,
            launch_spec=launch_spec,
            presentation_driver_id=presentation_driver_id,
        )
        return self.store.add_participant(
            request_id=request_id,
            request_digest=request_digest,
            host_generation=host_generation,
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            launch_spec=launch_spec,
            resolved_driver=resolved,
            note=note,
        )

    def list_templates(self) -> dict[str, Any]:
        result = self.driver.call("list_templates", {})
        templates = result.get("templates") if isinstance(result, dict) else None
        if (
            not isinstance(templates, list)
            or any(
                not isinstance(item, dict)
                or set(item)
                != {
                    "template_id",
                    "display_name",
                    "launch_spec",
                    "presentation_driver_id",
                }
                for item in templates
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant template registry differs"
            )
        template_ids: set[str] = set()
        for item in templates:
            template_id = item["template_id"]
            presentation_driver_id = item["presentation_driver_id"]
            if (
                not isinstance(template_id, str)
                or not template_id
                or template_id in template_ids
                or not isinstance(item["display_name"], str)
                or not item["display_name"]
                or (
                    presentation_driver_id is not None
                    and (
                        not isinstance(presentation_driver_id, str)
                        or not presentation_driver_id
                    )
                )
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "participant template values differ"
                )
            try:
                validate_runtime_launch_spec(item["launch_spec"])
            except ProtocolError as exc:
                raise ParticipantError(
                    "driver.invalid-reply", "participant template launch spec differs"
                ) from exc
            if (item["launch_spec"]["interaction_mode"] == "tui") != (
                presentation_driver_id is not None
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "participant template presentation differs"
                )
            template_ids.add(template_id)
        return {"templates": copy.deepcopy(templates)}

    def permission_probe(self) -> dict[str, Any]:
        """Return fresh provider-neutral presentation permission observations."""

        return self._permission_observations("permission_probe", allow_prompt=False)

    def permission_request(self) -> dict[str, Any]:
        """Let the platform request presentation permission (explicit user gesture).

        The driver may trigger the operating system's consent prompt; the
        returned observation reflects the user's live decision.
        """

        return self._permission_observations("permission_request", allow_prompt=True)

    def _permission_observations(
        self, operation: str, *, allow_prompt: bool
    ) -> dict[str, Any]:
        result = self.driver.call(operation, {})
        observations = (
            result.get("permission_observations")
            if isinstance(result, dict)
            else None
        )
        if (
            not isinstance(observations, list)
            or not observations
            or len(observations) > 64
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant permission observation differs"
            )
        validated: list[dict[str, Any]] = []
        identities: set[tuple[str, str]] = set()
        for value in observations:
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "permission_id",
                    "provider_ref",
                    "subject_ref",
                    "status",
                    "evidence_digest",
                    "provider_error_code",
                    "remediation_ref",
                    "prompt_requested",
                }
                or not all(
                    isinstance(value[field], str)
                    and NAMESPACED_RE.fullmatch(value[field]) is not None
                    for field in ("permission_id", "provider_ref", "subject_ref")
                )
                or not isinstance(value["status"], str)
                or value["status"] not in {
                    "granted",
                    "denied",
                    "not_determined",
                    "restricted",
                    "unavailable",
                    "unknown",
                }
                or not isinstance(value["evidence_digest"], str)
                or SHA256_RE.fullmatch(value["evidence_digest"]) is None
                or any(
                    item is not None
                    and (
                        not isinstance(item, str)
                        or NAMESPACED_RE.fullmatch(item) is None
                    )
                    for item in (
                        value["provider_error_code"],
                        value["remediation_ref"],
                    )
                )
                or (
                    not isinstance(value["prompt_requested"], bool)
                    if allow_prompt
                    else value["prompt_requested"] is not False
                )
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "participant permission values differ"
                )
            identity = (value["permission_id"], value["subject_ref"])
            if identity in identities:
                raise ParticipantError(
                    "driver.invalid-reply", "participant permission identity differs"
                )
            identities.add(identity)
            validated.append(copy.deepcopy(value))
        return {
            "permission_observations": sorted(
                validated,
                key=lambda value: (value["permission_id"], value["subject_ref"]),
            )
        }

    def environment_probe(self) -> dict[str, Any]:
        """Return fresh provider-neutral machine environment observations.

        The driver decides what to observe from its own data (runtime-profile
        registry, presentation target, shell); this supervisor only enforces
        the provider-neutral record shape, fail closed.
        """

        result = self.driver.call("environment_probe", {})
        observations = (
            result.get("environment_observations")
            if isinstance(result, dict)
            else None
        )
        if (
            not isinstance(observations, list)
            or not observations
            or len(observations) > 64
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant environment observation differs"
            )
        validated: list[dict[str, Any]] = []
        subjects: set[str] = set()
        for value in observations:
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "subject_ref",
                    "display_name",
                    "status",
                    "observed_version",
                    "evidence_digest",
                    "provider_error_code",
                    "remediation_ref",
                }
                or not isinstance(value["subject_ref"], str)
                or NAMESPACED_RE.fullmatch(value["subject_ref"]) is None
                or not isinstance(value["display_name"], str)
                or not 0 < len(value["display_name"]) <= 120
                or not isinstance(value["status"], str)
                or value["status"] not in {"available", "missing", "unknown"}
                # observed_version is displayable text: type and bound only,
                # never a format regex — vendors version however they like.
                or not (
                    value["observed_version"] is None
                    or (
                        isinstance(value["observed_version"], str)
                        and 0 < len(value["observed_version"]) <= 256
                    )
                )
                or not isinstance(value["evidence_digest"], str)
                or SHA256_RE.fullmatch(value["evidence_digest"]) is None
                or any(
                    item is not None
                    and (
                        not isinstance(item, str)
                        or NAMESPACED_RE.fullmatch(item) is None
                    )
                    for item in (
                        value["provider_error_code"],
                        value["remediation_ref"],
                    )
                )
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "participant environment values differ"
                )
            if value["subject_ref"] in subjects:
                raise ParticipantError(
                    "driver.invalid-reply",
                    "participant environment identity differs",
                )
            subjects.add(value["subject_ref"])
            validated.append(copy.deepcopy(value))
        return {
            "environment_observations": sorted(
                validated, key=lambda value: value["subject_ref"]
            )
        }

    def start(
        self,
        *,
        participant_client: Mapping[str, str],
        require_bound_vendor_identity: bool = False,
        **values: Any,
    ) -> tuple[str, dict[str, Any]]:
        self._validate_participant_client(participant_client)
        if not isinstance(require_bound_vendor_identity, bool):
            raise ParticipantError(
                "driver.invalid-reply", "vendor identity requirement differs"
            )
        operation_id, replay, execution = self.store.begin_participant_start(**values)
        if replay is not None:
            return operation_id, replay
        assert execution is not None
        execution["participant_client"] = copy.deepcopy(dict(participant_client))
        self._bind_workspace_directory(
            execution, values["project_instance_id"], values["scenario_id"]
        )
        artifacts: dict[str, Any] | None = None
        try:
            self._ensure_private_root(Path(execution["private_root"]))
            artifacts = self.driver.call(
                "start",
                execution,
                timeout_seconds=PARTICIPANT_START_TIMEOUT_SECONDS,
            )
            self._validate_start_artifacts(execution, artifacts)
            if (
                require_bound_vendor_identity
                and execution["launch_spec"]["continuity_mode"] == "exact_resume"
                and not self._sha256(
                    artifacts["runtime_ready_ack"]["binding"].get(
                        "vendor_session_identity_sha256"
                    )
                )
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "exact resume binding evidence differs"
                )
            observation = self.driver.call(
                "supervise",
                self._supervision_payload(execution, artifacts),
                timeout_seconds=SUPERVISION_TIMEOUT_SECONDS,
            )
            self._validate_supervision_observation(artifacts, observation)
            result = self.store.finalize_participant_start(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                artifacts=artifacts,
                supervision_observation=observation,
            )
            return operation_id, result
        except (ParticipantError, OSError, StoreError, KeyError, TypeError) as exc:
            cleanup_pending = True
            failure_artifacts = artifacts
            cleanup_evidence = None
            if (
                isinstance(exc, StoreError)
                and exc.code == "resource.conflict"
                and artifacts is not None
            ):
                try:
                    stopped = self.driver.call(
                        "stop", self._supervision_payload(execution, artifacts)
                    )
                    self._validate_stop_result(stopped)
                    cleanup_pending = False
                    failure_artifacts = None
                    cleanup_evidence = stopped[
                        "owned_resource_evidence_sha256"
                    ]
                except (ParticipantError, OSError, KeyError, TypeError):
                    failure_artifacts = artifacts
            self._fail_committed(
                values=values,
                operation_id=operation_id,
                reason="launch_failed",
                failure_code="lifecycle.launch-failed",
                cleanup_pending=cleanup_pending,
                failure_artifacts=failure_artifacts,
                owned_resource_evidence_sha256=cleanup_evidence,
            )
            if isinstance(exc, StoreError) and exc.code != "resource.conflict":
                raise
            raise ParticipantError(
                "participant.launch-failed",
                "participant launch failed",
                retryable=True,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc

    def status(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        participant_generation: int,
        participant_state_revision: int,
    ) -> tuple[str, dict[str, Any]]:
        record, artifact = self.store.participant_status_input(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            participant_generation=participant_generation,
            participant_state_revision=participant_state_revision,
        )
        if record["observed_state"] != "ready":
            return f"read-{participant_id}", {"participant": record}
        observation = self.driver.call(
            "status",
            {
                "launch_spec": artifact["launch_spec"],
                "resolved_driver": artifact["resolved_driver"],
                "runtime_ready_ack": artifact["runtime_ready_ack"],
                "presentation_create_ack": artifact["presentation_create_ack"],
                "private_root": str(
                    self.store.participant_private_path(
                        project_instance_id,
                        scenario_id,
                        scenario_generation,
                        participant_id,
                        participant_generation,
                    )
                ),
            },
        )
        expected = {
            "healthy",
            "runtime_binding_id",
            "presentation_binding_id",
        }
        presentation = artifact["presentation_create_ack"]
        expected_presentation = (
            None
            if presentation is None
            else presentation["binding"]["presentation_instance_id"]
        )
        if (
            not isinstance(observation, dict)
            or set(observation) != expected
            or observation["healthy"] is not True
            or observation["runtime_binding_id"] != record["runtime_binding_id"]
            or observation["presentation_binding_id"] != expected_presentation
        ):
            raise ParticipantError(
                "participant.binding-drift",
                "participant binding health differs",
                retryable=True,
            )
        return f"read-{participant_id}", {"participant": record}

    def scenario_presentation(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        action: str,
    ) -> dict[str, Any]:
        """Inspect or focus every current presentation without coupling failures."""

        if action not in {"inspect", "focus"}:
            raise ParticipantError(
                "participant.presentation-invalid", "presentation action differs"
            )
        records = self.store.list_participants(
            project_instance_id, scenario_id
        )["participants"]
        observations: list[dict[str, Any]] = []
        for record in records:
            base = {
                "participant_id": record["participant_id"],
                "participant_generation": record["participant_generation"],
                "interaction_mode": record["interaction_mode"],
            }
            if record["interaction_mode"] != "tui":
                observations.append(
                    {
                        **base,
                        "health": "not_required",
                        "focused": False,
                        "restore_outcome": "not_requested",
                        "geometry": None,
                        "display_topology_fingerprint": None,
                        "error_code": None,
                    }
                )
                continue
            if record["observed_state"] != "ready":
                observations.append(
                    {
                        **base,
                        "health": "not_running",
                        "focused": False,
                        "restore_outcome": "not_requested",
                        "geometry": None,
                        "display_topology_fingerprint": None,
                        "error_code": None,
                    }
                )
                continue
            try:
                current, artifact = self.store.participant_status_input(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    participant_id=record["participant_id"],
                    scenario_generation=scenario_generation,
                    scenario_state_revision=scenario_state_revision,
                    participant_generation=record["participant_generation"],
                    participant_state_revision=record["state_revision"],
                )
                result = self.driver.call(
                    "presentation_action",
                    {
                        "launch_spec": artifact["launch_spec"],
                        "resolved_driver": artifact["resolved_driver"],
                        "runtime_ready_ack": artifact["runtime_ready_ack"],
                        "presentation_create_ack": artifact[
                            "presentation_create_ack"
                        ],
                        "private_root": str(
                            self.store.participant_private_path(
                                project_instance_id,
                                scenario_id,
                                scenario_generation,
                                record["participant_id"],
                                record["participant_generation"],
                            )
                        ),
                        "action": action,
                    },
                )
                observation = self._validate_presentation_observation(
                    result, current, action=action
                )
                observation.pop("participant_generation")
                observation.pop("presentation_instance_id")
                observations.append({**base, **observation, "error_code": None})
            except (ParticipantError, StoreError, OSError, KeyError, TypeError):
                observations.append(
                    {
                        **base,
                        "health": "degraded",
                        "focused": False,
                        "restore_outcome": "not_requested",
                        "geometry": None,
                        "display_topology_fingerprint": None,
                        "error_code": "presentation.action-failed",
                    }
                )
        topology = {
            "schema_version": 1,
            "action": action,
            "participants": observations,
        }
        topology["summary_digest"] = canonical_json_sha256(topology)
        return {"topology": topology}

    def stop(self, **values: Any) -> tuple[str, dict[str, Any]]:
        return self._stop_with_driver("stop", values)

    def force_stop(self, **values: Any) -> tuple[str, dict[str, Any]]:
        """Invoke the distinct exact force-stop driver action after Host auth."""

        return self._stop_with_driver("force_stop", values)

    def recover(self, **values: Any) -> tuple[str, dict[str, Any]]:
        """Recover one exact degraded generation without reusing its identity."""

        operation_id, replay, execution = self.store.begin_participant_recover(
            **values
        )
        if replay is not None:
            return operation_id, replay
        assert execution is not None
        try:
            recovery = self.driver.call("repair", execution)
            self._validate_recovery_result(execution, recovery)
            self.store.record_participant_recovery_evidence(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                recovery=recovery,
            )
            result = self.store.finalize_participant_recover(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                recovery=recovery,
            )
            return operation_id, result
        except (ParticipantError, OSError, StoreError, KeyError, TypeError) as exc:
            self._fail_committed(
                values=values,
                operation_id=operation_id,
                reason="recovery_failed",
                failure_code="lifecycle.recovery-failed",
                cleanup_pending=True,
            )
            if isinstance(exc, StoreError):
                raise
            raise ParticipantError(
                "participant.recovery-failed",
                "participant recovery failed",
                retryable=True,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc

    def replace(
        self,
        *,
        launch_spec: dict[str, Any],
        presentation_driver_id: str | None,
        participant_client_factory: Callable[[int, int], Mapping[str, str]],
        **values: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Validate, clean up, generation-CAS, then conditionally restart."""

        resolved = self.driver.call(
            "resolve",
            {
                "launch_spec": launch_spec,
                "presentation_driver_id": presentation_driver_id,
            },
        )
        self._validate_resolved(
            resolved,
            launch_spec=launch_spec,
            presentation_driver_id=presentation_driver_id,
        )
        operation_id, replay, cleanup = self.store.begin_participant_replace(
            **values,
            launch_spec=launch_spec,
            resolved_driver=resolved,
        )
        if replay is not None:
            return operation_id, replay
        assert cleanup is not None
        generation_committed = False
        cleanup_evidence: str | None = None
        recovery: dict[str, Any] | None = None
        start_execution: dict[str, Any] | None = None
        artifacts: dict[str, Any] | None = None
        try:
            cleanup_kind = cleanup["cleanup_kind"]
            if cleanup_kind == "stop":
                stopped = self.driver.call("stop", cleanup)
                self._validate_stop_result(stopped)
                cleanup_evidence = stopped[
                    "owned_resource_evidence_sha256"
                ]
            elif cleanup_kind == "repair":
                recovery = self.driver.call("repair", cleanup)
                self._validate_recovery_result(cleanup, recovery)
                cleanup_evidence = recovery[
                    "owned_resource_evidence_sha256"
                ]
            if cleanup_kind != "none":
                assert cleanup_evidence is not None
                self.store.record_participant_replacement_cleanup(
                    project_instance_id=values["project_instance_id"],
                    scenario_id=values["scenario_id"],
                    participant_id=values["participant_id"],
                    request_id=values["request_id"],
                    operation_id=operation_id,
                    launch_spec=launch_spec,
                    resolved_driver=resolved,
                    cleanup_kind=cleanup_kind,
                    owned_resource_evidence_sha256=cleanup_evidence,
                    recovery=recovery,
                )
            result, start_execution = self.store.commit_participant_replacement(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                launch_spec=launch_spec,
                resolved_driver=resolved,
                cleanup_kind=cleanup_kind,
                owned_resource_evidence_sha256=cleanup_evidence,
                recovery=recovery,
            )
            generation_committed = True
            if start_execution is None:
                return operation_id, result
            replacement = result["participant"]
            participant_client = participant_client_factory(
                replacement["participant_generation"],
                replacement["state_revision"],
            )
            self._validate_participant_client(participant_client)
            start_execution["participant_client"] = copy.deepcopy(
                dict(participant_client)
            )
            self._bind_workspace_directory(
                start_execution,
                values["project_instance_id"],
                values["scenario_id"],
            )
            self._ensure_private_root(Path(start_execution["private_root"]))
            artifacts = self.driver.call(
                "start",
                start_execution,
                timeout_seconds=PARTICIPANT_START_TIMEOUT_SECONDS,
            )
            self._validate_start_artifacts(start_execution, artifacts)
            observation = self.driver.call(
                "supervise",
                self._supervision_payload(start_execution, artifacts),
                timeout_seconds=SUPERVISION_TIMEOUT_SECONDS,
            )
            self._validate_supervision_observation(artifacts, observation)
            result = self.store.finalize_participant_start(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                artifacts=artifacts,
                supervision_observation=observation,
            )
            return operation_id, result
        except (
            ParticipantError,
            OSError,
            StoreError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            if not generation_committed:
                self.store.fail_participant_replace_before_cas(
                    project_instance_id=values["project_instance_id"],
                    scenario_id=values["scenario_id"],
                    participant_id=values["participant_id"],
                    request_id=values["request_id"],
                    operation_id=operation_id,
                    cleanup_pending=cleanup_evidence is None,
                    owned_resource_evidence_sha256=cleanup_evidence,
                )
            else:
                failure_artifacts = artifacts
                cleanup_pending = True
                replacement_cleanup_evidence = None
                if (
                    isinstance(exc, StoreError)
                    and exc.code == "resource.conflict"
                    and artifacts is not None
                    and start_execution is not None
                ):
                    try:
                        stopped = self.driver.call(
                            "stop",
                            self._supervision_payload(
                                start_execution, artifacts
                            ),
                        )
                        self._validate_stop_result(stopped)
                        cleanup_pending = False
                        failure_artifacts = None
                        replacement_cleanup_evidence = stopped[
                            "owned_resource_evidence_sha256"
                        ]
                    except (ParticipantError, OSError, KeyError, TypeError):
                        pass
                self._fail_committed(
                    values=values,
                    operation_id=operation_id,
                    reason="launch_failed",
                    failure_code="lifecycle.replace-launch-failed",
                    cleanup_pending=cleanup_pending,
                    failure_artifacts=failure_artifacts,
                    owned_resource_evidence_sha256=(
                        replacement_cleanup_evidence
                    ),
                )
            if isinstance(exc, StoreError) and exc.code != "resource.conflict":
                raise
            raise ParticipantError(
                "participant.replace-failed",
                "participant replacement failed",
                retryable=True,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc

    def _stop_with_driver(
        self, driver_operation: str, values: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        operation_id, replay, execution = self.store.begin_participant_stop(
            **values,
            operation_kind=(
                "participant.force-stop"
                if driver_operation == "force_stop"
                else "participant.stop"
            ),
        )
        if replay is not None:
            return operation_id, replay
        assert execution is not None
        try:
            stopped = self.driver.call(driver_operation, execution)
            self._validate_stop_result(stopped)
            self.store.record_participant_stop_evidence(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                owned_resource_evidence_sha256=stopped[
                    "owned_resource_evidence_sha256"
                ],
            )
            result = self.store.finalize_participant_stop(
                project_instance_id=values["project_instance_id"],
                scenario_id=values["scenario_id"],
                participant_id=values["participant_id"],
                request_id=values["request_id"],
                operation_id=operation_id,
                release_evidence_sha256=stopped[
                    "owned_resource_evidence_sha256"
                ],
            )
            return operation_id, result
        except (ParticipantError, OSError, StoreError, KeyError, TypeError) as exc:
            self._fail_committed(
                values=values,
                operation_id=operation_id,
                reason=(
                    "force_stop_failed"
                    if driver_operation == "force_stop"
                    else "stop_failed"
                ),
                failure_code=(
                    "lifecycle.force-stop-failed"
                    if driver_operation == "force_stop"
                    else "lifecycle.stop-failed"
                ),
                cleanup_pending=True,
            )
            if isinstance(exc, StoreError):
                raise
            raise ParticipantError(
                (
                    "participant.force-stop-failed"
                    if driver_operation == "force_stop"
                    else "participant.stop-failed"
                ),
                (
                    "participant force stop failed"
                    if driver_operation == "force_stop"
                    else "participant stop failed"
                ),
                retryable=True,
                mutation_state="committed",
                operation_id=operation_id,
            ) from exc

    def close_scenario_participants(
        self,
        executions: list[dict[str, Any]],
        *,
        should_cancel: Callable[[], bool] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Safely close a frozen participant set without force-stop fallback."""

        reports: list[dict[str, Any]] = []
        total = len(executions)
        for index, execution in enumerate(executions):
            if should_cancel is not None and should_cancel():
                for remaining in executions[index:]:
                    cancelled_base = {
                        "participant_id": remaining["participant_id"],
                        "participant_generation": remaining[
                            "participant_generation"
                        ],
                        "participant_state_revision": remaining[
                            "participant_state_revision"
                        ],
                        "desired_state_before_close": remaining["desired_state"],
                        "continuity_mode": remaining["continuity_mode"],
                    }
                    reports.append(
                        {
                            **cancelled_base,
                            "classification": "unknown",
                            "closed": False,
                            "action_outcome_known": True,
                            "drain_requested": False,
                            "progress_event_count": 0,
                            "runtime_binding_id": None,
                            "presentation_binding_id": None,
                            "owned_resource_evidence_sha256": canonical_json_sha256(
                                {**cancelled_base, "cancelled_before_action": True}
                            ),
                            "owner": remaining["participant_id"],
                            "command": "cancelled-before-action",
                            "started_at_unix_ms": None,
                        }
                    )
                return reports, True
            base = {
                "participant_id": execution["participant_id"],
                "participant_generation": execution["participant_generation"],
                "participant_state_revision": execution[
                    "participant_state_revision"
                ],
                "desired_state_before_close": execution["desired_state"],
                "continuity_mode": execution["continuity_mode"],
            }
            if execution["kind"] == "inactive":
                reports.append(
                    {
                        **base,
                        "classification": "idle",
                        "closed": True,
                        "action_outcome_known": True,
                        "drain_requested": False,
                        "progress_event_count": 0,
                        "runtime_binding_id": None,
                        "presentation_binding_id": None,
                        "owned_resource_evidence_sha256": canonical_json_sha256(
                            {**base, "inactive": True}
                        ),
                        "owner": execution["participant_id"],
                        "command": "inactive",
                        "started_at_unix_ms": None,
                    }
                )
                if progress_callback is not None:
                    progress_callback(index + 1, total, execution["participant_id"])
                continue
            if execution["kind"] == "settled":
                reports.append(
                    {
                        **base,
                        "classification": "settled_cleanup_pending",
                        "closed": True,
                        "action_outcome_known": True,
                        "drain_requested": False,
                        "progress_event_count": 0,
                        "runtime_binding_id": None,
                        "presentation_binding_id": None,
                        "owned_resource_evidence_sha256": canonical_json_sha256(
                            {**base, "settled": True, "observation_available": False}
                        ),
                        "owner": execution["participant_id"],
                        "command": "settled-cleanup-pending",
                        "started_at_unix_ms": None,
                    }
                )
                if progress_callback is not None:
                    progress_callback(index + 1, total, execution["participant_id"])
                continue
            if execution["kind"] == "unknown":
                reports.append(
                    {
                        **base,
                        "classification": "unknown",
                        "closed": False,
                        "action_outcome_known": True,
                        "drain_requested": False,
                        "progress_event_count": 0,
                        "runtime_binding_id": None,
                        "presentation_binding_id": None,
                        "owned_resource_evidence_sha256": canonical_json_sha256(
                            {**base, "observation_available": False}
                        ),
                        "owner": execution["participant_id"],
                        "command": "unknown",
                        "started_at_unix_ms": None,
                    }
                )
                if progress_callback is not None:
                    progress_callback(index + 1, total, execution["participant_id"])
                continue
            payload = execution["driver_payload"]
            try:
                result = self.driver.call("close", payload)
                self._validate_close_result(payload, result)
                reports.append({**base, **copy.deepcopy(result)})
            except (ParticipantError, OSError, KeyError, TypeError):
                runtime_ack = payload.get("runtime_ready_ack", {})
                presentation_ack = payload.get("presentation_create_ack")
                reports.append(
                    {
                        **base,
                        "classification": "unknown",
                        "closed": False,
                        "action_outcome_known": False,
                        "drain_requested": None,
                        "progress_event_count": None,
                        "runtime_binding_id": runtime_ack.get("binding", {}).get(
                            "runtime_binding_id"
                        ),
                        "presentation_binding_id": (
                            None
                            if presentation_ack is None
                            else presentation_ack.get("binding", {}).get(
                                "presentation_instance_id"
                            )
                        ),
                        "owned_resource_evidence_sha256": canonical_json_sha256(
                            {**base, "driver_outcome_known": False}
                        ),
                        "owner": execution["participant_id"],
                        "command": payload.get("launch_spec", {}).get(
                            "runtime_profile_ref", "unknown"
                        ),
                        "started_at_unix_ms": None,
                    }
                )
            if progress_callback is not None:
                progress_callback(index + 1, total, execution["participant_id"])
        return reports, False

    @staticmethod
    def _force_close_report(
        execution: dict[str, Any],
        *,
        classification: str,
        closed: bool,
        outcome_known: bool,
        command: str,
        evidence: dict[str, Any] | str,
        runtime_binding: str | None,
        presentation_binding: str | None,
    ) -> dict[str, Any]:
        base = {
            "participant_id": execution["participant_id"],
            "participant_generation": execution["participant_generation"],
            "participant_state_revision": execution["participant_state_revision"],
            "desired_state_before_close": execution["desired_state"],
            "continuity_mode": execution["continuity_mode"],
        }
        return {
            **base,
            "classification": classification,
            "closed": closed,
            "action_outcome_known": outcome_known,
            "drain_requested": False,
            "progress_event_count": 0,
            "runtime_binding_id": runtime_binding,
            "presentation_binding_id": presentation_binding,
            "owned_resource_evidence_sha256": (
                evidence
                if isinstance(evidence, str)
                else canonical_json_sha256({**base, **evidence})
            ),
            "owner": execution["participant_id"],
            "command": command,
            "started_at_unix_ms": None,
        }

    def force_close_scenario_participants(
        self,
        executions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Force-close an exact frozen Scenario target set after Host authorization."""

        reports: list[dict[str, Any]] = []
        for execution in executions:
            runtime_binding = execution.get("runtime_binding_id")
            presentation_binding = execution.get("presentation_binding_id")
            if execution["kind"] == "inactive":
                details = {
                    "classification": "idle",
                    "closed": True,
                    "outcome_known": True,
                    "command": "inactive",
                    "evidence": {"inactive": True, "force_destroy": True},
                    "runtime_binding": None,
                    "presentation_binding": None,
                }
            elif execution["kind"] == "settled":
                details = {
                    "classification": "settled_cleanup_pending",
                    "closed": True,
                    "outcome_known": True,
                    "command": "settled-cleanup-pending",
                    "evidence": {
                        "settled": True,
                        "observation_available": False,
                        "force_destroy": True,
                    },
                    "runtime_binding": None,
                    "presentation_binding": None,
                }
            elif execution["kind"] == "unknown":
                binding_absent = runtime_binding is None and presentation_binding is None
                details = {
                    "classification": "unbound" if binding_absent else "unknown",
                    "closed": binding_absent,
                    "outcome_known": binding_absent,
                    "command": (
                        "no-owned-binding" if binding_absent else "ownership-unproven"
                    ),
                    "evidence": {"force_destroy": True, "binding_absent": binding_absent},
                    "runtime_binding": runtime_binding,
                    "presentation_binding": presentation_binding,
                }
            else:
                payload = execution["driver_payload"]
                try:
                    stopped = self.driver.call("force_stop", payload)
                    self._validate_stop_result(stopped)
                    runtime_ack = payload.get("runtime_ready_ack", {})
                    presentation_ack = payload.get("presentation_create_ack")
                    details = {
                        "classification": "forced",
                        "closed": True,
                        "outcome_known": True,
                        "command": "force-stop",
                        "evidence": stopped["owned_resource_evidence_sha256"],
                        "runtime_binding": runtime_ack.get("binding", {}).get(
                            "runtime_binding_id"
                        ),
                        "presentation_binding": (
                            None
                            if presentation_ack is None
                            else presentation_ack.get("binding", {}).get(
                                "presentation_instance_id"
                            )
                        ),
                    }
                except (ParticipantError, OSError, KeyError, TypeError):
                    details = {
                        "classification": "unknown",
                        "closed": False,
                        "outcome_known": False,
                        "command": "force-stop",
                        "evidence": {"force_destroy": True, "outcome": "unknown"},
                        "runtime_binding": runtime_binding,
                        "presentation_binding": presentation_binding,
                    }
            reports.append(self._force_close_report(execution, **details))
        return reports

    def supervise_once(
        self,
        host_generation: int,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, int]:
        """Re-observe every exact active binding without taking ownership action."""

        inputs = self.store.resource_supervision_inputs(host_generation)
        observed = 0
        stale = 0
        for entry in inputs:
            if should_stop is not None and should_stop():
                break
            identity = {
                key: entry[key]
                for key in (
                    "project_instance_id",
                    "scenario_id",
                    "participant_id",
                    "participant_generation",
                    "runtime_binding_id",
                )
            }
            try:
                observation = self.driver.call(
                    "supervise",
                    entry["driver_payload"],
                    timeout_seconds=SUPERVISION_TIMEOUT_SECONDS,
                )
                self._validate_supervision_observation(
                    entry["artifacts"], observation
                )
                if self.store.commit_resource_supervision(
                    **identity, observation=observation
                ):
                    observed += 1
            except (ParticipantError, OSError, StoreError, KeyError, TypeError):
                if self.store.mark_resource_supervision_stale(
                    **identity, reason="observation_failed"
                ):
                    stale += 1
        return {"observed": observed, "stale": stale}

    def deliver(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_record: Mapping[str, Any],
        message: str,
        message_kind: str,
        reply_to_delivery_id: str | None,
        consumption_token: str,
    ) -> dict[str, Any]:
        """Dispatch through the exact receiver runtime/presentation binding."""

        target = delivery_record["target"]
        receiver = target["receiver"]
        _, artifact, private_root, scenario_workspace = (
            self.store.participant_delivery_input(
                project_instance_id=project_instance_id,
                scenario_id=scenario_id,
                participant_id=receiver["participant_id"],
                participant_generation=receiver["participant_generation"],
                runtime_binding_id=target["runtime_binding_id"],
                presentation_binding_id=target["presentation_binding_id"],
            )
        )
        self._ensure_private_root(private_root)
        delivery_workspace = {"workspace_path": str(scenario_workspace)}
        self._bind_workspace_directory(
            delivery_workspace, project_instance_id, scenario_id
        )
        result = self.driver.call(
            "deliver",
            {
                "delivery_record": copy.deepcopy(delivery_record),
                "message": message,
                "message_kind": message_kind,
                "reply_to_delivery_id": reply_to_delivery_id,
                "consumption_token": consumption_token,
                "runtime_ready_ack": artifact["runtime_ready_ack"],
                "presentation_create_ack": artifact["presentation_create_ack"],
                "private_root": str(private_root),
                **delivery_workspace,
            },
        )
        if (
            not isinstance(result, dict)
            or set(result) != {"delivery_ack", "consumption_ack"}
            or not isinstance(result["delivery_ack"], dict)
            or (
                result["consumption_ack"] is not None
                and not isinstance(result["consumption_ack"], dict)
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant delivery reply is invalid"
            )
        return result

    def authorize_sender(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        runtime_binding_id: str,
        presentation_binding_id: str | None,
        peer_pid: int,
    ) -> dict[str, Any]:
        """Prove that one local IPC peer is an exact owned descendant."""

        record, artifact, private_root, _ = self.store.participant_delivery_input(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            participant_generation=participant_generation,
            runtime_binding_id=runtime_binding_id,
            presentation_binding_id=presentation_binding_id,
        )
        result = self.driver.call(
            "authorize_sender",
            {
                "peer_pid": peer_pid,
                "runtime_ready_ack": artifact["runtime_ready_ack"],
                "presentation_create_ack": artifact["presentation_create_ack"],
                "private_root": str(private_root),
            },
            timeout_seconds=SUPERVISION_TIMEOUT_SECONDS,
        )
        sender = {
            "scenario_id": scenario_id,
            "participant_id": participant_id,
            "participant_generation": participant_generation,
        }
        if (
            not isinstance(result, dict)
            or set(result)
            != {
                "authorized",
                "sender",
                "runtime_binding_id",
                "process_chain_evidence_sha256",
            }
            or result["authorized"] is not True
            or result["sender"] != sender
            or result["runtime_binding_id"] != runtime_binding_id
            or not self._sha256(result["process_chain_evidence_sha256"])
            or record["observed_state"] != "ready"
        ):
            raise ParticipantError(
                "identity.sender-rejected", "participant sender proof differs"
            )
        return copy.deepcopy(record)

    def await_consumption(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_record: Mapping[str, Any],
        consumption_token: str,
    ) -> dict[str, Any]:
        """Observe an explicit agent consumption signal on the exact binding."""

        target = delivery_record["target"]
        receiver = target["receiver"]
        _, artifact, private_root, _ = self.store.participant_delivery_input(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=receiver["participant_id"],
            participant_generation=receiver["participant_generation"],
            runtime_binding_id=target["runtime_binding_id"],
            presentation_binding_id=target["presentation_binding_id"],
        )
        result = self.driver.call(
            "await_consumption",
            {
                "delivery_record": copy.deepcopy(delivery_record),
                "consumption_token": consumption_token,
                "runtime_ready_ack": artifact["runtime_ready_ack"],
                "presentation_create_ack": artifact["presentation_create_ack"],
                "private_root": str(private_root),
            },
        )
        if (
            not isinstance(result, dict)
            or set(result) != {"consumption_ack"}
            or not isinstance(result["consumption_ack"], dict)
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant consumption reply is invalid"
            )
        return result["consumption_ack"]

    @staticmethod
    def _supervision_payload(
        execution: Mapping[str, Any], artifacts: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            "launch_spec": copy.deepcopy(execution["launch_spec"]),
            "resolved_driver": copy.deepcopy(execution["resolved_driver"]),
            "runtime_ready_ack": copy.deepcopy(artifacts["runtime_ready_ack"]),
            "presentation_create_ack": copy.deepcopy(
                artifacts["presentation_create_ack"]
            ),
            "private_root": execution["private_root"],
        }

    @staticmethod
    def _validate_participant_client(value: Mapping[str, str]) -> None:
        if not isinstance(value, Mapping) or set(value) != {
            "context_path",
            "client_executable",
            "client_pythonpath",
            "collaboration_context_path",
        }:
            raise ParticipantError(
                "identity.context-invalid", "participant client context differs"
            )
        context_path = Path(value["context_path"])
        executable = Path(value["client_executable"])
        pythonpath = Path(value["client_pythonpath"])
        collaboration_path = Path(value["collaboration_context_path"])
        if (
            any(
                not path.is_absolute() or path.is_symlink()
                for path in (
                    context_path,
                    executable,
                    pythonpath,
                    collaboration_path,
                )
            )
            or not context_path.is_file()
            or stat.S_IMODE(context_path.stat().st_mode) != 0o600
            or context_path.stat().st_uid != os.getuid()
            or not collaboration_path.is_file()
            or stat.S_IMODE(collaboration_path.stat().st_mode) != 0o600
            or collaboration_path.stat().st_uid != os.getuid()
            or not executable.is_file()
            or executable.stat().st_uid not in {0, os.getuid()}
            or stat.S_IMODE(executable.stat().st_mode) & 0o022
            or not pythonpath.is_dir()
            or pythonpath.stat().st_uid not in {0, os.getuid()}
            or stat.S_IMODE(pythonpath.stat().st_mode) & 0o022
        ):
            raise ParticipantError(
                "identity.context-invalid", "participant client context is unsafe"
            )

    @classmethod
    def _validate_supervision_observation(
        cls, artifacts: Mapping[str, Any], value: Any
    ) -> None:
        fields = {
            "schema_version",
            "runtime_binding_id",
            "process_start_identity_sha256",
            "boot_id_sha256",
            "heartbeat_sequence",
            "heartbeat_at_unix_ms",
            "fencing_token_sha256",
            "resources",
            "observation_evidence_sha256",
        }
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["schema_version"] != SUPERVISION_SCHEMA_VERSION
            or not isinstance(value["runtime_binding_id"], str)
            or not value["runtime_binding_id"]
            or not cls._sha256(value["process_start_identity_sha256"])
            or not cls._sha256(value["boot_id_sha256"])
            or not cls._positive_int(value["heartbeat_sequence"])
            or not cls._nonnegative_int(value["heartbeat_at_unix_ms"])
            or not cls._sha256(value["fencing_token_sha256"])
            or not isinstance(value["resources"], list)
            or not value["resources"]
            or not cls._sha256(value["observation_evidence_sha256"])
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant supervision reply is invalid"
            )
        binding = artifacts["runtime_ready_ack"]["binding"]
        if (
            value["runtime_binding_id"] != binding["runtime_binding_id"]
            or value["process_start_identity_sha256"]
            != binding["process_identity_sha256"]
        ):
            raise ParticipantError(
                "participant.binding-drift", "participant supervision binding differs"
            )
        identities: set[tuple[str, str]] = set()
        for resource in value["resources"]:
            if (
                not isinstance(resource, dict)
                or set(resource)
                != {"resource_class", "resource_identity_sha256", "state"}
                or resource["resource_class"] not in RESOURCE_CLASSES
                or not cls._sha256(resource["resource_identity_sha256"])
                or resource["state"] != "held"
            ):
                raise ParticipantError(
                    "driver.invalid-reply", "participant resource observation is invalid"
                )
            identities.add(
                (resource["resource_class"], resource["resource_identity_sha256"])
            )
        if (
            len(identities) != len(value["resources"])
            or value["resources"]
            != sorted(
                value["resources"],
                key=lambda resource: (
                    resource["resource_class"],
                    resource["resource_identity_sha256"],
                ),
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant resource observation differs"
            )
        evidence = {
            key: copy.deepcopy(item)
            for key, item in value.items()
            if key != "observation_evidence_sha256"
        }
        if canonical_json_sha256(evidence) != value["observation_evidence_sha256"]:
            raise ParticipantError(
                "driver.invalid-reply", "participant supervision evidence differs"
            )

    def _fail_committed(
        self,
        *,
        values: Mapping[str, Any],
        operation_id: str,
        reason: str,
        failure_code: str,
        cleanup_pending: bool,
        failure_artifacts: dict[str, Any] | None = None,
        owned_resource_evidence_sha256: str | None = None,
    ) -> None:
        self.store.fail_participant_operation(
            project_instance_id=values["project_instance_id"],
            scenario_id=values["scenario_id"],
            participant_id=values["participant_id"],
            request_id=values["request_id"],
            operation_id=operation_id,
            reason=reason,
            failure_code=failure_code,
            cleanup_pending=cleanup_pending,
            failure_artifacts=failure_artifacts,
            owned_resource_evidence_sha256=owned_resource_evidence_sha256,
        )

    @classmethod
    def _validate_stop_result(cls, stopped: Any) -> None:
        fields = {"stopped", "owned_resource_evidence_sha256"}
        optional_fields = {"vendor_session_identity_sha256"}
        if (
            not isinstance(stopped, dict)
            or not fields.issubset(stopped)
            or set(stopped) - fields - optional_fields
            or stopped["stopped"] is not True
            or not cls._sha256(stopped["owned_resource_evidence_sha256"])
            or (
                stopped.get("vendor_session_identity_sha256") is not None
                and not cls._sha256(stopped["vendor_session_identity_sha256"])
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant stop reply is invalid"
            )

    @classmethod
    def _validate_recovery_result(
        cls, execution: Mapping[str, Any], recovery: Any
    ) -> None:
        expected = {
            "recovered",
            "recovery_class",
            "previous_participant_generation",
            "next_participant_generation",
            "external_resources_absent",
            "private_generation_retained",
            "owned_resource_evidence_sha256",
        }
        context = execution["context"]
        if (
            not isinstance(recovery, dict)
            or set(recovery) != expected
            or recovery["recovered"] is not True
            or recovery["recovery_class"]
            not in {"pre_binding_absent", "exact_binding_stopped"}
            or recovery["previous_participant_generation"]
            != context["participant_generation"]
            or recovery["next_participant_generation"]
            != execution["next_participant_generation"]
            or recovery["external_resources_absent"] is not True
            or recovery["private_generation_retained"] is not True
            or not cls._sha256(recovery["owned_resource_evidence_sha256"])
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant recovery reply is invalid"
            )

    def _ensure_private_root(self, path: Path) -> None:
        parent = path.parent
        expected_root = self.store.participant_root
        if not path.is_relative_to(expected_root) or parent.parent != expected_root:
            raise ParticipantError(
                "driver.private-root-invalid", "participant private root is invalid"
            )
        parent.mkdir(mode=0o700, exist_ok=True)
        path.mkdir(mode=0o700, exist_ok=True)
        for candidate in (parent, path):
            details = candidate.stat()
            if (
                candidate.is_symlink()
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o700
            ):
                raise ParticipantError(
                    "driver.private-root-invalid", "participant private root is invalid"
                )

    @classmethod
    def _validate_resolved(
        cls,
        value: Any,
        *,
        launch_spec: Mapping[str, Any],
        presentation_driver_id: str | None,
    ) -> None:
        fields = {
            "driver_registry",
            "driver_registry_digest",
            "runtime_descriptor",
            "presentation_descriptor",
            "capability_snapshot_digest",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ParticipantError(
                "driver.invalid-reply", "participant driver resolution is invalid"
            )
        registry = value["driver_registry"]
        runtime = value["runtime_descriptor"]
        presentation = value["presentation_descriptor"]
        if (
            not isinstance(registry, dict)
            or canonical_json_sha256(registry) != value["driver_registry_digest"]
            or not cls._sha256(value["capability_snapshot_digest"])
            or not isinstance(runtime, dict)
            or runtime.get("driver_kind") != "runtime"
            or runtime.get("driver_id") != launch_spec["driver_id"]
            or runtime.get("contract_version")
            != launch_spec["driver_contract_version"]
            or launch_spec["interaction_mode"]
            not in runtime.get("interaction_modes", [])
            or launch_spec["continuity_mode"]
            not in runtime.get("continuity_modes", [])
            or runtime not in registry.get("runtime_drivers", [])
        ):
            raise ParticipantError(
                "driver.invalid-reply", "runtime driver resolution differs"
            )
        continuity_mode = launch_spec["continuity_mode"]
        continuity_binding_ref = launch_spec["continuity_binding_ref"]
        if continuity_mode == "exact_resume":
            vendor_operations = runtime.get("optional_vendor_lifecycle_operations", [])
            if (
                not runtime.get("supports_vendor_session_identity")
                or runtime.get("vendor_lifecycle_surface") is None
                or not {"vendor_resume", "vendor_bind"}.issubset(
                    set(vendor_operations)
                )
                or continuity_binding_ref is None
            ):
                raise ParticipantError(
                    "driver.capability-unavailable",
                    "exact resume capability is unavailable",
                )
        elif continuity_binding_ref is not None:
            raise ParticipantError(
                "driver.invalid-reply",
                "explicit recreate cannot consume a continuity binding",
            )
        if presentation_driver_id is None:
            if presentation is not None:
                raise ParticipantError(
                    "driver.invalid-reply", "headless resolution returned presentation"
                )
        elif (
            not isinstance(presentation, dict)
            or presentation.get("driver_kind") != "presentation"
            or presentation.get("driver_id") != presentation_driver_id
            or presentation not in registry.get("presentation_drivers", [])
        ):
            raise ParticipantError(
                "driver.invalid-reply", "presentation driver resolution differs"
            )

    @classmethod
    def _validate_start_artifacts(
        cls, execution: Mapping[str, Any], artifacts: Any
    ) -> None:
        fields = {
            "runtime_create_request",
            "prepared_runtime_launch",
            "runtime_ready_ack",
            "presentation_create_request",
            "presentation_create_ack",
        }
        if not isinstance(artifacts, dict) or set(artifacts) != fields:
            raise ParticipantError(
                "driver.invalid-reply", "participant start artifacts are invalid"
            )
        context = execution["context"]
        launch_spec = execution["launch_spec"]
        runtime_request = artifacts["runtime_create_request"]
        prepared = artifacts["prepared_runtime_launch"]
        runtime_ack = artifacts["runtime_ready_ack"]
        if (
            not cls._exact_context(runtime_request, context)
            or runtime_request.get("launch_spec") != launch_spec
            or not cls._exact_context(prepared, context)
            or prepared.get("driver_id") != launch_spec["driver_id"]
            or not cls._exact_context(runtime_ack, context)
            or runtime_ack.get("ready") is not True
            or not isinstance(runtime_ack.get("binding"), dict)
        ):
            raise ParticipantError(
                "driver.invalid-reply", "runtime start identity differs"
            )
        binding = runtime_ack["binding"]
        for field in ("scenario_id", "participant_id", "participant_generation"):
            if binding.get(field) != context[field]:
                raise ParticipantError(
                    "driver.invalid-reply", "runtime binding identity differs"
                )
        if (
            binding.get("driver_id") != launch_spec["driver_id"]
            or binding.get("runtime_instance_id")
            != prepared.get("runtime_instance_id")
            or binding.get("continuity_mode") != launch_spec["continuity_mode"]
            or binding.get("capability_snapshot_digest")
            != context["capability_snapshot_digest"]
            or not cls._sha256(binding.get("process_identity_sha256"))
            or (
                binding.get("vendor_session_identity_sha256") is not None
                and not cls._sha256(
                    binding.get("vendor_session_identity_sha256")
                )
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "runtime binding evidence differs"
            )
        # A fresh interactive runtime may be input-ready before its first real
        # employee turn materializes the vendor conversation.  The nullable
        # digest is therefore a pending binding, while capability admission and
        # the exact continuity_binding_ref remain mandatory.  Runtime drivers
        # must capture the first-turn identity privately before normal close.
        presentation_request = artifacts["presentation_create_request"]
        presentation_ack = artifacts["presentation_create_ack"]
        if launch_spec["interaction_mode"] == "headless":
            if presentation_request is not None or presentation_ack is not None:
                raise ParticipantError(
                    "driver.invalid-reply", "headless participant returned presentation"
                )
            return
        if (
            not cls._exact_context(presentation_request, context)
            or not cls._exact_context(presentation_ack, context)
            or presentation_ack.get("created") is not True
            or not isinstance(presentation_ack.get("binding"), dict)
        ):
            raise ParticipantError(
                "driver.invalid-reply", "presentation start identity differs"
            )
        presentation_binding = presentation_ack["binding"]
        if (
            presentation_request.get("runtime_binding_id")
            != binding.get("runtime_binding_id")
            or presentation_binding.get("runtime_binding_id")
            != binding.get("runtime_binding_id")
            or presentation_binding.get("driver_id")
            != presentation_request.get("presentation_driver_id")
            or presentation_binding.get("capability_snapshot_digest")
            != context["capability_snapshot_digest"]
            or presentation_binding.get("display_topology_fingerprint")
            != presentation_request.get("display_topology_fingerprint")
            or not cls._sha256(presentation_binding.get("window_identity_sha256"))
            or not cls._sha256(presentation_binding.get("session_identity_sha256"))
        ):
            raise ParticipantError(
                "driver.invalid-reply", "presentation binding evidence differs"
            )

    @staticmethod
    def _exact_context(value: Any, context: Mapping[str, Any]) -> bool:
        return isinstance(value, dict) and value.get("context") == context

    @staticmethod
    def _sha256(value: Any) -> bool:
        return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None

    @staticmethod
    def _positive_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    @staticmethod
    def _nonnegative_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @classmethod
    def _validate_close_result(
        cls, payload: Mapping[str, Any], result: Any
    ) -> None:
        fields = {
            "classification",
            "closed",
            "action_outcome_known",
            "drain_requested",
            "progress_event_count",
            "runtime_binding_id",
            "presentation_binding_id",
            "owned_resource_evidence_sha256",
            "owner",
            "command",
            "started_at_unix_ms",
        }
        optional_fields = {"vendor_session_identity_sha256"}
        runtime_ack = payload["runtime_ready_ack"]["binding"]
        presentation_ack = payload["presentation_create_ack"]
        expected_presentation = (
            None
            if presentation_ack is None
            else presentation_ack["binding"]["presentation_instance_id"]
        )
        if (
            not isinstance(result, dict)
            or not fields.issubset(result)
            or set(result) - fields - optional_fields
            or result["classification"]
            not in {"idle", "busy", "requested", "timeout", "unknown"}
            or not isinstance(result["closed"], bool)
            or result["action_outcome_known"] is not True
            or not isinstance(result["drain_requested"], bool)
            or not isinstance(result["progress_event_count"], int)
            or isinstance(result["progress_event_count"], bool)
            or result["progress_event_count"] < 0
            or result["runtime_binding_id"]
            != runtime_ack["runtime_binding_id"]
            or result["presentation_binding_id"] != expected_presentation
            or not cls._sha256(result["owned_resource_evidence_sha256"])
            or result["owner"] != payload["context"]["participant_id"]
            or not isinstance(result["command"], str)
            or not result["command"]
            or (
                result["started_at_unix_ms"] is not None
                and (
                    not isinstance(result["started_at_unix_ms"], int)
                    or isinstance(result["started_at_unix_ms"], bool)
                    or result["started_at_unix_ms"] < 0
                )
            )
            or (
                result.get("vendor_session_identity_sha256") is not None
                and not cls._sha256(result["vendor_session_identity_sha256"])
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant close reply is invalid"
            )
        classification = result["classification"]
        if (
            (result["closed"] is True)
            != (classification in {"idle", "busy", "requested"})
            or (
                classification == "idle"
                and (
                    result["drain_requested"]
                    or result["progress_event_count"] != 0
                )
            )
            or (
                classification == "busy"
                and (
                    not result["drain_requested"]
                    or result["progress_event_count"] < 1
                )
            )
            or (
                classification == "requested"
                and (
                    result["drain_requested"]
                    or result["progress_event_count"] != 0
                )
            )
            or (
                classification == "unknown"
                and (
                    result["drain_requested"]
                    or result["progress_event_count"] != 0
                )
            )
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant close semantics are invalid"
            )

    @classmethod
    def _validate_presentation_observation(
        cls,
        value: Any,
        participant: Mapping[str, Any],
        *,
        action: str,
    ) -> dict[str, Any]:
        presentation = value.get("presentation") if isinstance(value, dict) else None
        if (
            not isinstance(presentation, dict)
            or set(presentation)
            != {
                "participant_generation",
                "presentation_instance_id",
                "health",
                "focused",
                "restore_outcome",
                "geometry",
                "display_topology_fingerprint",
            }
            or presentation["participant_generation"]
            != participant["participant_generation"]
            or presentation["presentation_instance_id"]
            != participant["presentation_binding_id"]
            or presentation["health"] != "ready"
            or presentation["focused"] is not (action == "focus")
            or presentation["restore_outcome"]
            not in {
                "not_requested",
                "not_available",
                "applied_exact",
                "applied_adjusted",
            }
            or (
                action == "inspect"
                and presentation["restore_outcome"] != "not_requested"
            )
            or not cls._sha256(presentation["display_topology_fingerprint"])
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant presentation result differs"
            )
        geometry = presentation["geometry"]
        if (
            not isinstance(geometry, dict)
            or set(geometry) != {"x", "y", "width", "height"}
            or any(
                not isinstance(geometry[field], int)
                or isinstance(geometry[field], bool)
                for field in geometry
            )
            or geometry["width"] < 1
            or geometry["height"] < 1
        ):
            raise ParticipantError(
                "driver.invalid-reply", "participant presentation geometry differs"
            )
        return copy.deepcopy(presentation)
