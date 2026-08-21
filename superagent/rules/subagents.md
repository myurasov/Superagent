# Subagents (bulk-read floor + leveled delegation)

[Do not change manually — managed by Superagent]

What runs **outside** the main context. Two layers: an always-on **bulk-read
floor** keeps oversized reads out of the main transcript, and a **leveled
delegation posture** on top lowers the bar to delegate-by-default. Sibling:
`rules/token-economy.md` (how much enters context, how fast it is re-sent);
its resolved level drives this rule's `auto` posture.

## Bulk-read floor (always-on)

Accumulated tool results are the dominant token cost of a long session.
When a lookup is expected to pull **more than ~20k tokens of raw tool
results** (roughly: more than 3 full files, a multi-entity sweep across
`Domains/` or `Projects/`, a whole email thread tree, a long log or archive
dump) **and the session will continue afterward**, run it in a subagent —
read-only agent type for sweeps, read-write only when it must write (Claude
Code: `Explore` / `general-purpose`) — and let only the conclusion return.
Phrase the prompt to return the synthesized answer: named facts, quotes,
`file:line` pointers, entity handles — never raw dumps. When the economy
level resolves to **`full`**, the threshold tightens to **~10k tokens**.

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

**Delegated reads of live sources capture what they see.** When a subagent
reads email, calendar, web, bank, or any live MCP / CLI source, the dispatch
prompt must explicitly instruct the same-pass write-through into the local
stores (email-archive capture per `contracts/email-capture.md`, Sources
cache, entity filing with `provenance`) — "read-only" means no upstream
writes, not no local capture — and the orchestrator verifies the capture
landed before closing the pass (the siblings logged a real miss here).

**Harness without a subagent tool?** (see `docs/harness-matrix.md`.) Do not
skip the lookup — run it checkpointed inline: sliced/grepped reads within the
read budget, notes accumulated in a scratch file under `~/.superagent/tmp/`,
and only the conclusions restated in the reply. The posture below still
applies through this fallback: at `quality`/`cost` checkpoint-inline
delegable work by default; at `off` only floor-sized lookups.

**When NOT to delegate:**

- 1–3 known small files, or sliced/grepped reads within the read budget —
  direct reads are cheaper than the subagent's spin-up overhead.
- The result itself is the deliverable and the session ends there.
- The delta sweeps inside cadence skills (`whatsup`, `daily-update`) — index
  reads and `log_window.py` windows are already header-shaped and small.

**Floor tiering, regardless of posture:** mechanical sweeps (enumerate,
filter, extract) run on the fast tier at low effort; keep the session model
for judgment-heavy synthesis. Independent sweeps launch in a single parallel
batch (one tool-call message), per the batching floor in
`rules/token-economy.md`.

## Posture switch

`preferences.token_economy.subagents` in `_memory/config.yaml` —
`off | auto | quality | cost`; key or block absent = `auto`. Aliases,
accepted wherever the value is read: `med`/`q` = `quality`; `full`/`save` =
`cost`.

- **`off`** — leveled posture off; the bulk-read floor above still applies
  (it is never disabled by this switch).
- **`auto`** (default) — derive from the **resolved** economy level
  (`rules/token-economy.md`): economy `off` → `off`, `med` → `quality`,
  `full` → `cost`. One dial — a crunch tightens both.
- **`quality`** — posture in force, every tier choice one-upped (see Model
  tiering) — output quality over cost.
- **`cost`** — posture in force at the cheapest viable tier.

`quality` and `cost` differ only in WHICH model runs a delegated task —
never in WHETHER to delegate. Both delegate aggressively: anything delegable
is delegated by default, parallel subagents beat one long inline pass, and a
close call goes to delegation. Frugality at `cost` means a cheaper tier, not
keeping the work inline.

**Per-request override:** `subagents: off|auto|quality|cost` (or an alias)
anywhere in a user message applies to that request only — acknowledge in one
line, no config write. An unrecognized value gets a one-line correction
listing the valid values, and no override. **Config changes only on explicit
persistence language** ("set / remember / save / from now on").

## The posture

At `quality`/`cost`, any self-contained unit of work runs in a subagent by
default: multi-file reads and grep sweeps (even under the floor threshold,
when 2+ round-trips are likely), "find where X is defined/used" lookups,
summarizing a document/thread/log, per-entity context assembly, mechanical
edits across files once the exact change is specified, verification passes
whose outcome is a short verdict, research questions answerable from
docs/web/MCP sources. Independent tasks launch in one parallel batch. Before
starting any multi-step lookup or mechanical task inline, ask "why is this
not a subagent?" — proceed inline only on a carve-out below. Repeatedly doing
delegable work inline is a defect (capture an action signal for
`supertailor-review`).

Delegation buys **context headroom**, not one-shot savings: raw reads die
with the subagent, which compounds across every later round-trip — but
sibling-measured, delegating short work cost 7–22% MORE than inline (each
spawn pays a fresh system prompt). Delegate for long-session headroom,
wall-clock parallelism, and tier arbitrage. For a mechanical sweep over
greppable material, a single batched shell call (`rules/token-economy.md`,
batching floor) protects context cheaper still — delegate when the work
needs judgment per item, when raw volume would flood a continuing session,
or when independent sub-questions can run in parallel.

## Task contract (every delegated prompt)

A delegated task must be executable by a weaker model. Every subagent prompt
carries:

1. **Exact scope** — the files, directories, queries, or ids to operate on;
   no "look around and figure it out".
2. **Exact procedure** — which tools/commands, in what order, with known
   invocations spelled out (e.g. `log_window.py read`, `archive.find`, the
   sources-cache read order) so the subagent doesn't rediscover them.
3. **Exact return shape** — named facts, quotes, `file:line` pointers, a
   verdict, a table; never raw dumps.
4. **Boundaries** — read-only vs write, what NOT to touch, and any privacy /
   sensitive-tier rules in scope for the material being handled.
5. **Active modes restated** — subagents do not see the always-on rules;
   restate any active mode that shapes the deliverable (the economy level,
   capture-through duties, output conventions). Subagents follow the
   token-economy floor too — bulk reads in a helper are billed all the same.

If a task cannot be phrased this way, split it until it can — or keep it
inline only when judgment is genuinely inseparable from the reading.

## Model tiering

Rules speak in abstract tiers — Superagent uses three (**frontier /
balanced / fast**). The tier definitions, current model examples, AND the
task-class → tier mapping per posture live in `docs/harness-matrix.md`
§ "Model tiers (abstraction for rules)" — the perishable layer; check it,
not memory. Summary: mechanical work runs fast (`cost`) / balanced
(`quality`); moderate synthesis runs balanced / frontier; judgment-heavy
work stays on the session model (at `quality`, never below frontier). In
doubt at `cost`, take the cheaper tier; in doubt at `quality`, the stronger
one.

## What stays inline

- Single reads of known-small files (`config.yaml`, `context.yaml`, a
  `status.md`), or one sliced/grepped read — spin-up costs more than it
  saves.
- Steps whose output the very next decision depends on, completing in one
  round-trip.
- Destructive, upstream-mutating, or outward-facing actions and their
  confirmations (Non-Negotiables item 6; `contracts/outbound-surface.md`) —
  these never delegate; the confirmation stays with the user-facing session.
- Work where the deliverable IS the reading (the user asked to see the
  file).

## Override / user overlay

Users may extend this policy at `workspace/_custom/rules/subagents.md`
(additive; read after the framework file).
