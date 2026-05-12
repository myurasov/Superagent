# Domain & Asset Management

<!-- Migrated from `procedures.md § 6`. Citation form: `contracts/domains-and-assets.md`. -->

### 6.1 The 13 default domains

Init seeds these 13 by default. The user can delete the ones they don't need, add more via `add-domain`, or rename in-place. New domains can also be **auto-suggested** by the detector per § 6.4b.

| Domain | Scope (illustrative) |
|---|---|
| **Health** | medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history |
| **Finances** | operational financial life — bills, banking accounts (the operational tubes), credit cards, loans, mortgages, insurance policies, payroll, taxes, budget, cash flow. Holdings themselves live in Assets |
| **Home** | mortgage (the loan / payment side; the equity / structure-as-property is also Home), utilities, insurance, HOA, maintenance schedule, contractors, security, deliveries |
| **Vehicles** | every vehicle owned (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel |
| **Assets** | things of value — physical (electronics, appliances, jewelry, instruments, tools, art, collectibles), financial (stock holdings, ETFs, bonds, crypto, significant cash positions, precious metals, treasuries), and non-residential real estate. Excludes vehicles + the home structure |
| **Pets** | each pet's vet, vaccinations, prescriptions, food, grooming, boarding |
| **Family** | spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events (kids' schooling included here, NOT in Education) |
| **Travel** | trips planned and past, flight / hotel / rental records, packing lists, frequent-flier numbers, passports |
| **Career** | resume, certifications, performance reviews, learning goals, networking, salary history (W-2 employment side) |
| **Business** | side income, freelancing, consulting, sole-proprietor / LLC operations — clients, contracts, invoices, business expenses, business taxes (separate from W-2 Career) |
| **Education** | active enrollment in a degree / certificate program (yourself — kids' schooling lives in Family). Courses, credits, credentials being pursued, advisors, registrar, transcripts, FAFSA, employer tuition assistance |
| **Hobbies** | each meaningful hobby — fitness goal, reading log, side project, garden, workshop, etc. |
| **Self** | personal-development goals, journaling, books / podcasts / media log, life themes |

The split between `Vehicles` / `Assets` / `Home` is by **kind of physical thing**: Vehicles owns titled motor vehicles (cars, motorcycles, RVs, boats), Home owns the primary residence structure + fixtures + utilities, Assets owns everything movable / non-residential that doesn't fit either. The `assets-index.yaml.<asset>.domain` field selects which of the three a given asset row is filed under. Investment / rental / vacant-land real estate sits in Assets; the primary residence sits in Home.

The split between `Finances` / `Assets` is by **operational vs holding**: Finances owns the operational machinery (accounts as routing/access tubes, bills, credit, payroll, taxes, cash flow, insurance policies). Assets owns the **holdings themselves** — what you OWN, not the rails it moves on. A brokerage account row in `accounts-index.yaml` lives in Finances; the AAPL position inside it lives in `assets-index.yaml` with `held_in_account: "account:schwab-brokerage"`. A high-yield-savings account lives in Finances; a $200k cash position parked there above the threshold (`config.preferences.assets.cash_position_threshold`, default $50k) lives in Assets as a `cash_position` row. Mortgages and other debts stay in Finances (operational liabilities). For day-to-day "did this bill clear?" the user lives in Finances; for "what's my net worth and what do I own?" the user lives in Assets.

The split between `Career` / `Business` is by **income source**: Career owns W-2 employment, employer-paid benefits, professional development tied to that job; Business owns side-income / sole-proprietor / LLC operations, client relationships, business expenses, business taxes. A user with no side income can leave `Business` empty (or delete it); a freelance-only user can leave `Career` empty.

The split between `Career` / `Education` is by **what you're pursuing**: Career covers the role you currently hold (or want next) and the certifications / development tied to it. Education covers active enrollment in a structured program (degree, multi-semester certificate, bootcamp) — registrar, syllabi, advisors, transcripts, financial aid. A one-off cert renewal stays in Career; a multi-year MBA program goes in Education with `related_domain: career` cross-link.

### 6.2 4-file convention

Every domain folder has the same 4 files (the folder itself is lazy — see § 6.4a):

```
Domains/<domain>/
  info.md       # narrative overview, current state, key facts
  status.md     # RAG status + open / done tasks
  history.md    # chronological log of touchpoints (newest at top)
  rolodex.md    # contact directory scoped to this domain
  sources.md    # curated catalogue of Sources/ entries relevant to this domain
  Resources/    # optional, lazily created — drafts, working files, agent-generated artifacts
```

### 6.3 Maintenance banner

Every framework-managed markdown file under `workspace/Domains/` opens with the maintenance banner:

```markdown
> **[Do not change manually — managed by Superagent]**
```

(Plus an HTML comment block immediately below explaining the file's role and the schema contract.) This signals to a human reader that the file is auto-mutated by skills and that hand-edits may be overwritten — though hand-edits are still respected when the next sync runs (skills MUST diff and merge, not blindly clobber).

### 6.4 Adding a new domain

The full skill is in `skills/add-domain.md`. Summary:

1. Ask the user: domain name, slug (auto-derived), one-line scope, priority (P0–P3).
2. Append a row to `_memory/domains-index.yaml` — this is the **registration**.
3. **Do NOT** pre-create `Domains/<Name>/`. The folder is lazy per § 6.4a — it
   appears the first time a real piece of data lands for the domain (a contact,
   a contract, a logged event, a status update, …), via `ensure_folder` (see
   § 6.4a). Tell the user "I've registered <Name>; the folder will appear when
   the first row of data lands."
4. Append a `domain_added` entry to `interaction-log.yaml`.

### 6.4a Lazy folder materialization (no empty domains)

Default-domain (and custom-domain) folders under `Domains/<Name>/` are
**created on first write**, never speculatively at init time. The contract:

- `init` (and `add-domain`) **registers** the domain in `_memory/domains-index.yaml`
  but does NOT create `Domains/<Name>/`. The user opens `Domains/` and sees
  only what the workspace has actually accumulated content for — no clutter.
- The first time any skill is about to write to `Domains/<Name>/<file>` (any
  of `info.md`, `status.md`, `history.md`, `rolodex.md`, `sources.md`) — or
  about to drop a working file under `Domains/<Name>/Resources/` — it MUST
  first call:

      uv run python -m superagent.tools.domains ensure <id>

  …or, when the skill is itself a Python tool, import the helper directly:

      from superagent.tools.domains import ensure_folder
      ensure_folder(workspace, framework, "<id>")

- `ensure_folder` is **idempotent**: when the folder already exists it is a
  near-no-op. When missing, it materializes the 5-file scaffold from
  `superagent/templates/domains/` with the registered domain name substituted.
- After `ensure_folder` returns, the skill proceeds with its normal write.
  The newly-materialized folder is now permanent (until explicitly removed by
  a future `purge-empty` sweep, which only deletes folders with NO user
  content beyond the bare template).
- **`purge-empty`** (CLI: `uv run python -m superagent.tools.domains purge-empty`)
  is the housekeeping pass that removes default-domain folders that match the
  bare-template state (no real entries beyond the framework's seeded
  scaffolding). Custom-named domains created by the user are NEVER touched by
  the purge — only folders whose name matches one of the defaults seeded by
  init.

Skills MUST cite this section in their frontmatter / first step when they are
in the set that writes to `Domains/<X>/<file>`. Implicated skills: `health-log`,
`pet-care`, `vehicle-log`, `home-maintenance`, `log-event`, `add-contact` (when
appending to the rolodex), `add-source` (when appending to sources.md),
`add-asset` (when writing to Resources/), `add-bill` / `add-subscription` /
`add-account` (when touching the domain's `info.md` Routines section), and
every ingestor that writes domain history (per `contracts/ingestion.md`).

### 6.4b Detection-driven domain suggestions

In addition to the user explicitly invoking `add-domain`, Superagent
proactively surfaces a candidate new domain when accumulated workspace
signals strongly suggest one fits the user's situation. The contract is
ask-once-per-cluster — pestering is the failure mode this section exists to
prevent.

#### Signals the detector watches

`superagent/tools/domain_detector.py` walks these signals on demand (CLI:
`uv run python -m superagent.tools.domain_detector run`) and on schedule
(monthly-review § 7d). It surfaces clusters that:

- **Off-domain tags** — a tag in `_memory/tags.yaml` used across ≥ N entities
  (default N=5) that doesn't map to any registered domain id, name, or its
  built-in synonym list. Example: tag `sailing` on 8 entities (3 contacts,
  2 sources, 3 projects) → suggest `Sailing` domain.
- **Off-domain contact-role clusters** — ≥ N contacts (default 3) sharing a
  `role` value that doesn't fit any registered domain. Example: 4 contacts
  with `role: "board_member"` → suggest `Volunteer` or `Community`.
- **Off-domain project clusters** — ≥ 2 projects in `projects-index.yaml`
  with empty `related_domains[]` AND a common keyword in their `name` /
  `goal`. Example: "kitchen-renovation" + "bathroom-remodel" → suggest
  `Renovations`.
- **Off-domain source-folder clusters** — top-level `Sources/<folder>/`
  paths with ≥ N entries (default 5) that don't map to any registered
  domain. Example: many files under `Sources/Sailing/` → suggest `Sailing`.

The detector folds each cluster's signals into one canonical `theme` slug
(e.g. `sailing`, `garden`, `crypto`) and computes a confidence score
(entity count + recency bonus). It returns the top N (default 3)
candidates per run.

#### Filtering — don't suggest what's already handled

Before scoring, the detector filters out:

1. Themes that map to a registered domain (id, name, or synonym).
2. Themes already in `_memory/domain-suggestions.yaml.accepted[]`.
3. Themes in `domain-suggestions.yaml.declined[]` with `revisit_after: null`
   (the default — "never").
4. Themes in `domain-suggestions.yaml.deferred[]` whose `revisit_after` is
   in the future.

This is the "ask once" enforcement. A user who said "never" is never asked
again. A user who said "not now" is asked again after the deferral window
(default 90 days).

#### How the agent surfaces a candidate

Two surfacing paths:

1. **Periodic** (highest confidence, low noise) — `monthly-review` calls
   `domain-suggest --run-detector` after Domain hygiene (§ 7d in
   `monthly-review.md`). The skill renders a short "I noticed …" block per
   candidate (max 3) and asks the user via `AskQuestion` with three
   options: **yes** (route to `add-domain`), **not now** (defer 90d),
   **never** (decline forever).

2. **Ambient** (mid-conversation, opportunistic) — when the agent observes
   a strong cluster *during a normal turn* (e.g. the user mentions the
   same off-domain theme three times in this session, OR is creating
   multiple entities the agent can't naturally route), it MAY surface
   ONCE per session at the START of the next turn:

   > Quick observation — I notice <evidence summary>. Want me to add a
   > `<Name>` domain to track that separately? (yes / not now / never)

   Constraints on ambient surfacing:
   - Only ONE ambient surfacing per session. If the agent has already
     asked about a different cluster in this session, defer the next one
     to monthly-review.
   - Don't interrupt high-friction flows (active triage, payment
     processing, emergency captures). Hold the question for the next turn.
   - Skip when `_memory/config.yaml.preferences.domain_detection.enabled`
     is `false` (default `true`).

#### Per-answer side-effects

| Answer | What the agent does |
|---|---|
| **yes** | Invoke `add-domain` skill with `proposed_name` / `proposed_scope` / `proposed_priority` pre-filled (user can edit). On the new domain row landing in `domains-index.yaml`, move the suggestion row to `accepted[]` with `domain_id` set. Append to `interaction-log.yaml`. |
| **not now** | Append to `deferred[]` with `revisit_after = today + 90d` (or per `config.preferences.domain_detection.defer_days`). Detector skips this theme until that date. |
| **never** | Append to `declined[]` with `revisit_after: null`. Detector skips this theme forever (until the user explicitly clears the row via `domain-suggest forget <theme>`). |

Every answer is also written to `surfaced[]` for audit ("when did the agent
last ask about <theme>?").

#### User overrides

- **Disable detection entirely**:
  `_memory/config.yaml.preferences.domain_detection.enabled: false`.
- **Tighten thresholds** (suppress noisy suggestions):
  `_memory/config.yaml.preferences.domain_detection.min_score: <int>`.
- **Forget a previously declined / deferred theme** (re-allow it to surface):
  `uv run python -m superagent.tools.domain_detector forget <theme>`
  (see `skills/domain-suggest.md` for the user-facing flow).
- **Force a fresh detection run** any time:
  `uv run python -m superagent.tools.domain_detector run`.

### 6.5 Adding an asset

Assets are physical things you own (vehicles, electronics, appliances, jewelry, instruments, tools, collectibles). The full skill is in `skills/add-asset.md`. Summary:

1. Ask: name, kind (vehicle / appliance / electronics / jewelry / tool / instrument / collectible / other), domain (which Domain owns it — Vehicles, Home, Hobbies, …), purchase date, purchase price, serial / VIN if applicable, warranty expiration if applicable, notes.
2. Append a row to `_memory/assets-index.yaml`.
3. **Source documents** (titles, registrations, warranties, vault-grade receipts) → invoke `add-source --to-domain <domain> --asset <asset-slug>` for each. Files land in `Sources/documents/<category>/<asset-slug>/`; pointers added to `Domains/<Domain>/sources.md`. NEVER under `Domains/<X>/` directly.
4. **Working files** (context photos, scratch worksheets, agent-rendered briefings) → `Domains/<Domain>/Resources/<asset-slug>/` (lazy; created on first write).
4. If the asset has a recurring maintenance schedule (vehicle oil change, HVAC filter change, septic pump-out), the skill prompts to add maintenance rows to `bills.yaml` (one-shot or recurring) AND to the domain's `status.md` `Next Steps` section.

### 6.6 Per-entity Resources/ (working artifacts)

`Resources/` is created lazily, inside the relevant `Domains/<domain>/` (or `Projects/<slug>/`) folder, the first time a working file is written. There is no workspace-wide `Resources/`.

`Resources/` holds **drafts, working files, photos-as-references, and agent-generated artifacts** that are NOT meant to leave the workspace (`Outbox/` is for those) and NOT vault-grade canonical records (`Sources/documents/<category>/` is for those).

Per-asset / per-event sub-folders inside a domain's `Resources/` are encouraged (e.g. `Domains/Vehicles/Resources/blue-camry-2018/` for fuel-log CSVs and label photos; `Domains/Home/Resources/hvac/` for leak photos and scratch quote-comparisons).

The 5-year test for placing a file: *"would I want this in five years even if its domain went away?"* If yes → `Sources/`. If no → `Resources/`.

### 6.7 The 5-file Domain structure (info / status / history / rolodex / sources)

Every Domain folder has **5** managed markdown files (the previous 4 + `sources.md`):

- `info.md` — narrative overview, current state, key facts, routines, stakeholders, open questions.
- `status.md` — RAG + open / done tasks (auto-synced from `_memory/todo.yaml`).
- `history.md` — chronological log; H4 entries; newest at top.
- `rolodex.md` — domain-scoped contact directory (auto-synced from `_memory/contacts.yaml`).
- `sources.md` — curated catalogue of `Sources/` entries relevant to this domain. Auto-maintained by `add-source --to-domain <id>`; respects hand-edits.

The same 5-file structure applies to Projects (`Projects/<slug>/`).

### 6.7 Archival rules

- A **domain** with no `history.md` entry in 12 months and no open tasks → surfaced by `doctor` for archive into `workspace/Archive/<YYYY-MM>/Domains/`.
- An **asset** that the user marks `status: disposed` (sold, donated, lost, replaced) → moved to `_memory/assets-index.yaml.disposed[]` (kept for tax / insurance history) and the corresponding `Domains/<X>/Resources/<asset-slug>/` working folder moved to `Archive/`. The asset's source documents in `Sources/documents/<category>/<asset-slug>/` stay where they are (immutable per § 15.2); the user moves them manually if desired.
- A **completed appointment / paid bill / past important date** → retained in indexes for 12 months (so the year-over-year skills work), then rotated by `doctor` into year-stamped archive YAMLs (`bills-2024.yaml`, etc.).

Archival is **always reversible** — moving back from `Archive/` is a single `mv` command.
