# Domain Reflection Contract

<!-- Citation form: `contracts/domain-reflection.md`. -->

Domain folders (`Domains/<Name>/info.md`, `status.md`, `history.md`) are the
human-readable, narrative view of a life area. The structured truth lives in
`_memory/*.yaml` indexes (accounts, bills, subscriptions, appointments,
transactions, contacts, …) and in `Sources/` files. The two MUST stay in
sync: whenever a write lands in a `_memory/*.yaml` index, the domain files
that surface that data are refreshed.

This contract codifies that obligation. Ingestors and capture skills MUST
trigger a domain refresh after they write. Skills that READ domain files
MUST be able to assume the auto-managed blocks reflect the latest index
state.

## The marker convention

Domain files (`info.md`, `history.md`) are a mix of **curated narrative**
(authored by the user or the agent on a thinking pass) and
**auto-managed blocks** (regenerated from `_memory/*.yaml`). The two are
separated by explicit markers:

```markdown
<!-- auto:<name>:start -->
...rendered content...
<!-- auto:<name>:end -->
```

- **Outside markers**: hand-curated. NEVER overwritten by a renderer.
- **Inside markers**: agent territory. Regenerated on every refresh; any
  edits between renders are clobbered.

Naming: `<name>` is a stable slug per renderer (e.g. `accounts-summary`,
`recurring-commitments`, `ingest-events`). The renderer registry maps each
slug to a Python function.

Adopters opt in by inserting a marker pair where they want the block to
land; they opt out by removing it. Domain files without any markers are
left alone — never silently rewritten.

## The refresh tool

[`superagent/tools/render_domain.py`](../tools/render_domain.py) is the
canonical implementation. Invocation:

```bash
uv run python -m superagent.tools.render_domain --domain finances
uv run python -m superagent.tools.render_domain --all
```

A single call to `render_domain.refresh(workspace, domains)` refreshes
**every derived view** for the affected domain(s), in this order:

1. **Marker blocks** in `Domains/<Name>/{info,history}.md` (this module).
   Reads `_memory/*.yaml`, renders each registered block, **splices** the
   new content between markers, leaves everything else untouched.
2. **`status.md` task tables** (delegated to `tools/render_status.py`).
   The `## Open` / `## Done` tables are spliced into any existing curated
   `status.md`, preserving the RAG / Recent Progress / Blockers / Next
   Steps narrative authored by humans.
3. **Per-domain `.xlsx` workbook** (delegated to `tools/render_workbooks.py`).
   Mtime-lazy — re-rendering is a no-op when no source yaml has changed
   since the last build.

All three stages are **best-effort**: failures land in the aggregate
`errors` list returned by `refresh()` but never raise. The underlying
data is already safely in `_memory/*.yaml`; rendering is derived.

If no markers are present in any domain file, stage 1 is a no-op. Stages
2 and 3 still run — `status.md` and the `.xlsx` are managed by their own
tools' rules independent of marker adoption.

## Ingestor obligation

Every ingestor under `superagent/tools/ingest/<source>.py` MUST:

1. Declare `affected_domains: tuple[str, ...]` on its class. Empty tuple
   means "no domain refresh needed" (e.g. raw photo metadata).
2. Call `render_domain.refresh(workspace, domains)` at the end of a
   successful `run()` — before returning the `RunResult`. The base class
   provides `IngestorBase._refresh_domains()` for this.
3. Tolerate `render_domain` failure: a refresh error is logged into
   `RunResult.notes` but does NOT fail the ingest. The data is already
   safely in the index file; rendering is a derived view.

## Capture-skill obligation

Capture skills (`add-account`, `add-bill`, `add-subscription`, `add-contact`,
`bills`, `subscriptions`, `appointments`, `log-event`, etc.) MUST end every
mutation with the equivalent refresh call:

```python
from superagent.tools import render_domain
render_domain.refresh(workspace, ["finances", "vehicles"])
```

Skills MAY pass the empty list `[]` to skip the refresh when the change
doesn't surface in any domain (rare).

## Read-side rule

Skills that read domain files (`whatsup`, `daily-update`, `weekly-review`,
`monthly-review`, the persona renderers) MAY treat the auto-managed blocks
as fresh — they were refreshed at the moment of the last upstream write.
Skills MUST NOT, however, read curated narrative outside the markers and
assume it reflects current data; that text is for humans.

## Registry: which slugs surface which data

Defined in `superagent/tools/render_domain.py` as the `RENDERERS` table.
Per-domain marker slugs (current set):

| Domain    | Slug                      | Source            | Where it lives  |
|-----------|---------------------------|-------------------|-----------------|
| Finances  | `accounts-summary`        | `accounts-index`  | `info.md`       |
| Finances  | `financial-balances`      | `accounts-index`  | `info.md`       |
| Finances  | `recurring-commitments`   | `bills` + `subs`  | `info.md`       |
| Finances  | `ingest-events`           | `ingestion-log`   | `history.md`    |

Future domains register additional slugs by:

1. Adding a `def render_<slug>(workspace) -> str:` function.
2. Adding the slug to `RENDERERS` keyed by `(domain, slug)`.
3. Inserting the marker pair into the target domain file.

## Backwards compatibility

This contract is **additive**. Existing workspaces without markers in their
domain files are unaffected — the tool is a no-op there. To opt in, the
user (or the `migrate` skill in a future MINOR bump) inserts marker pairs
where they want auto-content. No data is destroyed by the introduction of
this contract.

## Failure modes

- **Marker mismatch** (start without end, or vice versa): renderer logs
  the issue, skips that block, continues with others. The user must fix
  the file.
- **Stale marker** (block name no longer registered): renderer logs a
  warning, leaves the block intact. The user can remove the marker if no
  longer needed.
- **YAML parse error in source index**: renderer skips affected blocks and
  surfaces the error in stderr. The block keeps its prior content.

## Relationship to other contracts

- [`contracts/ingestion.md`](ingestion.md) § 2.2 obligation 11 (added in
  0.6.2): "Refresh affected domains via `render_domain.refresh`".
- [`contracts/task-management.md`](task-management.md) § 5.1: the
  prior-art splice pattern that `render_status.py` uses for `## Open` /
  `## Done` task blocks. Domain reflection generalizes it.
- [`contracts/domains-and-assets.md`](domains-and-assets.md): defines the
  4-file structure. This contract specifies how those files stay current.
