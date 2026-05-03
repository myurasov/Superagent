# `Sources/` — your reference library

The workspace's vault for documents and pointers to external data. Three things to know:

1. **Layout is yours.** Drop files in any folder structure that makes sense to you. The agent doesn't enforce `documents/` / `references/` / `<category>/` subdirs anymore. You can use them if you like — or invent your own — or have a flat folder.
2. **Index is derived.** The structured catalogue lives at `_memory/sources-index.yaml` and is rebuilt automatically when you (or the agent) read it. Drop a file by hand from a shell, and on the next agent read it shows up in the index.
3. **Local-first.** When the agent needs the contents of a `.ref`-pointed source, it reads `_cache/` first; only goes to live MCP / API when the cache is stale or missing. `_summary.md` first, then `_toc.yaml`, then specific chunks. **Never** load the whole raw file into context unless it's small.

## Reserved names

The agent only owns two names under `Sources/`:

| Name | Purpose |
|---|---|
| `README.md` | This file. |
| `_cache/` | Agent-managed fetch cache. TTL + LRU bounded. Don't put your own files here — they'll be evicted. |

Everything else is yours.

## Documents vs references

| What you have | What to call the file | What the agent does |
|---|---|---|
| A PDF / scan / spreadsheet you own | Whatever you want — `camry-title.pdf`, `2024-tax-return.pdf`, `Resources/medical/labs.pdf` | Indexes the path; opens it directly when read. |
| A pointer to data that lives elsewhere | `<name>.ref.md` (canonical) or `<name>.ref.txt` (free-form) | Resolves the pointer through the local-first cache. |
| Both: a file + extra metadata | `<name>.<ext>` + sibling `<name>.ref.md` | Indexes the file; uses the ref for metadata + cross-references. |

## Authoring `.ref` files by hand

The canonical form is YAML frontmatter + free-text notes (template: `superagent/templates/sources/ref.md`):

```markdown
---
ref_version: 1
title: "Fidelity 401k portal"
description: "Account balances and contribution history."
kind: url
source: "https://401k.fidelity.com/dashboard"
ttl_minutes: 1440
sensitive: true
related_account: "acct-fidelity-401k"
tags: [retirement, login-required]
---

# Notes

Login flow: SSO via work email; YubiKey for the 2FA prompt.
Balance is on the dashboard top bar; contribution history is under
"Sources of money".
```

But you don't have to write that by hand. Author whatever's easiest:

```text
URL: https://401k.fidelity.com/dashboard
Title: Fidelity 401k portal
Type: url
Sensitive: yes
Notes:
  Login flow: SSO via work email; YubiKey for the 2FA prompt.
```

Or just paste a URL on the first line:

```
https://401k.fidelity.com/dashboard
```

On the **first** time the agent reads a non-canonical ref, it asks whether to normalize it (default policy is `ask`, configurable in `config.preferences.sources.normalize_policy`):

```
Normalize Sources/finance/fidelity.ref.txt?
  [r] Rewrite in place + keep .ref.txt.original backup  [recommended]
  [n] Rewrite in place, no backup
  [s] Write sibling .normalized.md, leave my file alone
  [k] Use parsed values for this read only; don't write
```

`.ref.txt` and `.ref.md` are equivalent — pick whichever extension you prefer for hand-authored files.

## How `add-source` fits

`add-source` is a convenience — it copies the file into a sensible subfolder (you confirm or override the path), writes a row to the index, and cross-links it to the relevant Domain / Project / Asset. You can skip it and just drop files in by hand; the auto-refresh will pick them up.

| Source kind | Suggested location | Auto-routed cross-refs |
|---|---|---|
| Vehicle title / registration / insurance card | `Sources/vehicles/<vehicle-slug>/` (suggested; you can override) | `Domains/Vehicles/sources.md` |
| Tax return | `Sources/taxes/<year>/` | `Domains/Finance/sources.md` (+ `Projects/tax-<year>/sources.md` if active) |
| Medical record / lab / imaging | `Sources/medical/<member>/` | `Domains/Health/sources.md` |
| Appliance manual / warranty | `Sources/warranties/<asset>/` | `Domains/Home/sources.md` |
| Will / trust / POA / advance directive | `Sources/legal/` | `Domains/Family/sources.md` AND `Domains/Finance/sources.md` |
| Insurance policy | `Sources/insurance/<policy>/` | `Domains/Finance/sources.md` |
| Mortgage / lease / deed | `Sources/property/` | `Domains/Home/sources.md` |
| School records / diplomas | `Sources/education/<member>/` | `Domains/Family/sources.md` AND/OR `Domains/Career/sources.md` |
| Pet vaccination / vet records | `Sources/pets/<pet>/` | `Domains/Pets/sources.md` |

These are suggestions, not rules. If you want all medical stuff under `Sources/health-stuff/`, the index handles it the same way.

## What `Sources/` is NOT for

- **Drafts, working files, photos-as-references, agent-generated artifacts** → `Domains/<X>/Resources/` or `Projects/<X>/Resources/`.
- **Things you're sending to someone else** → `Outbox/`.
- **Files in transit, pending classification** → `Inbox/`.

The crisp boundaries:

| Lives in | Lifetime | Mutability |
|---|---|---|
| `Sources/<your-paths>/` | indefinite (until you manually delete) | read-only to the agent |
| `Sources/_cache/` | TTL-limited (1440min default) + LRU-bounded | auto-evicted |
| `Domains/<X>/Resources/` or `Projects/<X>/Resources/` | as long as the entity is active; archived with it | hand-managed |
| `Outbox/` | until you mark sent OR send manually | append + mark; rarely deleted |
| `Inbox/` | transient (~14 days max) | drained regularly |

## Caching policy

- **Default TTL**: 1440 minutes (24 hours) per fetch.
- **Per-source TTL**: set in the `.ref` file frontmatter (`ttl_minutes`).
- **Eviction**: `_cache/` is bounded by `_memory/config.yaml.preferences.sources.cache_max_mb` (default 500 MB). When the cap is exceeded, LRU rows are evicted.
- **Cache location**: defaults to `Sources/_cache/`; override via `config.preferences.sources.cache_path` to point at an external/encrypted volume.
- **Force refresh**: pass `--refresh` to any skill that reads sources, or run `sources refresh <ref-id>` directly.
- **Force never-cache**: set `ttl_minutes: 0` in the ref file.

## Sensitive sources

Set `sensitive: true` in the ref's frontmatter (or in the index row) for medical records, account statements, legal documents. The cached content lives in `_cache/` like everything else, but:

- Outbound surfaces (`draft-email`, `summarize-thread`, anything that lands in `Outbox/`) redact the content.
- When the sensitive-store option is enabled (roadmap M-01), the cache is encrypted-at-rest.
- The `handoff` skill picks up sensitive sources by default; non-sensitive ones are opt-in.

## Privacy

`Sources/` is gitignored along with the rest of `workspace/`. It stays local. Superagent never publishes anything on its own — that is always an explicit user action.
