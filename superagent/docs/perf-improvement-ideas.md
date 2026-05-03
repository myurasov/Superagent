# Performance — token efficiency, cache hit rate, and response latency

A working catalogue of ways to reduce tokens-per-skill-invocation and shorten response latency in Superagent. The patterns address how the agent reads files, when MCP / CLI tool calls are made, what gets cached, and how prompt-cache mechanics reward stable prefixes.

This doc is **diagnostic + catalogue**, not a roadmap. The Supertailor's strategic pass picks items off the shelf when matching friction shows up.

---

## Table of Contents

- [Performance — token efficiency, cache hit rate, and response latency](#performance--token-efficiency-cache-hit-rate-and-response-latency)
  - [Where the tokens go today](#where-the-tokens-go-today)
  - [Quick wins — XS / S](#quick-wins--xs--s)
    - [QW-1. Skill-discovery manifest](#qw-1-skill-discovery-manifest)
    - [QW-2. Per-skill step index](#qw-2-per-skill-step-index)
    - [QW-3. Read-budget policy in AGENTS.md](#qw-3-read-budget-policy-in-agentsmd)
    - [QW-4. `summary.md` siblings for unbounded YAML logs](#qw-4-summarymd-siblings-for-unbounded-yaml-logs)
    - [QW-5. Pre-rendered briefing cache](#qw-5-pre-rendered-briefing-cache)
    - [QW-6. Real LLM-generated `_summary.md` for the Sources cache](#qw-6-real-llm-generated-_summarymd-for-the-sources-cache)
    - [QW-7. Codify local-first read order across every ingestor](#qw-7-codify-local-first-read-order-across-every-ingestor)
  - [Medium investments — S / M](#medium-investments--s--m)
    - [MI-1. Per-session scratchpad / dedupe](#mi-1-per-session-scratchpad--dedupe)
    - [MI-2. Time-partitioned interaction log + events stream](#mi-2-time-partitioned-interaction-log--events-stream)
    - [MI-3. Embedded full-text search (SQLite FTS5)](#mi-3-embedded-full-text-search-sqlite-fts5)
    - [MI-4. Range-aware `add-toc.py`](#mi-4-range-aware-add-tocpy)
    - [MI-5. Skill-output write-back caching](#mi-5-skill-output-write-back-caching)
  - [Big bets — M / L](#big-bets--m--l)
    - [BB-1. Embeddings for semantic retrieval](#bb-1-embeddings-for-semantic-retrieval)
    - [BB-2. Anthropic prompt-cache alignment](#bb-2-anthropic-prompt-cache-alignment)
    - [BB-3. Pre-warmed cadence briefings (cron / launchd)](#bb-3-pre-warmed-cadence-briefings-cron--launchd)
    - [BB-4. World-model entity graph](#bb-4-world-model-entity-graph)
  - [Anti-patterns to flag in skills](#anti-patterns-to-flag-in-skills)
  - [Measurement — how to know if any of this worked](#measurement--how-to-know-if-any-of-this-worked)
  - [Recommended sequence](#recommended-sequence)

---

## Where the tokens go today

A diagnostic of the burn pattern in Superagent as of MVP:

| Source of waste | Why it happens | Fix layer |
|---|---|---|
| Whole-file reads of long skills (`init.md` ≈ 460 lines, `contracts/` ≈ 1500 lines) when only one section is needed | Agent reads top-to-bottom; no per-section index | Skill / doc shape |
| Whole-YAML reads of `interaction-log.yaml`, `ingestion-log.yaml`, `supertailor-suggestions.yaml` — append-only files that grow unbounded | No partitioning; no summary; no offset-aware reader | Memory schema |
| Re-reading the same file multiple times in one conversation | Agent has no "I already loaded this" memory | Session cache |
| MCP / API calls when the local mirror would do | Already mostly solved (email + slack archive contracts; Sources/_cache); the discipline is per-skill, not enforced | Read-side contract wording |
| Speculative reads ("let me load `<entity>` info just in case") | Agent's natural pattern when a skill says "read the entity's info" without specifying which sections | Skill instruction shape |
| Re-loading the entire prompt prefix on minor turns | Anthropic prompt caching only helps if the prefix is stable AND ordered; today's structure is opportunistic | Prompt-prefix discipline |
| `Sources/_cache/<hash>/_summary.md` is currently a "first 500 chars" placeholder, not a real summary | The shipped `sources_cache.py` deliberately avoided an LLM dependency to stay runnable offline | Summary-on-write |
| Skill discovery ("which skill applies?") — agent reads multiple skill frontmatters to decide | No central manifest | Index file |
| Sequential MCP calls when parallel would do | Agent doesn't always recognize independence | Skill instruction shape (explicit "do these in parallel") |

---

## Quick wins — XS / S

The "do these in an afternoon" tier. Small, targeted, safe — no schema migration required.

### QW-1. Skill-discovery manifest

- **LOE**: XS (one new file + a small tool to regenerate).
- **Saves**: ~2-5k tokens on every "which skill should I run?" turn.

Add `superagent/skills/_manifest.yaml`:

```yaml
schema_version: 1
generated_at: "2026-04-28T13:50:00-07:00"
skills:
  - name: daily-update
    one_line: "Morning briefing: bills, appointments, tasks, alerts."
    triggers: [daily update, morning briefing, what's today]
    typical_files_read:
      - workspace/_memory/config.yaml
      - workspace/_memory/context.yaml
      - workspace/_memory/todo.yaml
      - workspace/_memory/bills.yaml
    typical_token_cost: ~3000
    mcp_required: []
  - name: whatsup
    one_line: "30-second delta since last_check."
    triggers: ["what's up", status check, catch me up]
    ...
```

The agent reads this ONE small file (~5 KB total) to pick a skill instead of grepping through 35+ skill markdowns. After picking the skill, the agent reads only that one skill file in detail.

Generate via `tools/build_skill_manifest.py` — walks `superagent/skills/*.md` plus any `workspace/_custom/skills/*.md`, parses YAML frontmatter, emits the manifest. Re-run after any skill add / change. The Supertailor's hygiene pass should re-render it as part of its existing checks.

### QW-2. Per-skill step index

- **LOE**: XS-S (one-time edit per long skill, plus add to the skill template).
- **Saves**: 3-10k tokens per skill invocation on long skills.

Add a tiny step index right after the YAML frontmatter on every skill > 100 lines:

```markdown
## Step index

| # | Step | Lines |
|---|------|-------|
| 1 | Load configuration | 25-32 |
| 2 | Load last-check context | 34-44 |
| 3 | Run scheduled ingestors | 46-78 |
| 4 | Today's calendar / appointments | 80-114 |
| ... | ... | ... |
```

The agent reads the frontmatter + step index (≈ 40 lines), uses `Read --offset --limit` to pull only the relevant step, and never loads the full body. Long skills like `init.md`, `daily-update.md`, `monthly-review.md`, `supertailor-review.md`, `add-source.md` benefit most.

A new `superagent/tools/add_step_index.py` can auto-generate step indexes from H2/H3 headings + line numbers.

### QW-3. Read-budget policy in AGENTS.md

- **LOE**: XS (one paragraph).
- **Saves**: discipline gain — varies by adherence; could be 20-40% reduction in per-turn token cost.

Add an explicit rule to `AGENTS.md`:

> **Read budget**. For any file longer than 200 lines, the agent MUST run `Grep` first to locate the relevant section, then use `Read --offset --limit` to pull only that range. Whole-file reads are reserved for files known to be < 200 lines OR explicitly required (e.g. config.yaml, todo template). Skills that say "read X" implicitly mean "read the relevant section of X" — agents should treat full reads as the exception.
>
> When invoking multiple file reads or MCP calls that don't depend on each other, BATCH them in a single tool-call message (parallel execution) rather than chaining them sequentially. Sequential chains are reserved for the cases where output of step N feeds step N+1.

Costs nothing to add; bumps agent discipline noticeably across every invocation.

### QW-4. `summary.md` siblings for unbounded YAML logs

- **LOE**: S (small writer + reader).
- **Saves**: 5-50k tokens per skill that previously full-loaded a long log.

For files that grow without bound (`interaction-log.yaml`, `ingestion-log.yaml`, `supertailor-suggestions.yaml`):

Maintain a sibling `<file>.summary.yaml`, updated on every write OR nightly:

```yaml
# interaction-log.summary.yaml
schema_version: 1
generated_at: "2026-04-28T22:00:00-07:00"
total_rows: 12_487
last_30_days:
  count: 312
  by_type:
    email: 198
    meeting: 47
    skill_run: 67
  notable_threads:
    - "<one-line summary>"
    - "<one-line summary>"
last_7_days:
  count: 71
  by_type:
    email: 45
    meeting: 11
    skill_run: 15
oldest_entry_at: "2024-04-12T..."
newest_entry_at: "2026-04-28T..."
```

Skills consult `<file>.summary.yaml` first; they only pull the actual rows when the summary tells them they need to. The summary is small (~500 tokens) and stable for a 24h window. The writer is a small `tools/log_summarize.py` invoked by the writer of each log file, OR by a nightly cron.

### QW-5. Pre-rendered briefing cache

- **LOE**: S.
- **Saves**: 3-8k tokens on intra-day follow-up turns; reduces latency by ~3-5x on those.

When `daily-update` runs, it writes its rendered output to `_memory/_briefings/<YYYY-MM-DD>.md`. Subsequent intra-day skills (`whatsup`, conversational follow-ups about today) read the briefing from disk instead of regenerating it.

Invalidate when:
- The day flips (next day's `_briefings/<NEW>.md` is generated by the next `daily-update`).
- `_memory/context.yaml.last_check` advances (means the user explicitly asked for a refresh).
- Any of the source files (`bills.yaml`, `appointments.yaml`, `important-dates.yaml`, `todo.yaml`) was modified more recently than the briefing.

Same pattern for `weekly-review` (`_briefings/<YYYY-Www>.md`) and `monthly-review` (`_briefings/<YYYY-MM>.md`). 80%+ of "remind me what's on today" follow-ups become a single small file read.

### QW-6. Real LLM-generated `_summary.md` for the Sources cache

- **LOE**: S.
- **Saves**: keeps the local-first pattern actually useful (today's placeholder summary often forces the agent to load `raw.<ext>`).

Replace the "first 500 chars" placeholder in `superagent/tools/sources_cache.py:write_summary()` with an actual LLM-generated summary at fetch time. Structure:

- 1-2 sentence overview ("what this document is").
- Bullet list of what's in it ("3 sections: overview, technical specs, FAQs").
- Pointers to which sections are typically relevant ("for installation see § 4; for troubleshooting see § 7").

Implementation choices:
- Cleanest: call out via the agent's tool layer (the write happens during a skill invocation, so the agent can summarize).
- Alternative: use a tiny local model (`bge-m3` summarizer or similar) — slower one-time cost, no LLM dependency.

The cost is 200-500 tokens per fetch; the savings are 2-10k tokens per subsequent read of the same source (because the agent stops reading the raw on cheap-summary-was-enough cases).

### QW-7. Codify local-first read order across every ingestor

- **LOE**: XS.
- **Saves**: prevents agents from going to live MCPs / APIs when the cache or local mirror would do.

The Sources / `_cache/` contract (per `contracts/sources.md` § 15.5) already specifies the local-first read order. The same discipline needs to apply to ingestor-fetched data once the email / calendar / messaging / health ingestors land. Codify in `contracts/`:

> The agent **SHALL** consult the relevant local index (`_memory/<index>.yaml` for structured rows, `Sources/_cache/<hash>/` for cached external content, `Domains/<X>/history.md` for narrative) first. The agent SHALL fall through to a live ingestor / MCP **only when ALL of these are true**: (a) the local read returned no candidates that match the question; AND (b) the time window the question is asking about extends past the source's `last_ingest`; AND (c) freshness genuinely matters for the question. Otherwise, return what the local read found and stop.

Today's per-ingestor wording allows "I'll check both" patterns that double-pay. The strict form forces a single source of truth per query.

---

## Medium investments — S / M

### MI-1. Per-session scratchpad / dedupe

- **LOE**: S.
- **Saves**: 30-50% reduction in total session tokens on long conversations (10+ turns about the same entity).

`_memory/_session/<session-id>.yaml` tracks what the agent loaded in this conversation:

```yaml
session_id: "2026-04-28T13-45-A7F2"
started_at: "2026-04-28T13:45:00-07:00"
loaded_files:
  - { path: "workspace/_memory/config.yaml", at: "13:46:02", size_kb: 3.1, hash: "abc123" }
  - { path: "workspace/Domains/<X>/info.md", at: "13:46:18", size_kb: 4.2, hash: "def456" }
mcp_calls:
  - { server: "gmail", tool: "list_messages", args_hash: "...", at: "13:47:01" }
```

Before any read, the agent checks the scratchpad. If the file's mtime hasn't changed since the recorded `at` (or the hash matches), the agent reuses what it already saw THIS SESSION and skips the read.

Implementation note: this is partially what Cursor already does at the IDE layer (recent files are in the context window). MI-1 makes it explicit + persistent so a follow-up turn an hour later still benefits.

### MI-2. Time-partitioned interaction log + events stream

- **LOE**: M.
- **Saves**: order-of-magnitude reduction on per-skill log reads as the workspace ages.

Already in `ideas-better-structure.md` § #16 (events stream) and § #22 (time-windowed views). Convert `_memory/interaction-log.yaml` from a single growing file to a directory of monthly partitions:

```
_memory/interaction-log/
  2025-Q4.yaml
  2026-Q1.yaml
  2026-Q2.yaml      ← current (writes go here)
  _index.yaml       ← per-quarter event counts + date ranges
```

A `tools/log_window.py` yields a sub-window in O(log n). Daily-update reads only the current partition (~hundreds of rows) instead of the whole log (~tens of thousands).

The token win compounds with QW-4: skills query `<file>.summary.yaml` first, then narrow to a partition only when needed.

### MI-3. Embedded full-text search (SQLite FTS5)

- **LOE**: M.
- **Saves**: replaces "load 5-10 candidate files via Read" with "load the 200-token snippet that FTS5 returned".

Already in `ideas-better-structure.md` § #7. Beyond the search-latency win, the token-efficiency case is direct: ranked snippets give the agent the ~200 relevant tokens instead of forcing it to load whole candidate files to find them.

`_memory/_search/index.sqlite` updated incrementally. The agent's `research`, `summarize-thread`, `follow-up`, and Domain / Project lookup skills query the index. Falls back to grep when the index is missing or stale.

### MI-4. Range-aware `add_step_index.py`

- **LOE**: S.
- **Saves**: the toolchain to make QW-2 sustainable across many skills.

A new `superagent/tools/add_step_index.py` walks every skill markdown, generates a TOC from H2/H3 headings, and emits per-step line ranges (the QW-2 step index). Re-run as part of the `doctor` / `supertailor-review` hygiene pass to keep step indexes in sync as skill files are edited.

### MI-5. Skill-output write-back caching

- **LOE**: S.
- **Saves**: closes the loop on QW-5 by formalizing "every skill that produces a structured artifact writes it to a known cache location with an invalidation rule."

A new convention in `contracts/`: every skill whose output is a candidate for re-read within the same day (briefings, summaries, dashboard renders, top-5-things drafts) writes to `_memory/_artifacts/<skill>/<key>.md` with a sibling `_meta.yaml`:

```yaml
generated_at: "2026-04-28T08:30:00-07:00"
skill: daily-update
key: "2026-04-28"
inputs_hash: "<sha256 of the source files at generation time>"
ttl_minutes: 720
size_bytes: 2_312
```

Skills check the cache before regenerating. The Supertailor's strategic pass surfaces skills that NEVER cache-hit (candidates for shorter TTL or different cache key).

---

## Big bets — M / L

### BB-1. Embeddings for semantic retrieval

- **LOE**: L.
- **Saves**: order-of-magnitude reduction in "load candidate files to find the answer" patterns.

Already in `ideas-better-structure.md` § #8. Token-efficiency case: instead of "load 8 candidate domain notes to find the one about X", embed once + retrieve top-3 chunks (~600 tokens total). The relevant snippet comes back; the agent never loads the irrelevant 7.

A small local embedder (e.g. `bge-small` or `bge-m3`) is reliable + cheap, runs offline, and avoids any external API calls.

### BB-2. Anthropic prompt-cache alignment

- **LOE**: M (within an API wrapper); not actionable inside Cursor today.
- **Saves**: ~10x cost reduction + ~3-5x latency reduction on cached-prefix turns.

Anthropic's Claude prompt cache rewards a **stable prefix** with cache breakpoints. The first call after a cache miss pays full cost; subsequent calls within ~5 minutes that share the same prefix pay 10x less for the cached portion AND respond faster.

What this means structurally:

(a) **Move AGENTS.md + role files into the SYSTEM prompt** when running via the API directly. Cursor doesn't expose this knob today, but if you ever build a CLI wrapper or run via the API directly, structure the system prompt as:

```
[stable: AGENTS.md + role files]                                  ← cached
[cache_breakpoint]
[per-skill: the active skill + the contracts/<slug>.md it cites]  ← cached if same skill twice in a row
[cache_breakpoint]
[per-turn: this turn's user message + tool results]               ← never cached
```

A wrapper that does this and tags each block with `cache_control: { type: "ephemeral" }` would yield the headline speedup. Worth it for power users running via API.

(b) **For Cursor today**: the IDE controls the prompt structure. The framework can still help by keeping AGENTS.md SHORT and STABLE, and by keeping each `contracts/<slug>.md` self-contained — avoiding edits during a session that would invalidate the cache for downstream turns. The current docs are well-shaped for this; the real risk is when the user opens 5 different framework files mid-session and each one bumps the prefix.

Practical guidance for the user:
- Don't edit AGENTS.md during an active conversation if you can help it; commit edits between sessions. (Editing a single `contracts/<slug>.md` mid-session is much cheaper, since it's only loaded by the skills that cite it.)
- The Supertailor / Supercoder loop's commit-then-restart cycle is well-suited to this.

### BB-3. Pre-warmed cadence briefings (cron / launchd)

- **LOE**: M (one launchd plist + the briefing-cache plumbing from QW-5).
- **Saves**: makes "good morning" a single 200-token file read instead of 30+ tool calls.
- **Both frameworks**.

For known cadences (`daily-update` runs at 08:30 every day), have a launchd job pre-render the briefing into `_memory/_briefings/today.md` BEFORE the user opens the chat. Then the user's first turn is essentially "show me today.md", a single cheap read. The expensive work happened off-thread.

Operationalize via:
- A new `tools/install_launchd.py` that writes the appropriate `~/Library/LaunchAgents/com.<framework>.daily-update.plist`.
- A `--background` flag on `daily-update` that suppresses interactive prompts.
- An entry in `init`'s setup that asks "want me to install the daily-update launchd job?".

The ROI scales with how often the user reaches for the agent first thing in the morning. For SAs that's most days.

### BB-4. World-model entity graph

- **LOE**: L.
- **Saves**: replaces "load a customer file + grep its history.md + cross-check rolodex.md + scan interaction-log for that customer" (4-6k tokens) with "graph_query.py related_to=<customer>" (50 tokens).
- **Both frameworks**.

Already T3-A in both ideas docs. The token win is direct: an inverse index over the workspace lets "show me everything connected to X" be O(degree), with each result being a small reference rather than a loaded file.

The maintenance cost is real (every entity write touches the graph). Pays off once "show me everything related to" queries become common.

---

## Anti-patterns to flag in skills

When reviewing existing skills (or writing new ones), watch for these patterns that burn tokens without value:

1. **"Read the customer's info, status, history, rolodex"** — 4 file reads when usually only one is needed for the question. Replace with: "read the customer's `info.md` § Profile + `status.md` § Current Status; pull `history.md` only if the question is about past events."

2. **"Read all open tasks"** when the question is about a specific domain or project. Always include the filter in the instruction.

3. **"Search across the whole workspace"** for a topic that's clearly scoped to one domain.

4. **"Run X, then Y, then Z"** when X / Y / Z are independent. Use parallel batching.

5. **"Pull the full email thread"** when the agent already has the message-level summary in `interaction-log.yaml`.

6. **"Re-render the daily-update"** when the briefing-cache (QW-5) would have it.

7. **"Read the whole `contracts/`"** to remind the agent of one section. Use `Read --offset --limit` against the documented line range, OR read `contracts/` Table of Contents first.

8. **"Read every skill markdown to figure out which applies"** — replaced by QW-1 manifest.

9. **"Loaded large file, then tool-called the LLM to extract one fact"** — should have grep'd or used FTS5 first.

The Supertailor's strategic pass should grow a check that flags these patterns from `_memory/working-sets.jsonl` (T3-F idea) — once that's wired, anti-pattern detection becomes automatic.

---

## Measurement — how to know if any of this worked

Token + latency optimizations are easy to claim, hard to prove. Recommended measurement protocol:

1. **Per-turn telemetry**: add `_memory/_telemetry/<YYYY-MM-DD>.jsonl` rows recording per-skill: `{skill, started_at, finished_at, tool_calls_count, tool_call_kinds, files_read, total_bytes_read, mcp_calls_count, output_tokens_estimate}`. (Estimate via token-counting tools or by output character count / 4.)

2. **Baseline week**: before shipping any of these changes, gather a week of telemetry from normal usage.

3. **A/B at the skill level**: ship one optimization at a time. Compare the per-skill mean token-cost and mean latency between the baseline week and the post-change week.

4. **Surface the deltas in `monthly-review`**: a "framework efficiency" section showing the trailing 30 days vs the prior 30. The Supertailor uses this signal to suggest which optimizations to keep iterating on.

5. **Anti-metric: quality regression**. Token reduction is worthless if the answers get worse. Track agent-correction frequency from `model-context.yaml.corrections` as a proxy — if it climbs after a change, suspect that change.

The instrumentation itself is small (one new tool, ~50 LOC), but it transforms the rest of this catalogue from "ideas" into "things we can prioritize from data".

---

## Recommended sequence

**Today / this week (LOE-XS-S):**

1. **QW-1** — skill manifest. Single biggest discoverability win.
2. **QW-3** — read-budget policy in AGENTS.md. Free.
3. **QW-5** — briefing cache. Closes a real loop.

**This sprint (LOE-S):**

4. **QW-2** — per-skill step index (start with the 5 longest skills).
5. **QW-4** — log summaries.
6. **QW-7** — codify local-first read order across every ingestor.
7. **QW-6** — real LLM-summary in sources_cache (Superagent).

**Next month (LOE-S-M):**

8. **MI-1** — per-session scratchpad.
9. **MI-4** — range-aware add-toc.py to keep step indexes maintained.
10. **MI-5** — skill-output write-back caching convention.

**Next quarter (LOE-M):**

11. **MI-2** — time-partitioned interaction log + events stream.
12. **MI-3** — SQLite FTS5 search index.
13. Telemetry from the measurement section — the foundation for everything else.

**Bigger bets (LOE-L):**

14. **BB-1** — embeddings for semantic retrieval.
15. **BB-3** — launchd pre-warming for cadence briefings.
16. **BB-2** — Anthropic prompt-cache alignment (only when running via API directly).
17. **BB-4** — world-model entity graph.

The Supertailor's strategic pass should be the actual prioritizer — these tiers are a starting point, but the order changes based on which friction signals trip first in the wild.
