# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import copy
import json
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from ai_collab.client import (
    HarnessClient,
    HarnessClientError,
    ParticipantHarnessClient,
)
from ai_collab.delivery import DeliveryCoordinator
from ai_collab.host import HarnessHost
from ai_collab.participant import ParticipantCoordinator, ParticipantError
from ai_collab.protocol import canonical_json_sha256


PROJECT_ID = "project-one"
SCENARIO_ID = "scenario-one"
SENDER_ID = "sender-one"
RECEIVER_ID = "receiver-one"
PROJECT_RENDER = {
    "render_contract_version": 1,
    "source": {"kind": "fileless", "intent_schema_version": None, "source_digest": "1" * 64},
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
    "collaboration": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
    "availability": {"status": "ready", "observations": [], "changes": [], "warnings": []},
}
PROJECT_RENDER["availability"]["fingerprint"] = canonical_json_sha256(  # type: ignore[index]
    PROJECT_RENDER["availability"]
)
PROJECT_RENDER["render_digest"] = canonical_json_sha256(
    {key: value for key, value in PROJECT_RENDER.items() if key != "availability"}
)
PROJECT_DIGEST = PROJECT_RENDER["render_digest"]
CAPABILITY_DIGEST = "b" * 64


def _runtime_descriptor() -> dict[str, Any]:
    return {
        "driver_kind": "runtime",
        "driver_id": "runtime.generic-process",
        "contract_version": 2,
        "implementation_ref": "implementation.test-process",
        "interaction_modes": ["tui", "headless"],
        "lifecycle_operations": [
            "create", "start", "stop", "health", "delivery_ack",
            "session_drift", "repair",
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
            "permission_probe", "create_top_level", "focus", "close_exact",
            "health", "capture_geometry", "restore_geometry",
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


class FakeDeliveryDriver:
    def __init__(self) -> None:
        self.delivery_calls = 0
        self.failures_remaining = 0
        self.interrupt_once = False
        self.supervision_sequences: dict[str, int] = {}
        self.authorize_sender_calls = 0
        self.reject_sender = False
        self.defer_first_consumption = False
        self.consumption_failures_remaining = 0
        self.deferred_delivery_id: str | None = None
        self.consumption_waiting = threading.Event()
        self.release_consumption = threading.Event()

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        runtime = _runtime_descriptor()
        presentation = _presentation_descriptor()
        registry = {
            "schema_version": 1,
            "participant_driver_contract_version": 2,
            "runtime_drivers": [runtime],
            "presentation_drivers": [presentation],
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
            context = payload["context"]
            participant_id = context["participant_id"]
            binding_scope = f"{context['scenario_id']}-{participant_id}"
            runtime_id = f"runtime-{binding_scope}"
            presentation_id = f"presentation-{binding_scope}"
            launch_spec = payload["launch_spec"]
            runtime_binding = {
                "scenario_id": context["scenario_id"],
                "participant_id": participant_id,
                "participant_generation": context["participant_generation"],
                "driver_id": launch_spec["driver_id"],
                "runtime_instance_id": f"instance-{binding_scope}",
                "runtime_binding_id": runtime_id,
                "process_instance_id": f"process-{binding_scope}",
                "process_identity_sha256": canonical_json_sha256(
                    {"process": binding_scope}
                ),
                "continuity_mode": "explicit_recreate",
                "vendor_session_identity_sha256": None,
                "private_driver_binding_ref": f"private-runtime-{binding_scope}",
                "capability_snapshot_digest": CAPABILITY_DIGEST,
            }
            presentation_binding = {
                "scenario_id": context["scenario_id"],
                "participant_id": participant_id,
                "participant_generation": context["participant_generation"],
                "driver_id": "presentation.iterm2",
                "presentation_instance_id": presentation_id,
                "runtime_binding_id": runtime_id,
                "window_identity_sha256": canonical_json_sha256(
                    {"window": binding_scope}
                ),
                "session_identity_sha256": canonical_json_sha256(
                    {"session": binding_scope}
                ),
                "private_driver_binding_ref": f"private-presentation-{binding_scope}",
                "geometry": {"x": 0, "y": 0, "width": 800, "height": 600},
                "display_topology_fingerprint": "f" * 64,
                "capability_snapshot_digest": CAPABILITY_DIGEST,
            }
            return {
                "runtime_create_request": {"context": context, "launch_spec": launch_spec},
                "prepared_runtime_launch": {
                    "context": context,
                    "driver_id": launch_spec["driver_id"],
                    "runtime_instance_id": f"instance-{binding_scope}",
                    "private_launch_handle_ref": f"launch-{binding_scope}",
                },
                "runtime_ready_ack": {"context": context, "binding": runtime_binding, "ready": True},
                "presentation_create_request": {
                    "context": context,
                    "presentation_driver_id": "presentation.iterm2",
                    "runtime_binding_id": runtime_id,
                    "restore_geometry": None,
                    "display_topology_fingerprint": "f" * 64,
                },
                "presentation_create_ack": {
                    "context": context,
                    "binding": presentation_binding,
                    "geometry_restore_outcome": "not_requested",
                    "created": True,
                },
            }
        if operation == "authorize_sender":
            self.authorize_sender_calls += 1
            if self.reject_sender:
                raise ParticipantError(
                    "identity.sender-rejected", "injected sender rejection"
                )
            binding = payload["runtime_ready_ack"]["binding"]
            return {
                "authorized": True,
                "sender": {
                    "scenario_id": binding["scenario_id"],
                    "participant_id": binding["participant_id"],
                    "participant_generation": binding[
                        "participant_generation"
                    ],
                },
                "runtime_binding_id": binding["runtime_binding_id"],
                "process_chain_evidence_sha256": canonical_json_sha256(
                    {
                        "peer_pid": payload["peer_pid"],
                        "runtime_binding_id": binding["runtime_binding_id"],
                    }
                ),
            }
        if operation == "deliver":
            self.delivery_calls += 1
            if self.interrupt_once:
                self.interrupt_once = False
                raise KeyboardInterrupt
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise ParticipantError("driver.execution-failed", "injected failure")
            record = payload["delivery_record"]
            attempt = record["events"][-1]
            delivery_ack = {
                "ack_kind": "delivered",
                "delivery_id": record["delivery_id"],
                "message_id": record["message_id"],
                "target": record["target"],
                "payload_digest": record["payload_digest"],
                "attempt_number": attempt["attempt_number"],
                "transport_attempt_id": attempt["transport_attempt_id"],
            }
            consumption_ack = {
                "ack_kind": "consumed",
                "delivery_id": record["delivery_id"],
                "message_id": record["message_id"],
                "target": record["target"],
                "payload_digest": record["payload_digest"],
                "attempt_number": attempt["attempt_number"],
                "transport_attempt_id": attempt["transport_attempt_id"],
                "delivery_ack_digest": canonical_json_sha256(delivery_ack),
            }
            if (
                self.defer_first_consumption
                and self.deferred_delivery_id is None
            ) or self.consumption_failures_remaining:
                self.deferred_delivery_id = record["delivery_id"]
                consumption_ack = None
            return {"delivery_ack": delivery_ack, "consumption_ack": consumption_ack}
        if operation == "await_consumption":
            record = payload["delivery_record"]
            if self.consumption_failures_remaining:
                self.consumption_failures_remaining -= 1
                raise ParticipantError(
                    "driver.execution-failed",
                    "injected consumption observation failure",
                )
            if (
                self.deferred_delivery_id is not None
                and record["delivery_id"] != self.deferred_delivery_id
            ):
                raise ParticipantError(
                    "driver.execution-failed",
                    "unexpected deferred delivery",
                )
            if self.defer_first_consumption:
                self.consumption_waiting.set()
                if not self.release_consumption.wait(timeout=5):
                    raise ParticipantError(
                        "driver.execution-failed",
                        "deferred consumption timed out",
                    )
            delivered = record["events"][-1]
            return {
                "consumption_ack": {
                    "ack_kind": "consumed",
                    "delivery_id": record["delivery_id"],
                    "message_id": record["message_id"],
                    "target": record["target"],
                    "payload_digest": record["payload_digest"],
                    "attempt_number": delivered["attempt_number"],
                    "transport_attempt_id": delivered[
                        "transport_attempt_id"
                    ],
                    "delivery_ack_digest": delivered["evidence_digest"],
                }
            }
        if operation == "supervise":
            binding = payload["runtime_ready_ack"]["binding"]
            participant_id = binding["participant_id"]
            sequence = self.supervision_sequences.get(participant_id, 0) + 1
            self.supervision_sequences[participant_id] = sequence
            observation = {
                "schema_version": 1,
                "runtime_binding_id": binding["runtime_binding_id"],
                "process_start_identity_sha256": binding[
                    "process_identity_sha256"
                ],
                "boot_id_sha256": "1" * 64,
                "heartbeat_sequence": sequence,
                "heartbeat_at_unix_ms": 1_786_435_200_000 + sequence,
                "fencing_token_sha256": canonical_json_sha256(
                    {"participant": participant_id}
                ),
                "resources": [
                    {
                        "resource_class": "exclusive_runtime",
                        "resource_identity_sha256": canonical_json_sha256(
                            {"runtime": binding["runtime_binding_id"]}
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
                "healthy": True,
                "runtime_binding_id": payload["runtime_ready_ack"]["binding"]["runtime_binding_id"],
                "presentation_binding_id": payload["presentation_create_ack"]["binding"]["presentation_instance_id"],
            }
        if operation == "stop":
            return {"stopped": True, "owned_resource_evidence_sha256": "d" * 64}
        raise AssertionError(operation)


@contextmanager
def running_host(state_root: Path) -> Iterator[tuple[HarnessHost, HarnessClient, FakeDeliveryDriver]]:
    with tempfile.TemporaryDirectory(prefix="ai-collab-m4-") as runtime:
        host = HarnessHost(state_root, Path(runtime) / "host.sock")
        host.projects.validate_binding = lambda _project, _digest: None  # type: ignore[method-assign]
        host.projects.resolved_render = (  # type: ignore[method-assign]
            lambda _project, digest=None: PROJECT_RENDER
            if digest in {None, PROJECT_DIGEST}
            else None
        )
        driver = FakeDeliveryDriver()
        host.participants = ParticipantCoordinator(host.store, driver)  # type: ignore[arg-type]
        host.delivery = DeliveryCoordinator(state_root, host.store, host.participants)
        host.bind()
        thread = threading.Thread(target=host.serve_forever, daemon=True)
        thread.start()
        try:
            yield host, HarnessClient(state_root, host.socket_path), driver
        finally:
            host.shutdown()
            thread.join(timeout=3)


def _launch_spec() -> dict[str, Any]:
    return {
        "driver_id": "runtime.generic-process",
        "driver_contract_version": 2,
        "interaction_mode": "tui",
        "continuity_mode": "explicit_recreate",
        "runtime_profile_ref": "runtime-profile.inert",
        "model_binding": None,
        "continuity_binding_ref": None,
    }


def _participant_ref_for_test(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: value[field]
        for field in ("scenario_id", "participant_id", "participant_generation")
    }


def _prepare(
    client: HarnessClient,
    *,
    scenario_id: str = SCENARIO_ID,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    created = client.create_scenario(
        project_instance_id=PROJECT_ID,
        scenario_id=scenario_id,
        project_binding_digest=PROJECT_DIGEST,
    )["scenario"]
    added: dict[str, dict[str, Any]] = {}
    for participant_id in (SENDER_ID, RECEIVER_ID):
        added[participant_id] = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
        )["participant"]
    opened = client.open_scenario(
        project_instance_id=PROJECT_ID,
        scenario_id=scenario_id,
        scenario_generation=1,
        scenario_state_revision=created["state_revision"],
    )["scenario"]
    ready: dict[str, dict[str, Any]] = {}
    for participant_id in (SENDER_ID, RECEIVER_ID):
        ready[participant_id] = client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=1,
            participant_state_revision=added[participant_id]["state_revision"],
        )["participant"]
    return opened, ready[SENDER_ID], ready[RECEIVER_ID]


def _policy(sender: Mapping[str, Any], receiver: Mapping[str, Any], *, attempts: int = 2) -> dict[str, Any]:
    sender_ref = _participant_ref_for_test(sender)
    receiver_ref = _participant_ref_for_test(receiver)
    return {
        "policy_contract_version": 1,
        "policy_id": "policy.edgestudio-dogfood",
        "policy_version": 1,
        "scenario_id": sender["scenario_id"],
        "default_effect": "deny",
        "assignments": [],
        "retry_profiles": [
            {"profile_id": "dogfood", "max_attempts": attempts, "backoff_ms": [0] * attempts}
        ],
        "route_rules": [
            {
                "rule_id": "sender-to-receiver",
                "sender": {"kind": "participant", "participant": sender_ref},
                "receiver": {"kind": "participant", "participant": receiver_ref},
                "message_kind": "collaboration.request",
                "effect": "allow",
                "retry_profile_id": "dogfood",
            }
        ],
    }


def _policy_template(
    *, participant_ids: tuple[str, ...] = (SENDER_ID, RECEIVER_ID)
) -> dict[str, Any]:
    assignments = [
        {
            "assignment_id": "assignment-sender",
            "attribute": "collaboration.role",
            "task_id": None,
            "participant_id": SENDER_ID,
        },
        {
            "assignment_id": "assignment-receiver",
            "attribute": "collaboration.role",
            "task_id": "review",
            "participant_id": RECEIVER_ID,
        },
    ]
    assignments.extend(
        {
            "assignment_id": f"assignment-{participant_id}",
            "attribute": "collaboration.role",
            "task_id": participant_id,
            "participant_id": participant_id,
        }
        for participant_id in participant_ids
        if participant_id not in {SENDER_ID, RECEIVER_ID}
    )
    return {
        "template_contract_version": 1,
        "template_id": "team.peer-review",
        "display_name": "Peer review team",
        "policy_id": "policy.peer-review",
        "participant_ids": list(participant_ids),
        "assignments": assignments,
        "retry_profiles": [
            {"profile_id": "interactive", "max_attempts": 2, "backoff_ms": [0, 0]}
        ],
        "route_rules": [
            {
                "rule_id": "sender-to-receiver",
                "sender": {"kind": "participant", "participant_id": SENDER_ID},
                "receiver": {
                    "kind": "participant",
                    "participant_id": RECEIVER_ID,
                },
                "message_kind": "collaboration.request",
                "effect": "allow",
                "retry_profile_id": "interactive",
            },
            {
                "rule_id": "receiver-to-sender",
                "sender": {
                    "kind": "participant",
                    "participant_id": RECEIVER_ID,
                },
                "receiver": {"kind": "participant", "participant_id": SENDER_ID},
                "message_kind": "collaboration.response",
                "effect": "allow",
                "retry_profile_id": "interactive",
            },
        ],
    }


def _send(client: HarnessClient, opened: Mapping[str, Any], sender: Mapping[str, Any], receiver: Mapping[str, Any], *, request_id: str = "send-one") -> dict[str, Any]:
    receiver_ref = {field: receiver[field] for field in ("scenario_id", "participant_id", "participant_generation")}
    return client.send_message(
        project_instance_id=PROJECT_ID,
        scenario_id=sender["scenario_id"],
        scenario_generation=1,
        scenario_state_revision=opened["state_revision"],
        sender_participant_id=sender["participant_id"],
        sender_participant_generation=sender["participant_generation"],
        sender_participant_state_revision=sender["state_revision"],
        receiver_intent={"kind": "participant", "participant": receiver_ref},
        message_id="message-one",
        message_kind="collaboration.request",
        message="Review the exact M4 delivery contract.",
        request_id=request_id,
    )


def _wait_delivery(
    client: HarnessClient,
    delivery_id: str,
    *,
    state: str = "consumed",
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        record = client.delivery_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            delivery_id=delivery_id,
        )["delivery"]
        if record["state"] == state:
            return record
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"delivery {delivery_id} stayed {record['state']} instead of {state}"
            )
        time.sleep(0.01)


def test_typed_policy_route_delivery_and_consumption(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        opened, sender, receiver = _prepare(client)
        applied = client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
            request_id="apply-policy",
        )
        assert applied["policy_snapshot"]["policy_digest"] == canonical_json_sha256(applied["policy"])
        assert host.delivery is not None
        collaboration = host.delivery.participant_collaboration_context(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=sender["participant_generation"],
        )
        assert collaboration["participant"]["assignments"] == []
        assert collaboration["peers"] == [
            {
                "participant_id": RECEIVER_ID,
                "participant_generation": receiver["participant_generation"],
                "assignments": [],
            }
        ]
        assert collaboration["allowed_outbound"] == [
            {
                "message_kind": "collaboration.request",
                "receiver_label": RECEIVER_ID,
            }
        ]
        assert collaboration["policy"] == applied["policy_snapshot"]
        assert collaboration["context_digest"] == canonical_json_sha256(
            {
                key: value
                for key, value in collaboration.items()
                if key != "context_digest"
            }
        )
        result = _send(client, opened, sender, receiver)
        assert result["acceptance"] == {
            "outcome": "accepted",
            "durably_enqueued": True,
            "delivery_ids": [result["deliveries"][0]["delivery_id"]],
        }
        assert result["route_decision"]["target_participants"] == [
            {field: receiver[field] for field in ("scenario_id", "participant_id", "participant_generation")}
        ]
        accepted = result["deliveries"][0]
        assert accepted["state"] == "queued"
        assert accepted["events"] == []
        record = _wait_delivery(client, accepted["delivery_id"])
        assert driver.delivery_calls == 1
        assert [event["event"] for event in record["events"]] == [
            "attempt_started", "ack_accepted", "consumed"
        ]
        assert record["target"]["runtime_binding_id"] == (
            "runtime-scenario-one-receiver-one"
        )
        assert record["target"]["presentation_binding_id"] == (
            "presentation-scenario-one-receiver-one"
        )
        assert "message" not in record
        assert client.delivery_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            delivery_id=record["delivery_id"],
        )["delivery"] == record
        assert stat.S_IMODE((state_root / "delivery-state.json").stat().st_mode) == 0o600
        durable = json.loads((state_root / "delivery-state.json").read_text(encoding="utf-8"))
        assert durable["deliveries"][record["delivery_id"]]["message"] == "Review the exact M4 delivery contract."


def test_project_template_plan_apply_and_generation_drift_require_replan(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    template = _policy_template()
    with running_host(state_root) as (host, client, _):
        host.projects.collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id: {"templates": [copy.deepcopy(template)]}
        )
        opened, sender, receiver = _prepare(client)

        listed = client.list_policy_templates(project_instance_id=PROJECT_ID)
        assert listed == {"templates": [template]}
        planned = client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        assert planned["can_apply"] is True
        assert planned["blockers"] == []
        assert planned["policy_pack"]["policy_version"] == 1
        assert planned["team"] == [
            {
                "participant_id": SENDER_ID,
                "participant_generation": 1,
                "present": True,
            },
            {
                "participant_id": RECEIVER_ID,
                "participant_generation": 1,
                "present": True,
            },
        ]
        assert client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]["plan_digest"] == planned["plan_digest"]

        applied = client.apply_policy_plan(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
            plan_digest=planned["plan_digest"],
            request_id="apply-planned-policy",
        )
        assert applied["policy"]["policy_version"] == 1
        assert client.show_policy(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["policy_health"] == {
            "requires_replan": False,
            "generation_drift": [],
        }
        assert host.delivery is not None
        collaboration = host.delivery.participant_collaboration_context(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=sender["participant_generation"],
        )
        assert collaboration["participant"]["assignments"] == [
            {"attribute": "collaboration.role", "task_id": None}
        ]
        assert collaboration["peers"] == [
            {
                "participant_id": RECEIVER_ID,
                "participant_generation": receiver["participant_generation"],
                "assignments": ["collaboration.role:review"],
            }
        ]

        next_plan = client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        assert next_plan["policy_pack"]["policy_version"] == 2
        original_snapshot = host.store.delivery_snapshot

        def drifted_snapshot(
            project_instance_id: str, scenario_id: str
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            scenario, participants = original_snapshot(
                project_instance_id, scenario_id
            )
            participants = copy.deepcopy(participants)
            for participant in participants:
                if participant["participant_id"] == RECEIVER_ID:
                    participant["participant_generation"] = 2
            return scenario, participants

        host.store.delivery_snapshot = drifted_snapshot  # type: ignore[method-assign]
        with pytest.raises(HarnessClientError) as stale_plan:
            client.apply_policy_plan(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                template_id=template["template_id"],
                plan_digest=next_plan["plan_digest"],
                request_id="apply-stale-plan",
            )
        assert stale_plan.value.code == "fence.stale-operation-generation"
        health = client.show_policy(
            project_instance_id=PROJECT_ID, scenario_id=SCENARIO_ID
        )["policy_health"]
        assert health == {
            "requires_replan": True,
            "generation_drift": [
                {
                    "participant_id": RECEIVER_ID,
                    "policy_generation": 1,
                    "current_generation": 2,
                }
            ],
        }
        with pytest.raises(HarnessClientError) as stale_send:
            _send(client, opened, sender, receiver, request_id="send-after-drift")
        assert stale_send.value.code == "fence.stale-operation-generation"


def test_policy_plan_reports_missing_declared_participant(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    missing_id = "synthesizer"
    template = _policy_template(
        participant_ids=(SENDER_ID, RECEIVER_ID, missing_id)
    )
    with running_host(state_root) as (host, client, _):
        host.projects.collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id: {"templates": [copy.deepcopy(template)]}
        )
        opened, _, _ = _prepare(client)
        plan = client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        assert plan["can_apply"] is False
        assert plan["policy_pack"] is None
        assert plan["blockers"] == [f"team.participant-missing:{missing_id}"]


def test_participants_self_send_and_reply_with_scoped_identity(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, owner, driver):
        opened, sender, receiver = _prepare(owner)
        pack = _policy(sender, receiver)
        pack["route_rules"].append(
            {
                "rule_id": "receiver-replies-to-sender",
                "sender": {
                    "kind": "participant",
                    "participant": _participant_ref_for_test(receiver),
                },
                "receiver": {
                    "kind": "participant",
                    "participant": _participant_ref_for_test(sender),
                },
                "message_kind": "collaboration.response",
                "effect": "allow",
                "retry_profile_id": "dogfood",
            }
        )
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=pack,
        )
        sender_context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=1,
            participant_state_revision=sender["state_revision"],
        )["context_path"]
        receiver_context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            participant_generation=1,
            participant_state_revision=receiver["state_revision"],
        )["context_path"]
        forward = ParticipantHarnessClient(Path(sender_context)).send(
            receiver_participant_id=RECEIVER_ID,
            message_id="participant-message-one",
            message_kind="collaboration.request",
            message="Review this fixed implementation.",
        )
        original = forward["deliveries"][0]
        assert forward["acceptance"]["outcome"] == "accepted"
        _wait_delivery(owner, original["delivery_id"])
        reverse = ParticipantHarnessClient(Path(receiver_context)).reply(
            reply_to_delivery_id=original["delivery_id"],
            receiver_participant_id=SENDER_ID,
            message_id="participant-message-two",
            message_kind="collaboration.response",
            message="P0=0 P1=0.",
        )
        assert reverse["acceptance"]["outcome"] == "accepted"
        reply = _wait_delivery(
            owner, reverse["deliveries"][0]["delivery_id"]
        )
        assert reply["target"]["sender"]["participant_id"] == RECEIVER_ID
        assert reply["target"]["receiver"]["participant_id"] == SENDER_ID
        durable = json.loads(
            (state_root / "delivery-state.json").read_text(encoding="utf-8")
        )["deliveries"]
        original_envelope = durable[original["delivery_id"]]
        reply_envelope = durable[reply["delivery_id"]]
        assert original_envelope["reply_to_delivery_id"] is None
        assert (
            original_envelope["thread_root_delivery_id"]
            == original["delivery_id"]
        )
        assert reply_envelope["reply_to_delivery_id"] == original["delivery_id"]
        assert (
            reply_envelope["thread_root_delivery_id"]
            == original["delivery_id"]
        )
        first_page = owner.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=1,
            thread_root_delivery_id=original["delivery_id"],
        )["delivery_collection"]
        assert first_page["summary"] == {"total": 2, "states": {"consumed": 2}}
        assert first_page["deliveries"][0] == {
            "delivery_id": original["delivery_id"],
            "message_kind": "collaboration.request",
            "sender": {"participant_id": SENDER_ID, "participant_generation": 1},
            "receiver": {
                "participant_id": RECEIVER_ID,
                "participant_generation": 1,
            },
            "policy_snapshot": original["policy_snapshot"],
            "thread_root_delivery_id": original["delivery_id"],
            "reply_to_delivery_id": None,
            "state": "consumed",
            "degraded_reason": None,
            "event_sequence": 3,
            "last_event": {
                "sequence": 3,
                "event": "consumed",
                "attempt_number": 1,
                "error_code": None,
            },
            "retry_eligibility": {
                "eligible": False,
                "event_sequence": 3,
                "reason": "delivery.retry-terminal",
            },
        }
        assert first_page["next_page"] is not None
        second_page = owner.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=1,
            after_delivery_id=first_page["next_page"]["after_delivery_id"],
            collection_digest=first_page["next_page"]["collection_digest"],
            thread_root_delivery_id=original["delivery_id"],
        )["delivery_collection"]
        assert [
            value["delivery_id"] for value in second_page["deliveries"]
        ] == [reply["delivery_id"]]
        assert second_page["deliveries"][0]["reply_to_delivery_id"] == original[
            "delivery_id"
        ]
        assert second_page["next_page"] is None
        public_json = json.dumps({"first": first_page, "second": second_page})
        assert "Review this fixed implementation." not in public_json
        assert "P0=0 P1=0." not in public_json
        assert "runtime_binding_id" not in public_json
        assert "presentation_binding_id" not in public_json
        assert "consumption_token" not in public_json
        assert "participant-message-one" not in public_json
        with pytest.raises(HarnessClientError) as invalid_page_size:
            owner.list_deliveries(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                limit=0,
            )
        assert invalid_page_size.value.code == "ipc.operation-schema-mismatch"
        stale_page = owner.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=1,
        )["delivery_collection"]
        ParticipantHarnessClient(Path(sender_context)).send(
            receiver_participant_id=RECEIVER_ID,
            message_id="participant-message-three",
            message_kind="collaboration.request",
            message="Change the collection after its cursor was issued.",
        )
        with pytest.raises(HarnessClientError) as stale_collection:
            owner.list_deliveries(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                limit=1,
                after_delivery_id=stale_page["next_page"]["after_delivery_id"],
                collection_digest=stale_page["next_page"]["collection_digest"],
            )
        assert stale_collection.value.code == "fence.stale-operation-generation"
        assert driver.authorize_sender_calls == 3


def test_reverse_reply_completes_while_original_delivery_awaits_consumption(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, owner, driver):
        opened, sender, receiver = _prepare(owner)
        pack = _policy(sender, receiver)
        pack["route_rules"].append(
            {
                "rule_id": "concurrent-reverse-reply",
                "sender": {
                    "kind": "participant",
                    "participant": _participant_ref_for_test(receiver),
                },
                "receiver": {
                    "kind": "participant",
                    "participant": _participant_ref_for_test(sender),
                },
                "message_kind": "collaboration.response",
                "effect": "allow",
                "retry_profile_id": "dogfood",
            }
        )
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=pack,
        )
        sender_context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=1,
            participant_state_revision=sender["state_revision"],
        )["context_path"]
        receiver_context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            participant_generation=1,
            participant_state_revision=receiver["state_revision"],
        )["context_path"]
        driver.defer_first_consumption = True
        forward = ParticipantHarnessClient(Path(sender_context)).send(
            receiver_participant_id=RECEIVER_ID,
            message_id="concurrent-forward",
            message_kind="collaboration.request",
            message="Reply before my consumption wait completes.",
        )
        assert forward["acceptance"]["outcome"] == "accepted"
        assert forward["deliveries"][0]["state"] == "queued"
        try:
            assert driver.consumption_waiting.wait(timeout=2)
            durable = json.loads(
                (state_root / "delivery-state.json").read_text(
                    encoding="utf-8"
                )
            )["deliveries"]
            original_id = next(iter(durable))
            assert durable[original_id]["record"]["state"] == "delivered"

            reverse = ParticipantHarnessClient(Path(receiver_context)).reply(
                reply_to_delivery_id=original_id,
                receiver_participant_id=SENDER_ID,
                message_id="concurrent-reverse",
                message_kind="collaboration.response",
                message="The Host accepted this without waiting for the first marker.",
            )
            assert reverse["acceptance"]["outcome"] == "accepted"
            assert _wait_delivery(
                owner, reverse["deliveries"][0]["delivery_id"]
            )["state"] == "consumed"
        finally:
            driver.release_consumption.set()
        assert _wait_delivery(
            owner, forward["deliveries"][0]["delivery_id"]
        )["state"] == "consumed"


def test_owner_capability_cannot_impersonate_participant_self_send(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (_, owner, driver):
        opened, sender, receiver = _prepare(owner)
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )
        with pytest.raises(HarnessClientError) as exc:
            owner._call(  # noqa: SLF001 - explicit authority-boundary test
                "message.send-self",
                {
                    "scope": "participant",
                    "project_instance_id": PROJECT_ID,
                    "scenario_id": SCENARIO_ID,
                    "participant_id": SENDER_ID,
                },
                {
                    "operation_generation": sender["state_revision"],
                    "participant_generation": 1,
                },
                {
                    "receiver_participant_id": RECEIVER_ID,
                    "message_id": "owner-impersonation",
                    "message_kind": "collaboration.request",
                    "message": "This must not be accepted.",
                },
            )
        assert exc.value.code == "auth.capability-denied"
        assert driver.authorize_sender_calls == 0


def test_scoped_capability_without_owned_process_proof_is_rejected(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, owner, driver):
        opened, sender, receiver = _prepare(owner)
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )
        context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=1,
            participant_state_revision=sender["state_revision"],
        )["context_path"]
        driver.reject_sender = True
        with pytest.raises(HarnessClientError) as exc:
            ParticipantHarnessClient(Path(context)).send(
                receiver_participant_id=RECEIVER_ID,
                message_id="rejected-process",
                message_kind="collaboration.request",
                message="The process proof is intentionally invalid.",
            )
        assert exc.value.code == "identity.sender-rejected"
        assert driver.delivery_calls == 0


def test_scoped_sender_revision_fence_rejects_stale_context(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, owner, driver):
        opened, sender, receiver = _prepare(owner)
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )
        context = host.participant_auth.ensure(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=1,
            participant_state_revision=sender["state_revision"],
        )["context_path"]
        scoped = ParticipantHarnessClient(Path(context))
        scoped.context["participant_state_revision"] -= 1
        with pytest.raises(HarnessClientError) as exc:
            scoped.send(
                receiver_participant_id=RECEIVER_ID,
                message_id="stale-context",
                message_kind="collaboration.request",
                message="This stale sender fence must be rejected.",
            )
        assert exc.value.code == "fence.stale-operation-generation"
        assert driver.authorize_sender_calls == 0
        assert driver.delivery_calls == 0


def test_bounded_retry_is_target_exact_and_idempotent(tmp_path: Path) -> None:
    with running_host(tmp_path / "state") as (host, client, driver):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver, attempts=2),
        )
        driver.failures_remaining = 1
        result = _send(client, opened, sender, receiver)
        record = _wait_delivery(
            client, result["deliveries"][0]["delivery_id"]
        )
        assert [value["event"] for value in record["events"]] == [
            "attempt_started", "attempt_failed", "attempt_started", "ack_accepted", "consumed"
        ]
        assert driver.delivery_calls == 2
        assert _send(client, opened, sender, receiver) == result
        assert driver.delivery_calls == 2
        delivery_id = result["deliveries"][0]["delivery_id"]
        assert host.delivery is not None
        assert host.delivery._schedule_dispatch(delivery_id) is False  # noqa: SLF001
        assert delivery_id not in host.delivery._active_delivery_ids  # noqa: SLF001


def test_default_deny_fails_before_transport(tmp_path: Path) -> None:
    with running_host(tmp_path / "state") as (_, client, driver):
        opened, sender, receiver = _prepare(client)
        pack = _policy(sender, receiver)
        pack["route_rules"] = []
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=pack,
        )
        with pytest.raises(HarnessClientError) as exc:
            _send(client, opened, sender, receiver)
        assert exc.value.code == "auth.capability-denied"
        assert driver.delivery_calls == 0


def test_all_declared_same_runtime_targets_route_exactly_once(
    tmp_path: Path,
) -> None:
    with running_host(tmp_path / "state") as (_, client, driver):
        opened, sender, receiver = _prepare(client)
        pack = _policy(sender, receiver)
        pack["route_rules"].append(
            {
                "rule_id": "receiver-to-sender",
                "sender": {
                    "kind": "participant",
                    "participant": {
                        field: receiver[field]
                        for field in (
                            "scenario_id",
                            "participant_id",
                            "participant_generation",
                        )
                    },
                },
                "receiver": {
                    "kind": "participant",
                    "participant": {
                        field: sender[field]
                        for field in (
                            "scenario_id",
                            "participant_id",
                            "participant_generation",
                        )
                    },
                },
                "message_kind": "collaboration.request",
                "effect": "allow",
                "retry_profile_id": "dogfood",
            }
        )
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=pack,
        )

        forward = _send(client, opened, sender, receiver, request_id="matrix-forward")
        reverse = client.send_message(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            sender_participant_id=receiver["participant_id"],
            sender_participant_generation=receiver["participant_generation"],
            sender_participant_state_revision=receiver["state_revision"],
            receiver_intent={
                "kind": "participant",
                "participant": {
                    field: sender[field]
                    for field in (
                        "scenario_id",
                        "participant_id",
                        "participant_generation",
                    )
                },
            },
            message_id="message-reverse",
            message_kind="collaboration.request",
            message="Return the exact review result.",
            request_id="matrix-reverse",
        )

        forward_record = _wait_delivery(
            client, forward["deliveries"][0]["delivery_id"]
        )
        reverse_record = _wait_delivery(
            client, reverse["deliveries"][0]["delivery_id"]
        )
        assert driver.delivery_calls == 2
        assert forward_record["state"] == "consumed"
        assert reverse_record["state"] == "consumed"
        assert {
            value["deliveries"][0]["target"]["receiver"]["participant_id"]
            for value in (forward, reverse)
        } == {SENDER_ID, RECEIVER_ID}
        assert all(len(value["deliveries"]) == 1 for value in (forward, reverse))


def test_cross_scenario_receiver_intent_is_denied_before_transport(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    scenario_a = "scenario-a"
    scenario_b = "scenario-b"
    with running_host(state_root) as (_, client, driver):
        opened_a, sender_a, receiver_a = _prepare(client, scenario_id=scenario_a)
        _, _, receiver_b = _prepare(client, scenario_id=scenario_b)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=scenario_a,
            scenario_generation=1,
            scenario_state_revision=opened_a["state_revision"],
            policy_pack=_policy(sender_a, receiver_a),
        )

        with pytest.raises(HarnessClientError) as exc:
            _send(
                client,
                opened_a,
                sender_a,
                receiver_b,
                request_id="cross-scenario-denied",
            )
        assert exc.value.code == "auth.capability-denied"
        assert driver.delivery_calls == 0
        durable = json.loads(
            (state_root / "delivery-state.json").read_text(encoding="utf-8")
        )
        assert durable["deliveries"] == {}
        assert all(
            value["delivery_ids"] == []
            for value in durable["requests"].values()
        )
        for scenario_id, participant in (
            (scenario_a, receiver_a),
            (scenario_b, receiver_b),
        ):
            status = client.participant_status(
                project_instance_id=PROJECT_ID,
                scenario_id=scenario_id,
                participant_id=participant["participant_id"],
                scenario_generation=1,
                scenario_state_revision=(
                    opened_a["state_revision"]
                    if scenario_id == scenario_a
                    else client.scenario_status(
                        project_instance_id=PROJECT_ID,
                        scenario_id=scenario_b,
                    )["scenario"]["state_revision"]
                ),
                participant_generation=participant["participant_generation"],
                participant_state_revision=participant["state_revision"],
            )["participant"]
            assert status["observed_state"] == "ready"
            assert status["runtime_binding_id"] == participant["runtime_binding_id"]


def test_restart_preserves_unknown_attempt_for_exact_retry(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver, attempts=2),
        )
        receiver_ref = {field: receiver[field] for field in ("scenario_id", "participant_id", "participant_generation")}
        driver.interrupt_once = True
        delivery = host.delivery
        assert delivery is not None
        delivery.stop_supervision()
        _, accepted = delivery.send_message(
            request_id="crash-send",
            request_digest="c" * 64,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            sender_participant_id=SENDER_ID,
            sender_participant_generation=1,
            sender_participant_state_revision=sender["state_revision"],
            receiver_intent={"kind": "participant", "participant": receiver_ref},
            message_id="crash-message",
            message_kind="collaboration.request",
            message="Resume this exact delivery.",
        )
        delivery_id = accepted["deliveries"][0]["delivery_id"]
        with pytest.raises(KeyboardInterrupt):
            delivery._dispatch(delivery_id)  # noqa: SLF001
        assert delivery.resumable_delivery_ids() == [delivery_id]
        before = client.delivery_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            delivery_id=delivery_id,
        )["delivery"]
        assert before["state"] == "delivery_attempted"
        assert before["events"][-1]["event"] == "attempt_started"
        delivery._active_delivery_ids.add(delivery_id)  # noqa: SLF001
        try:
            active_view = client.list_deliveries(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["delivery_collection"]["deliveries"][0]
            assert active_view["retry_eligibility"] == {
                "eligible": False,
                "event_sequence": 1,
                "reason": "delivery.retry-in-flight",
            }
            with pytest.raises(HarnessClientError) as concurrent_retry:
                client.retry_delivery(
                    project_instance_id=PROJECT_ID,
                    scenario_id=SCENARIO_ID,
                    delivery_id=delivery_id,
                    event_sequence=1,
                )
            assert concurrent_retry.value.code == "operation.precondition-failed"
        finally:
            delivery._active_delivery_ids.remove(delivery_id)  # noqa: SLF001

    with running_host(state_root) as (_, client, _):
        after = _wait_delivery(client, delivery_id)
        assert after["state"] == "consumed"
        assert [value["event"] for value in after["events"]] == [
            "attempt_started", "attempt_failed", "attempt_started", "ack_accepted", "consumed"
        ]


def test_restart_resumes_delivered_but_unconsumed_without_reinjection(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, driver):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )
        delivery = host.delivery
        assert delivery is not None
        delivery.stop_supervision()
        driver.consumption_failures_remaining = 1
        result = _send(client, opened, sender, receiver)
        delivery_id = result["deliveries"][0]["delivery_id"]
        delivery._dispatch(delivery_id)  # noqa: SLF001
        before = client.delivery_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            delivery_id=delivery_id,
        )["delivery"]
        assert before["state"] == "delivered"
        assert driver.delivery_calls == 1

    with running_host(state_root) as (_, client, resumed_driver):
        after = _wait_delivery(client, delivery_id)
        assert after["state"] == "consumed"
        assert resumed_driver.delivery_calls == 0
