# World Graph Contract

<!-- Citation form: `contracts/world-graph.md`. Store: `_memory/world.yaml` (template `templates/memory/world.yaml`). Engine: `tools/world.py`. User-facing front-end: `skills/world.md`. -->

## 1. What it is

`_memory/world.yaml` is the workspace's explicit entity-relationship index: one **node** per entity, keyed by operational handle (`<kind>:<slug>`, per `contracts/operational-handles.md`), and one directed **edge** per cross-reference between entities. It answers "show me everything connected to X" in one cheap query instead of scanning many entity files.

**The graph is DERIVED state.** It can be fully rebuilt from the entity-shape files (`contracts/memory-taxonomy.md`) at any time via `world rebuild`; it is a cache of the entity files, never a source of truth, and deleting it loses nothing. Skills SHOULD update it incrementally (§ 4); if they don't, the next rebuild fixes the drift.

## 2. Nodes

Each node carries `id` (handle), `kind`, `path` (workspace-relative locator of the row or file, e.g. `_memory/contacts.yaml#contact-dr-smith-dentist` or `Domains/Health/info.md`), `label`, and cached `tags`. Node kinds are exactly the kinds `rebuild` emits — one per entity file it walks, plus watchers: `domain | project | contact | asset | account | bill | subscription | appointment | important_date | document | source | decision | tag | watch`.

**Watchers.** Every watcher in the registry (`Sources/Watchlist/<name>.ref.md`, per `contracts/watchlist.md`) is indexed in `sources-index.yaml` as a row carrying a `watch:` mapping. Besides that row's `source:<row id>` node, the rebuilder emits a `watch:<id>` node — `id` = the ref filename stem lowercased (`tools/watchlist.py::id_from_stem`), `kind: watch`, `label` = the ref `title` — so a project's or domain's watchers appear among its neighbours.

## 3. Edges

Each edge carries `from`, `to` (handles), `kind`, and `evidence` (the field that produced it, e.g. `bills.yaml.<id>.pay_from_account`). The canonical kinds are exactly the ones `rebuild` derives; a skill-written edge of any other kind does not survive the next rebuild. Row fields map to edge kinds uniformly for every entity row, watchers included:

| Row field(s) | Edge kind | Target node |
|---|---|---|
| `domain`, `related_domain`, `related_project` | `scoped` | `domain:<id>` / `project:<id>` |
| `related_asset`, `asset`, `linked_assets[]` | `related_asset` | `asset:<id>` |
| `related_account`, `pay_from_account`, `account` | `pay_from` | `account:<id>` |
| `provider`, `primary_care`, `pharmacy`, `prescribed_by` | `provider` | `contact:<id>` |
| `contact` | `contact` | `contact:<id>` |
| `for_member` (a contact id; the `self` / `household` / `world` sentinels yield no edge) | `for_member` | `contact:<id>` |
| `ordered_by` | `ordered_by` | `contact:<id>` |
| `parent` | `lives_under` | `domain:<id>`, or `project:<id>` when the parent is a live or archived project |
| `workflow` | `instantiated_from` | `workflow:<id>` |
| `linked_accounts[]` (each row's `account` key) | `linked_account` | `account:<id>` |
| `stakeholders[]` | `stakeholder` | `contact:<id>` |
| `primary_contacts[]` | `rolodex_member` | `contact:<id>` |
| `tags[]` | `tagged` | `tag:<tag>` (the tag node is materialized implicitly) |
| a watcher row's `watch:` mapping | `indexed_as` | `source:<row id>` (from the `watch:<id>` node) |

A watcher's edges run from its `watch:<id>` node to the related domain / project / asset / account (`scoped`, `related_asset`, `pay_from`), plus one `indexed_as` edge to its own `source:<row id>` node. Edges to contacts are emitted only when the target resolves to a real contact row (prose values such as a free-text provider name are skipped), so `validate` stays quiet on well-formed data. Account rows' `linked_bills[]` / `linked_subs[]` yield no edge of their own — the bill → account `pay_from` edge comes from the bill's or subscription's `pay_from_account`.

## 4. Maintenance — every entity-mutating skill SHOULD

1. Call `tools/world.py.ensure_node(workspace, handle, kind, path, label, tags)` after creating an entity row.
2. Call `tools/world.py.ensure_edge(workspace, from_h, to_h, kind, evidence)` for every cross-reference written.

Both are idempotent and stamp `last_updated`; only `rebuild` stamps `last_rebuild`. The `watch` skill applies the same two calls when it registers a watcher. Nothing edits `world.yaml` by hand.

## 5. CLI — `uv run python -m superagent.tools.world`

| Command | Prints | Exit |
|---|---|---|
| `rebuild` | `rebuilt: <N> nodes, <M> edges`, then the `stats` JSON | 0 |
| `related <handle> [--depth N] [--json]` | `# Related to <handle> (kind: <kind>)`, a `# <n> neighbor(s) within depth <N>, <m> edge(s) traversed` line, then one `  - <handle> [<kind>] — <label>` line per neighbour; with `--json` the object `{node, neighbors[], edges[]}` | 0 |
| `stats` | JSON: `node_total`, `edge_total`, `by_node_kind`, `by_edge_kind`, `last_rebuild`, `last_updated` | 0 |
| `validate` | `Graph consistent.`, or one `  warn: edge from|to unknown node: <handle>` line per dangling edge and a `<n> warning(s).` footer | 0 / 1 |

Global flag `--workspace <path>` (default: the `workspace/` sibling of `superagent/`). `related` traverses undirected to `--depth` (default 1) and accepts canonical handles as well as legacy bare ids via `tools/handles.py.parse()` (`contact-abc` → `contact:abc`); a bare slug with no known legacy prefix is looked up as written and, if unknown, reported as not found. `related`, `stats` and `validate` print a one-line stderr warning (`warn: world.yaml last rebuilt <N> days ago (> 30) — consider ... rebuild`) when the graph is stale; queries still answer from the stale graph.

## 6. Consumers

- **`world` skill** — the user-facing front-end (`skills/world.md`).
- **`doctor`** — flags a `world.yaml` older than 14 days and offers `rebuild` or the retirement question.
- **`supertailor-review`** — runs `validate` in its hygiene pass; warnings surface as `needs-attention`, the fix is `rebuild`.
- **Migrations** that move or rename entity files end with a `rebuild`.
