---
name: superagent-world
description: >-
  Query and rebuild the world graph (`_memory/world.yaml`). Answers
  "show me everything connected to X" in one cheap query instead of
  scanning multiple entity files.
triggers:
  - world
  - related
  - show me everything connected to
  - what is linked to
  - graph
  - rebuild graph
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent world skill

Implements superagent/docs/_internal/ideas-better-structure.md item #3 + superagent/docs/_internal/perf-improvement-ideas.md BB-4. Engine: `tools/world.py`.

## 1. Branch on intent

### Related (default)

User said "what's connected to X":

1. Resolve `X` to a canonical handle (use `tools/handles.py parse` semantics — accepts canonical `<kind>:<slug>` and legacy bare ids).
2. Run `uv run python -m superagent.tools.world related <handle> --depth 1` (or 2 if the user said "show me everything" rather than "directly").
3. Group neighbors by `kind`; render compact list.

Example output:

```
## Related to contact:dr-smith-dentist (depth 1)

### appointments (3)
- appointment:20260512-cleaning — 2026-05-12 cleaning
- appointment:20251115-cleaning — 2025-11-15 cleaning
- appointment:20250515-cleaning — 2025-05-15 cleaning

### domains (1)
- domain:health

### tags (2)
- tag:provider
- tag:dental
```

### Stats

`world stats` — node + edge counts by kind. Useful for sanity-checking after schema changes.

```
uv run python -m superagent.tools.world stats
```

### Rebuild

User said "rebuild the graph" / after a major data import:

```
uv run python -m superagent.tools.world rebuild
```

This walks every entity-shape file and reconstructs nodes + edges from scratch. The graph is *derived* state — safe to delete and rebuild.

### Validate

`world validate` — check for edges pointing at non-existent nodes. Returns warnings only; the rebuilder fixes them.

## 2. Maintenance contract

Per `contracts/world-graph.md`, every entity-mutating skill SHOULD:

1. Call `tools/world.py.ensure_node(workspace, handle, kind, path, label, tags)` after creating an entity row.
2. Call `tools/world.py.ensure_edge(workspace, from, to, kind, evidence)` after creating each cross-reference.

If a skill doesn't (legacy / oversight), the next `world rebuild` corrects the graph. The graph is a CACHE of the entity files, not a source of truth.

## 3. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "world (<sub-action>)"
  summary: "Queried/rebuilt graph: <result-summary>"
```
