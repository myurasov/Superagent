# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.21.0 migration helpers (migrate / validate / revert).

The fixture is an `initialized_workspace` dressed up as a 0.20.1 workspace
that used the retired workbook and inbox-triage stacks: two domain workbooks
with their `.xlsx.meta.yaml` render-cache sidecars, one per-entity workbook
(sidecar present), one user spreadsheet in a domain folder (no sidecar; must
stay), an orphan sidecar, the inbox-triage decision log at both its 0.17.0+
and pre-0.17.0 locations, a user file in `Inbox/`, and a config carrying the
now-inert `workbooks` / `inbox_triage` blocks. Nothing here touches the real
workspace.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = REPO_ROOT / "superagent" / "migrations" / "0.21.0"
FRAMEWORK = REPO_ROOT / "superagent"
NOW = dt.datetime(2026, 9, 9, 12, 0, 0, tzinfo=dt.UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_21_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


migrate = _load("migrate")
validate = _load("validate")
revert = _load("revert")

CONFIG_TEXT = """# [Do not change manually]
schema_version: 1
last_updated: "2026-09-09T00:00:00-07:00"

profile:
  name: ""

preferences:
  workspace_path: "workspace"

  workbooks:
    enabled: true
    history_window_years: 10

  inbox_triage:
    stale_days: 14
    auto_classify: true
"""

XLSX_BYTES = b"PK\x03\x04fake-workbook-bytes\x00\x01\x02"
META_TEXT = "schema_version: 1\nsource_mtimes: {}\nconfig_signature: abc\n"
INBOX_LOG_TEXT = "schema_version: 1\ndecisions:\n  - ts: '2026-08-01T00:00:00-07:00'\n    file: a.pdf\n    action: filed\n"
LEGACY_LOG_TEXT = "schema_version: 1\ndecisions: []\n"

RENDERED = (
    "Domains/Home/home.xlsx",
    "Domains/Home/home.xlsx.meta.yaml",
    "Domains/Vehicles/vehicles.xlsx",
    "Domains/Vehicles/vehicles.xlsx.meta.yaml",
    "Domains/Vehicles/blue-camry.xlsx",            # per-entity: sidecar present
    "Domains/Vehicles/blue-camry.xlsx.meta.yaml",
    "Domains/Finances/orphan.xlsx.meta.yaml",      # orphan sidecar: render junk
)
USER_XLSX = "Domains/Home/contractor-quotes.xlsx"   # no sidecar, not domain-named
INBOX_STATE = ("_memory/inbox-log.yaml", "Inbox/_processed.yaml")
USER_INBOX_FILE = "Inbox/photo.jpg"


def _write(p: Path, data: str | bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")


def build_workspace(ws: Path) -> Path:
    (ws / ".version").write_text("0.20.1\n")
    _write(ws / "_memory" / "config.yaml", CONFIG_TEXT)
    for rel in RENDERED:
        _write(ws / rel, META_TEXT if rel.endswith(".meta.yaml") else XLSX_BYTES + rel.encode())
    _write(ws / USER_XLSX, XLSX_BYTES + b"user")
    _write(ws / "Domains" / "Home" / "info.md", "# Home\n")
    _write(ws / "_memory" / "inbox-log.yaml", INBOX_LOG_TEXT)
    _write(ws / "Inbox" / "_processed.yaml", LEGACY_LOG_TEXT)
    _write(ws / USER_INBOX_FILE, b"\xff\xd8\xff")
    return ws


def _snapshot(ws: Path) -> dict[str, str]:
    return {p.relative_to(ws).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ws.rglob("*") if p.is_file()}


def _run(ws: Path, **kw) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = migrate.run_migration(ws, framework=FRAMEWORK, now=NOW, out=lines.append, **kw)
    return code, lines


def _migrated(initialized_workspace: Path) -> Path:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    return ws


def _ledger(ws: Path) -> dict:
    return yaml.safe_load((ws / "_memory" / "_retired" / "0.21.0-moves.yaml").read_text())


CKPT = Path("_memory") / "_checkpoints" / "0.21.0"
ORIG = Path("_memory") / "_retired" / "0.21.0-originals"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_workbooks_distinguishes_rendered_from_user_files(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    rendered, strays = migrate.classify_workbooks(ws)
    assert {p.relative_to(ws).as_posix() for p, _k, _r in rendered} == set(RENDERED)
    kinds = {p.relative_to(ws).as_posix(): k for p, k, _r in rendered}
    assert kinds["Domains/Home/home.xlsx"] == "workbook"
    assert kinds["Domains/Vehicles/blue-camry.xlsx"] == "workbook"
    assert kinds["Domains/Home/home.xlsx.meta.yaml"] == "workbook_meta"
    assert [p.relative_to(ws).as_posix() for p in strays] == [USER_XLSX]
    # Case-insensitive stem match: `Domains/Home/HOME.xlsx` is a domain workbook too.
    _write(ws / "Domains" / "Pets" / "PETS.xlsx", XLSX_BYTES)
    rendered2, _ = migrate.classify_workbooks(ws)
    assert "Domains/Pets/PETS.xlsx" in {p.relative_to(ws).as_posix() for p, _k, _r in rendered2}
    # `Domains/README.md` and dot / underscore folders are not domains.
    assert all(p.is_dir() and p.name not in {"README.md"} for p in migrate.domain_dirs(ws))


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


def test_rendered_workbooks_and_sidecars_are_parked_user_xlsx_kept(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = {rel: (ws / rel).read_bytes() for rel in RENDERED}
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    for rel in RENDERED:
        assert not (ws / rel).exists(), rel
        assert (ws / CKPT / rel).read_bytes() == before[rel], rel
    assert (ws / USER_XLSX).is_file()
    assert (ws / "Domains" / "Home" / "info.md").read_text() == "# Home\n"
    assert any(ln.startswith("retire Domains/Home/home.xlsx -> _memory/_checkpoints/0.21.0/Domains/Home/home.xlsx "
                             "(domain workbook)") for ln in lines)
    assert any("Domains/Vehicles/blue-camry.xlsx" in ln and "per-entity workbook" in ln for ln in lines)
    assert any(ln.startswith(f"note: {USER_XLSX} is not a rendered workbook") for ln in lines)
    ledger = _ledger(ws)
    moved = {m["path"]: m for m in ledger["moved"]}
    assert set(moved) == set(RENDERED) | set(INBOX_STATE)
    assert moved["Domains/Home/home.xlsx"]["kind"] == "workbook"
    assert moved["Domains/Home/home.xlsx"]["sha256"] == hashlib.sha256(before["Domains/Home/home.xlsx"]).hexdigest()
    assert moved["Domains/Home/home.xlsx"]["size"] == len(before["Domains/Home/home.xlsx"])
    assert ledger["kept"] == [{"path": USER_XLSX, "reason": "user spreadsheet"}]


def test_inbox_state_parked_inbox_files_untouched(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    for rel in INBOX_STATE:
        assert not (ws / rel).exists(), rel
    assert (ws / CKPT / "_memory" / "inbox-log.yaml").read_text() == INBOX_LOG_TEXT
    assert (ws / CKPT / "Inbox" / "_processed.yaml").read_text() == LEGACY_LOG_TEXT
    assert (ws / USER_INBOX_FILE).is_file()
    assert (ws / "Inbox" / "README.md").is_file()  # scaffolded by init; untouched
    moved = {m["path"]: m for m in _ledger(ws)["moved"]}
    assert moved["_memory/inbox-log.yaml"]["kind"] == "inbox_state"


def test_config_untouched_and_reported(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    assert (ws / "_memory" / "config.yaml").read_text() == CONFIG_TEXT
    assert any(ln == "config.yaml: preferences.workbooks / preferences.inbox_triage left in place "
               "(inert since 0.21.0; nothing reads them)" for ln in lines), lines


def test_version_bumped(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    assert (ws / ".version").read_text().strip() == "0.21.0"


def test_dry_run_writes_nothing(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    code, lines = _run(ws, dry_run=True)
    assert code == 0
    assert _snapshot(ws) == before
    assert not (ws / "_memory" / "_retired" / "0.21.0-moves.yaml").exists()
    assert not (ws / CKPT).exists()
    assert any(ln.startswith("[dry-run] retire Domains/Home/home.xlsx") for ln in lines)
    assert any(ln.startswith("[dry-run] retire _memory/inbox-log.yaml") for ln in lines)
    assert any(ln == "[dry-run] .version: 0.20.1 -> 0.21.0" for ln in lines)
    assert any("10 file(s) would be changed" in ln for ln in lines), lines


def test_rerun_is_a_noop(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    before = _snapshot(ws)
    code, lines = _run(ws)
    assert code == 0
    assert any("nothing to do" in ln for ln in lines)
    assert _snapshot(ws) == before


def test_clean_workspace_only_bumps_version(initialized_workspace: Path) -> None:
    """A workspace that never used either stack: no moves, no ledger, just .version."""
    ws = initialized_workspace
    (ws / ".version").write_text("0.20.1\n")
    code, lines = _run(ws)
    assert code == 0
    assert (ws / ".version").read_text().strip() == "0.21.0"
    assert not (ws / CKPT).exists()
    ledger = _ledger(ws)
    assert ledger["moved"] == [] and ledger["kept"] == []
    assert not any("config.yaml:" in ln for ln in lines)  # template config has no inert blocks
    assert not [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]


def test_halted_rerun_never_overwrites_parked_original(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    # A second copy appears at the live path after the original was parked.
    _write(ws / "Domains" / "Home" / "home.xlsx", b"second copy")
    parked_before = (ws / CKPT / "Domains" / "Home" / "home.xlsx").read_bytes()
    code, lines = _run(ws)
    assert code == 0
    assert (ws / CKPT / "Domains" / "Home" / "home.xlsx").read_bytes() == parked_before
    assert (ws / "Domains" / "Home" / "home.xlsx").read_bytes() == b"second copy"
    assert any(ln.startswith("keep Domains/Home/home.xlsx (") and "resolve by hand" in ln for ln in lines)
    assert any(ln.startswith("files left in place") for ln in lines)


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_validate_passes_after_migration(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    results = validate.run_checks(ws, FRAMEWORK)
    failed = [m for ok, m in results if not ok]
    assert not failed, failed
    messages = "\n".join(m for _, m in results)
    assert "workbooks: no rendered workbook or render-cache sidecar under Domains/" in messages
    assert f"note: {USER_XLSX} kept (user spreadsheet)" in messages
    assert "inbox: no inbox-triage decision log remains" in messages
    assert "ledger: 9 moved file(s) parked once each, bytes intact" in messages
    assert ".version reads 0.21.0" in messages
    assert "rerun: migrate.py --dry-run reports nothing to do" in messages


def test_validate_fails_before_migration_and_on_regressions(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m.startswith("workbooks: Domains/Home/home.xlsx still present") for m in failed), failed
    assert any(m == "inbox: _memory/inbox-log.yaml still present" for m in failed), failed
    assert ".version reads 0.20.1" in failed
    assert any(m.startswith("rerun:") for m in failed)
    _run(ws)
    # Regressions after a successful run: a workbook re-appears, a parked copy is tampered with.
    _write(ws / "Domains" / "Vehicles" / "vehicles.xlsx", b"re-rendered")
    (ws / CKPT / "Domains" / "Home" / "home.xlsx").write_bytes(b"tampered")
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m.startswith("workbooks: Domains/Vehicles/vehicles.xlsx still present") for m in failed), failed
    assert "ledger: Domains/Vehicles/vehicles.xlsx is back at its live path" in failed
    assert "ledger: Domains/Home/home.xlsx parked bytes differ from the recorded sha256" in failed


def test_validate_cli_exit_codes(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK)]) == 1
    _run(ws)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK),
                          "--keep-checkpoints"]) == 0
    assert (ws / CKPT).is_dir()


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def test_revert_restores_prior_state_byte_for_byte(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    code, _ = _run(ws)
    assert code == 0
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert _snapshot(ws) == before, "\n".join(lines)
    assert not (ws / "_memory" / "_retired" / "0.21.0-moves.yaml").exists()
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert (ws / ".version").read_text().strip() == "0.20.1"
    assert any(ln == "restore Domains/Home/home.xlsx" for ln in lines)
    assert any(ln == "9 file(s) restored" for ln in lines), lines


def test_validate_relocates_checkpoints_and_revert_still_restores(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    assert _run(ws)[0] == 0
    assert (ws / CKPT).is_dir()
    lines: list[str] = []
    assert validate.run_validate(ws, FRAMEWORK, out=lines.append) == 0
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert (ws / ORIG / "Domains" / "Home" / "home.xlsx").is_file()
    assert (ws / ORIG / "_memory" / "inbox-log.yaml").read_text() == INBOX_LOG_TEXT
    assert _ledger(ws)["originals"] == "_memory/_retired/0.21.0-originals"
    assert any(ln.startswith("checkpoints: _memory/_checkpoints/0.21.0/ -> ") for ln in lines)
    # Validate is repeatable once relocated, and revert still restores byte-for-byte.
    assert validate.run_validate(ws, FRAMEWORK, out=lambda _m: None) == 0
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _snapshot(ws) == before
    assert not (ws / ORIG).exists()


def test_revert_never_overwrites_a_newer_live_file(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    _write(ws / "Domains" / "Home" / "home.xlsx", b"new user workbook")
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert (ws / "Domains" / "Home" / "home.xlsx").read_bytes() == b"new user workbook"
    assert (ws / CKPT / "Domains" / "Home" / "home.xlsx").is_file()   # left parked
    assert not (ws / CKPT / "Domains" / "Vehicles" / "vehicles.xlsx").exists()  # restored
    assert (ws / "Domains" / "Vehicles" / "vehicles.xlsx").is_file()
    assert any("keep Domains/Home/home.xlsx in _memory/_checkpoints/0.21.0/" in ln for ln in lines)
    assert any(ln == "8 file(s) restored; 1 left parked" for ln in lines), lines
    assert (ws / ".version").read_text().strip() == "0.20.1"


def test_revert_dry_run_writes_nothing(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    before = _snapshot(ws)
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, dry_run=True, out=lines.append) == 0
    assert _snapshot(ws) == before
    assert any(ln.startswith("[dry-run] restore ") for ln in lines)


def test_migrate_revert_migrate_cycle(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert _run(ws)[0] == 0
    first = _snapshot(ws)
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _run(ws)[0] == 0
    assert _snapshot(ws) == first
    assert not [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]


def test_migration_file_is_registered() -> None:
    manifest = yaml.safe_load((FRAMEWORK / "migrations" / "_manifest.yaml").read_text())
    rows = {r["to_version"]: r for r in manifest["migrations"]}
    assert "0.21.0" in rows
    assert rows["0.21.0"]["file"] == "0.21.0.md"
    assert rows["0.21.0"]["revertible"] is True and rows["0.21.0"]["breaking"] is False
    md = (FRAMEWORK / "migrations" / "0.21.0.md").read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---")[1])
    assert fm["helper_scripts"] == {"migrate": "0.21.0/migrate.py", "revert": "0.21.0/revert.py",
                                    "validate": "0.21.0/validate.py"}
    assert fm["from_version"] == "0.20.1"
    assert md.isascii(), "migration file must stay ASCII-clean"


def test_round_trip_on_a_workspace_with_nothing_to_move_leaves_no_trace(initialized_workspace: Path) -> None:
    """Review finding (R1-5): the ledger alone used to leave an empty `_memory/_retired/` behind."""
    ws = initialized_workspace
    (ws / ".version").write_text("0.20.1\n")
    assert not (ws / "_memory" / "_retired").exists()
    before = _snapshot(ws)
    code, _ = _run(ws)
    assert code == 0
    assert (ws / ".version").read_text().strip() == "0.21.0"
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert _snapshot(ws) == before
    assert not (ws / "_memory" / "_retired").exists(), "empty _retired/ must not survive the round trip"
    assert not (ws / "_memory" / "_checkpoints").exists()
