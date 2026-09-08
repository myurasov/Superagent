# `path` — local file or folder watcher

Named after its detect type. Fingerprints a local path and reports `changed`
when it moves:

| Target | Fingerprint |
|---|---|
| file | sha256 of the content |
| directory | newest modification time + recursive entry count |

A missing path is `unreachable`, never `changed`, so a temporarily unmounted
volume cannot evict the watcher.

## Rules

- Local reads only. No network, no shell.
- Do not watch a file that a harvest handler writes (for example
  `_memory/transactions.yaml`): the watcher would fire on the harvest's own
  write and look like a real signal.
- A bare `type: path` watcher (no `watch.pack`) uses the same detect; the
  pack is only needed when you want its defaults.

## Example

Pack instance (the locator comes through `watch.params`):

```yaml
# Sources/Watchlist/Bank-Exports.ref.md  →  watch:bank-exports
---
ref_version: 2
title: "Bank CSV exports folder"
related_domain: finances
watch:
  pack: path
  params:
    path: "~/Downloads/bank-exports"
---
```

Bare watcher (no pack; the locator is `watch.path`):

```yaml
# Sources/Watchlist/Bank-Exports.ref.md  →  watch:bank-exports
---
ref_version: 2
title: "Bank CSV exports folder"
related_domain: finances
watch:
  type: path
  path: "~/Downloads/bank-exports"
---
```
