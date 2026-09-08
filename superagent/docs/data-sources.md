# Superagent — Data sources (the watchlist)

---

## Table of Contents

- [Superagent — Data sources (the watchlist)](#superagent--data-sources-the-watchlist)
  - [The model](#the-model)
  - [Adding a source](#adding-a-source)
  - [Writing your own pack](#writing-your-own-pack)
  - [Suggested utility MCPs](#suggested-utility-mcps)
  - [Shipped packs](#shipped-packs)
    - [simplefin](#simplefin)
    - [gmail](#gmail)
    - [url](#url)
    - [cmd](#cmd)
    - [path](#path)
    - [subagent](#subagent)
  - [Standalone importers](#standalone-importers)
    - [csv](#csv)

---

This is the reference for how Superagent reaches external data. Every external source — a bank feed, a Gmail query, a permit portal, a page that occasionally changes — is a **watcher** (internal synonym: **ext-source**) on the **watchlist**. The normative contract is [`contracts/watchlist.md`](../contracts/watchlist.md); the user-facing skill is `skills/watch.md`; the tool is `uv run python -m superagent.tools.watchlist` (alias `superagent.tools.ext_sources`).

## The model

Every source decomposes into two stages:

| Stage | Question | Cost | Per-source code |
|---|---|---|---|
| **detect** | "did it move?" | cheap | none — declarative |
| **harvest** | "pull the records and normalize them into a typed index" | expensive | only where a typed index is fed (`simplefin` today) |

The watchlist owns **detect** for every source and invokes **harvest** only where a handler exists and the watcher's `capture_mode` allows it. One lifecycle — scheduling, throttling, failure streaks, eviction, budgets, reporting — implemented once.

**Registry = a folder of watcher files.** Each watcher is one `Sources/Watchlist/<name>.ref.md` (path overridable via `config.preferences.watchlist.path`); a `.ref.md` is a watcher definition and nothing else. Schema `ref_version: 2`: `title`, `description`, `related_*`, `tags`, `added_by` / `added_at`, and a `watch:` block that carries both the detect settings (`pack` or `type`, `enabled`, `cycles`, `evict_after_days`, `min_check_interval_minutes`, `schedule`, `capture_mode`) and **what** is watched — a `url:` / `path:` / `cmd:` / `prompt:` locator for a bare watcher (`type: url | path | cmd | subagent`), `params` for a pack instance (`gmail` is always a pack instance; `query` is its locator). There is no `kind` / `source` / `ttl_minutes`. The tool writes Title_Case names (`enable --id gmail-bills` → `Gmail-Bills.ref.md`; `Simplefin.ref.md`); name your own files as you like (`HA.ref.md`, `ha.ref.md` — kept as written, never renamed or warned about); the `id` is the stem lowercased (`simplefin`, `gmail-bills`, `ha`) — the state key and the handle (`watch:<id>`). `Sources/Watchlist/` is a reserved *name* with user-editable *contents* — hand-author, edit, or delete refs freely; `sources_index.py refresh` indexes them alongside your documents.

**State = one machine-owned file.** `_memory/watchlist-state.yaml` holds per-watcher `status`, `last_checked`, `last_changed`, `last_success`, `fingerprint`, `error_streak`, `last_outcome`, and — for harvest-bearing watchers — `last_harvest`, `calls_today`. Never hand-edit it.

**Defaults** live in `config.yaml` under `preferences.watchlist` (`path`, `cycles`, `evict_after_days`, `allow_cmd`, `min_check_interval_minutes`).

**Detect types** (implemented once in the tool): `url` (ETag / Last-Modified, then a scoped content hash), `path` (file hash or directory mtime + count), `cmd` (stdout hash; disabled unless `preferences.watchlist.allow_cmd: true`), `subagent` (the agent runs a read-only prompt and `stamp`s a one-line delta), `gmail` (a **live** Gmail API search using the token the Gmail MCP saved; results capture-through into the local email archive), `harvest` (the pack's handler *is* the detect). Outcomes: `changed | unchanged | indeterminate | unreachable`.

**Cadence wiring.** `daily-update` runs `check --cycle daily-update` (`simplefin` lands here — harvested daily and automatically, budget-gated); `weekly-review` / `monthly-review` run their own cycles, which nest — a weekly check also covers every daily watcher and a monthly check covers all three tiers, so the cadences never have to be run one after another (throttles and budgets make same-day overlap a no-op). `whatsup` never checks — it reads the alerts the last check wrote to `context.yaml.alerts` and labels their age. Changed watchers append an `interaction-log.yaml` row (`action: watch_change_detected`, derived into the events stream as `kind: watch_changed`); harvest runs keep appending to `ingestion-log.yaml`.

**Packs** are self-contained folders: `superagent/watchers/<id>/pack.yaml` (detect config, optional `harvest.handler`, declarative `probe:`, `auth:`, `budget:`, `defaults:`) plus, only when real code is needed, a same-folder `handler.py` and `README.md`. The same folder shape works under `workspace/_custom/watchers/<id>/` — the user's overlay, discovered alongside the shipped packs; on an id collision the custom folder wins and the tool announces it: *"Using `_custom/watchers/<id>` (overrides framework pack)."*

## Adding a source

```bash
# what is set up on this machine? (no args = every pack's probe: block; --all = every registered watcher; <id> = one)
uv run python -m superagent.tools.watchlist probe

# enable a pack — writes Sources/Watchlist/<Title_Case>.ref.md from the template
uv run python -m superagent.tools.watchlist enable simplefin --id simplefin                                          # → Simplefin.ref.md
uv run python -m superagent.tools.watchlist enable gmail --id gmail-bills --param query="label:Bills newer_than:30d"  # → Gmail-Bills.ref.md

# inspect
uv run python -m superagent.tools.watchlist list [--status active|evicted|disabled]

# run a cycle (cadence skills do this); --report renders the briefing block
uv run python -m superagent.tools.watchlist check --cycle daily-update --report [--no-harvest] [--dry-run]

# on-demand harvest (also the only way a watcher pinned to capture_mode: manual ever pulls)
uv run python -m superagent.tools.watchlist harvest --id simplefin [--dry-run] [--backfill]

# record a dispatched subagent's outcome
uv run python -m superagent.tools.watchlist stamp --id <id> --changed|--unchanged|--unreachable --note "<one-line delta>"
```

A source with no pack is a **bare watcher**: drop a `<name>.ref.md` (any name you like) into `Sources/Watchlist/` with a `watch:` block whose `type` is `url`, `path`, `cmd`, or `subagent` and the one locator field that type needs inside `watch:` (`url:`, `path:`, `cmd:`, `prompt:`) — `gmail` is pack-provided and always a pack instance (`pack: gmail` + `params: {query: ...}`; a bare `type: gmail` is a load error) — or say "watch this page" and let the `watch` skill write it. For example:

```yaml
# Sources/Watchlist/Solar_Permit.ref.md  →  watch:solar_permit
---
ref_version: 2
title: "City — solar permit status"
related_project: solar
watch:
  type: url
  url: "https://permits.example.gov/status?id=12345"
  selector: "#status-panel"
  evict_after_days: 30
---
```

Nothing is fetched on demand and nothing is cached: detect keeps its fingerprint in `_memory/watchlist-state.yaml`, and a harvest's records land in the typed index the pack writes.

## Writing your own pack

1. Create `workspace/_custom/watchers/<id>/pack.yaml` with `watcher_version: 1`, `id`, `title`, `kind`, a `detect:` block, an optional `probe:` (`file_exists | cli_on_path | python_import | cmd_exit_zero | always`), `auth:`, `budget:`, and `defaults:` (`cycles`, `evict_after_days`, `schedule`, `capture_mode`).
2. If the source feeds a typed `_memory/*.yaml` index, add `handler.py` in the same folder implementing `IngestorBase.run(config_row, dry_run=False)` (`superagent/tools/ingest/_base.py` is the contract) and reference it from `harvest:`; declare `writes:` and `affected_domains:`.
3. Enable it: `uv run python -m superagent.tools.watchlist enable <id> --id <watcher-id>`.
4. Share it by copying the folder. A shared pack's `handler.py` is importable Python — read it before you trust it, exactly as you would a script from someone else.

Framework-side packs (`superagent/watchers/<id>/`) go through the same review as any other core code and are listed in `superagent/watchers/_manifest.yaml`.

---

## Suggested utility MCPs

These do not feed personal-life data into Superagent, but they are useful workspace companions for setup, verification, and debugging.

- **browserctl CLI** (not an MCP) - browser automation for live page checks, screenshots, accessibility snapshots, and web smoke tests. Replaces the former Playwright MCP.
  - **Install**: nothing to add to MCP config; the tool ships at `superagent/tools/browserctl.py` (see the `browserctl` skill). One-time per machine: `uv run --with playwright playwright install chromium`.
  - **Probe**: `uv run superagent/tools/browserctl.py launch --profile default --headless --url https://www.google.com` then `snapshot`, and confirm the page title returns; `stop` when done.

---

## Shipped packs

### simplefin

- **Pack**: `superagent/watchers/simplefin/pack.yaml`; detect type `harvest` (detect and harvest are the same call). Handler: `superagent/watchers/simplefin/handler.py` (implements `IngestorBase.run`).
- **Kind**: API.
- **Underlying tool**: [SimpleFin Bridge](https://beta-bridge.simplefin.org/) ($1.50/month or $15/year, covers up to 25 institutions and 25 apps; read-only by design).
- **Harvests**: bank, credit-card, brokerage, and mortgage transactions; balances; account metadata.
- **Writes to**:
  - `_memory/transactions.yaml` (canonical normalized target).
  - `_memory/accounts-index.yaml.<acct>` balances.
  - `_memory/watchlist-state.yaml` (`simplefin` row: `last_harvest`, `last_harvest_result`, `calls_today`).
  - `_memory/ingestion-log.yaml` (per-run rows).
- **Reads / cross-links**: `accounts-index.yaml.<acct>.simplefin_account_id` — set this field on each account row to its SimpleFin account UUID so the reconciler can join transactions to bills' `pay_from_account` (`harvest --id simplefin --dry-run` lists the feed's accounts without writing).
- **Defaults** (pack `defaults:`, since 0.20.0): `schedule: daily`, `capture_mode: automatic`, `cycles: [daily-update]`, `evict_after_days: null` (a quiet bank feed is quiet, not dead), `min_check_interval_minutes: 60`. `daily-update`'s `check` harvests the feed every day on its own, within the budget below; `harvest --id simplefin` is the on-demand pull ("refresh my transactions"). Pin `capture_mode: manual` in `Simplefin.ref.md`'s `watch:` block to be asked before every pull instead — a present key beats the pack default, and the tool never changes it on its own.
- **Budget** (enforced *before* dispatch): `max_calls_per_day: 24`, `min_interval_minutes: 60`, `max_window_days: 90`. A harvest withheld by budget is reported as `budget_exceeded` and state is left untouched so the next eligible run retries cleanly.
- **Install**:
  1. Sign up at `bridge.simplefin.org` ($1.50/mo or $15/yr).
  2. Connect your bank institutions through their hosted UI.
  3. Generate a "Setup Token" for an app called `superagent`.
  4. `uv run python superagent/watchers/simplefin/claim.py <SETUP_TOKEN>` — claims the token into a long-lived Access URL stored at `workspace/_memory/sensitive/simplefin-credentials.yaml` (mode 600).
  5. `uv run python -m superagent.tools.watchlist enable simplefin --id simplefin` — writes `Sources/Watchlist/Simplefin.ref.md`.
- **Probe** (declarative, `file_exists`): `_memory/sensitive/simplefin-credentials.yaml` exists; `setup_hint` points at `superagent/watchers/simplefin/claim.py`.
- **Run**: every `daily-update` (`check --cycle daily-update`) harvests it automatically; on demand, `uv run python -m superagent.tools.watchlist harvest --id simplefin` (incremental delta with 3-day overlap). A full backfill is `uv run python -m superagent.tools.watchlist harvest --id simplefin --backfill` (budget-counted like any harvest). The handler's own CLI keeps only `--dry-run` and the local `--reconcile` repair and refuses live runs, so nothing bypasses the watchlist state.
- **Reconciliation**: `uv run python -m superagent.tools.reconcile_transactions [--days N] [--json]` — surfaces matched / missed bills and recurring-charge candidates not yet tracked in `bills.yaml` / `subscriptions.yaml`. The `weekly-review` skill calls this from its Bookkeeper pass.
- **Pending → posted matching**: every run pairs each stored pending row with its later posted twin (same account, same sign, absolute amount delta ≤ $1.00, within the reconcile date window) and marks the pending row `superseded_by: <posted external_id>`. A pending row still orphaned after `stale_pending_days` (a pack `harvest.defaults` key, overridable in the `simplefin` ref's `watch:` block; handler default 14, measured from `transacted_at`) is flagged `stale_pending: true`. Both flags keep the row verbatim for audit; `reconcile_transactions` skips `superseded_by` and `stale_pending` rows so neither double-counts against bills.
- **Caveats**:
  - Per-day budget: SimpleFin allows ≤ 24 requests/day per account — the pack's `budget:` block mirrors it.
  - Per-call window: ≤ 45 days (warning) / 90 days (hard cap). The handler auto-chunks larger windows.
  - A bad institution connection has made `/accounts` take 29–60 s; the check `--timeout` bounds the wait and reports `unreachable` rather than hanging the briefing.
  - Employer 401(k) plans (Fidelity NetBenefits) often expose balances as `$0.00` to aggregators — verify directly at the provider when those numbers look wrong.
  - The Access URL embeds HTTP Basic credentials (`https://USER:PASS@host/`). Treat it as banking credential material; it lives in `_memory/sensitive/` for that reason.
  - Detect *is* the expensive call here, so "skip harvest when quiet" saves nothing for this pack; the win is the unified lifecycle and the pre-dispatch budget guard. A cheaper `/accounts`-only detect is a candidate optimization.

### gmail

Two complementary paths, both live, both feeding one local archive:

- **Capture-on-touch archive** at `workspace/_memory/email/`, governed by [`contracts/email-capture.md`](../contracts/email-capture.md). Every email the agent reads (`read_email`), sends (`send_email`), or lists (`search_emails`) through the Gmail MCP is mirrored locally as a side-effect of normal work — full per-message JSON for read/sent, metadata stubs for search hits — plus the append-only sidecar `_messages.jsonl` (truth) and the `_index.yaml` counter cache. The bridge is `superagent/tools/email/archive_hook.py`: wired as a tool-call hook where the harness has one (Cursor `afterMCPExecution` with `--kind=auto`; Claude Code `PostToolUse` with three `mcp__gmail__<tool>` matchers — contract § 8.1), and invoked by the agent directly with `--raw` on every harness per the hook-free floor in [`rules/email-capture-fallback.md`](../rules/email-capture-fallback.md). Read-side: skills scan the archive first (`archive.find` / `find_by_query`) and only go live for the strictly-newer slice.
- **The `gmail` watcher pack** — `superagent/watchers/gmail/pack.yaml`, detect type `gmail`, **parameterized** (`params.query`, required: a Gmail search query — always bound it with `newer_than:` so the result set stays small). Each check is one **live** `messages.list` call against the Gmail API using the OAuth token the MCP saved at `~/.gmail-mcp/credentials.json`; the fingerprint is the newest `internalDate` + result count; every result set is passed through `archive.maybe_capture_stubs`, so a check also grows the archive exactly as `search_emails` does. This is a targeted query with capture-through, **not** bulk fetch — there is no bulk Gmail ingestor any more. One shipped pack backs many rows (`gmail-bills`, `gmail-school`, `gmail-vet`).
- **Verify**: `uv run python -m superagent.tools.email.archive stats` prints counts derived from `_messages.jsonl` (and repairs the `_index.yaml` cache if it drifted); `... archive find <message-id>` confirms a single capture; `uv run python -m superagent.tools.watchlist list` shows the gmail watchers and their last outcome.
- **Kind**: MCP for the chat-time tool surface (and therefore for the archive); API for the watcher. Both share one OAuth grant.
- **Underlying tools**:
  - **Chat-time MCP** (every harness): the [`@gongrzhe/server-gmail-autoauth-mcp`](https://github.com/GongRzhe/Gmail-MCP-Server) server exposes 19 Gmail tools (search_emails, read_email, modify_email, send_email, ...) for interactive use during a chat. Configured in the repo-local `.cursor/mcp.json` (Cursor) / `.mcp.json` (Claude Code); see step 4 below.
  - **Watcher**: `tools/watchlist.py`'s `gmail` detect talks to Google's Gmail API directly via `google-api-python-client`, reusing the MCP's saved token (token loading and search helpers salvaged from the former bulk ingestor).
- **Captures**: whatever the agent touches — full message (headers, body, labels, attachment metadata) on read/sent; id / subject / from / date / snippet stubs on search hits and on watcher matches. Attachments are metadata-only unless the user asks, the message looks like a receipt, or the bytes are the task's primary data (contract § 5).
- **Writes to**: `_memory/email/<YYYY>/<MM>/<DD>/<YYYY-MM-DD>_<in|out>_<from_slug>_<subject_slug>_<hash8>.json`, `_memory/email/_messages.jsonl`, `_memory/email/_index.yaml`, lazily `_memory/email/attachments/`; the watcher additionally writes its row in `_memory/watchlist-state.yaml` and a `context.yaml.alerts` row on change.
- **Future writes** (separate skills, reading the archive):
  - `_memory/contacts.yaml` — auto-fill from senders not yet in contacts.
  - `_memory/bills.yaml` — detect "your statement is ready" / "amount due" patterns.
  - `_memory/subscriptions.yaml` — detect "Welcome to <service>" / "your subscription has renewed".
  - `_memory/appointments.yaml` — detect "your appointment is confirmed" / "see you on".
  - `Domains/<inferred>/history.md` — narrative entries for high-signal threads.
- **Install** (one-time, ~25 min):
  1. **Google Cloud project + OAuth Desktop client** ([console.cloud.google.com](https://console.cloud.google.com/) → enable Gmail API, configure OAuth consent screen as External + add yourself as Test User, create Desktop OAuth client). Download the JSON; `~/.config/google-mcp/oauth-client.json` is the convention.
  2. **Install the gongrzhe MCP globally** (avoids a 17-second `npx -y` cold-start that exceeds Cursor's 10-second startup timeout):
     ```bash
     sudo npm install -g @gongrzhe/server-gmail-autoauth-mcp
     ```
  3. **Stage the OAuth client where the MCP looks for it, then run auth** (browser opens; sign in; grant):
     ```bash
     mkdir -p ~/.gmail-mcp
     cp ~/.config/google-mcp/oauth-client.json ~/.gmail-mcp/gcp-oauth.keys.json
     npx -y @gongrzhe/server-gmail-autoauth-mcp auth
     # tokens land at ~/.gmail-mcp/credentials.json — the watcher reuses this file
     ```
  4. **Wire the MCP into the harness.** Each harness reads its own repo-local runtime file — `.cursor/mcp.json` (Cursor) / `.mcp.json` (Claude Code) — both regular-file copies of the committed templates `.cursor/mcp.json.cursor` / `.mcp.json.claude` (per `AGENTS.md` § "Harness setup"; `init` creates them and detects drift). The server key must be `gmail` (the archive's `--kind=auto` discriminates on it). Then fully quit and reopen the harness:
     ```json
     {
       "mcpServers": {
         "gmail": { "command": "/usr/local/bin/gmail-mcp" }
       }
     }
     ```
     Verify: `gmail` shows connected with 19 tools; smoke-test in a fresh chat ("list my 3 most recent emails"), then `uv run python -m superagent.tools.email.archive stats` should show the stubs that search just captured.
  5. **Hook wiring for the archive** (optional enhancement): `init` writes the Cursor `afterMCPExecution` entry to `.cursor/hooks.json` and the Claude Code `PostToolUse` matchers to `.claude/settings.json` per `contracts/email-capture.md` § 8.1. Nothing depends on them — the agent runs `archive_hook --raw` after every Gmail call regardless (`rules/email-capture-fallback.md`).
  6. **Register a watcher**: `uv run python -m superagent.tools.watchlist enable gmail --id gmail-bills --param query="label:Bills newer_than:30d"`. Repeat with a different `--id` / query per slice you care about.
- **Defaults** (pack): `cycles: [daily-update]`, `evict_after_days: 30`, `min_check_interval_minutes: 60` (re-running `daily-update` by hand several times a day does not re-query Gmail).
- **Scopes granted**: `gmail.modify` + `gmail.settings.basic`. The MCP requests these (not configurable). The archive only mirrors responses the agent already received; the watcher uses ONLY the read subset (`messages.list`, `messages.get`); the framework's "no remote write" rule (`AGENTS.md` § "Privacy and data location") enforces read-only at the skill level. Any future skill that needs actual modify capability must declare `writes_upstream: true` in its frontmatter and ask per-call confirmation per the same rule.
- **Probe** (declarative, `file_exists`): `~/.gmail-mcp/credentials.json` exists; `setup_hint`: authorize the MCP once (step 3) and the watcher reuses its token. A check with the token file absent reports `unreachable` with that hint.
- **Caveats**:
  - The archive grows by side-effect of work the agent does and by watcher matches; it is not a backfill. `archive find <message-id>` proves a record exists, not that it has content — a stderr warning from `archive_hook --raw --kind=sent` (missing / malformed `--request-json`) means the capture was refused and must be re-run.
  - Gmail's API quota is huge (1B units/day per project); the per-watcher `min_check_interval_minutes` is for politeness and briefing stability, not quota.
  - First-time `npx -y` for the MCP downloads ~30 MB of npm packages; pre-install globally (step 2 above) to skip this on every harness cold start.
  - Token refresh happens in-memory on the watcher side (it doesn't write back to `~/.gmail-mcp/credentials.json`). Refresh tokens last indefinitely unless the user revokes via [myaccount.google.com/permissions](https://myaccount.google.com/permissions).
  - If the MCP smoke test fails in your harness, the watcher will too — both share the same OAuth grant.

### url

- **Pack**: `superagent/watchers/url/pack.yaml`; detect type `url`; parameterized by the target URL (`watch.params.url`), optional `selector:` (CSS selector to scope the hash) and `ignore_patterns:` (regexes stripped before hashing).
- **Detect**: a conditional `GET` carrying the stored validators (`If-None-Match` / `If-Modified-Since`); `304` means unchanged with no body downloaded. A `200` body (capped at 2 MB, http/https only, no credentials in the URL) is always normalized and hashed — strip `<script>` / `<style>` / comments, apply `selector` / `ignore_patterns` — so a rotated `ETag` alone is never reported as a change. `min_change_interval_minutes` keeps a flapping page from alerting more than once per window.
- **Install**: nothing. **Probe**: `always`.
- **Enable**: `uv run python -m superagent.tools.watchlist enable url --id <slug> --param url=<https://...>`, or hand-author a bare ref in `Sources/Watchlist/` with `watch.type: url` and `watch.url:` (example under "Adding a source").
- **Defaults**: `cycles: [daily-update]`, `evict_after_days: 30` (revive an evicted watcher with `status: active` in its ref).
- **Caveats**: pages that embed CSRF tokens, session ids, or timestamps will churn without a `selector:` — scope the hash to the panel you actually care about. Sites that require a login are `subagent` territory.

### cmd

- **Pack**: `superagent/watchers/cmd/pack.yaml`; detect type `cmd`; parameterized by `cmd` (required — the shell command, verbatim). Any CLI-reachable source becomes a watcher: `git ls-remote <url> HEAD`, `gh api ...`, a `curl` against a JSON status endpoint.
- **Detect**: runs the command with a timeout, requires exit 0, fingerprints the sha256 of stdout. Non-zero exit or timeout is `unreachable`, never `changed`.
- **Gate**: **off by default.** The tool refuses to run any `cmd` watcher until `config.preferences.watchlist.allow_cmd: true`; until then every check reports `unreachable` naming the flag. The same gate covers `probe.kind: cmd_exit_zero`. The command string comes only from the ref (`watch.params.cmd` on a pack instance, `watch.cmd` on a bare watcher) and is never templated from anything fetched over the network; it must be read-only.
- **Enable**: `uv run python -m superagent.tools.watchlist enable cmd --id <slug> --param cmd="<command>"`, or hand-author a bare ref with `watch.type: cmd` and `watch.cmd:` (the command, verbatim).
- **Defaults**: `cycles: [daily-update]`, `evict_after_days: 30`, `min_check_interval_minutes: 60` (raise it for rate-limited CLIs).

### path

- **Pack**: `superagent/watchers/path/pack.yaml`; detect type `path`; parameterized by `path` (required — absolute or `~`-relative file or directory).
- **Detect**: a file is fingerprinted by the sha256 of its content; a directory by its newest modification time plus a recursive entry count, so a new export dropped into a folder registers on the next cycle. Local reads only; a missing path is `unreachable`.
- **Install**: nothing. **Probe**: `always`.
- **Enable**: `uv run python -m superagent.tools.watchlist enable path --id <slug> --param path=<~/Downloads/bank-exports>`, or hand-author a bare ref with `watch.type: path` and `watch.path:`.
- **Defaults**: `cycles: [daily-update]`, `evict_after_days: 30`.
- **Caveats**: never watch a file a harvest handler writes (for example `_memory/transactions.yaml`) — the watcher would fire on the harvest's own write. A folder watched for bank CSVs still needs a manual `tools/ingest/csv.py --file` import today; the `csv-drop` pack that would harvest it automatically is postponed.

### subagent

- **Pack**: `superagent/watchers/subagent/pack.yaml`; detect type `subagent`; parameterized by `prompt` (required — what to look at and how to report; must be read-only and ask for ONE line: the delta, or "no change").
- **Detect**: the tool never spawns agents. `check` emits a **dispatch spec** for each eligible watcher; the calling agent runs it as a read-only subagent per `rules/subagents.md` (read the thing, compare against the last stamped note, return ONE line; never submit a form or write upstream; capture-through anything legitimately encountered) and records the outcome with `stamp --id <id> --changed|--unchanged|--unreachable --note "<delta>"`. The stamped note is the next fingerprint; it is rendered as quoted data in briefings and never concatenated into a later prompt.
- **Install**: nothing beyond whatever the prompt needs (typically a `browserctl` profile with a saved login — see `workspace/_custom/skills/browserctl.<app>.md`). **Probe**: `always`.
- **Enable**: `uv run python -m superagent.tools.watchlist enable subagent --id <slug> --param prompt="<instructions>"`; or hand-author a bare ref with `watch.type: subagent` and `watch.prompt:`.
- **Defaults**: `cycles: [daily-update]`, `evict_after_days: 45`; set `expires: YYYY-MM-DD` for a hard end-of-life (a portal for a project that finishes).
- **Caveats**: prompt injection reaches further here than anywhere else — a watched page can try to steer the subagent. Prompts must be read-only; notes are length-capped and control-character-stripped by the tool. This is the escape hatch for the long tail, not the default: prefer `url` when the page is public.

---

## Standalone importers

### csv

- **Module**: `superagent/tools/ingest/csv.py` — a standalone importer with its own `--file` CLI; not wrapped in a watcher pack (a `csv-drop` folder watcher is a postponed roadmap item).
- **Kind**: file.
- **Underlying tool**: built-in (Python `csv` stdlib + format auto-detect).
- **Imports**: a single CSV passed via `--file <path>`. Auto-detects column headers from Chase, Bank of America, Wells Fargo, American Express, Schwab, Fidelity, plus a generic fallback.
- **Writes to**: `_memory/transactions.yaml` (same shape as the `simplefin` harvest, same dedup key), plus a row in `_memory/ingestion-log.yaml`.
- **Install**: nothing.
- **Run**: `uv run python -m superagent.tools.ingest.csv --file <path> [--dry-run]`.
- **Caveats**: amount sign convention varies by bank; the importer normalizes to "positive = inflow, negative = outflow" but warns when ambiguous.
