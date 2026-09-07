"""Shape regressions for ``superagent/skills/vehicle-log.md`` (0.18.0).

``templates/memory/interaction-log.yaml`` is the single normative definition
of the interaction-log row shape, and it forbids new appends in the retired
``timestamp`` / ``type`` / ``subject`` form. The vehicle-log skill's § 4
"Sync downstream" snippet is what the agent copies when it logs a vehicle
event, so this test pins that snippet to the canonical shape: it must parse
as YAML, carry exactly the template's key set, name the skill by its manifest
stem, and route to the ``vehicles`` domain.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "superagent" / "skills" / "vehicle-log.md"
TEMPLATE_PATH = REPO_ROOT / "superagent" / "templates" / "memory" / "interaction-log.yaml"

LEGACY_KEYS = {"timestamp", "type", "subject", "participants"}


def _sync_downstream_section(body: str) -> str:
    """Return the text of the ``## 4. Sync downstream`` section only."""
    match = re.search(r"^## 4\. Sync downstream\n(.*?)(?=^## \d+\.|\Z)", body, re.S | re.M)
    assert match, "vehicle-log.md lost its '## 4. Sync downstream' step"
    return match.group(1)


def _interaction_log_snippet(section: str) -> str:
    """Return the YAML fence that follows the interaction-log.yaml bullet."""
    match = re.search(r"interaction-log\.yaml.*?```yaml\n(.*?)```", section, re.S)
    assert match, "no ```yaml fence after the interaction-log.yaml bullet"
    return match.group(1)


@pytest.fixture(scope="module")
def snippet_row() -> dict:
    body = SKILL_PATH.read_text(encoding="utf-8")
    raw = _interaction_log_snippet(_sync_downstream_section(body))
    # The fence is indented under a list bullet; dedent before parsing.
    dedented = "\n".join(line[2:] if line.startswith("  ") else line for line in raw.splitlines())
    rows = yaml.safe_load(dedented)
    assert isinstance(rows, list) and len(rows) == 1, "snippet must be a one-row YAML list"
    assert isinstance(rows[0], dict)
    return rows[0]


@pytest.fixture(scope="module")
def template_keys() -> set[str]:
    data = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]
    assert isinstance(entries, list) and entries
    return set(entries[0].keys())


def test_snippet_has_no_legacy_keys(snippet_row: dict) -> None:
    assert not (set(snippet_row) & LEGACY_KEYS), (
        f"legacy interaction-log keys in vehicle-log snippet: {set(snippet_row) & LEGACY_KEYS}"
    )


def test_snippet_matches_template_key_set(snippet_row: dict, template_keys: set[str]) -> None:
    assert set(snippet_row) == template_keys


def test_snippet_names_skill_by_stem_and_routes_to_vehicles(snippet_row: dict) -> None:
    assert snippet_row["skill"] == "vehicle-log"
    assert snippet_row["action"].startswith("log_")
    assert snippet_row["related_domain"] == "vehicles"
    assert snippet_row["related_asset"], "related_asset must point at the vehicle slug"
    assert snippet_row["ingestion_log_ref"] is None
    assert isinstance(snippet_row["action_items"], list)


def test_skill_body_has_no_legacy_shape_anywhere() -> None:
    body = SKILL_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*- timestamp:", body, re.M)
    assert not re.search(r"^\s*type: maintenance", body, re.M)
