# Privacy and Sensitive Data

<!-- Citation form: `contracts/privacy.md`. Canonical text; `AGENTS.md` § "Privacy and data location" carries the binding one-line summary of each rule. -->

## 1. Data location

- **`workspace/`** (including everything under it) is **gitignored** and **local only** to this machine unless the user copies or syncs it themselves. Do not assume it is backed up or shared anywhere. No agent-initiated upload, sync, or telemetry.
- Framework code under `superagent/` is committed and may be published; it therefore carries **no personal data** — the Framework Artifact Creation Contract (`contracts/framework-artifacts.md`) and the Supertailor / Supercoder safeguard (§ 5) enforce this.

## 2. No telemetry

Superagent does not phone home. No metrics, no crash reports, no "anonymous usage data" — any such mechanism would be a major-version change explicitly proposed in `docs/roadmap.md`, opt-in only, and clearly labelled.

## 3. No remote write

Superagent watchers and harvests are read-only against upstream sources by default (`contracts/watchlist.md`, `contracts/ingestion.md`). Any skill that intends to *write* upstream (e.g. a future "auto-pay this bill" or "create this calendar event in Google Calendar") must:

1. declare it loudly in its frontmatter (`writes_upstream: true`);
2. ask for confirmation **per call**; and
3. log the write to `_memory/interaction-log.yaml` (and `_memory/upstream-writes.yaml`).

## 4. Sensitive subfiles

`_memory/health-records.yaml` and `_memory/accounts-index.yaml` are the most sensitive items in the workspace, together with anything the user routes into `_memory/sensitive/` (`contracts/sensitive-tier.md`) and the sealed sub-tree of the Outbox (`Outbox/sealed/`, per `contracts/outbox-lifecycle.md`). They live alongside everything else (no separate encrypted store in MVP) but are explicitly called out — and surfaced in `docs/architecture.md` § "Sensitive subfiles" — so the user can choose to symlink them to an encrypted disk image, a 1Password / Bitwarden secure-note reference, or a Vault-style backend later. `docs/roadmap.md` M-01 "Sensitive-store options" tracks first-class encryption support.

- Skills that *produce* a sensitive artifact write it to a clearly-labelled sealed subfolder and surface the location in chat with a "store this somewhere safe" reminder.
- Skills that *consume* sensitive data (medic prep brief, bookkeeper tax packet) include a "do not paste this into a chat assistant unless you trust it" banner at the top of the rendered output.
- Outbound artifacts respect each entity's `visibility` field (`contracts/visibility.md`); the outbound scrub (`contracts/outbound-surface.md`) redacts `private` content.

## 5. Safeguard on framework-bound writes

The Supertailor / Supercoder safeguard scans every framework-bound write for names from `_memory/contacts.yaml`, `domains-index.yaml`, `assets-index.yaml`, `accounts-index.yaml`, address fragments, account-number patterns, and license-plate patterns. On any match, the destination is forcibly re-routed to `workspace/_custom/` regardless of the original tag. Commit messages never mention anything personally identifying, household-specific, or account-specific (`rules/git-commits.md`).

## 6. Sharing the workspace

If the user wants their partner / household to share Superagent state, the supported approaches are documented in `docs/architecture.md` § "Multi-user options". TL;DR: copy the workspace folder to a shared iCloud Drive / Dropbox / Syncthing folder. There is no built-in multi-tenancy in MVP.

## 7. Host-IDE memory stores

"Remember this" content never leaves the repo: it goes to `workspace/_memory/` or `workspace/_custom/`, never to a host-IDE memory feature outside the repo root (`rules/memory-routing.md`). `tools/memory_routing_check.py` detects violations read-only.
