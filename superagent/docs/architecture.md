# Superagent — Architecture

---

## Table of Contents

- [Superagent — Architecture](#superagent--architecture)
  - [The mental model](#the-mental-model)
  - [Two halves of the repo](#two-halves-of-the-repo)
  - [Memory: structured + narrative](#memory-structured--narrative)
  - [Skills, tools, ingestors](#skills-tools-ingestors)
  - [The dual-agent loop (Supertailor + Supercoder)](#the-dual-agent-loop-supertailor--supercoder)
  - [The hard safeguard](#the-hard-safeguard)
  - [The 4-file domain convention](#the-4-file-domain-convention)
  - [The PARA / GTD / CODE lineage](#the-para--gtd--code-lineage)
  - [Sensitive subfiles](#sensitive-subfiles)
  - [Multi-user options](#multi-user-options)
  - [Extracting to a standalone repo](#extracting-to-a-standalone-repo)

---

## The mental model

Superagent is a **personal-life operating system** built on five layers:

1. **A structured-state vault** (`workspace/_memory/*.yaml`) — small, queryable indexes for things that need fast retrieval: bills, subscriptions, appointments, important dates, contacts, accounts, assets, documents, health records, projects, sources.
2. **A narrative layer — Domains** (`workspace/Domains/<domain>/*.md`) — markdown for *ongoing* areas of responsibility (Health, Finance, Home, …). The story of each domain over time, the people in it, the routines.
3. **A narrative layer — Projects** (`workspace/Projects/<slug>/*.md`) — markdown for *time-bounded* efforts (file taxes, plan trip, replace dishwasher, renovate kitchen). Same 4-file shape as Domains; they cross-link via `related_domains: [..]`.
4. **A reference library — Sources** (`workspace/Sources/`) — immutable documents the user owns (`documents/`) plus pointers to external data (`references/<*>.ref.md`), with an evictable cache (`_cache/`) so the agent reads local first.
5. **An agent skin** (`superagent/skills/*.md` + `superagent/tools/`) — invocable behaviours that read all four layers, write to layers 1-3, and speak the user's language.

All layers are designed for **graceful degradation**. Structured vault works without narrative. Narrative works without structured. Sources work without ingestion. Agent skin works at the most basic level by reading and writing markdown — every advanced feature (ingestion, caching, surfacing, audit, the Supertailor / Supercoder loop) layers on top.

The split between Domains and Projects is the PARA distinction made explicit:

- **Domain (PARA "Area")** — ongoing responsibility with a *standard to maintain*. Health is a domain because you're never "done" being healthy. Home is a domain because you're never "done" maintaining a house.
- **Project (PARA "Project")** — time-bounded effort with a *clear goal and end*. "File 2026 taxes" is a project — when the return is filed, the project is done. "Replace the dishwasher" is a project — when it's installed and running, done.

A project can touch multiple domains (a kitchen renovation touches Home + Finance). The cross-link is `related_domains: [..]` on the project; the domain's status surfaces a one-bullet summary back.

## Two halves of the repo

```
<repo-root>/
├── AGENTS.md                       ← canonical IDE-agnostic rules (read on demand)
├── README.md                       ← repo intro
├── CLAUDE.md                       ← Claude Code shim that imports AGENTS.md
├── pyproject.toml                  ← Python project config
├── .gitignore, .githooks/, .mcp.json.example, .cursor/, .claude/
│
├── superagent/                     ← framework code (committed; this is the product)
│   ├── superagent.agent.md         ← role definitions (Superagent + helpers)
│   ├── procedures.md               ← contracts: ingestion, capture, surfacing, cadences
│   ├── supertailor.agent.md             ← Supertailor's role (observer + proposer)
│   ├── supercoder.agent.md         ← Supercoder's role (Mode 1 framework + Mode 2 project build)
│   ├── skills/                     ← every skill, one .md per skill (incl. `supercoder.md`)
│   ├── templates/
│   │   ├── memory/                 ← YAML templates copied to _memory/ on init
│   │   ├── domains/                ← markdown templates per the 4-file convention
│   │   ├── projects/               ← personal-life Project templates
│   │   ├── folder-readmes/         ← READMEs scaffolded into top-level workspace folders
│   │   ├── workflows/, sources/, githooks/
│   │   └── todo.md                 ← scoped task-view template
│   ├── tools/                      ← workspace_init, validate, render_status, log_user_query, …
│   │   └── ingest/                 ← _base, _registry, _orchestrator, _stubs, per-source ingestors
│   ├── tests/                      ← pytest; runs against templates + tools + skills
│   └── docs/                       ← this file and friends
│
└── workspace/                      ← user data (gitignored; created by init)
    ├── _memory/                    ← YAML indexes (the structured-state vault)
    ├── _custom/                    ← per-user overlay (additive; rules / skills / templates)
    ├── _checkpoints/<date>/        ← daily memory snapshots (auto, 14-day retention)
    ├── Domains/                    ← per-domain folders (ongoing responsibilities)
    │   └── <Domain>/               ←   info.md / status.md / history.md / rolodex.md / sources.md
    │       └── Resources/          ←     drafts / working files / agent artifacts (lazy)
    ├── Projects/                   ← personal-life projects (time-bounded efforts)
    │   └── <project-slug>/         ←   info.md / status.md / history.md / rolodex.md / sources.md
    ├── Sources/                    ← reference library (IMMUTABLE except _cache/)
    │   ├── documents/              ←   actual local files; never deleted by skills
    │   ├── references/             ←   `.ref.md` pointers to external data
    │   └── _cache/                 ←   fetched copies (TTL + LRU eviction)
    ├── Inbox/                      ← staging for incoming files
    ├── Outbox/                     ← shareable artifacts (drafts, summaries, handoff packet)
    ├── Archive/                    ← reversible archive (per `doctor` skill)
    └── todo.md                     ← cross-cutting task view
```

The two halves never write into each other. The framework reads the workspace and may modify the workspace; the workspace never modifies the framework. The framework code is committable; the workspace is gitignored and stays local to the user's machine forever.

## Memory: structured + narrative

The two layers serve different access patterns:

**Structured (YAML in `_memory/`)** — when the agent needs to answer "show me X" questions fast:

| File | Owns | Read by |
|---|---|---|
| `config.yaml` | profile, preferences, ingestion config | every skill |
| `context.yaml` | rolling state (last_check, current_focus, alerts) | `whatsup`, `daily-update`, `weekly-review`, `monthly-review` |
| `model-context.yaml` | model's accumulated learning across sessions | every skill (read at session start) |
| `interaction-log.yaml` | append-only log of touchpoints (every interaction the agent records) | `follow-up`, `summarize-thread`, all cadence skills |
| `ingestion-log.yaml` | append-only per-run summaries of every ingestor invocation | `ingest`, Supertailor |
| `todo.yaml` | task list (P0-P3) | `todo`, `triage-overdue`, all cadence skills |
| `domains-index.yaml` | metadata about each domain folder | every skill |
| `projects-index.yaml` | metadata about each project (charter, lifecycle, target_date) | `add-project`, `projects`, all cadence skills |
| `sources-index.yaml` | metadata about each Sources/ entry (doc + ref) | `sources`, `add-source`, every skill that needs reference data |
| `assets-index.yaml` | every owned physical thing | `add-asset`, `vehicle-log`, `home-maintenance`, `pet-care`, monthly-review |
| `accounts-index.yaml` | every financial / utility / subscription account | `add-account`, `bills`, `expenses`, finance ingestors |
| `contacts.yaml` | every person | `add-contact`, every interaction skill |
| `bills.yaml` | recurring bills | `bills`, `daily-update`, `monthly-review`, finance ingestors |
| `subscriptions.yaml` | recurring subscriptions | `subscriptions`, `monthly-review` |
| `appointments.yaml` | scheduled appointments | `appointments`, `daily-update`, calendar ingestors |
| `important-dates.yaml` | birthdays / anniversaries / deadlines | `important-dates`, `daily-update` |
| `documents-index.yaml` | important documents (with expiration tracking) | `add-document`, `monthly-review`, `handoff` |
| `health-records.yaml` | medical events, meds, vitals, conditions | `health-log`, `appointments` (medical), monthly-review |
| `data-sources.yaml` | per-source ingestion config and state | every ingestor, `ingest` skill |
| `personal-signals.yaml` | self-development feedback (capture + surface) | `personal-signals`, `weekly-review`, Supertailor |
| `action-signals.yaml` | "this should change" signals (target: tailor / superagent) | every skill (capture); Supertailor (drain) |
| `supertailor-suggestions.yaml` | Supertailor's framework-improvement backlog | Supertailor, Supercoder |
| `procedures.yaml` | personal playbooks | `research`, ad-hoc retrieval |
| `insights.yaml` | distilled learnings | `research`, ad-hoc retrieval |

**Narrative (markdown in `Domains/<domain>/`)** — when the agent needs to recall context, explain a situation, or write something for a human:

```
Domains/<domain>/
  info.md       — narrative overview, current state, key facts, routines, stakeholders
  status.md     — RAG status + open / done tasks (auto-synced from todo.yaml)
  history.md    — chronological log (newest at top); H4 entries: #### YYYY-MM-DD — title
  rolodex.md    — domain-scoped contact directory (auto-synced from contacts.yaml)
  sources.md    — curated catalogue of Sources/ entries for this domain
  Resources/    — optional, lazily created — drafts, working files, agent artifacts
```

Skills know how to traverse both layers — query the YAML for "what", read the markdown for "why".

## Skills, tools, ingestors

The three are deliberately separated:

- **Skills** (`skills/*.md`) are **instructions for the agent** in human-readable markdown with YAML frontmatter. The agent reads the file when invoked and follows the steps. Skills do not contain executable code; they contain procedures the agent runs.
- **Tools** (`tools/*.py`) are **executable Python** for repeatable transforms (scaffold, validate, render, hook). Tools are invoked from skills via the agent's shell tool (`python3 superagent/tools/<tool>.py`).
- **Ingestors** (`tools/ingest/<source>.py`) are a **specialized class of tools** — one per data source — that implement the `IngestorBase` contract. They probe, reauth, and run. The `ingest` skill is the user-facing front-end; the `_orchestrator.py` is its CLI implementation; per-source modules contain the actual scraping logic.

This separation means **skills are language**, **tools are code**, and **ingestors are pluggable**. A user can write a custom skill (in `_custom/skills/`) without touching code; a developer can add a new tool without changing any skill; a community contributor can add a new ingestor for a new data source by dropping a file in `tools/ingest/` and adding a row to `_registry.py`.

## The dual-agent loop (Supertailor + Supercoder)

Superagent ships with built-in introspection. The full role definitions are in `supertailor.agent.md` and `supercoder.agent.md`; the user-facing skill is `tailor-review`. The high-level loop:

```
┌────────────────────────────────────────────────────────────────────────┐
│  user works with Superagent                                             │
│    every chat turn → optional ambient capture into:                     │
│      _memory/personal-signals.yaml   (about the user)                   │
│      _memory/action-signals.yaml     (about the framework or workspace) │
│    every skill run → entry in _memory/interaction-log.yaml              │
│    every UserPromptSubmit → line in _memory/user-queries.jsonl          │
└────────────────────────┬───────────────────────────────────────────────┘
                         │
                         │  (every 90 days, or when nudged)
                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Supertailor (tailor-review skill)                                           │
│    1. HYGIENE PASS  — template compliance, orphans, staleness, schemas  │
│    2. STRATEGIC PASS — friction patterns, capability gaps, novel needs  │
│    Each suggestion: tagged destination = superagent | _custom         │
│    Hard safeguard: token-scan for personal data → forces _custom        │
│    Writes to _memory/supertailor-suggestions.yaml; surfaces report               │
└────────────────────────┬───────────────────────────────────────────────┘
                         │
                         │  user approves / declines / defers
                         ▼
┌──────────────────────────────────┬─────────────────────────────────────┐
│  destination = _custom           │  destination = superagent           │
│   ────────────────────           │   ───────────────────────           │
│  Supertailor hands brief to      │  Supertailor packages a brief       │
│   Supercoder                     │   and hands to Supercoder           │
│  Supercoder writes into          │  Supercoder re-runs safeguard       │
│   workspace/_custom/             │    REFUSE on personal-data match    │
│  Supercoder runs overlay tests   │  Supercoder modifies superagent/    │
│   if any                         │  Supercoder updates tests           │
│  No commit (workspace is         │  Supercoder runs `pytest -q`        │
│   gitignored)                    │  Supercoder commits (single sent.,  │
│  Supertailor flips status:       │    imperative, no AI-attribution)   │
│   implemented (no SHA)           │  Supertailor flips status:          │
│                                  │   implemented with commit SHA       │
└──────────────────────────────────┴─────────────────────────────────────┘
```

This is "the framework that builds itself." It works because the safeguards and the destination contract make it provably safe to commit Supercoder's output: workspace data CANNOT leak into committed framework code.

## The hard safeguard

Both Supertailor and Supercoder run a token scan before writing anything to `superagent/`:

- Every name from `_memory/contacts.yaml.contacts[].name` and `.aliases[]`.
- Every domain id from `_memory/domains-index.yaml`.
- Every asset name and serial from `_memory/assets-index.yaml`.
- Every account label from `_memory/accounts-index.yaml`.
- Address / license-plate / account-number regex patterns.

On any match, the destination is **forcibly re-routed to `_custom/`**, and an `implementation_notes` row records what triggered the re-route. The user is told.

The safeguard runs **twice** — once in the Supertailor (at suggestion-write time) and once in the Supercoder (at brief-receipt time). Defense in depth: a Supertailor bug should not be able to leak data through to committed code.

## The 4-file domain convention

Every Domain folder has the same four files. The convention exists for two reasons:

1. **The same skills can read every domain identically.** A skill that needs "show me the recent history of <X>" can read `Domains/<X>/history.md` without knowing whether X is Health, Finance, or a custom domain the user added five minutes ago.
2. **Hand-edits and machine-edits coexist.** Each file has named sub-sections; skills update specific sections only, and respect (diff and merge, not blindly clobber) hand-edits in any section.

The four files:

- `info.md` — narrative overview, current state, key facts, routines, stakeholders. Mostly hand-curated; skills update specific sub-sections (Key Facts, Stakeholders, Routines).
- `status.md` — RAG + Open / Done. Auto-synced from `todo.yaml` for the Open / Done blocks; the RAG / Recent Progress / Active Blockers / Next Steps blocks are hand-curated with skill assistance.
- `history.md` — chronological log; newest entry at top; H4 entries (`#### YYYY-MM-DD — <title>`); skills append automatically.
- `rolodex.md` — domain-scoped contact directory; auto-synced from `contacts.yaml`.

The convention extends to per-pet folders (`Domains/Pets/<pet-slug>/`) and could extend to per-vehicle, per-property, per-trip if a user wants finer slicing.

## The PARA / GTD / CODE lineage

Superagent's design borrows from three established frameworks:

- **PARA (Tiago Forte)** — Projects, Areas, Resources, Archives. Superagent maps cleanly: **Domains** = Areas (ongoing responsibilities). **Projects** = Projects (time-bounded efforts). **`Sources/` + per-domain `Resources/`** together cover Resources (canonical reference vault for the immutable source documents; per-domain working artifacts for everything else). **`Archive/`** = Archives. The split between `Sources/` (immutable canonical store) and `Resources/` (working / agent-generated) is Superagent-specific — PARA collapses both into "Resources".
- **GTD (David Allen)** — Capture, Clarify, Organize, Reflect, Engage. Superagent's `add-*` and `log-event` skills are "capture". The structured indexes are "organize". Cadence skills (`whatsup`, `daily-update`, `weekly-review`, `monthly-review`) are "reflect". `todo` and the surfaced action prompts are "engage". "Clarify" happens in the chat conversation between user and agent.
- **CODE (Tiago Forte's "Building a Second Brain", refreshed 2026)** — Capture, Organize, Distill, Express. Superagent does the AI-evolved version: ambient ingestion handles Capture; the indexes + auto-routing handle Organize; the cadence skills do Distill (turning a flood of input into a daily 5-line briefing); the user — with `draft-email`, `summarize-thread`, the rendered Outbox artifacts — does Express.

The frameworks were designed for the pre-AI era when capture / organize / distill all required human effort. Superagent's bet is that those three are what AI is great at; the human stays focused on the parts that need judgment (decisions, creativity, relationships).

## Sensitive subfiles

`workspace/` is gitignored end-to-end and stays on the user's machine. Even so, some subfiles are more sensitive than others:

| File / folder | Why sensitive | Mitigation options |
|---|---|---|
| `_memory/health-records.yaml` | medications, conditions, family history | symlink to encrypted disk image; back up only via encrypted destination |
| `_memory/accounts-index.yaml` | account labels + last-4 (full creds in vault) | same; full creds NEVER stored here, always vault_ref |
| `_memory/contacts.yaml` | phone numbers, addresses | same |
| `Outbox/handoff/` | aggregated estate-handoff packet | print + safe-deposit-box; encrypted USB |
| `Sources/documents/pets/*` | vet records (often contain home address) | encrypted destination |
| `_memory/data-sources.yaml.<source>.auth.ref` | references to credentials | not the credentials themselves; references to a vault |

In MVP, no built-in encryption. Roadmap entry "Sensitive-store options" (`docs/roadmap.md`) tracks the path to native encryption support.

## Multi-user options

If the user wants their partner / household to share Superagent state:

1. **Shared workspace (full sharing)** — copy `workspace/` to a shared iCloud Drive / Dropbox / Syncthing folder. Both users point Superagent at the same workspace. Edit conflicts are last-write-wins (no merge logic in MVP); simultaneous editing is not recommended.
2. **Federated workspace (per-user, with shared subset)** — each user has their own `workspace/`. Specific subfolders (e.g. `Domains/Family/`, `Domains/Home/`, `Domains/Pets/`) are symlinked into a shared cloud folder. Personal subfolders (Health, Career, Self) stay private.
3. **Single-user with handoff** — one person runs Superagent; the partner gets the `handoff` packet annually for "if hit by a bus" continuity, and on demand for tax-prep / annual-review style snapshots.

Built-in multi-user / sync is on the roadmap (LOE-L: "Multi-user vault with last-write-wins + per-domain ACL").

## Extracting to a standalone repo

Superagent is intentionally self-contained. To extract it to its own repo:

```bash
# from the host repo
mkdir ~/superagent-repo
cp -r superagent ~/superagent-repo/
cd ~/superagent-repo
mv superagent/* .
mv superagent/.* . 2>/dev/null
rmdir superagent

git init
mkdir -p .githooks
cp templates/githooks/commit-msg .githooks/commit-msg
chmod +x .githooks/commit-msg
git config core.hooksPath .githooks

cat > .gitignore <<EOF
workspace/
__pycache__/
*.pyc
.pytest_cache/
.venv/
.DS_Store
EOF

# Optional: add IDE-specific shims so the framework is auto-recognized.
mkdir -p .cursor/rules
cat > .cursor/rules/superagent.mdc <<'EOF'
---
description: Superagent personal-life assistant — read when invoked or when working under workspace/
alwaysApply: false
globs:
  - "**/*"
---
Read and follow AGENTS.md.
EOF

# At this point AGENTS.md is at the repo root; everything works.
git add .
git commit -m "Initialize Superagent framework"
```

After extraction, the framework is fully standalone — no references to any host repo or other framework remain. (The framework was designed with this property as a hard requirement.)
