# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Pin the interaction-log row shape documented by ``skills/sources.md``.

``templates/memory/interaction-log.yaml`` is the single normative definition
of an interaction-log row: every new append uses ``id`` / ``ts`` / ``skill`` /
``action`` / ``summary`` / ``related_*`` / ``action_items`` /
``ingestion_log_ref``. The retired ``timestamp`` / ``type`` / ``subject``
keys are read-only history. The ``sources`` skill's "## 3. Logging" section
shipped a legacy-shape snippet after the template switched; this test parses
the fenced YAML under that heading in the real skill file and asserts it
matches the canonical shape so the drift cannot recur.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

CANONICAL_KEYS = {
    "id",
    "ts",
    "skill",
    "action",
    "summary",
    "related_domain",
    "related_project",
    "related_asset",
    "related_account",
    "action_items",
    "ingestion_log_ref",
}
LEGACY_KEYS = {"timestamp", "type", "subject", "participants"}

LOGGING_HEADING_RE = re.compile(r"^## \d+\. Logging\s*$", re.MULTILINE)
NEXT_H2_RE = re.compile(r"^## ", re.MULTILINE)
YAML_FENCE_RE = re.compile(r"^```ya?ml\s*\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _logging_section(body: str) -> str:
    """Return the text of the ``## N. Logging`` section (up to the next H2)."""
    match = LOGGING_HEADING_RE.search(body)
    assert match is not None, "sources.md has no `## N. Logging` section"
    rest = body[match.end():]
    nxt = NEXT_H2_RE.search(rest)
    return rest if nxt is None else rest[: nxt.start()]


@pytest.fixture(scope="module")
def logging_row(framework_dir: Path) -> dict:
    body = (framework_dir / "skills" / "sources.md").read_text(encoding="utf-8")
    section = _logging_section(body)
    fence = YAML_FENCE_RE.search(section)
    assert fence is not None, "Logging section has no fenced ```yaml snippet"
    rows = yaml.safe_load(fence.group(1))
    assert isinstance(rows, list) and len(rows) == 1, "expected a single example row"
    row = rows[0]
    assert isinstance(row, dict)
    return row


def test_logging_snippet_uses_canonical_keys(logging_row: dict) -> None:
    assert set(logging_row) == CANONICAL_KEYS, (
        f"sources.md logging row keys drift from templates/memory/interaction-log.yaml: "
        f"missing={sorted(CANONICAL_KEYS - set(logging_row))} "
        f"extra={sorted(set(logging_row) - CANONICAL_KEYS)}"
    )


def test_logging_snippet_has_no_legacy_keys(logging_row: dict) -> None:
    leaked = LEGACY_KEYS & set(logging_row)
    assert not leaked, f"retired legacy keys present in sources.md logging row: {sorted(leaked)}"


def test_logging_snippet_skill_is_manifest_stem(logging_row: dict) -> None:
    # `skill` must equal the `skills/<stem>.md` filename, with no prefix or qualifiers.
    assert logging_row["skill"] == "sources"


def test_logging_snippet_action_covers_each_read_verb(logging_row: dict) -> None:
    # 0.20.0: the skill is read-only over documents, sidecars and watchers —
    # `open` / `show` / `rescan`. The retired cache verbs (fetch / refresh /
    # evict) must not come back through the logging placeholder.
    action = logging_row["action"]
    assert isinstance(action, str)
    for verb in ("open", "show", "rescan"):
        assert verb in action, f"action placeholder does not cover `{verb}`: {action!r}"
    for retired in ("fetch", "evict"):
        assert retired not in action, f"retired cache verb `{retired}` is back: {action!r}"


def test_logging_snippet_defaults(logging_row: dict) -> None:
    assert logging_row["related_asset"] is None
    assert logging_row["related_account"] is None
    assert logging_row["action_items"] == []
    assert logging_row["ingestion_log_ref"] is None
