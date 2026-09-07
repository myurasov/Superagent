# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/add_step_index.py`.

Covers --file mode, the --numbered-only filter, idempotency, absolute
line-number accuracy of the `Lines` column, fenced-code masking, and the
read-only contract of --check.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

ROW_RE = re.compile(r"^\| (?P<num>[^|]+?) \| (?P<title>.*?) \| (?P<a>\d+)-(?P<b>\d+) \|$")

SKILL_FIXTURE = textwrap.dedent("""\
    ---
    name: fixture-skill
    description: Synthetic skill used by the step-index tests.
    triggers:
      - fixture
    ---

    # Fixture skill

    Intro paragraph before the first step.

    ## 1. Load configuration

    Read the config.

    Second paragraph.

    ## 2. Branch on intent

    Decide.

    ## 3. Write the result

    Persist.

    ## Guardrails

    Unnumbered trailing section.
""")


def _index_rows(text: str) -> list[tuple[str, str, int, int]]:
    """Parse `| n | title | a-b |` rows out of the rendered step-index block."""
    rows: list[tuple[str, str, int, int]] = []
    block = text.split("<!-- step-index:start -->", 1)[1].split("<!-- step-index:end -->", 1)[0]
    for line in block.splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append((m["num"], m["title"], int(m["a"]), int(m["b"])))
    return rows


def _assert_rows_point_at_headings(text: str) -> None:
    """Every row's `a` is the 1-based line of its H2; `b` is the line before the next H2."""
    lines = text.splitlines()
    rows = _index_rows(text)
    assert rows, "expected at least one index row"
    for i, (num, title, a, b) in enumerate(rows):
        heading = lines[a - 1]
        assert heading.startswith("## "), f"row {num!r}: line {a} is not an H2: {heading!r}"
        assert title in heading, f"row {num!r}: line {a} ({heading!r}) lacks title {title!r}"
        assert a <= b, f"row {num!r}: inverted range {a}-{b}"
        if i + 1 < len(rows):
            assert b + 1 == rows[i + 1][2], (
                f"row {num!r}: end {b} should abut next start {rows[i + 1][2]}"
            )
        else:
            assert b == len(lines), f"last row {num!r}: end {b} != file length {len(lines)}"


def test_add_step_index_file_mode_doc_without_frontmatter(tmp_path: Path) -> None:
    """--file mode handles a markdown doc whose only top-matter is an H1."""
    from superagent.tools.add_step_index import process_one

    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent("""\
        # Big Doc

        ---

        ## 1. First Section

        Body of one.

        ## 2. Second Section

        Body of two.

        ## 3. Third Section

        Body of three.
    """) + "\n" * 100)
    ok, reason = process_one(
        doc, min_lines=10, numbered_only=False, require_frontmatter=False,
    )
    assert ok, f"process_one should succeed; got: {reason}"
    rendered = doc.read_text()
    assert "## Step index" in rendered
    assert "| 1 | First Section |" in rendered
    assert "| 2 | Second Section |" in rendered
    assert "| 3 | Third Section |" in rendered


def test_add_step_index_numbered_only_skips_unnumbered_h2(tmp_path: Path) -> None:
    """--numbered-only filters out H2s without numeric prefix (e.g. ToC)."""
    from superagent.tools.add_step_index import process_one

    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent("""\
        # Manual

        ## Table of Contents

        - one
        - two

        ---

        ## 1. Setup

        Foo.

        ## 2. Run

        Bar.
    """) + "\n" * 100)
    ok, _ = process_one(
        doc, min_lines=10, numbered_only=True, require_frontmatter=False,
    )
    assert ok
    rendered = doc.read_text()
    assert "Table of Contents" in rendered, "ToC content must remain"
    assert "## Step index" in rendered
    rows_block = rendered.split("## Step index", 1)[1].split("---", 1)[0]
    assert "Table of Contents" not in rows_block, (
        "Step index rows must not list scaffolding H2s"
    )
    assert "1. Setup" not in rows_block  # numeric prefix is stripped
    assert "| 1 | Setup |" in rendered
    assert "| 2 | Run |" in rendered


def test_add_step_index_idempotent(tmp_path: Path) -> None:
    """Running --file mode twice produces the same content."""
    from superagent.tools.add_step_index import process_one

    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent("""\
        # Doc

        ## 1. A

        Foo.

        ## 2. B

        Bar.
    """) + "\n" * 100)
    process_one(doc, min_lines=10, require_frontmatter=False)
    after_first = doc.read_text()
    process_one(doc, min_lines=10, require_frontmatter=False)
    after_second = doc.read_text()
    assert after_first == after_second, "second run must be a no-op"


# ---------------------------------------------------------------------------
# Line-number accuracy (the `Lines` column must address the file AS WRITTEN).
# ---------------------------------------------------------------------------


def test_step_index_lines_are_absolute_after_insertion(tmp_path: Path) -> None:
    """Each row's start is the 1-based file line of its H2 once the block is in place."""
    from superagent.tools.add_step_index import process_one

    skill = tmp_path / "fixture-skill.md"
    skill.write_text(SKILL_FIXTURE + "\n" * 100)
    ok, reason = process_one(skill, min_lines=10)
    assert ok, reason
    rendered = skill.read_text()
    _assert_rows_point_at_headings(rendered)
    rows = _index_rows(rendered)
    assert [r[0] for r in rows] == ["1", "2", "3", "4"]
    assert rows[3][1] == "Guardrails"  # unnumbered -> sequential number
    # Idempotency must survive the shift: the block height depends on the
    # row COUNT, not on digit width, so a second pass computes the same text.
    ok2, reason2 = process_one(skill, min_lines=10)
    assert (ok2, reason2) == (False, "no change")
    assert skill.read_text() == rendered


def test_step_index_lines_accurate_in_file_mode_without_intro(tmp_path: Path) -> None:
    """--file mode on a doc with no frontmatter and no H1 still lands on the H2s."""
    from superagent.tools.add_step_index import process_one

    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent("""\
        Preamble without any title.

        ## 1. Alpha

        a

        ## 2. Beta

        b
    """) + "\n" * 100)
    ok, reason = process_one(doc, min_lines=10, require_frontmatter=False)
    assert ok, reason
    _assert_rows_point_at_headings(doc.read_text())


def test_step_index_lines_accurate_when_refreshing_stale_block(tmp_path: Path) -> None:
    """Refreshing a file whose existing block carries wrong offsets yields correct ones."""
    from superagent.tools.add_step_index import (
        STEP_INDEX_BEGIN,
        STEP_INDEX_END,
        process_one,
    )

    stale_block = "\n".join([
        STEP_INDEX_BEGIN,
        "",
        "## Step index",
        "",
        "_Auto-generated by `tools/add_step_index.py`. Re-run on any edit._",
        "",
        "| # | Step | Lines |",
        "|---|------|-------|",
        "| 1 | Load configuration | 1-2 |",
        "| 2 | Branch on intent | 3-4 |",
        "",
        STEP_INDEX_END,
    ])
    fm, body = SKILL_FIXTURE.split("---\n\n", 1)
    skill = tmp_path / "fixture-skill.md"
    skill.write_text(fm + "---\n\n" + stale_block + "\n\n" + body + "\n" * 100)
    ok, reason = process_one(skill, min_lines=10)
    assert ok, reason
    rendered = skill.read_text()
    assert rendered.count(STEP_INDEX_BEGIN) == 1
    _assert_rows_point_at_headings(rendered)


def test_step_number_falls_back_to_sequence_for_odd_prefixes(tmp_path: Path) -> None:
    """`## 2½. Foo` has no leading integer -> numbered by position; `## 2a Bar` keeps `2a`."""
    from superagent.tools.add_step_index import collect_steps

    body = "## 1. One\n\nx\n\n## 2½. Odd\n\nx\n\n## 2a Sub\n\nx\n\n## Plain\n\nx\n"
    steps = collect_steps(body, body_offset=0)
    assert [(s.number, s.title) for s in steps] == [
        ("1", "One"), ("2", "2½. Odd"), ("2a", "Sub"), ("4", "Plain"),
    ]


def test_step_number_accepts_lettered_form_with_trailing_period(tmp_path: Path) -> None:
    """`## 3a.` / `## 3b.` index as `3a` / `3b` — no fallback, no duplicate numbers.

    Regression: the `Nb.` form (digit + letter + period) used by daily-update,
    doctor, weekly-review, and monthly-review sub-steps previously fell through
    to sequential numbering, which collided with the surrounding `N.` steps.
    """
    from superagent.tools.add_step_index import collect_steps, process_one

    body = (
        "## 3. Three\n\nx\n\n## 3a. Three-a\n\nx\n\n## 3b. Three-b\n\nx\n\n"
        "## 4. Four\n\nx\n"
    )
    steps = collect_steps(body, body_offset=0)
    assert [(s.number, s.title) for s in steps] == [
        ("3", "Three"), ("3a", "Three-a"), ("3b", "Three-b"), ("4", "Four"),
    ]
    # `--numbered-only` must treat the `Nb.` form as numbered too.
    assert [s.number for s in collect_steps(body, body_offset=0, numbered_only=True)] == [
        "3", "3a", "3b", "4",
    ]

    doc = tmp_path / "lettered.md"
    doc.write_text("# Lettered\n\nIntro.\n\n" + body, encoding="utf-8")
    changed, _reason = process_one(
        doc, min_lines=10, numbered_only=True, require_frontmatter=False,
    )
    assert changed is True
    rendered = doc.read_text(encoding="utf-8")
    numbers = [row[0] for row in _index_rows(rendered)]
    assert numbers == ["3", "3a", "3b", "4"]
    assert len(numbers) == len(set(numbers)), f"duplicate step numbers: {numbers}"
    _assert_rows_point_at_headings(rendered)


# ---------------------------------------------------------------------------
# Fenced-code masking.
# ---------------------------------------------------------------------------


def test_mask_fenced_code_preserves_line_count_and_blanks_fences() -> None:
    from superagent.tools.add_step_index import mask_fenced_code

    lines = [
        "## 1. Real",
        "```yaml",
        "## Not a step",
        "key: value",
        "```",
        "after",
        "~~~",
        "## Also not a step",
        "~~~~",  # longer closing run of the same char closes it
        "## 2. Real again",
        "````",
        "```",  # shorter run does NOT close a 4-backtick fence
        "## still fenced",
        "````",
        "tail",
    ]
    masked = mask_fenced_code(lines)
    assert len(masked) == len(lines)
    assert [ln for ln in masked if ln] == ["## 1. Real", "after", "## 2. Real again", "tail"]


def test_mask_fenced_code_unterminated_fence_runs_to_eof() -> None:
    from superagent.tools.add_step_index import mask_fenced_code

    lines = ["## 1. Real", "```", "## swallowed", "## also swallowed"]
    assert mask_fenced_code(lines) == ["## 1. Real", "", "", ""]


def test_mask_fenced_code_ignores_inline_backticks_and_deep_indent() -> None:
    """Inline code with a backtick after ``` is not a fence; 4+ spaces is a code block, not a fence."""
    from superagent.tools.add_step_index import mask_fenced_code

    lines = [
        "Use ```foo``` inline",
        "## 1. Real",
        "    ```",
        "## 2. Also real",
    ]
    assert mask_fenced_code(lines) == lines


def test_fenced_h2_is_not_indexed_and_preceding_step_spans_fence(tmp_path: Path) -> None:
    """H2s inside ``` / ```yaml / ~~~ fences are not steps; numbering stays contiguous."""
    from superagent.tools.add_step_index import process_one

    skill = tmp_path / "fixture-skill.md"
    skill.write_text(textwrap.dedent("""\
        ---
        name: fixture-skill
        description: Fenced template fixture.
        triggers:
          - fixture
        ---

        ## 1. Render the report

        Use this template:

        ```
        # Report

        ## Summary

        ## Details
        ```

        Then also this one:

        ```yaml
        ## Not a heading either
        key: value
        ```

        ~~~markdown
        ## Tilde-fenced heading
        ~~~

        ## 2. Deliver

        Send it.
    """) + "\n" * 100)
    ok, reason = process_one(skill, min_lines=10)
    assert ok, reason
    rendered = skill.read_text()
    rows = _index_rows(rendered)
    assert [(r[0], r[1]) for r in rows] == [("1", "Render the report"), ("2", "Deliver")]
    for bogus in ("Summary", "Details", "Not a heading either", "Tilde-fenced heading"):
        assert all(bogus != r[1] for r in rows), f"fenced heading {bogus!r} was indexed"
    _assert_rows_point_at_headings(rendered)
    # Step 1's range must run through the fences up to the line before `## 2.`
    lines = rendered.splitlines()
    a, b = rows[0][2], rows[0][3]
    assert "~~~" in lines[a - 1:b], "fence content must be inside step 1's range"
    assert lines[b] == "## 2. Deliver"
    # Idempotent with fences present.
    assert process_one(skill, min_lines=10) == (False, "no change")


def test_unterminated_fence_hides_following_h2s(tmp_path: Path) -> None:
    from superagent.tools.add_step_index import process_one

    doc = tmp_path / "doc.md"
    doc.write_text(textwrap.dedent("""\
        # Doc

        ## 1. Only real step

        ```
        ## Swallowed by the open fence
    """) + "\n" * 100)
    ok, reason = process_one(doc, min_lines=10, require_frontmatter=False)
    assert ok, reason
    assert [r[1] for r in _index_rows(doc.read_text())] == ["Only real step"]


# ---------------------------------------------------------------------------
# --check: read-only, reports only real deltas.
# ---------------------------------------------------------------------------


def _corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (framework_root, indexed_skill, short_skill) fixture paths."""
    skills = tmp_path / "skills"
    skills.mkdir()
    indexed = skills / "indexed.md"
    indexed.write_text(SKILL_FIXTURE + "\n" * 100)
    short = skills / "short.md"
    short.write_text(SKILL_FIXTURE)  # below the default min-lines
    return tmp_path, indexed, short


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def test_check_reports_zero_on_indexed_corpus_and_never_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from superagent.tools.add_step_index import main, process_one

    root, indexed, short = _corpus(tmp_path)
    assert process_one(indexed, min_lines=10)[0]
    capsys.readouterr()
    before = {p: _snapshot(p) for p in (indexed, short)}

    assert main(["--framework", str(root), "--check"]) == 0
    out = capsys.readouterr().out
    assert "would touch" not in out
    assert "skipped      indexed.md (no change)" in out
    assert "skipped      short.md (below min-lines)" in out
    assert "0 file(s) would be updated." in out
    assert {p: _snapshot(p) for p in (indexed, short)} == before


def test_check_reports_one_on_stale_corpus_and_never_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from superagent.tools.add_step_index import main

    root, indexed, short = _corpus(tmp_path)
    before = {p: _snapshot(p) for p in (indexed, short)}

    assert main(["--framework", str(root), "--check"]) == 0
    out = capsys.readouterr().out
    assert "would touch  indexed.md (4 steps would be indexed)" in out
    assert "would touch  short.md" not in out
    assert "1 file(s) would be updated." in out
    assert {p: _snapshot(p) for p in (indexed, short)} == before, "--check must not write"

    # The real run then changes exactly that file, and a follow-up --check is clean.
    assert main(["--framework", str(root)]) == 0
    assert "1 file(s) updated." in capsys.readouterr().out
    assert main(["--framework", str(root), "--check"]) == 0
    assert "0 file(s) would be updated." in capsys.readouterr().out


def test_check_file_mode_mirrors_real_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """--file --check honors --numbered-only and the no-frontmatter path, and never writes."""
    from superagent.tools.add_step_index import main

    doc = tmp_path / "doc.md"
    doc.write_text("# Manual\n\n## Table of Contents\n\n- x\n\n## 1. Setup\n\nFoo.\n" + "\n" * 100)
    before = _snapshot(doc)

    assert main(["--file", str(doc), "--check", "--numbered-only"]) == 0
    assert "would touch  doc.md (1 steps would be indexed)" in capsys.readouterr().out
    assert _snapshot(doc) == before

    assert main(["--file", str(doc), "--numbered-only"]) == 0
    assert "updated      doc.md (1 steps indexed)" in capsys.readouterr().out
    assert main(["--file", str(doc), "--check", "--numbered-only"]) == 0
    assert "skipped      doc.md (no change)" in capsys.readouterr().out

    # A file with no H2 steps is reported as skipped, not "would touch".
    plain = tmp_path / "plain.md"
    plain.write_text("# Plain\n\nNo sections here.\n" + "\n" * 100)
    assert main(["--file", str(plain), "--check"]) == 0
    assert "skipped      plain.md (no H2 steps)" in capsys.readouterr().out
