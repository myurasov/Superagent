#!/usr/bin/env python3
"""Scanner for anti-patterns in skill markdown files.

Implements perf-improvement-ideas.md § "Anti-patterns to flag in skills".

Scans every skill under `superagent/skills/*.md` (and optionally
`workspace/_custom/skills/*.md`) for the documented patterns and prints a
report. Used by `supertailor-review` and `doctor` to surface candidates for
refactor.

The pattern catalogue lives in `superagent/rules/anti-patterns.yaml` (one row
per pattern: id, severity, description, regex source, flags, mitigation).
Users may extend the catalogue by writing a same-shape file at
`workspace/_custom/rules/anti-patterns.yaml`; the loader concatenates the
framework list followed by the user list.

Pattern shape (each row in the YAML):
    id:          str   (stable id, e.g. "AP-1")
    severity:    "warning" | "info"
    description: str
    pattern:     str   (Python re.compile source)
    flags:       list  (subset of: IGNORECASE, DOTALL, MULTILINE)
    mitigation:  str
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


_FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent
_RULES_FILE = _FRAMEWORK_ROOT / "rules" / "anti-patterns.yaml"

_FLAG_MAP = {
    "IGNORECASE": re.IGNORECASE,
    "I": re.IGNORECASE,
    "DOTALL": re.DOTALL,
    "S": re.DOTALL,
    "MULTILINE": re.MULTILINE,
    "M": re.MULTILINE,
}


def _resolve_flags(names: list[str] | None) -> int:
    """Translate a list of flag names to an integer bitmask."""
    if not names:
        return 0
    flags = 0
    for name in names:
        key = str(name).upper()
        if key not in _FLAG_MAP:
            raise ValueError(f"Unknown regex flag: {name!r}")
        flags |= _FLAG_MAP[key]
    return flags


def _load_yaml_rules(path: Path) -> list[dict[str, Any]]:
    """Read a rule YAML file. Returns the `rules` list (empty if missing)."""
    if not path.exists():
        return []
    with path.open() as fh:
        doc = yaml.safe_load(fh) or {}
    rules = doc.get("rules") or []
    if not isinstance(rules, list):
        raise ValueError(f"{path}: top-level `rules` must be a list")
    return rules


def _compile_rules(
    raw_rules: list[dict[str, Any]],
) -> list[tuple[str, str, str, re.Pattern[str]]]:
    """Compile raw YAML rule rows into the (id, severity, description, regex) tuples."""
    compiled: list[tuple[str, str, str, re.Pattern[str]]] = []
    for row in raw_rules:
        rid = str(row["id"])
        severity = str(row.get("severity", "info"))
        description = str(row.get("description", ""))
        pattern_src = row["pattern"]
        flags = _resolve_flags(row.get("flags"))
        compiled.append((rid, severity, description, re.compile(pattern_src, flags)))
    return compiled


def load_rules(
    framework_rules_file: Path = _RULES_FILE,
    user_rules_file: Path | None = None,
) -> tuple[list[tuple[str, str, str, re.Pattern[str]]], dict[str, str]]:
    """Load + compile framework rules, then optionally append user rules.

    Returns (compiled_patterns, mitigations_by_id).
    """
    raw = _load_yaml_rules(framework_rules_file)
    if user_rules_file is not None:
        raw = raw + _load_yaml_rules(user_rules_file)
    patterns = _compile_rules(raw)
    mitigations = {
        str(row["id"]): str(row.get("mitigation", "")) for row in raw
    }
    return patterns, mitigations


# Default catalogue compiled at import time from the framework YAML.
PATTERNS, MITIGATIONS = load_rules()


def scan_file(
    path: Path,
    patterns: list[tuple[str, str, str, re.Pattern[str]]] | None = None,
    mitigations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return a list of pattern hits in `path`.

    `patterns` and `mitigations` default to the module-level catalogue (loaded
    from `superagent/rules/anti-patterns.yaml`). Callers can pass an extended
    catalogue (e.g. framework + user-overlay) via `load_rules(...)`.
    """
    use_patterns = patterns if patterns is not None else PATTERNS
    use_mitigations = mitigations if mitigations is not None else MITIGATIONS
    body = path.read_text()
    hits: list[dict[str, Any]] = []
    for pid, severity, description, pattern in use_patterns:
        for match in pattern.finditer(body):
            line_no = body[:match.start()].count("\n") + 1
            snippet = body[max(0, match.start() - 30):match.end() + 30]
            snippet = " ".join(snippet.split())[:160]
            hits.append({
                "pattern": pid,
                "severity": severity,
                "description": description,
                "mitigation": use_mitigations.get(pid, ""),
                "line": line_no,
                "snippet": snippet,
            })
    return hits


def scan_dir(
    directory: Path,
    patterns: list[tuple[str, str, str, re.Pattern[str]]] | None = None,
    mitigations: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan every non-underscore *.md file in `directory`."""
    if not directory.exists():
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        hits = scan_file(path, patterns=patterns, mitigations=mitigations)
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
    framework_rules = args.framework / "rules" / "anti-patterns.yaml"
    user_rules = (
        args.workspace / "_custom" / "rules" / "anti-patterns.yaml"
        if args.workspace else None
    )
    patterns, mitigations = load_rules(framework_rules, user_rules)
    framework_skills = args.framework / "skills"
    by_file = scan_dir(framework_skills, patterns=patterns, mitigations=mitigations)
    if args.workspace:
        custom_skills = args.workspace / "_custom" / "skills"
        custom_hits = scan_dir(
            custom_skills, patterns=patterns, mitigations=mitigations,
        )
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
