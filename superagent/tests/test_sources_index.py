# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/sources_index.py` (the derived-index tool).

Exercises the contract documented in `contracts/sources.md`:
  - Filesystem under `Sources/` is canonical; the index is derived.
  - Refresh is mtime-lazy.
  - Hand-curated fields (notes, tags, related_*, last_accessed, read_count,
    sensitive) are preserved across refreshes.
  - A `<doc>.<ext>.meta.md` sidecar next to a document is metadata for the
    document (not a separate row); an orphan sidecar is a warning.
  - Every `.ref.md` is a watcher: indexed (kind `watcher`, `watch` lifted)
    only inside the registry; a stray one elsewhere is a warning, not a row.
    `.ref.txt` is an ordinary file.
  - Removed files survive one refresh cycle as `present: false` before being
    dropped.
  - A leftover `Sources/_cache/` (retired 0.19.0 fetch cache) is excluded.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def _write(p: Path, text: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _refresh(ws: Path, **kw) -> tuple[dict, list[str]]:
    """`refresh(force=True)` returning (index, warnings)."""
    from superagent.tools.sources_index import refresh

    warnings: list[str] = []
    index = refresh(ws, force=True, warn=warnings.append, **kw)
    return index, warnings


SIDECAR = (
    "---\n"
    "title: Camry title 2018\n"
    "tags: [titles, camry]\n"
    "related_asset: asset-camry-2018\n"
    "sensitive: true\n"
    "---\n\nThe physical original is in the file cabinet.\n"
)


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


def test_stray_ref_outside_the_registry_warns_and_is_not_indexed(initialized_workspace: Path) -> None:
    """Every `.ref.md` is a watcher (0.20.0): one outside `Sources/Watchlist/` is a stray."""
    ref = initialized_workspace / "Sources" / "finance" / "fidelity.ref.md"
    _write(ref, "---\nref_version: 2\ntitle: Fidelity 401k portal\nwatch:\n  type: url\n"
                "  url: \"https://401k.fidelity.com/dashboard\"\n---\n")
    _write(initialized_workspace / "Projects" / "x" / "Sources" / "Portal.ref.md",
           "---\nref_version: 2\ntitle: p\nwatch: {type: url, url: https://x}\n---\n")
    index, warnings = _refresh(initialized_workspace)
    paths = {r["path"] for r in index["sources"] if r.get("id")}
    assert "Sources/finance/fidelity.ref.md" not in paths
    assert "Projects/x/Sources/Portal.ref.md" not in paths
    assert sorted(warnings) == [
        "Projects/x/Sources/Portal.ref.md: stray .ref.md outside the registry — a .ref.md is a "
        "watcher definition (Sources/Watchlist/); document metadata belongs in `<doc>.<ext>.meta.md`; "
        "not indexed",
        "Sources/finance/fidelity.ref.md: stray .ref.md outside the registry — a .ref.md is a "
        "watcher definition (Sources/Watchlist/); document metadata belongs in `<doc>.<ext>.meta.md`; "
        "not indexed",
    ]


def test_ref_txt_is_an_ordinary_file(initialized_workspace: Path) -> None:
    """`.ref.txt` support is gone: the file indexes like any other document, nothing is parsed."""
    ref = initialized_workspace / "Sources" / "misc" / "freeform.ref.txt"
    _write(ref, "URL: https://example.com\nTitle: Example\n")
    index, warnings = _refresh(initialized_workspace)
    row = next(r for r in index["sources"] if r["path"].endswith("freeform.ref.txt"))
    assert row["kind"] == "document"
    assert row["title"] == "freeform.ref" and "watch" not in row
    assert warnings == []


def test_meta_sidecar_does_not_create_separate_row(initialized_workspace: Path) -> None:
    """A `<doc>.<ext>.meta.md` sibling is metadata for the document, not a row of its own."""
    doc = initialized_workspace / "Sources" / "vehicles" / "title.pdf"
    sidecar = initialized_workspace / "Sources" / "vehicles" / "title.pdf.meta.md"
    _write(doc, "%PDF-1.4 stub\n")
    _write(sidecar, SIDECAR)
    index, warnings = _refresh(initialized_workspace)
    assert warnings == []
    rows = [r for r in index["sources"] if r["path"].startswith("Sources/vehicles/")]
    paths = sorted(r["path"] for r in rows)
    assert paths == ["Sources/vehicles/title.pdf"], (
        f"expected only the document row, got {paths}"
    )
    row = rows[0]
    assert row["kind"] == "document"
    assert row["title"] == "Camry title 2018"
    assert row["related_asset"] == "asset-camry-2018"
    assert row["sensitive"] is True
    assert "titles" in row["tags"]
    assert "watch" not in row


def test_meta_sidecar_form_b_and_project_scoped(initialized_workspace: Path) -> None:
    """`<stem>.meta.md` next to `<stem>.<ext>` works too, including under Projects/*/Sources/."""
    doc = initialized_workspace / "Projects" / "tax-2025" / "Sources" / "w2.pdf"
    _write(doc, "%PDF\n")
    _write(doc.with_name("w2.meta.md"), "---\ntitle: 2025 W-2\ntags: [taxes]\n---\n")
    index, warnings = _refresh(initialized_workspace)
    assert warnings == []
    rows = {r["path"]: r for r in index["sources"] if r.get("id")}
    assert "Projects/tax-2025/Sources/w2.meta.md" not in rows
    row = rows["Projects/tax-2025/Sources/w2.pdf"]
    assert row["title"] == "2025 W-2" and row["tags"] == ["taxes"] and row["related_project"] == "tax-2025"


def test_orphan_meta_sidecar_warns_and_is_not_indexed(initialized_workspace: Path) -> None:
    _write(initialized_workspace / "Sources" / "vehicles" / "gone.pdf.meta.md", SIDECAR)
    index, warnings = _refresh(initialized_workspace)
    assert not any(r["path"].endswith("gone.pdf.meta.md") for r in index["sources"] if r.get("id"))
    assert warnings == ["Sources/vehicles/gone.pdf.meta.md: orphan sidecar — no document next to it "
                        "(expected `<doc>.<ext>.meta.md` beside its document); not indexed"]


def test_legacy_ref_sidecar_is_a_stray_and_lends_no_metadata(initialized_workspace: Path) -> None:
    """A pre-0.20.0 `<doc>.<ext>.ref.md` next to its document: warned, not applied (rename it)."""
    doc = initialized_workspace / "Sources" / "vehicles" / "title.pdf"
    _write(doc, "%PDF-1.4 stub\n")
    _write(doc.with_name("title.pdf.ref.md"), SIDECAR)
    index, warnings = _refresh(initialized_workspace)
    rows = {r["path"]: r for r in index["sources"] if r.get("id")}
    assert "Sources/vehicles/title.pdf.ref.md" not in rows
    assert rows["Sources/vehicles/title.pdf"]["title"] == "title", "legacy sidecar metadata is not applied"
    assert len(warnings) == 1
    assert warnings[0].startswith("Sources/vehicles/title.pdf.ref.md: stray .ref.md outside the registry")
    assert "belongs in `title.pdf.meta.md`" in warnings[0], "the rename is spelled out"


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


def test_leftover_cache_subtree_excluded_from_index(initialized_workspace: Path) -> None:
    """A `Sources/_cache/` left behind by 0.19.0 never becomes "documents"."""
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


WATCH_REF = (
    "---\n"
    "ref_version: 2\n"
    "title: Permit portal\n"
    "related_project: solar\n"
    "watch:\n"
    "  type: url\n"
    "  url: \"https://permits.example.gov/status?id=1\"\n"
    "  enabled: true\n"
    "  cycles: [daily-update]\n"
    "  selector: \"#status\"\n"
    "---\n\nNotes.\n"
)


def test_watchlist_ref_indexed_as_watcher_with_watch_lifted(initialized_workspace: Path) -> None:
    """A `Sources/Watchlist/<name>.ref.md` is a `watcher` row with the `watch` mapping lifted;
    the registry README is excluded like `Sources/README.md`."""
    from superagent.tools.sources_index import ref_stem

    ref = initialized_workspace / "Sources" / "Watchlist" / "Solar-Permit.ref.md"
    _write(ref, WATCH_REF)
    index, warnings = _refresh(initialized_workspace)
    assert warnings == []
    rows = {r["path"]: r for r in index["sources"] if r.get("id")}
    row = rows["Sources/Watchlist/Solar-Permit.ref.md"]
    assert row["kind"] == "watcher" and row["category"] == "Watchlist"
    assert row["title"] == "Permit portal"
    assert row["related_project"] == "solar"
    assert row["normalized"] is True
    assert row["watch"] == {"type": "url", "url": "https://permits.example.gov/status?id=1",
                            "enabled": True, "cycles": ["daily-update"], "selector": "#status"}
    assert ref_stem(row["path"]) == "Solar-Permit", "the stem as written; the tool lowercases it for the id"
    assert "Sources/Watchlist/README.md" not in rows
    # Rows without a watch: block carry no `watch` key at all.
    assert all("watch" not in r for p, r in rows.items() if p != row["path"])


def test_registry_path_from_config_and_meta_inside_registry(initialized_workspace: Path) -> None:
    """`preferences.watchlist.path` moves the registry; the registry holds watchers only —
    a document parked inside it is warned about and not indexed (its `.meta.md` stays a sidecar)."""
    import yaml

    ws = initialized_workspace
    cfg_path = ws / "_memory" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    cfg.setdefault("preferences", {})["watchlist"] = {"path": "Sources/Watchers"}
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    _write(ws / "Sources" / "Watchers" / "Solar-Permit.ref.md", WATCH_REF)
    _write(ws / "Sources" / "Watchlist" / "Old.ref.md", WATCH_REF)  # no longer the registry: stray
    _write(ws / "Sources" / "Watchers" / "notes.pdf", "%PDF\n")
    _write(ws / "Sources" / "Watchers" / "notes.pdf.meta.md", "---\ntitle: Registry notes\n---\n")
    index, warnings = _refresh(ws)
    rows = {r["path"]: r for r in index["sources"] if r.get("id")}
    assert rows["Sources/Watchers/Solar-Permit.ref.md"]["kind"] == "watcher"
    assert "Sources/Watchers/notes.pdf" not in rows, "the registry is for watchers; documents go elsewhere"
    assert "Sources/Watchers/notes.pdf.meta.md" not in rows
    assert "Sources/Watchlist/Old.ref.md" not in rows
    assert len(warnings) == 2, warnings
    stray = [w for w in warnings if w.startswith("Sources/Watchlist/Old.ref.md: stray .ref.md")]
    assert stray and "(Sources/Watchers/)" in stray[0]
    parked = [w for w in warnings if w.startswith("Sources/Watchers/notes.pdf: not a .ref.md — not a watcher")]
    assert parked and "(Sources/Watchers/)" in parked[0]


def test_watch_field_survives_broken_file_but_drops_when_block_removed(
    initialized_workspace: Path,
) -> None:
    ref = initialized_workspace / "Sources" / "Watchlist" / "Solar-Permit.ref.md"
    _write(ref, WATCH_REF)
    _refresh(initialized_workspace)
    # Transiently unparseable frontmatter: keep the previously lifted mapping.
    _write(ref, "---\ntitle: x\nwatch: [unclosed\n---\n")
    _bump_mtime(ref)
    index, _ = _refresh(initialized_workspace)
    row = next(r for r in index["sources"] if r.get("path") == "Sources/Watchlist/Solar-Permit.ref.md")
    assert row["normalized"] is False
    assert row["watch"]["type"] == "url"
    # A clean file WITHOUT a watch: block means the block is gone: drop it.
    _write(ref, WATCH_REF.split("watch:\n")[0] + "---\n\nNotes.\n")
    _bump_mtime(ref, 4.0)
    index, _ = _refresh(initialized_workspace)
    row = next(r for r in index["sources"] if r.get("path") == "Sources/Watchlist/Solar-Permit.ref.md")
    assert row["normalized"] is True
    assert "watch" not in row


def test_cli_list_kind_choices_and_refresh_prints_warnings(initialized_workspace: Path, capsys) -> None:
    from superagent.tools.sources_index import main

    ws = initialized_workspace
    _write(ws / "Sources" / "Watchlist" / "Solar-Permit.ref.md", WATCH_REF)
    _write(ws / "Sources" / "docs" / "a.pdf", "%PDF\n")
    _write(ws / "Sources" / "docs" / "stray.ref.md", "---\ntitle: s\n---\n")
    assert main(["--workspace", str(ws), "refresh", "--force"]) == 0
    out = capsys.readouterr().out
    assert "Index has 2 row(s)" in out
    assert "WARN  Sources/docs/stray.ref.md: stray .ref.md outside the registry" in out
    assert main(["--workspace", str(ws), "list", "--kind", "watcher"]) == 0
    out = capsys.readouterr().out
    assert "Solar-Permit.ref.md" in out and "a.pdf" not in out
    assert main(["--workspace", str(ws), "list", "--kind", "document"]) == 0
    out = capsys.readouterr().out
    assert "a.pdf" in out and "Solar-Permit" not in out
    import pytest

    with pytest.raises(SystemExit):
        main(["--workspace", str(ws), "list", "--kind", "reference"])


def test_path_rewritten_in_place_keeps_row_id(initialized_workspace: Path) -> None:
    """Path identity: a present row whose `path` already names the scanned file keeps
    its id even though the derived `src-<sha1(path)>` would differ (contracts/sources.md).
    This is what lets a migration relocate a file without orphaning the `src-...` ids
    cited in history / log rows."""
    from superagent.tools.sources_index import (
        id_for_path,
        load_index,
        refresh,
        save_index,
        update_row,
    )

    old = initialized_workspace / "Sources" / "a" / "thing.pdf"
    new = initialized_workspace / "Sources" / "b" / "thing_moved.pdf"
    _write(old, "%PDF\n")
    refresh(initialized_workspace, force=True)
    old_id = id_for_path("Sources/a/thing.pdf")
    update_row(initialized_workspace, old_id, {"notes": "keep me"})
    # Move the file and rewrite the row's path in place (id untouched).
    new.parent.mkdir(parents=True)
    old.rename(new)
    index = load_index(initialized_workspace)
    for row in index["sources"]:
        if row.get("id") == old_id:
            row["path"] = "Sources/b/thing_moved.pdf"
    save_index(initialized_workspace, index)
    rows = {r["path"]: r for r in refresh(initialized_workspace, force=True)["sources"] if r.get("id")}
    row = rows["Sources/b/thing_moved.pdf"]
    assert row["id"] == old_id != id_for_path("Sources/b/thing_moved.pdf")
    assert row["notes"] == "keep me" and row["present"] is True
    assert "Sources/a/thing.pdf" not in rows
    assert sum(1 for r in rows.values() if r["id"] == old_id) == 1


def test_non_ref_file_in_the_registry_is_warned_and_not_indexed(initialized_workspace: Path) -> None:
    """Review finding: `Sources/Watchlist/HA.md` (a v1 watcher parked under the wrong name)
    was indexed as a bogus `document` row and never mentioned."""
    reg = initialized_workspace / "Sources" / "Watchlist"
    _write(reg / "README.md", "# registry\n")
    _write(reg / "Ha.md", "---\nref_version: 1\ntitle: parked\nkind: cli\nsource: 'echo hi'\n---\n")
    _write(reg / "Ok.ref.md", "---\nref_version: 2\ntitle: ok\nwatch: {type: url, url: https://x}\n---\n")
    index, warnings = _refresh(initialized_workspace)
    paths = {r["path"]: r for r in index["sources"] if r.get("id")}
    assert "Sources/Watchlist/Ha.md" not in paths
    assert "Sources/Watchlist/README.md" not in paths
    assert paths["Sources/Watchlist/Ok.ref.md"]["kind"] == "watcher"
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("Sources/Watchlist/Ha.md: not a .ref.md — not a watcher; rename it to "
                                  "`<name>.ref.md`"), "no casing is prescribed for the user's file"
    assert "Title_Case" not in warnings[0]
