# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.30.0 migration (blocking asks): a version step with no data to convert."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from superagent.tools import asks
from superagent.tools.version import current_version, find_chain, parse

NOW = dt.datetime(2026, 11, 23, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))


def test_migration_is_registered_and_converts_nothing(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.30.0")
    assert row["from_version"] == "0.29.0" and row["revertible"] is True and row["breaking"] is False
    assert not (framework_dir / "migrations" / "0.30.0").exists(), "no helper script: nothing to convert"
    assert parse(current_version()) >= parse("0.30.0")
    assert [step.to_version for step in find_chain("0.29.0", "0.30.0")] == ["0.30.0"]
    assert [step.to_version for step in find_chain("0.28.1", "0.30.0")] == ["0.29.0", "0.30.0"]
    front = yaml.safe_load((framework_dir / "migrations" / "0.30.0.md").read_text().split("---")[1])
    assert front["touches"] == ["workspace/.version"] and front["helper_scripts"] == {}


def test_a_queue_from_the_version_before_reads_as_it_is(initialized_workspace: Path) -> None:
    """The step's Validate: rows without the two new fields list, and close, as plain asks."""
    ws = initialized_workspace
    ask_id, _ = asks.add_ask(ws, agent="bookkeeper", question="Track it as a bill?", now=NOW)
    path = ws / "_memory" / "asks.yaml"
    older = "\n".join(line for line in path.read_text().splitlines()
                      if line.strip() not in ("blocking: false", "inbox: null")) + "\n"
    path.write_text(older)
    assert asks.main(["list", "--open", "--workspace", str(ws), "--now", NOW.isoformat()]) == 0
    assert [r["id"] for r in asks.open_asks(ws, now=NOW)] == [ask_id]
    assert asks.blocking_asks(ws, "bookkeeper", now=NOW) == []
    assert asks.answer_ask(ws, ask_id, answer="yes", now=NOW)["status"] == "answered"
