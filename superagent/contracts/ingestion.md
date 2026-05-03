# Data Ingestion Contract

<!-- Migrated from `procedures.md § 2`. Citation form: `contracts/ingestion.md`. -->

Superagent's value scales with the breadth of ingested data. Every data source is wired in via an **ingestor**, and every ingestor obeys this contract. The contract makes ingestion safe to run on any cadence, idempotent within its window, and reversible if it goes wrong.

### 2.1 What an ingestor is

An **ingestor** is a Python script under `superagent/tools/ingest/<source>.py` that pulls data from one external source and writes structured rows into the right `_memory/` index file (`bills.yaml`, `subscriptions.yaml`, `appointments.yaml`, `interaction-log.yaml`, etc.) and / or appends narrative entries to the right `Domains/<domain>/history.md`.

Ingestors are NOT skills. They are invoked by the **`ingest`** skill (which orchestrates them) or run directly from the command line.

### 2.2 Ingestor obligations

Every ingestor MUST:

1. **Read its row from `data-sources.yaml`** to get `enabled`, `last_ingest`, `recency_window_days`, `max_items_per_run`, source-specific auth pointer, and any per-source filter rules.
2. **Run the preflight** for its source (see § 1) and exit cleanly if blocked.
3. **Pull only the delta** since `last_ingest` (or `recency_window_days` back from `now` on first run) up to `now`.
4. **Cap the pull** at `max_items_per_run`. If the cap was hit, record `truncated: true` in the run summary so the next run picks up where this one left off.
5. **Normalize each item** into the appropriate index row OR append it to the appropriate domain `history.md`. Per-source mapping rules live in the ingestor's docstring and in `docs/data-sources.md`.
6. **Be idempotent within the window** — keyed by an upstream-stable identifier (Gmail message ID, Plaid transaction ID, Apple Health UUID, calendar event UID, …) so re-running over the same period does not duplicate rows.
7. **Update `data-sources.yaml`**: set `last_ingest` to `now`, and write the per-run summary into the row's `last_run` field.
8. **Append to `ingestion-log.yaml`** a row with timestamp, source, items pulled, items inserted, items updated, items skipped (already-present), errors, duration. **Append-only.**
9. **Append to `interaction-log.yaml`** a single line summarizing the run (so daily-update / whatsup can surface "X new items from Y sources" without parsing the per-source ingestion log).
10. **Read-only by default.** An ingestor that needs write access upstream MUST declare `writes_upstream: true` in `data-sources.yaml` for its row, MUST require explicit `--write` invocation, and MUST log every write.

### 2.3 Capture modes

Each row in `data-sources.yaml` has a `capture_mode` field:

- **`disabled`** — source is configured (auth exists) but never runs. Default for a freshly-discovered source until the user confirms.
- **`manual`** — runs only when the user explicitly invokes `ingest <source>` or `ingest --all`.
- **`scheduled`** — runs on the cadence in `preferences.ingestion_schedule.<source>` (see `config.yaml`). Default cadences:
  - email / calendar / reminders / messages: every `daily-update` run.
  - banks / cards (Plaid / Monarch / YNAB): every `weekly-review` run.
  - health (Apple Health / WHOOP / Strava / Garmin / Oura): every `daily-update` run.
  - smart-home / vehicles: every `daily-update` run.
  - notes / Obsidian / Notion: every `weekly-review` run (slow-changing).
  - photos / location: every `monthly-review` run (heavy).

The user can override any per-source cadence in `config.yaml`.

### 2.4 Per-source budgets

Every source row carries:

- `recency_window_days` — for first-run ingestion (no `last_ingest` set), how many days of history to pull. Defaults vary by source — email might be 30, photos might be 7, health might be 365.
- `max_items_per_run` — hard cap so a single run can never blow up disk / token budget. Defaults vary by source.
- `backfill_window_days` (optional) — for **deferred backfill**: a separate, larger window the user can invoke explicitly via `ingest <source> --backfill`. Used for "now that I trust this, pull the last 5 years".

### 2.5 Idempotency and dedup

Every ingestor's normalized output rows include an `external_id` field carrying the upstream-stable identifier. Skills that read indexes (especially the Bookkeeper, the Concierge, the daily-update) MUST treat `external_id` as the primary dedup key when present, falling back to natural keys (date + amount + payee for transactions, date + summary for events) only if `external_id` is missing.

### 2.6 Failure handling

- A source that fails preflight is logged once per `data-sources.yaml.<source>.failure_streak`-bucket and skipped without aborting the rest of the ingest run.
- After 3 consecutive failures, the source is auto-flipped to `capture_mode: manual` and the next daily-update surfaces it under "Sources needing attention".
- The user can re-enable with `ingest <source> --enable` after fixing the problem.

### 2.7 Read path for skills

Skills should **never** call an MCP or CLI tool directly when the answer is already in a local index or domain file. The read order is:

1. **Local YAML indexes** (`_memory/*.yaml`) for structured queries.
2. **Local Domain history files** (`Domains/<domain>/history.md`) for narrative recall.
3. **Local interaction-log / ingestion-log** for "what happened recently".
4. **Live MCP / CLI source** ONLY for the strictly newer slice past `data-sources.yaml.<source>.last_ingest`, OR for live state (current bank balance, current sleep score) where staleness matters.

When the live call happens, capture-through MUST run (per § 2.2 obligation 7-9) so the next read is local.
