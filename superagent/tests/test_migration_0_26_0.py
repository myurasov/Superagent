# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.26.0 migration (update and brief, the asks queue): migrate, validate, revert."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

from superagent.tools import asks, cadence
from superagent.tools.version import set_workspace_version, workspace_version

MIG_DIR = Path(__file__).resolve().parents[1] / "migrations" / "0.26.0"

CONFIG = """# header comment
schema_version: 1
last_updated: null

profile:
  name: ""

preferences:
  # Automation and reminder toggles.
  workspace_path: "workspace"
  cadences:
    daily_update: "08:30"
    weekly_review: "Sunday 18:00"   # an inline comment survives
    doctor: "every 180 days"

  # Anti-pattern scanner.
  anti_patterns:
    scan_skills: true

# --- Data sources ---
data_sources_configured:
  email:
    gmail: false
"""


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_26_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _old_workspace(tmp_path: Path, config: str = CONFIG) -> Path:
    """A 0.25.0-shaped workspace: no update cadences, no update block, no asks queue."""
    ws = tmp_path / "workspace"
    memory = ws / "_memory"
    memory.mkdir(parents=True)
    (memory / "config.yaml").write_text(config, encoding="utf-8")
    (memory / "todo.yaml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")
    (memory / "context.yaml").write_text("schema_version: 1\nlast_runs:\n  daily_update: null\n",
                                         encoding="utf-8")
    set_workspace_version(ws, "0.25.0")
    return ws


def _files(ws: Path) -> dict[str, bytes]:
    return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}


def _prefs(ws: Path) -> dict:
    return yaml.safe_load((ws / "_memory" / "config.yaml").read_text())["preferences"]


def test_migrate_adds_the_keys_and_the_queue_and_touches_nothing_else(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _files(ws)
    out: list[str] = []
    assert _load("migrate").run_migration(ws, out=out.append) == 0
    after = _files(ws)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {"_memory/config.yaml", "_memory/asks.yaml", ".version",
                       "_memory/_retired/0.26.0-ledger.yaml"}
    assert after["_memory/context.yaml"] == before["_memory/context.yaml"], "a hot shared file is left alone"
    prefs = _prefs(ws)
    assert prefs["cadences"] == {"daily_update": "08:30", "weekly_review": "Sunday 18:00",
                                 "doctor": "every 180 days", "update_light": "every 30 minutes",
                                 "update_full": "06:00"}
    assert prefs["update"] == {"drop_folders": []}
    assert prefs["anti_patterns"] == {"scan_skills": True} and prefs["workspace_path"] == "workspace"
    text = (ws / "_memory" / "config.yaml").read_text()
    old_lines = [ln for ln in CONFIG.splitlines() if ln.strip()]
    assert [ln for ln in text.splitlines() if ln in old_lines] == old_lines, "every old line survives, in order"
    assert "# an inline comment survives" in text and text.startswith("# header comment\n")
    assert text.index("update_full:") < text.index("# Anti-pattern scanner."), "the keys sit in the cadences block"
    assert text.index("  update:\n") < text.index("# --- Data sources ---")
    assert asks.load_asks(ws) == [] and "asks:" in (ws / "_memory" / "asks.yaml").read_text()
    assert workspace_version(ws) == "0.26.0"
    assert any("add update_light, update_full" in line for line in out)


def test_migrated_keys_match_the_config_template(tmp_path: Path, framework_dir: Path) -> None:
    ws = _old_workspace(tmp_path)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    template = yaml.safe_load((framework_dir / "templates" / "memory" / "config.yaml").read_text())
    wanted = template["preferences"]
    got = _prefs(ws)
    assert got["update"]["drop_folders"] == wanted["update"]["drop_folders"], (
        "the other keys of the block came with 0.27.0 and its own migration")
    assert {k: got["cadences"][k] for k in ("update_light", "update_full")} == {
        k: wanted["cadences"][k] for k in ("update_light", "update_full")}
    assert (ws / "_memory" / "asks.yaml").read_text() == (
        framework_dir / "templates" / "memory" / "asks.yaml").read_text()


def test_migrate_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    mig = _load("migrate")
    before = _files(ws)
    out: list[str] = []
    assert mig.run_migration(ws, dry_run=True, out=out.append) == 0
    assert _files(ws) == before, "a dry run writes nothing"
    assert any("3 item(s) would be changed" in line for line in out)
    assert mig.run_migration(ws, out=lambda _m: None) == 0
    once = _files(ws)
    out.clear()
    assert mig.run_migration(ws, out=out.append) == 0
    assert _files(ws) == once and out == ["nothing to do; workspace already at 0.26.0 shape"]


def test_migrate_keeps_what_the_workspace_already_has(tmp_path: Path) -> None:
    config = CONFIG.replace('    doctor: "every 180 days"\n',
                            '    doctor: "every 180 days"\n    update_full: "04:15"\n').replace(
        "  # Anti-pattern scanner.\n", "  update:\n    drop_folders: [\"~/Drop\"]\n\n  # Anti-pattern scanner.\n")
    ws = _old_workspace(tmp_path, config)
    (ws / "_memory" / "asks.yaml").write_text("schema_version: 1\nasks:\n  - id: ask-1\n    status: open\n")
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    prefs = _prefs(ws)
    assert prefs["cadences"]["update_full"] == "04:15", "an existing value is never overwritten"
    assert prefs["cadences"]["update_light"] == "every 30 minutes"
    assert prefs["update"] == {"drop_folders": ["~/Drop"]}
    assert yaml.safe_load((ws / "_memory" / "asks.yaml").read_text())["asks"][0]["id"] == "ask-1"
    ledger = yaml.safe_load((ws / "_memory" / "_retired" / "0.26.0-ledger.yaml").read_text())
    assert "inserted_block" not in ledger and "asks_created" not in ledger
    assert "update_light" in ledger["inserted_cadences"] and "update_full" not in ledger["inserted_cadences"]


def test_migrate_respects_a_four_space_indent(tmp_path: Path) -> None:
    config = CONFIG.replace("  ", "    ")
    ws = _old_workspace(tmp_path, config)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    text = (ws / "_memory" / "config.yaml").read_text()
    assert '        update_full: "06:00"' in text and "    update:\n        # Extra folders" in text
    assert _prefs(ws)["update"] == {"drop_folders": []}


def test_migrate_leaves_an_unusual_config_alone_and_still_finishes(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path, "schema_version: 1\npreferences: {workspace_path: workspace}\n")
    before = (ws / "_memory" / "config.yaml").read_text()
    out: list[str] = []
    assert _load("migrate").run_migration(ws, out=out.append) == 0
    assert (ws / "_memory" / "config.yaml").read_text() == before
    assert any("no `preferences.cadences` block" in line for line in out)
    assert workspace_version(ws) == "0.26.0" and (ws / "_memory" / "asks.yaml").is_file()
    assert cadence.cadence_values(ws)["update_full"] == "06:00", "the tools fall back to the template"
    assert all(ok for ok, _m in _load("validate").run_checks(ws))


def test_migrate_refuses_a_broken_config(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path, "preferences:\n  cadences: [unclosed\n")
    before = _files(ws)
    out: list[str] = []
    assert _load("migrate").run_migration(ws, out=out.append) == 1
    assert _files(ws) == before and "not well-formed YAML" in out[0]


def test_validate_passes_after_migrate_and_fails_before(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    validate = _load("validate")
    failed = [m for ok, m in validate.run_checks(ws) if not ok]
    assert any("asks.yaml exists" in m for m in failed) and any(".version reads 0.26.0" in m for m in failed)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    results = validate.run_checks(ws)
    assert all(ok for ok, _m in results), [m for ok, m in results if not ok]
    assert len(results) == 9
    assert validate.main(["--workspace", str(ws)]) == 0


def test_revert_restores_the_workspace_byte_for_byte(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _files(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    out: list[str] = []
    assert _load("revert").run_revert(ws, out=out.append) == 0
    assert _files(ws) == before, out
    assert workspace_version(ws) == "0.25.0"


def test_revert_keeps_what_was_set_since(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    config = ws / "_memory" / "config.yaml"
    config.write_text(config.read_text().replace('update_full: "06:00"', 'update_full: "05:00"').replace(
        "drop_folders: []", 'drop_folders: ["~/Sync/Inbox/Assistant"]'))
    asks.add_ask(ws, agent="updater", question="Pull the feed?")
    out: list[str] = []
    assert _load("revert").run_revert(ws, dry_run=True, out=out.append) == 0
    assert "update_full" in _prefs(ws)["cadences"], "a dry run removes nothing"
    assert _load("revert").run_revert(ws, out=out.append) == 0
    prefs = _prefs(ws)
    assert "update" not in prefs and set(prefs["cadences"]) == {"daily_update", "weekly_review", "doctor"}
    assert config.read_text() == CONFIG, "the config reads as it did before the migration"
    kept = ws / "_memory" / "_retired" / "0.26.0-originals"
    assert 'update_full: "05:00"' in (kept / "config-cadences.yaml").read_text()
    assert "~/Sync/Inbox/Assistant" in (kept / "config-update.yaml").read_text()
    assert yaml.safe_load((kept / "asks.yaml").read_text())["asks"][-1]["question"] == "Pull the feed?"
    assert not (ws / "_memory" / "asks.yaml").exists()
    assert not (ws / "_memory" / "_retired" / "0.26.0-ledger.yaml").exists()
    assert workspace_version(ws) == "0.25.0"


def test_revert_leaves_what_the_workspace_had_before(tmp_path: Path) -> None:
    config = CONFIG.replace("  # Anti-pattern scanner.\n",
                            "  update:\n    drop_folders: []\n\n  # Anti-pattern scanner.\n")
    ws = _old_workspace(tmp_path, config)
    (ws / "_memory" / "asks.yaml").write_text("schema_version: 1\nasks: []\n")
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert _load("revert").run_revert(ws, out=lambda _m: None) == 0
    assert (ws / "_memory" / "config.yaml").read_text() == config
    assert (ws / "_memory" / "asks.yaml").is_file(), "a queue the migration did not create stays"


def test_migration_on_an_initialized_workspace_is_a_version_stamp_only(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    set_workspace_version(ws, "0.25.0")
    before = _files(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    after = _files(ws)
    assert {k for k in after if after[k] != before.get(k)} == {".version"}
    assert all(ok for ok, _m in _load("validate").run_checks(ws))


def test_migration_is_registered(framework_dir: Path) -> None:
    from superagent.tools.version import current_version, find_chain

    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.26.0")
    assert row["from_version"] == "0.25.0" and row["revertible"] is True and row["breaking"] is False
    assert current_version() >= "0.26.0"
    chain = find_chain("0.25.0", "0.26.0")
    assert [step.to_version for step in chain] == ["0.26.0"]
