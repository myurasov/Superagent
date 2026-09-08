# Sources — {{DOMAIN_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  The curated catalogue of `Sources/` entries relevant to this domain.

  Foundational rule (contracts/sources.md): source documents are NEVER stored
  inside Domains/<domain>/. They live under `Sources/<your-folders>/` (the
  layout under Sources/ is user-defined; the agent reserves only `README.md`
  and the `Watchlist/` registry). This file is the human-readable,
  domain-scoped POINTER LIST so when you open the domain you can see "what
  does Superagent know about this domain?" at a glance.

  Sync contract: every `add-source --to-domain <id>` invocation appends a row
  here AND updates `_memory/sources-index.yaml.<row>.related_domain`.

  The structured truth lives in `_memory/sources-index.yaml`. This file is the
  human-readable projection of that index, filtered to this domain. It is
  managed by skills BUT respects user hand-edits (diff-and-merge, not
  blind-clobber). Add commentary, group rows differently, leave notes —
  Superagent will preserve them.
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Sources — {{DOMAIN_NAME}}](#sources--domain_name)
  - [How this file stays current](#how-this-file-stays-current)
  - [Documents](#documents)
  - [Watchers](#watchers)
  - [Domain-generated artifacts](#domain-generated-artifacts)

---

## How this file stays current

- Every time you say "add X to {{DOMAIN_NAME}}" (or run `add-source --to-domain {{DOMAIN_ID}}`), a row appears here.
- Every time you remove a Sources entry from this domain (`add-source --untag <id>`), the row is moved to `## Removed (history)` at the bottom.
- Every watcher the `watch` skill registers with this domain (`related_domain: {{DOMAIN_ID}}`) gets a row under `## Watchers`.
- The agent NEVER deletes files from `Sources/` itself — only rows from this catalogue. The actual file under `Sources/<your-folders>/` stays put.
- Hand-edits are welcome — group rows your way, add commentary in the **Notes** column, regroup under custom sub-headings. Superagent preserves your structure on the next sync.

---

## Documents

<!-- Files under `Sources/...` that belong to this domain.
     Format: one row per document. The Path is workspace-relative. When the
     document carries a `<doc>.<ext>.meta.md` sidecar (contracts/sources.md
     § 15.3), say so in Notes — the sidecar is metadata, never a row of its own.
     Sensitive items get a marker rendered as the literal "[sensitive]"
     (no-emoji rule for committed framework files). -->

| Title | Path | Category | Added | Notes |
|-------|------|----------|-------|-------|
| {{DOC_1_TITLE}} | {{DOC_1_PATH}} | {{DOC_1_CATEGORY}} | {{DOC_1_ADDED}} | {{DOC_1_NOTES}} |

{{DOCUMENTS_TABLE_ROWS}}

---

## Watchers

<!-- `Sources/Watchlist/<Title_Case>.ref.md` watchers whose `related_domain`
     is this domain (contracts/watchlist.md). A watcher is watched for
     change, never fetched on demand: its state is in
     `_memory/watchlist-state.yaml`, its harvested records (if any) in the
     typed index its pack writes. Handle: `watch:<id>` (id = stem lowercased). -->

| Title | Ref path | Pack / type | Watches | Notes |
|-------|----------|-------------|---------|-------|
| {{WATCH_1_TITLE}} | {{WATCH_1_PATH}} | {{WATCH_1_TYPE}} | {{WATCH_1_TARGET}} | {{WATCH_1_NOTES}} |

{{WATCHERS_TABLE_ROWS}}

---

## Domain-generated artifacts

<!-- Optional. Files in `Domains/{{DOMAIN_NAME}}/Resources/` that were
     produced by you or by Superagent for this domain (drafts, photos,
     scratch worksheets, agent-rendered briefings that aren't outbound).

     These are NOT in `Sources/` — they live alongside the domain because
     they're working artifacts, not vault-grade records. Listed here so the
     domain has a single "what files exist for me?" view. -->

| Title | Path | Kind | Added | Notes |
|-------|------|------|-------|-------|
| {{ART_1_TITLE}} | {{ART_1_PATH}} | {{ART_1_KIND}} | {{ART_1_ADDED}} | {{ART_1_NOTES}} |

{{ARTIFACTS_TABLE_ROWS}}
