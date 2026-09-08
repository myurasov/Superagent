# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/validate.py`."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_validate_passes_on_fresh_workspace(framework_dir: Path, initialized_workspace: Path) -> None:
    """A freshly-initialized workspace must validate clean."""
    from superagent.tools.validate import main as validate_main

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0


def test_validate_catches_unexpected_top_level_key(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """Adding an unexpected top-level key to a memory file must fail validation."""
    from superagent.tools.validate import main as validate_main

    config_path = initialized_workspace / "_memory" / "config.yaml"
    with config_path.open() as fh:
        data = yaml.safe_load(fh)
    data["bogus_key"] = "should-not-be-here"
    with config_path.open("w") as fh:
        yaml.safe_dump(data, fh)
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc != 0


def test_validate_catches_missing_schema_version(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """A YAML file missing `schema_version` must fail validation."""
    from superagent.tools.validate import main as validate_main

    todo_path = initialized_workspace / "_memory" / "todo.yaml"
    with todo_path.open() as fh:
        data = yaml.safe_load(fh)
    data.pop("schema_version", None)
    with todo_path.open("w") as fh:
        yaml.safe_dump(data, fh)
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc != 0


def test_validate_passes_after_events_derivation(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """A workspace that has run an events derivation must still validate clean.

    Validation compares each memory file's top-level keys against the
    template's. 0.16.0 taught `events_derive.py` to write a `derive_state`
    bookkeeping block into `_memory/events.yaml` but never declared that key
    in the template, so every workspace that had ever derived its events
    stream failed validation on an unexpected-top-level-key error. Only a
    post-derivation validate catches it — a fresh workspace passes either way.
    """
    from superagent.tools.events_derive import rebuild
    from superagent.tools.validate import main as validate_main

    result = rebuild(initialized_workspace)
    assert "error" not in result and "skipped" not in result, result

    events = yaml.safe_load(
        (initialized_workspace / "_memory" / "events.yaml").read_text()
    )
    assert "derive_state" in events

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0


# ------------------------------------------------ interaction-log soft checks


def _write_ilog(workspace: Path, rows: list[dict]) -> Path:
    path = workspace / "_memory" / "interaction-log.yaml"
    with path.open("w") as fh:
        yaml.safe_dump({"schema_version": 1, "entries": rows}, fh, sort_keys=False)
    return path


def test_skill_stems_include_framework_and_custom_overlay(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """Stems come from superagent/skills/*.md plus <workspace>/_custom/skills/*.md."""
    from superagent.tools.validate import skill_stems

    custom = initialized_workspace / "_custom" / "skills"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "browserctl.shop.md").write_text("---\nname: browserctl.shop\n---\n")
    stems = skill_stems(framework_dir, initialized_workspace)
    assert "migrate" in stems
    assert "daily-update" in stems  # `ingest` was retired by 0.19.0 in favour of `watch`
    assert "browserctl.shop" in stems
    assert not any(s.startswith("_") for s in stems)
    # Missing workspace / overlay dir is fine.
    assert "migrate" in skill_stems(framework_dir, None)


def test_check_interaction_log_classifies_skill_values() -> None:
    """Unknown vs prefixed alias vs stem; new-shape rows missing id/ts/skill counted."""
    from superagent.tools.validate import check_interaction_log

    stems = {"migrate", "ingest", "browserctl", "browserctl.shop"}
    rows = [
        # placeholder row from the template — ignored
        {"id": None, "ts": None, "skill": None, "action": "", "summary": "",
         "related_domain": None, "action_items": [], "ingestion_log_ref": None},
        # canonical, stem
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "migrate", "action": "apply", "summary": "ok"},
        # canonical, overlay stem
        {"id": "ilog-2026-09-01-002", "ts": "2026-09-01T10:05:00-07:00",
         "skill": "browserctl.shop", "action": "order_lookup", "summary": "ok"},
        # canonical, null skill (non-skill note) — allowed
        {"id": "ilog-2026-09-01-003", "ts": "2026-09-01T10:06:00-07:00",
         "skill": None, "action": "note", "summary": "ok"},
        # legacy prefixed alias
        {"id": "ilog-2026-09-01-004", "ts": "2026-09-01T10:10:00-07:00",
         "skill": "superagent-migrate", "action": "apply", "summary": "ok"},
        # unknown free text (compound)
        {"id": "ilog-2026-09-01-005", "ts": "2026-09-01T10:20:00-07:00",
         "skill": "ingest + log-event (update)", "action": "x", "summary": "ok"},
        # unknown: project slug in skill
        {"id": "ilog-2026-09-01-006", "ts": "2026-09-01T10:21:00-07:00",
         "skill": "some-project", "action": "x", "summary": "ok"},
        # new-shape row missing id
        {"ts": "2026-09-01T10:30:00-07:00", "skill": "migrate", "action": "x", "summary": "ok"},
        # legacy row — shape never flagged
        {"timestamp": "2026-08-01T09:00:00-07:00", "type": "skill_run",
         "subject": "migrate", "summary": "legacy"},
        # hybrid legacy row carrying a bad skill — value flagged, shape not
        {"timestamp": "2026-08-02T09:00:00-07:00", "type": "skill_run",
         "subject": "x", "skill": "superagent-browserctl", "summary": "legacy"},
    ]
    warnings = check_interaction_log(rows, stems)
    assert len(warnings) == 3, warnings
    unknown, prefixed, incomplete = warnings
    assert "2 row(s) use a 'skill' value that is not a skill stem" in unknown
    assert "'ingest + log-event (update)' x1" in unknown
    assert "'some-project' x1" in unknown
    assert "2 row(s) use the legacy prefixed" in prefixed
    assert "'superagent-migrate' x1" in prefixed
    assert "'superagent-browserctl' x1" in prefixed
    assert "1 new-shape row(s) missing one of id/ts/skill" in incomplete
    assert "(row index 7)" in incomplete


def test_check_interaction_log_clean_rows_emit_nothing() -> None:
    from superagent.tools.validate import check_interaction_log

    rows = [
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "migrate", "action": "apply", "summary": "ok"},
        {"timestamp": "2026-08-01T09:00:00-07:00", "type": "note", "subject": "s"},
    ]
    assert check_interaction_log(rows, {"migrate"}) == []
    assert check_interaction_log("not-a-list", {"migrate"}) == []


def test_validate_skill_drift_is_warning_not_error(
    framework_dir: Path, initialized_workspace: Path, capsys
) -> None:
    """Drifted skill values print WARN lines but the run still exits 0."""
    from superagent.tools.validate import main as validate_main

    _write_ilog(initialized_workspace, [
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "superagent-migrate", "action": "apply", "summary": "ok",
         "related_domain": None, "related_project": None, "related_asset": None,
         "related_account": None, "action_items": [], "ingestion_log_ref": None},
        {"id": "ilog-2026-09-01-002", "ts": "2026-09-01T10:01:00-07:00",
         "skill": "free text (qualifier)", "action": "x", "summary": "ok",
         "related_domain": None, "related_project": None, "related_asset": None,
         "related_account": None, "action_items": [], "ingestion_log_ref": None},
    ])
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK     interaction-log.yaml" in out
    assert "WARN   interaction-log.yaml" in out
    assert "not a skill stem" in out
    assert "legacy prefixed" in out
    assert "2 warning(s)" in out


def test_validate_fresh_interaction_log_has_no_warnings(
    framework_dir: Path, initialized_workspace: Path, capsys
) -> None:
    """The template placeholder row must not trip the soft checks."""
    from superagent.tools.validate import main as validate_main

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" not in out


def test_check_watchlist_state_shape() -> None:
    from superagent.tools.validate import check_watchlist_state

    assert check_watchlist_state({"schema_version": 1, "watchers": {}}) == []
    assert check_watchlist_state({"schema_version": 1, "watchers": {"a": {"status": "active"}}}) == []
    assert any("missing required key 'watchers'" in e
               for e in check_watchlist_state({"schema_version": 1}))
    assert any("must be a mapping" in e
               for e in check_watchlist_state({"schema_version": 1, "watchers": []}))
    assert any("watchers.a must be a mapping" in e
               for e in check_watchlist_state({"schema_version": 1, "watchers": {"a": "x"}}))


def _watch_ref(body: str, *, head: str = "---\nref_version: 2\ntitle: t\n") -> str:
    """A `ref_version: 2` watcher ref: frontmatter head + the caller's `watch:` block."""
    return head + body + "---\n"


def test_ref_helpers_title_case_and_ids() -> None:
    from superagent.tools import validate as v

    assert v.title_case_id("simplefin") == "Simplefin"
    assert v.title_case_id("home_assistant-hub") == "Home_Assistant-Hub"
    assert v.title_case_id("Home_Assistant-Hub") == "Home_Assistant-Hub"
    assert v.watch_id_from_stem("Home_Assistant-Hub") == "home_assistant-hub"
    assert v.ref_stem("Gmail-Bills.ref.md") == "Gmail-Bills" and v.ref_stem("x.REF.MD") == "x"
    assert v.is_ref_name("A.ref.md") and not v.is_ref_name(".ref.md") and not v.is_ref_name("a.meta.md")
    assert v.WATCH_ID_RE.match("home_assistant-hub") and not v.WATCH_ID_RE.match("Home_Assistant-Hub")
    assert v.REF_SUFFIX == ".ref.md" and v.META_SUFFIX == ".meta.md"
    assert v.LEGACY_REF_MIGRATION == "0.20.0"
    assert "watch" in v.REF_TOP_KEYS and not (v.REF_TOP_KEYS & v.LEGACY_REF_KEYS)
    assert v.WATCH_LOCATOR_KEY == {"url": "url", "path": "path", "cmd": "cmd", "subagent": "prompt",
                                   "gmail": "query"}
    assert set(v.WATCH_BUILTIN_TYPES) == {"url", "path", "cmd", "subagent", "harvest"}
    # Retained for the migrations only (v1 kind -> watch.type conversion).
    assert v.WATCH_TYPE_BY_REF_KIND == {"url": "url", "cli": "cmd", "file": "path", "manual": "subagent"}


def test_validate_watchlist_refs_schema(framework_dir: Path, initialized_workspace: Path) -> None:
    from superagent.tools.validate import validate_watchlist_refs

    reg = initialized_workspace / "Sources" / "Watchlist"
    (reg / "Good-Url.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://example.com\n"))
    (reg / "Good-Pack.ref.md").write_text(_watch_ref("watch:\n  pack: simplefin\n  capture_mode: manual\n"))
    (reg / "Bad-Reserved.ref.md").write_text(_watch_ref("watch:\n  type: index_query\n"))
    (reg / "Bad-Pack.ref.md").write_text(_watch_ref("watch:\n  pack: nope\n"))
    (reg / "Bad-Shape.ref.md").write_text(_watch_ref(
        "watch:\n  type: url\n  url: https://e.com\n  enabled: yes please\n  cycles: [1, 2]\n"
        "  capture_mode: sometimes\n"))
    (reg / "No-Block.ref.md").write_text(_watch_ref(""))
    (reg / "No-Type.ref.md").write_text(_watch_ref("watch:\n  enabled: true\n"))
    (reg / "Bare-Subagent.ref.md").write_text(_watch_ref("watch:\n  type: subagent\n"))
    (reg / "Bare-Url.ref.md").write_text(_watch_ref("watch:\n  type: url\n"))
    (reg / "Bare-Gmail.ref.md").write_text(_watch_ref("watch:\n  type: gmail\n  query: x\n"))
    (reg / "Bare-Harvest.ref.md").write_text(_watch_ref("watch:\n  type: harvest\n"))
    # The 0.19.0 reference shape: legacy keys and ref_version 1 point at the migration.
    (reg / "Legacy.ref.md").write_text(_watch_ref(
        "watch:\n  type: url\n  url: https://e.com\n",
        head="---\nref_version: 1\ntitle: t\nkind: url\nsource: \"https://example.com\"\nttl_minutes: 60\n"))
    (reg / "Typo.ref.md").write_text(_watch_ref("relatd_domain: home\nwatch:\n  type: url\n  url: https://e.com\n"))
    (reg / "Bad Id.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    (reg / "No-Front.ref.md").write_text("just text\n")
    # Not watchers: a .meta.md, nested files, README.md — the tool never loads them.
    (reg / "manual.pdf.meta.md").write_text("---\ntitle: sidecar, not a watcher\n---\n")
    (reg / "sub").mkdir()
    (reg / "sub" / "Nested.ref.md").write_text(_watch_ref("watch:\n  type: index_query\n"))
    oks, errs, warns = validate_watchlist_refs(initialized_workspace, framework_dir)
    assert sorted(oks) == ["Sources/Watchlist/Good-Pack.ref.md", "Sources/Watchlist/Good-Url.ref.md"]
    joined = "\n".join(errs)
    assert "Bad-Reserved.ref.md: watch.type 'index_query' is reserved" in joined
    assert "Bad-Pack.ref.md: watch.pack 'nope' is not a known pack" in joined
    assert "Bad-Shape.ref.md: watch.enabled must be bool" in joined
    assert "Bad-Shape.ref.md: watch.cycles must be a list" in joined
    assert "Bad-Shape.ref.md: watch.capture_mode must be one of" in joined
    assert "No-Block.ref.md: registry ref has no 'watch:' block" in joined
    assert "No-Type.ref.md: watch.pack or watch.type is required" in joined
    assert "Bare-Subagent.ref.md: a bare subagent watcher needs watch.prompt" in joined
    assert "Bare-Url.ref.md: a bare url watcher needs watch.url" in joined
    assert "Bare-Gmail.ref.md: watch.type 'gmail' is provided by a pack" in joined
    assert "Bare-Harvest.ref.md: watch.type 'harvest' needs a pack" in joined
    assert "Legacy.ref.md: legacy reference key(s) 'kind', 'source', 'ttl_minutes'" in joined
    assert "0.20.0 migration" in joined and "Legacy.ref.md: ref_version 1 is not 2" in joined
    assert "Typo.ref.md: unknown frontmatter key(s) 'relatd_domain'" in joined
    assert "Bad Id.ref.md: id 'bad id'" in joined and "must match" in joined
    assert "No-Front.ref.md: missing or unparseable YAML frontmatter" in joined
    assert "meta.md" not in joined and "Nested.ref.md" not in joined
    # The nested ref is a stray (warning, never an error); the sidecar in the registry is ignored.
    assert any("sub/Nested.ref.md: stray .ref.md outside the registry" in w for w in warns)
    assert not any(w.startswith("Sources/Watchlist/manual.pdf.meta.md") for w in warns)


def test_validate_watchlist_refs_respect_user_casing_and_flag_collisions(framework_dir: Path,
                                                                        initialized_workspace: Path,
                                                                        monkeypatch) -> None:
    """User decision: the tool never enforces its Title_Case naming on the user's files.
    `ha.ref.md`, `HA.ref.md`, `lowercase_name.ref.md` are all valid; only a collision errors."""
    from superagent.tools import validate as v

    reg = initialized_workspace / "Sources" / "Watchlist"
    (reg / "lowercase_name.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    (reg / "HA.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    (reg / "Fine.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    oks, errs, warns = v.validate_watchlist_refs(initialized_workspace, framework_dir)
    assert errs == []
    assert sorted(oks) == ["Sources/Watchlist/Fine.ref.md", "Sources/Watchlist/HA.ref.md",
                           "Sources/Watchlist/lowercase_name.ref.md"], (
        "any casing validates (loaded case-insensitively)"
    )
    assert warns == [], "a user's filename casing is never warned about"
    # Two files whose lowercase stems collide (presented via the listing so the test does not
    # depend on a case-sensitive filesystem): both are errors, neither is OK.
    (reg / "FINE.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    monkeypatch.setattr(v, "watch_ref_files", lambda registry: [reg / "FINE.ref.md", reg / "Fine.ref.md",
                                                                 reg / "lowercase_name.ref.md"])
    oks, errs, warns = v.validate_watchlist_refs(initialized_workspace, framework_dir)
    assert oks == ["Sources/Watchlist/lowercase_name.ref.md"]
    assert len(errs) == 2 and all("watcher id 'fine' is claimed by 2 files" in e for e in errs)
    assert warns == []


def test_stray_ref_and_ref_txt_warnings(framework_dir: Path, initialized_workspace: Path) -> None:
    from superagent.tools.validate import stray_ref_warnings, watchlist_path

    ws = initialized_workspace
    (ws / "Sources" / "Vehicles").mkdir(parents=True)
    (ws / "Sources" / "Vehicles" / "manual.pdf").write_text("%PDF\n")
    (ws / "Sources" / "Vehicles" / "manual.pdf.ref.md").write_text("---\ntitle: legacy sidecar\n---\n")
    (ws / "Sources" / "Vehicles" / "manual.pdf.meta.md").write_text("---\ntitle: fine sidecar\n---\n")
    (ws / "Sources" / "notes").mkdir()
    (ws / "Sources" / "notes" / "loose.ref.txt").write_text("https://example.com\n")
    (ws / "Projects" / "x" / "Sources").mkdir(parents=True)
    (ws / "Projects" / "x" / "Sources" / "Portal.ref.md").write_text("---\ntitle: misplaced\n---\n")
    # Payment-confirmation sidecars live under a project's Resources/ (contracts/payment-confirmations.md).
    (ws / "Projects" / "x" / "Resources" / "orders").mkdir(parents=True)
    (ws / "Projects" / "x" / "Resources" / "orders" / "receipt.pdf").write_text("%PDF\n")
    (ws / "Projects" / "x" / "Resources" / "orders" / "receipt.pdf.ref.md").write_text("---\npayee: x\n---\n")
    (ws / "Sources" / "Watchlist" / "Ok.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    warns = stray_ref_warnings(ws, watchlist_path(ws))
    assert len(warns) == 4
    assert any(w.startswith("Projects/x/Resources/orders/receipt.pdf.ref.md: stray .ref.md")
               and "receipt.pdf.meta.md" in w for w in warns), "Resources/ is swept too"
    assert any(w.startswith("Sources/Vehicles/manual.pdf.ref.md: stray .ref.md outside the registry")
               and "manual.pdf.meta.md" in w for w in warns)
    assert any(w.startswith("Sources/notes/loose.ref.txt: `.ref.txt` is no longer supported") for w in warns)
    assert any(w.startswith("Projects/x/Sources/Portal.ref.md: stray .ref.md") for w in warns)


def test_validate_main_reports_registry_errors(framework_dir: Path, initialized_workspace: Path,
                                               capsys) -> None:
    from superagent.tools.validate import main as validate_main

    reg = initialized_workspace / "Sources" / "Watchlist"
    (reg / "Ok.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    assert validate_main(["--workspace", str(initialized_workspace),
                          "--framework", str(framework_dir)]) == 0
    out = capsys.readouterr().out
    assert "OK     Sources/Watchlist/Ok.ref.md" in out and "WARN" not in out
    (reg / "Bad.ref.md").write_text(_watch_ref("watch:\n  type: index_query\n"))
    (reg / "shouting.ref.md").write_text(_watch_ref("watch:\n  type: url\n  url: https://e.com\n"))
    (initialized_workspace / "Sources" / "loose.ref.txt").write_text("https://e.com\n")
    rc = validate_main(["--workspace", str(initialized_workspace), "--framework", str(framework_dir)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR  Sources/Watchlist/Bad.ref.md" in out
    assert "OK     Sources/Watchlist/Ok.ref.md" in out
    assert "OK     Sources/Watchlist/shouting.ref.md" in out, "a lowercase name is the user's choice"
    assert "shouting.ref.md: filename" not in out, "no naming warning on a user-named ref"
    assert "WARN   Sources/loose.ref.txt: `.ref.txt` is no longer supported" in out
    assert "1 warning(s)" in out, "registry warnings count as soft checks"


def test_validate_passes_without_data_sources_yaml(framework_dir: Path,
                                                   initialized_workspace: Path) -> None:
    """0.19.0 retired data-sources.yaml; a workspace without it validates clean."""
    from superagent.tools.validate import LIST_FILES
    from superagent.tools.validate import main as validate_main

    assert "data-sources.yaml" not in LIST_FILES
    assert not (initialized_workspace / "_memory" / "data-sources.yaml").exists()
    assert validate_main(["--workspace", str(initialized_workspace),
                          "--framework", str(framework_dir)]) == 0
