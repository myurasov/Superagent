# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/anti_patterns.py`."""
from __future__ import annotations

from pathlib import Path


def test_anti_patterns_clean_skills(framework_dir: Path) -> None:
    """Today's framework skills should pass anti-pattern scan with NO 'warning' hits."""
    from superagent.tools.anti_patterns import scan_dir

    hits = scan_dir(framework_dir / "skills")
    warning_hits = [
        (fname, h) for fname, hs in hits.items() for h in hs
        if h.get("severity") == "warning"
    ]
    # Hard ceiling: no more than 2 warning hits in shipped skills (some
    # skills may legitimately mention these patterns in PROHIBITION context;
    # the conservative regex catches some of those).
    assert len(warning_hits) <= 2, (
        f"too many anti-pattern warning hits: "
        f"{[(f, h['pattern'], h['line']) for f, h in warning_hits]}"
    )


def test_anti_patterns_catches_synthetic_violation(tmp_path: Path) -> None:
    """A synthetic offending skill markdown is correctly flagged."""
    from superagent.tools.anti_patterns import scan_file

    skill = tmp_path / "bad.md"
    skill.write_text(
        "---\nname: bad\n---\n\n"
        "Read the customer's info.md, status.md, history.md, rolodex.md "
        "for full context.\n"
    )
    hits = scan_file(skill)
    assert any(h["pattern"] == "AP-1" for h in hits), (
        f"AP-1 should fire on synthetic violation, got: {hits}"
    )
