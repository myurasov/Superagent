# Email Capture Contract

<!-- Citation form: `contracts/email-capture.md`. -->

Every email the agent **reads** via the Gmail MCP (`mcp_user-gmail_read_email`) or **sends** via `mcp_user-gmail_send_email` is mirrored to a local per-message archive under `workspace/_memory/email/`. The archive is the **primary source of truth for any email the agent has already touched**; the live Gmail MCP is the source of truth only for messages the agent has not yet read in this workspace.

The archive is grown by **side-effect of normal work**, not by bulk backfill. There is no auto-sweep; the existing Gmail ingestor (`superagent/tools/ingest/gmail.py`) stays dormant unless the user explicitly opts in. The contract is a **capture-on-touch** discipline, modeled on the local-archive pattern in the Co-SA workspace but simplified (no SQLite FTS, no scheduled sync).

## 1. Layout

```
workspace/_memory/email/
  _index.yaml                    # singleton: schema_version, attachments mode, counts, last_capture
  _messages.jsonl                # append-only sidecar; one line per Gmail message.id; latest-wins
  <YYYY>/<MM>/<DD>/
    <YYYY-MM-DD>_<in|out>_<from_slug>_<subject_slug>_<hash8>.json
  attachments/                   # lazy; only created on first save
    <sha256_8>_<safe_filename>
```

Filenames follow `rules/file-naming.md`: ISO date with hyphens; underscores between fields; slug components are lowercase alphanumeric + underscore; `hash8` is the first 8 hex chars of `sha256(message_id)`.

## 2. Sidecar record schema (`_messages.jsonl`)

One JSON object per line. Fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Gmail `message.id` — the dedup key |
| `thread_id` | string | Gmail `threadId` |
| `path` | string | `_memory/email/`-relative path to the per-message JSON |
| `hash` | string | First 16 hex of `sha256(id)` (filename uses first 8) |
| `kind` | `"stub" \| "full"` | `stub` = metadata only from `search_emails`; `full` = body present |
| `direction` | `"in" \| "out"` | Inferred from `labelIds` (`SENT` → out) or from the calling skill |
| `from` | string | Verbatim `From` header |
| `to` | list[string] | Parsed `To` recipients |
| `cc` / `bcc` | list[string] | Parsed `Cc` / `Bcc` |
| `subject` | string | Verbatim `Subject` header |
| `internal_date_utc` | string | ISO 8601 UTC, derived from Gmail `internalDate` (epoch ms) |
| `labels` | list[string] | Gmail `labelIds` |
| `snippet` | string | Gmail `snippet` |
| `has_attachments` | bool | `True` if any `payload.parts[*].filename` is non-empty |
| `attachments_saved` | int | Count of attachment blobs saved under `attachments/` |
| `captured_at` | string | ISO 8601 UTC when this record was written |
| `provenance` | object | Per `contracts/provenance.md` — `source`, `at`, optional `source_id` |

Latest-wins on dedup: when a record for a given `id` is re-appended, the **last** line in the file is the current state. Optional compaction can squash older lines later; it is not required for correctness.

## 3. When to capture

The agent MUST capture a message through `superagent/tools/email/archive.py` immediately after:

- A successful `mcp_user-gmail_read_email` call → `capture_inbound(raw_message)`. Direction is inferred from the message's `labelIds` (presence of `SENT` → `direction="out"`).
- A successful `mcp_user-gmail_send_email` call → `capture_sent(request_args, response)`. The request body (text and/or HTML) is preserved verbatim as the message content even when Gmail's response is minimal (`{messageId, threadId}`).

The agent MAY capture stub records when:

- A `mcp_user-gmail_search_emails` call returned metadata for messages that are not yet in the archive → `maybe_capture_stubs(results)`. Stubs are cheap and pay for themselves the next time the user asks about the same thread. A stub is upgraded to `full` on the next `read_email` for that id.

The agent MUST NOT:

- Capture from `mcp_user-gmail_draft_email` (unsent drafts stay in `workspace/Outbox/emails/` per the `draft-email` skill).
- Capture from any `modify_email`, `delete_email`, label, or filter tool. Those mutate Gmail state; mirroring them belongs in `upstream-writes.yaml`, not here.
- Bulk-fetch beyond the message ids the current task actually needs. There is no scheduled sync. The capture-on-touch loop is the only path.

## 4. Idempotency

Keyed by Gmail `message.id`:

- **New id** → write the per-message JSON, append a sidecar row, bump `_index.yaml.counts`.
- **stub → full** → overwrite the per-message JSON in place, append a new sidecar row with `kind: "full"` (latest-wins).
- **stub → stub** → no-op when nothing visible changed (same labels, same snippet). On change, append a fresh sidecar row.
- **full → stub** → no-op (never downgrade).
- **full → full** → no-op unless one of `attachments_saved` (the count, on save) or `labels` (e.g. read/unread flip) changed; on change, append sidecar-only update (do not rewrite the JSON unless the body itself changed).

The dedup pivot is the in-memory map built from the sidecar at call time; no separate state file.

## 5. Attachment policy

The default is **metadata-only**: the per-message JSON keeps every `payload.parts[*]` entry (with `filename`, `mimeType`, `size`, and Gmail `attachmentId`), but the bytes are not pulled.

Save the bytes only when ONE of:

1. **The user explicitly asks** ("save the receipt", "keep that PDF").
2. **The message looks like a receipt or confirmation.** Subject-line heuristic (case-insensitive, word-boundary): `\b(receipt|invoice|order|payment|confirmation|booking|reservation|statement|ticket|registration|paid)\b`. Or a known receipt-sender domain (`stripe.com`, `paypal.com`, `*-receipts@*`).
3. **The attachment is the primary data the current task needs to act on.** Agent judgment; the saved record stores the agent's `reason` so the call is auditable.

Saved bytes go to `workspace/_memory/email/attachments/<sha256_8>_<safe_filename>` (SHA-256 of content for cross-message dedup). Skip files larger than 25 MB with a note in the sidecar. On save, the per-message JSON's matching `payload.parts[*]` entry gets a `saved_to: "attachments/..."` field, and the sidecar `attachments_saved` count increments.

Long-lived auditable payments (a receipt the user will actually rely on later) belong in `Sources/<Domain>/` per `contracts/payment-confirmations.md`; the email archive is the **first** stop, not the final home. Promotion to `Sources/` is a separate, explicit user action.

## 6. Local-first read order

Before any new `mcp_user-gmail_read_email` or `mcp_user-gmail_search_emails` call, the skill MUST scan `_memory/email/_messages.jsonl` for the message id, sender, or subject substring. The helper `superagent.tools.email.archive.find` / `find_by_query` does this without parsing the per-message JSONs. The live MCP is consulted only when the local scan returned no candidates AND freshness genuinely matters for the question, per `contracts/local-first-read-order.md`.

## 7. Privacy and sensitive handling

The `_memory/email/` tree is local-only (gitignored under `workspace/`). It is **not** auto-routed into `_memory/sensitive/` because the archive is too broad for the sensitive-tier defaults; per-message sensitivity is handled by Gmail labels the user already applies (e.g. a custom `Confidential` label flows into the sidecar's `labels` field and downstream skills can branch on it).

Outbound scrubbing (per `contracts/outbound-surface.md`) treats anything pulled from the archive as private by default — the agent never quotes a captured email into a draft to a different recipient without explicit user consent.

## 8. Helpers

A single module backs the contract: `superagent/tools/email/archive.py`. It is **pure** — no MCP calls happen inside. Skills call MCP, then hand the result to the helper.

```python
from superagent.tools.email import archive

archive.capture_inbound(raw_message, workspace=ws)
archive.capture_sent(request_args, response, workspace=ws)
archive.maybe_capture_stubs(search_results, workspace=ws)
archive.save_attachment(message_id, attachment_id, filename, content_bytes, reason, workspace=ws)
archive.find(message_id, workspace=ws)
archive.find_by_query(workspace=ws, from_substr=..., subject_substr=..., since=..., until=..., limit=...)
```

CLI for inspection (no MCP, no fetching):

```bash
uv run python -m superagent.tools.email.archive find <message-id>
uv run python -m superagent.tools.email.archive query [--from PAT] [--subject PAT] [--since DATE] [--until DATE] [--limit N]
uv run python -m superagent.tools.email.archive stats
```

## 9. Relationship to the Gmail ingestor

The pre-existing Gmail ingestor (`superagent/tools/ingest/gmail.py`) writes a separate, metadata-only stream under `workspace/_memory/_gmail/<YYYY-MM>.jsonl`. It is unrelated to this archive and stays **dormant** unless the user explicitly enables its row in `data-sources.yaml` and runs `ingest gmail`. The two paths can coexist; the email archive is the live one for chat-time work.

## 10. Out of scope (for now)

- SQLite FTS index over the archive (Co-SA's `archive-query.py` pattern). Linear scans of `_messages.jsonl` are fine at the volume on-touch capture produces.
- Bulk Gmail backfill of historical messages.
- Outlook / Apple Mail / iCloud Mail MCPs (additional capture contracts can mirror this one source-by-source).
- Calendar-invite (`.ics`) extraction into `appointments.yaml`.
- Thread-level summary files. Threads remain a JOIN-on-`thread_id` over the sidecar.
- Auto-promotion of receipts to `Sources/<Domain>/`. The `save_attachment` helper marks candidates; promotion stays an explicit user action.
