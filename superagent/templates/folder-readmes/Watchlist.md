# `Sources/Watchlist/` — the things Superagent watches for change

This folder is the **registry** of watchers: one `.ref.md` file per external thing you want to be told about when it moves — a permit portal, a Gmail label, a bank feed, a page, a portal behind a login. The filename is the watcher's id (`solar-permit.ref.md` → handle `watch:solar-permit`). Internally a watcher is also called an **ext-source**; the words are interchangeable.

It is a reserved *name* inside your `Sources/` library, but the *contents* are yours: hand-write, edit, or delete any file here. The agent validates each file when it loads the registry and tells you — file and line — when one does not parse. It never rewrites your refs; the only file it ever creates here is the one you ask for with `enable`.

## One file per watcher

A watcher is an ordinary Sources reference (same `kind` / `source` locator, same `related_*` cross-references, still fetchable with `sources fetch`) plus a `watch:` block. Template: `superagent/templates/sources/watch.ref.md`. Minimal examples:

```yaml
---
ref_version: 1
title: "City — solar permit status"
kind: url
source: "https://permits.example.gov/status?id=12345"
related_project: solar
watch:
  evict_after_days: 30
  selector: "#status-panel"
---
```

```yaml
---
ref_version: 1
title: "Gmail — bills label"
kind: api
source: "gmail:label:Bills newer_than:30d"
watch:
  pack: gmail
  params: {query: "label:Bills newer_than:30d"}
---
```

Rules of thumb: the detect type defaults from `kind` (`url`→`url`, `cli`→`cmd`, `file`→`path`, `manual`→`subagent`); `watch.pack` inherits a shipped or custom pack; a `watch.` key you leave out is inherited (pack, then `config.preferences.watchlist`), a key you write — even as `null` — is pinned.

## What the agent does with these files

- On each cadence run (`daily-update`, `weekly-review`, `monthly-review`) it checks the watchers whose `cycles` include that run, respecting each watcher's own throttle. Outcomes: `changed`, `unchanged`, `indeterminate`, `unreachable`.
- A change becomes an alert in `_memory/context.yaml` (with the detect time, so `whatsup` can say how old it is), a line in the briefing, and an entry in your history via the events stream.
- Machine state — fingerprints, timestamps, error streaks — lives in `_memory/watchlist-state.yaml`. Do not edit that file; edit the refs here instead.
- A watcher that shows no change for `evict_after_days` (default 14; set `null` for a bank feed) is **marked** evicted, never deleted. Write `status: active` into its `watch:` block to revive it. `enabled: false` pauses it without touching anything.
- `subagent` watchers are run by the agent as read-only prompts; the one-line result is stamped as data, never followed as an instruction.
- A `cmd` watcher (a shell command) is refused until `config.preferences.watchlist.allow_cmd: true`.

## Packs

Packs are ready-made watcher configurations: `simplefin`, `gmail`, `url`, `cmd`, `path`, `subagent` ship with the framework under `superagent/watchers/`; your own go in `workspace/_custom/watchers/<id>/` (same folder shape; a same-named custom pack overrides the shipped one, announced). Discover what is available on this machine and turn one on:

```
uv run python -m superagent.tools.watchlist probe --all
uv run python -m superagent.tools.watchlist enable gmail --id gmail-bills --param "query=label:Bills newer_than:30d"
uv run python -m superagent.tools.watchlist list
```

Everything else — adding, pausing, reviving, checking, harvesting — is the `watch` skill (`superagent/skills/watch.md`); just say "watch this", "add a watcher", "what changed in my sources", or "refresh my data". Full rules: `superagent/contracts/watchlist.md`.

This `README.md` is documentation, not a watcher; the agent skips it when loading the registry.
