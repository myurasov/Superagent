# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Shape tests for `skills/health-log.md`.

The skill's § 3 "Sync downstream" step appends a row to
`_memory/interaction-log.yaml`. That file's template is the single
normative definition of the row shape: new appends MUST use the canonical
`id` / `ts` / `skill` / `action` / ... keys and MUST NOT use the retired
`timestamp` / `type` / `subject` keys. These tests pin the skill's example
row to the canonical shape so the legacy residue cannot silently return.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

CANONICAL_KEYS = {
    "id", "ts", "skill", "action", "summary",
    "related_domain", "related_project", "related_asset", "related_account",
    "action_items", "ingestion_log_ref",
}
LEGACY_KEYS = {"timestamp", "type", "subject", "participants"}


def _skill_body(framework_dir: Path) -> str:
    return (framework_dir / "skills" / "health-log.md").read_text(encoding="utf-8")


def _section(body: str, heading: str) -> str:
    """Return the text of one `## N. <heading>` section (up to the next `## `)."""
    match = re.search(
        rf"^## \d+\. {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        body, re.MULTILINE | re.DOTALL,
    )
    assert match, f"section '{heading}' not found in health-log.md"
    return match.group(1)


def _yaml_blocks(text: str) -> list[list[dict]]:
    """Parse every fenced ```yaml block in `text`; each must be a list of rows."""
    blocks = re.findall(r"```yaml\s*\n(.*?)\n\s*```", text, re.DOTALL)
    assert blocks, "no fenced yaml block found"
    parsed: list[list[dict]] = []
    for block in blocks:
        # Fenced blocks inside a list item are indented; dedent before parsing.
        lines = block.splitlines()
        indent = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
        rows = yaml.safe_load("\n".join(ln[indent:] for ln in lines))
        assert isinstance(rows, list) and rows and isinstance(rows[0], dict)
        parsed.append(rows)
    return parsed


def test_sync_downstream_row_uses_canonical_interaction_log_shape(
    framework_dir: Path,
) -> None:
    section = _section(_skill_body(framework_dir), "Sync downstream")
    assert "interaction-log.yaml" in section
    assert "next_id --kind ilog" in section, "id must be minted via next_id"

    (rows,) = _yaml_blocks(section)
    row = rows[0]
    assert set(row) == CANONICAL_KEYS, (
        f"missing={CANONICAL_KEYS - set(row)} extra={set(row) - CANONICAL_KEYS}"
    )
    assert row["skill"] == "health-log", "skill must be the bare manifest stem"
    assert row["action"].startswith("log_")
    assert row["related_domain"] == "health"
    for key in ("related_project", "related_asset", "related_account", "ingestion_log_ref"):
        assert row[key] is None, f"{key} should be null for a health-log row"
    assert row["action_items"] == []


def test_skill_never_writes_legacy_interaction_log_keys(framework_dir: Path) -> None:
    """No yaml block in the skill may carry a legacy interaction-log row.

    `health-records.yaml.symptoms[]` / `vitals[]` legitimately use a
    `timestamp` key of their own, so the check is scoped to blocks that also
    carry the legacy `type` / `subject` discriminators, which only ever
    appeared on interaction-log rows.
    """
    body = _skill_body(framework_dir)
    assert "health_event" not in body, "retired legacy `type: health_event` must not appear"
    for rows in _yaml_blocks(body):
        for row in rows:
            if "type" in row or "subject" in row:
                raise AssertionError(f"legacy interaction-log keys in row: {sorted(row)}")
