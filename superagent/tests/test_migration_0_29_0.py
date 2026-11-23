# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.29.0 migration (browserctl doctor): a version step with no data to convert."""
from __future__ import annotations

from pathlib import Path

import yaml

from superagent.tools.version import current_version, find_chain, parse


def test_migration_is_registered_and_converts_nothing(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.29.0")
    assert row["from_version"] == "0.28.1" and row["revertible"] is True and row["breaking"] is False
    assert not (framework_dir / "migrations" / "0.29.0").exists(), "no helper script: nothing to convert"
    assert parse(current_version()) >= parse("0.29.0")
    assert [step.to_version for step in find_chain("0.28.1", "0.29.0")] == ["0.29.0"]
    assert [step.to_version for step in find_chain("0.28.0", "0.29.0")] == ["0.29.0"]
    assert [step.to_version for step in find_chain("0.27.0", "0.29.0")] == ["0.28.0", "0.29.0"]


def test_the_step_touches_the_version_stamp_and_nothing_else(framework_dir: Path) -> None:
    text = (framework_dir / "migrations" / "0.29.0.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    assert front["touches"] == ["workspace/.version"]
    assert front["helper_scripts"] == {}
