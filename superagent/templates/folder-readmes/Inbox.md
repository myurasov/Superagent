# `Inbox/` — staging area for incoming files

Drop incoming files here so they have a single, predictable landing pad before being filed to their proper home. Examples:

- Email attachments saved manually (insurance cards, statements, receipts).
- Files received over AirDrop, USB, or a meeting share.
- Photos you took to remember a serial number / model number / warranty card.
- Anything you want to inspect or rename before deciding where it belongs.

`Inbox/` is **transient by design** — it is not a long-term store. Drop files here, then ask the agent to file them ("file what's in my inbox", "add this to Vehicles"). Nothing classifies or moves a file on its own; the agent moves each item to its proper destination when you ask.

## Where things go from here

Source documents NEVER end up under `Domains/<X>/` directly — they go under `Sources/<your-folders>/`, with a pointer added to the relevant domain's `sources.md`. The `Sources/` layout is **yours**: the agent reserves only `Sources/README.md` and `Sources/Watchlist/`; every other folder name and nesting is your choice (`contracts/sources.md` § 15.1). The filing path is **`add-source --to-domain <id>` (or just "add this to <domain>" in chat)** — the agent moves the file into the folder you name (or the one you already use for that kind of thing). The table below shows where each kind lands; the `Sources/` paths are examples, not a fixed scheme:

| File type | Lands under `Sources/` (your layout — examples) | Pointer in |
|---|---|---|
| Insurance card / policy doc | e.g. `Sources/Insurance/` | `Domains/Finances/sources.md` |
| Vehicle registration / title / receipt | e.g. `Sources/Vehicles/<vehicle-slug>/` | `Domains/Vehicles/sources.md` |
| Medical record / lab result / vaccine card | e.g. `Sources/Medical/<member-slug>/` | `Domains/Health/sources.md` |
| Appliance manual / warranty / receipt | e.g. `Sources/Warranties/<appliance-slug>/` | `Domains/Home/sources.md` |
| Pet vaccination / vet record | e.g. `Sources/Pets/<pet-slug>/` | `Domains/Pets/sources.md` |
| Travel itinerary / boarding pass / passport scan | e.g. `Sources/Travel/<trip-slug>/` | `Domains/Travel/sources.md` (or active trip `Projects/<trip-slug>/sources.md`) |
| Tax return / W-2 / 1099 | e.g. `Sources/Taxes/<year>/` | `Domains/Finances/sources.md` AND `Projects/tax-<year>/sources.md` |
| Reference material (article you want to keep) | e.g. `Sources/Reference/` (or your notes app of choice) | `Domains/Self/sources.md` |
| Working draft / scratch photo / quote spreadsheet | `Domains/<X>/Resources/` (or `Projects/<X>/Resources/`) | n/a (not a Source) |
| Thing to send to someone | `Outbox/` after the agent drafts it | n/a |
| Junk / one-off | Delete |

## Hygiene

- Anything in `Inbox/` for **more than ~14 days** is listed by the `doctor` skill as a candidate to file or discard. Nothing is moved or deleted without your say-so.
- This README is scaffolded by `workspace_init.py` (`uv run python -m superagent.tools.workspace_init`). Edit it freely — it will not be overwritten on subsequent inits.
