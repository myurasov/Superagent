# Subagent bulk reads (always-on)

[Do not change manually — managed by Superagent]

Accumulated tool results are the dominant token cost of a long session: everything
read into the main session's transcript is re-sent on every subsequent tool
round-trip for the rest of the session. Keep bulk material out of the main
transcript by running it in a subagent and letting only the conclusion return.

This rule is the delegation-side companion of the read budget (`AGENTS.md`
§ "Read budget") and of `rules/large-file-reads.md` (slice instead of
whole-read) — it covers the case where even sliced results are collectively
too large for the main transcript.

## The rule

When a lookup is expected to pull **more than ~20k tokens of raw tool results**
(roughly: more than 3 full files, a multi-entity sweep across Domains/ or
Projects/, a whole email thread tree, a long log or archive dump) **and the
session will continue afterward**, run it in a subagent (Agent tool: `Explore`
for read-only sweeps, `general-purpose` when the sweep must write) and let only
the conclusion return. Phrase the subagent prompt to return the synthesized
answer — named facts, quotes, `file:line` pointers, entity handles — never the
raw dumps.

Typical delegate cases:

- Per-entity context assembly: "everything about X" questions that span a
  domain's `info.md` + `history.md` + related Sources + the events stream.
- Historical archive reads beyond the current delta window (email archive
  sweeps, old `events/<YYYY-Qn>.yaml` partitions, long `history.md` files).
- Multi-domain sweeps (doctor, supertailor-review evidence gathering,
  follow-up's dropped-ball hunt across every open commitment).
- Transcript / attachment / statement ingestion where the raw content is
  large but the capture is a few index rows.
- Multi-file framework-code investigations.

## When NOT to delegate

- 1–3 known small files, or sliced/grepped reads within the read budget —
  direct reads are cheaper than the subagent's spin-up overhead.
- The result itself is the deliverable and the session ends there.
- The delta sweeps inside cadence skills (`whatsup`, `daily-update`) — index
  reads and `log_window.py` windows are already header-shaped and small.

## Cost / model tiering

Mechanical sweeps (enumerate, filter, extract) run on a cheaper model at low
effort (e.g. `model: sonnet` or `haiku` on the Agent call); keep the session
model for judgment-heavy synthesis. Independent sweeps launch in a single
parallel batch (one tool-call message), per the read-budget BATCH rule.

## Enforcement

Sweep-heavy skills carry a one-line pointer to this rule in their body. The
Supertailor's hygiene pass flags skills that instruct unbounded multi-file
reads without either a slice discipline or a delegation note.

## Override / user overlay

Users may extend this policy at `workspace/_custom/rules/subagent-bulk-reads.md`
(additive; read after the framework file).
