#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Insert / refresh step indexes (superagent/docs/_internal/perf-improvement-ideas.md QW-2 + MI-4).

Operates in two modes:

  --skill MODE (default)
    Walks every skill markdown under `superagent/skills/*.md` longer than
    `--min-lines` (default 100). Inserts a "Step index" block right after
    the YAML frontmatter.

  --file PATH (single-doc MODE)
    Process one arbitrary markdown file (e.g. a long doc under
    `superagent/docs/`). Inserts the step index right after the H1 title;
    YAML frontmatter is optional. Combine with `--numbered-only` to index
    only H2 headings that begin with a numeric prefix (`## 1. ...`),
    ignoring scaffolding H2s like `## Table of Contents`.

The block looks like:

```
## Step index

| # | Step | Lines |
|---|------|-------|
| 1 | Load configuration | 25-32 |
| 2 | Load last-check context | 34-44 |
...
```

The agent reads the top of the file (~40 lines for skills, ~50 for docs),
uses `Read --offset --limit` to pull only the relevant step, and never
loads the full body. Long skills (`init.md`, `daily-update.md`, ...) and
long docs (`contracts/`, `architecture.md`) benefit most.

Step boundaries are derived from H2 headings (`## N. <name>`). H2-looking
lines inside fenced code blocks (``` or ~~~, with or without an info string)
are NOT steps — render templates embedded in skills routinely carry `## ...`
headings that belong to the rendered artifact, not to the skill.

The `Lines` column holds ABSOLUTE 1-based line numbers of the file as it
reads AFTER the block is inserted: `start` is the line of the H2 itself and
`end` is the last line before the next indexed H2 (or the last line of the
file). The block's own height is folded into the offsets, so `Read --offset
<start> --limit <end-start+1>` lands exactly on the step.

Re-run after any edit; idempotent (replaces an existing step-index block
in place). `--check` computes the would-be text and reports a file only
when it differs from what is on disk; it never writes.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# The closing fence matches only trailing spaces/tabs — NOT `\s*`, which
# would swallow following blank lines into the intro and grow the file by
# one blank line per re-run (idempotency bug, fixed 0.12.0).
FRONTMATTER_RE = re.compile(r"^(---[ \t]*\n.*?\n---[ \t]*\n)", re.DOTALL)
H1_RE = re.compile(r"^(#\s+.+\n)")
H2_LINE_RE = re.compile(r"^##\s+(.*)$")
# Leading step number: `1`, `1.`, `2a`, `2a.`, `2.a` — anything else (`2½.`,
# `A.`) is treated as unnumbered and the step is numbered sequentially. The
# trailing `\.?` after the letter accepts the `Nb.` form (`## 7b. Foo`);
# without it that heading fell through to sequential numbering and produced
# duplicate step numbers in the generated index.
STEP_PREFIX_RE = re.compile(r"^(\d+\.?[a-z]?)\.?\s+(.*)$")
NUMBERED_PREFIX_RE = re.compile(r"^\d+\.?[a-z]?\.?\s")
# Fenced-code delimiters (CommonMark: up to three spaces of indentation,
# then a run of >= 3 backticks or tildes). The opening fence may carry an
# info string; the closing fence must use the same character with a run at
# least as long, and nothing but whitespace after it.
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

# Explicit start/end markers wrap the generated block so we can replace it
# atomically on every re-run — no regex-against-content fragility.
STEP_INDEX_BEGIN = "<!-- step-index:start -->"
STEP_INDEX_END = "<!-- step-index:end -->"
EXISTING_INDEX_RE = re.compile(
    re.escape(STEP_INDEX_BEGIN) + r".*?" + re.escape(STEP_INDEX_END) + r"\n*",
    re.DOTALL,
)
# Backwards-compatible match for unmarked legacy blocks (the old format).
# The legacy block is: `## Step index` heading + intro lines + a contiguous
# table (lines starting with `|`). We lazily skip non-table lines, then
# greedily consume the FIRST contiguous `|`-prefixed block, then stop. This
# avoids swallowing body paragraphs OR a tables-in-body that appears later.
LEGACY_INDEX_RE = re.compile(
    r"^## Step index\n(?:[^|].*\n|\n)*?(?:\|.*\n)+",
    re.MULTILINE,
)


class Step(NamedTuple):
    number: str
    title: str
    start_line: int
    end_line: int


def mask_fenced_code(lines: list[str]) -> list[str]:
    """Return a copy of `lines` with every fenced-code line blanked.

    Line-preserving state machine: the list has the same length and the
    same indices as the input, so line numbers computed on the result are
    valid for the original. Both the fence delimiters and the lines between
    them become empty strings. Handles ``` and ~~~ fences (with info
    strings), requires the closing fence to reuse the opening character with
    a run at least as long, and treats an unterminated fence as running to
    the end of the input.
    """
    masked: list[str] = []
    close_re: re.Pattern[str] | None = None
    for line in lines:
        if close_re is not None:
            masked.append("")
            if close_re.match(line):
                close_re = None
            continue
        m = FENCE_OPEN_RE.match(line)
        # CommonMark: a backtick fence's info string may not contain a
        # backtick (that is inline code, not a fence). Tilde fences are
        # unrestricted.
        if m and (m.group(1)[0] == "~" or "`" not in m.group(2)):
            run = m.group(1)
            close_re = re.compile(
                r"^ {0,3}" + re.escape(run[0]) + r"{" + str(len(run)) + r",}[ \t]*$"
            )
            masked.append("")
            continue
        masked.append(line)
    return masked


def collect_steps(
    body: str,
    body_offset: int,
    numbered_only: bool = False,
) -> list[Step]:
    """Find H2 headings in the body. Returns Step entries with absolute line numbers.

    `body_offset` is the number of file lines that precede `body` in the
    final on-disk file (frontmatter / H1 intro PLUS the inserted step-index
    block and its surrounding blank lines). `start_line` is the 1-based
    absolute line of the H2; `end_line` is the 1-based absolute line just
    before the next indexed H2, or the last line of the body for the final
    step.

    H2s inside fenced code blocks are never steps (see `mask_fenced_code`).
    The step number is the heading's leading integer prefix when present
    (`## 3. Foo` -> `3`, `## 2a Bar` -> `2a`, `## 2b. Baz` -> `2b`);
    otherwise the step is numbered sequentially by its position among the
    collected steps.

    When `numbered_only` is True, H2s whose title does not begin with a
    numeric prefix are skipped — useful on long docs that have scaffolding
    H2s like `## Table of Contents`.
    """
    lines = body.splitlines()
    scan_lines = mask_fenced_code(lines)
    headings: list[tuple[int, str]] = []
    for idx, line in enumerate(scan_lines):
        m = H2_LINE_RE.match(line)
        if not m:
            continue
        title_full = m.group(1).strip()
        if numbered_only and not NUMBERED_PREFIX_RE.match(title_full):
            continue
        headings.append((idx, title_full))
    if not headings:
        return []
    steps: list[Step] = []
    for i, (idx, title_full) in enumerate(headings):
        next_idx = headings[i + 1][0] if i + 1 < len(headings) else len(lines)
        prefix_match = STEP_PREFIX_RE.match(title_full)
        if prefix_match:
            number = prefix_match.group(1).rstrip(".")
            title = prefix_match.group(2)
        else:
            number = str(len(steps) + 1)
            title = title_full
        steps.append(Step(
            number=number,
            title=title,
            start_line=idx + 1 + body_offset,
            # `next_idx` (0-based) is the next heading's index, which equals
            # the 1-based number of the line right before it; for the last
            # step it is the body's line count, i.e. its last line.
            end_line=max(next_idx, idx + 1) + body_offset,
        ))
    return steps


def index_block_height(steps: list[Step]) -> int:
    """Number of file lines the inserted block occupies, incl. its two blank guards.

    `process_one` writes `intro + "\\n" + render_index(steps) + "\\n\\n" + body`,
    i.e. one blank line, the rendered block, and one more blank line above
    the body. The height depends only on the NUMBER of steps (one table row
    each), never on the digit width of the line numbers, so a single pass
    over the steps converges and re-runs stay idempotent.
    """
    if not steps:
        return 0
    return len(render_index(steps).splitlines()) + 2


def render_index(steps: list[Step]) -> str:
    if not steps:
        return ""
    lines = [
        STEP_INDEX_BEGIN,
        "",
        "## Step index",
        "",
        "_Auto-generated by `tools/add_step_index.py`. Re-run on any edit._",
        "",
        "| # | Step | Lines |",
        "|---|------|-------|",
    ]
    for s in steps:
        title = s.title.replace("|", "\\|")
        lines.append(f"| {s.number} | {title} | {s.start_line}-{s.end_line} |")
    lines.append("")
    lines.append(STEP_INDEX_END)
    return "\n".join(lines)


def has_step_index(body: str) -> bool:
    """True if the body carries either the marker-wrapped block or a legacy one."""
    if STEP_INDEX_BEGIN in body:
        return True
    return bool(re.search(r"^##\s+Step index\s*$", body, re.MULTILINE))


def strip_existing_index(body: str) -> str:
    """Remove a marker-wrapped block (preferred) or a legacy unmarked block."""
    if STEP_INDEX_BEGIN in body:
        return EXISTING_INDEX_RE.sub("", body)
    return LEGACY_INDEX_RE.sub("", body)


def process_one(
    path: Path,
    min_lines: int,
    numbered_only: bool = False,
    require_frontmatter: bool = True,
    write: bool = True,
) -> tuple[bool, str]:
    """Process one markdown file. Returns (changed, reason).

    The "intro" — the prefix the step-index is inserted AFTER — is:
      * the YAML frontmatter, when present (skill-style files); else
      * the H1 title line, when present (doc-style files);
      * else position 0.

    `require_frontmatter` (default True for back-compat with the skill-walker
    behavior) refuses files lacking a YAML frontmatter; pass False when
    operating on plain docs.

    `write=False` (the `--check` path) computes the would-be text and
    returns `(True, reason)` when it differs from the file on disk, without
    writing anything. All other refusal reasons ("below min-lines", "no
    frontmatter", "no H2 steps", "no change") are reported identically in
    both modes so `--check` mirrors the real run exactly.
    """
    text = path.read_text()
    if len(text.splitlines()) < min_lines:
        return False, "below min-lines"
    fm_match = FRONTMATTER_RE.match(text)
    if fm_match:
        intro = fm_match.group(1)
        intro_line_count = len(intro.splitlines())
    else:
        if require_frontmatter:
            return False, "no frontmatter"
        h1_match = H1_RE.match(text)
        if h1_match:
            intro = h1_match.group(1)
            intro_line_count = len(intro.splitlines())
        else:
            intro = ""
            intro_line_count = 0
    after_intro = text[len(intro):]
    after_clean = strip_existing_index(after_intro) if has_step_index(after_intro) else after_intro
    after_clean = after_clean.lstrip("\n")
    # First pass: collect steps relative to the intro only, to learn how tall
    # the inserted block will be. Then shift every range by that height so
    # the `Lines` column addresses the file AS WRITTEN (block included).
    steps = collect_steps(after_clean, intro_line_count, numbered_only=numbered_only)
    if not steps:
        return False, "no H2 steps"
    shift = index_block_height(steps)
    steps = [
        s._replace(start_line=s.start_line + shift, end_line=s.end_line + shift)
        for s in steps
    ]
    rendered_index = render_index(steps).rstrip()
    insertion = rendered_index + "\n\n"
    new_text = intro + "\n" + insertion + after_clean
    if new_text == text:
        return False, "no change"
    if not write:
        return True, f"{len(steps)} steps would be indexed"
    path.write_text(new_text)
    return True, f"{len(steps)} steps indexed"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="add_step_index")
    parser.add_argument("--framework", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--min-lines", type=int, default=100,
                        help="Skip files shorter than this (default 100).")
    parser.add_argument("--skill", type=str, default=None,
                        help="Process only the skill with this stem.")
    parser.add_argument("--file", type=Path, default=None,
                        help="Process a single arbitrary markdown file "
                             "(e.g. a long doc). YAML frontmatter is optional "
                             "in this mode.")
    parser.add_argument("--numbered-only", action="store_true",
                        help="Skip H2 headings without a numeric prefix "
                             "(useful on long docs that have scaffolding H2s "
                             "like 'Table of Contents').")
    parser.add_argument("--check", action="store_true",
                        help="Only report which files would change (the "
                             "would-be text is computed and compared; nothing "
                             "is written).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint; see the module docstring for the two modes."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    verb = "would touch" if args.check else "updated    "
    if args.file is not None:
        if not args.file.exists():
            print(f"No such file: {args.file}", file=sys.stderr)
            return 1
        ok, reason = process_one(
            args.file,
            args.min_lines,
            numbered_only=args.numbered_only,
            require_frontmatter=False,
            write=not args.check,
        )
        if ok:
            print(f"{verb}  {args.file.name} ({reason})")
            return 0
        print(f"skipped      {args.file.name} ({reason})")
        return 0
    skills_dir = args.framework / "skills"
    if not skills_dir.is_dir():
        print(f"No skills dir at {skills_dir}", file=sys.stderr)
        return 1
    paths = sorted(skills_dir.glob("*.md"))
    if args.skill:
        paths = [p for p in paths if p.stem == args.skill]
    changed = 0
    for path in paths:
        ok, reason = process_one(
            path,
            args.min_lines,
            numbered_only=args.numbered_only,
            write=not args.check,
        )
        if ok:
            print(f"{verb}  {path.name} ({reason})")
            changed += 1
        else:
            print(f"skipped      {path.name} ({reason})")
    print(f"\n{changed} file(s) {'would be ' if args.check else ''}updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
