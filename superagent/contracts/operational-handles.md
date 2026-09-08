# Operational Handles Contract

<!-- Migrated from `procedures.md § 18`. Citation form: `contracts/operational-handles.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #20. Canonical: `<kind>:<slug>` (colon separator).

**Kinds** (lowercase singular; see `tools/handles.py.KINDS`):

`contact`, `account`, `asset`, `bill`, `subscription`, `appointment`, `important_date`, `document`, `domain`, `project`, `source`, `medication`, `vital`, `task`, `health_visit`, `decision`, `tag`, `event`, `skill`, `watch`, `other`.

`watch:<slug>` is a watcher (ext-source) per `contracts/watchlist.md`: the slug is the lowercased filename stem of `Sources/Watchlist/<Title_Case>.ref.md` (`Gmail-Bills.ref.md` → `watch:gmail-bills`), which is also its `watchlist-state.yaml` key — renaming the file to a different stem renames the handle; a case-only rename does not. The same file is additionally indexed as a `source:<id>` row; `tools/world.py rebuild` links the two with an `indexed_as` edge.

**Slugs**: lowercase, hyphenated, no punctuation (per `tools/handles.py.slug_for(name)`).

**Back-compat**: legacy bare ids (`contact-dr-smith`, `task-20260428-001`, `dec-2026-04-28-001`) are accepted by the parser via `LEGACY_PREFIXES` mapping. New writes SHOULD use the canonical form.

**Where handles appear**:
- Every entity-row carries an optional `handle` field.
- Every cross-reference (`related_*`, `pay_from_account`, `provider`, `affects`, etc.) accepts a handle OR a bare id.
- The world graph (`_memory/world.yaml`) is keyed by handle.
- Every operational-handle string in chat (when the user types one or the agent surfaces one) parses unambiguously.

**Tooling**: `tools/handles.py` is the single canonical parser / formatter. Skills MUST use it; never split on colon by hand.
