# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.24.0 migration (mixed-machine safety): migrate, validate, revert."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import yaml

from superagent.tools import signals as sg
from superagent.tools.version import set_workspace_version, workspace_version

MIG_DIR = Path(__file__).resolve().parents[1] / "migrations" / "0.24.0"
TS = "2026-10-08T10:00:00-07:00"

CONTEXT = """# header comment
schema_version: 1

# Date (YYYY-MM-DD) the on-open whatsup/daily-update suggestion was last made.
# Caps the stale-context nag at once per calendar day across session restarts.
refresh_suggested: '2026-10-07'

# Short bullet-like strings.
pending_items:
  - call the plumber   # an inline comment survives
last_check: '2026-10-07T08:30:00-07:00'
"""
ILOG = """# old single log
schema_version: 1
entries:
  - timestamp: '2026-05-01T09:00:00-07:00'
    type: note
    subject: a legacy-shape row
  - id: ilog-2026-10-07-001
    ts: '2026-10-07T08:30:00-07:00'
    skill: daily-update
    action: run_daily_briefing
    summary: before the migration
"""
QUERIES = json.dumps({"ts": "2026-10-07T08:29:00-07:00", "source": "cursor", "prompt": "daily update"}) + "\n"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_24_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _old_workspace(tmp_path: Path) -> Path:
    """A workspace as 0.23.0 left it."""
    ws = tmp_path / "workspace"
    memory = ws / "_memory"
    memory.mkdir(parents=True)
    (memory / "config.yaml").write_text("schema_version: 1\npreferences: {}\n")
    (memory / "context.yaml").write_text(CONTEXT)
    (memory / "interaction-log.yaml").write_text(ILOG)
    (memory / "user-queries.jsonl").write_text(QUERIES)
    set_workspace_version(ws, "0.23.0")
    return ws


def _snapshot(ws: Path) -> dict[str, bytes]:
    return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}


def test_migrate_prepares_the_workspace_and_touches_nothing_else(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _snapshot(ws)
    lines: list[str] = []
    assert _load("migrate").run_migration(ws, out=lines.append) == 0
    after = _snapshot(ws)

    for folder in ("interaction-log", "user-queries", "machines"):
        assert (ws / "_memory" / folder).is_dir()
    assert after["_memory/interaction-log.yaml"] == before["_memory/interaction-log.yaml"]
    assert after["_memory/user-queries.jsonl"] == before["_memory/user-queries.jsonl"]
    assert workspace_version(ws) == "0.24.0"

    marker = yaml.safe_load(after["_memory/interaction-log/_frozen.yaml"])
    assert marker["interaction_log"] == {
        "file": "interaction-log.yaml", "rows": 2, "size": len(ILOG.encode()), "last_id": "ilog-2026-10-07-001"}
    assert marker["user_queries"] == {"file": "user-queries.jsonl", "lines": 1, "size": len(QUERIES.encode())}

    context = after["_memory/context.yaml"].decode()
    assert "refresh_suggested" not in context and "on-open whatsup" not in context
    assert context == CONTEXT.replace(
        "# Date (YYYY-MM-DD) the on-open whatsup/daily-update suggestion was last made.\n"
        "# Caps the stale-context nag at once per calendar day across session restarts.\n"
        "refresh_suggested: '2026-10-07'\n\n", ""), "only the retired block is gone"
    ledger = yaml.safe_load(after["_memory/_retired/0.24.0-ledger.yaml"])
    assert "refresh_suggested: '2026-10-07'" in ledger["context"]["removed_block"]

    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {".version", "_memory/context.yaml", "_memory/interaction-log/_frozen.yaml",
                       "_memory/_retired/0.24.0-ledger.yaml"}


def test_migrate_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    mig = _load("migrate")
    before = _snapshot(ws)
    lines: list[str] = []
    assert mig.run_migration(ws, dry_run=True, out=lines.append) == 0
    assert _snapshot(ws) == before and not (ws / "_memory" / "machines").exists()
    assert any("freeze _memory/interaction-log.yaml" in line for line in lines)
    assert mig.run_migration(ws, out=lambda _m: None) == 0
    once = _snapshot(ws)
    lines.clear()
    assert mig.run_migration(ws, out=lines.append) == 0
    assert _snapshot(ws) == once
    assert lines == ["nothing to do; workspace already at 0.24.0 shape"]


def test_migrate_on_a_workspace_without_the_old_files(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "_memory").mkdir(parents=True)
    (ws / "_memory" / "context.yaml").write_text("schema_version: 1\nalerts: []\n")
    set_workspace_version(ws, "0.23.0")
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert not (ws / "_memory" / "interaction-log" / "_frozen.yaml").exists()
    assert not (ws / "_memory" / "_retired").exists()
    assert (ws / "_memory" / "context.yaml").read_text() == "schema_version: 1\nalerts: []\n"
    assert _load("validate").run_validate(ws, out=lambda _m: None) == 0


def test_migrate_refuses_a_broken_context(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    (ws / "_memory" / "context.yaml").write_text("schema_version: 1\nalerts: [unclosed\n")
    lines: list[str] = []
    assert _load("migrate").run_migration(ws, out=lines.append) == 1
    assert "not well-formed YAML" in lines[0]
    assert workspace_version(ws) == "0.23.0" and not (ws / "_memory" / "machines").exists()


def test_validate_passes_after_migrate_and_fails_before(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    val = _load("validate")
    lines: list[str] = []
    assert val.run_validate(ws, out=lines.append) == 1
    assert any(line.startswith("  FAIL") and "interaction-log/ exists" in line for line in lines)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    lines.clear()
    assert val.run_validate(ws, out=lines.append) == 0
    assert lines[-1].endswith("checks passed") and not any("FAIL" in line for line in lines)
    # Rows logged after the migration go to the machine's file; the frozen file is untouched.
    sg.append_interaction_log(ws, skill=None, action="note", summary="after", ts=TS)
    assert val.run_validate(ws, out=lambda _m: None) == 0
    assert (ws / "_memory" / "interaction-log.yaml").read_text() == ILOG


def test_revert_folds_the_per_machine_logs_back(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    memory = ws / "_memory"
    sg.append_interaction_log(ws, skill=None, action="note", summary="here, later", ts="2026-10-08T12:00:00-07:00")
    (memory / "interaction-log" / "laptop.yaml").write_text(
        "schema_version: 1\nentries:\n  - id: ilog-2026-10-08-laptop-001\n"
        "    ts: '2026-10-08T11:00:00-07:00'\n    skill: null\n    action: note\n    summary: laptop, earlier\n")
    (memory / "user-queries" / "testbox.jsonl").write_text(
        json.dumps({"ts": "2026-10-08T12:00:00-07:00", "prompt": "second"}) + "\n")
    (memory / "user-queries" / "laptop.jsonl").write_text(
        json.dumps({"ts": "2026-10-08T11:00:00-07:00", "prompt": "first"}) + "\n")

    rev = _load("revert")
    before = _snapshot(ws)
    assert rev.run_revert(ws, dry_run=True, out=lambda _m: None) == 0
    assert _snapshot(ws) == before, "a dry run writes nothing"
    assert rev.run_revert(ws, out=lambda _m: None) == 0

    assert workspace_version(ws) == "0.23.0"
    assert (memory / "context.yaml").read_text() == CONTEXT, "context.yaml is back byte for byte"
    rows = yaml.safe_load((memory / "interaction-log.yaml").read_text())["entries"]
    assert [r.get("summary") for r in rows[2:]] == ["laptop, earlier", "here, later"], "oldest first"
    assert (memory / "interaction-log.yaml").read_text().startswith(ILOG), "old rows are untouched"
    assert [json.loads(line)["prompt"] for line in (memory / "user-queries.jsonl").read_text().splitlines()] == [
        "daily update", "first", "second"]
    for folder in ("interaction-log", "user-queries", "machines"):
        assert not (memory / folder).exists()
    parked = sorted(p.relative_to(memory / "_retired" / "0.24.0-originals").as_posix()
                    for p in (memory / "_retired" / "0.24.0-originals").rglob("*") if p.is_file())
    assert parked == ["interaction-log/laptop.yaml", "interaction-log/testbox.yaml",
                      "user-queries/laptop.jsonl", "user-queries/testbox.jsonl"]
    assert not (memory / "_retired" / "0.24.0-ledger.yaml").exists()
    after = _snapshot(ws)
    assert rev.run_revert(ws, out=lambda _m: None) == 0
    assert _snapshot(ws) == after, "a second revert changes nothing"


def test_migration_is_registered(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    rows = manifest["migrations"] if isinstance(manifest, dict) else manifest
    row = next(r for r in rows if r["to_version"] == "0.24.0")
    assert row["from_version"] == "0.23.0" and row["revertible"] is True and row["breaking"] is False
