# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ai_collab_app.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("build_ai_collab_app", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_venv(tmp_path: Path) -> Path:
    """A venv-shaped tree whose bin/python is a symlink to a base interpreter."""
    base = tmp_path / "base" / "bin"
    base.mkdir(parents=True)
    base_python = base / "python3.11"
    base_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    base_python.chmod(0o755)

    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(base_python)
    return venv_bin / "python"


def test_selected_interpreter_keeps_a_virtualenv_executable_in_place(
    tmp_path: Path,
) -> None:
    module = _module()
    venv_python = _fake_venv(tmp_path)

    selected = module._selected_interpreter(venv_python)  # noqa: SLF001

    # Resolving the executable itself would land on the base interpreter, whose
    # site-packages lacks the environment's dependencies, so the payload would
    # be vendored from the wrong environment.
    assert selected == venv_python
    assert selected.parent.name == "bin"
    assert selected.parent.parent.name == "venv"
    assert selected != venv_python.resolve()


def test_selected_interpreter_normalises_without_following_the_executable(
    tmp_path: Path,
) -> None:
    module = _module()
    venv_python = _fake_venv(tmp_path)
    indirect = venv_python.parent / ".." / "bin" / "python"

    assert module._selected_interpreter(indirect) == venv_python  # noqa: SLF001


def test_selected_interpreter_rejects_unusable_paths(tmp_path: Path) -> None:
    module = _module()
    missing = tmp_path / "bin" / "python"
    missing.parent.mkdir(parents=True)
    with pytest.raises(SystemExit):
        module._selected_interpreter(missing)  # noqa: SLF001

    not_executable = tmp_path / "bin" / "python-plain"
    not_executable.write_text("", encoding="utf-8")
    not_executable.chmod(0o644)
    with pytest.raises(SystemExit):
        module._selected_interpreter(not_executable)  # noqa: SLF001


def test_selected_interpreter_accepts_the_running_interpreter() -> None:
    module = _module()
    selected = module._selected_interpreter(Path(sys.executable))  # noqa: SLF001
    assert selected.is_file()
    assert os.access(selected, os.X_OK)
