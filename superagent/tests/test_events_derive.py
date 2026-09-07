# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/events_derive.py` (events stream as a derived view)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import yaml

from superagent.tools import events_derive
from superagent.tools.events_derive import check_stale, flag_legacy, rebuild
from superagent.tools.log_window import index_path, load_yaml, partition_path

LEGACY_PARTITION = """\
# hand-authored partition (pre-0.16.0)
quarter: "2026-Q2"
event_count: 2

events:
  - id: "evt-2026-05-13-001"
    legacy: true
    ts: "2026-05-13T04:15:00-07:00"
    kind: "capture_signal"
    actor: "user+agent"
    subject: "CHP citation captured"
    summary: |
      Three citation photos filed by the agent.
      Odd    spacing   and — punctuation preserved.
    entities:
      - "asset:audi-q7-2022"
    tags: ["traffic", "fix-it-ticket"]

  - id: "evt-2026-05-18-001"
    legacy: true
    ts: "2026-05-18T04:59:35-07:00"
    kind: "payment"
    actor: "agent"
    subject: "DMV S5 registration renewal payment submitted"
    summary: |
      DMV order 110747799; $589.00 via direct bank payment.
    entities:
      - "asset:audi-s5-2022"
    payload:
      amount: 589.00
"""

ILOG_NEW_ROW = {
    "id": "ilog-2026-06-08-001",
    "ts": "2026-06-08T10:00:00-07:00",
    "skill": "inbox-scan-import",
    "action": "import_document",
    "summary": "Processed 2 inbox scan PDFs. Filed to Sources/. Created new asset.",
    "related_domain": "vehicles",
    "related_project": None,
    "related_asset": "zero-motorcycle-23t8317",
    "related_account": None,
}

ILOG_NEW_ROW_DEFAULT_KIND = {
    "id": "ilog-2026-06-20-001",
    "ts": "2026-06-20T23:30:00-07:00",
    "skill": "log-event",
    "summary": "User reported insurance reinstated. Confirmed via Gmail thread.",
    "related_domain": "home",
    "related_project": "mortgage-insurance-lapse",
}

ILOG_OLD_ROW = {
    "timestamp": "2026-05-20T08:00:00-07:00",
    "type": "email_sent",
    "subject": "Sent dispute letter to FasTrak",
    "participants": ["Alex"],
    "summary": "Dispute letter for 3 toll notices sent.",
    "related_domain": "vehicles",
}

ILOG_OLD_ROW_PRE_WATERMARK = {
    "timestamp": "2026-05-13T04:15:00-07:00",
    "type": "skill_run",
    "subject": "capture: CHP citation",
    "summary": "Already represented by a legacy event row.",
    "related_domain": "vehicles",
}

HISTORY_MD = """\
# History — Vehicles

## Log

#### 2026-07-05 — Imported PG&E billing history (24 months)

- Pulled 24 statements.
- Filed under Sources/.

#### 2026-05-14 — FasTrak resolved (pre-watermark, must not derive)

Body text.
"""


def build_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "_memory" / "events").mkdir(parents=True)
    (ws / "_memory" / "config.yaml").write_text(yaml.safe_dump({
        "preferences": {"events": {"partition": "quarterly",
                                   "mode": "derived", "skip_kinds": []}},
    }))
    (ws / "_memory" / "interaction-log.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "entries": [ILOG_OLD_ROW_PRE_WATERMARK, ILOG_OLD_ROW,
                    ILOG_NEW_ROW, ILOG_NEW_ROW_DEFAULT_KIND],
    }, sort_keys=False, allow_unicode=True))
    (ws / "_memory" / "events" / "2026-Q2.yaml").write_text(LEGACY_PARTITION)
    hist = ws / "Domains" / "Vehicles" / "history.md"
    hist.parent.mkdir(parents=True)
    hist.write_text(HISTORY_MD)
    return ws


def events_of(ws: Path, quarter: str) -> list[dict]:
    data = load_yaml(partition_path(ws, quarter))
    return data["events"]


def test_new_format_ilog_row_derives(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws)
    q2 = events_of(ws, "2026-Q2")
    ev = next(e for e in q2 if e.get("source") == "interaction-log.yaml#ilog-2026-06-08-001")
    assert ev["kind"] == "source_added"  # action import_document maps
    assert ev["actor"] == "agent"
    assert ev["subject"] == "import_document"
    assert ev["id"].startswith("evt-2026-06-08-")
    assert "domain:vehicles" in ev["entities"]
    assert "asset:zero-motorcycle-23t8317" in ev["entities"]
    # Default kind when no action mapping applies:
    ev2 = next(e for e in q2 if e.get("source") == "interaction-log.yaml#ilog-2026-06-20-001")
    assert ev2["kind"] == "skill_run"
    assert ev2["subject"] == "User reported insurance reinstated."
    assert "project:mortgage-insurance-lapse" in ev2["entities"]


def test_old_format_ilog_row_derives(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws)
    q2 = events_of(ws, "2026-Q2")
    ev = next(e for e in q2 if e.get("kind") == "interaction")
    assert ev["subject"] == "Sent dispute letter to FasTrak"
    assert ev["ts"] == "2026-05-20T08:00:00-07:00"
    assert ev["entities"] == ["domain:vehicles"]
    assert ev["source"].startswith("interaction-log.yaml#")


def test_history_md_entry_derives(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws)
    q3 = events_of(ws, "2026-Q3")
    ev = next(e for e in q3 if e["kind"] == "history_entry")
    assert ev["subject"] == "Imported PG&E billing history (24 months)"
    assert ev["ts"].startswith("2026-07-05T00:00:00")
    assert ev["entities"] == ["domain:vehicles"]
    assert ev["source"].startswith("Domains/Vehicles/history.md#2026-07-05")
    assert "Pulled 24 statements." in ev["summary"]


def test_legacy_rows_preserved_byte_verbatim(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    original_chunks = events_derive.split_partition_items(LEGACY_PARTITION)
    assert len(original_chunks) == 2
    rebuild(ws)
    text = partition_path(ws, "2026-Q2").read_text()
    for chunk in original_chunks:
        assert chunk in text  # exact bytes, including odd spacing
    rows = events_of(ws, "2026-Q2")
    assert [r["id"] for r in rows if r.get("legacy")] == [
        "evt-2026-05-13-001", "evt-2026-05-18-001"]


def test_determinism_rebuild_twice_byte_identical(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws, force=True)
    snapshots = {p.name: p.read_bytes()
                 for p in sorted((ws / "_memory" / "events").glob("*.yaml"))}
    first_state = (load_yaml(index_path(ws)) or {}).get("derive_state")
    rebuild(ws, force=True)
    for p in sorted((ws / "_memory" / "events").glob("*.yaml")):
        assert p.read_bytes() == snapshots[p.name]
    second_state = (load_yaml(index_path(ws)) or {}).get("derive_state")
    # derived_at only bumps on content change.
    assert second_state["derived_at"] == first_state["derived_at"]
    assert second_state["content_hash"] == first_state["content_hash"]


def test_watermark_excludes_pre_cutoff_sources(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws)
    all_rows = events_of(ws, "2026-Q2") + events_of(ws, "2026-Q3")
    # The pre-watermark old-format ilog row (2026-05-13, <= legacy max
    # 2026-05-18T04:59:35) and the 2026-05-14 history entry must NOT derive.
    assert not any("Already represented" in str(r.get("summary")) for r in all_rows)
    assert not any("pre-watermark" in str(r.get("subject")) for r in all_rows)
    # The 2026-05-20 old-format row (after watermark) DID derive.
    assert any(r.get("kind") == "interaction" for r in all_rows)


def test_summary_and_subject_caps(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    long_row = {
        "id": "ilog-2026-06-25-001",
        "ts": "2026-06-25T12:00:00-07:00",
        "skill": "x",
        "summary": "word " * 400,
    }
    ilog = ws / "_memory" / "interaction-log.yaml"
    data = yaml.safe_load(ilog.read_text())
    data["entries"].append(long_row)
    ilog.write_text(yaml.safe_dump(data, sort_keys=False))
    rebuild(ws, force=True)
    ev = next(e for e in events_of(ws, "2026-Q2")
              if e.get("source") == "interaction-log.yaml#ilog-2026-06-25-001")
    assert len(ev["summary"]) <= events_derive.SUMMARY_MAX + 3
    assert ev["summary"].endswith("...")
    assert len(ev["subject"]) <= events_derive.SUBJECT_MAX + 3


def test_check_staleness(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    stale, _ = check_stale(ws)
    assert stale  # never derived
    rebuild(ws)
    stale, _ = check_stale(ws)
    assert not stale
    ilog = ws / "_memory" / "interaction-log.yaml"
    future = dt.datetime.now().timestamp() + 60
    os.utime(ilog, (future, future))
    stale, _ = check_stale(ws)
    assert stale


def test_mtime_lazy_skip_and_force(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    summary = rebuild(ws)
    assert summary["partitions"] == 2
    summary2 = rebuild(ws)
    assert summary2.get("skipped")  # nothing changed -> lazy no-op
    summary3 = rebuild(ws, force=True)
    assert summary3.get("changed") is False


def test_partition_index_updated(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    rebuild(ws)
    idx = load_yaml(index_path(ws))
    partitions = {p["quarter"]: p for p in idx["partitions"]}
    assert set(partitions) == {"2026-Q2", "2026-Q3"}
    q2 = partitions["2026-Q2"]
    assert q2["derived"] is True
    assert q2["legacy_rows"] == 2
    assert q2["event_count"] == 2 + 3  # 2 legacy + old row + 2 new rows
    assert q2["by_kind"]["payment"] == 1
    state = idx["derive_state"]
    assert state["derived_at"] and state["content_hash"]


def test_flag_legacy_idempotent_and_byte_preserving(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    unflagged = LEGACY_PARTITION.replace("    legacy: true\n", "")
    path = partition_path(ws, "2026-Q2")
    path.write_text(unflagged)
    assert flag_legacy(ws, "2026-Q2") == 0
    text = path.read_text()
    assert text.count("    legacy: true\n") == 2
    # Removing the inserted lines restores the original bytes exactly.
    assert text.replace("    legacy: true\n", "") == unflagged
    assert flag_legacy(ws, "2026-Q2") == 0  # second run inserts nothing
    assert path.read_text() == text


def test_h4_suffix_forms_derive(tmp_path: Path) -> None:
    """Time / parenthetical suffixes between date and separator must parse."""
    ws = build_workspace(tmp_path)
    hist = ws / "Projects" / "insurance-saga" / "history.md"
    hist.parent.mkdir(parents=True)
    hist.write_text(
        "# History\n\n"
        "#### 2026-07-10 08:30 PT — Escalation email SENT to agent\n\n"
        "Body A.\n\n"
        "#### 2026-07-11 (later) — Fence-contractor quote received\n\n"
        "Body B.\n\n"
        "#### 2026-07-12 (pm2) — Portal verification done\n\n"
        "Body C.\n")
    summary = rebuild(ws)
    assert summary["unmatched_headers"] == []
    q3 = events_of(ws, "2026-Q3")
    subjects = {e["subject"] for e in q3 if e["kind"] == "history_entry"}
    assert "Escalation email SENT to agent" in subjects
    assert "Fence-contractor quote received" in subjects
    assert "Portal verification done" in subjects
    ev = next(e for e in q3 if e["subject"] == "Fence-contractor quote received")
    assert ev["ts"].startswith("2026-07-11T00:00:00")
    assert ev["entities"] == ["project:insurance-saga"]


def test_unmatched_h4_headers_reported(tmp_path: Path) -> None:
    """Date-led H4 lines that fail to parse are surfaced, never silent."""
    ws = build_workspace(tmp_path)
    hist = ws / "Domains" / "Home" / "history.md"
    hist.parent.mkdir(parents=True)
    hist.write_text("# History\n\n#### 2026-07-15 no separator at all\n\nBody.\n")
    summary = rebuild(ws)
    assert summary["unmatched_headers"] == ["Domains/Home/history.md:3"]
    # The unparseable header derived nothing.
    q3 = events_of(ws, "2026-Q3")
    assert not any("no separator" in str(e.get("subject")) for e in q3)


def test_rebuild_refuses_unflagged_hand_partition(tmp_path: Path) -> None:
    """Never-derived partitions without `legacy: true` rows are not clobbered."""
    ws = build_workspace(tmp_path)
    path = partition_path(ws, "2026-Q2")
    unflagged = LEGACY_PARTITION.replace("    legacy: true\n", "")
    path.write_text(unflagged)
    summary = rebuild(ws)
    assert "error" in summary and "flag-legacy" in summary["error"]
    assert path.read_text() == unflagged  # untouched
    stale, message = check_stale(ws)
    assert stale and "migrate" in message
    # Explicit opt-out still works (and regenerates from sources only).
    summary2 = rebuild(ws, allow_overwrite=True)
    assert summary2["legacy_rows"] == 0
    assert summary2["partitions"] >= 1


def test_source_move_or_delete_detected(tmp_path: Path) -> None:
    """A removed/moved history.md registers as stale even with mtimes unchanged."""
    ws = build_workspace(tmp_path)
    rebuild(ws)
    stale, _ = check_stale(ws)
    assert not stale
    (ws / "Domains" / "Vehicles" / "history.md").unlink()
    stale, message = check_stale(ws)
    assert stale and "file set" in message
    summary = rebuild(ws)  # non-force must not skip
    assert "skipped" not in summary
    # The history-only Q3 partition no longer derives.
    assert not partition_path(ws, "2026-Q3").exists()


def test_mode_off_skips(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    cfg = ws / "_memory" / "config.yaml"
    cfg.write_text(yaml.safe_dump(
        {"preferences": {"events": {"mode": "off"}}}))
    assert "skipped" in rebuild(ws)


def test_skip_kinds_filters_derivation(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    cfg = ws / "_memory" / "config.yaml"
    cfg.write_text(yaml.safe_dump({"preferences": {"events": {
        "mode": "derived", "skip_kinds": ["history_entry"]}}}))
    rebuild(ws)
    assert not partition_path(ws, "2026-Q3").exists()  # only had history rows


def test_log_window_reads_derived_partition(tmp_path: Path) -> None:
    from superagent.tools.log_window import parse_iso_dt, read_window

    ws = build_workspace(tmp_path)
    rebuild(ws)
    rows = list(read_window(
        ws,
        parse_iso_dt("2026-05-01T00:00:00-07:00"),
        parse_iso_dt("2026-08-01T00:00:00-07:00"),
    ))
    kinds = {r["kind"] for r in rows}
    # Legacy + derived rows both readable through the unchanged consumer.
    assert {"payment", "capture_signal", "interaction",
            "history_entry", "skill_run"} <= kinds
    # 2 legacy + 1 old-format + 2 new-format + 1 history entry (2026-07-05).
    assert len(rows) == 6


def test_kind_for_new_row_ingest_requires_exact_stem() -> None:
    """`ingest_run` only for skill == ingest or ingest-<source>; compound text is skill_run."""
    kind = events_derive.kind_for_new_row
    assert kind({"skill": "ingest", "action": "run"}) == "ingest_run"
    assert kind({"skill": "ingest-simplefin", "action": "run"}) == "ingest_run"
    assert kind({"skill": "ingest + log-event (update)", "action": "run"}) == "skill_run"
    assert kind({"skill": "ingestion-review", "action": "run"}) == "skill_run"
    assert kind({"skill": "ingest_csv", "action": "run"}) == "skill_run"
    assert kind({"skill": None, "action": "note"}) == "skill_run"
    # `action` mapping still wins over the skill.
    assert kind({"skill": "ingest", "action": "file_source"}) == "source_added"
