# Scenarios Contract

<!-- Migrated from `procedures.md § 27`. Citation form: `contracts/scenarios.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #14. Backed by `tools/scenarios.py` and the `scenarios` skill.

**Five canned scenarios** (no generic engine in MVP):

| Scenario | Question answered |
|---|---|
| `cancel-subscriptions <ids>` | Annual savings if these subscriptions are cancelled. |
| `trial-end-impact` | Monthly increase if all currently-active trials convert. |
| `bill-shock --percent N` | Monthly impact of every bill going up by N%. |
| `balance-floor --account <id> --starting-balance <X> --days N` | When does this account dip below threshold given upcoming bills. |
| `project-overrun <project> --percent N` | New total if the project goes N% over. |

Output to stdout; optionally `--out <name>.md` writes to `Outbox/scenarios/<name>.md`.

If a scenario reveals an actionable insight, the skill offers the next step (e.g. "Want me to walk through cancelling them via `subscriptions audit`?").
