# Provenance Contract

<!-- Migrated from `procedures.md § 19`. Citation form: `contracts/provenance.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #4. Every fact carries a source pointer.

**Schema** (additive optional field on every entity row):

```yaml
provenance:
  source: "user|<skill>|<ingestor>|init|derived"
  source_id: "<optional anchor — interaction-log id, ingest run id, etc.>"
  at: <iso datetime>
  verified_at: <optional iso — when the user last confirmed it>
```

**For markdown facts** (e.g. `info.md` § Key Facts bullets): inline annotation `<!-- src: <ref> -->` on the bullet. The agent respects the annotation when re-rendering and surfaces it on demand.

**Default**: `provenance: { source: "user", at: <created> }` for hand-entered rows; the relevant skill / ingestor name for derived rows.

**Surface contract**: when the agent makes a factual claim sourced from the workspace, prefer to cite the provenance:

> "Yes — HVAC was installed in 2019 (source: install-receipt at `Sources/documents/warranties/hvac/install-2019.pdf`, verified 2024-03-12)."
