# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/sources_normalize.py` (the liberal ref parser)."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_canonical_frontmatter_is_passthrough(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import is_canonical, propose

    p = tmp_path / "ok.ref.md"
    p.write_text(
        "---\n"
        "ref_version: 1\n"
        "title: Already canonical\n"
        "kind: url\n"
        "source: https://example.com\n"
        "---\n\nbody\n"
    )
    assert is_canonical(p.read_text())
    proposal = propose(p)
    assert proposal["already_canonical"] is True
    assert proposal["fields"]["title"] == "Already canonical"
    assert proposal["fields"]["kind"] == "url"


def test_bare_url_first_line_becomes_url_kind(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import propose

    p = tmp_path / "bare.ref.txt"
    p.write_text("https://401k.fidelity.com/dashboard\n")
    proposal = propose(p)
    assert proposal["already_canonical"] is False
    assert proposal["fields"]["kind"] == "url"
    assert proposal["fields"]["source"] == "https://401k.fidelity.com/dashboard"
    assert "401k.fidelity.com" in proposal["fields"]["title"]
    assert proposal["missing_required"] == []


def test_bare_path_becomes_file_kind(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import propose

    p = tmp_path / "bare.ref.txt"
    p.write_text("/Users/x/Documents/old-tax-returns/2020.pdf\n")
    proposal = propose(p)
    assert proposal["fields"]["kind"] == "file"
    assert proposal["fields"]["source"].endswith("2020.pdf")


def test_loose_key_value_form(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import propose

    p = tmp_path / "loose.ref.txt"
    p.write_text(
        "Title: Fidelity 401k portal\n"
        "Type: url\n"
        "URL: https://401k.fidelity.com/dashboard\n"
        "Sensitive: yes\n"
        "Tags: retirement, login-required\n"
        "Notes: SSO via work email; YubiKey for the 2FA prompt.\n"
    )
    proposal = propose(p)
    f = proposal["fields"]
    assert f["title"] == "Fidelity 401k portal"
    assert f["kind"] == "url"
    assert f["source"] == "https://401k.fidelity.com/dashboard"
    assert f["sensitive"] is True
    assert "retirement" in f["tags"] and "login-required" in f["tags"]
    assert "SSO via work email" in f["_notes"]
    assert proposal["missing_required"] == []


def test_cmd_alias_implies_cli_kind(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import propose

    p = tmp_path / "loose.ref.txt"
    p.write_text("Title: Reminders\nCmd: rem list --json\n")
    f = propose(p)["fields"]
    assert f["kind"] == "cli"
    assert f["source"] == "rem list --json"


def test_vault_uri_becomes_vault_kind(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import propose

    p = tmp_path / "v.ref.txt"
    p.write_text("1Password://Personal/Tax-PIN-2024\n")
    f = propose(p)["fields"]
    assert f["kind"] == "vault"
    assert f["source"] == "1Password://Personal/Tax-PIN-2024"


def test_to_canonical_round_trip(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import (
        is_canonical,
        propose,
        to_canonical,
    )

    p = tmp_path / "loose.ref.txt"
    p.write_text("URL: https://example.com\nTitle: Example\n")
    proposal = propose(p)
    canonical = to_canonical(proposal["fields"])
    assert is_canonical(canonical)
    fm = yaml.safe_load(canonical.split("---")[1])
    assert fm["kind"] == "url"
    assert fm["source"] == "https://example.com"
    assert fm["title"] == "Example"


def test_apply_rewrite_keeps_original_backup(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode, is_canonical

    p = tmp_path / "loose.ref.txt"
    original = "URL: https://example.com\nTitle: Example\n"
    p.write_text(original)
    result = apply_mode(p, "rewrite")
    assert result["action"] == "rewrote"
    assert is_canonical(p.read_text())
    backup = p.with_name(p.name + ".original")
    assert backup.read_text() == original


def test_apply_rewrite_no_backup_skips_backup(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode

    p = tmp_path / "loose.ref.txt"
    p.write_text("URL: https://example.com\n")
    result = apply_mode(p, "rewrite_no_backup")
    assert result["action"] == "rewrote"
    assert not p.with_name(p.name + ".original").exists()


def test_apply_sibling_leaves_original_untouched(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode

    p = tmp_path / "loose.ref.txt"
    original = "URL: https://example.com\n"
    p.write_text(original)
    result = apply_mode(p, "sibling")
    assert result["action"] == "wrote_sibling"
    sibling = Path(result["wrote_to"])
    assert sibling.exists() and sibling != p
    assert p.read_text() == original


def test_apply_keep_writes_nothing(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode

    p = tmp_path / "loose.ref.txt"
    original = "URL: https://example.com\n"
    p.write_text(original)
    result = apply_mode(p, "keep")
    assert result["action"] == "kept"
    assert p.read_text() == original


def test_apply_blocks_when_required_fields_missing(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode

    p = tmp_path / "loose.ref.txt"
    p.write_text("Title: Just a title\nNotes: I forgot the source.\n")
    result = apply_mode(p, "rewrite")
    assert result["action"] == "blocked"
    assert "kind" in result["reason"] or "source" in result["reason"]


def test_already_canonical_apply_is_noop(tmp_path: Path) -> None:
    from superagent.tools.sources_normalize import apply_mode

    p = tmp_path / "ok.ref.md"
    p.write_text(
        "---\nref_version: 1\nkind: url\nsource: https://example.com\n"
        "title: ok\n---\n\nbody\n"
    )
    original = p.read_text()
    for mode in ("rewrite", "rewrite_no_backup", "sibling", "keep"):
        result = apply_mode(p, mode)
        assert result["action"] == "noop"
        assert p.read_text() == original
