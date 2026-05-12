# Domain & Asset Management

<!-- Migrated from `procedures.md § 6`. Citation form: `contracts/domains-and-assets.md`. -->

### 6.1 The 12 default domains

Init seeds these 12 by default. The user can delete the ones they don't need, add more via `add-domain`, or rename in-place.

| Domain | Scope (illustrative) |
|---|---|
| **Health** | medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history |
| **Finances** | bills, accounts (banks, brokerage, retirement), taxes, budget, insurance (health / life / umbrella), credit |
| **Home** | mortgage / rent, utilities, insurance, HOA, maintenance schedule, contractors, security, deliveries |
| **Vehicles** | every vehicle owned (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel |
| **Assets** | movable possessions worth tracking for insurance / warranty / recall — electronics, appliances, jewelry, instruments, tools, art, collectibles, sports gear (excludes vehicles + the home structure) |
| **Pets** | each pet's vet, vaccinations, prescriptions, food, grooming, boarding |
| **Family** | spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events |
| **Travel** | trips planned and past, flight / hotel / rental records, packing lists, frequent-flier numbers, passports |
| **Career** | resume, certifications, performance reviews, learning goals, networking, salary history (W-2 employment side) |
| **Business** | side income, freelancing, consulting, sole-proprietor / LLC operations — clients, contracts, invoices, business expenses, business taxes (separate from W-2 Career) |
| **Hobbies** | each meaningful hobby — fitness goal, reading log, side project, garden, workshop, etc. |
| **Self** | personal-development goals, journaling, books / podcasts / media log, life themes |

The split between `Vehicles` / `Assets` / `Home` is by **kind of physical thing**: Vehicles owns titled motor vehicles (cars, motorcycles, RVs, boats), Home owns the structure + fixtures + utilities, Assets owns everything movable that doesn't fit either. The `assets-index.yaml.<asset>.domain` field selects which of the three a given asset is filed under.

The split between `Career` / `Business` is by **income source**: Career owns W-2 employment, employer-paid benefits, professional development tied to that job; Business owns side-income / sole-proprietor / LLC operations, client relationships, business expenses, business taxes. A user with no side income can leave `Business` empty (or delete it); a freelance-only user can leave `Career` empty.

### 6.2 4-file convention

Every domain folder has the same 4 files:

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
2. Append a row to `_memory/domains-index.yaml`.
3. Create `Domains/<slug>/` with the four template files filled in.
4. Append `domain_added` entry to `interaction-log.yaml`.

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
