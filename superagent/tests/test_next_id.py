# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/next_id.py` (collision-safe id minting)."""
from __future__ import annotations

from pathlib import Path

import pytest

from superagent.tools.next_id import main, next_id


def test_empty_file_starts_at_001(tmp_path: Path) -> None:
    f = tmp_path / "interaction-log.yaml"
    f.write_text("")
    assert next_id("ilog", "2026-08-31", f) == "ilog-2026-08-31-001"


def test_missing_file_starts_at_001(tmp_path: Path) -> None:
    f = tmp_path / "does-not-exist.yaml"
    assert next_id("evt", "2026-08-31", f) == "evt-2026-08-31-001"


def test_gap_free_increment(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("entries:\n")
    for expected in ("sig-2026-08-31-001", "sig-2026-08-31-002", "sig-2026-08-31-003"):
        minted = next_id("sig", "2026-08-31", f)
        assert minted == expected
        with f.open("a") as fh:
            fh.write(f"  - id: \"{minted}\"\n")


def test_collision_avoidance_returns_max_plus_one(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text(
        "entries:\n"
        "  - id: \"dec-2026-08-31-001\"\n"
        "  - id: \"dec-2026-08-31-001\"\n"   # duplicate
        "  - id: \"dec-2026-08-31-003\"\n"   # gap
    )
    assert next_id("dec", "2026-08-31", f) == "dec-2026-08-31-004"


def test_mint_time_rescan(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("- id: \"ilog-2026-08-31-001\"\n")
    assert next_id("ilog", "2026-08-31", f) == "ilog-2026-08-31-002"
    # Another writer lands a row between the two mints.
    with f.open("a") as fh:
        fh.write("- id: \"ilog-2026-08-31-002\"\n- id: \"ilog-2026-08-31-005\"\n")
    assert next_id("ilog", "2026-08-31", f) == "ilog-2026-08-31-006"


def test_task_kind_uses_compact_date(tmp_path: Path) -> None:
    f = tmp_path / "todo.yaml"
    f.write_text("- id: \"task-20260831-001\"\n")
    # Dashed input date is normalized to the task kind's compact form.
    assert next_id("task", "2026-08-31", f) == "task-20260831-002"


def test_dashed_kind_accepts_compact_date_input(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("- id: \"ilog-2026-08-31-002\"\n")
    assert next_id("ilog", "20260831", f) == "ilog-2026-08-31-003"


def test_other_dates_do_not_bleed(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("- id: \"evt-2026-08-30-009\"\n")
    assert next_id("evt", "2026-08-31", f) == "evt-2026-08-31-001"


def test_kind_prefix_does_not_match_inside_longer_kind(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("- id: \"psig-2026-08-31-007\"\n")
    # `sig-…` must not count `psig-…` rows.
    assert next_id("sig", "2026-08-31", f) == "sig-2026-08-31-001"


def test_bad_date_raises(tmp_path: Path) -> None:
    f = tmp_path / "log.yaml"
    with pytest.raises(ValueError):
        next_id("ilog", "not-a-date", f)


def test_cli_prints_next_id(tmp_path: Path, capsys) -> None:
    f = tmp_path / "log.yaml"
    f.write_text("- id: \"ilog-2026-08-31-004\"\n")
    rc = main(["--kind", "ilog", "--file", str(f), "--date", "2026-08-31"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "ilog-2026-08-31-005"


def test_cli_bad_date_exits_2(tmp_path: Path, capsys) -> None:
    f = tmp_path / "log.yaml"
    rc = main(["--kind", "ilog", "--file", str(f), "--date", "31/08/2026"])
    assert rc == 2
    assert "next_id:" in capsys.readouterr().err
