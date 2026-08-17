# SPDX-License-Identifier: MIT
# Copyright (c) 2026 AtomGradient

"""Shape tests for the generic project descriptor and repo manifest validators."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ai_collab_project_descriptor as descriptor_validator  # noqa: E402
import ai_collab_repo_manifest as manifest_validator  # noqa: E402


DESCRIPTOR = """\
schema_version: 1
project_key: {project_key}
product_contract_version: "1.0"
workspace_adapter: ai-collab-workspace-v1
repo_manifest: repo_manifest.yaml
environment_adapter: ai-collab-environment-v1
gate_registry: gates.yaml
participant_driver_contract: 2
collaboration_policy_schema: 1
"""

MANIFEST = """\
schema_version: 1
project_key: {project_key}
repos:
  - repo_key: {root_key}
    classification: required
    placement: project_root
    path: .
    remote: {remote}
    base_branch: main
    provision_order: 0
    provision_after: []
    acceptance_layer: base
    smoke_policy: required{extra_root_fields}
"""


def _write_project(
    base: Path,
    *,
    project_key: str = "anyproject",
    root_key: str | None = None,
    remote: str = "git@github.com:example/anyproject.git",
    extra_root_fields: str = "",
) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "project_descriptor.yaml").write_text(
        DESCRIPTOR.format(project_key=project_key), encoding="utf-8"
    )
    (base / "gates.yaml").write_text(
        "schema_version: 1\n"
        f"registry_id: ai-collab-scenario-harness-{project_key}-v1.0-20260817\n",
        encoding="utf-8",
    )
    (base / "repo_manifest.yaml").write_text(
        MANIFEST.format(
            project_key=project_key,
            root_key=root_key or project_key,
            remote=remote,
            extra_root_fields=extra_root_fields,
        ),
        encoding="utf-8",
    )
    return base


def test_descriptor_accepts_any_declared_project_key(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "p", project_key="my-side-project")
    (project / "repo_manifest.yaml").write_text(
        MANIFEST.format(
            project_key="my-side-project",
            root_key="my-side-project",
            remote="git@github.com:example/side.git",
            extra_root_fields="",
        ),
        encoding="utf-8",
    )
    result = descriptor_validator.validate_descriptor(repo_root=project)
    assert result["status"] == "valid"
    assert result["project_key"] == "my-side-project"


def test_descriptor_rejects_project_key_shape_violation(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "p")
    descriptor = project / "project_descriptor.yaml"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8").replace(
            "project_key: anyproject", "project_key: 0badkey"
        ),
        encoding="utf-8",
    )
    with pytest.raises(descriptor_validator.DescriptorError):
        descriptor_validator.validate_descriptor(repo_root=project)


def test_descriptor_rejects_gate_registry_project_mismatch(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "p")
    (project / "gates.yaml").write_text(
        "schema_version: 1\n"
        "registry_id: ai-collab-scenario-harness-otherproject-v1.0-20260817\n",
        encoding="utf-8",
    )
    with pytest.raises(descriptor_validator.DescriptorError):
        descriptor_validator.validate_descriptor(repo_root=project)


def test_manifest_accepts_declared_key_and_https_remote(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        remote="https://github.com/example/anyproject.git",
    )
    result = manifest_validator.validate_manifest(repo_root=project)
    assert result["status"] == "valid"
    assert result["project_key"] == "anyproject"
    assert result["project_root_repo_key"] == "anyproject"


def test_manifest_rejects_root_row_not_matching_project_key(tmp_path: Path) -> None:
    project = _write_project(tmp_path / "p", root_key="differently-named")
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)


def test_manifest_rejects_unsafe_remote(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p", remote="http://github.com/example/anyproject.git"
    )
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)


def test_manifest_accepts_declared_environment_bindings(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        extra_root_fields=(
            "\n    dependency_lock: uv.lock"
            "\n    python_source_path: src"
            "\n    python_import_name: any_package"
        ),
    )
    result = manifest_validator.validate_manifest(repo_root=project)
    assert result["status"] == "valid"


def test_manifest_rejects_half_declared_python_binding(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        extra_root_fields="\n    python_source_path: src",
    )
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)


def test_manifest_rejects_import_name_shape_violation(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        extra_root_fields=(
            "\n    python_source_path: src"
            "\n    python_import_name: not-an-import"
        ),
    )
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)


def test_manifest_rejects_escaping_dependency_lock_path(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        extra_root_fields="\n    dependency_lock: ../outside.lock",
    )
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)


def test_manifest_rejects_a_second_dependency_lock(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path / "p",
        extra_root_fields="\n    dependency_lock: uv.lock",
    )
    manifest = project / "repo_manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + (
            "  - repo_key: second-lock\n"
            "    classification: required\n"
            "    placement: bundle_sibling\n"
            "    path: second-lock\n"
            "    remote: git@github.com:example/second-lock.git\n"
            "    base_branch: main\n"
            "    provision_order: 10\n"
            "    provision_after: []\n"
            "    acceptance_layer: base\n"
            "    smoke_policy: optional\n"
            "    dependency_lock: another.lock\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(manifest_validator.ManifestError):
        manifest_validator.validate_manifest(repo_root=project)
