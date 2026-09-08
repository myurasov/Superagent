# `gmail` watcher pack

A parameterized, code-free pack: one registry row per Gmail search you want to be told about. Contract: `contracts/watchlist.md`.

## How a check works

1. The tool loads the OAuth token the Gmail MCP saved at `~/.gmail-mcp/credentials.json`. Token absent → outcome `unreachable` with the setup hint; nothing else happens.
2. It runs the row's `query` **live** against the Gmail API (read-only scope; labels are never modified) — one bounded search per check.
3. Fingerprint = newest `internalDate` in the result set + the result count. A new matching message changes it → `changed`.
4. The result set passes through `superagent.tools.email.archive.maybe_capture_stubs`, so later reads of those messages are local (`contracts/email-capture.md`). Bulk fetch stays OFF — this is capture-through on a targeted query.

## Authoring rows

```
uv run python -m superagent.tools.watchlist enable gmail --id gmail-bills \
    --param "query=label:Bills newer_than:30d" --title "Gmail — bills label"
```

Bound every query with `newer_than:`; an unbounded `label:X` fingerprints the whole label and the count stops being informative once the label is large.

Defaults: checked on `daily-update`, at most once an hour (`min_check_interval_minutes: 60`), evicted after 30 quiet days (revive with `watch.status: active`; set `evict_after_days: null` for a query that is legitimately quiet for long stretches).
