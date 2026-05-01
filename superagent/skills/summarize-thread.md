---
name: superagent-summarize-thread
description: >-
  Condense a long email or message thread into key points, decisions, action
  items. Reads from local email / message mirror; falls through to live
  source for the strictly-newer slice.
triggers:
  - summarize this thread
  - tldr this email
  - what's the upshot of <thread>
mcp_required: []
mcp_optional:
  - email ingestors
  - imessage / whatsapp / signal / slack ingestors
cli_required: []
cli_optional: []
---

# Superagent summarize-thread skill

## 1. Resolve the thread

If the user pasted the thread, use that. Otherwise resolve by:
- Subject substring against the email mirror.
- Sender + date range.
- A specific message ID.

## 2. Read the thread

Read from the local mirror first. If `last_ingest` for the relevant source < end-of-thread, ingest the strictly-newer slice (capture-through per the contract).

## 3. Generate the summary

Structure:

```
## Thread: <subject>
**Participants**: <list>
**Span**: <first message date> → <last message date> (<N> messages)

### Key points
- <point 1>
- <point 2>

### Decisions made
- <decision 1>
- <decision 2>

### Open questions
- <question 1>

### Action items
- <action> (owner: <who>, due: <date or "not specified">)
```

Render to `workspace/Outbox/summaries/<YYYYMMDD>-<thread-slug>.md`.

## 4. Capture action items

For each action where the user is the owner (or no owner is named and the user was on the thread):

- Create a P2 task in `todo.yaml` (P1 if a date is named).
- Set `source: summarize-thread`.

## 5. Surface

```
Summarized thread "<subject>" — <N> messages → <key points count> points, <action count> actions.
Saved to <path>.
Tasks created: <count>.
```

## 6. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "summarize-thread"
  summary: "Summarized <subject>; captured <N> action items."
  related_domain: <inferred>
  action_items: <list>
```
