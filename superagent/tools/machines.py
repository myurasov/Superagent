#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Which machine this is, who else wrote here lately, and the per-machine logs.

A workspace may be carried between machines by a folder-sync tool
(`rules/concurrent-sessions.md`). Three things here keep the files written most
often from being damaged by that:

  name   This machine's name: `$SUPERAGENT_MACHINE`, else the name fixed at
         first run in `~/.superagent/machine.json` (taken from the host name the
         owner set; `name --set NAME` changes it). The name tags new ids
         (`tools/next_id.py`) and names this machine's log files.
  beat   Write `_memory/machines/<name>.json` (`machine`, `at`, `harness`).
         One writer per file, so the sync tool never sees two versions of it.
  who    The other machines whose beat is recent, as one line each:
         "laptop wrote here 12 min ago: let the sync settle and re-read a
         file before editing it". As fresh as the last sync.

Per-machine logs. The two logs every session appends to are split by machine,
one writer per file, so two machines can log at the same moment:

  _memory/interaction-log/<machine>.yaml   written only by `signals log`
  _memory/user-queries/<machine>.jsonl     written only by `log_user_query`

The single files of older workspaces (`_memory/interaction-log.yaml`,
`_memory/user-queries.jsonl`) stay in place as read-only history. Readers get
one merged view sorted by time: `interaction_log_rows()` / `read_user_queries()`
here, `signals log-show` and `log_user_query --show` on the command line.

Machine-local state for a workspace (the once-a-day nag marker of the on-open
check) lives in `~/.superagent/state/`, never in the synced workspace.

CLI:
    uv run python -m superagent.tools.machines name [--set NAME]
    uv run python -m superagent.tools.machines beat [--workspace PATH]
    uv run python -m superagent.tools.machines who [--within MINUTES] [--json]

Exit codes: 0 ok; 1 runtime error; 2 usage error (a name that is not
lowercase letters, digits and hyphens).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.home import superagent_home
from superagent.tools.syncnames import is_sync_artifact

FRAMEWORK = Path(__file__).resolve().parent.parent
MACHINE_ENV = "SUPERAGENT_MACHINE"
MACHINE_FILE = "machine.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
FALLBACK_NAME = "machine"
BEATS_DIR = "machines"
#: A beat younger than this many minutes means "that machine is writing here now".
ACTIVE_MINUTES = 30

ILOG_LEGACY = "interaction-log.yaml"
ILOG_DIR = "interaction-log"
ILOG_KEY = "entries"
QUERIES_LEGACY = "user-queries.jsonl"
QUERIES_DIR = "user-queries"
#: What a machine's interaction-log file starts as; rows are appended under `entries:`.
ILOG_SKELETON = (
    "# [Do not change manually - managed by Superagent]\n"
    "# Interaction log of the machine named in this file's name. Written only by\n"
    "# `uv run python -m superagent.tools.signals log` on that machine; append-only.\n"
    "# Row shape: superagent/templates/memory/interaction-log.yaml. Read every\n"
    "# machine's rows as one view: `uv run python -m superagent.tools.signals log-show`.\n"
    "schema_version: 1\n"
    "entries:\n"
)

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# ------------------------------------------------------------------- identity

def _sanitize(raw: str) -> str:
    """A host name as a machine name: first label, lowercase, `[a-z0-9-]` only."""
    label = raw.strip().split(".", 1)[0].lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", label)).strip("-")[:31]


def _host_name() -> str:
    """The host name the owner set (macOS: the local host name; else the system's)."""
    if sys.platform == "darwin":
        try:
            done = subprocess.run(["scutil", "--get", "LocalHostName"], check=True,
                                  capture_output=True, text=True, timeout=5)
            if done.stdout.strip():
                return done.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname()


def machine_file() -> Path:
    """`~/.superagent/machine.json` (machine-local, never synced)."""
    return superagent_home() / MACHINE_FILE


def _read_machine_file() -> dict[str, Any]:
    try:
        data = json.loads(machine_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic JSON write (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def set_machine_name(name: str) -> str:
    """Fix this machine's name in `machine.json`; raises ValueError for a bad name."""
    if not NAME_RE.match(name):
        raise ValueError(f"{name!r} is not a machine name (lowercase letters, digits and hyphens, "
                         "starting with a letter or digit, at most 31 characters)")
    data = _read_machine_file()
    data.update({"name": name, "set_at": _now().isoformat(), "source": "owner"})
    data.setdefault("uid", uuid.uuid4().hex[:8])
    _write_json(machine_file(), data)
    return name


def machine_identity(*, create: bool = True) -> dict[str, Any]:
    """`{"name", "uid", "source"}` of this machine.

    Order: `$SUPERAGENT_MACHINE` (never stored), then `machine.json`, then the
    host name, which `create` stores in `machine.json` so that a later host
    name change does not rename the machine. The uid tells two machines apart
    that ended up with the same name.
    """
    env = _sanitize(os.environ.get(MACHINE_ENV, ""))
    stored = _read_machine_file()
    if env and NAME_RE.match(env):
        return {"name": env, "uid": stored.get("uid") or f"env-{env}", "source": "env"}
    name = stored.get("name")
    if isinstance(name, str) and NAME_RE.match(name):
        return {"name": name, "uid": stored.get("uid") or name, "source": stored.get("source") or "stored"}
    name = _sanitize(_host_name()) or FALLBACK_NAME
    if not NAME_RE.match(name):
        name = FALLBACK_NAME
    identity = {"name": name, "uid": uuid.uuid4().hex[:8], "source": "hostname"}
    if create:
        # A read-only home still yields a name; it is derived again next time.
        with contextlib.suppress(OSError):
            _write_json(machine_file(), {**identity, "set_at": _now().isoformat()})
    return identity


def machine_name(*, create: bool = True) -> str:
    """This machine's name (see `machine_identity`)."""
    return machine_identity(create=create)["name"]


def machine_tag(name: str | None = None) -> str:
    """The name as an id segment: letters and digits only, starting with a letter."""
    tag = re.sub(r"[^a-z0-9]", "", (name or machine_name()).lower()) or FALLBACK_NAME
    return tag if tag[0].isalpha() else "m" + tag


# ----------------------------------------------------------------------- time

def parse_ts(value: Any) -> dt.datetime | None:
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


def _age(minutes: float) -> str:
    if minutes < 1:
        return "under a minute"
    if minutes < 90:
        return f"{int(minutes)} min"
    return f"{minutes / 60:.0f} h"


# ----------------------------------------------------------------------- beat

def beats_dir(workspace: Path) -> Path:
    """`<workspace>/_memory/machines/`."""
    return workspace / "_memory" / BEATS_DIR


def beat_path(workspace: Path, name: str | None = None) -> Path:
    """This machine's beat file, `_memory/machines/<name>.json`."""
    return beats_dir(workspace) / f"{name or machine_name()}.json"


def _read_beat(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _harness() -> str:
    try:
        from superagent.tools.ide import detect, label
        return label(detect())
    except Exception:  # the beat never fails over a label
        return "unknown"


def beat(workspace: Path, *, now: dt.datetime | None = None, min_interval_s: int = 0) -> bool:
    """Write this machine's beat file; return True when it was written.

    Skips the write when the stored beat is younger than `min_interval_s`
    (callers on a hot path pass 60 so the file is not rewritten per call) and
    when the workspace has no `_memory/` yet. Never raises: a beat that cannot
    be written must not fail the command that asked for it.
    """
    if not (workspace / "_memory").is_dir():
        return False
    now = now or _now()
    identity = machine_identity()
    path = beat_path(workspace, identity["name"])
    old = _read_beat(path)
    if old and min_interval_s:
        last = parse_ts(old.get("at"))
        if last is not None and old.get("uid") == identity["uid"] \
                and abs((now - last).total_seconds()) < min_interval_s:
            return False
    try:
        _write_json(path, {"machine": identity["name"], "uid": identity["uid"],
                           "at": now.isoformat(timespec="seconds"), "harness": _harness()})
    except OSError:
        return False
    return True


def who(workspace: Path, *, now: dt.datetime | None = None,
        within_minutes: int = ACTIVE_MINUTES) -> list[dict[str, Any]]:
    """Other machines whose beat is at most `within_minutes` old, newest first.

    Each row: `machine`, `at`, `minutes_ago`, `harness`. A beat file under this
    machine's own name written by another machine (same name, other uid) is
    reported with `same_name: True`: the two would write each other's files.
    """
    now = now or _now()
    me = machine_identity(create=False)
    out: list[dict[str, Any]] = []
    folder = beats_dir(workspace)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        if is_sync_artifact(path):
            continue
        row = _read_beat(path)
        when = parse_ts(row.get("at")) if row else None
        if row is None or when is None:
            continue
        same_name = path.stem == me["name"]
        if same_name and row.get("uid") in (None, me["uid"]):
            continue
        minutes = (now - when).total_seconds() / 60
        if minutes > within_minutes:
            continue
        out.append({"machine": path.stem, "at": when.isoformat(timespec="seconds"),
                    "minutes_ago": max(0, round(minutes, 1)), "harness": row.get("harness"),
                    "same_name": same_name})
    return sorted(out, key=lambda r: r["minutes_ago"])


def presence_lines(workspace: Path, *, now: dt.datetime | None = None,
                   within_minutes: int = ACTIVE_MINUTES) -> list[str]:
    """`who` as the lines the on-open check prints; [] when no other machine is active."""
    lines: list[str] = []
    for row in who(workspace, now=now, within_minutes=within_minutes):
        if row["same_name"]:
            lines.append(f"Another machine also calls itself {row['machine']} and wrote here "
                         f"{_age(row['minutes_ago'])} ago: give one of them its own name "
                         "(`uv run python -m superagent.tools.machines name --set <name>`).")
        else:
            lines.append(f"{row['machine']} wrote here {_age(row['minutes_ago'])} ago: let the sync "
                         "settle and re-read a file before editing it.")
    return lines


# ---------------------------------------------------------------- local state

def local_state_path(workspace: Path) -> Path:
    """`~/.superagent/state/workspace-<hash>.json`, this machine's notes about one workspace."""
    key = hashlib.sha256(str(workspace.resolve()).encode("utf-8")).hexdigest()[:12]
    return superagent_home() / "state" / f"workspace-{key}.json"


def read_local_state(workspace: Path) -> dict[str, Any]:
    """This machine's state for `workspace`; {} when none was written yet."""
    try:
        data = json.loads(local_state_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_local_state(workspace: Path, **updates: Any) -> None:
    """Merge `updates` into this machine's state for `workspace`."""
    data = read_local_state(workspace)
    data.update(updates)
    data["workspace"] = str(workspace.resolve())
    _write_json(local_state_path(workspace), data)


# ------------------------------------------------------------ per-machine logs

def _family_files(workspace: Path, legacy: str, folder: str, suffix: str) -> list[Path]:
    """The legacy single file (when present), then every machine's file, by name."""
    memory = workspace / "_memory"
    files = [memory / legacy] if (memory / legacy).is_file() else []
    root = memory / folder
    if root.is_dir():
        files.extend(sorted(
            p for p in root.glob(f"*{suffix}")
            if p.is_file() and not is_sync_artifact(p) and not p.name.startswith(("_", "."))))
    return files


def interaction_log_path(workspace: Path, name: str | None = None) -> Path:
    """This machine's interaction log, `_memory/interaction-log/<machine>.yaml`."""
    return workspace / "_memory" / ILOG_DIR / f"{name or machine_name()}.yaml"


def interaction_log_files(workspace: Path) -> list[Path]:
    """Every file of the interaction log: the old single file, then one per machine."""
    return _family_files(workspace, ILOG_LEGACY, ILOG_DIR, ".yaml")


def file_label(path: Path) -> str:
    """A memory file's name for messages: `interaction-log/<machine>.yaml`, else the bare name."""
    if path.parent.name in (ILOG_DIR, QUERIES_DIR):
        return f"{path.parent.name}/{path.name}"
    return path.name


def is_placeholder(row: Any) -> bool:
    """True for a template's example row: a mapping whose every value is empty."""
    return isinstance(row, dict) and not any(v not in (None, "", [], {}) for v in row.values())


def _sorted_rows(per_file: list[tuple[Path, list[dict[str, Any]]]]) -> list[tuple[Path, int, dict[str, Any]]]:
    """Rows of several files in one list, oldest first.

    A row without a readable time keeps its place: it sorts with the row
    before it in its own file. Ties keep file order, then row order.
    """
    keyed: list[tuple[float, int, int, Path, dict[str, Any]]] = []
    for order, (path, rows) in enumerate(per_file):
        last = float("-inf")
        for index, row in enumerate(rows):
            when = parse_ts(row.get("ts") or row.get("timestamp"))
            if when is not None:
                last = when.timestamp()
            keyed.append((last, order, index, path, row))
    keyed.sort(key=lambda k: k[:3])
    return [(path, index, row) for _ts, _order, index, path, row in keyed]


def interaction_log_rows(workspace: Path, *, errors: list[str] | None = None
                         ) -> list[tuple[Path, int, dict[str, Any]]]:
    """The merged interaction log as `(file, row index, row)`, oldest first.

    Template placeholder rows are left out. A file that cannot be read or
    parsed is skipped, and named in `errors` when a list is given.
    """
    per_file: list[tuple[Path, list[dict[str, Any]]]] = []
    for path in interaction_log_files(workspace):
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=_LOADER)  # a safe loader
        except (OSError, yaml.YAMLError) as exc:
            if errors is not None:
                errors.append(f"{path.name}: {(str(exc).splitlines() or ['unreadable'])[0]}")
            continue
        rows = data.get(ILOG_KEY) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        per_file.append((path, rows))
    merged = _sorted_rows([(p, [r if isinstance(r, dict) else {} for r in rows]) for p, rows in per_file])
    return [(p, i, r) for p, i, r in merged if r and not is_placeholder(r)]


def read_interaction_log(workspace: Path, *, since: dt.datetime | None = None,
                         skill: str | None = None, action: str | None = None,
                         errors: list[str] | None = None) -> list[dict[str, Any]]:
    """Merged interaction-log rows, oldest first, optionally filtered.

    `since` keeps rows at or after that time (rows without a time are dropped
    then); `skill` matches the row's `skill` exactly; `action` matches when the
    text occurs in the row's `action` (legacy rows: `subject` / `type`).
    """
    out: list[dict[str, Any]] = []
    for _path, _index, row in interaction_log_rows(workspace, errors=errors):
        if since is not None:
            when = parse_ts(row.get("ts") or row.get("timestamp"))
            if when is None or when < since:
                continue
        if skill is not None and row.get("skill") != skill:
            continue
        if action is not None:
            haystack = " ".join(str(row.get(k) or "") for k in ("action", "subject", "type"))
            if action not in haystack:
                continue
        out.append(row)
    return out


def user_queries_path(workspace: Path, name: str | None = None) -> Path:
    """This machine's prompt log, `_memory/user-queries/<machine>.jsonl`."""
    return workspace / "_memory" / QUERIES_DIR / f"{name or machine_name()}.jsonl"


def user_queries_files(workspace: Path) -> list[Path]:
    """Every file of the prompt log: the old single file, then one per machine."""
    return _family_files(workspace, QUERIES_LEGACY, QUERIES_DIR, ".jsonl")


def read_user_queries(workspace: Path, *, since: dt.datetime | None = None,
                      include_synthetic: bool = True) -> list[dict[str, Any]]:
    """The merged prompt log, oldest first. Lines that are not JSON objects are skipped."""
    per_file: list[tuple[Path, list[dict[str, Any]]]] = []
    for path in user_queries_files(workspace):
        rows: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            continue
        per_file.append((path, rows))
    out: list[dict[str, Any]] = []
    for _path, _index, row in _sorted_rows(per_file):
        if not include_synthetic and row.get("synthetic"):
            continue
        if since is not None:
            when = parse_ts(row.get("ts"))
            if when is None or when < since:
                continue
        out.append(row)
    return out


# ------------------------------------------------------------------------ CLI

def build_parser() -> argparse.ArgumentParser:
    """The `machines` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    parser = argparse.ArgumentParser(prog="machines", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    name = sub.add_parser("name", help="Print this machine's name, or set it.")
    name.add_argument("--set", dest="new_name", default=None, metavar="NAME")
    sub.add_parser("beat", parents=[common], help="Write this machine's beat file.")
    w = sub.add_parser("who", parents=[common], help="Other machines that wrote here lately.")
    w.add_argument("--within", type=int, default=ACTIVE_MINUTES, metavar="MINUTES")
    w.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "name":
        if args.new_name is not None:
            try:
                set_machine_name(args.new_name)
            except ValueError as exc:
                print(f"machines: {exc}", file=sys.stderr)
                return 2
        print(machine_name())
        return 0
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    if args.command == "beat":
        if not beat(workspace):
            print(f"machines: no beat written (no _memory/ under {workspace}?)", file=sys.stderr)
            return 1
        print(beat_path(workspace))
        return 0
    rows = who(workspace, within_minutes=args.within)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for line in presence_lines(workspace, within_minutes=args.within):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
