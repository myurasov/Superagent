# Sources Contract (user-curated document library + watcher registry)

<!-- Citation form: `contracts/sources.md`. -->

`Sources/` is the workspace's **reference library** — the long-term store of documents the user owns, each optionally described by a `.meta.md` sidecar, plus the **watcher registry** (`Sources/Watchlist/`, one `.ref.md` per external thing Superagent watches for change). It exists so the agent can answer "where is X?" / "what does my X say?" from local files, and "did X move?" from a local fingerprint, while leaving an audit trail of where each fact came from.

The single design goal of this contract: **the user owns the layout; the agent maintains a derived index.** The user can place files anywhere under `Sources/`, with whatever folder structure they like, with or without `add-source`. The agent's job is to keep `_memory/sources-index.yaml` in sync with whatever the user does.

There is no fetch-on-demand tier. A `.ref.md` is a **watcher definition and nothing else** (§ 15.4); the older pull-through-a-cache reference model (`kind` / `source` / `ttl_minutes`, `Sources/_cache/`, `.ref.txt`, the normalizer) was retired in 0.20.0. Live data reaches the workspace through a watcher's harvest or through capture-through on a live call (`contracts/local-first-read-order.md`), never through a cached copy of a pointer.

### 15.1 Layout

The layout is **user-defined**. The agent reserves exactly two names under `Sources/`:

| Reserved | Why |
|---|---|
| `Sources/README.md` | The folder's user-facing docs (templates/folder-readmes/Sources.md). Excluded from the index. |
| `Sources/Watchlist/` (or `config.preferences.watchlist.path`) | The **watcher registry** (`contracts/watchlist.md`). Reserved *name*, user-editable *contents*: hand-author, edit or delete any `<name>.ref.md` in it. Its `README.md` (templates/folder-readmes/Watchlist.md) is excluded from the index like `Sources/README.md`. |

Everything else under `Sources/` is yours. Examples — all valid:

```
Sources/
  README.md
  Watchlist/                       ← reserved name; one `<name>.ref.md` per watcher
    README.md
    Simplefin.ref.md               ← id `simplefin`, handle `watch:simplefin`
    Solar_Permit.ref.md            ← id `solar_permit`
  vehicles/
    camry-2018-title.pdf
    camry-2018-title.pdf.meta.md   ← optional sidecar metadata for the document
    insurance-card.pdf
  taxes/
    2024/return.pdf
    2025/estimated-q1-confirmation.pdf
    2025/estimated-q1-confirmation.pdf.meta.md
  random-bucket/whatever.md
  family-medical/2026-03-cardiology-summary.pdf
```

A "category" is just a folder name (or any tag in `_memory/sources-index.yaml.<row>.tags`). It is not enforced by the layout.

Per-Project copies of the same model may exist at `Projects/<slug>/Sources/`. Same rules: user-defined layout, indexed into the workspace-level `_memory/sources-index.yaml` with `related_project: <slug>` set on each row. There is one registry only — watchers live under the workspace-level `Sources/Watchlist/`, never under a project's `Sources/`; a project-scoped watcher sets `related_project` instead.

### 15.2 Source-of-truth: the filesystem

The filesystem under `Sources/` (and per-project `Projects/<slug>/Sources/`) is **canonical**. `_memory/sources-index.yaml` is a **derived view** that the agent rebuilds on demand.

This means:

- The user can drop a new PDF under `Sources/random-folder/` from a shell. On the next read of `_memory/sources-index.yaml`, the agent's refresh routine notices it and adds a row.
- The user can `mv` / `rm` files freely. On the next refresh, missing rows are pruned (after one grace cycle, § 15.6).
- Hand-curated index fields (`notes`, `tags`, `related_domain`, `related_project`, `related_asset`, `related_account`, `sensitive`) are **preserved** across refreshes — keyed by `id` (which is in turn keyed by path-relative-to-Sources). The agent never overwrites user-set fields with empty values.
- `read_count` and `last_accessed` are stable across refreshes (they live in the index, not on the file).
- Renames are tracked best-effort by content hash + filename similarity. On ambiguity the agent asks before rewriting cross-references.

### 15.3 The `.meta.md` sidecar (document metadata)

A **document** is any file under `Sources/` that is not a `.meta.md` sidecar, not a `.ref.md` watcher, and not a reserved `README.md`. The document IS the source; the agent opens it directly when read.

A document may carry an optional sidecar. Two filename forms are recognized:

- **Form A — canonical**: `<doc>.<ext>.meta.md`, the document's full filename plus `.meta.md` (`camry-2018-title.pdf` → `camry-2018-title.pdf.meta.md`). This is the form `add-source` and every capture skill write, and what "sidecar" means everywhere else in this contract.
- **Form B — accepted**: `<stem>.meta.md` beside `<stem>.<ext>` (`camry-2018-title.meta.md` next to `camry-2018-title.pdf`). The refresh pairs it with the sibling document whose stem matches; when several siblings share the stem (`report.pdf` and `report.docx`), the first in sorted filename order wins. For one document, a Form A sidecar wins over a Form B one when both exist.

The sidecar holds metadata the index should remember about the document and free-text notes the agent reads before opening the document itself. It is never a source in its own right: the index never creates a row for a sidecar, and a sidecar whose document is missing is reported as orphaned, not indexed. A payment confirmation captured with no native document beside it (`contracts/payment-confirmations.md` § 2) and written as `<stem>.meta.md` is, by name, a Form B sidecar with no companion — refresh warns it as an orphan and skips it; save such a confirmation as the markdown stub that contract prescribes (`<stem>.md`, fields in its frontmatter), which is a document and gets its own row.

YAML frontmatter (optional fields; all may be omitted), free-text notes body (optional):

| Field | Notes |
|---|---|
| `title` | Display title; defaults to the filename. |
| `description` | One-line summary. |
| `sensitive` | Default `false`; `true` triggers redaction in outbound surfaces (`contracts/visibility.md`). |
| `related_domain` / `related_project` / `related_asset` / `related_account` | Cross-references; become `world.yaml` edges from `source:<id>`. |
| `tags` | Free-form labels. |
| `added_by` / `added_at` | Provenance shorthand (`user` / `<skill>` / `<migration version>`; ISO 8601). |
| `provenance` | Full block per `contracts/provenance.md` when the sidecar records facts beyond the file itself. |
| any domain-specific field | e.g. the payment-confirmation fields of `contracts/payment-confirmations.md` § 2 (`payee`, `amount`, `currency`, `payment_date`, `method`, `confirmation`, `purpose`, `related_entities`). Fields the index does not model stay in the sidecar and are read from there (§ 15.5 step 3). |

A sidecar is the right place for anything the document cannot carry itself: a scanned receipt's structured fields, the story behind a title document, which household member a medical record belongs to. It is OPTIONAL — skip it when everything fits in the `_memory/sources-index.yaml` row (which preserves hand-curated fields on its own, § 15.2). The sidecar wins over the index row on a conflict; the index row wins over nothing (it is derived).

Sidecars are the only markdown files under `Sources/` the agent may write outside `Sources/Watchlist/`, and only when the user asks for one (`add-source`, a payment capture, a document promotion). It never rewrites a sidecar the user hand-authored except to add a field the user asked for.

### 15.4 Watchers — `Sources/Watchlist/<name>.ref.md`

**Every `.ref.md` is a watcher.** Anywhere under `Sources/`, the `.ref.md` suffix means one thing: a change-detection definition governed by `contracts/watchlist.md`. There is no exception (document metadata is `.meta.md`, § 15.3), and `.ref.txt` is not recognized at all. A `.ref.md` outside the registry folder is neither a document nor a watcher — move it into the registry, or rename it `<doc>.<ext>.meta.md` if it was meant as document metadata.

What this contract says about them; everything else is `contracts/watchlist.md`:

- **Location**: the registry folder, `Sources/Watchlist/` by default (`config.preferences.watchlist.path`).
- **Filename**: `<name>.ref.md` — the suffix is what makes it a watcher; the name is the user's and is kept as written (`HA.ref.md`, `ha.ref.md`, `Home_Assistant.ref.md` are all valid; nothing renames them or warns about their casing). The names the tool generates are `Title_Case` — the first letter of every `_`- or `-`-delimited token capitalized (`enable --id gmail-bills` → `Gmail-Bills.ref.md`; `--id foo_bar` → `Foo_Bar.ref.md`; `Simplefin.ref.md`, `Home_Assistant-Hub.ref.md`). The watcher **id** is the stem lowercased (`simplefin`, `home_assistant-hub`, `gmail-bills`, `ha`); the handle is `watch:<id>`; files resolve case-insensitively; two files with the same lowercased stem are a load error.
- **Schema**: `ref_version: 2` — `title`, `description`, `related_*`, `tags`, `added_by`, `added_at`, and a required `watch:` block that carries the detect configuration AND the locator (`url:` / `path:` / `cmd:` / `prompt:` / `query:` for a bare watcher; `params:` for a pack instance). The template is `superagent/templates/sources/ref.md`. Field table: `contracts/watchlist.md` § 2.
- **Indexing**: `sources_index.py refresh` indexes each watcher like any other source so `sources list` / `sources search` see it; the `watch:` mapping is lifted verbatim into the row as `watch`. The world graph links the `watch:<id>` node and the `source:<id>` row with an `indexed_as` edge (`contracts/operational-handles.md`).
- **What it is not**: a watcher is not fetched on demand and has no read-freshness of its own. The question it answers is "did it move?", with the fingerprint kept in `_memory/watchlist-state.yaml`; when a watcher's harvest pulls records they land in a typed `_memory/` index, which is where skills read them (§ 15.5).

The body of the file (after the frontmatter) is free-text **notes** — why this is watched, what a change would mean — surfaced by the `watch` skill's "show" (there is no `show` subcommand: it is `watchlist list --json` filtered to the id, plus the ref file itself) and never parsed by the tool.

### 15.5 Read pattern (local-first)

Every skill that needs source data MUST follow this read order:

1. **Refresh the index** by calling `tools/sources_index.py refresh` (cheap — see § 15.6 for the laziness contract). This guarantees the index reflects the filesystem.
2. **Read `_memory/sources-index.yaml`** to identify candidate rows.
3. For a **document**: read its `.meta.md` sidecar first when one exists (frontmatter + notes — cheap; sometimes the notes answer the question). Then read the document itself from its path. **Never load a large document whole into context** — small text (≤ 4 KB) may be surfaced inline; otherwise surface the path and extract the specific pages or ranges the question needs.
4. For a **watcher**: the answer is state, not content. Read the row's `watch` block for what is watched, and `_memory/watchlist-state.yaml` for `last_checked` / `last_changed` / `last_outcome` and the stamped note; for a harvest-bearing watcher read the typed index it writes (`transactions.yaml`, ...). Running a check or a harvest is the `watch` skill's job (`contracts/watchlist.md` § 7–8), never a side-effect of a read.
5. Increment `_memory/sources-index.yaml.<row>.read_count` and set `last_accessed` (via `tools/sources_index touch <id>`).

Skills that violate this — e.g. that go to a live MCP for a document the library already holds, or that run a watcher's harvest to answer a question the typed index already answers — are flagged by the Supertailor's strategic pass as candidates for refactor.

### 15.6 Index refresh (auto, lazy, mtime-based)

`_memory/sources-index.yaml` is **derived**. The agent rebuilds it on demand via `tools/sources_index.py refresh`, which:

1. Reads `_memory/sources-index.yaml.last_filesystem_scan` (ISO 8601).
2. Walks `Sources/` (and `Projects/*/Sources/`) and computes `max_mtime` across all eligible files.
3. **If `max_mtime <= last_filesystem_scan`**: no-op. Returns immediately.
4. **Else** rescans:
   - Enumerate every file under `Sources/` excluding `README.md` files (workspace root and `Watchlist/`) and dotfiles.
   - Classify each file: **sidecar** (`*.meta.md` — never a row of its own), **watcher** (`*.ref.md` inside the registry folder), or **document** (everything else). A `*.ref.md` outside the registry folder is skipped (§ 15.4).
   - For each document: build the row from path + (sibling `<doc>.<ext>.meta.md` if present) + existing index fields.
   - For each watcher: parse the frontmatter; pull `title`, `description`, `tags`, cross-references, `added_by`, `added_at` into the row and lift the `watch:` mapping verbatim as `watch`. The `watch` field is carried across a refresh that could not parse the file and dropped only when the file parses cleanly without it. The refresh never rewrites a ref.
   - Build the row id deterministically: `id = "src-<sha1(workspace_relative_path)[:10]>"`. This is stable across renames only if the sidecar carries the original id — or if the existing row's `path` was already updated in place to the new location (path identity: a present row whose `path` names the scanned file keeps its `id`, so `history.md` / log rows that cite it stay live; the 0.19.0 and 0.20.0 migrations rely on this when they relocate or rename refs and sidecars).
   - **Diff** the rebuilt rows against the existing index:
     - **Added**: append.
     - **Removed**: keep the row and mark `present: false` for one cycle (so the user doesn't lose `notes` after an accidental `rm`); permanently drop on the next refresh if still missing.
     - **Changed (path move detected by content-hash + filename match)**: update `path` in place, preserve everything else.
     - **Unchanged**: leave alone (do NOT touch `last_accessed`, `read_count`, `notes`, `tags`).
5. Update `last_filesystem_scan = now`. Save.

Skills call `refresh()` defensively — it's idempotent and cheap (one filesystem walk + one yaml load + one mtime comparison).

`add-source`, `sources rescan`, `watch enable`, and any explicit "I just added a file" invocation also call `refresh()` to surface the change immediately.

### 15.7 Mutability

The agent's posture toward user-curated content under `Sources/`:

- **Read-only by default** — the agent never deletes or overwrites a file under `Sources/` without an explicit user-confirmed action.
- `add-source --replace <id> <new-file>` — replaces a document. The old file is renamed `<oldname>-superseded-<YYYY-MM-DD>.<ext>` next to it (history preserved); its sidecar, if any, is renamed alongside.
- `add-source --untag <id> --from-domain <id>` — removes the cross-reference, never the file.
- `add-source --move <id> --to-path <new-path>` — moves the document (and its sidecar) within `Sources/` and updates the index.
- `watch enable` writes exactly one new `.ref.md` into the registry; the tool never deletes a ref (`contracts/watchlist.md` § 13), and the `watch` skill removes one only after the user confirms, offering `enabled: false` first.
- Nothing under `Sources/` is auto-managed: there is no cache subtree and no silent eviction of any kind. Watcher eviction is a state-file mark, never a file operation.
- The `doctor` skill is forbidden from touching `Sources/` content. It may suggest cleanups; the user runs them.

### 15.8 Index sync (write paths)

Every operation that touches `Sources/`:

- **`add-source --document <path>`**: copy / move the file into the chosen subfolder; optionally write `<doc>.<ext>.meta.md` beside it; call `sources_index refresh` (which picks up the new file); annotate the new row with cross-references.
- **`watch enable` / hand-authored watcher**: the file lands in the registry; `sources_index refresh` indexes it and lifts `watch:` into the row.
- **User drops a file by hand**: nothing happens until the next read — then `sources_index refresh` (see § 15.6) catches it.
- **Watcher check / harvest**: no index change. State goes to `_memory/watchlist-state.yaml`; harvested rows go to their typed `_memory/` index.

### 15.9 Project-scoped Sources

A Project may have its own `Projects/<slug>/Sources/` with the same model: documents plus optional `.meta.md` sidecars, user-defined layout. The index is still the workspace-level `_memory/sources-index.yaml`, but per-project sources carry `related_project: <slug>`. When the project is archived the folder moves with it and the rows follow the path move (§ 15.6). Watchers are never project-scoped by location — they live in the one registry and point at the project via `related_project`.
