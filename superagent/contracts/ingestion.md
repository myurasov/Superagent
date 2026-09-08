# Data Ingestion Contract

<!-- Migrated from `procedures.md § 2`; rewritten for 0.19.0 when the watchlist became the driver. Citation form: `contracts/ingestion.md`. -->

Superagent's value scales with the breadth of data it can read. Since 0.19.0 every external source is registered, scheduled and driven by **the watchlist** (`contracts/watchlist.md`); ingestion — pulling records and normalizing them into typed `_memory/` indexes — is the optional **harvest** half of a watcher. This contract states what a harvest handler owes the workspace. Detection, cadence, state, eviction and reporting are the watchlist's job and are specified there, not here.

### 2.1 Where a source lives

- **Registry.** A source is one watcher file, `Sources/Watchlist/<Title_Case>.ref.md` (folder overridable via `config.preferences.watchlist.path`), `ref_version: 2`: title, cross-references, provenance, and a `watch:` block carrying the pack or type, the locator, and the cadence fields. The filename stem lowercased is the source id, its state key and its handle (`watch:<id>`). There is no Python table of sources and no central YAML registry.
- **Packs.** Per-source knowledge ships as a **pack folder** — `superagent/watchers/<id>/pack.yaml` in core (listed in `superagent/watchers/_manifest.yaml`) or `workspace/_custom/watchers/<id>/pack.yaml` in the user overlay (custom wins on an id collision, announced loudly). A pack declares `detect:`, an optional `harvest:` handler, a declarative `probe:`, `auth:`, `budget:` and `defaults:`. A registry row references it with `watch.pack: <id>` and overrides only what it needs. A source with no typed index to feed needs no pack at all: it is a bare `url` / `path` / `cmd` / `subagent` watcher.
- **State.** Run state (`last_success`, `last_harvest`, `last_harvest_result`, `error_streak`, `calls_today`, ...) lives in the machine-owned singleton `_memory/watchlist-state.yaml`, written only by `tools/watchlist.py`. Configuration never lives there; state never lives in the ref.
- **Discovery.** `init` and `watchlist probe` iterate the shipped packs' `probe:` blocks (`file_exists | cli_on_path | python_import | cmd_exit_zero | always`) to say what is already set up on this machine. Init never silently enables a source; it lists what is available and asks. Quick-start works with nothing enabled.

### 2.2 What a harvest handler is

A **harvest handler** is an `IngestorBase` subclass (`superagent/tools/ingest/_base.py`) that implements `run(config_row, dry_run=False) -> RunResult`. A pack names it with `harvest.handler: <dotted.module>` or ships it as `<pack folder>/handler.py`. The watchlist invokes it — a cadence skill's `check --cycle <cadence>` or an explicit `watchlist harvest --id <id>` — after detect fires and after the budget gate passes. Handlers are not skills and are never invoked by a skill directly. The handler's config row is the pack's `harvest.defaults` overridden by the same-named keys of the ref's `watch:` block (`params`, `auth`, budgets, `recency_window_days`, `max_items_per_run`, ...).

### 2.3 Handler obligations

Every harvest handler MUST:

1. **Read only its config row** as handed over by the watchlist; never open the registry or the state file itself.
2. **Pull only the delta** since the watcher's `last_harvest` (or `recency_window_days` back from now on a first run) up to now.
3. **Cap the pull** at `max_items_per_run` and record `truncated: true` in the `RunResult` when the cap was hit, so the next run resumes where this one left off.
4. **Normalize each item** into the appropriate index row OR append it to the appropriate domain `history.md`. Per-source mapping rules live in the handler's docstring and in `docs/data-sources.md`.
5. **Be idempotent within the window** — keyed by an upstream-stable identifier (`external_id`: a transaction id, message id, event UID, ...) so re-running over the same period never duplicates rows.
6. **Return a `RunResult`** (items pulled / inserted / updated / skipped, errors, duration, `truncated`). The watchlist appends `to_log_row()` to **`ingestion-log.yaml`** (append-only audit trail, `trigger: scheduled | manual`), writes `last_harvest` / `last_harvest_result` into `watchlist-state.yaml`, and sets `ingestion_log_ref` on the change row it logs to `interaction-log.yaml`. The handler writes none of these itself.
7. **Honour `dry_run`.** With `dry_run=True` the handler MUST NOT write any file and should return what it would have done; `check --dry-run` exercises this path.
8. **Be read-only upstream by default.** A handler that must write upstream declares `writes_upstream: true` in its pack, requires explicit opt-in on the row, and logs every write to `upstream-writes.yaml` — per the no-silent-upstream-writes floor in `AGENTS.md`.
9. **Refresh affected domains.** Declare `affected_domains` (pack `harvest.affected_domains` or the class attribute) and call `self._refresh_domains()` at the end of a successful run per [`contracts/domain-reflection.md`](domain-reflection.md); failure is best-effort (errors land in `RunResult.notes`, never fail the harvest).

### 2.4 Cadence, capture mode and budgets

- **`cycles`** on the ref (default `config.preferences.watchlist.cycles`, normally `[daily-update]`) says which cadence runs a watcher is eligible for (built-in cycles nest: a slower run covers every faster one); a pack may set `defaults.cycles`. `schedule` (`daily | weekly | monthly | ...`) and `capture_mode` are first-class row fields a pack supplies as defaults and a row may override.
- **`capture_mode: manual` never auto-runs.** A cadence-triggered `check` may detect, but it never dispatches a manual watcher's harvest; only an explicit user-invoked `watchlist harvest --id <id>` may. Nothing in the tool widens `manual` to `automatic` or shortens `schedule` on its own initiative; the 0.19.0 migration's validate step asserts each folded source kept its pre-migration cadence, and the 0.20.0 change of the shipped `simplefin` defaults to daily / automatic is a user-directed pack default applied by a user-run migration (`contracts/watchlist.md` § 8), not a tool decision.
- **Budgets are enforced before dispatch, not recorded after the fact.** Before any handler call the watchlist checks `calls_today` (reset when `calls_today_date` is not today) against `budget.max_calls_per_day` and the time since `last_harvest` against `budget.min_interval_minutes`. Either failing skips the harvest for this run, reported as `budget_exceeded`, with fingerprint and `last_harvest` left untouched so the next eligible run retries cleanly. `max_window_days` bounds how far back a single call may reach.
- **Heavy ingestion is opt-in and deferred.** Backfilling years of history is a separate explicit invocation (`backfill_window_days` on the row), never a side effect of a cadence run.

### 2.5 Idempotency and dedup

Every handler's normalized output rows include an `external_id` field carrying the upstream-stable identifier. Skills that read indexes (especially the Bookkeeper, the Concierge, the daily-update) MUST treat `external_id` as the primary dedup key when present, falling back to natural keys (date + amount + payee for transactions, date + summary for events) only if `external_id` is missing.

### 2.6 Failure handling

- A watcher whose source cannot be reached reports `unreachable`: `error_streak` increments, the fingerprint is untouched, eviction is suppressed (a down server is not a dead source). Harvest errors land in `RunResult.errors` and therefore in `ingestion-log.yaml`.
- The cadence skill surfaces streaks under "Sources needing attention"; a run that ends with errors also files a `target: tailor` action-signal so failures reach the Supertailor without a manual filing.
- Auth repair is the pack's `probe.setup_hint` (e.g. `uv run python superagent/watchers/simplefin/claim.py`); `watchlist probe <id>` re-checks it.

### 2.7 Read path for skills

Skills should **never** call an MCP or CLI tool directly when the answer is already in a local index or domain file. The read order (also `contracts/local-first-read-order.md`):

1. **Local YAML indexes** (`_memory/*.yaml`) for structured queries.
2. **Local Domain history files** (`Domains/<domain>/history.md`) for narrative recall.
3. **Local interaction-log / ingestion-log / events stream** for "what happened recently"; `watch_changed` events and `context.yaml.alerts` for "what moved".
4. **Live MCP / CLI source** ONLY for the strictly newer slice past the watcher's `last_harvest` (or `last_success` for detect-only watchers), OR for live state (current balance, current sleep score) where staleness matters.

When the live call happens, capture-through MUST run (per `contracts/capture.md` and the email-capture floor) so the next read is local.
