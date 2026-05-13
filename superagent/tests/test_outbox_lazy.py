"""Tests for `tools/outbox.py` (lazy Outbox sub-directory materialization).

Implements `contracts/outbox-lifecycle.md` § "Lazy sub-directory creation".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from superagent.tools.outbox import (
    KNOWN_STAGES,
    ensure,
    list_status,
    purge_empty,
)


def test_init_leaves_outbox_flat(initialized_workspace: Path) -> None:
    """Sanity-check the lazy contract from the perspective of `tools/outbox`."""
    outbox = initialized_workspace / "Outbox"
    children = sorted(p.name for p in outbox.iterdir())
    assert children == ["README.md"]


def test_known_stages_documented() -> None:
    assert "drafts" in KNOWN_STAGES
    assert "staging" in KNOWN_STAGES
    assert "sent" in KNOWN_STAGES
    assert "sealed" in KNOWN_STAGES
    assert len(KNOWN_STAGES) == 4


def test_ensure_creates_top_level_subdir(initialized_workspace: Path) -> None:
    created = ensure(initialized_workspace, "drafts")
    assert created is True
    assert (initialized_workspace / "Outbox" / "drafts").is_dir()


def test_ensure_creates_nested_subdir(initialized_workspace: Path) -> None:
    created = ensure(initialized_workspace, "drafts", "emails")
    assert created is True
    assert (initialized_workspace / "Outbox" / "drafts" / "emails").is_dir()


def test_ensure_idempotent(initialized_workspace: Path) -> None:
    assert ensure(initialized_workspace, "handoff") is True
    assert ensure(initialized_workspace, "handoff") is False


def test_ensure_does_not_clobber_existing_files(initialized_workspace: Path) -> None:
    ensure(initialized_workspace, "drafts")
    f = initialized_workspace / "Outbox" / "drafts" / "user-file.md"
    f.write_text("user content")
    ensure(initialized_workspace, "drafts")
    assert f.read_text() == "user content"


def test_ensure_rejects_absolute_path(initialized_workspace: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        ensure(initialized_workspace, "/etc")


def test_ensure_rejects_dotdot_traversal(initialized_workspace: Path) -> None:
    with pytest.raises(ValueError, match=r"\.\."):
        ensure(initialized_workspace, "..", "secrets")


def test_ensure_rejects_empty_args(initialized_workspace: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        ensure(initialized_workspace)


def test_list_status_empty_when_flat(initialized_workspace: Path) -> None:
    assert list_status(initialized_workspace) == []


def test_list_status_walks_recursively(initialized_workspace: Path) -> None:
    ensure(initialized_workspace, "drafts")
    ensure(initialized_workspace, "drafts", "emails")
    ensure(initialized_workspace, "handoff")
    (initialized_workspace / "Outbox" / "drafts" / "emails" / "x.md").write_text("hi")
    rows = list_status(initialized_workspace)
    paths = sorted(r["path"] for r in rows)
    assert paths == ["drafts", "drafts/emails", "handoff"]
    by_path = {r["path"]: r for r in rows}
    assert by_path["drafts/emails"]["file_count"] == 1
    assert by_path["handoff"]["file_count"] == 0
    assert by_path["drafts"]["is_known_stage"] is True
    assert by_path["handoff"]["is_known_stage"] is False


def test_purge_empty_removes_only_empty_subdirs(initialized_workspace: Path) -> None:
    ensure(initialized_workspace, "drafts")
    ensure(initialized_workspace, "staging")
    ensure(initialized_workspace, "handoff")
    # Put a real file in drafts so it stays
    (initialized_workspace / "Outbox" / "drafts" / "proposal.md").write_text("body")
    deleted, kept = purge_empty(initialized_workspace)
    assert "Outbox/staging/" in deleted
    assert "Outbox/handoff/" in deleted
    assert "Outbox/drafts/" in kept
    assert (initialized_workspace / "Outbox" / "drafts").is_dir()
    assert not (initialized_workspace / "Outbox" / "staging").exists()
    assert not (initialized_workspace / "Outbox" / "handoff").exists()


def test_purge_empty_dry_run_deletes_nothing(initialized_workspace: Path) -> None:
    ensure(initialized_workspace, "staging")
    deleted, _ = purge_empty(initialized_workspace, dry_run=True)
    assert "Outbox/staging/" in deleted
    assert (initialized_workspace / "Outbox" / "staging").is_dir(), (
        "dry_run must not actually delete folders"
    )


def test_purge_empty_recurses_bottom_up(initialized_workspace: Path) -> None:
    """An empty parent containing only empty children should be removed."""
    ensure(initialized_workspace, "drafts", "emails", "alice")
    deleted, kept = purge_empty(initialized_workspace)
    assert "Outbox/drafts/emails/alice/" in deleted
    assert "Outbox/drafts/emails/" in deleted
    assert "Outbox/drafts/" in deleted
    assert kept == []


def test_purge_empty_preserves_outbox_root_and_readme(
    initialized_workspace: Path,
) -> None:
    purge_empty(initialized_workspace)
    assert (initialized_workspace / "Outbox").is_dir()
    assert (initialized_workspace / "Outbox" / "README.md").is_file()


def test_purge_empty_keeps_subdir_with_only_subsub_with_file(
    initialized_workspace: Path,
) -> None:
    ensure(initialized_workspace, "drafts", "emails")
    (initialized_workspace / "Outbox" / "drafts" / "emails" / "msg.md").write_text("x")
    deleted, kept = purge_empty(initialized_workspace)
    assert deleted == []
    assert "Outbox/drafts/" in kept
    assert "Outbox/drafts/emails/" in kept
