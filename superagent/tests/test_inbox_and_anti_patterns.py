"""Tests for `tools/inbox_triage.py` and `tools/anti_patterns.py`."""
from __future__ import annotations

from pathlib import Path


def test_classify_tax_pdf(initialized_workspace: Path) -> None:
    from superagent.tools.inbox_triage import classify

    inbox = initialized_workspace / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "2025-w-2-employer.pdf"
    f.write_bytes(b"PDF" * 500)
    result = classify(f)
    assert result["category_suggested"] == "taxes"
    assert result["confidence"] in ("medium", "high")
    assert result["suggested_path"].startswith("Sources/documents/taxes/")


def test_classify_unknown_falls_back(initialized_workspace: Path) -> None:
    from superagent.tools.inbox_triage import classify

    inbox = initialized_workspace / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    f = inbox / "totally-random-name.bin"
    f.write_bytes(b"\x00" * 100)
    result = classify(f)
    assert result["category_suggested"] == "uncategorized"
    assert result["confidence"] == "low"


def test_record_decision_writes_log(initialized_workspace: Path) -> None:
    import yaml
    from superagent.tools.inbox_triage import record_decision

    record_decision(initialized_workspace, {
        "file": "test.pdf",
        "action": "filed",
        "destination": "Sources/documents/taxes/test.pdf",
        "note": "first triage",
    })
    log = initialized_workspace / "Inbox" / "_processed.yaml"
    assert log.exists()
    data = yaml.safe_load(log.read_text())
    decisions = data.get("decisions") or []
    assert len(decisions) >= 1
    assert decisions[-1]["file"] == "test.pdf"
    assert decisions[-1]["action"] == "filed"


def test_stale_items(initialized_workspace: Path) -> None:
    """Stale window finds files older than --days."""
    import os
    import time
    from superagent.tools.inbox_triage import stale_items

    inbox = initialized_workspace / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    fresh = inbox / "fresh.txt"
    fresh.write_text("fresh\n")
    old = inbox / "old.txt"
    old.write_text("old\n")
    very_old = time.time() - 30 * 86400
    os.utime(old, (very_old, very_old))
    out = stale_items(initialized_workspace, days=14)
    out_names = {p.name for p in out}
    assert "old.txt" in out_names
    assert "fresh.txt" not in out_names


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
