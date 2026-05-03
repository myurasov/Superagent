# Operational Handles Contract

<!-- Migrated from `procedures.md § 18`. Citation form: `contracts/operational-handles.md`. -->

Implements ideas-better-structure.md item #20. Canonical: `<kind>:<slug>` (colon separator).

**Kinds** (lowercase singular; see `tools/handles.py.KINDS`):

`contact`, `account`, `asset`, `bill`, `subscription`, `appointment`, `important_date`, `document`, `domain`, `project`, `source`, `medication`, `vital`, `task`, `health_visit`, `decision`, `tag`, `event`, `skill`, `workflow`, `playbook`, `scenario`, `other`.

**Slugs**: lowercase, hyphenated, no punctuation (per `tools/handles.py.slug_for(name)`).

**Back-compat**: legacy bare ids (`contact-dr-smith`, `task-20260428-001`, `dec-2026-04-28-001`) are accepted by the parser via `LEGACY_PREFIXES` mapping. New writes SHOULD use the canonical form.

**Where handles appear**:
- Every entity-row carries an optional `handle` field.
- Every cross-reference (`related_*`, `pay_from_account`, `provider`, `affects`, etc.) accepts a handle OR a bare id.
- The world graph (`_memory/world.yaml`) is keyed by handle.
- Every operational-handle string in chat (when the user types one or the agent surfaces one) parses unambiguously.

**Tooling**: `tools/handles.py` is the single canonical parser / formatter. Skills MUST use it; never split on colon by hand.
