---
name: superagent-draft-email
description: >-
  Compose a personal email with full context (recipient history, related
  domain, prior thread). Writes the draft to `Outbox/emails/`. Read-only
  against email sources unless `writes_upstream: true` for that source
  (which is not the MVP default).
triggers:
  - draft an email to <recipient>
  - reply to <thread>
  - write a message to <person> about <topic>
mcp_required: []
mcp_optional:
  - email ingestors (for prior-thread context)
cli_required: []
cli_optional: []
---

# Superagent draft-email skill

## 1. Resolve the recipient

1. Match user's reference against `contacts.yaml.contacts[].name + aliases + email`.
2. If unambiguous, use it.
3. If ambiguous (multiple matches), ask user.
4. If new (no match), offer `add-contact` first.

## 2. Determine the context

Pull all of:
- **Recipient row** (`contacts.yaml.<id>`) — relationship, organization, last_contacted, notes.
- **Domain context** — for each domain in `related_domains[]`, read the latest 3 H4 entries from `Domains/<domain>/history.md` (mentioning this person).
- **Prior thread** if user references one — fetch from local email mirror or via the ingestor (with capture-through).
- **User voice** — `model-context.yaml.communication.style + addressing + abbreviations`.

## 3. Compose the draft

Generate a draft:

- **Subject**: short, purpose-led.
- **Opening**: appropriate to relationship (warm for friends / family; professional for providers).
- **Body**: get to the point in the first paragraph. Specific reference to prior context if relevant. One ask or one update — not both.
- **Closing**: matches relationship.
- **Signature**: `config.profile.preferred_name`.

Render to `workspace/Outbox/emails/<YYYYMMDD>-<recipient-slug>-<short-subject-slug>.md`:

```markdown
---
to: <email>
cc: <list>
subject: <subject>
domain: <related domain>
related_thread: <id or null>
draft_for: <user>
created: <now>
---

<body>

—
<preferred_name>
```

## 4. Run the outbound scrub pipeline

Per `procedures.md` § 13:

1. Redact internal IDs / `_memory` references.
2. Verify voice is the user's, not "Superagent thinks…".
3. Compress PII to what the recipient actually needs.
4. Confirm destination (Outbox).

## 5. Surface

```
Drafted email to <recipient> at <Outbox path>.
Subject: <subject>.
Length: <words> words.
```

Offer:

> "Open the file to review / send via your email client. (Superagent does not send on your behalf in MVP.)"

## 6. Logging

Append to `interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "draft-email"
  participants: ["<recipient>"]
  summary: "Drafted email to <recipient> re: <subject>."
  related_domain: <domain>
```

Update `contacts.yaml.<recipient>.last_contacted` only when the user explicitly confirms they sent it (via `bills mark-paid`-style "I sent it" follow-up).
