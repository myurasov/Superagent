#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""One writer for the append-only signal and interaction-log files.

`_memory/action-signals.yaml` and `_memory/interaction-log.yaml` used to be
appended by hand, and the row shapes drifted (`disposition` instead of
`status`, off-enum kinds, a missing `target`, legacy-shape log rows); twice a
hand-append broke the file's parse. New rows go through this module instead:

  add     mint a `sig-YYYY-MM-DD-NNN` id (tools/next_id.py) and append a
          canonical signal row (schema: templates/memory/action-signals.yaml).
          `trigger_phrase`, `context` and `proposed_direction` pass through
          `log_user_query.redact()` first: signal rows sit outside the
          sensitive tier.
  list    print signal rows. `--open` keeps rows whose `status` is neither
          `processed` nor `declined`, which is THE definition of "open" every
          counter uses. `--count` prints `tailor=T superagent=S total=N`.
  close   flip one signal to `processed` (`--became-suggestion ID` or
          `--became-action TEXT`) or `declined` (`--declined REASON`), stamp
          `processed_at`, and append a dated line to `notes`.
  log     append a canonical interaction-log row (id / ts / skill / action /
          summary plus the template's related_* fields); `skill` must be a
          skill manifest stem or omitted.

Writes are text-level, so everything else in the file stays byte-for-byte as
it was: a new row is rendered at the indent the file already uses and inserted
after the list's last row; `close` re-renders only the target row. Every write
is parse-checked before (an unparseable file is refused and left untouched, as
dc81b2f does for the watchlist writers) and after (the new text must parse,
every other row must be unchanged, and the written row must read back exactly);
on any mismatch nothing is written. The write itself is atomic (temp file +
rename, file mode preserved).

Exit codes: 0 ok; 1 runtime error (corrupt file, unknown or already-closed id,
post-write check failed); 2 usage error (bad arguments, unknown skill stem).

CLI:
    uv run python -m superagent.tools.signals add --target tailor --kind correction \\
        --trigger "..." --context "..." [--artifact-ref REF] [--source-skill STEM]
    uv run python -m superagent.tools.signals list --open [--target tailor] [--count]
    uv run python -m superagent.tools.signals close <sig-id> --became-suggestion <st-id>
    uv run python -m superagent.tools.signals log --skill daily-update \\
        --action run_daily_briefing --summary "..."
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.log_user_query import redact
from superagent.tools.next_id import next_id
from superagent.tools.validate import (
    LEGACY_SKILL_PREFIX,
    SIGNAL_CLOSED_STATUSES,
    SIGNAL_KINDS,
    SIGNAL_TARGETS,
    skill_stems,
)

FRAMEWORK = Path(__file__).resolve().parent.parent
SIGNALS_FILE = "action-signals.yaml"
SIGNALS_KEY = "signals"
ILOG_FILE = "interaction-log.yaml"
ILOG_KEY = "entries"
#: Free-text signal fields that can quote the user; scrubbed before every write.
REDACTED_SIGNAL_FIELDS = ("trigger_phrase", "context", "proposed_direction")
#: Indent for the first row of a list that has none yet (the templates' style).
DEFAULT_ROW_INDENT = 2

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml: the log is ~16k lines
_TOP_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*\s*:")
_ROW_DASH_RE = re.compile(r"^( *)-(?: |$)")


class CorruptFileError(Exception):
    """The target file exists but is not a YAML mapping with a list under the key; never rewritten."""


class WriteRefusedError(Exception):
    """The edit could not be applied safely (unknown / closed id, post-write check failed)."""


# --------------------------------------------------------------------- helpers

def now_iso() -> str:
    """Current local time, ISO 8601 with offset, seconds precision."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def default_workspace() -> Path:
    """`workspace/` next to the framework folder (same default as tools/validate.py)."""
    return FRAMEWORK.parent / "workspace"


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """`line N: <problem>` when the parser says where, else the first line of the error."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or (str(exc).splitlines() or ["unparseable"])[0]
    return f"line {mark.line + 1}: {problem}" if mark is not None else str(problem)


def _parse(text: str, name: str) -> dict[str, Any]:
    """Parse a memory file's text; {} when empty. Raises CorruptFileError otherwise-unusable."""
    try:
        data = yaml.load(text, Loader=_LOADER)  # a safe loader (C or Python)
    except yaml.YAMLError as exc:
        raise CorruptFileError(
            f"{name} is not valid YAML ({_yaml_problem(exc)}); refusing to rewrite it. Fix the "
            "file by hand (`uv run python -m superagent.tools.validate` locates the line), then re-run"
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise CorruptFileError(
            f"{name}: top level is a {type(data).__name__}, expected a mapping; refusing to rewrite it"
        )
    return data


def _read(path: Path, list_key: str) -> tuple[str, dict[str, Any], list[Any]]:
    """Return (text, parsed mapping, rows) for a file about to be rewritten; the 'before' check."""
    text = ""
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorruptFileError(f"{path.name} cannot be read ({exc}); refusing to rewrite it") from exc
    data = _parse(text, path.name)
    rows = data.get(list_key)
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise CorruptFileError(
            f"{path.name}: `{list_key}` is a {type(rows).__name__}, expected a list; refusing to rewrite it"
        )
    return text, data, rows


def _list_block(lines: list[str], list_key: str) -> tuple[int, int] | None:
    """(key line, end line) of the top-level `list_key:` block, or None when absent."""
    key_re = re.compile(rf"^{re.escape(list_key)}\s*:")
    for k, line in enumerate(lines):
        if key_re.match(line):
            end = next((j for j in range(k + 1, len(lines)) if _TOP_KEY_RE.match(lines[j])), len(lines))
            return k, end
    return None


def _row_layout(lines: list[str], k: int, end: int) -> tuple[int, list[int]]:
    """(row indent, line index of every row's `-`) inside a list block."""
    indent: int | None = None
    starts: list[int] = []
    for j in range(k + 1, end):
        m = _ROW_DASH_RE.match(lines[j])
        if not m:
            continue
        if indent is None:
            indent = len(m.group(1))
        if len(m.group(1)) == indent:
            starts.append(j)
    return (DEFAULT_ROW_INDENT if indent is None else indent), starts


def _render_row(row: dict[str, Any], indent: int) -> list[str]:
    """One row as YAML list-item lines at `indent`."""
    body = yaml.safe_dump([row], sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
    pad = " " * indent
    return [pad + line if line.strip() else line for line in body.splitlines(keepends=True)]


def _is_filler(line: str) -> bool:
    """Blank or comment-only line (sits between rows, never part of one)."""
    return not line.strip() or line.lstrip().startswith("#")


def _set_top_scalar(text: str, key: str, value: Any) -> str:
    """Rewrite an existing top-level `key: <scalar>` line; leave the text alone when absent."""
    rendered = yaml.safe_dump({key: value}, allow_unicode=True, default_flow_style=False).rstrip("\n")
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
    return pattern.sub(lambda _m: rendered, text, count=1)


def _check_rows(name: str, new_text: str, list_key: str, expected: list[Any],
                old_data: dict[str, Any]) -> None:
    """The 'after' check: the new text parses, the list reads back as `expected`, other keys are intact."""
    try:
        new_data = _parse(new_text, name)
    except CorruptFileError as exc:
        raise WriteRefusedError(f"{name}: the edited text does not parse ({exc}); nothing written") from exc
    if new_data.get(list_key) != expected:
        raise WriteRefusedError(f"{name}: post-write check failed (rows differ from the intended edit); "
                                "nothing written")
    ignore = {list_key, "last_updated"}
    if old_data and ({k: v for k, v in new_data.items() if k not in ignore}
                     != {k: v for k, v in old_data.items() if k not in ignore}):
        raise WriteRefusedError(f"{name}: post-write check failed (a top-level key changed); nothing written")


def _atomic_write(path: Path, text: str) -> None:
    """Temp file + rename (atomic on POSIX); keeps the original file mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)


def append_row(path: Path, list_key: str, row: dict[str, Any], *,
               skeleton: str = "schema_version: 1\n", last_updated: str | None = None) -> None:
    """Append `row` to the top-level list `list_key` of `path`, text-level and parse-checked.

    The row lands after the list's last row at the file's own row indent (the
    templates' 2 when the list is empty); an empty flow list (`key: []`)
    becomes block form. A top-level `last_updated:` line, when present, is set
    to `last_updated`. Nothing else in the file changes. Raises
    CorruptFileError for a broken file and WriteRefusedError when the
    post-write check fails; the file is untouched in both cases.
    """
    text, data, rows = _read(path, list_key)
    if not text.strip():
        text = skeleton
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines(keepends=True)
    block = _list_block(lines, list_key)
    if block is None:
        lines.append(f"{list_key}:\n")
        block = (len(lines) - 1, len(lines))
    k, end = block
    if re.match(rf"^{re.escape(list_key)}\s*:\s*\[\s*\]\s*(#.*)?$", lines[k].rstrip("\n")):
        lines[k] = f"{list_key}:\n"
    indent, starts = _row_layout(lines, k, end)
    if len(starts) != len(rows):
        raise WriteRefusedError(f"{path.name}: found {len(starts)} row(s) in the text but {len(rows)} "
                                "parsed; refusing a text-level append")
    content = [j for j in range(k, end) if not _is_filler(lines[j])]
    insert_at = (content[-1] if content else k) + 1
    lines[insert_at:insert_at] = _render_row(row, indent)
    new_text = "".join(lines)
    if last_updated is not None:
        new_text = _set_top_scalar(new_text, "last_updated", last_updated)
    _check_rows(path.name, new_text, list_key, [*rows, row], data)
    _atomic_write(path, new_text)


def replace_row(path: Path, list_key: str, index: int, new_row: dict[str, Any], *,
                last_updated: str | None = None) -> None:
    """Re-render row `index` of `list_key` as `new_row`; every other line stays as it was.

    Same before / after checks as `append_row`.
    """
    text, data, rows = _read(path, list_key)
    lines = text.splitlines(keepends=True)
    block = _list_block(lines, list_key)
    if block is None or not 0 <= index < len(rows):
        raise WriteRefusedError(f"{path.name}: row {index} not found")
    k, end = block
    indent, starts = _row_layout(lines, k, end)
    if len(starts) != len(rows):
        raise WriteRefusedError(f"{path.name}: found {len(starts)} row(s) in the text but {len(rows)} "
                                "parsed; refusing a text-level edit")
    first = starts[index]
    last = starts[index + 1] if index + 1 < len(starts) else end
    while last > first + 1 and _is_filler(lines[last - 1]):
        last -= 1
    lines[first:last] = _render_row(new_row, indent)
    new_text = "".join(lines)
    if last_updated is not None:
        new_text = _set_top_scalar(new_text, "last_updated", last_updated)
    expected = list(rows)
    expected[index] = new_row
    _check_rows(path.name, new_text, list_key, expected, data)
    _atomic_write(path, new_text)


# --------------------------------------------------------------------- signals

def signals_path(workspace: Path) -> Path:
    """`<workspace>/_memory/action-signals.yaml`."""
    return workspace / "_memory" / SIGNALS_FILE


def is_open(row: Any) -> bool:
    """An open signal: a real row (has an id) whose status is neither processed nor declined."""
    return isinstance(row, dict) and bool(row.get("id")) and row.get("status") not in SIGNAL_CLOSED_STATUSES


def load_signals(workspace: Path) -> list[dict[str, Any]]:
    """Every real signal row (rows with an id; the template placeholder is skipped)."""
    path = signals_path(workspace)
    if not path.exists():
        return []
    _text, _data, rows = _read(path, SIGNALS_KEY)
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def open_signals(workspace: Path, target: str | None = None) -> list[dict[str, Any]]:
    """Open signals, optionally for one target."""
    return [r for r in load_signals(workspace)
            if is_open(r) and (target is None or r.get("target") == target)]


def open_counts(workspace: Path) -> dict[str, int]:
    """`{"tailor": T, "superagent": S, "total": N}` over open signals."""
    rows = open_signals(workspace)
    counts = {t: sum(1 for r in rows if r.get("target") == t) for t in SIGNAL_TARGETS}
    counts["total"] = len(rows)
    return counts


def signal_row(*, sid: str, captured_at: str, target: str, kind: str, trigger_phrase: str,
               context: str, artifact_ref: str | None = None, proposed_direction: str | None = None,
               source_skill: str = "chat", source_turn: str | None = None) -> dict[str, Any]:
    """A canonical signal row (template field order), free text already redacted."""
    row: dict[str, Any] = {
        "id": sid,
        "captured_at": captured_at,
        "target": target,
        "kind": kind,
        "trigger_phrase": trigger_phrase,
        "context": context,
        "artifact_ref": artifact_ref,
        "proposed_direction": proposed_direction,
        "source_skill": source_skill,
        "source_turn": source_turn,
        "status": "captured",
        "became_suggestion": None,
        "became_action": None,
        "processed_at": None,
        "notes": None,
    }
    for field in REDACTED_SIGNAL_FIELDS:
        if isinstance(row[field], str):
            row[field] = redact(row[field])
    return row


def _check_skill(value: str, workspace: Path, *, allow: tuple[str, ...] = ()) -> None:
    """Raise ValueError unless `value` is a skill manifest stem (or an allowed literal)."""
    if value in allow:
        return
    stems = skill_stems(FRAMEWORK, workspace)
    if value in stems:
        return
    if value.startswith(LEGACY_SKILL_PREFIX) and value[len(LEGACY_SKILL_PREFIX):] in stems:
        raise ValueError(f"use the bare stem {value[len(LEGACY_SKILL_PREFIX):]!r}, not {value!r}")
    raise ValueError(f"{value!r} is not a skill stem (superagent/skills/ or _custom/skills/)")


def add_signal(workspace: Path, *, target: str, kind: str, trigger_phrase: str, context: str,
               artifact_ref: str | None = None, proposed_direction: str | None = None,
               source_skill: str = "chat", source_turn: str | None = None,
               ts: str | None = None) -> str:
    """Append one canonical signal row; return its new id.

    Raises ValueError for an off-schema target / kind / source_skill or empty
    text, CorruptFileError / WriteRefusedError when the file cannot be
    written safely.
    """
    if target not in SIGNAL_TARGETS:
        raise ValueError(f"target must be one of {', '.join(SIGNAL_TARGETS)}")
    if kind not in SIGNAL_KINDS:
        raise ValueError(f"kind must be one of {', '.join(SIGNAL_KINDS)}")
    if not trigger_phrase.strip() or not context.strip():
        raise ValueError("trigger phrase and context must be non-empty")
    _check_skill(source_skill, workspace, allow=("chat",))
    ts = ts or now_iso()
    path = signals_path(workspace)
    _read(path, SIGNALS_KEY)  # refuse a broken file before minting
    sid = next_id("sig", ts[:10], path)
    row = signal_row(sid=sid, captured_at=ts, target=target, kind=kind, trigger_phrase=trigger_phrase,
                     context=context, artifact_ref=artifact_ref, proposed_direction=proposed_direction,
                     source_skill=source_skill, source_turn=source_turn)
    append_row(path, SIGNALS_KEY, row, skeleton="schema_version: 1\nlast_updated: null\n",
               last_updated=ts)
    return sid


def close_signal(workspace: Path, sid: str, *, became_suggestion: str | None = None,
                 became_action: str | None = None, declined: str | None = None,
                 note: str | None = None, ts: str | None = None) -> dict[str, Any]:
    """Close one open signal; return the updated row.

    Exactly one of `became_suggestion` / `became_action` (-> processed) or
    `declined` (-> declined, the reason goes to notes) must be given.
    """
    chosen = [v for v in (became_suggestion, became_action, declined) if v]
    if len(chosen) != 1:
        raise ValueError("give exactly one of --became-suggestion / --became-action / --declined")
    ts = ts or now_iso()
    path = signals_path(workspace)
    _text, _data, rows = _read(path, SIGNALS_KEY)
    hits = [i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("id") == sid]
    if not hits:
        raise WriteRefusedError(f"no signal with id {sid!r} in {SIGNALS_FILE}")
    if len(hits) > 1:
        raise WriteRefusedError(f"{len(hits)} rows share id {sid!r}; fix the duplicate by hand first")
    index = hits[0]
    old = rows[index]
    if not is_open(old):
        raise WriteRefusedError(f"{sid} is already {old.get('status')}; nothing changed")
    new = dict(old)
    day = ts[:10]
    if declined:
        new["status"] = "declined"
        line = f"[{day}] declined: {declined.strip()}"
    else:
        new["status"] = "processed"
        if became_suggestion:
            new["became_suggestion"] = became_suggestion.strip()
            line = f"[{day}] processed: became {became_suggestion.strip()}"
        else:
            new["became_action"] = (became_action or "").strip()
            line = f"[{day}] processed: became a workspace action"
    if note and note.strip():
        line += f" ({note.strip()})"
    new["processed_at"] = ts
    prior = old.get("notes")
    new["notes"] = f"{str(prior).rstrip()} {line}" if prior not in (None, "") else line
    replace_row(path, SIGNALS_KEY, index, new, last_updated=ts)
    return new


# ------------------------------------------------------------- interaction log

def ilog_row(*, rid: str, ts: str, skill: str | None, action: str, summary: str,
             related_domain: str | None = None, related_project: str | None = None,
             related_asset: str | None = None, related_account: str | None = None,
             action_items: list[str] | None = None,
             ingestion_log_ref: str | None = None) -> dict[str, Any]:
    """A canonical interaction-log row (templates/memory/interaction-log.yaml field order)."""
    return {
        "id": rid,
        "ts": ts,
        "skill": skill,
        "action": action,
        "summary": summary,
        "related_domain": related_domain,
        "related_project": related_project,
        "related_asset": related_asset,
        "related_account": related_account,
        "action_items": list(action_items or []),
        "ingestion_log_ref": ingestion_log_ref,
    }


def append_interaction_log(workspace: Path, *, skill: str | None, action: str, summary: str,
                           ts: str | None = None, **related: Any) -> str:
    """Append one canonical interaction-log row; return its new `ilog-` id.

    `skill` must be a skill manifest stem (no `superagent-` prefix) or None
    for a non-skill note. `related` takes the template's related_* /
    action_items / ingestion_log_ref fields.
    """
    if skill is not None:
        _check_skill(skill, workspace)
    if not action.strip() or not summary.strip():
        raise ValueError("action and summary must be non-empty")
    ts = ts or now_iso()
    path = workspace / "_memory" / ILOG_FILE
    _read(path, ILOG_KEY)
    rid = next_id("ilog", ts[:10], path)
    row = ilog_row(rid=rid, ts=ts, skill=skill, action=action.strip(), summary=summary.strip(), **related)
    append_row(path, ILOG_KEY, row)
    return rid


# ------------------------------------------------------------------------- CLI

def _iso(value: str) -> str:
    """argparse type: an ISO 8601 datetime WITH offset."""
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value!r}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp needs a UTC offset (e.g. -07:00)")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The `signals` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    parser = argparse.ArgumentParser(prog="signals", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", parents=[common], help="Append a canonical action-signal row.")
    add.add_argument("--target", required=True, choices=SIGNAL_TARGETS)
    add.add_argument("--kind", required=True, choices=SIGNAL_KINDS)
    add.add_argument("--trigger", required=True, help="Short excerpt of what the user said.")
    add.add_argument("--context", required=True, help="1-3 sentences: where it arose, what it affects.")
    add.add_argument("--artifact-ref", default=None)
    add.add_argument("--proposed-direction", default=None)
    add.add_argument("--source-skill", default="chat", help="Skill stem, or 'chat' (default).")
    add.add_argument("--source-turn", default=None)
    add.add_argument("--ts", type=_iso, default=None, help="Capture time (default: now).")

    lst = sub.add_parser("list", parents=[common], help="List signals (all, or --open).")
    lst.add_argument("--open", action="store_true", help="Only status not in {processed, declined}.")
    lst.add_argument("--target", choices=SIGNAL_TARGETS, default=None)
    lst.add_argument("--count", action="store_true", help="Print open counts: tailor= superagent= total=.")

    close = sub.add_parser("close", parents=[common], help="Mark one signal processed or declined.")
    close.add_argument("id")
    how = close.add_mutually_exclusive_group(required=True)
    how.add_argument("--became-suggestion", metavar="ID")
    how.add_argument("--became-action", metavar="TEXT")
    how.add_argument("--declined", metavar="REASON")
    close.add_argument("--note", default=None, help="Extra text for the appended notes line.")
    close.add_argument("--ts", type=_iso, default=None)

    log = sub.add_parser("log", parents=[common], help="Append a canonical interaction-log row.")
    log.add_argument("--skill", default=None, help="Skill manifest stem; omit for a non-skill note.")
    log.add_argument("--action", required=True, help="snake_case verb phrase.")
    log.add_argument("--summary", required=True)
    for field in ("domain", "project", "asset", "account"):
        log.add_argument(f"--related-{field}", default=None)
    log.add_argument("--action-item", action="append", default=None, dest="action_items")
    log.add_argument("--ingestion-log-ref", default=None)
    log.add_argument("--ts", type=_iso, default=None)
    return parser


def _list_line(row: dict[str, Any]) -> str:
    trigger = redact(str(row.get("trigger_phrase") or "")).replace("\n", " ")
    if len(trigger) > 80:
        trigger = trigger[:77] + "..."
    return (f"{row.get('id')}  {row.get('target')}  {row.get('kind')}  {row.get('status')}  "
            f"{str(row.get('captured_at') or '')[:10]}  {trigger}")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or default_workspace()
    try:
        if args.command == "add":
            print(add_signal(workspace, target=args.target, kind=args.kind, trigger_phrase=args.trigger,
                             context=args.context, artifact_ref=args.artifact_ref,
                             proposed_direction=args.proposed_direction, source_skill=args.source_skill,
                             source_turn=args.source_turn, ts=args.ts))
        elif args.command == "list":
            if args.count:
                counts = open_counts(workspace)
                print(" ".join(f"{k}={v}" for k, v in counts.items()))
                return 0
            rows = open_signals(workspace, args.target) if args.open else [
                r for r in load_signals(workspace) if args.target is None or r.get("target") == args.target]
            for row in rows:
                print(_list_line(row))
        elif args.command == "close":
            row = close_signal(workspace, args.id, became_suggestion=args.became_suggestion,
                               became_action=args.became_action, declined=args.declined,
                               note=args.note, ts=args.ts)
            print(f"{args.id}: {row['status']}")
        elif args.command == "log":
            print(append_interaction_log(
                workspace, skill=args.skill, action=args.action, summary=args.summary, ts=args.ts,
                related_domain=args.related_domain, related_project=args.related_project,
                related_asset=args.related_asset, related_account=args.related_account,
                action_items=args.action_items, ingestion_log_ref=args.ingestion_log_ref))
    except ValueError as exc:
        print(f"signals: {exc}", file=sys.stderr)
        return 2
    except (CorruptFileError, WriteRefusedError) as exc:
        print(f"signals: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
