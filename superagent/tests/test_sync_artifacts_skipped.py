# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Scans of registries, indexes and stores skip what a folder-sync tool leaves behind.

A conflict copy (`<stem>.sync-conflict-<date>-<time>-<device>.<ext>`) of a
watcher ref must not become a second watcher, a copy of a skill must not
become a second skill, a copy of a memory file is not a memory file, and a
file still arriving (`.syncthing.<name>.tmp`) is not a document yet. Also
covered: the readers that take the per-machine interaction-log files.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from superagent.tools import build_skill_manifest, events_derive, sources_index, validate
from superagent.tools import watchlist as wl
from superagent.tools.log_window import events_root, load_yaml, partition_path

MARK = "sync-conflict-20261008-101010-ABCDEFG"
REF = "---\nref_version: 2\ntitle: Bank\nwatch:\n  type: url\n  url: https://example.org/\n---\n"


def test_a_conflict_copy_of_a_ref_is_not_a_second_watcher(tmp_path: Path) -> None:
    registry = tmp_path / "ws" / "Sources" / "Watchlist"
    registry.mkdir(parents=True)
    (registry / "Bank.ref.md").write_text(REF)
    (registry / f"Bank.ref.{MARK}.md").write_text(REF)
    (registry / f"Bank.{MARK}.ref.md").write_text(REF)
    (registry / ".syncthing.Card.ref.md.tmp").write_text(REF)
    (registry / "README.md").write_text("# registry\n")
    assert [p.name for p in wl.registry_files(registry)] == ["Bank.ref.md"]
    assert [p.name for p in validate.watch_ref_files(registry)] == ["Bank.ref.md"]
    assert wl.registry_stray_files(registry) == [], "sync leftovers are the sweep's to report"
    assert not validate.is_ref_name(f"Bank.{MARK}.ref.md")


def test_documents_scan_leaves_sync_files_out(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    folder = ws / "Sources" / "Bank"
    folder.mkdir(parents=True)
    (folder / "statement.pdf").write_bytes(b"%PDF")
    (folder / "statement.pdf.meta.md").write_text("---\ntitle: Statement\n---\n")
    (folder / f"statement.{MARK}.pdf").write_bytes(b"%PDF other")
    (folder / f"statement.pdf.meta.{MARK}.md").write_text("---\ntitle: Other\n---\n")
    (folder / ".syncthing.scan.pdf.tmp").write_bytes(b"partial")
    (folder / "~syncthing~scan2.pdf.tmp").write_bytes(b"partial")
    names = sorted(p.name for p in sources_index.walk_sources(ws))
    assert names == ["statement.pdf", "statement.pdf.meta.md"]


def test_a_conflict_copy_of_a_skill_is_not_a_skill(tmp_path: Path) -> None:
    framework = tmp_path / "superagent"
    skills = framework / "skills"
    skills.mkdir(parents=True)
    body = "---\nname: superagent-demo\ndescription: Demo.\ntriggers:\n  - demo\n---\n\n# Demo\n"
    (skills / "demo.md").write_text(body)
    (skills / f"demo.{MARK}.md").write_text(body)
    custom = tmp_path / "ws" / "_custom" / "skills"
    custom.mkdir(parents=True)
    (custom / "mine.md").write_text(body.replace("demo", "mine"))
    (custom / f"mine.{MARK}.md").write_text(body.replace("demo", "mine"))
    rows = build_skill_manifest.collect_skills(framework, tmp_path / "ws")
    assert len(rows) == 2 and sorted(r["origin"] for r in rows) == ["custom", "framework"]
    assert validate.skill_stems(framework, tmp_path / "ws") == {"demo", "mine"}


def test_validate_reads_each_machines_log_and_skips_conflict_copies(initialized_workspace: Path,
                                                                    capsys) -> None:
    memory = initialized_workspace / "_memory"
    (memory / f"todo.{MARK}.yaml").write_text("this: [is not valid yaml\n")
    machine_log = memory / "interaction-log" / "laptop.yaml"
    machine_log.write_text(
        "schema_version: 1\nentries:\n"
        "  - id: ilog-2026-10-08-laptop-001\n    ts: '2026-10-08T10:00:00-07:00'\n"
        "    skill: not-a-skill\n    action: x\n    summary: y\n")
    (memory / "interaction-log" / f"laptop.{MARK}.yaml").write_text("also: [broken\n")
    (memory / "interaction-log" / "_frozen.yaml").write_text("schema_version: 1\ninteraction_log: {rows: 0}\n")
    names = [p.relative_to(memory).as_posix() for p in validate.memory_yaml_files(memory)]
    assert "interaction-log/laptop.yaml" in names
    assert not any("sync-conflict" in n or "_frozen" in n for n in names)
    assert validate.main(["--workspace", str(initialized_workspace)]) == 0
    out = capsys.readouterr().out
    assert "OK     interaction-log/laptop.yaml" in out
    assert "WARN   interaction-log/laptop.yaml: 1 row(s) use a 'skill' value that is not a skill stem" in out
    assert "sync-conflict" not in out and "parse failed" not in out
    machine_log.write_text("schema_version: 1\nentries: {not: a list}\nextra_key: 1\n")
    assert validate.main(["--workspace", str(initialized_workspace)]) == 1
    out = capsys.readouterr().out
    assert "interaction-log/laptop.yaml: unexpected top-level keys ['extra_key']" in out
    assert "interaction-log/laptop.yaml: top-level 'entries' must be a list" in out


def _events_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "_memory" / "events").mkdir(parents=True)
    (ws / "_memory" / "interaction-log").mkdir()
    (ws / "_memory" / "config.yaml").write_text(yaml.safe_dump({
        "preferences": {"events": {"partition": "quarterly", "mode": "derived", "skip_kinds": []}}}))
    (ws / "_memory" / "interaction-log.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "entries": [{"id": "ilog-2026-10-08-001", "ts": "2026-10-08T09:00:00-07:00", "skill": "bills",
                     "action": "mark_bill_paid", "summary": "Paid the water bill."}]}, sort_keys=False))
    for name, hour in (("laptop", 10), ("desk", 11)):
        (ws / "_memory" / "interaction-log" / f"{name}.yaml").write_text(yaml.safe_dump({
            "schema_version": 1,
            "entries": [{"id": f"ilog-2026-10-08-{name}-001", "ts": f"2026-10-08T{hour}:00:00-07:00",
                         "skill": "todo", "action": "add_task", "summary": f"Added on {name}."}]},
            sort_keys=False))
    return ws


def test_events_derive_from_every_machines_log(tmp_path: Path) -> None:
    ws = _events_ws(tmp_path)
    summary = events_derive.rebuild(ws, force=True)
    assert "error" not in summary and "skipped" not in summary
    events = load_yaml(partition_path(ws, "2026-Q4"))["events"]
    assert [e["source"] for e in events] == [
        "interaction-log.yaml#ilog-2026-10-08-001",
        "interaction-log/laptop.yaml#ilog-2026-10-08-laptop-001",
        "interaction-log/desk.yaml#ilog-2026-10-08-desk-001",
    ], "one merged list, oldest first; the anchor names the file the row sits in"
    assert [p.name for p in events_derive.source_files(ws)] == ["interaction-log.yaml", "desk.yaml", "laptop.yaml"]


def test_a_conflict_copy_of_a_partition_is_neither_read_nor_deleted(tmp_path: Path) -> None:
    ws = _events_ws(tmp_path)
    events_derive.rebuild(ws, force=True)
    copy = events_root(ws) / f"2026-Q4.{MARK}.yaml"
    copy.write_text("events:\n  - id: evt-x\n    legacy: true\n    ts: '2030-01-01T00:00:00-07:00'\n")
    stale = events_root(ws) / "2019-Q1.yaml"
    stale.write_text("derived: true\nevents: []\n")
    assert events_derive.partition_files(events_root(ws)) == [stale, partition_path(ws, "2026-Q4")]
    events_derive.rebuild(ws, force=True)
    assert copy.exists(), "left for the conflict sweep to report"
    assert not stale.exists(), "a partition that no longer derives is still removed"
    assert len(load_yaml(partition_path(ws, "2026-Q4"))["events"]) == 3, "the copy's far-future row set no watermark"
