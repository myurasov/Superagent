#!/usr/bin/env python3
"""Generate `superagent/skills/_manifest.yaml` — the skill-discovery manifest.

Implements superagent/docs/_internal/perf-improvement-ideas.md QW-1.

The manifest is a small (~5-10 KB) summary of every skill: name, one-line,
triggers, mcp_required, mcp_optional, cli_required, cli_optional, plus a
heuristic "typical files read" / "typical token cost" hint inferred from
the skill body.

The agent reads the manifest ONCE to decide which skill applies. Then it
reads ONLY the chosen skill's full markdown for execution. Saves 2-5k
tokens on every "which skill should I run?" turn.

Re-run after any skill add / change. The Supertailor's hygiene pass should
re-render it as part of its existing checks.

Also includes (when present) any user-overlay skills under
`workspace/_custom/skills/*.md`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
H2_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)
H3_RE = re.compile(r"^###\s+(.*)$", re.MULTILINE)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_skill(path: Path) -> dict[str, Any] | None:
    """Parse one skill markdown file. Returns the manifest row or None."""
    body = path.read_text()
    match = FRONTMATTER_RE.match(body)
    if not match:
        return None
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None
    rest = body[match.end():]
    h2_count = len(H2_RE.findall(rest))
    h3_count = len(H3_RE.findall(rest))
    line_count = len(body.splitlines())
    char_count = len(body)
    typical_token_cost = char_count // 4

    typical_files = infer_files_read(rest)

    return {
        "name": fm.get("name", path.stem),
        "stem": path.stem,
        "path": str(path),
        "one_line": (fm.get("description") or "").strip().split("\n")[0][:200],
        "triggers": fm.get("triggers") or [],
        "mcp_required": fm.get("mcp_required") or [],
        "mcp_optional": fm.get("mcp_optional") or [],
        "cli_required": fm.get("cli_required") or [],
        "cli_optional": fm.get("cli_optional") or [],
        "lines": line_count,
        "h2_steps": h2_count,
        "h3_subsections": h3_count,
        "typical_token_cost": typical_token_cost,
        "typical_files_read": typical_files,
    }


def infer_files_read(body: str) -> list[str]:
    """Heuristic: pull paths under workspace/_memory or Domains/ or Projects/."""
    seen: list[str] = []
    pattern = re.compile(
        r"(?:workspace/)?"
        r"(?:_memory/[a-z_-]+\.yaml"
        r"|Domains/[A-Za-z_-]+/[a-z_-]+\.md"
        r"|Projects/[a-z0-9_-]+/[a-z_-]+\.md"
        r"|Sources/(?:documents|references|_cache)/[^\s\)]+)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(body):
        path = match.group(0)
        if path not in seen:
            seen.append(path)
        if len(seen) >= 8:
            break
    return seen


def collect_skills(framework: Path, workspace: Path | None) -> list[dict[str, Any]]:
    """Walk skill directories and return a list of manifest rows."""
    skills: list[dict[str, Any]] = []
    framework_dir = framework / "skills"
    if framework_dir.exists():
        for path in sorted(framework_dir.glob("*.md")):
            row = parse_skill(path)
            if row is not None:
                row["origin"] = "framework"
                skills.append(row)
    if workspace is not None:
        custom_dir = workspace / "_custom" / "skills"
        if custom_dir.exists():
            for path in sorted(custom_dir.glob("*.md")):
                row = parse_skill(path)
                if row is not None:
                    row["origin"] = "custom"
                    skills.append(row)
    return skills


def render(skills: list[dict[str, Any]]) -> str:
    """Render the manifest YAML."""
    payload = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "skill_count": len(skills),
        "skills": skills,
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="build_skill_manifest")
    parser.add_argument("--framework", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--workspace", type=Path, default=None,
                        help="Optional workspace root for _custom/skills/.")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output path; default <framework>/skills/_manifest.yaml.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    skills = collect_skills(args.framework, args.workspace)
    output = args.output or (args.framework / "skills" / "_manifest.yaml")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(skills))
    print(f"Wrote {len(skills)} skill rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
