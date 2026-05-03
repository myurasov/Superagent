# Superagent — Domain guide

What each of the 10 default Domains covers, what to put in each file, and which skills naturally write to each.

The Domain folders are seeded by `init`. You can delete any default you don't need (the system tolerates a missing default folder gracefully) and add any number of your own via `add-domain`.

---

## Table of Contents

- [Health](#health)
- [Finance](#finance)
- [Home](#home)
- [Vehicles](#vehicles)
- [Pets](#pets)
- [Family](#family)
- [Travel](#travel)
- [Career](#career)
- [Hobbies](#hobbies)
- [Self](#self)
- [Custom domains you might add](#custom-domains-you-might-add)

---

## Health

**Scope**: medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history, conditions, allergies, surgeries.

| File | Owns |
|---|---|
| `info.md` | Per-member profile (you + spouse + kids), insurance carrier + plan + member id last-4, primary care + dentist + specialists, current conditions, allergies, current medications. |
| `status.md` | Health goals (annual physical scheduled? prescription refills caught up? overdue cleanings?), open follow-ups from recent appointments. |
| `history.md` | Every doctor / dentist / vet visit; every notable health event; lab results summaries (the actual PDFs go in `Sources/documents/medical/<member>/` via `add-source --to-domain health`); medication changes. |
| `rolodex.md` | Doctors, dentists, optometrists, mental-health professionals, specialists, pharmacy, urgent care. |

**Skills that write here**: `add-contact` (provider) → `rolodex.md`; `add-appointment` (medical) → `history.md` after completion; `health-log` → `_memory/health-records.yaml` plus `history.md`; `appointments mark-complete` → `history.md` + `_memory/health-records.yaml.visits[]`.

**Sensitive**. The structured data (`_memory/health-records.yaml`) is one of the most sensitive files in the workspace. See `architecture.md` § "Sensitive subfiles" for encryption guidance.

---

## Finance

**Scope**: bills, accounts (banks / brokerage / retirement / loans / mortgages), taxes, budget, insurance (health / life / umbrella / homeowner / renter), credit, charitable giving.

| File | Owns |
|---|---|
| `info.md` | Net-worth snapshot, budget summary, insurance carriers + policy numbers last-4, financial advisor + accountant + lawyer, savings goals, retirement contributions targets. |
| `status.md` | This month's bill payment progress, upcoming bills, account low-balance alerts, tax deadlines this quarter. |
| `history.md` | Major financial events (open / close account, new policy, refinance, large purchase, charitable giving log, tax filing). |
| `rolodex.md` | Bank rep (if you have one), financial advisor, accountant, tax preparer, insurance agents (one per policy carrier), estate lawyer. |

**Skills that write here**: `add-account` → `_memory/accounts-index.yaml` + `history.md`; `add-bill` / `add-subscription` → respective YAML + `info.md` § Routines; `bills mark-paid` → `bills.yaml.history[]` + occasionally `history.md`; `expenses` → ad-hoc analysis (no writes); finance ingestors → `_memory/transactions.yaml` + cross-checks into `bills.yaml` + `subscriptions.yaml`.

---

## Home

**Scope**: mortgage / rent, utilities, HOA, homeowner / renter insurance, alarm system, HVAC, plumbing, electrical, appliances, contractors, deliveries, lawn / landscaping, pool / spa.

| File | Owns |
|---|---|
| `info.md` | Address, mortgage / rent details (last-4), utility account numbers (last-4), HOA + dues, alarm code reference (vault), HVAC make / model / install date, water-heater specs, key contractors. |
| `status.md` | Maintenance items due (filter changes, HVAC service, gutter clean, pest control, smoke-detector batteries), open contractor work, pending deliveries. |
| `history.md` | Repairs done, contractors used, deliveries received, alarm events (if Home Assistant is wired in), notable maintenance. |
| `rolodex.md` | Plumber, electrician, HVAC tech, handyman, gardener, pest control, alarm-monitoring company, neighbours (the "let me know if anything looks weird" people). |

**Skills that write here**: `add-asset` (HVAC / appliance / etc.) → `_memory/assets-index.yaml`; `home-maintenance log` → `history.md` + asset's `maintenance.last_done`; Home Assistant ingestor → `history.md` for anomalies.

---

## Vehicles

**Scope**: every vehicle owned (cars, motorcycles, bicycles, RVs, boats); registration, inspection, insurance, maintenance, fuel, mileage.

| File | Owns |
|---|---|
| `info.md` | Per-vehicle: VIN, make / model / year, color, registration / inspection expirations, insurance carrier + policy ref, current mileage, fuel preferences, key tags / spare-key locations. |
| `status.md` | Maintenance due (oil, tires, brakes, registration, inspection), insurance renewal, recall alerts. |
| `history.md` | Every service event, fuel-up (optional), repair, accident, registration / inspection / insurance renewal, mileage milestones. |
| `rolodex.md` | Mechanic(s), dealership service department, insurance agent, body shop, towing service, dashcam manufacturer support. |

**Skills that write here**: `add-asset` (vehicle) → `_memory/assets-index.yaml` + maintenance schedule rows; `vehicle-log` → `history.md` + asset updates; Tesla ingestor → mileage / charging / alerts.

---

## Pets

**Scope**: each pet's vet, vaccinations, prescriptions, food, grooming, boarding, microchip, pet insurance.

| File | Owns |
|---|---|
| `info.md` | Per-pet sub-folder OR per-pet section: name, species / breed, DOB / age, weight, microchip, conditions, allergies, food (brand + amount + cadence), current meds, vaccination status. |
| `status.md` | Next vet visit, vaccinations due, prescriptions due to refill, grooming due, boarding planned. |
| `history.md` | Vet visits, vaccinations administered, illness episodes, food brand changes, weight log entries, boarding stays. |
| `rolodex.md` | Vet (per pet if different), groomer, boarding kennel, pet-sitter, emergency vet, microchip-registry company. |

**Skills that write here**: `add-contact` (vet / groomer) → `rolodex.md`; `pet-care log` → `history.md` + per-pet `info.md` § Key Facts.

---

## Family

**Scope**: spouse, kids, parents, siblings, extended family. School calendars, kids' doctors / dentists / activities, eldercare, family events.

| File | Owns |
|---|---|
| `info.md` | Per-member: DOB, school / grade (if kids), key extracurriculars, allergies + special needs (cross-references Health), passport expirations (cross-references Documents). |
| `status.md` | Upcoming family events, parent-teacher conferences, kids' doctor visits, eldercare check-ins. |
| `history.md` | Family events log (birthdays past + how it went, school plays, vacations, parent-care visits). |
| `rolodex.md` | School(s), pediatrician, kids' dentist, kids' activities (sports coach, music teacher, tutor), parents' physicians, in-laws, extended family. |

**Skills that write here**: `add-contact` (family / school) → `rolodex.md`; `add-appointment` (school / kids' medical) → `appointments.yaml` + `history.md`; `add-important-date` (kids' birthdays / anniversaries) → `important-dates.yaml`.

---

## Travel

**Scope**: trips planned + past, flights, hotels, rental cars, packing lists, frequent-flier numbers, passports + visa expirations, foreign-currency cash, pet-sitter coordination.

| File | Owns |
|---|---|
| `info.md` | Frequent-flier / hotel-loyalty numbers (last-4), TSA Pre / Global Entry expirations, passport expirations (cross-references Documents), preferred airlines / hotels, packing-list templates. |
| `status.md` | Upcoming trips, in-progress bookings, things to do before next trip. |
| `history.md` | Per-trip: dates, locations, who went, flights, hotels, highlights, lessons learned. |
| `rolodex.md` | Travel agent (if you have one), preferred hotel reps, pet-sitter, house-sitter, neighbour for "watch the house". |

**Per-trip sub-folders** are encouraged: `Travel/<YYYY-trip-slug>/` with its own `info.md` / `status.md` / `history.md` / `sources.md` / `Resources/`. Vault-grade itineraries / boarding passes / passport scans go in `Sources/documents/travel/<trip-slug>/` and are pointed at from the trip's `sources.md`. Working photos and trip-prep drafts go in `Resources/`.

**Skills that write here**: `add-document` (passport / visa) → `documents-index.yaml` + `info.md`; `add-important-date` (document expirations) → `important-dates.yaml`; email ingestor (flight confirmations) → `appointments.yaml` (kind: travel) + `history.md`.

---

## Career

**Scope**: resume, certifications, performance reviews, learning goals, networking, salary history, equity / RSUs / 401(k) vesting (cross-references Finance for the dollar tracking).

| File | Owns |
|---|---|
| `info.md` | Current role, manager, performance-review cadence, certification list with expirations, learning goals, salary history (last-N years), equity vesting schedule reference. |
| `status.md` | Career goals progress, certifications due to renew, networking touchpoints due. |
| `history.md` | Job changes, promotions, performance-review summaries, certifications earned, conferences attended, courses completed. |
| `rolodex.md` | Manager (current + recent past), key colleagues, mentor(s), recruiters worth keeping warm, interview-loop contacts, certification authorities. |

**Skills that write here**: `add-document` (cert / diploma) → `documents-index.yaml`; `add-contact` (mentor / recruiter) → `contacts.yaml` + rolodex; `personal-signals` capture (career-development cues) → eventually rolls up here.

---

## Hobbies

**Scope**: each meaningful hobby — fitness goals, reading log, side projects, garden, workshop, music, photography, etc. One sub-folder per hobby is encouraged.

| File | Owns |
|---|---|
| `info.md` | What hobbies are active, current goals, key gear (cross-references Assets), preferred suppliers / dealers / subreddits. |
| `status.md` | Goal progress per hobby, in-flight projects, upcoming events (race, recital, exhibit, club meeting). |
| `history.md` | Per-hobby milestones — race results, books finished, projects completed, exhibitions, workshop builds. |
| `rolodex.md` | Coach / instructor / trainer per hobby, club members worth staying in touch with, suppliers, fellow hobbyists who matter to you. |

**Per-hobby sub-folders** for big ones: `Hobbies/Cycling/`, `Hobbies/Reading/`, `Hobbies/Workshop/`. Each can have its own 4-file structure.

**Skills that write here**: `add-asset` (gear) → `_memory/assets-index.yaml` with `domain: hobbies`; Strava / Whoop / Garmin / Oura ingestors → `history.md` for workouts above a threshold; `personal-signals` capture (hobby-specific commitments) → roll-up.

---

## Self

**Scope**: personal-development goals, journaling, books / podcasts / media log, life themes, the consolidated home for `personal-signals.yaml` rollups.

| File | Owns |
|---|---|
| `info.md` | Current life themes (1-3 short phrases — what you're trying to embody this year), reading goals, learning goals, mindfulness practices in flight. |
| `status.md` | Goal progress, current habits being practiced. |
| `history.md` | Weekly / monthly review summaries (auto-appended by `weekly-review` and `monthly-review`), personal-signals rollups (auto-appended by `personal-signals` skill in surface mode), notable life events. |
| `rolodex.md` | Therapist, coach, mentor, accountability partner — the people who help you become more yourself. (Tiny by design — most contacts live in domain-specific rolodexes.) |

**Skills that write here**: `personal-signals` capture (silently) → `_memory/personal-signals.yaml`; `personal-signals` surface → H4 entry in `history.md`; `weekly-review`, `monthly-review` → H4 entries in `history.md`.

This domain is **not optional** — it's the home for self-reflection captures. If you delete it, the personal-signals skill creates it on first use.

---

## Custom domains you might add

Examples worth their own domain (each runs the standard 4-file structure):

- **Boat** — registration, marina, mooring, maintenance.
- **Cabin** / **Vacation home** — separate from primary residence; same structure.
- **Side-business** — clients, invoices, expenses (cross-references Finance).
- **Volunteer** / **Board work** — meetings, commitments, contacts.
- **Music** — gear (Assets), gigs (history), bandmates / studio engineers (rolodex).
- **Garden** — plot maps, planting calendar, suppliers, harvest log.
- **Wine cellar** — inventory (Assets), tasting notes (history), wine-shop contacts.
- **Cars (multi-vehicle enthusiast)** — could split out from Vehicles into its own domain if you have many.

Just say "add a domain for X" and the agent walks the user through `add-domain`.
