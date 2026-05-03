```
 ____  _   _ ____  _____ ____      _    ____ _____ _   _ _____
/ ___|| | | |  _ \| ____|  _ \    / \  / ___| ____| \ | |_   _|
\___ \| | | | |_) |  _| | |_) |  / _ \| |  _|  _| |  \| | | |
 ___) | |_| |  __/| |___|  _ <  / ___ \ |_| | |___| |\  | | |
|____/ \___/|_|   |_____|_| \_\/_/   \_\____|_____|_| \_| |_|

       your personal-life chief of staff — local, private, AI-native
```

> Bills, health, home, family, finances, vehicles, pets, hobbies, important dates.
> Captured ambiently. Surfaced when it matters. Never sent anywhere.

**Superagent** turns your laptop's AI assistant into the chief of staff a wealthy person would hire — minus the salary, the trust trade-off, and the cloud account. It runs as a folder you open in **Cursor**: a vault for everything that runs your life, plus the discipline to surface what needs your attention before you have to ask.

**Quick-start works in 5 minutes** with zero data-source setup. Heavy ingestion (years of email, banks, health, smart home) is opt-in, deferred, and reversible.

---

## What you'll feel after using it

- **Day 1**: a clean folder gets scaffolded. You log a bill. The agent surfaces it on the right day. The friction of "I need to remember this" drops by one.
- **Week 1**: you've added 5–10 things by saying them in chat. The morning briefing tells you what's due today, what's tomorrow, what's slipping. You stop holding the schedule in your head.
- **Month 1**: ingestion is on for email + calendar. The agent notices the trial that's about to convert, the bill that arrived 30% higher than usual, the dentist appointment your daughter just confirmed by email. You make decisions; the agent does the noticing.
- **Year 1**: the workspace is your second brain. *"When did I last see Dr. Smith?"* — milliseconds, sourced. *"How much have I spent on streaming this year?"* — categorized, surfaced. *"Draft the executor packet"* — a single readable document an executor could use to take over your affairs, with vault references for credentials and physical-location notes for documents.

The bet: the administrative load of modern life is something AI is finally good enough to take off your shoulders, **surgically and ambiently**, so the work that matters has more room.

---

## Get started in 5 minutes

1. **Open this folder in Cursor.** That's the only IDE supported today.
2. **In the chat:**

   ```
   Follow AGENTS.md and run init.
   ```

3. The agent reads `AGENTS.md` (the always-on rulebook), finds `superagent/skills/init.md`, and walks you through:
   - 3 quick orientation questions (your name, household, what hurts most today)
   - Scaffolds `workspace/` with the memory indexes and the 10 default Domain folders
   - Asks whether to probe for data sources to enable now, or "later"
   - Walks through one capture skill that matches your top pain point

After init, the five commands you'll use most:

```
whatsup            30-second status check
daily-update       full morning briefing
weekly-review      Friday/Sunday wrap
add-bill           capture a recurring bill
add-appointment    capture an upcoming appointment
```

Or just describe what you want in plain English — the agent matches your phrasing against the [skill catalog](#what-it-can-do).

---

## What it can do

Capabilities are organized by what you'd actually do with them. Full skill list lives at `superagent/skills/_manifest.yaml` (machine-readable; ~50 skills today).

### Capture (say it; the agent files it)

| Skill | When you'd use it |
|---|---|
| `add-bill` | "I have a new electric bill, $120/mo, due the 15th" |
| `add-subscription` | "Just signed up for Spotify Family, $17, monthly" |
| `add-appointment` | "Dentist cleaning Tue 9am, prep notes: bring insurance card" |
| `add-important-date` | "Mom's 70th birthday is May 14" |
| `add-contact / add-account / add-asset / add-document` | one-shot capture for a person, account, owned thing, or important document |
| `add-domain / add-project` | scaffold a new ongoing area or time-bounded effort |
| `log-event` | "Log this medical visit / car service / home repair / conversation" — finds the right history.md and updates indexes |
| `health-log / vehicle-log / home-maintenance / pet-care` | domain-specific quick-capture |
| `inbox-triage` | walk every file in `Inbox/` and decide file/discard/leave/defer |

### Recall (ask; the agent answers from local data first)

| Question | What happens |
|---|---|
| "When did I last see the dentist?" | answered in milliseconds from `appointments.yaml` + `Domains/Health/history.md` |
| "How much did I spend on coffee this year?" | reconciled from ingested bank data, categorized |
| "Show me everything connected to my mechanic" | `world.py related contact:joe-mechanic` returns every linked entity |
| "What's our pet's vaccine schedule?" | `pet-care` skill reads from `health-records.yaml` (pets section) |

### Surface (the agent reaches out before you have to ask)

| Skill | What it surfaces |
|---|---|
| `whatsup` | 30-second delta since last check: bills due, appointments today, P0/P1 tasks, alerts |
| `daily-update` | full morning briefing: today's calendar + bills due in 7d + P0/P1 tasks + ingest highlights + health signal of note |
| `weekly-review` | Bookkeeper / Coach / Concierge / Quartermaster passes for the trailing week and next two weeks |
| `monthly-review` | subscription audit, document expirations, vehicle/home maintenance windows, financial recap, health overdue list |
| `appointments / bills / subscriptions / important-dates` | per-kind list, prep, mark-paid, cancel, etc. |
| `follow-up` | hunts for dropped balls — overdue tasks, unanswered messages, unfulfilled commitments |
| `triage-overdue` | force a decision on every overdue task |
| `personal-signals` | growth themes from ambient self-development capture |

### Plan ahead

| Skill | Purpose |
|---|---|
| `projects` | list, show, complete, pause, resume, cancel, archive any time-bounded effort |
| `pm-review` | project-manager pass over every Project: RAG, stalls, deadline pressure, top 3 next moves |
| `play` | run a named playbook (sequence of skills) — `start-of-day`, `end-of-week`, `tax-prep-season`, `pre-trip-week`, `health-checkup-quarter` |
| `scenarios` | what-if: cancel-subscriptions, bill-shock, balance-floor, project-overrun, trial-end-impact |
| `handoff` | render the "if hit by a bus" packet — accounts, documents, beneficiaries, vault refs |

### Self-improve (Superagent improves Superagent)

| Skill | Purpose |
|---|---|
| `supertailor-review` | every 90 days: hygiene + strategic-improvement passes; ranked suggestions in `supertailor-suggestions.yaml` |
| `doctor` | workspace data hygiene — duplicates, stale domains, broken refs, expiring documents |

---

## Data sources you can plug in (all opt-in)

Quick-start works with **zero** ingestion. You enter what matters by hand for the first week. Then enable sources you trust, one at a time. Catalog of 27 supported sources, including:

- **Email + calendar**: Gmail, iCloud Mail / Calendar, Outlook, Google Calendar
- **Reminders + notes**: Apple Reminders, Apple Notes, Obsidian, Notion
- **Finance**: Plaid, Monarch, YNAB, generic CSV
- **Health + wearables**: Apple Health, WHOOP, Strava, Garmin, Oura, Fitbit
- **Smart home + vehicles**: Home Assistant, SmartThings, Tesla
- **Communications**: iMessage, Slack
- **Files + media + location**: photos via `exiftool`, Google Maps Timeline

Per-source install / probe / writes destinations / caveats: [`superagent/docs/data-sources.md`](superagent/docs/data-sources.md).

Heavy backfill (5 years of email, 3 years of bank data) is a separate, explicit invocation: `ingest gmail --backfill`.

---

## Privacy by construction

- **`workspace/` is gitignored.** End-to-end. Local-only to your machine unless **you** copy it somewhere.
- **No telemetry.** No metrics, no crash reports, no "anonymous usage data". Ever.
- **No remote write by default.** Ingestors are read-only. Any future skill that writes upstream (auto-pay, auto-book) must declare it loudly and ask per call.
- **The framework's self-improvement loop has a hard safeguard** — a token-scan that prevents your personal data from accidentally leaking into committed framework code. Even if you ask it to.
- **Sensitive subfiles** (`health-records.yaml`, `accounts-index.yaml`, `Outbox/handoff/`) are explicitly called out — symlink them to an encrypted disk image, a vault, or anywhere stricter if you want.
- **Credentials never stored in plaintext.** Each account row carries a `vault_ref` pointing at your password manager; Superagent doesn't open the vault.

The long version: [`superagent/docs/faq.md`](superagent/docs/faq.md) and the framework's [contracts/privacy.md](superagent/contracts/privacy.md).

---

## How it's built

```
<repo-root>/
  AGENTS.md                  ← canonical operating rules (read by Cursor on every Superagent turn)
  README.md                  ← this file
  pyproject.toml             ← Python project config
  .githooks/commit-msg       ← AI-attribution guard
  .cursor/hooks.json         ← UserPromptSubmit hook for the Supertailor's friction analysis
  .mcp.json.example          ← MCP server template (copy to .mcp.json)

superagent/                  ← framework code (committed)
  superagent.agent.md        ← role definitions (Superagent + helper personas)
  supertailor.agent.md       ← Supertailor role (observer + proposer)
  supercoder.agent.md        ← Supercoder role (sole implementer)
  skills/                    ← ~50 skills, one .md per skill, indexed by _manifest.yaml
  contracts/                 ← 39 multi-actor protocols, one .md per contract, indexed by _manifest.yaml
  rules/                     ← machine-readable rule catalogues (anti-patterns)
  templates/
    memory/                  ← YAML templates copied to _memory/ on init
    domains/                 ← 5-file Domain template
    projects/                ← 5-file Project template
    sources/                 ← .ref.md template
    workflows/, playbooks/, githooks/, _custom-starters/
  tools/                     ← workspace_init, validate, render_status, log_user_query, …
    ingest/                  ← _base, _registry, _orchestrator, per-source ingestors
  tests/                     ← pytest; ~100 tests
  docs/                      ← architecture, data-sources, domain-guide, faq, roadmap
    _internal/               ← Supertailor-only planning + history (not user-facing)

workspace/                   ← user data (gitignored, local-only, created by init)
  _memory/                   ← YAML indexes (the structured-state vault)
  _custom/                   ← per-user overlay (additive; rules / skills / templates)
  _checkpoints/<date>/       ← daily memory snapshots (auto, 14-day retention)
  Domains/                   ← ongoing responsibilities (5-file structure each)
  Projects/                  ← time-bounded efforts
  Sources/                   ← reference library (immutable except _cache/)
  Inbox/                     ← staging for incoming files
  Outbox/                    ← shareable artifacts
  Archive/                   ← reversible archive
```

For the deeper mental model — three layers (structured / narrative / reference), the dual-agent self-improvement loop, the PARA / GTD / CODE lineage, multi-user options, how to extract Superagent into its own repo — read [`superagent/docs/architecture.md`](superagent/docs/architecture.md).

---

## Status

| Component | State |
|---|---|
| Memory schema | 32 YAML templates, all v1, all schema-validated |
| Domain templates | 5-file (info / status / history / rolodex / sources) + per-domain `parent`, `visibility`, `provenance` |
| Project templates | 5-file with charter; can be instantiated from a workflow |
| Sources templates | `.ref.md` template + Sources/ folder convention with cache |
| Workflow templates | 5 starter workflows + `_schema.yaml` |
| Playbooks | 5 starter playbooks + `_schema.yaml` |
| Skills | ~50 skills documented + indexed in `_manifest.yaml`; long ones carry an auto-generated step index |
| Contracts | 39 multi-actor contracts in `superagent/contracts/`, indexed by `_manifest.yaml` |
| Rules | machine-readable rule catalogues (anti-patterns shipped) + `workspace/_custom/rules/` user overlay |
| Tools | 19 shipped + tested (workspace_init, validate, render_status, world, sources_cache, briefing_cache, log_window, ...) |
| Ingestor framework | `IngestorBase`, registry of 27 sources, orchestrator CLI, stub fall-back, 2 reference ingestors shipped |
| World graph | `_memory/world.yaml` derived state; `tools/world.py related <handle>` for "show me everything connected to X" |
| Events stream | quarterly-partitioned `_memory/events/<YYYY-Qn>.yaml`; cross-entity timeline queries |
| Tests | ~100 pytest tests; all passing |

---

## Roadmap

T-shirt-sized (XS / S / M / L / XL) with rationale and "done when" criteria. Re-prioritized continuously by the Supertailor based on actual usage friction.

[`superagent/docs/roadmap.md`](superagent/docs/roadmap.md).

Headline near-term work: implement the highest-leverage ingestors (gmail, google_calendar, apple_health, plaid), wire the auto-capture rules they enable (trial-end alerts, low-balance alerts, recurring-charge detection, package tracking).

---

## Self-improving by design

Superagent ships with a **Supertailor / Supercoder loop** that watches how you use it:

- **Supertailor** observes (`interaction-log.yaml`, `user-queries.jsonl`, `personal-signals.yaml`, `action-signals.yaml`) and proposes ranked framework improvements.
- Each suggestion is tagged `destination: superagent` (committed framework) or `destination: _custom` (your overlay). The Supertailor never writes implementation code; both destinations route through the Supercoder.
- A token-scan safeguard runs at proposal time AND at implementation time. **Personal data cannot leak into committed framework code.**
- **Supercoder** implements approved generic suggestions, runs `pytest`, and commits with a single-sentence imperative subject.

Run `supertailor-review` every 90 days (the framework will nudge you).

---

## Contributing

If you're using this and find friction, the most useful thing you can do is **tell the agent**. Action signals get captured into `_memory/action-signals.yaml`, the Supertailor digests them, and approved fixes ship as code.

If you want to write code: [`superagent/supercoder.agent.md`](superagent/supercoder.agent.md) documents the conventions. Add a new ingestor by dropping a file in `superagent/tools/ingest/<source>.py` that subclasses `IngestorBase`, registering it in `_registry.py`, and adding a smoke test. That's the loop.

---

## License

To be determined. The framework code is intended to be open-source-friendly; the workspace data is yours.

---

## A note on naming

The product is **Superagent**. The repository layout is:

- `superagent/` — framework code (committed; the product).
- `workspace/` — user data (gitignored; created by `init`; never committed).
- Root-level config (`AGENTS.md`, `README.md`, `pyproject.toml`, `.githooks/`, `.cursor/`, `.mcp.json.example`) — git-tracked entry points.

If you extract Superagent into its own repo (per [`superagent/docs/architecture.md`](superagent/docs/architecture.md) § "Extracting to a standalone repo"), you can rename the folder to anything you like; the framework doesn't care, as long as you update the references.
