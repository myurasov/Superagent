# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.28.0 migration (the start skill): a version step with no data to convert."""
from __future__ import annotations

from pathlib import Path

import yaml

from superagent.tools import autocode
from superagent.tools.version import current_version, find_chain, parse, set_workspace_version


def test_migration_is_registered_and_converts_nothing(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.28.0")
    assert row["from_version"] == "0.27.0" and row["revertible"] is True and row["breaking"] is False
    assert not (framework_dir / "migrations" / "0.28.0").exists(), "no helper script: nothing to convert"
    assert parse(current_version()) >= parse("0.28.0")
    assert [step.to_version for step in find_chain("0.27.0", "0.28.0")] == ["0.28.0"]
    assert [step.to_version for step in find_chain("0.26.0", "0.28.0")] == ["0.27.0", "0.28.0"]


def test_a_workspace_from_the_version_before_reads_as_is(initialized_workspace: Path) -> None:
    """The step's Validate: the setup state reads on a workspace still stamped 0.27.0, and writes nothing."""
    ws = initialized_workspace
    set_workspace_version(ws, "0.27.0")
    before = {p: p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}
    assert autocode.main(["setup", "--workspace", str(ws)]) == 0
    assert {p: p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()} == before
