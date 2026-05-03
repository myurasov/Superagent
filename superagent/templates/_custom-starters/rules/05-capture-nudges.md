# Capture nudges — what to AMBIENTLY catch

Tells the agent to NOTICE certain phrases in conversation and propose
captures, without doing them silently. Output stays the same; an extra
trailing line proposes the capture.

Per `contracts/capture.md` (Capture Contracts), captures default to ambient
+ on-request surface. These nudges add specific propose-then-confirm
triggers on top.

## Tasks

* When I say "I should …", "I need to …", "remind me to …", "don't let
  me forget …": propose adding a P3 task with the obvious title. Don't
  add it silently.

* When I say "today" / "this week" / a specific date in the next 7 days
  in the same sentence: bump the proposed priority to P1.

* When I say "urgent" / "ASAP" / "now": propose P0.

## Contacts

* When I mention a tradesperson / contractor / professional by name
  (electrician, plumber, dentist, mechanic, lawyer, accountant), propose
  adding them to the relevant `Domains/<X>/rolodex.md` if they're not
  already there. Cross-reference with `_memory/contacts.yaml`.

## Expenses / bills

* When I mention a price + vendor (e.g. "Spotify is now $14"), propose
  either:
  - Updating an existing row in `subscriptions.yaml` / `bills.yaml`, OR
  - Adding a new one if no row matches.
* For one-shot expenses (e.g. "I just paid the plumber $400"), propose
  logging it to `Domains/Home/history.md` and to the bills.yaml as a
  one-shot bill (status: paid, paid_on: today).

## Decisions

* When I say "we / I decided" or "we agreed" or "going with X", propose
  appending a row to `_memory/decisions.yaml` (per `contracts/decisions.md`).

## Health events

* When I mention a symptom, dose change, or vital reading in passing,
  propose appending to `_memory/health-records.yaml` (the right
  sub-stream: `vitals`, `symptoms`, `meds`).
