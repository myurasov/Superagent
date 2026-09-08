# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Structural tests for the shipped watcher packs and the watchlist templates.

Covers the declarative surface of the watchlist feature (contracts/watchlist.md):

  - every `superagent/watchers/<id>/pack.yaml` parses and carries the required keys
  - each pack's `id` equals its folder name
  - `superagent/watchers/_manifest.yaml` lists exactly the folders present
  - `{{param}}` placeholders in `detect:` refer to declared params
  - the `simplefin` pack captures daily / automatically, budget-protected (0.20.0)
  - the ref template (`templates/sources/ref.md`, a `ref_version: 2` watcher
    definition), the folder README and the state template parse
  - the config template carries `preferences.watchlist` and no `ingestion_schedule`
  - no file under `superagent/watchers/` names the sibling framework

Deliberately does NOT import `superagent.tools.watchlist`: the tool is built
separately and has its own tests; this module pins the data it consumes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

DETECT_TYPES = {"url", "path", "cmd", "subagent", "gmail", "harvest"}
PACK_KINDS = {"api", "local", "generic"}
PROBE_KINDS = {"file_exists", "cli_on_path", "python_import", "cmd_exit_zero", "always"}
PROBE_LOCATOR = {
    "file_exists": "path",
    "cli_on_path": "cli",
    "python_import": "module",
    "cmd_exit_zero": "cmd",
    "always": None,
}
CYCLES = {"daily-update", "weekly-review", "monthly-review"}
EXPECTED_PACKS = {"simplefin", "gmail", "url", "subagent", "cmd", "path"}
WATCH_FIELDS = {
    "pack", "type", "enabled", "status", "cycles", "evict_after_days", "expires",
    "min_check_interval_minutes", "schedule", "capture_mode", "params", "selector",
    "ignore_patterns", "min_change_interval_minutes", "url", "path", "cmd", "prompt", "query",
}
# 0.19.0 reference keys a `ref_version: 2` template must not carry.
LEGACY_REF_KEYS = {"kind", "source", "ttl_minutes", "sensitive", "auth_ref", "chunk_for_large",
                   "normalized_at"}
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
# Built at runtime so this file never contains the forbidden token itself.
FORBIDDEN_SIBLING = ("co" + "-sa").lower()


def _watchers_dir(framework_dir: Path) -> Path:
    return framework_dir / "watchers"


def _pack_dirs(framework_dir: Path) -> list[Path]:
    return sorted(
        p for p in _watchers_dir(framework_dir).iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )


def _load_pack(pack_dir: Path) -> dict:
    data = yaml.safe_load((pack_dir / "pack.yaml").read_text())
    assert isinstance(data, dict), f"{pack_dir.name}/pack.yaml: top level is not a mapping"
    return data


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    assert match, "no frontmatter fence"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), "frontmatter is not a mapping"
    return data


def _walk_placeholders(node) -> set[str]:
    found: set[str] = set()
    if isinstance(node, str):
        found.update(PLACEHOLDER_RE.findall(node))
    elif isinstance(node, dict):
        for v in node.values():
            found |= _walk_placeholders(v)
    elif isinstance(node, list):
        for v in node:
            found |= _walk_placeholders(v)
    return found


# --- packs -------------------------------------------------------------------

def test_expected_pack_folders_exist(framework_dir: Path) -> None:
    present = {p.name for p in _pack_dirs(framework_dir)}
    assert present == EXPECTED_PACKS, f"pack folders differ from spec: {sorted(present)}"
    for p in _pack_dirs(framework_dir):
        assert (p / "pack.yaml").is_file(), f"{p.name}: missing pack.yaml"


def test_every_pack_parses_with_required_keys(framework_dir: Path) -> None:
    for pack_dir in _pack_dirs(framework_dir):
        data = _load_pack(pack_dir)
        name = pack_dir.name
        for key in ("watcher_version", "id", "title", "kind", "parameterized",
                    "detect", "probe", "defaults"):
            assert key in data, f"{name}: missing top-level key `{key}`"
        assert data["watcher_version"] == 1, f"{name}: watcher_version must be 1"
        assert data["id"] == name, f"{name}: id {data['id']!r} != folder name"
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", data["id"]), f"{name}: id not a slug"
        assert isinstance(data["title"], str) and data["title"].strip(), f"{name}: empty title"
        assert data["kind"] in PACK_KINDS, f"{name}: kind {data['kind']!r} not in {PACK_KINDS}"
        assert isinstance(data["parameterized"], bool), f"{name}: parameterized must be bool"

        detect = data["detect"]
        assert isinstance(detect, dict), f"{name}: detect must be a mapping"
        assert detect.get("type") in DETECT_TYPES, (
            f"{name}: detect.type {detect.get('type')!r} not in {sorted(DETECT_TYPES)}"
        )
        assert detect["type"] != "index_query", f"{name}: index_query is reserved"

        probe = data["probe"]
        assert isinstance(probe, dict), f"{name}: probe must be a mapping"
        assert probe.get("kind") in PROBE_KINDS, f"{name}: probe.kind {probe.get('kind')!r}"
        locator = PROBE_LOCATOR[probe["kind"]]
        if locator:
            assert probe.get(locator), f"{name}: probe.{locator} required for {probe['kind']}"
            assert "setup_hint" in probe, f"{name}: a non-trivial probe needs a setup_hint"

        defaults = data["defaults"]
        assert isinstance(defaults, dict), f"{name}: defaults must be a mapping"
        cycles = defaults.get("cycles")
        assert isinstance(cycles, list) and cycles, f"{name}: defaults.cycles must be a list"
        assert set(cycles) <= CYCLES, f"{name}: unknown cycle in {cycles}"
        assert "evict_after_days" in defaults, f"{name}: defaults.evict_after_days missing"
        ev = defaults["evict_after_days"]
        assert ev is None or (isinstance(ev, int) and ev > 0), f"{name}: bad evict_after_days"
        assert defaults.get("schedule") in {"daily", "weekly", "monthly", "manual"}, (
            f"{name}: defaults.schedule {defaults.get('schedule')!r}"
        )
        assert defaults.get("capture_mode") in {"automatic", "manual"}, (
            f"{name}: defaults.capture_mode {defaults.get('capture_mode')!r}"
        )


def test_parameterized_packs_declare_their_params(framework_dir: Path) -> None:
    for pack_dir in _pack_dirs(framework_dir):
        data = _load_pack(pack_dir)
        name = pack_dir.name
        params = data.get("params") or {}
        assert isinstance(params, dict), f"{name}: params must be a mapping"
        used = _walk_placeholders(data["detect"])
        # Placeholders are allowed in detect: only.
        outside = _walk_placeholders({k: v for k, v in data.items() if k != "detect"})
        assert not outside, f"{name}: {{{{param}}}} placeholders outside detect: {outside}"
        if data["parameterized"]:
            assert params, f"{name}: parameterized pack declares no params"
            for pname, spec in params.items():
                assert isinstance(spec, dict), f"{name}: params.{pname} must be a mapping"
                assert isinstance(spec.get("required"), bool), (
                    f"{name}: params.{pname}.required must be bool"
                )
                assert spec.get("description"), f"{name}: params.{pname} needs a description"
                if not spec["required"]:
                    assert "default" in spec, f"{name}: optional param {pname} needs a default"
            assert used, f"{name}: parameterized but detect uses no placeholder"
            assert any(params[p]["required"] for p in params), (
                f"{name}: a parameterized pack needs at least one required param"
            )
        assert used <= set(params), f"{name}: undeclared placeholders {used - set(params)}"


def test_harvest_blocks_are_well_formed(framework_dir: Path) -> None:
    for pack_dir in _pack_dirs(framework_dir):
        data = _load_pack(pack_dir)
        name = pack_dir.name
        harvest = data.get("harvest")
        if data["detect"]["type"] == "harvest":
            assert harvest, f"{name}: detect.type harvest requires a harvest block"
        if not harvest:
            continue
        assert isinstance(harvest, dict), f"{name}: harvest must be a mapping"
        handler = harvest.get("handler")
        if handler is None:
            assert (pack_dir / "handler.py").is_file(), (
                f"{name}: no harvest.handler and no handler.py in the folder"
            )
        else:
            assert re.fullmatch(r"[a-zA-Z_][\w.]*", handler), f"{name}: handler not dotted"
            module_path = framework_dir.parent.joinpath(*handler.split(".")).with_suffix(".py")
            assert module_path.is_file(), f"{name}: harvest.handler {handler} not found"
        writes = harvest.get("writes")
        assert isinstance(writes, list) and writes, f"{name}: harvest.writes must be non-empty"
        assert all(w.endswith(".yaml") for w in writes), f"{name}: writes must be _memory yamls"
        assert isinstance(harvest.get("affected_domains"), list), (
            f"{name}: harvest.affected_domains must be a list"
        )
        budget = data.get("budget")
        assert isinstance(budget, dict), f"{name}: a harvesting pack must declare a budget"
        assert isinstance(budget.get("max_calls_per_day"), int), f"{name}: budget.max_calls_per_day"
        assert isinstance(budget.get("min_interval_minutes"), int), (
            f"{name}: budget.min_interval_minutes"
        )


def test_auth_refs_are_pointers_not_secrets(framework_dir: Path) -> None:
    for pack_dir in _pack_dirs(framework_dir):
        data = _load_pack(pack_dir)
        name = pack_dir.name
        auth = data.get("auth")
        if auth is None:
            continue
        assert auth.get("kind") in {"basic", "oauth", "token", "none"}, f"{name}: auth.kind"
        ref = auth.get("ref", "")
        assert ref.startswith(("file:", "vault:", "1Password://")), (
            f"{name}: auth.ref must be a pointer, got {ref!r}"
        )
        assert "://" not in ref.split(":", 1)[1] or ref.startswith("1Password://"), (
            f"{name}: auth.ref looks like a URL with embedded credentials"
        )


def test_simplefin_pack_defaults_daily_automatic_budgeted(framework_dir: Path) -> None:
    """0.20.0: SimpleFIN is captured daily and automatically; the budget is unchanged."""
    data = _load_pack(_watchers_dir(framework_dir) / "simplefin")
    assert data["detect"]["type"] == "harvest"
    assert "handler" not in data["harvest"], "folder-default handler.py is canonical"
    assert (framework_dir / "watchers" / "simplefin" / "handler.py").is_file()
    assert (framework_dir / "watchers" / "simplefin" / "claim.py").is_file()
    assert set(data["harvest"]["writes"]) == {"transactions.yaml", "accounts-index.yaml"}
    assert data["probe"]["kind"] == "file_exists"
    assert data["probe"]["path"] == "_memory/sensitive/simplefin-credentials.yaml"
    assert data["auth"] == {"kind": "basic",
                            "ref": "file:_memory/sensitive/simplefin-credentials.yaml"}
    assert data["budget"]["max_calls_per_day"] == 24
    assert data["budget"]["min_interval_minutes"] == 60
    assert data["budget"]["max_window_days"] == 90
    d = data["defaults"]
    assert d["cycles"] == ["daily-update"]
    assert d["evict_after_days"] is None
    assert d["schedule"] == "daily"
    assert d["capture_mode"] == "automatic"
    assert d["min_check_interval_minutes"] == 60
    readme = (_watchers_dir(framework_dir) / "simplefin" / "README.md").read_text()
    assert "daily" in readme and "automatic" in readme
    assert "harvest --id simplefin" in readme, "on-demand pull documented"
    assert "24 calls/day" in readme and "budget" in readme.lower(), "the budget protects the API"
    assert "Simplefin.ref.md" in readme, "Title_Case registry filename"
    manifest = yaml.safe_load((_watchers_dir(framework_dir) / "_manifest.yaml").read_text())
    row = next(r for r in manifest["packs"] if r["id"] == "simplefin")
    assert "daily" in row["one_line"] and "weekly" not in row["one_line"]


def test_gmail_pack_is_live_and_parameterized(framework_dir: Path) -> None:
    data = _load_pack(_watchers_dir(framework_dir) / "gmail")
    assert data["parameterized"] is True
    assert data["params"]["query"]["required"] is True
    assert data["detect"] == {"type": "gmail", "query": "{{query}}"}
    assert data["probe"]["path"] == "~/.gmail-mcp/credentials.json"
    assert data["auth"]["kind"] == "oauth"
    assert data["defaults"]["min_check_interval_minutes"] == 60
    assert data["defaults"]["evict_after_days"] == 30


def test_generic_packs_probe_always(framework_dir: Path) -> None:
    for pid in ("url", "subagent"):
        data = _load_pack(_watchers_dir(framework_dir) / pid)
        assert data["kind"] == "generic"
        assert data["parameterized"] is True
        assert data["probe"] == {"kind": "always"}
        assert "harvest" not in data
    web = _load_pack(_watchers_dir(framework_dir) / "url")
    assert web["detect"]["type"] == "url"
    assert web["params"]["url"]["required"] is True
    assert web["params"]["selector"]["required"] is False
    assert web["defaults"]["min_check_interval_minutes"] == 720
    sub = _load_pack(_watchers_dir(framework_dir) / "subagent")
    assert sub["detect"]["type"] == "subagent"
    assert sub["params"]["prompt"]["required"] is True
    assert sub["defaults"]["evict_after_days"] == 45
    readme = (_watchers_dir(framework_dir) / "subagent" / "README.md").read_text()
    assert "read-only" in readme
    assert "never instructions" in readme


def test_manifest_lists_exactly_the_folders_present(framework_dir: Path) -> None:
    manifest_path = _watchers_dir(framework_dir) / "_manifest.yaml"
    assert manifest_path.is_file(), "missing superagent/watchers/_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    rows = manifest["packs"]
    assert isinstance(rows, list) and rows
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate pack ids in manifest"
    assert set(ids) == {p.name for p in _pack_dirs(framework_dir)}
    for row in rows:
        for key in ("id", "path", "kind", "one_line"):
            assert key in row, f"manifest row {row.get('id')}: missing {key}"
        assert row["path"] == f"watchers/{row['id']}/pack.yaml"
        assert (framework_dir / row["path"]).is_file(), row["path"]
        pack = _load_pack(_watchers_dir(framework_dir) / row["id"])
        assert row["kind"] == pack["kind"], f"{row['id']}: manifest kind != pack kind"
        assert row["one_line"].strip(), f"{row['id']}: empty one_line"


def test_no_watcher_file_references_other_frameworks(framework_dir: Path) -> None:
    for path in sorted(_watchers_dir(framework_dir).rglob("*")):
        if path.is_file():
            body = path.read_text(errors="replace").lower()
            assert FORBIDDEN_SIBLING not in body, f"{path.relative_to(framework_dir)}"


# --- templates -----------------------------------------------------------------

def test_ref_template_is_a_v2_watcher_definition(framework_dir: Path) -> None:
    """`templates/sources/ref.md` IS the watcher template (0.20.0); `watch.ref.md` is gone."""
    sources_templates = framework_dir / "templates" / "sources"
    assert sorted(p.name for p in sources_templates.iterdir()) == ["ref.md"]
    assert not (sources_templates / "watch.ref.md").exists()
    path = sources_templates / "ref.md"
    text = path.read_text()
    assert text.startswith("---\n"), "template must open with the frontmatter fence"
    fm = _frontmatter(text)
    for field in ("ref_version", "title", "description", "related_domain", "related_project",
                  "related_asset", "related_account", "added_by", "added_at", "tags", "watch"):
        assert field in fm, f"ref.md frontmatter missing {field!r}"
    assert fm["ref_version"] == 2
    assert not (set(fm) & LEGACY_REF_KEYS), "no kind / source / ttl_minutes in a v2 template"
    watch = fm["watch"]
    assert isinstance(watch, dict) and watch.get("enabled") is True
    # Optional keys are commented out (absent = inherit), but every field the
    # contract defines must be documented in the template text.
    for field in WATCH_FIELDS:
        assert re.search(rf"^\s*#?\s*{re.escape(field)}:", text, re.MULTILINE), (
            f"ref.md does not document watch.{field}"
        )
    assert "Title_Case" in text, "the Title_Case filename rule is stated"
    assert "Simplefin.ref.md" in text
    assert "meta.md" in text, "points document metadata at .meta.md sidecars"
    assert "0.20.0" in text, "names the migration that converts v1 refs"
    assert not re.search(r"\bkind\s*->\s*\w+", text), "no kind->type defaulting is documented"
    assert not re.search(r"^\s*#?\s*(kind|source|ttl_minutes):", text, re.MULTILINE)
    assert "# Notes" in text


def test_watchlist_folder_readme_exists(framework_dir: Path) -> None:
    path = framework_dir / "templates" / "folder-readmes" / "Watchlist.md"
    assert path.is_file(), "missing templates/folder-readmes/Watchlist.md"
    body = path.read_text()
    assert "ext-source" in body
    assert "skills/watch.md" in body
    assert "watchlist-state.yaml" in body
    sources_readme = (framework_dir / "templates" / "folder-readmes" / "Sources.md").read_text()
    assert "Watchlist/" in sources_readme, "Sources.md must list the reserved Watchlist/ folder"


def test_watchlist_state_template(framework_dir: Path) -> None:
    path = framework_dir / "templates" / "memory" / "watchlist-state.yaml"
    assert path.is_file(), "missing templates/memory/watchlist-state.yaml"
    data = yaml.safe_load(path.read_text())
    assert data == {"schema_version": 1, "last_updated": None, "watchers": {}}
    body = path.read_text()
    for field in ("last_checked", "last_changed", "last_success", "baseline_at", "fingerprint",
                  "last_outcome", "error_streak", "evicted_at", "evict_reason", "last_harvest",
                  "calls_today", "calls_today_date"):
        assert field in body, f"state template does not document {field}"


def test_config_template_has_watchlist_block_and_no_ingestion_schedule(
    framework_dir: Path,
) -> None:
    cfg = yaml.safe_load((framework_dir / "templates" / "memory" / "config.yaml").read_text())
    prefs = cfg["preferences"]
    assert "ingestion_schedule" not in prefs, "ingestion_schedule must be dropped from the template"
    wl = prefs["watchlist"]
    assert wl == {
        "path": "Sources/Watchlist",
        "cycles": ["daily-update"],
        "evict_after_days": 14,
        "allow_cmd": False,
        "min_check_interval_minutes": None,
    }


def test_data_sources_template_is_retired(framework_dir: Path) -> None:
    assert not (framework_dir / "templates" / "memory" / "data-sources.yaml").exists(), (
        "templates/memory/data-sources.yaml is retired; the registry is Sources/Watchlist/"
    )


def test_contract_is_registered(framework_dir: Path) -> None:
    contract = framework_dir / "contracts" / "watchlist.md"
    assert contract.is_file()
    body = contract.read_text()
    assert "Using `_custom/watchers/<id>` (overrides framework pack)" in body
    manifest = yaml.safe_load((framework_dir / "contracts" / "_manifest.yaml").read_text())
    slugs = {row["slug"]: row for row in manifest["contracts"]}
    assert "watchlist" in slugs
    assert slugs["watchlist"]["path"] == "contracts/watchlist.md"


@pytest.mark.parametrize("term", ["gmail-label", "sources-ref", "csv-drop", "apple-reminders"])
def test_retired_pack_names_are_not_shipped(framework_dir: Path, term: str) -> None:
    assert not (_watchers_dir(framework_dir) / term).exists(), f"{term} must not ship"
