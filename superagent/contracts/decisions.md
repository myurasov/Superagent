# Decisions Log Contract

<!-- Migrated from `procedures.md § 28`. Citation form: `contracts/decisions.md`. -->

Implements ideas-better-structure.md item #24. Backed by `_memory/decisions.yaml` and the `decisions` skill.

**Append-only** (event-shape). Mutate only `outcome_measured_at`, `outcome`, `outcome_notes`, `revisited` on existing rows.

**Schema**: see `templates/memory/decisions.yaml`. Required fields: `id`, `ts`, `decision`, `context`, `confidence`, `reversibility`. Optional but encouraged: `alternatives_considered`, `rationale`, `affects` (list of handles), `review_at`.

**Surface windows**:
- `weekly-review` lists decisions made in the trailing 7 days.
- `monthly-review` lists decisions whose `review_at <= today` AND `outcome is null` (waiting to be measured).

**Cross-reference**: when the user logs a decision sourced from a `scenarios` run, the skill adds `tags: ["from-scenario"]` and links to the scenario output in `notes`.
