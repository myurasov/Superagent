# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the YAML-driven anti-pattern rule loader."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import yaml


def test_anti_patterns_yaml_loads_into_module_globals(framework_dir: Path) -> None:
    """The catalogue YAML is loaded at import time and matches contracts/anti-patterns.md."""
    from superagent.tools.anti_patterns import MITIGATIONS, PATTERNS

    rules_path = framework_dir / "rules" / "anti-patterns.yaml"
    assert rules_path.exists(), "framework rules YAML must ship at superagent/rules/"
    with rules_path.open() as fh:
        doc = yaml.safe_load(fh)
    expected_ids = {row["id"] for row in doc["rules"]}
    loaded_ids = {pid for pid, _, _, _ in PATTERNS}
    assert loaded_ids == expected_ids, (
        f"compiled patterns ({loaded_ids}) drifted from YAML ({expected_ids})"
    )
    for rid in expected_ids:
        assert MITIGATIONS.get(rid), f"every rule needs a mitigation; {rid} missing"


def test_anti_patterns_user_overlay_extends_framework_rules(
    tmp_path: Path, framework_dir: Path
) -> None:
    """`load_rules` concatenates framework + user-overlay rules."""
    from superagent.tools.anti_patterns import load_rules

    user_rules = tmp_path / "anti-patterns.yaml"
    user_rules.write_text(textwrap.dedent("""
        schema_version: 1
        rules:
          - id: AP-USER-1
            severity: warning
            description: "User-defined rule for testing."
            pattern: 'magic-test-string-XYZ'
            flags: [IGNORECASE]
            mitigation: "Don't write magic-test-string-XYZ."
    """).strip())
    framework_yaml = framework_dir / "rules" / "anti-patterns.yaml"
    patterns, mitigations = load_rules(framework_yaml, user_rules)
    ids = [pid for pid, _, _, _ in patterns]
    assert "AP-USER-1" in ids, "user overlay rule must appear in compiled list"
    assert ids.index("AP-USER-1") > ids.index("AP-1"), (
        "user rules must come AFTER framework rules"
    )
    assert mitigations["AP-USER-1"] == "Don't write magic-test-string-XYZ."


def test_anti_patterns_user_overlay_missing_file_is_silent(
    tmp_path: Path, framework_dir: Path
) -> None:
    """Pointing at a non-existent overlay returns just the framework rules."""
    from superagent.tools.anti_patterns import load_rules

    framework_yaml = framework_dir / "rules" / "anti-patterns.yaml"
    patterns, _ = load_rules(framework_yaml, tmp_path / "does-not-exist.yaml")
    ids = {pid for pid, _, _, _ in patterns}
    assert ids and "AP-1" in ids


def _pattern_by_id(rule_id: str) -> re.Pattern[str]:
    from superagent.tools.anti_patterns import PATTERNS

    for pid, _, _, pattern in PATTERNS:
        if pid == rule_id:
            return pattern
    raise AssertionError(f"rule {rule_id} not found in compiled catalogue")


def test_token_economy_anti_patterns_fire_on_violations() -> None:
    """AP-11/12/13 (0.12.0) must hit the read patterns they were added for."""
    cases = {
        "AP-11": [
            "Read the whole `_memory/todo.yaml` to see what is open.",
            "Load `_memory/interaction-log.yaml` in full before summarizing.",
            "Open the entire transactions.yaml and scan for the merchant.",
            "cat _memory/email/_messages.jsonl and look for the sender",
            "Read `_memory/user-queries.jsonl` in full to find themes.",
            "Read `_memory/email/_messages.jsonl` line by line.",
            "Read the full interaction-log.yaml before summarizing the week.",
            "Read all of `_memory/user-queries.jsonl` to find recurring asks.",
            "Load todo.yaml completely before rendering.",
            "Load the complete transactions.yaml into context.",
            "Pull every row of transactions.yaml and total by category.",
            "Run `cat _memory/todo.yaml` to see what is open.",
        ],
        "AP-12": [
            "Read each domain file one at a time and note the status.",
            "Open the histories one by one, starting with Health.",
            "For each domain, read the whole history.md before deciding.",
            "for each project, open info.md and summarize it",
        ],
        "AP-13": [
            "Read the whole file to find the one field you need.",
            "Load the entire document and check the expiration date.",
            "Read info.md in full to confirm the account number.",
        ],
    }
    for rid, texts in cases.items():
        pattern = _pattern_by_id(rid)
        for text in texts:
            assert pattern.search(text), f"{rid} should fire on: {text!r}"


def test_token_economy_anti_patterns_spare_sanctioned_prose() -> None:
    """AP-11/12/13 must NOT fire on sanctioned read forms or prohibition prose
    (the 0.7.0 release shipped a trigger that over-fired on ordinary text —
    this matrix is the regression guard)."""
    cases = {
        "AP-11": [
            "Read `_memory/todo.yaml`:",
            "Read `workspace/_memory/todo.yaml`. If missing, initialize from the template.",
            "tail-read `_memory/interaction-log.yaml` with a negative offset",
            "grep `_memory/transactions.yaml` for the merchant name",
            "before writing todo.md, read todo.yaml in full per rules/live-todo.md",
            "Per rules/live-todo.md, read `_memory/todo.yaml` in full before regenerating todo.md.",
            "scan `_messages.jsonl` via `superagent.tools.email.archive.find(...)`",
            "read `_messages.jsonl` through `archive.find` / `find_by_query`",
            "never read todo.yaml whole on a browse path",
            "Don't read the whole `todo.yaml` — filter by status in a tool instead.",
            "Never `Read` unbounded memory files whole — `todo.yaml` and `transactions.yaml` are sliced.",
            "Never blindly read `transactions.yaml` whole",
            "Avoid `cat` on `_memory/email/_messages.jsonl`",
            "Read `_memory/config.yaml` in full (it is a singleton snapshot), then slice `_memory/interaction-log.yaml` via `log_window.py`.",
            "Read `_memory/config.yaml` and `_memory/data-sources.yaml` in full, then tail the last 20 rows of `_memory/ingestion-log.yaml`.",
        ],
        "AP-12": [
            "Read `_memory/config.yaml` and `_memory/context.yaml` in one batch.",
            "For each domain, decide whether it is stale.",
            "For each candidate file, grep for the id first.",
            "never read the histories one by one",
            "Don't read each file individually — batch them.",
        ],
        "AP-13": [
            "Read the relevant section of AGENTS.md.",
            "wording passes read the whole artifact before condensing",
            "Read the whole per-message shard — that is the unit of work.",
            "never load the entire file to check the one field",
            "Don't read the whole doc to find the answer — grep it.",
        ],
    }
    for rid, texts in cases.items():
        pattern = _pattern_by_id(rid)
        for text in texts:
            assert not pattern.search(text), f"{rid} must not fire on: {text!r}"


def test_token_economy_anti_patterns_clean_on_shipped_skills(
    framework_dir: Path,
) -> None:
    """The shipped skill corpus must be clean of AP-11/12/13 hits."""
    from superagent.tools.anti_patterns import scan_dir

    by_file = scan_dir(framework_dir / "skills")
    new_ids = {"AP-11", "AP-12", "AP-13"}
    offenders = {
        fname: [h for h in hits if h["pattern"] in new_ids]
        for fname, hits in by_file.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"new anti-patterns fire on shipped skills: {offenders}"
