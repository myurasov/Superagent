# Quiet hours — when surfacing is suppressed

Tells the cadence and surfacing skills (`whatsup`, `daily-update`,
`weekly-review`, ad-hoc nudges) when to hold back.

Complements `_memory/notification-policy.yaml` (per `contracts/notification-policy.md`)
which is the structured, machine-checkable version of the same idea.

## Hard quiet window

* **22:00 — 07:00 local**: only P0 items surface. No P1 / P2 / P3 nudges,
  no "FYI" capture confirmations, no "you might want to look at" prompts.
* The `daily-update` cadence may still RUN (so the briefing is fresh by
  morning), but its OUTPUT is not pushed; it's read on demand when I ask.

## Soft quiet window

* **Weekend mornings before 09:00**: skip the routine briefing unless I
  ask for it. Bills due that day still count as P0 and surface.

## Work focus blocks

* When my calendar shows an event tagged `focus:` or with the word
  "deep work" / "focus" in the title, suppress all non-P0 surfacing
  for the duration. Write a "skipped X items, surface after?" entry to
  `_memory/context.yaml` so I can drain after the block.

## Travel / out-of-routine

* When my calendar shows a multi-day trip, default the daily-update to
  the destination timezone for that trip, but apply the same quiet window
  as `local` (the time zone shifts with me).
