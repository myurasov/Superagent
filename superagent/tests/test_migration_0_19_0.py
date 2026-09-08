# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.19.0 migration helpers (migrate / validate / revert).

The fixture is an `initialized_workspace` dressed up as a 0.18.1 workspace:
a `data-sources.yaml` with one shipped-pack row (simplefin, weekly / manual)
and one unknown row, a standalone ref, a project-scoped standalone ref, two
sidecars (a `.pdf.ref.md` metadata sidecar and a payment confirmation), a
non-canonical `.ref.txt`, and catalogues referencing the standalone refs. Its
`sources-index.yaml` is written by hand in the 0.18.x shape (standalone refs
are `reference` rows) because the installed index tool (0.20.0+) no longer
indexes refs outside the registry. The migration is exercised under today's
tools, including the 0.19.0 -> 0.20.0 chain and its full revert.
Nothing here touches the real workspace.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = REPO_ROOT / "superagent" / "migrations" / "0.19.0"
MIG_DIR_020 = REPO_ROOT / "superagent" / "migrations" / "0.20.0"
FRAMEWORK = REPO_ROOT / "superagent"
NOW = dt.datetime(2026, 9, 7, 12, 0, 0, tzinfo=dt.UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_19_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_020(name: str) -> ModuleType:
    """The 0.20.0 helper scripts, for the chain tests."""
    spec = importlib.util.spec_from_file_location(f"mig_0_20_0_chain_{name}",
                                                  MIG_DIR_020 / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


migrate = _load("migrate")
validate = _load("validate")
revert = _load("revert")

# ---------------------------------------------------------------------------
# Fixture material
# ---------------------------------------------------------------------------

CONFIG_TEXT = """# [Do not change manually]
# Header comment that must survive.
schema_version: 1
last_updated: "2026-05-21T10:00:00-07:00"

profile:
  name: ""

preferences:
  workspace_path: "workspace"

  # Per-source ingestion cadence override (inert after 0.19.0).
  ingestion_schedule:
    simplefin: "weekly"      # inline comment

  sources:
    cache_path: "Sources/_cache"

  # Trailing preference comment (indented: belongs to preferences).
  skill_autoload: true

# --- Data sources (which MCPs / CLI tools you've authorized) ---
# This header belongs to the NEXT key and must stay glued to it.
data_sources_configured:
  finance:
    simplefin: true
"""

LAST_RUN = {
    "started_at": "2026-09-06T01:41:59-07:00", "finished_at": "2026-09-06T01:42:13-07:00",
    "items_pulled": 953, "items_inserted": 13, "items_updated": 2, "items_skipped": 940,
    "errors": [], "truncated": False, "duration_ms": 14525, "run_log_id": "ingest-20260906-002",
}

DATA_SOURCES = {
    "schema_version": 1,
    "last_updated": "2026-09-06T01:42:13-07:00",
    "sources": [
        {
            "id": "simplefin", "kind": "api", "enabled": True, "capture_mode": "manual",
            "schedule": "weekly", "recency_window_days": 30, "backfill_window_days": 365,
            "max_items_per_run": 10000, "writes_upstream": False,
            "auth": {"kind": "basic", "ref": "file:_memory/sensitive/simplefin-credentials.yaml",
                     "scope": [], "server_id": ""},
            "last_ingest": "2026-09-06T01:42:13-07:00", "last_run": LAST_RUN,
            "failure_streak": 2,
            "notes": "Bridge notes.\nAPI budget: <=24 requests/day.\n",
        },
        {
            "id": "strava", "kind": "api", "enabled": False, "capture_mode": "disabled",
            "schedule": "daily", "recency_window_days": 30, "max_items_per_run": 200,
            "writes_upstream": False, "auth": None, "last_ingest": None, "last_run": None,
            "failure_streak": 0, "notes": "",
        },
    ],
}

HUB_REF = """---
ref_version: 1
title: "Home Assistant - hub"
description: "Local Home Assistant host"
kind: cli
source: "ssh user@192.0.2.10"
ttl_minutes: 0
related_domain: "home"
tags: ["smart-home"]
params:
  hostname: "hub"          # inline comment must survive
---

# Notes

Primary smart-home host.
"""

PORTAL_REF = """---
ref_version: 1
title: "Permit portal"
kind: url
source: "https://permits.example.gov/status?id=1"
ttl_minutes: 1440
---
"""

MANUAL_SIDECAR = """---
ref_version: 1
title: "Repair manual"
description: "Metadata for the sibling PDF."
related_domain: "vehicles"
---

# Notes
"""

PAYMENT_SIDECAR = """---
kind: file
source: "Projects/x/Resources/orders/2026-01-01_order-1.pdf"
payee: "Vendor"
amount: 10.0
currency: "USD"
confirmation: "1"
---

# Sidecar
"""

HOME_CATALOGUE = """# Sources - Home

| Title | Ref path | Kind |
|---|---|---|
| Home Assistant | `Sources/Smart_Home/home_assistant-hub.ref.md` | cli |
"""

PROJECT_CATALOGUE = """# Sources - x

| Title | Ref path |
|---|---|
| Permit portal | `Sources/portal.ref.md` |
| Order sidecar | `Resources/orders/2026-01-01_order-1.pdf` + `.ref.md` |
"""


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _index_row(rel: str, *, kind: str, title: str, normalized: bool = True, **fields) -> dict:
    """One `sources-index.yaml` row in the 0.18.x shape."""
    from superagent.tools import sources_index as si

    row = {
        "id": si.id_for_path(rel), "kind": kind, "title": title, "path": rel,
        "category": si.category_from_path(rel),
        "related_domain": None, "related_project": None, "related_asset": None,
        "related_account": None, "sensitive": False, "added": "2026-08-25T23:31:33-07:00",
        "last_accessed": None, "read_count": 0, "present": True, "normalized": normalized,
        "tags": [], "notes": "",
    }
    row.update(fields)
    return row


def build_workspace(ws: Path, *, keep_state: bool = False) -> Path:
    """Dress an initialized workspace up as a 0.18.1 one with the fixture material."""
    from superagent.tools import sources_index as si

    (ws / ".version").write_text("0.18.1\n")
    # A 0.18.1 workspace has neither the registry folder nor the state file.
    readme = ws / "Sources" / "Watchlist" / "README.md"
    if readme.exists():
        readme.unlink()
        readme.parent.rmdir()
    state = ws / "_memory" / "watchlist-state.yaml"
    if state.exists() and not keep_state:
        state.unlink()
    _write(ws / "_memory" / "config.yaml", CONFIG_TEXT)
    _write(ws / "_memory" / "data-sources.yaml",
           yaml.safe_dump(DATA_SOURCES, sort_keys=False, allow_unicode=True))
    _write(ws / "Sources" / "Smart_Home" / "home_assistant-hub.ref.md", HUB_REF)
    _write(ws / "Sources" / "Vehicles" / "manual.pdf", "%PDF-1.4 fake\n")
    _write(ws / "Sources" / "Vehicles" / "manual.pdf.ref.md", MANUAL_SIDECAR)
    _write(ws / "Sources" / "notes" / "loose.ref.txt", "https://example.com/loose\n")
    _write(ws / "Projects" / "x" / "Sources" / "portal.ref.md", PORTAL_REF)
    _write(ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.pdf", "%PDF\n")
    _write(ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.ref.md",
           PAYMENT_SIDECAR)
    _write(ws / "Projects" / "x" / "sources.md", PROJECT_CATALOGUE)
    _write(ws / "Domains" / "Home" / "sources.md", HOME_CATALOGUE)
    # The 0.18.x index, as that release's tool wrote it: standalone refs (and
    # `.ref.txt`) are `reference` rows, the document beside a `.ref.md` sidecar
    # carries the sidecar's metadata. The hub row is hand-curated so the
    # migration has curated fields to carry across the move.
    index = si.load_index(ws)
    index["sources"] = [
        _index_row("Sources/Smart_Home/home_assistant-hub.ref.md", kind="reference",
                   title="Home Assistant - hub", related_domain="home", tags=["smart-home"],
                   notes="hand note", read_count=3),
        _index_row("Sources/Vehicles/manual.pdf", kind="document", title="Repair manual",
                   related_domain="vehicles"),
        _index_row("Sources/notes/loose.ref.txt", kind="reference", title="loose",
                   normalized=False),
        _index_row("Projects/x/Sources/portal.ref.md", kind="reference", title="Permit portal",
                   related_project="x"),
    ]
    index["last_filesystem_scan"] = "2026-08-25T23:31:33-07:00"
    si.save_index(ws, index)
    return ws


def _snapshot(ws: Path) -> dict[str, str]:
    skip = {"_memory/world.yaml"}
    return {p.relative_to(ws).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ws.rglob("*") if p.is_file() and p.relative_to(ws).as_posix() not in skip}


def _run(ws: Path, **kw) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = migrate.run_migration(ws, framework=FRAMEWORK, skip_world=True, now=NOW,
                                 out=lines.append, **kw)
    return code, lines


def _fm(path: Path) -> dict:
    fm, _ = migrate.parse_canonical_ref(path)
    assert fm is not None, f"{path} is not canonical"
    return fm


def _migrated(initialized_workspace: Path) -> Path:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    return ws


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_ref_id_keeps_sources_naming() -> None:
    # Usual Sources naming: lowercase, `_` between words, `-` inside tokens;
    # a stem that already fits is kept verbatim (never kebab-cased).
    assert migrate.ref_id("home_assistant-hub") == "home_assistant-hub"
    assert migrate.ref_id("Fiat_500_2013_Repair_Manual.pdf") == "fiat_500_2013_repair_manual_pdf"
    assert migrate.ref_id("--x__y--") == "x_y"


def test_capture_mode_normalization() -> None:
    norm = migrate.normalize_capture_mode
    assert norm("manual") == "manual"
    assert norm("automatic") == "automatic"
    assert norm("scheduled") == "automatic"
    assert norm("disabled") == "manual"
    assert norm("") is None and norm(None) is None


def test_inject_watch_block_is_idempotent_and_keeps_existing() -> None:
    text, changed = migrate.inject_watch_block(HUB_REF, "cmd")
    assert changed
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["watch"] == {"type": "cmd", "enabled": False}
    assert "# inline comment must survive" in text
    again, changed2 = migrate.inject_watch_block(text, "cmd")
    assert not changed2 and again == text
    custom = HUB_REF.replace("---\n\n# Notes", "watch:\n  type: subagent\n---\n\n# Notes")
    kept, changed3 = migrate.inject_watch_block(custom, "cmd")
    assert not changed3 and kept == custom


def test_ensure_watchlist_config_without_preferences() -> None:
    text, changed = migrate.ensure_watchlist_config("schema_version: 1\nprofile:\n  name: x\n",
                                                    "2026-09-07T12:00:00+00:00")
    assert changed
    cfg = yaml.safe_load(text)
    assert cfg["preferences"]["watchlist"]["path"] == "Sources/Watchlist"
    assert cfg["preferences"]["watchlist"]["allow_cmd"] is False
    assert cfg["last_updated"] == "2026-09-07T12:00:00+00:00"
    text2, changed2 = migrate.ensure_watchlist_config(text, "2026-09-08T00:00:00+00:00")
    assert not changed2 and text2 == text


def test_classify_ref_shapes(tmp_path: Path) -> None:
    ws = tmp_path
    standalone = ws / "Sources" / "a" / "thing.ref.md"
    _write(standalone, PORTAL_REF)
    assert migrate.classify_ref(standalone, ws)[0] == "standalone"
    form_a = ws / "Sources" / "b" / "doc.pdf.ref.md"
    _write(ws / "Sources" / "b" / "doc.pdf", "x")
    _write(form_a, MANUAL_SIDECAR)
    assert migrate.classify_ref(form_a, ws)[0] == "sidecar"
    form_b = ws / "Sources" / "c" / "doc.ref.md"
    _write(ws / "Sources" / "c" / "doc.pdf", "x")
    _write(form_b, MANUAL_SIDECAR)
    assert migrate.classify_ref(form_b, ws)[0] == "sidecar"
    payment = ws / "Sources" / "d" / "receipt.ref.md"
    _write(payment, PAYMENT_SIDECAR)
    assert migrate.classify_ref(payment, ws) [:2] == ("sidecar", "payment-confirmation fields")
    file_sibling = ws / "Sources" / "e" / "pointer.ref.md"
    _write(ws / "Sources" / "e" / "scan.pdf", "x")
    _write(file_sibling, '---\nkind: file\nsource: "Sources/e/scan.pdf"\ntitle: t\n---\n')
    assert migrate.classify_ref(file_sibling, ws)[0] == "sidecar"
    loose = ws / "Sources" / "f" / "loose.ref.txt"
    _write(loose, "https://example.com\n")
    assert migrate.classify_ref(loose, ws)[0] == "noncanonical"


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


def test_fold_simplefin_row_preserves_cadence_and_carries_params(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    ref = ws / "Sources" / "Watchlist" / "simplefin.ref.md"
    fm = _fm(ref)
    assert fm["kind"] == "api" and fm["source"] == "simplefin"
    assert fm["related_domain"] == "finances"
    watch = fm["watch"]
    assert watch["pack"] == "simplefin"
    assert watch["enabled"] is True
    assert watch["schedule"] == "weekly"          # B4: verbatim
    assert watch["capture_mode"] == "manual"      # B4: never widened
    assert watch["evict_after_days"] is None
    params = watch["params"]
    assert params["auth"]["ref"] == "file:_memory/sensitive/simplefin-credentials.yaml"
    assert params["recency_window_days"] == 30
    assert params["backfill_window_days"] == 365
    assert params["max_items_per_run"] == 10000 and params["writes_upstream"] is False
    for lifecycle in ("last_ingest", "last_run", "failure_streak", "notes", "enabled"):
        assert lifecycle not in params
    body = ref.read_text().split("---", 2)[2]
    assert "# Notes" in body and "Bridge notes." in body
    assert "API budget: <=24 requests/day." in body


def test_state_seeded_with_run_state(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    state = yaml.safe_load((ws / "_memory" / "watchlist-state.yaml").read_text())
    assert state["schema_version"] == 1
    row = state["watchers"]["simplefin"]
    assert row["status"] == "active"
    assert row["last_success"] == "2026-09-06T01:42:13-07:00"
    assert row["last_harvest"] == "2026-09-06T01:42:13-07:00"
    assert row["last_checked"] == "2026-09-06T01:42:13-07:00"
    assert row["baseline_at"] == "2026-09-06T01:42:13-07:00"
    assert row["error_streak"] == 2
    assert row["last_harvest_result"] == LAST_RUN
    assert "strava" not in state["watchers"]


def test_unknown_row_left_unfolded_and_reported(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    assert not (ws / "Sources" / "Watchlist" / "strava.ref.md").exists()
    assert any("strava" in ln and "unfolded" in ln for ln in lines)
    ledger = yaml.safe_load((ws / "_memory" / "_retired" / "0.19.0-moves.yaml").read_text())
    assert ledger["unfolded"] == [{"id": "strava"}]
    retired = yaml.safe_load((ws / "_memory" / "_retired" / "data-sources.yaml").read_text())
    assert [r["id"] for r in retired["sources"]] == ["simplefin", "strava"]


def test_data_sources_moved_not_deleted(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    original = (ws / "_memory" / "data-sources.yaml").read_bytes()
    _run(ws)
    assert not (ws / "_memory" / "data-sources.yaml").exists()
    assert (ws / "_memory" / "_retired" / "data-sources.yaml").read_bytes() == original


def test_standalone_refs_move_with_paused_watch_block(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    hub = ws / "Sources" / "Watchlist" / "home_assistant-hub.ref.md"
    assert hub.is_file()
    assert not (ws / "Sources" / "Smart_Home" / "home_assistant-hub.ref.md").exists()
    fm = _fm(hub)
    assert fm["watch"] == {"type": "cmd", "enabled": False}
    assert fm["kind"] == "cli" and fm["related_domain"] == "home" and fm["ttl_minutes"] == 0
    text = hub.read_text()
    assert "# inline comment must survive" in text
    assert "migrated 0.19.0" in text and "set enabled: true to start watching" in text
    assert text.endswith("Primary smart-home host.\n")
    portal = ws / "Sources" / "Watchlist" / "portal.ref.md"
    assert portal.is_file()
    assert not (ws / "Projects" / "x" / "Sources" / "portal.ref.md").exists()
    assert _fm(portal)["watch"] == {"type": "url", "enabled": False}


def test_sidecars_and_noncanonical_refs_stay(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    assert (ws / "Sources" / "Vehicles" / "manual.pdf.ref.md").read_text() == MANUAL_SIDECAR
    assert (ws / "Projects" / "x" / "Resources" / "orders"
            / "2026-01-01_order-1.ref.md").read_text() == PAYMENT_SIDECAR
    assert (ws / "Sources" / "notes" / "loose.ref.txt").is_file()
    names = sorted(p.name for p in (ws / "Sources" / "Watchlist").iterdir())
    assert names == ["README.md", "home_assistant-hub.ref.md", "portal.ref.md", "simplefin.ref.md"]
    assert any("keep Sources/Vehicles/manual.pdf.ref.md (sidecar" in ln for ln in lines)
    assert any("payment-confirmation" in ln or "sibling document 2026-01-01_order-1.pdf" in ln
               for ln in lines)
    assert any("loose.ref.txt" in ln and "normalize" in ln for ln in lines)


def test_catalogues_rewritten(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    home = (ws / "Domains" / "Home" / "sources.md").read_text()
    assert "`Sources/Watchlist/home_assistant-hub.ref.md`" in home
    assert "Smart_Home/home_assistant-hub" not in home
    proj = (ws / "Projects" / "x" / "sources.md").read_text()
    assert "`Sources/Watchlist/portal.ref.md`" in proj
    assert "`Sources/portal.ref.md`" not in proj
    # The sidecar row is untouched.
    assert "`Resources/orders/2026-01-01_order-1.pdf` + `.ref.md`" in proj


def test_sources_index_row_carries_curated_fields_and_lifts_watch(initialized_workspace: Path) -> None:
    from superagent.tools import sources_index as si

    ws = _migrated(initialized_workspace)
    index = si.load_index(ws)
    rows = {r["path"]: r for r in index["sources"] if r.get("id")}
    assert "Sources/Smart_Home/home_assistant-hub.ref.md" not in rows
    row = rows["Sources/Watchlist/home_assistant-hub.ref.md"]
    # The row keeps its pre-migration id (path identity in sources_index.refresh),
    # so history / log rows citing `src-...` keep pointing at a live row.
    original_id = si.id_for_path("Sources/Smart_Home/home_assistant-hub.ref.md")
    assert row["id"] == original_id
    assert row["id"] != si.id_for_path("Sources/Watchlist/home_assistant-hub.ref.md")
    assert sum(1 for r in index["sources"] if r.get("id") == original_id) == 1
    assert row["notes"] == "hand note" and row["read_count"] == 3
    assert row["watch"] == {"type": "cmd", "enabled": False}
    # `reference` under the 0.19.0 tool, `watcher` under 0.20.0+: the row kind
    # is the installed index's vocabulary, not the migration's.
    assert row["present"] is True and row["kind"] in ("reference", "watcher")
    simplefin = rows["Sources/Watchlist/simplefin.ref.md"]
    assert simplefin["watch"]["pack"] == "simplefin"
    assert simplefin["related_domain"] == "finances"
    assert "Sources/Watchlist/README.md" not in rows
    ledger = yaml.safe_load((ws / "_memory" / "_retired" / "0.19.0-moves.yaml").read_text())
    move = next(m for m in ledger["moved_refs"] if m["from"].endswith("hub.ref.md"))
    assert move["index_id"] == original_id


def test_config_block_inserted_inside_preferences(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    text = (ws / "_memory" / "config.yaml").read_text()
    cfg = yaml.safe_load(text)
    wl = cfg["preferences"]["watchlist"]
    assert wl == {"path": "Sources/Watchlist", "cycles": ["daily-update"], "evict_after_days": 14,
                  "allow_cmd": False, "min_check_interval_minutes": None}
    assert cfg["preferences"]["ingestion_schedule"] == {"simplefin": "weekly"}  # inert, kept
    assert "# Header comment that must survive." in text
    assert "# Trailing preference comment" in text
    # The next section's flush-left header stays glued to its key.
    assert ("# This header belongs to the NEXT key and must stay glued to it.\n"
            "data_sources_configured:") in text
    assert text.index("  watchlist:") < text.index("# --- Data sources")
    assert cfg["last_updated"] == migrate.now_iso(NOW)


def test_version_bumped_and_registry_readme_seeded(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    assert (ws / ".version").read_text().strip() == "0.19.0"
    assert (ws / "Sources" / "Watchlist" / "README.md").is_file()


def test_dry_run_writes_nothing(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    code, lines = _run(ws, dry_run=True)
    assert code == 0
    assert _snapshot(ws) == before
    assert any(ln.startswith("[dry-run] move Sources/Smart_Home/home_assistant-hub.ref.md")
               for ln in lines)
    assert any("would be changed" in ln for ln in lines)


def test_rerun_is_a_noop(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    before = _snapshot(ws)
    code, lines = _run(ws)
    assert code == 0
    assert any("nothing to do" in ln for ln in lines)
    assert _snapshot(ws) == before


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def test_validate_passes_after_migration(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    results = validate.run_checks(ws, FRAMEWORK)
    failed = [msg for ok, msg in results if not ok]
    assert not failed, failed
    messages = "\n".join(msg for _, msg in results)
    assert "B4: simplefin schedule 'weekly' -> 'weekly', capture_mode 'manual' -> 'manual'" in messages
    assert "strava unfolded" in messages
    assert "no sidecar moved (2 standalone ref(s) relocated)" in messages
    # The installed loader reads ref schema 2: the live dry run is the 0.20.0
    # validate's job, and this one says so instead of failing on schema-1 refs.
    assert "watchlist dry-run deferred: the shipped loader reads ref schema 2" in messages


def test_validate_fails_when_capture_mode_widened(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    ref = ws / "Sources" / "Watchlist" / "simplefin.ref.md"
    ref.write_text(ref.read_text().replace("capture_mode: manual", "capture_mode: automatic"))
    results = validate.run_checks(ws, FRAMEWORK)
    failed = [msg for ok, msg in results if not ok]
    assert any(msg.startswith("B4: simplefin") for msg in failed), failed


def test_validate_fails_on_reserved_type_and_moved_sidecar(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    _write(ws / "Sources" / "Watchlist" / "bad.ref.md",
           '---\nref_version: 1\ntitle: t\nkind: file\nsource: "_memory/todo.yaml"\n'
           "watch:\n  type: index_query\n---\n")
    # A document appearing where the hub ref used to live means a sidecar was moved.
    _write(ws / "Sources" / "Smart_Home" / "home_assistant-hub.pdf", "%PDF\n")
    failed = [msg for ok, msg in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any("index_query" in msg and "reserved" in msg for msg in failed), failed
    assert any(msg.startswith("sidecar moved: Sources/Smart_Home/home_assistant-hub.ref.md")
               for msg in failed), failed


def test_validate_cli_exit_codes(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK)]) == 1
    _run(ws)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK)]) == 0


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
    assert not (ws / "Sources" / "Watchlist").exists()
    assert not (ws / "_memory" / "watchlist-state.yaml").exists()
    assert not (ws / "_memory" / "_retired" / "data-sources.yaml").exists()
    assert not (ws / "_memory" / "_retired" / "0.19.0-moves.yaml").exists()
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert (ws / ".version").read_text().strip() == "0.18.1"


def test_revert_keeps_user_authored_registry_files(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    mine = ws / "Sources" / "Watchlist" / "mine.ref.md"
    _write(mine, PORTAL_REF.replace("---\n$", "watch:\n  type: url\n---\n"))
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert mine.is_file()
    assert not (ws / "Sources" / "Watchlist" / "simplefin.ref.md").exists()
    assert not (ws / "Sources" / "Watchlist" / "README.md").exists()
    assert any("user file(s) remain" in ln for ln in lines)


def test_revert_restores_pre_existing_state_file(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace, keep_state=True)
    state = ws / "_memory" / "watchlist-state.yaml"
    state.write_text("schema_version: 1\nwatchers:\n  keep-me:\n    status: active\n")
    original = state.read_bytes()
    _run(ws)
    migrated = yaml.safe_load(state.read_text())
    assert set(migrated["watchers"]) == {"keep-me", "simplefin"}
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert state.read_bytes() == original


def test_migrate_revert_migrate_cycle(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert _run(ws)[0] == 0
    first = _snapshot(ws)
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _run(ws)[0] == 0
    second = _snapshot(ws)
    # Only the wall-clock `added` stamps in the derived index and the ledger's
    # `applied_at` may differ between two otherwise identical migrations.
    volatile = {"_memory/sources-index.yaml", "_memory/_retired/0.19.0-moves.yaml"}
    assert {k: v for k, v in first.items() if k not in volatile} == \
        {k: v for k, v in second.items() if k not in volatile}
    assert not [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]


# ---------------------------------------------------------------------------
# Adversarial-review follow-ups (S-1, M-14)
# ---------------------------------------------------------------------------

API_REF = """---
ref_version: 1
title: "Broker API"
kind: api
source: "https://api.example.com/v1/balances"
auth_ref: "1Password://Personal/broker-api"
---

# Notes

Balances endpoint; token in the vault.
"""


def test_derived_prompt_and_subagent_block() -> None:
    fm = {"title": "Broker API", "source": "https://api.example.com/v1", "kind": "api"}
    prompt = migrate.derived_prompt(fm, "broker_api")
    assert prompt.startswith("Read Broker API at https://api.example.com/v1; "
                             "compare against the previous note")
    assert "return ONE line: the delta, or 'no change'" in prompt
    assert "never submit, send, or write" in prompt
    text, changed = migrate.inject_watch_block(API_REF, "subagent", prompt)
    assert changed
    fm2 = yaml.safe_load(text.split("---")[1])
    assert fm2["watch"] == {"type": "subagent", "enabled": False, "prompt": prompt}
    # Built-in fetch types get no prompt.
    text3, _ = migrate.inject_watch_block(PORTAL_REF, "url")
    assert "prompt" not in yaml.safe_load(text3.split("---")[1])["watch"]
    # A title-less ref falls back to the stem.
    assert migrate.derived_prompt({"kind": "vault"}, "pin_vault").startswith("Read pin_vault;")


def test_moved_api_ref_gets_prompt_and_loads_after_the_0_20_0_step(
    initialized_workspace: Path,
) -> None:
    """S-1: a `kind: api` ref becomes a `subagent` watcher WITH a prompt. The
    installed loader reads ref schema 2, so the schema-1 refs this migration
    writes load once the 0.20.0 step has converted them -- as in the real chain."""
    ws = build_workspace(initialized_workspace)
    _write(ws / "Sources" / "Finance" / "broker_api.ref.md", API_REF)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    moved = ws / "Sources" / "Watchlist" / "broker_api.ref.md"
    fm = _fm(moved)
    assert fm["watch"]["type"] == "subagent" and fm["watch"]["enabled"] is False
    assert fm["watch"]["prompt"].startswith(
        "Read Broker API at https://api.example.com/v1/balances; compare against the previous note")
    assert any("derived read-only prompt" in ln for ln in lines)
    out: list[str] = []
    assert _load_020("migrate").run_migration(ws, framework=FRAMEWORK, skip_world=True, now=NOW,
                                              out=out.append) == 0, "\n".join(out)
    wl = importlib.import_module("superagent.tools.watchlist")
    cfg = wl.load_config(ws)
    packs, pack_errors = wl.discover_packs(FRAMEWORK, ws, announce=lambda _m: None)
    assert not pack_errors, pack_errors
    watchers, errors = wl.load_registry(ws, cfg, packs)
    assert errors == [], errors
    by_id = {w.id: w for w in watchers}
    assert {"broker_api", "simplefin", "home_assistant-hub", "portal"} <= set(by_id)
    api = by_id["broker_api"]
    assert api.type == "subagent" and api.enabled is False
    assert api.detect["prompt"] == fm["watch"]["prompt"]
    assert by_id["home_assistant-hub"].type == "cmd" and by_id["portal"].type == "url"
    assert by_id["home_assistant-hub"].locator == "ssh user@192.0.2.10"
    assert by_id["simplefin"].pack is not None


def test_chain_0_18_1_to_0_20_0_and_back_is_byte_identical(initialized_workspace: Path) -> None:
    """A 0.18.x workspace runs 0.19.0 -> 0.20.0 under today's tools (each validate
    included); revert 0.20.0 -> revert 0.19.0 then lands on the original bytes."""
    m20, v20, r20 = (_load_020(name) for name in ("migrate", "validate", "revert"))
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    reg = ws / "Sources" / "Watchlist"
    assert _run(ws)[0] == 0
    assert validate.run_validate(ws, FRAMEWORK, out=lambda _m: None) == 0
    assert (ws / ".version").read_text().strip() == "0.19.0"
    lines: list[str] = []
    assert m20.run_migration(ws, framework=FRAMEWORK, skip_world=True, now=NOW,
                             out=lines.append) == 0, "\n".join(lines)
    assert (ws / ".version").read_text().strip() == "0.20.0"
    assert sorted(os.listdir(reg)) == ["Home_Assistant-Hub.ref.md", "Portal.ref.md", "README.md",
                                       "Simplefin.ref.md"]
    assert (ws / "Sources" / "Vehicles" / "manual.pdf.meta.md").is_file()
    assert (ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.pdf.meta.md").is_file()
    v_lines: list[str] = []
    assert v20.run_validate(ws, FRAMEWORK, out=v_lines.append) == 0, "\n".join(v_lines)
    assert r20.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert (ws / ".version").read_text().strip() == "0.19.0"
    assert sorted(os.listdir(reg)) == ["README.md", "home_assistant-hub.ref.md", "portal.ref.md",
                                       "simplefin.ref.md"]
    assert _fm(reg / "simplefin.ref.md")["watch"]["capture_mode"] == "manual"
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert (ws / ".version").read_text().strip() == "0.18.1"
    assert _snapshot(ws) == before


def test_empty_source_folder_removed_and_restored(initialized_workspace: Path) -> None:
    """M-14: a folder left empty by a ref move goes away (scan roots never do);
    revert brings the ref -- and therefore the folder -- back."""
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    assert not (ws / "Sources" / "Smart_Home").exists()
    assert (ws / "Projects" / "x" / "Sources").is_dir()  # scan root, kept even when empty
    assert (ws / "Sources" / "notes").is_dir()          # still holds loose.ref.txt
    assert any(ln == "remove Sources/Smart_Home/ (empty after the ref moved)" for ln in lines)
    ledger = yaml.safe_load((ws / "_memory" / "_retired" / "0.19.0-moves.yaml").read_text())
    assert ledger["removed_dirs"] == [{"path": "Sources/Smart_Home/"}]
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert (ws / "Sources" / "Smart_Home" / "home_assistant-hub.ref.md").read_text() == HUB_REF


def test_validate_relocates_checkpoints_and_revert_still_restores(
    initialized_workspace: Path,
) -> None:
    """M-14: after a successful validate the checkpoint folder is gone; the originals
    are parked under _retired/ and revert stays byte-for-byte from there."""
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    assert _run(ws)[0] == 0
    assert (ws / "_memory" / "_checkpoints" / "0.19.0").is_dir()
    lines: list[str] = []
    assert validate.run_validate(ws, FRAMEWORK, out=lines.append) == 0
    assert not (ws / "_memory" / "_checkpoints").exists()
    originals = ws / "_memory" / "_retired" / "0.19.0-originals"
    assert (originals / "Sources" / "Smart_Home" / "home_assistant-hub.ref.md").read_text() == HUB_REF
    assert (originals / "_memory" / "config.yaml").read_text() == CONFIG_TEXT
    assert (originals / "_seeded.txt").is_file()
    ledger = yaml.safe_load((ws / "_memory" / "_retired" / "0.19.0-moves.yaml").read_text())
    assert ledger["originals"] == "_memory/_retired/0.19.0-originals"
    assert any(ln.startswith("checkpoints: _memory/_checkpoints/0.19.0/ -> ") for ln in lines)
    # Validate again: still clean, nothing left to relocate.
    assert validate.run_validate(ws, FRAMEWORK, out=lambda _m: None) == 0
    # --keep-checkpoints path: a fresh migration leaves the folder alone.
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _snapshot(ws) == before
    assert not originals.exists()
    assert _run(ws)[0] == 0
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK),
                          "--keep-checkpoints"]) == 0
    assert (ws / "_memory" / "_checkpoints" / "0.19.0").is_dir()


def test_world_yaml_checkpointed_and_restored_on_revert(initialized_workspace: Path) -> None:
    """M-14: the derived world.yaml is snapshotted before the rebuild and restored on revert."""
    ws = build_workspace(initialized_workspace)
    world = ws / "_memory" / "world.yaml"
    original = world.read_bytes()
    lines: list[str] = []
    code = migrate.run_migration(ws, framework=FRAMEWORK, skip_world=False, now=NOW,
                                 out=lines.append)
    assert code == 0, "\n".join(lines)
    assert any(ln == "world.yaml: rebuilt (derived)" for ln in lines), lines
    assert world.read_bytes() != original
    assert (ws / "_memory" / "_checkpoints" / "0.19.0" / "_memory" / "world.yaml").read_bytes() == original
    assert "watch:simplefin" in world.read_text()
    out: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=out.append) == 0
    assert world.read_bytes() == original
    assert any("world.yaml restored to its pre-migration bytes" in ln for ln in out)


def test_dry_run_failure_message_prefers_first_error() -> None:
    payload = json.dumps({
        "summary": {"errors": 1},
        "errors": [{"id": "x", "file": "Sources/Watchlist/x.ref.md", "line": 9,
                    "error": "Sources/Watchlist/x.ref.md:9: subagent watcher needs watch.prompt"}],
    }, indent=2)
    assert validate._dry_run_failure(payload, "") == \
        "Sources/Watchlist/x.ref.md:9: subagent watcher needs watch.prompt"
    assert validate._dry_run_failure("", "Traceback (most recent call last):\nValueError: boom") \
        == "ValueError: boom"
    assert validate._dry_run_failure(json.dumps({"summary": {}}, indent=2), "") == "}"  # no errors[]: last line
    assert validate._dry_run_failure("", "") == "(no output)"
