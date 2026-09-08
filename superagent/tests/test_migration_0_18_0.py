# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.18.0 migration helpers (migrate / validate / revert).

All fixtures are synthetic; nothing here touches the real workspace.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MIG_DIR = REPO_ROOT / "superagent" / "migrations" / "0.18.0"
FRAMEWORK = REPO_ROOT / "superagent"


def _simplefin(monkeypatch):
    """The simplefin handler now lives in its pack; expose it under the legacy import name."""
    import sys

    from superagent.tools import watchlist as wl

    module = wl.import_handler_module(FRAMEWORK / "watchers" / "simplefin" / "handler.py")
    monkeypatch.setitem(sys.modules, "superagent.tools.ingest.simplefin", module)
    return module
NOW = dt.datetime(2026, 9, 6, 12, 0, 0, tzinfo=dt.UTC)
EM = "—"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"mig_0_18_0_{name}", MIG_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod


migrate = _load("migrate")
validate = _load("validate")
revert = _load("revert")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HISTORY_HEAD = f"""# History {EM} Demo

> **[Do not change manually {EM} managed by Superagent]**

_Last updated: 2026-05-01_

---

## Table of Contents

- [History {EM} Demo](#history--demo)
  - [Log](#log)

---

## How this file stays current

- Skills append a new H4 entry {EM} `### old style {EM} 2026-01-01` is NOT a log entry here.

---

## Log

<!-- Newest entry at the top. Format:
       #### YYYY-MM-DD {EM} <one-line title>
-->

"""

MIXED_LOG = f"""#### 2026-06-01 {EM} Canonical entry
- body c

---

#### 2026-05-20 Missing separator entry
- body d

---

## 2026-05-28 {EM} Date-led H2 entry
### Undated sub-header stays
- body a
### Another sub-header {EM} with a dash but no date
- more

---

### Title-first entry {EM} with inner dash {EM} 2026-05-28
- body b1

---

### Title-first evening entry {EM} 2026-05-28 (evening)
- body b2

---

### Title-first trailing word - 2026-05-11 captured
- body b3

---
"""

RECENT_TAIL = """
## Recent ingest events

- 2026-05-01 gmail: 3 messages
"""


def _config_text() -> str:
    return """# [Do not change manually]
# Header comment that must survive.
schema_version: 3
last_updated: "2026-05-21T10:00:00-07:00"

profile:
  name: ""

preferences:
  workspace_path: "workspace"

  # Per-source ingestion cadence override.
  ingestion_schedule:
    gmail: "daily"      # inline comment
    plaid: "weekly"

  # Trailing preference comment.
  skill_autoload: true

data_sources_configured:
  email:
    gmail: false
  finance:
    plaid: false                 # placeholder
    csv_only: false
  health:
    apple_health: false
"""


def _data_sources_text(*, stale_pending_days: int | None = None) -> str:
    override = f"    stale_pending_days: {stale_pending_days}\n" if stale_pending_days else ""
    return f"""schema_version: 1
sources:
  - id: simplefin
    enabled: true
    schedule: weekly
{override}  - id: gmail
    enabled: true
    schedule: ""
  - id: plaid
    enabled: false
    schedule: weekly
"""


def _sessions_yaml(n: int) -> str:
    # 13 sessions, deliberately NOT newest-first; two share a date to test stability.
    dates = ["2026-08-24", "2026-08-26", "2026-08-27", "2026-08-31", "2026-08-31",
             "2026-09-05", "2026-09-06", "2026-09-06", "2026-09-01", "2026-09-02",
             "2026-09-03", "2026-09-04", "2026-09-06"][:n]
    rows = []
    for i, d in enumerate(dates):
        rows.append(f"  - date: \"{d}\"\n    summary: \"session {i}\"  # note {i}\n")
    return ("# Model context (synthetic)\nschema_version: 1\nlast_updated: null\n\n"
            "preferences:\n  tone: terse\n\n"
            "# sessions: newest first, keep 10\nsessions:\n" + "".join(rows) +
            "\nterminology:\n  demo: \"x\"\n")


def build_workspace(root: Path, *, with_transactions: bool = False,
                    stale_pending_days: int | None = None) -> Path:
    ws = root / "ws"
    (ws / "_memory").mkdir(parents=True)
    (ws / ".version").write_text("0.17.2\n")
    (ws / "_memory" / "config.yaml").write_text(_config_text())
    (ws / "_memory" / "data-sources.yaml").write_text(
        _data_sources_text(stale_pending_days=stale_pending_days))
    (ws / "_memory" / "model-context.yaml").write_text(_sessions_yaml(13))
    home = ws / "Domains" / "Home"
    home.mkdir(parents=True)
    (home / "history.md").write_text(HISTORY_HEAD + MIXED_LOG)
    fin = ws / "Domains" / "Finances"
    fin.mkdir(parents=True)
    (fin / "history.md").write_text(HISTORY_HEAD + MIXED_LOG + RECENT_TAIL)
    proj = ws / "Projects" / "demo-project"
    proj.mkdir(parents=True)
    (proj / "history.md").write_text(
        HISTORY_HEAD + f"#### 2026-07-01 {EM} Only entry\n- fine\n\n---\n")
    (ws / "Outbox").mkdir()
    if with_transactions:
        (ws / "_memory" / "transactions.yaml").write_text(
            "schema_version: 1\ntransactions:\n  - id: t1\n    pending: true\n"
            "    date: '2026-01-01'\n")
    return ws


def _snapshot(ws: Path) -> dict[str, str]:
    return {p.relative_to(ws).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in ws.rglob("*") if p.is_file()}


def _run(ws: Path, **kw) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = migrate.run_migration(ws, framework=FRAMEWORK, skip_world=True, now=NOW,
                                 out=lines.append, **kw)
    return code, lines


# ---------------------------------------------------------------------------
# parse_header: shapes a-d
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"## 2026-05-28 {EM} Date-led H2", f"#### 2026-05-28 {EM} Date-led H2"),
        (f"### 2026-05-28 {EM} Date-led H3", f"#### 2026-05-28 {EM} Date-led H3"),
        (f"### Title first {EM} 2026-05-28", f"#### 2026-05-28 {EM} Title first"),
        ("## Title first H2 - 2026-05-28", f"#### 2026-05-28 {EM} Title first H2"),
        (f"### Evening {EM} 2026-05-28 (evening)", f"#### 2026-05-28 {EM} Evening (evening)"),
        (f"### Word {EM} 2026-05-28 captured", f"#### 2026-05-28 {EM} Word (captured)"),
        (f"### Word2 {EM} 2026-05-28 {EM} captured", f"#### 2026-05-28 {EM} Word2 (captured)"),
        (f"### Word3 {EM} captured 2026-07-21", f"#### 2026-07-21 {EM} Word3 (captured)"),
        (f"### Word4 {EM} captured 2026-07-21 (evening)",
         f"#### 2026-07-21 {EM} Word4 (captured) (evening)"),
        (f"#### 2026-05-28 {EM} Canonical", f"#### 2026-05-28 {EM} Canonical"),
        (f"#### 2026-05-28 (later) {EM} Suffixed", f"#### 2026-05-28 (later) {EM} Suffixed"),
        (f"#### 2026-05-26 08:30 PT {EM} Timed", f"#### 2026-05-26 08:30 PT {EM} Timed"),
        ("#### 2026-05-20 Missing separator", f"#### 2026-05-20 {EM} Missing separator"),
        (f"#### 2026-03-04 {EM} Inner {EM} dashes {EM} kept",
         f"#### 2026-03-04 {EM} Inner {EM} dashes {EM} kept"),
    ],
)
def test_parse_header_shapes(line: str, expected: str) -> None:
    parsed = migrate.parse_header(line)
    assert parsed is not None
    assert parsed.normalized == expected
    assert parsed.date == expected.split()[1]


@pytest.mark.parametrize(
    "line",
    ["### Undated sub-header", f"### Sub {EM} with dash but no date", "## Recent ingest events",
     f"### March 2026 {EM} (historical)", "- 2026-05-01 not a header", "#### 2026-05-281 bad"],
)
def test_parse_header_rejects_undated(line: str) -> None:
    assert migrate.parse_header(line) is None


# ---------------------------------------------------------------------------
# normalize_history_text
# ---------------------------------------------------------------------------


def test_normalize_mixed_log_orders_and_relevels() -> None:
    text = HISTORY_HEAD + MIXED_LOG + RECENT_TAIL
    new, n_norm, reordered = migrate.normalize_history_text(text, today="2026-09-06")
    assert n_norm == 5 and reordered
    log = new.split("## Log", 1)[1]
    headers = [ln for ln in log.split("\n") if ln.startswith("#### ")]
    assert headers == [
        f"#### 2026-06-01 {EM} Canonical entry",
        f"#### 2026-05-28 {EM} Date-led H2 entry",
        f"#### 2026-05-28 {EM} Title-first entry {EM} with inner dash",
        f"#### 2026-05-28 {EM} Title-first evening entry (evening)",
        f"#### 2026-05-20 {EM} Missing separator entry",
        f"#### 2026-05-11 {EM} Title-first trailing word (captured)",
    ]
    # Undated sub-headers stay with their entry body, untouched.
    assert "### Undated sub-header stays\n- body a\n### Another sub-header" in new
    # Non-Log sections preserved byte-for-byte (modulo the _Last updated refresh).
    head_new, head_old = new.split("## Log", 1)[0], text.split("## Log", 1)[0]
    assert head_new == head_old.replace("_Last updated: 2026-05-01_", "_Last updated: 2026-09-06_")
    assert new.endswith(RECENT_TAIL)
    assert "- body b3\n\n---\n\n## Recent ingest events" in new or "- body b3\n\n---\n" in new


def test_normalize_same_date_stability() -> None:
    log = (f"#### 2026-05-28 {EM} first\n- a\n\n---\n\n"
           f"### second {EM} 2026-05-28\n- b\n\n---\n\n"
           f"#### 2026-05-28 (later) {EM} third\n- c\n\n---\n\n"
           f"#### 2026-06-01 {EM} newer\n- d\n\n---\n")
    new, _, reordered = migrate.normalize_history_text(HISTORY_HEAD + log)
    assert reordered
    headers = [ln for ln in new.split("\n") if ln.startswith("#### ")]
    assert headers == [f"#### 2026-06-01 {EM} newer", f"#### 2026-05-28 {EM} first",
                       f"#### 2026-05-28 {EM} second", f"#### 2026-05-28 (later) {EM} third"]


def test_normalize_noop_on_canonical_file() -> None:
    text = HISTORY_HEAD + f"#### 2026-07-01 {EM} A\n- x\n\n---\n\n#### 2026-06-01 {EM} B\n- y\n"
    assert migrate.normalize_history_text(text, today="2026-09-06") == (text, 0, False)
    assert migrate.normalize_history_text("# No log section\n\n### Foo {EM} 2026-01-01\n") == (
        "# No log section\n\n### Foo {EM} 2026-01-01\n", 0, False)


def test_normalize_is_idempotent() -> None:
    once, *_ = migrate.normalize_history_text(HISTORY_HEAD + MIXED_LOG + RECENT_TAIL, "2026-09-06")
    assert migrate.normalize_history_text(once, "2026-09-07") == (once, 0, False)
    assert migrate.log_section_offenders(once) == []
    assert len(migrate.log_section_offenders(HISTORY_HEAD + MIXED_LOG)) == 5


# ---------------------------------------------------------------------------
# sessions trim / config edit (pure text functions)
# ---------------------------------------------------------------------------


def test_trim_sessions_keeps_newest_and_comments() -> None:
    new, dropped = migrate.trim_sessions_text(_sessions_yaml(13))
    assert dropped == 3
    data = yaml.safe_load(new)
    dates = [s["date"] for s in data["sessions"]]
    assert dates == sorted(dates, reverse=True) and len(dates) == 10
    # Stability: the three 2026-09-06 rows keep their original relative order (6, 7, 12).
    assert [s["summary"] for s in data["sessions"][:3]] == ["session 6", "session 7", "session 12"]
    assert "# sessions: newest first, keep 10" in new and "# note 12" in new
    assert "session 0" not in new and "session 1\"" not in new and "session 2\"" not in new
    assert data["terminology"] == {"demo": "x"} and data["preferences"] == {"tone": "terse"}
    assert migrate.trim_sessions_text(new) == (new, 0)


def test_trim_sessions_noop_under_cap() -> None:
    text = _sessions_yaml(9)
    assert migrate.trim_sessions_text(text) == (text, 0)
    assert migrate.trim_sessions_text("schema_version: 1\nsessions: []\n") == (
        "schema_version: 1\nsessions: []\n", 0)


def test_update_config_inserts_and_preserves_comments() -> None:
    sources = [{"id": "simplefin", "enabled": True, "schedule": "weekly"},
               {"id": "gmail", "enabled": True, "schedule": ""},
               {"id": "custom_thing", "enabled": True}]
    new, actions = migrate.update_config_text(_config_text(), sources, "2026-09-06T12:00:00+00:00")
    cfg = yaml.safe_load(new)
    assert cfg["data_sources_configured"]["finance"]["simplefin"] is True
    assert cfg["data_sources_configured"]["finance"]["plaid"] is False
    assert cfg["data_sources_configured"]["email"]["gmail"] is True
    assert cfg["data_sources_configured"]["other"]["custom_thing"] is True
    assert cfg["preferences"]["ingestion_schedule"] == {
        "gmail": "daily", "plaid": "weekly", "simplefin": "weekly", "custom_thing": "daily"}
    assert cfg["preferences"]["skill_autoload"] is True
    assert cfg["last_updated"] == "2026-09-06T12:00:00+00:00"
    for comment in ("# Header comment that must survive.", "# inline comment", "# placeholder",
                    "# Trailing preference comment.", "# Per-source ingestion cadence override."):
        assert comment in new
    assert any(a.startswith("last_updated") for a in actions)
    # Second pass is a no-op.
    assert migrate.update_config_text(new, sources, "2027-01-01T00:00:00+00:00") == (new, [])


def test_update_config_creates_missing_blocks() -> None:
    text = "schema_version: 1\nlast_updated: null\nprofile:\n  name: \"\"\n"
    new, _ = migrate.update_config_text(text, [{"id": "simplefin", "enabled": True}], "now")
    cfg = yaml.safe_load(new)
    assert cfg["data_sources_configured"]["finance"]["simplefin"] is True
    assert cfg["preferences"]["ingestion_schedule"]["simplefin"] == "daily"
    assert cfg["last_updated"] == "now"


# ---------------------------------------------------------------------------
# End-to-end: dry-run, real run, idempotency, validate, revert
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path, with_transactions=True)
    before = _snapshot(ws)
    code, lines = _run(ws, dry_run=True)
    assert code == 0
    assert _snapshot(ws) == before
    assert not (ws / "_memory" / "_checkpoints").exists()
    joined = "\n".join(lines)
    assert "[dry-run] history Domains/Home/history.md: 5 header(s) normalized, reordered: yes" in joined
    assert "[dry-run] Outbox/README.md: seeded" in joined
    assert "[dry-run] .version: 0.17.2 -> 0.18.0" in joined
    assert validate.run_validate(ws, out=lambda _s: None) == 1


def test_real_run_then_idempotent_then_validate(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    originals = {p: p.read_bytes() for p in ws.rglob("*") if p.is_file()}
    code, lines = _run(ws)
    assert code == 0
    # history
    home = (ws / "Domains" / "Home" / "history.md").read_text()
    assert migrate.log_section_offenders(home) == []
    assert "_Last updated: 2026-09-06_" in home
    fin = (ws / "Domains" / "Finances" / "history.md").read_text()
    assert fin.endswith(RECENT_TAIL)
    # untouched project history: not rewritten, not checkpointed
    ckpt = ws / "_memory" / "_checkpoints" / "0.18.0"
    assert not (ckpt / "Projects" / "demo-project" / "history.md").exists()
    assert (ckpt / "Domains" / "Home" / "history.md").read_bytes() == originals[
        ws / "Domains" / "Home" / "history.md"]
    assert (ckpt / "_memory" / "config.yaml").read_bytes() == originals[ws / "_memory" / "config.yaml"]
    # sessions
    mc = yaml.safe_load((ws / "_memory" / "model-context.yaml").read_text())
    assert len(mc["sessions"]) == 10
    # config
    cfg = yaml.safe_load((ws / "_memory" / "config.yaml").read_text())
    assert cfg["data_sources_configured"]["finance"]["simplefin"] is True
    assert cfg["preferences"]["ingestion_schedule"]["simplefin"] == "weekly"
    assert cfg["preferences"]["ingestion_schedule"]["gmail"] == "daily"
    assert cfg["last_updated"].startswith("2026-09-06T12:00:00")
    # README + version
    template = (FRAMEWORK / "templates" / "folder-readmes" / "Outbox.md").read_bytes()
    assert (ws / "Outbox" / "README.md").read_bytes() == template
    assert (ws / ".version").read_text() == "0.18.0\n"
    # validate passes
    assert validate.run_validate(ws, out=lambda _s: None) == 0
    # idempotent
    after = _snapshot(ws)
    code2, lines2 = _run(ws)
    assert code2 == 0 and _snapshot(ws) == after
    assert any("nothing to do" in ln for ln in lines2)


OUTBOX_TEMPLATE = FRAMEWORK / "templates" / "folder-readmes" / "Outbox.md"


def _record_seeded(ws: Path, *rels: str) -> Path:
    """Write the seed record migrate.py leaves for files it created from scratch.

    Mirrors the contract revert.py consumes (`<checkpoint dir>/_seeded.txt`,
    one workspace-relative POSIX path per line). Writing it explicitly keeps
    these tests pinned to the contract rather than to migrate.py's internals.
    """
    marker = revert.seeded_marker_path(ws, migrate)
    marker.parent.mkdir(parents=True, exist_ok=True)
    existing = revert.read_seeded(marker)
    marker.write_text("".join(f"{r}\n" for r in [*existing, *rels] if r), encoding="utf-8")
    return marker


def test_revert_restores_bytes(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    before = _snapshot(ws)
    assert _run(ws)[0] == 0
    assert _snapshot(ws) != before
    _record_seeded(ws, "Outbox/README.md")
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, dry_run=True, out=lines.append) == 0
    assert (ws / ".version").read_text() == "0.18.0\n"  # dry-run left everything
    assert (ws / "Outbox" / "README.md").exists()
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert _snapshot(ws) == before
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert not (ws / "Outbox" / "README.md").exists()
    assert not (ws / "_seeded.txt").exists()  # the record is bookkeeping, never "restored"
    assert (ws / ".version").read_text() == "0.17.2\n"
    assert any(ln.startswith("remove Outbox/README.md (seeded") for ln in lines)


def test_revert_keeps_user_edited_readme(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    assert _run(ws)[0] == 0
    _record_seeded(ws, "Outbox/README.md")
    readme = ws / "Outbox" / "README.md"
    readme.write_text(readme.read_text() + "\nuser note\n")
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert readme.exists()
    assert any("no longer byte-identical" in ln for ln in lines)


def test_revert_keeps_preexisting_template_readme(tmp_path: Path) -> None:
    """An init-scaffolded README (byte-identical to the template) survives a revert."""
    ws = build_workspace(tmp_path)
    readme = ws / "Outbox" / "README.md"
    readme.write_bytes(OUTBOX_TEMPLATE.read_bytes())
    before = _snapshot(ws)
    code, lines = _run(ws)
    assert code == 0
    assert not any("Outbox/README.md: seeded" in ln for ln in lines)  # step returned early
    out: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=out.append) == 0
    assert readme.exists()
    assert readme.read_bytes() == OUTBOX_TEMPLATE.read_bytes()
    assert _snapshot(ws) == before
    assert any(ln == "keep Outbox/README.md (not recorded as seeded by this migration)"
               for ln in out)


def test_revert_without_seed_record_never_removes_readme(tmp_path: Path) -> None:
    """No `_seeded.txt` -> byte-identity alone must not trigger an unlink."""
    ws = build_workspace(tmp_path)
    readme = ws / "Outbox" / "README.md"
    readme.write_bytes(OUTBOX_TEMPLATE.read_bytes())
    ckpt = ws / migrate.CHECKPOINT_REL
    (ckpt / "_memory").mkdir(parents=True)
    (ckpt / "_memory" / "config.yaml").write_text("schema_version: 1\n")
    (ws / ".version").write_text("0.18.0\n")
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _s: None) == 0
    assert readme.exists()
    assert (ws / "_memory" / "config.yaml").read_text() == "schema_version: 1\n"
    assert (ws / ".version").read_text() == "0.17.2\n"


def test_read_seeded_filters_unsafe_lines(tmp_path: Path) -> None:
    marker = tmp_path / "_seeded.txt"
    marker.write_text("# created by the migration\n\nOutbox/README.md\n"
                      "../escape.md\n/abs/path.md\nOutbox/README.md\nOther/file.md\n")
    assert revert.read_seeded(marker) == ["Outbox/README.md", "Other/file.md"]
    assert revert.read_seeded(tmp_path / "missing.txt") == []


def test_revert_reports_unknown_seeded_path(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    assert _run(ws)[0] == 0
    _record_seeded(ws, "Domains/Home/extra.md")
    (ws / "Domains" / "Home" / "extra.md").write_text("x\n")
    lines: list[str] = []
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lines.append) == 0
    assert (ws / "Domains" / "Home" / "extra.md").exists()  # no template known -> kept
    assert any("no template is known" in ln for ln in lines)


def test_transactions_step_skips_when_helper_missing(tmp_path: Path, monkeypatch) -> None:
    simplefin = _simplefin(monkeypatch)
    monkeypatch.delattr(simplefin, "mark_stale_pending", raising=False)
    ws = build_workspace(tmp_path, with_transactions=True)
    before = (ws / "_memory" / "transactions.yaml").read_bytes()
    code, lines = _run(ws)
    assert code == 0
    assert any("mark_stale_pending not available" in ln for ln in lines)
    assert (ws / "_memory" / "transactions.yaml").read_bytes() == before


def test_transactions_step_applies_helper(tmp_path: Path, monkeypatch) -> None:
    simplefin = _simplefin(monkeypatch)

    def fake_mark(rows: list[dict], *, days: int = -1, today=None) -> int:
        assert today == NOW.date()
        assert days == simplefin.DEFAULT_STALE_PENDING_DAYS  # no override on the fixture row
        n = 0
        for r in rows:
            if r.get("pending"):
                r["pending"] = False
                r["stale"] = True
                n += 1
        return n

    monkeypatch.setattr(simplefin, "mark_stale_pending", fake_mark, raising=False)
    ws = build_workspace(tmp_path, with_transactions=True)
    code, lines = _run(ws)
    assert code == 0
    data = yaml.safe_load((ws / "_memory" / "transactions.yaml").read_text())
    assert data["transactions"][0]["stale"] is True
    assert (ws / "_memory" / "_checkpoints" / "0.18.0" / "_memory" / "transactions.yaml").exists()
    assert any("1 stale pending row(s) marked" in ln for ln in lines)


def test_missing_workspace_exits_1(tmp_path: Path) -> None:
    assert migrate.run_migration(tmp_path / "nope", skip_world=True, out=lambda _s: None) == 1


def test_cli_dry_run_subprocess(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    before = _snapshot(ws)
    res = subprocess.run(
        [sys.executable, str(MIG_DIR / "migrate.py"), "--workspace", str(ws), "--dry-run",
         "--skip-world"],
        capture_output=True, text=True, cwd=str(tmp_path), check=False)
    assert res.returncode == 0, res.stderr
    assert "[dry-run]" in res.stdout
    assert _snapshot(ws) == before
    res_v = subprocess.run([sys.executable, str(MIG_DIR / "validate.py"), "--workspace", str(ws)],
                           capture_output=True, text=True, cwd=str(tmp_path), check=False)
    assert res_v.returncode == 1 and "FAIL" in res_v.stdout


# ---------------------------------------------------------------------------
# Review blockers: stale_pending_days override, world.yaml not a change,
# pre-flight YAML parse, column-0 comments inside blocks, seed record
# ---------------------------------------------------------------------------


def test_transactions_step_honours_stale_pending_days_override(tmp_path: Path, monkeypatch) -> None:
    simplefin = _simplefin(monkeypatch)
    seen: dict[str, int] = {}

    def fake_mark(rows: list[dict], *, days: int = -1, today=None) -> int:
        seen["days"] = days
        rows[0]["stale_pending"] = True
        return 1

    monkeypatch.setattr(simplefin, "mark_stale_pending", fake_mark, raising=False)
    ws = build_workspace(tmp_path, with_transactions=True, stale_pending_days=45)
    code, _lines = _run(ws)
    assert code == 0
    assert seen["days"] == 45
    assert migrate.data_source_row(ws, "simplefin")["stale_pending_days"] == 45
    assert migrate.data_source_row(ws, "nope") == {}


def test_world_rebuild_is_not_a_workspace_change(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(migrate.subprocess, "run", fake_run)
    ws = build_workspace(tmp_path)
    lines: list[str] = []
    assert migrate.run_migration(ws, framework=FRAMEWORK, now=NOW, out=lines.append) == 0
    assert any(ln == "world.yaml: rebuilt (derived)" for ln in lines)
    assert not any("nothing to do" in ln for ln in lines)
    assert not any("world.yaml" in ln and "changed" in ln for ln in lines)
    # Second run in the DOCUMENTED invocation (no --skip-world): still "nothing to do".
    lines2: list[str] = []
    assert migrate.run_migration(ws, framework=FRAMEWORK, now=NOW, out=lines2.append) == 0
    assert any(ln.startswith("nothing to do; workspace already at 0.18.0 shape")
               and "world.yaml re-derived" in ln for ln in lines2)
    assert len(calls) == 2 and calls[0][-1] == "rebuild"
    assert validate.run_validate(ws, out=lambda _s: None) == 0


def test_world_rebuild_failure_is_warning_only(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom\nlast line\n")

    monkeypatch.setattr(migrate.subprocess, "run", fake_run)
    ws = build_workspace(tmp_path)
    lines: list[str] = []
    assert migrate.run_migration(ws, framework=FRAMEWORK, now=NOW, out=lines.append) == 0
    assert any(ln == "warning: world rebuild exited 1: last line" for ln in lines)
    lines2: list[str] = []
    assert migrate.run_migration(ws, framework=FRAMEWORK, now=NOW, out=lines2.append) == 0
    assert any(ln == "nothing to do; workspace already at 0.18.0 shape" for ln in lines2)


BROKEN_YAML = "schema_version: 1\nsources: [\n  - id: simplefin\n  enabled: true\n"


@pytest.mark.parametrize("rel", ["data-sources.yaml", "model-context.yaml", "config.yaml"])
def test_preflight_rejects_broken_yaml_before_any_write(tmp_path: Path, rel: str) -> None:
    ws = build_workspace(tmp_path, with_transactions=True)
    (ws / "_memory" / rel).write_text(BROKEN_YAML)
    before = _snapshot(ws)
    code, lines = _run(ws)
    assert code == 1
    assert any(ln.startswith(f"error: _memory/{rel} is not well-formed YAML") for ln in lines)
    assert not any(ln.startswith("history ") for ln in lines)  # no step ran
    assert _snapshot(ws) == before
    assert not (ws / "_memory" / "_checkpoints").exists()
    assert (ws / ".version").read_text() == "0.17.2\n"


def test_enabled_sources_propagates_parse_error(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    assert [r["id"] for r in migrate.enabled_sources(ws)] == ["simplefin", "gmail"]
    (ws / "_memory" / "data-sources.yaml").write_text(BROKEN_YAML)
    with pytest.raises(yaml.YAMLError):
        migrate.enabled_sources(ws)
    (ws / "_memory" / "data-sources.yaml").unlink()
    assert migrate.enabled_sources(ws) == []


def test_trim_sessions_survives_column0_comments_between_rows() -> None:
    text = _sessions_yaml(13)
    # Column-0 comment between two rows (after the row "session 7", which is kept) and a
    # column-0 comment after the LAST row, right before the next top-level key.
    text = text.replace('    summary: "session 7"  # note 7\n',
                        '    summary: "session 7"  # note 7\n# older sessions below\n')
    text = text.replace("\nterminology:", "# end of sessions\n\nterminology:")
    assert "# older sessions below" in text and "# end of sessions" in text
    new, dropped = migrate.trim_sessions_text(text)
    assert dropped == 3
    data = yaml.safe_load(new)
    dates = [s["date"] for s in data["sessions"]]
    assert len(dates) == 10 and dates == sorted(dates, reverse=True)
    assert "# older sessions below" in new and "# end of sessions" in new
    assert "# sessions: newest first, keep 10" in new
    assert data["terminology"] == {"demo": "x"} and data["preferences"] == {"tone": "terse"}
    assert new.endswith("# end of sessions\n\nterminology:\n  demo: \"x\"\n")
    assert migrate.trim_sessions_text(new) == (new, 0)


def test_update_config_column0_comment_inside_block_does_not_hide_keys() -> None:
    text = _config_text().replace(
        '    plaid: "weekly"\n',
        '    plaid: "weekly"\n# column-0 comment inside ingestion_schedule\n    simplefin: "monthly"\n',
    ).replace(
        "  skill_autoload: true\n",
        "# column-0 comment inside preferences\n  skill_autoload: true\n",
    )
    assert yaml.safe_load(text)["preferences"]["ingestion_schedule"]["simplefin"] == "monthly"
    sources = [{"id": "simplefin", "enabled": True, "schedule": "weekly"}]
    new, actions = migrate.update_config_text(text, sources, "2026-09-06T12:00:00+00:00")
    cfg = yaml.safe_load(new)
    # The existing schedule row was FOUND (not duplicated) -- user value survives.
    assert cfg["preferences"]["ingestion_schedule"]["simplefin"] == "monthly"
    assert new.count('    simplefin: "monthly"') == 1 and '    simplefin: "weekly"' not in new
    assert not any("ingestion_schedule" in a for a in actions)
    assert cfg["data_sources_configured"]["finance"]["simplefin"] is True
    assert cfg["preferences"]["skill_autoload"] is True
    assert "# column-0 comment inside ingestion_schedule" in new
    assert "# column-0 comment inside preferences" in new
    assert migrate.update_config_text(new, sources, "later") == (new, [])


def test_block_end_skips_comment_only_lines() -> None:
    lines = ["a:", "  b: 1", "# col-0 comment", "  c: 2", "d: 3"]
    assert migrate._block_end(lines, 1, len(lines), 0) == 4
    assert migrate._block_end(["a:", "  b: 1", "# only comment"], 1, 3, 0) == 3


def test_migration_records_seeded_files_for_revert(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    before = _snapshot(ws)
    assert _run(ws)[0] == 0
    marker = revert.seeded_marker_path(ws, migrate)
    assert marker.name == migrate.SEEDED_MARKER_NAME
    assert revert.read_seeded(marker) == ["Outbox/README.md"]
    assert _run(ws)[0] == 0  # re-run does not duplicate the record
    assert marker.read_text().splitlines() == ["Outbox/README.md"]
    # Without any test-side bookkeeping, revert removes what the migration seeded.
    assert revert.run_revert(ws, framework=FRAMEWORK, out=lambda _s: None) == 0
    assert not (ws / "Outbox" / "README.md").exists()
    assert _snapshot(ws) == before


def test_dry_run_leaves_no_seed_record(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path)
    assert _run(ws, dry_run=True)[0] == 0
    assert not revert.seeded_marker_path(ws, migrate).exists()
