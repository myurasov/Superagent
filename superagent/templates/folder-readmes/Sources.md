# `Sources/` — your reference library

The workspace's vault for the documents you own, plus the registry of external things Superagent watches for change. Three things to know:

1. **Layout is yours.** Drop files in any folder structure that makes sense to you. The agent doesn't enforce `documents/` / `<category>/` subdirs. You can use them if you like — or invent your own — or have a flat folder.
2. **Index is derived.** The structured catalogue lives at `_memory/sources-index.yaml` and is rebuilt automatically when you (or the agent) read it. Drop a file by hand from a shell, and on the next agent read it shows up in the index.
3. **Local-first.** A document is read from its path — its `.meta.md` sidecar first when one exists, then the file itself (never a large file whole). An external thing is not fetched on demand: it is **watched** — a `Watchlist/` entry records "did it move?" in `_memory/watchlist-state.yaml`, and a pack's harvest lands its records in a typed `_memory/` index.

## Reserved names

The agent reserves two names under `Sources/`:

| Name | Purpose |
|---|---|
| `README.md` | This file. |
| `Watchlist/` | The **watchlist registry** — one `<name>.ref.md` per external thing the agent watches for change (a portal, a Gmail label, a bank feed, a page). Reserved *name*, but the *contents* are yours to hand-write, edit, or delete; see its own `README.md` and `contracts/watchlist.md`. The folder path is configurable (`config.preferences.watchlist.path`). |

Everything else is yours. There is no cache folder: nothing under `Sources/` is written or evicted by the agent on its own.

## Documents, sidecars, watchers

| What you have | What to call the file | What the agent does |
|---|---|---|
| A PDF / scan / spreadsheet you own | Whatever you want — `camry-title.pdf`, `2024-tax-return.pdf`, `medical/labs.pdf` | Indexes the path; opens it directly when read. |
| Extra metadata about one of those documents | `<doc>.<ext>.meta.md` beside it — `camry-title.pdf.meta.md` | Reads it before the document; lifts its fields and cross-references into the index row. |
| Something elsewhere you want to be told about when it changes | `Watchlist/<name>.ref.md` | Checks it on a cadence; alerts on change; never fetches it on demand. |

**Every `.ref.md` is a watcher.** The suffix means one thing, so a `.ref.md` outside `Watchlist/` is a mistake — move it in, or rename it `<doc>.<ext>.meta.md` if it was meant as document metadata. `.ref.txt` is not recognized.

## Authoring a `.meta.md` sidecar by hand

Optional. YAML frontmatter + free-text notes; every field may be omitted:

```markdown
---
title: "Camry title 2018"
description: "Original title, held in the fire safe."
sensitive: true
related_asset: "car-camry-2018"
related_domain: "vehicles"
tags: [title, dmv]
---

# Notes

Lien released 2024-03; the release letter is filed next to it.
```

Sidecars are also where a saved payment confirmation carries its structured fields (`payee`, `amount`, `payment_date`, `confirmation`, ...) per `contracts/payment-confirmations.md`.

## Authoring a watcher by hand

Template: `superagent/templates/sources/ref.md`. Name the file as you like (`Solar_Permit.ref.md`, `solar_permit.ref.md` — the agent writes Title_Case when it creates one for you and keeps yours exactly as named); the watcher id is the stem lowercased (`solar_permit`, handle `watch:solar_permit`). The `watch:` block carries what to watch and how:

```markdown
---
ref_version: 2
title: "City — solar permit status"
related_project: solar
watch:
  type: url
  url: "https://permits.example.gov/status?id=12345"
  selector: "#status-panel"
  evict_after_days: 30
---
```

Or say "watch this page" and let the `watch` skill write it. Details in `Watchlist/README.md`.

## How `add-source` fits

`add-source` is a convenience for documents — it copies the file into a sensible subfolder (you confirm or override the path), optionally writes the `.meta.md` sidecar, refreshes the index, and cross-links the document to the relevant Domain / Project / Asset. You can skip it and just drop files in by hand; the auto-refresh will pick them up. Watchers go through the `watch` skill instead.

| Document | Suggested location | Auto-routed cross-refs |
|---|---|---|
| Vehicle title / registration / insurance card | `Sources/vehicles/<vehicle-slug>/` (suggested; you can override) | `Domains/Vehicles/sources.md` |
| Tax return | `Sources/taxes/<year>/` | `Domains/Finances/sources.md` (+ `Projects/tax-<year>/sources.md` if active) |
| Medical record / lab / imaging | `Sources/medical/<member>/` | `Domains/Health/sources.md` |
| Appliance manual / warranty | `Sources/warranties/<asset>/` | `Domains/Home/sources.md` |
| Will / trust / POA / advance directive | `Sources/legal/` | `Domains/Family/sources.md` AND `Domains/Finances/sources.md` |
| Insurance policy | `Sources/insurance/<policy>/` | `Domains/Finances/sources.md` |
| Mortgage / lease / deed | `Sources/property/` | `Domains/Home/sources.md` |
| School records / diplomas | `Sources/education/<member>/` | `Domains/Family/sources.md` AND/OR `Domains/Career/sources.md` |
| Pet vaccination / vet records | `Sources/pets/<pet>/` | `Domains/Pets/sources.md` |

These are suggestions, not rules. If you want all medical stuff under `Sources/health-stuff/`, the index handles it the same way.

## What `Sources/` is NOT for

- **Drafts, working files, photos-as-references, agent-generated artifacts** → `Domains/<X>/Resources/` or `Projects/<X>/Resources/`.
- **Things you're sending to someone else** → `Outbox/`.
- **Files in transit, pending classification** → `Inbox/`.
- **A URL you merely want to remember** (no change to watch for) → the domain's `info.md` / `rolodex.md`.

The crisp boundaries:

| Lives in | Lifetime | Mutability |
|---|---|---|
| `Sources/<your-paths>/` | indefinite (until you manually delete) | read-only to the agent |
| `Sources/Watchlist/` | until you delete the ref (eviction is a state mark, never a file operation) | yours; the agent only adds the ref you ask for with `enable` |
| `Domains/<X>/Resources/` or `Projects/<X>/Resources/` | as long as the entity is active; archived with it | hand-managed |
| `Outbox/` | until you mark sent OR send manually | append + mark; rarely deleted |
| `Inbox/` | transient (~14 days max) | drained regularly |

## Sensitive sources

Set `sensitive: true` in a document's `.meta.md` sidecar (or in its index row) for medical records, account statements, legal documents:

- Outbound surfaces (`draft-email`, `summarize-thread`, anything that lands in `Outbox/`) redact the content.
- Anything carrying a full account number, SSN, or medical detail beyond a receipt line routes per `contracts/sensitive-tier.md` rather than sitting here in the clear.

## Privacy

`Sources/` is gitignored along with the rest of `workspace/`. It stays local. Superagent never publishes anything on its own — that is always an explicit user action.
