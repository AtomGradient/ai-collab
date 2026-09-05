# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

from __future__ import annotations

import copy
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
from ai_collab.project import ProjectError, ProjectRegistry
from ai_collab.protocol import canonical_json_sha256


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


def _resolved_render(
    *,
    source_digest: str = "d" * 64,
    collaboration_templates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if collaboration_templates is None:
        collaboration_templates = [_collaboration_template()]
    availability: dict[str, Any] = {
        "status": "ready",
        "observations": [],
        "changes": [],
        "warnings": [],
    }
    availability["fingerprint"] = canonical_json_sha256(availability)
    value: dict[str, Any] = {
        "render_contract_version": 1,
        "source": {
            "kind": "fileless",
            "intent_schema_version": None,
            "source_digest": source_digest,
        },
        "project": {
            "project_key": "test-project",
            "product_contract_version": "1.0",
            "workspace_adapter_id": "workspace.test-v1",
            "environment_adapter_id": "environment.test-v1",
            "participant_driver_contract": 2,
            "collaboration_policy_schema": 1,
        },
        "repo_manifest": {"schema_version": 1, "project_key": "test-project", "repos": []},
        "repo_manifest_digest": "b" * 64,
        "gate": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
        "collaboration": {"kind": "builtin", "profile_id": "builtin.standard-v1"},
        "availability": availability,
    }
    registry = {
        "schema_version": 1,
        "templates": collaboration_templates,
    }
    value["collaboration"].update(  # type: ignore[union-attr]
        {
            "digest": "a" * 64,
            "registry_snapshot": registry,
            "registry_snapshot_digest": canonical_json_sha256(registry),
        }
    )
    value["render_digest"] = canonical_json_sha256(
        {key: item for key, item in value.items() if key != "availability"}
    )
    return value


PROJECT_DIGEST = _resolved_render()["render_digest"]


class FakeProjectAdapter:
    def __init__(
        self,
        *,
        project_digest: str | None = None,
        source_digest: str = "d" * 64,
        collaboration_templates: list[dict[str, Any]] | None = None,
    ) -> None:
        self.project_digest = project_digest
        self.source_digest = source_digest
        self.collaboration_templates = collaboration_templates

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
                    "created": [],
                    "already_configured": False,
                    "project_key": "test-project",
                    "proposal": {
                        "intent_digest": "7" * 64,
                        "yaml": "schema_version: 1\n",
                    },
                }
            }
        assert operation == "register"
        assert Path(payload["canonical_project_path"]).is_dir()
        render = _resolved_render(
            source_digest=self.source_digest,
            collaboration_templates=copy.deepcopy(self.collaboration_templates),
        )
        return {
            "project": {
                "project_key": "test-project",
                "project_binding_digest": self.project_digest
                or render["render_digest"],
                "product_contract_version": "1.0",
                "workspace_adapter_id": "workspace.test-v1",
                "environment_adapter_id": "environment.test-v1",
                "participant_driver_contract": 2,
                "collaboration_policy_schema": 1,
                "repo_manifest_digest": "b" * 64,
                "adapter_capability_digest": "c" * 64,
            },
            "render": render,
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
    assert private_project["render"] == _resolved_render()

    with running_host(state_root) as (_, client):
        assert client.list_projects()["projects"][0]["project_instance_id"] == (
            project_instance_id
        )
        client.create_scenario(
            project_instance_id=project_instance_id,
            scenario_id="after-restart",
            project_binding_digest=PROJECT_DIGEST,
        )


def test_v0161_project_registry_migrates_with_a_last_good_snapshot(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        project_id = client.register_project(
            canonical_project_path=str(project_root), request_id="legacy-register"
        )["project"]["project_instance_id"]

    registry_path = state_root / "project-registry.json"
    legacy = json.loads(registry_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy["state_revision"] -= 1
    del legacy["projects"][project_id]["render"]
    del legacy["projects"][project_id]["pending_reconciliation"]
    del legacy["projects"][project_id]["accepted_binding_digests"]
    legacy["requests"]["legacy-bootstrap"] = {
        "request_digest": "8" * 64,
        "operation_id": "project-op-legacy-bootstrap",
        "result": {
            "bootstrap": {
                "created": ["project_descriptor.yaml", "repo_manifest.yaml"],
                "already_configured": False,
                "project_key": "test-project",
            }
        },
    }
    registry_path.write_text(json.dumps(legacy), encoding="utf-8")
    registry_path.chmod(0o600)

    registry = ProjectRegistry(state_root, None)
    assert registry.list()["projects"][0]["project_instance_id"] == project_id
    migrated = json.loads(registry_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["projects"][project_id]["render"] is None
    assert migrated["projects"][project_id]["pending_reconciliation"] is None
    assert migrated["projects"][project_id]["accepted_binding_digests"] == [
        PROJECT_DIGEST
    ]
    backup = state_root / "project-registry.v1.last-good.json"
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == 1


def test_v0161_project_registry_rejects_an_untrusted_last_good_target(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        project_id = client.register_project(
            canonical_project_path=str(project_root), request_id="legacy-register"
        )["project"]["project_instance_id"]

    registry_path = state_root / "project-registry.json"
    legacy = json.loads(registry_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    item = legacy["projects"][project_id]
    del item["render"]
    del item["pending_reconciliation"]
    del item["accepted_binding_digests"]
    registry_path.write_text(json.dumps(legacy), encoding="utf-8")
    registry_path.chmod(0o600)
    (state_root / "project-registry.v1.last-good.json").symlink_to(
        tmp_path / "outside"
    )

    with pytest.raises(ProjectError, match="last-good snapshot differs"):
        ProjectRegistry(state_root, None)


def test_v0161_tool_pins_refresh_without_employee_acceptance(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        project_id = client.register_project(
            canonical_project_path=str(project_root), request_id="legacy-register"
        )["project"]["project_instance_id"]

    registry_path = state_root / "project-registry.json"
    legacy = json.loads(registry_path.read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    item = legacy["projects"][project_id]
    item["record"]["project_binding_digest"] = "7" * 64
    item["record"]["product_contract_version"] = "3.2"
    item["record"]["workspace_adapter_id"] = "ai-collab-edgestudio-workspace-v1"
    item["record"]["environment_adapter_id"] = "ai-collab-edgestudio-environment-v1"
    del item["render"]
    del item["pending_reconciliation"]
    del item["accepted_binding_digests"]
    registry_path.write_text(json.dumps(legacy), encoding="utf-8")
    registry_path.chmod(0o600)

    with running_host(state_root) as (host, client):
        with pytest.raises(HarnessClientError) as unresolved:
            client.create_scenario(
                project_instance_id=project_id,
                scenario_id="before-runtime-refresh",
                project_binding_digest="7" * 64,
                request_id="create-before-runtime-refresh",
            )
        assert unresolved.value.code == "project.reconciliation-required"

        refreshed = client.reconcile_project(
            project_instance_id=project_id,
            request_id="upgrade-runtime-pins",
        )
        assert refreshed["reconciliation"]["binding_changed"] is False
        assert refreshed["project"]["project_binding_digest"] == PROJECT_DIGEST
        with pytest.raises(ProjectError, match="current project snapshot"):
            host.projects.validate_binding(project_id, "7" * 64)
        host.projects.validate_binding(project_id, PROJECT_DIGEST)


def test_tool_owned_pins_refresh_with_an_existing_render(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(state_root) as (_, client):
        project_id = client.register_project(
            canonical_project_path=str(project_root), request_id="register-old-pins"
        )["project"]["project_instance_id"]

    registry_path = state_root / "project-registry.json"
    state = json.loads(registry_path.read_text(encoding="utf-8"))
    item = state["projects"][project_id]
    old_digest = "7" * 64
    item["record"]["project_binding_digest"] = old_digest
    item["record"]["product_contract_version"] = "0.9"
    item["record"]["workspace_adapter_id"] = "workspace.old-v1"
    item["record"]["environment_adapter_id"] = "environment.old-v1"
    item["accepted_binding_digests"] = [old_digest]
    registry_path.write_text(json.dumps(state), encoding="utf-8")
    registry_path.chmod(0o600)

    with running_host(state_root) as (_, client):
        refreshed = client.reconcile_project(
            project_instance_id=project_id,
            request_id="refresh-existing-render-pins",
        )

    assert refreshed["reconciliation"]["binding_changed"] is False
    assert refreshed["project"]["project_binding_digest"] == PROJECT_DIGEST
    assert refreshed["project"]["registration_revision"] == 2


def test_unregistered_project_is_rejected(tmp_path: Path) -> None:
    with running_host(tmp_path / "state") as (_, client):
        with pytest.raises(HarnessClientError) as rejected:
            client.create_scenario(
                project_instance_id="unregistered",
                scenario_id="scenario",
                project_binding_digest=PROJECT_DIGEST,
            )
        assert rejected.value.code == "target.project-not-found"


def test_migrated_binding_validation_does_not_require_canonical_root_online(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        project = client.register_project(
            canonical_project_path=str(project_root)
        )["project"]
        project_root.rename(tmp_path / "project-offline")

        host.projects.validate_existing_binding(
            project["project_instance_id"], project["project_binding_digest"]
        )
        with pytest.raises(ProjectError, match="unavailable"):
            host.projects.canonical_root(project["project_instance_id"])


def test_accepted_binding_history_always_retains_the_migrated_digest() -> None:
    original = "0" * 64
    accepted = [original]
    for index in range(1, 300):
        accepted = ProjectRegistry._retain_accepted_bindings(  # noqa: SLF001
            accepted, f"{index:064x}"
        )

    assert len(accepted) == 256
    assert accepted[0] == original
    assert accepted[-1] == f"{299:064x}"


def test_project_configuration_errors_have_contextual_repair_actions() -> None:
    invalid = HarnessHost.project_error(
        ProjectError("project.intent-invalid", "project intent is invalid")
    )
    assert invalid.repair_action == "project.fix-configuration"

    too_new = HarnessHost.project_error(
        ProjectError("project.intent-too-new", "newer AICollab required")
    )
    assert too_new.repair_action == "host.update"

    incomplete = HarnessHost.project_error(
        ProjectError(
            "project.intent-proposal-incomplete",
            "canonical Git remote is missing",
        )
    )
    assert incomplete.repair_action == "project.resolve-remote"


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


def test_builtin_catalog_refresh_is_not_offered_to_rooms_and_snapshot_stays_frozen(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    project_root.mkdir()
    version_one = _collaboration_template()
    version_two = copy.deepcopy(version_one)
    version_two["display_name"] = "Peer review v2"

    with running_host(state_root) as (host, client):
        adapter = FakeProjectAdapter(
            collaboration_templates=[copy.deepcopy(version_one)]
        )
        host.projects.adapter = adapter  # type: ignore[assignment]
        registered = client.register_project(
            canonical_project_path=str(project_root), request_id="register-v1"
        )["project"]
        project_id = registered["project_instance_id"]
        client.create_scenario(
            project_instance_id=project_id,
            scenario_id="scenario-v1",
            project_binding_digest=registered["project_binding_digest"],
            request_id="create-v1",
        )
        frozen = host.store.scenario_project_contract(project_id, "scenario-v1")

        adapter.collaboration_templates = [copy.deepcopy(version_two)]
        refreshed = client.reconcile_project(
            project_instance_id=project_id, request_id="refresh-v2"
        )
        assert refreshed["reconciliation"]["binding_changed"] is False
        current = refreshed["project"]
        assert current["project_binding_digest"] != registered[
            "project_binding_digest"
        ]
        assert host.projects.collaboration_templates(project_id) == {
            "templates": [version_two]
        }
        assert client.list_policy_templates(project_instance_id=project_id) == {
            "templates": [],
            "source": "builtin",
        }
        assert host.store.scenario_project_contract(project_id, "scenario-v1") == frozen

        client.create_scenario(
            project_instance_id=project_id,
            scenario_id="scenario-v2",
            project_binding_digest=current["project_binding_digest"],
            request_id="create-v2",
        )
        project_root.rmdir()
        assert host.projects.collaboration_templates(project_id) == {
            "templates": [version_two]
        }


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
        assert refreshed["project"]["registration_revision"] == 1
        with pytest.raises(HarnessClientError) as reused:
            client.register_project(
                canonical_project_path=str(second_root), request_id="same-request"
            )
        assert reused.value.code == "operation.precondition-failed"


def test_reregistering_same_root_cannot_bypass_binding_reconciliation(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        first = client.register_project(
            canonical_project_path=str(project_root), request_id="register-original"
        )["project"]
        host.projects.adapter = FakeProjectAdapter(  # type: ignore[assignment]
            source_digest="9" * 64
        )

        repeated = client.register_project(
            canonical_project_path=str(project_root), request_id="register-again"
        )["project"]
        assert repeated == first
        assert host.projects.resolved_render(first["project_instance_id"]) == (
            _resolved_render()
        )

        observed = client.reconcile_project(
            project_instance_id=first["project_instance_id"],
            request_id="observe-after-reregister",
        )
        assert observed["project"] == first
        assert observed["reconciliation"]["binding_changed"] is True


def test_project_reconciliation_refreshes_availability_without_changing_binding(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        project = client.register_project(
            canonical_project_path=str(project_root), request_id="register-reconcile"
        )["project"]
        first = client.reconcile_project(
            project_instance_id=project["project_instance_id"],
            request_id="reconcile-project",
        )
        assert first["reconciliation"] == {
            "status": "ready",
            "binding_changed": False,
            "availability_fingerprint": canonical_json_sha256(
                {
                    "availability_fingerprint": _resolved_render()["availability"][
                        "fingerprint"
                    ],
                    "project_binding_digest": PROJECT_DIGEST,
                }
            ),
            "changes": [],
            "warnings": [],
        }
        assert first["project"]["registration_revision"] == 1
        assert host.projects.resolved_render(project["project_instance_id"]) == _resolved_render()
        assert (
            client.reconcile_project(
                project_instance_id=project["project_instance_id"],
                request_id="reconcile-project",
            )
            == first
        )


def test_project_binding_update_waits_for_exact_user_acceptance(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        project = client.register_project(
            canonical_project_path=str(project_root), request_id="register-change"
        )["project"]
        original_render = host.projects.resolved_render(project["project_instance_id"])
        created = client.create_scenario(
            project_instance_id=project["project_instance_id"],
            scenario_id="pinned-before-update",
            project_binding_digest=PROJECT_DIGEST,
            request_id="create-before-change",
        )
        changed_render = _resolved_render(source_digest="9" * 64)
        host.projects.adapter = FakeProjectAdapter(
            source_digest="9" * 64
        )  # type: ignore[assignment]
        observed = client.reconcile_project(
            project_instance_id=project["project_instance_id"],
            request_id="observe-change",
        )
        assert observed["reconciliation"]["binding_changed"] is True
        assert observed["project"] == project
        assert host.projects.resolved_render(project["project_instance_id"]) == original_render

        with pytest.raises(HarnessClientError) as stale:
            client.accept_project_reconciliation(
                project_instance_id=project["project_instance_id"],
                availability_fingerprint="0" * 64,
                request_id="accept-stale",
            )
        assert stale.value.code == "project.reconciliation-stale"

        accepted = client.accept_project_reconciliation(
            project_instance_id=project["project_instance_id"],
            availability_fingerprint=observed["reconciliation"][
                "availability_fingerprint"
            ],
            request_id="accept-change",
        )
        assert accepted["project"]["project_binding_digest"] == changed_render[
            "render_digest"
        ]
        assert accepted["project"]["registration_revision"] == 2
        assert client.create_scenario(
            project_instance_id=project["project_instance_id"],
            scenario_id="pinned-before-update",
            project_binding_digest=PROJECT_DIGEST,
            request_id="create-before-change",
        ) == created
        # Historical bindings cannot be used for a new Scenario. Existing
        # Scenarios remain pinned through their own private snapshot below.
        with pytest.raises(ProjectError, match="current project snapshot"):
            host.projects.validate_binding(
                project["project_instance_id"], PROJECT_DIGEST
            )
        host.projects.validate_binding(
            project["project_instance_id"], changed_render["render_digest"]
        )
        host.projects.validate_existing_binding(
            project["project_instance_id"], PROJECT_DIGEST
        )
        with pytest.raises(ProjectError, match="not accepted"):
            host.projects.validate_existing_binding(
                project["project_instance_id"], "0" * 64
            )
        assert host.projects.resolved_render(
            project["project_instance_id"], PROJECT_DIGEST
        ) is None
        assert host.store.scenario_project_contract(
            project["project_instance_id"], "pinned-before-update"
        ) == original_render
        assert host._scenario_project_render(  # noqa: SLF001 - snapshot contract witness
            project["project_instance_id"],
            "pinned-before-update",
            PROJECT_DIGEST,
        ) == original_render
        assert host.projects.resolved_render(project["project_instance_id"]) == (
            changed_render
        )


def test_reconciliation_acceptance_fences_binding_when_availability_is_unchanged(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (host, client):
        project = client.register_project(
            canonical_project_path=str(project_root), request_id="register-fenced-change"
        )["project"]
        host.projects.adapter = FakeProjectAdapter(  # type: ignore[assignment]
            source_digest="8" * 64
        )
        first = client.reconcile_project(
            project_instance_id=project["project_instance_id"],
            request_id="observe-fenced-change-1",
        )["reconciliation"]
        host.projects.adapter = FakeProjectAdapter(  # type: ignore[assignment]
            source_digest="9" * 64
        )
        second = client.reconcile_project(
            project_instance_id=project["project_instance_id"],
            request_id="observe-fenced-change-2",
        )["reconciliation"]

        assert first["availability_fingerprint"] != second[
            "availability_fingerprint"
        ]
        with pytest.raises(HarnessClientError) as stale:
            client.accept_project_reconciliation(
                project_instance_id=project["project_instance_id"],
                availability_fingerprint=first["availability_fingerprint"],
                request_id="accept-fenced-change-stale",
            )
        assert stale.value.code == "project.reconciliation-stale"


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


def test_bootstrap_returns_an_owner_private_intent_proposal_and_replays(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "bare-project"
    project_root.mkdir()
    with running_host(tmp_path / "state") as (_, client):
        drafted = client.bootstrap_project(
            canonical_project_path=str(project_root), request_id="bootstrap-1"
        )
        assert drafted["bootstrap"]["already_configured"] is False
        assert drafted["bootstrap"]["created"] == []
        assert drafted["bootstrap"]["proposal"]["yaml"].startswith("schema_version")
        # Exact replay returns the durable result.
        assert (
            client.bootstrap_project(
                canonical_project_path=str(project_root), request_id="bootstrap-1"
            )
            == drafted
        )
