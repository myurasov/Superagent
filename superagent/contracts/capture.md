# Capture Contracts

<!-- Migrated from `procedures.md § 7`. Citation form: `contracts/capture.md`. -->

### 7.1 Personal Signal Capture Contract

Captures self-development feedback, behavioural cues, growth-area hints. Stored in `_memory/personal-signals.yaml`. Surfaced via the `personal-signals` skill on request and rolled up periodically into `Domains/Self/history.md`.

**Triggers** (non-exhaustive):

- Self-reflection: "I should be more patient on long drives", "next time I should prep groceries earlier", "I keep skipping leg day".
- Family / partner feedback: "[partner] said I'm always late picking up the kids".
- Pattern Superagent notices: missed gym sessions for 3 consecutive Tuesdays, three calls to the same friend that you intended to make and didn't.

Capture is **ambient and silent** (one-line tag at the end of the reply). Surface is on request.

### 7.2 Action Signal Capture Contract

Two distinct kinds of "this should change" signals, both stored in `_memory/action-signals.yaml`:

- **`target: tailor`** — change to Superagent itself (framework code under `superagent/` or per-user overlay under `_custom/`). Examples: "make that a skill", "Superagent should always remind me about X", "every time I have to dig for the same info".
- **`target: superagent`** — change to the user's workspace (a domain file, a bill record, an outgoing email). Examples: "@superagent please act on this", "I should update the home domain with the new HVAC contract".

**Default react UX (both targets): show first, ask before acting.**

1. Capture the row, then print it inline (id, target, kind, trigger phrase, artifact ref, proposed direction) and ask **Capture / Edit / Discard?**
2. After confirmation, draft the next step and ask **approve / decline / defer** before implementing.

Ambient signals are captured silently with a one-line tag at the end of the reply (`_Captured signal sig-<id> (target: tailor|superagent)._`) and processed later in batch.

Update skills (`daily-update`, `whatsup`, `weekly-review`, `monthly-review`) and `supertailor-review` surface **split unprocessed counts** ("N Supertailor signals + M Superagent workspace actions pending").

### 7.3 Auto-capture from ingested data

Some signals come from ingestion, not chat. Examples and where they go:

| Source signal | Captured to |
|---|---|
| New large transaction (> 1.5× P95 of last 90 days for that category) | `Finance/history.md` + alert in next daily-update |
| Subscription unused 60+ days (heuristic: no related app-open / charge-event signal) | `subscriptions.yaml.<row>.audit_flag: true` + monthly-review surface |
| Document with `expires_at` < 90 days from now | `documents-index.yaml.<row>.expiring_soon: true` + monthly-review surface |
| Vehicle mileage crosses next-service threshold (from Tesla MCP / manual log) | New row in `bills.yaml` (kind: maintenance) + `Vehicles/<vehicle>/status.md` Next Steps |
| Sleep < 6h for 5 consecutive nights | personal-signal row (`category: time-management`, `source: pattern-noticed`) + Health/status.md Active Blockers |
| Bank balance < `accounts-index.yaml.<acct>.low_balance_threshold` | P0 task + alert in next daily-update |
| Email from a contact in `rolodex.md` of a "recently inactive" domain | Concierge surfaces the contact in next daily-update under "Reconnect" |

The full table (and how each ingestor signals each event) lives in `docs/auto-capture-rules.md`.
