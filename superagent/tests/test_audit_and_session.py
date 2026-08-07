# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/audit.py`."""
from __future__ import annotations

from pathlib import Path


def test_audit_record_and_read(initialized_workspace: Path) -> None:
    from superagent.tools.audit import history_path, read_history, record_change

    target = initialized_workspace / "_memory" / "contacts.yaml"
    written = record_change(
        initialized_workspace, target, kind="create",
        row_id="contact:alice", old=None,
        new={"id": "contact:alice", "name": "Alice"},
        who="user", source="add-contact", note="initial create",
    )
    assert written is True
    h = history_path(target)
    assert h.exists()
    rows = read_history(target, "contact:alice")
    assert len(rows) == 1
    assert rows[0]["kind"] == "create"
    assert rows[0]["row_id"] == "contact:alice"


def test_audit_skips_when_disabled(initialized_workspace: Path) -> None:
    import yaml

    from superagent.tools.audit import history_path, record_change

    cfg_path = initialized_workspace / "_memory" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg.setdefault("preferences", {}).setdefault("audit", {})["enabled"] = False
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    target = initialized_workspace / "_memory" / "tags.yaml"
    written = record_change(
        initialized_workspace, target, kind="create",
        row_id="tag:test", old=None, new={"id": "tag:test"},
    )
    assert written is False
    assert not history_path(target).exists()


def test_audit_skips_listed_files(initialized_workspace: Path) -> None:
    from superagent.tools.audit import history_path, record_change

    target = initialized_workspace / "_memory" / "interaction-log.yaml"
    written = record_change(
        initialized_workspace, target, kind="create",
        row_id="evt:test", old=None, new={"id": "evt:test"},
    )
    assert written is False
    assert not history_path(target).exists()



