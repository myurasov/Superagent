---
name: superagent-email-refresh
description: >-
  Topic-scoped reply check: "did they respond?" for the email thread(s)
  behind the current task. Scans the local email archive first, live-fetches
  only the strictly-newer slice, then surfaces what changed + suggested next
  steps. Sits between whatsup (general delta) and summarize-thread (one
  known thread). Governed by contracts/email-capture.md and
  contracts/local-first-read-order.md.
triggers:
  - any replies
  - check for new email on this
  - did they respond
  - check replies
  - new email on this topic
mcp_required: []
mcp_optional:
  - gmail
cli_required: []
cli_optional: []
---

# Superagent email-refresh skill

> **Pre-flight (email-capture contract — `contracts/email-capture.md`)**:
> Before any `mcp_user-gmail_read_email` / `mcp_user-gmail_search_emails`
> call, scan the local archive via `superagent.tools.email.archive.find` or
> `find_by_query(...)`. Hit the live MCP only for the strictly-newer slice
> the local read does not cover. After every MCP read, call
> `archive.capture_inbound(raw_message)`; after every `search_emails`, call
> `archive.maybe_capture_stubs(results)`. Never bulk-fetch.

## 1. Resolve the topic → thread set

- **Explicit argument wins**: a contact name/email, a thread/message id, or a subject substring the user names.
- **Otherwise infer from the active context**: the project or domain being worked on this session — its `rolodex.md` contacts and any thread ids referenced in `status.md` / `history.md` / recent `todo.yaml` rows.
- If neither yields a topic, ask one short question ("Replies on which thread or from whom?") rather than sweeping the inbox.

Result: a small set of thread ids and/or (sender, subject) pairs.

## 2. Local archive scan (local-first)

Per `contracts/local-first-read-order.md`, scan
`_memory/email/_messages.jsonl` via
`superagent.tools.email.archive.find` / `find_by_query` for the thread set.
Record, per thread: newest archived message date, direction, one-line gist.
The newest archived date defines the "strictly newer than" boundary.

## 3. Live fetch — strictly-newer slice only

Only if freshness matters for the question (it usually does here), search
the Gmail MCP scoped to the thread set with `after:<newest archived date>`.
Read only the new messages. Capture-through mirrors them to the archive
automatically per the pre-flight above — no separate bookkeeping step.

## 4. Present

```
### New replies (<N>)
- <date> — <sender> re: <subject> — <one-line gist>

### Suggested next steps
- <action 1 — e.g. "confirm the quote", "answer their scheduling question">
- <action 2>
```

Offer drafts via the `draft-email` skill for any reply worth sending; do not
compose inline here. Capture any new action item as a `todo.yaml` row
(P2 default, P1 if a date is named, `source: email-refresh`).

## 5. Nothing new

Say so explicitly, anchored to the boundary:

> No new replies on <topic>. Newest message on file: <date> from <sender>.

## 6. Logging

```yaml
- id: "<ilog-YYYY-MM-DD-NNN via uv run python -m superagent.tools.next_id --kind ilog --file _memory/interaction-log.yaml>"
  ts: "<ISO 8601 datetime with offset>"
  skill: "email-refresh"
  action: "check_replies"
  summary: "Checked replies on <topic>: <N> new, <M> next steps suggested."
  related_domain: <inferred>
  action_items: <list or []>
```
