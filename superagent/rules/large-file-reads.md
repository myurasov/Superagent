# Large file read discipline (always-on)

[Do not change manually — managed by Superagent]

Canonical specification for **"never `Read` unbounded memory-resident files
whole."** Several `_memory/` files grow without bound; loading one whole into
the agent's context pays a token + latency cost on every subsequent turn of
the session. This is the decision table for those files: never `Read` them
whole; always slice, tail, grep, or route through the canonical filtering
tool.

Companion levers: the read budget in `AGENTS.md` (grep-then-slice for any file
over 200 lines) and `rules/subagent-bulk-reads.md` (delegate when even sliced
results are collectively too large).

## The list

| File | Growth | Allowed reads | Forbidden |
|---|---|---|---|
| `_memory/todo.yaml` | hundreds–thousands of tasks | `yaml.safe_load` in a tool, then filter by `status` / `priority` / `related_*`; OR grep for a specific task id when known; OR the rendered `workspace/todo.md` view | Reading the file whole into context to "look at the todo list". The model never needs every row at once. |
| `_memory/email/_messages.jsonl` | one row per touched email, unbounded | `superagent.tools.email.archive.find(...)` / `find_by_query(...)` | Direct `cat` / `Read` / whole-file grep dumps. |
| `_memory/email/<YYYY>/<MM>/<DD>/<file>.json` | per-message shards, individually small | `Read` whole per message — that is the unit of work | Enumerating shards via a directory walk; use `archive.find` to locate the shard first. |
| `_memory/interaction-log.yaml` | one entry per skill run, unbounded | Tail-read with `Read offset=-N` for the recent slice; `_memory/interaction-log.summary.yaml` for "what happened lately"; full scans ONLY in `supertailor-review` and `monthly-review` | Loading the full file for "what did I do recently". |
| `_memory/ingestion-log.yaml` | one row per ingest run | Same as interaction-log (tail / summary sibling) | Full read in non-review skills. |
| `_memory/events/<YYYY-Qn>.yaml` | quarter-partitioned event stream | `uv run python -m superagent.tools.log_window read --since <date>` (loads only the partitions the window touches) | Reading every partition for a timeline question. |
| `_memory/user-queries.jsonl` | one row per user prompt | Tail-read or grep for a specific phrase; full scans ONLY in `supertailor-review` | Reading the whole log to summarize "what the user usually asks". |
| `_memory/action-signals.yaml` / `_memory/personal-signals.yaml` | grow with captures | `yaml.safe_load` + filter by `target` / `status` / date in a tool; OR tail-read | Full read in non-review skills. |
| `_memory/transactions.yaml` | one row per bank transaction | Filter by date window / account in a tool (`reconcile_transactions.py` does this); grep for a merchant when known | Whole-file read to "review spending" — the workbook Bank Feed sheet and the reconciler are the surfaces. |
| `Domains/<d>/history.md` (long-lived domains) | chronological, unbounded | Grep for the entity/date, then `Read --offset --limit` the matching slice; recent-window read via tail | Whole-file read of a multi-year history for a single-event question. |

## Decision rule (for any new memory-resident file)

1. **Bounded by design** (`config.yaml`, `contacts.yaml`, `domains-index.yaml`,
   `bills.yaml`, other entity-shape indexes)? → OK to `Read` whole.
2. **Append-mostly with no upper bound** (logs, signal stores, archives)? →
   Must have a canonical filtering tool **before** any skill reads from it. If
   no tool exists yet, write one (or extend `log_window.py` / the archive
   helpers) before extending the file's use.
3. **Append + the agent often needs the latest N entries**
   (`interaction-log.yaml`)? → Tail-read with explicit negative `offset`.

Search method and read discipline are orthogonal: this rule governs *not
loading huge files whole*, not which search tool to use. A harness without a
ranged-read tool satisfies the rule with any equivalent mechanism (`sed -n
'200,260p'`, `awk`, `rg` with context) — the constraint is on bytes loaded
into context, not on the specific tool.

## Enforcement

Skills under `superagent/skills/` are expected to follow this rule;
violations are surfaced in review and in the Supertailor's hygiene pass.
A machine check (an `anti-patterns.yaml` rule flagging a bare `Read` of a
file in the table above without `offset` / `limit` / canonical-tool
routing) is future work, tracked in `docs/roadmap.md`.

## Override / user overlay

Users may extend the table at `workspace/_custom/rules/large-file-reads.md`
(additive; same shape).
