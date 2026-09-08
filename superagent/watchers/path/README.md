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
- A `kind: file` ref without `watch.pack` gets this detect type by default
  (`file` -> `path`), so the pack is only needed when you want its defaults.

## Example

```yaml
# Sources/Watchlist/bank-exports.ref.md
---
ref_version: 1
title: "Bank CSV exports folder"
kind: file
source: "~/Downloads/bank-exports"
related_domain: finances
watch:
  pack: path
  params:
    path: "~/Downloads/bank-exports"
---
```
