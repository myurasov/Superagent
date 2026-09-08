---
# Watcher definition — one file per watcher under `Sources/Watchlist/`
# (or wherever `config.preferences.watchlist.path` points).
#
# A `.ref.md` IS A WATCHER AND NOTHING ELSE (ref_version 2, since 0.20.0).
# The older reference locator (`kind` / `source` / `ttl_minutes` / `sensitive` /
# `auth_ref` / `chunk_for_large` / `normalized_at`) is gone: a file that still
# carries one of those keys is rejected on load with a pointer at the 0.20.0
# migration. Document metadata is NOT a ref — it lives in `<doc>.<ext>.meta.md`
# next to the document, anywhere under `Sources/`.
#
# FILENAME: Title_Case of the id — capitalize the first letter of every `_`-
# or `-`-delimited token: `Simplefin.ref.md`, `Home_Assistant-Hub.ref.md`,
# `Gmail-Bills.ref.md`. THE STEM LOWERCASED IS THE ID (`simplefin`,
# `home_assistant-hub`): the state key and the handle (`watch:<id>`),
# matching `^[a-z0-9][a-z0-9_-]{0,62}$`. Files resolve case-insensitively;
# two files whose lowercase stems collide are a load error. Renaming the file
# renames the watcher (the tool warns about the orphaned state row and
# offers to carry it across). A watcher is also called an "ext-source".
#
# INHERITANCE RULE: a `watch.` key that is PRESENT — even as `null` — is
# set on this row. A key that is ABSENT inherits: pack default, then
# `config.preferences.watchlist`, then the built-in default. That is why
# most keys below are commented out — uncomment only what you mean to pin.

ref_version: 2

# --- What this watches ---
title: "<short title>"
description: "<one line: what changes here and why you care>"

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
# ONE of `pack` / `type` is REQUIRED. Nothing defaults from anything else.
watch:
  enabled: true             # false = paused: state frozen, never checked, never aged
  # pack: gmail             # inherit detect / harvest / probe / auth / defaults from a pack:
                            #   shipped: simplefin | gmail | url | cmd | path | subagent; or a
                            #   folder under workspace/_custom/watchers/. Overrides `type`.
  # type: url               # a BARE (packless) watcher: url | path | cmd | subagent, with its
                            #   locator below. `gmail` and `harvest` need a pack.
                            #   (`index_query` is reserved and rejected this release)
  # --- locator of a bare watcher (one of these, matching `type`) ---
  # url: "https://example.gov/status"    # type url: the page to fetch (http/https, no credentials)
  # path: "~/Downloads/statements"       # type path: a local file or directory
  # cmd: "git ls-remote https://x HEAD"  # type cmd: the shell command (refused until
                                         #   config allow_cmd: true)
  # prompt: >-                           # type subagent: read-only prompt asking for ONE line
  #   Read ... return ONE line: the delta, or "no change".
  # query: "label:Bills newer_than:30d"  # gmail: overrides a `pack: gmail` instance's query
  # --- pack instances supply their locator through params instead ---
  # params:                 # values for a parameterized pack's {{name}} placeholders
  #   query: "label:Bills newer_than:30d"      # gmail
  #   url: "https://example.gov/status"        # url
  #   selector: "#status-panel"                # url (optional)
  #   prompt: "Read ... return ONE line ..."   # subagent (read-only!)
  #   cmd: "git ls-remote https://x HEAD"      # cmd
  #   path: "~/Downloads/statements"           # path
  # --- lifecycle ---
  # status: active          # write this line to REVIVE an evicted watcher — the edit's
                            #   mtime is the signal; the next run re-arms it
  # cycles: [daily-update]  # fastest cadence that checks it: daily-update | weekly-review | monthly-review;
                            #   cycles nest — a weekly run also checks daily watchers, monthly checks all
  # evict_after_days: 14    # days without a detected change before mark-only eviction;
                            #   null = never (right for an infrastructure feed such as a bank)
  # expires: "2026-12-31"   # hard end of life — evicted the day after
  # min_check_interval_minutes: 720   # throttle regardless of cycle (720 = at most twice a day)
  # schedule: weekly        # daily | weekly | monthly | manual — cadence hint; maps to `cycles`
                            #   when `cycles` is absent
  # capture_mode: manual    # automatic | manual — manual: a cadence check NEVER dispatches
                            #   this watcher's harvest; only `harvest --id <id>` after your yes
  # --- url hardening (type url / pack url) ---
  # selector: "#status-panel"       # CSS selector scoping the fingerprint to one element
  # ignore_patterns: ["\\d{2}:\\d{2}:\\d{2}"]   # regexes removed before hashing (clocks, counters, tokens)
  # min_change_interval_minutes: 1440          # a flapping page alerts at most once per window
---

# Notes

<!-- Free text. Why this is watched, what a change would mean, who to tell. -->
<!-- Never parsed by the tool; shown by `list` / show. A subagent's stamped
     note lives in _memory/watchlist-state.yaml, not here. -->
