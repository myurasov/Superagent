# `superagent` workspace

---

## Table of Contents

- [`superagent` workspace](#superagent-workspace)
  - [How this file is loaded](#how-this-file-is-loaded)
  - [Canonical references](#canonical-references)
  - [Custom overlay — read on every superagent turn](#custom-overlay--read-on-every-superagent-turn)
  - [Framework Artifact Creation Contract](#framework-artifact-creation-contract)
  - [On workspace open](#on-workspace-open)
  - [Before any file or MCP operation](#before-any-file-or-mcp-operation)
  - [Model context (cross-session memory)](#model-context-cross-session-memory)
  - [Skills](#skills)
  - [Data ingestion contract](#data-ingestion-contract)
  - [Logging](#logging)
  - [Domain / Project / Sources folder convention](#domain--project--sources-folder-convention)
  - [Public artifact destination](#public-artifact-destination)
  - [Git commits](#git-commits)
  - [Local task references](#local-task-references)
  - [Read budget (token efficiency)](#read-budget-token-efficiency)
  - [Local-first read order](#local-first-read-order)
  - [Operational handles](#operational-handles)
  - [Visibility and sensitive tier](#visibility-and-sensitive-tier)
  - [Provenance](#provenance)
  - [Time-shape vs entity-shape vs event-shape](#time-shape-vs-entity-shape-vs-event-shape)
  - [Privacy and data location](#privacy-and-data-location)
  - [Local development tooling](#local-development-tooling)
  - [IDE setup (Cursor)](#ide-setup-cursor)
  - [Prompt-cache discipline](#prompt-cache-discipline)

---

This file is the **canonical always-on instructions** for **Superagent** — a personal-life AI assistant that runs inside Cursor. The framework code lives in the `superagent/` folder; user data lives in the sibling `workspace/` folder. Whenever the user invokes Superagent (or works within `workspace/`), the agent reads this file and follows it in full.

Superagent is designed as a **standalone framework**. It depends on nothing outside the `superagent/` directory and its sibling workspace. It can be extracted into its own repo at any time (see `docs/architecture.md` § "Extracting to a standalone repo").

---

## How this file is loaded

The agent reads this file on the first turn of any session in which the user:

- Invokes a Superagent skill by name (`init`, `daily-update`, `whatsup`, `bills`, `add-domain`, `ingest`, etc.) or asks for one in plain English ("draft my weekly review", "what bills are due", "log a vet visit").
- Says "use superagent" / "this is for superagent" / "switch to superagent mode" or any obvious natural-language equivalent.
- Opens or edits a file under `workspace/` or under `superagent/`.
- Asks a question that is obviously about personal life — bills, health, family, home maintenance, personal vehicles, pets, personal travel, hobbies, important dates.

The agent should announce the switch lightly the first time it happens in a session ("Reading `AGENTS.md` for personal-life context.") and then proceed normally.

If this repository also hosts other AI-assistant frameworks (work assistants, project-specific agents, etc.), the agent is responsible for routing each turn to the right framework based on the request's evident scope. When uncertain, **ask** ("This sounds personal — should I switch to Superagent?") rather than guess.

---

## Canonical references

- **Role definitions** (Superagent + helper personas): read and follow [`superagent/superagent.agent.md`](superagent/superagent.agent.md).
- **Operational contracts** (init, cadences, data-ingestion, capture / surfacing patterns, autonomy, memory taxonomy, ...): browse [`superagent/contracts/`](superagent/contracts/) — one markdown file per contract, indexed by `superagent/contracts/_manifest.yaml`. Skills cite a specific contract as `contracts/<slug>.md`.
- **Supertailor (framework hygiene + improvement)**: read and follow [`superagent/supertailor.agent.md`](superagent/supertailor.agent.md).
- **Supercoder (implementation)**: read and follow [`superagent/supercoder.agent.md`](superagent/supercoder.agent.md).
- **Custom overlay** (per-user extensions): see § "Custom overlay" below; full reference in [`superagent/docs/custom-overlay.md`](superagent/docs/custom-overlay.md).

---

## Custom overlay — read on every superagent turn

Superagent supports a per-user overlay at **`workspace/_custom/`**. The overlay is **additive** to the framework at `superagent/`; it never silently replaces framework behavior.

On **every Superagent turn**, before doing anything else:

1. If `workspace/_custom/rules/` exists and contains files, list them and **read every file**. Treat their contents as **additional rules** that apply to the current interaction on top of this `AGENTS.md`. Ordering: alphabetical by filename.
2. When the user invokes or infers a skill, search **both** `superagent/skills/` **and** `workspace/_custom/skills/`. On name collision, run the framework skill first, then apply the custom file as an addendum (extra steps) — and announce the overlay at the top of the response.
3. When a role definition is needed, check `workspace/_custom/agents/` for a same-named overlay and merge its content as additional boundaries / preferences on top of the framework role (never weakening framework safety).
4. When resolving a template, check `workspace/_custom/templates/` first. If a same-named template exists, use the custom version **and announce** it loudly: *"Using `_custom/templates/<name>` (overrides framework template)."*

If `workspace/_custom/` does not exist, skip the overlay silently — no error, no warning. It is optional scaffolding.

The Supertailor (`supertailor-review` skill) auto-scaffolds the `_custom/` directory structure during its hygiene pass if it is missing.

---

## Framework Artifact Creation Contract

Whenever the agent (or any skill) is about to create a new **skill**, **agent role**, **rule**, **template**, **tool**, or **doc page**, follow the contract in `contracts/framework-artifacts.md`:

1. **Auto-classify destination** — `superagent` (generic, committed) or `_custom` (user-specific, gitignored). **Default to `_custom` when ambiguous.**
2. **If unambiguous**, announce the destination at the top of the response and proceed.
3. **If ambiguous**, **ask the user** with the `AskQuestion` tool: choose `superagent`, `_custom`, or `cancel`.
4. **Safeguard**: scan the artifact body for any name from `_memory/contacts.yaml`, `domains-index.yaml`, `assets-index.yaml`, `accounts-index.yaml`, address fragments, account numbers, license-plate patterns, or known personal identifiers. On any match, refuse the `superagent/` path and re-route to `workspace/_custom/`.
5. **NEVER** silently write user-specific content under `superagent/` — even if explicitly requested. The Supercoder enforces this with a refusal on receipt.

This contract does **not** govern workspace **data** writes (domain files, contact entries, bill records, appointment rows) — those go to `workspace/` by their own conventions (see `contracts/`).

---

## On workspace open

When the agent first opens (or first acts in) `workspace/` in a session:

1. Check whether **`workspace/_memory/config.yaml`** exists.
2. **If it does not exist**, suggest running the **init** skill (`superagent/skills/init.md`) before relying on Superagent memory or paths.
3. **If it exists**, read **`workspace/_memory/context.yaml`** and inspect **`last_check`**. If `last_check` is **more than 24 hours ago** (or null / stale), suggest running the **whatsup** or **daily-update** skill to refresh.

---

## Before any file or MCP operation

- **Always read `workspace/_memory/config.yaml` first** to resolve `preferences.workspace_path`, the user profile, MCP and CLI tool flags, automation preferences, and ingestion budgets. Do not assume a hardcoded `workspace/` path except as the documented default when config is missing.
- **Always read `workspace/_memory/data-sources.yaml`** before invoking any ingestor or any skill that reads MCPs / CLI tools. That file is the single source of truth for which sources are configured, when each was last ingested, and the recency / size budget per source.

---

## Model context (cross-session memory)

- **Read `workspace/_memory/model-context.yaml` at session start** to restore the model's accumulated understanding of the user (communication style, working patterns, terminology, prior corrections, household composition, recurring routines).
- **Update `model-context.yaml` before session end** (or when significant learnings occur) with:
  - New domain knowledge discovered.
  - User corrections or preferences expressed.
  - A brief session summary appended to `sessions` (keep last 10; drop oldest).

This file is the model's own memory — distinct from `context.yaml` (operational state) and `config.yaml` (profile / preferences).

---

## Skills

For **any** user question or task, consider whether a **Superagent skill** in `superagent/skills/` **or** `workspace/_custom/skills/` applies; if so, follow that skill's steps and cite it. Custom skills are first-class — invoke them like any framework skill. On same-name collision between framework and custom, run the framework skill first and then apply the custom file as an appendix of additional steps.

The full skill catalog (machine-readable, with one-liners + triggers) lives in [`superagent/skills/_manifest.yaml`](superagent/skills/_manifest.yaml). Highlights:

| Skill | One-line description |
|---|---|
| **init** | First-run: short questionnaire, scaffold `_memory/` and `Domains/`, optionally probe and enable available data sources. |
| **whatsup** | Quick delta since `last_check`: bills due, appointments, mail, tasks, alerts. |
| **daily-update** | Daily briefing: bills due / overdue, today's appointments, P0/P1 tasks, anything from ingest sources that needs your attention. |
| **weekly-review** | Sunday-style review: spend by category, fitness summary, what got done, what slipped, what's coming. |
| **monthly-review** | First-of-month: subscription audit, document expirations, vehicle / home maintenance windows, financial recap. |
| **todo** | Add / list / complete / update tasks in `todo.yaml` with P0–P3 priority rules. |
| **bills** | Add / list / mark-paid bills; reconcile against ingested bank transactions. |
| **subscriptions** | Audit recurring charges; flag unused / lapsed-promo / candidate-cancel. |
| **appointments** | Add / list / prep for appointments (doctor, dentist, vet, mechanic, hairdresser, school, …). |
| **important-dates** | Add / list birthdays, anniversaries, document expirations, recurring deadlines. |
| **add-domain / add-project / add-asset / add-contact / add-account / add-bill / add-subscription / add-appointment / add-important-date / add-document / add-source** | Capture skills — bootstrap a single new entity with the right template + index row. |
| **projects** | List, show, complete, pause, resume, cancel, archive Projects. Per-project burn-down. |
| **sources** | List, search, fetch (through cache), refresh the Sources/ library. |
| **log-event** | One-shot capture: "log this medical visit", "log this car service", "log this home repair" — appends to the right `history.md` and updates indexes. |
| **health-log** | Log a symptom / med change / vital reading; rolls into `health-records.yaml`. |
| **vehicle-log** | Log a service / fuel-up / mileage reading; rolls into the vehicle's `history.md`. |
| **home-maintenance** | Track home-care schedule (HVAC, filters, gutters, pest, etc.). |
| **pet-care** | Vet schedule, vaccinations, meds, food / treat preferences. |
| **expenses** | Categorize and review spending; cross-checks ingested transactions. |
| **draft-email** | Compose personal email with full context (recipient history, related domain, prior thread). |
| **summarize-thread** | Condense a long email or message thread into key points and follow-ups. |
| **follow-up** | Hunt for dropped balls: overdue tasks, unanswered messages, unfulfilled commitments. |
| **research** | Research a topic across notes, web, knowledge MCPs (Obsidian, Notion). |
| **ingest** | Run one or more configured ingestors (Gmail, Plaid, Apple Health, etc.). Front-end for `tools/ingest/`. |
| **personal-signals** | Capture self-development feedback; surface growth themes on request. |
| **doctor** | Workspace data hygiene — stale domains, duplicate contacts, near-duplicate todos, simplification candidates. |
| **supertailor-review** | Framework hygiene + strategic improvement; produces ranked suggestions in `supertailor-suggestions.yaml`. |
| **handoff** | Generate the "if I get hit by a bus" packet — account list, document locations, executor instructions. |

---

## Data ingestion contract

Superagent's value scales with the breadth of authorized data sources. The contract that governs all ingestion is in `contracts/ingestion.md`; the one-paragraph summary:

- **Every ingestor** lives in `superagent/tools/ingest/<source>.py`. One file per source.
- **Every ingestor** reads its config from `workspace/_memory/data-sources.yaml` (which holds `enabled`, `last_ingest`, `recency_window_days`, `max_items_per_run`, source-specific auth pointer).
- **Every ingestor** writes its state back to the same row of `data-sources.yaml` and appends a run summary to `workspace/_memory/ingestion-log.yaml`.
- **Every ingestor** is **read-only** from the user's perspective unless explicitly documented otherwise. It pulls; it does not push, delete, or modify upstream state.
- **Every ingestor** is **idempotent** within its recency window — re-running over the same window must not duplicate rows in any index or any domain `history.md`.
- **Quick-start works without any ingestor enabled.** Init never silently turns on a source; it lists what's available and asks.
- **Heavy ingestion is opt-in and deferred.** Backfilling 5 years of email or 3 years of bank data is a separate, explicit invocation.

---

## Logging

- **Log significant agent actions** (skill runs, structural edits to memory, autonomous suggestions that change files, ingestion runs) by appending to **`workspace/_memory/interaction-log.yaml`** per its schema (append-only; do not rewrite history).
- **Ingestion runs** *also* append a row to **`workspace/_memory/ingestion-log.yaml`** with per-source counts, durations, and any errors. The interaction-log entry can simply reference the ingestion-log row.
- **Payment confirmations** (any time money changes hands on the user's behalf, the user reports a completed payment, or the user shares a receipt) MUST be captured as files per `contracts/payment-confirmations.md` — long-lived / auditable payments go to `Sources/<Domain>/`; project-scoped purchases go to `Projects/<project>/Resources/`. Cross-linked from `bills.yaml` / `subscriptions.yaml` / `appointments.yaml` / project history. Capture by *what* the payment is, not by dollar amount.

---

## Domain / Project / Sources folder convention

Three first-class folder kinds in `workspace/`:

### Domains/

**Domain** = ongoing area of responsibility (Health, Finances, Home, …). Never "completes". 4-file structure:

```
Domains/<domain>/
  info.md       # narrative overview, current state, key facts
  status.md     # RAG status + open / done tasks for this domain
  history.md    # chronological log of touchpoints / events / decisions
  rolodex.md    # contact directory scoped to this domain
  sources.md    # curated catalogue of Sources/ entries relevant to this domain
  Resources/    # optional, lazily created — drafts, working files, agent-generated artifacts
```

The 12 default domains **registered** by `init` are: **Health**, **Finances**, **Home**, **Vehicles**, **Assets**, **Pets**, **Family**, **Travel**, **Career**, **Business**, **Hobbies**, **Self**. Users add their own via `add-domain`. Per `contracts/domains-and-assets.md` § 6.4a, the per-domain folder under `Domains/<Name>/` is **lazy** — it materializes the first time real data lands for that domain (call `uv run python -m superagent.tools.domains ensure <id>` before writing).

### Projects/

**Project** = time-bounded effort with a clear goal and target date. Tax filing, kitchen renovation, trip planning, job search, annual health tune-up. Same 4-file structure as Domains:

```
Projects/<project-slug>/
  info.md       # charter (goal, scope, success criteria, deliverables, team, dates, budget)
  status.md     # RAG + open / done tasks (auto-synced from todo.yaml)
  history.md    # chronological log of decisions / milestones / status flips
  rolodex.md    # project-scoped contact directory
  sources.md    # curated catalogue of Sources/ entries relevant to this project
  Resources/    # optional, lazily created — drafts, working files, agent-generated artifacts
  Sources/      # optional project-scoped reference library (per § Sources below)
```

A Project links UP to one or more Domains via `related_domains: [..]`. Tasks in `_memory/todo.yaml` link DOWN to a Project via `related_project: <slug>`. The `add-project` skill collects a 5-question charter (name, goal, scope, target date, related domains) BEFORE creating the folder. Lifecycle: `planning → active → paused → completed → archived` (per `contracts/projects.md`).

### Sources/

**Sources** = the workspace's reference library: documents the user owns + pointers (`.ref.md` / `.ref.txt`) to external data. Three foundational rules (per `contracts/sources.md`):

1. **Layout is user-defined.** The agent reserves only `Sources/README.md` and `Sources/_cache/`; everything else is the user's territory. Drop files anywhere; organize folders any way.
2. **Index is derived.** `_memory/sources-index.yaml` is rebuilt from the filesystem on demand by `tools/sources_index.py refresh` (mtime-lazy — near-no-op when nothing changed). Hand-curated fields (notes, tags, sensitive, related_*, last_accessed, read_count) are preserved across refreshes.
3. **Local-first.** Every skill that needs source data reads the cache first; only goes to live MCP / API when the cache is stale or missing. Reads `_summary.md` + `_toc.yaml` first; only pulls relevant chunks of `raw.<ext>` when needed.

```
Sources/
  README.md                                 # user-facing docs (template)
  _cache/<source-hash>/                     # agent-managed (TTL + LRU)
    _meta.yaml _summary.md _toc.yaml raw.<ext> chunks/
  <whatever-folders-you-want>/<files>       # user-curated; any layout
    <doc>.<ext>                             # documents
    <doc>.<ext>.ref.md                      # optional sidecar metadata
    <name>.ref.md  /  <name>.ref.txt        # standalone references
```

Reference files (`.ref.md` / `.ref.txt`) describe `kind` (mcp / cli / url / api / file / vault / manual) + `source` (the identifier) + `ttl_minutes`. The canonical form is YAML frontmatter (`superagent/templates/sources/ref.md`); hand-authored loose `Key: value` or bare-URL forms are accepted and **normalized on first use** with the user's permission (`tools/sources_normalize.py`; default policy `ask`). The agent resolves a ref by computing `sha256(kind + source)`, checking the cache (default `Sources/_cache/<hash>/`, override via `config.preferences.sources.cache_path`), fetching only if necessary.

Filenames inside Domain / Project folders are lowercase and hyphenated; sub-folders for events, trips, sub-efforts follow the same rule.

---

## Public artifact destination

- **Default outbox: `workspace/Outbox/`**. Whenever a Superagent skill or tool generates a **publicly-shareable artifact** (a draft email, a printable shopping list, a packed-and-ready PDF for a contractor, an exported spreadsheet you'll attach to a tax return) and no destination is otherwise specified, the file lands here. The folder is created on first use.
- **What does *not* go in Outbox** — destination-specific artifacts that already have a canonical home:
  - **Domain notes** → `workspace/Domains/<domain>/`.
  - **Memory / state files** → `workspace/_memory/`.
  - **Source documents** (receipts, scans, vital records, signed PDFs) → `workspace/Sources/<your-folders>/` (user-defined layout per `contracts/sources.md` § 15.1). Cross-linked from `Domains/<domain>/sources.md`. Source documents NEVER live directly under `Domains/` or `Projects/`.
  - **Drafts, working files, agent-generated artifacts (not for sending)** → `workspace/Domains/<domain>/Resources/` or `workspace/Projects/<project>/Resources/`.
  - **The "if hit by a bus" packet** → handled by `handoff` skill into a configured destination (default: `workspace/Outbox/handoff/`).
- **`Outbox/` is gitignored along with the rest of `workspace/`.** Contents stay local; the framework never publishes anything on its own — that is always an explicit user action.

---

## Git commits

This policy applies whenever the agent commits framework code under `superagent/`:

### Length and tense

- **One sentence, imperative / future tense.** Examples: `Add packages.yaml index`, `Wire WHOOP ingestor into weekly-review`, `Bump version to 0.4.0`.
- **No past tense.** Not `Added…`, not `Wired…`.
- **≤ 72 characters in the subject when possible.** Tighten wording rather than wrapping onto a second line.
- **No body, no bullet list, no extra paragraphs.** If a change needs more explanation, that goes in a PR description or a code comment, not in the commit message.
- **No non-ASCII characters** in commit messages.

### No AI-attribution

**NEVER** include trailers, footers, or sign-offs that mention any AI tool used to author the change. Examples that must NOT appear in commit messages, PR descriptions, or bodies:

- `Made-with: Cursor`
- `Co-Authored-By: Cursor <cursoragent@cursor.com>` / any `Co-Authored-By:` trailer naming an AI agent or vendor
- `Generated with [Cursor]` / `Generated by AI` / `Created with AI` / `Made with ChatGPT`
- robot / sparkles emoji indicating AI origin
- `"via Cursor"` / `"with Composer"` / similar attributions
- references to model names (`gpt-…`, `claude-…`, `gemini-…`, `composer-…`)
- any mention in the message body that the change was AI-assisted, agent-generated, or co-authored with a model

This applies whether the commit is authored by a human, by the Supercoder, or by any other agent.

### Strip-after-commit (Cursor injection workaround)

Cursor auto-injects `Made-with: Cursor` on every `git commit` it runs. This is non-optional from the agent side, so the only correct workflow is **strip after every commit, before any push**. Default procedure:

```bash
git log -1 --format=%B

RANGE="$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)..HEAD"
[ "$RANGE" = "..HEAD" ] && RANGE="--all"
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --msg-filter '
  awk "
    { lines[NR]=\$0 }
    END {
      for (i=1; i<=NR; i++)
        if (lines[i] !~ /^(Made-with|Co-authored-by|Generated-by|Generated-with):.*(Cursor|Claude|Copilot|Gemini|GPT|ChatGPT|Anthropic|OpenAI)/)
          out[++n]=lines[i]
      while (n > 1 && out[n] == \"\") n--
      for (i=1; i<=n; i++) print out[i]
    }
  "
' -- $RANGE
```

The vendor-name alternation **must** be parenthesized — `(Cursor|Claude|...)`. Without parens, the `|` operator binds across the entire pattern and any commit subject containing those words anywhere gets stripped.

Run this immediately after each commit, or as a batch right before `git push`. Never push commits that contain `Made-with`, `Co-authored-by: …<AI vendor>…`, or similar trailers. After stripping, re-run `git log -1 --format=%B` to confirm.

### Atomicity and content

- **Atomic commits.** Unrelated or loosely-related changes go in different commits.
- **Only framework files** under `superagent/` are committed — **never** `workspace/` data.
- **Commit messages do not mention** anything personally identifying, household-specific, or account-specific.

---

## Local task references

When discussing items from the local task tracker (e.g. `task-2026-04-28-001`, `task-031`), **do not surface the task IDs to the user** in casual conversation. Give a brief inline noun-phrase description of what the task is about. Internal tool calls (grep, file reads) may still use IDs to locate the right rows.

---

## Read budget (token efficiency)

Per `docs/superagent/docs/_internal/perf-improvement-ideas.md` QW-3, every Superagent skill execution operates under this discipline:

- **For any file longer than 200 lines, run `Grep` first** to locate the relevant section, then use `Read --offset --limit` to pull only that range. Whole-file reads are reserved for files known to be < 200 lines OR explicitly required (e.g. `_memory/config.yaml`).
- **Skills that say "read X" implicitly mean "read the relevant section of X"** — agents should treat full reads as the exception.
- **For long skills** (`init.md`, `daily-update.md`, `monthly-review.md`, `supertailor-review.md`, `add-source.md`, …), read the YAML frontmatter + the `## Step index` block first; use `Read --offset --limit` against the listed step ranges.
- **Use the skill manifest**: read `superagent/skills/_manifest.yaml` (cheap, ~5-10 KB) to pick which skill applies, instead of grepping every skill markdown.
- **Use briefing-cache before regenerating**: if `_memory/_artifacts/<skill>/<key>.md` is fresh, read it instead of running the skill again. The `tools/briefing_cache.py get` helper checks for you.
- **Use the events stream + log summaries** instead of full-loading append-only YAML logs: `_memory/<file>.summary.yaml` (the QW-4 summary sibling) tells you whether you need the full log at all; `tools/log_window.py read` pulls just a date range.
- **BATCH parallel reads / MCP calls** in a single tool-call message rather than chaining them sequentially. Sequential chains are reserved for the cases where step N's output feeds step N+1.

The `tools/anti_patterns.py` scanner flags skills that violate these patterns. The Supertailor's strategic pass surfaces persistent violations.

## Local-first read order

Per `contracts/local-first-read-order.md` (codifies QW-7), every skill that needs data MUST consult the local copy first and fall through to a live MCP / API only when the local copy is genuinely insufficient:

1. **Local index** (`_memory/<index>.yaml`) for structured rows — bills, subscriptions, appointments, important dates, contacts, accounts, assets, documents, health records.
2. **Local Sources cache** (`<cache_path>/<hash>/`, default `Sources/_cache/`) for cached external content — read `_summary.md` first, then `_toc.yaml`, then only the relevant chunk(s) from `raw.<ext>` or `chunks/`. Refresh the derived index first via `tools/sources_index.py refresh`.
3. **Domain / Project history.md** for narrative recall.
4. **Events stream** (`_memory/events/<YYYY-Qn>.yaml` via `tools/log_window.py`) for cross-entity timeline queries.
5. **Live MCP / CLI source** ONLY when **all** are true: (a) the local read returned no candidates that match the question; AND (b) the time window the question is asking about extends past the source's `last_ingest`; AND (c) freshness genuinely matters for the question.

When the live call happens, capture-through MUST run (per the ingestion contract in `contracts/ingestion.md`) so the next read is local.

## Operational handles

Per `contracts/operational-handles.md` (item #20), every entity in the workspace has a canonical operational handle in `<kind>:<slug>` form:

- `contact:dr-smith-dentist`
- `bill:pge-electric`
- `appointment:20260512-dr-smith-cleaning`
- `project:tax-2026`
- `domain:health`
- `asset:car-blue-camry-2018`
- `task:20260428-001`
- `decision:dec-2026-04-28-001`

Use `superagent/tools/handles.py.parse(value)` to canonicalize legacy ids (`contact-dr-smith-dentist` → `Handle(kind="contact", slug="dr-smith-dentist")`). Use `format(kind, slug)` to construct. Use `is_handle(value)` to test.

The world graph (`_memory/world.yaml`) is keyed by handle. Skills that want to answer "show me everything connected to X" call `tools/world.py related <handle>`.

## Visibility and sensitive tier

Per `contracts/visibility.md` (item #19) + § "Sensitive Tier" (item #18):

- Every entity carries an optional `visibility: private | household | public` field. Default `private`. Outbound-scrub uses this to decide what redacts in shareable artifacts.
- Files routed to `_memory/sensitive/` (per `config.preferences.sensitive.auto_route_files` — defaults: `health-records.yaml`, `accounts-index.yaml`) are first-class flagged for stricter handling. The user can symlink the entire `_memory/sensitive/` directory to an encrypted disk image; skills work transparently against either layout.
- The `handoff` skill (sensitive output) writes to `Outbox/sealed/handoff/`. Skills that consume sensitive data (`medic-prep`, `bookkeeper-tax-packet`) include a "do not paste this into a chat assistant unless you trust it" banner.

## Provenance

Per `contracts/provenance.md` (item #4):

- Every newly-created entity row carries a `provenance` field documenting where the row (or its key facts) came from. Schema:
  ```yaml
  provenance:
    source: "user|<skill>|<ingestor>|init|derived"
    source_id: "<optional anchor — interaction-log id, ingest run id, etc.>"
    at: <iso datetime>
    verified_at: <optional iso — when the user last confirmed it>
  ```
- For `info.md` § Key Facts entries, use the inline annotation `<!-- src: <ref> -->` on the bullet.
- The agent uses provenance to answer "are you sure?" / "where did you get that?" — by default, surface the provenance row in the response.

## Time-shape vs entity-shape vs event-shape

Per `contracts/memory-taxonomy.md` (item #9), every YAML file in `_memory/` belongs to one of three shapes:

- **Entity-shape** — long-lived rows; mutate-in-place; cross-referenced by id.
  Files: `contacts.yaml`, `accounts-index.yaml`, `assets-index.yaml`, `domains-index.yaml`, `projects-index.yaml`, `documents-index.yaml`, `subscriptions.yaml`, `bills.yaml`, `appointments.yaml`, `important-dates.yaml`, `sources-index.yaml`, `tags.yaml`, `world.yaml`, `notification-policy.yaml`.
- **Time-shape (append-only)** — each row is an event in time; never rewritten in place.
  Files: `interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`, `decisions.yaml`, `outbox-log.yaml`, `upstream-writes.yaml`, `supertailor-suggestions.yaml`, `health-records.yaml.{vitals,symptoms,vaccines,results,visits}`.
  AND the partitioned event stream under `_memory/events/<YYYY-Qn>.yaml`.
- **State-shape (singleton snapshot)** — one current snapshot of "now"; read at session start, write at session end.
  Files: `context.yaml`, `model-context.yaml`, `data-sources.yaml`, `config.yaml`.

Tools enforce the shape — `tools/audit.py.record_change()` writes to `<file>.history.jsonl` for entity-shape files only; mutating a row in an append-only file is a bug.

## Privacy and data location

- **`workspace/`** (including everything under it) is **gitignored** and **local only** to this machine unless you copy or sync it yourself. Do not assume it is backed up or shared anywhere.
- **No telemetry.** Superagent does not phone home. No metrics, no crash reports, no "anonymous usage data" — any such mechanism would be a major-version change explicitly proposed in `docs/roadmap.md`, opt-in only, and clearly labelled.
- **No remote write.** Superagent ingestors are read-only against upstream sources by default. Any skill that intends to *write* upstream (e.g. a future "auto-pay this bill" or "create this calendar event in Google Calendar") must declare it loudly in its frontmatter, ask for confirmation per call, and log the write to `interaction-log.yaml`.
- **Sensitive subfiles.** `_memory/health-records.yaml`, `_memory/accounts-index.yaml`, and the `handoff/` subfolder are the most sensitive items in the workspace. They live alongside everything else (no separate encrypted store in MVP) but are explicitly called out so the user can choose to symlink them to an encrypted disk image, a 1Password / Bitwarden secure note reference, or a Vault-style backend later. The `roadmap.md` "Sensitive-store options" entry tracks this.
- **Sharing the workspace.** If the user wants their partner / household to share Superagent state, the supported approaches are documented in `docs/architecture.md` § "Multi-user options". TL;DR: copy the workspace folder to a shared iCloud Drive / Dropbox / Syncthing folder. There is no built-in multi-tenancy in MVP.

---

## Local development tooling

Full policy: [`superagent/rules/development-tooling.md`](superagent/rules/development-tooling.md). Four non-negotiable defaults for any contributor (human or agent):

- **Python** — one shared `uv` venv at `./.venv/`. ALWAYS invoke through `uv run` (e.g. `uv run python superagent/tools/foo.py`, `uv run python -m superagent.tools.sources_index refresh`, `uv run pytest`). NEVER call `python3 …` directly; NEVER create per-tool venvs. Dependencies live in root `pyproject.toml`; lockfile is `uv.lock` (committed).
- **Non-Python tools** — install under `./.tools/` (gitignored). NEVER install system-wide (`brew install`, `npm -g`, `cargo install --root /usr/local`, etc.) from inside this project.
- **Temporary files** — write to `./.tmp/` only (gitignored). NEVER use `/tmp`, `$TMPDIR`, `~/`, OS temp dirs, sibling repos, or anything outside the project root.
- **Scope discipline (safety rule)** — the agent MUST NOT install software, create files, or modify files outside this project folder unless the user explicitly authorizes that specific action. Read access outside is fine; write access outside is forbidden by default — refuse and ask first.

---

## IDE setup (Cursor)

Superagent currently targets Cursor as the host IDE. Cursor reads `AGENTS.md` at the repo root natively, so no `.cursor/rules/` shim is needed — the user opts in to Superagent per session by invoking a skill or by saying so, and the agent loads `AGENTS.md` on demand.

Optional Cursor wiring:

- **User-prompt logging** (used by the Supertailor for friction analysis): wire a `UserPromptSubmit` hook in `.cursor/hooks.json` that runs `uv run python superagent/tools/log_user_query.py`. The script appends one line per prompt to `workspace/_memory/user-queries.jsonl`. This is opt-in and can be disabled via `_memory/config.yaml.preferences.privacy.log_user_queries: false`.
- **Commit-message hook**: install a `commit-msg` hook at `.githooks/commit-msg` (and `git config core.hooksPath .githooks`) that blocks AI-attribution patterns at commit time. The reference implementation lives at `superagent/templates/githooks/commit-msg`.

If the repo also hosts other assistant frameworks, route each turn to the right framework based on the request's evident scope rather than auto-injecting Superagent on every turn.

## Prompt-cache discipline

Per `docs/superagent/docs/_internal/perf-improvement-ideas.md` BB-2-b — practical guidance for Cursor today:

The IDE controls how the prompt is structured and which prefixes are cached. The framework can still help by keeping AGENTS.md SHORT and STABLE, and by keeping each `contracts/<name>.md` self-contained — avoiding edits during a session that would invalidate the cache for downstream turns.

- **Don't edit `AGENTS.md` / `contracts/` mid-session.** Cursor's prompt cache rewards a stable prefix; mutating the docs that anchor the prefix forces a full re-cache, paying the long form back to the model on every subsequent turn.
- **The Supertailor / Supercoder loop's commit-then-restart cycle is well-suited to this.** After the Supertailor proposes a doc change, approve it, let the Supercoder commit, then start a fresh chat session. The new session pays the full prompt cost ONCE; subsequent turns reap the cache savings.
- **Don't open many framework files mid-session.** Each one bumps the prompt; fewer files = better cache reuse.
- **For long-running ingestion or scenario sessions**, prefer running them via dedicated tool invocations (each is a stand-alone process) rather than long chat threads.

If a future Superagent CLI wraps the Anthropic API directly, structure the prompt as: `[stable: AGENTS.md + role files] → [cache_breakpoint] → [per-skill: the active skill + the contracts it cites] → [cache_breakpoint] → [per-turn: user message + tool results]`. That's the BB-2-a path; it requires API-level control that the IDEs don't expose today.
