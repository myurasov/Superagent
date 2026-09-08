# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/watchlist.py` — detect types, lifecycle, budget, effects, CLI.

Pack-loader and probe tests (including the real shipped packs) live in
`test_watchlist_packs.py`. Everything here runs against a temp workspace and
a temp *framework* dir (so the shipped packs under `superagent/watchers/`
never leak in) and never touches the network: url tests monkeypatch
`_http_open` or run a loopback `http.server`; gmail tests swap
`GMAIL_CLIENT_FACTORY` for a fake.
"""
from __future__ import annotations

import datetime as dt
import email.message
import http.server
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
import yaml

from superagent.tools import watchlist as wl

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fw(tmp_path: Path) -> Path:
    """A fake framework dir: empty `watchers/`, no watch.ref.md template."""
    root = tmp_path / "framework"
    (root / "watchers").mkdir(parents=True)
    (root / "templates" / "sources").mkdir(parents=True)
    return root


@pytest.fixture
def ws(initialized_workspace: Path) -> Path:
    """An initialized workspace with the registry folder present."""
    (initialized_workspace / "Sources" / "Watchlist").mkdir(parents=True, exist_ok=True)
    return initialized_workspace


def write_ref(ws: Path, wid: str, fm: dict[str, Any], body: str = "# Notes\n") -> Path:
    path = ws / "Sources" / "Watchlist" / f"{wid}.ref.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    path.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return path


def set_config(ws: Path, **watchlist_prefs: Any) -> None:
    path = ws / "_memory" / "config.yaml"
    cfg = yaml.safe_load(path.read_text()) or {}
    cfg.setdefault("preferences", {})["watchlist"] = watchlist_prefs
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def check(ws: Path, fw: Path, cycle: str = "daily-update", **kw: Any) -> dict[str, Any]:
    ctx = wl.CheckContext(workspace=ws, framework=fw, cfg=wl.load_config(ws), cycle=cycle, **kw)
    payload = wl.run_check(ctx)
    assert payload is not None
    return payload


def rows(ws: Path) -> dict[str, Any]:
    return wl.load_state(ws)["watchers"]


def state_row(ws: Path, wid: str) -> dict[str, Any]:
    return rows(ws)[wid]


def patch_state(ws: Path, wid: str, **fields: Any) -> None:
    state = wl.load_state(ws)
    state["watchers"].setdefault(wid, wl.default_row()).update(fields)
    wl.save_state(ws, state)


def iso_ago(**delta: Any) -> str:
    return (dt.datetime.now().astimezone() - dt.timedelta(**delta)).isoformat(timespec="seconds")


def ids(items: list[dict[str, Any]]) -> list[str]:
    return [i["id"] for i in items]


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


class FakeResp:
    """Just enough of a urllib response for `detect_url`."""

    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None):
        self.body = body
        self.pos = 0
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk, self.pos = self.body[self.pos:], len(self.body)
        else:
            chunk, self.pos = self.body[self.pos:self.pos + n], min(self.pos + n, len(self.body))
        return chunk

    def __enter__(self) -> FakeResp:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def fake_http(monkeypatch: pytest.MonkeyPatch, response: Any) -> list[dict[str, Any]]:
    """Route `_http_open` to one canned response (FakeResp, Exception, or callable(headers))."""
    calls: list[dict[str, Any]] = []

    def _open(url: str, method: str, timeout: int, headers: dict[str, str] | None = None) -> FakeResp:
        calls.append({"url": url, "method": method, "headers": dict(headers or {})})
        value = response(headers or {}) if callable(response) and not isinstance(response, FakeResp) else response
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(wl, "_http_open", _open)
    return calls


def http_304() -> HTTPError:
    return HTTPError("https://e.com", 304, "Not Modified", email.message.Message(), None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# config + registry basics
# ---------------------------------------------------------------------------


def test_config_defaults_when_block_absent(ws: Path) -> None:
    cfg = wl.load_config(ws)
    assert cfg.path == "Sources/Watchlist"
    assert cfg.cycles == ["daily-update"]
    assert cfg.evict_after_days == 14
    assert cfg.allow_cmd is False
    assert cfg.min_check_interval_minutes is None


def test_config_block_overrides(ws: Path) -> None:
    set_config(ws, path="Sources/Watch", cycles=["weekly-review"], evict_after_days=None,
               allow_cmd=True, min_check_interval_minutes=30)
    cfg = wl.load_config(ws)
    assert cfg.path == "Sources/Watch"
    assert cfg.cycles == ["weekly-review"]
    assert cfg.evict_after_days is None
    assert cfg.allow_cmd is True
    assert cfg.min_check_interval_minutes == 30


def test_absent_folder_is_feature_off(initialized_workspace: Path, fw: Path, capsys: Any) -> None:
    folder = initialized_workspace / "Sources" / "Watchlist"
    if folder.exists():
        shutil.rmtree(folder)
    rc = wl.main(["--workspace", str(initialized_workspace), "--framework", str(fw),
                  "check", "--cycle", "daily-update"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "{}"
    assert wl.load_state(initialized_workspace)["watchers"] == {}
    rc = wl.main(["--workspace", str(initialized_workspace), "--framework", str(fw),
                  "check", "--cycle", "daily-update", "--report"])
    assert rc == 0
    assert capsys.readouterr().out == ""
    rc = wl.main(["--workspace", str(initialized_workspace), "--framework", str(fw), "list", "--json"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_shared_constants_come_from_validate() -> None:
    from superagent.tools import validate

    assert wl.DETECT_TYPES is validate.WATCH_TYPES
    assert wl.KIND_TO_TYPE is validate.WATCH_TYPE_BY_REF_KIND
    assert set(wl.RESERVED_TYPES) == set(validate.WATCH_RESERVED_TYPES)
    assert wl.DEFAULT_CONFIG["path"] == validate.DEFAULT_WATCHLIST_PATH


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("url", "url"), ("cli", "cmd"), ("file", "path"), ("manual", "subagent")],
)
def test_type_defaults_from_ref_kind(ws: Path, kind: str, expected: str) -> None:
    fm: dict[str, Any] = {"ref_version": 1, "title": "t", "kind": kind, "source": "x"}
    if kind == "manual":
        fm["watch"] = {"prompt": "look"}
    write_ref(ws, "k-test", fm)
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert errors == []
    assert watchers[0].type == expected


def test_kind_without_locator_is_a_file_line_error(ws: Path) -> None:
    write_ref(ws, "api-x", {"ref_version": 1, "title": "t", "kind": "api", "source": "x"})
    write_ref(ws, "no-kind", {"ref_version": 1, "title": "t", "source": "x"})
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert watchers == []
    by_id = {e["id"]: e for e in errors}
    assert by_id["api-x"]["error"].startswith("Sources/Watchlist/api-x.ref.md:")
    # `api` / `mcp` / `vault` refs have NO default detect type (contract § 2):
    # they must name a pack or an explicit type.
    assert "cannot infer watch.type from kind 'api'" in by_id["api-x"]["error"]
    assert by_id["api-x"]["line"] >= 1
    assert "cannot infer watch.type" in by_id["no-kind"]["error"]


def test_index_query_is_rejected(ws: Path) -> None:
    write_ref(ws, "iq", {"ref_version": 1, "title": "t", "kind": "file", "source": "x",
                         "watch": {"type": "index_query", "file": "_memory/bills.yaml"}})
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert watchers == []
    assert "not implemented in this release" in errors[0]["error"]
    # The reported line points at the offending key inside the watch block.
    text = (ws / "Sources" / "Watchlist" / "iq.ref.md").read_text().splitlines()
    assert "type: index_query" in text[errors[0]["line"] - 1]


def test_bad_id_and_bad_frontmatter_are_reported_not_fatal(ws: Path, fw: Path) -> None:
    (ws / "Sources" / "Watchlist" / "Bad_Name.ref.md").write_text(
        "---\ntitle: x\nkind: url\nsource: https://e.com\n---\n"
    )
    (ws / "Sources" / "Watchlist" / "dot.name.ref.md").write_text(
        "---\ntitle: x\nkind: url\nsource: https://e.com\n---\n"
    )
    (ws / "Sources" / "Watchlist" / "no-front.ref.md").write_text("just text\n")
    (ws / "Sources" / "Watchlist" / "README.md").write_text("# not a watcher\n")
    write_ref(ws, "home_assistant-hub", {"ref_version": 1, "title": "ok", "kind": "file",
                                           "source": str(ws / "_memory" / "config.yaml")})
    payload = check(ws, fw)
    assert payload["summary"]["errors"] == 3
    assert sorted(e["id"] for e in payload["errors"]) == ["Bad_Name", "dot.name", "no-front"]
    bad = next(e for e in payload["errors"] if e["id"] == "Bad_Name")
    assert "not a valid watcher id" in bad["error"] and "bad_name.ref.md" in bad["error"]
    assert ids(payload["unchanged"]) == ["home_assistant-hub"], "underscores are fine"


def test_schedule_stands_in_for_cycles_and_null_keys_pin(ws: Path) -> None:
    write_ref(ws, "sched", {"ref_version": 1, "title": "t", "kind": "url",
                            "source": "https://e.com", "watch": {"schedule": "weekly"}})
    write_ref(ws, "manual", {"ref_version": 1, "title": "t", "kind": "url",
                             "source": "https://e.com", "watch": {"schedule": "manual"}})
    write_ref(ws, "pinned", {"ref_version": 1, "title": "t", "kind": "url",
                             "source": "https://e.com",
                             "watch": {"evict_after_days": None, "cycles": None,
                                       "min_check_interval_minutes": None}})
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert errors == []
    by_id = {w.id: w for w in watchers}
    assert by_id["sched"].cycles == ["weekly-review"] and by_id["sched"].schedule == "weekly"
    assert by_id["manual"].cycles == []
    assert by_id["pinned"].evict_after_days is None, "explicit null = never, not inherit 14"
    assert by_id["pinned"].cycles == []
    assert by_id["pinned"].min_check_interval_minutes is None


def test_related_fields_and_handle(ws: Path) -> None:
    write_ref(ws, "rel", {"ref_version": 1, "title": "t", "kind": "url", "source": "https://e.com",
                          "related_domain": "home", "related_project": "solar", "tags": ["a"]})
    watchers, _ = wl.load_registry(ws, wl.load_config(ws), {})
    w = watchers[0]
    assert w.handle == "watch:rel"
    assert w.related == {"related_domain": "home", "related_project": "solar"}
    assert w.tags == ["a"]


# ---------------------------------------------------------------------------
# detect: path
# ---------------------------------------------------------------------------


def test_path_file_baseline_then_change_writes_effects(ws: Path, fw: Path) -> None:
    target = ws / "watched.txt"
    target.write_text("v1")
    write_ref(ws, "doc", {"ref_version": 1, "title": "A doc", "kind": "file",
                          "source": str(target), "related_domain": "home"})
    first = check(ws, fw)
    assert first["summary"]["checked"] == 1
    assert ids(first["unchanged"]) == ["doc"]
    assert "baseline" in first["unchanged"][0]["detail"]
    row = state_row(ws, "doc")
    assert row["fingerprint"].startswith("sha256:")
    assert row["last_outcome"] == "unchanged"
    assert row["baseline_at"] is not None
    assert row["last_changed"] is None

    # Edges exist from load, before any change (contract § 9).
    world = read_yaml(ws / "_memory" / "world.yaml")
    edges = {(e["from"], e["to"], e["kind"]) for e in world["edges"]}
    assert ("watch:doc", "domain:home", "related_domain") in edges
    assert any(n["id"] == "watch:doc" and n["kind"] == "watch" for n in world["nodes"])
    world_text = (ws / "_memory" / "world.yaml").read_text()

    second = check(ws, fw)
    assert ids(second["unchanged"]) == ["doc"]
    assert second["changed"] == []
    assert (ws / "_memory" / "world.yaml").read_text() == world_text, "no churn when edges exist"

    target.write_text("v2")
    third = check(ws, fw)
    assert ids(third["changed"]) == ["doc"]
    item = third["changed"][0]
    ts = third["checked_at"]
    assert item["effects"]["alert"] == f"[watch:doc] {ts}: file sha256"
    assert item["effects"]["interaction_log_id"].startswith("ilog-")
    assert state_row(ws, "doc")["last_changed"] == ts

    ctx = read_yaml(ws / "_memory" / "context.yaml")
    assert ctx["alerts"][-1] == f"[watch:doc] {ts}: file sha256"
    assert (ws / "_memory" / "context.yaml").read_text().startswith("#"), "header comments survive"

    ilog = read_yaml(ws / "_memory" / "interaction-log.yaml")
    entry = ilog["entries"][-1]
    assert entry["skill"] == "watch"
    assert entry["action"] == "watch_change_detected"
    assert entry["summary"].startswith("watch:doc — ")
    assert entry["related_domain"] == "home"
    assert entry["related_project"] is None
    assert entry["ingestion_log_ref"] is None
    assert all(e.get("id") for e in ilog["entries"]), "template placeholder row dropped"


def test_one_live_alert_per_watcher_prior_moves_to_archive(ws: Path, fw: Path) -> None:
    target = ws / "w.txt"
    target.write_text("1")
    write_ref(ws, "w", {"ref_version": 1, "title": "W", "kind": "file", "source": str(target)})
    ctx_path = ws / "_memory" / "context.yaml"
    ctx = read_yaml(ctx_path)
    ctx["alerts"] = ["unrelated alert stays"]
    ctx_path.write_text(yaml.safe_dump(ctx, sort_keys=False))
    check(ws, fw)
    target.write_text("2")
    first = check(ws, fw)["changed"][0]["effects"]["alert"]
    target.write_text("3")
    second = check(ws, fw)["changed"][0]["effects"]["alert"]
    alerts = read_yaml(ctx_path)["alerts"]
    assert alerts == ["unrelated alert stays", second]
    archived = read_yaml(ws / "_memory" / "alerts-archive.yaml")["archived"]
    assert archived[-1]["text"] == first
    assert archived[-1]["kind"] == "alert"
    assert archived[-1]["archived_at"]


def test_path_dir_fingerprint_tracks_entries(ws: Path, fw: Path) -> None:
    folder = ws / "drop"
    folder.mkdir()
    (folder / "a.csv").write_text("a")
    write_ref(ws, "drop", {"ref_version": 1, "title": "t", "kind": "file", "source": str(folder)})
    check(ws, fw)
    assert state_row(ws, "drop")["fingerprint"].startswith("dir:")
    (folder / "b.csv").write_text("b")
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["drop"]


def test_path_missing_is_unreachable(ws: Path, fw: Path) -> None:
    write_ref(ws, "gone", {"ref_version": 1, "title": "t", "kind": "file",
                           "source": str(ws / "nope.txt")})
    payload = check(ws, fw)
    assert ids(payload["unreachable"]) == ["gone"]
    row = state_row(ws, "gone")
    assert row["error_streak"] == 1
    assert row["error_since"] is not None
    assert "path not found" in row["last_error"]
    assert row["last_outcome"] == "unreachable"
    check(ws, fw)
    assert state_row(ws, "gone")["error_streak"] == 2


# ---------------------------------------------------------------------------
# detect: url
# ---------------------------------------------------------------------------


def test_url_conditional_get_and_304(ws: Path, fw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_http(monkeypatch, FakeResp(b"<h1>A</h1>", {"ETag": '"abc"', "Last-Modified": "Mon"}))
    write_ref(ws, "page", {"ref_version": 1, "title": "t", "kind": "url", "source": "https://e.com/x"})
    payload = check(ws, fw)
    assert calls[0]["method"] == "GET" and calls[0]["headers"] == {}
    row = state_row(ws, "page")
    assert row["fingerprint"].startswith("sha256:")
    assert row["validators"] == {"etag": '"abc"', "last_modified": "Mon"}
    assert any("First check for url watcher `page`: e.com/x" in n for n in payload["notes"])

    # Validators go out; a 304 is `unchanged` with no body download.
    calls = fake_http(monkeypatch, http_304())
    payload = check(ws, fw)
    assert calls[0]["headers"] == {"If-None-Match": '"abc"', "If-Modified-Since": "Mon"}
    assert ids(payload["unchanged"]) == ["page"]
    assert "304" in payload["unchanged"][0]["detail"]
    assert state_row(ws, "page")["fingerprint"] == row["fingerprint"]

    # A rotated ETag with identical content is NOT a change.
    fake_http(monkeypatch, FakeResp(b"<h1>A</h1>", {"ETag": '"rotated"'}))
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["page"]
    assert state_row(ws, "page")["validators"] == {"etag": '"rotated"'}

    fake_http(monkeypatch, FakeResp(b"<h1>B</h1>"))
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["page"]
    assert "validators" not in state_row(ws, "page"), "no validators on the last 200"


def test_url_content_hash_strips_noise_and_scopes_selector(
    ws: Path, fw: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def page(stamp: str, status: str, footer: str = "v1") -> bytes:
        return (
            f"<html><head><style>.x{{}}</style><script>var t='{stamp}';</script></head>"
            f"<body><!-- built {stamp} --><div id='status-panel'>Status: {status}</div>"
            f"<footer>Rendered by {footer}</footer></body></html>"
        ).encode()

    fake_http(monkeypatch, FakeResp(page("1", "pending")))
    write_ref(ws, "permit", {"ref_version": 1, "title": "t", "kind": "url",
                             "source": "https://permits.example/x"})
    check(ws, fw)
    fake_http(monkeypatch, FakeResp(page("2", "pending")))
    assert ids(check(ws, fw)["unchanged"]) == ["permit"], "script/comment stamps are stripped"
    fake_http(monkeypatch, FakeResp(page("3", "pending", footer="v2")))
    assert ids(check(ws, fw)["changed"]) == ["permit"], "visible footer text flaps without a selector"

    write_ref(ws, "permit", {"ref_version": 1, "title": "t", "kind": "url",
                             "source": "https://permits.example/x",
                             "watch": {"selector": "#status-panel"}})
    fake_http(monkeypatch, FakeResp(page("4", "pending", footer="v3")))
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["permit"], "scoping changes the fingerprint once"
    fake_http(monkeypatch, FakeResp(page("5", "pending", footer="v4")))
    assert ids(check(ws, fw)["unchanged"]) == ["permit"], "footer churn is outside the selector"
    fake_http(monkeypatch, FakeResp(page("6", "approved", footer="v4")))
    assert ids(check(ws, fw)["changed"]) == ["permit"]
    fake_http(monkeypatch, FakeResp(b"<html><body>panel gone</body></html>"))
    payload = check(ws, fw)
    assert ids(payload["unreachable"]) == ["permit"]
    assert "matched nothing" in payload["unreachable"][0]["error"]


def test_url_ignore_patterns_and_min_change_interval(
    ws: Path, fw: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ref(ws, "flap", {"ref_version": 1, "title": "t", "kind": "url", "source": "https://e.com",
                           "watch": {"ignore_patterns": [r"\d{2}:\d{2}:\d{2}"],
                                     "min_change_interval_minutes": 1440}})
    fake_http(monkeypatch, FakeResp(b"<p>clock 10:00:01</p><p>A</p>"))
    check(ws, fw)
    fake_http(monkeypatch, FakeResp(b"<p>clock 10:00:02</p><p>A</p>"))
    assert ids(check(ws, fw)["unchanged"]) == ["flap"], "ignored pattern must not flap"
    fake_http(monkeypatch, FakeResp(b"<p>B</p>"))
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["flap"]
    changed_at = state_row(ws, "flap")["last_changed"]
    fp_b = state_row(ws, "flap")["fingerprint"]
    fake_http(monkeypatch, FakeResp(b"<p>C</p>"))
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["flap"]
    assert "suppressed" in payload["unchanged"][0]["detail"]
    row = state_row(ws, "flap")
    assert row["last_changed"] == changed_at, "last_changed untouched"
    assert row["fingerprint"] != fp_b, "fingerprint advanced silently"
    assert len(read_yaml(ws / "_memory" / "context.yaml")["alerts"]) == 1, "no second alert"


def test_url_errors_are_unreachable(ws: Path, fw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_http(monkeypatch, HTTPError("https://e.com", 500, "boom", None, None))  # type: ignore[arg-type]
    write_ref(ws, "down", {"ref_version": 1, "title": "t", "kind": "url", "source": "https://e.com"})
    write_ref(ws, "ftp", {"ref_version": 1, "title": "t", "kind": "url", "source": "ftp://e.com"})
    write_ref(ws, "creds", {"ref_version": 1, "title": "t", "kind": "url",
                            "source": "https://user:pw@e.com/x"})
    payload = check(ws, fw)
    assert sorted(ids(payload["unreachable"])) == ["creds", "down", "ftp"]
    errors = {i["id"]: i["error"] for i in payload["unreachable"]}
    assert "HTTP 500" in errors["down"]
    assert "http://" in errors["ftp"]
    assert "userinfo" in errors["creds"]


def test_url_against_loopback_http_server(ws: Path, fw: Path) -> None:
    body = {"html": b"<html><script>x=1</script><h1>Hello</h1></html>"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body["html"])

        def log_message(self, *args: Any) -> None:
            return None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/status"
        write_ref(ws, "loop", {"ref_version": 1, "title": "t", "kind": "url", "source": url})
        check(ws, fw, timeout=5)
        body["html"] = b"<html><script>x=2</script><h1>Hello</h1></html>"
        assert ids(check(ws, fw, timeout=5)["unchanged"]) == ["loop"]
        body["html"] = b"<html><h1>Goodbye</h1></html>"
        assert ids(check(ws, fw, timeout=5)["changed"]) == ["loop"]
    finally:
        server.shutdown()
        server.server_close()


def test_url_body_read_respects_the_timeout_budget(ws: Path, fw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_http(monkeypatch, FakeResp(b"<p>" + b"x" * (3 * wl.URL_READ_CHUNK) + b"</p>"))
    clock = iter([0.0, 0.0, 1.0, 100.0, 100.0, 100.0, 100.0])  # deadline, then reads
    monkeypatch.setattr(wl, "_monotonic", lambda: next(clock, 100.0))
    write_ref(ws, "slow", {"ref_version": 1, "title": "t", "kind": "url", "source": "https://e.com/slow"})
    payload = check(ws, fw, timeout=5)
    assert ids(payload["unreachable"]) == ["slow"]
    assert "exceeded the 5s timeout budget" in payload["unreachable"][0]["error"]
    # A fast body is read to completion in chunks (stream semantics), not truncated.
    fake_http(monkeypatch, FakeResp(b"<p>" + b"y" * (3 * wl.URL_READ_CHUNK) + b"</p>"))
    monkeypatch.setattr(wl, "_monotonic", lambda: 0.0)
    payload = check(ws, fw, timeout=5)
    assert ids(payload["unchanged"]) == ["slow"], "baseline after the failed first attempt"


def test_bare_harvest_type_needs_a_pack(ws: Path) -> None:
    write_ref(ws, "bare_h", {"ref_version": 1, "title": "t", "kind": "api", "source": "x",
                             "watch": {"type": "harvest"}})
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert watchers == []
    assert "needs a pack" in errors[0]["error"]


def test_validate_passes_after_a_real_check(ws: Path, framework_dir: Path, capsys: Any) -> None:
    from superagent.tools.validate import main as validate_main

    target = _file_watcher(ws)
    check(ws, framework_dir)
    target.write_text("v2")
    check(ws, framework_dir)  # writes state, an alert, an interaction-log row, world edges
    assert (ws / "_memory" / "watchlist-state.yaml").read_text().startswith("#"), "template header kept"
    assert wl.load_state(ws)["last_updated"]
    rc = validate_main(["--workspace", str(ws), "--framework", str(framework_dir)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ERROR" not in out


def test_select_html_simple_selectors() -> None:
    html = ('<div class="a b"><p id="x">one</p><p class="b">two<span>2</span></p></div>'
            '<div id="y">three</div>')
    assert wl.select_html(html, "#x") == '<p id="x">one</p>'
    assert wl.select_html(html, "p.b") == '<p class="b">two<span>2</span></p>'
    assert wl.select_html(html, "div#y") == '<div id="y">three</div>'
    assert wl.select_html(html, "div") == html[: html.index('<div id="y">')]
    assert wl.select_html(html, "#missing") is None
    with pytest.raises(wl.DetectError, match="unsupported selector"):
        wl.select_html(html, "div > p")


# ---------------------------------------------------------------------------
# detect: cmd
# ---------------------------------------------------------------------------


def test_cmd_disabled_by_default(ws: Path, fw: Path) -> None:
    write_ref(ws, "gh", {"ref_version": 1, "title": "t", "kind": "cli", "source": "echo hi"})
    payload = check(ws, fw)
    assert ids(payload["unreachable"]) == ["gh"]
    assert payload["unreachable"][0]["error"] == wl.CMD_DISABLED_REASON


def test_cmd_allowed_hashes_stdout_and_requires_exit_zero(ws: Path, fw: Path) -> None:
    set_config(ws, allow_cmd=True)
    data = ws / "data.txt"
    data.write_text("one")
    write_ref(ws, "cat", {"ref_version": 1, "title": "t", "kind": "cli", "source": f"cat '{data}'"})
    write_ref(ws, "fail", {"ref_version": 1, "title": "t", "kind": "cli", "source": "exit 3"})
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["cat"]
    assert ids(payload["unreachable"]) == ["fail"]
    assert "exit 3" in payload["unreachable"][0]["error"]
    assert any("`cat` runs: cat" in n for n in payload["notes"]), "first use shows the command"
    assert wl.render_report(payload) != "", "the first-use note is surfaced in a briefing"
    data.write_text("two")
    assert ids(check(ws, fw)["changed"]) == ["cat"]


# ---------------------------------------------------------------------------
# detect: subagent + stamp
# ---------------------------------------------------------------------------


def test_subagent_dispatch_then_stamp_cycle(ws: Path, fw: Path) -> None:
    write_ref(ws, "portal", {
        "ref_version": 1, "title": "Installer portal", "kind": "manual",
        "source": "sign in per browserctl notes", "related_project": "solar",
        "watch": {"prompt": "Read the milestone page; return a ONE-LINE delta."},
    })
    payload = check(ws, fw)
    assert payload["summary"]["dispatch"] == 1
    assert payload["summary"]["checked"] == 0
    spec = payload["dispatch"][0]
    assert spec["kind"] == "subagent"
    assert spec["id"] == "portal"
    assert spec["handle"] == "watch:portal"
    assert spec["prompt"].startswith("Read the milestone page")
    assert wl.RETENTION_NOTE in spec["prompt"]
    assert spec["previous_note"] is None
    assert spec["previous_note_label"] == wl.PREVIOUS_NOTE_LABEL
    assert spec["source"] == "sign in per browserctl notes"
    assert spec["return_shape"] == wl.RETURN_SHAPE
    assert "stamp --id portal" in spec["stamp_command"]
    assert wl.render_report(payload) != "", "a dispatch is never quiet"

    noisy = "Payment\x00 due\n\t$1,200 by 2026-10-01 " + "x" * 600
    rc, res = wl.run_stamp(ws, fw, watcher_id="portal", outcome="changed", note=noisy)
    assert rc == 0
    assert "\x00" not in res["note"] and "\n" not in res["note"]
    assert len(res["note"]) == wl.MAX_NOTE_CHARS
    assert res["note"].startswith("Payment due $1,200 by 2026-10-01")
    row = state_row(ws, "portal")
    assert row["fingerprint"] == res["note"]
    assert row["last_outcome"] == "changed"
    assert row["baseline_at"] is not None
    assert row["last_changed"] == row["last_success"] == row["last_checked"]
    alerts = read_yaml(ws / "_memory" / "context.yaml")["alerts"]
    assert alerts[-1] == f"[watch:portal] {row['last_changed']}: {res['note']}"
    ilog = read_yaml(ws / "_memory" / "interaction-log.yaml")
    assert ilog["entries"][-1]["related_project"] == "solar"
    assert ilog["entries"][-1]["action"] == "watch_change_detected"

    # The next dispatch carries the note as labelled data, never inside the prompt.
    spec = check(ws, fw)["dispatch"][0]
    assert spec["previous_note"] == res["note"]
    assert res["note"] not in spec["prompt"]

    rc, res = wl.run_stamp(ws, fw, watcher_id="portal", outcome="unchanged", note=None)
    assert rc == 0
    assert state_row(ws, "portal")["last_outcome"] == "unchanged"
    assert state_row(ws, "portal")["error_streak"] == 0

    rc, res = wl.run_stamp(ws, fw, watcher_id="portal", outcome="unreachable", note="portal 502")
    assert rc == 0
    row = state_row(ws, "portal")
    assert row["error_streak"] == 1
    assert row["last_error"] == "portal 502"

    rc, res = wl.run_stamp(ws, fw, watcher_id="nope", outcome="changed", note="x")
    assert rc == 2
    assert "no watcher" in res["error"]


def test_subagent_about_to_be_evicted_is_not_dispatched(ws: Path, fw: Path) -> None:
    write_ref(ws, "portal", {"ref_version": 1, "title": "P", "kind": "manual", "source": "x",
                             "watch": {"prompt": "look", "evict_after_days": 7}})
    assert check(ws, fw)["summary"]["dispatch"] == 1
    patch_state(ws, "portal", baseline_at=iso_ago(days=8), last_success=iso_ago(days=2))
    payload = check(ws, fw)
    assert payload["dispatch"] == []
    assert ids(payload["evicted"]) == ["portal"]
    assert state_row(ws, "portal")["status"] == "evicted"
    # Stamping an evicted watcher revives it AND re-baselines: the note is the new baseline.
    rc, res = wl.run_stamp(ws, fw, watcher_id="portal", outcome="changed", note="back")
    assert rc == 0 and res["revived"] is True
    row = state_row(ws, "portal")
    assert row["status"] == "active" and row["evicted_at"] is None and row["error_streak"] == 0
    assert row["fingerprint"] == "back"
    assert row["baseline_at"] == row["last_changed"] == row["last_checked"]


def test_subagent_without_prompt_is_a_registry_error(ws: Path) -> None:
    write_ref(ws, "np", {"ref_version": 1, "title": "t", "kind": "manual", "source": "x"})
    _, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert "needs watch.prompt" in errors[0]["error"]


# ---------------------------------------------------------------------------
# detect: gmail — the shipped pack's handler.py with a fake API client
# ---------------------------------------------------------------------------


class FakeGmail:
    def __init__(self, messages: list[dict[str, Any]]):
        self.messages = messages  # newest first, each {id, internalDate, subject}
        self.list_calls = 0
        self.get_calls: list[str] = []

    def list_messages(self, query: str, max_results: int) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [{"id": m["id"], "threadId": "t" + m["id"]} for m in self.messages[:max_results]]

    def get_metadata(self, message_id: str) -> dict[str, Any]:
        self.get_calls.append(message_id)
        m = next(x for x in self.messages if x["id"] == message_id)
        return {
            "id": m["id"], "threadId": "t" + m["id"], "labelIds": ["INBOX"],
            "snippet": "snip", "internalDate": str(m["internalDate"]),
            "payload": {"headers": [{"name": "Subject", "value": m["subject"]},
                                    {"name": "From", "value": "Bills <bills@example.com>"}]},
        }


def _msgs(n: int, start_ms: int = 1_757_000_000_000) -> list[dict[str, Any]]:
    return [{"id": f"m{start_ms + i}", "internalDate": start_ms + i * 1000, "subject": f"Bill {i}"}
            for i in range(n - 1, -1, -1)]


def gmail_handler(framework_dir: Path, ws: Path) -> Any:
    packs, errors = wl.discover_packs(framework_dir, ws, announce=lambda m: None)
    assert errors == []
    return packs["gmail"].handler_module()


def test_gmail_fingerprint_capture_and_get_cap(
    ws: Path, framework_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from superagent.tools.email import archive

    gm = gmail_handler(framework_dir, ws)
    fake = FakeGmail(_msgs(15))
    monkeypatch.setattr(gm, "CLIENT_FACTORY", lambda: fake)
    write_ref(ws, "gmail_bills", {"ref_version": 1, "title": "Bills mail", "kind": "api",
                                  "source": "gmail:label:Bills newer_than:30d",
                                  "watch": {"pack": "gmail",
                                            "params": {"query": "label:Bills newer_than:30d"}}})
    packs, _ = wl.discover_packs(framework_dir, ws, announce=lambda m: None)
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), packs)
    assert errors == []
    assert watchers[0].type == "gmail"
    assert watchers[0].detect["query"] == "label:Bills newer_than:30d"
    assert watchers[0].min_check_interval_minutes == 60, "pack default throttle"

    payload = check(ws, framework_dir)
    assert ids(payload["unchanged"]) == ["gmail_bills"]
    assert fake.list_calls == 1
    assert len(fake.get_calls) == gm.MAX_GETS == 10, "at most 10 metadata gets per check"
    newest = fake.messages[0]
    newest_iso = archive._internal_date_utc(str(newest["internalDate"]))
    assert state_row(ws, "gmail_bills")["fingerprint"] == f"gmail:{newest['id']}:{newest_iso}"
    assert "15 match(es)" in payload["unchanged"][0]["detail"]
    rec = archive.find(newest["id"], workspace=ws)
    assert rec is not None and rec.kind == "stub" and rec.subject == newest["subject"]
    assert rec.provenance["source"] == "watchlist"

    # Throttled on an immediate re-run (pack default 60 min), state untouched.
    row_before = state_row(ws, "gmail_bills")
    payload = check(ws, framework_dir)
    assert ids(payload["skipped_throttled"]) == ["gmail_bills"]
    assert state_row(ws, "gmail_bills") == row_before
    assert fake.list_calls == 1

    # Re-checking with the newest message now archived must fingerprint identically.
    patch_state(ws, "gmail_bills", last_checked=iso_ago(hours=2))
    assert ids(check(ws, framework_dir)["unchanged"]) == ["gmail_bills"]
    assert fake.list_calls == 2
    assert len(fake.get_calls) == 15, "the 5 ids the capped first round skipped are fetched now"

    # New mail arrives -> changed; only the genuinely new id is fetched.
    patch_state(ws, "gmail_bills", last_checked=iso_ago(hours=2))
    fake.messages.insert(0, {"id": "mNEW", "internalDate": 1_757_999_999_000, "subject": "New bill"})
    fake.get_calls.clear()
    payload = check(ws, framework_dir)
    assert ids(payload["changed"]) == ["gmail_bills"]
    assert fake.get_calls == ["mNEW"], "only the genuinely new id is fetched"
    assert archive.find("mNEW", workspace=ws) is not None


def test_gmail_result_count_decrement_is_not_a_change(
    ws: Path, framework_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`newer_than:30d` shrinks the result set daily as mail ages out; only new mail counts."""
    gm = gmail_handler(framework_dir, ws)
    fake = FakeGmail(_msgs(5))
    monkeypatch.setattr(gm, "CLIENT_FACTORY", lambda: fake)
    write_ref(ws, "gm", {"ref_version": 1, "title": "t", "kind": "api", "source": "gmail:x",
                         "watch": {"pack": "gmail", "params": {"query": "label:Bills newer_than:30d"},
                                   "min_check_interval_minutes": None}})
    check(ws, framework_dir)
    fp = state_row(ws, "gm")["fingerprint"]
    fake.messages.pop()  # the oldest message aged out of the window
    payload = check(ws, framework_dir)
    assert ids(payload["unchanged"]) == ["gm"]
    assert state_row(ws, "gm")["fingerprint"] == fp
    assert "4 match(es)" in payload["unchanged"][0]["detail"]


def test_gmail_missing_token_is_unreachable_with_hint(
    ws: Path, framework_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gm = gmail_handler(framework_dir, ws)
    monkeypatch.setattr(gm, "DEFAULT_CREDENTIALS_PATH", tmp_path / "absent" / "credentials.json")
    monkeypatch.setattr(gm, "CLIENT_FACTORY", gm.default_client_factory)
    write_ref(ws, "gm", {"ref_version": 1, "title": "t", "kind": "api", "source": "gmail:is:unread",
                         "watch": {"pack": "gmail", "params": {"query": "is:unread"}}})
    payload = check(ws, framework_dir)
    assert ids(payload["unreachable"]) == ["gm"]
    err = payload["unreachable"][0]["error"]
    assert "OAuth token not found" in err
    assert "Authorize the Gmail MCP" in err


def test_gmail_empty_result_and_dry_run_skips_capture(
    ws: Path, framework_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from superagent.tools.email import archive

    gm = gmail_handler(framework_dir, ws)
    fake = FakeGmail([])
    monkeypatch.setattr(gm, "CLIENT_FACTORY", lambda: fake)
    write_ref(ws, "gm", {"ref_version": 1, "title": "t", "kind": "api", "source": "x",
                         "watch": {"pack": "gmail", "params": {"query": "label:Nothing"}}})
    check(ws, framework_dir)
    assert state_row(ws, "gm")["fingerprint"] == "gmail:none"
    fake.messages = _msgs(2)
    patch_state(ws, "gm", last_checked=iso_ago(hours=2))
    payload = check(ws, framework_dir, dry_run=True)
    assert ids(payload["changed"]) == ["gm"]
    assert archive.find(fake.messages[0]["id"], workspace=ws) is None, "dry-run never captures"
    assert state_row(ws, "gm")["fingerprint"] == "gmail:none", "dry-run never writes state"


def test_pack_defined_type_needs_a_pack(ws: Path, fw: Path) -> None:
    """`gmail` is not built into core: a bare `watch.type: gmail` row is rejected."""
    write_ref(ws, "bare", {"ref_version": 1, "title": "t", "kind": "api", "source": "gmail:x",
                           "watch": {"type": "gmail", "query": "x"}})
    watchers, errors = wl.load_registry(ws, wl.load_config(ws), {})
    assert watchers == []
    assert "not built in" in errors[0]["error"] and "handler.py" in errors[0]["error"]


# ---------------------------------------------------------------------------
# harvest bridge (fake IngestorBase handler in a custom pack)
# ---------------------------------------------------------------------------

HANDLER_SRC = '''
from pathlib import Path
from superagent.tools.ingest._base import IngestorBase, RunResult, now_iso


class FakeIngestor(IngestorBase):
    source = "fake"

    def run(self, config_row, dry_run=False):
        control = Path(config_row["control"])
        n = int((control.read_text().strip() or "0"))
        marker = self.workspace / "_memory" / "fake-harvest-runs.jsonl"
        if not dry_run:
            with marker.open("a") as fh:
                fh.write(repr(sorted(config_row.items())) + "\\n")
        result = RunResult(source=self.source, started_at=now_iso(), finished_at=now_iso(),
                           items_pulled=abs(n), items_inserted=max(n, 0))
        if n < 0:
            result.items_pulled = 0
            result.items_inserted = 0
            result.errors.append("bridge down")
        if n == 99:
            result.items_inserted = 0
            result.items_updated = 1
        if n == 77:
            result.errors.append("partial: one institution timed out")
        if dry_run:
            result.notes = "dry-run"
        return result
'''


def make_harvest_pack(ws: Path, control: Path, *, capture_mode: str = "automatic",
                      budget: dict[str, Any] | None = None, pack_id: str = "fakebank",
                      detect_type: str = "harvest") -> Path:
    folder = ws / "_custom" / "watchers" / pack_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "handler.py").write_text(HANDLER_SRC)
    manifest: dict[str, Any] = {
        "watcher_version": 1, "id": pack_id, "title": "Fake bank", "kind": "api",
        "detect": {"type": detect_type},
        "harvest": {"defaults": {"control": str(control), "recency_window_days": 30},
                    "writes": ["transactions.yaml"], "affected_domains": []},
        "probe": {"kind": "file_exists", "path": str(control), "setup_hint": "touch it"},
        "budget": budget or {},
        "defaults": {"cycles": ["daily-update"], "evict_after_days": None,
                     "schedule": "daily", "capture_mode": capture_mode},
    }
    if detect_type == "path":
        manifest["detect"]["path"] = str(control)
    (folder / "pack.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return folder


def handler_calls(ws: Path) -> list[str]:
    marker = ws / "_memory" / "fake-harvest-runs.jsonl"
    return marker.read_text().splitlines() if marker.exists() else []


def test_harvest_type_runs_handler_logs_and_alerts(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("3")
    make_harvest_pack(ws, control, budget={"max_window_days": 7})
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "related_domain": "finances",
                           "watch": {"pack": "fakebank", "params": {"extra": "yes"},
                                     "include_pending": False}})
    payload = check(ws, fw)
    assert payload["summary"]["harvested"] == 1
    assert payload["summary"]["checked"] == 1
    assert ids(payload["changed"]) == ["bank"], "rows inserted on the first run are a change"
    assert payload["changed"][0]["effects"]["alert"].startswith("[watch:bank] ")
    row = state_row(ws, "bank")
    assert row["last_harvest"] is not None
    assert row["baseline_at"] is not None
    assert row["last_changed"] == row["baseline_at"]
    assert row["calls_today"] == 1
    assert row["calls_today_date"] == dt.date.today().isoformat()
    assert row["last_harvest_result"]["items_inserted"] == 3
    run_id = payload["harvested"][0]["run_id"]
    assert run_id.startswith("ingest-") and run_id.endswith("-001")
    log = read_yaml(ws / "_memory" / "ingestion-log.yaml")
    assert [r["id"] for r in log["runs"]] == [run_id]
    assert log["runs"][0]["source"] == "fake"
    assert log["runs"][0]["trigger"] == "scheduled"
    calls = handler_calls(ws)
    assert len(calls) == 1
    assert "('control'" in calls[0] and "('extra', 'yes')" in calls[0] and "('id', 'bank')" in calls[0]
    assert "('include_pending', False)" in calls[0], "extra watch: keys override harvest.defaults"
    assert "('recency_window_days', 7)" in calls[0], "budget.max_window_days caps the window"

    ilog = read_yaml(ws / "_memory" / "interaction-log.yaml")
    assert ilog["entries"][-1]["ingestion_log_ref"] == run_id
    assert ilog["entries"][-1]["related_domain"] == "finances"
    assert read_yaml(ws / "_memory" / "context.yaml")["alerts"][-1].startswith("[watch:bank] ")

    # Second harvest with new rows -> changed again; the prior alert is archived.
    patch_state(ws, "bank", last_harvest=iso_ago(hours=3))
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["bank"]
    assert len([a for a in read_yaml(ws / "_memory" / "context.yaml")["alerts"]
                if str(a).startswith("[watch:bank]")]) == 1

    # Updated-only delta counts as changed; quiet harvest -> unchanged; failure -> unreachable.
    control.write_text("99")
    patch_state(ws, "bank", last_harvest=iso_ago(hours=3))
    assert ids(check(ws, fw)["changed"]) == ["bank"]
    control.write_text("0")
    patch_state(ws, "bank", last_harvest=iso_ago(hours=3))
    assert ids(check(ws, fw)["unchanged"]) == ["bank"]
    control.write_text("-1")
    patch_state(ws, "bank", last_harvest=iso_ago(hours=3))
    payload = check(ws, fw)
    assert ids(payload["unreachable"]) == ["bank"]
    assert "bridge down" in payload["unreachable"][0]["error"]


def test_harvest_errors_raise_one_tailor_signal(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("77")
    make_harvest_pack(ws, control)
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    payload = check(ws, fw)
    run_id = payload["harvested"][0]["run_id"]
    signals = [s for s in read_yaml(ws / "_memory" / "action-signals.yaml")["signals"] if s.get("id")]
    assert len(signals) == 1
    sig = signals[0]
    assert sig["id"].startswith("sig-")
    assert sig["target"] == "tailor" and sig["kind"] == "ambient"
    assert sig["source_skill"] == "watch"
    assert sig["artifact_ref"] == f"ingestion-log:{run_id}"
    assert sig["status"] == "captured"
    assert "partial" in sig["context"]


def test_harvest_dry_run_passes_through_and_writes_nothing(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("2")
    make_harvest_pack(ws, control)
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    payload = check(ws, fw, dry_run=True)
    assert payload["summary"]["harvested"] == 1
    assert payload["harvested"][0]["run_id"] is None
    assert payload["harvested"][0]["detail"].startswith("dry-run")
    assert handler_calls(ws) == [], "handler saw dry_run=True"
    row = rows(ws)["bank"]
    assert row["calls_today"] == 1, "a dry-run handler call still hits the API: it counts"
    assert row["calls_today_date"] == dt.date.today().isoformat()
    assert row["fingerprint"] is None and row["last_checked"] is None
    assert "last_harvest" not in row
    log = read_yaml(ws / "_memory" / "ingestion-log.yaml")
    assert all(not r.get("id") for r in log["runs"]), "no ingestion-log row on dry-run"
    ctx_text = (ws / "_memory" / "context.yaml").read_text()
    assert "[watch:bank]" not in ctx_text


def test_budget_max_calls_and_min_interval_leave_state_untouched(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("1")
    make_harvest_pack(ws, control, budget={"max_calls_per_day": 2, "min_interval_minutes": 60})
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    check(ws, fw)
    before = state_row(ws, "bank")
    payload = check(ws, fw)
    assert ids(payload["budget_exceeded"]) == ["bank"]
    assert "min_interval_minutes" in payload["budget_exceeded"][0]["reason"]
    assert payload["summary"]["harvested"] == 0
    assert state_row(ws, "bank") == before, "budget_exceeded must not touch the row"

    patch_state(ws, "bank", last_harvest=iso_ago(hours=2))
    assert check(ws, fw)["summary"]["harvested"] == 1
    assert state_row(ws, "bank")["calls_today"] == 2
    patch_state(ws, "bank", last_harvest=iso_ago(hours=2))
    before = state_row(ws, "bank")
    payload = check(ws, fw)
    assert ids(payload["budget_exceeded"]) == ["bank"]
    assert "max_calls_per_day" in payload["budget_exceeded"][0]["reason"]
    assert state_row(ws, "bank") == before

    # A new day resets the counter.
    patch_state(ws, "bank", last_harvest=iso_ago(hours=2), calls_today_date="2000-01-01")
    assert check(ws, fw)["summary"]["harvested"] == 1
    assert state_row(ws, "bank")["calls_today"] == 1


def test_capture_mode_manual_emits_harvest_dispatch_never_calls_handler(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("5")
    make_harvest_pack(ws, control, capture_mode="manual")
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    payload = check(ws, fw)
    assert payload["summary"]["harvested"] == 0
    assert payload["summary"]["checked"] == 0
    assert payload["summary"]["dispatch"] == 1
    spec = payload["dispatch"][0]
    assert spec["kind"] == "harvest" and spec["id"] == "bank"
    assert spec["command"] == "uv run python -m superagent.tools.watchlist harvest --id bank"
    assert spec["last_harvest"] is None
    assert spec["reason"] == "capture_mode manual"
    assert handler_calls(ws) == []
    assert state_row(ws, "bank").get("last_harvest") is None
    report = wl.render_report(payload)
    assert "awaiting your confirmation" in report and "`bank`" in report

    # `--id` bypasses throttle/cycle gates but never the capture_mode gate.
    payload = check(ws, fw, cycle="monthly-review", only_id="bank")
    assert payload["summary"]["dispatch"] == 1 and payload["summary"]["harvested"] == 0

    # A registry row may override the pack's manual default explicitly.
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank", "capture_mode": "automatic"}})
    assert check(ws, fw)["summary"]["harvested"] == 1


def test_explicit_harvest_bypasses_capture_mode_but_not_budget(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("4")
    make_harvest_pack(ws, control, capture_mode="manual", budget={"max_calls_per_day": 1})
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=False)
    assert rc == 0
    assert payload["summary"]["harvested"] == 1
    assert ids(payload["changed"]) == ["bank"], "4 rows inserted on the first run"
    row = state_row(ws, "bank")
    assert row["fingerprint"].startswith("harvest:")
    log = read_yaml(ws / "_memory" / "ingestion-log.yaml")
    assert log["runs"][-1]["trigger"] == "manual"
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=False)
    assert rc == 1
    assert ids(payload["budget_exceeded"]) == ["bank"]
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="missing", dry_run=False)
    assert rc == 2


def test_explicit_harvest_backfill_flag_and_dry_run_budget(ws: Path, fw: Path) -> None:
    control = ws / "control.txt"
    control.write_text("0")
    make_harvest_pack(ws, control, budget={"max_calls_per_day": 2})
    write_ref(ws, "bank", {"ref_version": 1, "title": "Bank", "kind": "api", "source": "fakebank",
                           "watch": {"pack": "fakebank"}})
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=False, backfill=True)
    assert rc == 0 and payload["backfill"] is True
    assert "('backfill', True)" in handler_calls(ws)[-1]
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=False)
    assert "('backfill'" not in handler_calls(ws)[-1]
    # Two calls made; a dry-run is a third API hit and is refused by the budget.
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=True)
    assert rc == 1 and ids(payload["budget_exceeded"]) == ["bank"]
    patch_state(ws, "bank", calls_today=1)
    rc, payload = wl.run_explicit_harvest(ws, fw, watcher_id="bank", dry_run=True)
    assert rc == 0 and payload["summary"]["harvested"] == 1
    assert state_row(ws, "bank")["calls_today"] == 2, "dry-run call counted and persisted"


def test_no_harvest_flag_and_detect_gated_harvest(ws: Path, fw: Path) -> None:
    """A non-harvest detect type with a pack handler harvests only when detect fires."""
    control = ws / "control.txt"
    control.write_text("2")
    make_harvest_pack(ws, control, detect_type="path")
    write_ref(ws, "drop", {"ref_version": 1, "title": "Drop", "kind": "file", "source": str(control),
                           "watch": {"pack": "fakebank"}})
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["drop"]
    assert payload["summary"]["harvested"] == 0, "baseline does not harvest"
    control.write_text("3")
    payload = check(ws, fw, no_harvest=True)
    assert ids(payload["changed"]) == ["drop"]
    assert payload["summary"]["harvested"] == 0
    assert ids(payload["skipped_harvest"]) == ["drop"]
    control.write_text("4")
    payload = check(ws, fw)
    assert ids(payload["changed"]) == ["drop"]
    assert payload["summary"]["harvested"] == 1


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


def _file_watcher(ws: Path, wid: str = "doc", **watch: Any) -> Path:
    target = ws / f"{wid}.txt"
    if not target.exists():
        target.write_text("v1")
    write_ref(ws, wid, {"ref_version": 1, "title": wid, "kind": "file", "source": str(target),
                        "watch": watch})
    return target


def test_evict_after_quiet_window_then_inactive_then_revive(ws: Path, fw: Path) -> None:
    _file_watcher(ws, evict_after_days=7)
    check(ws, fw)
    patch_state(ws, "doc", baseline_at=iso_ago(days=8), last_success=iso_ago(days=8))
    payload = check(ws, fw)
    assert ids(payload["evicted"]) == ["doc"]
    row = state_row(ws, "doc")
    assert row["status"] == "evicted"
    assert row["evicted_at"] is not None
    assert row["evict_reason"] == "stale"

    payload = check(ws, fw)
    assert payload["summary"]["checked"] == 0
    assert ids(payload["inactive"]) == ["doc"]

    # `status: active` in the ref, edited after the eviction, revives + re-baselines.
    ref = _file_watcher(ws, evict_after_days=7, status="active")
    future = (dt.datetime.now() + dt.timedelta(seconds=5)).timestamp()
    os.utime(ws / "Sources" / "Watchlist" / "doc.ref.md", (future, future))
    (ws / "doc.txt").write_text("v9")
    payload = check(ws, fw)
    assert any("revived" in w for w in payload["warnings"])
    assert ids(payload["unchanged"]) == ["doc"]
    row = state_row(ws, "doc")
    assert row["status"] == "active" and row["evicted_at"] is None
    assert "baseline" in payload["unchanged"][0]["detail"]
    assert ref.exists()


def test_eviction_window_measured_from_last_change(ws: Path, fw: Path) -> None:
    _file_watcher(ws, evict_after_days=7)
    check(ws, fw)
    # Old baseline but a recent change: still inside the window.
    patch_state(ws, "doc", baseline_at=iso_ago(days=30), last_changed=iso_ago(days=2),
                last_success=iso_ago(days=2))
    assert check(ws, fw)["evicted"] == []
    patch_state(ws, "doc", last_changed=iso_ago(days=8), last_success=iso_ago(days=8))
    assert ids(check(ws, fw)["evicted"]) == ["doc"]


def test_unreachable_suppresses_eviction(ws: Path, fw: Path) -> None:
    target = _file_watcher(ws, evict_after_days=7)
    check(ws, fw)
    target.unlink()
    patch_state(ws, "doc", baseline_at=iso_ago(days=8), last_success=iso_ago(days=8))
    payload = check(ws, fw)
    assert ids(payload["unreachable"]) == ["doc"]
    assert payload["evicted"] == []
    assert state_row(ws, "doc")["status"] == "active"


def test_evict_after_null_never_evicts(ws: Path, fw: Path) -> None:
    _file_watcher(ws, evict_after_days=None)
    check(ws, fw)
    patch_state(ws, "doc", baseline_at=iso_ago(days=400), last_success=iso_ago(days=1))
    payload = check(ws, fw)
    assert payload["evicted"] == []
    assert state_row(ws, "doc")["status"] == "active"


def test_expires_evicts_the_day_after(ws: Path, fw: Path) -> None:
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    _file_watcher(ws, expires=yesterday)
    payload = check(ws, fw)
    assert ids(payload["evicted"]) == ["doc"]
    assert payload["evicted"][0]["reason"] == "expired"
    assert state_row(ws, "doc")["evict_reason"] == "expired"
    assert payload["summary"]["checked"] == 0
    _file_watcher(ws, wid="later", expires=dt.date.today().isoformat())
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["later"], "expires day itself is still in life"


def test_enabled_false_freezes_and_reenable_rebaselines(ws: Path, fw: Path) -> None:
    target = _file_watcher(ws)
    check(ws, fw)
    fp = state_row(ws, "doc")["fingerprint"]
    _file_watcher(ws, enabled=False)
    target.write_text("changed while paused")
    payload = check(ws, fw)
    assert ids(payload["disabled"]) == ["doc"]
    assert payload["summary"]["checked"] == 0
    row = state_row(ws, "doc")
    assert row["status"] == "disabled"
    assert row["fingerprint"] == fp, "frozen"
    _file_watcher(ws, enabled=True)
    payload = check(ws, fw)
    assert ids(payload["unchanged"]) == ["doc"]
    assert "baseline" in payload["unchanged"][0]["detail"], "re-enable re-baselines, no alert"
    assert payload["changed"] == []
    assert state_row(ws, "doc")["status"] == "active"


def test_throttle_skip_leaves_state_untouched(ws: Path, fw: Path) -> None:
    target = _file_watcher(ws, min_check_interval_minutes=60)
    check(ws, fw)
    before = state_row(ws, "doc")
    target.write_text("v2")
    payload = check(ws, fw)
    assert ids(payload["skipped_throttled"]) == ["doc"]
    assert payload["summary"]["checked"] == 0
    assert state_row(ws, "doc") == before
    # `--id` bypasses the throttle gate.
    payload = check(ws, fw, only_id="doc")
    assert ids(payload["changed"]) == ["doc"]
    patch_state(ws, "doc", last_checked=iso_ago(minutes=61))
    target.write_text("v3")
    assert ids(check(ws, fw)["changed"]) == ["doc"]


def test_cycles_skip_and_only_id_override(ws: Path, fw: Path) -> None:
    _file_watcher(ws, cycles=["weekly-review"])
    payload = check(ws, fw, cycle="daily-update")
    assert ids(payload["skipped_cycle"]) == ["doc"]
    assert state_row(ws, "doc")["last_checked"] is None
    payload = check(ws, fw, cycle="weekly-review")
    assert ids(payload["unchanged"]) == ["doc"]
    _file_watcher(ws, wid="other", cycles=["monthly-review"])
    payload = check(ws, fw, cycle="daily-update", only_id="other")
    assert ids(payload["unchanged"]) == ["other"]
    assert payload["skipped_cycle"] == []
    payload = check(ws, fw, cycle="daily-update", only_id="ghost")
    assert payload["summary"]["errors"] == 1


def test_orphaned_state_rows_age_three_runs_before_pruning(ws: Path, fw: Path) -> None:
    _file_watcher(ws, wid="new_name")
    state = wl.load_state(ws)
    state["watchers"]["old_name"] = {**wl.default_row(), "fingerprint": "sha256:abc"}
    wl.save_state(ws, state)
    payload = check(ws, fw)
    assert any("old_name" in w and "new_name" in w and "rename" in w for w in payload["warnings"])
    assert any("run 1/3" in w for w in payload["warnings"])
    row = rows(ws)["old_name"]
    assert row["orphan_runs"] == 1 and row["orphaned_at"]
    assert row["fingerprint"] == "sha256:abc", "nothing lost on the first sighting"
    check(ws, fw)
    assert rows(ws)["old_name"]["orphan_runs"] == 2
    payload = check(ws, fw)
    assert any("pruned state rows: old_name" in w for w in payload["warnings"])
    assert "old_name" not in rows(ws) and "new_name" in rows(ws)


def test_orphan_counter_resets_when_the_ref_returns(ws: Path, fw: Path) -> None:
    target = _file_watcher(ws, wid="blip")
    check(ws, fw)
    ref = ws / "Sources" / "Watchlist" / "blip.ref.md"
    text = ref.read_text()
    ref.unlink()
    check(ws, fw)
    check(ws, fw)
    assert rows(ws)["blip"]["orphan_runs"] == 2
    ref.write_text(text)
    check(ws, fw)
    row = rows(ws)["blip"]
    assert "orphan_runs" not in row and "orphaned_at" not in row
    assert row["fingerprint"] is not None
    assert target.exists()


def test_icloud_placeholder_never_ages_the_state_row(ws: Path, fw: Path) -> None:
    _file_watcher(ws, wid="cloudy")
    check(ws, fw)
    ref = ws / "Sources" / "Watchlist" / "cloudy.ref.md"
    ref.unlink()
    (ws / "Sources" / "Watchlist" / ".cloudy.ref.md.icloud").write_bytes(b"placeholder")
    for _ in range(4):
        payload = check(ws, fw)
    assert "cloudy" in rows(ws)
    assert "orphan_runs" not in rows(ws)["cloudy"]
    assert any("iCloud placeholder" in w for w in payload["warnings"])


def test_dry_run_writes_nothing(ws: Path, fw: Path) -> None:
    target = _file_watcher(ws)
    check(ws, fw)
    target.write_text("v2")
    before = {p.name: p.read_text() for p in (ws / "_memory").glob("*.yaml")}
    payload = check(ws, fw, dry_run=True)
    assert ids(payload["changed"]) == ["doc"]
    assert "effects" not in payload["changed"][0]
    assert payload["dry_run"] is True
    after = {p.name: p.read_text() for p in (ws / "_memory").glob("*.yaml")}
    assert after == before


# ---------------------------------------------------------------------------
# report + CLI surface
# ---------------------------------------------------------------------------


def test_report_is_silent_when_quiet_and_lists_changes(ws: Path, fw: Path, capsys: Any) -> None:
    target = _file_watcher(ws)
    argv = ["--workspace", str(ws), "--framework", str(fw), "check", "--cycle", "daily-update", "--report"]
    assert wl.main(argv) == 0
    assert capsys.readouterr().out == "", "baseline-only run is quiet"
    assert wl.main(argv) == 0
    assert capsys.readouterr().out == "", "unchanged-only run is quiet"
    target.write_text("v2")
    assert wl.main(argv) == 0
    out = capsys.readouterr().out
    assert out.startswith("### Watchlist")
    assert "**Changed** (1)" in out and "`doc`" in out
    assert "_checked 1;" in out


def test_report_counts_throttled_as_quiet(ws: Path, fw: Path) -> None:
    _file_watcher(ws, min_check_interval_minutes=60)
    check(ws, fw)
    payload = check(ws, fw)
    assert payload["summary"]["skipped_throttled"] == 1
    assert wl.render_report(payload) == ""


def test_cli_check_json_and_exit_codes(ws: Path, fw: Path, capsys: Any) -> None:
    _file_watcher(ws)
    rc = wl.main(["--workspace", str(ws), "--framework", str(fw), "check", "--cycle", "daily-update"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(wl.SUMMARY_KEYS) <= set(payload["summary"])
    assert payload["cycle"] == "daily-update"
    (ws / "Sources" / "Watchlist" / "broken.ref.md").write_text("---\nkind: api\nsource: x\n---\n")
    rc = wl.main(["--workspace", str(ws), "--framework", str(fw), "check", "--cycle", "daily-update"])
    assert rc == 1, "registry errors surface in the exit code"


def test_cli_stamp_requires_exactly_one_outcome(ws: Path, fw: Path) -> None:
    base = ["--workspace", str(ws), "--framework", str(fw)]
    with pytest.raises(SystemExit):
        wl.main([*base, "stamp", "--id", "x"])
    with pytest.raises(SystemExit):
        wl.main([*base, "stamp", "--id", "x", "--changed", "--unchanged"])


def test_cli_stamp_list_enable_probe(ws: Path, fw: Path, capsys: Any) -> None:
    write_ref(ws, "portal", {"ref_version": 1, "title": "Portal", "kind": "manual", "source": "x",
                             "watch": {"prompt": "look"}})
    base = ["--workspace", str(ws), "--framework", str(fw)]
    assert wl.main([*base, "stamp", "--id", "portal", "--changed", "--note", "milestone 3 done"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["outcome"] == "changed" and res["state"]["fingerprint"] == "milestone 3 done"

    assert wl.main([*base, "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["id"] == "portal" and listed[0]["status"] == "active"
    assert listed[0]["last_outcome"] == "changed"
    assert wl.main([*base, "list", "--status", "evicted"]) == 0
    assert "(none)" in capsys.readouterr().out
    assert wl.main([*base, "list"]) == 0
    assert "portal" in capsys.readouterr().out

    # enable: unknown pack -> 2; a pack from the fake framework -> file written.
    assert wl.main([*base, "enable", "nope", "--id", "x"]) == 2
    assert "unknown pack" in capsys.readouterr().err
    pack_dir = fw / "watchers" / "url"
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(yaml.safe_dump({
        "watcher_version": 1, "id": "url", "title": "Web page", "kind": "generic",
        "parameterized": True,
        "params": {"url": {"required": True}, "selector": {"required": False, "default": ""}},
        "detect": {"type": "url", "url": "{{url}}", "selector": "{{selector}}"},
        "probe": {"kind": "always"},
        "defaults": {"cycles": ["daily-update"], "evict_after_days": 30},
    }))
    assert wl.main([*base, "enable", "url", "--id", "permit", "--param",
                    "url=https://permits.example/x", "--title", "Permit portal"]) == 0
    assert "created Sources/Watchlist/permit.ref.md" in capsys.readouterr().out
    fm, body = wl.parse_frontmatter((ws / "Sources" / "Watchlist" / "permit.ref.md").read_text())
    assert fm is not None
    assert fm["title"] == "Permit portal" and fm["kind"] == "url"
    assert fm["source"] == "https://permits.example/x"
    assert fm["watch"] == {"pack": "url", "enabled": True, "params": {"url": "https://permits.example/x"}}
    assert fm["added_by"] == "watch" and fm["added_at"]
    assert "# Notes" in body
    assert wl.main([*base, "enable", "url", "--id", "permit", "--param", "url=https://x"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert wl.main([*base, "enable", "url", "--id", "nourl"]) == 2
    assert "missing required param `url`" in capsys.readouterr().err
    assert wl.main([*base, "enable", "url", "--id", "Bad Id", "--param", "url=https://x"]) == 2
    err = capsys.readouterr().err
    assert "not a valid watcher id" in err and "--id bad_id" in err
    assert wl.main([*base, "enable", "url", "--id", "ok_id-x", "--param", "url=https://x"]) == 0
    capsys.readouterr()

    watchers, errors = wl.load_registry(ws, wl.load_config(ws), wl.discover_packs(fw, ws)[0])
    assert errors == []
    permit = next(w for w in watchers if w.id == "permit")
    assert permit.type == "url" and permit.detect["url"] == "https://permits.example/x"
    assert "selector" not in permit.detect, "empty substituted value is unset"
    assert permit.evict_after_days == 30

    assert wl.main([*base, "probe", "--json"]) == 0
    probed = json.loads(capsys.readouterr().out)
    assert probed == [{"pack": "url", "origin": "framework", "probe": "always",
                       "status": "available", "detail": "", "setup_hint": ""}]
    assert wl.main([*base, "probe", "--all"]) == 0
    out = capsys.readouterr().out
    assert "permit" in out and "portal" in out and "n/a" in out
    assert wl.main([*base, "probe", "ghost", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "error"


def test_ext_sources_alias_reexports_main() -> None:
    from superagent.tools import ext_sources

    assert ext_sources.main is wl.main


def test_sanitize_note() -> None:
    assert wl.sanitize_note(None) == ""
    assert wl.sanitize_note("a\x01b\nc\t d") == "a b c d"
    assert len(wl.sanitize_note("x" * 1000)) == wl.MAX_NOTE_CHARS
    assert wl.sanitize_note("a\u200bb\u200fc\u202ed\u2066e\u2069f\ufeffg") == "abcdefg"
    assert wl.sanitize_note("\u2028line\u2029break") == "linebreak"
