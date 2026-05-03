# Superagent — Ideas to better structure the framework

A working brainstorm of structural improvements to consider as Superagent matures. Not a roadmap (`roadmap.md` is the prioritized backlog) — a **catalogue of options** with reasoning. Each idea has rough trade-offs so you can decide later which to pursue.

Ideas are tagged by **scope of change**:

- **Mechanical** — reorganization, no schema breakage. Easy to apply or revert.
- **Schema** — touches YAML schema; needs a migration. Riskier.
- **Conceptual** — changes the mental model. Requires user re-orientation.
- **Operational** — changes how skills behave; no schema or layout change.

---

## Table of Contents

- [Superagent — Ideas to better structure the framework](#superagent--ideas-to-better-structure-the-framework)
  - [1. The PARA tetrad — promote Resources to a first-class peer of Domains/Projects](#1-the-para-tetrad--promote-resources-to-a-first-class-peer-of-domainsprojects)
  - [2. Workflow templates as first-class artifacts](#2-workflow-templates-as-first-class-artifacts)
  - [3. The "world model" YAML — entity graph](#3-the-world-model-yaml--entity-graph)
  - [4. Provenance + facts-with-sources](#4-provenance--facts-with-sources)
  - [5. Inbox → Triage → File as a first-class pipeline](#5-inbox--triage--file-as-a-first-class-pipeline)
  - [6. Differential snapshots + change-detection](#6-differential-snapshots--change-detection)
  - [7. Embedded full-text search index](#7-embedded-full-text-search-index)
  - [8. Embeddings for semantic recall](#8-embeddings-for-semantic-recall)
  - [9. Time-shape vs entity-shape vs event-shape](#9-time-shape-vs-entity-shape-vs-event-shape)
  - [10. Sub-domains and sub-projects (proper hierarchies)](#10-sub-domains-and-sub-projects-proper-hierarchies)
  - [11. Tagging as a first-class index](#11-tagging-as-a-first-class-index)
  - [12. Multi-user separation (per-user workspaces with shared sub-trees)](#12-multi-user-separation-per-user-workspaces-with-shared-sub-trees)
  - [13. The Outbox lifecycle (drafts vs sent vs sealed)](#13-the-outbox-lifecycle-drafts-vs-sent-vs-sealed)
  - [14. Scenario / what-if planning](#14-scenario--what-if-planning)
  - [15. Calendar write-back as an opt-in capability](#15-calendar-write-back-as-an-opt-in-capability)
  - [16. The "events" stream as the unifying timeline](#16-the-events-stream-as-the-unifying-timeline)
  - [17. Audit trail on every YAML row](#17-audit-trail-on-every-yaml-row)
  - [18. Tiered storage for sensitive data](#18-tiered-storage-for-sensitive-data)
  - [19. Public / private / shared visibility flags](#19-public--private--shared-visibility-flags)
  - [20. The "operational handle" pattern (every entity gets a slug-id)](#20-the-operational-handle-pattern-every-entity-gets-a-slug-id)
  - [21. Skill bundles / playbooks / "scenes"](#21-skill-bundles--playbooks--scenes)
  - [22. Time-windowed views over append-only logs](#22-time-windowed-views-over-append-only-logs)
  - [23. The agent's working set as an explicit object](#23-the-agents-working-set-as-an-explicit-object)
  - [24. First-class "decisions" log](#24-first-class-decisions-log)
  - [25. Notification policy as configuration, not skill code](#25-notification-policy-as-configuration-not-skill-code)
  - [Selection guide — which ideas to pursue first](#selection-guide--which-ideas-to-pursue-first)

---

## 1. The PARA tetrad — promote Resources to a first-class peer of Domains/Projects

**Tag**: Conceptual.

The current MVP maps Tiago Forte's PARA as: Domains = Areas, Projects = Projects, Sources = Resources, Archive = Archive. But Sources is structured around documents-and-pointers, not around topics. **Resources** in PARA is topic-shaped — "things I'm interested in but not actively responsible for or working on": photography, woodworking, distributed systems, history of medicine, etc.

**Idea**: split `Sources/` into two:

- `Sources/` — current shape: documents + .ref pointers (the "vault").
- `Resources/` — topic-shaped reference areas (the "library"). Same 4-file structure as Domains. Examples: `Resources/Photography/`, `Resources/Distributed-Systems/`. Used for Reading / learning / "I want to study this".

**Trade-off**: Adds another concept the user has to learn. But makes the agent's understanding of "what kind of thing is this?" more accurate. A book about taxes goes in `Sources/documents/taxes/` (it's a working doc). A book about the history of taxes goes in (potentially) `Resources/Tax-Policy/Resources/` (it's an interest). The naming would need tightening — perhaps the topic-shaped library should be called `Library/` to avoid colliding with the per-entity `Resources/` folder.

**When to do**: when the user finds themselves asking "where do I put a book / podcast / paper I want to read but isn't tied to a domain or project?".

---

## 2. Workflow templates as first-class artifacts

**Tag**: Schema.

`_memory/procedures.yaml` exists but is just a YAML log of "things to remember". Real workflow templates would be:

- A versioned, parameterized template stored under `superagent/templates/workflows/<name>.yaml`.
- Instantiated by a skill ("instantiate the `tax-filing` workflow for year 2026") which generates a Project, the seed task list, the success criteria, the deliverables, the Sources sub-folder, the relevant ingestor configs.
- Lessons-learned from each instance feed back into a `procedures.yaml` row that gets surfaced when the next instance is created.

**Trade-off**: A workflow language is a small DSL — needs design. Could go too far into "low-code workflow builder" territory.

**When to do**: when the user notices they keep repeating the same multi-step setup ("I always create the same 6 tasks when I file taxes"). Today's `add-project` Q4 (suggest tasks based on project kind) is a precursor.

---

## 3. The "world model" YAML — entity graph

**Tag**: Schema.

Today's data model has many indexes (`contacts`, `assets`, `accounts`, `bills`, `subscriptions`, `appointments`, …) but the relationships between them are ad-hoc string ids in fields like `related_domain` / `related_project` / `related_asset`. There's no inverse index — you can ask "what bills are linked to this account?" but only by scanning bills.yaml.

**Idea**: a `_memory/world.yaml` (or `_memory/_graph/*.yaml`) that maintains an **explicit entity-relationship graph**:

```yaml
nodes:
  - { id: "acct-chase-1234", kind: "account" }
  - { id: "bill-pge-electric", kind: "bill" }

edges:
  - { from: "bill-pge-electric", to: "acct-chase-1234", kind: "pay_from" }
  - { from: "appt-20260512-dr-smith-cleaning", to: "contact-dr-smith-dentist", kind: "provider" }
  - { from: "appt-20260512-dr-smith-cleaning", to: "domain-health", kind: "scoped" }
```

Updated transactionally on every entity write. Queryable in O(degree) for "what's connected to X?".

**Trade-off**: maintenance burden — every write touches the graph. Risk of getting out of sync. But unlocks "show me everything related to <thing>" as a single fast query.

**When to do**: when the user asks "show me everything about <thing>" frequently and the agent has to scan multiple files to assemble it.

---

## 4. Provenance + facts-with-sources

**Tag**: Schema.

Right now a fact in a domain's `info.md` § Key Facts (e.g. "HVAC: Carrier 5-ton, installed 2019") has no recorded source. Did the user enter it? Did the home_assistant ingestor extract it? When?

**Idea**: every fact carries a source pointer. In structured indexes, add a `source: { skill: "...", source_id: "...", at: "<iso>" }` field. In markdown, allow inline `<!-- src: <ref> -->` annotations that the agent respects when re-rendering.

This becomes critical when the user says "are you sure the HVAC was installed in 2019?" — the agent can answer "yes; sourced from the install receipt at `Sources/documents/warranties/hvac/install-2019.pdf`, verified 2024-03-12".

**Trade-off**: bloats the schema. Mitigated by making `source` optional with a sane default ("source: user, at: <created>").

**When to do**: when the user catches the agent making an unsourced claim and asks where it came from.

---

## 5. Inbox → Triage → File as a first-class pipeline

**Tag**: Operational.

Today `Inbox/` is a free-for-all the user manually drains. There's no triage flow.

**Idea**: an `inbox-triage` skill that walks every file:

- Auto-classify by content (PDF / image / text / audio).
- Auto-suggest a destination (Sources/documents/<category>/<asset-slug>/) based on filename, OCR, EXIF.
- Ask the user for each: file / discard / leave.
- Fully reversible (everything moved goes through `add-source` so it's indexed).

Plus an `Inbox/_processed.yaml` log that records every triage decision so the agent learns the user's filing patterns ("you always file Verizon receipts under `home/utilities/verizon/`").

**Trade-off**: needs OCR for images / scans. Roadmap M-06 already covers this.

**When to do**: as soon as the gmail ingestor lands and starts auto-saving attachments to `Inbox/`.

---

## 6. Differential snapshots + change-detection

**Tag**: Operational.

`_memory/_checkpoints/<date>/` is a daily backup. It's coarse — the whole `_memory/` snapshotted, no diff, no summary.

**Idea**: a `tools/snapshot_diff.py` that, between any two checkpoints, computes:

- Per-file changes (rows added / modified / removed).
- New entities introduced.
- Status flips on existing entities.
- Renders a "what changed in your workspace this week" report.

This is the data view of the Supertailor's strategic-pass insights — but for the user, not the framework.

**Trade-off**: snapshots already exist; this is just adding a diff tool. Low cost, high value.

**When to do**: as soon as a user wants "what changed since I last opened this".

---

## 7. Embedded full-text search index

**Tag**: Operational.

Today the agent grep's across the workspace for keyword search. That's fine for small workspaces but degrades as content grows (every file scanned; no relevance ranking).

**Idea**: maintain a tiny SQLite FTS5 index at `_memory/_search/index.sqlite`. Updated incrementally on every write. Queries return ranked, snippet-bearing results in O(log n).

**Trade-off**: maintenance burden + dependency on `sqlite3`. Could degrade to grep when the index is stale or missing (graceful fall-back).

**When to do**: when the workspace exceeds a few thousand markdown files and grep starts feeling slow.

---

## 8. Embeddings for semantic recall

**Tag**: Operational + Schema.

Beyond keyword search, the agent should be able to answer "find that thing I wrote about <vague concept> a few months ago" — semantic search.

**Idea**: every markdown file (and every long YAML row) gets an embedding stored in `_memory/_embeddings/<path>.vec`. On query, the agent embeds the query, runs a cosine-similarity search, returns the top-k.

Store with a small format like `numpy .npz` per file or one consolidated `vectors.parquet`. Use a small local embedder (e.g. `bge-small`) so this runs without external API calls.

**Trade-off**: requires an embedding model + vector library. Roadmap M-08 ("Supertailor's strategic pass — better friction-clustering using embeddings") would set the precedent.

**When to do**: when "find that thing I wrote about" is a frequent failure of grep-based search.

---

## 9. Time-shape vs entity-shape vs event-shape

**Tag**: Conceptual.

Looking at the YAML indexes, there are three distinct shapes:

- **Entity-shape**: `contacts.yaml`, `accounts-index.yaml`, `assets-index.yaml`, `domains-index.yaml`, `projects-index.yaml`, `documents-index.yaml`, `subscriptions.yaml`, `bills.yaml` — each row is a long-lived thing.
- **Time-shape (append-only)**: `interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml`, `health-records.yaml.{vitals,symptoms,vaccines,results,visits}`, `supertailor-suggestions.yaml`. Each row is an event in time.
- **State-shape (mutable)**: `context.yaml`, `model-context.yaml`, `data-sources.yaml`. A mostly-singleton snapshot of "now".

**Idea**: codify this taxonomy. Every index file declares one of `{entity, time, state}` in its header comment. Tools handle each kind differently:

- Entity-shape: full read on access; mutate-in-place; cross-referenced by id.
- Time-shape: append-only; rotate yearly; queryable by time range.
- State-shape: read-on-start; write-on-end; never grows.

The kinds are already informally followed; making them explicit would catch schema drift early (e.g. if someone tries to mutate an `interaction-log` row in place, validation refuses).

**Trade-off**: another contract to enforce. Low cost; high clarity.

**When to do**: at the next schema migration.

---

## 10. Sub-domains and sub-projects (proper hierarchies)

**Tag**: Schema.

Today, Domains and Projects are flat. `Domains/Health/` doesn't have `Domains/Health/Mental-Health/` as a sub-domain. Projects can have sub-folders ad-hoc but no first-class concept.

**Idea**: allow `parent` field in `domains-index.yaml` / `projects-index.yaml`. Render hierarchically. Skills walk parents transparently:

```yaml
domains:
  - id: "health"
    name: "Health"
    parent: null
  - id: "health-mental"
    name: "Mental Health"
    parent: "health"
```

Or for projects:

```yaml
projects:
  - id: "kitchen-reno-2026"
    name: "Renovate Kitchen"
  - id: "kitchen-reno-2026-cabinets"
    name: "Cabinets sub-project"
    parent: "kitchen-reno-2026"
```

**Trade-off**: adds cognitive overhead for users who don't need it. Mitigation: keep it strictly opt-in; flat is the default.

**When to do**: when a user's domain or project starts overflowing its single folder into 50+ files and natural sub-groups emerge.

---

## 11. Tagging as a first-class index

**Tag**: Schema.

Many YAML rows have `tags: []`. They're free-form strings. There's no validation, no "show me everything tagged X" skill.

**Idea**: `_memory/tags.yaml` with one row per tag (canonical name, aliases, brief description, count of usages). A `tags` skill that lists, renames, merges, surfaces tagged items across all index files.

**Trade-off**: small overhead. Pays off when the user starts using tags as a cross-cutting categorization (e.g. `tax-deductible`, `must-do-quarterly`, `urgent`).

**When to do**: as soon as a tag appears in 3+ different index files.

---

## 12. Multi-user separation (per-user workspaces with shared sub-trees)

**Tag**: Schema + Conceptual.

Today the workspace is single-user. The roadmap (L-01, L-02) tracks proper multi-user. The structural question is: do we represent the multi-user case as separate workspaces with symlinked sub-trees, OR as a single workspace with `owner: <user>` tags on every entity?

**Option A** (federated): one workspace per user; share `Domains/Family/`, `Domains/Home/`, `Domains/Pets/`, parts of `Sources/` via symlinks or a synced cloud folder. Simple; clean privacy boundaries. Conflict on shared files is last-write-wins.

**Option B** (single-tenant-with-ACL): one workspace; every entity has `owner` and `visible_to: []`. Skills filter by current-user perspective. Cleaner unified queries; harder to implement; trickier on backup / extract.

The MVP recommends Option A; the roadmap notes the trade-off. The Supertailor's strategic pass would surface evidence that points to Option B if multi-user use becomes real.

**When to do**: if / when a household member asks to be onboarded.

---

## 13. The Outbox lifecycle (drafts vs sent vs sealed)

**Tag**: Operational + Schema.

Today, `Outbox/` is a flat folder. There's no concept of "this draft is awaiting send" vs "this was sent" vs "this is a sealed snapshot".

**Idea**: introduce sub-folders by lifecycle:

```
Outbox/
  drafts/      ← in-progress; agent may revise
  staging/     ← finalized; awaiting user "send"
  sent/        ← user marked sent; immutable thereafter
  sealed/      ← snapshots (e.g. handoff packet versions); immutable
```

Plus `_memory/outbox-log.yaml` recording every transition and every user-initiated send.

**Trade-off**: adds folders. Pays off when the user starts confusing "draft I'm still editing" with "draft I forgot to send".

**When to do**: when there are 10+ unsent drafts in `Outbox/` from a single user session.

---

## 14. Scenario / what-if planning

**Tag**: Operational.

The agent today is reactive. A scenario engine would let the user ask:

- "If I cancel Adobe, Disney+, and Hulu, how much do I save annually?" → reads `subscriptions.yaml`, computes.
- "If I take that job offer with the salary delta, when do I hit my retirement target?" → reads `accounts-index.yaml.<retirement>` + the offer details.
- "If the kitchen reno overruns by 30%, do I still hit my emergency-fund minimum?" → reads `bills.yaml` (planned) + `accounts-index.yaml`.

**Idea**: a `scenarios` skill that runs forward simulations from current state given user-provided perturbations. Outputs go to `Outbox/scenarios/<name>.md`.

**Trade-off**: each scenario is a small custom calculation. Generic framework is hard; the right cut may be to ship 5-10 named scenarios rather than a generic engine.

**When to do**: roadmap LOE-L item ("AI-assisted financial planning") covers this for the financial slice. Generalize from there.

---

## 15. Calendar write-back as an opt-in capability

**Tag**: Operational.

Today, every ingestor is `writes_upstream: false` in MVP — read-only. Calendar write-back (creating an event in Google Calendar from `add-appointment`) would close the loop on appointment-management.

**Idea**: per-source write capability, gated by a `writes_upstream: true` flag in `data-sources.yaml.<source>`. When true:

- The skill that wants to write surfaces the user-facing prompt: "Want me to also create this in Google Calendar?".
- Each write is logged to `_memory/upstream-writes.yaml` (its own append-only log) so the user can audit.
- The Supertailor surfaces unusual write patterns as candidates for review.

**Trade-off**: write capability is a major safety boundary. Don't enable casually. Per-source opt-in + per-call confirmation is the right shape.

**When to do**: when the user asks "why doesn't appointments mark-complete also update my Google Calendar?".

---

## 16. The "events" stream as the unifying timeline

**Tag**: Conceptual + Schema.

Today there are multiple time-shape streams: `interaction-log.yaml`, `health-records.yaml.visits[]`, `bills.yaml.history[]`, `assets-index.yaml.<>.maintenance.history`, project `history.md` H4 entries, etc.

**Idea**: a single canonical events stream — `_memory/events.yaml` (rotated yearly to `events-<YYYY>.yaml`) — that every skill writes to with a discriminated `kind`. The per-entity history stays as a denormalized projection; the events stream is the source of truth.

This makes "what happened on April 15, 2024?" a single-file query. It also makes the diff / snapshot-diff / per-day timeline UI trivial.

**Trade-off**: schema migration. Risk of double-write bugs (write to events AND to the per-entity history).

**When to do**: when cross-entity timeline queries become common.

---

## 17. Audit trail on every YAML row

**Tag**: Schema.

Each YAML row has a `last_updated` field but no history of WHO / WHEN / WHY.

**Idea**: each entity-shape file gets a sibling `<file>.history.jsonl` (append-only). Every mutation writes a row: `{ ts, who, kind: "create|update|delete", row_id, old, new, source: <skill-name> }`.

Lets the user answer "when did the dentist's email change? from what to what?". Also lets the user roll back individual row changes without restoring a full snapshot.

**Trade-off**: doubles the write count. Disk space scales with edits. Mitigation: rotate yearly.

**When to do**: when the user catches the agent silently mutating a row they didn't expect.

---

## 18. Tiered storage for sensitive data

**Tag**: Operational + Schema.

Today, all `_memory/` lives in one place, gitignored. Some files are obviously more sensitive (health-records, accounts-index, handoff packet).

**Idea**: a `_memory/sensitive/` sub-folder. Skills that write sensitive data write here. The folder is `chmod 700`. Optionally symlinked to an encrypted disk image. The `config.preferences.sensitive_path` setting points anywhere.

This makes "back up my workspace but don't include the sensitive bits" a single `rsync --exclude=sensitive/` away.

**Trade-off**: schema migration to move existing files. Doable at next major version.

**When to do**: roadmap M-01 covers this. Higher priority once the user has a year of health-records.

---

## 19. Public / private / shared visibility flags

**Tag**: Schema.

Pre-cursor to multi-user. Every entity gets a `visibility: private | household | public` field:

- `private`: only the owner sees it.
- `household`: everyone in the household workspace sees it.
- `public`: included in any export or handoff packet by default.

Today, everything is implicitly `private`. Adding the field opens the door to multi-user without requiring it.

**When to do**: just before the multi-user feature lands. Premature otherwise.

---

## 20. The "operational handle" pattern (every entity gets a slug-id)

**Tag**: Schema (cleanup).

Today, ids are inconsistent: `contact-dr-smith-dentist`, `task-20260428-001`, `tax-2026`, `psig-2026-04-28-001`, `st-2026-04-28-001`. Each works, but they don't compose.

**Idea**: standardize on `<kind>:<slug>` (with a colon). Every reference in any field uses this canonical form: `contact:dr-smith-dentist`, `task:20260428-001`, `project:tax-2026`. The agent's parser splits on `:` to find the kind.

**Trade-off**: schema migration; touches every cross-reference field. Big-bang change. Better done sooner than later.

**When to do**: at the next major schema bump (would coincide with idea #16 or #17).

---

## 21. Skill bundles / playbooks / "scenes"

**Tag**: Operational.

Today, skills are atomic. The user invokes one at a time. But many real workflows are a sequence of skills.

**Idea**: a `playbooks/` folder of named multi-skill flows:

```yaml
# superagent/playbooks/start-of-day.yaml
name: "Start of day"
trigger:
  - "good morning"
  - "starting my day"
steps:
  - whatsup
  - if: bills_overdue > 0
    then: bills mark-paid (interactive)
  - daily-update
  - if: appointments_today > 0
    then: appointments prep (for today's first)
```

Or "End of week", "Tax-prep season opening", "Pre-trip-week".

**Trade-off**: a tiny DSL. The agent could already chain skills if asked, but a named bundle is more discoverable + more consistent.

**When to do**: when the user notices they always run skills A → B → C in sequence.

---

## 22. Time-windowed views over append-only logs

**Tag**: Operational.

`interaction-log.yaml` grows forever. Reading the whole thing on every skill invocation gets slow.

**Idea**: a `tools/log_window.py` that yields a sub-window in O(log n) using monthly partitioning:

```
_memory/interaction-log/
  2026-04.yaml
  2026-05.yaml
  ...
  current.yaml      ← symlink or short live tail
```

Skills query by time range; reader knows which partitions to load.

**Trade-off**: schema migration (single file → partitions). Rotation logic. Mitigation: keep `interaction-log.yaml` as a back-compat symlink to `current.yaml` for a release.

**When to do**: when `interaction-log.yaml` exceeds 50 MB.

---

## 23. The agent's working set as an explicit object

**Tag**: Operational.

Right now the agent's "what am I working on this turn" is implicit — pulled from chat context. There's no record afterward of what files were loaded, what was retrieved.

**Idea**: each skill invocation records its `working_set` — the full list of files / cache entries / live MCP calls it consumed — to `_memory/working-sets.jsonl`. Used by the Supertailor to spot:

- Skills that consistently over-read (loading 50 files when 5 would do).
- Skills that miss obvious sources (asking about a topic but not reading the relevant Sources entry).
- Patterns where ingest-then-discard occurs (fetched data never read).

**Trade-off**: minor write overhead. Major insight gain for the Supertailor.

**When to do**: when the Supertailor's strategic pass starts producing weak suggestions because it can't see what the agent did.

---

## 24. First-class "decisions" log

**Tag**: Schema.

Today, decisions are scattered — some in `history.md` H4 entries, some implied by `status` flips, some in `interaction-log.yaml.summary` strings.

**Idea**: `_memory/decisions.yaml` — append-only log of every meaningful decision the user (or the agent) made:

```yaml
- id: "dec-2026-04-28-001"
  ts: <iso>
  decision: "Move to a high-yield savings account at <bank>."
  context: "Existing checking pays 0%; HYSA pays 4.2%."
  alternatives_considered: ["leave as-is", "switch to a money-market fund"]
  rationale: "HYSA is FDIC-insured and instant-access; aligns with emergency-fund liquidity rule."
  related_domain: "finance"
  related_project: null
  source: "user"
  outcome_measured_at: null
  outcome: null
```

`outcome_measured_at` + `outcome` filled in later when the agent (or user) checks "did that decision pan out?". Powerful for retrospectives.

**Trade-off**: another schema. Very lightweight. Could surface in `weekly-review` ("decisions made this week").

**When to do**: when the user makes 3+ noteworthy decisions per month and wants to remember them.

---

## 25. Notification policy as configuration, not skill code

**Tag**: Operational.

Today, when a daily-update / weekly-review surfaces something is hard-coded in the skill markdown. Changing "alert me when a bill is due in 3 days" instead of 7 means editing the skill.

**Idea**: `_memory/notification-policy.yaml` — one declaration per surfacing rule:

```yaml
rules:
  - id: "bill-due-soon"
    when: "bill.next_due - now <= 7 days"
    where: ["daily-update", "whatsup"]
    severity: "info"
  - id: "bill-overdue"
    when: "bill.next_due < now"
    where: ["daily-update"]
    severity: "alert"
    auto_create_task: { priority: "P0", title: "Pay overdue bill: {bill.name}" }
```

The cadence skills consume this, generate the surfacing block. User edits the policy without editing skill code.

**Trade-off**: tiny rule engine. Mitigates the "every preference change is a skill edit" friction.

**When to do**: when the Supertailor's strategic pass surfaces 5+ preference-change requests for surfacing thresholds.

---

## Selection guide — which ideas to pursue first

If you can do **3** of these in the next quarter, do these:

1. **#5 Inbox → Triage → File pipeline** — converts the gmail-ingestor's per-attachment dumps into actual filed Sources, closes the loop on email ingestion.
2. **#6 Differential snapshots** — small tool, big insight ("what changed this week").
3. **#4 Provenance + facts-with-sources** — establishes the discipline before the schema fills with un-sourced facts.

If you can do **3 more** in the quarter after, do these:

4. **#3 World model entity graph** — unlocks "show me everything related to X" as a real query.
5. **#21 Skill bundles / playbooks** — captures recurring workflows that today are re-typed each time.
6. **#16 Unified events stream** — sets up the foundation for #6, #17, #22, #24.

The remaining ideas are valuable but situational — pursue when the specific pain emerges.

The **Supertailor's strategic pass** is the right mechanism for prioritizing these. It watches usage; it surfaces friction; it proposes the matching idea. You confirm; the Supercoder ships. That loop will pull the right ideas forward in the right order, without requiring you to predict the future from this document.
