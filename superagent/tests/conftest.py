# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for Superagent tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the framework package importable as `superagent`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


FRAMEWORK_DIR = REPO_ROOT / "superagent"


#: The machine name every test runs under (tools/machines.py); new ids carry it.
TEST_MACHINE = "testbox"


@pytest.fixture(autouse=True)
def _machine_local_sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep every test off the real `~/.superagent/` and on one fixed machine name.

    Tools write machine-local state (the machine's name, the on-open nag
    marker, the conflict sweep's trash) under `$SUPERAGENT_HOME`; a test that
    needs another home or name sets its own with `monkeypatch`.
    """
    monkeypatch.setenv("SUPERAGENT_HOME", str(tmp_path_factory.mktemp("sa-home")))
    monkeypatch.setenv("SUPERAGENT_MACHINE", TEST_MACHINE)


@pytest.fixture(scope="session")
def framework_dir() -> Path:
    """Absolute path to the superagent/ framework directory."""
    return FRAMEWORK_DIR


@pytest.fixture
def fresh_workspace(tmp_path: Path) -> Path:
    """Return a workspace path under tmp_path; do not pre-create."""
    return tmp_path / "workspace"


@pytest.fixture
def initialized_workspace(framework_dir: Path, fresh_workspace: Path) -> Path:
    """Run workspace_init.py against a tmp workspace; return its path."""
    from superagent.tools.workspace_init import main as init_main

    rc = init_main([
        "--workspace", str(fresh_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0
    return fresh_workspace
