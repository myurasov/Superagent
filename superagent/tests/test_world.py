# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/world.py` (entity graph)."""
from __future__ import annotations

from pathlib import Path


def test_rebuild_produces_nodes_for_default_domains(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild

    data = rebuild(initialized_workspace)
    assert "nodes" in data and "edges" in data
    domain_nodes = [n for n in data["nodes"] if n.get("kind") == "domain"]
    assert len(domain_nodes) == 13  # the 13 default domains
    handles = sorted(n["id"] for n in domain_nodes)
    assert "domain:health" in handles
    assert "domain:finances" in handles
    assert "domain:assets" in handles
    assert "domain:business" in handles
    assert "domain:education" in handles


def test_related_to_returns_node_and_neighbors(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild, related_to

    rebuild(initialized_workspace)
    result = related_to(initialized_workspace, "domain:health")
    assert result["node"]["kind"] == "domain"
    # No domain has neighbors in a fresh workspace, but the node exists.
    assert isinstance(result["neighbors"], list)
    assert isinstance(result["edges"], list)


def test_ensure_node_and_edge_idempotent(initialized_workspace: Path) -> None:
    from superagent.tools.world import ensure_edge, ensure_node, load_world

    ensure_node(initialized_workspace, "contact:alice", "contact",
                path="_memory/contacts.yaml#alice", label="Alice")
    ensure_node(initialized_workspace, "contact:alice", "contact",
                path="_memory/contacts.yaml#alice", label="Alice Smith")
    ensure_edge(initialized_workspace, "contact:alice", "domain:health",
                "rolodex_member", evidence="manual")
    ensure_edge(initialized_workspace, "contact:alice", "domain:health",
                "rolodex_member", evidence="manual")
    data = load_world(initialized_workspace)
    contacts = [n for n in data["nodes"] if n["id"] == "contact:alice"]
    assert len(contacts) == 1
    assert contacts[0]["label"] in ("Alice", "Alice Smith")
    rolodex_edges = [
        e for e in data["edges"]
        if e.get("from") == "contact:alice"
        and e.get("to") == "domain:health"
        and e.get("kind") == "rolodex_member"
    ]
    assert len(rolodex_edges) == 1


def test_validate_returns_no_warnings_after_rebuild(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild, validate

    rebuild(initialized_workspace)
    warnings = validate(initialized_workspace)
    # Default workspace has no broken edges; rebuild produces only consistent state.
    assert warnings == []


def test_stats_reports_counts(initialized_workspace: Path) -> None:
    from superagent.tools.world import rebuild, stats

    rebuild(initialized_workspace)
    s = stats(initialized_workspace)
    assert s["node_total"] >= 13  # at least the 13 default domains
    assert "domain" in s["by_node_kind"]


def test_query_warns_on_stderr_when_graph_stale(
    initialized_workspace: Path, capsys,
) -> None:
    import datetime as dt

    from superagent.tools.world import (
        load_world,
        main,
        rebuild,
        save_yaml,
        world_path,
    )

    rebuild(initialized_workspace)
    main(["--workspace", str(initialized_workspace), "related", "domain:health"])
    assert "last rebuilt" not in capsys.readouterr().err  # fresh: no warning

    data = load_world(initialized_workspace)
    old = (dt.datetime.now().astimezone() - dt.timedelta(days=45))
    data["last_rebuild"] = old.isoformat(timespec="seconds")
    data["last_updated"] = old.isoformat(timespec="seconds")
    save_yaml(world_path(initialized_workspace), data)
    main(["--workspace", str(initialized_workspace), "related", "domain:health"])
    assert "last rebuilt 45 days ago" in capsys.readouterr().err
