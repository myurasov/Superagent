# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/sources_cache.py`."""
from __future__ import annotations

from pathlib import Path

import yaml


def _write_ref(path: Path, kind: str, source: str, ttl_minutes: int = 1440,
               sensitive: bool = False, body: str = "") -> None:
    """Write a minimal `.ref.md` for testing."""
    fm = (
        f"---\n"
        f"ref_version: 1\n"
        f"title: test\n"
        f"description: test ref\n"
        f"kind: {kind}\n"
        f"source: \"{source}\"\n"
        f"ttl_minutes: {ttl_minutes}\n"
        f"sensitive: {str(sensitive).lower()}\n"
        f"---\n\n"
        f"{body}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm)


def test_source_hash_deterministic() -> None:
    from superagent.tools.sources_cache import source_hash

    h1 = source_hash("file", "/tmp/foo.txt")
    h2 = source_hash("file", "/tmp/foo.txt")
    assert h1 == h2
    assert len(h1) == 16
    assert source_hash("file", "/tmp/foo.txt") != source_hash("url", "/tmp/foo.txt")


def test_fetch_kind_file_writes_cache(initialized_workspace: Path, tmp_path: Path) -> None:
    """A `kind: file` ref produces a populated cache folder."""
    from superagent.tools.sources_cache import (
        cache_dir, get_cache, source_hash,
    )

    src = tmp_path / "sample.md"
    src.write_text("# Sample\n\nThis is a small reference document.\n")
    ref = initialized_workspace / "Sources" / "references" / "test" / "sample.ref.md"
    _write_ref(ref, kind="file", source=str(src))
    meta = get_cache(initialized_workspace, ref)
    assert meta["source_hash"] == source_hash("file", str(src))
    cache = cache_dir(initialized_workspace, "file", str(src))
    assert (cache / "raw.md").exists()
    assert (cache / "_summary.md").exists()
    assert (cache / "_toc.yaml").exists()
    assert (cache / "_meta.yaml").exists()


def test_get_cache_reuses_fresh_entry(initialized_workspace: Path, tmp_path: Path) -> None:
    """A second call within TTL must NOT re-read the source."""
    from superagent.tools.sources_cache import cache_dir, get_cache

    src = tmp_path / "sample.md"
    src.write_text("original\n")
    ref = initialized_workspace / "Sources" / "references" / "test" / "ref.ref.md"
    _write_ref(ref, kind="file", source=str(src), ttl_minutes=1440)
    meta1 = get_cache(initialized_workspace, ref)
    fetched1 = meta1["fetched_at"]
    src.write_text("changed\n")
    meta2 = get_cache(initialized_workspace, ref)
    assert meta2["fetched_at"] == fetched1, "cache should not have re-fetched within TTL"
    cache = cache_dir(initialized_workspace, "file", str(src))
    raw = (cache / "raw.md").read_text()
    assert raw == "original\n"


def test_get_cache_refresh_forces_refetch(initialized_workspace: Path, tmp_path: Path) -> None:
    """`refresh=True` re-fetches even when cache is fresh."""
    from superagent.tools.sources_cache import cache_dir, get_cache

    src = tmp_path / "sample.md"
    src.write_text("original\n")
    ref = initialized_workspace / "Sources" / "references" / "test" / "ref.ref.md"
    _write_ref(ref, kind="file", source=str(src))
    get_cache(initialized_workspace, ref)
    src.write_text("changed\n")
    get_cache(initialized_workspace, ref, refresh=True)
    cache = cache_dir(initialized_workspace, "file", str(src))
    assert (cache / "raw.md").read_text() == "changed\n"


def test_get_cache_zero_ttl_always_refetches(initialized_workspace: Path, tmp_path: Path) -> None:
    """`ttl_minutes: 0` skips caching — every read re-fetches."""
    from superagent.tools.sources_cache import cache_dir, get_cache

    src = tmp_path / "sample.md"
    src.write_text("v1\n")
    ref = initialized_workspace / "Sources" / "references" / "test" / "ref.ref.md"
    _write_ref(ref, kind="file", source=str(src), ttl_minutes=0)
    get_cache(initialized_workspace, ref)
    src.write_text("v2\n")
    get_cache(initialized_workspace, ref)
    cache = cache_dir(initialized_workspace, "file", str(src))
    assert (cache / "raw.md").read_text() == "v2\n"


def test_chunking_kicks_in_for_large_content(
    initialized_workspace: Path, tmp_path: Path
) -> None:
    """A ref pointing at content over chunk_threshold_kb gets chunked."""
    from superagent.tools.sources_cache import cache_dir, get_cache

    src = tmp_path / "big.md"
    huge = "para\n\n" * 30000  # ~180 KB → exceeds 100 KB threshold
    src.write_text(huge)
    ref = initialized_workspace / "Sources" / "references" / "test" / "big.ref.md"
    _write_ref(ref, kind="file", source=str(src))
    meta = get_cache(initialized_workspace, ref)
    assert meta["chunks_count"] > 0
    cache = cache_dir(initialized_workspace, "file", str(src))
    chunks = list((cache / "chunks").glob("chunk-*.md"))
    assert len(chunks) == meta["chunks_count"]
    idx = yaml.safe_load((cache / "chunks" / "_index.yaml").read_text())
    assert len(idx["chunks"]) == meta["chunks_count"]


def test_evict_lru_removes_oldest_first(
    initialized_workspace: Path, tmp_path: Path
) -> None:
    """LRU eviction removes the oldest-`last_used` entry first."""
    from superagent.tools.sources_cache import (
        cache_dir, evict_lru, get_cache, total_cache_size,
    )

    a = tmp_path / "a.md"
    a.write_text("alpha\n")
    b = tmp_path / "b.md"
    b.write_text("beta\n")
    ref_a = initialized_workspace / "Sources" / "references" / "test" / "a.ref.md"
    ref_b = initialized_workspace / "Sources" / "references" / "test" / "b.ref.md"
    _write_ref(ref_a, kind="file", source=str(a))
    _write_ref(ref_b, kind="file", source=str(b))
    get_cache(initialized_workspace, ref_a)
    get_cache(initialized_workspace, ref_b)
    cache_a = cache_dir(initialized_workspace, "file", str(a))
    cache_b = cache_dir(initialized_workspace, "file", str(b))
    assert cache_a.exists() and cache_b.exists()
    cur = total_cache_size(initialized_workspace)
    n = evict_lru(initialized_workspace, target_bytes=cur // 2)
    assert n >= 1
    remaining = list((initialized_workspace / "Sources" / "_cache").iterdir())
    assert len(remaining) < 2


def test_list_cache_summarizes(initialized_workspace: Path, tmp_path: Path) -> None:
    from superagent.tools.sources_cache import get_cache, list_cache

    src = tmp_path / "x.md"
    src.write_text("hi\n")
    ref = initialized_workspace / "Sources" / "references" / "test" / "x.ref.md"
    _write_ref(ref, kind="file", source=str(src))
    get_cache(initialized_workspace, ref)
    entries = list_cache(initialized_workspace)
    assert len(entries) == 1
    assert entries[0]["kind"] == "file"
    assert entries[0]["chunks"] == 0
    assert entries[0]["size_kb"] > 0
