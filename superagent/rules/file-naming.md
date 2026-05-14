# File naming policy

[Do not change manually — managed by Superagent]

This rule governs how the agent names files and folders it creates anywhere under `workspace/` (or under `superagent/` for framework-author flows). It applies to every contributor (human or agent) working under the project root.

The policy is forward-only: it constrains paths the agent **creates, renames, or moves to**. It does NOT trigger retroactive renames of pre-existing paths.

---

## 1. No spaces in agent-created paths

When the agent creates, renames, or moves a file or folder anywhere under `workspace/`, the **on-disk path components** (both filenames AND directory names) MUST use **underscores (`_`)** in place of spaces.

This applies to:

- Files the agent generates (Sources/, Domains/, Projects/, Outbox/, `_memory/_artifacts/`, anywhere).
- Files the agent renames as part of a move (e.g. filing an `Inbox/` drop with a descriptive name).
- New folders the agent creates (e.g. a new institution folder under `Sources/Finances/`).
- Any path component the agent constructs from a template or skill.

It does NOT apply to:

- The human-readable `title` field inside index rows (`_memory/sources-index.yaml`, `accounts-index.yaml`, etc.) — those are display strings and may contain spaces.
- The contents of files (markdown body, YAML values).
- Path components inside URLs, vault references, or other non-filesystem identifiers.

### Concrete examples

| Don't | Do |
|---|---|
| `2026-03-06 CA Unclaimed Property Notice.pdf` | `2026-03-06_CA_Unclaimed_Property_Notice.pdf` |
| `Account Inventory.md` | `Account_Inventory.md` |
| `Sources/Finances/Bank of America/` | `Sources/Finances/Bank_of_America/` |
| `Projects/Tax 2026/` | `Projects/tax-2026/` (project slugs are kebab-case by convention; underscores are also fine) |
| `2025 W-2.pdf` | `2025_W-2.pdf` |

Hyphens (`-`) remain valid and are preferred for:

- Date separators: `2026-03-06`.
- Project / asset / domain slugs that are already kebab-case by convention: `tax-2026`, `audi-q7-2022`, `domain:health`.
- Within-word breaks where the user / convention already uses them: `W-2`, `1099-DIV`, `year-end-summary`.

---

## 2. Pre-existing spaced paths are NOT auto-renamed

The agent must NOT sweep pre-existing spaced filenames or folder names without explicit user request. Reasons:

- Silent mass renames break index `id` values in `_memory/sources-index.yaml` (id is `src-<sha1(rel_path)[:10]>`); a rename changes the id and forces a re-curation pass.
- Folder renames cascade: every file under the renamed folder gets a new path / new id; cross-references in domain `sources.md`, project `sources.md`, `world.yaml`, and `interaction-log.yaml` entries may need updating.
- The user may have stable references (shell aliases, bookmarks, links from outside the workspace) to existing paths.

The agent MAY:

- **Mention** pre-existing spaced paths in a reply when they're relevant ("the existing `Sources/Finances/Bank of America/` folder uses spaces").
- **Offer** to rename a specific subtree on request ("want me to rename the 4 files under `Sources/Finances/Schwab/Tax Forms/`?").
- **Suggest** a rename when filing a new related document into a spaced folder ("filing this to `Bank of America/`; rename that folder to `Bank_of_America/` while I'm here?").

The agent MUST NOT:

- Silently rename any pre-existing path.
- Block on the convention — if the user wants something filed to `Bank of America/` today, the agent files it there and writes a new file with underscores INSIDE that spaced folder. The rule constrains agent-created components, not the parent context.

---

## 3. Allowed character set

Beyond the no-spaces rule, agent-created path components SHOULD restrict themselves to:

- ASCII letters: `A-Z`, `a-z`.
- Digits: `0-9`.
- Underscore: `_`.
- Hyphen: `-`.
- Period: `.` (for file extensions and inside numeric versions like `0.2.0`).

Avoid in agent-created paths:

- Punctuation that requires shell quoting: `& ! @ # $ % ^ * ( ) [ ] { } | \ ; ' " < > ?`.
- Unicode characters in path components (titles in index rows are fine; the on-disk name is not).
- Trailing dots or whitespace.

When generating a path from a user-supplied string (e.g. an email subject, a contact name, a document title), the agent SHOULD slugify: lowercase, strip disallowed characters, collapse whitespace to single underscores, trim leading / trailing underscores and hyphens.

**Exception — leading-underscore convention.** The framework reserves a small set of leading-underscore names for agent-managed metadata that sits alongside user files:

| Name | Purpose |
|---|---|
| `_cache/` | Sources fetch cache (under `Sources/`). |
| `_summary.md` | Agent-generated folder summary. Captures OCR / extraction of nearby files so future skills don't re-OCR. |
| `_meta.yaml` | Cache-row metadata (under `_cache/<hash>/`). |
| `_toc.yaml` | Cache-row chunk table-of-contents (under `_cache/<hash>/`). |
| `_artifacts/` | Briefing-cache + skill working-state (under `_memory/`). |
| `_checkpoints/` | Memory snapshots (under `_memory/`). |
| `_telemetry/` | Per-turn telemetry (under `_memory/`). |
| `_custom/` | Per-user overlay (under `workspace/`). |

When slugifying or sweep-renaming, the agent MUST preserve a single leading `_` if the original name began with `_`. Trim only EXCESS leading underscores (`___foo` → `_foo`). The right-side trim of `_` and `-` continues to apply normally.

Hidden / OS files (those starting with `.`, e.g. `.DS_Store`, `.gitignore`) are not modified by any sweep.

Date-led names SHOULD use ISO 8601: `YYYY-MM-DD_<rest_of_name>`. The date prefix lets directory listings sort chronologically.

---

## 4. Verification at end of any file-creating task

Before declaring a task complete, the agent reviews its own newly-created paths for any component containing ` ` (space). If found, the agent renames before signing off and updates any index row, `_summary.md`, or cross-reference that names the path.

---

## Override / user overlay

Users may override or extend this policy at `workspace/_custom/rules/file-naming.md` (same shape). On collision, the custom file is read after the framework file and treated as additive.

Skills that generate a new path MUST cite this file and follow its conventions.
