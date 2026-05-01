# `Sources/` — your reference library (the canonical document store)

The workspace's vault of truth for documents and pointers to external data. Three foundational rules:

1. **THE single canonical place for finished documents.** Nothing under `Domains/<X>/` or `Projects/<X>/` ever holds a source document directly — those folders carry only working files (`Resources/`) and a curated catalogue (`sources.md`) pointing at entries here. The 5-year test: *"would I want this file in five years even if its domain / project went away?"* If yes, it lives here.
2. **Immutable.** Skills MUST NOT delete or overwrite anything in `Sources/documents/` or `Sources/references/`. The `doctor` skill is forbidden from touching this subtree. Only `Sources/_cache/` is auto-managed (TTL + LRU eviction).
3. **Local-first.** The agent reads from the cache before touching live MCPs / APIs. `_summary.md` first; `_toc.yaml` second; specific chunks of `raw.<ext>` only when needed.

## Layout

```
Sources/
  README.md                            ← this file
  documents/                           ← actual local files (PDFs, scans, exports, manuals)
    <category>/                        ← grouped by category (vehicles / taxes / medical / warranties / …)
      <doc>.<ext>
      <doc>.ref.md                     ← optional sidecar metadata
  references/                          ← `.ref.md` files pointing at external data
    <category>/
      <name>.ref.md                    ← describes WHERE to fetch (mcp / cli / url / api / file / vault / manual)
  _cache/                              ← agent-managed cache of fetched references
    <source-hash>/
      _meta.yaml _summary.md _toc.yaml raw.<ext> chunks/
```

Per-Project copies of this structure may exist at `Projects/<slug>/Sources/` for project-scoped material that should follow the project to Archive when it completes.

## How a document gets here

The canonical capture flow is **`add-source`** (or natural language: "add my insurance card to Health"):

1. Place the file under `Sources/documents/<category>/<…>`.
2. Append a row to `_memory/sources-index.yaml` with cross-references (`related_domain`, `related_project`, `related_asset`, etc).
3. Append a row to the relevant `Domains/<X>/sources.md` (and / or `Projects/<X>/sources.md`) so the domain / project has a human-readable catalogue entry.

You should almost never write directly into `Sources/documents/` from a shell. Always go through `add-source` so the indexes stay consistent.

## How a `.ref.md` works

A `.ref.md` is a markdown file with YAML frontmatter that tells the agent how to fetch a piece of external data. Template: `superagent/templates/sources/ref.md`. Required fields:

- `kind`: `mcp` | `cli` | `url` | `api` | `file` | `vault` | `manual`
- `source`: the source identifier (URL / shell command / MCP call / etc.)
- `ttl_minutes`: how long the cached fetch stays fresh

The agent's read flow when it needs a `.ref`-pointed source:

1. Reads the `.ref.md` (cheap; fits in any context).
2. Computes `source_hash = sha256(kind + source)` and checks `_cache/<hash>/_meta.yaml`.
3. If the cache is fresh (within `ttl_minutes`), reads `_summary.md` first; then `_toc.yaml` to find relevant sections; then only the necessary chunks. **Never the whole raw file unless it's small.**
4. If the cache is stale or missing, runs the appropriate ingestor / shell / fetch, writes the result to `_cache/<hash>/`, and only then reads.
5. Updates `_meta.yaml.last_used` and increments `read_count` in `_memory/sources-index.yaml`.

## Where things go from here

When you receive a new piece of paper / PDF / scan, capture it via `add-source --to-domain <id>` (the agent files it correctly):

| Item | Auto-routed to |
|---|---|
| Vehicle title, registration, insurance card | `documents/vehicles/<vehicle-slug>/` (+ row in `Domains/Vehicles/sources.md`) |
| Tax return (filed) | `documents/taxes/<year>/` (+ row in `Domains/Finance/sources.md`, plus the active `Projects/tax-<year>/sources.md` if present) |
| Medical record / lab result / imaging | `documents/medical/<member-slug>/` (+ row in `Domains/Health/sources.md`) |
| Appliance manual / warranty / receipt | `documents/warranties/<asset-slug>/` (+ row in `Domains/Home/sources.md`) |
| Will / trust / POA / advance directive | `documents/legal/` (+ rows in `Domains/Family/sources.md` AND `Domains/Finance/sources.md`) |
| Insurance policy (home / auto / life / umbrella) | `documents/insurance/<policy-slug>/` (+ row in `Domains/Finance/sources.md`) |
| Mortgage / lease / deed | `documents/property/` (+ row in `Domains/Home/sources.md`) |
| School records / diploma / certification | `documents/education/<member-slug>/` (+ rows in `Domains/Family/sources.md` AND/OR `Domains/Career/sources.md`) |
| Pet vaccination / vet records | `documents/pets/<pet-slug>/` (+ row in `Domains/Pets/sources.md`) |

## What `Sources/` is NOT for

- **Drafts, working files, photos-as-references, agent-generated artifacts** → `Domains/<X>/Resources/` or `Projects/<X>/Resources/`.
- **Things you're sending to someone else** → `Outbox/`.
- **Files in transit, pending classification** → `Inbox/`.

The crisp boundaries:

| Lives in | Lifetime | Mutability |
|---|---|---|
| `Sources/documents/` | indefinite (until you manually delete) | immutable to skills |
| `Sources/_cache/` | TTL-limited (1440min default) + LRU-bounded | auto-evicted |
| `Domains/<X>/Resources/` or `Projects/<X>/Resources/` | as long as the entity is active; archived with it | hand-managed |
| `Outbox/` | until you mark sent OR send manually | append + mark; rarely deleted |
| `Inbox/` | transient (~14 days max) | drained regularly |

## Caching policy

- **Default TTL**: 1440 minutes (24 hours) per fetch.
- **Per-source TTL**: set in the `.ref.md` frontmatter (`ttl_minutes`).
- **Eviction**: `_cache/` is bounded by `_memory/config.yaml.preferences.sources.cache_max_mb` (default 500 MB). When the cap is exceeded, LRU rows are evicted until under the cap. The `_cache/<hash>/_meta.yaml.last_used` field drives LRU.
- **Force refresh**: pass `--refresh` to any skill that reads sources, or run `sources refresh <ref-id>` directly.
- **Force never-cache**: set `ttl_minutes: 0` in the `.ref.md`. Useful for rapidly-changing data (current bank balance, current sleep score).

## Sensitive sources

Set `sensitive: true` in the `.ref.md` frontmatter (or in `_memory/sources-index.yaml.<row>.sensitive`) for things like medical records, account statements, legal documents. The cached content lives in `_cache/<hash>/` like everything else, but:

- Outbound surfaces (`draft-email`, `summarize-thread`, anything that lands in `Outbox/`) redact the content.
- When the sensitive-store option is enabled (roadmap M-01), the cache is encrypted-at-rest.
- The `handoff` skill picks up sensitive sources by default; non-sensitive ones are opt-in.

## Privacy

`Sources/` is gitignored along with the rest of `workspace/`. It stays local to your machine. Superagent never publishes anything on its own — that is always an explicit user action.
