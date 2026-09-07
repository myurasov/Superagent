# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/domains.py` index stamping on first materialization.

When `ensure_folder` first creates `Domains/<Name>/`, the matching
`_memory/domains-index.yaml` row gets `created` (only if null) and
`last_updated` stamped. Repeat calls must leave the index byte-identical,
and the stamp itself must be surgical: the hand-curated header banner,
schema comments and every other row survive byte-for-byte.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from superagent.tools.domains import (
    domains_index_path,
    ensure_folder,
    load_domains_index,
    lookup_domain,
    stamp_index_row,
)

ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
HEADER_BANNER = "# [Do not change manually"

# Synthetic hand-curated index: mixed id quoting, a row missing both
# timestamp keys, a row with a real `created`, flow-style provenance and
# comments everywhere a human might leave them.
SYNTHETIC_INDEX = """\
# [Do not change manually — managed by Superagent]
# synthetic fixture header

schema_version: 1

domains:
  # per-row schema notes live here
  - id: garden
    name: "Garden"
    provenance: { source: "user", at: null }
    tags: ["custom"]
  # a comment between rows
  - id: "workshop"
    name: "Workshop"
    created: "2025-01-02T03:04:05+00:00"
    tags: ["custom"]
    notes: ""
"""


def _row(workspace: Path, domain_id: str) -> dict:
    row = lookup_domain(workspace, domain_id)
    assert row is not None, f"{domain_id} must stay registered"
    return row


def _write_index(workspace: Path, text: str) -> Path:
    path = domains_index_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _changed_lines(before: str, after: str) -> list[tuple[int, str, str]]:
    """Line-wise diff of two equal-length texts: (index, before, after)."""
    b_lines = before.splitlines(keepends=True)
    a_lines = after.splitlines(keepends=True)
    assert len(a_lines) == len(b_lines), "line count must not change"
    return [(i, b, a) for i, (b, a) in enumerate(zip(b_lines, a_lines, strict=True)) if b != a]


def test_first_ensure_stamps_created_and_last_updated(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    before = _row(initialized_workspace, "health")
    assert before.get("created") is None
    assert before.get("last_updated") is None

    assert ensure_folder(initialized_workspace, framework_dir, "health") is True

    after = _row(initialized_workspace, "health")
    assert isinstance(after["created"], str)
    assert ISO_WITH_OFFSET.match(after["created"]), after["created"]
    assert after["last_updated"] == after["created"]


def test_second_ensure_leaves_index_untouched(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    index = domains_index_path(initialized_workspace)
    assert ensure_folder(initialized_workspace, framework_dir, "pets") is True
    snapshot = index.read_bytes()

    assert ensure_folder(initialized_workspace, framework_dir, "pets") is False
    assert index.read_bytes() == snapshot, "no-op ensure must not rewrite the index"


def test_existing_created_is_preserved(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    original = "2025-01-02T03:04:05+00:00"
    data = load_domains_index(initialized_workspace)
    for row in data["domains"]:
        if row["id"] == "travel":
            row["created"] = original
    with domains_index_path(initialized_workspace).open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    assert ensure_folder(initialized_workspace, framework_dir, "travel") is True

    after = _row(initialized_workspace, "travel")
    assert after["created"] == original
    assert ISO_WITH_OFFSET.match(after["last_updated"]), after["last_updated"]
    assert after["last_updated"] != original


def test_stamp_preserves_other_rows_and_key_order(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    index = domains_index_path(initialized_workspace)
    before_text = index.read_text(encoding="utf-8")
    assert before_text.startswith(HEADER_BANNER)
    before = load_domains_index(initialized_workspace)
    before_rows = {r["id"]: r for r in before["domains"]}

    assert ensure_folder(initialized_workspace, framework_dir, "home") is True

    after = load_domains_index(initialized_workspace)
    after_rows = {r["id"]: r for r in after["domains"]}
    assert list(after.keys()) == list(before.keys())
    assert list(after_rows) == list(before_rows), "row order must be preserved"
    for domain_id, row in before_rows.items():
        if domain_id != "home":
            assert after_rows[domain_id] == row, f"{domain_id} row must be untouched"
    assert list(after_rows["home"].keys()) == list(before_rows["home"].keys())
    stamped = ("created", "last_updated")
    unchanged = {k: v for k, v in before_rows["home"].items() if k not in stamped}
    assert {k: after_rows["home"][k] for k in unchanged} == unchanged

    # Byte level: the header banner survives, and exactly the two timestamp
    # lines of the `home` block changed — every other byte is identical.
    after_text = index.read_text(encoding="utf-8")
    assert after_text.startswith(HEADER_BANNER), "hand-curated header must survive"
    assert after_text.count("#") == before_text.count("#"), "no comment may be lost"
    changed = _changed_lines(before_text, after_text)
    assert [b for _, b, _ in changed] == [
        "    created: null\n",
        "    last_updated: null\n",
    ]
    for _, _, a in changed:
        assert re.fullmatch(r'    (created|last_updated): "[^"]+"\n', a), a
        assert ISO_WITH_OFFSET.match(a.split('"')[1]), a
    lines = before_text.splitlines()
    home_start = lines.index('  - id: "home"')
    home_end = next(i for i in range(home_start + 1, len(lines)) if lines[i].startswith("  - id:"))
    assert all(home_start < i < home_end for i, _, _ in changed), "stamps must land in the home block"
    assert not index.with_suffix(".yaml.tmp").exists()


def test_stamp_surgical_unquoted_id_inserts_missing_keys(fresh_workspace: Path) -> None:
    index = _write_index(fresh_workspace, SYNTHETIC_INDEX)
    ts = "2026-05-06T07:08:09+02:00"

    assert stamp_index_row(fresh_workspace, "garden", now=ts) is True

    after = index.read_text(encoding="utf-8")
    expected = SYNTHETIC_INDEX.replace(
        "  - id: garden\n",
        f'  - id: garden\n    created: "{ts}"\n    last_updated: "{ts}"\n',
    )
    assert after == expected, "only two lines may be inserted, right after the id line"
    row = _row(fresh_workspace, "garden")
    assert row["created"] == ts and row["last_updated"] == ts
    assert list(row) == ["id", "created", "last_updated", "name", "provenance", "tags"]


def test_stamp_surgical_keeps_created_and_appends_last_updated(fresh_workspace: Path) -> None:
    index = _write_index(fresh_workspace, SYNTHETIC_INDEX)
    ts = "2026-05-06T07:08:09+02:00"

    assert stamp_index_row(fresh_workspace, "workshop", now=ts) is True

    after = index.read_text(encoding="utf-8")
    original = '    created: "2025-01-02T03:04:05+00:00"\n'
    assert after == SYNTHETIC_INDEX.replace(
        original, original + f'    last_updated: "{ts}"\n'
    ), "existing created stays; last_updated slots in right after it"
    assert _row(fresh_workspace, "garden") == {
        "id": "garden",
        "name": "Garden",
        "provenance": {"source": "user", "at": None},
        "tags": ["custom"],
    }


def test_stamp_restamps_last_updated_but_not_created(fresh_workspace: Path) -> None:
    index = _write_index(fresh_workspace, SYNTHETIC_INDEX)
    first, second = "2026-05-06T07:08:09+02:00", "2026-06-07T08:09:10+02:00"
    stamp_index_row(fresh_workspace, "garden", now=first)
    snapshot = index.read_text(encoding="utf-8")

    assert stamp_index_row(fresh_workspace, "garden", now=second) is True

    changed = _changed_lines(snapshot, index.read_text(encoding="utf-8"))
    assert [(b, a) for _, b, a in changed] == [
        (f'    last_updated: "{first}"\n', f'    last_updated: "{second}"\n'),
    ]


def test_stamp_falls_back_to_dump_when_row_is_not_block_style(fresh_workspace: Path) -> None:
    text = (
        "# [Do not change manually — managed by Superagent]\n"
        "# flow-style rows cannot be edited line-wise\n"
        "\n"
        "schema_version: 1\n"
        "domains:\n"
        '  - { id: "garden", name: "Garden", created: null, last_updated: null }\n'
        '  - { id: "workshop", name: "Workshop", created: "2025-01-02", last_updated: null }\n'
    )
    index = _write_index(fresh_workspace, text)
    ts = "2026-05-06T07:08:09+02:00"

    assert stamp_index_row(fresh_workspace, "garden", now=ts) is True

    after = index.read_text(encoding="utf-8")
    assert after.startswith(
        "# [Do not change manually — managed by Superagent]\n"
        "# flow-style rows cannot be edited line-wise\n"
        "\n"
        "schema_version: 1\n"
    ), "fallback dump must keep the leading comment header"
    data = yaml.safe_load(after)
    assert data["schema_version"] == 1
    rows = {r["id"]: r for r in data["domains"]}
    assert rows["garden"]["created"] == ts and rows["garden"]["last_updated"] == ts
    assert rows["workshop"] == {
        "id": "workshop", "name": "Workshop", "created": "2025-01-02", "last_updated": None,
    }
    assert not index.with_suffix(".yaml.tmp").exists()


def test_stamp_index_row_explicit_now_and_unknown_id(
    initialized_workspace: Path,
) -> None:
    ts = "2026-03-04T05:06:07-07:00"
    assert stamp_index_row(initialized_workspace, "career", now=ts) is True
    row = _row(initialized_workspace, "career")
    assert row["created"] == ts
    assert row["last_updated"] == ts

    index = domains_index_path(initialized_workspace)
    snapshot = index.read_bytes()
    assert stamp_index_row(initialized_workspace, "no-such-domain-xyz") is False
    assert index.read_bytes() == snapshot
