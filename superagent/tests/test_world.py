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


# --- helpers for the entity-file fixtures below --------------------------


def _append_rows(workspace: Path, fname: str, list_key: str,
                 rows: list[dict]) -> None:
    """Append synthetic rows to `<workspace>/_memory/<fname>.<list_key>[]`."""
    from superagent.tools.world import load_yaml, save_yaml

    path = workspace / "_memory" / fname
    data = load_yaml(path) or {}
    data.setdefault(list_key, [])
    data[list_key] = (data[list_key] or []) + rows
    save_yaml(path, data)


def _edges(data: dict, **match: str) -> list[dict]:
    return [
        e for e in data["edges"]
        if all(e.get(k) == v for k, v in match.items())
    ]


# --- contact-ref gating (free text must not become contact: handles) ------


def test_prose_stakeholder_and_provider_do_not_become_contact_edges(
    initialized_workspace: Path, capsys,
) -> None:
    from superagent.tools.world import rebuild, validate

    _append_rows(initialized_workspace, "contacts.yaml", "contacts", [
        {"id": "agent-jane", "name": "Jane (agent)"},
    ])
    _append_rows(initialized_workspace, "projects-index.yaml", "projects", [
        {
            "id": "roof-claim", "name": "Roof claim", "status": "active",
            "stakeholders": [
                "Jane Doe (claims adjuster) 555-0100",   # prose -> skipped
                "someone@example.com",                  # prose -> skipped
                "agent-jane",                           # slug -> contact:agent-jane
                "contact:agent-jane",                   # explicit handle -> as-is
            ],
            "primary_contacts": ["Dr. Somebody, DDS"],  # prose -> skipped
        },
    ])
    _append_rows(initialized_workspace, "appointments.yaml", "appointments", [
        {"id": "20260910-cleaning", "title": "Cleaning",
         "provider": "Smile Dental — Main St"},        # prose -> skipped
    ])

    data = rebuild(initialized_workspace)
    err = capsys.readouterr().err

    stake = _edges(data, kind="stakeholder", **{"from": "project:roof-claim"})
    assert [e["to"] for e in stake] == ["contact:agent-jane", "contact:agent-jane"]
    assert _edges(data, kind="rolodex_member") == []
    assert _edges(data, kind="provider") == []
    bogus = [n["id"] for n in data["nodes"]
             if n["id"].startswith("contact:") and " " in n["id"]]
    assert bogus == []
    assert validate(initialized_workspace) == []

    # Exactly ONE aggregated stderr summary, counting all 4 skipped refs.
    summary_lines = [ln for ln in err.splitlines() if "free-text contact ref" in ln]
    assert len(summary_lines) == 1
    assert "skipped 4 free-text contact ref(s)" in summary_lines[0]
    assert "projects-index.yaml: 3" in summary_lines[0]
    assert "appointments.yaml: 1" in summary_lines[0]


def test_slug_gate_applies_only_to_contact_typed_refs(
    initialized_workspace: Path, capsys,
) -> None:
    """Bill / asset ids are not slug-constrained (uppercase markers exist)."""
    from superagent.tools.world import rebuild

    _append_rows(initialized_workspace, "bills.yaml", "bills", [
        {"id": "water-2026Q3", "name": "Water Q3",
         "related_asset": "Cabin-Lot-B", "pay_from_account": "Main-Checking"},
    ])
    data = rebuild(initialized_workspace)
    assert _edges(data, **{"from": "bill:water-2026Q3", "to": "asset:Cabin-Lot-B"})
    assert _edges(data, **{"from": "bill:water-2026Q3", "to": "account:Main-Checking"})
    assert "free-text contact ref" not in capsys.readouterr().err


def test_no_stderr_summary_when_nothing_skipped(
    initialized_workspace: Path, capsys,
) -> None:
    from superagent.tools.world import rebuild

    rebuild(initialized_workspace)
    assert capsys.readouterr().err == ""


# --- for_member sentinels --------------------------------------------------


def test_for_member_sentinels_are_skipped_case_insensitively(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild, validate

    _append_rows(initialized_workspace, "contacts.yaml", "contacts", [
        {"id": "kid-one", "name": "Kid One"},
    ])
    _append_rows(initialized_workspace, "important-dates.yaml", "dates", [
        {"id": "reg-renewal", "title": "Registration renewal", "for_member": "self"},
        {"id": "trash-day", "title": "Trash day", "for_member": "Household"},
        {"id": "leap-day", "title": "Leap day", "for_member": "WORLD"},
        {"id": "kid-bday", "title": "Birthday", "for_member": "kid-one"},
    ])
    _append_rows(initialized_workspace, "documents-index.yaml", "documents", [
        {"id": "doc-passport", "title": "Passport", "for_member": " self "},
    ])

    data = rebuild(initialized_workspace)
    fm = _edges(data, kind="for_member")
    assert [(e["from"], e["to"]) for e in fm] == [
        ("important_date:kid-bday", "contact:kid-one"),
    ]
    assert not any(n["id"] in ("contact:self", "contact:household", "contact:world",
                               "contact:Household", "contact:WORLD")
                   for n in data["nodes"])
    assert validate(initialized_workspace) == []


# --- archived projects stay resolvable ------------------------------------


def test_archived_projects_are_nodes_tagged_archived(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild, related_to, validate

    _append_rows(initialized_workspace, "projects-index.yaml", "projects", [
        {"id": "kitchen-reno", "name": "Kitchen reno", "status": "active",
         "related_domains": ["home"], "related_domain": "home"},
    ])
    _append_rows(initialized_workspace, "projects-index.yaml", "archived", [
        {"id": "old-tax-year", "name": "Old tax year", "status": "archived",
         "related_domain": "finances", "tags": ["taxes"]},
    ])

    data = rebuild(initialized_workspace)
    by_id = {n["id"]: n for n in data["nodes"]}
    assert "project:kitchen-reno" in by_id
    assert "archived" not in by_id["project:kitchen-reno"]["tags"]
    archived = by_id["project:old-tax-year"]
    assert archived["kind"] == "project"
    assert archived["label"] == "Old tax year"
    assert archived["path"] == "_memory/projects-index.yaml#old-tax-year"
    assert "archived" in archived["tags"]
    assert "taxes" in archived["tags"]  # row tags preserved alongside the marker
    # Edges from the archived row are still emitted, so `related` keeps working.
    assert _edges(data, **{"from": "project:old-tax-year", "to": "domain:finances"})
    neighbors = {n["id"] for n in related_to(initialized_workspace,
                                             "project:old-tax-year")["neighbors"]}
    assert "domain:finances" in neighbors
    assert validate(initialized_workspace) == []


# --- project `parent` names a project, not a domain ------------------------


def test_project_parent_resolves_to_umbrella_project(
    initialized_workspace: Path,
) -> None:
    """`parent` on a projects-index row is a project id (per the template).

    The child is listed BEFORE its umbrella so resolution cannot depend on row
    order; an archived umbrella must resolve too; a parent that matches no
    project id still falls back to `domain:<slug>`; domains-index is untouched.
    """
    from superagent.tools.world import rebuild, validate

    _append_rows(initialized_workspace, "projects-index.yaml", "projects", [
        {"id": "reno-plumbing", "name": "Reno: plumbing", "status": "active",
         "parent": "house-reno"},
        {"id": "house-reno", "name": "House reno", "status": "active", "parent": None},
        {"id": "garden-beds", "name": "Garden beds", "status": "active",
         "parent": "home"},                            # no such project -> domain
    ])
    _append_rows(initialized_workspace, "projects-index.yaml", "archived", [
        {"id": "old-move", "name": "Old move", "status": "archived"},
        {"id": "old-move-packing", "name": "Old move: packing", "status": "archived",
         "parent": "old-move"},
    ])
    _append_rows(initialized_workspace, "domains-index.yaml", "domains", [
        {"id": "home-garage", "name": "Garage", "parent": "home"},
    ])

    data = rebuild(initialized_workspace)
    lives_under = {(e["from"], e["to"]) for e in _edges(data, kind="lives_under")}
    assert lives_under == {
        ("project:reno-plumbing", "project:house-reno"),
        ("project:old-move-packing", "project:old-move"),
        ("project:garden-beds", "domain:home"),
        ("domain:home-garage", "domain:home"),
    }
    node_ids = {n["id"] for n in data["nodes"]}
    assert "domain:house-reno" not in node_ids
    assert "domain:old-move" not in node_ids
    assert validate(initialized_workspace) == []


# --- accounts-index linked_accounts / linked_assets -----------------------


def test_linked_accounts_and_linked_assets_emit_edges(
    initialized_workspace: Path,
) -> None:
    from superagent.tools.world import rebuild, validate

    _append_rows(initialized_workspace, "assets-index.yaml", "assets", [
        {"id": "car-blue-sedan", "name": "Blue sedan"},
        {"id": "car-red-wagon", "name": "Red wagon"},
    ])
    _append_rows(initialized_workspace, "accounts-index.yaml", "accounts", [
        {"id": "main-checking", "name": "Main checking", "kind": "checking"},
        {"id": "toll-transponder", "name": "Toll transponder", "kind": "other",
         "linked_assets": ["car-blue-sedan", "car-red-wagon"],
         "linked_accounts": [
             {"account": "main-checking",
              "relationship": "auto_replenish_payment_method",
              "since": "2026-01-01", "notes": ""},
             "asset:car-blue-sedan",   # explicit handle string is accepted as-is
             {"relationship": "orphan-without-account-key"},  # ignored
             42,                                             # ignored
         ]},
    ])

    data = rebuild(initialized_workspace)
    linked = _edges(data, kind="linked_account", **{"from": "account:toll-transponder"})
    assert sorted(e["to"] for e in linked) == ["account:main-checking", "asset:car-blue-sedan"]
    assert linked[0]["evidence"] == "accounts-index.yaml.<toll-transponder>.linked_accounts"
    assets = _edges(data, kind="related_asset", **{"from": "account:toll-transponder"})
    assert sorted(e["to"] for e in assets) == ["asset:car-blue-sedan", "asset:car-red-wagon"]
    assert validate(initialized_workspace) == []


def test_accounts_template_documents_linked_assets_and_accounts(
    framework_dir: Path,
) -> None:
    import yaml

    path = framework_dir / "templates" / "memory" / "accounts-index.yaml"
    text = path.read_text()
    assert "linked_assets" in text and "linked_accounts" in text
    for key in ("account", "relationship", "since", "notes"):
        assert f"#     {key}" in text, f"linked_accounts sub-field {key} undocumented"
    example = (yaml.safe_load(text)["accounts"] or [])[0]
    assert example["linked_assets"] == []
    assert example["linked_accounts"] == []
