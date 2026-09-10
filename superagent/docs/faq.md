# Superagent — FAQ

---

## Table of Contents

- [Superagent — FAQ](#superagent--faq)
  - [Why "Superagent"?](#why-superagent)
  - [Is this an app I run, or a folder I clone?](#is-this-an-app-i-run-or-a-folder-i-clone)
  - [Why markdown + YAML instead of a database?](#why-markdown--yaml-instead-of-a-database)
  - [Where does my data go?](#where-does-my-data-go)
  - [What does it cost?](#what-does-it-cost)
  - [Do I need any of the MCPs / CLI tools?](#do-i-need-any-of-the-mcps--cli-tools)
  - [What if a data source breaks?](#what-if-a-data-source-breaks)
  - [How does it compare to Notion / Obsidian / a spreadsheet?](#how-does-it-compare-to-notion--obsidian--a-spreadsheet)
  - [How does it compare to commercial AI life-managers like eeva, Kora, Okto, Alfred:Home?](#how-does-it-compare-to-commercial-ai-life-managers-like-eeva-kora-okto-alfredhome)
  - [Can my partner / family use it too?](#can-my-partnerfamily-use-it-too)
  - [What about phone access?](#what-about-phone-access)
  - [Is it secure?](#is-it-secure)
  - [What if the AI gets something wrong?](#what-if-the-ai-gets-something-wrong)
  - [Which data sources are supported?](#which-data-sources-are-supported)
  - [Can I add my own skill?](#can-i-add-my-own-skill)
  - [What's the long-term plan?](#whats-the-long-term-plan)

---

## Why "Superagent"?

The product is **Superagent**. It's designed to do for personal life what a personal-admin staff does for someone wealthy enough to have one — only the staff is one AI assistant, working from your laptop, with no salary and no human-trust trade-off.

The folders it lives in (`superagent/` for the framework code, `workspace/` for your data) are an implementation detail of this host repo. If you extract Superagent into its own repo (see `architecture.md` § "Extracting to a standalone repo"), you can rename them to anything you like.

## Is this an app I run, or a folder I clone?

A folder. There's no daemon, no server, no app to install. The framework is markdown files (instructions to the AI) + Python helpers (idempotent transforms over your data) + YAML / markdown templates. Cursor reads the folder and acts on the workspace.

This is intentional. It means:

- Zero infrastructure to maintain.
- Zero vendor lock-in (your data is plain text — open it in any editor anytime).
- Zero "the company shut down" risk.
- The model behind Cursor can be swapped out — Superagent doesn't depend on any specific one.

## Why markdown + YAML instead of a database?

Three reasons:

1. **Human readability.** When you want to know what Superagent thinks about your dentist, you can open `Domains/Health/rolodex.md` and read it. No query language, no UI, no app. Plain text.
2. **Version-control friendliness.** Even though `workspace/` is gitignored by default, you CAN put it in your own private git repo if you want history / sync / backup; markdown / YAML diff cleanly. Try diffing two SQLite files.
3. **AI friendliness.** Modern AI assistants are extremely good at reading + writing structured text. Markdown / YAML is the format they were trained on. A SQL schema would require an extra translation layer.

The trade-off: you can't run a complex JOIN over your bills.yaml and transactions.yaml in 0.5ms. But the data sizes here (thousands of rows, not millions) make that a non-issue. When something gets big enough to need indexing — like ingested transactions over years — the ingestor builds a derived index on disk and the cost stays linear in the queries you actually run.

## Where does my data go?

Local. `workspace/` is gitignored, lives on your machine, is never copied or synced anywhere by the framework. If you want to back it up to iCloud Drive / Dropbox / Syncthing / a private git repo — you do it yourself, explicitly, the same way you'd back up any other folder.

The agent does NOT phone home. There is no telemetry. No "anonymous usage data". No metrics. No crash reports. None.

## What does it cost?

The framework itself: nothing. It's a folder.

The AI: whatever your Cursor subscription costs.

The data sources: most are free for personal use. A handful charge:
- Plaid Production (real bank links): free up to a small request budget; paid tier for heavy use.
- Some MCP servers may be commercial in the future.

The CLI tools listed in `data-sources.md`: all open-source.

## Do I need any of the MCPs / CLI tools?

**No.** Quick-start works with zero ingestion. You enter bills, contacts, appointments by hand. The agent surfaces them on cadence. That's already useful.

Each data source you enable is an upgrade — Superagent now knows things you'd otherwise have to remember. The list is opt-in and reversible.

## What if a data source breaks?

Each watcher's failures are isolated. If the `gmail-bills` watcher can't reach Gmail, `simplefin` keeps working. A source that can't be reached is reported `unreachable` and **kept** — a down server is not a dead source, so it is never auto-evicted while unreachable — its `error_streak` grows in `_memory/watchlist-state.yaml`, and the next daily-update lists it under "Sources needing attention" with the last error. You fix it (re-auth, re-install, …) and the next check clears the streak.

The Supertailor's hygiene pass surfaces every watcher with `error_streak > 0` or `status: evicted`; its strategic pass proposes reauth automation or retiring the watcher if the upstream service has shut down.

## How does it compare to Notion / Obsidian / a spreadsheet?

Notion / Obsidian / spreadsheets are **canvases** — they give you tools to organize information you put in. They don't read your inbox or know your bills are due.

Superagent is a **system** — it has opinions about what to track, defaults for how to track it, and watchers that fill in the data so you don't have to. You can absolutely use Notion or Obsidian alongside Superagent (point a `path` or `cmd` watcher at the vault, or a `url` watcher at the pages you care about — you are told when your existing notes move, and the agent reads them from disk). What you can't do with a canvas is have the canvas wake you up when a bill is due.

## How does it compare to commercial AI life-managers like eeva, Kora, Okto, Alfred:Home?

The 2025-2026 wave of AI life-management apps mostly land on a similar promise: "let AI handle the household admin". Superagent's specific differences:

- **Local first, your data, your machine.** No cloud account required.
- **Open source, customizable, self-hostable.** Your skills, your overlays, your ingestors.
- **Self-improving (Supertailor / Supercoder loop).** Most commercial apps have a fixed feature set; Superagent learns from your usage and ships framework improvements you approve.
- **Heavy-import depth.** The data-source catalogue is broader than any commercial app's integration list, by design — you can plug in everything from your fitness ring to your smart home to your bank.
- **No subscription.** You pay for Cursor and that's it.

The trade-off: commercial apps have a polished mobile UI; Superagent works in your laptop's chat window. If you want a phone-only personal assistant today, eeva / Kora / Okto are better. If you want depth, customization, and ownership, Superagent is the play.

## Can my partner / family use it too?

Three options, documented in `docs/architecture.md` § "Multi-user options":

1. **Shared workspace** — copy `workspace/` to a shared cloud folder. Both users point Superagent at it. Last-write-wins; don't edit simultaneously.
2. **Federated workspace** — each user has their own workspace; symlink `Domains/Family/` and `Domains/Home/` into a shared folder.
3. **Single-user with shared snapshots** — one person runs Superagent; the partner gets a rendered `report` on demand (tax-prep, annual review).

Built-in multi-user with proper conflict resolution is on the roadmap (LOE-L).

## What about phone access?

Two ways:

1. **iCloud Drive / Dropbox sync** — put `workspace/` in a synced folder. View any file on your phone via the Files app or Drafts / Working Copy / iA Writer. Read-only is enough for "show me today's appointments while I'm out of the house".
2. **Shortcuts / quick-add tools** — capture-only flows (add a contact, log a symptom, mark a bill paid) via iOS Shortcuts that append to the right YAML / markdown. The roadmap entry "iOS Shortcut pack" tracks this.

A native mobile app is not on the MVP. The Supertailor will tell you if your usage patterns suggest you really need one.

## Is it secure?

Encryption: not built-in in MVP. The whole workspace is gitignored and lives on your machine; macOS FileVault is the default underlying encryption.

For the most sensitive subfiles (`health-records.yaml`, `accounts-index.yaml`, `Outbox/sealed/`), the recommended pattern is:
- Symlink them onto an encrypted disk image (Disk Utility → New Image, AES-256, sparse-bundle).
- Or move them into a 1Password / Bitwarden secure-note reference and let Superagent reference them by `vault_ref`.

The roadmap entry "Sensitive-store options" tracks native encryption support (LOE-M).

Credentials: never stored in plaintext in the workspace. Account credentials are referenced by `vault_ref` — a string that points at a 1Password / Bitwarden / Keychain entry. Superagent doesn't open the vault on your behalf; you do.

## What if the AI gets something wrong?

Multiple safety nets:

- **Append-only logs.** `interaction-log.yaml`, `ingestion-log.yaml`, `personal-signals.yaml`, `action-signals.yaml` never lose information; they only grow.
- **Per-row audit trail.** Every entity-shape index file (`bills.yaml`, `contacts.yaml`, …) gets a sibling `<file>.history.jsonl` written by `tools/audit.py` on every change — so "when did this row change, and to what?" is always answerable.
- **Reversible archives.** Anything `doctor` moves to `Archive/` is one `mv` away from coming back.
- **Diff-and-merge, not clobber.** Skills that update markdown files diff against your hand-edits and merge; they don't overwrite blindly.
- **The hard safeguard.** The Supertailor / Supercoder loop has a token-scan that prevents personal data from accidentally leaking into committed framework code, regardless of what the AI thinks.
- **Plain-text data.** Worst case, you open the file in any editor and fix it manually. There's nothing the agent does that you can't undo.


## Which data sources are supported?

The framework ships with its skills documented as markdown instruction sets (the catalog is `skills/_manifest.yaml`), ~30 Python tools (workspace_init, validate, render_status, log_user_query, world, sources_index, log_window, audit, events_derive, anti_patterns, home, skill_loader, icloud_dup_check, watchlist, …), and a **watchlist** of external sources (`contracts/watchlist.md`) with six shipped watcher packs: `simplefin` (bank / brokerage harvest), `gmail` (live query for new mail, capture-through into the local archive), `url` (any URL), `cmd` (the stdout of a read-only shell command; gated by `allow_cmd`), `path` (a local file or folder), and `subagent` (anything an agent can read — the escape hatch). A standalone CSV importer (`tools/ingest/csv.py --file`) covers bank statements. Setup notes: `docs/data-sources.md`.

There is deliberately **no catalogue of stubs**. A source Superagent does not know about is a one-file `url` / `cmd` / `subagent` row under `Sources/Watchlist/`, or — when it feeds a typed index and needs real normalization — a self-contained pack folder (`pack.yaml` + optional `handler.py` implementing `IngestorBase.run`) dropped into `workspace/_custom/watchers/<id>/`, no framework code touched. Sharing a watcher is copying its folder.

## Can I add my own skill?

Yes — drop a markdown file into `workspace/_custom/skills/<your-skill>.md` with the standard frontmatter (`name`, `description`, `triggers`, `mcp_required`, `mcp_optional`). The agent finds it the next turn and treats it as first-class.

If your skill turns out to be useful for everyone, the Supertailor's strategic pass may surface it as a `supertailor-suggestions.yaml` candidate to promote into the committed framework. The Supercoder will only take it if it's generic (the safeguard refuses anything with personal-data tokens).

## What's the long-term plan?

`docs/roadmap.md` has the full plan with LOE tiers (T-shirt sizes XS / S / M / L / XL). High-level shape:

- **XS / S (this quarter)**: more shipped watcher packs on the watchlist (calendar, health export) and a `csv-drop` folder watcher. Polish the daily / weekly / monthly briefings based on real usage.
- **M (next quarter)**: a cheaper `/accounts`-only detect for the bank feed. Native encryption support. iOS Shortcut pack.
- **L (next year)**: multi-user vault with proper conflict resolution. Voice-first capture (audio in, transcribe, route to the right skill). Family-mode (shared Domains, per-user private Domains). A polished read-only mobile UI.
- **XL (vision)**: a full evolution into "the personal-life equivalent of an AI engineering co-pilot — proactive, calibrated, ambient, indispensable".

Roadmap items get re-prioritized by the Supertailor's strategic pass based on actual usage friction. The framework that builds itself.
