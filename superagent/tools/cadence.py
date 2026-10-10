#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Cadence status, run markers, and the on-open staleness nag.

Watchers run only inside cadence skills, and the only hook-free prompt to run
a cadence is the AGENTS.md "On workspace open" nag. When cadences lapse,
nothing else fires: watchers go stale with `error_streak: 0`, and a run
abandoned midway used to leave no trace at all. This tool makes both visible:

  start <skill>    stamp `context.yaml.last_runs.<key>_started` as a cadence run
                   begins (the "attempted" marker).
  finish <skill>   stamp `last_runs.<key>` (the "completed" marker) and drop the
                   started marker. A started marker newer than the completed
                   stamp is a run that began and never finished. Readers of
                   `last_runs.<key>` are unaffected: a `_started` key is never
                   a completed run.
  status           print what is late:
                     - `last_check` older than 24 hours (or never set);
                     - overdue cadences, by the daily-update § 15 rules;
                     - healthy watchers (active, error_streak 0) whose
                       `last_success` is older than their fastest cycle's
                       period plus one day of grace (daily 1, weekly 7,
                       monthly 31 days; `manual` watchers are never late);
                     - unfinished runs (see `finish`).
                   Prints nothing when everything is current. `--stamp` does
                   the on-open bookkeeping: it prints nothing when
                   `refresh_suggested` already equals today, and sets it to
                   today whenever it printed findings. `--json` prints the
                   full structure instead.

Skill -> `last_runs` key: daily-update daily_update, weekly-review
weekly_review, monthly-review monthly_review, supertailor-review
tailor_review, doctor doctor, whatsup whatsup.

`context.yaml` edits are text-level (only the touched line changes, comments
survive), parse-checked before (an unparseable file is refused and left
untouched) and after (the file must read back as exactly the intended edit),
then written atomically.

Exit codes: 0 ok; 1 runtime error (unparseable file, post-write check
failed); 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import watchlist as wl

FRAMEWORK = Path(__file__).resolve().parent.parent
CADENCE_KEYS = {
    "daily-update": "daily_update",
    "weekly-review": "weekly_review",
    "monthly-review": "monthly_review",
    "supertailor-review": "tailor_review",
    "doctor": "doctor",
    "whatsup": "whatsup",
}
STARTED_SUFFIX = "_started"
#: Watcher cycle -> period in days (contracts/watchlist.md cycles).
CYCLE_PERIOD_DAYS = {"daily-update": 1, "weekly-review": 7, "monthly-review": 31}
GRACE_DAYS = 1
STALE_CHECK_HOURS = 24
DOCTOR_DAYS = 180
TAILOR_DAYS = 90
WORKSPACE_MIN_AGE_DAYS = 180  # fresh installs are never nagged about doctor

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_EVERY_DAYS_RE = re.compile(r"every\s+(\d+)\s+days?", re.IGNORECASE)


class CorruptFileError(Exception):
    """`context.yaml` exists but is not a YAML mapping; never rewritten."""


class WriteRefusedError(Exception):
    """The edited text did not read back as the intended edit; nothing written."""


# --------------------------------------------------------------------- helpers

def now_local() -> dt.datetime:
    """Current local time, seconds precision, with offset."""
    return dt.datetime.now().astimezone().replace(microsecond=0)


def as_datetime(value: Any) -> dt.datetime | None:
    """A timezone-aware datetime from an ISO string, datetime or date; None otherwise."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.astimezone()
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time()).astimezone()
    if isinstance(value, str) and value.strip():
        try:
            parsed = dt.datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.astimezone()
    return None


def _context_path(workspace: Path) -> Path:
    return workspace / "_memory" / "context.yaml"


def _parse(text: str, name: str) -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_LOADER)  # a safe loader (C or Python)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}: " if mark is not None else ""
        raise CorruptFileError(
            f"{name} is not valid YAML ({where}{getattr(exc, 'problem', None) or exc}); refusing to "
            "rewrite it. Fix it by hand (`uv run python -m superagent.tools.validate` locates the line)"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CorruptFileError(f"{name}: top level is a {type(data).__name__}, expected a mapping")
    return data


def load_context(workspace: Path) -> tuple[str, dict[str, Any]]:
    """(text, parsed) of context.yaml; ("", {}) when absent. Raises CorruptFileError."""
    path = _context_path(workspace)
    if not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    return text, _parse(text, path.name)


def load_preferences(workspace: Path) -> dict[str, Any]:
    """`config.yaml.preferences` (read-only; {} when missing or unreadable)."""
    try:
        cfg = yaml.load((workspace / "_memory" / "config.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)
    except (OSError, yaml.YAMLError):
        return {}
    prefs = cfg.get("preferences") if isinstance(cfg, dict) else None
    return prefs if isinstance(prefs, dict) else {}


def _render(key: str, value: Any) -> str:
    return yaml.safe_dump({key: value}, allow_unicode=True, default_flow_style=False).rstrip("\n")


def _set_child(text: str, parent: str, key: str, value: Any, *, delete: bool = False) -> str:
    """Set (or delete) `parent.key` in a top-level block mapping, editing only that line."""
    if text and not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines(keepends=True)
    head_re = re.compile(rf"^{re.escape(parent)}\s*:(.*)$")
    k = next((i for i, ln in enumerate(lines) if head_re.match(ln.rstrip("\n"))), None)
    if k is None:
        if delete:
            return text
        return text + f"{parent}:\n  {_render(key, value)}\n"
    rest = head_re.match(lines[k].rstrip("\n")).group(1).split("#", 1)[0].strip()
    if rest in ("{}", "null", "~"):
        lines[k] = f"{parent}:\n"
    end = next((j for j in range(k + 1, len(lines))
                if lines[j].strip() and not lines[j].startswith((" ", "\t", "#"))), len(lines))
    children = [j for j in range(k + 1, end)
                if lines[j].strip() and not lines[j].lstrip().startswith("#")]
    indent = (len(lines[children[0]]) - len(lines[children[0]].lstrip(" "))) if children else 2
    key_re = re.compile(rf"^ {{{indent}}}{re.escape(key)}\s*:")
    hit = next((j for j in children if key_re.match(lines[j])), None)
    if delete:
        if hit is not None:
            del lines[hit]
        return "".join(lines)
    new_line = " " * indent + _render(key, value) + "\n"
    if hit is not None:
        lines[hit] = new_line
    else:
        lines.insert((children[-1] if children else k) + 1, new_line)
    return "".join(lines)


def _set_top(text: str, key: str, value: Any) -> str:
    """Set a top-level scalar line (appended when the key is absent)."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(lambda _m: _render(key, value), text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + _render(key, value) + "\n"


def _write_checked(workspace: Path, new_text: str, expected: dict[str, Any]) -> None:
    """Parse-check `new_text` against `expected`, then write context.yaml atomically."""
    path = _context_path(workspace)
    try:
        if _parse(new_text, path.name) != expected:
            raise WriteRefusedError(f"{path.name}: post-write check failed; nothing written")
    except CorruptFileError as exc:
        raise WriteRefusedError(f"{path.name}: the edited text does not parse; nothing written") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


# ----------------------------------------------------------------- markers

def mark(workspace: Path, skill: str, *, phase: str, now: dt.datetime | None = None) -> str:
    """`phase` "start" stamps `<key>_started`; "finish" stamps `<key>` and drops the marker.

    Returns the `last_runs` key written. Raises ValueError for a non-cadence
    skill, CorruptFileError / WriteRefusedError when context.yaml cannot be
    written safely.
    """
    if skill not in CADENCE_KEYS:
        raise ValueError(f"{skill!r} is not a cadence skill ({', '.join(CADENCE_KEYS)})")
    key = CADENCE_KEYS[skill]
    stamp = _iso(now or now_local())
    text, ctx = load_context(workspace)
    runs = ctx.get("last_runs")
    if runs is not None and not isinstance(runs, dict):
        raise CorruptFileError("context.yaml: `last_runs` is not a mapping; refusing to rewrite it")
    expected = dict(ctx)
    new_runs = dict(runs or {})
    if phase == "start":
        new_runs[key + STARTED_SUFFIX] = stamp
        new_text = _set_child(text, "last_runs", key + STARTED_SUFFIX, stamp)
        written = key + STARTED_SUFFIX
    else:
        new_runs[key] = stamp
        new_runs.pop(key + STARTED_SUFFIX, None)
        new_text = _set_child(text, "last_runs", key, stamp)
        new_text = _set_child(new_text, "last_runs", key + STARTED_SUFFIX, None, delete=True)
        written = key
    expected["last_runs"] = new_runs
    _write_checked(workspace, new_text, expected)
    return written


# ------------------------------------------------------------------ status

def _every_days(value: Any, default: int) -> int:
    m = _EVERY_DAYS_RE.search(str(value or ""))
    return int(m.group(1)) if m else default


def workspace_since(workspace: Path) -> dt.datetime | None:
    """Earliest of the first interaction-log row and `workspace/.version` (birth) time."""
    found: list[dt.datetime] = []
    try:
        log = yaml.load((workspace / "_memory" / "interaction-log.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)
        rows = log.get("entries") if isinstance(log, dict) else None
        for row in (rows or [])[:5]:
            if isinstance(row, dict):
                when = as_datetime(row.get("ts") or row.get("timestamp"))
                if when:
                    found.append(when)
    except (OSError, yaml.YAMLError):
        pass
    version = workspace / ".version"
    if version.exists():
        st = version.stat()
        born = getattr(st, "st_birthtime", None) or st.st_mtime
        found.append(dt.datetime.fromtimestamp(born).astimezone())
    return min(found) if found else None


def overdue_cadences(runs: dict[str, Any], prefs: dict[str, Any], now: dt.datetime,
                     since: dt.datetime | None) -> list[dict[str, Any]]:
    """Overdue cadences by the daily-update § 15 rules."""
    cadences = prefs.get("cadences") if isinstance(prefs.get("cadences"), dict) else {}
    out: list[dict[str, Any]] = []

    def add(skill: str, last: dt.datetime | None, reason: str) -> None:
        out.append({"cadence": skill, "last_run": _iso(last), "reason": reason})

    last = as_datetime(runs.get("weekly_review"))
    if prefs.get("weekly_review_auto") and (last is None or now - last > dt.timedelta(days=7)):
        add("weekly-review", last, "no run in the last 7 days")
    last = as_datetime(runs.get("monthly_review"))
    if (prefs.get("monthly_review_auto") and now.day > 1
            and (last is None or (last.year, last.month) < (now.year, now.month))):
        add("monthly-review", last, f"the {now:%B} pass, due the 1st, has not run")
    doctor_days = _every_days(cadences.get("doctor"), DOCTOR_DAYS)
    last = as_datetime(runs.get("doctor"))
    old_enough = since is not None and now - since > dt.timedelta(days=WORKSPACE_MIN_AGE_DAYS)
    if old_enough and (last is None or now - last > dt.timedelta(days=doctor_days)):
        add("doctor", last, f"no hygiene pass in {doctor_days}+ days")
    tailor_days = _every_days(cadences.get("tailor_review"), TAILOR_DAYS)
    last = as_datetime(runs.get("tailor_review"))
    if last is None or now - last > dt.timedelta(days=tailor_days):
        add("supertailor-review", last, f"no framework review in {tailor_days}+ days")
    return out


def overdue_watchers(workspace: Path, now: dt.datetime, *, framework: Path = FRAMEWORK,
                     grace_days: int = GRACE_DAYS) -> list[dict[str, Any]]:
    """Healthy watchers whose last success is older than their fastest cycle's period plus grace.

    Healthy = enabled, state `status: active`, `error_streak` 0 (erroring and
    evicted rows are reported by their own checks). A watcher that never
    succeeded is measured from its ref file's mtime, so a freshly registered
    one is not late yet. `manual` / custom-cycle watchers are never late.
    """
    packs, _errors = wl.discover_packs(framework, workspace, announce=lambda _msg: None)
    watchers, _registry_errors = wl.load_registry(workspace, wl.load_config(workspace), packs)
    state = wl.load_yaml(wl.state_path(workspace))
    rows = state.get("watchers") if isinstance(state, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    out: list[dict[str, Any]] = []
    for watcher in watchers:
        periods = {c: CYCLE_PERIOD_DAYS[c] for c in watcher.cycles if c in CYCLE_PERIOD_DAYS}
        if not watcher.enabled or not periods:
            continue
        row = rows.get(watcher.id) if isinstance(rows.get(watcher.id), dict) else {}
        if row.get("status", "active") != "active" or int(row.get("error_streak") or 0) > 0:
            continue
        cycle = min(periods, key=periods.__getitem__)
        last = as_datetime(row.get("last_success"))
        anchor = last or (dt.datetime.fromtimestamp(watcher.mtime).astimezone() if watcher.mtime else None)
        limit = dt.timedelta(days=periods[cycle] + grace_days)
        if anchor is None or now - anchor > limit:
            out.append({
                "watcher": watcher.id,
                "cycle": cycle,
                "last_success": _iso(last),
                "days_since": None if anchor is None else (now - anchor).days,
            })
    return out


def unfinished_runs(runs: dict[str, Any]) -> list[dict[str, Any]]:
    """Cadence runs whose started marker is newer than their completed stamp."""
    out: list[dict[str, Any]] = []
    for key, value in runs.items():
        if not str(key).endswith(STARTED_SUFFIX):
            continue
        base = str(key)[: -len(STARTED_SUFFIX)]
        started, done = as_datetime(value), as_datetime(runs.get(base))
        if started and (done is None or done < started):
            skill = next((s for s, k in CADENCE_KEYS.items() if k == base), base)
            out.append({"cadence": skill, "started": _iso(started), "last_completed": _iso(done)})
    return out


def status(workspace: Path, *, now: dt.datetime | None = None, framework: Path = FRAMEWORK,
           grace_days: int = GRACE_DAYS) -> dict[str, Any]:
    """Everything `status` reports, as one mapping."""
    now = now or now_local()
    _text, ctx = load_context(workspace)
    runs = ctx.get("last_runs") if isinstance(ctx.get("last_runs"), dict) else {}
    last_check = as_datetime(ctx.get("last_check"))
    stale = last_check is None or now - last_check > dt.timedelta(hours=STALE_CHECK_HOURS)
    return {
        "now": _iso(now),
        "last_check": _iso(last_check),
        "last_check_stale": stale,
        "overdue_cadences": overdue_cadences(runs, load_preferences(workspace), now,
                                             workspace_since(workspace)),
        "overdue_watchers": overdue_watchers(workspace, now, framework=framework,
                                             grace_days=grace_days),
        "unfinished_runs": unfinished_runs(runs),
        "refresh_suggested": str(ctx["refresh_suggested"]) if ctx.get("refresh_suggested") else None,
    }


def render(report: dict[str, Any]) -> list[str]:
    """Human lines for the findings in `report`; [] when everything is current."""
    lines: list[str] = []
    now = as_datetime(report["now"])
    if report["last_check_stale"]:
        last = as_datetime(report["last_check"])
        age = f"{(now - last).days} day(s) ago, {last:%Y-%m-%d}" if last and now else "never"
        lines.append(f"Context last refreshed {age}: run `daily-update` (or `whatsup`).")
    if report["overdue_cadences"]:
        lines.append("Overdue cadences: " + "; ".join(
            f"{c['cadence']} ({c['reason']}; last {str(c['last_run'] or 'never')[:10]})"
            for c in report["overdue_cadences"]) + ".")
    if report["overdue_watchers"]:
        lines.append("Watchers overdue for a check: " + "; ".join(
            f"{w['watcher']} ({w['cycle']}; last success {str(w['last_success'] or 'never')[:10]}"
            + (f", {w['days_since']} day(s) ago)" if w["days_since"] is not None else ")")
            for w in report["overdue_watchers"]) + ".")
    if report["unfinished_runs"]:
        lines.append("Unfinished runs: " + "; ".join(
            f"{u['cadence']} started {str(u['started'])[:16].replace('T', ' ')} and never finished"
            for u in report["unfinished_runs"]) + ".")
    return lines


def stamp_refresh_suggested(workspace: Path, today: str) -> None:
    """Set `context.yaml.refresh_suggested` to `today` (YYYY-MM-DD), parse-checked."""
    text, ctx = load_context(workspace)
    expected = dict(ctx)
    expected["refresh_suggested"] = today
    _write_checked(workspace, _set_top(text, "refresh_suggested", today), expected)


# --------------------------------------------------------------------- CLI

def _aware(value: str) -> dt.datetime:
    parsed = as_datetime(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """The `cadence` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    common.add_argument("--now", type=_aware, default=None, help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="cadence", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("start", "Stamp the attempted marker for a cadence run."),
                       ("finish", "Stamp the completed marker and drop the attempted one.")):
        cmd = sub.add_parser(name, parents=[common], help=text)
        cmd.add_argument("skill", choices=sorted(CADENCE_KEYS))
    st = sub.add_parser("status", parents=[common], help="Print stale context, overdue cadences / "
                                                          "watchers and unfinished runs.")
    st.add_argument("--stamp", action="store_true",
                    help="On-open mode: silent when already nagged today; stamp refresh_suggested.")
    st.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    st.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    now = args.now or now_local()
    try:
        if args.command in ("start", "finish"):
            key = mark(workspace, args.skill, phase=args.command, now=now)
            print(f"last_runs.{key} = {_iso(now)}")
            return 0
        report = status(workspace, now=now, grace_days=args.grace_days)
        today = now.date().isoformat()
        if args.stamp and report["refresh_suggested"] == today:
            return 0  # already nagged today (AGENTS.md "On workspace open")
        lines = render(report)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for line in lines:
                print(line)
        if args.stamp and lines:
            stamp_refresh_suggested(workspace, today)
    except ValueError as exc:
        print(f"cadence: {exc}", file=sys.stderr)
        return 2
    except (CorruptFileError, WriteRefusedError) as exc:
        print(f"cadence: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
