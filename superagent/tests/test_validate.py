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
