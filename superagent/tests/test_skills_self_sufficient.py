# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Skills are self-sufficient: no skill leans on the internal idea catalogues.

A skill is a contract the agent reads before acting. It must be executable
from its own body plus the contracts / rules / tools it cites; it must never
send the reader to the maintainers' internal design notes (`docs/_internal/`),
to the improvement-idea catalogues by name, or to an idea-catalogue entry by
number ("item #N"). Those documents are hand-curated maintainer inputs to the
Supertailor's strategic pass, not runtime instructions, and they move or
renumber freely. 0.21.0 rewrote `skills/world.md` and
`skills/supertailor-review.md` to this rule; this test keeps every skill on
it.

Inputs: `superagent/skills/*.md` in the repo checkout. No workspace reads.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal docs path", re.compile(r"docs/_internal")),
    ("perf-ideas catalogue by name", re.compile(r"perf-improvement-ideas")),
    ("structure-ideas catalogue by name", re.compile(r"ideas-better-structure")),
    ("idea-catalogue entry number", re.compile(r"\bitem #\d+")),
    ("idea-catalogue code (QW-/BB-/MI-)", re.compile(r"\b(QW|BB|MI)-\d+\b")),
    ("supertailor suggestion id", re.compile(r"\bst-\d{3}\b")),
)


def _skill_files(framework_dir: Path) -> list[Path]:
    return sorted(p for p in (framework_dir / "skills").glob("*.md") if not p.name.startswith("_"))


def test_skill_directory_is_non_empty(framework_dir: Path) -> None:
    assert _skill_files(framework_dir), "no skill files found under superagent/skills/"


@pytest.mark.parametrize("label,pattern", FORBIDDEN_PATTERNS, ids=[p[0] for p in FORBIDDEN_PATTERNS])
def test_no_skill_mentions_internal_docs(framework_dir: Path, label: str, pattern: re.Pattern[str]) -> None:
    offenders: list[str] = []
    for path in _skill_files(framework_dir):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        f"skills must be self-sufficient — {label} found in:\n  " + "\n  ".join(offenders)
    )


def test_world_skill_cites_only_its_contract_and_tool(framework_dir: Path) -> None:
    """`world.md` is the reference rewrite: contract + tool, nothing internal."""
    body = (framework_dir / "skills" / "world.md").read_text(encoding="utf-8")
    assert "contracts/world-graph.md" in body
    assert "superagent.tools.world" in body
    # Every command line in the skill must be a real subcommand of tools/world.py.
    real = {"rebuild", "related", "stats", "validate"}
    for match in re.finditer(r"superagent\.tools\.world (\w+)", body):
        assert match.group(1) in real, f"world.md cites unknown subcommand `{match.group(1)}`"
