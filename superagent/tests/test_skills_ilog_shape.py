# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Hygiene test: skill files only show the canonical interaction-log row shape.

`templates/memory/interaction-log.yaml` and `contracts/events-stream.md`
§ "Canonical row shape" retire the legacy `timestamp` / `type` / `subject`
row. Every skill that quotes an interaction-log row must show the canonical
`id` / `ts` / `skill` / `action` shape so the agent never copies the legacy
form into a fresh row. `bills.md` was the last holdout; this test pins the
whole catalogue. Since 0.24.0 it also pins HOW a row is written: through
`signals log`, never by appending to a named log file.
"""
from __future__ import annotations

import re
from pathlib import Path

# Legacy-only keys at the row / field indent of a YAML block inside a skill.
LEGACY_ROW_RE = re.compile(r"^\s*-?\s*(timestamp|type|subject):", re.MULTILINE)
CANONICAL_KEYS = (
    "id:", "ts:", 'skill: "bills"', 'action: "reconcile_transactions"',
    "related_domain: finances", "related_project: null", "related_asset: null",
    "related_account: null", "action_items:", "ingestion_log_ref: null",
)


def _yaml_blocks(body: str) -> list[str]:
    """Return the contents of every fenced ```yaml block in a markdown body."""
    return re.findall(r"```yaml\s*\n(.*?)```", body, re.DOTALL)


def test_no_skill_shows_legacy_skill_run_row(framework_dir: Path) -> None:
    offenders: list[str] = []
    for path in sorted((framework_dir / "skills").glob("*.md")):
        body = path.read_text()
        if "type: skill_run" in body:
            offenders.append(path.name)
    assert not offenders, f"legacy `type: skill_run` ilog row in: {offenders}"


def test_bills_reconcile_row_is_canonical(framework_dir: Path) -> None:
    body = (framework_dir / "skills" / "bills.md").read_text()
    sync = body.split("## 2. Sync downstream", 1)[1].split("\n## ", 1)[0]
    blocks = _yaml_blocks(sync)
    assert blocks, "bills.md § 2 must quote an interaction-log row"
    row = blocks[0]
    for key in CANONICAL_KEYS:
        assert key in row, f"bills.md § 2 ilog row missing canonical field `{key}`"
    assert not LEGACY_ROW_RE.search(row), (
        "bills.md § 2 ilog row still carries legacy timestamp/type/subject keys"
    )
    assert "minted by signals log" in row, "the id is minted by `signals log`, not by hand"


# Since 0.24.0 the interaction log is one file per machine. Skills never name a
# log file: they log through `signals log` and read through `signals log-show`
# (rules/concurrent-sessions.md). The row-shape template keeps its path.
SINGLE_LOG_FILE_RE = re.compile(r"(?<!templates/memory/)interaction-log\.yaml")
HAND_MINTED_ID = "next_id --kind ilog"


def test_no_skill_names_the_single_log_file_or_mints_a_log_id(framework_dir: Path) -> None:
    offenders: dict[str, list[int]] = {}
    for path in sorted((framework_dir / "skills").glob("*.md")):
        if ".sync-conflict-" in path.name:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if SINGLE_LOG_FILE_RE.search(line) or HAND_MINTED_ID in line:
                offenders.setdefault(path.name, []).append(number)
    assert not offenders, (
        "skills must log through `signals log` and read through `signals log-show`, "
        f"never name `interaction-log.yaml` or mint an `ilog-` id by hand: {offenders}"
    )


def test_skills_that_log_point_at_the_writer(framework_dir: Path) -> None:
    """Every skill that quotes a log row tells the agent which tool writes it."""
    missing = []
    for path in sorted((framework_dir / "skills").glob("*.md")):
        body = path.read_text()
        if "minted by signals log" in body and "signals log`" not in body:
            missing.append(path.name)
    assert not missing, f"log row quoted without naming `signals log`: {missing}"
