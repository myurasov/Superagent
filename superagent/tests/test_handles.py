# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/handles.py` (operational-handles parser)."""
from __future__ import annotations


def test_parse_canonical() -> None:
    from superagent.tools.handles import Handle, parse

    h = parse("contact:dr-smith-dentist")
    assert h == Handle(kind="contact", slug="dr-smith-dentist")
    assert str(h) == "contact:dr-smith-dentist"


def test_parse_legacy_prefix() -> None:
    from superagent.tools.handles import parse

    h = parse("contact-dr-smith-dentist")
    assert h.kind == "contact"
    assert h.slug == "dr-smith-dentist"


def test_parse_task_legacy() -> None:
    from superagent.tools.handles import parse

    h = parse("task-20260428-001")
    assert h.kind == "task"
    assert h.slug == "20260428-001"


def test_parse_unknown_falls_back_to_other() -> None:
    from superagent.tools.handles import parse

    h = parse("just-a-bare-thing")
    assert h.kind == "other"
    assert h.slug == "just-a-bare-thing"


def test_format_round_trip() -> None:
    from superagent.tools.handles import format, parse

    s = format("project", "tax-2026")
    assert s == "project:tax-2026"
    h = parse(s)
    assert h.kind == "project" and h.slug == "tax-2026"


def test_is_handle() -> None:
    from superagent.tools.handles import is_handle

    assert is_handle("contact:alice")
    assert not is_handle("contact_alice")
    assert not is_handle("")
    assert not is_handle("contact:")  # missing slug
    assert not is_handle(":alice")    # missing kind


def test_slug_for() -> None:
    from superagent.tools.handles import slug_for

    assert slug_for("contact", "Dr. Smith — Dentist") == "dr-smith-dentist"
    assert slug_for("project", "Tax 2026") == "tax-2026"
    assert slug_for("asset", "Blue Camry  (2018)!") == "blue-camry-2018"


def test_collect_handles_in() -> None:
    from superagent.tools.handles import collect_handles_in

    text = (
        "Refer to contact:alice and project:tax-2026; also see "
        "domain:health and contact:alice (duplicate)."
    )
    out = sorted(str(h) for h in collect_handles_in(text))
    assert out == ["contact:alice", "domain:health", "project:tax-2026"]


def test_filter_kind() -> None:
    from superagent.tools.handles import filter_kind, parse

    handles = [parse("contact:a"), parse("project:b"), parse("contact:c")]
    out = sorted(str(h) for h in filter_kind(handles, "contact"))
    assert out == ["contact:a", "contact:c"]


def test_watch_kind_is_canonical() -> None:
    """0.19.0: a watcher (`Sources/Watchlist/<id>.ref.md`) has the handle `watch:<id>`."""
    from superagent.tools.handles import KINDS, is_handle, parse

    assert "watch" in KINDS
    h = parse("watch:solar-permit")
    assert h.kind == "watch" and h.slug == "solar-permit"
    assert is_handle("watch:solar-permit")


def test_agent_kind_is_canonical_and_keeps_its_colon() -> None:
    """0.25.0: an agent has the handle `agent:<instance name>` (contracts/agents.md § 4)."""
    from superagent.tools.handles import KINDS, collect_handles_in, format, is_handle, parse

    assert "agent" in KINDS
    h = parse("agent:updater")
    assert h.kind == "agent" and h.slug == "updater"
    project = parse("agent:project:tax-2026")
    assert project.kind == "agent" and project.slug == "project:tax-2026"
    assert str(project) == format("agent", "project:tax-2026") == "agent:project:tax-2026"
    assert is_handle("agent:project:tax-2026")
    found = [str(x) for x in collect_handles_in(
        "ask agent:project:tax-2026 or agent:updater; see project:tax-2026 and source:a:b")]
    assert found == ["agent:project:tax-2026", "agent:updater", "project:tax-2026", "source:a"], (
        "only the agent kind takes further segments")
