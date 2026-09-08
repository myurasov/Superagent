# `url` watcher pack

Generic change detection on one URL. Contract: `contracts/watchlist.md` (§ 5 for the `url` hardening rules).

## Authoring rows

```
uv run python -m superagent.tools.watchlist enable url --id solar-permit \
    --param "url=https://permits.example.gov/status?id=12345" \
    --param "selector=#status-panel" --title "City — solar permit status"
```

Then tune the row's `watch:` block by hand when a page flaps:

- `selector` — scope the fingerprint to the element that matters. A selector that matches nothing reports `unreachable` with a note, so a site redesign is visible rather than silent.
- `ignore_patterns` — regexes removed before hashing (view counters, "last updated" clocks, CSRF tokens).
- `min_change_interval_minutes` — a page that changes on every fetch alerts at most once per window.

Default cadence: `daily-update`, at most twice a day (`min_check_interval_minutes: 720`), evicted after 30 quiet days. Page fetches are read-only GETs with a 2 MB body cap and a bounded redirect chain; nothing is submitted.
