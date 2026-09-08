# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Pack loader, probes, and the real shipped packs (`tools/watchlist.py`, contract § 4)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from superagent.tools import watchlist as wl
from superagent.tools.ingest._base import ProbeStatus, RunResult, now_iso


@pytest.fixture
def fw(tmp_path: Path) -> Path:
    root = tmp_path / "framework"
    (root / "watchers").mkdir(parents=True)
    return root


@pytest.fixture
def ws(initialized_workspace: Path) -> Path:
    (initialized_workspace / "Sources" / "Watchlist").mkdir(parents=True, exist_ok=True)
    return initialized_workspace


def write_pack(root: Path, pack_id: str, manifest: dict[str, Any], *, folder: str | None = None) -> Path:
    d = root / "watchers" / (folder or pack_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.yaml").write_text(yaml.safe_dump({"watcher_version": 1, "id": pack_id, **manifest},
                                                sort_keys=False))
    return d


def write_ref(ws: Path, wid: str, watch: dict[str, Any], *, title: str = "t") -> Path:
    """A `ref_version: 2` ref at the Title_Case filename for `wid`."""
    path = ws / "Sources" / "Watchlist" / wl.ref_filename(wid)
    fm = {"ref_version": 2, "title": title, "watch": watch}
    path.write_text(f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n# Notes\n")
    return path


URL_PACK: dict[str, Any] = {
    "title": "Web page", "kind": "generic", "parameterized": True,
    "params": {"url": {"required": True}, "selector": {"required": False, "default": ""}},
    "detect": {"type": "url", "url": "{{url}}", "selector": "{{selector}}"},
    "probe": {"kind": "always"},
    "defaults": {"cycles": ["daily-update"], "evict_after_days": 30, "capture_mode": "automatic",
                 "min_check_interval_minutes": 720},
}


# ---------------------------------------------------------------------------
# discovery + collision
# ---------------------------------------------------------------------------


def test_discovery_scans_framework_and_custom(ws: Path, fw: Path) -> None:
    write_pack(fw, "url", URL_PACK)
    write_pack(ws / "_custom", "mine", {"title": "Mine", "kind": "local",
                                        "description": "  my\n  local  thing ",
                                        "detect": {"type": "path", "path": "/tmp/x"}})
    packs, errors = wl.discover_packs(fw, ws, announce=lambda m: None)
    assert errors == []
    assert set(packs) == {"url", "mine"}
    assert packs["url"].origin == "framework" and packs["mine"].origin == "custom"
    assert packs["url"].parameterized is True
    assert packs["url"].defaults["min_check_interval_minutes"] == 720
    assert packs["mine"].description == "my local thing"


def test_custom_pack_overrides_framework_with_verbatim_announcement(ws: Path, fw: Path, capsys: Any) -> None:
    write_pack(fw, "url", URL_PACK)
    write_pack(ws / "_custom", "url", {**URL_PACK, "title": "My url pack"})
    packs, _ = wl.discover_packs(fw, ws)
    assert packs["url"].origin == "custom" and packs["url"].title == "My url pack"
    assert capsys.readouterr().err.strip() == "Using `_custom/watchers/url` (overrides framework pack)."
    # A collision-free scan announces nothing.
    (ws / "_custom" / "watchers" / "url" / "pack.yaml").unlink()
    wl.discover_packs(fw, ws)
    assert capsys.readouterr().err == ""


def test_pack_validation_errors(ws: Path, fw: Path) -> None:
    write_pack(fw, "mismatch", {"title": "x", "detect": {"type": "url"}}, folder="other-folder")
    write_pack(fw, "iq", {"title": "x", "detect": {"type": "index_query"}})
    write_pack(fw, "rss", {"title": "x", "detect": {"type": "rss"}})  # no handler.py
    nodetect = write_pack(fw, "nodetect", {"title": "x", "detect": {"type": "feed"}})
    (nodetect / "handler.py").write_text("X = 1\n")  # handler without detect()
    write_pack(fw, "dotted", {"title": "x", "detect": {"type": "harvest"},
                              "harvest": {"handler": "superagent.tools.something"}})
    write_pack(fw, "noharvest", {"title": "x", "detect": {"type": "harvest"}})  # no handler.py
    write_pack(fw, "v2", {"title": "x", "detect": {"type": "url"}, "watcher_version": 2})
    (fw / "watchers" / "no-manifest").mkdir()
    (fw / "watchers" / "bad-yaml").mkdir()
    (fw / "watchers" / "bad-yaml" / "pack.yaml").write_text("detect: [unclosed\n")
    packs, errors = wl.discover_packs(fw, ws, announce=lambda m: None)
    assert packs == {}
    joined = "\n".join(errors)
    assert "must equal the folder name" in joined
    assert "not implemented in this release" in joined
    assert "'rss' is not built in and the pack ships no handler.py" in joined
    assert "'feed' is not built in and handler.py exposes no detect()" in joined
    assert "harvest.handler must be omitted" in joined
    assert "harvest declared but the pack ships no handler.py" in joined
    assert "unsupported watcher_version 2" in joined
    assert "bad-yaml" in joined
    assert len(errors) == 8


CUSTOM_HANDLER = '''
from superagent.tools.ingest._base import DetectContext, DetectError, DetectResult, RunResult, now_iso

CALLS = []


def detect(ctx: DetectContext) -> DetectResult:
    CALLS.append(("detect", ctx.watcher_id, dict(ctx.detect), ctx.dry_run))
    value = ctx.detect.get("channel", "")
    if value == "down":
        raise DetectError("feed offline")
    marker = ctx.workspace / f"feed-{value}.txt"
    return DetectResult(f"feed:{marker.read_text() if marker.exists() else 'none'}", f"channel {value}")


def harvest(config_row, dry_run=False, workspace=None):
    CALLS.append(("harvest", dict(config_row), dry_run, workspace))
    return RunResult(source="feed", started_at=now_iso(), finished_at=now_iso(),
                     items_pulled=2, items_inserted=2)
'''


def test_custom_pack_defines_its_own_detect_type_and_function_harvest(ws: Path, fw: Path) -> None:
    folder = write_pack(ws / "_custom", "feedwatch", {
        "title": "Feed", "kind": "api", "parameterized": True,
        "params": {"channel": {"required": True}},
        "detect": {"type": "feed", "channel": "{{channel}}"},
        "harvest": {"defaults": {"depth": 3}, "affected_domains": []},
        "probe": {"kind": "always"},
        "defaults": {"cycles": ["daily-update"], "evict_after_days": None},
    })
    (folder / "handler.py").write_text(CUSTOM_HANDLER)
    packs, errors = wl.discover_packs(fw, ws, announce=lambda m: None)
    assert errors == []
    pack = packs["feedwatch"]
    assert pack.provides_detect and pack.handler_path == folder / "handler.py"
    mod = pack.handler_module()
    assert pack.handler_module() is mod, "handler modules are cached per path"

    write_ref(ws, "news", {"pack": "feedwatch", "params": {"channel": "news"}}, title="News")
    write_ref(ws, "dead", {"pack": "feedwatch", "params": {"channel": "down"}}, title="Dead")
    (ws / "feed-news.txt").write_text("a")

    def run(**kw: Any) -> dict[str, Any]:
        ctx = wl.CheckContext(workspace=ws, framework=fw, cfg=wl.load_config(ws), cycle="daily-update", **kw)
        payload = wl.run_check(ctx)
        assert payload is not None
        return payload

    payload = run()
    assert [i["id"] for i in payload["unchanged"]] == ["news"]
    assert [i["id"] for i in payload["unreachable"]] == ["dead"]
    assert payload["unreachable"][0]["error"] == "feed offline"
    assert payload["summary"]["harvested"] == 0, "baseline never harvests"
    assert wl.load_state(ws)["watchers"]["news"]["fingerprint"] == "feed:a"

    (ws / "feed-news.txt").write_text("b")
    payload = run()
    assert [i["id"] for i in payload["changed"]] == ["news"]
    assert payload["summary"]["harvested"] == 1, "detect fired -> function harvest ran"
    harvest_calls = [c for c in mod.CALLS if c[0] == "harvest"]
    assert len(harvest_calls) == 1
    _, config_row, dry_run, workspace = harvest_calls[0]
    assert config_row["depth"] == 3 and config_row["channel"] == "news" and config_row["id"] == "news"
    assert dry_run is False and workspace == ws
    log = yaml.safe_load((ws / "_memory" / "ingestion-log.yaml").read_text())
    assert log["runs"][-1]["source"] == "feed"

    # `enable` describes a pack-defined type as "<pack title>: <primary param>"; no kind/source.
    target = wl.enable_pack(ws, fw, pack_id="feedwatch", watcher_id="sports",
                            params={"channel": "sports"}, title=None)
    assert target.name == "Sports.ref.md"
    fm, _ = wl.parse_frontmatter(target.read_text())
    assert fm is not None and fm["ref_version"] == 2
    assert fm["description"] == "Feed: sports"
    assert "kind" not in fm and "source" not in fm


# ---------------------------------------------------------------------------
# params templating + inheritance
# ---------------------------------------------------------------------------


def test_params_templating_defaults_and_required(ws: Path, fw: Path) -> None:
    write_pack(fw, "url", URL_PACK)
    packs, _ = wl.discover_packs(fw, ws, announce=lambda m: None)
    write_ref(ws, "ok", {"pack": "url", "params": {"url": "https://e.com/a", "selector": "#s"}}, title="ok")
    write_ref(ws, "missing", {"pack": "url"}, title="m")
    write_ref(ws, "conflict", {"pack": "url", "type": "path", "params": {"url": "https://x"}}, title="c")
    write_ref(ws, "nopack", {"pack": "ghost"}, title="n")
    write_ref(ws, "empty", {"pack": "url", "params": {"url": ""}}, title="e")
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), packs)
    assert [w.id for w in watchers] == ["ok"]
    ok = watchers[0]
    assert ok.detect == {"type": "url", "url": "https://e.com/a", "selector": "#s"}
    assert ok.evict_after_days == 30 and ok.min_check_interval_minutes == 720
    assert ok.cycles == ["daily-update"]
    by_id = {e["id"]: e["error"] for e in errors}
    assert "missing required param `url`" in by_id["missing"]
    assert "conflicts with pack" in by_id["conflict"]
    assert "unknown pack `ghost`" in by_id["nopack"]
    assert "url watcher needs its locator: watch.url" in by_id["empty"] and "pack `url`" in by_id["empty"]


def test_row_overrides_pack_defaults_and_unknown_placeholder_errors(ws: Path, fw: Path) -> None:
    write_pack(fw, "url", URL_PACK)
    write_pack(fw, "typo", {"title": "t", "detect": {"type": "url", "url": "{{nope}}"}})
    packs, _ = wl.discover_packs(fw, ws, announce=lambda m: None)
    write_ref(ws, "ov", {"pack": "url", "params": {"url": "https://x"},
                         "evict_after_days": None, "cycles": ["weekly-review"],
                         "min_check_interval_minutes": 5, "selector": "#row"}, title="o")
    write_ref(ws, "ty", {"pack": "typo"}, title="t")
    write_ref(ws, "sch", {"pack": "url", "params": {"url": "https://x"}, "schedule": "weekly"}, title="s")
    write_ref(ws, "loc", {"pack": "url", "params": {"url": "https://x"}, "url": "https://row-wins"}, title="l")
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), packs)
    sch = next(w for w in watchers if w.id == "sch")
    assert sch.cycles == ["weekly-review"], "row schedule beats the pack's default cycles"
    ov = next(w for w in watchers if w.id == "ov")
    assert ov.evict_after_days is None
    assert ov.cycles == ["weekly-review"]
    assert ov.min_check_interval_minutes == 5
    assert ov.detect["selector"] == "#row", "row value overrides the pack detect field"
    loc = next(w for w in watchers if w.id == "loc")
    assert loc.detect["url"] == "https://row-wins", "a row locator overrides the templated one"
    assert any("unknown pack param `nope`" in e["error"] for e in errors)


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------


def test_probe_kinds(ws: Path, tmp_path: Path) -> None:
    present = tmp_path / "present.txt"
    present.write_text("x")
    (ws / "_memory" / "rel.txt").write_text("y")
    cases: list[tuple[dict[str, Any] | None, str]] = [
        (None, ProbeStatus.AVAILABLE),
        ({"kind": "always"}, ProbeStatus.AVAILABLE),
        ({"kind": "file_exists", "path": str(present)}, ProbeStatus.AVAILABLE),
        ({"kind": "file_exists", "path": "_memory/rel.txt"}, ProbeStatus.AVAILABLE),
        ({"kind": "file_exists", "path": str(tmp_path / "absent"), "setup_hint": "make it"},
         ProbeStatus.NEEDS_SETUP),
        ({"kind": "cli_on_path", "cli": "sh"}, ProbeStatus.AVAILABLE),
        ({"kind": "cli_on_path", "cli": "definitely-not-a-cli-xyz"}, ProbeStatus.NOT_DETECTED),
        ({"kind": "python_import", "module": "json"}, ProbeStatus.AVAILABLE),
        ({"kind": "python_import", "module": "no_such_module_xyz"}, ProbeStatus.NOT_DETECTED),
        ({"kind": "teleport"}, ProbeStatus.NEEDS_SETUP),
    ]
    for probe, expected in cases:
        res = wl.run_probe(probe, workspace=ws, source="t")
        assert res.status == expected, (probe, res)
    # cmd_exit_zero runs a shell command from a pack file: gated like the cmd detect.
    gated = wl.run_probe({"kind": "cmd_exit_zero", "cmd": "true"}, workspace=ws, source="t")
    assert gated.status == ProbeStatus.NEEDS_SETUP
    assert "allow_cmd" in gated.detail and "allow_cmd" in gated.setup_hint
    ok = wl.run_probe({"kind": "cmd_exit_zero", "cmd": "true"}, workspace=ws, source="t", allow_cmd=True)
    assert ok.status == ProbeStatus.AVAILABLE
    bad = wl.run_probe({"kind": "cmd_exit_zero", "cmd": "false"}, workspace=ws, source="t", allow_cmd=True)
    assert bad.status == ProbeStatus.NEEDS_SETUP
    res = wl.run_probe({"kind": "file_exists", "path": str(tmp_path / "absent"), "setup_hint": "make it"},
                       workspace=ws, source="t")
    assert res.setup_hint == "make it" and "missing" in res.detail


def test_probe_home_expansion(ws: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tool").mkdir()
    (tmp_path / ".tool" / "token.json").write_text("{}")
    res = wl.run_probe({"kind": "file_exists", "path": "~/.tool/token.json"}, workspace=ws, source="t")
    assert res.status == ProbeStatus.AVAILABLE


# ---------------------------------------------------------------------------
# the real shipped packs
# ---------------------------------------------------------------------------


def test_shipped_packs_load_and_match_manifest(framework_dir: Path, ws: Path) -> None:
    packs, errors = wl.discover_packs(framework_dir, ws, announce=lambda m: None)
    assert errors == []
    manifest = yaml.safe_load((framework_dir / "watchers" / "_manifest.yaml").read_text())
    manifest_ids = {row["id"] for row in manifest["packs"]}
    assert set(packs) == manifest_ids
    assert {"simplefin", "gmail", "url", "subagent", "cmd", "path"} <= set(packs)
    for pack in packs.values():
        assert pack.origin == "framework"
        assert pack.detect_type in wl.DETECT_TYPES
        if pack.parameterized:
            assert pack.params, f"{pack.id}: parameterized without params"
        if pack.detect_type not in wl.BUILTIN_DETECT_TYPES:
            assert pack.provides_detect, f"{pack.id}: pack-defined type without handler.detect()"
    assert packs["simplefin"].detect_type == "harvest"
    assert "handler" not in packs["simplefin"].harvest, "folder-default handler.py is canonical"
    assert packs["simplefin"].handler_path == framework_dir / "watchers" / "simplefin" / "handler.py"
    assert (framework_dir / "watchers" / "simplefin" / "claim.py").is_file()
    assert packs["simplefin"].defaults["capture_mode"] == "automatic"
    assert packs["simplefin"].defaults["schedule"] == "daily"
    assert packs["simplefin"].defaults["cycles"] == ["daily-update"]
    assert packs["simplefin"].budget["max_calls_per_day"] == 24
    assert packs["gmail"].detect == {"type": "gmail", "query": "{{query}}"}
    assert packs["gmail"].provides_detect and not packs["gmail"].harvest
    # Nothing source-specific is left under tools/: the ingest package is the contract + csv only.
    ingest = sorted(p.name for p in (framework_dir / "tools" / "ingest").glob("*.py"))
    assert ingest == ["__init__.py", "_base.py", "csv.py"]
    assert not (framework_dir / "tools" / "simplefin_claim.py").exists()


def test_probe_no_args_lists_every_shipped_pack(framework_dir: Path, ws: Path, capsys: Any) -> None:
    import json

    rc = wl.main(["--workspace", str(ws), "--framework", str(framework_dir), "probe", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    manifest = yaml.safe_load((framework_dir / "watchers" / "_manifest.yaml").read_text())
    assert {r["pack"] for r in rows} == {row["id"] for row in manifest["packs"]}
    by_pack = {r["pack"]: r for r in rows}
    assert by_pack["simplefin"]["status"] == ProbeStatus.NEEDS_SETUP, "fresh workspace: no credentials"
    assert "watchers/simplefin/claim.py" in by_pack["simplefin"]["setup_hint"]
    assert by_pack["simplefin"]["probe"] == "file_exists"
    assert by_pack["subagent"]["status"] == ProbeStatus.AVAILABLE
    assert by_pack["url"]["status"] == ProbeStatus.AVAILABLE
    assert all(r["status"] != "error" for r in rows)


def test_enable_each_shipped_pack_yields_a_loadable_ref(framework_dir: Path, ws: Path) -> None:
    wl.enable_pack(ws, framework_dir, pack_id="simplefin", watcher_id=None, params={}, title=None)
    wl.enable_pack(ws, framework_dir, pack_id="gmail", watcher_id="gmail-bills",
                   params={"query": "label:Bills newer_than:30d"}, title=None)
    wl.enable_pack(ws, framework_dir, pack_id="url", watcher_id="permit",
                   params={"url": "https://permits.example/x", "selector": "#status"}, title="Permit")
    wl.enable_pack(ws, framework_dir, pack_id="subagent", watcher_id="portal",
                   params={"prompt": "Read the portal.\nReturn one line."}, title=None)
    wl.enable_pack(ws, framework_dir, pack_id="cmd", watcher_id="repo",
                   params={"cmd": "git ls-remote https://example.com/r HEAD"}, title=None)
    wl.enable_pack(ws, framework_dir, pack_id="path", watcher_id="drop_folder",
                   params={"path": "~/Downloads/statements"}, title=None)
    registry = ws / "Sources" / "Watchlist"
    assert sorted(p.name for p in registry.iterdir()) == [
        "Drop_Folder.ref.md", "Gmail-Bills.ref.md", "Permit.ref.md", "Portal.ref.md",
        "README.md", "Repo.ref.md", "Simplefin.ref.md",
    ], "enable writes Title_Case filenames"
    packs, _ = wl.discover_packs(framework_dir, ws, announce=lambda m: None)
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), packs)
    assert errors == []
    by_id = {w.id: w for w in watchers}
    assert set(by_id) == {"simplefin", "gmail-bills", "permit", "portal", "repo", "drop_folder"}
    assert by_id["simplefin"].type == "harvest"
    assert by_id["simplefin"].capture_mode == "automatic" and by_id["simplefin"].schedule == "daily"
    assert by_id["simplefin"].cycles == ["daily-update"]
    assert by_id["simplefin"].evict_after_days is None
    assert by_id["simplefin"].min_check_interval_minutes == 60
    assert by_id["simplefin"].description.startswith("Aggregated bank")
    assert by_id["gmail-bills"].detect["query"] == "label:Bills newer_than:30d"
    assert by_id["gmail-bills"].description == "label:Bills newer_than:30d"
    assert by_id["gmail-bills"].min_check_interval_minutes == 60
    assert by_id["permit"].detect["url"] == "https://permits.example/x"
    assert by_id["permit"].detect["selector"] == "#status"
    assert by_id["permit"].description == "https://permits.example/x"
    assert by_id["portal"].detect["prompt"] == "Read the portal.\nReturn one line."
    assert by_id["portal"].description == "Read the portal."
    assert by_id["repo"].type == "cmd" and by_id["repo"].locator == "git ls-remote https://example.com/r HEAD"
    assert by_id["drop_folder"].type == "path" and by_id["drop_folder"].locator == "~/Downloads/statements"
    for w in watchers:
        fm, _ = wl.parse_frontmatter(w.path.read_text())
        assert fm is not None and fm["ref_version"] == 2
        assert fm["added_by"] == "watch" and fm["added_at"]
        assert not any(isinstance(v, str) and v.startswith("<") and v.endswith(">") for v in fm.values())
        assert not (set(fm) & {"kind", "source", "ttl_minutes", "sensitive", "auth_ref"})


def _fake_simplefin_handler(calls: list[dict[str, Any]]) -> Any:
    class _Handler:
        def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
            calls.append({"row": dict(config_row), "dry_run": dry_run})
            return RunResult(source="simplefin", started_at=now_iso(), finished_at=now_iso(),
                             items_pulled=1, items_inserted=1)
    return _Handler()


def test_simplefin_daily_automatic_and_budget_gated(framework_dir: Path, ws: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """0.20.0: the daily-update check pulls SimpleFIN itself (capture_mode automatic,
    schedule daily); the budget and the 60-minute throttle are what protect the API."""
    wl.enable_pack(ws, framework_dir, pack_id="simplefin", watcher_id=None, params={}, title=None)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(wl, "load_handler", lambda pack, workspace: _fake_simplefin_handler(calls))

    def run(cycle: str, **kw: Any) -> dict[str, Any]:
        ctx = wl.CheckContext(workspace=ws, framework=framework_dir, cfg=wl.load_config(ws), cycle=cycle, **kw)
        payload = wl.run_check(ctx)
        assert payload is not None
        return payload

    daily = run("daily-update")
    assert daily["summary"]["harvested"] == 1 and daily["summary"]["dispatch"] == 0
    assert [i["id"] for i in daily["changed"]] == ["simplefin"], "first pull inserted rows"
    assert len(calls) == 1 and calls[0]["row"]["recency_window_days"] == 30
    row = wl.load_state(ws)["watchers"]["simplefin"]
    assert row["last_harvest"] and row["calls_today"] == 1

    # Same hour again: the throttle (pack default 60 min) skips it before the budget.
    again = run("daily-update")
    assert [i["id"] for i in again["skipped_throttled"]] == ["simplefin"]
    assert len(calls) == 1
    # Slower cycles include the daily watcher (nested cycles) — still budget-gated.
    state = wl.load_state(ws)
    state["watchers"]["simplefin"]["last_checked"] = "2000-01-01T00:00:00+00:00"
    wl.save_state(ws, state)
    weekly = run("weekly-review")
    assert [i["id"] for i in weekly["budget_exceeded"]] == ["simplefin"], "60 min not elapsed since last_harvest"
    assert weekly["skipped_cycle"] == [] and len(calls) == 1
    state = wl.load_state(ws)
    state["watchers"]["simplefin"]["last_harvest"] = "2000-01-01T00:00:00+00:00"
    wl.save_state(ws, state)
    monthly = run("monthly-review")
    assert monthly["summary"]["harvested"] == 1 and len(calls) == 2

    # `--no-harvest` never calls the handler; neither does a manual row (the tool never widens).
    state = wl.load_state(ws)
    state["watchers"]["simplefin"].update(last_checked="2000-01-01T00:00:00+00:00",
                                         last_harvest="2000-01-01T00:00:00+00:00")
    wl.save_state(ws, state)
    quiet = run("daily-update", no_harvest=True)
    assert [i["id"] for i in quiet["skipped_harvest"]] == ["simplefin"] and len(calls) == 2
    ref = ws / "Sources" / "Watchlist" / "Simplefin.ref.md"
    fm, body = wl.parse_frontmatter(ref.read_text())
    assert fm is not None
    fm["watch"]["capture_mode"] = "manual"
    ref.write_text(f"---\n{yaml.safe_dump(fm, sort_keys=False)}---\n{body}")
    manual = run("daily-update")
    assert manual["summary"]["harvested"] == 0 and manual["summary"]["dispatch"] == 1
    spec = manual["dispatch"][0]
    assert spec["kind"] == "harvest" and spec["id"] == "simplefin"
    assert spec["command"].endswith("harvest --id simplefin")
    assert len(calls) == 2, "a manual row is never pulled by a cadence check"
    # Explicit request works regardless of cadence (budget permitting).
    rc, payload = wl.run_explicit_harvest(ws, framework_dir, watcher_id="simplefin", dry_run=False)
    assert rc == 0 and payload["summary"]["harvested"] == 1 and len(calls) == 3
