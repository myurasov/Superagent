# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/home.py` (machine-local transient root)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def local_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sa-home"
    monkeypatch.setenv("SUPERAGENT_HOME", str(home))
    return home


def test_env_override(local_home: Path) -> None:
    from superagent.tools.home import superagent_home

    assert superagent_home() == local_home


def test_default_is_under_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    from superagent.tools.home import superagent_home

    monkeypatch.delenv("SUPERAGENT_HOME", raising=False)
    assert superagent_home() == Path.home() / ".superagent"


def test_tmp_and_tools_dirs_created_lazily(local_home: Path) -> None:
    from superagent.tools.home import tmp_dir, tools_dir

    assert not local_home.exists()
    assert tmp_dir() == local_home / "tmp"
    assert (local_home / "tmp").is_dir()
    assert tools_dir() == local_home / "tools"
    assert (local_home / "tools").is_dir()


def test_tmp_dir_no_ensure(local_home: Path) -> None:
    from superagent.tools.home import tmp_dir

    assert tmp_dir(ensure=False) == local_home / "tmp"
    assert not local_home.exists()


def test_cli_ensure_then_check(local_home: Path, capsys: pytest.CaptureFixture) -> None:
    from superagent.tools.home import SUBDIRS, main

    assert main(["--check"]) == 1  # nothing exists yet
    assert main([]) == 0  # ensure
    for name in SUBDIRS:
        assert (local_home / name).is_dir()
    assert main(["--check"]) == 0
    capsys.readouterr()
    assert main(["--json"]) == 0
    import json

    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["home"] == str(local_home)
