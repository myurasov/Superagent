# Superagent — Roadmap

The future. Organized by **LOE tier** so you can match work to time available.

---

## Table of Contents

- [Superagent — Roadmap](#superagent--roadmap)
  - [How to read this](#how-to-read-this)
  - [LOE legend](#loe-legend)
  - [North-star themes](#north-star-themes)
  - [LOE-XS — under an hour each](#loe-xs--under-an-hour-each)
  - [LOE-S — half a day each](#loe-s--half-a-day-each)
  - [LOE-M — a few days each](#loe-m--a-few-days-each)
  - [LOE-L — a week or two each](#loe-l--a-week-or-two-each)
  - [LOE-XL — multi-week / quarter projects](#loe-xl--multi-week--quarter-projects)
  - [Vision (no LOE)](#vision-no-loe)
  - [Explicitly out of scope](#explicitly-out-of-scope)
  - [How items get prioritized](#how-items-get-prioritized)

---

## How to read this

This roadmap is structured by **LOE tier (T-shirt size)**, not by quarter. Each item carries:

- **What** — one-line description.
- **Why** — what it unlocks for the user.
- **Done when** — observable success criterion.
- **Depends on** — explicit dependencies on other items, where they exist.

The roadmap is **continuously re-prioritized** by the Supertailor's strategic pass (`tailor-review` skill, which writes to `_memory/supertailor-suggestions.yaml`). Items here are starting points; the actual sequence is set by what the user keeps tripping over.

## LOE legend

| Tier | Effort | Typical scope |
|---|---|---|
| **XS** | < 1 hour | Tiny additions, polish, doc fixes, single-file changes. |
| **S** | < 1 day | A small feature or one ingestor; clear plan, low risk. |
| **M** | 2-5 days | A meaningful feature requiring multiple files, tests, and a small redesign of an existing surface. |
| **L** | 1-2 weeks | A new subsystem (multi-user, encryption, voice). Touches many files; needs deliberate design. |
| **XL** | > 2 weeks | A new product surface (mobile UI, hosted version). Out of MVP; included for completeness. |

## North-star themes

Three themes guide which items get pulled forward:

1. **Know your life better than you do.** Anything that closes the gap between "data Superagent could see" and "data Superagent does see" is high-leverage. Most LOE-S work is in this theme — implementing more ingestors.
2. **Be ambient, not a chore.** Anything that turns a manual chore into background work — auto-capture rules, smarter surfacing windows, trial-end alerts, recurring-charge auto-detection — is high-leverage.
3. **Stay safe by construction.** Anything that strengthens the safeguards — sensitive-store options, deeper data redaction in outbound, multi-user permission boundaries — moves up the priority list when the data volume increases.

---

## LOE-XS — under an hour each

The "while you're in the workspace anyway" tier.

| ID | What | Why | Done when |
|---|---|---|---|
| XS-01 | Add a "snapshot now" CLI option to `tools/render_status.py`. | Lets the user force a re-render after manual edits to `todo.yaml`. | `--all-scopes` flag works; tests cover it. |
| XS-02 | Detect macOS dark / light theme in `whatsup` opening line. | Tiny polish — agent greets you "good morning" or "good evening" appropriately. | Greeting varies by local hour (already correct) and by `defaults read -g AppleInterfaceStyle`. |
| XS-03 | Add `--days` flag to `appointments` skill. | Easier to ask "appointments next 30 days" instead of seeing only 14. | Skill respects `--days N`. |
| XS-04 | Auto-detect timezone in `init` from `date +%Z`. | Saves the user typing it. | `config.profile.timezone` populated automatically. |
| XS-05 | Add a "weekly digest" template to `Outbox/`. | One markdown file per week, rendered by `weekly-review`, easy to print or paste anywhere. | Template + render path. |
| XS-06 | Implement `triage-overdue --priority Px` filter. | Burn through one priority bucket at a time. | Skill respects the flag. |
| XS-07 | Add a `--dry-run` flag to every `add-*` skill. | Confirm what would be captured before committing. | All `add-*` skills support `--dry-run`. |
| XS-08 | Color the `daily-update` overdue block when stdout is a TTY. | Tiny visual nudge. | ANSI red on TTY; plain on file redirect. |
| XS-09 | Add a "what's new" section to `weekly-review` for newly-discovered ingestor capabilities. | The user notices when a stub becomes real. | Section appears when any source's `description` changed since last weekly-review. |
| XS-10 | Add docstring examples to `IngestorBase`. | Makes adding a new ingestor faster. | Each abstract method has a working example in its docstring. |

## LOE-S — half a day each

The "implement one ingestor" tier — the bulk of where Superagent grows.

### Ingestor implementations (priority order)

| ID | What | Why | Done when | Depends on |
|---|---|---|---|---|
| S-01 | Implement **gmail** ingestor. | Email is the highest-leverage source by a wide margin. Auto-detects appointments / bills / subs / shipments. | `probe()` works; `run()` fetches + writes; smoke test in `test_ingest_registry.py`; covered by `tests/test_ingest_gmail_smoke.py`. | Google Workspace MCP installed. |
| S-02 | Implement **google-calendar** ingestor. | Pairs with gmail; same MCP, same OAuth. | Same. | S-01 (shared MCP setup). |
| S-03 | Implement **icloud-mail** + **icloud-calendar** + **icloud-reminders** ingestors. | Native macOS coverage; no OAuth, just an app-specific password. | Three ingestors shipped + tests. | iCloud MCP installed. |
| S-04 | Implement **apple-health** ingestor (via `healthsync` SQLite). | Year of vitals + workouts in one SQL query. | `probe()` finds `~/.healthsync/healthsync.db`; `run()` parses + normalizes; tests cover normalization. | `healthsync` installed. |
| S-05 | Implement **plaid** ingestor. | Money is the second-highest-leverage source. Reconciles against bills + subs. | `probe()` checks for `~/.config/plaid-cli/config.json`; `run()` pulls trailing window; tests use Plaid Sandbox. | `plaid-cli` or `yapcli`. |
| S-06 | Implement **monarch** ingestor. | For users already on Monarch (no extra Plaid setup). | Probe + run + tests. | `monarch-cli`. |
| S-07 | Implement **whoop** ingestor. | Sleep + recovery + strain → Health vitals + Hobbies streak. | Probe + run + tests. | A WHOOP MCP. |
| S-08 | Implement **strava** ingestor. | Workouts → Hobbies history + per-workout HR vitals. | Probe + run + tests. | A Strava MCP. |
| S-09 | Implement **oura** ingestor. | Sleep / readiness → Health vitals. | Probe + run + tests. | An Oura MCP. |
| S-10 | Implement **garmin** ingestor. | Comprehensive (29+ tools) — replaces the union of strava + whoop + oura for Garmin users. | Probe + run + tests. | GarminMCP. |
| S-11 | Implement **apple-notes** ingestor. | Notes -> per-domain Resources/notes/. Easy capture path for the user (write a note in Apple Notes; Superagent indexes it). | Probe + run + tests. | macOS osascript. |
| S-12 | Implement **obsidian** ingestor. | For Obsidian users — index the vault, cross-reference by tag. | Probe + run + tests. | MCPVault. |
| S-13 | Implement **notion** ingestor. | For Notion users. | Probe + run + tests. | Notion API token. |
| S-14 | Implement **outlook** + **outlook-calendar** ingestors. | For Microsoft-ecosystem users. | Same shape as gmail. | An Outlook MCP. |
| S-15 | Implement **imessage** ingestor (read-only, contact-filtered). | Family group threads, important contact threads. | Probe checks chat.db + Full Disk Access; run is contact-filtered by default. | `imessage-exporter`. |
| S-16 | Implement **photos** ingestor (via exiftool). | Location timeline + date-stamping. | Probe + run + tests. | `exiftool`. |
| S-17 | Implement **gmaps-timeline** ingestor. | Long-tail location coverage where photos miss. | Probe + run + tests. | nothing (file-based). |
| S-18 | Implement **home-assistant** ingestor. | Smart-home anomaly notes + thermostat / energy snapshots. | Probe + run + tests. | ha-mcp. |
| S-19 | Implement **tesla** ingestor. | Mileage threshold → auto next-service task; charging anomaly alerts. | Probe + run + tests. | tesla-mcp. |
| S-20 | Implement **ynab** ingestor. | For YNAB users. | Probe + run + tests. | YNAB token. |

### Auto-capture and surfacing rules (per `procedures.md` § 7.3 and § 8)

| ID | What | Why | Done when |
|---|---|---|---|
| S-21 | Auto-detect package shipments from email; new `packages.yaml` index + skill. | "Did the X arrive yet?" is a frequent micro-question. | New ingestor pass + index + skill + daily-update integration. |
| S-22 | Auto-detect "trial ends in N days" from email + create P1 task. | Trial-conversion is the #1 forgotten subscription. | Heuristic in gmail ingestor + task auto-creation. |
| S-23 | Auto-detect new recurring charges in transactions; weekly-review prompt. | Catches forgotten subscriptions. | Heuristic in finance ingestors + weekly-review surface. |
| S-24 | Vehicle mileage threshold → auto next-service task. | Tesla ingestor first; expandable to other vehicle MCPs. | Threshold check + task creation. |
| S-25 | "Sleep < 6h for 5 consecutive nights" → personal-signal capture. | Catches creeping sleep debt. | Pattern detection in apple_health / whoop / oura ingestor. |
| S-26 | Bank balance < threshold → P0 task + alert. | Avoids overdraft. | Threshold field in accounts-index; check after every plaid / monarch ingest. |

### Polish + tooling

| ID | What | Why | Done when |
|---|---|---|---|
| S-27 | Add `_memory/_checkpoints/<date>/` daily auto-snapshot of `_memory/`. | Roll back any "agent did something I didn't want" mishap. | Snapshot on first agent action of each day; 14-day rolling retention. |
| S-28 | Implement the `migrations/` framework + `tools/migrate.py`. | Any future schema bump needs a clean upgrade path. | Migration registry + per-version migrators + tests. |
| S-29 | Add `tools/export.py` (one-shot full workspace JSON export). | For backup, for moving machines, for "I want to inspect everything". | One-file export; round-trip test. |
| S-30 | Add `tools/import.py` (round-trip with export). | For restore, for moving machines. | One-file import; round-trip test passes. |
| S-31 | Implement `_memory/expense-categories.yaml` user-rules support (manual override of ingestor categorization). | Plaid's auto-category is rarely 100% right for a person's mental model. | Rules file + apply at read time + persist user corrections. |
| S-32 | Polish `daily-update` based on actual usage (per Supertailor's first strategic pass after a month of use). | Most user-facing surface; gets the most refinement. | Supertailor surface improvements implemented. |

## LOE-M — a few days each

| ID | What | Why | Done when | Depends on |
|---|---|---|---|---|
| M-01 | **Sensitive-store options** — first-class encryption support for `health-records.yaml`, `accounts-index.yaml`, `Outbox/handoff/`. | Today, sensitive files live alongside everything else; first-class encryption raises the floor without forcing the user into a workaround. | User can mark a file `encrypted: true` in `data-sources.yaml` (or a new `sensitive.yaml`); skills transparently decrypt on read; encryption uses age / sops / OS keychain. Tests cover round-trip. | depends on a chosen crypto backend. |
| M-02 | **Voice-first capture** — audio in (via macOS dictation or Whisper local), transcribe, route to the right capture skill. | "I just left the dentist" → automatically logs visit, marks appointment complete, asks for outcome — all from a 30-second voice memo. | New `tools/voice_capture.py` + skill that consumes it; falls through to `log-event` for routing; tests with sample audio. |
| M-03 | **iOS Shortcut pack** — capture-only flows from the phone (add-bill, add-appointment, log-symptom, mark-bill-paid, log-event quick) that append to the right YAML / markdown via the user's iCloud-Drive-synced workspace. | Bridges the "I'm not at my laptop" gap without a real mobile app. | A signed Shortcut bundle + setup instructions in `docs/`; each Shortcut uses the Files app to append to a file. |
| M-04 | **Smart calendar — appointment-shape ML classifier**, replacing the regex-based heuristic in calendar ingestors. | Higher precision on "is this calendar event an appointment or a meeting?". | Tiny on-device classifier (logistic regression on text features); training set bootstrapped from the user's own labeled events; tested in CI with synthetic data. |
| M-05 | **Email-driven capture flows** — when the gmail ingestor sees a "your dentist appointment is confirmed" email, it doesn't just add the appointment, it also pulls the prep_notes from the message body. Same for "your prescription is ready", "your package shipped", etc. | Each new flow is small but they compound — the workspace fills itself. | Flow registry in `tools/ingest/_flows.yaml` + extension to gmail ingestor + per-flow tests. |
| M-06 | **Receipt OCR** for files dropped into Inbox/. | Scan a receipt, the agent extracts payee + amount + date + line items, routes through `add-source --to-domain <inferred>` so the file lands in `Sources/documents/<category>/<asset-slug>/` with a pointer in the relevant `sources.md`. Optionally creates a bill / subscription / one-shot expense. | New `tools/ocr.py` + Inbox-watcher integration; uses local Tesseract + a small classifier. |
| M-07 | **`tools/ingest/_orchestrator.py` — parallel runs**. | Today the orchestrator runs sources serially. With 10+ enabled sources, parallelism saves several minutes per cadence. | Async orchestrator with a per-source serialization-policy file (some sources can't run in parallel — e.g. two Plaid endpoints sharing a token). |
| M-08 | **Supertailor's strategic pass — better friction-clustering** using embeddings of `user-queries.jsonl`. | Today's clustering is heuristic. Embeddings would surface true friction. | Switch to embedding-based clustering; falls back to heuristic if no embedder. |
| M-09 | **Handoff packet generator — quarterly auto-render** + diff against previous packet. | Captures the truth of "what changed since the last time I made this packet" for an executor reading it. | `monthly-review` triggers `handoff` every 3 months; diff against last `Outbox/handoff/handoff-<YYYY-MM-DD>.md`. |
| M-10 | **Custom-overlay starter kits** — example overlays the user can copy into `_custom/` (carpool-pickup-email, kids-allowance-tracker, parents-care-cadence, freelance-invoicing). | Concrete examples lower the barrier to writing your own overlays. | `templates/_custom-starters/` directory + docs. |

## LOE-L — a week or two each

| ID | What | Why | Done when |
|---|---|---|---|
| L-01 | **Multi-user vault** with proper conflict resolution. | Today's "shared workspace" mode is last-write-wins — fragile when both partners edit. A real CRDT (or per-domain ACL with sync points) makes it robust. | Workspace can be opened by 2+ Superagent instances simultaneously; merges are clean; per-domain ACLs respected. |
| L-02 | **Family mode** — shared `Domains/Family/` and `Domains/Home/` with per-member private domains (Health, Career, Self stay local). | Most households have one person who runs the admin; family-mode lets that be a shared role. | Per-member `_memory/`; shared `Domains/`; per-domain ACL; documented setup. |
| L-03 | **Read-only mobile UI** — a small static site generator that renders the workspace as a phone-friendly read-only website (deployed locally or via a personal Cloudflare Tunnel). | Phone access without a real app. | `tools/render_web.py` + deploy recipe; works offline-first. |
| L-04 | **Audit trail** — per-row change history for every YAML index (instead of only `last_updated`). | Lets the user answer "when did we change the dentist?" / "when did this bill amount change?". | Each index file gets a sibling `<file>.history.jsonl`; updates append to it; reads are unaffected. |
| L-05 | **Auto-tax-prep packet** — at year-end, sweep `_memory/transactions.yaml` for tax-deductible categories (charitable, medical, business, education), pull supporting documents from `documents-index.yaml`, render a tax-preparer-ready packet to `Outbox/taxes/<year>/`. | Saves an entire weekend in February. | Pack runs from `monthly-review` in January; renders packet + checklist + flagged-questions list. |
| L-06 | **Web search MCP integration** for `research` skill (when the IDE doesn't supply one). | Many users' AI assistants don't have a built-in web search. | Optional source in `data-sources.yaml`; research skill uses it when present. |
| L-07 | **Health-records EHR pull** — Apple HealthKit Clinical Records / Epic MyChart / Kaiser portal scraping (where APIs exist). | Massive jump in `health-records.yaml` quality without manual entry. | Per-EHR ingestors; opt-in; tested against sandbox / synthetic data. |
| L-08 | **Estate-planning sync** — the `handoff` packet's vault references can be auto-validated against 1Password / Bitwarden / Apple Keychain (does the referenced item still exist?). | Detects stale vault refs that would surprise an executor. | Pre-flight check before each `handoff` run; surfaces broken refs as P1 tasks. |
| L-09 | **Voice output** — `daily-update` and `whatsup` can speak the briefing via macOS `say` or a local TTS, optimized for "while making coffee". | "Read me my morning" hands-free. | New `--speak` flag on cadence skills; uses `say` by default; configurable voice. |
| L-10 | **Per-domain dashboards** — for power users, render a domain (e.g. Finance) as an interactive HTML dashboard with charts. | Visual access for people who think in pictures. | `tools/render_dashboard.py <domain>` → static HTML in `Outbox/dashboards/`. |

## LOE-XL — multi-week / quarter projects

| ID | What | Why |
|---|---|---|
| XL-01 | **Native macOS / iOS app** — a wrapper around the workspace that gives capture-everywhere ergonomics, push notifications for surfacing windows, and offline-first sync. | Removes the friction of "I have to open the chat to capture this thought". |
| XL-02 | **Hosted variant** — a privacy-respecting server-hosted version for users who don't have a Mac / aren't comfortable with the AI-in-IDE setup. End-to-end encrypted; user holds the keys. | Broadens the audience beyond engineers and power users. |
| XL-03 | **Plug-and-play model swap** — Superagent works with any model the user's IDE provides. Today the skills are written assuming Claude / GPT-class models. An XL effort would be to formalize the model-capability contract so smaller / local models work too. | Lets a user run Superagent on Ollama with no commercial AI cost. |
| XL-04 | **Structured "second brain" graph** — beyond `Domains/`, a graph view across the whole workspace (people ↔ events ↔ documents ↔ assets). Surfaces non-obvious connections ("the contractor who replaced the dishwasher in 2024 is the same one your sister is using for her kitchen remodel"). | Pure cognitive amplification. |
| XL-05 | **AI-assisted financial planning** — beyond bill tracking, a real "are we on track for retirement / college / down-payment?" model fed by Plaid balances + 401(k) projections + historical inflows. | The single biggest piece of personal admin AI could meaningfully take off the user's plate. |

## Vision (no LOE)

The 5-year picture, recorded so the framework has a shape to grow toward:

- **Predictive surfacing.** Today's daily-update reacts to what's in the indexes. The vision is to predict — "you usually book the dentist around this time of year; want me to reach out?", "this bill amount is unusual based on the last 12 months; want to question it?", "the dishwasher is making a noise per Home Assistant; based on similar patterns, it's likely the rinse aid pump; here are three contractors who've fixed this".
- **Conversational depth.** The user can ask anything about their own life and get a sourced answer. "Where did I leave that umbrella?" "What was Mom's reaction when we got her the watch in 2024?" "How much have I spent on coffee this year?" "Is my heart-rate trend healthy compared to a year ago?"
- **Outbound automation, opt-in.** With explicit per-task approval, Superagent can send the email / file the doc / book the appointment — using the user's voice, their preferences, their accounts. The trust is built from a thousand correct surface-and-ask interactions before any automate-and-tell move.
- **The Supertailor / Supercoder loop runs hot.** The framework that builds itself becomes self-sustaining; the user maintains it by saying "this should be different" rather than by writing code.

## Explicitly out of scope

What Superagent will NOT do, by design:

- **Replace human professionals.** The medic-prep brief makes you better-prepared for the doctor; it never tells you what to take. The bookkeeper finds the deductibles; it never files the return. The handoff packet helps the executor; it never executes the will.
- **Cloud-by-default.** Local-first is a hard requirement; any cloud feature is opt-in, encrypted, and clearly labelled.
- **Telemetry of any kind.** No "anonymous usage data". Ever. The Supertailor's friction analysis runs on the user's own machine over their own queries.
- **Push notifications without user setup.** Superagent doesn't get to surprise you. Surfacing happens when you run a cadence skill OR when you've opted into a notification mechanism (which you set up; Superagent doesn't ship with one).
- **Multi-tenant / SaaS.** The hosted variant in LOE-XL is single-tenant — your encrypted vault, your keys.

## How items get prioritized

The Supertailor reads `_memory/user-queries.jsonl` to spot which roadmap items would address recurring questions you couldn't get answered. Approved promotions move from this file into `_memory/supertailor-suggestions.yaml` with `status: approved` and the right destination tag.

You can also pin priority manually:

```
edit superagent/docs/roadmap.md
```

Add `**[PINNED]**` next to any row. The Supertailor respects pins for one quarter, then asks again.

The default sequence the maintainers (initially: just you) work through is roughly:

1. The 4 highest-leverage **S** ingestors: gmail (S-01), google_calendar (S-02), apple_health (S-04), plaid (S-05).
2. The auto-capture rules that those ingestors enable: trial-end (S-22), recurring-charge detect (S-23), low-balance (S-26), packages (S-21).
3. **M-01 sensitive-store options** as soon as `health-records.yaml` is non-trivial.
4. The remaining S-tier ingestors as your own data sources expand.
5. **M-02 voice capture** when typing-on-the-laptop becomes the most-mentioned friction in `personal-signals.yaml`.
6. **L-01 multi-user vault** when a partner / household member asks to be onboarded.

Everything else is on the bench. Re-prioritize freely.
