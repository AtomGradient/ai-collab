# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import json
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest

from ai_collab.client import HarnessClient, HarnessClientError
from ai_collab.host import HarnessHost
from ai_collab.project import ProjectError


PROJECT_DIGEST = "a" * 64


class FakeProjectAdapter:
    def call(
        self,
        operation: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        del timeout_seconds
        if operation == "collaboration_templates":
            assert payload == {}
            return {"templates": [_collaboration_template()]}
        if operation == "bootstrap":
            assert Path(payload["canonical_project_path"]).is_dir()
            return {
                "bootstrap": {
                    "created": ["project_descriptor.yaml", "repo_manifest.yaml"],
                    "already_configured": False,
                    "project_key": "test-project",
                }
            }
        assert operation == "register"
        assert Path(payload["canonical_project_path"]).is_dir()
        return {
            "project": {
                "project_key": "test-project",
                "project_binding_digest": PROJECT_DIGEST,
                "product_contract_version": "1.0",
                "workspace_adapter_id": "workspace.test-v1",
                "environment_adapter_id": "environment.test-v1",
                "participant_driver_contract": 2,
                "collaboration_policy_schema": 1,
                "repo_manifest_digest": "b" * 64,
                "adapter_capability_digest": "c" * 64,
            }
        }


def _collaboration_template() -> dict[str, Any]:
    return {
        "template_contract_version": 1,
        "template_id": "team.peer-review",
        "display_name": "Peer review",
        "policy_id": "policy.peer-review",
        "participant_ids": ["analyst", "reviewer"],
        "assignments": [],
        "retry_profiles": [
            {"profile_id": "interactive", "max_attempts": 1, "backoff_ms": [0]}
        ],
        "route_rules": [
            {
                "rule_id": "review",
                "sender": {"kind": "participant", "participant_id": "analyst"},
                "receiver": {"kind": "participant", "participant_id": "reviewer"},
                "message_kind": "collaboration.request",
                "effect": "allow",
                "retry_profile_id": "interactive",
            }
        ],
    }


@contextmanager
def running_host(
    state_root: Path,
) -> Iterator[tuple[HarnessHost, HarnessClient]]:
    with tempfile.TemporaryDirectory(prefix="harness-project-") as runtime:
        socket_path = Path(runtime) / "host.sock"
        host = HarnessHost(state_root, socket_path)
        host.projects.adapter = FakeProjectAdapter()  # type: ignore[assignment]
        errors: list[BaseException] = []

        def run() -> None:
            try:
                host.serve_forever()
            except BaseException as exc:  # pragma: no cover - fixture surfaces it
                errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3
        while (
            (not socket_path.exists() or host.host_generation == 0)
            and not errors
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if errors:
            raise errors[0]
        try:
            yield host, HarnessClient(state_root, socket_path)
        finally:
            host.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert not errors


def test_register_list_binding_and_restart_keep_root_private(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        registered = client.register_project(
            canonical_project_path=str(project_root), request_id="register-project"
        )["project"]
        project_instance_id = registered["project_instance_id"]
        assert registered["project_key"] == "test-project"
        assert registered["project_binding_digest"] == PROJECT_DIGEST
        assert "canonical_root" not in registered
        assert "canonical_root_fingerprint" not in registered
        assert client.list_projects() == {"projects": [registered]}

        created = client.create_scenario(
            project_instance_id=project_instance_id,
            scenario_id="registered-scenario",
            project_binding_digest=PROJECT_DIGEST,
        )["scenario"]
        assert created["scenario_id"] == "registered-scenario"
        with pytest.raises(HarnessClientError) as mismatch:
            client.create_scenario(
                project_instance_id=project_instance_id,
                scenario_id="wrong-binding",
                project_binding_digest="d" * 64,
            )
        assert mismatch.value.code == "operation.precondition-failed"

    durable = json.loads(
        (state_root / "project-registry.json").read_text(encoding="utf-8")
    )
    private_project = durable["projects"][project_instance_id]
    assert private_project["canonical_root"] == str(project_root.resolve())
    assert "canonical_root" not in private_project["record"]

    with running_host(state_root) as (_, client):
        assert client.list_projects()["projects"][0]["project_instance_id"] == (
            project_instance_id
        )
        client.create_scenario(
            project_instance_id=project_instance_id,
            scenario_id="after-restart",
            project_binding_digest=PROJECT_DIGEST,
        )


def test_unregistered_project_is_rejected(tmp_path: Path) -> None:
    with running_host(tmp_path / "state") as (_, client):
        with pytest.raises(HarnessClientError) as rejected:
            client.create_scenario(
                project_instance_id="unregistered",
                scenario_id="scenario",
                project_binding_digest=PROJECT_DIGEST,
            )
        assert rejected.value.code == "target.project-not-found"


def test_registered_project_exposes_bounded_path_free_collaboration_templates(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        registered = client.register_project(canonical_project_path=str(project_root))
        project_id = registered["project"]["project_instance_id"]
        assert host.projects.collaboration_templates(project_id) == {
            "templates": [_collaboration_template()]
        }
        encoded = json.dumps(host.projects.collaboration_templates(project_id))
        assert str(project_root) not in encoded


def test_registration_is_idempotent_and_rejects_request_reuse(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    with running_host(tmp_path / "state") as (_, client):
        first = client.register_project(
            canonical_project_path=str(first_root), request_id="same-request"
        )
        assert (
            client.register_project(
                canonical_project_path=str(first_root), request_id="same-request"
            )
            == first
        )
        refreshed = client.register_project(
            canonical_project_path=str(first_root), request_id="new-request"
        )
        assert refreshed["project"]["project_instance_id"] == first["project"][
            "project_instance_id"
        ]
        assert refreshed["project"]["registration_revision"] == 2
        with pytest.raises(HarnessClientError) as reused:
            client.register_project(
                canonical_project_path=str(second_root), request_id="same-request"
            )
        assert reused.value.code == "operation.precondition-failed"


def test_corrupt_private_registry_fails_closed(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        client.register_project(canonical_project_path=str(project_root))

    registry_path = state_root / "project-registry.json"
    value = json.loads(registry_path.read_text(encoding="utf-8"))
    next(iter(value["projects"].values()))["canonical_root_fingerprint"] = "bad"
    registry_path.write_text(json.dumps(value), encoding="utf-8")
    registry_path.chmod(0o600)
    with pytest.raises(ValueError, match="project registry records differ"):
        HarnessHost(state_root, tmp_path / "host.sock")


def test_private_canonical_root_is_revalidated_before_adapter_dispatch(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (host, client):
        registered = client.register_project(canonical_project_path=str(project_root))
        project_id = registered["project"]["project_instance_id"]
        assert host.projects.canonical_root(project_id) == project_root.resolve()
        project_root.rmdir()
        with pytest.raises(ProjectError, match="unavailable"):
            host.projects.canonical_root(project_id)


def test_unregister_removes_only_a_scenario_free_project(tmp_path: Path) -> None:
    """Unregistering forgets the registration record and nothing else; a
    project that still owns durable Scenarios keeps it until every one of
    them is explicitly destroyed."""
    state_root = tmp_path / "state"
    busy_root = tmp_path / "busy-project"
    idle_root = tmp_path / "idle-project"
    busy_root.mkdir()
    idle_root.mkdir()
    with running_host(state_root) as (_, client):
        busy = client.register_project(
            canonical_project_path=str(busy_root), request_id="register-busy"
        )["project"]
        idle = client.register_project(
            canonical_project_path=str(idle_root), request_id="register-idle"
        )["project"]
        client.create_scenario(
            project_instance_id=busy["project_instance_id"],
            scenario_id="scenario-busy",
            project_binding_digest=PROJECT_DIGEST,
            request_id="create-busy",
        )

        with pytest.raises(HarnessClientError) as blocked:
            client.unregister_project(
                project_instance_id=busy["project_instance_id"],
                request_id="unregister-busy",
            )
        assert blocked.value.code == "project.scenarios-exist"
        assert len(client.list_projects()["projects"]) == 2

        removed = client.unregister_project(
            project_instance_id=idle["project_instance_id"],
            request_id="unregister-idle",
        )
        assert removed["unregistered"] == {
            "project_instance_id": idle["project_instance_id"],
            "project_key": idle["project_key"],
            "registration_revision": idle["registration_revision"],
        }
        assert client.list_projects() == {"projects": [busy]}

        # Exact replay returns the durable result instead of failing.
        assert (
            client.unregister_project(
                project_instance_id=idle["project_instance_id"],
                request_id="unregister-idle",
            )
            == removed
        )
        # A fresh request against the forgotten project is a typed refusal
        # (project.not-found is translated to the identity-scoped IPC code).
        with pytest.raises(HarnessClientError) as missing:
            client.unregister_project(
                project_instance_id=idle["project_instance_id"],
                request_id="unregister-gone",
            )
        assert missing.value.code == "target.project-not-found"

        # The project can simply be registered again, as a fresh record.
        back = client.register_project(
            canonical_project_path=str(idle_root), request_id="register-idle-again"
        )["project"]
        assert back["registration_revision"] == 1
        assert len(client.list_projects()["projects"]) == 2


def test_bootstrap_drafts_are_reported_and_replayed(tmp_path: Path) -> None:
    project_root = tmp_path / "bare-project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (_, client):
        drafted = client.bootstrap_project(
            canonical_project_path=str(project_root), request_id="bootstrap-1"
        )
        assert drafted["bootstrap"]["already_configured"] is False
        assert "project_descriptor.yaml" in drafted["bootstrap"]["created"]
        # Exact replay returns the durable result.
        assert (
            client.bootstrap_project(
                canonical_project_path=str(project_root), request_id="bootstrap-1"
            )
            == drafted
        )
