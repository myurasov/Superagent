# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.27.0 migration (Autocode mode and agent creation): migrate, validate, revert."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

from superagent.tools import agent_roles as ar
from superagent.tools.version import set_workspace_version, workspace_version

MIG_DIR = Path(__file__).resolve().parents[1] / "migrations" / "0.27.0"

CONFIG = """# header comment
schema_version: 1

preferences:
  workspace_path: "workspace"
  cadences:
    update_full: "06:00"   # an inline comment survives

  # The update cycle.
  update:
    # Extra folders drained like `Inbox/`.
    drop_folders: ["~/Drop"]

  # Agents and Autocode.
  autocode:
    id_prefix: "fam"
    models:
      runner:
        model: null
        variant: null
    daily_usd: null

# --- Data sources ---
data_sources_configured:
  email:
    gmail: false
"""


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_27_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _old_workspace(tmp_path: Path, config: str = CONFIG) -> Path:
    ws = tmp_path / "workspace"
    (ws / "_memory").mkdir(parents=True)
    (ws / "_memory" / "config.yaml").write_text(config, encoding="utf-8")
    set_workspace_version(ws, "0.26.0")
    return ws


def _files(ws: Path) -> dict[str, bytes]:
    return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}


def _prefs(ws: Path) -> dict:
    return yaml.safe_load((ws / "_memory" / "config.yaml").read_text())["preferences"]


def test_migrate_adds_three_keys_and_the_switch_is_off(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _files(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    after = _files(ws)
    assert {k for k in after if after[k] != before.get(k)} == {
        "_memory/config.yaml", ".version", "_memory/_retired/0.27.0-ledger.yaml"}
    prefs = _prefs(ws)
    assert prefs["autocode"]["enabled"] is False and ar.autocode_enabled(ws) is False
    assert prefs["autocode"]["id_prefix"] == "fam" and prefs["autocode"]["daily_usd"] is None
    assert prefs["update"] == {"drop_folders": ["~/Drop"], "work_hours": None, "off_days": []}
    text = (ws / "_memory" / "config.yaml").read_text()
    old_lines = [ln for ln in CONFIG.splitlines() if ln.strip()]
    assert [ln for ln in text.splitlines() if ln in old_lines] == old_lines, "every old line survives, in order"
    assert text.index("    daily_usd: null") < text.index("    enabled: false") < text.index("# --- Data sources")
    assert text.index('drop_folders: ["~/Drop"]') < text.index("    work_hours: null") < text.index("  # Agents and")
    assert workspace_version(ws) == "0.27.0"


def test_migrated_workspace_has_every_key_of_the_config_template(tmp_path: Path, framework_dir: Path) -> None:
    ws = _old_workspace(tmp_path)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    template = yaml.safe_load((framework_dir / "templates" / "memory" / "config.yaml").read_text())
    for block in ("autocode", "update"):
        wanted = template["preferences"][block]
        got = _prefs(ws)[block]
        assert set(wanted) - {"models"} <= set(got), f"`{block}` lacks a key the template has"
        for key, _value, _line in _load("migrate").KEYS[block]:
            assert got[key] == wanted[key], f"{block}.{key} differs from the template's default"


def test_migrate_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    mig = _load("migrate")
    before = _files(ws)
    assert mig.run_migration(ws, dry_run=True, out=lambda _m: None) == 0 and _files(ws) == before
    assert mig.run_migration(ws, out=lambda _m: None) == 0
    once = _files(ws)
    out: list[str] = []
    assert mig.run_migration(ws, out=out.append) == 0
    assert _files(ws) == once and out == ["nothing to do; workspace already at 0.27.0 shape"]


def test_migrate_keeps_set_keys_and_leaves_an_unusual_config_alone(tmp_path: Path) -> None:
    config = CONFIG.replace('    id_prefix: "fam"\n', '    id_prefix: "fam"\n    enabled: true\n')
    ws = _old_workspace(tmp_path, config)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert _prefs(ws)["autocode"]["enabled"] is True, "a value that is set is never overwritten"
    assert (ws / "_memory" / "config.yaml").read_text().count("enabled:") == 1
    bare = _old_workspace(tmp_path / "bare", "schema_version: 1\npreferences: {workspace_path: workspace}\n")
    before = (bare / "_memory" / "config.yaml").read_text()
    out: list[str] = []
    assert _load("migrate").run_migration(bare, out=out.append) == 0
    assert (bare / "_memory" / "config.yaml").read_text() == before
    assert any("no `preferences.autocode` block" in line for line in out)
    assert workspace_version(bare) == "0.27.0" and ar.autocode_enabled(bare) is False
    assert all(ok for ok, _m in _load("validate").run_checks(bare))
    broken = _old_workspace(tmp_path / "broken", "preferences:\n  autocode: [unclosed\n")
    assert _load("migrate").run_migration(broken, out=lambda _m: None) == 1


def test_validate_passes_after_migrate_and_fails_before(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    validate = _load("validate")
    assert [m for ok, m in validate.run_checks(ws) if not ok and ".version reads 0.27.0" in m]
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    results = validate.run_checks(ws)
    assert all(ok for ok, _m in results) and len(results) == 7
    assert validate.main(["--workspace", str(ws)]) == 0
    config = ws / "_memory" / "config.yaml"
    config.write_text(config.read_text().replace("off_days: []", "off_days: Saturday"))
    assert [m for ok, m in validate.run_checks(ws) if not ok] == ["`preferences.update.off_days` is a list"]


def test_revert_restores_the_workspace_and_keeps_edited_lines(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _files(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert _load("revert").run_revert(ws, out=lambda _m: None) == 0
    assert _files(ws) == before and workspace_version(ws) == "0.26.0"
    # The switch was turned on and work hours were set since: the lines go, and are kept aside.
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    config = ws / "_memory" / "config.yaml"
    config.write_text(config.read_text().replace("enabled: false", "enabled: true ").replace(
        "work_hours: null", 'work_hours: "07:00-22:00"'))
    assert _load("revert").run_revert(ws, dry_run=True, out=lambda _m: None) == 0
    assert _prefs(ws)["autocode"]["enabled"] is True, "a dry run removes nothing"
    assert _load("revert").run_revert(ws, out=lambda _m: None) == 0
    assert config.read_text() == CONFIG
    kept = (ws / "_memory" / "_retired" / "0.27.0-originals" / "config-keys.yaml").read_text()
    assert "enabled: true" in kept and 'work_hours: "07:00-22:00"' in kept and "off_days" not in kept
    assert not (ws / "_memory" / "_retired" / "0.27.0-ledger.yaml").exists()


def test_on_an_initialized_workspace_it_is_a_version_stamp_only_and_is_registered(
        initialized_workspace: Path, framework_dir: Path) -> None:
    from superagent.tools.version import current_version, find_chain, parse

    ws = initialized_workspace
    set_workspace_version(ws, "0.26.0")
    before = _files(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    after = _files(ws)
    assert {k for k in after if after[k] != before.get(k)} == {".version"}
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.27.0")
    assert row["from_version"] == "0.26.0" and row["revertible"] is True and row["breaking"] is False
    assert parse(current_version()) >= parse("0.27.0")
    assert [step.to_version for step in find_chain("0.26.0", "0.27.0")] == ["0.27.0"]
