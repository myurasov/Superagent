# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/memory_routing_check.py` (host-IDE memory-store detector)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from superagent.tools.memory_routing_check import (
    candidate_roots,
    check,
    claude_project_slug,
    main,
)

# --- slug derivation ----------------------------------------------------------

def test_claude_project_slug_replaces_non_alphanumerics() -> None:
    slug = claude_project_slug(
        Path("/Users/someone/Library/Mobile Documents/com~apple~CloudDocs/MY-Repo")
    )
    assert slug == "-Users-someone-Library-Mobile-Documents-com-apple-CloudDocs-MY-Repo"


def test_candidate_roots_are_repo_scoped(tmp_path: Path) -> None:
    repo = tmp_path / "some.repo"
    repo.mkdir()
    roots = candidate_roots(repo, home=tmp_path / "home")
    assert roots, "expected at least one candidate root"
    slug = claude_project_slug(repo)
    for root in roots:
        assert str(tmp_path / "home" / ".claude") in str(root)
        assert slug in str(root)


# --- check() ------------------------------------------------------------------

def test_check_missing_root_is_clean(tmp_path: Path) -> None:
    assert check([tmp_path / "nope"]) == []


def test_check_empty_dir_is_clean(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    assert check([root]) == []


def test_check_lists_files_recursively(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    (root / "sub").mkdir(parents=True)
    (root / "MEMORY.md").write_text("remembered outside the workspace")
    (root / "sub" / "note.md").write_text("more")
    offenders = check([root])
    paths = [p for p, _ in offenders]
    assert root / "MEMORY.md" in paths
    assert root / "sub" / "note.md" in paths
    assert all(isinstance(m, dt.datetime) for _, m in offenders)


def test_check_since_filters_old_files(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    old = root / "old.md"
    old.write_text("old")
    stamp = dt.datetime(2026, 1, 1, 12, 0, 0).timestamp()
    os.utime(old, (stamp, stamp))

    cutoff = dt.datetime(2026, 6, 1)
    assert check([root], since=cutoff) == []
    assert len(check([root], since=dt.datetime(2025, 6, 1))) == 1


def test_check_file_root(tmp_path: Path) -> None:
    f = tmp_path / "MEMORY.md"
    f.write_text("flat-layout memory file")
    offenders = check([f])
    assert [p for p, _ in offenders] == [f]


# --- CLI ------------------------------------------------------------------------

def test_cli_clean_dir_exits_0(tmp_path: Path, capsys) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    rc = main(["--root", str(root)])
    assert rc == 0
    assert "clean" in capsys.readouterr().out


def test_cli_offending_files_exit_1_and_listed(tmp_path: Path, capsys) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text("oops")
    rc = main(["--root", str(root)])
    assert rc == 1
    out = capsys.readouterr().out
    assert str(root / "MEMORY.md") in out
    assert "mtime" in out


def test_cli_since_filtering(tmp_path: Path, capsys) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    f = root / "MEMORY.md"
    f.write_text("old content")
    stamp = dt.datetime(2026, 1, 1, 12, 0, 0).timestamp()
    os.utime(f, (stamp, stamp))

    assert main(["--root", str(root), "--since", "2026-06-01T00:00:00"]) == 0
    capsys.readouterr()
    assert main(["--root", str(root), "--since", "2025-06-01T00:00:00"]) == 1


def test_cli_bad_since_exits_2(tmp_path: Path, capsys) -> None:
    rc = main(["--root", str(tmp_path), "--since", "yesterday"])
    assert rc == 2
    assert "bad --since" in capsys.readouterr().err


def test_cli_json_output(tmp_path: Path, capsys) -> None:
    import json

    root = tmp_path / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text("oops")
    rc = main(["--root", str(root), "--json"])
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["offenders"][0]["path"] == str(root / "MEMORY.md")


def test_check_never_creates_roots(tmp_path: Path) -> None:
    root = tmp_path / "never-created"
    assert main(["--root", str(root)]) == 0
    assert not root.exists()
