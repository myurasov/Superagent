# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/sources_index.py` (the derived-index tool).

Exercises the contract documented in `contracts/sources.md` § 15.6:
  - Filesystem under `Sources/` is canonical; the index is derived.
  - Refresh is mtime-lazy.
  - Hand-curated fields (notes, tags, related_*, last_accessed, read_count,
    sensitive) are preserved across refreshes.
  - Sidecar `.ref.md` next to a non-ref document is metadata for the document
    (not a separate row).
  - Removed files survive one refresh cycle as `present: false` before being
    dropped.
  - The cache subtree (`Sources/_cache/`) is excluded from the index.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _bump_mtime(p: Path, seconds_into_future: float = 2.0) -> None:
    """Force a file's mtime forward so the lazy refresher detects the change."""
    new_time = time.time() + seconds_into_future
    os.utime(p, (new_time, new_time))


def test_refresh_picks_up_new_document(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import refresh

    _write(initialized_workspace / "Sources" / "vehicles" / "camry-title.pdf",
           "%PDF-1.4 fake\n")
    index = refresh(initialized_workspace)
    rows = index["sources"]
    assert any(r["path"] == "Sources/vehicles/camry-title.pdf" for r in rows)
    row = next(r for r in rows if r["path"] == "Sources/vehicles/camry-title.pdf")
    assert row["kind"] == "document"
    assert row["category"] == "vehicles"
    assert row["title"]


def test_refresh_picks_up_standalone_reference(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import refresh

    ref = initialized_workspace / "Sources" / "finance" / "fidelity.ref.md"
    ref_body = (
        "---\n"
        "ref_version: 1\n"
        "title: Fidelity 401k portal\n"
        "kind: url\n"
        "source: \"https://401k.fidelity.com/dashboard\"\n"
        "sensitive: true\n"
        "tags: [retirement, login-required]\n"
        "---\n\n"
        "Notes: SSO via work email.\n"
    )
    _write(ref, ref_body)
    index = refresh(initialized_workspace)
    rows = index["sources"]
    row = next(r for r in rows if r["path"].endswith("fidelity.ref.md"))
    assert row["kind"] == "reference"
    assert row["title"] == "Fidelity 401k portal"
    assert row["sensitive"] is True
    assert "retirement" in row["tags"]
    assert row["normalized"] is True


def test_refresh_recognizes_ref_txt_extension(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import refresh

    ref = initialized_workspace / "Sources" / "misc" / "freeform.ref.txt"
    _write(ref, "URL: https://example.com\nTitle: Example\n")
    index = refresh(initialized_workspace)
    rows = index["sources"]
    row = next(r for r in rows if r["path"].endswith("freeform.ref.txt"))
    assert row["kind"] == "reference"
    assert row["normalized"] is False, "txt freeform should not be auto-normalized on refresh"


def test_sidecar_ref_does_not_create_separate_row(initialized_workspace: Path) -> None:
    """A `.ref.md` sibling of a document is metadata for the document, not a row of its own."""
    from superagent.tools.sources_index import refresh

    doc = initialized_workspace / "Sources" / "vehicles" / "title.pdf"
    sidecar = initialized_workspace / "Sources" / "vehicles" / "title.pdf.ref.md"
    _write(doc, "%PDF-1.4 stub\n")
    _write(sidecar,
           "---\n"
           "ref_version: 1\n"
           "title: Camry title 2018\n"
           "kind: file\n"
           "source: \"Sources/vehicles/title.pdf\"\n"
           "tags: [titles, camry]\n"
           "related_asset: asset-camry-2018\n"
           "---\n\nThe physical original is in the file cabinet.\n")
    index = refresh(initialized_workspace)
    rows = [r for r in index["sources"] if r["path"].startswith("Sources/vehicles/")]
    paths = sorted(r["path"] for r in rows)
    assert paths == ["Sources/vehicles/title.pdf"], (
        f"expected only the document row, got {paths}"
    )
    row = rows[0]
    assert row["kind"] == "document"
    assert row["title"] == "Camry title 2018"
    assert row["related_asset"] == "asset-camry-2018"
    assert "titles" in row["tags"]


def test_refresh_is_mtime_lazy(initialized_workspace: Path) -> None:
    """A second refresh with no filesystem changes must NOT rewrite the index."""
    from superagent.tools.sources_index import index_path, refresh

    _write(initialized_workspace / "Sources" / "x.pdf", "x")
    refresh(initialized_workspace)
    first_mtime = index_path(initialized_workspace).stat().st_mtime
    time.sleep(0.05)
    refresh(initialized_workspace)
    second_mtime = index_path(initialized_workspace).stat().st_mtime
    assert first_mtime == second_mtime, "lazy refresh should be a no-op when nothing changed"


def test_preserve_user_curated_fields(initialized_workspace: Path) -> None:
    """Hand-curated notes / tags / cross-refs survive a refresh."""
    from superagent.tools.sources_index import (
        get_by_path,
        refresh,
        update_row,
    )

    doc = initialized_workspace / "Sources" / "taxes" / "2024-return.pdf"
    _write(doc, "%PDF-1.4 fake\n")
    refresh(initialized_workspace)
    row = get_by_path(initialized_workspace, "Sources/taxes/2024-return.pdf")
    assert row is not None
    update_row(initialized_workspace, row["id"], {
        "notes": "Filed on April 14; accepted on April 15.",
        "tags": ["tax-2024", "filed"],
        "related_project": "tax-2024",
        "sensitive": True,
        "last_accessed": "2026-04-30T10:00:00-07:00",
        "read_count": 3,
    })
    _bump_mtime(doc)
    refresh(initialized_workspace, force=True)
    row2 = get_by_path(initialized_workspace, "Sources/taxes/2024-return.pdf")
    assert row2 is not None
    assert row2["notes"] == "Filed on April 14; accepted on April 15."
    assert "tax-2024" in row2["tags"]
    assert row2["related_project"] == "tax-2024"
    assert row2["sensitive"] is True
    assert row2["last_accessed"] == "2026-04-30T10:00:00-07:00"
    assert row2["read_count"] == 3


def test_removed_file_marked_present_false_first_then_dropped(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.sources_index import (
        get_by_path,
        refresh,
    )

    doc = initialized_workspace / "Sources" / "doomed" / "x.pdf"
    _write(doc, "x")
    refresh(initialized_workspace)
    row = get_by_path(initialized_workspace, "Sources/doomed/x.pdf")
    assert row is not None and row["present"] is True

    doc.unlink()
    # Bump the doomed/ directory mtime so the lazy refresh re-walks.
    _bump_mtime(doc.parent)
    refresh(initialized_workspace, force=True)
    row = get_by_path(initialized_workspace, "Sources/doomed/x.pdf")
    assert row is not None and row["present"] is False, (
        "first refresh after rm should mark present=false, not drop"
    )

    refresh(initialized_workspace, force=True)
    row = get_by_path(initialized_workspace, "Sources/doomed/x.pdf")
    assert row is None, "second refresh after rm should drop the row"


def test_cache_subtree_excluded_from_index(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import refresh

    cache_file = initialized_workspace / "Sources" / "_cache" / "abc123" / "raw.txt"
    _write(cache_file, "cached content")
    real_doc = initialized_workspace / "Sources" / "vehicles" / "registration.pdf"
    _write(real_doc, "%PDF reg\n")
    index = refresh(initialized_workspace)
    paths = {r["path"] for r in index["sources"]}
    assert "Sources/vehicles/registration.pdf" in paths
    assert not any(p.startswith("Sources/_cache/") for p in paths), (
        "_cache/ contents must not appear in the index"
    )


def test_project_scoped_sources_indexed_with_related_project(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.sources_index import refresh

    doc = initialized_workspace / "Projects" / "tax-2025" / "Sources" / "draft.pdf"
    _write(doc, "%PDF draft\n")
    index = refresh(initialized_workspace)
    rows = index["sources"]
    row = next(r for r in rows if r["path"].endswith("draft.pdf"))
    assert row["kind"] == "document"
    assert row["related_project"] == "tax-2025"


def test_mark_accessed_increments_read_count(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import (
        get_by_id,
        mark_accessed,
        refresh,
    )

    doc = initialized_workspace / "Sources" / "warranties" / "fridge.pdf"
    _write(doc, "fridge manual")
    refresh(initialized_workspace)
    row_id = next(r["id"] for r in
                  refresh(initialized_workspace)["sources"]
                  if r["path"].endswith("fridge.pdf"))
    mark_accessed(initialized_workspace, row_id)
    mark_accessed(initialized_workspace, row_id)
    row = get_by_id(initialized_workspace, row_id, refresh_first=False)
    assert row["read_count"] == 2
    assert row["last_accessed"] is not None


def test_remove_drops_row_but_not_file(initialized_workspace: Path) -> None:
    from superagent.tools.sources_index import (
        get_by_id,
        refresh,
        remove_row,
    )

    doc = initialized_workspace / "Sources" / "misc" / "keep-me.txt"
    _write(doc, "important")
    refresh(initialized_workspace)
    row_id = next(r["id"] for r in
                  refresh(initialized_workspace)["sources"]
                  if r["path"].endswith("keep-me.txt"))
    assert remove_row(initialized_workspace, row_id) is True
    assert get_by_id(initialized_workspace, row_id, refresh_first=False) is None
    assert doc.exists(), "remove_row must NOT delete the file"


def test_refresh_deduplicates_on_rerun(initialized_workspace: Path) -> None:
    """Calling refresh twice with no changes leaves rows unchanged."""
    from superagent.tools.sources_index import refresh

    _write(initialized_workspace / "Sources" / "a" / "1.txt", "one")
    _write(initialized_workspace / "Sources" / "a" / "2.txt", "two")
    index1 = refresh(initialized_workspace)
    index2 = refresh(initialized_workspace, force=True)
    paths1 = sorted(r["path"] for r in index1["sources"])
    paths2 = sorted(r["path"] for r in index2["sources"])
    assert paths1 == paths2
    assert len([r for r in index2["sources"] if r["path"].startswith("Sources/a/")]) == 2


def test_directory_rename_preserves_curated_fields(initialized_workspace: Path) -> None:
    """Renaming a directory under Sources/ must NOT clobber hand-curated fields.

    Honors `contracts/sources.md` § 15.6:
      'Changed (path move detected by content-hash + filename match):
       update path in place, preserve everything else.'

    Implementation: `diff_and_merge` pass 2 detects renames by basename match
    when both old and new have exactly one row with the same basename.
    """
    from superagent.tools.sources_index import (
        get_by_path,
        id_for_path,
        refresh,
        update_row,
    )

    doc_a = initialized_workspace / "Sources" / "Taxes" / "Taxes 2026" / "W-2.pdf"
    doc_b = initialized_workspace / "Sources" / "Taxes" / "Taxes 2026" / "transcript.pdf"
    _write(doc_a, "%PDF W2 stub\n")
    _write(doc_b, "%PDF transcript stub\n")
    refresh(initialized_workspace)

    row_a = get_by_path(initialized_workspace, "Sources/Taxes/Taxes 2026/W-2.pdf")
    row_b = get_by_path(initialized_workspace, "Sources/Taxes/Taxes 2026/transcript.pdf")
    assert row_a is not None and row_b is not None
    update_row(initialized_workspace, row_a["id"], {
        "title": "2025 W-2 (NVIDIA)",
        "notes": "Primary income input for tax-2026 filing.",
        "tags": ["taxes", "w-2", "2025", "primary"],
        "sensitive": True,
        "related_domain": "finance",
        "related_project": "tax-2026",
        "read_count": 4,
    })
    update_row(initialized_workspace, row_b["id"], {
        "title": "IRS 2025 Wage and Income Transcript",
        "notes": "Cross-check against W-2 + expected 1099s.",
        "tags": ["taxes", "irs-transcript", "2025"],
        "sensitive": True,
        "related_domain": "finance",
        "related_project": "tax-2026",
    })

    old_dir = initialized_workspace / "Sources" / "Taxes" / "Taxes 2026"
    new_dir = initialized_workspace / "Sources" / "Taxes" / "2026 Taxes"
    old_dir.rename(new_dir)
    _bump_mtime(new_dir)

    refresh(initialized_workspace, force=True)

    moved_a = get_by_path(initialized_workspace, "Sources/Taxes/2026 Taxes/W-2.pdf")
    moved_b = get_by_path(initialized_workspace, "Sources/Taxes/2026 Taxes/transcript.pdf")
    assert moved_a is not None, "renamed file should still be in the index at its new path"
    assert moved_b is not None
    assert moved_a["id"] == id_for_path("Sources/Taxes/2026 Taxes/W-2.pdf")
    assert moved_a["id"] != row_a["id"], "id is path-derived; rename produces new id"
    assert moved_a["title"] == "2025 W-2 (NVIDIA)", "curated title must survive rename"
    assert moved_a["notes"] == "Primary income input for tax-2026 filing."
    assert moved_a["tags"] == ["taxes", "w-2", "2025", "primary"]
    assert moved_a["sensitive"] is True
    assert moved_a["related_domain"] == "finance"
    assert moved_a["related_project"] == "tax-2026"
    assert moved_a["read_count"] == 4
    assert moved_a["present"] is True

    assert moved_b["title"] == "IRS 2025 Wage and Income Transcript"
    assert moved_b["sensitive"] is True

    old_a = get_by_path(initialized_workspace, "Sources/Taxes/Taxes 2026/W-2.pdf")
    old_b = get_by_path(initialized_workspace, "Sources/Taxes/Taxes 2026/transcript.pdf")
    assert old_a is None, "old-path row should be replaced by the rename, not retained as 'present:false'"
    assert old_b is None


def test_rename_ambiguous_when_multiple_basename_matches(initialized_workspace: Path) -> None:
    """When TWO directories with same basename get renamed simultaneously, do NOT auto-pair.

    Mark all old paths as `present: false` and treat the new paths as fresh
    rows. This is conservative — better to require manual intervention than
    to silently swap curated fields between unrelated files.
    """
    from superagent.tools.sources_index import get_by_path, refresh, update_row

    a1 = initialized_workspace / "Sources" / "dir-a" / "shared.pdf"
    a2 = initialized_workspace / "Sources" / "dir-b" / "shared.pdf"
    _write(a1, "alpha content")
    _write(a2, "bravo content")
    refresh(initialized_workspace)
    row_a1 = get_by_path(initialized_workspace, "Sources/dir-a/shared.pdf")
    row_a2 = get_by_path(initialized_workspace, "Sources/dir-b/shared.pdf")
    assert row_a1 is not None and row_a2 is not None
    update_row(initialized_workspace, row_a1["id"], {"notes": "alpha"})
    update_row(initialized_workspace, row_a2["id"], {"notes": "bravo"})

    (initialized_workspace / "Sources" / "dir-a").rename(
        initialized_workspace / "Sources" / "dir-a-renamed"
    )
    (initialized_workspace / "Sources" / "dir-b").rename(
        initialized_workspace / "Sources" / "dir-b-renamed"
    )
    _bump_mtime(initialized_workspace / "Sources" / "dir-a-renamed")
    _bump_mtime(initialized_workspace / "Sources" / "dir-b-renamed")

    refresh(initialized_workspace, force=True)

    moved_a = get_by_path(initialized_workspace, "Sources/dir-a-renamed/shared.pdf")
    moved_b = get_by_path(initialized_workspace, "Sources/dir-b-renamed/shared.pdf")
    assert moved_a is not None and moved_b is not None
    assert not moved_a.get("notes"), "ambiguous rename must NOT auto-transplant notes"
    assert not moved_b.get("notes")
    old_a = get_by_path(initialized_workspace, "Sources/dir-a/shared.pdf")
    old_b = get_by_path(initialized_workspace, "Sources/dir-b/shared.pdf")
    assert old_a is not None and old_a["present"] is False
    assert old_b is not None and old_b["present"] is False
    assert old_a["notes"] == "alpha", "user's notes must survive on the present=false row"
    assert old_b["notes"] == "bravo"
