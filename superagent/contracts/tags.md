# Tags Contract

<!-- Migrated from `procedures.md § 23`. Citation form: `contracts/tags.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #11. Backed by `_memory/tags.yaml` and the `tags` skill.

**Auto-register** (`config.preferences.tags.auto_register: true` default): when any skill writes a tag NOT in `tags.yaml`, the new canonical row is appended automatically with `created_by: <skill>`. The user curates description + category later.

**Strict canonical** (`config.preferences.tags.strict_canonical: false` default): when true, skills MUST refuse to write a tag not in `tags.yaml`. Promoted by the Supertailor when the tags taxonomy stabilizes.

**Aliases**: every tag row carries an `aliases: []` list. Skills that read tags SHOULD canonicalize via `tags.yaml` lookup before treating "tax-deductible" and "deductible" as different.

**Cross-cutting**: any entity may carry `tags: [..]`. The `tags` skill walks every entity-shape file to surface "show me everything tagged X".

**Recount maintenance**: the `tags` skill's `recount` sub-action walks all entities and updates `uses_count` per tag; `supertailor-review` runs it during the hygiene pass.
