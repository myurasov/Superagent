```
███████╗██╗   ██╗██████╗ ███████╗██████╗  █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██╔════╝██║   ██║██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████╗██║   ██║██████╔╝█████╗  ██████╔╝███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
╚════██║██║   ██║██╔═══╝ ██╔══╝  ██╔══██╗██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
███████║╚██████╔╝██║     ███████╗██║  ██║██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
╚══════╝ ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝

      your personal-life chief of staff  ·  local  ·  private  ·  AI-native
```

> Bills, health, home, family, finances, vehicles, pets, hobbies, important dates.
> Captured ambiently. Surfaced when it matters. Never sent anywhere.

---

## Contents

- [What it is](#what-it-is)
- [What you'll feel](#what-youll-feel)
- [Get started in 5 minutes](#get-started-in-5-minutes)
- [What it can do](#what-it-can-do)
- [Data sources](#data-sources)
- [Privacy](#privacy)
- [Deeper docs](#deeper-docs)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## What it is

**Superagent** turns Cursor's AI assistant into the chief of staff a wealthy person would hire — minus the salary, the trust trade-off, and the cloud account. It runs as a folder you open in Cursor: a vault for everything that runs your life, plus the discipline to surface what needs your attention before you have to ask.

**Quick-start works in 5 minutes** with zero data-source setup. Heavy ingestion (years of email, banks, health, smart home) is opt-in, deferred, and reversible.

## What you'll feel

- **Day 1** — a clean folder gets scaffolded. You log a bill. The agent surfaces it on the right day.
- **Week 1** — you've added 5–10 things by saying them in chat. The morning briefing tells you what's due today, what's tomorrow, what's slipping.
- **Month 1** — ingestion is on for email + calendar. The agent catches the trial about to convert, the bill 30% higher than usual, the dentist appointment your daughter just confirmed by email.
- **Year 1** — *"When did I last see Dr. Smith?"* answered in milliseconds. *"How much have I spent on streaming this year?"* categorized and surfaced. *"Draft the executor packet"* — one document an executor could use to take over your affairs.

The bet: AI is finally good enough to take the administrative load of modern life off your shoulders, **surgically and ambiently**, so the work that matters has more room.

## Get started in 5 minutes

1. **Open this folder in Cursor.**
2. In chat:

   ```
   Follow AGENTS.md and run init.
   ```

3. The agent reads `AGENTS.md`, finds `superagent/skills/init.md`, asks 3 orientation questions (your name, household, what hurts most today), scaffolds `workspace/`, and walks through one capture skill that matches your top pain point.

After init, the five commands you'll use most:

```
whatsup            30-second status check
daily-update       full morning briefing
weekly-review      Friday/Sunday wrap
add-bill           capture a recurring bill
add-appointment    capture an upcoming appointment
```

Or just describe what you want in plain English — the agent matches your phrasing against the [skill catalog](superagent/skills/_manifest.yaml).

## What it can do

Capabilities grouped by intent. Full skill list (~50) lives at `superagent/skills/_manifest.yaml`.

| Intent | Skills | Example use |
|---|---|---|
| **Capture** (say it; the agent files it) | `add-bill`, `add-subscription`, `add-appointment`, `add-important-date`, `add-contact`, `add-account`, `add-asset`, `add-document`, `add-domain`, `add-project`, `add-source`, `log-event`, `health-log`, `vehicle-log`, `home-maintenance`, `pet-care`, `inbox-triage` | "Just signed up for Spotify Family, $17/mo." |
| **Recall** (ask; the agent answers from local data first) | `world` graph, per-domain `history.md`, `audit`, `events` | "Show me everything connected to my mechanic." |
| **Surface** (the agent reaches out before you have to ask) | `whatsup`, `daily-update`, `weekly-review`, `monthly-review`, `appointments`, `bills`, `subscriptions`, `important-dates`, `follow-up`, `triage-overdue`, `personal-signals` | The morning briefing tells you the trial converts in 3 days. |
| **Plan** (time-bounded efforts + scenarios) | `projects`, `pm-review`, `play` (playbooks), `scenarios`, `handoff` | "Plan the kitchen renovation. Budget $25k. Done by August." |
| **Self-improve** (the framework improves itself) | `supertailor-review`, `doctor` | Every 90 days: ranked framework-improvement suggestions, with a hard safeguard against personal-data leakage into committed code. |

## Data sources

Quick-start works with **zero** ingestion. Catalog of 27 supported sources, opt-in:

- Email + calendar — Gmail, iCloud, Outlook, Google Calendar
- Reminders + notes — Apple Reminders / Notes, Obsidian, Notion
- Finance — Plaid, Monarch, YNAB, generic CSV
- Health + wearables — Apple Health, WHOOP, Strava, Garmin, Oura, Fitbit
- Smart home + vehicles — Home Assistant, SmartThings, Tesla
- Communications — iMessage, Slack
- Files + media + location — photos via `exiftool`, Google Maps Timeline

Per-source install / probe / writes destinations / caveats: [`superagent/docs/data-sources.md`](superagent/docs/data-sources.md).

## Privacy

- **`workspace/` is gitignored.** Local-only to your machine unless **you** copy it somewhere.
- **No telemetry.** No metrics, no crash reports, no "anonymous usage data". Ever.
- **No remote write by default.** Ingestors are read-only.
- **Hard safeguard** in the framework's self-improvement loop — token-scan that prevents your personal data from leaking into committed framework code, even if you ask it to.
- **Credentials never stored in plaintext.** Each account row carries a `vault_ref` pointing at your password manager.

The long version: [`superagent/docs/faq.md`](superagent/docs/faq.md) and [`superagent/contracts/privacy.md`](superagent/contracts/privacy.md).

## Deeper docs

| Doc | What's in it |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Canonical operating rules — what the agent reads on every Superagent turn |
| [`superagent/docs/architecture.md`](superagent/docs/architecture.md) | Mental model, repo layout, dual-agent loop, current build status |
| [`superagent/docs/data-sources.md`](superagent/docs/data-sources.md) | Per-source install + probe + caveats (27 sources cataloged) |
| [`superagent/docs/domain-guide.md`](superagent/docs/domain-guide.md) | Per-domain practical guide |
| [`superagent/docs/faq.md`](superagent/docs/faq.md) | Naming, comparisons, security, multi-user, mobile, what-ifs |
| [`superagent/docs/roadmap.md`](superagent/docs/roadmap.md) | T-shirt-sized backlog, re-prioritized continuously by the Supertailor |

## Roadmap

T-shirt-sized (XS/S/M/L/XL) with rationale and "done when" criteria. Headline near-term work: implement the highest-leverage ingestors (gmail, google_calendar, apple_health, plaid) and wire the auto-capture rules they enable. Full plan: [`superagent/docs/roadmap.md`](superagent/docs/roadmap.md).

## Contributing

If you're using this and find friction, the most useful thing you can do is **tell the agent**. Action signals get captured into `_memory/action-signals.yaml`, the Supertailor digests them, and approved fixes ship as code.

If you want to write code: [`superagent/supercoder.agent.md`](superagent/supercoder.agent.md) documents the conventions. Add a new ingestor by dropping a file in `superagent/tools/ingest/<source>.py` that subclasses `IngestorBase`, registering it in `_registry.py`, and adding a smoke test.

## License

[Apache License 2.0](LICENSE) — copyright 2026 Mikhail Yurasov. The framework code is open-source under Apache 2.0; **your `workspace/` data is yours alone** (gitignored, never published, never licensed by this project).
