# Superagent — Data sources catalogue

---

## Table of Contents

- [Superagent — Data sources catalogue](#superagent--data-sources-catalogue)
  - [Maturity legend](#maturity-legend)
  - [Email and calendar](#email-and-calendar)
    - [gmail](#gmail)
    - [icloud-mail](#icloud-mail)
    - [outlook](#outlook)
    - [google-calendar](#google-calendar)
    - [icloud-calendar](#icloud-calendar)
    - [outlook-calendar](#outlook-calendar)
  - [Reminders and notes](#reminders-and-notes)
    - [apple-reminders](#apple-reminders)
    - [apple-notes](#apple-notes)
    - [obsidian](#obsidian)
    - [notion](#notion)
  - [Finance](#finance)
    - [plaid](#plaid)
    - [monarch](#monarch)
    - [ynab](#ynab)
    - [csv](#csv)
  - [Health and wearables](#health-and-wearables)
    - [apple-health](#apple-health)
    - [whoop](#whoop)
    - [strava](#strava)
    - [garmin](#garmin)
    - [oura](#oura)
    - [fitbit](#fitbit)
  - [Smart home and vehicles](#smart-home-and-vehicles)
    - [home-assistant](#home-assistant)
    - [smartthings](#smartthings)
    - [tesla](#tesla)
  - [Communications](#communications)
    - [imessage](#imessage)
    - [slack](#slack)
  - [Files, media, location](#files-media-location)
    - [photos](#photos)
    - [gmaps-timeline](#gmaps-timeline)

---

This is the master catalogue of every data source Superagent supports (or stubs out for future implementation). Each entry covers: what gets ingested, where it writes, install / auth steps, the probe that tells you whether it's available, and any known caveats.

Run `python3 -m superagent.tools.ingest._orchestrator setup` to probe every source on your machine and get an availability table. Run `... run --source <name>` to ingest one. Run `... run --all` to refresh everything that's enabled.

## Maturity legend

| Tag | Meaning |
|---|---|
| **shipped** | Real ingestor implementation exists in `superagent/tools/ingest/<source>.py`. |
| **stub** | Listed in registry; falls back to `_stubs.StubIngestor` for now. Implementing it just means adding a real `<source>.py` that subclasses `IngestorBase`. |
| **community-best** | A reasonable third-party MCP / CLI exists; Superagent's per-source implementation can be a thin wrapper. |

---

## Email and calendar

### gmail

- **Maturity**: stub (priority: high — see `roadmap.md` LOE-S "Implement gmail ingestor").
- **Kind**: MCP.
- **Underlying tool**: [Google Workspace MCP](https://github.com/epaproditus/google-workspace-mcp-server).
- **Ingests**: messages from the user's primary inbox, sent folder, and any opted-in labels.
- **Writes to**:
  - `_memory/interaction-log.yaml` — one row per substantive thread per recipient.
  - `_memory/appointments.yaml` — auto-detected from "your appointment is confirmed" / "see you on" emails (regex + classifier).
  - `_memory/bills.yaml` — auto-detected from "your <utility> statement is ready" / "amount due" emails.
  - `_memory/subscriptions.yaml` — auto-detected from "Welcome to <service>" / "your subscription has renewed" emails.
  - `Domains/<inferred>/history.md` — narrative entries for high-signal threads.
- **Install**: install the Google Workspace MCP per its README; OAuth your Google account; grant `gmail.readonly` and `gmail.metadata` scopes (the ingestor never sends or modifies).
- **Probe**: tries to call `list_messages` with `maxResults: 1` and discards the result.
- **Caveats**: Gmail's API is generous but rate-limited; the ingestor respects `max_items_per_run` (default 200) and exponentially backs off on 429.

### icloud-mail

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [iCloud MCP](https://github.com/iteratio/icloud-mcp) (IMAP via Apple credentials in macOS Keychain).
- **Ingests**: same shape as `gmail`, against an iCloud Mail account.
- **Install**: install iCloud MCP; create an app-specific password in Apple ID settings; the MCP stores it in macOS Keychain.
- **Probe**: lists the INBOX folder summary (UIDs only).
- **Caveats**: macOS-only. IMAP is slower than the Gmail API; first-run backfills can take longer.

### outlook

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: an Outlook MCP that speaks Microsoft Graph (e.g. the same family used by NVIDIA's MaaS Outlook MCP, but pointed at a personal Microsoft account).
- **Ingests**: same shape as `gmail`, against an Outlook.com / Microsoft 365 mailbox.
- **Install**: install the MCP; OAuth your Microsoft account.
- **Probe**: `list_messages limit=1`.
- **Caveats**: Microsoft Graph rate limits are tighter than Gmail's at low tiers; the ingestor honors `max_items_per_run` aggressively.

### google-calendar

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: same Google Workspace MCP as `gmail`.
- **Ingests**: events from primary + opted-in calendars.
- **Writes to**:
  - `_memory/appointments.yaml` — when the event matches the appointment-shape heuristic (single attendee at an external location, OR a known provider in `contacts.yaml`, OR matches one of the appointment-pattern regexes in `tools/ingest/_patterns.yaml`).
  - `Domains/<inferred>/history.md` — for substantive events.
- **Install**: same MCP as gmail; ensure `calendar.readonly` scope.
- **Probe**: `list_events maxResults=1`.

### icloud-calendar

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: iCloud MCP (CalDAV).
- **Ingests**: same shape as google-calendar against iCloud Calendar.
- **Install**: same as icloud-mail; CalDAV access uses the same app-specific password.
- **Probe**: list the default calendar's most recent event.

### outlook-calendar

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: Outlook MCP (same as `outlook`).
- **Ingests**: same shape as google-calendar against Outlook calendar.
- **Install**: same MCP; ensure calendar scope.
- **Probe**: `list_events limit=1`.

---

## Reminders and notes

### apple-reminders

- **Maturity**: **shipped** (`superagent/tools/ingest/apple_reminders.py`).
- **Kind**: CLI.
- **Underlying tool**: [`rem`](https://rem.sidv.dev/) — a sub-200ms EventKit-backed CLI for Apple Reminders.
- **Ingests**: every reminder across every list.
- **Writes to**: `_memory/todo.yaml` (one P2 task per reminder, tagged with `rem:<reminder-id>` for idempotency).
- **Install**: `curl -fsSL https://rem.sidv.dev/install | bash` then grant Reminders permission in System Settings → Privacy & Security → Reminders.
- **Probe**: `which rem` AND `rem list --json --limit 1` (returns 0 if the permission grant succeeded).
- **Caveats**: one-way only — Superagent does NOT push back to Reminders. Mark a reminder done in Reminders and the next ingest run picks up the new state on the same reminder id.

### apple-notes

- **Maturity**: stub.
- **Kind**: CLI (osascript / JXA).
- **Underlying tool**: macOS built-in `osascript`.
- **Ingests**: full text of all notes (or a subset of folders if configured).
- **Writes to**: `Domains/<inferred>/Resources/notes/<YYYY-MM-DD-slug>.md` (snapshots; one file per note per ingest run; deduplicated by note ID + content hash). Notes are working artifacts, not vault docs — `Resources/` is the right home.
- **Install**: macOS built-in; first run prompts for Automation permission.
- **Probe**: `osascript -e 'tell application "Notes" to count notes'` returns a number.
- **Caveats**: notes with attachments only get the text portion; attachments are noted but not extracted in MVP.

### obsidian

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [MCPVault](https://mcp-obsidian.org/) or [obsidian-mcp-server](https://github.com/bazylhorsey/obsidian-mcp-server).
- **Ingests**: vault metadata — note titles, tags, frontmatter, links graph. Optional: full body for tagged notes.
- **Writes to**: `_memory/obsidian-index.yaml` (a derived index built by the ingestor).
- **Install**: install MCPVault: `npx @bitbonsai/mcpvault@latest /path/to/vault`.
- **Probe**: vault path readable + at least one `.md` file at depth ≤ 2.
- **Caveats**: read-only by default. Writing back is technically supported (frontmatter-preserving) but `writes_upstream: false` in MVP — flip when you want Superagent to update vault notes.

### notion

- **Maturity**: stub.
- **Kind**: API (official Notion API).
- **Underlying tool**: official Notion REST + a thin Python wrapper.
- **Ingests**: page / database titles, properties, last-edited timestamps. Bodies on demand.
- **Writes to**: `_memory/notion-index.yaml`.
- **Install**: generate an integration token at notion.so/my-integrations; share each top-level workspace page with the integration; set `NOTION_TOKEN` env var.
- **Probe**: GET `/v1/users/me`.
- **Caveats**: Notion API rate limit is 3 req/s; the ingestor sleeps appropriately.

---

## Finance

### plaid

- **Maturity**: stub.
- **Kind**: CLI.
- **Underlying tool**: [`plaid-cli`](https://github.com/landakram/plaid-cli) or [`yapcli`](https://pypi.org/project/yapcli/).
- **Ingests**: transactions, balances, holdings across linked checking, savings, credit, brokerage accounts.
- **Writes to**:
  - `_memory/transactions.yaml` (a derived index — created by the ingestor on first run).
  - `_memory/accounts-index.yaml.<acct>.last_balance` (per-run balance snapshots).
  - `_memory/bills.yaml` and `_memory/subscriptions.yaml` (recurring-charge auto-detection cross-checks).
- **Install**: see Plaid's developer docs to obtain client_id + secret + access_token; `pip install yapcli` or `brew install plaid-cli`.
- **Probe**: `plaid-cli accounts` returns ≥ 1 linked account.
- **Caveats**: Plaid Sandbox is free; real bank links require a Plaid Production tier (which is free for personal use up to a small request budget).

### monarch

- **Maturity**: stub.
- **Kind**: CLI.
- **Underlying tool**: [`monarch-cli`](https://github.com/crcatala/monarch-cli) (unofficial; Python).
- **Ingests**: transactions, accounts, categories, budget assignments from Monarch Money.
- **Writes to**: same as Plaid.
- **Install**: `pip install monarch-cli`; log in with Monarch credentials (kept in macOS Keychain).
- **Probe**: `monarch-cli accounts list` succeeds.
- **Caveats**: unofficial wrapper — Monarch may break it without notice; the ingestor logs failures cleanly so it's always recoverable.

### ynab

- **Maturity**: stub.
- **Kind**: API.
- **Underlying tool**: official YNAB API + a thin Python wrapper.
- **Ingests**: transactions, categories, budget vs actuals.
- **Writes to**: `_memory/transactions.yaml`, `_memory/accounts-index.yaml`.
- **Install**: generate a personal access token at api.ynab.com; set `YNAB_TOKEN` env var.
- **Probe**: GET `/v1/user`.

### csv

- **Maturity**: **shipped** (`superagent/tools/ingest/csv.py`).
- **Kind**: file.
- **Underlying tool**: built-in (Python `csv` stdlib + format auto-detect).
- **Ingests**: a single CSV passed via `--file <path>`. Auto-detects column headers from Chase, Bank of America, Wells Fargo, American Express, Schwab, Fidelity, plus a generic fallback.
- **Writes to**: `_memory/transactions.yaml`.
- **Install**: nothing.
- **Probe**: always available.
- **Caveats**: amount sign convention varies by bank; the ingestor normalizes to "positive = inflow, negative = outflow" but warns when ambiguous.

---

## Health and wearables

### apple-health

- **Maturity**: stub (highest-priority for Day-1 health value).
- **Kind**: CLI.
- **Underlying tool**: [`healthsync`](https://healthsync.sidv.dev/) — parses iPhone-side Apple Health `export.zip` into SQLite at `~/.healthsync/healthsync.db`.
- **Ingests**: every datapoint Apple Health collects — steps, heart rate, weight, sleep, BP, glucose, workouts, vitals, vaccines (when manually entered).
- **Writes to**: `_memory/health-records.yaml.vitals[]` (rate-limited to one row per kind per day to keep file size sane).
- **Install**: `curl -fsSL https://healthsync.sidv.dev/install | bash`. On the iPhone: Settings → Health → Profile picture → Export All Health Data; AirDrop / iCloud Drive the resulting zip to the Mac and run `healthsync parse <export>.zip` once.
- **Probe**: `healthsync` binary on PATH AND `~/.healthsync/healthsync.db` exists.
- **Caveats**: heavy first run (years of data). Backfill window is configurable (default 365 days).

### whoop

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: a WHOOP MCP server (e.g. [mcpforwhoop.com](https://mcpforwhoop.com/)).
- **Ingests**: recovery score, strain, sleep stages, cycles.
- **Writes to**: `_memory/health-records.yaml.vitals[]` for sleep / HR; daily summaries to `Domains/Health/history.md` (or `Domains/Hobbies/history.md` if a fitness goal is active).
- **Install**: install the MCP; OAuth your WHOOP account.
- **Probe**: list-cycles call returns at least one cycle.

### strava

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: any of the 7+ available Strava MCPs.
- **Ingests**: workouts, segment efforts, kudos, route summaries.
- **Writes to**:
  - `Domains/Hobbies/history.md` — one H4 entry per workout above a duration threshold.
  - `_memory/health-records.yaml.vitals[]` — workout-summary HR averages.
- **Install**: install the MCP; OAuth Strava with `read,activity:read_all` scopes.
- **Probe**: list-activities call returns ≥ 0 activities.
- **Caveats**: Strava's auth is per-app; rate limit is 600 calls / 15 minutes.

### garmin

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [GarminMCP](https://github.com/JohanBellander/GarminMCP) — 29+ tools.
- **Ingests**: every metric Garmin Connect tracks (sleep, HR, HRV, VO2max, training-load, body-battery, weight, body composition, runs, rides, swims, hikes).
- **Writes to**: same as Strava + Whoop combined.
- **Install**: install the MCP; provide Garmin Connect credentials (stored in Keychain).
- **Probe**: `get_user_summary` for the most recent day.

### oura

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: any Oura MCP, or via the Open Wearables aggregator.
- **Ingests**: sleep, readiness, activity scores from Oura Ring.
- **Writes to**: `_memory/health-records.yaml.vitals[]`.
- **Install**: install the MCP; OAuth Oura.
- **Probe**: list-sleep call.

### fitbit

- **Maturity**: stub (no mature MCP yet).
- **Kind**: planned via Open Wearables aggregator.
- **Ingests**: same shape as Oura.
- **Install**: when an MCP ships; until then use Apple Health on iPhone (Apple Health pulls Fitbit data via the Fitbit iPhone app's Health-share toggle).
- **Probe**: stub returns NEEDS_SETUP.

---

## Smart home and vehicles

### home-assistant

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [ha-mcp](https://github.com/zorak1103/ha-mcp) — 39+ tools, full CRUD; or Home Assistant's official MCP integration.
- **Ingests**:
  - Automation / scene state (any change since last ingest → narrative entry).
  - Sensor anomalies (door left open > 1 hour; motion in kid's room at 3am; sensor offline > 24 hours).
  - Daily energy usage snapshot.
  - Thermostat schedule + actual deviations.
- **Writes to**:
  - `Domains/Home/history.md` — one H4 entry per day if anything notable.
  - `Domains/Home/Resources/ha-snapshots/<date>.json` — full state snapshot. Working / browseable artifact (not a vault document); lives in `Resources/`, not `Sources/`.
- **Install**: install Home Assistant locally or use Nabu Casa Cloud; install ha-mcp; set `HOMEASSISTANT_URL` and a long-lived access token.
- **Probe**: `GET /api/` succeeds with the auth token.

### smartthings

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [smartthings-mcp](https://github.com/technohead/smartthings-mcp).
- **Ingests**: same shape as home-assistant against Samsung SmartThings ecosystem.
- **Install**: install the MCP; OAuth SmartThings.
- **Probe**: list-locations call.

### tesla

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: [tesla-mcp](https://github.com/ysrdevs/tesla-mcp) — 96 tools.
- **Ingests**:
  - Mileage at last sync (cross-checked against `assets-index.yaml.<vehicle>.maintenance` thresholds).
  - Charging session summaries.
  - Climate state + scheduled departures.
  - Service alerts.
  - Optional: location history (the user toggles `track_location: true` per vehicle).
- **Writes to**:
  - `_memory/assets-index.yaml.<vehicle>` — current mileage, last charge, last service alert.
  - `Domains/Vehicles/history.md` — H4 entries for service alerts and charging anomalies.
  - When mileage crosses next-service threshold → adds a P2 task to `todo.yaml` and a Next-Steps bullet to `Domains/Vehicles/status.md`.
- **Install**: install the MCP; complete the Tesla Fleet API auth flow (requires a public domain to host the key file — Tesla's restriction).
- **Probe**: `vehicle_data` call returns a vehicle.
- **Caveats**: Tesla Fleet API setup is non-trivial; the install_hint surfaces a link to the canonical guide.

---

## Communications

### imessage

- **Maturity**: stub.
- **Kind**: CLI.
- **Underlying tool**: [`imessage-exporter`](https://github.com/VasimPatel/imessage-exporter) — read-only export of `~/Library/Messages/chat.db`.
- **Ingests**: messages from / to the user, filtered by an "important contact" list (the contacts in `_memory/contacts.yaml` plus any opted-in numbers).
- **Writes to**: `_memory/interaction-log.yaml` — one row per substantive thread per day (not per message — would be too noisy).
- **Install**: `pip install imessage-exporter`. macOS requires Full Disk Access in System Settings → Privacy & Security → Full Disk Access (the chat.db lives in a TCC-protected folder).
- **Probe**: `~/Library/Messages/chat.db` is readable.
- **Caveats**: privacy-sensitive — defaults to importing only contacts already in `contacts.yaml`. The user can broaden via `data-sources.yaml.imessage.scope: "all"` (then the ingestor pulls every conversation).

### slack

- **Maturity**: stub.
- **Kind**: MCP.
- **Underlying tool**: a Slack MCP for personal workspaces.
- **Ingests**: DMs, mentions in channels the user is in.
- **Writes to**: `_memory/interaction-log.yaml`.
- **Install**: install the MCP; OAuth your personal workspace(s).
- **Probe**: `auth.test` returns user info.
- **Caveats**: out-of-the-box this is intended for personal workspaces (book club, side-project, family Slack). Work Slacks should not be ingested by Superagent — that's what your work assistant is for.

---

## Files, media, location

### photos

- **Maturity**: stub.
- **Kind**: CLI.
- **Underlying tool**: [`exiftool`](https://exiftool.org/).
- **Ingests**: EXIF metadata (date, GPS, camera) for every photo in a configured library or folder.
- **Writes to**: `_memory/photo-locations.jsonl` (append-only timeline; one line per photo: `{ts, lat, lon, file_path}`).
- **Install**: `brew install exiftool`. Configure `data-sources.yaml.photos.library_path` to point at the Photos library or a folder containing photos.
- **Probe**: `exiftool` on PATH; `library_path` exists.
- **Caveats**: large libraries (>100k photos) — first run can take 10+ minutes. Backfill is split into chunks of 1000 photos per ingest run by default.

### gmaps-timeline

- **Maturity**: stub.
- **Kind**: file.
- **Underlying tool**: built-in (Python JSON parser).
- **Ingests**: Google Takeout's `Location History/Records.json` (or per-month `Semantic Location History/<YYYY>/<YYYY>_<MONTH>.json`).
- **Writes to**: `_memory/location-timeline.jsonl`.
- **Install**: nothing. User downloads location history from takeout.google.com.
- **Probe**: always available.
- **Caveats**: Google deprecated server-side Timeline in 2024; users now have to enable on-device Timeline in the Google Maps app and export from Settings → "Your Timeline" → "Export Timeline data". The ingestor handles both old and new export shapes.

---

## Adding a new data source

The full procedure is documented in `superagent/supercoder.agent.md` and `superagent/tools/ingest/_base.py`. Summary:

1. Add a row to `superagent/tools/ingest/_registry.py.REGISTRY`.
2. Create `superagent/tools/ingest/<source>.py` exporting a class `<Source>Ingestor` that subclasses `IngestorBase`.
3. Implement `probe()` (lightweight) and `run(config_row, dry_run=False)` (real work).
4. Add a row to the appropriate `data_sources_configured.<category>` block in `superagent/templates/memory/config.yaml`.
5. Add a smoke test in `superagent/tests/test_ingest_registry.py`.
6. Document the source in this catalogue.

The Supertailor's strategic pass watches for "user keeps asking about X but has no ingestor for it" patterns and proposes new ingestors as `supertailor-suggestions.yaml` rows tagged `category: new-ingestor, destination: superagent` — handing off to the Supercoder for implementation.
