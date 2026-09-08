# `simplefin` watcher pack

The SimpleFIN Bridge feed, wrapped as a watcher. Contract: `contracts/watchlist.md`; handler: `superagent/watchers/simplefin/handler.py` (the normalizer stays real code — this folder only declares how and when it runs).

## Detect is the harvest call

SimpleFIN has no cheap "did anything move?" endpoint that this pack uses today, so `detect.type: harvest` — the check *is* the pull, and the handler's delta (transactions inserted or updated, balances changed) is the fingerprint. That means the "skip harvest when quiet" saving does not apply here; what the watchlist buys for this source is one lifecycle, one state file, one report, and pre-dispatch budget enforcement. A lighter `/accounts?balances-only=1` detect is a later optimization, not a day-one requirement.

## Live cadence — preserved, not widened

The pack defaults mirror the cadence the source ran on as an ingestor, and the tool is not allowed to widen them on its own:

| Setting | Value | Why |
|---|---|---|
| `schedule` / `cycles` | `weekly` / `[weekly-review]` | A bank feed does not need daily polling; the weekly bookkeeper pass is where the rows are read. |
| `capture_mode` | `manual` | A cadence `check` never dispatches this harvest. It emits a dispatch spec and the `watch` skill asks; only `harvest --id simplefin` after an explicit yes pulls. |
| `budget` | 24 calls/day, 60 min apart, 90-day window | SimpleFIN's documented ceilings. Enforced BEFORE the call; a withheld harvest is counted as `budget_exceeded` and state is left untouched. |
| `evict_after_days` | `null` | Two quiet weeks means no spending, not a dead source. |

A registry row (`Sources/Watchlist/simplefin.ref.md`) may override any of these, and the fold-in migration validates that the effective values after migration equal the pre-migration ones.

## Known incident risk: a slow `/accounts` means a bad connection

One broken institution connection degrades the **whole** feed, not just its own accounts. Observed in a live workspace: with a connection in an "auth required" state attached, `/accounts` took about 29 s or timed out at 60 s; with that connection removed from the Bridge, the same call returned in about 2.5 s with zero errors.

Treat a slow or timing-out refresh as a symptom, not as load:

1. Do not retry in a loop — every retry burns one of the 24 daily calls.
2. Open the SimpleFIN Bridge dashboard and look for a connection flagged as needing re-authentication; fix or remove it there (an upstream action the user takes — the pack never writes upstream).
3. Re-run `harvest --id simplefin` once the connection list is healthy.

Pending transactions are included by default (`include_pending: true`); re-running after they post captures the final upstream id, and rows older than `stale_pending_days` that never posted are dropped by the handler.

## Files

- `pack.yaml` — the declarative manifest.
- Credentials: `_memory/sensitive/simplefin-credentials.yaml`, written once by `uv run python superagent/watchers/simplefin/claim.py`. The pack points at it (`auth.ref`) and probes for it (`probe`); it never contains it.
