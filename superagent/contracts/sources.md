# Sources Contract (immutable reference library + cache)

<!-- Migrated from `procedures.md § 15`. Citation form: `contracts/sources.md`. -->

`Sources/` is the workspace's **reference library** — the long-term store of documents the user owns plus pointers (`.ref.md` files) to data that lives elsewhere. It exists so the agent can answer "where is X?" / "what does my X say?" without hitting the network every time, while leaving an audit trail of where each fact came from.

### 15.1 Layout

```
Sources/
  README.md              ← user-facing docs (templates/folder-readmes/Sources.md)
  documents/             ← actual local files (PDFs, scans, exports, manuals)
    <category>/<doc>.<ext>
    <category>/<doc>.ref.md    ← optional sidecar metadata
  references/            ← `.ref.md` files pointing at external data
    <category>/<name>.ref.md
  _cache/                ← agent-managed cache of fetched references
    <source-hash>/
      _meta.yaml
      _summary.md
      _toc.yaml
      raw.<ext>
      chunks/            ← lazily created for large fetches
        chunk-001.md
        ...
        _index.yaml
```

### 15.2 Immutability

`Sources/documents/` and `Sources/references/` are **immutable from the agent's perspective**. Skills MUST NOT delete or overwrite anything in these subtrees without an explicit user-confirmed action through `add-source --replace <id>`. The `doctor` skill is forbidden from touching them at all.

`Sources/_cache/` is the only auto-managed subtree. Skills evict on TTL or LRU per `config.preferences.sources.cache_max_mb`. Cache eviction is silent; no user prompt needed.

When the user wants to remove a document or reference, they do it manually with `mv` / `rm` from a shell. Superagent never deletes user-curated reference material.

### 15.3 The `.ref.md` file

Template: `superagent/templates/sources/ref.md`. Required frontmatter:

| Field | Required | Notes |
|---|---|---|
| `ref_version` | yes | Schema version. Currently `1`. |
| `title` | yes | Display title. |
| `description` | yes | One-line summary. |
| `kind` | yes | `mcp` / `cli` / `url` / `api` / `file` / `vault` / `manual`. |
| `source` | yes | Source identifier (format depends on `kind`). |
| `ttl_minutes` | no | Cache freshness (default from `config.preferences.sources.default_ttl_minutes`). |
| `sensitive` | no | Default `false`; `true` triggers redaction in outbound surfaces. |
| `chunk_for_large` | no | Default `true`; chunk fetches over `chunk_threshold_kb`. |
| `auth_ref` | no | Vault reference for credentials. |
| `params` | no | Key/value parameters specific to the source. |
| `related_domain` / `related_project` / `related_asset` / `related_account` | no | Cross-references. |

The body of the file (after the frontmatter) is free-text **notes** the agent reads BEFORE fetching. Sometimes the notes answer the question and the fetch is unnecessary.

### 15.4 Caching contract

Every fetch of a `.ref.md`-pointed source goes through the cache. The cache is a per-source folder under `Sources/_cache/<sha256(kind + source)>/`:

| File | Purpose |
|---|---|
| `_meta.yaml` | `fetched_at`, `last_used`, `ttl_minutes`, `size_bytes`, `source` (full ref dict), `sensitive`, `read_count`. |
| `_summary.md` | AI-generated short summary (≤ 500 words) of the fetched content. The cheap thing the agent reads first. |
| `_toc.yaml` | Table of contents with section names + line ranges (or chunk indexes). Drives "fetch only the relevant bit". |
| `raw.<ext>` | The raw fetched content. May be skipped entirely when chunks/ exists. |
| `chunks/chunk-NNN.md` | Optional. For raw size > `chunk_threshold_kb`, the content is split into ~`chunk_target_kb` chunks. |
| `chunks/_index.yaml` | Maps logical sections → chunk numbers. |

**Eviction rules:**

- **TTL eviction** runs lazily — when a skill goes to read a cached entry, if `now - fetched_at > ttl_minutes`, the entry is treated as missing and re-fetched.
- **LRU eviction** runs at write time — when total cache size > `cache_max_mb`, oldest-`last_used` entries are deleted until under the cap. The user is never asked.
- **Force refresh**: any skill that reads a source accepts `--refresh`, which bypasses cache and re-fetches.
- **Force never-cache**: `ttl_minutes: 0` in the `.ref.md` skips caching entirely (every read = live fetch).

### 15.5 Read pattern (local-first)

Every skill that needs source data MUST follow this read order:

1. **Read `_memory/sources-index.yaml`** to identify candidate `.ref.md` / document paths.
2. For documents: read the file directly from `documents/<...>` — no fetch needed.
3. For references: open the `.ref.md`, read frontmatter + notes (cheap).
4. Compute `source_hash`; check `_cache/<hash>/_meta.yaml` for freshness.
5. **If fresh**: read `_summary.md` first; if the answer is in the summary, stop. Else read `_toc.yaml` to find the relevant section; only then read the matching chunk OR matching range from `raw.<ext>`. **Never load the whole raw content into context unless it's small (≤ 4 KB).**
6. **If stale or missing**: invoke the appropriate ingestor / shell / fetch (per `kind` + `source` in the `.ref.md`). Write the result to `_cache/<hash>/`. Then jump back to step 5.
7. Update `_meta.yaml.last_used: now`; increment `_memory/sources-index.yaml.<row>.read_count`.

Skills that violate this — e.g. that go straight to a live MCP without checking the cache — are flagged by the Supertailor's strategic pass as candidates for refactor.

### 15.6 Index sync

Every operation on `Sources/`:

- **Add a document** (`add-source --document <path>`): copy / move the file into `Sources/documents/<category>/`; write a row to `_memory/sources-index.yaml` with `kind: document`.
- **Add a reference** (`add-source --reference`): write the `.ref.md` to `Sources/references/<category>/`; write a row to `_memory/sources-index.yaml` with `kind: reference`.
- **Read a source** (any skill): increment `read_count`; update `last_accessed`.
- **Cache write** (any fetch): no index change (cache is not user-visible).
- **Cache evict** (silent): no index change; just updates `_meta.yaml.last_used` on remaining entries.

### 15.7 Project-scoped Sources

A Project may have its own `Projects/<slug>/Sources/` with the same layout. The index is still the workspace-level `_memory/sources-index.yaml`, but per-project sources carry `related_project: <slug>`.

The same caching logic applies; the cache lives under `Projects/<slug>/Sources/_cache/` (project-scoped — when the project is archived, its cache goes with it).
