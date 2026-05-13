# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for the skill catalogue.

Every skill named in the AGENTS.md skill table must exist as a markdown
file under `superagent/skills/`, parse with valid YAML frontmatter,
and declare the four required frontmatter fields.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Skill files that must exist (one per row in AGENTS.md skill table).
EXPECTED_SKILLS = {
    "init", "whatsup", "daily-update", "weekly-review", "monthly-review",
    "todo",
    "add-domain", "add-project", "add-asset", "add-contact", "add-account",
    "add-bill", "add-subscription", "add-appointment", "add-important-date",
    "add-document", "add-source",
    "projects", "sources",
    "log-event", "health-log", "vehicle-log", "home-maintenance", "pet-care",
    "bills", "subscriptions", "appointments", "important-dates",
    "expenses",
    "draft-email", "summarize-thread", "follow-up", "research",
    "ingest",
    "personal-signals", "supertailor-review", "doctor", "triage-overdue", "handoff",
    # Added by the second-pass implementation:
    "inbox-triage", "tags", "decisions", "play", "scenarios",
    "world", "events", "audit",
    # Project-manager-angle review of personal-life Projects.
    "pm-review",
}


def parse_frontmatter(body: str) -> dict | None:
    """Extract the YAML frontmatter from a skill markdown file."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None


def test_every_expected_skill_exists(framework_dir: Path) -> None:
    skills_dir = framework_dir / "skills"
    assert skills_dir.is_dir()
    present = {p.stem for p in skills_dir.glob("*.md")}
    missing = EXPECTED_SKILLS - present
    assert not missing, f"missing skill files: {sorted(missing)}"


def test_every_skill_has_valid_frontmatter(framework_dir: Path) -> None:
    skills_dir = framework_dir / "skills"
    for path in sorted(skills_dir.glob("*.md")):
        body = path.read_text()
        fm = parse_frontmatter(body)
        assert fm is not None, f"{path.name}: no parseable frontmatter"
        assert isinstance(fm, dict), f"{path.name}: frontmatter is not a mapping"
        for field in ("name", "description", "triggers", "mcp_required"):
            assert field in fm, f"{path.name}: missing frontmatter field `{field}`"
        assert fm["name"].startswith("superagent-"), (
            f"{path.name}: skill name should start with 'superagent-' "
            f"(was {fm['name']!r})"
        )
        assert isinstance(fm["triggers"], list), f"{path.name}: triggers must be a list"


def test_no_skill_file_references_other_frameworks(framework_dir: Path) -> None:
    """Skill files should not reference unrelated frameworks."""
    skills_dir = framework_dir / "skills"
    forbidden = ("co-sa", "Co-SA", "CO-SA")
    for path in sorted(skills_dir.glob("*.md")):
        body = path.read_text()
        for term in forbidden:
            assert term not in body, f"{path.name}: contains forbidden reference '{term}'"
