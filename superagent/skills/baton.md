---
name: superagent-baton
description: >-
  Assemble a compact, paste-ready prompt that hands the CURRENT work off to
  another (or new) agent session: what/why, state now, exact file paths,
  next steps, gotchas. Session continuity, not estate planning — distinct
  from the `handoff` skill.
triggers:
  - hand this off
  - prompt for another session
  - continue in a new session
  - handoff prompt
  - baton
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent baton skill

> **Not the `handoff` skill.** `handoff` generates the "if I get hit by a bus"
> estate packet (accounts, documents, executor instructions). `baton` moves
> the **current in-flight work** to a fresh agent session — same user, new
> context window. If the user says "handoff" ambiguously, ask which they mean.

Resolve paths via `_memory/config.yaml` (`workspace_path`).

## 1. Gather state

Collect ONLY what this session actually touched — no workspace sweep:

- **Task / project context**: rows in `_memory/todo.yaml` created or updated this session; the active project's `Projects/<slug>/status.md` if one is in play.
- **Files modified this session**: exact absolute-or-repo-relative paths, one line each with a 5-10 word "what changed" note. Use the session transcript as the source of truth; `git status` may corroborate for framework files.
- **Open decisions / questions**: anything the user was asked and has not answered, plus any decision deferred mid-task.
- **Next steps**: the 1-3 concrete actions the receiving session should take first.

## 2. Render the baton prompt

Produce a plain-text block, target **< 400 words**, in this shape:

```
BATON — <one-line task title> (<date>)

WHAT / WHY
<2-3 sentences: the goal and why it matters now.>

STATE NOW
- <done item 1>
- <done item 2>
- Files touched:
  - <path> — <what changed>

NEXT STEPS
1. <first action, concrete>
2. <second action>
3. <third action>

GOTCHAS
- <trap, constraint, or half-applied change the next session must know>

CONTEXT POINTERS
- <relevant skill / contract / status.md / todo ids to read first>
```

Skip empty sections. Prefer exact paths and ids over prose. The receiving
agent is assumed to load `AGENTS.md` itself — do not restate framework rules.

## 3. Output

Print the block inline (in a fenced code block for clean copy-paste) — that
is the default and usually the end of the skill.

Only if the user asks for a file, write it to
`Outbox/drafts/<YYYYMMDD>-baton-<slug>.md`, calling
`uv run python -m superagent.tools.outbox ensure drafts` first (lazy
sub-directory rule, `contracts/outbox-lifecycle.md`).

## 4. Logging

Append to `_memory/interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "baton"
  summary: "Generated session-handoff prompt for <task title> (<N> next steps, <M> files listed)."
  related_domain: <inferred or null>
  action_items: []
```
