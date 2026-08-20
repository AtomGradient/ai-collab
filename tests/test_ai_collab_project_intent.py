# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "project-config"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_collab_project_intent as intent  # noqa: E402


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *arguments]).decode().strip()


def _repo(path: Path, *, remote: str | None = None) -> Path:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Intent Test")
    _git(path, "commit", "--allow-empty", "-m", "initial")
    if remote is not None:
        _git(path, "remote", "add", "origin", remote)
    return path.resolve()


def _row(project_key: str) -> dict[str, object]:
    return {
        "repo_key": project_key,
        "classification": "required",
        "placement": "project_root",
        "path": ".",
        "remote": f"https://github.com/example/{project_key}.git",
        "base_branch": "main",
        "provision_order": 0,
        "provision_after": [],
        "acceptance_layer": "base",
        "smoke_policy": "required",
    }


def _write_intent(project: Path, *, min_reader: str = "0.1.7") -> None:
    directory = project / ".aicollab"
    directory.mkdir()
    value = {
        "schema_version": 1,
        "min_reader": min_reader,
        "project_key": project.name,
        "repos": [_row(project.name)],
        "gates": {"profile": "builtin.standard-v1"},
        "collaboration": {"profile": "builtin.standard-v1"},
    }
    (directory / "project.yaml").write_text(
        yaml.safe_dump(value, sort_keys=False), encoding="utf-8"
    )


def test_fileless_git_project_resolves_without_writing_canonical_source(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    before = sorted(item.relative_to(project).as_posix() for item in project.rglob("*"))
    first = intent.resolve_project(project)
    second = intent.resolve_project(project)
    after = sorted(item.relative_to(project).as_posix() for item in project.rglob("*"))

    assert first == second
    assert before == after
    assert first["source"]["kind"] == "fileless"
    assert first["project"]["project_key"] == "sampleproject"
    assert first["gate"] == {
        "kind": "builtin",
        "profile_id": "builtin.standard-v1",
        "digest": first["gate"]["digest"],
    }
    assert first["availability"]["status"] == "ready"


def test_render_digest_is_machine_path_independent(tmp_path: Path) -> None:
    first_root = _repo(
        tmp_path / "machine-a" / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    second_root = _repo(
        tmp_path / "machine-b" / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )

    first = intent.resolve_project(first_root)
    second = intent.resolve_project(second_root)

    assert first["render_digest"] == second["render_digest"]
    assert first["repo_manifest"] == second["repo_manifest"]
    assert first["gate"] == second["gate"]
    assert first["collaboration"] == second["collaboration"]


def test_team_intent_is_the_stable_source_and_detects_an_undeclared_repo(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    _write_intent(project)
    _repo(
        project / "new-helper",
        remote="https://github.com/example/new-helper.git",
    )
    render = intent.resolve_project(project)

    assert render["source"]["kind"] == "team-intent"
    assert render["source"]["intent_schema_version"] == 1
    assert render["availability"]["status"] == "attention"
    assert render["availability"]["changes"] == [
        {
            "repo_key": "new-helper",
            "path": "new-helper",
            "classification": "undeclared",
            "status": "undeclared",
        }
    ]


def test_intent_min_reader_is_a_forward_compatibility_guard(tmp_path: Path) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    _write_intent(project, min_reader="0.1.8")
    with pytest.raises(intent.IntentError) as exc:
        intent.resolve_project(project)
    assert exc.value.code == "project.intent-too-new"


def test_additive_top_level_intent_field_is_ignored_with_warning(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    _write_intent(project)
    path = project / ".aicollab" / "project.yaml"
    baseline = intent.resolve_project(project)
    path.write_text(
        path.read_text(encoding="utf-8") + "future_display_hint: compact\n",
        encoding="utf-8",
    )

    extended = intent.resolve_project(project)

    assert extended["render_digest"] == baseline["render_digest"]
    assert extended["availability"]["warnings"] == [
        "intent.unknown-field:future_display_hint"
    ]


def test_legacy_product_3_2_pins_render_with_current_runtime_contracts(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    fixture = FIXTURES / "v0161"
    for name in ("project_descriptor.yaml", "repo_manifest.yaml", "gates.yaml"):
        (project / name).write_bytes((fixture / name).read_bytes())

    render = intent.resolve_project(project)
    assert render["source"]["kind"] == "legacy"
    assert render["project"]["product_contract_version"] == "1.0"
    assert render["project"]["workspace_adapter_id"] == "ai-collab-workspace-v1"
    assert render["project"]["environment_adapter_id"] == "ai-collab-environment-v1"


def test_partial_legacy_configuration_registers_with_an_actionable_warning(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    (project / "repo_manifest.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "project_key": "sampleproject", "repos": [_row("sampleproject")]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    render = intent.resolve_project(project)
    assert render["source"]["kind"] == "legacy-partial"
    assert render["availability"]["warnings"] == ["legacy.descriptor-missing"]
    assert render["availability"]["status"] == "attention"


def test_draft_is_owner_private_data_and_contains_no_tool_contract_pins(
    tmp_path: Path,
) -> None:
    project = _repo(
        tmp_path / "sampleproject",
        remote="https://github.com/example/sampleproject.git",
    )
    proposal = intent.draft_intent(project)
    drafted = proposal["intent"]
    assert drafted["min_reader"] == "0.1.7"
    assert "workspace_adapter" not in proposal["yaml"]
    assert "product_contract_version" not in proposal["yaml"]
    assert not (project / ".aicollab").exists()
