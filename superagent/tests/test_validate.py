# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/validate.py`."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_validate_passes_on_fresh_workspace(framework_dir: Path, initialized_workspace: Path) -> None:
    """A freshly-initialized workspace must validate clean."""
    from superagent.tools.validate import main as validate_main

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0


def test_validate_catches_unexpected_top_level_key(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """Adding an unexpected top-level key to a memory file must fail validation."""
    from superagent.tools.validate import main as validate_main

    config_path = initialized_workspace / "_memory" / "config.yaml"
    with config_path.open() as fh:
        data = yaml.safe_load(fh)
    data["bogus_key"] = "should-not-be-here"
    with config_path.open("w") as fh:
        yaml.safe_dump(data, fh)
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc != 0


def test_validate_catches_missing_schema_version(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """A YAML file missing `schema_version` must fail validation."""
    from superagent.tools.validate import main as validate_main

    todo_path = initialized_workspace / "_memory" / "todo.yaml"
    with todo_path.open() as fh:
        data = yaml.safe_load(fh)
    data.pop("schema_version", None)
    with todo_path.open("w") as fh:
        yaml.safe_dump(data, fh)
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc != 0


def test_validate_passes_after_events_derivation(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """A workspace that has run an events derivation must still validate clean.

    Validation compares each memory file's top-level keys against the
    template's. 0.16.0 taught `events_derive.py` to write a `derive_state`
    bookkeeping block into `_memory/events.yaml` but never declared that key
    in the template, so every workspace that had ever derived its events
    stream failed validation on an unexpected-top-level-key error. Only a
    post-derivation validate catches it — a fresh workspace passes either way.
    """
    from superagent.tools.events_derive import rebuild
    from superagent.tools.validate import main as validate_main

    result = rebuild(initialized_workspace)
    assert "error" not in result and "skipped" not in result, result

    events = yaml.safe_load(
        (initialized_workspace / "_memory" / "events.yaml").read_text()
    )
    assert "derive_state" in events

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0


# ------------------------------------------------ interaction-log soft checks


def _write_ilog(workspace: Path, rows: list[dict]) -> Path:
    path = workspace / "_memory" / "interaction-log.yaml"
    with path.open("w") as fh:
        yaml.safe_dump({"schema_version": 1, "entries": rows}, fh, sort_keys=False)
    return path


def test_skill_stems_include_framework_and_custom_overlay(
    framework_dir: Path, initialized_workspace: Path
) -> None:
    """Stems come from superagent/skills/*.md plus <workspace>/_custom/skills/*.md."""
    from superagent.tools.validate import skill_stems

    custom = initialized_workspace / "_custom" / "skills"
    custom.mkdir(parents=True, exist_ok=True)
    (custom / "browserctl.shop.md").write_text("---\nname: browserctl.shop\n---\n")
    stems = skill_stems(framework_dir, initialized_workspace)
    assert "migrate" in stems
    assert "ingest" in stems
    assert "browserctl.shop" in stems
    assert not any(s.startswith("_") for s in stems)
    # Missing workspace / overlay dir is fine.
    assert "migrate" in skill_stems(framework_dir, None)


def test_check_interaction_log_classifies_skill_values() -> None:
    """Unknown vs prefixed alias vs stem; new-shape rows missing id/ts/skill counted."""
    from superagent.tools.validate import check_interaction_log

    stems = {"migrate", "ingest", "browserctl", "browserctl.shop"}
    rows = [
        # placeholder row from the template — ignored
        {"id": None, "ts": None, "skill": None, "action": "", "summary": "",
         "related_domain": None, "action_items": [], "ingestion_log_ref": None},
        # canonical, stem
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "migrate", "action": "apply", "summary": "ok"},
        # canonical, overlay stem
        {"id": "ilog-2026-09-01-002", "ts": "2026-09-01T10:05:00-07:00",
         "skill": "browserctl.shop", "action": "order_lookup", "summary": "ok"},
        # canonical, null skill (non-skill note) — allowed
        {"id": "ilog-2026-09-01-003", "ts": "2026-09-01T10:06:00-07:00",
         "skill": None, "action": "note", "summary": "ok"},
        # legacy prefixed alias
        {"id": "ilog-2026-09-01-004", "ts": "2026-09-01T10:10:00-07:00",
         "skill": "superagent-migrate", "action": "apply", "summary": "ok"},
        # unknown free text (compound)
        {"id": "ilog-2026-09-01-005", "ts": "2026-09-01T10:20:00-07:00",
         "skill": "ingest + log-event (update)", "action": "x", "summary": "ok"},
        # unknown: project slug in skill
        {"id": "ilog-2026-09-01-006", "ts": "2026-09-01T10:21:00-07:00",
         "skill": "some-project", "action": "x", "summary": "ok"},
        # new-shape row missing id
        {"ts": "2026-09-01T10:30:00-07:00", "skill": "migrate", "action": "x", "summary": "ok"},
        # legacy row — shape never flagged
        {"timestamp": "2026-08-01T09:00:00-07:00", "type": "skill_run",
         "subject": "migrate", "summary": "legacy"},
        # hybrid legacy row carrying a bad skill — value flagged, shape not
        {"timestamp": "2026-08-02T09:00:00-07:00", "type": "skill_run",
         "subject": "x", "skill": "superagent-browserctl", "summary": "legacy"},
    ]
    warnings = check_interaction_log(rows, stems)
    assert len(warnings) == 3, warnings
    unknown, prefixed, incomplete = warnings
    assert "2 row(s) use a 'skill' value that is not a skill stem" in unknown
    assert "'ingest + log-event (update)' x1" in unknown
    assert "'some-project' x1" in unknown
    assert "2 row(s) use the legacy prefixed" in prefixed
    assert "'superagent-migrate' x1" in prefixed
    assert "'superagent-browserctl' x1" in prefixed
    assert "1 new-shape row(s) missing one of id/ts/skill" in incomplete
    assert "(row index 7)" in incomplete


def test_check_interaction_log_clean_rows_emit_nothing() -> None:
    from superagent.tools.validate import check_interaction_log

    rows = [
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "migrate", "action": "apply", "summary": "ok"},
        {"timestamp": "2026-08-01T09:00:00-07:00", "type": "note", "subject": "s"},
    ]
    assert check_interaction_log(rows, {"migrate"}) == []
    assert check_interaction_log("not-a-list", {"migrate"}) == []


def test_validate_skill_drift_is_warning_not_error(
    framework_dir: Path, initialized_workspace: Path, capsys
) -> None:
    """Drifted skill values print WARN lines but the run still exits 0."""
    from superagent.tools.validate import main as validate_main

    _write_ilog(initialized_workspace, [
        {"id": "ilog-2026-09-01-001", "ts": "2026-09-01T10:00:00-07:00",
         "skill": "superagent-migrate", "action": "apply", "summary": "ok",
         "related_domain": None, "related_project": None, "related_asset": None,
         "related_account": None, "action_items": [], "ingestion_log_ref": None},
        {"id": "ilog-2026-09-01-002", "ts": "2026-09-01T10:01:00-07:00",
         "skill": "free text (qualifier)", "action": "x", "summary": "ok",
         "related_domain": None, "related_project": None, "related_asset": None,
         "related_account": None, "action_items": [], "ingestion_log_ref": None},
    ])
    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK     interaction-log.yaml" in out
    assert "WARN   interaction-log.yaml" in out
    assert "not a skill stem" in out
    assert "legacy prefixed" in out
    assert "2 warning(s)" in out


def test_validate_fresh_interaction_log_has_no_warnings(
    framework_dir: Path, initialized_workspace: Path, capsys
) -> None:
    """The template placeholder row must not trip the soft checks."""
    from superagent.tools.validate import main as validate_main

    rc = validate_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" not in out
