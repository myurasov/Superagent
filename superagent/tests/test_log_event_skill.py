# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Shape tests for `superagent/skills/log-event.md`.

The **interaction** sub-flow appends a row to `_memory/interaction-log.yaml`.
`templates/memory/interaction-log.yaml` is the single normative definition of
that row shape; the skill's example row must use the canonical keys and must
not instruct the agent to write the retired legacy keys (`timestamp`, `type`,
`subject`, `participants`).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "log-event.md"
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "templates" / "memory" / "interaction-log.yaml"
)

LEGACY_KEYS = {"timestamp", "type", "subject", "participants"}
REQUIRED_KEYS = {
    "id", "ts", "skill", "action", "summary", "related_domain", "action_items",
}

_FENCE_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _yaml_blocks(text: str) -> list[str]:
    """Return the dedented bodies of every fenced ```yaml block in `text`."""
    blocks: list[str] = []
    for match in _FENCE_RE.finditer(text):
        lines = match.group(1).splitlines()
        indent = min(
            (len(line) - len(line.lstrip()) for line in lines if line.strip()),
            default=0,
        )
        blocks.append("\n".join(line[indent:] for line in lines))
    return blocks


def _interaction_row() -> dict:
    """Locate and parse the interaction sub-flow's example interaction-log row."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    for block in _yaml_blocks(text):
        if 'skill: "log-event"' not in block:
            continue
        parsed = yaml.safe_load(block)
        assert isinstance(parsed, list) and len(parsed) == 1, block
        assert isinstance(parsed[0], dict), block
        return parsed[0]
    raise AssertionError("log-event.md has no interaction-log example row with skill: log-event")


def test_interaction_row_uses_canonical_shape() -> None:
    """The example row carries every canonical key and no legacy key."""
    row = _interaction_row()
    keys = set(row)
    missing = REQUIRED_KEYS - keys
    assert not missing, f"canonical keys missing from log-event interaction row: {sorted(missing)}"
    leaked = LEGACY_KEYS & keys
    assert not leaked, f"legacy keys present in log-event interaction row: {sorted(leaked)}"
    assert row["skill"] == "log-event"
    assert row["ingestion_log_ref"] is None


def test_interaction_row_keys_are_subset_of_template_keys() -> None:
    """Every key in the skill's example row exists on the template's canonical row."""
    template = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    template_keys = set(template["entries"][0])
    extra = set(_interaction_row()) - template_keys
    assert not extra, f"log-event row uses keys the template does not define: {sorted(extra)}"


def test_no_yaml_block_in_skill_writes_legacy_keys() -> None:
    """No fenced YAML example anywhere in the skill uses a retired top-level key."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    offenders: list[str] = []
    for block in _yaml_blocks(text):
        for line in block.splitlines():
            match = re.match(r"^(?:- )?(\w+):", line)
            if match and match.group(1) in LEGACY_KEYS:
                offenders.append(line.strip())
    assert not offenders, f"legacy interaction-log keys in log-event.md examples: {offenders}"


def test_vehicle_and_home_events_are_handled_inline() -> None:
    """0.21.0 retired `vehicle-log` and `home-maintenance`; a vehicle or home
    event is a generic `history.md` append inside this skill, never a
    delegation to a skill file that no longer exists."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "vehicle-log" not in text
    assert "home-maintenance" not in text
    assert "Domains/Vehicles/history.md" in text
    assert "Domains/Home/history.md" in text


def test_prose_does_not_reference_timestamp_field() -> None:
    """The rolodex `Last contacted` step points at `ts`, not the retired `timestamp`."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    last_contacted = [line for line in text.splitlines() if "Last contacted" in line]
    assert last_contacted, "expected a `Last contacted` step in the interaction sub-flow"
    for line in last_contacted:
        assert "`ts`" in line, line
        assert "timestamp" not in line, line
