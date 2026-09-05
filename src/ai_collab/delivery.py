# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Durable ordered policy routing and exact participant delivery."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .participant import ParticipantCoordinator, ParticipantError
from .playbooks import opening_text
from .protocol import canonical_json_bytes, canonical_json_sha256
from .store import ScenarioStore, StoreError


DELIVERY_STATE_SCHEMA_VERSION = 1
NAMESPACED_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
OPAQUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
# The room-wide default: every colleague may send every kind to every other
# colleague. The engine has no wildcard, so the kinds are enumerated and one
# exact rule is written per ordered pair and kind, bound to live generations.
DEFAULT_POLICY_ID = "team.room-open"
OPEN_MESSAGE_KINDS = (
    "collaboration.message",
    "collaboration.response",
    "collaboration.request",
    "collaboration.question",
    "collaboration.review-request",
    "collaboration.review-response",
    "collaboration.pushback",
    "collaboration.notice",
    "collaboration.done",
)
OPEN_RETRY_PROFILE = {"profile_id": "interactive", "max_attempts": 3, "backoff_ms": [0, 500, 2000]}
IMMUTABLE_FIELDS = {
    "delivery_id",
    "message_id",
    "route_request_digest",
    "route_decision_digest",
    "policy_snapshot",
    "target",
    "payload_digest",
    "retry_profile",
}
REPLY_EXPECTED_MESSAGE_KINDS = frozenset(
    {
        "collaboration.request",
        "collaboration.question",
        "collaboration.review-request",
        "collaboration.pushback",
    }
)


@dataclass
class DeliveryError(ValueError):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


def _participant_ref(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": record["scenario_id"],
        "participant_id": record["participant_id"],
        "participant_generation": record["participant_generation"],
    }


def _policy_snapshot(pack: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": pack["policy_id"],
        "policy_version": pack["policy_version"],
        "policy_digest": canonical_json_sha256(pack),
    }


class DeliveryCoordinator:
    """Own policy snapshots, append-only delivery records, and driver ACK joins."""

    def __init__(
        self,
        state_root: Path,
        store: ScenarioStore,
        participants: ParticipantCoordinator,
    ) -> None:
        self.state_root = Path(state_root).resolve()
        self.state_path = self.state_root / "delivery-state.json"
        self.store = store
        self.participants = participants
        self._lock = threading.RLock()
        self._active_delivery_ids: set[str] = set()
        self._dispatch_threads: dict[str, threading.Thread] = {}
        self._supervision_enabled = False
        with self._lock:
            if not self.state_path.exists():
                self._write_state(self._empty_state())
            self._read_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": DELIVERY_STATE_SCHEMA_VERSION,
            "state_revision": 0,
            "policies": {},
            "deliveries": {},
            "requests": {},
        }

    def _read_state(self) -> dict[str, Any]:
        if self.state_path.is_symlink() or not self.state_path.is_file():
            raise DeliveryError("delivery.state-invalid", "delivery state is unavailable")
        details = self.state_path.stat()
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
            raise DeliveryError("delivery.state-invalid", "delivery state permissions differ")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeliveryError("delivery.state-invalid", "delivery state is invalid") from exc
        if (
            not isinstance(value, dict)
            or set(value) - {"policy_upgrade"}
            != {"schema_version", "state_revision", "policies", "deliveries", "requests"}
            or value["schema_version"] != DELIVERY_STATE_SCHEMA_VERSION
            or not isinstance(value.get("policy_upgrade", 0), int)
            or not isinstance(value["state_revision"], int)
            or not isinstance(value["policies"], dict)
            or not isinstance(value["deliveries"], dict)
            or not isinstance(value["requests"], dict)
        ):
            raise DeliveryError("delivery.state-invalid", "delivery state schema differs")
        used_sequences: set[int] = set()
        missing_sequence_ids: list[str] = []
        for delivery_id in sorted(value["deliveries"]):
            item = value["deliveries"][delivery_id]
            if not isinstance(item, dict):
                raise DeliveryError(
                    "delivery.state-invalid", "delivery state item differs"
                )
            sequence = item.get("enqueue_sequence")
            if sequence is None:
                missing_sequence_ids.append(delivery_id)
            elif (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 1
                or sequence in used_sequences
            ):
                raise DeliveryError(
                    "delivery.state-invalid", "delivery enqueue sequence differs"
                )
            else:
                used_sequences.add(sequence)
        next_sequence = max(used_sequences, default=0) + 1
        for delivery_id in missing_sequence_ids:
            value["deliveries"][delivery_id]["enqueue_sequence"] = next_sequence
            next_sequence += 1
        return value

    def _write_state(self, value: Mapping[str, Any]) -> None:
        temporary = self.state_root / f".delivery-state.{os.getpid()}.{secrets.token_hex(6)}.tmp"
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

    @staticmethod
    def _key(project_instance_id: str, scenario_id: str) -> str:
        return f"{project_instance_id}\0{scenario_id}"

    def apply_policy(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        policy_pack: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        self._check_scenario_fence(
            scenario, scenario_generation, scenario_state_revision
        )
        pack = copy.deepcopy(dict(policy_pack))
        self._validate_policy(pack, scenario_id, participants)
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._previous_request(state, request_id, request_digest)
            if replay is not None:
                return replay
            previous = state["policies"].get(key)
            replacing_default = (
                previous is not None
                and pack["policy_id"] != previous["policy_id"]
                and DEFAULT_POLICY_ID in {pack["policy_id"], previous["policy_id"]}
            )
            if previous is None or replacing_default:
                # A policy line starts at version one: the first policy of a
                # room, a project policy replacing the room-wide default, or
                # the default replacing a project policy.
                if pack["policy_version"] != 1:
                    raise DeliveryError(
                        "policy.version-invalid", "initial policy version must be one"
                    )
                if previous is not None and (
                    pack["scenario_id"] != previous["scenario_id"]
                    or pack["policy_contract_version"] != previous["policy_contract_version"]
                ):
                    raise DeliveryError(
                        "policy.version-invalid", "policy update fence differs"
                    )
            elif (
                pack["policy_id"] != previous["policy_id"]
                or pack["scenario_id"] != previous["scenario_id"]
                or pack["policy_contract_version"]
                != previous["policy_contract_version"]
                or pack["policy_version"] != previous["policy_version"] + 1
            ):
                raise DeliveryError(
                    "policy.version-invalid", "policy update fence differs"
                )
            state["policies"][key] = pack
            result = {"policy": copy.deepcopy(pack), "policy_snapshot": _policy_snapshot(pack)}
            operation_id = f"policy-{uuid.uuid4().hex}"
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "delivery_ids": [],
                "result": result,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    @staticmethod
    def default_policy_pack(
        *,
        scenario_id: str,
        participants: Sequence[Mapping[str, Any]],
        policy_version: int,
    ) -> dict[str, Any]:
        """The room-wide policy for exactly these colleagues and generations."""

        refs = [
            _participant_ref(value)
            for value in sorted(participants, key=lambda value: value["participant_id"])
        ]
        route_rules: list[dict[str, Any]] = []
        for sender_index, sender in enumerate(refs):
            for receiver_index, receiver in enumerate(refs):
                if sender_index == receiver_index:
                    continue
                for kind in OPEN_MESSAGE_KINDS:
                    route_rules.append(
                        {
                            "rule_id": (
                                f"open-{sender_index}-{receiver_index}-"
                                f"{kind.removeprefix('collaboration.')}"
                            ),
                            "sender": {"kind": "participant", "participant": copy.deepcopy(sender)},
                            "receiver": {"kind": "participant", "participant": copy.deepcopy(receiver)},
                            "message_kind": kind,
                            "effect": "allow",
                            "retry_profile_id": OPEN_RETRY_PROFILE["profile_id"],
                        }
                    )
        return {
            "policy_contract_version": 1,
            "policy_id": DEFAULT_POLICY_ID,
            "policy_version": policy_version,
            "scenario_id": scenario_id,
            "default_effect": "deny",
            "assignments": [],
            "retry_profiles": [copy.deepcopy(OPEN_RETRY_PROFILE)],
            "route_rules": route_rules,
        }

    def sync_default_policy(
        self, *, project_instance_id: str, scenario_id: str
    ) -> bool:
        """Keep the room-wide policy current; leave a project policy alone.

        Returns True when a new policy version was applied.
        """

        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            current = copy.deepcopy(self._read_state()["policies"].get(key))
        if current is not None and current["policy_id"] != DEFAULT_POLICY_ID:
            return False
        version = 1 if current is None else current["policy_version"] + 1
        target = self.default_policy_pack(
            scenario_id=scenario_id, participants=participants, policy_version=version
        )
        if current is not None and {**current, "policy_version": 0} == {**target, "policy_version": 0}:
            return False
        request_id = f"policy-sync-{uuid.uuid4().hex}"
        self.apply_policy(
            request_id=request_id,
            request_digest=canonical_json_sha256({"policy_sync": request_id}),
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            policy_pack=target,
        )
        return True

    def policy_upgrade_done(self) -> bool:
        with self._lock:
            return self._read_state().get("policy_upgrade") == 3

    def mark_policy_upgrade_done(self) -> None:
        with self._lock:
            state = self._read_state()
            state["policy_upgrade"] = 3
            state["state_revision"] += 1
            self._write_state(state)

    def reset_to_default_policy(
        self,
        *,
        request_id: str,
        request_digest: str | None,
        project_instance_id: str,
        scenario_id: str,
    ) -> tuple[str, dict[str, Any]]:
        """Replace whatever policy a room has with the room-wide default."""

        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._previous_request(state, request_id, request_digest or "")
            if replay is not None:
                return replay
            current = copy.deepcopy(state["policies"].get(key))
        if current is not None and current["policy_id"] == DEFAULT_POLICY_ID:
            self.sync_default_policy(
                project_instance_id=project_instance_id, scenario_id=scenario_id
            )
            operation_id, shown = self.show_policy(
                project_instance_id=project_instance_id, scenario_id=scenario_id
            )
            return operation_id, {
                "policy": shown["policy"],
                "policy_snapshot": shown["policy_snapshot"],
            }
        target = self.default_policy_pack(
            scenario_id=scenario_id, participants=participants, policy_version=1
        )
        return self.apply_policy(
            request_id=request_id,
            request_digest=request_digest or canonical_json_sha256({"policy_upgrade": request_id}),
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            policy_pack=target,
        )

    def show_policy(
        self, *, project_instance_id: str, scenario_id: str
    ) -> tuple[str, dict[str, Any]]:
        key = self._key(project_instance_id, scenario_id)
        _, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        with self._lock:
            pack = self._read_state()["policies"].get(key)
            if pack is None:
                raise DeliveryError("policy.not-found", "scenario policy is unavailable")
            return f"read-policy-{scenario_id}", {
                "policy": copy.deepcopy(pack),
                "policy_snapshot": _policy_snapshot(pack),
                "policy_health": self._policy_health(pack, participants),
            }

    def participant_collaboration_context(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> dict[str, Any]:
        """Render a provider-neutral, owner-private participant context.

        This snapshot is model guidance only.  Delivery authorization always
        re-evaluates the live policy and exact participant-generation fences.
        """

        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        current = {value["participant_id"]: value for value in participants}
        participant = current.get(participant_id)
        if (
            participant is None
            or participant["participant_generation"] != participant_generation
        ):
            raise DeliveryError(
                "delivery.stale-sender", "participant identity differs", True
            )
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            pack = copy.deepcopy(state["policies"].get(key))
            # The context revision follows the Scenario aggregate, not delivery
            # traffic.  Team/generation changes advance this revision while an
            # unrelated message ACK cannot make a launch snapshot look stale.
            context_revision = scenario["state_revision"]

        assignments_by_participant: dict[
            str, list[dict[str, str | None]]
        ] = {
            value["participant_id"]: [] for value in participants
        }
        if pack is not None:
            for assignment in pack["assignments"]:
                target_id = assignment["participant"]["participant_id"]
                assignments_by_participant.setdefault(target_id, []).append(
                    {
                        "attribute": assignment["attribute"],
                        "task_id": assignment["task_id"],
                    }
                )
        own_ref = _participant_ref(participant)
        allowed_outbound: list[dict[str, str]] = []
        if pack is not None:
            for rule in pack["route_rules"]:
                if rule["effect"] != "allow" or not self._selector_contains(
                    rule["sender"], own_ref, pack
                ):
                    continue
                receiver = rule["receiver"]
                receiver_label = (
                    receiver["participant"]["participant_id"]
                    if receiver["kind"] == "participant"
                    else (
                        receiver["attribute"]
                        if receiver["task_id"] is None
                        else f"{receiver['attribute']}:{receiver['task_id']}"
                    )
                )
                allowed_outbound.append(
                    {
                        "message_kind": rule["message_kind"],
                        "receiver_label": receiver_label,
                    }
                )

        unsigned: dict[str, Any] = {
            "schema_version": 2,
            "context_revision": context_revision,
            "opening": opening_text(scenario.get("playbook", "none")),
            "note": participant.get("note", ""),
            "scenario": {
                "project_instance_id": project_instance_id,
                "scenario_id": scenario_id,
                "scenario_generation": scenario["scenario_generation"],
                "objective": {
                    "revision": len(scenario["objective_history"]),
                    "objective": scenario["objective"],
                    "acceptance_criteria": (
                        scenario["objective_history"][-1]["acceptance_criteria"]
                        if scenario["objective_history"]
                        else ""
                    ),
                },
            },
            "participant": {
                "participant_id": participant_id,
                "participant_generation": participant_generation,
                "assignments": sorted(
                    assignments_by_participant.get(participant_id, []),
                    key=lambda value: (
                        value["attribute"],
                        value["task_id"] or "",
                    ),
                ),
            },
            "peers": [
                {
                    "participant_id": value["participant_id"],
                    "participant_generation": value["participant_generation"],
                    "assignments": sorted(
                        (
                            assignment["attribute"]
                            if assignment["task_id"] is None
                            else f"{assignment['attribute']}:{assignment['task_id']}"
                        )
                        for assignment in assignments_by_participant.get(
                            value["participant_id"], []
                        )
                    ),
                }
                for value in participants
                if value["participant_id"] != participant_id
            ],
            "policy": None if pack is None else _policy_snapshot(pack),
            "allowed_outbound": sorted(
                allowed_outbound,
                key=lambda value: (
                    value["message_kind"],
                    value["receiver_label"],
                ),
            ),
            "reply_semantics": {
                "reply_expected_kinds": [
                    "collaboration.request",
                    "collaboration.question",
                    "collaboration.review-request",
                    "collaboration.pushback",
                ],
                "terminal_kinds": [
                    "collaboration.response",
                    "collaboration.review-response",
                    "collaboration.notice",
                    "collaboration.done",
                ],
                "preserve_reply_to": True,
                "machine_ack_is_silent": True,
            },
        }
        return {**unsigned, "context_digest": canonical_json_sha256(unsigned)}

    def plan_policy(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        template: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Resolve project template identities to one deterministic exact plan."""

        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        self._check_scenario_fence(
            scenario, scenario_generation, scenario_state_revision
        )
        participant_ids = self.validate_template(template, scenario_id)
        current = {value["participant_id"]: value for value in participants}
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            previous = copy.deepcopy(self._read_state()["policies"].get(key))
        policy_version = (
            1
            if previous is None or previous["policy_id"] != template["policy_id"]
            else previous["policy_version"] + 1
        )

        team: list[dict[str, Any]] = []
        blockers: list[str] = []
        refs: dict[str, dict[str, Any]] = {}
        resolved_ids = {
            participant_id: participant_id
            for participant_id in participant_ids
            if participant_id in current
        }
        for participant_id in participant_ids:
            resolved_id = resolved_ids.get(participant_id, participant_id)
            record = current.get(resolved_id)
            if record is None:
                team.append(
                    {
                        "participant_id": participant_id,
                        "participant_generation": None,
                        "present": False,
                    }
                )
                blockers.append(f"team.participant-missing:{participant_id}")
                continue
            ref = _participant_ref(record)
            refs[participant_id] = ref
            team.append(
                {
                    "participant_id": resolved_id,
                    "participant_generation": ref["participant_generation"],
                    "present": True,
                }
            )

        if (
            previous is not None
            and previous["policy_id"] not in {template["policy_id"], DEFAULT_POLICY_ID}
        ):
            blockers.append("policy.template-conflict")

        policy_pack: dict[str, Any] | None = None
        route_effects: list[dict[str, Any]] = []
        if not blockers:
            policy_pack = self._template_policy_pack(
                template,
                scenario_id=scenario_id,
                refs=refs,
                policy_version=policy_version,
            )
            self._validate_policy(policy_pack, scenario_id, participants)
            profiles = {
                value["profile_id"]: value
                for value in policy_pack["retry_profiles"]
            }
            for rule in policy_pack["route_rules"]:
                route_effects.append(
                    {
                        "rule_id": rule["rule_id"],
                        "message_kind": rule["message_kind"],
                        "effect": rule["effect"],
                        "sender_participants": [
                            value["participant_id"]
                            for value in self._resolve_selector(
                                rule["sender"], policy_pack
                            )
                        ],
                        "receiver_participants": [
                            value["participant_id"]
                            for value in self._resolve_selector(
                                rule["receiver"], policy_pack
                            )
                        ],
                        "retry_profile": (
                            None
                            if rule["retry_profile_id"] is None
                            else copy.deepcopy(profiles[rule["retry_profile_id"]])
                        ),
                    }
                )

        template_snapshot = {
            "template_id": template["template_id"],
            "template_digest": canonical_json_sha256(template),
        }
        plan = {
            "plan_schema_version": 1,
            "scenario": {
                "scenario_id": scenario_id,
                "scenario_generation": scenario_generation,
                "scenario_state_revision": scenario_state_revision,
            },
            "template_snapshot": template_snapshot,
            "team": team,
            "policy_pack": policy_pack,
            "route_effects": route_effects,
            "blockers": sorted(blockers),
            "can_apply": not blockers,
        }
        plan["plan_digest"] = canonical_json_sha256(plan)
        return f"read-policy-plan-{scenario_id}", {"policy_plan": plan}

    def apply_policy_plan(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        template: Mapping[str, Any],
        plan_digest: str,
    ) -> tuple[str, dict[str, Any]]:
        replay = self.replay_request(request_id, request_digest)
        if replay is not None:
            return replay
        _, planned = self.plan_policy(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            template=template,
        )
        plan = planned["policy_plan"]
        if plan["plan_digest"] != plan_digest:
            raise DeliveryError(
                "policy.plan-stale", "policy plan differs from current identities", True
            )
        if plan["can_apply"] is not True or plan["policy_pack"] is None:
            raise DeliveryError(
                "policy.plan-blocked", "policy plan has unresolved blockers"
            )
        return self.apply_policy(
            request_id=request_id,
            request_digest=request_digest,
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario_generation,
            scenario_state_revision=scenario_state_revision,
            policy_pack=plan["policy_pack"],
        )

    def replay_request(
        self, request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Replay a durable delivery mutation before mutable plan inputs."""

        with self._lock:
            state = self._read_state()
            return self._previous_request(state, request_id, request_digest)

    def settle_deleted_recipient(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> tuple[str, dict[str, Any]]:
        """Terminalize every non-consumed delivery for one deleted identity."""

        target = {
            "project_instance_id": project_instance_id,
            "scenario_id": scenario_id,
            "participant_id": participant_id,
            "participant_generation": participant_generation,
        }
        receiver = {
            "scenario_id": scenario_id,
            "participant_id": participant_id,
            "participant_generation": participant_generation,
        }
        with self._lock:
            state = self._read_state()
            replay = self._previous_request(state, request_id, request_digest)
            if replay is not None:
                return replay
            settled: list[str] = []
            for delivery_id, item in state["deliveries"].items():
                record = item["record"]
                if (
                    item["project_instance_id"] != project_instance_id
                    or item["scenario_id"] != scenario_id
                    or record["target"]["receiver"] != receiver
                    or record["state"] == "consumed"
                ):
                    continue
                record["state"] = "recipient_deleted"
                record["delivery_degraded_reason"] = "delivery.recipient-deleted"
                settled.append(delivery_id)
            settled.sort()
            evidence = {
                "target": target,
                "settled_delivery_ids": settled,
                "terminal_reason": "delivery.recipient-deleted",
            }
            settlement = {
                **evidence,
                "evidence_digest": canonical_json_sha256(evidence),
            }
            operation_id = f"delivery-settle-{uuid.uuid4().hex}"
            result = {"delivery_settlement": settlement}
            state["requests"][request_id] = {
                "request_digest": request_digest,
                "operation_id": operation_id,
                "delivery_ids": settled,
                "result": result,
            }
            state["state_revision"] += 1
            self._write_state(state)
            return operation_id, result

    def send_message(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario_generation: int,
        scenario_state_revision: int,
        sender_participant_id: str,
        sender_participant_generation: int,
        sender_participant_state_revision: int,
        receiver_intent: Mapping[str, Any],
        message_id: str,
        message_kind: str,
        message: str,
        reply_to_delivery_id: str | None = None,
        thread_root_delivery_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        scenario, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        self._check_scenario_fence(
            scenario, scenario_generation, scenario_state_revision
        )
        participant_map = {value["participant_id"]: value for value in participants}
        sender = participant_map.get(sender_participant_id)
        if (
            sender is None
            or sender["participant_generation"] != sender_participant_generation
            or sender["state_revision"] != sender_participant_state_revision
        ):
            raise DeliveryError("delivery.stale-sender", "sender fence differs", True)
        self._require_ready(sender)
        if not isinstance(message, str) or not message or len(message.encode("utf-8")) > 524_288:
            raise DeliveryError("delivery.payload-invalid", "message payload is invalid")
        if not self._namespaced(message_kind):
            raise DeliveryError("delivery.payload-invalid", "message kind is invalid")
        key = self._key(project_instance_id, scenario_id)
        with self._lock:
            state = self._read_state()
            replay = self._previous_request(state, request_id, request_digest)
            if replay is not None:
                operation_id, result = replay
                delivery_ids = state["requests"][request_id]["delivery_ids"]
            else:
                pack = state["policies"].get(key)
                if pack is None:
                    raise DeliveryError("policy.not-found", "scenario policy is unavailable")
                if self._policy_health(pack, participants)["requires_replan"]:
                    raise DeliveryError(
                        "policy.generation-drift",
                        "policy participant generation differs; replan is required",
                        True,
                    )
                snapshot = _policy_snapshot(pack)
                route_request = {
                    "request_id": f"route-{uuid.uuid4().hex}",
                    "message_id": message_id,
                    "scenario_id": scenario_id,
                    "sender": _participant_ref(sender),
                    "receiver_intent": copy.deepcopy(dict(receiver_intent)),
                    "message_kind": message_kind,
                    "payload_digest": hashlib.sha256(message.encode("utf-8")).hexdigest(),
                    "policy_snapshot": snapshot,
                }
                decision, targets, profile = self._resolve_route(
                    pack, route_request, participants
                )
                if decision["outcome"] != "allow":
                    raise DeliveryError("policy.denied", decision["denial_code"])
                operation_id = f"delivery-operation-{uuid.uuid4().hex}"
                delivery_ids: list[str] = []
                enqueue_sequence = max(
                    (
                        item["enqueue_sequence"]
                        for item in state["deliveries"].values()
                    ),
                    default=0,
                )
                for receiver in targets:
                    delivery_id = f"delivery-{uuid.uuid4().hex}"
                    delivery_ids.append(delivery_id)
                    enqueue_sequence += 1
                    record = self._enqueue_record(
                        delivery_id,
                        route_request,
                        decision,
                        sender,
                        receiver,
                        profile,
                    )
                    state["deliveries"][delivery_id] = {
                        "project_instance_id": project_instance_id,
                        "scenario_id": scenario_id,
                        "enqueue_sequence": enqueue_sequence,
                        "record": record,
                        "message": message,
                        "message_kind": message_kind,
                        "reply_to_delivery_id": reply_to_delivery_id,
                        "thread_root_delivery_id": (
                            thread_root_delivery_id or delivery_id
                        ),
                        "consumption_token": secrets.token_hex(24),
                    }
                result = {
                    "acceptance": {
                        "outcome": "accepted",
                        "durably_enqueued": True,
                        "delivery_ids": copy.deepcopy(delivery_ids),
                    },
                    "route_decision": copy.deepcopy(decision),
                    "deliveries": [
                        copy.deepcopy(state["deliveries"][value]["record"])
                        for value in delivery_ids
                    ],
                }
                state["requests"][request_id] = {
                    "request_digest": request_digest,
                    "operation_id": operation_id,
                    "delivery_ids": delivery_ids,
                    "result": result,
                }
                state["state_revision"] += 1
                self._write_state(state)

        for delivery_id in delivery_ids:
            self._schedule_dispatch(delivery_id)
        return operation_id, copy.deepcopy(result)

    def send_self_message(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario: Mapping[str, Any],
        sender: Mapping[str, Any],
        receiver_participant_id: str,
        message_id: str,
        message_kind: str,
        message: str,
    ) -> tuple[str, dict[str, Any]]:
        _, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        receiver = next(
            (
                value
                for value in participants
                if value["participant_id"] == receiver_participant_id
            ),
            None,
        )
        if receiver is None:
            raise DeliveryError(
                "policy.target-unavailable", "receiver participant is unavailable"
            )
        return self.send_message(
            request_id=request_id,
            request_digest=request_digest,
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            sender_participant_id=sender["participant_id"],
            sender_participant_generation=sender["participant_generation"],
            sender_participant_state_revision=sender["state_revision"],
            receiver_intent={
                "kind": "participant",
                "participant": _participant_ref(receiver),
            },
            message_id=message_id,
            message_kind=message_kind,
            message=message,
        )

    def reply_message(
        self,
        *,
        request_id: str,
        request_digest: str,
        project_instance_id: str,
        scenario_id: str,
        scenario: Mapping[str, Any],
        sender: Mapping[str, Any],
        receiver_participant_id: str,
        reply_to_delivery_id: str,
        message_id: str,
        message_kind: str,
        message: str,
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            original_item = copy.deepcopy(
                self._delivery_item(
                    self._read_state(),
                    project_instance_id,
                    scenario_id,
                    reply_to_delivery_id,
                )
            )
            original = original_item["record"]
        if (
            original["state"] not in {"delivered", "consumed"}
            or original["target"]["receiver"] != _participant_ref(sender)
            or original["target"]["sender"]["participant_id"]
            != receiver_participant_id
        ):
            raise DeliveryError(
                "delivery.reply-denied", "reply delivery binding differs"
            )
        _, participants = self.store.delivery_snapshot(
            project_instance_id, scenario_id
        )
        receiver = next(
            (
                value
                for value in participants
                if _participant_ref(value) == original["target"]["sender"]
            ),
            None,
        )
        if receiver is None:
            raise DeliveryError(
                "delivery.stale-reply-target",
                "reply target generation differs",
                True,
            )
        return self.send_message(
            request_id=request_id,
            request_digest=request_digest,
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            scenario_generation=scenario["scenario_generation"],
            scenario_state_revision=scenario["state_revision"],
            sender_participant_id=sender["participant_id"],
            sender_participant_generation=sender["participant_generation"],
            sender_participant_state_revision=sender["state_revision"],
            receiver_intent={
                "kind": "participant",
                "participant": _participant_ref(receiver),
            },
            message_id=message_id,
            message_kind=message_kind,
            message=message,
            reply_to_delivery_id=reply_to_delivery_id,
            thread_root_delivery_id=original_item.get(
                "thread_root_delivery_id", original["delivery_id"]
            ),
        )

    def status(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            item = self._delivery_item(
                self._read_state(), project_instance_id, scenario_id, delivery_id
            )
            return f"read-{delivery_id}", {"delivery": copy.deepcopy(item["record"])}

    def list_deliveries(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        limit: int,
        after_delivery_id: str | None = None,
        collection_digest: str | None = None,
        thread_root_delivery_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Return one bounded Scenario-scoped owner view without message content."""

        with self._lock:
            state = self._read_state()
            if thread_root_delivery_id is not None:
                root = self._delivery_item(
                    state,
                    project_instance_id,
                    scenario_id,
                    thread_root_delivery_id,
                )
                if (
                    root.get("thread_root_delivery_id", thread_root_delivery_id)
                    != thread_root_delivery_id
                ):
                    raise DeliveryError(
                        "delivery.not-found", "delivery thread root does not exist"
                    )
            projections = [
                self._delivery_projection(
                    item,
                    in_flight=item["record"]["delivery_id"]
                    in self._active_delivery_ids,
                )
                for item in state["deliveries"].values()
                if item["project_instance_id"] == project_instance_id
                and item["scenario_id"] == scenario_id
                and (
                    thread_root_delivery_id is None
                    or item.get(
                        "thread_root_delivery_id", item["record"]["delivery_id"]
                    )
                    == thread_root_delivery_id
                )
            ]
            projections.sort(
                key=lambda value: value["enqueue_sequence"],
                reverse=True,
            )
            digest = canonical_json_sha256(
                {
                    "scenario_id": scenario_id,
                    "thread_root_delivery_id": thread_root_delivery_id,
                    "deliveries": projections,
                }
            )
            if collection_digest is not None and collection_digest != digest:
                raise DeliveryError(
                    "delivery.collection-stale",
                    "delivery collection changed during pagination",
                    True,
                )
            start = 0
            if after_delivery_id is not None:
                cursor_index = next(
                    (
                        index
                        for index, value in enumerate(projections)
                        if value["delivery_id"] == after_delivery_id
                    ),
                    None,
                )
                if cursor_index is None:
                    raise DeliveryError(
                        "delivery.cursor-invalid",
                        "delivery pagination cursor does not exist",
                    )
                start = cursor_index + 1
            page = projections[start : start + limit]
            has_more = start + len(page) < len(projections)
            next_page = None
            if has_more and page:
                next_page = {
                    "after_delivery_id": page[-1]["delivery_id"],
                    "collection_digest": digest,
                }
            states: dict[str, int] = {}
            kinds: dict[str, int] = {"collaboration.message": 0}
            reply_expected_ids: set[str] = set()
            reply_target_ids: set[str] = set()
            delivered_ids: set[str] = set()
            attempted_total = 0
            first_attempt_total = 0
            degraded_total = 0
            for value in projections:
                states[value["state"]] = states.get(value["state"], 0) + 1
                kind = value["message_kind"]
                kinds[kind] = kinds.get(kind, 0) + 1
                if kind in REPLY_EXPECTED_MESSAGE_KINDS:
                    reply_expected_ids.add(value["delivery_id"])
                if value["reply_to_delivery_id"] is not None:
                    reply_target_ids.add(value["reply_to_delivery_id"])
                if value["state"] == "delivered":
                    delivered_ids.add(value["delivery_id"])
                last_event = value["last_event"]
                if last_event is not None:
                    attempted_total += 1
                    if last_event["attempt_number"] == 1:
                        first_attempt_total += 1
                if value["degraded_reason"] is not None:
                    degraded_total += 1
            result = {
                "scenario_id": scenario_id,
                "collection_revision": state["state_revision"],
                "collection_digest": digest,
                "thread_root_delivery_id": thread_root_delivery_id,
                "deliveries": copy.deepcopy(page),
                "next_page": next_page,
                "summary": {
                    "total": len(projections),
                    "states": dict(sorted(states.items())),
                    "kinds": dict(sorted(kinds.items())),
                    "reply_expected_total": len(reply_expected_ids),
                    "reply_expected_closed": len(
                        reply_expected_ids & reply_target_ids
                    ),
                    "delivered_with_reply": len(delivered_ids & reply_target_ids),
                    "attempted_total": attempted_total,
                    "first_attempt_total": first_attempt_total,
                    "degraded_total": degraded_total,
                },
            }
            return f"read-deliveries-{scenario_id}", {
                "delivery_collection": result
            }

    def consume(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
        event_sequence: int,
        consumption_ack: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            item = self._delivery_item(state, project_instance_id, scenario_id, delivery_id)
            record = item["record"]
            if len(record["events"]) != event_sequence:
                raise DeliveryError("delivery.stale-fence", "delivery event fence differs", True)
            if record["delivery_degraded_reason"] is not None:
                raise DeliveryError(
                    "delivery.consume-ineligible",
                    record["delivery_degraded_reason"],
                )
            self._accept_consumption(record, dict(consumption_ack))
            state["state_revision"] += 1
            self._write_state(state)
            return f"consume-{delivery_id}", {"delivery": copy.deepcopy(record)}

    def retry(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
        event_sequence: int,
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            item = self._delivery_item(
                self._read_state(), project_instance_id, scenario_id, delivery_id
            )
            if len(item["record"]["events"]) != event_sequence:
                raise DeliveryError("delivery.stale-fence", "delivery event fence differs", True)
            eligibility = self._retry_eligibility(
                item["record"],
                in_flight=delivery_id in self._active_delivery_ids,
            )
            if eligibility["eligible"] is not True:
                raise DeliveryError(
                    "delivery.retry-ineligible",
                    eligibility["reason"],
                )
        if not self._schedule_dispatch(delivery_id):
            with self._lock:
                enabled = self._supervision_enabled
            if not enabled:
                self._dispatch(delivery_id)
        return self.status(
            project_instance_id=project_instance_id,
            scenario_id=scenario_id,
            delivery_id=delivery_id,
        )

    def resumable_delivery_ids(self) -> list[str]:
        with self._lock:
            state = self._read_state()
            return sorted(
                delivery_id
                for delivery_id, item in state["deliveries"].items()
                if item["record"]["state"]
                in {"queued", "delivery_attempted", "delivered"}
                and item["record"]["delivery_degraded_reason"] is None
            )

    def start_supervision(self) -> dict[str, int]:
        """Enable Host-owned asynchronous delivery and resume durable work."""

        with self._lock:
            self._supervision_enabled = True
        return self.run_supervision_once()

    def stop_supervision(self) -> None:
        """Stop accepting new local workers; durable state remains restartable."""

        with self._lock:
            self._supervision_enabled = False

    def run_supervision_once(self) -> dict[str, int]:
        """Schedule every durable non-terminal delivery not already in flight."""

        delivery_ids = self.resumable_delivery_ids()
        scheduled = sum(self._schedule_dispatch(value) for value in delivery_ids)
        return {"observed": len(delivery_ids), "scheduled": scheduled}

    def _schedule_dispatch(self, delivery_id: str) -> bool:
        with self._lock:
            if not self._supervision_enabled or delivery_id in self._active_delivery_ids:
                return False
            state = self._read_state()
            item = state["deliveries"].get(delivery_id)
            if (
                item is None
                or item["record"]["state"] == "consumed"
                or item["record"]["delivery_degraded_reason"] is not None
            ):
                return False
            self._active_delivery_ids.add(delivery_id)
            thread = threading.Thread(
                target=self._dispatch_reserved,
                args=(delivery_id,),
                name=f"ai-collab-delivery-{delivery_id}",
                daemon=True,
            )
            self._dispatch_threads[delivery_id] = thread
        thread.start()
        return True

    def _dispatch_reserved(self, delivery_id: str) -> None:
        try:
            self._dispatch_active(delivery_id)
        finally:
            with self._lock:
                self._active_delivery_ids.discard(delivery_id)
                self._dispatch_threads.pop(delivery_id, None)

    def _dispatch(self, delivery_id: str) -> None:
        with self._lock:
            if delivery_id in self._active_delivery_ids:
                raise DeliveryError(
                    "delivery.retry-ineligible",
                    "delivery.retry-in-flight",
                )
            self._active_delivery_ids.add(delivery_id)
        try:
            self._dispatch_active(delivery_id)
        finally:
            with self._lock:
                self._active_delivery_ids.discard(delivery_id)

    def _dispatch_active(self, delivery_id: str) -> None:
        while True:
            with self._lock:
                state = self._read_state()
                item = state["deliveries"][delivery_id]
                record = item["record"]
                reply_to_delivery_id = item.get("reply_to_delivery_id")
                if (
                    record["state"] == "consumed"
                    or record["delivery_degraded_reason"] is not None
                ):
                    return
                if record["state"] == "delivered":
                    delivered_record = copy.deepcopy(record)
                    delivered_event = record["events"][-1]
                    ack_digest = delivered_event["evidence_digest"]
                    token = item["consumption_token"]
                    project_instance_id = item["project_instance_id"]
                    scenario_id = item["scenario_id"]
                else:
                    profile = record["retry_profile"]["profile"]
                    events = record["events"]
                    starts = [value for value in events if value["event"] == "attempt_started"]
                    if events and events[-1]["event"] == "attempt_started":
                        active = events[-1]
                        self._append_event(
                            record,
                            event="attempt_failed",
                            attempt_number=active["attempt_number"],
                            backoff_ms=active["backoff_ms"],
                            transport_attempt_id=active["transport_attempt_id"],
                            evidence_digest=None,
                            error_code="delivery.unknown-outcome",
                        )
                    if len(starts) >= profile["max_attempts"]:
                        record["delivery_degraded_reason"] = "delivery.retry-exhausted"
                        state["state_revision"] += 1
                        self._write_state(state)
                        return
                    number = len(starts) + 1
                    backoff_ms = profile["backoff_ms"][number - 1]
                    transport_attempt_id = f"attempt-{uuid.uuid4().hex}"
                    self._append_event(
                        record,
                        event="attempt_started",
                        attempt_number=number,
                        backoff_ms=backoff_ms,
                        transport_attempt_id=transport_attempt_id,
                        evidence_digest=None,
                        error_code=None,
                    )
                    state["state_revision"] += 1
                    self._write_state(state)
                    public_record = copy.deepcopy(record)
                    message = item["message"]
                    message_kind = item.get(
                        "message_kind", "collaboration.request"
                    )
                    token = item["consumption_token"]
                    project_instance_id = item["project_instance_id"]
                    scenario_id = item["scenario_id"]
            if record["state"] == "delivered":
                self._finish_consumption(
                    delivery_id=delivery_id,
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    delivered_record=delivered_record,
                    consumption_token=token,
                    delivery_ack_digest=ack_digest,
                    consumption=None,
                )
                return
            if backoff_ms:
                time.sleep(backoff_ms / 1000)
            try:
                response = self.participants.deliver(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    delivery_record=public_record,
                    message=message,
                    message_kind=message_kind,
                    reply_to_delivery_id=reply_to_delivery_id,
                    consumption_token=token,
                )
                with self._lock:
                    state = self._read_state()
                    record = state["deliveries"][delivery_id]["record"]
                    if record["delivery_degraded_reason"] is not None:
                        return
                    ack_digest = self._accept_delivery(record, response["delivery_ack"])
                    consumption = response["consumption_ack"]
                    state["state_revision"] += 1
                    self._write_state(state)
                    delivered_record = copy.deepcopy(record)
                self._finish_consumption(
                    delivery_id=delivery_id,
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    delivered_record=delivered_record,
                    consumption_token=token,
                    delivery_ack_digest=ack_digest,
                    consumption=consumption,
                )
                return
            except (DeliveryError, ParticipantError, StoreError, OSError, KeyError, TypeError):
                with self._lock:
                    state = self._read_state()
                    record = state["deliveries"][delivery_id]["record"]
                    if record["delivery_degraded_reason"] is not None:
                        return
                    active = record["events"][-1]
                    if active["event"] == "attempt_started":
                        self._append_event(
                            record,
                            event="attempt_failed",
                            attempt_number=active["attempt_number"],
                            backoff_ms=active["backoff_ms"],
                            transport_attempt_id=active["transport_attempt_id"],
                            evidence_digest=None,
                            error_code="delivery.transport-failed",
                        )
                    state["state_revision"] += 1
                    self._write_state(state)

    def _finish_consumption(
        self,
        *,
        delivery_id: str,
        project_instance_id: str,
        scenario_id: str,
        delivered_record: Mapping[str, Any],
        consumption_token: str,
        delivery_ack_digest: str,
        consumption: Mapping[str, Any] | None,
    ) -> None:
        if consumption is None:
            try:
                consumption = self.participants.await_consumption(
                    project_instance_id=project_instance_id,
                    scenario_id=scenario_id,
                    delivery_record=delivered_record,
                    consumption_token=consumption_token,
                )
            except (ParticipantError, StoreError, OSError, KeyError, TypeError):
                return
        if consumption.get("delivery_ack_digest") != delivery_ack_digest:
            return
        with self._lock:
            state = self._read_state()
            record = state["deliveries"][delivery_id]["record"]
            if record["delivery_degraded_reason"] is not None:
                return
            try:
                self._accept_consumption(record, dict(consumption))
            except DeliveryError:
                return
            state["state_revision"] += 1
            self._write_state(state)

    @staticmethod
    def _enqueue_record(
        delivery_id: str,
        request: Mapping[str, Any],
        decision: Mapping[str, Any],
        sender: Mapping[str, Any],
        receiver: Mapping[str, Any],
        profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "delivery_id": delivery_id,
            "message_id": request["message_id"],
            "route_request_digest": canonical_json_sha256(request),
            "route_decision_digest": canonical_json_sha256(decision),
            "policy_snapshot": copy.deepcopy(request["policy_snapshot"]),
            "target": {
                "sender": _participant_ref(sender),
                "receiver": _participant_ref(receiver),
                "interaction_mode": receiver["interaction_mode"],
                "runtime_binding_id": receiver["runtime_binding_id"],
                "presentation_binding_id": receiver["presentation_binding_id"],
            },
            "payload_digest": request["payload_digest"],
            "retry_profile": {
                "profile": copy.deepcopy(dict(profile)),
                "profile_digest": canonical_json_sha256(profile),
            },
            "state": "queued",
            "events": [],
            "delivery_degraded_reason": None,
        }

    @classmethod
    def _delivery_projection(
        cls, item: Mapping[str, Any], *, in_flight: bool
    ) -> dict[str, Any]:
        record = item["record"]
        events = record["events"]
        last_event = None
        if events:
            event = events[-1]
            last_event = {
                "sequence": event["sequence"],
                "event": event["event"],
                "attempt_number": event["attempt_number"],
                "error_code": event["error_code"],
            }
        return {
            "delivery_id": record["delivery_id"],
            "enqueue_sequence": item["enqueue_sequence"],
            "message_kind": item.get("message_kind", "collaboration.request"),
            "sender": cls._redacted_participant_ref(record["target"]["sender"]),
            "receiver": cls._redacted_participant_ref(record["target"]["receiver"]),
            "policy_snapshot": copy.deepcopy(record["policy_snapshot"]),
            "thread_root_delivery_id": item.get(
                "thread_root_delivery_id", record["delivery_id"]
            ),
            "reply_to_delivery_id": item.get("reply_to_delivery_id"),
            "state": record["state"],
            "degraded_reason": record["delivery_degraded_reason"],
            "event_sequence": len(events),
            "last_event": last_event,
            "retry_eligibility": cls._retry_eligibility(
                record, in_flight=in_flight
            ),
        }

    @staticmethod
    def _redacted_participant_ref(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "participant_id": value["participant_id"],
            "participant_generation": value["participant_generation"],
        }

    @staticmethod
    def _retry_eligibility(
        record: Mapping[str, Any], *, in_flight: bool = False
    ) -> dict[str, Any]:
        events = record["events"]
        event_sequence = len(events)
        attempts = sum(value["event"] == "attempt_started" for value in events)
        max_attempts = record["retry_profile"]["profile"]["max_attempts"]
        if record["state"] == "consumed":
            eligible = False
            reason = "delivery.retry-terminal"
        elif record["state"] == "delivered":
            eligible = not in_flight
            reason = (
                "delivery.consumption-awaiting"
                if eligible
                else "delivery.retry-in-flight"
            )
        elif record["delivery_degraded_reason"] is not None:
            eligible = False
            reason = record["delivery_degraded_reason"]
        elif in_flight:
            eligible = False
            reason = "delivery.retry-in-flight"
        elif attempts >= max_attempts:
            eligible = False
            reason = "delivery.retry-exhausted"
        else:
            eligible = True
            reason = "delivery.retry-ready"
        return {
            "eligible": eligible,
            "event_sequence": event_sequence,
            "reason": reason,
        }

    @classmethod
    def _resolve_route(
        cls,
        pack: Mapping[str, Any],
        request: Mapping[str, Any],
        participants: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], list[Mapping[str, Any]], Mapping[str, Any]]:
        current = {value["participant_id"]: value for value in participants}
        sender_ref = request["sender"]
        sender = current.get(sender_ref["participant_id"])
        if sender is None or _participant_ref(sender) != sender_ref:
            raise DeliveryError("delivery.stale-sender", "sender identity differs", True)
        matched: Mapping[str, Any] | None = None
        for rule in pack["route_rules"]:
            if (
                rule["message_kind"] == request["message_kind"]
                and rule["receiver"] == request["receiver_intent"]
                and cls._selector_contains(rule["sender"], sender_ref, pack)
            ):
                matched = rule
                break
        decision: dict[str, Any] = {
            "request_id": request["request_id"],
            "request_digest": canonical_json_sha256(request),
            "policy_snapshot": copy.deepcopy(request["policy_snapshot"]),
            "outcome": "deny",
            "matched_rule_id": None,
            "target_participants": [],
            "retry_profile_id": None,
            "denial_code": "policy.no-matching-rule",
        }
        if matched is None:
            return decision, [], {}
        decision["matched_rule_id"] = matched["rule_id"]
        if matched["effect"] == "deny":
            decision["denial_code"] = "policy.rule-denied"
            return decision, [], {}
        refs = cls._resolve_selector(request["receiver_intent"], pack)
        targets: list[Mapping[str, Any]] = []
        for ref in refs:
            value = current.get(ref["participant_id"])
            if value is None or _participant_ref(value) != ref:
                decision["denial_code"] = "policy.target-unavailable"
                return decision, [], {}
            try:
                cls._require_ready(value)
            except DeliveryError:
                decision["denial_code"] = "policy.target-unavailable"
                return decision, [], {}
            targets.append(value)
        profile = next(
            value
            for value in pack["retry_profiles"]
            if value["profile_id"] == matched["retry_profile_id"]
        )
        decision.update(
            {
                "outcome": "allow",
                "target_participants": [_participant_ref(value) for value in targets],
                "retry_profile_id": profile["profile_id"],
                "denial_code": None,
            }
        )
        return decision, targets, profile

    @staticmethod
    def _resolve_selector(
        selector: Mapping[str, Any], pack: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if selector["kind"] == "participant":
            return [copy.deepcopy(selector["participant"])]
        return [
            copy.deepcopy(value["participant"])
            for value in pack["assignments"]
            if value["attribute"] == selector["attribute"]
            and value["task_id"] == selector["task_id"]
        ]

    @classmethod
    def _selector_contains(
        cls,
        selector: Mapping[str, Any],
        participant: Mapping[str, Any],
        pack: Mapping[str, Any],
    ) -> bool:
        return participant in cls._resolve_selector(selector, pack)

    @classmethod
    def _validate_policy(
        cls,
        pack: Mapping[str, Any],
        scenario_id: str,
        participants: Sequence[Mapping[str, Any]],
    ) -> None:
        fields = {
            "policy_contract_version",
            "policy_id",
            "policy_version",
            "scenario_id",
            "default_effect",
            "assignments",
            "retry_profiles",
            "route_rules",
        }
        if (
            set(pack) != fields
            or pack["policy_contract_version"] != 1
            or not cls._namespaced(pack["policy_id"])
            or not isinstance(pack["policy_version"], int)
            or isinstance(pack["policy_version"], bool)
            or pack["policy_version"] < 1
            or pack["scenario_id"] != scenario_id
            or pack["default_effect"] != "deny"
            or not all(isinstance(pack[field], list) for field in ("assignments", "retry_profiles", "route_rules"))
        ):
            raise DeliveryError("policy.invalid", "policy pack is invalid")
        current_refs = {_participant_ref(value)["participant_id"]: _participant_ref(value) for value in participants}
        assignment_ids: set[str] = set()
        assignment_keys: set[str] = set()
        for assignment in pack["assignments"]:
            if not isinstance(assignment, dict) or set(assignment) != {
                "assignment_id", "attribute", "task_id", "participant"
            }:
                raise DeliveryError("policy.invalid", "policy assignment is invalid")
            cls._validate_participant_ref(assignment["participant"], scenario_id)
            if current_refs.get(assignment["participant"]["participant_id"]) != assignment["participant"]:
                raise DeliveryError("policy.invalid", "policy assignment is stale")
            key = canonical_json_sha256(
                {
                    "attribute": assignment["attribute"],
                    "task_id": assignment["task_id"],
                    "participant": assignment["participant"],
                }
            )
            if (
                not cls._opaque(assignment["assignment_id"])
                or assignment["assignment_id"] in assignment_ids
                or not cls._namespaced(assignment["attribute"])
                or (
                    assignment["task_id"] is not None
                    and not cls._opaque(assignment["task_id"])
                )
                or key in assignment_keys
            ):
                raise DeliveryError("policy.invalid", "policy assignment differs")
            assignment_ids.add(assignment["assignment_id"])
            assignment_keys.add(key)
        profiles: dict[str, Mapping[str, Any]] = {}
        for profile in pack["retry_profiles"]:
            if not isinstance(profile, dict) or set(profile) != {"profile_id", "max_attempts", "backoff_ms"}:
                raise DeliveryError("policy.invalid", "retry profile is invalid")
            schedule = profile["backoff_ms"]
            if (
                not cls._opaque(profile["profile_id"])
                or profile["profile_id"] in profiles
                or not isinstance(profile["max_attempts"], int)
                or isinstance(profile["max_attempts"], bool)
                or profile["max_attempts"] < 1
                or not isinstance(schedule, list)
                or len(schedule) != profile["max_attempts"]
                or not schedule
                or schedule[0] != 0
                or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in schedule)
                or any(later < earlier for earlier, later in zip(schedule, schedule[1:]))
            ):
                raise DeliveryError("policy.invalid", "retry profile differs")
            profiles[profile["profile_id"]] = profile
        rule_ids: set[str] = set()
        matchers: set[str] = set()
        for rule in pack["route_rules"]:
            if not isinstance(rule, dict) or set(rule) != {
                "rule_id", "sender", "receiver", "message_kind", "effect", "retry_profile_id"
            }:
                raise DeliveryError("policy.invalid", "route rule is invalid")
            cls._validate_selector(rule["sender"], scenario_id, pack)
            cls._validate_selector(rule["receiver"], scenario_id, pack)
            matcher = canonical_json_sha256(
                {"sender": rule["sender"], "receiver": rule["receiver"], "message_kind": rule["message_kind"]}
            )
            if (
                not cls._opaque(rule["rule_id"])
                or rule["rule_id"] in rule_ids
                or matcher in matchers
                or not cls._namespaced(rule["message_kind"])
                or rule["effect"] not in {"allow", "deny"}
                or (rule["effect"] == "allow" and rule["retry_profile_id"] not in profiles)
                or (rule["effect"] == "deny" and rule["retry_profile_id"] is not None)
            ):
                raise DeliveryError("policy.invalid", "route rule differs")
            rule_ids.add(rule["rule_id"])
            matchers.add(matcher)

    @classmethod
    def validate_template(
        cls, template: Mapping[str, Any], scenario_id: str
    ) -> list[str]:
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
            not isinstance(template, Mapping)
            or set(template) != fields
            or template["template_contract_version"] != 1
            or not cls._namespaced(template["template_id"])
            or not isinstance(template["display_name"], str)
            or not template["display_name"].strip()
            or not cls._namespaced(template["policy_id"])
            or not isinstance(template["participant_ids"], list)
            or not template["participant_ids"]
            or any(not cls._opaque(value) for value in template["participant_ids"])
            or len(set(template["participant_ids"]))
            != len(template["participant_ids"])
            or any(
                not isinstance(template[field], list)
                for field in ("assignments", "retry_profiles", "route_rules")
            )
        ):
            raise DeliveryError("policy.template-invalid", "policy template differs")
        participant_ids = list(template["participant_ids"])
        fake_refs = {
            participant_id: {
                "scenario_id": scenario_id,
                "participant_id": participant_id,
                "participant_generation": 1,
            }
            for participant_id in participant_ids
        }
        fake_participants = list(fake_refs.values())
        fake_pack = cls._template_policy_pack(
            template,
            scenario_id=scenario_id,
            refs=fake_refs,
            policy_version=1,
        )
        cls._validate_policy(fake_pack, scenario_id, fake_participants)
        referenced_ids = {
            value["participant"]["participant_id"]
            for value in fake_pack["assignments"]
        }
        for rule in fake_pack["route_rules"]:
            for selector in (rule["sender"], rule["receiver"]):
                if selector["kind"] == "participant":
                    referenced_ids.add(selector["participant"]["participant_id"])
        if referenced_ids != set(participant_ids):
            raise DeliveryError(
                "policy.template-invalid",
                "every declared team participant must be represented by policy",
            )
        return participant_ids

    @classmethod
    def _template_policy_pack(
        cls,
        template: Mapping[str, Any],
        *,
        scenario_id: str,
        refs: Mapping[str, Mapping[str, Any]],
        policy_version: int,
    ) -> dict[str, Any]:
        assignments: list[dict[str, Any]] = []
        for value in template["assignments"]:
            if (
                not isinstance(value, Mapping)
                or set(value)
                != {"assignment_id", "attribute", "task_id", "participant_id"}
                or value["participant_id"] not in refs
            ):
                raise DeliveryError(
                    "policy.template-invalid", "policy template assignment differs"
                )
            assignments.append(
                {
                    "assignment_id": value["assignment_id"],
                    "attribute": value["attribute"],
                    "task_id": value["task_id"],
                    "participant": copy.deepcopy(refs[value["participant_id"]]),
                }
            )
        route_rules: list[dict[str, Any]] = []
        for value in template["route_rules"]:
            if not isinstance(value, Mapping) or set(value) != {
                "rule_id",
                "sender",
                "receiver",
                "message_kind",
                "effect",
                "retry_profile_id",
            }:
                raise DeliveryError(
                    "policy.template-invalid", "policy template route differs"
                )
            route_rules.append(
                {
                    **{
                        key: copy.deepcopy(value[key])
                        for key in (
                            "rule_id",
                            "message_kind",
                            "effect",
                            "retry_profile_id",
                        )
                    },
                    "sender": cls._template_selector(value["sender"], refs),
                    "receiver": cls._template_selector(value["receiver"], refs),
                }
            )
        return {
            "policy_contract_version": 1,
            "policy_id": template["policy_id"],
            "policy_version": policy_version,
            "scenario_id": scenario_id,
            "default_effect": "deny",
            "assignments": assignments,
            "retry_profiles": copy.deepcopy(template["retry_profiles"]),
            "route_rules": route_rules,
        }

    @classmethod
    def _template_selector(
        cls, value: Any, refs: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise DeliveryError("policy.template-invalid", "policy selector differs")
        if value.get("kind") == "participant":
            if set(value) != {"kind", "participant_id"} or value["participant_id"] not in refs:
                raise DeliveryError(
                    "policy.template-invalid", "policy participant selector differs"
                )
            return {
                "kind": "participant",
                "participant": copy.deepcopy(refs[value["participant_id"]]),
            }
        if value.get("kind") == "assignment" and set(value) == {
            "kind",
            "attribute",
            "task_id",
        }:
            return copy.deepcopy(dict(value))
        raise DeliveryError("policy.template-invalid", "policy selector differs")

    @classmethod
    def _policy_health(
        cls,
        pack: Mapping[str, Any],
        participants: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        refs: dict[tuple[str, int], dict[str, Any]] = {}
        for assignment in pack["assignments"]:
            ref = assignment["participant"]
            refs[(ref["participant_id"], ref["participant_generation"])] = ref
        for rule in pack["route_rules"]:
            for selector in (rule["sender"], rule["receiver"]):
                if selector["kind"] == "participant":
                    ref = selector["participant"]
                    refs[(ref["participant_id"], ref["participant_generation"])] = ref
        current = {value["participant_id"]: value for value in participants}
        drift: list[dict[str, Any]] = []
        for ref in sorted(
            refs.values(),
            key=lambda value: (
                value["participant_id"], value["participant_generation"]
            ),
        ):
            record = current.get(ref["participant_id"])
            if record is None or _participant_ref(record) != ref:
                drift.append(
                    {
                        "participant_id": ref["participant_id"],
                        "policy_generation": ref["participant_generation"],
                        "current_generation": (
                            None
                            if record is None
                            else record["participant_generation"]
                        ),
                    }
                )
        return {
            "requires_replan": bool(drift),
            "generation_drift": drift,
        }

    @classmethod
    def _validate_selector(
        cls, selector: Any, scenario_id: str, pack: Mapping[str, Any]
    ) -> None:
        if not isinstance(selector, dict) or selector.get("kind") not in {"participant", "assignment"}:
            raise DeliveryError("policy.invalid", "route selector is invalid")
        if selector["kind"] == "participant":
            if set(selector) != {"kind", "participant"}:
                raise DeliveryError("policy.invalid", "participant selector differs")
            cls._validate_participant_ref(selector["participant"], scenario_id)
            return
        if (
            set(selector) != {"kind", "attribute", "task_id"}
            or not cls._namespaced(selector["attribute"])
            or not any(
                value["attribute"] == selector["attribute"]
                and value["task_id"] == selector["task_id"]
                for value in pack["assignments"]
            )
        ):
            raise DeliveryError("policy.invalid", "assignment selector differs")

    @staticmethod
    def _validate_participant_ref(value: Any, scenario_id: str) -> None:
        if (
            not isinstance(value, dict)
            or set(value) != {"scenario_id", "participant_id", "participant_generation"}
            or value["scenario_id"] != scenario_id
            or not DeliveryCoordinator._opaque(value["scenario_id"])
            or not DeliveryCoordinator._opaque(value["participant_id"])
            or not isinstance(value["participant_generation"], int)
            or isinstance(value["participant_generation"], bool)
            or value["participant_generation"] < 1
        ):
            raise DeliveryError("policy.invalid", "participant reference is invalid")

    @staticmethod
    def _require_ready(record: Mapping[str, Any]) -> None:
        if (
            record["desired_state"] != "running"
            or record["observed_state"] != "ready"
            or record["runtime_binding_id"] is None
            or (
                record["interaction_mode"] == "tui"
                and record["presentation_binding_id"] is None
            )
            or (
                record["interaction_mode"] == "headless"
                and record["presentation_binding_id"] is not None
            )
        ):
            raise DeliveryError("policy.target-unavailable", "participant is not ready")

    @staticmethod
    def _namespaced(value: Any) -> bool:
        return isinstance(value, str) and NAMESPACED_RE.fullmatch(value) is not None

    @staticmethod
    def _opaque(value: Any) -> bool:
        return isinstance(value, str) and OPAQUE_RE.fullmatch(value) is not None

    @staticmethod
    def _check_scenario_fence(
        scenario: Mapping[str, Any], generation: int, revision: int
    ) -> None:
        if (
            scenario["scenario_generation"] != generation
            or scenario["state_revision"] != revision
        ):
            raise DeliveryError("delivery.stale-fence", "Scenario fence differs", True)
        if scenario["observed_state"] not in {"running", "degraded"}:
            raise DeliveryError("delivery.inactive-scenario", "Scenario is not active")

    @staticmethod
    def _previous_request(
        state: Mapping[str, Any], request_id: str, request_digest: str
    ) -> tuple[str, dict[str, Any]] | None:
        previous = state["requests"].get(request_id)
        if previous is None:
            return None
        if previous["request_digest"] != request_digest:
            raise DeliveryError("delivery.request-reused", "request identity was reused")
        return previous["operation_id"], copy.deepcopy(previous["result"])

    @staticmethod
    def _delivery_item(
        state: Mapping[str, Any],
        project_instance_id: str,
        scenario_id: str,
        delivery_id: str,
    ) -> dict[str, Any]:
        item = state["deliveries"].get(delivery_id)
        if (
            item is None
            or item["project_instance_id"] != project_instance_id
            or item["scenario_id"] != scenario_id
        ):
            raise DeliveryError("delivery.not-found", "delivery does not exist")
        return item

    @staticmethod
    def _append_event(
        record: dict[str, Any],
        *,
        event: str,
        attempt_number: int,
        backoff_ms: int,
        transport_attempt_id: str,
        evidence_digest: str | None,
        error_code: str | None,
    ) -> None:
        record["events"].append(
            {
                "sequence": len(record["events"]) + 1,
                "event": event,
                "attempt_number": attempt_number,
                "backoff_ms": backoff_ms,
                "transport_attempt_id": transport_attempt_id,
                "evidence_digest": evidence_digest,
                "error_code": error_code,
            }
        )
        if event in {"attempt_started", "attempt_failed"}:
            record["state"] = "delivery_attempted"
        elif event == "ack_accepted":
            record["state"] = "delivered"
        elif event == "consumed":
            record["state"] = "consumed"

    @classmethod
    def _accept_delivery(cls, record: dict[str, Any], ack: Mapping[str, Any]) -> str:
        active = record["events"][-1]
        expected = {
            "ack_kind": "delivered",
            "delivery_id": record["delivery_id"],
            "message_id": record["message_id"],
            "target": record["target"],
            "payload_digest": record["payload_digest"],
            "attempt_number": active["attempt_number"],
            "transport_attempt_id": active["transport_attempt_id"],
        }
        if active["event"] != "attempt_started" or ack != expected:
            raise DeliveryError("delivery.ack-mismatch", "delivery ACK differs")
        digest = canonical_json_sha256(ack)
        cls._append_event(
            record,
            event="ack_accepted",
            attempt_number=active["attempt_number"],
            backoff_ms=active["backoff_ms"],
            transport_attempt_id=active["transport_attempt_id"],
            evidence_digest=digest,
            error_code=None,
        )
        return digest

    @classmethod
    def _accept_consumption(
        cls, record: dict[str, Any], ack: Mapping[str, Any]
    ) -> None:
        delivered = record["events"][-1]
        expected = {
            "ack_kind": "consumed",
            "delivery_id": record["delivery_id"],
            "message_id": record["message_id"],
            "target": record["target"],
            "payload_digest": record["payload_digest"],
            "attempt_number": delivered["attempt_number"],
            "transport_attempt_id": delivered["transport_attempt_id"],
            "delivery_ack_digest": delivered["evidence_digest"],
        }
        if delivered["event"] != "ack_accepted" or ack != expected:
            raise DeliveryError("delivery.ack-mismatch", "consumption ACK differs")
        cls._append_event(
            record,
            event="consumed",
            attempt_number=delivered["attempt_number"],
            backoff_ms=delivered["backoff_ms"],
            transport_attempt_id=delivered["transport_attempt_id"],
            evidence_digest=canonical_json_sha256(ack),
            error_code=None,
        )
