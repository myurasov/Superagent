"""Tests for `tools/domains.py` (lazy folder materialization + purge).

Implements `contracts/domains-and-assets.md` § 6.4a.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from superagent.tools.domains import (
    ensure_folder,
    is_bare_template,
    list_status,
    purge_empty,
)

DOMAIN_FILES = ("info.md", "status.md", "history.md", "rolodex.md", "sources.md")


def test_init_leaves_no_per_domain_folders(initialized_workspace: Path) -> None:
    """Sanity-check the lazy contract from the perspective of `tools/domains`.

    After init, `Domains/` exists with README only — no per-domain folders.
    """
    domains_dir = initialized_workspace / "Domains"
    assert domains_dir.is_dir()
    children = sorted(p.name for p in domains_dir.iterdir())
    assert children == ["README.md"], f"expected only README.md, got {children}"


def test_ensure_folder_materializes_with_full_scaffold(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    created = ensure_folder(initialized_workspace, framework_dir, "health")
    assert created is True
    folder = initialized_workspace / "Domains" / "Health"
    assert folder.is_dir()
    for fname in DOMAIN_FILES:
        path = folder / fname
        assert path.is_file(), f"missing {fname}"
        body = path.read_text()
        assert "{{DOMAIN_NAME}}" not in body, (
            f"{fname} still contains unsubstituted placeholder"
        )
        assert "Health" in body, f"{fname} should mention the domain name"


def test_ensure_folder_idempotent(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    first = ensure_folder(initialized_workspace, framework_dir, "pets")
    second = ensure_folder(initialized_workspace, framework_dir, "pets")
    assert first is True
    assert second is False


def test_ensure_folder_does_not_clobber_user_edits(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "vehicles")
    history = initialized_workspace / "Domains" / "Vehicles" / "history.md"
    user_marker = "\n#### 2026-05-12 — User-added entry\n\nReal content.\n"
    history.write_text(history.read_text() + user_marker)
    ensure_folder(initialized_workspace, framework_dir, "vehicles")
    assert user_marker in history.read_text()


def test_ensure_folder_unknown_domain_raises(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    with pytest.raises(ValueError, match="not registered"):
        ensure_folder(initialized_workspace, framework_dir, "no-such-domain-xyz")


def test_list_status_marks_registered_vs_materialized(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "self")
    rows = {r["id"]: r for r in list_status(initialized_workspace)}
    assert rows["self"]["materialized"] is True
    assert rows["health"]["materialized"] is False
    assert rows["business"]["materialized"] is False
    assert rows["education"]["materialized"] is False
    assert all(r["registered"] for r in rows.values())
    assert len(rows) == 13


def test_purge_empty_removes_only_bare_templates(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "hobbies")
    ensure_folder(initialized_workspace, framework_dir, "travel")

    history = initialized_workspace / "Domains" / "Travel" / "history.md"
    history.write_text(
        history.read_text()
        + "\n#### 2026-05-12 — Lisbon trip planning kickoff\n\nNotes.\n"
    )

    deleted, kept = purge_empty(initialized_workspace, framework_dir)
    assert "Hobbies" in deleted, "bare-template default must be deleted"
    assert "Travel" in kept, "user-edited default must be kept"
    assert not (initialized_workspace / "Domains" / "Hobbies").exists()
    assert (initialized_workspace / "Domains" / "Travel").is_dir()


def test_purge_empty_dry_run_deletes_nothing(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "career")
    deleted, _ = purge_empty(initialized_workspace, framework_dir, dry_run=True)
    assert "Career" in deleted
    assert (initialized_workspace / "Domains" / "Career").is_dir(), (
        "dry_run must not actually delete folders"
    )


def test_is_bare_template_distinguishes_user_content(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "family")
    folder = initialized_workspace / "Domains" / "Family"
    assert is_bare_template(folder, framework_dir) is True

    info = folder / "info.md"
    info.write_text(info.read_text().replace(
        "## Open Questions",
        "## Open Questions\n\n- Should we move closer to grandma?\n",
    ))
    assert is_bare_template(folder, framework_dir) is False


def test_is_bare_template_resources_marks_user_content(
    framework_dir: Path,
    initialized_workspace: Path,
) -> None:
    ensure_folder(initialized_workspace, framework_dir, "home")
    folder = initialized_workspace / "Domains" / "Home"
    assert is_bare_template(folder, framework_dir) is True

    res = folder / "Resources"
    res.mkdir(parents=True, exist_ok=True)
    (res / "scratch.md").write_text("user file")
    assert is_bare_template(folder, framework_dir) is False
