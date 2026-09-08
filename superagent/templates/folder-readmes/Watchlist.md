# `Sources/Watchlist/` — the things Superagent watches for change

This folder is the **registry** of watchers: one `.ref.md` file per external thing you want to be told about when it moves — a permit portal, a Gmail label, a bank feed, a page, a portal behind a login. A `.ref.md` is a watcher definition and nothing else; document metadata elsewhere in `Sources/` is a `<doc>.<ext>.meta.md` sidecar instead. Internally a watcher is also called an **ext-source**; the words are interchangeable.

**Name your files as you like; ids are lowercase.** The `.ref.md` suffix is what makes a file a watcher — `HA.ref.md`, `ha.ref.md`, `Home_Assistant.ref.md` are all valid, and the agent keeps the name you gave it (it never renames a file of yours or warns about its casing). The files the tool writes for you are Title_Case — first letter of each `_`- or `-`-separated token capitalized: `enable --id gmail-bills` → `Gmail-Bills.ref.md`, `Simplefin.ref.md`, `Home_Assistant-Hub.ref.md`. The watcher's id is the stem lowercased (`ha`, `simplefin`, `gmail-bills`, `home_assistant-hub`) and its handle is `watch:<id>`. Files resolve case-insensitively; two files whose lowercased stems collide are a load error.

It is a reserved *name* inside your `Sources/` library, but the *contents* are yours: hand-write, edit, or delete any file here. The agent validates each file when it loads the registry and tells you — file and line — when one does not parse. It never rewrites your refs; the only file it ever creates here is the one you ask for with `enable`.

## One file per watcher

Schema `ref_version: 2`: `title`, `description`, `related_*` cross-references, `tags`, `added_by` / `added_at`, and a `watch:` block that carries both the detect settings and **what** to watch — a `url:` / `path:` / `cmd:` / `prompt:` / `query:` locator for a bare watcher, or `pack:` + `params:` for a pack instance. There is no `kind` / `source` / `ttl_minutes`. Template: `superagent/templates/sources/ref.md`. Minimal examples:

```yaml
# Solar_Permit.ref.md  →  watch:solar_permit
---
ref_version: 2
title: "City — solar permit status"
related_project: solar
watch:
  type: url
  url: "https://permits.example.gov/status?id=12345"
  selector: "#status-panel"
  evict_after_days: 30
---
```

```yaml
# Gmail-Bills.ref.md  →  watch:gmail-bills
---
ref_version: 2
title: "Gmail — bills label"
watch:
  pack: gmail
  params: {query: "label:Bills newer_than:30d"}
---
```

Rules of thumb: exactly one of `watch.pack` / `watch.type` is required; a bare watcher's locator lives inside `watch:` (`url`, `path`, `cmd`, `prompt`, or `query` — the one its `type` needs); a pack instance passes its locator through `params`; a `watch.` key you leave out is inherited (pack, then `config.preferences.watchlist`), a key you write — even as `null` — is pinned.

## What the agent does with these files

- On each cadence run (`daily-update`, `weekly-review`, `monthly-review`) it checks the watchers whose `cycles` include that run or a faster one — cycles nest, so a weekly run also covers the daily watchers and a monthly run covers everything — respecting each watcher's own throttle. Outcomes: `changed`, `unchanged`, `indeterminate`, `unreachable`.
- A change becomes an alert in `_memory/context.yaml` (with the detect time, so `whatsup` can say how old it is), a line in the briefing, and an entry in your history via the events stream.
- A watcher whose pack has a harvest handler (`simplefin`) pulls its records when it fires — daily and automatically by default, within the pack's budget (24 calls a day, an hour apart) — into a typed index such as `_memory/transactions.yaml`. "Pull it now" is `harvest --id simplefin`. Pin `capture_mode: manual` in the `watch:` block if you would rather be asked every time.
- Machine state — fingerprints, timestamps, error streaks, budget counters — lives in `_memory/watchlist-state.yaml`. Do not edit that file; edit the refs here instead.
- A watcher that shows no change for `evict_after_days` (default 14; set `null` for a bank feed) is **marked** evicted, never deleted. Write `status: active` into its `watch:` block to revive it. `enabled: false` pauses it without touching anything.
- `subagent` watchers are run by the agent as read-only prompts; the one-line result is stamped as data, never followed as an instruction.
- A `cmd` watcher (a shell command) is refused until `config.preferences.watchlist.allow_cmd: true`.
- Nothing here is fetched on demand and nothing is cached: the question a watcher answers is "did it move?", and the records a harvest pulls are read from the typed index they land in.

## Packs

Packs are ready-made watcher configurations: `simplefin`, `gmail`, `url`, `cmd`, `path`, `subagent` ship with the framework under `superagent/watchers/`; your own go in `workspace/_custom/watchers/<id>/` (same folder shape; a same-named custom pack overrides the shipped one, announced). Discover what is available on this machine and turn one on:

```
uv run python -m superagent.tools.watchlist probe                                                                    # every discovered pack (`probe --all` = every registered watcher)
uv run python -m superagent.tools.watchlist enable gmail --id gmail-bills --param "query=label:Bills newer_than:30d"   # writes Gmail-Bills.ref.md
uv run python -m superagent.tools.watchlist enable simplefin --id simplefin                                          # writes Simplefin.ref.md
uv run python -m superagent.tools.watchlist list
```

Everything else — adding, pausing, reviving, checking, harvesting — is the `watch` skill (`superagent/skills/watch.md`); just say "watch this", "add a watcher", "what changed in my sources", or "refresh my data". Full rules: `superagent/contracts/watchlist.md`.

This `README.md` is documentation, not a watcher; the agent skips it when loading the registry. Any other file here that does not end in `.ref.md` is not a watcher either — the agent warns ("not a .ref.md — not a watcher; rename to `<name>.ref.md` or move it out of the registry") and skips it.
