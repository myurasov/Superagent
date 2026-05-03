# World Graph Contract

<!-- Migrated from `procedures.md § 24`. Citation form: `contracts/world-graph.md`. -->

Implements ideas-better-structure.md item #3 + perf-improvement-ideas.md BB-4. Backed by `_memory/world.yaml` and `tools/world.py`.

**The graph is DERIVED state**. It can be fully rebuilt from entity files at any time via `tools/world.py rebuild`. Skills SHOULD update it incrementally; if they don't, the next rebuild fixes drift.

**Every entity-mutating skill SHOULD**:

1. Call `tools/world.py.ensure_node(workspace, handle, kind, path, label, tags)` after creating an entity row.
2. Call `tools/world.py.ensure_edge(workspace, from_h, to_h, kind, evidence)` for every cross-reference written.

**Edge kinds** (canonical; see `templates/memory/world.yaml`):
`pay_from`, `provider`, `scoped`, `related_domain`, `related_project`, `related_asset`, `owns`, `holds`, `stakeholder`, `rolodex_member`, `covers`, `lives_under`, `tagged`, `supersedes`, `derives_from`, `triggered_by`, `affects`.

**Query**: `tools/world.py related <handle> [--depth N]` returns all neighbors. The `world` skill is the user-facing front-end.

**Validation**: `tools/world.py validate` walks edges and warns about references to missing nodes. `supertailor-review` includes it in its hygiene pass.
