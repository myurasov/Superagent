# `Domains/` — your life, sliced into stable buckets

Every folder under `Domains/` is one slice of your life. The 10 default domains seeded by `init` are:

| Domain | What it covers |
|---|---|
| **Health** | Medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history. |
| **Finances** | Bills, accounts (banks, brokerage, retirement), taxes, budget, insurance (health / life / umbrella), credit. |
| **Home** | Mortgage / rent, utilities, HOA, maintenance schedule, contractors, security, deliveries. |
| **Vehicles** | Every vehicle you own (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel. |
| **Pets** | Each pet's vet, vaccinations, prescriptions, food, grooming, boarding. |
| **Family** | Spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events. |
| **Travel** | Trips planned and past, flights, hotels, rentals, packing lists, frequent-flier numbers, passports. |
| **Career** | Resume, certifications, performance reviews, learning goals, networking, salary history. |
| **Hobbies** | Each meaningful hobby — fitness goal, reading log, side project, garden, workshop, etc. |
| **Self** | Personal-development goals, journaling, books / podcasts / media log, life themes. |

Add your own (e.g. `Boat/`, `Cabin/`, `Side-business/`, `Volunteer/`) via `add-domain`. Delete any default you don't need; the system tolerates a missing default folder gracefully.

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
