# Token economy (always-on, leveled)

[Do not change manually — managed by Superagent]

Governs how much enters the main context and how fast it is re-sent.
Everything read into the main transcript is re-billed on every later tool
round-trip — the cheapest token is the one never loaded. Two layers: an
**always-on floor** (no quality trade-off at any level) and **graded
measures** whose parameters the level sets. Delegation mechanics — including
the bulk-read threshold and its `full`-level tightening — live in
`rules/subagents.md`; that rule's `auto` posture follows this rule's
resolved level.

## Level switch

`preferences.token_economy.level` in `_memory/config.yaml` —
`off | med | full | auto`; key or block absent = `auto`.

- **`off`** — the floor only, no graded measures.
- **`med`** — the standing posture.
- **`full`** — the budget-crunch bundle: maximum frugality within the hard
  floors. Sibling-measured 13–28% cheaper on heavy sessions at unchanged
  quality, but +5–8% on short light one-shots — for crunches and heavy
  sessions, not a free upgrade.
- **`auto`** (default; context-scaled) — `med` while the session is light;
  `full` once the session is provably heavy. Superagent cannot observe its
  own token count without a hook, so the ratchet fires on proxies — any of:
  - a compaction or summarization marker appears in the transcript;
  - the session has read more than ~10 files or made more than ~30 tool
    calls;
  - the user says the session is getting long.

  One-way ratchet within a session (never drops back to `med`). A wrong call
  costs a few percent of frugality, never correctness — the hard floors sit
  underneath at every level.

**Per-request override:** `economy: off|med|full|auto` (alias
`token-economy:`) anywhere in a user message applies to that request only —
acknowledge in one line, no config write. An unrecognized value gets a
one-line correction and no override. **Config changes only on explicit
persistence language** ("set / remember / save / from now on"). Pacing has
its own override (`asap`, below).

## Always-on floor

Applies at every level, including `off`:

- **Read budget.** For any file past ~200 lines, `Grep` (or the file's TOC /
  step index) to locate the relevant section, then `Read --offset --limit`
  to pull only that range. Whole-file reads are for known-small files or
  explicitly-required singletons (`_memory/config.yaml`, `context.yaml`).
  "Read X" in a skill means the relevant section of X. For "which skill
  applies", read `skills/_manifest.yaml`, never every skill body; for long
  skills, read the frontmatter + `## Step index` first, then the listed step
  ranges. Hook-injected content (auto-loaded skill bodies) is already in
  context — never re-open those files unless editing them.
- **Unbounded files.** Never read the files in the decision table below
  whole — slice, tail, grep, or route through the canonical tool.
- **Batching.** Independent tool calls (no data dependency, no decision
  between them, no shared mutated state) go in one parallel message — never
  one call per message; sequential chains are only for step-N-feeds-step-N+1.
  A mechanical survey of 4+ files you will summarize (not edit) is one
  batched shell sweep (`head -40 f1 f2 ...`; the `==> file <==` delimiters
  prevent misattribution), sibling-measured up to 3.7x cheaper than per-file
  Reads. Guardrails, never relaxed by level: `Read` (the tool) any file you
  are about to Edit — shell-read content is not registered for edits and you
  pay for the bytes twice; chunk sweeps past ~20 files so output caps cannot
  silently truncate the tail; prefer a targeted grep whenever the question
  allows early exit. A harness without parallel batching runs the calls
  back-to-back with no commentary between them.
- **Cache stability.** Time-varying fields (timestamps, counters, "last X"
  markers) go at the END of always-loaded files, never the top; no
  timestamps in `AGENTS.md` or rule files. Draft the complete change first
  and edit an always-on file ONCE per session — never N incremental
  revisions of the same file (each is re-ingested by every future session).
- **Never re-read your own writes.** The Edit/Write result already proves
  the change landed; reuse evidence already in context instead of
  re-fetching it.

## Unbounded-file decision table

Canonical decision table for memory-resident files that grow without bound.

| File | Growth | Allowed reads | Forbidden |
|---|---|---|---|
| `_memory/todo.yaml` | hundreds–thousands of tasks | `yaml.safe_load` in a tool, then filter by `status` / `priority` / `related_*`; OR grep for a specific task id when known; OR the rendered `workspace/todo.md` view. Exception: the `live-todo` write path (see Hard floors) | Reading the file whole into context to "look at the todo list" on a browse / lookup path. The model never needs every row at once. |
| `_memory/email/_messages.jsonl` | one row per touched email, unbounded | `superagent.tools.email.archive.find(...)` / `find_by_query(...)` | Direct `cat` / `Read` / whole-file grep dumps. |
| `_memory/email/<YYYY>/<MM>/<DD>/<file>.json` | per-message shards, individually small | `Read` whole per message — that is the unit of work | Enumerating shards via a directory walk; use `archive.find` to locate the shard first. |
| `_memory/interaction-log.yaml` | one entry per skill run, unbounded | Tail-read with `Read offset=-N` for the recent slice; `_memory/interaction-log.summary.yaml` for "what happened lately"; full scans ONLY in `supertailor-review` and `monthly-review` | Loading the full file for "what did I do recently". |
| `_memory/ingestion-log.yaml` | one row per ingest run | Same as interaction-log (tail / summary sibling) | Full read in non-review skills. |
| `_memory/events/<YYYY-Qn>.yaml` | quarter-partitioned event stream | `uv run python -m superagent.tools.log_window read --since <date>` (loads only the partitions the window touches) | Reading every partition for a timeline question. |
| `_memory/user-queries.jsonl` | one row per user prompt | Tail-read or grep for a specific phrase; full scans ONLY in `supertailor-review` | Reading the whole log to summarize "what the user usually asks". |
| `_memory/action-signals.yaml` / `_memory/personal-signals.yaml` | grow with captures | `yaml.safe_load` + filter by `target` / `status` / date in a tool; OR tail-read | Full read in non-review skills. |
| `_memory/transactions.yaml` | one row per bank transaction | Filter by date window / account in a tool (`reconcile_transactions.py` does this); grep for a merchant when known | Whole-file read to "review spending" — the workbook Bank Feed sheet and the reconciler are the surfaces. |
| `Domains/<d>/history.md` (long-lived domains) | chronological, unbounded | Grep for the entity/date, then `Read --offset --limit` the matching slice; recent-window read via tail | Whole-file read of a multi-year history for a single-event question. |

**Decision rule for any new memory-resident file:**

1. **Bounded by design** (`config.yaml`, `contacts.yaml`, entity-shape
   indexes like `domains-index.yaml` / `bills.yaml`)? → OK to `Read` whole
   (subject to the read budget above).
2. **Append-mostly with no upper bound** (logs, signal stores, archives)? →
   Must have a canonical filtering tool **before** any skill reads from it.
   If no tool exists yet, write one (or extend `log_window.py` / the archive
   helpers) before extending the file's use.
3. **Append + the agent often needs the latest N entries**
   (`interaction-log.yaml`)? → Tail-read with explicit negative `offset`.

Search method and read discipline are orthogonal: this table governs *not
loading huge files whole*, not which search tool to use. A harness without a
ranged-read tool satisfies it with any equivalent mechanism (`sed -n
'200,260p'`, `awk`, `rg` with context) — the constraint is on bytes loaded
into context, not on the specific tool.

## Measures by level

`off` = the floor alone; `med` and `full` add:

| # | Measure | `med` | `full` |
|---|---|---|---|
| 1 | Multi-file surveys | One shell sweep capped ~30–35 lines/file; ruled-out items get zero further reads | Same; metadata-only pre-triage allowed only past ~50 candidates |
| 2 | Read slicing | Grep-then-slice; slice via the Read tool when an Edit is likely | Med, plus shell slicing reserved for provably read-only surveys |
| 3 | Prior-art check | Grep the symptom / id / function name before any fix | Required before any fix or new artifact; named in the report |
| 4 | Schema learning | One sibling example over doc reads | Sibling example only; docs only when no example exists |
| 5 | Verification style | Counts where a count proves it (`grep -c`, exit codes) | Same, firmly — content verification whenever anything is ambiguous |
| 6 | Re-fetch discipline | Reuse in-context evidence over re-fetching | Med, plus prefer in-context knowledge for non-decision-critical data (flag staleness) |
| 7 | Enrichment sources | Skip when the primary material suffices — and say so | Skip by default — and say so |
| 8 | New-artifact size | Compact: trigger, imperative, one example | Minimal |
| 9 | Round-trip discipline | Batch all independent calls; merge shell steps where safe | A round-trip only when its output gates the next step |
| 10 | Heavy command output | Output expected past ~2k tokens → redirect to a log under `~/.superagent/tmp/`, read back a filtered slice (guardrails below). Commands only — NEVER an artifact you are writing | Default for every heavy command; open more of the log on any anomaly |
| 11 | Subagent return shape | Name the expected shape in every delegated prompt | Med, plus a hard size target ("under N lines"); schema-forced output where the harness supports it |

**Measure 10 guardrails** (mandatory when used): capture and echo the exit
code first — never infer success from a clean-looking tail; grep the log for
error/warn/fail counts plus a bounded tail; on any anomaly (odd exit code,
nonzero error count, unexpected timing) open a larger slice of the log
before concluding — the redirect trades away incidental noticing, this buys
it back; unique log names under `~/.superagent/tmp/`; exempt when the output
IS the deliverable; if a sandbox makes the redirect cost an escalation,
bounded inline output is cheaper.

## Pacing

Keep round-trips/min ≤ budget ÷ current context tokens. Budget:
`preferences.token_economy.input_tokens_per_minute` — an integer or k/m
shorthand (`700k`, `1m`); key absent = 1m. At 1m that is ≤10/min at 100k
context, ≤5/min at 200k. When over, re-batch the remaining work into fewer,
larger calls or pause briefly — a pause beats a rate-limit retry loop. Never
poll long-running work on short intervals — use background monitors with
filtered output and space out checks; every check re-sends the whole
context. Pacing is level-independent, and a pacing rule, not a hard cap:
correctness and data integrity win over speed — when in doubt, slow down
rather than drop steps. **Per-request override:** `asap` anywhere in the
message — burst for that request only; the economy measures keep their own
override above.

## Hard floors (never trimmed)

Never save tokens by:

- Skipping same-turn verification of action claims, confirmation gates, or
  the Non-Negotiables (upstream-write confirmation, skill discipline).
- Truncation-reading a rule, contract, skill, or template you are about to
  modify or condense — wording passes read the whole artifact.
- Skipping the Framework Artifact Creation Contract's personal-data
  safeguard scan before any `superagent/` write.
- Dropping opportunistic retention / capture-through of relevant content
  actually seen (`contracts/ingestion.md`; § "Knowledge discipline").
- Skipping the interaction-log or `model-context.yaml` save points.
- Shortcutting the `live-todo` write path: before writing
  `workspace/todo.md`, `rules/live-todo.md` mandates a full read of
  `_memory/todo.yaml` — that full read IS the floor there. The decision
  table's `todo.yaml` ban governs browse / lookup paths only.

Token savings are a tiebreaker among correct approaches, never a reason to
degrade correctness, verification, or privacy.

## Why (sibling-measured)

The calibration behind the levels was run on the sibling frameworks (Co-SA /
Solaris, 2026-08: ~200 graded runs plus headed follow-ups, three models) —
cited here as sibling evidence, not native Superagent measurement:
heavy-session spend is dominated by context re-sends (~60% observed), so
savings compound; `full` measured Pareto-optimal on heavy real work on every
model tested, with quality unaffected at every level. Survey caps of ~30–35
lines/file cover most file summaries at ~57% of full-read cost (a 15-line
cap covered none and forced re-reads). Counts vs content for a yes/no check:
~2,000x cheaper. Batched sweeps: up to 3.7x cheaper than per-file reads.
Stronger models apply frugality more intelligently — economy is what lets a
frontier-tier main session with fast-tier delegation cost about what an
un-economized mid-tier session does.

## Override / user overlay

Users may extend this rule (including the decision table) at
`workspace/_custom/rules/token-economy.md` (additive; same shape).
