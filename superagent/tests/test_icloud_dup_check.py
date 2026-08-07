# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/icloud_dup_check.py` (report-only conflict-copy scanner)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def local_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sa-home"
    monkeypatch.setenv("SUPERAGENT_HOME", str(home))
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "Sources").mkdir(parents=True)
    (r / ".git").mkdir()
    return r


def test_pattern_matches_conflict_copies(repo: Path) -> None:
    from superagent.tools.icloud_dup_check import load_ignored, scan

    (repo / "Sources" / "receipt 2.pdf").touch()
    (repo / "Sources" / "notes 12.md").touch()
    (repo / "Sources" / "photo 2 .jpg").touch()
    (repo / "Sources" / "dir 3").mkdir()
    (repo / "Sources" / "legit-file.pdf").touch()
    (repo / "Sources" / "W-2.pdf").touch()  # single digit after hyphen: no match
    hits = scan(repo, load_ignored(repo))
    joined = "\n".join(hits)
    assert "receipt 2.pdf" in joined
    assert "notes 12.md" in joined
    assert "photo 2 .jpg" in joined
    assert "dir 3" in joined
    assert "legit-file.pdf" not in joined
    assert "W-2.pdf" not in joined


def test_skip_dirs_and_ignore_list(repo: Path) -> None:
    from superagent.tools.icloud_dup_check import load_ignored, scan

    (repo / ".git" / "objects 2").mkdir(parents=True)
    (repo / "Sources" / "known 2.pdf").touch()
    mem = repo / "workspace" / "_memory"
    mem.mkdir(parents=True)
    (mem / "icloud-dup-ignore.txt").write_text(
        "# legitimate names\nSources/known 2.pdf\n"
    )
    hits = scan(repo, load_ignored(repo))
    assert hits == []


def test_main_reports_and_always_exits_zero(
    repo: Path, capsys: pytest.CaptureFixture
) -> None:
    from superagent.tools.icloud_dup_check import main

    (repo / "Sources" / "receipt 2.pdf").touch()
    # not an iCloud path -> silent no-op without --force
    assert main(["--repo", str(repo)]) == 0
    assert capsys.readouterr().out == ""
    # forced scan reports but still exits 0
    assert main(["--repo", str(repo), "--force"]) == 0
    out = capsys.readouterr().out
    assert "receipt 2.pdf" in out
    assert "Report only" in out


def test_if_stale_throttle(repo: Path, capsys: pytest.CaptureFixture) -> None:
    from superagent.tools.icloud_dup_check import main

    (repo / "Sources" / "receipt 2.pdf").touch()
    assert main(["--repo", str(repo), "--force", "--if-stale", "24h"]) == 0
    assert "receipt 2.pdf" in capsys.readouterr().out
    # second run inside the window is silent
    assert main(["--repo", str(repo), "--force", "--if-stale", "24h"]) == 0
    assert capsys.readouterr().out == ""


def test_parse_window_rejects_garbage() -> None:
    import argparse

    from superagent.tools.icloud_dup_check import parse_window

    assert parse_window("24h") == 24 * 3600
    assert parse_window("7d") == 7 * 86400
    with pytest.raises(argparse.ArgumentTypeError):
        parse_window("soon")
