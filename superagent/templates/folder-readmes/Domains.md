# `Domains/` — your life, sliced into stable buckets

Every folder under `Domains/` is one slice of your life. The 13 default domains **registered** by `init` (the folder appears the first time real data lands for that domain — see "lazy materialization" below):

| Domain | What it covers |
|---|---|
| **Health** | Medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history. |
| **Finances** | Operational financial life — bills, banking accounts (the operational tubes), credit cards, loans, mortgages, insurance policies, payroll, taxes, budget, cash flow. *Holdings* themselves live in Assets. |
| **Home** | Primary residence — mortgage / rent, utilities, HOA, maintenance schedule, contractors, security, deliveries. |
| **Vehicles** | Every vehicle you own (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel. |
| **Assets** | Things of value — physical (electronics, jewelry, instruments, tools, art, collectibles), financial (stock holdings, ETFs, bonds, crypto, significant cash positions, precious metals, treasuries), and non-residential real estate. The asset is the holding; the operational account it lives in stays in Finances. |
| **Pets** | Each pet's vet, vaccinations, prescriptions, food, grooming, boarding. |
| **Family** | Spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events (kids' schooling lives here, NOT in Education). |
| **Travel** | Trips planned and past, flights, hotels, rentals, packing lists, frequent-flier numbers, passports. |
| **Career** | Resume, certifications, performance reviews, learning goals, networking, salary history (W-2 employment side). |
| **Business** | Side income, freelancing, consulting, sole-proprietor / LLC operations — clients, contracts, invoices, business expenses, business taxes (separate from W-2 Career). |
| **Education** | Active enrollment in a degree / certificate program (yourself — kids' schooling lives in Family). Courses, credits, advisors, registrar, transcripts, FAFSA, employer tuition assistance. |
| **Hobbies** | Each meaningful hobby — fitness goal, reading log, side project, garden, workshop, etc. |
| **Self** | Personal-development goals, journaling, books / podcasts / media log, life themes. |

Add your own (e.g. `Boat/`, `Cabin/`, `Volunteer/`, `Estate/`) via `add-domain`. Or wait — Superagent will auto-suggest a new domain (per `contracts/domains-and-assets.md` § 6.4b) when accumulated tags / contacts / projects strongly suggest one fits your situation; you'll be asked once with three options (yes / not now / never).

## Lazy materialization

`Domains/<Name>/` folders are **created the first time real data lands for that domain** — never speculatively at init time. The principle is "no empty folders": if you've never logged anything Pet-related, `Domains/Pets/` simply doesn't exist on disk.

Triggers that materialize a folder:

- A capture skill writes the first row referencing the domain (`add-bill`, `add-contact`, `add-asset`, `add-source`, `add-document`, `add-appointment`, `add-important-date`).
- A logging skill writes the first event (`log-event`, `health-log`, `vehicle-log`, `home-maintenance`, `pet-care`).
- An ingestor writes its first row attributable to the domain.
- The `add-domain` flow's optional "capture an initial fact" step.

The 12 defaults stay **registered** in `_memory/domains-index.yaml` regardless — capture skills know where to route when you start. The folder itself is just absent until earned.

To prune folders that ended up empty (e.g. after a default-domain experiment that never accumulated real data):

```
uv run python -m superagent.tools.domains purge-empty [--dry-run]
```

User-edited folders are always kept; only bare-template defaults get deleted.

## File structure

Every domain folder has the same 4 files:

```
<Domain>/
  info.md       — overview, current state, key facts, routines, stakeholders
  status.md     — RAG status + open / done tasks
  history.md    — chronological log of touchpoints (newest at top)
  rolodex.md    — contact directory scoped to this domain
  sources.md    — curated catalogue of Sources/ entries relevant to this domain
  Resources/    — optional, lazily created — drafts, working files, agent-generated artifacts
```

The skills know this shape; you don't have to remember it.

## Hand-edits are welcome

The maintenance banner at the top of each managed file (`> [Do not change manually — managed by Superagent]`) is a courtesy warning that skills will mutate the file. Skills do NOT clobber hand-edits — they diff and merge. If you fix a typo, restructure a section, or add a paragraph, the next sync respects your version.
