#!/usr/bin/env python3
"""Scanner for anti-patterns in skill markdown files.

Implements perf-improvement-ideas.md § "Anti-patterns to flag in skills".

Scans every skill under `superagent/skills/*.md` (and optionally
`workspace/_custom/skills/*.md`) for the documented patterns
and prints a report. Used by `tailor-review` and `doctor` to surface
candidates for refactor.

Patterns detected (regex-based; conservative to avoid false positives):

  AP-1  "read the X's info, status, history, rolodex" — 4-file blanket reads.
  AP-2  "all open tasks" without a domain/project/asset filter.
  AP-3  "search across the whole workspace" / "grep across" without scope.
  AP-4  Sequential "run X, then Y, then Z" that should be parallel-batched.
  AP-5  "pull the full email thread" without consulting interaction-log first.
  AP-6  "re-render the daily-update" / "regenerate the briefing" without cache check.
  AP-7  "read the whole procedures.md" / unbounded full-file Read.
  AP-8  "read every skill markdown" — manifest-bypass.
  AP-9  Loaded large file then LLM-extracted one fact (no Grep/FTS first).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# Each pattern: (id, severity, description, regex). Case-insensitive.
PATTERNS: list[tuple[str, str, str, re.Pattern[str]]] = [
    (
        "AP-1", "warning",
        "Blanket 4-file read (info + status + history + rolodex).",
        re.compile(
            r"\b(?:read|load|open)\b[^.]*\binfo(?:\.md)?\b[^.]*\bstatus(?:\.md)?\b"
            r"[^.]*\bhistory(?:\.md)?\b[^.]*\brolodex(?:\.md)?\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "AP-2", "warning",
        "Unfiltered 'all open tasks' read.",
        re.compile(
            r"\ball (?:open|active) tasks\b(?![^.]*(?:related_domain|related_project|"
            r"related_asset|--domain|--project|--asset|filter|scope))",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "AP-3", "info",
        "Whole-workspace grep without scope.",
        re.compile(
            r"\b(?:grep|search)\b[^.]*\b(?:across the (?:whole |entire )?workspace|every file)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "AP-4", "info",
        "Sequential run-X-then-Y-then-Z that may be parallelizable.",
        re.compile(
            r"\b(?:run|invoke|call)\b\s+\S+\s*[,;]\s*then\s+\S+\s*[,;]\s*then\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "AP-5", "info",
        "Full email-thread pull without checking interaction-log first.",
        re.compile(
            r"\b(?:pull|fetch|load|get)\b[^.]*\bfull (?:email )?thread\b"
            r"(?![^.]*(?:interaction-log|local mirror|cache))",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "AP-6", "info",
        "Briefing regen without cache check.",
        re.compile(
            r"\b(?:re-?render|regenerate)\b[^.]*\b(?:daily[- ]update|briefing|"
            r"weekly[- ]review|monthly[- ]review)\b"
            r"(?![^.]*(?:briefing[_ -]cache|_artifacts|cache hit))",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "AP-7", "warning",
        "Unbounded read of a long doc (procedures.md / AGENTS.md).",
        re.compile(
            r"\b(?:read|load)\b[^.]*\b(?:whole|entire|all of|full)\b[^.]*"
            r"\b(?:procedures\.md|AGENTS\.md)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "AP-8", "warning",
        "Manifest-bypass: 'read every skill markdown' to discover a skill.",
        re.compile(
            r"\bread (?:every|all) skill (?:markdown|file|md)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "AP-9", "info",
        "Load-then-extract: large file loaded, single fact extracted.",
        re.compile(
            r"\b(?:load|read|open)\b[^.]*\b(?:full file|entire file|whole file)\b"
            r"[^.]*\b(?:extract|find|locate|get)\s+(?:one|a single|the)\s+\w+\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
]


# Mitigations a skill author can use to avoid each pattern.
MITIGATIONS: dict[str, str] = {
    "AP-1": "Specify which sections to read: 'read info.md § Profile + status.md § Current Status; pull history.md only if needed'.",
    "AP-2": "Always include the filter: 'tasks where related_domain == X' or '--project Y'.",
    "AP-3": "Scope the grep to a specific Domain / Project folder.",
    "AP-4": "Use a single tool-call message with all tool calls so they parallelize.",
    "AP-5": "Consult the local interaction-log mirror or Sources cache first; only fetch the thread if the local copy is stale.",
    "AP-6": "Call `tools/briefing_cache.py get` (or read `_memory/_artifacts/<skill>/<key>.md`) before regenerating.",
    "AP-7": "Use Grep first, then `Read --offset --limit` against the matching range. Or read the file's table of contents only.",
    "AP-8": "Read `skills/_manifest.yaml` first; load only the chosen skill's markdown.",
    "AP-9": "Use `Grep` (or, when available, the FTS5 search index) to extract just the matching lines.",
}


def scan_file(path: Path) -> list[dict[str, Any]]:
    """Return a list of pattern hits in `path`."""
    body = path.read_text()
    hits: list[dict[str, Any]] = []
    for pid, severity, description, pattern in PATTERNS:
        for match in pattern.finditer(body):
            line_no = body[:match.start()].count("\n") + 1
            snippet = body[max(0, match.start() - 30):match.end() + 30]
            snippet = " ".join(snippet.split())[:160]
            hits.append({
                "pattern": pid,
                "severity": severity,
                "description": description,
                "mitigation": MITIGATIONS.get(pid, ""),
                "line": line_no,
                "snippet": snippet,
            })
    return hits


def scan_dir(directory: Path) -> dict[str, list[dict[str, Any]]]:
    if not directory.exists():
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        hits = scan_file(path)
        if hits:
            out[path.name] = hits
    return out


def render_text(by_file: dict[str, list[dict[str, Any]]]) -> str:
    if not by_file:
        return "No anti-patterns detected.\n"
    lines = []
    for fname, hits in by_file.items():
        lines.append(f"## {fname} ({len(hits)} hit(s))")
        lines.append("")
        for h in hits:
            lines.append(f"  [{h['pattern']}] {h['severity']:<8} line {h['line']:>4}: {h['description']}")
            lines.append(f"    snippet: {h['snippet']}")
            if h.get("mitigation"):
                lines.append(f"    mitigation: {h['mitigation']}")
            lines.append("")
    total = sum(len(v) for v in by_file.values())
    lines.append(f"# Total: {total} hit(s) across {len(by_file)} file(s).")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="anti_patterns")
    parser.add_argument("--framework", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--workspace", type=Path, default=None,
                        help="Optional workspace root (also scans _custom/skills/).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with non-zero status if any 'warning' hits found.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework_skills = args.framework / "skills"
    by_file = scan_dir(framework_skills)
    if args.workspace:
        custom_skills = args.workspace / "_custom" / "skills"
        custom_hits = scan_dir(custom_skills)
        for k, v in custom_hits.items():
            by_file[f"_custom/{k}"] = v
    if args.json:
        print(json.dumps(by_file, indent=2))
    else:
        print(render_text(by_file))
    if args.strict:
        warning_hits = sum(
            1 for hits in by_file.values()
            for h in hits if h.get("severity") == "warning"
        )
        return 1 if warning_hits > 0 else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
