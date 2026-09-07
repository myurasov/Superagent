# `superagent/docs/_internal/`

Planning + history docs for the **Supertailor** (Superagent's strategic-improvement loop). **Not user-facing.** New users should never need to open these.

## What lives here

| File | Purpose |
|---|---|
| `ideas-better-structure.md` | Full catalogue of 25 structural-improvement options. Kept whole by design — `supertailor-review` hygiene check 8 expects § 1-25 + Selection guide to be present. Implementation status lives in the `.done.md` sibling. |
| `ideas-better-structure.done.md` | Implementation log: which catalogue entries shipped and where their artifacts live. Entries are never moved out of the catalogue; kept for institutional memory. |
| `perf-improvement-ideas.md` | Full catalogue of token-efficiency / latency / cache-hit options, tiered Quick wins / Medium investments / Big bets. Kept whole (hygiene check 8 expects the tiers). Implementation status lives in the `.done.md` sibling. |
| `perf-improvement-ideas.done.md` | Implementation log for the perf catalogue; same purpose as above. |
| `claude-compatibility-proposal.md` | Design + implementation plan for first-class Claude Code support; shipped in 0.5.0 (see roadmap § Released). Kept as design history. |

## Why these are separate

These files exist to give the Supertailor a place to think out loud. They're long, opinionated, occasionally aspirational, and they reference internal mechanics most users don't need to know exists.

If you're a new user looking for "what does Superagent do" → start at the [repo-root README](../../../README.md). If you're trying to extend the framework → start at [architecture.md](../architecture.md).
