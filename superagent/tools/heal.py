#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""The conflict sweep: fold or report what a folder-sync tool left behind.

When two machines change one file before a sync, the sync tool keeps the newer
write and saves the other as a conflict copy
(`<stem>.sync-conflict-<date>-<time>-<device>.<ext>`). Nothing is lost, but
nothing reads the copy either. This tool finds every copy and deals with it
(`rules/concurrent-sessions.md`):

  sweep       For each conflict copy under the workspace (and, reported only,
              the framework and `tasks/` folders beside it):
                - identical to its original, or a machine's beat file (it is
                  rewritten on every open): the copy is removed;
                - an append-only log: the rows only the copy has are merged
                  into the original, then the copy is removed. Logs: the
                  `.jsonl` files under `_memory/` (audit histories, the email
                  sidecar, the prompt log) by line, and `action-signals.yaml`,
                  `asks.yaml`, `ingestion-log.yaml` and the interaction-log
                  files by row id (a signal or an ask closed on one machine
                  stays closed);
                - anything else: reported with a short diff and LEFT IN PLACE,
                  to be folded by hand. Diffs of sensitive files are withheld.
              A merge is a function of the two files only (no clock, no
              machine name), so two machines that merge the same pair write
              the same bytes. Removed copies go to `~/.superagent/tmp/heal/`.
  strays      Rows appended to the single `interaction-log.yaml` after it
              became read-only history (0.24.0). `--move` moves them into this
              machine's log file.
  sync-check  Read the sync tool's settings for this folder and say which
              differ from the safe ones: unlimited conflict copies, file
              versioning on, and `.git` kept out of the sync without the
              delete flag. Read-only; prints setting names, never the file.

The on-open check (`cadence status --stamp`) runs `sweep` and prints its
lines; `doctor` runs all three.

Exit codes: 0 ok (findings are output, not failures); 1 runtime error; 2 usage.

CLI:
    uv run python -m superagent.tools.heal sweep [--dry-run] [--no-diff] [--json]
    uv run python -m superagent.tools.heal strays [--move]
    uv run python -m superagent.tools.heal sync-check
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import machines, signals
from superagent.tools.home import tmp_dir
from superagent.tools.syncnames import conflict_info, conflict_original, is_conflict_copy

FRAMEWORK = Path(__file__).resolve().parent.parent
#: Row-keyed append-only YAML logs, by path relative to `_memory/` -> list key.
YAML_LOGS = {
    "action-signals.yaml": "signals",
    "asks.yaml": "asks",
    "ingestion-log.yaml": "runs",
    machines.ILOG_LEGACY: machines.ILOG_KEY,
}
SIGNALS_LOG = "action-signals.yaml"
SIGNAL_CLOSED = ("processed", "declined")
#: Logs whose rows are closed in place: log -> (closed statuses, the field that
#: holds when a row was closed). When both versions carry one row id with
#: different content, the closed version wins, and of two closed ones the later.
CLOSE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    SIGNALS_LOG: (SIGNAL_CLOSED, "processed_at"),
    "asks.yaml": (("answered", "expired", "withdrawn"), "closed_at"),
}
#: Folders never walked: tool state, virtual environments, the sync tool's own.
SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
SENSITIVE_PARTS = frozenset({"sensitive", "sealed"})
SENSITIVE_DEFAULT_FILES = ("health-records.yaml", "accounts-index.yaml")
SENSITIVE_NAME_RE = re.compile(r"credential|secret|token|password|\.env$|\.pem$|\.key$", re.IGNORECASE)
MAX_DIFF_BYTES = 5_000_000
DIFF_LINES = 12
DIFF_WIDTH = 160
FREEZE_MARKER = "_frozen.yaml"
#: Outcome statuses that leave no conflict copy behind.
HANDLED = ("merged", "identical", "dropped")

_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dc.dataclass
class Outcome:
    """What the sweep did with one conflict copy."""

    copy: str            # path of the conflict copy, relative to the swept root's parent
    original: str        # path of the file it is a version of
    status: str          # merged | identical | dropped | unresolved | orphan
    detail: str          # one line: what was merged, or why it was not
    device: str = ""
    when: str = ""
    sensitive: bool = False
    only_in_copy: int = 0
    only_in_original: int = 0
    diff: list[str] = dc.field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        """The outcome as a plain mapping."""
        return dc.asdict(self)


# --------------------------------------------------------------------- finding

def find_conflicts(root: Path) -> list[Path]:
    """Every sync conflict copy under `root`, sorted; dot-folders are not walked."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        found.extend(Path(folder) / name for name in files if is_conflict_copy(name))
    return sorted(found)


def _sensitive_names(workspace: Path) -> set[str]:
    try:
        cfg = yaml.load((workspace / "_memory" / "config.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)
    except (OSError, yaml.YAMLError):
        cfg = None
    prefs = cfg.get("preferences") if isinstance(cfg, dict) else None
    block = prefs.get("sensitive") if isinstance(prefs, dict) else None
    names = block.get("auto_route_files") if isinstance(block, dict) else None
    return set(names) if isinstance(names, list) else set(SENSITIVE_DEFAULT_FILES)


def is_sensitive(original: Path, sensitive_names: set[str]) -> bool:
    """True when a diff of this file must not be printed (sensitive tier, secrets by name)."""
    return (bool(SENSITIVE_PARTS.intersection(original.parts)) or original.name in sensitive_names
            or SENSITIVE_NAME_RE.search(original.name) is not None)


def _log_kind(workspace: Path, original: Path) -> tuple[str, str | None]:
    """("yaml", list key) / ("jsonl", None) for an append-only log, else ("other", None)."""
    memory = workspace / "_memory"
    try:
        rel = original.relative_to(memory).as_posix()
    except ValueError:
        return "other", None
    if rel.endswith(".jsonl"):
        return "jsonl", None
    if rel in YAML_LOGS:
        return "yaml", YAML_LOGS[rel]
    parts = rel.split("/")
    if len(parts) == 2 and parts[0] == machines.ILOG_DIR and parts[1].endswith(".yaml"):
        return "yaml", machines.ILOG_KEY
    return "other", None


# --------------------------------------------------------------------- merging

class Unmergeable(Exception):
    """The two versions cannot be merged by rule; the copy stays for a hand fold."""


def _row_key(row: Any) -> str:
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


def _pick_closed(base: dict[str, Any], other: dict[str, Any],
                 rule: tuple[tuple[str, ...], str] = CLOSE_RULES[SIGNALS_LOG]
                 ) -> dict[str, Any] | None:
    """The version of one row that wins under a close rule: the closed one, else the later close."""
    closed, stamp = rule
    base_closed, other_closed = (r.get("status") in closed for r in (base, other))
    if base_closed != other_closed:
        return other if other_closed else base
    if base_closed and other_closed:
        a, b = str(base.get(stamp) or ""), str(other.get(stamp) or "")
        if a != b:
            return other if b > a else base
    return None


def merge_yaml_rows(name: str, base_text: str, copy_text: str, list_key: str, *,
                    signals_rules: bool = False,
                    close_rule: tuple[tuple[str, ...], str] | None = None) -> tuple[str, int, int]:
    """Merge a row-keyed log: returns (new text, rows added, rows replaced).

    Rows only the copy has are appended in the copy's order. A row id both
    carry with different content is a clash: under a `close_rule` (closed
    statuses, close-time field; `signals_rules` is the signals' one) the closed
    version wins, anything else raises Unmergeable. Top-level keys other than
    the list and `last_updated` must agree. The result depends on the two
    texts only.
    """
    if close_rule is None and signals_rules:
        close_rule = CLOSE_RULES[SIGNALS_LOG]
    try:
        base_data, base_rows = signals.parse_rows(name, base_text, list_key)
        copy_data, copy_rows = signals.parse_rows(name, copy_text, list_key)
    except signals.CorruptFileError as exc:
        raise Unmergeable(f"a version does not parse ({exc})") from exc
    ignore = {list_key, "last_updated"}
    if ({k: v for k, v in base_data.items() if k not in ignore}
            != {k: v for k, v in copy_data.items() if k not in ignore}):
        raise Unmergeable("the two versions differ outside the row list")
    present = {_row_key(r) for r in base_rows}
    by_id: dict[Any, int] = {}
    for i, row in enumerate(base_rows):
        if isinstance(row, dict) and row.get("id") is not None:
            by_id.setdefault(row["id"], i)
    append: list[dict[str, Any]] = []
    replace: dict[int, dict[str, Any]] = {}
    clashes: list[str] = []
    for row in copy_rows:
        if not isinstance(row, dict) or machines.is_placeholder(row) or _row_key(row) in present:
            continue
        rid = row.get("id")
        if rid is None or rid not in by_id:
            append.append(row)
            present.add(_row_key(row))
            continue
        winner = _pick_closed(base_rows[by_id[rid]], row, close_rule) if close_rule else None
        if winner is None:
            clashes.append(str(rid))
        elif winner is row:
            replace[by_id[rid]] = row
    if clashes:
        shown = ", ".join(clashes[:5]) + (f", +{len(clashes) - 5} more" if len(clashes) > 5 else "")
        raise Unmergeable(f"{len(clashes)} row id(s) carry different content in the two versions ({shown})")
    stamps = [str(d["last_updated"]) for d in (base_data, copy_data) if d.get("last_updated")]
    try:
        new_text = signals.edit_rows_text(name, base_text, list_key, append=append, replace=replace,
                                          last_updated=max(stamps) if stamps else None)
    except (signals.CorruptFileError, signals.WriteRefusedError) as exc:
        raise Unmergeable(str(exc)) from exc
    return new_text, len(append), len(replace)


def merge_jsonl(base_text: str, copy_text: str) -> tuple[str, int]:
    """Merge a line log: (new text, lines added). Lines only the copy has go to the end, in its order."""
    base_lines = [ln for ln in base_text.splitlines() if ln.strip()]
    seen = set(base_lines)
    extra: list[str] = []
    for line in copy_text.splitlines():
        if line.strip() and line not in seen:
            seen.add(line)
            extra.append(line)
    body = "\n".join(base_lines + extra)
    return (body + "\n" if body else ""), len(extra)


# ----------------------------------------------------------------- reporting

def _read_text(path: Path) -> str | None:
    """The file as text; None when it is too large or not UTF-8."""
    try:
        if path.stat().st_size > MAX_DIFF_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _line_counts(original: str, copy: str) -> tuple[int, int]:
    """(lines only in the copy, lines only in the original), counting repeats."""
    a, b = original.splitlines(), copy.splitlines()
    count_a: dict[str, int] = {}
    for line in a:
        count_a[line] = count_a.get(line, 0) + 1
    only_copy = 0
    for line in b:
        if count_a.get(line, 0) > 0:
            count_a[line] -= 1
        else:
            only_copy += 1
    return only_copy, sum(count_a.values())


def short_diff(original: str, copy: str, limit: int = DIFF_LINES) -> list[str]:
    """At most `limit` changed lines of original -> copy (`-` only here, `+` only in the copy)."""
    out: list[str] = []
    changed = [ln for ln in difflib.unified_diff(original.splitlines(), copy.splitlines(), lineterm="", n=0)
               if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    for line in changed[:limit]:
        out.append(line if len(line) <= DIFF_WIDTH else line[: DIFF_WIDTH - 3] + "...")
    if len(changed) > limit:
        out.append(f"... {len(changed) - limit} more changed line(s)")
    return out


def _trash(path: Path, base: Path, stamp: str) -> Path:
    """Move a handled conflict copy out of the synced tree; return where it went."""
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = Path(path.name)
    dest = tmp_dir() / "heal" / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


# ----------------------------------------------------------------------- sweep

def _resolve(workspace: Path, copy: Path, base: Path, *, mergeable: bool, dry_run: bool,
             with_diff: bool, sensitive_names: set[str], stamp: str) -> Outcome:
    original = conflict_original(copy) or copy
    info = conflict_info(copy) or {}
    out = Outcome(copy=copy.relative_to(base).as_posix(), original=original.relative_to(base).as_posix(),
                  status="unresolved", detail="", device=info.get("device", ""),
                  when=f"{info.get('date', '')} {info.get('time', '')}".strip(),
                  sensitive=is_sensitive(original, sensitive_names))
    if mergeable and original.parent == machines.beats_dir(workspace) and original.suffix == ".json":
        out.status, out.detail = "dropped", "a beat file is rewritten on every open"
        if not dry_run:
            _trash(copy, base, stamp)
        return out
    if not original.exists():
        out.status, out.detail = "orphan", "the file it is a version of no longer exists"
        return out
    try:
        if original.stat().st_size == copy.stat().st_size and _sha(original) == _sha(copy):
            out.status, out.detail = "identical", "same content as the original"
            if not dry_run:
                _trash(copy, base, stamp)
            return out
    except OSError as exc:
        out.detail = f"cannot be read ({exc})"
        return out
    base_text, copy_text = _read_text(original), _read_text(copy)
    kind, list_key = _log_kind(workspace, original) if mergeable else ("other", None)
    if kind != "other" and base_text is not None and copy_text is not None:
        try:
            if kind == "jsonl":
                new_text, added = merge_jsonl(base_text, copy_text)
                detail = f"+{added} line(s)"
            else:
                rel = original.relative_to(workspace / "_memory").as_posix()
                new_text, added, replaced = merge_yaml_rows(
                    original.name, base_text, copy_text, str(list_key),
                    close_rule=CLOSE_RULES.get(rel))
                detail = f"+{added} row(s)" + (f", {replaced} updated" if replaced else "")
            if not dry_run:
                if new_text != base_text:
                    signals._atomic_write(original, new_text)
                _trash(copy, base, stamp)
            out.status, out.detail = "merged", detail
            return out
        except Unmergeable as exc:
            out.detail = str(exc)
    if base_text is None or copy_text is None:
        out.detail = out.detail or (f"not text, or too large to compare ({original.stat().st_size} "
                                    f"vs {copy.stat().st_size} bytes)")
        return out
    out.only_in_copy, out.only_in_original = _line_counts(base_text, copy_text)
    out.detail = out.detail or (f"{out.only_in_copy} line(s) only in the copy, "
                                f"{out.only_in_original} only in the original")
    if out.sensitive:
        out.diff = ["(diff withheld: sensitive file)"] if with_diff else []
    elif with_diff:
        out.diff = short_diff(base_text, copy_text)
    return out


def sweep(workspace: Path, *, dry_run: bool = False, with_diff: bool = True,
          extra_roots: list[Path] | None = None) -> list[Outcome]:
    """Handle every conflict copy under the workspace; report those under `extra_roots`.

    `extra_roots` defaults to the framework and `tasks/` folders beside the
    workspace when they exist; copies there are never merged or removed
    unless identical. Paths in the outcomes are relative to the workspace's
    parent folder.
    """
    workspace = workspace.resolve()
    base = workspace.parent
    if extra_roots is None:
        extra_roots = [p for p in (FRAMEWORK, base / "tasks") if p.is_dir() and base in p.parents]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    sensitive_names = _sensitive_names(workspace)
    outcomes: list[Outcome] = []
    for root, mergeable in [(workspace, True)] + [(r, False) for r in extra_roots]:
        for copy in find_conflicts(root):
            outcomes.append(_resolve(workspace, copy, base, mergeable=mergeable, dry_run=dry_run,
                                     with_diff=with_diff, sensitive_names=sensitive_names, stamp=stamp))
    return outcomes


def render(outcomes: list[Outcome], *, dry_run: bool = False) -> list[str]:
    """Human lines for a sweep; [] when there was no conflict copy."""
    lines: list[str] = []
    merged = [o for o in outcomes if o.status == "merged"]
    removed = [o for o in outcomes if o.status in ("identical", "dropped")]
    would = "would be " if dry_run else ""
    if merged:
        lines.append(f"Sync conflicts {would}merged: "
                     + "; ".join(f"{o.original} ({o.detail})" for o in merged) + ".")
    if removed:
        lines.append(f"Sync conflict copies {would}removed (nothing in them to keep): {len(removed)}.")
    for o in outcomes:
        if o.status in HANDLED:
            continue
        who = f" (from device {o.device}, {o.when})" if o.device else ""
        lines.append(f"Sync conflict to fold by hand: {o.original} has a second version{who}: "
                     f"{o.detail}. Copy: {o.copy}.")
        lines.extend(f"    {line}" for line in o.diff)
    if any(o.status not in HANDLED for o in outcomes):
        lines.append("Fold what is missing into the original, then delete the copy "
                     "(rules/concurrent-sessions.md).")
    return lines


def sweep_lines(workspace: Path) -> list[str]:
    """The on-open form: run the sweep, return its lines without diffs."""
    return render(sweep(workspace, with_diff=False))


# ---------------------------------------------------------------------- strays

def freeze_marker_path(workspace: Path) -> Path:
    """`_memory/interaction-log/_frozen.yaml`, written once by the 0.24.0 migration."""
    return workspace / "_memory" / machines.ILOG_DIR / FREEZE_MARKER


def read_freeze_marker(workspace: Path) -> dict[str, Any]:
    """The freeze marker's `interaction_log` block; {} when absent or unreadable."""
    try:
        data = yaml.load(freeze_marker_path(workspace).read_text(encoding="utf-8"), Loader=_LOADER)
    except (OSError, yaml.YAMLError):
        return {}
    block = data.get("interaction_log") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def stray_rows(workspace: Path) -> tuple[list[int], list[dict[str, Any]]]:
    """(row indexes, rows) appended to the single interaction-log file after it was frozen.

    Empty when the workspace has no freeze marker (never migrated, or created
    after 0.24.0 with no single file) or the file is unchanged.
    """
    marker = read_freeze_marker(workspace)
    legacy = workspace / "_memory" / machines.ILOG_LEGACY
    frozen = marker.get("rows")
    if not isinstance(frozen, int) or not legacy.is_file():
        return [], []
    try:
        if marker.get("size") == legacy.stat().st_size:
            return [], []
        _data, rows = signals.parse_rows(legacy.name, legacy.read_text(encoding="utf-8"),
                                         machines.ILOG_KEY)
    except (OSError, signals.CorruptFileError):
        return [], []
    indexes = [i for i in range(frozen, len(rows))
               if isinstance(rows[i], dict) and not machines.is_placeholder(rows[i])]
    return indexes, [rows[i] for i in indexes]


def move_strays(workspace: Path) -> int:
    """Move the stray rows into this machine's interaction-log file; return how many."""
    indexes, rows = stray_rows(workspace)
    if not rows:
        return 0
    legacy = workspace / "_memory" / machines.ILOG_LEGACY
    target = machines.interaction_log_path(workspace)
    target_text = target.read_text(encoding="utf-8") if target.exists() else ""
    new_target = signals.edit_rows_text(target.name, target_text, machines.ILOG_KEY, append=rows,
                                        skeleton=signals.ILOG_SKELETON)
    new_legacy = signals.edit_rows_text(legacy.name, legacy.read_text(encoding="utf-8"),
                                        machines.ILOG_KEY, remove=indexes)
    signals._atomic_write(target, new_target)
    signals._atomic_write(legacy, new_legacy)
    return len(rows)


def stray_lines(workspace: Path) -> list[str]:
    """The on-open / doctor line for stray rows; [] when there are none."""
    _indexes, rows = stray_rows(workspace)
    if not rows:
        return []
    ids = ", ".join(str(r.get("id") or "(no id)") for r in rows[:5])
    more = f", +{len(rows) - 5} more" if len(rows) > 5 else ""
    return [f"{len(rows)} row(s) were appended to {machines.ILOG_LEGACY}, which is read-only history "
            f"({ids}{more}). Log through `signals log`; move these with "
            "`uv run python -m superagent.tools.heal strays --move`."]


# ------------------------------------------------------------------ sync-check

def _sync_config_candidates() -> list[Path]:
    home = Path.home()
    env = [os.environ.get(k) for k in ("STCONFDIR", "STHOMEDIR")]
    roots = [Path(e) for e in env if e]
    roots += [
        Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / "syncthing",
        Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "syncthing",
        home / "Library" / "Application Support" / "Syncthing",
    ]
    return [r / "config.xml" for r in roots]


def sync_settings(folder: Path, config: Path | None = None) -> dict[str, Any] | None:
    """The sync tool's settings for the synced folder that holds `folder`; None when not synced.

    Returns `{"root", "max_conflicts", "versioning", "git_ignore"}` where
    `git_ignore` is "kept" (ignored, no delete flag), "delete-flag" (ignored
    with `(?d)`), or "missing". Reads only these values; the settings file
    holds keys and is never printed.
    """
    candidates = [config] if config else _sync_config_candidates()
    target = folder.resolve()
    for path in candidates:
        if not path or not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for node in root.findall("folder"):
            raw = node.get("path") or ""
            synced = Path(os.path.expanduser(raw)).resolve()
            if synced != target and synced not in target.parents:
                continue
            versioning = node.find("versioning")
            try:
                max_conflicts: int | None = int(node.findtext("maxConflicts") or "")
            except ValueError:
                max_conflicts = None
            return {
                "root": str(synced),
                "max_conflicts": max_conflicts,
                "versioning": (versioning.get("type") or "") if versioning is not None else "",
                "git_ignore": _git_ignore_state(synced),
            }
    return None


def _git_ignore_state(root: Path) -> str:
    try:
        lines = (root / ".stignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "missing"
    state = "missing"
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(("//", "#")):
            continue
        flags = re.match(r"^((?:\(\?[a-z]\))*)", line).group(1)
        pattern = line[len(flags):].strip().rstrip("/")
        if pattern.lstrip("/").removeprefix("**/") == ".git":
            if "(?d)" not in flags:
                return "kept"
            state = "delete-flag"
    return state


def sync_findings(folder: Path, config: Path | None = None) -> list[str]:
    """What differs from the safe sync settings; [] when all is as it should be or not synced."""
    found = sync_settings(folder, config)
    if found is None:
        return []
    out: list[str] = []
    where = found["root"]
    if found["max_conflicts"] != -1:
        out.append(f"{where}: the sync tool keeps at most {found['max_conflicts']} conflict copies per "
                   "file and drops older ones; set `maxConflicts` to -1 so a losing version is never dropped.")
    if not found["versioning"]:
        out.append(f"{where}: file versioning is off on this machine; turn it on so a file the sync "
                   "replaced or removed can be brought back.")
    if found["git_ignore"] == "delete-flag":
        out.append(f"{where}/.stignore: the `.git` line carries the `(?d)` delete flag, which lets the "
                   "sync tool delete this machine's clone when a folder above it is removed; drop the flag.")
    elif found["git_ignore"] == "missing":
        out.append(f"{where}/.stignore: `.git` is not ignored; each machine keeps its own clone and "
                   "`.git` must never sync (rules/development-tooling.md).")
    return out


# ------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    """The `heal` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    parser = argparse.ArgumentParser(prog="heal", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sw = sub.add_parser("sweep", parents=[common], help="Merge or report sync conflict copies.")
    sw.add_argument("--dry-run", action="store_true", help="Report only; change nothing.")
    sw.add_argument("--no-diff", action="store_true", help="Leave the short diffs out.")
    sw.add_argument("--json", action="store_true")
    st = sub.add_parser("strays", parents=[common],
                        help="Rows appended to the read-only single interaction-log file.")
    st.add_argument("--move", action="store_true", help="Move them into this machine's log file.")
    sub.add_parser("sync-check", parents=[common], help="Compare the sync settings with the safe ones.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = (args.workspace or FRAMEWORK.parent / "workspace").resolve()
    try:
        if args.command == "sweep":
            outcomes = sweep(workspace, dry_run=args.dry_run, with_diff=not args.no_diff)
            if args.json:
                print(json.dumps([o.to_json() for o in outcomes], indent=2))
            else:
                for line in render(outcomes, dry_run=args.dry_run):
                    print(line)
        elif args.command == "strays":
            if args.move:
                moved = move_strays(workspace)
                if moved:
                    print(f"Moved {moved} row(s) from {machines.ILOG_LEGACY} to "
                          f"{machines.interaction_log_path(workspace).relative_to(workspace).as_posix()}.")
            else:
                for line in stray_lines(workspace):
                    print(line)
        else:
            for line in sync_findings(workspace):
                print(line)
    except (OSError, signals.CorruptFileError, signals.WriteRefusedError) as exc:
        print(f"heal: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
