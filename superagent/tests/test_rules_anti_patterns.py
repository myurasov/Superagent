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
        # 0.18.0 — the three decision-table / floor items AP-11 did not cover.
        "AP-14": [
            "Read every partition under `_memory/events/` to answer the timeline question.",
            "Load all quarterly partitions and merge the rows before filtering.",
            "Scan each partition of the event stream for the vet visit.",
            "Read all the `_memory/events/` partitions, then sort by date.",
            "Read all `_memory/events/2026-Q1.yaml`-style partitions into one list.",
            "Open the entire `_memory/events/` directory and merge the files.",
            "For each partition under `_memory/events/`, read it and merge the rows.",
        ],
        "AP-15": [
            "Read the whole `Domains/Health/history.md` to find the last vet visit.",
            "Load `history.md` in full before answering.",
            "Open the entire history.md and scan for the date.",
            "Read the full `Projects/<slug>/history.md` and summarize it.",
            "Read `Domains/Home/history.md` top to bottom for the roof repair date.",
        ],
        "AP-16": [
            "Re-read `_memory/config.yaml` to confirm the edit landed.",
            "After writing, read the file back to verify the YAML parses.",
            "Read `status.md` again to check that the RAG flag flipped.",
            "Open the file you just wrote and confirm the edit landed.",
            "Re-open `todo.yaml` and make sure the new row is present.",
            "read it back to double-check the frontmatter",
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
        # 0.18.0 — sanctioned phrasings lifted from the shipped corpus
        # (events.md, weekly-review.md, ad-hoc-task.md, rules/subagents.md,
        # rules/token-economy.md, init.md, sources.md, supertailor-review.md).
        "AP-14": [
            "Use `uv run python -m superagent.tools.log_window read --since <date>` "
            "(loads only the partitions the window touches).",
            "Never read every partition for a timeline question.",
            "`events stats` -- partition counts; useful after rotation or import.",
            "Do NOT append to partitions directly.",
            "list the current and previous month's partitions (`tasks/<YYYY>/<MM>/`)",
            "Rebuild the derived partitions (mtime-lazy; cheap when nothing changed).",
            "sweeps, old `events/<YYYY-Qn>.yaml` partitions, long `history.md` files).",
            "(`_memory/events/<YYYY-Qn>.yaml`, per `contracts/events-stream.md`) "
            "are both derived data.",
            "Don't read all partitions — `log_window` loads only the window.",
            "Read the current partition only; older quarters stay on disk.",
        ],
        "AP-15": [
            "Read its `history.md` last entry date; a stale date means the domain is dormant.",
            "Grep `history.md` for the entity, then `Read --offset --limit` the matching slice.",
            "never read the whole history.md for a single date",
            "Read `info.md` in full, then append the event to `history.md`.",
            "Append an H4 entry to `history.md` with the full ISO date.",
            "Capture as personal-signal AND as an H4 entry in the just-completed `history.md`.",
            "Don't read the entire history.md — tail the last 20 lines.",
            "Read `history.md`; append the entry. The full row goes at the top.",
        ],
        "AP-16": [
            "Read back the script's stdout to the user as confirmation; surface any errors.",
            "normalize the ref file (`tools/sources_normalize.py apply --mode ask <path>`), "
            "then re-read.",
            "Never re-read your own writes — the Edit result already proves the change landed.",
            "Don't read the file back to verify; the Write result is proof.",
            "Same skill re-reads same files multiple times in a session",
            "redirect to a log under `~/.superagent/tmp/`, read back a filtered slice "
            "(guardrails below).",
            "Re-run the scanner to confirm the corpus is clean.",
            "Read the rendered report to check the layout.",
            "never re-open those files unless editing them.",
            "Read the file the user just created and check its frontmatter.",
        ],
    }
    for rid, texts in cases.items():
        pattern = _pattern_by_id(rid)
        for text in texts:
            assert not pattern.search(text), f"{rid} must not fire on: {text!r}"


def test_token_economy_anti_patterns_clean_on_shipped_skills(
    framework_dir: Path,
) -> None:
    """The shipped skill corpus must be clean of AP-11..AP-16 hits."""
    from superagent.tools.anti_patterns import scan_dir

    by_file = scan_dir(framework_dir / "skills")
    new_ids = {"AP-11", "AP-12", "AP-13", "AP-14", "AP-15", "AP-16"}
    offenders = {
        fname: [h for h in hits if h["pattern"] in new_ids]
        for fname, hits in by_file.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"new anti-patterns fire on shipped skills: {offenders}"


def test_floor_anti_patterns_clean_on_shipped_rules(framework_dir: Path) -> None:
    """AP-14/15/16 must also be clean on `rules/` — that is where the floor
    prohibitions are *stated* (`rules/token-economy.md`, `rules/subagents.md`),
    so a rule that fired on its own prohibition prose would be useless.

    Scoped to the 0.18.0 ids only: the older AP-11 / AP-13 already fire on
    sanctioned rule prose (`rules/live-todo.md`'s mandatory full read, the
    per-message-shard row of the decision table) and were never held to the
    rules corpus."""
    from superagent.tools.anti_patterns import scan_dir

    floor_ids = {"AP-14", "AP-15", "AP-16"}
    offenders = {
        fname: [h for h in hits if h["pattern"] in floor_ids]
        for fname, hits in scan_dir(framework_dir / "rules").items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"floor anti-patterns fire on shipped rules: {offenders}"


def test_new_token_economy_rules_are_warnings_with_floor_citations() -> None:
    """AP-14/15/16 ship as `warning` and each cites rules/token-economy.md."""
    from superagent.tools.anti_patterns import MITIGATIONS, PATTERNS

    by_id = {pid: (sev, desc) for pid, sev, desc, _ in PATTERNS}
    for rid in ("AP-14", "AP-15", "AP-16"):
        severity, description = by_id[rid]
        assert severity == "warning", f"{rid} must be a warning, got {severity!r}"
        assert "rules/token-economy.md" in description, (
            f"{rid} description must cite the floor / decision-table source"
        )
        assert MITIGATIONS[rid].strip(), f"{rid} needs a non-empty mitigation"
