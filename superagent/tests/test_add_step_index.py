# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/add_step_index.py` --file mode and --numbered-only filter."""
from __future__ import annotations

import textwrap
from pathlib import Path


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
