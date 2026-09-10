# Tags Contract

<!-- Migrated from `procedures.md § 23`. Citation form: `contracts/tags.md`. -->

Backed by `_memory/tags.yaml` (template: `templates/memory/tags.yaml`). There is no dedicated tags skill: every skill that reads or writes `tags: [..]` follows this contract directly.

**Auto-register** (`config.preferences.tags.auto_register: true` default): when any skill writes a tag NOT in `tags.yaml`, the new canonical row is appended automatically with `created_by: <skill>`. The user curates description + category later.

**Strict canonical** (`config.preferences.tags.strict_canonical: false` default): when true, skills MUST refuse to write a tag not in `tags.yaml`. Promoted by the Supertailor when the tags taxonomy stabilizes.

**Aliases**: every tag row carries an `aliases: []` list. Skills that read tags SHOULD canonicalize via `tags.yaml` lookup before treating "tax-deductible" and "deductible" as different.

**Cross-cutting**: any entity may carry `tags: [..]`. "Show me everything tagged X" is a `grep -n "<tag>"` across the entity-shape files under `_memory/` (canonicalize X through `aliases` first), grouped by file in the answer.

**Recount maintenance**: a `doctor`-time manual step — walk the entity-shape files, count uses per canonical tag, update `uses_count`, and surface tags at `uses_count == 0` for cleanup confirmation. No tool automates it; keep it to a hygiene pass, never a per-write chore.
