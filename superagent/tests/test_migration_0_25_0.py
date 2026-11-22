# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.25.0 migration (agents as first-class objects): migrate, validate, revert."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

from superagent.tools import agents as ag
from superagent.tools.version import set_workspace_version, workspace_version

MIG_DIR = Path(__file__).resolve().parents[1] / "migrations" / "0.25.0"

CONFIG = """# header comment
schema_version: 1
last_updated: null

profile:
  name: ""

preferences:
  # Automation and reminder toggles.
  workspace_path: "workspace"
  cadences:
    weekly_review: "Sunday 18:00"   # an inline comment survives

  # Anti-pattern scanner.
  anti_patterns:
    scan_skills: true

# --- Data sources ---
data_sources_configured:
  email:
    gmail: false
"""


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_25_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _old_workspace(tmp_path: Path, config: str = CONFIG) -> Path:
    """A 0.24.0-shaped workspace: no autocode block, no registry."""
    ws = tmp_path / "workspace"
    memory = ws / "_memory"
    memory.mkdir(parents=True)
    (memory / "config.yaml").write_text(config, encoding="utf-8")
    (memory / "todo.yaml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")
    set_workspace_version(ws, "0.24.0")
    return ws


def _snapshot(ws: Path) -> dict[str, bytes]:
    return {p.relative_to(ws).as_posix(): p.read_bytes() for p in sorted(ws.rglob("*")) if p.is_file()}


def test_migrate_seeds_the_block_and_the_registry_and_touches_nothing_else(tmp_path: Path,
                                                                            framework_dir: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _snapshot(ws)
    lines: list[str] = []
    assert _load("migrate").run_migration(ws, out=lines.append) == 0
    after = _snapshot(ws)
    assert {k for k in after if after[k] != before.get(k)} == {
        ".version", "_memory/config.yaml", "_memory/agents.yaml", "_memory/_retired/0.25.0-ledger.yaml"}
    assert workspace_version(ws) == "0.25.0"

    text = after["_memory/config.yaml"].decode()
    cfg = yaml.safe_load(text)
    assert cfg["preferences"]["autocode"] == {
        "id_prefix": "sa",
        "models": {"runner": {"model": None, "variant": None}, "dev": {"model": None, "variant": None}},
        "daily_usd": None,
    }
    assert text.index("  autocode:") > text.index("scan_skills: true"), "inside `preferences`, at its end"
    assert text.index("  autocode:") < text.index("# --- Data sources ---")
    assert "an inline comment survives" in text

    assert after["_memory/agents.yaml"] == (framework_dir / "templates" / "memory" / "agents.yaml").read_bytes()
    assert ag.load_registry(ws) == ([], []), "the registry starts with no agent"
    assert ag.roster(ws)["prefix"] == "sa"
    ledger = yaml.safe_load(after["_memory/_retired/0.25.0-ledger.yaml"])
    inserted = ledger["config"]["inserted_block"]
    assert text.replace(inserted, "") == CONFIG, "one run of new lines; no old line changed or moved"


def test_migrated_block_matches_the_config_template(tmp_path: Path, framework_dir: Path) -> None:
    """A migrated workspace and a freshly initialized one get the same block."""
    template = (framework_dir / "templates" / "memory" / "config.yaml").read_text(encoding="utf-8")
    assert _load("migrate").AUTOCODE_BLOCK in template


def test_migrate_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    mig = _load("migrate")
    before = _snapshot(ws)
    lines: list[str] = []
    assert mig.run_migration(ws, dry_run=True, out=lines.append) == 0
    assert _snapshot(ws) == before, "a dry run writes nothing"
    assert any("3 item(s) would be changed" in line for line in lines)
    assert mig.run_migration(ws, out=lambda _m: None) == 0
    once = _snapshot(ws)
    lines.clear()
    assert mig.run_migration(ws, out=lines.append) == 0
    assert _snapshot(ws) == once
    assert any("nothing to do" in line for line in lines)


def test_migrate_keeps_an_existing_block_and_registry(tmp_path: Path) -> None:
    config = CONFIG.replace("  # Anti-pattern scanner.", '  autocode:\n    id_prefix: "fam"\n\n  # Anti-pattern scanner.')
    ws = _old_workspace(tmp_path, config)
    registry = ws / "_memory" / "agents.yaml"
    registry.write_text("schema_version: 1\nagents:\n  - name: updater\n    role: updater\n", encoding="utf-8")
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert (ws / "_memory" / "config.yaml").read_text() == config
    assert registry.read_text().endswith("role: updater\n")
    assert not (ws / "_memory" / "_retired" / "0.25.0-ledger.yaml").exists(), "nothing was added to the config"
    assert ag.roster(ws)["prefix"] == "fam"
    assert _load("validate").run_validate(ws, out=lambda _m: None) == 0


def test_migrate_respects_a_four_space_indent(tmp_path: Path) -> None:
    config = "schema_version: 1\npreferences:\n    workspace_path: workspace\n    cadences:\n        doctor: every 180 days\n"
    ws = _old_workspace(tmp_path, config)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    text = (ws / "_memory" / "config.yaml").read_text()
    assert "\n    autocode:\n" in text and '\n        id_prefix: "sa"\n' in text
    assert "\n" + " " * 16 + "model: null" in text, "four levels deep, four spaces each"
    assert yaml.safe_load(text)["preferences"]["autocode"]["models"]["dev"] == {"model": None, "variant": None}


def test_migrate_leaves_an_unusual_config_alone_and_still_finishes(tmp_path: Path) -> None:
    for config in ("schema_version: 1\n", "schema_version: 1\npreferences: {}\n"):
        ws = _old_workspace(tmp_path / str(len(config)), config)
        lines: list[str] = []
        assert _load("migrate").run_migration(ws, out=lines.append) == 0
        assert (ws / "_memory" / "config.yaml").read_text() == config
        assert any("left alone" in line for line in lines)
        assert workspace_version(ws) == "0.25.0"
        assert _load("validate").run_validate(ws, out=lambda _m: None) == 0, "the default prefix is valid"


def test_migrate_refuses_a_broken_config(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path, "preferences: [unclosed\n")
    lines: list[str] = []
    assert _load("migrate").run_migration(ws, out=lines.append) == 1
    assert any("config.yaml is not well-formed YAML" in line for line in lines)
    assert workspace_version(ws) == "0.24.0"
    assert _load("migrate").run_migration(tmp_path / "nowhere", out=lines.append) == 1


def test_validate_passes_after_migrate_and_fails_before(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    val = _load("validate")
    lines: list[str] = []
    assert val.run_validate(ws, out=lines.append) == 1
    assert any(line.startswith("  FAIL") and "agents.yaml" in line for line in lines)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    lines.clear()
    assert val.run_validate(ws, out=lines.append) == 0
    assert lines[-1] == "6/6 checks passed"
    # A prefix that is not valid fails the check by name.
    config = ws / "_memory" / "config.yaml"
    config.write_text(config.read_text().replace('id_prefix: "sa"', 'id_prefix: "owner"'))
    lines.clear()
    assert val.run_validate(ws, out=lines.append) == 1
    assert any(line.startswith("  FAIL") and "id prefix" in line for line in lines)


def test_revert_restores_the_workspace_byte_for_byte(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    before = _snapshot(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    rev = _load("revert")
    migrated = _snapshot(ws)
    assert rev.run_revert(ws, dry_run=True, out=lambda _m: None) == 0
    assert _snapshot(ws) == migrated, "a dry run writes nothing"
    assert rev.run_revert(ws, out=lambda _m: None) == 0
    assert _snapshot(ws) == before
    assert rev.run_revert(ws, out=lambda _m: None) == 0, "reverting twice is harmless"
    assert _snapshot(ws) == before


def test_revert_keeps_what_was_set_since(tmp_path: Path) -> None:
    ws = _old_workspace(tmp_path)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    config = ws / "_memory" / "config.yaml"
    config.write_text(config.read_text().replace('id_prefix: "sa"', 'id_prefix: "fam"'))
    registry = ws / "_memory" / "agents.yaml"
    registry.write_text(registry.read_text().replace('  - name: ""\n    role: ""',
                                                     '  - name: "updater"\n    role: "updater"'))
    lines: list[str] = []
    assert _load("revert").run_revert(ws, out=lines.append) == 0
    assert config.read_text() == CONFIG, "the edited block is cut out by its shape"
    originals = ws / "_memory" / "_retired" / "0.25.0-originals"
    assert 'id_prefix: "fam"' in (originals / "config-autocode.yaml").read_text()
    assert 'name: "updater"' in (originals / "agents.yaml").read_text(), "a registry with an agent is kept"
    assert not registry.exists() and not (ws / "_memory" / "_retired" / "0.25.0-ledger.yaml").exists()
    assert workspace_version(ws) == "0.24.0"
    assert any("edited since the migration" in line for line in lines)


def test_revert_leaves_a_block_the_workspace_had_before(tmp_path: Path) -> None:
    config = CONFIG.replace("  # Anti-pattern scanner.", '  autocode:\n    id_prefix: "fam"\n\n  # Anti-pattern scanner.')
    ws = _old_workspace(tmp_path, config)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    assert _load("revert").run_revert(ws, out=lambda _m: None) == 0
    assert (ws / "_memory" / "config.yaml").read_text() == config


def test_migration_on_an_initialized_workspace_is_a_version_stamp_only(initialized_workspace: Path) -> None:
    """A workspace created by this framework already has the block and the registry."""
    ws = initialized_workspace
    set_workspace_version(ws, "0.24.0")
    before = _snapshot(ws)
    assert _load("migrate").run_migration(ws, out=lambda _m: None) == 0
    after = _snapshot(ws)
    assert {k for k in after if after[k] != before.get(k)} == {".version"}
    assert _load("validate").run_validate(ws, out=lambda _m: None) == 0


def test_migration_is_registered(framework_dir: Path) -> None:
    manifest = yaml.safe_load((framework_dir / "migrations" / "_manifest.yaml").read_text())
    row = next(m for m in manifest["migrations"] if m["to_version"] == "0.25.0")
    assert row["from_version"] == "0.24.0" and row["file"] == "0.25.0.md"
    assert row["revertible"] is True and row["breaking"] is False
    body = (framework_dir / "migrations" / "0.25.0.md").read_text()
    for name in ("migrate.py", "revert.py", "validate.py"):
        assert f"0.25.0/{name}" in body and (MIG_DIR / name).is_file()
