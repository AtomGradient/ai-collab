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

from ai_collab import cli as cli_main
from ai_collab.client import (
    HarnessClient,
    HarnessClientError,
    ParticipantHarnessClient,
)
from ai_collab.delivery import DeliveryCoordinator
from ai_collab.host import HarnessHost
from ai_collab.participant import ParticipantCoordinator, ParticipantError
from ai_collab.protocol import canonical_json_bytes, canonical_json_sha256


PROJECT_ID = "project-one"
SCENARIO_ID = "scenario-one"
SENDER_ID = "sender-one"
RECEIVER_ID = "receiver-one"
_BUILTIN_COLLABORATION_REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "ai_collab_team_policies.json").read_text(
        encoding="utf-8"
    )
)
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
    "collaboration": {
        "kind": "builtin",
        "profile_id": "builtin.standard-v1",
        "registry_snapshot": _BUILTIN_COLLABORATION_REGISTRY,
        "registry_snapshot_digest": canonical_json_sha256(
            _BUILTIN_COLLABORATION_REGISTRY
        ),
    },
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
        self.delivery_payloads: list[dict[str, Any]] = []
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
            self.delivery_payloads.append(copy.deepcopy(dict(payload)))
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


def _send(
    client: HarnessClient,
    opened: Mapping[str, Any],
    sender: Mapping[str, Any],
    receiver: Mapping[str, Any],
    *,
    request_id: str = "send-one",
    message_id: str = "message-one",
) -> dict[str, Any]:
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
        message_id=message_id,
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


def _summary_projection(
    delivery_id: str,
    *,
    kind: str,
    state: str = "consumed",
    reply_to: str | None = None,
    thread_root: str | None = None,
    attempt_number: int | None = 1,
    degraded_reason: str | None = None,
    enqueue_sequence: int = 1,
) -> dict[str, Any]:
    return {
        "delivery_id": delivery_id,
        "enqueue_sequence": enqueue_sequence,
        "message_kind": kind,
        "thread_root_delivery_id": thread_root or delivery_id,
        "reply_to_delivery_id": reply_to,
        "state": state,
        "degraded_reason": degraded_reason,
        "last_event": (
            {
                "event": "consumed" if state == "consumed" else "ack_accepted",
                "attempt_number": attempt_number,
                "error_code": None,
            }
            if attempt_number is not None
            else None
        ),
    }


def _m2_delivery_summary_fixture() -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for index in range(23):
        request_id = f"delivery-review-request-{index:02d}"
        projections.append(
            _summary_projection(
                request_id,
                kind="collaboration.review-request",
                state="delivered" if index < 10 else "consumed",
            )
        )
        projections.append(
            _summary_projection(
                f"delivery-review-response-{index:02d}",
                kind="collaboration.review-response",
                state="delivered" if index < 8 else "consumed",
                reply_to=request_id,
                thread_root=request_id,
            )
        )
    for index in range(12):
        projections.append(
            _summary_projection(
                f"delivery-notice-{index:02d}", kind="collaboration.notice"
            )
        )

    first_pushback = "delivery-pushback-00"
    nested_pushback = "delivery-pushback-01"
    projections.extend(
        [
            _summary_projection(
                first_pushback,
                kind="collaboration.pushback",
                state="delivered",
            ),
            _summary_projection(
                nested_pushback,
                kind="collaboration.pushback",
                reply_to=first_pushback,
                thread_root=first_pushback,
            ),
            _summary_projection(
                "delivery-response-00",
                kind="collaboration.response",
                reply_to=nested_pushback,
                thread_root=first_pushback,
            ),
        ]
    )
    for index in range(2, 5):
        pushback_id = f"delivery-pushback-{index:02d}"
        projections.append(
            _summary_projection(pushback_id, kind="collaboration.pushback")
        )
        projections.append(
            _summary_projection(
                f"delivery-response-{index - 1:02d}",
                kind="collaboration.response",
                reply_to=pushback_id,
                thread_root=pushback_id,
            )
        )
    question_id = "delivery-question-00"
    projections.extend(
        [
            _summary_projection(question_id, kind="collaboration.question"),
            _summary_projection(
                "delivery-response-04",
                kind="collaboration.response",
                reply_to=question_id,
                thread_root=question_id,
            ),
        ]
    )
    assert len(projections) == 69
    return projections


def _list_projection_fixture(
    projections: list[dict[str, Any]], *, limit: int
) -> dict[str, Any]:
    projections = [
        {**projection, "enqueue_sequence": index}
        for index, projection in enumerate(projections, start=1)
    ]
    coordinator = object.__new__(DeliveryCoordinator)
    coordinator._lock = threading.RLock()
    coordinator._active_delivery_ids = set()
    state = {
        "state_revision": 1,
        "deliveries": {
            projection["delivery_id"]: {
                "project_instance_id": PROJECT_ID,
                "scenario_id": SCENARIO_ID,
                "record": {"delivery_id": projection["delivery_id"]},
                "thread_root_delivery_id": projection["thread_root_delivery_id"],
                "projection": projection,
            }
            for projection in projections
        },
    }
    coordinator._read_state = lambda: copy.deepcopy(state)
    coordinator._delivery_projection = (
        lambda item, *, in_flight: copy.deepcopy(item["projection"])
    )
    return coordinator.list_deliveries(
        project_instance_id=PROJECT_ID,
        scenario_id=SCENARIO_ID,
        limit=limit,
    )[1]["delivery_collection"]


def test_delivery_summary_matches_m2_health_fixture() -> None:
    collection = _list_projection_fixture(
        _m2_delivery_summary_fixture(), limit=100
    )

    assert collection["next_page"] is None
    assert collection["summary"] == {
        "total": 69,
        "states": {"consumed": 50, "delivered": 19},
        "kinds": {
            "collaboration.message": 0,
            "collaboration.notice": 12,
            "collaboration.pushback": 5,
            "collaboration.question": 1,
            "collaboration.response": 5,
            "collaboration.review-request": 23,
            "collaboration.review-response": 23,
        },
        "reply_expected_total": 29,
        "reply_expected_closed": 29,
        "delivered_with_reply": 11,
        "attempted_total": 69,
        "first_attempt_total": 69,
        "degraded_total": 0,
    }
    assert collection["summary"]["states"]["consumed"] + collection["summary"][
        "states"
    ]["delivered"] == 69
    assert collection["summary"]["delivered_with_reply"] + 8 == 19
    assert (
        collection["summary"]["states"]["consumed"]
        + collection["summary"]["delivered_with_reply"]
        == 61
    )


def test_delivery_summary_is_full_collection_fact_across_page_limits() -> None:
    projections = _m2_delivery_summary_fixture()
    projections.extend(
        _summary_projection(
            f"delivery-extra-notice-{index:03d}", kind="collaboration.notice"
        )
        for index in range(60)
    )

    first_page = _list_projection_fixture(projections, limit=100)
    full_page = _list_projection_fixture(projections, limit=256)

    assert len(first_page["deliveries"]) == 100
    assert len(full_page["deliveries"]) == 129
    assert first_page["summary"] == full_page["summary"]
    assert first_page["summary"] == {
        "total": 129,
        "states": {"consumed": 110, "delivered": 19},
        "kinds": {
            "collaboration.message": 0,
            "collaboration.notice": 72,
            "collaboration.pushback": 5,
            "collaboration.question": 1,
            "collaboration.response": 5,
            "collaboration.review-request": 23,
            "collaboration.review-response": 23,
        },
        "reply_expected_total": 29,
        "reply_expected_closed": 29,
        "delivered_with_reply": 11,
        "attempted_total": 129,
        "first_attempt_total": 129,
        "degraded_total": 0,
}


def test_unattempted_delivery_does_not_reduce_first_attempt_rate() -> None:
    projections = _m2_delivery_summary_fixture()
    projections.append(
        _summary_projection(
            "delivery-queued",
            kind="collaboration.notice",
            state="queued",
            attempt_number=None,
        )
    )

    summary = _list_projection_fixture(projections, limit=100)["summary"]

    assert summary["total"] == 70
    assert summary["attempted_total"] == 69
    assert summary["first_attempt_total"] == 69
    assert summary["first_attempt_total"] == summary["attempted_total"]


def test_delivery_list_orders_recent_items_by_monotonic_enqueue_sequence(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    accepted_ids: list[str] = []
    with running_host(state_root) as (host, client, _):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )
        host.delivery.stop_supervision()
        for index in range(1, 4):
            result = _send(
                client,
                opened,
                sender,
                receiver,
                request_id=f"sequence-send-{index}",
                message_id=f"sequence-message-{index}",
            )
            accepted_ids.append(result["deliveries"][0]["delivery_id"])

        recent = client.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=2,
        )["delivery_collection"]["deliveries"]
        assert [value["delivery_id"] for value in recent] == list(
            reversed(accepted_ids[1:])
        )
        assert [value["enqueue_sequence"] for value in recent] == [3, 2]
        durable = json.loads(
            (state_root / "delivery-state.json").read_text(encoding="utf-8")
        )["deliveries"]
        assert [durable[value]["enqueue_sequence"] for value in accepted_ids] == [
            1,
            2,
            3,
        ]

    state_path = state_root / "delivery-state.json"
    legacy = json.loads(state_path.read_text(encoding="utf-8"))
    for item in legacy["deliveries"].values():
        item.pop("enqueue_sequence")
    state_path.write_bytes(canonical_json_bytes(legacy) + b"\n")

    with running_host(state_root) as (host, client, _):
        host.delivery.stop_supervision()
        migrated = client.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=3,
        )["delivery_collection"]["deliveries"]
        assert [value["enqueue_sequence"] for value in migrated] == [3, 2, 1]


def test_stopped_participant_delete_settles_delivery_and_readd_rotates_identity(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
            request_id="delete-apply-policy",
        )
        assert host.delivery is not None
        host.delivery.stop_supervision()
        queued = _send(
            client,
            opened,
            sender,
            receiver,
            request_id="delete-queued-message",
        )["deliveries"][0]
        assert queued["state"] == "queued"

        with pytest.raises(HarnessClientError) as active_delete:
            client.destroy_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=RECEIVER_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=receiver["participant_generation"],
                participant_state_revision=receiver["state_revision"],
                request_id="delete-active-receiver",
            )
        assert active_delete.value.code == "operation.precondition-failed"

        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id="delete-stop-receiver",
        )["participant"]
        old_context_path = Path(
            host.participant_auth.ensure(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=RECEIVER_ID,
                participant_generation=stopped["participant_generation"],
                participant_state_revision=stopped["state_revision"],
            )["context_path"]
        )
        assert old_context_path.exists()
        deleted = client.destroy_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=stopped["participant_generation"],
            participant_state_revision=stopped["state_revision"],
            request_id="delete-stopped-receiver",
        )["deleted_participant"]
        assert deleted["participant_generation"] == 1
        assert deleted["next_participant_generation"] == 2
        assert len(deleted["delivery_settlement_evidence_sha256"]) == 64
        assert not old_context_path.exists()
        assert [
            value["participant_id"]
            for value in client.list_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["participants"]
        ] == [SENDER_ID]
        remaining_contexts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (state_root / "participant-collaboration").glob(
                "participant-*.json"
            )
        ]
        assert len(remaining_contexts) == 1
        assert remaining_contexts[0]["participant"]["participant_id"] == SENDER_ID
        assert remaining_contexts[0]["peers"] == []

        delivery_view = client.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=10,
        )["delivery_collection"]
        assert delivery_view["summary"] == {
            "total": 1,
            "states": {"recipient_deleted": 1},
            "kinds": {
                "collaboration.message": 0,
                "collaboration.request": 1,
            },
            "reply_expected_total": 1,
            "reply_expected_closed": 0,
            "delivered_with_reply": 0,
            "attempted_total": 0,
            "first_attempt_total": 0,
            "degraded_total": 1,
        }
        assert delivery_view["deliveries"][0]["state"] == "recipient_deleted"
        assert delivery_view["deliveries"][0]["degraded_reason"] == (
            "delivery.recipient-deleted"
        )
        assert delivery_view["deliveries"][0]["retry_eligibility"] == {
            "eligible": False,
            "event_sequence": 0,
            "reason": "delivery.recipient-deleted",
        }

        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        readded = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="readd-deleted-receiver",
        )["participant"]
        assert readded["participant_generation"] == 2
        durable = json.loads(
            (state_root / "host-state.json").read_text(encoding="utf-8")
        )
        history = durable["scenarios"][
            f"{PROJECT_ID}\x00{SCENARIO_ID}"
        ]["participant_history"][RECEIVER_ID]
        assert [value["participant_generation"] for value in history] == [1]


def test_participant_delete_restart_joins_recorded_delivery_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    request_id = "delete-crash-before-finalize"
    with running_host(state_root) as (host, client, _):
        opened, _, receiver = _prepare(client)
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id="delete-crash-stop",
        )["participant"]
        original_finalize = host.store.finalize_participant_destroy

        def crash_before_finalize(**_: Any) -> dict[str, Any]:
            raise OSError("injected crash before participant finalize")

        monkeypatch.setattr(
            host.store, "finalize_participant_destroy", crash_before_finalize
        )
        with pytest.raises(HarnessClientError) as interrupted:
            client.destroy_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=RECEIVER_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=stopped["participant_generation"],
                participant_state_revision=stopped["state_revision"],
                request_id=request_id,
            )
        assert interrupted.value.code == "operation.external-failure"
        assert interrupted.value.retryable is True
        monkeypatch.setattr(
            host.store, "finalize_participant_destroy", original_finalize
        )

    with running_host(state_root) as (_, client, _):
        replay = client.destroy_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=stopped["participant_generation"],
            participant_state_revision=stopped["state_revision"],
            request_id=request_id,
        )["deleted_participant"]
        assert replay["participant_generation"] == 1
        assert [
            value["participant_id"]
            for value in client.list_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["participants"]
        ] == [SENDER_ID]


def test_participant_delete_stays_transitional_while_delivery_is_unavailable(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        opened, _, receiver = _prepare(client)
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id="delete-unavailable-stop",
        )["participant"]
        operation_id, replay, _ = host.store.begin_participant_destroy(
            request_id="delete-unavailable",
            request_digest="a" * 64,
            host_generation=host.host_generation,
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=stopped["participant_generation"],
            participant_state_revision=stopped["state_revision"],
        )
        assert replay is None

    with tempfile.TemporaryDirectory(prefix="acd-") as runtime:
        unavailable = HarnessHost(state_root, Path(runtime) / "host.sock")
        unavailable.bind()
        try:
            pending = unavailable.store.pending_participant_destroy_operations()
            assert [value["operation_id"] for value in pending] == [operation_id]
            participant = next(
                value
                for value in unavailable.store.list_participants(
                    PROJECT_ID, SCENARIO_ID
                )["participants"]
                if value["participant_id"] == RECEIVER_ID
            )
            assert participant["observed_state"] == "destroying"
        finally:
            assert unavailable._server is not None
            unavailable._server.server_close()
            unavailable._server = None
            unavailable._remove_owned_socket()

    with running_host(state_root) as (_, client, _):
        assert [
            value["participant_id"]
            for value in client.list_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["participants"]
        ] == [SENDER_ID]


def test_participant_delete_cli_requires_explicit_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        opened, _, receiver = _prepare(client)
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id="delete-cli-stop",
        )["participant"]
        arguments = [
            "harness",
            "participant",
            "delete",
            RECEIVER_ID,
            "--scenario-id",
            SCENARIO_ID,
            "--project-instance-id",
            PROJECT_ID,
            "--scenario-generation",
            str(opened["scenario_generation"]),
            "--scenario-state-revision",
            str(opened["state_revision"]),
            "--participant-generation",
            str(stopped["participant_generation"]),
            "--participant-state-revision",
            str(stopped["state_revision"]),
            "--request-id",
            "delete-cli-confirmed",
            "--state-root",
            str(state_root),
            "--socket-path",
            str(host.socket_path),
        ]
        assert cli_main.main(arguments) == 1
        refused = json.loads(capsys.readouterr().out)
        assert refused["code"] == "cli.confirmation-required"
        assert cli_main.main([*arguments, "--confirm"]) == 0
        completed = json.loads(capsys.readouterr().out)
        assert completed["deleted_participant"]["participant_generation"] == 1


@pytest.mark.parametrize("publication", ["before", "after"])
def test_participant_delete_resolves_begin_publication_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
) -> None:
    state_root = tmp_path / publication
    with running_host(state_root) as (host, client, _):
        opened, _, receiver = _prepare(client)
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id=f"delete-{publication}-stop",
        )["participant"]
        original_write = host.store._write_state
        injected = False

        def ambiguous_write(value: dict[str, Any]) -> None:
            nonlocal injected
            is_destroy_begin = any(
                operation.get("operation_kind") == "participant.destroy"
                and operation.get("state") == "executing_external"
                for operation in value["operations"].values()
            ) and any(
                request.get("status") == "pending"
                and "delivery_request_id" in request
                and request.get("pending_external_result") is None
                for request in value["requests"].values()
            )
            if not injected and is_destroy_begin:
                injected = True
                if publication == "after":
                    original_write(value)
                raise OSError(f"injected {publication}-publication failure")
            original_write(value)

        monkeypatch.setattr(host.store, "_write_state", ambiguous_write)
        call = {
            "project_instance_id": PROJECT_ID,
            "scenario_id": SCENARIO_ID,
            "participant_id": RECEIVER_ID,
            "scenario_generation": opened["scenario_generation"],
            "scenario_state_revision": opened["state_revision"],
            "participant_generation": stopped["participant_generation"],
            "participant_state_revision": stopped["state_revision"],
            "request_id": f"delete-{publication}-begin",
        }
        if publication == "before":
            with pytest.raises(HarnessClientError) as first:
                client.destroy_participant(**call)
            assert first.value.code == "operation.external-failure"
            assert first.value.mutation_state == "not_started"
            assert first.value.retryable is True
            monkeypatch.setattr(host.store, "_write_state", original_write)
            deleted = client.destroy_participant(**call)["deleted_participant"]
        else:
            deleted = client.destroy_participant(**call)["deleted_participant"]
        assert deleted["participant_generation"] == 1


def test_concurrent_send_cannot_land_after_recipient_deletion_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        opened, sender, receiver = _prepare(client)
        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
            request_id="delete-race-policy",
        )
        assert host.delivery is not None
        host.delivery.stop_supervision()
        original_snapshot = host.store.delivery_snapshot
        snapshot_taken = threading.Event()
        release_snapshot = threading.Event()
        block_once = True

        def blocking_snapshot(
            project_instance_id: str, scenario_id: str
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            nonlocal block_once
            value = original_snapshot(project_instance_id, scenario_id)
            if block_once:
                block_once = False
                snapshot_taken.set()
                assert release_snapshot.wait(timeout=3)
            return value

        monkeypatch.setattr(host.store, "delivery_snapshot", blocking_snapshot)
        send_result: list[dict[str, Any]] = []
        send_error: list[BaseException] = []

        def send() -> None:
            try:
                send_result.append(
                    _send(
                        client,
                        opened,
                        sender,
                        receiver,
                        request_id="delete-race-send",
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                send_error.append(exc)

        send_thread = threading.Thread(target=send)
        send_thread.start()
        assert snapshot_taken.wait(timeout=3)
        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=RECEIVER_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            participant_generation=receiver["participant_generation"],
            participant_state_revision=receiver["state_revision"],
            request_id="delete-race-stop",
        )["participant"]
        delete_result: list[dict[str, Any]] = []
        delete_error: list[BaseException] = []

        def delete() -> None:
            try:
                delete_result.append(
                    client.destroy_participant(
                        project_instance_id=PROJECT_ID,
                        scenario_id=SCENARIO_ID,
                        participant_id=RECEIVER_ID,
                        scenario_generation=opened["scenario_generation"],
                        scenario_state_revision=opened["state_revision"],
                        participant_generation=stopped[
                            "participant_generation"
                        ],
                        participant_state_revision=stopped["state_revision"],
                        request_id="delete-race-destroy",
                    )
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                delete_error.append(exc)

        delete_thread = threading.Thread(target=delete)
        delete_thread.start()
        release_snapshot.set()
        send_thread.join(timeout=3)
        delete_thread.join(timeout=3)
        assert not send_thread.is_alive()
        assert not delete_thread.is_alive()
        assert send_error == []
        assert delete_error == []
        assert send_result[0]["acceptance"]["outcome"] == "accepted"
        assert delete_result[0]["deleted_participant"][
            "participant_generation"
        ] == 1
        collection = client.list_deliveries(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            limit=10,
        )["delivery_collection"]
        assert collection["summary"]["states"] == {
            "recipient_deleted": 1
        }


def test_participant_delete_rejects_unreleased_exact_generation_resource(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, client, _):
        opened, _, receiver = _prepare(client)
        host._stop_resource_supervisor()
        state = host.store._read_state()
        item = state["scenarios"][f"{PROJECT_ID}\x00{SCENARIO_ID}"]
        record = item["participants"][RECEIVER_ID]
        record.update(
            {
                "desired_state": "stopped",
                "observed_state": "stopped",
                "runtime_binding_id": None,
                "presentation_binding_id": None,
                "state_revision": record["state_revision"] + 1,
            }
        )
        host.store._write_state(state)
        with pytest.raises(HarnessClientError) as refused:
            client.destroy_participant(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                participant_id=RECEIVER_ID,
                scenario_generation=opened["scenario_generation"],
                scenario_state_revision=opened["state_revision"],
                participant_generation=receiver["participant_generation"],
                participant_state_revision=record["state_revision"],
                request_id="delete-unreleased-resource",
            )
        assert refused.value.code == "operation.precondition-failed"
        assert any(
            value["participant_id"] == RECEIVER_ID
            for value in client.list_participants(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
            )["participants"]
        )


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
        assert collaboration["scenario"]["objective"] == {
            "revision": 0,
            "objective": "",
            "acceptance_criteria": "",
        }
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


def test_participant_collaboration_context_contains_current_objective_revision(
    tmp_path: Path,
) -> None:
    with running_host(tmp_path / "state") as (host, client, _):
        opened, sender, _ = _prepare(client)
        updated = client.append_scenario_objective(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=opened["scenario_generation"],
            scenario_state_revision=opened["state_revision"],
            objective="Review the delivery evidence",
            acceptance_criteria="The final report cites concrete delivery IDs.",
        )["scenario"]

        assert host.delivery is not None
        context = host.delivery.participant_collaboration_context(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            participant_generation=sender["participant_generation"],
        )
        assert context["context_revision"] == updated["state_revision"]
        assert context["scenario"]["objective"] == {
            "revision": 1,
            "objective": "Review the delivery evidence",
            "acceptance_criteria": "The final report cites concrete delivery IDs.",
        }


def test_project_template_plan_apply_and_generation_drift_require_replan(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    template = _policy_template()
    with running_host(state_root) as (host, client, _):
        host.projects.collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id: {"templates": [copy.deepcopy(template)]}
        )
        host._scenario_collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id, scenario_id: {
                "templates": [copy.deepcopy(template)]
            }
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
        host._scenario_collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id, scenario_id: {
                "templates": [copy.deepcopy(template)]
            }
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


def test_policy_plan_does_not_infer_replacement_without_matching_policy(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    replacement_id = "sender-two"
    template = _policy_template()
    with running_host(state_root) as (host, client, _):
        host.projects.collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id: {"templates": [copy.deepcopy(template)]}
        )
        host._scenario_collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id, scenario_id: {
                "templates": [copy.deepcopy(template)]
            }
        )
        opened, sender, receiver = _prepare(client)
        original_snapshot = host.store.delivery_snapshot

        def renamed_sender(
            project_instance_id: str, scenario_id: str
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            scenario, participants = original_snapshot(
                project_instance_id, scenario_id
            )
            renamed = copy.deepcopy(sender)
            renamed["participant_id"] = replacement_id
            current_receiver = next(
                value
                for value in participants
                if value["participant_id"] == RECEIVER_ID
            )
            return scenario, [renamed, current_receiver]

        host.store.delivery_snapshot = renamed_sender  # type: ignore[method-assign]
        try:
            initial = client.plan_policy(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                template_id=template["template_id"],
            )["policy_plan"]
        finally:
            host.store.delivery_snapshot = original_snapshot  # type: ignore[method-assign]
        assert initial["can_apply"] is False
        assert initial["blockers"] == [
            f"team.participant-missing:{SENDER_ID}"
        ]
        assert all(
            "template_participant_id" not in member
            for member in initial["team"]
        )

        client.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
            request_id="apply-different-policy-before-replacement",
        )
        host.store.delivery_snapshot = renamed_sender  # type: ignore[method-assign]
        try:
            conflict = client.plan_policy(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                template_id=template["template_id"],
            )["policy_plan"]
        finally:
            host.store.delivery_snapshot = original_snapshot  # type: ignore[method-assign]
        assert conflict["can_apply"] is False
        assert conflict["blockers"] == [
            "policy.template-conflict",
            f"team.participant-missing:{SENDER_ID}",
        ]
        assert all(
            "template_participant_id" not in member
            for member in conflict["team"]
        )


def test_policy_replan_rebinds_one_missing_member_and_refreshes_live_context(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    replacement_id = "sender-two"
    template = _policy_template()
    with running_host(state_root) as (host, client, _):
        host.projects.collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id: {"templates": [copy.deepcopy(template)]}
        )
        host._scenario_collaboration_templates = (  # type: ignore[method-assign]
            lambda project_id, scenario_id: {
                "templates": [copy.deepcopy(template)]
            }
        )
        opened, sender, receiver = _prepare(client)
        initial = client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        client.apply_policy_plan(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
            plan_digest=initial["plan_digest"],
            request_id="apply-policy-before-replacement",
        )

        original_snapshot = host.store.delivery_snapshot

        def headless_replacement(
            project_instance_id: str, scenario_id: str
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            scenario, participants = original_snapshot(
                project_instance_id, scenario_id
            )
            current_receiver = next(
                value
                for value in participants
                if value["participant_id"] == RECEIVER_ID
            )
            replacement = copy.deepcopy(sender)
            replacement["participant_id"] = replacement_id
            replacement["interaction_mode"] = "headless"
            return scenario, [current_receiver, replacement]

        host.store.delivery_snapshot = headless_replacement  # type: ignore[method-assign]
        try:
            headless = client.plan_policy(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                template_id=template["template_id"],
            )["policy_plan"]
        finally:
            host.store.delivery_snapshot = original_snapshot  # type: ignore[method-assign]
        assert headless["can_apply"] is False
        assert headless["blockers"] == [
            f"team.participant-missing:{SENDER_ID}"
        ]

        def ambiguous_replacements(
            project_instance_id: str, scenario_id: str
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            scenario, participants = original_snapshot(
                project_instance_id, scenario_id
            )
            current_receiver = next(
                value
                for value in participants
                if value["participant_id"] == RECEIVER_ID
            )
            replacements = []
            for participant_id in ("sender-two", "sender-three"):
                replacement = copy.deepcopy(sender)
                replacement["participant_id"] = participant_id
                replacements.append(replacement)
            return scenario, [current_receiver, *replacements]

        host.store.delivery_snapshot = ambiguous_replacements  # type: ignore[method-assign]
        try:
            ambiguous = client.plan_policy(
                project_instance_id=PROJECT_ID,
                scenario_id=SCENARIO_ID,
                scenario_generation=1,
                scenario_state_revision=opened["state_revision"],
                template_id=template["template_id"],
            )["policy_plan"]
        finally:
            host.store.delivery_snapshot = original_snapshot  # type: ignore[method-assign]
        assert ambiguous["can_apply"] is False
        assert ambiguous["blockers"] == [
            f"team.participant-missing:{SENDER_ID}"
        ]

        stopped = client.stop_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=sender["participant_generation"],
            participant_state_revision=sender["state_revision"],
            request_id="stop-policy-member",
        )["participant"]
        client.destroy_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=SENDER_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=stopped["participant_generation"],
            participant_state_revision=stopped["state_revision"],
            request_id="delete-policy-member",
        )
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]
        replacement = client.add_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=replacement_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
            request_id="add-policy-replacement",
        )["participant"]
        scenario = client.scenario_status(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["scenario"]

        repair = client.plan_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        assert repair["can_apply"] is True
        assert repair["blockers"] == []
        assert repair["team"][0] == {
            "participant_id": replacement_id,
            "participant_generation": 1,
            "present": True,
            "template_participant_id": SENDER_ID,
        }
        applied = client.apply_policy_plan(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            template_id=template["template_id"],
            plan_digest=repair["plan_digest"],
            request_id="apply-policy-replacement",
        )
        assert applied["policy"]["policy_version"] == 2
        assert {
            assignment["participant"]["participant_id"]
            for assignment in applied["policy"]["assignments"]
        } == {replacement_id, RECEIVER_ID}
        assert client.show_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
        )["policy_health"] == {
            "requires_replan": False,
            "generation_drift": [],
        }

        receiver_context = next(
            value
            for value in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (state_root / "participant-collaboration").glob(
                    "participant-*.json"
                )
            )
            if value["participant"]["participant_id"] == RECEIVER_ID
        )
        assert receiver_context["policy"]["policy_version"] == 2
        assert receiver_context["peers"] == [
            {
                "participant_id": replacement_id,
                "participant_generation": 1,
                "assignments": ["collaboration.role"],
            }
        ]
        assert receiver_context["allowed_outbound"] == [
            {
                "message_kind": "collaboration.response",
                "receiver_label": replacement_id,
            }
        ]

        client.start_participant(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            participant_id=replacement_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            participant_generation=replacement["participant_generation"],
            participant_state_revision=replacement["state_revision"],
            request_id="start-policy-replacement",
        )
        replacement_context = next(
            value
            for value in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in (state_root / "participant-collaboration").glob(
                    "participant-*.json"
                )
            )
            if value["participant"]["participant_id"] == replacement_id
        )
        assert replacement_context["participant"]["assignments"] == [
            {"attribute": "collaboration.role", "task_id": None}
        ]
        assert replacement_context["allowed_outbound"] == [
            {
                "message_kind": "collaboration.request",
                "receiver_label": RECEIVER_ID,
            }
        ]


def test_host_delivery_carries_receipt_working_directory(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    with running_host(state_root) as (host, owner, driver):
        opened, sender, receiver = _prepare(owner)
        assert host.participants is not None
        host.participants._workspace_summary = lambda _project, _scenario: {
            "receipt": {
                "participant_working_directory": "bundle/someproject",
            }
        }
        owner.apply_policy(
            project_instance_id=PROJECT_ID,
            scenario_id=SCENARIO_ID,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            policy_pack=_policy(sender, receiver),
        )

        sent = _send(
            owner,
            opened,
            sender,
            receiver,
            request_id="send-with-receipt-working-directory",
            message_id="message-with-receipt-working-directory",
        )
        _wait_delivery(owner, sent["deliveries"][0]["delivery_id"])

        payload = driver.delivery_payloads[-1]
        assert payload["workspace_path"] == str(
            host.store.workspace_path(opened["workspace_binding_id"])
        )
        assert payload["participant_working_directory"] == "bundle/someproject"


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
        assert driver.delivery_payloads[-1]["reply_to_delivery_id"] is None
        assert driver.delivery_payloads[-1]["workspace_path"] == str(
            host.store.workspace_path(opened["workspace_binding_id"])
        )
        assert "participant_working_directory" not in driver.delivery_payloads[-1]
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
        assert (
            driver.delivery_payloads[-1]["reply_to_delivery_id"]
            == original["delivery_id"]
        )
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
        assert first_page["summary"] == {
            "total": 2,
            "states": {"consumed": 2},
            "kinds": {
                "collaboration.message": 0,
                "collaboration.request": 1,
                "collaboration.response": 1,
            },
            "reply_expected_total": 1,
            "reply_expected_closed": 1,
            "delivered_with_reply": 0,
            "attempted_total": 2,
            "first_attempt_total": 2,
            "degraded_total": 0,
        }
        assert first_page["deliveries"][0] == {
            "delivery_id": reply["delivery_id"],
            "enqueue_sequence": 2,
            "message_kind": "collaboration.response",
            "sender": {"participant_id": RECEIVER_ID, "participant_generation": 1},
            "receiver": {
                "participant_id": SENDER_ID,
                "participant_generation": 1,
            },
            "policy_snapshot": reply["policy_snapshot"],
            "thread_root_delivery_id": original["delivery_id"],
            "reply_to_delivery_id": original["delivery_id"],
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
        ] == [original["delivery_id"]]
        assert second_page["deliveries"][0]["enqueue_sequence"] == 1
        assert second_page["deliveries"][0]["reply_to_delivery_id"] is None
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


def _versioned_policy_template(version: int) -> dict[str, Any]:
    template = _policy_template()
    template["display_name"] = f"Peer review team v{version}"
    for assignment in template["assignments"]:
        if assignment["participant_id"] == RECEIVER_ID:
            assignment["task_id"] = f"review-v{version}"
    return template


def _snapshot_project_render(
    template: Mapping[str, Any], *, version: int
) -> dict[str, Any]:
    registry = {
        "schema_version": 1,
        "templates": [copy.deepcopy(dict(template))],
    }
    availability: dict[str, Any] = {
        "status": "ready",
        "observations": [],
        "changes": [],
        "warnings": [],
    }
    availability["fingerprint"] = canonical_json_sha256(availability)
    render: dict[str, Any] = {
        "render_contract_version": 1,
        "source": {
            "kind": "team-intent",
            "intent_schema_version": 1,
            "source_digest": str(version) * 64,
        },
        "project": {
            "project_key": "snapshot-project",
            "product_contract_version": "1.0",
            "workspace_adapter_id": "workspace.test-v1",
            "environment_adapter_id": "environment.test-v1",
            "participant_driver_contract": 2,
            "collaboration_policy_schema": 1,
        },
        "repo_manifest": {
            "schema_version": 1,
            "project_key": "snapshot-project",
            "repos": [],
        },
        "repo_manifest_digest": "2" * 64,
        "gate": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
        "collaboration": {
            "kind": "project-registry",
            "relative_path": "ai_collab_team_policies.json",
            "digest": canonical_json_sha256(registry),
            "registry_snapshot": registry,
            "registry_snapshot_digest": canonical_json_sha256(registry),
        },
        "availability": availability,
    }
    render["render_digest"] = canonical_json_sha256(
        {key: value for key, value in render.items() if key != "availability"}
    )
    return render


class SnapshotProjectAdapter:
    def __init__(self, project_root: Path, render: Mapping[str, Any]) -> None:
        self.project_root = project_root
        self.render = copy.deepcopy(dict(render))
        self.collaboration_calls = 0

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        del timeout_seconds
        if operation == "collaboration_templates":
            self.collaboration_calls += 1
            registry_path = self.project_root / "ai_collab_team_policies.json"
            if not registry_path.is_file():
                raise AssertionError("mutable collaboration registry was consulted")
            return {
                "templates": json.loads(registry_path.read_text(encoding="utf-8"))[
                    "templates"
                ]
            }
        assert operation == "register"
        assert Path(payload["canonical_project_path"]) == self.project_root
        render = copy.deepcopy(self.render)
        project = render["project"]
        return {
            "project": {
                "project_key": project["project_key"],
                "project_binding_digest": render["render_digest"],
                "product_contract_version": project["product_contract_version"],
                "workspace_adapter_id": project["workspace_adapter_id"],
                "environment_adapter_id": project["environment_adapter_id"],
                "participant_driver_contract": project[
                    "participant_driver_contract"
                ],
                "collaboration_policy_schema": project[
                    "collaboration_policy_schema"
                ],
                "repo_manifest_digest": render["repo_manifest_digest"],
                "adapter_capability_digest": "b" * 64,
            },
            "render": render,
        }


@contextmanager
def running_snapshot_policy_host(
    state_root: Path, adapter: SnapshotProjectAdapter
) -> Iterator[tuple[HarnessHost, HarnessClient]]:
    with tempfile.TemporaryDirectory(prefix="acps-") as runtime:
        host = HarnessHost(state_root, Path(runtime) / "host.sock")
        host.projects.adapter = adapter  # type: ignore[assignment]
        driver = FakeDeliveryDriver()
        host.participants = ParticipantCoordinator(host.store, driver)  # type: ignore[arg-type]
        host.delivery = DeliveryCoordinator(state_root, host.store, host.participants)
        host.bind()
        thread = threading.Thread(target=host.serve_forever, daemon=True)
        thread.start()
        try:
            yield host, HarnessClient(state_root, host.socket_path)
        finally:
            host.shutdown()
            thread.join(timeout=3)


def _prepare_snapshot_policy_scenario(
    client: HarnessClient,
    *,
    project_instance_id: str,
    scenario_id: str,
    project_binding_digest: str,
) -> dict[str, Any]:
    created = client.create_scenario(
        project_instance_id=project_instance_id,
        scenario_id=scenario_id,
        project_binding_digest=project_binding_digest,
    )["scenario"]
    added: dict[str, dict[str, Any]] = {}
    for participant_id in (SENDER_ID, RECEIVER_ID):
        added[participant_id] = client.add_participant(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=1,
            scenario_state_revision=created["state_revision"],
            launch_spec=_launch_spec(),
            presentation_driver_id="presentation.iterm2",
        )["participant"]
    opened = client.open_scenario(
        project_instance_id=project_instance_id,
        scenario_id=scenario_id,
        scenario_generation=1,
        scenario_state_revision=created["state_revision"],
    )["scenario"]
    for participant_id in (SENDER_ID, RECEIVER_ID):
        client.start_participant(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            participant_id=participant_id,
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            participant_generation=1,
            participant_state_revision=added[participant_id]["state_revision"],
        )
    return opened


def _write_snapshot_project_files(
    project_root: Path, template: Mapping[str, Any]
) -> None:
    intent_root = project_root / ".aicollab"
    intent_root.mkdir(parents=True, exist_ok=True)
    (intent_root / "project.yaml").write_text(
        "schema_version: 1\ncollaboration:\n  registry: ai_collab_team_policies.json\n",
        encoding="utf-8",
    )
    (project_root / "ai_collab_team_policies.json").write_text(
        json.dumps({"schema_version": 1, "templates": [template]}),
        encoding="utf-8",
    )


def _remove_snapshot_project_files(project_root: Path) -> None:
    (project_root / "ai_collab_team_policies.json").unlink()
    (project_root / ".aicollab" / "project.yaml").unlink()
    (project_root / ".aicollab").rmdir()
    project_root.rmdir()


def test_policy_catalog_updates_without_rewriting_scenario_snapshots(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "canonical"
    project_root.mkdir()
    template_v1 = _versioned_policy_template(1)
    template_v2 = _versioned_policy_template(2)
    render_v1 = _snapshot_project_render(template_v1, version=1)
    render_v2 = _snapshot_project_render(template_v2, version=2)
    _write_snapshot_project_files(project_root, template_v1)
    adapter = SnapshotProjectAdapter(project_root, render_v1)

    with running_snapshot_policy_host(state_root, adapter) as (_, client):
        project = client.register_project(
            canonical_project_path=str(project_root),
            request_id="register-policy-snapshot-project",
        )["project"]
        project_id = project["project_instance_id"]
        opened_a = _prepare_snapshot_policy_scenario(
            client,
            project_instance_id=project_id,
            scenario_id="snapshot-a",
            project_binding_digest=render_v1["render_digest"],
        )

        _write_snapshot_project_files(project_root, template_v2)
        adapter.render = copy.deepcopy(render_v2)
        observed = client.reconcile_project(
            project_instance_id=project_id,
            request_id="observe-policy-v2",
        )
        assert observed["reconciliation"]["binding_changed"] is True
        accepted = client.accept_project_reconciliation(
            project_instance_id=project_id,
            availability_fingerprint=observed["reconciliation"][
                "availability_fingerprint"
            ],
            request_id="accept-policy-v2",
        )
        assert accepted["project"]["project_binding_digest"] == render_v2[
            "render_digest"
        ]
        opened_b = _prepare_snapshot_policy_scenario(
            client,
            project_instance_id=project_id,
            scenario_id="snapshot-b",
            project_binding_digest=render_v2["render_digest"],
        )

        assert client.list_policy_templates(project_instance_id=project_id) == {
            "templates": [template_v2]
        }
        plan_a = client.plan_policy(
            project_instance_id=project_id,
            scenario_id="snapshot-a",
            scenario_generation=1,
            scenario_state_revision=opened_a["state_revision"],
            template_id=template_v1["template_id"],
        )["policy_plan"]
        plan_b = client.plan_policy(
            project_instance_id=project_id,
            scenario_id="snapshot-b",
            scenario_generation=1,
            scenario_state_revision=opened_b["state_revision"],
            template_id=template_v2["template_id"],
        )["policy_plan"]
        assert plan_a["template_snapshot"]["template_digest"] == (
            canonical_json_sha256(template_v1)
        )
        assert plan_b["template_snapshot"]["template_digest"] == (
            canonical_json_sha256(template_v2)
        )
        assert next(
            value
            for value in plan_a["policy_pack"]["assignments"]
            if value["participant"]["participant_id"] == RECEIVER_ID
        )["task_id"] == "review-v1"
        assert next(
            value
            for value in plan_b["policy_pack"]["assignments"]
            if value["participant"]["participant_id"] == RECEIVER_ID
        )["task_id"] == "review-v2"

        _remove_snapshot_project_files(project_root)
        assert client.list_policy_templates(project_instance_id=project_id) == {
            "templates": [template_v2]
        }
        assert client.plan_policy(
            project_instance_id=project_id,
            scenario_id="snapshot-a",
            scenario_generation=1,
            scenario_state_revision=opened_a["state_revision"],
            template_id=template_v1["template_id"],
        )["policy_plan"] == plan_a
        assert adapter.collaboration_calls == 0


def test_policy_apply_plan_replays_before_mutable_template_inputs(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "canonical"
    project_root.mkdir()
    template = _versioned_policy_template(1)
    render = _snapshot_project_render(template, version=1)
    _write_snapshot_project_files(project_root, template)
    adapter = SnapshotProjectAdapter(project_root, render)

    with running_snapshot_policy_host(state_root, adapter) as (host, client):
        project_id = client.register_project(
            canonical_project_path=str(project_root),
            request_id="register-policy-replay-project",
        )["project"]["project_instance_id"]
        opened = _prepare_snapshot_policy_scenario(
            client,
            project_instance_id=project_id,
            scenario_id="snapshot-replay",
            project_binding_digest=render["render_digest"],
        )
        plan = client.plan_policy(
            project_instance_id=project_id,
            scenario_id="snapshot-replay",
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
        )["policy_plan"]
        applied = client.apply_policy_plan(
            project_instance_id=project_id,
            scenario_id="snapshot-replay",
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
            plan_digest=plan["plan_digest"],
            request_id="apply-frozen-policy-plan",
        )

        _remove_snapshot_project_files(project_root)

        def mutable_input_was_consulted(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("mutable policy input was consulted before replay")

        host._scenario_collaboration_templates = (  # type: ignore[method-assign]
            mutable_input_was_consulted
        )
        assert host.delivery is not None
        host.delivery.plan_policy = mutable_input_was_consulted  # type: ignore[method-assign]
        assert client.apply_policy_plan(
            project_instance_id=project_id,
            scenario_id="snapshot-replay",
            scenario_generation=1,
            scenario_state_revision=opened["state_revision"],
            template_id=template["template_id"],
            plan_digest=plan["plan_digest"],
            request_id="apply-frozen-policy-plan",
        ) == applied
        assert adapter.collaboration_calls == 0
