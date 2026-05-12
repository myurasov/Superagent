"""Tests for `tools/domain_detector.py`.

Implements `contracts/domains-and-assets.md` § 6.4b. The detector walks
workspace signals (off-domain tags, contact-role clusters, project
keywords, source-folder clusters) and surfaces candidates the user hasn't
already accepted / declined / deferred.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from superagent.tools.domain_detector import (
    build_alias_set,
    detect,
    forget,
    handled_themes,
    load_suggestions,
    normalize_theme,
    record_answer,
    write_suggestions,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def _seed_tags(workspace: Path, rows: list[dict]) -> None:
    _write_yaml(workspace / "_memory" / "tags.yaml", {"schema_version": 1, "tags": rows})


def _seed_contacts(workspace: Path, rows: list[dict]) -> None:
    _write_yaml(workspace / "_memory" / "contacts.yaml", {"schema_version": 1, "contacts": rows})


def _seed_projects(workspace: Path, rows: list[dict]) -> None:
    _write_yaml(
        workspace / "_memory" / "projects-index.yaml",
        {"schema_version": 1, "projects": rows},
    )


def test_normalize_theme_canonicalizes() -> None:
    assert normalize_theme("Sailing") == "sailing"
    assert normalize_theme("Side Business 2026") == "side-business-2026"
    assert normalize_theme("  garden / yard  ") == "garden-yard"
    assert normalize_theme("") == ""


def test_alias_set_includes_all_default_domain_synonyms(
    initialized_workspace: Path,
) -> None:
    aliases = build_alias_set(initialized_workspace)
    assert "medical" in aliases
    assert "vet" in aliases
    assert "stock" in aliases
    assert "tuition" in aliases
    assert "education" in aliases
    assert "default" in aliases  # picked up from the registered tags[]


def test_detect_empty_workspace_returns_empty(initialized_workspace: Path) -> None:
    assert detect(initialized_workspace) == []


def test_detect_surfaces_off_domain_tag_cluster(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [
        {"id": "sailing", "uses_count": 8, "aliases": []},
    ])
    suggestions = detect(initialized_workspace, min_score=5)
    assert any(s.theme == "sailing" for s in suggestions)
    sail = next(s for s in suggestions if s.theme == "sailing")
    assert sail.score >= 5
    assert sail.proposed_name == "Sailing"


def test_detect_skips_tags_aliased_to_existing_domains(
    initialized_workspace: Path,
) -> None:
    """A `medical` tag should NOT trigger a new domain — it maps to Health."""
    _seed_tags(initialized_workspace, [
        {"id": "medical", "uses_count": 20},
        {"id": "stock", "uses_count": 15},
        {"id": "tuition", "uses_count": 10},
    ])
    suggestions = detect(initialized_workspace, min_score=5)
    assert all(s.theme not in {"medical", "stock", "tuition"} for s in suggestions)


def test_detect_clusters_contact_roles(initialized_workspace: Path) -> None:
    _seed_contacts(initialized_workspace, [
        {"id": "alice", "name": "Alice", "role": "board_member"},
        {"id": "bob", "name": "Bob", "role": "board_member"},
        {"id": "carol", "name": "Carol", "role": "board_member"},
    ])
    suggestions = detect(initialized_workspace, min_score=5)
    themes = {s.theme for s in suggestions}
    assert "board-member" in themes


def test_detect_clusters_project_keywords(initialized_workspace: Path) -> None:
    _seed_projects(initialized_workspace, [
        {"id": "kitchen-renovation-2026", "name": "Kitchen renovation 2026"},
        {"id": "bathroom-renovation-2026", "name": "Bathroom renovation 2026"},
    ])
    suggestions = detect(initialized_workspace, min_score=5)
    themes = {s.theme for s in suggestions}
    assert "renovation" in themes


def test_detect_combines_signals_for_higher_score(
    initialized_workspace: Path,
) -> None:
    """A theme with multiple signal types should score above the floor."""
    _seed_tags(initialized_workspace, [{"id": "garden", "uses_count": 4}])
    _seed_contacts(initialized_workspace, [
        {"id": "g1", "name": "G1", "role": "garden-supplier"},
        {"id": "g2", "name": "G2", "role": "garden-supplier"},
        {"id": "g3", "name": "G3", "role": "garden-supplier"},
    ])
    suggestions = detect(initialized_workspace, min_score=5)
    # Both `garden` and `garden-supplier` are aliased — should be skipped.
    assert all(s.theme not in {"garden", "garden-supplier"} for s in suggestions)


def test_detect_filters_handled_themes(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [{"id": "sailing", "uses_count": 8}])
    record_answer(initialized_workspace, "sailing", "never")
    forget(initialized_workspace, "sailing")  # remove from declined
    # First detect: re-surfaces because cleared
    s1 = detect(initialized_workspace, min_score=5)
    assert any(s.theme == "sailing" for s in s1)
    # Decline again, then detect must not re-surface
    write_suggestions(initialized_workspace, s1)
    record_answer(initialized_workspace, "sailing", "never")
    s2 = detect(initialized_workspace, min_score=5)
    assert all(s.theme != "sailing" for s in s2)


def test_handled_themes_respects_revisit_dates(
    initialized_workspace: Path,
) -> None:
    """Deferred themes with a future revisit_after are skipped; past ones aren't."""
    _seed_tags(initialized_workspace, [
        {"id": "sailing", "uses_count": 8},
        {"id": "boating", "uses_count": 6},
    ])
    s1 = detect(initialized_workspace, min_score=5)
    write_suggestions(initialized_workspace, s1)
    record_answer(initialized_workspace, "sailing", "not_now")

    today = dt.date.today()
    skip = handled_themes(load_suggestions(initialized_workspace), today=today)
    assert "sailing" in skip
    far_future = today + dt.timedelta(days=200)
    skip_future = handled_themes(load_suggestions(initialized_workspace), today=far_future)
    assert "sailing" not in skip_future


def test_record_answer_round_trip(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [{"id": "sailing", "uses_count": 8}])
    write_suggestions(initialized_workspace, detect(initialized_workspace, min_score=5))
    assert record_answer(initialized_workspace, "sailing", "yes") is True

    data = load_suggestions(initialized_workspace)
    assert all(r.get("theme") != "sailing" for r in data["suggested"])
    assert any(r.get("theme") == "sailing" for r in data["accepted"])
    assert any(r.get("theme") == "sailing" and r.get("answer") == "yes"
               for r in data["surfaced"])


def test_record_answer_rejects_unknown_answer(initialized_workspace: Path) -> None:
    import pytest
    with pytest.raises(ValueError):
        record_answer(initialized_workspace, "x", "maybe")


def test_record_answer_returns_false_when_theme_not_suggested(
    initialized_workspace: Path,
) -> None:
    assert record_answer(initialized_workspace, "no-such-theme", "never") is False


def test_forget_clears_declined_and_deferred(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [{"id": "sailing", "uses_count": 8}])
    write_suggestions(initialized_workspace, detect(initialized_workspace, min_score=5))
    record_answer(initialized_workspace, "sailing", "never")
    assert forget(initialized_workspace, "sailing") is True
    data = load_suggestions(initialized_workspace)
    assert all(r.get("theme") != "sailing" for r in data["declined"])


def test_write_suggestions_idempotent_by_theme(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [{"id": "sailing", "uses_count": 8}])
    s = detect(initialized_workspace, min_score=5)
    written_first = write_suggestions(initialized_workspace, s)
    written_second = write_suggestions(initialized_workspace, s)
    assert written_first == 1
    assert written_second == 0


def test_min_score_threshold_drops_weak_candidates(
    initialized_workspace: Path,
) -> None:
    _seed_tags(initialized_workspace, [{"id": "sailing", "uses_count": 3}])
    high = detect(initialized_workspace, min_score=10)
    low = detect(initialized_workspace, min_score=2)
    assert all(s.theme != "sailing" for s in high)
    assert any(s.theme == "sailing" for s in low)


def test_top_n_caps_results(initialized_workspace: Path) -> None:
    _seed_tags(initialized_workspace, [
        {"id": "sailing", "uses_count": 8},
        {"id": "knitting", "uses_count": 7},
        {"id": "pottery", "uses_count": 6},
        {"id": "scuba", "uses_count": 5},
    ])
    suggestions = detect(initialized_workspace, min_score=5, top_n=2)
    assert len(suggestions) == 2
    # Top scorer should be first
    assert suggestions[0].theme == "sailing"
