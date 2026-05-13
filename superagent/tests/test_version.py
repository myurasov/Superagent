# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/version.py` (versioning + migration-chain resolution)."""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# parse / compare / bump_kind
# ---------------------------------------------------------------------------


def test_parse_basic() -> None:
    from superagent.tools.version import Version, parse

    v = parse("1.2.3")
    assert v == Version(1, 2, 3)
    assert str(v) == "1.2.3"


def test_parse_idempotent_on_version_instance() -> None:
    from superagent.tools.version import parse

    v = parse("0.2.0")
    assert parse(v) is v


@pytest.mark.parametrize(
    "bad",
    ["", "1", "1.2", "1.2.3.4", "v1.2.3", "1.2.3-rc.1", "1.0.0+build", "abc", "1.x.0"],
)
def test_parse_rejects_invalid(bad: str) -> None:
    from superagent.tools.version import parse

    with pytest.raises((ValueError, TypeError)):
        parse(bad)


def test_compare_orderings() -> None:
    from superagent.tools.version import compare

    assert compare("0.1.0", "0.2.0") == -1
    assert compare("0.2.0", "0.2.0") == 0
    assert compare("1.0.0", "0.9.9") == 1
    assert compare("0.2.10", "0.2.2") == 1


def test_version_dataclass_orders() -> None:
    from superagent.tools.version import Version

    assert sorted([Version(0, 2, 0), Version(0, 1, 5), Version(1, 0, 0)]) == [
        Version(0, 1, 5),
        Version(0, 2, 0),
        Version(1, 0, 0),
    ]


def test_bump_kind_classification() -> None:
    from superagent.tools.version import bump_kind

    assert bump_kind("0.1.0", "0.1.0") == "same"
    assert bump_kind("0.1.0", "0.1.1") == "patch"
    assert bump_kind("0.1.0", "0.2.0") == "minor"
    assert bump_kind("0.1.0", "0.5.7") == "minor"
    assert bump_kind("0.9.9", "1.0.0") == "major"


def test_bump_kind_rejects_downgrade() -> None:
    from superagent.tools.version import bump_kind

    with pytest.raises(ValueError, match="downgrade"):
        bump_kind("0.2.0", "0.1.0")


# ---------------------------------------------------------------------------
# current_version (pyproject.toml)
# ---------------------------------------------------------------------------


def test_current_version_reads_real_pyproject() -> None:
    from superagent.tools.version import current_version, parse

    v = current_version()
    parse(v)


def test_current_version_custom_path(tmp_path: Path) -> None:
    from superagent.tools.version import current_version

    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nname = "x"\nversion = "3.4.5"\n', encoding="utf-8")
    assert current_version(pp) == "3.4.5"


def test_current_version_missing_file(tmp_path: Path) -> None:
    from superagent.tools.version import current_version

    with pytest.raises(FileNotFoundError):
        current_version(tmp_path / "missing.toml")


# ---------------------------------------------------------------------------
# workspace_version + set_workspace_version
# ---------------------------------------------------------------------------


def test_workspace_version_missing_returns_legacy(tmp_path: Path) -> None:
    from superagent.tools.version import LEGACY_DEFAULT, workspace_version

    assert workspace_version(tmp_path) == LEGACY_DEFAULT


def test_workspace_version_round_trip(tmp_path: Path) -> None:
    from superagent.tools.version import set_workspace_version, workspace_version

    set_workspace_version(tmp_path, "0.7.3")
    assert workspace_version(tmp_path) == "0.7.3"
    assert (tmp_path / ".version").read_text(encoding="utf-8") == "0.7.3\n"


def test_workspace_version_strips_whitespace(tmp_path: Path) -> None:
    from superagent.tools.version import workspace_version

    (tmp_path / ".version").write_text("  0.4.2  \n\n", encoding="utf-8")
    assert workspace_version(tmp_path) == "0.4.2"


def test_workspace_version_empty_file_is_legacy(tmp_path: Path) -> None:
    from superagent.tools.version import LEGACY_DEFAULT, workspace_version

    (tmp_path / ".version").write_text("", encoding="utf-8")
    assert workspace_version(tmp_path) == LEGACY_DEFAULT


def test_set_workspace_version_validates(tmp_path: Path) -> None:
    from superagent.tools.version import set_workspace_version

    with pytest.raises(ValueError):
        set_workspace_version(tmp_path, "not-a-version")


def test_set_workspace_version_creates_dir(tmp_path: Path) -> None:
    from superagent.tools.version import set_workspace_version, workspace_version

    nested = tmp_path / "deep" / "workspace"
    set_workspace_version(nested, "1.0.0")
    assert workspace_version(nested) == "1.0.0"


# ---------------------------------------------------------------------------
# Manifest IO + chain resolution
# ---------------------------------------------------------------------------


def _write_migration(
    md_dir: Path,
    to_v: str,
    from_v: str,
    breaking: bool = False,
    revertible: bool = True,
) -> Path:
    body = f"""---
to_version: {to_v}
from_version: {from_v}
title: "Test migration {from_v} -> {to_v}"
breaking: {str(breaking).lower()}
revertible: {str(revertible).lower()}
estimated_duration: "<1 minute"
touches:
  - workspace/.version
helper_scripts: {{}}
---

## Summary

Test migration body.

## Pre-flight checks
- [ ] noop

## Migrate
1. noop

## Validate
- [ ] noop

## Revert
1. noop
"""
    path = md_dir / f"{to_v}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_refresh_manifest_builds_from_md(tmp_path: Path) -> None:
    from superagent.tools.version import _load_manifest, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    _write_migration(tmp_path, "0.3.0", "0.2.0", breaking=True)
    n = refresh_manifest(tmp_path)
    assert n == 2

    entries = _load_manifest(tmp_path / "_manifest.yaml")
    assert [e.to_version for e in entries] == ["0.2.0", "0.3.0"]
    assert entries[1].breaking is True
    assert entries[0].breaking is False


def test_refresh_manifest_skips_template(tmp_path: Path) -> None:
    from superagent.tools.version import refresh_manifest

    (tmp_path / "_template.md").write_text(
        '---\nto_version: 0.0.0\nfrom_version: 0.0.0\ntitle: "x"\n'
        'breaking: false\nrevertible: true\nestimated_duration: ""\n'
        'touches: []\nhelper_scripts: {}\n---\n',
        encoding="utf-8",
    )
    _write_migration(tmp_path, "0.2.0", "0.1.0")
    n = refresh_manifest(tmp_path)
    assert n == 1


def test_find_chain_simple(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    _write_migration(tmp_path, "0.3.0", "0.2.0")
    refresh_manifest(tmp_path)

    chain = find_chain("0.1.0", "0.3.0", tmp_path / "_manifest.yaml")
    assert [e.to_version for e in chain] == ["0.2.0", "0.3.0"]


def test_find_chain_partial(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    _write_migration(tmp_path, "0.3.0", "0.2.0")
    _write_migration(tmp_path, "0.4.0", "0.3.0")
    refresh_manifest(tmp_path)

    chain = find_chain("0.2.0", "0.3.0", tmp_path / "_manifest.yaml")
    assert [e.to_version for e in chain] == ["0.3.0"]


def test_find_chain_same_version_returns_empty(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain

    assert find_chain("0.2.0", "0.2.0", tmp_path / "_manifest.yaml") == []


def test_find_chain_patch_only_returns_empty(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    refresh_manifest(tmp_path)

    chain = find_chain("0.2.0", "0.2.7", tmp_path / "_manifest.yaml")
    assert chain == []


def test_find_chain_rejects_downgrade(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain

    with pytest.raises(ValueError, match="downgrade"):
        find_chain("0.5.0", "0.2.0", tmp_path / "_manifest.yaml")


def test_find_chain_detects_broken_chain(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    _write_migration(tmp_path, "0.4.0", "0.3.0")
    refresh_manifest(tmp_path)

    with pytest.raises(ValueError, match="chain broken"):
        find_chain("0.1.0", "0.4.0", tmp_path / "_manifest.yaml")


def test_find_chain_missing_target(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain, refresh_manifest

    _write_migration(tmp_path, "0.2.0", "0.1.0")
    refresh_manifest(tmp_path)

    with pytest.raises(ValueError):
        find_chain("0.1.0", "0.5.0", tmp_path / "_manifest.yaml")


def test_find_chain_no_manifest_file(tmp_path: Path) -> None:
    from superagent.tools.version import find_chain

    with pytest.raises(ValueError, match="no migrations"):
        find_chain("0.1.0", "0.5.0", tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# Real manifest sanity check (the one we ship)
# ---------------------------------------------------------------------------


def test_shipped_manifest_loads() -> None:
    from superagent.tools.version import _load_manifest

    entries = _load_manifest()
    assert len(entries) >= 1
    first = entries[0]
    assert first.to_version == "0.2.0"
    assert first.from_version == "0.1.0"


def test_shipped_chain_from_legacy_to_current() -> None:
    from superagent.tools.version import current_version, find_chain

    cur = current_version()
    chain = find_chain("0.1.0", cur)
    if cur == "0.1.0":
        assert chain == []
    else:
        assert len(chain) >= 1
        assert chain[0].from_version == "0.1.0"


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_cli_current(capsys: pytest.CaptureFixture[str]) -> None:
    from superagent.tools.version import main

    rc = main(["current"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    from superagent.tools.version import parse

    parse(out)


def test_cli_workspace_default_legacy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from superagent.tools.version import LEGACY_DEFAULT, main

    rc = main(["workspace", "--workspace", str(tmp_path)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == LEGACY_DEFAULT


def test_cli_set_then_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from superagent.tools.version import main

    main(["set", "--workspace", str(tmp_path), "0.4.2"])
    capsys.readouterr()
    main(["workspace", "--workspace", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "0.4.2"


def test_cli_check_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from superagent.tools.version import current_version, main

    cur = current_version()
    (tmp_path / ".version").write_text(f"{cur}\n", encoding="utf-8")
    rc = main(["check", "--workspace", str(tmp_path)])
    assert rc == 0
    assert "up-to-date" in capsys.readouterr().out


def test_cli_check_downgrade_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from superagent.tools.version import main

    (tmp_path / ".version").write_text("99.0.0\n", encoding="utf-8")
    rc = main(["check", "--workspace", str(tmp_path)])
    assert rc == 2
    assert "downgrade" in capsys.readouterr().err
