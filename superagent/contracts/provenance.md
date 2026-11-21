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

> "Yes — HVAC was installed in 2019 (source: install-receipt at `Sources/Warranties/hvac/install-2019.pdf`, verified 2024-03-12)."

## When a fact is corrected

1. **Establish what changed.** Identify the entity, date or period, old claim, corrected claim and primary source (or the user's explicit correction). A newer observation may be true alongside an older one. If the sources conflict and neither settles the question, mark the claim uncertain and report what would resolve it.
2. **Trace the old claim.** Start at its source pointer and follow links into current records, calculations and recommendations. Search within that scope for the entity, field and period as well as the old value: a derived total may never mention its input. A matching number alone is not evidence of a dependency.
3. **Update confirmed dependencies.** Correct the current record and its citation, recompute each dependent result from its inputs, and revisit decisions that relied on the result. Follow [the memory taxonomy](memory-taxonomy.md): append a dated correction referencing the original history entry rather than rewriting it. Leave original evidence and sent or sealed artifacts intact.
4. **Verify and report.** Compare the corrected records with the source and check the arithmetic. Tell the user which records and conclusions changed, which still hold, and which possible dependencies remain unresolved.

**Worked example (fictional).** A packing list estimated 12 panels for batch B; the signed count confirms 10. In `info.md`, change the batch count from 12 to 10 and cite the signed count. In `status.md`, the packing estimate used 3 kg per panel: update 36 kg to 30 kg. With a 32 kg limit, the batch now fits in one crate. A separate batch also has 12 panels; its record stays as written. In `history.md`, append a dated note linking the original estimate and signed count. Report the count, revised weight and changed crate recommendation together.
