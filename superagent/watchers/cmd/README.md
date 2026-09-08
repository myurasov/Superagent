# `cmd` — command-output watcher

Named after its detect type. Runs one shell command per eligible cycle and
reports `changed` when the sha256 of its stdout differs from the stamped
fingerprint. Exit code must be 0; a non-zero exit or a timeout is
`unreachable`, never `changed`.

## Gate

This pack executes shell from a data file. It is **off by default**: set

```yaml
preferences:
  watchlist:
    allow_cmd: true
```

in `_memory/config.yaml` or every check reports `unreachable` with the
reason `cmd disabled (preferences.watchlist.allow_cmd)`. The first `--report`
that runs a command shows the command text so it is visible in a briefing.

## Rules

- The command must be **read-only**: no writes, no sends, no upstream
  mutation. The registry is user-owned and local — the same trust tier as a
  custom skill — but this is still a new execution surface.
- The command string is taken verbatim from the ref (`watch.params.cmd`) and
  is **never** templated from anything fetched over the network.
- Prefer `min_check_interval_minutes` at or above 60 for commands that hit
  rate-limited CLIs (`gh api`, cloud CLIs).

## Example

Pack instance (the locator comes through `watch.params`):

```yaml
# Sources/Watchlist/Lab-Main-Head.ref.md  →  watch:lab-main-head
---
ref_version: 2
title: "Lab repo — main HEAD"
watch:
  pack: cmd
  params:
    cmd: "git ls-remote https://github.com/example/lab.git HEAD"
  min_check_interval_minutes: 360
---
```

Bare watcher (no pack; the locator is `watch.cmd`):

```yaml
# Sources/Watchlist/Lab-Main-Head.ref.md  →  watch:lab-main-head
---
ref_version: 2
title: "Lab repo — main HEAD"
watch:
  type: cmd
  cmd: "git ls-remote https://github.com/example/lab.git HEAD"
  min_check_interval_minutes: 360
---
```
