---
name: superagent-workbooks
description: >-
  Render lazy per-domain (and per-entity) `.xlsx` projections of the
  structured YAML data — Bills / Subscriptions / Accounts /
  Vehicles / Pets / etc. — for human-friendly review in Excel,
  Numbers, or Google Sheets. Source-of-truth stays in YAML; the xlsx is a
  read-only projection (the agent never reads it back). Workbooks
  materialize lazily — only when ≥1 row of source data exists, and only
  when the underlying source files have changed since the last render.
  The `health` and `business` workbooks are opt-in (default OFF).
triggers:
  - render workbooks
  - update spreadsheets
  - regenerate xlsx
  - workbooks
  - workbook status
  - what spreadsheets do I have
  - enable health workbook
  - enable business workbook
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent workbooks skill

Front-end to `superagent/tools/render_workbooks.py`. Five sub-modes.

## Mode 1: render-all (default)

```
uv run python -m superagent.tools.render_workbooks --all
```

Walks every registered domain (13 defaults + any custom) AND every
per-entity workbook candidate (per pet / per vehicle / per asset). For
each, the renderer:

- **Skips** when no source data exists (lazy rule).
- **Skips** when source-file mtimes haven't changed since the last
  render (mtime-cache rule). Cheap.
- **Renders** otherwise — emits `Domains/<Domain>/<id>.xlsx` (per-domain)
  or `Domains/<Domain>/<entity-slug>.xlsx` (per-entity), with a sidecar
  `<file>.xlsx.meta.yaml` recording sources mtimes + config signature.

The `health` and `business` workbooks are opt-in via the config flags
(see Mode 4 below); they're rendered only when explicitly enabled.

## Mode 2: render one workbook

```
uv run python -m superagent.tools.render_workbooks --domain finances
uv run python -m superagent.tools.render_workbooks --entity pet:buddy
uv run python -m superagent.tools.render_workbooks --entity vehicle:blue-camry-2018
uv run python -m superagent.tools.render_workbooks --entity asset:aapl-holdings
```

Same lazy / mtime / opt-in rules apply.

## Mode 3: status

```
uv run python -m superagent.tools.render_workbooks --status
```

Prints one line per registered workbook target, with the on-disk path,
file size, last-render timestamp, and (for health / business) the opt-in
state.

## Mode 4: enable / disable opt-in workbooks

```
uv run python -m superagent.tools.render_workbooks --enable health
uv run python -m superagent.tools.render_workbooks --enable business
uv run python -m superagent.tools.render_workbooks --disable health
```

Flips `_memory/config.yaml.preferences.workbooks.<name>.enabled`. Once
enabled, subsequent `--all` runs include that workbook. Runs through the
**privacy scan** (see § Privacy below) before any cell content is written.

## Mode 5: dry-run / check

```
uv run python -m superagent.tools.render_workbooks --all --check
```

Prints what WOULD render without writing any file. Useful before a big
render to confirm the lazy rule is doing what you expect.

---

## Privacy scan

Per `_memory/config.yaml.preferences.workbooks.privacy_scan` (default
`strict`):

- **strict** — refuses any cell matching SSN (`123-45-6789`), full
  account / card number patterns (13–19 digits with optional separators),
  or high-entropy strings (32+ chars looking like passwords / API keys).
  Any match becomes the literal `[REDACTED — privacy scan]`.
- **lenient** — only redacts SSN + 16-digit numerics (skips the
  high-entropy heuristic, which sometimes false-positives on long IDs).
- **off** — no scan. NOT recommended. The xlsx files leak easily; if you
  can't trust the inputs, leave the scan on.

The renderer never sends data outside the workspace. The scan is purely
to prevent the rendered `.xlsx` from carrying sensitive raw values that
were already redacted-by-convention in the YAML (e.g. last-4 only).

## History window

Per `_memory/config.yaml.preferences.workbooks.history_window_years`
(set during `init`; default 10 years). Time-series sheets (transactions,
vet visits, service history, vaccinations, …) filter to entries in the
last N years. Reference sheets (accounts, properties, certifications)
ignore the window — they're current-state, not history.

## Where workbooks live

- Per-domain: `Domains/<Domain>/<domain-id>.xlsx`
  (e.g. `Domains/Finances/finances.xlsx`).
- Per-entity: `Domains/<Domain>/<entity-slug>.xlsx`
  (e.g. `Domains/Pets/buddy.xlsx`, `Domains/Vehicles/blue-camry-2018.xlsx`,
  `Domains/Assets/aapl-holdings.xlsx`).
- Sidecar metadata: `<workbook>.xlsx.meta.yaml` records last render
  timestamp + source-file mtimes + config signature.

The renderer materializes the parent `Domains/<Domain>/` folder via the
lazy-domain helper if not present (per
`contracts/domains-and-assets.md` § 6.4a).

## Logging

Append to `_memory/interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "workbooks"
  summary: |
    Rendered <N> workbook(s); skipped <K> (empty / stale / opt-in off).
    Privacy redactions: <R> cell(s).
  related_domain: null
```
