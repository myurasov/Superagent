# `Resources/` — drafts, working files, and generated artifacts

Lives inside a `Domains/<domain>/` or `Projects/<project>/` folder. Created lazily — the first time a skill (or you) drops a working file for that domain / project, the folder appears.

## What `Resources/` is for

The **process layer**, not the **canonical layer**. Things that:

- You produced or captured for your own working purposes (drafts, sketches, photos-as-references).
- The agent generated for this domain / project that aren't meant to be sent anywhere (auto-rendered briefings, charts, scenario simulations, scratch summaries).
- You're actively iterating on (drafts of an email you'll send later, worksheets, half-finished checklists).
- Document state for a moment in time but aren't long-term records (a photo of an HVAC label so you remember the model number once it's transcribed into `info.md` § Key Facts; a before/after photo of a repair).

## What `Resources/` is NOT for

Use the right home instead:

| File | Where it goes |
|---|---|
| **Finished documents** (insurance card scan, signed will, tax return PDF, vehicle title, appliance manual, warranty receipt) | `Sources/documents/<category>/` — the immutable canonical store. Then add a row to this domain's `sources.md`. |
| **External-data pointers** (provider portals, MCP-fetchable records, frequently-referenced URLs) | `Sources/references/<category>/<name>.ref.md` |
| **Things to send / give to someone else** (drafts ready to copy-paste into email, printable checklists for a contractor, exports for a tax preparer) | `Outbox/` |

The crisp test:

> **Would I want this file in five years even if this Domain or Project went away?**
>
> - Yes → `Sources/documents/`
> - No → `Resources/`

## Sub-folder convention

Per-asset / per-event sub-folders are encouraged. Examples:

```
Domains/Vehicles/Resources/
  blue-camry-2018/
    label-photo.jpg              # working photo — model number transcribed into info.md
    cost-comparison-2026.md      # spreadsheet you sketched while shopping for tires
  red-bike-2022/
    bike-fit-photo.jpg           # photo to share with the bike-fit specialist later

Domains/Home/Resources/
  hvac/
    leak-photo-2026-04.jpg       # context photo for the plumber
    quote-comparison.md          # working spreadsheet of three contractor quotes
  drafts/
    contractor-email.md          # draft email you'll send tomorrow

Projects/kitchen-reno-2026/Resources/
  layout-sketch.jpg              # sketch you doodled while planning
  bid-comparison.md              # working comparison of three bids
  before-photos/                 # before-renovation state
```

The corresponding *finished* records (the actual signed contract, the final cost-tracking PDF, the receipt for the chosen contractor) go in `Sources/documents/<category>/` and are pointed at from the domain's / project's `sources.md`.

## Hand-managed

Unlike `info.md`, `status.md`, `history.md`, `rolodex.md`, and `sources.md`, this folder is NOT auto-mutated by skills. Skills may add files (e.g. an agent-rendered briefing) but otherwise treat it as your private filing cabinet.

## Cleanup

`doctor` proposes archive of `Resources/` files older than 24 months that no other entity references — surfacing only, never auto-deletion. The user decides.
