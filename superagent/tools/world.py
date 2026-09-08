#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""World-model entity graph — query and rebuild.

Implements superagent/docs/_internal/ideas-better-structure.md item #3 + superagent/docs/_internal/perf-improvement-ideas.md BB-4.

The graph is stored at `_memory/world.yaml` (per the template). Rebuild
scans every entity-shape file and reconstructs nodes + edges from scratch.
Query traverses the graph for "show me everything connected to X".

This module is the canonical writer / reader. Skills should call:
    from superagent.tools.world import rebuild, related_to, ensure_node, ensure_edge

CLI:
    uv run python -m superagent.tools.world rebuild           # full rebuild from entity files
    uv run python -m superagent.tools.world related <handle>  # one-hop neighbors
    uv run python -m superagent.tools.world expand <handle> --depth 2
    uv run python -m superagent.tools.world stats             # node/edge counts by kind
    uv run python -m superagent.tools.world validate          # check graph vs entities
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def world_path(workspace: Path) -> Path:
    return workspace / "_memory" / "world.yaml"


def load_world(workspace: Path) -> dict[str, Any]:
    data = load_yaml(world_path(workspace)) or {}
    data.setdefault("schema_version", 1)
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


STALE_AFTER_DAYS = 30


def warn_if_stale(workspace: Path) -> None:
    """Print a one-line stderr warning when the graph is stale (>30 days).

    Freshness comes from the embedded `last_rebuild` timestamp (falling back
    to `last_updated`, then to the file's mtime). Warning only — queries
    still answer from the stale graph.
    """
    path = world_path(workspace)
    if not path.exists():
        return
    data = load_yaml(path) or {}
    stamp = data.get("last_rebuild") or data.get("last_updated")
    when: dt.datetime | None = None
    if isinstance(stamp, dt.datetime):
        when = stamp
    elif isinstance(stamp, str):
        try:
            when = dt.datetime.fromisoformat(stamp)
        except ValueError:
            when = None
    if when is None:
        try:
            when = dt.datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return
    if when.tzinfo is None:
        when = when.astimezone()
    age_days = (dt.datetime.now().astimezone() - when).days
    if age_days > STALE_AFTER_DAYS:
        print(f"warn: world.yaml last rebuilt {age_days} days ago "
              f"(> {STALE_AFTER_DAYS}) — consider "
              f"'uv run python -m superagent.tools.world rebuild'",
              file=sys.stderr)


def normalize_handle(value: str | None, kind_default: str = "other") -> str | None:
    """Normalize an id-like string to a canonical handle, returning None if empty."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if ":" in raw:
        return raw
    return f"{kind_default}:{raw}"


# Same slug class `tools/handles.py` accepts: lowercase, digits, `_`, `-`.
# Applied ONLY to contact-typed refs — other kinds (e.g. bill ids carrying an
# uppercase timestamp marker) are deliberately left ungated.
_CONTACT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# `for_member` sentinels documented in the important-dates / documents-index
# templates. They are not contacts; matched case-insensitively.
_FOR_MEMBER_SENTINELS = frozenset({"self", "household", "world"})


def normalize_contact_ref(value: Any) -> str | None:
    """Return a canonical contact handle for `value`, or None when it is prose.

    Accepts either an explicit handle (contains `:`) or a bare slug in the
    class `handles.py` uses. Free-text names, phone numbers, emails and the
    like return None so callers can skip (and count) them rather than emit a
    dangling `contact:<free text>` edge.
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if ":" in raw:
        return raw
    if _CONTACT_SLUG_RE.match(raw):
        return f"contact:{raw}"
    return None


def is_for_member_sentinel(value: Any) -> bool:
    """True when a `for_member` value is one of the self/household/world sentinels."""
    return isinstance(value, str) and value.strip().lower() in _FOR_MEMBER_SENTINELS


def collect_nodes_edges(workspace: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Walk every entity-shape YAML and produce nodes + edges from scratch.

    Free-text contact refs (stakeholders / primary_contacts / provider-style
    fields whose value is not a handle or slug) are skipped; one aggregated
    summary line goes to stderr when any were dropped.
    """
    memory = workspace / "_memory"
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    skipped_contact_refs: dict[str, int] = defaultdict(int)
    # A project's `parent` names a parent PROJECT (templates/memory/projects-index.yaml),
    # not a domain. Pre-collect every project id so a child row resolves its umbrella
    # regardless of row order; `_kind_for_field` keeps `domain` as the fallback.
    project_ids = _project_ids(memory)

    def add_node(handle: str, kind: str, path: str, label: str = "",
                 tags: list[str] | None = None) -> None:
        h = normalize_handle(handle, kind)
        if not h:
            return
        node = nodes.get(h)
        if node is None:
            nodes[h] = {
                "id": h,
                "kind": kind,
                "path": path,
                "label": label or h.partition(":")[2],
                "tags": tags or [],
            }
        else:
            if not node.get("label") and label:
                node["label"] = label
            if tags:
                merged = list(dict.fromkeys((node.get("tags") or []) + tags))
                node["tags"] = merged

    def add_edge(from_h: str | None, to_h: str | None, kind: str, evidence: str) -> None:
        if not from_h or not to_h:
            return
        edges.append({"from": from_h, "to": to_h, "kind": kind, "evidence": evidence})

    def resolve_target(fname: str, field: str, value: Any) -> str | None:
        """Canonical target handle for a cross-ref field value, or None to skip.

        Contact-typed fields go through `normalize_contact_ref` so prose never
        becomes an edge endpoint; every other kind keeps the permissive
        `normalize_handle` path (bill / asset ids are not slug-constrained).
        """
        target_kind = _kind_for_field(field)
        if field == "for_member" and is_for_member_sentinel(value):
            return None
        if (field == "parent" and fname == "projects-index.yaml"
                and isinstance(value, str) and value.strip() in project_ids):
            return f"project:{value.strip()}"
        if target_kind == "contact":
            target = normalize_contact_ref(value)
            if target is None and isinstance(value, str) and value.strip():
                skipped_contact_refs[fname] += 1
            return target
        if value is None or isinstance(value, (dict, list)):
            return None
        return normalize_handle(str(value), target_kind)

    edge_fields = (
        ("related_domain", "scoped"),
        ("related_project", "scoped"),
        ("related_asset", "related_asset"),
        ("related_account", "pay_from"),
        ("pay_from_account", "pay_from"),
        ("provider", "provider"),
        ("contact", "contact"),
        ("for_member", "for_member"),
        ("parent", "lives_under"),
        ("workflow", "instantiated_from"),
        ("ordered_by", "ordered_by"),
        ("primary_care", "provider"),
        ("pharmacy", "provider"),
        ("prescribed_by", "provider"),
        ("asset", "related_asset"),
        ("account", "pay_from"),
    )
    # (row field, edge kind, kind of the target node). Values may be bare id
    # strings or {<target_kind>: <id>, ...} objects (e.g. accounts-index
    # `linked_accounts` rows carry relationship metadata alongside the id).
    linked_fields = (
        ("linked_accounts", "linked_account", "account"),
        ("linked_assets", "related_asset", "asset"),
    )

    def process_row(fname: str, kind: str, id_field: str, label_field: str,
                    row: dict[str, Any], extra_tags: list[str]) -> None:
        rid = row.get(id_field)
        if not rid:
            return
        handle = row.get("handle") or f"{kind}:{rid}"
        label = row.get(label_field) or rid
        tags = row.get("tags") or []
        tags = list(tags) if isinstance(tags, list) else []
        add_node(handle, kind, f"_memory/{fname}#{rid}",
                 label=str(label), tags=tags + extra_tags)
        for field, kind_label in edge_fields:
            v = row.get(field)
            if v in (None, "", []):
                continue
            items = v if isinstance(v, list) else [v]
            for item in items:
                target = resolve_target(fname, field, item)
                if target:
                    add_edge(handle, target, kind_label,
                             f"{fname}.<{rid}>.{field}")
        for field, kind_label, target_kind in linked_fields:
            for item in (row.get(field) or []):
                ref = item.get(target_kind) if isinstance(item, dict) else item
                target = normalize_handle(ref, target_kind) if isinstance(ref, str) else None
                if target:
                    add_edge(handle, target, kind_label, f"{fname}.<{rid}>.{field}")
        for t in tags:
            if isinstance(t, str) and t:
                # Materialize the tag node implicitly so the validate
                # pass doesn't see the edge as orphaned.
                add_node(f"tag:{t}", "tag",
                         path=f"_memory/tags.yaml#{t}",
                         label=t, tags=[])
                add_edge(handle, f"tag:{t}", "tagged",
                         f"{fname}.<{rid}>.tags")
        for field, kind_label in (("stakeholders", "stakeholder"),
                                  ("primary_contacts", "rolodex_member")):
            for ref in (row.get(field) or []):
                target = normalize_contact_ref(ref)
                if target:
                    add_edge(handle, target, kind_label, f"{fname}.<{rid}>.{field}")
                elif isinstance(ref, str) and ref.strip():
                    skipped_contact_refs[fname] += 1

    # (file, [(list key, extra node tags), ...], kind, id field, label field).
    # projects-index keeps completed-then-archived rows under a sibling
    # `archived` list (contracts/projects.md § lifecycle); they stay resolvable
    # handles, tagged `archived`.
    spec = [
        ("domains-index.yaml", [("domains", [])], "domain", "id", "name"),
        ("projects-index.yaml", [("projects", []), ("archived", ["archived"])],
         "project", "id", "name"),
        ("contacts.yaml", [("contacts", [])], "contact", "id", "name"),
        ("assets-index.yaml", [("assets", [])], "asset", "id", "name"),
        ("accounts-index.yaml", [("accounts", [])], "account", "id", "name"),
        ("bills.yaml", [("bills", [])], "bill", "id", "name"),
        ("subscriptions.yaml", [("subscriptions", [])], "subscription", "id", "name"),
        ("appointments.yaml", [("appointments", [])], "appointment", "id", "title"),
        ("important-dates.yaml", [("dates", [])], "important_date", "id", "title"),
        ("documents-index.yaml", [("documents", [])], "document", "id", "title"),
        ("sources-index.yaml", [("sources", [])], "source", "id", "title"),
        ("decisions.yaml", [("decisions", [])], "decision", "id", "decision"),
        ("tags.yaml", [("tags", [])], "tag", "id", "id"),
    ]
    for fname, list_keys, kind, id_field, label_field in spec:
        data = load_yaml(memory / fname) or {}
        if not isinstance(data, dict):
            continue
        for list_key, extra_tags in list_keys:
            for row in (data.get(list_key) or []):
                if isinstance(row, dict):
                    process_row(fname, kind, id_field, label_field, row, extra_tags)

    # Watchers (contracts/watchlist.md § 8.3). A sources-index row carrying a
    # `watch:` mapping is a `Sources/Watchlist/<id>.ref.md` watcher; besides
    # its `source:<row id>` node it gets a `watch:<id>` node (id = ref
    # filename stem) whose related_* fields become edges, plus an `indexed_as`
    # edge back to the source row. `ensure_edge` callers that add the same
    # edges between runs stay consistent with a full rebuild this way.
    sources_data = load_yaml(memory / "sources-index.yaml") or {}
    watch_rows = (sources_data.get("sources") or []) if isinstance(sources_data, dict) else []
    for row in watch_rows:
        if not isinstance(row, dict) or not isinstance(row.get("watch"), dict):
            continue
        wid = watch_id_for_path(row.get("path"))
        if not wid:
            continue
        handle = f"watch:{wid}"
        rid = row.get("id")
        tags = row.get("tags") or []
        tags = [t for t in tags if isinstance(t, str) and t] if isinstance(tags, list) else []
        add_node(handle, "watch", str(row.get("path") or ""),
                 label=str(row.get("title") or wid), tags=tags)
        for field in ("related_domain", "related_project", "related_asset", "related_account"):
            v = row.get(field)
            if isinstance(v, str) and v.strip():
                kind_label = dict(edge_fields)[field]
                add_edge(handle, normalize_handle(v, _kind_for_field(field)), kind_label,
                         f"sources-index.yaml.<{rid}>.{field}")
        if rid:
            add_edge(handle, f"source:{rid}", "indexed_as", f"sources-index.yaml.<{rid}>.watch")

    if skipped_contact_refs:
        total = sum(skipped_contact_refs.values())
        per_file = ", ".join(f"{f}: {n}" for f, n in sorted(skipped_contact_refs.items()))
        print(f"warn: skipped {total} free-text contact ref(s) ({per_file}); "
              f"use contact ids (contact:<slug>) per contracts/operational-handles.md",
              file=sys.stderr)

    return list(nodes.values()), edges


def watch_id_for_path(rel_path: Any) -> str | None:
    """Watcher id for a registry ref path: the filename minus `.ref.md` / `.ref.txt`."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        return None
    from superagent.tools.sources_index import ref_stem
    return ref_stem(rel_path)


def _project_ids(memory: Path) -> set[str]:
    """Every project id in projects-index.yaml, live (`projects`) and `archived`."""
    data = load_yaml(memory / "projects-index.yaml") or {}
    if not isinstance(data, dict):
        return set()
    return {
        str(row["id"])
        for list_key in ("projects", "archived")
        for row in (data.get(list_key) or [])
        if isinstance(row, dict) and row.get("id")
    }


def _kind_for_field(field: str) -> str:
    """Heuristic: which entity kind does this cross-reference field point at?"""
    return {
        "related_domain": "domain",
        "related_project": "project",
        "related_asset": "asset",
        "related_account": "account",
        "pay_from_account": "account",
        "asset": "asset",
        "account": "account",
        "provider": "contact",
        "contact": "contact",
        "primary_care": "contact",
        "pharmacy": "contact",
        "prescribed_by": "contact",
        "ordered_by": "contact",
        "parent": "domain",  # domains-index; projects-index resolves project ids first
        "workflow": "workflow",
        "for_member": "contact",
    }.get(field, "other")


def rebuild(workspace: Path) -> dict[str, Any]:
    """Regenerate world.yaml from scratch by walking every entity file."""
    nodes, edges = collect_nodes_edges(workspace)
    data = {
        "schema_version": 1,
        "last_updated": now_iso(),
        "last_rebuild": now_iso(),
        "nodes": nodes,
        "edges": edges,
    }
    save_yaml(world_path(workspace), data)
    return data


def related_to(workspace: Path, handle: str, depth: int = 1) -> dict[str, Any]:
    """Return nodes + edges within `depth` hops of `handle` (undirected)."""
    data = load_world(workspace)
    canonical = normalize_handle(handle)
    if not canonical:
        return {"node": None, "neighbors": [], "edges": []}
    by_id = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict)}
    out_edges = defaultdict(list)
    in_edges = defaultdict(list)
    for e in data.get("edges", []):
        if not isinstance(e, dict):
            continue
        out_edges[e.get("from", "")].append(e)
        in_edges[e.get("to", "")].append(e)

    visited: set[str] = {canonical}
    frontier: set[str] = {canonical}
    visited_edges: list[dict[str, Any]] = []
    for _ in range(max(1, depth)):
        next_frontier: set[str] = set()
        for h in frontier:
            for e in out_edges.get(h, []):
                visited_edges.append(e)
                target = e.get("to", "")
                if target and target not in visited:
                    visited.add(target)
                    next_frontier.add(target)
            for e in in_edges.get(h, []):
                visited_edges.append(e)
                source = e.get("from", "")
                if source and source not in visited:
                    visited.add(source)
                    next_frontier.add(source)
        frontier = next_frontier
        if not frontier:
            break
    neighbors = [by_id.get(v) for v in visited - {canonical}]
    neighbors = [n for n in neighbors if n is not None]
    return {
        "node": by_id.get(canonical, {"id": canonical, "kind": canonical.partition(":")[0]}),
        "neighbors": neighbors,
        "edges": visited_edges,
    }


def ensure_node(workspace: Path, handle: str, kind: str,
                path: str, label: str = "", tags: list[str] | None = None) -> None:
    """Idempotently add or update a node. Caller writes the file change."""
    data = load_world(workspace)
    canonical = normalize_handle(handle, kind) or handle
    by_id = {n["id"]: n for n in data["nodes"] if isinstance(n, dict)}
    if canonical in by_id:
        node = by_id[canonical]
        if label and not node.get("label"):
            node["label"] = label
        if tags:
            node["tags"] = list(dict.fromkeys((node.get("tags") or []) + tags))
    else:
        data["nodes"].append({
            "id": canonical,
            "kind": kind,
            "path": path,
            "label": label or canonical.partition(":")[2],
            "tags": tags or [],
        })
    data["last_updated"] = now_iso()
    save_yaml(world_path(workspace), data)


def ensure_edge(workspace: Path, from_h: str, to_h: str,
                kind: str, evidence: str = "") -> None:
    """Idempotently add an edge."""
    data = load_world(workspace)
    target = (from_h, to_h, kind)
    for e in data["edges"]:
        if not isinstance(e, dict):
            continue
        if (e.get("from"), e.get("to"), e.get("kind")) == target:
            if evidence and not e.get("evidence"):
                e["evidence"] = evidence
            data["last_updated"] = now_iso()
            save_yaml(world_path(workspace), data)
            return
    data["edges"].append({"from": from_h, "to": to_h, "kind": kind, "evidence": evidence})
    data["last_updated"] = now_iso()
    save_yaml(world_path(workspace), data)


def stats(workspace: Path) -> dict[str, Any]:
    data = load_world(workspace)
    by_node_kind: dict[str, int] = defaultdict(int)
    for n in data.get("nodes", []):
        if isinstance(n, dict):
            by_node_kind[n.get("kind", "")] += 1
    by_edge_kind: dict[str, int] = defaultdict(int)
    for e in data.get("edges", []):
        if isinstance(e, dict):
            by_edge_kind[e.get("kind", "")] += 1
    return {
        "node_total": sum(by_node_kind.values()),
        "edge_total": sum(by_edge_kind.values()),
        "by_node_kind": dict(by_node_kind),
        "by_edge_kind": dict(by_edge_kind),
        "last_rebuild": data.get("last_rebuild"),
        "last_updated": data.get("last_updated"),
    }


def validate(workspace: Path) -> list[str]:
    """Return a list of consistency-warning strings."""
    warnings: list[str] = []
    data = load_world(workspace)
    nodes = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and n.get("id")}
    for e in data.get("edges", []):
        if not isinstance(e, dict):
            continue
        if e.get("from") and e["from"] not in nodes:
            warnings.append(f"edge from unknown node: {e['from']}")
        if e.get("to") and e["to"] not in nodes:
            warnings.append(f"edge to unknown node: {e['to']}")
    return warnings


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="world")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rebuild", help="Regenerate world.yaml from entity files.")
    r = sub.add_parser("related", help="Show 1-hop neighbors of a handle.")
    r.add_argument("handle", type=str)
    r.add_argument("--depth", type=int, default=1)
    r.add_argument("--json", action="store_true")
    sub.add_parser("stats", help="Show graph statistics.")
    sub.add_parser("validate", help="Check graph vs entity files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1
    if args.cmd in ("related", "stats", "validate"):
        warn_if_stale(workspace)
    if args.cmd == "rebuild":
        data = rebuild(workspace)
        s = stats(workspace)
        print(f"rebuilt: {len(data['nodes'])} nodes, {len(data['edges'])} edges")
        print(json.dumps(s, indent=2, default=str))
        return 0
    if args.cmd == "related":
        result = related_to(workspace, args.handle, depth=args.depth)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0
        node = result.get("node") or {"id": args.handle, "kind": "?"}
        neighbors = result.get("neighbors", [])
        edges = result.get("edges", [])
        print(f"# Related to {node.get('id')} (kind: {node.get('kind')})")
        print(f"# {len(neighbors)} neighbor(s) within depth {args.depth}, "
              f"{len(edges)} edge(s) traversed\n")
        for n in neighbors:
            label = n.get("label") or n.get("id")
            print(f"  - {n.get('id')} [{n.get('kind')}] — {label}")
        return 0
    if args.cmd == "stats":
        print(json.dumps(stats(workspace), indent=2, default=str))
        return 0
    if args.cmd == "validate":
        warnings = validate(workspace)
        if not warnings:
            print("Graph consistent.")
            return 0
        for w in warnings:
            print(f"  warn: {w}")
        print(f"\n{len(warnings)} warning(s).")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
