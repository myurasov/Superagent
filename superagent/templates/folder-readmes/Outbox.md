# `Outbox/` — things to send or give to someone else

The single doorway out of the workspace. **Files end up here because they're meant to leave** — to be emailed, printed and handed over, attached to a portal upload, or shared on a thumb drive.

If a file is for *your own* use (a chart, a briefing, a draft you'll keep iterating on), it does NOT belong here. That's `Resources/` (per Domain or per Project).

## What goes in `Outbox/`

- **Drafts of emails** ready to copy-paste into your email client (or hand-send via the recipient's preferred channel).
- **Printable checklists** for a contractor, doctor visit, school event.
- **Exports** for a recipient (the CSV your tax preparer wants; the spreadsheet your accountant requested; the PDF for an insurance claim).
- **The `handoff/` packet** — the "if-hit-by-a-bus" document for an executor / spouse / trusted person.
- **Briefings to share** with a partner / financial advisor / second opinion.

## What does NOT go here

| Artifact | Where it goes instead |
|---|---|
| Domain narrative (info.md / status.md / history.md / rolodex.md / sources.md) | `Domains/<domain>/` |
| Project narrative | `Projects/<project>/` |
| Memory / state files (YAML) | `_memory/` |
| Source documents — receipts, scans, vital records | `Sources/documents/<category>/` |
| External-data pointers (.ref.md) | `Sources/references/<category>/` |
| Drafts you keep working on, agent-rendered briefings for your own use, working photos / sketches | `Domains/<X>/Resources/` or `Projects/<X>/Resources/` |
| Files in transit, pending classification | `Inbox/` |

## Lifecycle (suggested sub-folders)

The convention is loose; the agent uses sub-folders to mark intent:

```
Outbox/
  drafts/                 ← in-progress; agent may revise
  staging/                ← finalized; awaiting your "send"
  sent/                   ← user marked sent; immutable thereafter
  handoff/                ← versioned snapshots of the executor packet
  emails/<recipient>/     ← per-recipient drafted emails
  contractors/<job>/      ← per-job packets
  taxes/<year>/           ← year-end packets for the tax preparer
```

You don't have to use every sub-folder. The agent creates them lazily.

## Outbound is scrubbed

Every artifact written here goes through the **outbound scrub pipeline** (per `contracts/outbound-surface.md`):

1. Internal IDs and `_memory` references redacted.
2. Voice rendered as the user, not as Superagent ("the agent thinks…" gets rewritten to first-person).
3. PII compressed to what the recipient actually needs (full account numbers stay in `Sources/`; the outbound version carries only what's required for the question).
4. Sensitive Sources entries (`sensitive: true`) are redacted unless the user explicitly opted in for this artifact.

You can trust that what's in `Outbox/` is safe to send.

## Privacy

`Outbox/` is **gitignored** along with the rest of `workspace/`. Contents stay local to this machine. Superagent never publishes anything on its own — that is always an explicit user action (you copy the file, attach it, send it).

## Hygiene

- `doctor` proposes archive of files in `sent/` older than 90 days, but never deletes — moved to `Archive/<YYYY-MM>/Outbox-sent/` for history.
- `drafts/` files older than 30 days that haven't been promoted to `staging/` get surfaced in the next monthly review with a "still relevant?" prompt.
- The `handoff/` sub-folder accumulates versioned snapshots — `monthly-review` or the explicit `handoff` skill creates new ones; old ones stay forever (they're an audit trail).
