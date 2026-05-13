# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/briefing_cache.py`."""
from __future__ import annotations

import time
from pathlib import Path


def test_put_and_get_hit(initialized_workspace: Path) -> None:
    from superagent.tools.briefing_cache import get, put

    put(initialized_workspace, "daily-update", "2026-04-28",
        body="# briefing body\n", ttl_minutes=60)
    result = get(initialized_workspace, "daily-update", "2026-04-28")
    assert result is not None
    assert "briefing body" in result["body"]
    assert result["meta"]["skill"] == "daily-update"


def test_get_miss_when_absent(initialized_workspace: Path) -> None:
    from superagent.tools.briefing_cache import get

    result = get(initialized_workspace, "no-such-skill", "no-such-key")
    assert result is None


def test_input_mtime_invalidates(initialized_workspace: Path, tmp_path: Path) -> None:
    from superagent.tools.briefing_cache import get, put

    src = tmp_path / "input.yaml"
    src.write_text("first\n")
    put(initialized_workspace, "test-skill", "k1", body="body1\n",
        input_paths=[src], ttl_minutes=60)
    assert get(initialized_workspace, "test-skill", "k1",
               input_paths=[src]) is not None
    # Mutate the input.
    time.sleep(1.1)
    src.write_text("changed\n")
    result = get(initialized_workspace, "test-skill", "k1", input_paths=[src])
    assert result is None, "should miss when input mtime moved past created_at"


def test_evict_by_age(initialized_workspace: Path) -> None:
    from superagent.tools.briefing_cache import evict, list_artifacts, put

    put(initialized_workspace, "test-skill", "old1", body="x\n", ttl_minutes=60)
    put(initialized_workspace, "test-skill", "old2", body="x\n", ttl_minutes=60)
    assert len(list_artifacts(initialized_workspace)) >= 2
    # `older_than_minutes` semantics: evict entries created MORE than N minutes ago.
    # 0 means "anything created at or before now", which catches our entries.
    n = evict(initialized_workspace, skill="test-skill", older_than_minutes=0)
    assert n >= 2
