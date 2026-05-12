#!/usr/bin/env -S uv run python
"""Inbox triage helper.

Implements superagent/docs/_internal/ideas-better-structure.md item #5.

Walks files in `workspace/Inbox/`, classifies them by
extension + filename heuristics, and proposes a destination under
`Sources/<category>/` (the user can override; layout is user-defined per
contracts/sources.md \u00a7 15.1). Records every triage decision in
`Inbox/_processed.yaml` so the agent learns the user's filing patterns.

This module does the *classification* part. Actual file moves and
`add-source` invocations happen via the `inbox-triage` skill, which
prompts the user for each candidate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml


# Filename hints → suggested category.
KEYWORD_CATEGORIES: list[tuple[str, str]] = [
    ("w-2", "taxes"), ("w2", "taxes"), ("1099", "taxes"),
    ("k-1", "taxes"), ("1040", "taxes"), ("tax", "taxes"), ("return", "taxes"),
    ("vaccine", "medical"), ("imm", "medical"), ("lab", "medical"),
    ("rx", "medical"), ("prescription", "medical"), ("mri", "medical"),
    ("xray", "medical"), ("x-ray", "medical"), ("eob", "medical"),
    ("title", "vehicles"), ("registration", "vehicles"),
    ("insurance-card", "insurance"), ("policy", "insurance"),
    ("declarations", "insurance"),
    ("warranty", "warranties"), ("manual", "warranties"),
    ("receipt", "warranties"),
    ("deed", "property"), ("mortgage", "property"), ("lease", "property"),
    ("escrow", "property"), ("hoa", "property"),
    ("will", "legal"), ("trust", "legal"), ("poa", "legal"),
    ("directive", "legal"), ("nda", "legal"), ("contract", "legal"),
    ("passport", "identity"), ("license", "identity"), ("ssn", "identity"),
    ("birth", "identity"), ("marriage", "identity"),
    ("vet", "pets"), ("rabies", "pets"),
    ("diploma", "education"), ("transcript", "education"),
    ("certification", "education"),
]

EXTENSION_HINTS: dict[str, str] = {
    ".pdf": "document",
    ".jpg": "scan",
    ".jpeg": "scan",
    ".png": "scan",
    ".heic": "scan",
    ".tif": "scan",
    ".tiff": "scan",
    ".doc": "document",
    ".docx": "document",
    ".xls": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".csv": "spreadsheet",
    ".txt": "text",
    ".md": "text",
    ".m4a": "audio",
    ".mp3": "audio",
    ".mp4": "video",
    ".mov": "video",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def save_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def classify(path: Path) -> dict[str, Any]:
    """Heuristic classification: returns suggested category + confidence."""
    name = path.name.lower()
    ext = path.suffix.lower()
    kind = EXTENSION_HINTS.get(ext, "other")
    matched: list[tuple[str, str]] = []
    for keyword, cat in KEYWORD_CATEGORIES:
        if keyword in name:
            matched.append((keyword, cat))
    if matched:
        # Vote by frequency.
        votes: dict[str, int] = {}
        for _kw, cat in matched:
            votes[cat] = votes.get(cat, 0) + 1
        best_cat, best_count = max(votes.items(), key=lambda x: x[1])
        confidence = "high" if best_count >= 2 else "medium"
        category = best_cat
    else:
        category = "uncategorized"
        confidence = "low"
    suggested_path = f"Sources/{category}/{path.name}"
    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "extension": ext,
        "kind_hint": kind,
        "category_suggested": category,
        "confidence": confidence,
        "matched_keywords": [m[0] for m in matched],
        "suggested_path": suggested_path,
        "modified_at": dt.datetime.fromtimestamp(
            path.stat().st_mtime, tz=dt.timezone.utc).isoformat()
            if path.exists() else None,
    }


def list_inbox(workspace: Path) -> list[Path]:
    inbox = workspace / "Inbox"
    if not inbox.exists():
        return []
    return [p for p in inbox.iterdir()
            if p.is_file() and not p.name.startswith(".") and not p.name.startswith("_")]


def stale_items(workspace: Path, days: int = 14) -> list[Path]:
    cutoff = dt.datetime.now().timestamp() - days * 86400
    return [p for p in list_inbox(workspace) if p.stat().st_mtime < cutoff]


def record_decision(workspace: Path, decision: dict[str, Any]) -> None:
    inbox = workspace / "Inbox"
    if not inbox.exists():
        return
    log_path = inbox / "_processed.yaml"
    data = load_yaml(log_path) or {"schema_version": 1, "decisions": []}
    data.setdefault("decisions", []).append({
        "ts": now_iso(),
        **decision,
    })
    save_yaml(log_path, data)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="inbox_triage")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List items currently in Inbox/.")
    s = sub.add_parser("stale", help="List stale items (older than --days).")
    s.add_argument("--days", type=int, default=14)
    c = sub.add_parser("classify", help="Classify all (or one) inbox item(s).")
    c.add_argument("--file", type=str, default=None,
                   help="Specific file under Inbox/ (default: all).")
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("record", help="Record a triage decision.")
    r.add_argument("--file", required=True, type=str)
    r.add_argument("--action", required=True,
                   choices=["filed", "discarded", "left", "deferred"])
    r.add_argument("--destination", type=str, default="")
    r.add_argument("--note", type=str, default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    inbox = workspace / "Inbox"
    if not inbox.exists():
        print(f"no Inbox at {inbox}", file=sys.stderr)
        return 1
    if args.cmd == "list":
        for p in list_inbox(workspace):
            mtime = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
            print(f"{p.name:<50}{p.stat().st_size:>10}  {mtime}")
        return 0
    if args.cmd == "stale":
        for p in stale_items(workspace, args.days):
            mtime = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
            age = (dt.datetime.now() - dt.datetime.fromtimestamp(p.stat().st_mtime)).days
            print(f"{p.name:<50}{age:>4}d  {mtime}")
        return 0
    if args.cmd == "classify":
        files = [inbox / args.file] if args.file else list_inbox(workspace)
        results = [classify(f) for f in files]
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            print(f"{'file':<40}{'category':<16}{'conf':<8}{'suggested path'}")
            print("-" * 110)
            for r in results:
                print(f"{r['filename']:<40}{r['category_suggested']:<16}"
                      f"{r['confidence']:<8}{r['suggested_path']}")
        return 0
    if args.cmd == "record":
        record_decision(workspace, {
            "file": args.file,
            "action": args.action,
            "destination": args.destination,
            "note": args.note,
        })
        print(f"recorded {args.action} for {args.file}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
