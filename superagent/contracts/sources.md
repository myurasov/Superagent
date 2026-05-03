# Sources Contract (user-curated reference library + agent-managed cache)

<!-- Citation form: `contracts/sources.md`. -->

`Sources/` is the workspace's **reference library** — the long-term store of documents the user owns plus pointers (`.ref.md` / `.ref.txt` files) to data that lives elsewhere. It exists so the agent can answer "where is X?" / "what does my X say?" without hitting the network every time, while leaving an audit trail of where each fact came from.

The single design goal of this contract: **the user owns the layout; the agent maintains a derived index and a derived cache.** The user can place files anywhere under `Sources/`, with whatever folder structure they like, with or without `add-source`. The agent's job is to keep `_memory/sources-index.yaml` in sync with whatever the user does.

### 15.1 Layout

The layout is **user-defined**. The agent reserves exactly two names under `Sources/`:

| Reserved | Why |
|---|---|
| `Sources/README.md` | The folder's user-facing docs (templates/folder-readmes/Sources.md). |
| `Sources/_cache/` | Agent-managed fetch cache. Leading underscore = "agent territory; users should not touch by hand". |

Everything else under `Sources/` is yours. Examples — all valid:

```
Sources/
  README.md
  _cache/                          ← agent-managed (the only reserved name besides README.md)
  vehicles/
    camry-2018-title.pdf
    camry-2018-title.ref.md        ← optional sidecar metadata for the file
    insurance-card.pdf
    fidelity-401k-portal.ref.md    ← reference (no sibling file → standalone pointer)
  taxes/
    2024/return.pdf
    2025/in-progress.ref.txt       ← .ref.txt is also recognized
  random-bucket/whatever.md
  family-medical/dad-cardiologist-portal.ref.md
```

A "category" is now just a folder name (or any tag in `_memory/sources-index.yaml.<row>.tags`). It is not enforced by the layout.

Per-Project copies of the same model may exist at `Projects/<slug>/Sources/`. Same rules: user-defined layout, single reserved `_cache/` per project, indexed into the workspace-level `_memory/sources-index.yaml` with `related_project: <slug>` set on each row.

### 15.2 Source-of-truth: the filesystem

The filesystem under `Sources/` (and per-project `Projects/<slug>/Sources/`) is **canonical**. `_memory/sources-index.yaml` is a **derived view** that the agent rebuilds on demand.

This means:

- The user can drop a new PDF under `Sources/random-folder/` from a shell. On the next read of `_memory/sources-index.yaml`, the agent's refresh routine notices it and adds a row.
- The user can `mv` / `rm` files freely. On the next refresh, missing rows are pruned (the corresponding cache entries become eligible for LRU eviction).
- Hand-curated index fields (`notes`, `tags`, `related_domain`, `related_project`, `related_asset`, `related_account`, `sensitive`) are **preserved** across refreshes — keyed by `id` (which is in turn keyed by path-relative-to-Sources). The agent never overwrites user-set fields with empty values.
- `read_count` and `last_accessed` are stable across refreshes (they live in the index, not on the file).
- Renames are tracked best-effort by content hash + filename similarity. On ambiguity the agent asks before rewriting cross-references.

### 15.3 The `.ref.md` / `.ref.txt` file

A reference file is anything ending `.ref.md` or `.ref.txt`. The two extensions exist so users can hand-author either markdown (with YAML frontmatter) or plain text (free format). The agent treats both identically once normalized.

#### Canonical form (post-normalization)

`superagent/templates/sources/ref.md` is the canonical template. YAML frontmatter (required), free-text notes body (optional).

| Field | Required | Notes |
|---|---|---|
| `ref_version` | yes | Schema version. Currently `1`. |
| `title` | yes | Display title. |
| `description` | no | One-line summary. |
| `kind` | yes | `mcp` / `cli` / `url` / `api` / `file` / `vault` / `manual`. |
| `source` | yes | Source identifier (format depends on `kind`). |
| `ttl_minutes` | no | Cache freshness (default from `config.preferences.sources.default_ttl_minutes`). |
| `sensitive` | no | Default `false`; `true` triggers redaction in outbound surfaces. |
| `chunk_for_large` | no | Default `true`; chunk fetches over `chunk_threshold_kb`. |
| `auth_ref` | no | Vault reference for credentials. |
| `params` | no | Key/value parameters specific to the source. |
| `related_domain` / `related_project` / `related_asset` / `related_account` | no | Cross-references. |
| `tags` | no | Free-form labels. |

The body of the file (after the frontmatter) is free-text **notes** the agent reads BEFORE fetching. Sometimes the notes answer the question and the fetch is unnecessary.

#### Liberal input (pre-normalization)

A hand-authored ref file can be written in **any format the user finds convenient**. The agent's normalizer (`tools/sources_normalize.py`) accepts at minimum:

- **YAML frontmatter** — already canonical, no-op.
- **Loose `Key: value` lines** at the top of the file — `Title: …`, `Type: url`, `Source URL: https://…`, `URL: …`, `Cmd: …`, `MCP: …`, `Notes: …` etc. The normalizer maps known aliases to canonical fields:
  - `type` / `Type` / `kind` / `Kind` → `kind`
  - `url` / `URL` / `link` / `Link` → `source` (and `kind: url` if not set)
  - `cmd` / `command` / `shell` → `source` (and `kind: cli` if not set)
  - `path` / `file` → `source` (and `kind: file` if not set)
  - `mcp` → `source` (and `kind: mcp` if not set)
  - `vault` / `1password` / `1p` → `source` (and `kind: vault` if not set)
  - `notes` / `note` → trailing notes body
  - `tags` (comma-separated) → tags list
  - `domain` / `project` / `asset` / `account` → corresponding `related_*`
- **A bare URL** as the first non-blank line → `kind: url`, `source: <the-url>`, `title: <hostname>`.
- **A bare absolute path** as the first non-blank line → `kind: file`, `source: <path>`.
- **A `1Password://...` URI** → `kind: vault`, `source: <uri>`.

#### Normalize-on-first-use (interactive)

When a skill is about to read a non-canonical ref file (or `add-source` is processing one the user just dropped in):

1. The agent loads the raw file and runs the parser.
2. It produces a proposed canonical YAML+notes form and a unified diff against the original.
3. It **asks the user** before writing:
   ```
   Normalize <path>?
     [r] Rewrite in place + keep <name>.ref.md.original backup
     [n] Rewrite in place, no backup (rely on git history)
     [s] Write sibling <name>.ref.normalized.md, leave my file alone
     [k] Keep this file as-is for now (use parsed values for this read only)
   ```
4. The user's choice is remembered in `_memory/_session/` for the rest of the session (so they aren't re-asked for a batch). Persistent default lives in `config.preferences.sources.normalize_policy` (default `ask`).
5. If the parser cannot produce a complete canonical form (missing `kind` or `source`), the agent asks the user to fill the gaps before writing anything.

The non-interactive default for batch tools (e.g. a ref refresh sweep) is set by `config.preferences.sources.normalize_policy_batch` (default `keep` — never silently rewrite during a non-interactive run).

### 15.4 Caching contract

Every fetch of a `.ref.md` / `.ref.txt`-pointed source goes through the cache. The cache lives at `Sources/_cache/<sha256(kind + source)[:16]>/`:

| File | Purpose |
|---|---|
| `_meta.yaml` | `fetched_at`, `last_used`, `ttl_minutes`, `size_bytes`, `source` (full ref dict), `sensitive`, `read_count`. |
| `_summary.md` | AI-generated short summary (≤ 500 words) of the fetched content. The cheap thing the agent reads first. |
| `_toc.yaml` | Table of contents with section names + line ranges (or chunk indexes). Drives "fetch only the relevant bit". |
| `raw.<ext>` | The raw fetched content. May be skipped entirely when chunks/ exists. |
| `chunks/chunk-NNN.md` | Optional. For raw size > `chunk_threshold_kb`, the content is split into ~`chunk_target_kb` chunks. |
| `chunks/_index.yaml` | Maps logical sections → chunk numbers. |

The cache path is overridable via `config.preferences.sources.cache_path` (e.g. point it at an external/encrypted volume). Default is `Sources/_cache/`.

**Eviction rules:**

- **TTL eviction** runs lazily — when a skill goes to read a cached entry, if `now - fetched_at > ttl_minutes`, the entry is treated as missing and re-fetched.
- **LRU eviction** runs at write time — when total cache size > `cache_max_mb`, oldest-`last_used` entries are deleted until under the cap. The user is never asked.
- **Force refresh**: any skill that reads a source accepts `--refresh`, which bypasses cache and re-fetches.
- **Force never-cache**: `ttl_minutes: 0` in the ref file skips caching entirely (every read = live fetch).

### 15.5 Read pattern (local-first)

Every skill that needs source data MUST follow this read order:

1. **Refresh the index** by calling `tools/sources_index.py refresh` (cheap — see § 15.6 for the laziness contract). This guarantees the index reflects the filesystem.
2. **Read `_memory/sources-index.yaml`** to identify candidate ref / document paths.
3. For documents: read the file directly from its path — no fetch needed.
4. For references: open the ref file, read frontmatter + notes (cheap). If it's not in canonical form yet, run the normalize flow in § 15.3.
5. Compute `source_hash`; check `<cache_path>/<hash>/_meta.yaml` for freshness.
6. **If fresh**: read `_summary.md` first; if the answer is in the summary, stop. Else read `_toc.yaml` to find the relevant section; only then read the matching chunk OR matching range from `raw.<ext>`. **Never load the whole raw content into context unless it's small (≤ 4 KB).**
7. **If stale or missing**: invoke the appropriate ingestor / shell / fetch (per `kind` + `source` in the ref). Write the result to `<cache_path>/<hash>/`. Then jump back to step 6.
8. Update `_meta.yaml.last_used: now`; increment `_memory/sources-index.yaml.<row>.read_count`.

Skills that violate this — e.g. that go straight to a live MCP without checking the cache — are flagged by the Supertailor's strategic pass as candidates for refactor.

### 15.6 Index refresh (auto, lazy, mtime-based)

`_memory/sources-index.yaml` is **derived**. The agent rebuilds it on demand via `tools/sources_index.py refresh`, which:

1. Reads `_memory/sources-index.yaml.last_filesystem_scan` (ISO 8601).
2. Walks `Sources/` (and `Projects/*/Sources/`) and computes `max_mtime` across all eligible files.
3. **If `max_mtime <= last_filesystem_scan`**: no-op. Returns immediately.
4. **Else** rescans:
   - Enumerate every file under `Sources/` excluding `_cache/` and `README.md`.
   - For each file: classify as **document** (anything not matching `*.ref.md` / `*.ref.txt`) or **reference** (matching).
   - For each ref: parse frontmatter (canonical) or run the parser (liberal). Pull `kind`, `source`, `ttl_minutes`, `sensitive`, cross-references, tags into the row. **Do not** auto-normalize during a refresh — that's interactive (§ 15.3).
   - For each document: build the row from path + (sibling `.ref.md` if present) + existing index fields.
   - Build the row id deterministically: `id = "src-<sha1(workspace_relative_path)[:10]>"`. This is stable across renames only if a sibling `.ref.md` carries the original id.
   - **Diff** the rebuilt rows against the existing index:
     - **Added**: append.
     - **Removed**: keep the row and mark `present: false` for one cycle (so the user doesn't lose `notes` after an accidental `rm`); permanently drop on the next refresh if still missing.
     - **Changed (path move detected by content-hash + filename match)**: update `path` in place, preserve everything else.
     - **Unchanged**: leave alone (do NOT touch `last_accessed`, `read_count`, `notes`, `tags`).
5. Update `last_filesystem_scan = now`. Save.

Skills call `refresh()` defensively — it's idempotent and cheap (one filesystem walk + one yaml load + one mtime comparison).

`add-source`, `sources rescan`, and any explicit "I just added a file" invocation also call `refresh()` to surface the change immediately.

### 15.7 Mutability

The agent's posture toward user-curated content under `Sources/`:

- **Read-only by default** — the agent never deletes or overwrites a file under `Sources/` without an explicit user-confirmed action.
- `add-source --replace <id> <new-file>` — replaces a file. The old file is renamed `<oldname>-superseded-<YYYY-MM-DD>.<ext>` next to it (history preserved).
- `add-source --untag <id> --from-domain <id>` — removes the cross-reference, never the file.
- `add-source --move <id> --to-path <new-path>` — moves the file within `Sources/` and updates the index.
- `Sources/_cache/` (or whatever `cache_path` is set to) is the only auto-managed subtree — TTL + LRU eviction is silent.
- Normalize-on-first-use rewrites a ref file IF the user picks `[r]` or `[n]`; otherwise the file is untouched.
- The `doctor` skill is forbidden from touching `Sources/` content. It may suggest cleanups; the user runs them.

### 15.8 Index sync (write paths)

Every operation that touches `Sources/`:

- **`add-source --document <path>`**: copy / move the file into the chosen subfolder; call `sources_index refresh` (which picks up the new file); annotate the new row with cross-references.
- **`add-source --reference`**: write the canonical ref file at the chosen path; call `sources_index refresh`; annotate the new row.
- **User drops a file by hand**: nothing happens until the next read — then `sources_index refresh` (see § 15.6) catches it.
- **Cache write** (any fetch): no index change (cache is not user-visible).
- **Cache evict** (silent): no index change.

### 15.9 Project-scoped Sources

A Project may have its own `Projects/<slug>/Sources/` with the same model. Each project has its own reserved `Projects/<slug>/Sources/_cache/` (project-scoped — when the project is archived, its cache goes with it). The index is still the workspace-level `_memory/sources-index.yaml`, but per-project sources carry `related_project: <slug>`.
