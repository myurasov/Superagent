#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Lazy domain-folder materialization + bare-template purge.

Per the lazy-materialization contract (`contracts/domains-and-assets.md` § 6.4a),
`workspace/Domains/<Name>/` is created only when the first row of data lands
for that domain — never speculatively at init time. This module exposes the
helper that any skill / tool calls before writing to a domain markdown file:

    from superagent.tools.domains import ensure_folder
    ensure_folder(workspace, framework, "health")    # creates Domains/Health/ + 5 files

The shipped `_memory/domains-index.yaml` template still REGISTERS all 12
default domains; the folders themselves are absent until earned. When a
folder is first materialized, the matching index row is stamped
(`created` if still null, `last_updated` always) so materialized domains
carry timestamps; repeat calls leave the index untouched.

The stamp is a surgical text edit — only the two timestamp lines of the
target row change; the maintenance banner, the per-field schema comments and
every other row stay byte-identical. The result is verified by re-parsing;
only if that check fails does the tool fall back to a full `yaml.safe_dump`
(which drops in-body comments but keeps the leading header block).

CLI:

    uv run python -m superagent.tools.domains ensure <domain_id>
    uv run python -m superagent.tools.domains list
    uv run python -m superagent.tools.domains purge-empty [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from superagent.tools.workspace_init import DEFAULT_DOMAINS, render_domain_file

DOMAIN_FILES = ("info", "status", "history", "rolodex", "sources")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def workspace_default(framework: Path) -> Path:
    return framework.parent / "workspace"


def domains_index_path(workspace: Path) -> Path:
    return workspace / "_memory" / "domains-index.yaml"


def load_domains_index(workspace: Path) -> dict[str, Any]:
    """Load `_memory/domains-index.yaml`. Empty stub if missing."""
    path = domains_index_path(workspace)
    if not path.exists():
        return {"domains": []}
    with path.open() as fh:
        return yaml.safe_load(fh) or {"domains": []}


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _leading_comments(text: str) -> str:
    """Return the file's leading run of `#` / blank lines (the header block)."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.lstrip().startswith("#"):
            break
        out.append(line)
    return "".join(out)


def save_domains_index(workspace: Path, data: dict[str, Any], *, header: str = "") -> None:
    """Re-dump `_memory/domains-index.yaml` atomically, preserving key order.

    This is a full `yaml.safe_dump`: it drops every in-body comment and
    restyles scalars / flow mappings, so it is the LAST resort —
    `stamp_index_row` prefers a surgical text edit and only lands here when
    that edit cannot be verified. `header` (typically the file's leading
    `#` block from `_leading_comments`) is prepended verbatim so the
    maintenance banner survives the re-dump.
    """
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    _write_atomic(domains_index_path(workspace), header + body)


def lookup_domain(workspace: Path, domain_id: str) -> dict[str, Any] | None:
    for row in load_domains_index(workspace).get("domains", []) or []:
        if isinstance(row, dict) and row.get("id") == domain_id:
            return row
    return None


# Scalars that count as "no value yet" for `created` in the surgical stamp.
_NULLISH = frozenset({"", "~", "null", "Null", "NULL", '""', "''"})


def _row_block(lines: list[str], domain_id: str) -> tuple[int, int, int] | None:
    """Locate the `- id: <domain_id>` list item inside `lines`.

    Returns `(start, end, key_indent)`: `lines[start]` is the id line, `end`
    is the index of the first line past the block (next sibling item, a
    shallower non-blank non-comment line, or EOF) and `key_indent` is the
    column the item's keys sit at. None when no id line matches — e.g. a
    flow-style row or an id carrying an inline comment.
    """
    id_re = re.compile(
        r"^(?P<indent>[ ]*)-(?P<gap>[ ]+)id:[ ]*(?P<q>[\"']?)"
        + re.escape(domain_id)
        + r"(?P=q)[ ]*$"
    )
    for start, line in enumerate(lines):
        m = id_re.match(line.rstrip("\r\n"))
        if not m:
            continue
        indent = len(m.group("indent"))
        key_indent = indent + 1 + len(m.group("gap"))
        end = start + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped and not stripped.startswith("#"):
                cur = len(lines[end]) - len(lines[end].lstrip(" "))
                if cur <= indent:
                    break
            end += 1
        return start, end, key_indent
    return None


def _stamp_text(text: str, domain_id: str, ts: str) -> str | None:
    """Return `text` with the target row's timestamp lines stamped, else None.

    Rewrites `created: <nullish>` and `last_updated: <anything>` in place
    (preserving indent and line ending); a `created` that already holds a
    value is left alone. Keys missing from the row are inserted right after
    the id line (or after the last timestamp line already present). Nothing
    else in the file is touched. None means the row could not be located as
    an editable block-style item.
    """
    lines = text.splitlines(keepends=True)
    loc = _row_block(lines, domain_id)
    if loc is None:
        return None
    start, end, key_indent = loc
    prefix = " " * key_indent
    key_re = re.compile(rf"^{prefix}(created|last_updated):[ ]*(.*?)[ ]*$")
    nl = "\r\n" if lines[start].endswith("\r\n") else "\n"
    seen: set[str] = set()
    anchor = start
    for i in range(start + 1, end):
        body = lines[i].rstrip("\r\n")
        m = key_re.match(body)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        seen.add(key)
        anchor = i
        if key == "created" and value not in _NULLISH:
            continue
        lines[i] = f'{prefix}{key}: "{ts}"' + lines[i][len(body):]
    if not lines[start].endswith(("\n", "\r")):
        lines[start] += nl
    missing = [k for k in ("created", "last_updated") if k not in seen]
    for offset, key in enumerate(missing, start=1):
        lines.insert(anchor + offset, f'{prefix}{key}: "{ts}"{nl}')
    return "".join(lines)


def stamp_index_row(workspace: Path, domain_id: str, *, now: str | None = None) -> bool:
    """Stamp the `domains-index.yaml` row for `domain_id` with timestamps.

    Sets `created` only when it is null / missing and `last_updated`
    unconditionally, both to `now` (ISO 8601 with offset; defaults to the
    current local time). The edit is surgical: only those two lines of the
    target row change and the rest of the file — header banner, schema
    comments, every other row — stays byte-identical. The rewritten text is
    re-parsed and must equal the expected document (target row stamped,
    everything else dict-equal); if it does not, the tool falls back to a
    full re-dump that keeps only the leading comment header.
    Returns True iff a matching row was found and the file was rewritten.
    """
    path = domains_index_path(workspace)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {"domains": []}
    rows = data.get("domains", []) or []
    target = next((r for r in rows if isinstance(r, dict) and r.get("id") == domain_id), None)
    if target is None:
        return False
    ts = now or now_iso()
    if not target.get("created"):
        target["created"] = ts
    target["last_updated"] = ts
    new_text = _stamp_text(text, domain_id, ts)
    if new_text is not None and yaml.safe_load(new_text) == data:
        _write_atomic(path, new_text)
    else:
        save_domains_index(workspace, data, header=_leading_comments(text))
    return True


def ensure_folder(workspace: Path, framework: Path, domain_id: str) -> bool:
    """Materialize `Domains/<Name>/` with the 5-file scaffold if not present.

    Returns True iff the folder was newly created. Idempotent: subsequent
    calls are near-no-ops and leave `domains-index.yaml` untouched. On first
    creation the matching index row is stamped via `stamp_index_row`
    (`created` if null, `last_updated` always). Raises ValueError when
    `domain_id` is not registered in `_memory/domains-index.yaml`.
    """
    row = lookup_domain(workspace, domain_id)
    if row is None:
        raise ValueError(
            f"Domain '{domain_id}' is not registered in domains-index.yaml. "
            "Run the `add-domain` skill first."
        )
    name = row.get("name") or domain_id.title()
    folder = workspace / "Domains" / name
    if (folder / "info.md").exists():
        return False
    template_dir = framework / "templates" / "domains"
    folder.mkdir(parents=True, exist_ok=True)
    for kind in DOMAIN_FILES:
        body = render_domain_file(
            (template_dir / f"{kind}.md").read_text(),
            name,
        )
        (folder / f"{kind}.md").write_text(body)
    stamp_index_row(workspace, domain_id)
    return True


def list_status(workspace: Path) -> list[dict[str, Any]]:
    """Return per-domain status: id, name, registered (always True), materialized."""
    out: list[dict[str, Any]] = []
    domains_dir = workspace / "Domains"
    for row in load_domains_index(workspace).get("domains", []) or []:
        name = row.get("name", "")
        out.append({
            "id": row.get("id"),
            "name": name,
            "registered": True,
            "materialized": (domains_dir / name).is_dir() if name else False,
        })
    return out


# Bare-template detection (used by purge-empty) ------------------------------

# Cells the agent populates with actual user data. Used to distinguish
# placeholder table rows from real ones in `_has_user_content`.
_TABLE_HEADER_WORDS = frozenset({
    "Name", "Role", "Phone", "Email", "Notes", "Last contacted",
    "Relationship", "Service", "URL", "Title", "Path", "Category", "Added",
    "Ref path", "Kind", "Source",
})

# Cell content that the framework templates render in placeholder table rows
# (matched case-insensitively; em-dash / hyphen runs are also treated as
# placeholders by the dash-only check below).
_TABLE_PLACEHOLDER_TOKENS = frozenset({
    "no entries yet",
    "none",
    "n/a",
    "tbd",
})


def _has_user_content(text: str, kind: str) -> bool:
    """Return True iff a domain markdown file has any user-supplied content
    in its user-writable sections.

    The detector is structural — it looks for the markers that real captures
    leave behind (H4 dated entries in `history.md`, customized RAG line in
    `status.md`, non-TOC bullets / paragraphs in `info.md`, non-placeholder
    table rows in `rolodex.md` / `sources.md`) — rather than comparing the
    file's framework-supplied prose to a fixed template version. This keeps
    the check robust against framework-template wording changes between
    releases.
    """
    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    body = re.sub(r"^_Last updated: [^_\n]+_\s*$", "", body, flags=re.MULTILINE)

    if kind == "history":
        return bool(re.search(r"^####\s+\d{4}-\d{2}-\d{2}", body, re.MULTILINE))

    if kind == "status":
        if "**Green** — fresh workspace, no open issues." not in body:
            if re.search(r"^\*\*\w+\*\*\s*[\u2014-]", body, re.MULTILINE):
                return True
        for m in re.finditer(r"^-\s+(.+)$", body, re.MULTILINE):
            content = m.group(1).strip()
            if not content:
                continue
            if re.match(r"^\[.+?\]\(#.+?\)\s*$", content):
                continue
            return True
        return False

    if kind == "info":
        text2 = re.sub(r"^\s*-\s+\[.+?\]\(#.+?\)\s*$", "", body, flags=re.MULTILINE)
        text2 = re.sub(r"^#.*$", "", text2, flags=re.MULTILINE)
        text2 = re.sub(r"^---+$", "", text2, flags=re.MULTILINE)
        text2 = re.sub(r"^>.*$", "", text2, flags=re.MULTILINE)
        text2 = re.sub(r"\n\s*\n+", "\n", text2).strip()
        return bool(text2)

    if kind in ("rolodex", "sources"):
        # Strip the framework "How this file stays current" boilerplate prose
        # block so plain bullets in it don't count as user content. Lives
        # between the H2 header and the next H2 / hr.
        body2 = re.sub(
            r"##\s+How this file stays current.*?(?=\n##\s|\n---)",
            "",
            body,
            flags=re.DOTALL,
        )
        for m in re.finditer(r"^\|(.+)\|\s*$", body2, re.MULTILINE):
            cells = [c.strip() for c in m.group(1).split("|")]
            non_empty = [c for c in cells if c]
            if not non_empty:
                continue
            # Header rows: every non-empty cell is a known column header.
            if all(c in _TABLE_HEADER_WORDS for c in non_empty):
                continue
            # Placeholder rows: every non-empty cell is either dash-only or a
            # known framework placeholder string ("no entries yet" etc.).
            def _is_placeholder_cell(c: str) -> bool:
                if set(c) <= {"-", "\u2014", " "}:
                    return True
                return c.lower() in _TABLE_PLACEHOLDER_TOKENS
            if all(_is_placeholder_cell(c) for c in non_empty):
                continue
            return True
        return False

    return False


def is_bare_template(folder: Path, framework: Path) -> bool:
    """Return True iff the folder holds only template defaults (no user content).

    `framework` is currently unused — kept in the signature so callers stay
    stable if a future implementation needs to look up the canonical template.
    The detector is structural per `_has_user_content`. A non-empty
    `Resources/` sub-folder always counts as user content.
    """
    del framework
    if not folder.is_dir():
        return False
    for kind in DOMAIN_FILES:
        path = folder / f"{kind}.md"
        if not path.exists():
            continue
        if _has_user_content(path.read_text(), kind):
            return False
    res = folder / "Resources"
    if res.exists() and any(res.iterdir()):
        return False
    return True


def purge_empty(
    workspace: Path,
    framework: Path,
    *,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """Delete bare-template default-domain folders.

    Returns (deleted_names, kept_names). Touches only folders matching one of
    the default domain names AND whose content is indistinguishable from a
    fresh template. User-edited folders are always kept.
    """
    deleted: list[str] = []
    kept: list[str] = []
    domains_dir = workspace / "Domains"
    if not domains_dir.is_dir():
        return ([], [])
    for name, _scope in DEFAULT_DOMAINS:
        folder = domains_dir / name
        if not folder.is_dir():
            continue
        if is_bare_template(folder, framework):
            if not dry_run:
                shutil.rmtree(folder)
            deleted.append(name)
        else:
            kept.append(name)
    return deleted, kept


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="domains")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ensure", help="Materialize a domain folder if missing.")
    e.add_argument("domain_id")
    sub.add_parser("list", help="Show registered + materialized status per domain.")
    pe = sub.add_parser(
        "purge-empty",
        help="Delete bare-template default-domain folders.",
    )
    pe.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or workspace_default(framework)
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd == "ensure":
        try:
            created = ensure_folder(workspace, framework, args.domain_id)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        row = lookup_domain(workspace, args.domain_id)
        name = (row or {}).get("name", args.domain_id)
        print(f"{'created' if created else 'kept'} Domains/{name}/")
        return 0
    if args.cmd == "list":
        for d in list_status(workspace):
            mark = "[materialized]" if d["materialized"] else "[registered]  "
            print(f"  {d['id']:12s}  {d['name']:14s}  {mark}")
        return 0
    if args.cmd == "purge-empty":
        deleted, kept = purge_empty(workspace, framework, dry_run=args.dry_run)
        verb = "would delete" if args.dry_run else "deleted"
        for n in deleted:
            print(f"  {verb}: Domains/{n}/")
        for n in kept:
            print(f"  kept (has user content): Domains/{n}/")
        if not deleted and not kept:
            print("  no default-domain folders found.")
        if args.dry_run and deleted:
            print(f"\nDry run — re-run without --dry-run to delete {len(deleted)} folder(s).")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
