"""Hygiene guards for the committed rule files under ``superagent/rules/``.

Pins three properties that drifted once and were caught by review:

* no workspace state (dated counters, named timezones, household-specific
  examples) inside committed rules — that content belongs in
  ``workspace/_custom/rules/`` per the Framework Artifact Creation Contract;
* ``rules/_manifest.yaml`` ``consumed_by`` claims are true for the rule
  files whose lists were corrected (a consumer must actually cite the rule);
* ``rules/subagents.md`` carries the user-facing ETA / check-in guidance for
  delegated background work.

Inputs: the rule files and manifest in the repo checkout. No workspace reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

RULES = Path(__file__).resolve().parents[1] / "rules"
REPO = RULES.parents[1]


def _read(name: str) -> str:
    return (RULES / name).read_text(encoding="utf-8")


def test_task_ids_rule_carries_no_frozen_counter() -> None:
    text = _read("task-ids.md")
    assert not re.search(r"[Aa]s of \d{4}-\d{2}-\d{2}", text)
    assert not re.search(r"[Nn]ext (new )?task is TASK-\d{3}", text)
    # The derivation rule replaces the counter.
    assert "highest" in text and "TASK-001" in text


def test_live_todo_rule_uses_config_timezone_not_a_named_zone() -> None:
    text = _read("live-todo.md")
    assert "America/" not in text
    assert not re.search(r"\bP[DS]T\b", text)
    assert "preferences.timezone" in text
    # rules/token-economy.md exempts this line from AP-11 by name; keep it.
    assert "read `_memory/todo.yaml` in full" in text


def test_live_todo_example_is_generic() -> None:
    text = _read("live-todo.md")
    for token in ("CA OTPA", "Form 2918", "ftb.ca.gov", "MyFTB"):
        assert token not in text, f"household-specific example leaked: {token}"


def test_live_todo_points_at_sibling_rules() -> None:
    text = _read("live-todo.md")
    assert "rules/task-ids.md" in text
    assert "rules/markdown-formatting.md" in text


def test_manifest_task_ids_purpose_has_no_dated_counter() -> None:
    manifest = yaml.safe_load((RULES / "_manifest.yaml").read_text(encoding="utf-8"))
    by_path = {f["path"]: f for f in manifest["files"]}
    assert "as of" not in by_path["task-ids.md"]["purpose"].lower()
    assert not re.search(r"TASK-\d{3}\.", by_path["task-ids.md"]["purpose"])


def test_manifest_consumed_by_claims_are_true_for_corrected_entries() -> None:
    manifest = yaml.safe_load((RULES / "_manifest.yaml").read_text(encoding="utf-8"))
    by_path = {f["path"]: f for f in manifest["files"]}
    for rule in ("live-todo.md", "markdown-formatting.md", "task-ids.md", "skill-discipline.md"):
        stem = Path(rule).stem
        for consumer in by_path[rule].get("consumed_by") or []:
            target = REPO / consumer if consumer in ("AGENTS.md", "CLAUDE.md") else REPO / "superagent" / consumer
            assert target.exists(), f"{rule}: consumer {consumer} does not exist"
            assert stem in target.read_text(encoding="utf-8"), f"{rule}: {consumer} never cites {stem}"


def test_subagents_rule_has_eta_and_checkin_guidance() -> None:
    text = _read("subagents.md")
    assert "## Delegated work: ETA and check-ins" in text
    section = text.split("## Delegated work: ETA and check-ins", 1)[1].split("\n## ", 1)[0]
    assert "2x" in section
    assert "rules/token-economy.md" in section
    assert "asap" in section
    assert "polling loop" in section
