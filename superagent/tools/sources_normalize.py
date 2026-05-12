#!/usr/bin/env -S uv run python
"""Reference-file normalizer for Superagent.

Implements the liberal-input parser + canonical serializer described in
`contracts/sources.md` § 15.3. Hand-authored `.ref.md` / `.ref.txt` files
can be in any of these shapes:

  - YAML frontmatter (already canonical) — no-op.
  - Loose `Key: value` lines + a notes body.
  - A bare URL on the first non-blank line.
  - A bare absolute path / `~/path` on the first non-blank line.
  - A `1Password://...` URI on the first non-blank line.

The normalizer parses to a canonical dict, then serializes back to the
canonical YAML-frontmatter form (template: `templates/sources/ref.md`).

Modes (matches the interactive prompt described in the contract):
  rewrite             — rewrite in place, keep `<name>.original` backup
  rewrite_no_backup   — rewrite in place, no backup
  sibling             — write `<stem>.normalized.md` next to original; do not touch original
  keep                — return parsed fields without writing anything
  ask                 — interactive (CLI only)

CLI:
  uv run python -m superagent.tools.sources_normalize parse <path>
  uv run python -m superagent.tools.sources_normalize check <path>
  uv run python -m superagent.tools.sources_normalize apply <path> --mode rewrite|...
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------

CANONICAL_FIELDS = (
    "ref_version",
    "title",
    "description",
    "kind",
    "source",
    "auth_ref",
    "params",
    "ttl_minutes",
    "sensitive",
    "chunk_for_large",
    "related_domain",
    "related_project",
    "related_asset",
    "related_account",
    "added_by",
    "added_at",
    "normalized_at",
    "tags",
)

REQUIRED_FIELDS = ("kind", "source")

# Aliases for liberal parsing — case-insensitive on the LHS.
KEY_ALIASES: dict[str, str] = {
    "title": "title",
    "name": "title",
    "description": "description",
    "desc": "description",
    "summary": "description",

    "kind": "kind",
    "type": "kind",

    "source": "source",

    "url": ("source", "url"),
    "link": ("source", "url"),
    "source url": ("source", "url"),

    "cmd": ("source", "cli"),
    "command": ("source", "cli"),
    "shell": ("source", "cli"),

    "path": ("source", "file"),
    "file": ("source", "file"),
    "filepath": ("source", "file"),

    "mcp": ("source", "mcp"),
    "mcp call": ("source", "mcp"),

    "api": ("source", "api"),

    "vault": ("source", "vault"),
    "1password": ("source", "vault"),
    "1p": ("source", "vault"),

    "manual": ("source", "manual"),
    "instructions": ("source", "manual"),

    "ttl": "ttl_minutes",
    "ttl_minutes": "ttl_minutes",
    "ttl minutes": "ttl_minutes",
    "ttl_min": "ttl_minutes",
    "cache_ttl": "ttl_minutes",

    "sensitive": "sensitive",
    "secret": "sensitive",

    "auth": "auth_ref",
    "auth_ref": "auth_ref",
    "auth ref": "auth_ref",
    "credentials": "auth_ref",

    "domain": "related_domain",
    "related_domain": "related_domain",

    "project": "related_project",
    "related_project": "related_project",

    "asset": "related_asset",
    "related_asset": "related_asset",

    "account": "related_account",
    "related_account": "related_account",

    "tags": "tags",
    "labels": "tags",

    "notes": "_notes",
    "note": "_notes",
}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_URL_RE = re.compile(r"^https?://\S+$")
_PATH_RE = re.compile(r"^(/|~)[^\s]+$")
_VAULT_RE = re.compile(r"^[A-Za-z0-9_]+://\S+$")  # e.g. 1Password://, bitwarden://


def is_canonical(text: str) -> bool:
    """True if `text` parses as YAML frontmatter with required fields set."""
    fm, _body = _split_frontmatter(text)
    if fm is None:
        return False
    return all(fm.get(f) not in (None, "", []) for f in REQUIRED_FIELDS)


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return `(frontmatter_dict_or_None, body)`."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text.strip()
    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None, text.strip()
    if not isinstance(fm, dict):
        return None, text.strip()
    return fm, match.group(2).strip()


# ---------------------------------------------------------------------------
# Liberal parser
# ---------------------------------------------------------------------------

def parse_freeform(text: str) -> dict[str, Any]:
    """Parse liberal ref text into a partial canonical dict.

    Returns a dict with as many CANONICAL_FIELDS as we could lift, plus a
    `_notes` key holding the leftover body content.
    """
    fields: dict[str, Any] = {"ref_version": 1}

    # If the file already has YAML frontmatter, start from it.
    fm, body = _split_frontmatter(text)
    if fm is not None:
        for k, v in fm.items():
            if k in CANONICAL_FIELDS:
                fields[k] = v
        if body:
            fields["_notes"] = body
        # If frontmatter already has source + kind, we're done.
        if all(fields.get(k) for k in REQUIRED_FIELDS):
            return fields
        # Otherwise fall through to enrich from body lines.
        text_to_scan = body
    else:
        text_to_scan = text

    lines = text_to_scan.splitlines()
    notes_lines: list[str] = []
    notes_started = False

    # Pass 1: detect a bare URL / path / vault URI on the first non-blank line.
    first_nonblank = next((line.strip() for line in lines if line.strip()), "")
    if first_nonblank:
        if _URL_RE.match(first_nonblank):
            fields.setdefault("kind", "url")
            fields.setdefault("source", first_nonblank)
            fields.setdefault("title", _hostname_from_url(first_nonblank))
        elif _PATH_RE.match(first_nonblank):
            fields.setdefault("kind", "file")
            fields.setdefault("source", first_nonblank)
            fields.setdefault("title", first_nonblank.split("/")[-1])
        elif _VAULT_RE.match(first_nonblank) and "://" in first_nonblank:
            scheme = first_nonblank.split("://", 1)[0].lower()
            if scheme in ("1password", "bitwarden", "lastpass", "vault"):
                fields.setdefault("kind", "vault")
                fields.setdefault("source", first_nonblank)
                fields.setdefault("title", scheme + " item")

    # Pass 2: walk lines as Key: value, with the notes body starting at the
    # first heading or "Notes:" marker (or after a blank line that follows
    # any Key: value lines).
    for line in lines:
        stripped = line.rstrip()
        if notes_started:
            notes_lines.append(stripped)
            continue
        if stripped.startswith("#"):
            notes_started = True
            notes_lines.append(stripped)
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _-]*?)\s*[:=]\s*(.*)$", stripped)
        if not match:
            if stripped == "" and any(fields.get(k) for k in REQUIRED_FIELDS):
                continue
            if stripped:
                notes_lines.append(stripped)
            continue
        raw_key, raw_value = match.group(1).strip().lower(), match.group(2).strip()
        target = KEY_ALIASES.get(raw_key)
        if target is None:
            notes_lines.append(stripped)
            continue
        if isinstance(target, tuple):
            field_name, default_kind = target
            if field_name == "source" and not fields.get("source"):
                fields["source"] = _strip_quotes(raw_value)
                fields.setdefault("kind", default_kind)
            elif field_name == "source":
                pass
        else:
            value: Any = raw_value
            if target == "tags":
                value = [t.strip() for t in raw_value.split(",") if t.strip()]
            elif target == "sensitive":
                value = _coerce_bool(raw_value)
            elif target == "ttl_minutes":
                try:
                    value = int(raw_value)
                except ValueError:
                    notes_lines.append(stripped)
                    continue
            elif target == "_notes":
                notes_started = True
                if raw_value:
                    notes_lines.append(raw_value)
                continue
            else:
                value = _strip_quotes(raw_value)
            if target not in fields or fields[target] in (None, "", []):
                fields[target] = value

    leftover = "\n".join(notes_lines).strip()
    if leftover:
        fields["_notes"] = leftover

    return fields


def _strip_quotes(value: str) -> str:
    """Trim matching surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _coerce_bool(value: str) -> bool:
    """Liberal bool parsing."""
    return value.strip().lower() in ("true", "yes", "y", "1", "on")


def _hostname_from_url(url: str) -> str:
    """Extract a hostname for use as a default title."""
    match = re.match(r"^https?://([^/]+)", url)
    return match.group(1) if match else url


# ---------------------------------------------------------------------------
# Canonical serializer
# ---------------------------------------------------------------------------

def to_canonical(fields: dict[str, Any]) -> str:
    """Serialize parsed fields to canonical `<frontmatter>\\n---\\n# Notes\\n<body>`.

    Empties / unset fields are omitted; required defaults are filled.
    """
    out: dict[str, Any] = {}
    for f in CANONICAL_FIELDS:
        if f in ("ref_version",):
            out[f] = fields.get(f, 1)
            continue
        v = fields.get(f)
        if v in (None, "", []):
            continue
        out[f] = v
    out.setdefault("ref_version", 1)
    out.setdefault("normalized_at", now_iso())
    if not out.get("added_at"):
        out["added_at"] = now_iso()
    if not out.get("added_by"):
        out["added_by"] = "user"

    notes = fields.get("_notes", "")
    fm_text = yaml.safe_dump(out, sort_keys=False, allow_unicode=True).strip()
    body_section = f"\n\n# Notes\n\n{notes}\n" if notes else "\n"
    return f"---\n{fm_text}\n---{body_section}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def missing_required(fields: dict[str, Any]) -> list[str]:
    """Return required-field names that aren't set in `fields`."""
    return [f for f in REQUIRED_FIELDS if not fields.get(f)]


# ---------------------------------------------------------------------------
# Apply (write) modes
# ---------------------------------------------------------------------------

def propose(path: Path) -> dict[str, Any]:
    """Inspect a ref file. Returns a proposal dict:

      {
        "path": str,
        "already_canonical": bool,
        "fields": dict (canonical),
        "missing_required": [str],
        "canonical_text": str,
        "original_text": str,
      }
    """
    text = path.read_text()
    if is_canonical(text):
        fm, body = _split_frontmatter(text)
        fields = dict(fm or {})
        if body:
            fields["_notes"] = body
        return {
            "path": str(path),
            "already_canonical": True,
            "fields": fields,
            "missing_required": [],
            "canonical_text": text,
            "original_text": text,
        }
    fields = parse_freeform(text)
    return {
        "path": str(path),
        "already_canonical": False,
        "fields": fields,
        "missing_required": missing_required(fields),
        "canonical_text": to_canonical(fields),
        "original_text": text,
    }


def apply_mode(path: Path, mode: str) -> dict[str, Any]:
    """Apply a normalization mode to `path`. Returns a result summary.

    Modes: rewrite | rewrite_no_backup | sibling | keep
    """
    proposal = propose(path)
    if proposal["already_canonical"]:
        return {**proposal, "action": "noop"}
    if proposal["missing_required"]:
        return {
            **proposal,
            "action": "blocked",
            "reason": f"missing required fields: {proposal['missing_required']}",
        }

    canonical = proposal["canonical_text"]

    if mode == "keep":
        return {**proposal, "action": "kept"}

    if mode == "sibling":
        stem = path.name
        for suffix in (".ref.md", ".ref.txt"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        else:
            stem = path.stem
        sibling = path.with_name(f"{stem}.normalized.ref.md")
        sibling.write_text(canonical)
        return {**proposal, "action": "wrote_sibling", "wrote_to": str(sibling)}

    if mode == "rewrite":
        backup = path.with_name(path.name + ".original")
        if not backup.exists():
            backup.write_text(proposal["original_text"])
        path.write_text(canonical)
        return {**proposal, "action": "rewrote", "backup": str(backup)}

    if mode == "rewrite_no_backup":
        path.write_text(canonical)
        return {**proposal, "action": "rewrote", "backup": None}

    raise ValueError(f"unknown mode: {mode!r}")


# ---------------------------------------------------------------------------
# Interactive prompt (CLI ask mode)
# ---------------------------------------------------------------------------

PROMPT_TEXT = """Normalize {path}?
  [r] Rewrite in place + keep .original backup  [recommended]
  [n] Rewrite in place, no backup
  [s] Write sibling .normalized.ref.md, leave my file alone
  [k] Use parsed values for this read only; don't write
Choice [r/n/s/k]: """

CHOICE_TO_MODE = {
    "r": "rewrite",
    "n": "rewrite_no_backup",
    "s": "sibling",
    "k": "keep",
}


def ask_choice(path: Path) -> str:
    """Show the prompt; read one character; return the matching mode name."""
    while True:
        choice = input(PROMPT_TEXT.format(path=path)).strip().lower()[:1]
        if choice in CHOICE_TO_MODE:
            return CHOICE_TO_MODE[choice]
        print("Pick one of r / n / s / k.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(prog="sources_normalize")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="Print the parsed canonical fields as JSON.")
    p.add_argument("path", type=Path)

    c = sub.add_parser("check", help="Exit 0 if already canonical; non-zero otherwise.")
    c.add_argument("path", type=Path)

    a = sub.add_parser("apply", help="Apply a normalization mode.")
    a.add_argument("path", type=Path)
    a.add_argument("--mode", choices=["rewrite", "rewrite_no_backup",
                                       "sibling", "keep", "ask"],
                   default="ask")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.cmd == "parse":
        if not args.path.exists():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 1
        proposal = propose(args.path)
        print(json.dumps({
            "path": proposal["path"],
            "already_canonical": proposal["already_canonical"],
            "missing_required": proposal["missing_required"],
            "fields": proposal["fields"],
        }, indent=2, default=str))
        return 0

    if args.cmd == "check":
        if not args.path.exists():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 1
        return 0 if is_canonical(args.path.read_text()) else 2

    if args.cmd == "apply":
        if not args.path.exists():
            print(f"No such file: {args.path}", file=sys.stderr)
            return 1
        mode = args.mode
        if mode == "ask":
            proposal = propose(args.path)
            if proposal["already_canonical"]:
                print(f"{args.path}: already canonical; nothing to do.")
                return 0
            if proposal["missing_required"]:
                print(f"{args.path}: missing required fields "
                      f"{proposal['missing_required']!r}; "
                      f"add them and re-run.", file=sys.stderr)
                return 1
            mode = ask_choice(args.path)
        result = apply_mode(args.path, mode)
        print(json.dumps({
            "action": result["action"],
            "path": result["path"],
            "already_canonical": result["already_canonical"],
            "missing_required": result["missing_required"],
            **({"backup": result["backup"]} if result["action"] == "rewrote" else {}),
            **({"wrote_to": result["wrote_to"]} if result["action"] == "wrote_sibling" else {}),
            **({"reason": result["reason"]} if result.get("action") == "blocked" else {}),
        }, indent=2, default=str))
        return 0 if result["action"] != "blocked" else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
