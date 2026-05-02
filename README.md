# Superagent

A personal-life AI assistant that knows your life better than you do.

---

Superagent shadows you across **bills, health, home, family, vehicles, pets, finances, hobbies, important dates** — every workstream of running an adult life. It captures from your own data sources (email, calendar, banks, health, smart home, notes), organizes everything into a queryable local vault, and surfaces what needs your attention before you have to ask.

It is **local-first**, **markdown-based**, **privacy-by-construction**, **AI-assistant-agnostic** (Cursor, Claude Code, anything that reads files), and **self-improving** — a built-in Tailor / Supercoder loop watches how you use it and ships approved framework changes, with safeguards that prevent any of your personal data from leaking into committed code.

The **quick-start works in 5 minutes with zero data-source setup**. Heavy ingestion (years of email, banks, health, smart home) is opt-in, deferred, and reversible.

---

## What it does

Pick whichever line resonates:

- *"What's due this week?"* → 30 seconds, sourced from your real calendar + bills + appointments + tasks.
- *"When did I last see the dentist?"* → answered in milliseconds from local data.
- *"How much have I spent on streaming this year?"* → reconciled from your bank, categorized, surfaced with a one-line "want to cancel anything?" prompt.
- *"My HVAC is making a noise."* → log it; the agent assembles a brief with model + install date + last service + warranty status + the contractor who installed it.
- *"Mom turns 70 next month."* → gift suggestions based on prior years' gifts + recent things she mentioned + a draft card in your voice.
- *"Draft the executor packet."* → a single readable document an executor could use to take over your affairs, with vault references for credentials and physical-location notes for documents.
- *"What should I improve in my morning routine?"* → personal-signals captured ambiently over weeks; surfaced on request as growth themes.

The thing it's actually trying to do: **take the administrative load of modern life off your shoulders, surgically and ambiently, so the work that matters has more room.**

---

## Quick start

```
Follow AGENTS.md and run init.
```

Three questions. A folder gets scaffolded. You're done in five minutes.

Full quick-start: [`superagent/docs/quick-start.md`](superagent/docs/quick-start.md).

---

## How it's built

```
<repo-root>/
  AGENTS.md                  ← canonical IDE-agnostic operating rules (read on demand)
  README.md                  ← this file
  CLAUDE.md                  ← Claude Code shim that imports AGENTS.md
  pyproject.toml             ← Python project config
  .githooks/commit-msg       ← installed AI-attribution guard
  .mcp.json.example          ← MCP server template (copy to .mcp.json + fill in)

superagent/                  ← framework code (committed)
  superagent.agent.md        ← role definitions (Superagent + 9 helpers)
  procedures.md              ← contracts: ingestion, capture, surfacing, cadences
  tailor.agent.md            ← Tailor's role (observer + proposer)
  supercoder.agent.md             ← Supercoder's role (implementer)
  skills/                    ← 48 skills, one .md per skill, plus _manifest.yaml
  templates/
    memory/                  ← 33 YAML templates copied to _memory/ on init
    code-projects/           ← Supercoder Mode 2 scaffold (.supercoder/ + README + .gitignore)
    domains/                 ← 4-file domain template (info, status, history, rolodex, sources)
    projects/                ← 4-file project template
    folder-readmes/          ← READMEs scaffolded into top-level workspace folders
    workflows/               ← 5 starter workflows + _schema.yaml
    githooks/                ← commit-msg hook reference implementation
  playbooks/                 ← 5 starter playbooks + _schema.yaml
  tools/
    workspace_init.py        ← idempotent scaffold
    validate.py              ← schema-check the workspace
    render_status.py         ← regenerate scoped status.md / todo.md
    log_user_query.py        ← UserPromptSubmit hook for the Tailor's friction analysis
    build_skill_manifest.py  ← regenerate skills/_manifest.yaml
    + 14 more (handles, world, audit, briefing_cache, log_window, log_summarize,
       snapshot_diff, scenarios, play, sources_cache, session_scratch,
       inbox_triage, anti_patterns, add_step_index)
    ingest/
      _base.py               ← IngestorBase contract
      _registry.py           ← every supported source's metadata (27 sources)
      _stubs.py              ← StubIngestor for not-yet-implemented sources
      _orchestrator.py       ← `ingest` skill's CLI
      apple_reminders.py     ← shipped reference ingestor
      csv.py                 ← shipped reference ingestor
  tests/                     ← 106 tests covering templates + tools + ingestors + skills
  docs/                      ← architecture, data-sources, skills-reference,
                                domain-guide, faq, quick-start, roadmap

workspace/                   ← user data (gitignored, local-only, created by init)
  _memory/                   ← 33 YAML indexes (the structured-state vault)
  _custom/                   ← per-user overlay (additive; rules / skills / templates)
  _checkpoints/<date>/       ← daily memory snapshots (auto, 14-day retention)
  Domains/                   ← ongoing responsibilities (4-file structure each)
    Health/  Finance/  Home/  Vehicles/  Pets/  Family/  Travel/
    Career/  Hobbies/  Self/  + any custom domains you add
  Domains/<X>/sources.md     ← curated catalogue of Sources/ entries for the domain
  Domains/<X>/Resources/     ← drafts, working files, agent-generated artifacts (lazy)
  Projects/                  ← personal-life projects (time-bounded efforts)
    <project-slug>/          ←   info.md (charter) / status.md / history.md / rolodex.md / sources.md
                             ←   + Resources/ + Sources/ (lazy)
  Code/                      ← standalone coding projects built by Supercoder Mode 2
    <slug>/                  ←   .supercoder/ (info / status / history / decisions)
                             ←   + your source code, optional .git/, README.md
  Sources/                   ← reference library (IMMUTABLE)
    documents/<category>/    ←   actual local files; never auto-deleted
    references/<category>/   ←   .ref.md pointers to external data
    _cache/<source-hash>/    ←   fetched copies (TTL + LRU; only auto-managed sub-tree)
  Inbox/                     ← staging for incoming files
  Outbox/                    ← shareable artifacts (drafts, summaries, handoff packet)
  Archive/                   ← reversible archive (per `doctor` skill); `code/` for archived code projects
  todo.md                    ← cross-cutting task view
```

Full architecture: [`superagent/docs/architecture.md`](superagent/docs/architecture.md).

---

## Skills (highlights)

| Skill | What it does |
|---|---|
| `init` | First-run setup; scaffolds the workspace; optionally probes for data sources. |
| `whatsup` | 30-second status check: bills due, today's appointments, important dates, P0/P1 tasks. |
| `daily-update` | Full morning briefing — runs scheduled ingestors first, then surfaces today + this week + alerts. |
| `weekly-review` | Friday / Sunday wrap — Bookkeeper + Coach + Concierge + Quartermaster passes. |
| `monthly-review` | Subscription audit, document expirations, maintenance windows, financial recap. |
| `add-bill / add-subscription / add-appointment / add-asset / add-contact / add-account / add-document / add-domain / add-project / add-important-date / add-source` | Capture skills — bootstrap a single new entity in the right index. |
| `projects` | List, show, complete, pause, resume, cancel, archive Projects. Per-project burn-down. |
| `sources` | List, search, fetch (through cache), refresh entries in `Sources/`. |
| `inbox-triage` | Walk every file in `Inbox/`; classify; ask file/discard/leave/defer. Records decisions in `Inbox/_processed.yaml`. |
| `tags` | List / search / rename / merge tags across every YAML index. |
| `decisions` | Capture, list, review meaningful decisions with outcome tracking. |
| `play` | Run a named playbook (sequence of skills with conditions). |
| `scenarios` | Run a what-if scenario (cancel-subscriptions, bill-shock, balance-floor, project-overrun, trial-end-impact). |
| `world` | Query / rebuild the world graph; "show me everything connected to X". |
| `events` | Read or append events in the unified time stream (quarterly partitioned). |
| `audit` | Read the per-row audit history of any entity. |
| `log-event` | One-shot capture: "log this medical visit / car service / home repair / conversation". |
| `bills / subscriptions / appointments / important-dates / expenses` | Surface + manage skills for each kind of recurring thing. |
| `health-log / vehicle-log / home-maintenance / pet-care` | Domain-specific capture skills. |
| `draft-email / summarize-thread / follow-up / research` | Communication + research helpers. |
| `ingest` | Front-end to the data-source orchestrator. `setup` / `run --all` / `run --source X --backfill`. |
| `personal-signals` | Ambient capture of self-development feedback; on-request surface of growth themes. |
| `triage-overdue` | Force a decision on every overdue task. |
| `handoff` | Generate the "if hit by a bus" packet — accounts, documents, beneficiaries, vault refs. |
| `doctor` | Workspace data hygiene — duplicates, stale domains, broken refs, expiring documents. |
| `tailor-review` | Framework hygiene + strategic-improvement passes. The "framework that builds itself" loop. |
| `supercoder` | Build standalone coding projects under `workspace/Code/<slug>/`. Subcommands: `new` / `list` / `open` / `status` / `work` / `close` / `archive`. Self-contained per `procedures.md` § 40. |

Full catalogue: [`superagent/docs/skills-reference.md`](superagent/docs/skills-reference.md).

---

## Data sources

Superagent ingests from up to 27 cataloged sources. **None required for quick-start.** Enable each one when you trust it.

- **Email + calendar**: Gmail, iCloud Mail / Calendar, Outlook, Google Calendar.
- **Reminders + notes**: Apple Reminders (`rem`), Apple Notes, Obsidian, Notion.
- **Finance**: Plaid, Monarch, YNAB, generic CSV.
- **Health + wearables**: Apple Health (`healthsync`), WHOOP, Strava, Garmin, Oura, Fitbit.
- **Smart home + vehicles**: Home Assistant, SmartThings, Tesla.
- **Communications**: iMessage (`imessage-exporter`), Slack.
- **Files + media + location**: photos via `exiftool`, Google Maps Timeline.

Per-source install / probe / writes destinations / caveats: [`superagent/docs/data-sources.md`](superagent/docs/data-sources.md).

---

## Privacy

- Everything under `workspace/` is **gitignored** and **local-only**. No telemetry. No phone-home. No cloud sync.
- The agent's framework code under `superagent/` is committable; the workspace is not.
- Sensitive subfiles (`health-records.yaml`, `accounts-index.yaml`, `Outbox/handoff/`) are explicitly called out — see `architecture.md` § "Sensitive subfiles" for encryption guidance.
- The Tailor / Supercoder loop has a **hard safeguard** that prevents personal data from leaking into committed framework code, even when the user explicitly tries.
- Credentials are NEVER stored in the workspace. Each account row carries a `vault_ref` pointing at your password manager.

Read [`superagent/docs/faq.md`](superagent/docs/faq.md) for the long version.

---

## Roadmap

Organized by LOE tier (T-shirt sizes XS / S / M / L / XL) with rationale and "done when" criteria for each item. The Tailor's strategic pass continuously re-prioritizes based on actual usage friction.

[`superagent/docs/roadmap.md`](superagent/docs/roadmap.md).

---

## Self-improving + project-building

Superagent ships with a **Tailor / Supercoder dual-agent loop**. The Supercoder operates in two distinct modes:

**Mode 1 — Framework improvement** (the original loop):

- The **Tailor** observes (`interaction-log.yaml`, `user-queries.jsonl`, `personal-signals.yaml`, `action-signals.yaml`) and proposes ranked framework improvements.
- Each suggestion is tagged `destination: superagent` (generic — handed to the Supercoder for committed implementation) or `destination: _custom` (user-specific — Tailor implements directly into your overlay).
- A token-scan safeguard runs at proposal time AND at implementation time. Personal data CANNOT leak into committed framework code.
- The **Supercoder** implements approved generic suggestions, runs `pytest`, and commits with a single-sentence imperative subject (no AI-attribution trailers, ever).

**Mode 2 — Project build** (build coding projects for you):

- Invoked via the `supercoder` skill. Builds standalone coding projects under `workspace/Code/<slug>/` — each one self-contained, with its own `.supercoder/` charter + status, optional git repo, and (per the user's choice) language scaffold.
- A **path-scope safeguard** runs on every write: the Supercoder cannot touch the framework, personal-life data, or any other code project. Refusal is hard, not optional.
- See `superagent/supercoder.agent.md` for the full role definition and `superagent/procedures.md` § 40 for the Code Projects Contract.

Run `tailor-review` every 90 days for Mode 1 hygiene (the framework will nudge you). Mode 2 runs whenever you ask it to build something.

---

## Status

| Component | State |
|---|---|
| Memory schema | 33 YAML templates, all v1, all parsing, all schema-validated |
| Domain templates | 4-file structure (info / status / history / rolodex) + per-domain `parent`, `visibility`, `provenance` |
| Project templates | 4-file structure with charter; can be instantiated from a workflow |
| Sources templates | `.ref.md` template + Sources/ folder convention with cache |
| Workflow templates | 5 starter workflows (tax-filing, trip-planning, annual-health-tuneup, job-search, appliance-replacement) + `_schema.yaml` |
| Playbooks | 5 starter playbooks (start-of-day, end-of-week, tax-prep-season, pre-trip-week, health-checkup-quarter) + `_schema.yaml` |
| Skill manifest | `skills/_manifest.yaml` auto-generated; 48 rows |
| Step indices | Long skills (≥ 100 lines) carry an auto-generated `## Step index` block for `Read --offset --limit` targeting |
| Skills | 48 skills documented; markdown instruction sets ready to invoke |
| Tools | `workspace_init`, `validate`, `render_status`, `log_user_query`, `sources_cache`, `handles`, `build_skill_manifest`, `add_step_index`, `log_summarize`, `snapshot_diff`, `world`, `log_window`, `briefing_cache`, `session_scratch`, `audit`, `play`, `scenarios`, `inbox_triage`, `anti_patterns` — 19 shipped + tested |
| Ingestor framework | `IngestorBase`, registry of 27 sources, orchestrator CLI, stub fall-back, 2 reference ingestors shipped (`apple_reminders`, `csv`) |
| Sources cache | Local-first read pattern; TTL + LRU eviction; chunk + summary + TOC generation |
| World graph | `_memory/world.yaml` derived state; `tools/world.py rebuild` reconstructs from entity files; `related <handle>` for "show me everything connected to X" |
| Events stream | Quarterly-partitioned `_memory/events/<YYYY-Qn>.yaml`; cross-entity timeline queries via `tools/log_window.py read` |
| Tests | 106 pytest tests; all passing |
| Docs | architecture, data-sources, skills-reference, domain-guide, faq, quick-start, roadmap, ideas-better-structure (+ done log), perf-improvement-ideas (+ done log) |
| AGENTS.md | canonical operating rules; not auto-injected (the agent reads on-demand) |

---

## Contributing

If you're using this and find friction, the best way to help is the same way the framework is designed to learn: tell the agent. Action signals get captured into `_memory/action-signals.yaml`, the Tailor digests them, and approved fixes ship as code.

If you want to write code: `superagent/supercoder.agent.md` documents the conventions. Add a new ingestor by dropping a file in `superagent/tools/ingest/<source>.py` that subclasses `IngestorBase`. Add a row to `_registry.py`. Add a smoke test. That's the loop.

---

## License

To be determined. The framework code is intended to be open-source-friendly; the workspace data is yours.

---

## A note on naming

The product is **Superagent**. The repository layout is:

- `superagent/` — framework code (committed; the product).
- `workspace/` — user data (gitignored; created by `init`; never committed).
- Root-level config (`AGENTS.md`, `README.md`, `CLAUDE.md`, `pyproject.toml`, `.githooks/`, `.mcp.json.example`) — git-tracked entry points.

Throughout the docs and skills, "Superagent" is the proper-noun reference to the product / agent / framework. `superagent/` is the path reference to where the code lives. If you extract Superagent into its own repo (per `superagent/docs/architecture.md` § "Extracting to a standalone repo"), you can rename the folder to anything you like; the framework doesn't care, as long as you update the references.
