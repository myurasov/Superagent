# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.20.0 migration helpers (migrate / validate / revert).

The fixture is an `initialized_workspace` dressed up as a 0.19.0 workspace:
a registry with four `ref_version: 1` refs (a SimpleFIN pack instance at
weekly / manual, a bare `cli` ref carrying its locator in `source:`, a `url`
ref that relied on kind-to-type defaulting, an `api` ref already converted to
a `subagent` watcher with a prompt), two document sidecars (`<doc>.pdf.ref.md`
and a form-B payment confirmation under `Projects/x/Resources/`), a stale
`.ref.txt`, an empty `Sources/_cache/`, and catalogues naming the lowercase
registry paths and the sidecars. Nothing here touches the real workspace.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = REPO_ROOT / "superagent" / "migrations" / "0.20.0"
FRAMEWORK = REPO_ROOT / "superagent"
NOW = dt.datetime(2026, 9, 8, 12, 0, 0, tzinfo=dt.UTC)


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_20_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


migrate = _load("migrate")
validate = _load("validate")
revert = _load("revert")
wl = importlib.import_module("superagent.tools.watchlist")

# ---------------------------------------------------------------------------
# Fixture material (0.19.0 shapes)
# ---------------------------------------------------------------------------

CONFIG_TEXT = """# [Do not change manually]
schema_version: 1
last_updated: "2026-09-08T00:00:00-07:00"

profile:
  name: ""

preferences:
  workspace_path: "workspace"

  sources:
    cache_path: "Sources/_cache"     # inert after 0.20.0; left in place

  watchlist:
    path: "Sources/Watchlist"
    cycles: [daily-update]
    evict_after_days: 14
    allow_cmd: false
    min_check_interval_minutes: null
"""

SIMPLEFIN_REF = """---
ref_version: 1
title: "SimpleFIN Bridge - bank and brokerage feed"
kind: api
source: "simplefin"
related_domain: finances
added_by: "migrate-0.19.0"
added_at: "2026-09-08T00:27:12-07:00"
watch:
  pack: simplefin
  enabled: true
  schedule: weekly          # B4: carried verbatim by 0.19.0
  capture_mode: manual
  evict_after_days: null
  params:
    recency_window_days: 30
    backfill_window_days: 365
    max_items_per_run: 10000
    writes_upstream: false
    auth:
      kind: basic
      ref: file:_memory/sensitive/simplefin-credentials.yaml
      scope: []
      server_id: ''
---

# Notes

Bridge notes. API budget: <=24 requests/day.

<!-- folded from _memory/data-sources.yaml by migration 0.19.0 -->
"""

HUB_REF = """---
ref_version: 1
title: "Home Assistant - hub"
description: "Local Home Assistant host"
kind: cli
source: "ssh user@192.0.2.10"
ttl_minutes: 0
sensitive: false
chunk_for_large: true
related_domain: "home"
related_project: ""
added_by: "user"
added_at: "2026-08-26T06:35:00-07:00"
normalized_at: null
tags: ["smart-home"]
params:
  hostname: "hub"          # inline comment must survive
  install_type: "container"
watch:
  # migrated 0.19.0 — set enabled: true to start watching
  type: cmd
  enabled: false
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
watch:
  enabled: false
---
"""

API_REF = """---
ref_version: 1
title: "Broker API"
kind: api
source: "https://api.example.com/v1/balances"
auth_ref: "1Password://Personal/broker-api"
added_by: "watch"
watch:
  type: subagent
  enabled: false
  prompt: "Read Broker API; return ONE line: the delta, or 'no change'. Read-only."
---

# Notes

Balances endpoint; token in the vault.
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
| Home Assistant | `Sources/Watchlist/home_assistant-hub.ref.md` | cli |
| Permit portal | `Sources/Watchlist/permit_portal.ref.md` | url |
"""

VEHICLES_CATALOGUE = """# Sources - Vehicles

| Title | Path |
|---|---|
| Repair manual | `Sources/Vehicles/manual.pdf` (metadata: `Sources/Vehicles/manual.pdf.ref.md`) |
"""

PROJECT_CATALOGUE = """# Sources - x

| Title | Ref path |
|---|---|
| Bank feed | `Sources/Watchlist/simplefin.ref.md` |
| Order sidecar | `Resources/orders/2026-01-01_order-1.pdf` + `.ref.md` |
| Order sidecar (full) | `Projects/x/Resources/orders/2026-01-01_order-1.ref.md` |
"""


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_workspace(ws: Path) -> Path:
    """Dress an initialized workspace up as a 0.19.0 one with the fixture material."""
    from superagent.tools import sources_index as si

    (ws / ".version").write_text("0.19.0\n")
    _write(ws / "_memory" / "config.yaml", CONFIG_TEXT)
    state = ws / "_memory" / "watchlist-state.yaml"
    if not state.exists():
        _write(state, "schema_version: 1\nwatchers: {}\n")
    reg = ws / "Sources" / "Watchlist"
    reg.mkdir(parents=True, exist_ok=True)
    if not (reg / "README.md").exists():
        _write(reg / "README.md", "# Watchlist\n")
    _write(reg / "simplefin.ref.md", SIMPLEFIN_REF)
    _write(reg / "home_assistant-hub.ref.md", HUB_REF)
    _write(reg / "permit_portal.ref.md", PORTAL_REF)
    _write(reg / "broker_api.ref.md", API_REF)
    _write(ws / "Sources" / "Vehicles" / "manual.pdf", "%PDF-1.4 fake\n")
    _write(ws / "Sources" / "Vehicles" / "manual.pdf.ref.md", MANUAL_SIDECAR)
    _write(ws / "Sources" / "notes" / "loose.ref.txt", "https://example.com/loose\n")
    _write(ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.pdf", "%PDF\n")
    _write(ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.ref.md",
           PAYMENT_SIDECAR)
    _write(ws / "Projects" / "x" / "sources.md", PROJECT_CATALOGUE)
    _write(ws / "Domains" / "Home" / "sources.md", HOME_CATALOGUE)
    _write(ws / "Domains" / "Vehicles" / "sources.md", VEHICLES_CATALOGUE)
    (ws / "Sources" / "_cache").mkdir(parents=True, exist_ok=True)
    # Index the tree, then hand-curate the broker row (a framework-written ref
    # the migration renames) so it has curated fields to carry across the rename.
    si.refresh(ws, force=True, warn=lambda _m: None)
    row = si.get_by_path(ws, "Sources/Watchlist/broker_api.ref.md", refresh_first=False)
    assert row is not None
    si.update_row(ws, row["id"], {"notes": "hand note", "read_count": 3})
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
    fm = migrate.frontmatter_of(path)
    assert fm is not None, f"{path} is not canonical"
    return fm


def _migrated(initialized_workspace: Path) -> Path:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    return ws


def _names(folder: Path) -> list[str]:
    return sorted(os.listdir(folder))  # exact on-disk case


def _ledger(ws: Path) -> dict:
    return yaml.safe_load((ws / "_memory" / "_retired" / "0.20.0-moves.yaml").read_text())


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_framework_written_is_decided_by_added_by(tmp_path: Path) -> None:
    """Only `watch` / `init` / `migrate-*` mark a ref as framework-named; everything
    else (user, other values, missing, unparseable) is the user's name to keep."""
    assert migrate.framework_written({"added_by": "watch"})
    assert migrate.framework_written({"added_by": "init"})
    assert migrate.framework_written({"added_by": "migrate-0.19.0"})
    assert migrate.framework_written({"added_by": " migrate-0.18.1 "})
    assert not migrate.framework_written({"added_by": "user"})
    assert not migrate.framework_written({"added_by": "me"})
    assert not migrate.framework_written({"added_by": None})
    assert not migrate.framework_written({"added_by": 3})
    assert not migrate.framework_written({"title": "no provenance"})
    assert not migrate.framework_written(None)
    tool = tmp_path / "gmail-bills.ref.md"
    tool.write_text('---\nref_version: 2\ntitle: "x"\nadded_by: watch\nwatch: {pack: gmail}\n---\n')
    mine = tmp_path / "ha.ref.md"
    mine.write_text('---\nref_version: 2\ntitle: "x"\nadded_by: user\nwatch: {type: url, url: u}\n---\n')
    anon = tmp_path / "HA_Portal.ref.md"
    anon.write_text('---\nref_version: 2\ntitle: "x"\nwatch: {type: url, url: u}\n---\n')
    broken = tmp_path / "broken.ref.md"
    broken.write_text("no frontmatter at all\n")
    assert migrate.rename_target(tool) == "Gmail-Bills.ref.md"
    assert migrate.rename_target(mine) == "ha.ref.md"
    assert migrate.rename_target(anon) == "HA_Portal.ref.md"
    assert migrate.rename_target(broken) == "broken.ref.md"


def test_title_case_and_ids() -> None:
    assert migrate.title_case("home_assistant-hub") == "Home_Assistant-Hub"
    assert migrate.title_case("simplefin") == "Simplefin"
    assert migrate.id_from_stem("Home_Assistant-Hub") == "home_assistant-hub"
    assert migrate.target_name("simplefin.ref.md") == "Simplefin.ref.md"
    assert migrate.target_name("SimpleFIN.ref.md") == "Simplefin.ref.md"
    assert migrate.target_name("Home_Assistant-Hub.ref.md") == "Home_Assistant-Hub.ref.md"


def test_convert_bare_cli_ref_moves_source_and_keeps_comments() -> None:
    text, summary, blocker = migrate.convert_ref_text(HUB_REF, "home_assistant-hub")
    assert blocker is None and summary is not None
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["ref_version"] == 2
    for key in ("kind", "source", "ttl_minutes", "sensitive", "chunk_for_large", "normalized_at"):
        assert key not in fm
    assert fm["watch"] == {"type": "cmd", "cmd": "ssh user@192.0.2.10", "enabled": False}
    assert fm["title"] == "Home Assistant - hub" and fm["tags"] == ["smart-home"]
    assert "params" not in fm  # not a schema-2 key: relocated, not lost
    assert summary["moved_source_to"] == "watch.cmd"
    assert summary["kept_in_body"] == ["params"]
    assert summary["dropped"]["ttl_minutes"] == 0 and summary["dropped"]["kind"] == "cli"
    assert "# migrated 0.19.0 — set enabled: true to start watching" in text
    assert "# inline comment must survive" in text
    body = text.split("---", 2)[2]
    assert body.startswith("\n\n# Notes\n\nPrimary smart-home host.\n")
    assert migrate.KEPT_MARKER in body
    assert '```yaml\nparams:\n  hostname: "hub"          # inline comment must survive\n' in body
    assert text.endswith("```\n")
    # Order inside the watch block: the locator lands right under `type:`.
    assert text.index("  type: cmd\n  cmd: ") > 0
    again, summary2, blocker2 = migrate.convert_ref_text(text, "home_assistant-hub")
    assert blocker2 is None and summary2 is None and again == text


def test_convert_pack_instance_drops_source_without_locator() -> None:
    text, summary, blocker = migrate.convert_ref_text(SIMPLEFIN_REF, "simplefin")
    assert blocker is None and summary is not None
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["ref_version"] == 2 and "kind" not in fm and "source" not in fm
    assert fm["watch"]["pack"] == "simplefin" and "url" not in fm["watch"]
    assert fm["watch"]["schedule"] == "weekly"  # cadence is a separate, later step
    assert summary["moved_source_to"] is None and summary["kept_in_body"] == []
    assert "# B4: carried verbatim by 0.19.0" in text
    assert migrate.KEPT_MARKER not in text
    assert text.endswith("<!-- folded from _memory/data-sources.yaml by migration 0.19.0 -->\n")


def test_convert_derives_type_from_kind_and_keeps_auth_ref_in_body() -> None:
    text, summary, _ = migrate.convert_ref_text(PORTAL_REF, "permit_portal")
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["watch"] == {"type": "url", "url": "https://permits.example.gov/status?id=1",
                           "enabled": False}
    assert summary["type_set"] == "url" and summary["moved_source_to"] == "watch.url"
    text2, summary2, _ = migrate.convert_ref_text(API_REF, "broker_api")
    fm2 = yaml.safe_load(text2.split("---")[1])
    assert fm2["watch"]["type"] == "subagent" and fm2["watch"]["prompt"].startswith("Read Broker API")
    assert "auth_ref" not in fm2 and "source" not in fm2
    # The prompt already exists, so `source` is not moved; both it and the vault
    # pointer carry information nothing else holds -> kept verbatim in the body.
    assert summary2["kept_in_body"] == ["source", "auth_ref"]
    assert 'source: "https://api.example.com/v1/balances"\nauth_ref: "1Password://Personal/broker-api"' in text2
    assert text2.split("---", 2)[2].startswith("\n\n# Notes\n\nBalances endpoint; token in the vault.\n")


def test_convert_edge_shapes() -> None:
    # No `watch:` at all: a block is injected with the derived type + locator.
    bare = '---\nref_version: 1\ntitle: "t"\nkind: url\nsource: "https://x.example/"\n---\n\nBody.\n'
    text, summary, blocker = migrate.convert_ref_text(bare, "t")
    assert blocker is None and summary["watch_injected"]
    assert yaml.safe_load(text.split("---")[1])["watch"] == {"type": "url", "url": "https://x.example/"}
    assert text.endswith("---\n\nBody.\n")
    # `watch:` present but null: children are added under it (no duplicate key).
    nul = '---\nref_version: 1\ntitle: "t"\nkind: cli\nsource: "echo hi"\nwatch:\n---\n'
    text, _, blocker = migrate.convert_ref_text(nul, "t")
    assert blocker is None and text.count("\nwatch:") == 1
    assert yaml.safe_load(text.split("---")[1])["watch"] == {"type": "cmd", "cmd": "echo hi"}
    # Inline flow mapping is expanded so the locator can be inserted.
    flow = '---\nref_version: 1\ntitle: "t"\nkind: url\nsource: "https://x.example/"\nwatch: {enabled: false}  # c\n---\n'
    text, _, blocker = migrate.convert_ref_text(flow, "t")
    assert blocker is None
    assert yaml.safe_load(text.split("---")[1])["watch"] == {"enabled": False, "type": "url",
                                                             "url": "https://x.example/"}
    assert "watch:  # c\n" in text
    # A subagent without a prompt gets the derived read-only prompt.
    sub = '---\nref_version: 1\ntitle: "Vault"\nkind: manual\nsource: "the portal"\n---\n'
    text, summary, blocker = migrate.convert_ref_text(sub, "vault")
    assert blocker is None
    prompt = yaml.safe_load(text.split("---")[1])["watch"]["prompt"]
    assert prompt.startswith("Read Vault at the portal;") and "never submit" in prompt
    # Undeterminable type: untouched, reported.
    api = '---\nref_version: 1\ntitle: "t"\nkind: api\nsource: "https://x.example/"\n---\n'
    text, summary, blocker = migrate.convert_ref_text(api, "t")
    assert text == api and summary is None and "cannot derive watch.type" in blocker
    # Already schema 2: no change.
    v2 = '---\nref_version: 2\ntitle: "t"\nwatch:\n  type: url\n  url: "https://x.example/"\n---\n'
    assert migrate.convert_ref_text(v2, "t") == (v2, None, None)


def test_apply_simplefin_cadence_text_edits() -> None:
    text, previous, changed = migrate.apply_simplefin_cadence(SIMPLEFIN_REF)
    assert changed and previous == {"schedule": "weekly", "capture_mode": "manual",
                                    "cycles": migrate.ABSENT}
    watch = yaml.safe_load(text.split("---")[1])["watch"]
    assert watch["schedule"] == "daily" and watch["capture_mode"] == "automatic"
    assert watch["cycles"] == ["daily-update"]
    assert "  schedule: daily          # B4: carried verbatim by 0.19.0\n" in text
    assert ("  schedule: daily          # B4: carried verbatim by 0.19.0\n"
            "  cycles: [daily-update]\n  capture_mode: automatic\n") in text
    again, previous2, changed2 = migrate.apply_simplefin_cadence(text)
    assert not changed2 and again == text
    assert previous2 == {"schedule": "daily", "capture_mode": "automatic", "cycles": ["daily-update"]}
    # A block-form cycles list collapses onto the key line; other refs are untouched.
    multi = SIMPLEFIN_REF.replace("  capture_mode: manual\n",
                                  "  capture_mode: manual\n  cycles:\n    - weekly-review\n    - monthly-review\n")
    text3, _, changed3 = migrate.apply_simplefin_cadence(multi)
    assert changed3 and yaml.safe_load(text3.split("---")[1])["watch"]["cycles"] == ["daily-update"]
    assert "weekly-review" not in text3.split("---")[1]
    assert migrate.apply_simplefin_cadence(HUB_REF) == (HUB_REF, None, False)


def test_classify_sidecar_shapes(tmp_path: Path) -> None:
    ws = tmp_path
    form_a = ws / "Sources" / "b" / "doc.pdf.ref.md"
    _write(ws / "Sources" / "b" / "doc.pdf", "x")
    _write(form_a, MANUAL_SIDECAR)
    assert migrate.classify_sidecar(form_a, ws)[0] == form_a.with_name("doc.pdf.meta.md")
    form_b = ws / "Sources" / "c" / "doc.ref.md"
    _write(ws / "Sources" / "c" / "doc.pdf", "x")
    _write(form_b, MANUAL_SIDECAR)
    assert migrate.classify_sidecar(form_b, ws)[0] == form_b.with_name("doc.pdf.meta.md")
    payment = ws / "Sources" / "d" / "receipt.ref.md"
    _write(payment, PAYMENT_SIDECAR)
    target, reason = migrate.classify_sidecar(payment, ws)
    assert target == payment.with_name("receipt.meta.md") and "payment-confirmation" in reason
    stray = ws / "Sources" / "e" / "thing.ref.md"
    _write(stray, PORTAL_REF)
    assert migrate.classify_sidecar(stray, ws) == (None, "neither a document sidecar nor in the registry")


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


def test_registry_refs_converted_and_framework_written_ones_title_cased(initialized_workspace: Path) -> None:
    """Every ref is converted to schema 2; only the refs the FRAMEWORK named
    (`added_by: migrate-0.19.0` / `watch`) are renamed to Title_Case. The
    user-named ones (`added_by: user`, or no `added_by`) keep their casing."""
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    reg = ws / "Sources" / "Watchlist"
    assert _names(reg) == ["Broker_Api.ref.md", "README.md", "Simplefin.ref.md",
                           "home_assistant-hub.ref.md", "permit_portal.ref.md"]
    for name in _names(reg):
        if name == "README.md":
            continue
        fm = _fm(reg / name)
        assert fm["ref_version"] == 2
        assert not any(k in fm for k in migrate.LEGACY_REF_KEYS), name
        assert set(fm) <= set(migrate.REF_TOP_KEYS), name
    hub = _fm(reg / "home_assistant-hub.ref.md")
    assert hub["added_by"] == "user"
    assert hub["watch"] == {"type": "cmd", "cmd": "ssh user@192.0.2.10", "enabled": False}
    text = (reg / "home_assistant-hub.ref.md").read_text()
    assert "# inline comment must survive" in text and "Primary smart-home host." in text
    portal = _fm(reg / "permit_portal.ref.md")
    assert "added_by" not in portal
    assert portal["watch"] == {"type": "url", "url": "https://permits.example.gov/status?id=1",
                               "enabled": False}
    assert "kept Sources/Watchlist/home_assistant-hub.ref.md (user-named; casing respected)" in lines
    assert "kept Sources/Watchlist/permit_portal.ref.md (user-named; casing respected)" in lines
    assert any(ln.startswith("rename Sources/Watchlist/simplefin.ref.md -> Sources/Watchlist/Simplefin.ref.md "
                             "(Title_Case, framework-written ref") for ln in lines)
    ledger = _ledger(ws)
    assert {c["path"] for c in ledger["converted"]} == {
        "Sources/Watchlist/simplefin.ref.md", "Sources/Watchlist/home_assistant-hub.ref.md",
        "Sources/Watchlist/permit_portal.ref.md", "Sources/Watchlist/broker_api.ref.md"}
    renamed = {m["from"]: m["to"] for m in ledger["renamed"]}
    assert renamed == {"Sources/Watchlist/simplefin.ref.md": "Sources/Watchlist/Simplefin.ref.md",
                       "Sources/Watchlist/broker_api.ref.md": "Sources/Watchlist/Broker_Api.ref.md"}
    # Originals are checkpointed under their pre-migration names, byte-for-byte.
    ck = ws / "_memory" / "_checkpoints" / "0.20.0" / "Sources" / "Watchlist"
    assert _names(ck) == ["broker_api.ref.md", "home_assistant-hub.ref.md",
                          "permit_portal.ref.md", "simplefin.ref.md"]
    assert (ck / "home_assistant-hub.ref.md").read_text() == HUB_REF


def test_user_named_refs_keep_their_casing_through_migrate_validate_revert(
    initialized_workspace: Path,
) -> None:
    """User decision: `ha.ref.md`, `HA.ref.md`, `Home_Assistant.ref.md` are all the user's to
    name. The migration renames only what the framework wrote; validate does not flag the
    user's casing; revert leaves the user's files exactly where they were."""
    ws = build_workspace(initialized_workspace)
    reg = ws / "Sources" / "Watchlist"
    v2 = ('---\nref_version: 2\ntitle: "{t}"\n{who}watch:\n  type: url\n  enabled: false\n'
          '  url: "https://x.example/{t}"\n---\n')
    _write(reg / "ha.ref.md", v2.format(t="ha", who='added_by: "user"\n'))
    _write(reg / "HA_Portal.ref.md", v2.format(t="portal2", who=""))
    _write(reg / "Mixed_case-Thing.ref.md", v2.format(t="mixed", who='added_by: "someone-else"\n'))
    _write(reg / "gmail-bills.ref.md", v2.format(t="bills", who="added_by: watch\n"))
    before = _snapshot(ws)
    names_before = _names(reg)
    code, lines = _run(ws)
    assert code == 0, "\n".join(lines)
    assert _names(reg) == ["Broker_Api.ref.md", "Gmail-Bills.ref.md", "HA_Portal.ref.md",
                           "Mixed_case-Thing.ref.md", "README.md", "Simplefin.ref.md", "ha.ref.md",
                           "home_assistant-hub.ref.md", "permit_portal.ref.md"]
    assert "kept Sources/Watchlist/ha.ref.md (user-named; casing respected)" in lines
    assert "kept Sources/Watchlist/HA_Portal.ref.md (user-named; casing respected)" in lines
    assert "kept Sources/Watchlist/Mixed_case-Thing.ref.md (user-named; casing respected)" in lines
    assert any(ln.startswith("rename Sources/Watchlist/gmail-bills.ref.md -> "
                             "Sources/Watchlist/Gmail-Bills.ref.md") for ln in lines)
    renamed = {m["from"] for m in _ledger(ws)["renamed"]}
    assert renamed == {"Sources/Watchlist/simplefin.ref.md", "Sources/Watchlist/broker_api.ref.md",
                       "Sources/Watchlist/gmail-bills.ref.md"}
    results = validate.run_checks(ws, FRAMEWORK)
    failed = [m for ok, m in results if not ok]
    assert not failed, failed
    assert ("filename: 8 registry ref(s) with unique ids (3 framework-written, Title_Case; "
            "5 user-named, casing respected)") in [m for _, m in results]
    # The loader resolves every casing to its lowercase id.
    cfg = wl.load_config(ws)
    packs, _ = wl.discover_packs(FRAMEWORK, ws, announce=lambda _m: None)
    watchers, errors = wl.load_registry(ws, cfg, packs)
    assert errors == [], errors
    assert {w.id for w in watchers} >= {"ha", "ha_portal", "mixed_case-thing", "gmail-bills"}
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _names(reg) == names_before
    assert _snapshot(ws) == before


def test_simplefin_cadence_applied_and_recorded(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    fm = _fm(ws / "Sources" / "Watchlist" / "Simplefin.ref.md")
    watch = fm["watch"]
    assert watch["schedule"] == "daily" and watch["capture_mode"] == "automatic"
    assert watch["cycles"] == ["daily-update"] and watch["pack"] == "simplefin"
    assert watch["params"]["auth"]["ref"] == "file:_memory/sensitive/simplefin-credentials.yaml"
    cadence = _ledger(ws)["cadence"]
    assert cadence == [{"path": "Sources/Watchlist/Simplefin.ref.md",
                        "previous": {"schedule": "weekly", "capture_mode": "manual",
                                     "cycles": "<absent>"},
                        "applied": {"schedule": "daily", "capture_mode": "automatic",
                                    "cycles": ["daily-update"]}}]


def test_sidecars_renamed_and_catalogues_rewritten(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    manual = ws / "Sources" / "Vehicles" / "manual.pdf.meta.md"
    assert manual.read_text() == MANUAL_SIDECAR
    assert not (ws / "Sources" / "Vehicles" / "manual.pdf.ref.md").exists()
    order = ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.pdf.meta.md"
    assert order.read_text() == PAYMENT_SIDECAR  # form B lands on the canonical <doc>.<ext>.meta.md
    assert not (ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.ref.md").exists()
    assert (ws / "Sources" / "notes" / "loose.ref.txt").is_file()
    assert migrate.outside_refs(ws, ws / "Sources" / "Watchlist") == []
    assert any("rename Sources/Vehicles/manual.pdf.ref.md -> Sources/Vehicles/manual.pdf.meta.md" in ln
               for ln in lines)
    assert any("loose.ref.txt" in ln and "not recognized" in ln for ln in lines)
    vehicles = (ws / "Domains" / "Vehicles" / "sources.md").read_text()
    assert "`Sources/Vehicles/manual.pdf.meta.md`" in vehicles and ".ref.md" not in vehicles
    home = (ws / "Domains" / "Home" / "sources.md").read_text()
    # User-named refs were not renamed, so their catalogue rows are untouched.
    assert "`Sources/Watchlist/home_assistant-hub.ref.md`" in home
    assert "`Sources/Watchlist/permit_portal.ref.md`" in home
    assert "Home_Assistant-Hub" not in home and "Permit_Portal" not in home
    proj = (ws / "Projects" / "x" / "sources.md").read_text()
    assert "`Sources/Watchlist/Simplefin.ref.md`" in proj
    assert "`Resources/orders/2026-01-01_order-1.pdf` + `.meta.md`" in proj
    assert "`Projects/x/Resources/orders/2026-01-01_order-1.pdf.meta.md`" in proj
    assert "order-1.ref.md" not in proj and "+ `.ref.md`" not in proj
    assert "`Sources/Watchlist/Simplefin.ref.md`" in proj  # a watcher path keeps .ref.md
    sidecars = {m["from"]: m for m in _ledger(ws)["sidecars"]}
    assert sidecars["Sources/Vehicles/manual.pdf.ref.md"]["document"] == "Sources/Vehicles/manual.pdf"


def test_index_rows_keep_ids_across_renames(initialized_workspace: Path) -> None:
    from superagent.tools import sources_index as si

    ws = build_workspace(initialized_workspace)
    before = {r["path"]: r["id"] for r in si.load_index(ws)["sources"] if r.get("id")}
    hub_id = before["Sources/Watchlist/home_assistant-hub.ref.md"]
    api_id = before["Sources/Watchlist/broker_api.ref.md"]
    simplefin_id = before["Sources/Watchlist/simplefin.ref.md"]
    manual_id = before["Sources/Vehicles/manual.pdf"]
    assert _run(ws)[0] == 0
    index = si.load_index(ws)
    rows = {r["path"]: r for r in index["sources"] if r.get("id") and r.get("present", True)}
    assert "Sources/Watchlist/broker_api.ref.md" not in rows
    assert "Sources/Watchlist/simplefin.ref.md" not in rows
    api = rows["Sources/Watchlist/Broker_Api.ref.md"]
    assert api["id"] == api_id and api["notes"] == "hand note" and api["read_count"] == 3
    assert api["id"] != si.id_for_path("Sources/Watchlist/Broker_Api.ref.md")
    # The user-named hub ref was not renamed: same path, same id, converted content.
    assert "Sources/Watchlist/Home_Assistant-Hub.ref.md" not in rows
    hub = rows["Sources/Watchlist/home_assistant-hub.ref.md"]
    assert hub["id"] == hub_id
    assert hub["watch"] == {"type": "cmd", "cmd": "ssh user@192.0.2.10", "enabled": False}
    assert rows["Sources/Watchlist/Simplefin.ref.md"]["id"] == simplefin_id
    assert rows["Sources/Watchlist/Simplefin.ref.md"]["watch"]["capture_mode"] == "automatic"
    assert rows["Sources/Vehicles/manual.pdf"]["id"] == manual_id
    assert "Sources/Vehicles/manual.pdf.ref.md" not in rows
    assert sum(1 for r in index["sources"] if r.get("id") == api_id) == 1
    assert sum(1 for r in index["sources"] if r.get("id") == hub_id) == 1
    renamed = {m["from"]: m for m in _ledger(ws)["renamed"]}
    assert renamed["Sources/Watchlist/broker_api.ref.md"]["index_id"] == api_id
    assert "Sources/Watchlist/home_assistant-hub.ref.md" not in renamed


def test_case_only_mismatch_between_disk_and_index_is_aligned(initialized_workspace: Path) -> None:
    """The live shape: the file is already Title_Case, the index row still lowercase."""
    from superagent.tools import sources_index as si

    ws = build_workspace(initialized_workspace)
    reg = ws / "Sources" / "Watchlist"
    migrate.rename_two_step(reg / "simplefin.ref.md", reg / "Simplefin.ref.md")
    assert "Simplefin.ref.md" in _names(reg)
    old_id = next(r["id"] for r in si.load_index(ws)["sources"]
                  if r.get("path") == "Sources/Watchlist/simplefin.ref.md")
    code, lines = _run(ws)
    assert code == 0
    assert not any(ln.startswith("rename Sources/Watchlist/simplefin.ref.md") for ln in lines)
    rows = {r["path"]: r for r in si.load_index(ws)["sources"] if r.get("present", True)}
    assert rows["Sources/Watchlist/Simplefin.ref.md"]["id"] == old_id
    assert "Sources/Watchlist/simplefin.ref.md" not in rows
    assert _ledger(ws)["index_paths"] == [{"from": "Sources/Watchlist/simplefin.ref.md",
                                           "to": "Sources/Watchlist/Simplefin.ref.md",
                                           "index_id": old_id}]
    assert "`Sources/Watchlist/Simplefin.ref.md`" in (ws / "Projects" / "x" / "sources.md").read_text()


def test_empty_cache_removed_nonempty_kept(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    code, lines = _run(ws)
    assert code == 0
    assert not (ws / "Sources" / "_cache").exists()
    assert any(ln == "remove Sources/_cache/ (empty; the fetch cache is retired)" for ln in lines)
    assert any("preferences.sources.cache_* left in place" in ln for ln in lines)
    assert _ledger(ws)["removed_dirs"] == [{"path": "Sources/_cache/"}]
    cfg = yaml.safe_load((ws / "_memory" / "config.yaml").read_text())
    assert cfg["preferences"]["sources"] == {"cache_path": "Sources/_cache"}  # untouched
    # Non-empty: kept.
    ws2 = ws / "other"
    ws2.mkdir()
    _write(ws2 / "_memory" / "config.yaml", CONFIG_TEXT)
    (ws2 / ".version").write_text("0.19.0\n")
    _write(ws2 / "Sources" / "_cache" / "abc" / "_meta.yaml", "x: 1\n")
    lines2: list[str] = []
    assert migrate.run_migration(ws2, framework=FRAMEWORK, skip_world=True, now=NOW,
                                 out=lines2.append) == 0
    assert (ws2 / "Sources" / "_cache" / "abc" / "_meta.yaml").is_file()
    assert any(ln.startswith("keep Sources/_cache/ (1 entry") for ln in lines2)


def test_stray_ref_reported_not_moved_and_blocks_validate(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    stray = ws / "Sources" / "Misc" / "thing.ref.md"
    _write(stray, PORTAL_REF)
    code, lines = _run(ws)
    assert code == 0
    assert stray.read_text() == PORTAL_REF
    assert any(ln.startswith("stray Sources/Misc/thing.ref.md (neither a document sidecar") for ln in lines)
    assert _ledger(ws)["strays"] == [{"path": "Sources/Misc/thing.ref.md",
                                      "reason": "neither a document sidecar nor in the registry"}]
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m.startswith("stray: Sources/Misc/thing.ref.md") for m in failed), failed


def test_undeterminable_ref_left_untouched_and_reported(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    bad = ws / "Sources" / "Watchlist" / "mystery.ref.md"
    _write(bad, '---\nref_version: 1\ntitle: "m"\nkind: api\nsource: "https://x.example/"\n---\n')
    code, lines = _run(ws)
    assert code == 0
    # Left at ref_version 1 (not converted); no `added_by` -> user-named, so the
    # casing is respected too and the file stays exactly where it was.
    kept = ws / "Sources" / "Watchlist" / "mystery.ref.md"
    assert kept.read_text().startswith("---\nref_version: 1\n")
    names = _names(ws / "Sources" / "Watchlist")  # exact on-disk case (the fs is case-insensitive)
    assert "mystery.ref.md" in names and "Mystery.ref.md" not in names
    assert "kept Sources/Watchlist/mystery.ref.md (user-named; casing respected)" in lines
    assert any("cannot derive watch.type" in ln for ln in lines)
    assert any(ln.startswith("refs left at ref_version 1") for ln in lines)
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m.startswith("schema: Sources/Watchlist/mystery.ref.md") for m in failed), failed


def test_version_bumped(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    assert (ws / ".version").read_text().strip() == "0.20.0"


def test_dry_run_writes_nothing(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    names = _names(ws / "Sources" / "Watchlist")
    code, lines = _run(ws, dry_run=True)
    assert code == 0
    assert _snapshot(ws) == before and _names(ws / "Sources" / "Watchlist") == names
    assert (ws / "Sources" / "_cache").is_dir()
    assert any(ln.startswith("[dry-run] rename Sources/Watchlist/simplefin.ref.md -> "
                             "Sources/Watchlist/Simplefin.ref.md") for ln in lines)
    assert any(ln.startswith("[dry-run] Sources/Watchlist/home_assistant-hub.ref.md: ref_version 1 -> 2")
               for ln in lines)
    assert any("would be changed" in ln for ln in lines)
    assert not (ws / "_memory" / "_retired" / "0.20.0-moves.yaml").exists()


def test_rerun_is_a_noop(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    before = _snapshot(ws)
    names = _names(ws / "Sources" / "Watchlist")
    code, lines = _run(ws)
    assert code == 0
    assert any("nothing to do" in ln for ln in lines)
    assert _snapshot(ws) == before and _names(ws / "Sources" / "Watchlist") == names


# ---------------------------------------------------------------------------
# Loader + validate
# ---------------------------------------------------------------------------


def test_migrated_registry_loads_with_zero_errors(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    cfg = wl.load_config(ws)
    packs, pack_errors = wl.discover_packs(FRAMEWORK, ws, announce=lambda _m: None)
    assert not pack_errors, pack_errors
    watchers, errors = wl.load_registry(ws, cfg, packs)
    assert errors == [], errors
    by_id = {w.id: w for w in watchers}
    assert set(by_id) == {"simplefin", "home_assistant-hub", "permit_portal", "broker_api"}
    assert by_id["home_assistant-hub"].type == "cmd"
    assert by_id["home_assistant-hub"].locator == "ssh user@192.0.2.10"
    assert by_id["permit_portal"].type == "url"
    assert by_id["permit_portal"].locator == "https://permits.example.gov/status?id=1"
    assert by_id["broker_api"].type == "subagent" and by_id["broker_api"].enabled is False
    sf = by_id["simplefin"]
    assert sf.pack is not None and sf.capture_mode == "automatic" and sf.schedule == "daily"
    assert sf.cycles == ["daily-update"]
    assert sf.path.name == "Simplefin.ref.md"


def test_validate_passes_after_migration(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    results = validate.run_checks(ws, FRAMEWORK)
    failed = [msg for ok, msg in results if not ok]
    assert not failed, failed
    messages = "\n".join(msg for _, msg in results)
    assert "schema: 4 registry ref(s) at ref_version 2 with no legacy keys" in messages
    assert "loader: 4 watcher(s) load with zero errors" in messages
    assert ("filename: 4 registry ref(s) with unique ids (2 framework-written, Title_Case; "
            "2 user-named, casing respected)") in messages
    assert "stray: no .ref.md outside the registry" in messages
    assert "loose.ref.txt" in messages
    assert "sidecar: 2 .meta.md sidecar(s), every one beside its document" in messages
    assert "catalogue: no dangling path" in messages
    # The two framework-written registry rows (SimpleFIN, Broker API); the user-named
    # refs were never renamed, so they have no move to verify.
    assert "index: 2 renamed row(s) kept their ids" in messages
    assert ("simplefin: Sources/Watchlist/Simplefin.ref.md schedule 'daily', capture_mode "
            "'automatic', cycles ['daily-update']") in messages
    assert "cache: Sources/_cache/ absent" in messages
    assert ".version reads 0.20.0" in messages
    assert "watchlist dry-run: summary.errors=0 summary.harvested=0" in messages
    assert "(--no-harvest; offline-safe)" in messages
    assert "rerun: migrate.py --dry-run reports nothing to do" in messages


def test_validate_dry_run_is_offline_safe(initialized_workspace: Path) -> None:
    """With automatic capture SimpleFIN's harvest IS its detect, so a plain dry run
    would call the API; the check passes --no-harvest: no handler runs, the state
    file is untouched, and the withheld harvest shows up in skipped_harvest."""
    ws = _migrated(initialized_workspace)
    state = ws / "_memory" / "watchlist-state.yaml"
    before = state.read_bytes()
    ok, msg = validate.check_watchlist_dry_run(ws)
    assert ok, msg
    assert "summary.errors=0 summary.harvested=0" in msg and "skipped_harvest=1" in msg
    assert state.read_bytes() == before


def test_validate_fails_on_reintroduced_legacy_key_lowercase_name_and_cadence(
    initialized_workspace: Path,
) -> None:
    ws = _migrated(initialized_workspace)
    reg = ws / "Sources" / "Watchlist"
    sf = reg / "Simplefin.ref.md"
    sf.write_text(sf.read_text().replace("capture_mode: automatic", "capture_mode: manual"))
    portal = reg / "permit_portal.ref.md"  # user-named: never re-cased by the migration
    portal.write_text(portal.read_text().replace("ref_version: 2\n", "ref_version: 2\nkind: url\n"))
    migrate.rename_two_step(reg / "Broker_Api.ref.md", reg / "broker_api.ref.md")
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m.startswith("simplefin: Sources/Watchlist/Simplefin.ref.md") and "expected daily" in m
               for m in failed), failed
    assert any(m == "schema: Sources/Watchlist/permit_portal.ref.md still carries legacy key(s) kind"
               for m in failed), failed
    assert any(m.startswith("filename: Sources/Watchlist/broker_api.ref.md should be Broker_Api.ref.md")
               for m in failed), failed
    assert any(m.startswith("loader:") for m in failed), failed
    # A user-named ref re-cased by hand is the user's business: not a finding.
    migrate.rename_two_step(reg / "home_assistant-hub.ref.md", reg / "HOME_ASSISTANT-HUB.ref.md")
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert not any("HOME_ASSISTANT-HUB" in m and m.startswith("filename:") for m in failed), failed


def test_validate_fails_on_dangling_catalogue_path_and_orphan_sidecar(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    cat = ws / "Domains" / "Home" / "sources.md"
    cat.write_text(cat.read_text() + "| Old | `Sources/Watchlist/broker_api.ref.md` | api |\n")
    (ws / "Sources" / "Vehicles" / "manual.pdf").unlink()
    failed = [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
    assert any(m == "catalogue: Domains/Home/sources.md still names "
               "Sources/Watchlist/broker_api.ref.md" for m in failed), failed
    assert any(m == "sidecar: Sources/Vehicles/manual.pdf.meta.md has no document manual.pdf"
               for m in failed), failed


def test_validate_cli_exit_codes(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK)]) == 1
    _run(ws)
    assert validate.main(["--workspace", str(ws), "--framework", str(FRAMEWORK),
                          "--keep-checkpoints"]) == 0
    assert (ws / "_memory" / "_checkpoints" / "0.20.0").is_dir()


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def test_revert_restores_prior_state_byte_for_byte(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    names = _names(ws / "Sources" / "Watchlist")
    code, _ = _run(ws)
    assert code == 0
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert _snapshot(ws) == before, "\n".join(lines)
    assert _names(ws / "Sources" / "Watchlist") == names  # exact case restored
    assert (ws / "Sources" / "Vehicles" / "manual.pdf.ref.md").read_text() == MANUAL_SIDECAR
    assert (ws / "Projects" / "x" / "Resources" / "orders" / "2026-01-01_order-1.ref.md").is_file()
    assert (ws / "Sources" / "_cache").is_dir()
    assert not (ws / "_memory" / "_retired" / "0.20.0-moves.yaml").exists()
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert (ws / ".version").read_text().strip() == "0.19.0"
    assert any(ln.startswith("rename Sources/Watchlist/Simplefin.ref.md -> "
                             "Sources/Watchlist/simplefin.ref.md") for ln in lines)


def test_validate_relocates_checkpoints_and_revert_still_restores(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    before = _snapshot(ws)
    state = ws / "_memory" / "watchlist-state.yaml"
    state_before = state.read_bytes()
    assert _run(ws)[0] == 0
    assert (ws / "_memory" / "_checkpoints" / "0.20.0").is_dir()
    lines: list[str] = []
    assert validate.run_validate(ws, FRAMEWORK, out=lines.append) == 0
    assert not (ws / "_memory" / "_checkpoints").exists()
    originals = ws / "_memory" / "_retired" / "0.20.0-originals"
    assert (originals / "Sources" / "Watchlist" / "simplefin.ref.md").read_text() == SIMPLEFIN_REF
    assert (originals / "Sources" / "Vehicles" / "manual.pdf.ref.md").read_text() == MANUAL_SIDECAR
    # Nothing in the run writes the machine-owned state file, so it is not checkpointed.
    assert not (originals / "_memory" / "watchlist-state.yaml").exists()
    assert state.read_bytes() == state_before
    assert _ledger(ws)["originals"] == "_memory/_retired/0.20.0-originals"
    assert any(ln.startswith("checkpoints: _memory/_checkpoints/0.20.0/ -> ") for ln in lines)
    assert validate.run_validate(ws, FRAMEWORK, out=lambda _m: None) == 0
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _snapshot(ws) == before
    assert not originals.exists()
    assert not (ws / "_memory" / ".watchlist-state.lock").exists()


def test_revert_keeps_user_authored_registry_files(initialized_workspace: Path) -> None:
    ws = _migrated(initialized_workspace)
    mine = ws / "Sources" / "Watchlist" / "Mine.ref.md"
    _write(mine, '---\nref_version: 2\ntitle: "mine"\nwatch:\n  type: url\n  url: "https://x.example/"\n---\n')
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert mine.is_file()
    assert _names(ws / "Sources" / "Watchlist") == ["Mine.ref.md", "README.md", "broker_api.ref.md",
                                                     "home_assistant-hub.ref.md",
                                                     "permit_portal.ref.md", "simplefin.ref.md"]


def test_migrate_revert_migrate_cycle(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    assert _run(ws)[0] == 0
    first = _snapshot(ws)
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _m: None) == 0
    assert _run(ws)[0] == 0
    second = _snapshot(ws)
    # Only the wall-clock `last_refreshed` stamp in the derived index may differ.
    volatile = {"_memory/sources-index.yaml"}
    assert {k: v for k, v in first.items() if k not in volatile} == \
        {k: v for k, v in second.items() if k not in volatile}
    assert not [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]


def test_world_yaml_checkpointed_and_restored_on_revert(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    world = ws / "_memory" / "world.yaml"
    original = world.read_bytes()
    lines: list[str] = []
    code = migrate.run_migration(ws, framework=FRAMEWORK, skip_world=False, now=NOW,
                                 out=lines.append)
    assert code == 0, "\n".join(lines)
    assert any(ln == "world.yaml: rebuilt (derived)" for ln in lines), lines
    assert (ws / "_memory" / "_checkpoints" / "0.20.0" / "_memory" / "world.yaml").read_bytes() == original
    assert "watch:simplefin" in world.read_text()
    out: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=out.append) == 0
    assert world.read_bytes() == original
    assert any("world.yaml restored to its pre-migration bytes" in ln for ln in out)


def test_interrupted_rename_is_completed_on_rerun(initialized_workspace: Path) -> None:
    ws = build_workspace(initialized_workspace)
    reg = ws / "Sources" / "Watchlist"
    # Simulate a halt between the two renames of the case-only step (on a
    # framework-written ref -- the only kind the step renames).
    os.rename(reg / "simplefin.ref.md", reg / ("Simplefin.ref.md" + migrate.RENAME_TMP_SUFFIX))
    code, lines = _run(ws)
    assert code == 0
    assert "Simplefin.ref.md" in _names(reg)
    assert not any(n.endswith(migrate.RENAME_TMP_SUFFIX) for n in _names(reg))
    assert any("interrupted rename completed" in ln for ln in lines)
    assert not [m for ok, m in validate.run_checks(ws, FRAMEWORK) if not ok]
