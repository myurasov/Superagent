# `Inbox/` — staging area for incoming files

Drop incoming files here so they have a single, predictable landing pad before being filed to their proper home. Examples:

- Email attachments saved manually (insurance cards, statements, receipts).
- Files received over AirDrop, USB, or a meeting share.
- Photos you took to remember a serial number / model number / warranty card.
- Anything you want to inspect or rename before deciding where it belongs.

`Inbox/` is **transient by design** — it is not a long-term store. Superagent expects that you (or the agent) will move each item to its proper destination soon after it lands.

## Where things go from here

Source documents NEVER end up under `Domains/<X>/` directly — they go to `Sources/documents/<category>/`, with a pointer added to the relevant domain's `sources.md`. The capture path is **`add-source --to-domain <id>` (or just "add this to <domain>" in chat)** — the agent files it correctly. The table below shows where each kind lands:

| File type | Auto-routed to | Pointer in |
|---|---|---|
| Insurance card / policy doc | `Sources/documents/insurance/` | `Domains/Finances/sources.md` |
| Vehicle registration / title / receipt | `Sources/documents/vehicles/<vehicle-slug>/` | `Domains/Vehicles/sources.md` |
| Medical record / lab result / vaccine card | `Sources/documents/medical/<member-slug>/` | `Domains/Health/sources.md` |
| Appliance manual / warranty / receipt | `Sources/documents/warranties/<appliance-slug>/` | `Domains/Home/sources.md` |
| Pet vaccination / vet record | `Sources/documents/pets/<pet-slug>/` | `Domains/Pets/sources.md` |
| Travel itinerary / boarding pass / passport scan | `Sources/documents/travel/<trip-slug>/` | `Domains/Travel/sources.md` (or active trip `Projects/<trip-slug>/sources.md`) |
| Tax return / W-2 / 1099 | `Sources/documents/taxes/<year>/` | `Domains/Finances/sources.md` AND `Projects/tax-<year>/sources.md` |
| Reference material (article you want to keep) | `Sources/documents/reference/` (or your notes app of choice) | `Domains/Self/sources.md` |
| Working draft / scratch photo / quote spreadsheet | `Domains/<X>/Resources/` (or `Projects/<X>/Resources/`) | n/a (not a Source) |
| Thing to send to someone | `Outbox/` after the agent drafts it | n/a |
| Junk / one-off | Delete |

## Hygiene

- Anything in `Inbox/` for **more than ~14 days** is flagged by the `doctor` skill as a candidate to file or discard.
- The agent may proactively offer to drain `Inbox/` during `daily-update` or `weekly-review` if files have been sitting unattended.
- This README is scaffolded by `workspace-init.py`. Edit it freely — it will not be overwritten on subsequent inits.
