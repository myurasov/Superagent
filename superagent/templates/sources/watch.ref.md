---
# Watcher reference — one file per watcher under `Sources/Watchlist/`
# (or wherever `config.preferences.watchlist.path` points).
#
# THE FILENAME STEM IS THE ID: the state key and the handle (`watch:<id>`),
# matching `^[a-z0-9][a-z0-9_-]{0,62}$` (usual Sources naming: lowercase, `_` between words, `-` inside tokens). Renaming the file renames the
# watcher (the tool offers to carry the state row across). A watcher is also
# called an "ext-source" — same thing.
#
# Everything above the `watch:` block is an ordinary Sources reference
# (superagent/templates/sources/ref.md; contracts/sources.md § 15.3):
# `sources fetch` still resolves it and the index still lists it. The
# `watch:` block is what makes it a watcher (contracts/watchlist.md § 2).
#
# INHERITANCE RULE: a `watch.` key that is PRESENT — even as `null` — is
# set on this row. A key that is ABSENT inherits: pack default, then
# `config.preferences.watchlist`, then the built-in default. That is why
# most keys below are commented out — uncomment only what you mean to pin.

ref_version: 1

# --- What this points at ---
title: "<short title>"
description: "<one line: what changes here and why you care>"

# --- Where it lives (kind + source are REQUIRED, as for any ref) ---
kind: ""
  # url | cli | file | manual | api | mcp | vault  (meanings: see ref.md).
  # `kind` also picks the DEFAULT detect type when `watch.type` and
  # `watch.pack` are both absent:
  #   url -> url     cli -> cmd     file -> path     manual -> subagent
  # api / mcp / vault have no default: set `watch.type` or `watch.pack`.
source: ""
  # url:    the URL to watch
  # cli:    the shell command (refused until config allow_cmd: true)
  # file:   the local file or directory path
  # manual: how a human reaches it (the subagent prompt goes in `watch`)
  # api:    "gmail:<query>" for a gmail watcher; the pack id for a pack row

# --- Read-freshness for `sources fetch` ONLY. Detect never consults the cache. ---
ttl_minutes: 1440
sensitive: false

# --- Cross-references (become world.yaml edges from `watch:<id>`) ---
related_domain: ""
related_project: ""
related_asset: ""
related_account: ""

# --- Provenance ---
added_by: "user"            # user | watch | <skill-name> | <migration version>
added_at: null              # ISO 8601 datetime

tags: []

# --- The watch block (contracts/watchlist.md § 2 for every field) ---
watch:
  enabled: true             # false = paused: state frozen, never checked, never aged
  # pack: gmail             # inherit detect / harvest / probe / auth / defaults from a pack
                            #   shipped: simplefin | gmail | url | cmd | path | subagent; or a
                            #   folder under workspace/_custom/watchers/. Overrides `type`.
  # type: url               # url | path | cmd | subagent | gmail  (default from `kind`, above;
                            #   `index_query` is reserved and rejected this release)
  # status: active          # write this line to REVIVE an evicted watcher — the edit's
                            #   mtime is the signal; the next run re-arms it
  # cycles: [daily-update]  # which cadence runs may check it: daily-update | weekly-review | monthly-review
  # evict_after_days: 14    # days without a detected change before mark-only eviction;
                            #   null = never (right for an infrastructure feed such as a bank)
  # expires: "2026-12-31"   # hard end of life — evicted the day after
  # min_check_interval_minutes: 720   # throttle regardless of cycle (720 = at most twice a day)
  # schedule: weekly        # daily | weekly | monthly | manual — cadence hint; maps to `cycles`
                            #   when `cycles` is absent
  # capture_mode: manual    # automatic | manual — manual: a cadence check NEVER dispatches
                            #   this watcher's harvest; only `harvest --id <id>` after your yes
  # params:                 # values for a parameterized pack's {{name}} placeholders
  #   query: "label:Bills newer_than:30d"      # gmail
  #   url: "https://example.gov/status"        # url
  #   selector: "#status-panel"                # url (optional)
  #   prompt: "Read ... return ONE line ..."   # subagent (read-only!)
  # --- url hardening (type url / pack url) ---
  # selector: "#status-panel"       # CSS selector scoping the fingerprint to one element
  # ignore_patterns: ["\\d{2}:\\d{2}:\\d{2}"]   # regexes removed before hashing (clocks, counters, tokens)
  # min_change_interval_minutes: 1440          # a flapping page alerts at most once per window
  # --- bare (packless) watchers only; pack instances use `params` ---
  # prompt: >-              # subagent: read-only prompt asking for ONE line (the delta, or "no change")
  #   ...
  # query: "label:Bills newer_than:30d"   # gmail: search query, bounded with newer_than:
---

# Notes

<!-- Free text. Why this is watched, what a change would mean, who to tell. -->
<!-- Never parsed by the tool; shown by `list` / show. A subagent's stamped
     note lives in _memory/watchlist-state.yaml, not here. -->
