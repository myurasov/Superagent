# Proposal — Make Superagent fully Claude-Code-compatible

A design + implementation plan for adding **first-class Claude Code support** to Superagent, modeled on (but smaller than) the work that landed in the sibling NV-Co-SA framework as version `1.35.0`. The end-state: Superagent runs identically well under Cursor or Claude Code; the user picks the IDE; the framework loads, the hooks fire, the MCPs connect.

This document is **diagnostic + plan**, not a roadmap commitment. The Supertailor's strategic pass picks items off the shelf when matching friction shows up.

---

## Table of Contents

- [Proposal — Make Superagent fully Claude-Code-compatible](#proposal--make-superagent-fully-claude-code-compatible)
  - [Goal and non-goals](#goal-and-non-goals)
  - [Reference — what NV-Co-SA shipped in 1.35.0](#reference--what-nv-co-sa-shipped-in-1350)
  - [Where Superagent stands today](#where-superagent-stands-today)
  - [What "fully Claude-compatible" means for Superagent](#what-fully-claude-compatible-means-for-superagent)
  - [Proposed changes](#proposed-changes)
    - [A. `CLAUDE.md` shim at the repo root](#a-claudemd-shim-at-the-repo-root)
    - [B. `.claude/` directory with `settings.json` hook wiring](#b-claude-directory-with-settingsjson-hook-wiring)
    - [C. `.claudeignore` to avoid duplicate ambient context](#c-claudeignore-to-avoid-duplicate-ambient-context)
    - [D. Per-IDE MCP config templates and runtime copies](#d-per-ide-mcp-config-templates-and-runtime-copies)
    - [E. IDE-detection helper](#e-ide-detection-helper)
    - [F. AGENTS.md updates](#f-agentsmd-updates)
    - [G. `init` skill — step 12½ and 12¾](#g-init-skill--step-12-and-12)
    - [H. `.gitignore` updates](#h-gitignore-updates)
  - [Explicitly NOT in scope](#explicitly-not-in-scope)
    - [No batch `auth-mcps` skill](#no-batch-auth-mcps-skill)
    - [No cost-tracking subsystem in this release](#no-cost-tracking-subsystem-in-this-release)
    - [No cross-IDE token re-extraction](#no-cross-ide-token-re-extraction)
  - [Migration plan — `0.5.0`](#migration-plan--050)
  - [Roadmap entry](#roadmap-entry)
  - [Effort estimate](#effort-estimate)
  - [Open questions](#open-questions)
  - [Implementation order](#implementation-order)

---

## Goal and non-goals

**Goal.** A user who clones the Superagent repo can use it identically well from **Claude Code** or **Cursor**. Same skills run, same hooks fire, same MCPs connect, same workspace layout, no surprise edge cases.

**Non-goals.**

- No new ingestors. Claude support is a host-IDE concern, orthogonal to the data layer.
- No cost-tracking subsystem. Superagent has none today for Cursor; adding one is a separate feature.
- No migration of personal data shape. Workspace files stay byte-identical.
- No Anthropic-API direct integration. The "future Superagent CLI" mentioned in `AGENTS.md` § "Prompt-cache discipline" stays a future item.

---

## Reference — what NV-Co-SA shipped in 1.35.0

NV-Co-SA's `1.35.0` release ("first-class Claude Code support") landed nine changes. Listed for context — most have a Superagent analog, a few do not:

1. `CLAUDE.md` at the repo root with `@AGENTS.md` import directive — gives Claude Code the same instructions Cursor reads natively.
2. `.claude/settings.json` for Claude Code project settings (hook wiring).
3. `.claudeignore` to skip `.cursor/` so Claude Code doesn't load duplicate ambient context.
4. Per-IDE MCP config templates: `.mcp.json.claude` and `.cursor/mcp.json.cursor`, each pre-pinning the right ECI-allowlisted `client_id` for body-data MCPs.
5. `init.md` rewritten to copy each template into its runtime path (`.mcp.json` for Claude Code; `.cursor/mcp.json` for Cursor) as a regular-file copy — explicitly NOT a symlink, because iCloud sync mangles symlinks across machines.
6. New `auth-mcps` skill that batch-OAuths every HTTP MCP under Claude Code in one fan-out, then health-checks them in parallel; Cursor branch just points at Settings → MCP.
7. `maas_mcp.py` extended to also read tokens from Claude Code's `Claude Code-credentials` Keychain entry, with per-source `client_id` for refresh-token grants.
8. Per-turn cost-tracking `Stop` hook documented as opt-in for Claude Code (off by default; Claude Code surfaces `/cost` natively).
9. Marker migration `1.35.0.md` bumping `workspace/.version` (no schema changes).

Items 1-5 + 9 are directly applicable to Superagent. Items 6-7 are NVIDIA-MaaS-specific and have no Superagent analog. Item 8 is a follow-up feature, not a Claude-compatibility blocker.

---

## Where Superagent stands today

Snapshot of the relevant moving parts in `0.4.0`:

| Path | Role | IDE-coupled? |
|---|---|---|
| `AGENTS.md` | Canonical always-on instructions | Cursor reads natively; Claude Code needs a shim |
| `.cursor/hooks.json` | Wires `UserPromptSubmit` → `tools/log_user_query.py` | Cursor-only |
| `.cursor/mcp.json` | Project-level MCP config (Playwright only) — committed | Cursor reads this path |
| `.mcp.json.example` | Single template for any IDE | Generic, but no per-IDE pinning |
| `.gitignore` | Allows `.cursor/hooks.json` + `.mcp.json.example` through; ignores `.mcp.json` | Cursor-shaped |
| `.githooks/commit-msg` | AI-attribution scrub | IDE-agnostic |
| `superagent/skills/init.md` | Scaffolds the workspace | Mentions Cursor only |
| `AGENTS.md` § "IDE setup (Cursor)" | One-paragraph aside | Cursor-specific |
| `AGENTS.md` § "Prompt-cache discipline" | Caching advice | Phrased Cursor-only ("Cursor's prompt cache rewards a stable prefix") |

**No `CLAUDE.md`, no `.claude/` directory, no `.claudeignore`, no IDE-detection helper.** Running Superagent under Claude Code today works partially (Claude Code reads `AGENTS.md` opportunistically through workspace context) but is undocumented and missing the hook integration.

The repo lives in **iCloud Drive** (`~/Library/Mobile Documents/com~apple~CloudDocs/MY-Superagent`). Per NV-Co-SA's lessons, this rules out the symlink approach for `.mcp.json` ↔ `.cursor/mcp.json` — iCloud occasionally rewrites symlinks as placeholders. We need regular-file copies.

---

## What "fully Claude-compatible" means for Superagent

Concretely, after this lands:

1. Claude Code recognizes the project on first open and loads `AGENTS.md` through a `CLAUDE.md` shim.
2. The `UserPromptSubmit` hook fires under either IDE, writing to the same `workspace/_memory/user-queries.jsonl`.
3. The MCP servers in `.mcp.json` / `.cursor/mcp.json` resolve under either IDE with no manual reconfiguration.
4. The framework documentation (AGENTS.md, init.md, the rules tree) names both IDEs symmetrically; nothing says "Cursor is primary, Claude is afterthought" or vice versa.
5. The user can switch IDEs mid-week without re-running `init`.
6. `workspace/.version` lands at `0.5.0` after the migration.

What it does NOT mean:

- A batch MCP-auth skill (Superagent's MCPs auth heterogeneously per-ingestor — there's no equivalent of NV-Co-SA's one-gateway OAuth flow to batch).
- Cost tracking (Superagent has none today; out of scope).
- Cross-IDE token sharing (Superagent's MCPs each manage their own auth; no shared token cache).

---

## Proposed changes

Eight discrete edits, listed in implementation order. Each is small enough to land as its own commit.

### A. `CLAUDE.md` shim at the repo root

**New file:** `CLAUDE.md` (~10 lines).

Claude Code reads `CLAUDE.md` from the repo root on every turn and supports the `@<path>` import directive to pull in additional files. The shim's only job: tell Claude Code to read `AGENTS.md` and forward all the rules.

Reference content (Superagent-adapted):

```markdown
# Superagent — Claude Code loader

Claude Code reads this file on every turn. Superagent's canonical
always-on instructions live in `AGENTS.md`; import them here so a
single document drives behavior under both Cursor and Claude Code.

@AGENTS.md
```

**Properties:**

- Committed.
- Plain markdown — no frontmatter, no Cursor-specific cascade directives.
- Reads as a normal doc file under Cursor (Cursor ignores `@` imports outside `.cursor/rules/`).
- Adds zero ambient-context cost for Cursor users.

### B. `.claude/` directory with `settings.json` hook wiring

**New file:** `.claude/settings.json` (~15 lines).

Claude Code's project-settings format. Wires the same `UserPromptSubmit` hook Cursor already runs, so the Supertailor's friction-analysis log (`workspace/_memory/user-queries.jsonl`) captures prompts under either IDE.

Reference content:

```json
{
  "_comment": [
    "Claude Code project settings. Wires the UserPromptSubmit hook to",
    "Superagent's user-query logger so the Supertailor's friction-analysis",
    "log captures prompts regardless of IDE. Mirrors .cursor/hooks.json.",
    "Disable by setting workspace/_memory/config.yaml",
    "preferences.privacy.log_user_queries: false."
  ],
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run python superagent/tools/log_user_query.py"
          }
        ]
      }
    ]
  }
}
```

**Properties:**

- Committed.
- `tools/log_user_query.py` MUST already tolerate being invoked from either IDE — the script reads from stdin; it does not depend on `CURSOR_*` env vars. Verify this in step A of the implementation (it likely already does).
- No `Stop` hook (cost tracking not in scope this release).
- `.claude/settings.local.json` stays gitignored — that's Claude Code's per-machine override; let users keep it untracked.

### C. `.claudeignore` to avoid duplicate ambient context

**New file:** `.claudeignore` (~10 lines).

Same syntax as `.gitignore`. Without it, Claude Code's ambient-context loader pulls in `.cursor/rules/` (which mirrors `AGENTS.md` for Cursor) and any Cursor-only shim files, paying for them in every turn's prompt.

Reference content:

```gitignore
# Files Claude Code should skip for context, indexing, and ambient loading.
# Same syntax as .gitignore.

# Cursor IDE shim tree. Claude Code reads CLAUDE.md (which imports AGENTS.md)
# already; loading .cursor/rules/ on top would duplicate the same instructions
# in every turn's prompt for no gain. The files stay in the repo for Cursor
# users; Claude Code should disregard them.
.cursor/

# Workspace data — covered by .gitignore but listed here too as a belt-and-
# braces signal that the personal-data tree is off-limits as ambient context.
workspace/

# Notes:
# - AGENTS.md is shared and intentionally loaded by both IDEs (via CLAUDE.md
#   for Claude Code; natively for Cursor); do NOT add it here.
# - .mcp.json carries auth tokens and is already gitignored; not listed here.
```

**Properties:**

- Committed.
- Small enough that maintenance is trivial.
- Add patterns as new Cursor-only files are introduced.

### D. Per-IDE MCP config templates and runtime copies

**Renamed/new files:**

- `.mcp.json.example` → `.mcp.json.claude` (rename + light edits; this becomes Claude Code's template).
- `.cursor/mcp.json.cursor` (new; committed; this becomes Cursor's template).

**Behavior change:**

- `init` copies `.mcp.json.claude` → `.mcp.json` if missing (Claude Code's runtime path).
- `init` copies `.cursor/mcp.json.cursor` → `.cursor/mcp.json` if missing (Cursor's runtime path).
- Both runtime files are **gitignored**.
- **Regular-file copies, NOT symlinks** — iCloud sync mangles symlinks. NV-Co-SA learned this the hard way; Superagent inherits the lesson.

**Content of each template** — Superagent's MCPs are simple (just Playwright today). Both templates can be byte-identical except for the `_comment` block. Per-IDE `client_id` pinning is NOT needed (Superagent has no NVIDIA ECI body-data allowlist to satisfy).

Reference content for `.mcp.json.claude`:

```json
{
  "_comment": [
    "Starting-point MCP server config for Claude Code. Init copies this",
    "to .mcp.json on first run if .mcp.json is missing. After init, edit",
    ".mcp.json (not this template) and add any servers you want.",
    "",
    "Cursor users: see the companion template at .cursor/mcp.json.cursor.",
    "Content is intentionally identical (Superagent has no per-IDE OAuth",
    "client_id constraint, unlike NVIDIA-internal frameworks); only the",
    "destination path differs.",
    "",
    "Superagent itself requires no MCP servers for the quick-start. Each",
    "ingestor declares its own MCP / CLI requirements; see",
    "superagent/docs/data-sources.md."
  ],
  "mcpServers": {
    "Playwright": {
      "command": "npx",
      "args": [
        "@playwright/mcp@latest",
        "--user-data-dir",
        ".playwright-superagent",
        "--caps",
        "vision,pdf,devtools"
      ]
    }
  }
}
```

`.cursor/mcp.json.cursor` is the same JSON shape with the `_comment` block adjusted to name Cursor as the IDE.

**Sync convention.** `.mcp.json` and `.cursor/mcp.json` carry identical content. When a user hand-edits one (adds a new MCP) without mirroring to the other, `init` (if re-run) and an optional `tools/mcp_sync.py` helper will detect the drift and offer to fix. The simpler convention: edit `.mcp.json` (canonical for cross-IDE), then run a tiny `tools/mcp_sync.py copy-to-cursor` to mirror. The helper is a nice-to-have, not required for the first release — the `init` re-run detection is enough.

### E. IDE-detection helper

**New file:** `superagent/tools/ide.py` (~50 lines).

Tiny library other skills + scripts use to know which IDE is running. Driven by environment variables, with a single override in `_memory/config.yaml`.

Detection logic (in priority order):

1. `_memory/config.yaml.preferences.ide` — hard override (`claude-code` or `cursor`).
2. `CLAUDECODE=1` in the environment → Claude Code.
3. Any `CURSOR_*` env var set (`CURSOR_TRACE_ID`, `CURSOR_SESSION_ID`, ...) → Cursor.
4. Both / neither → unknown.

API surface:

```python
from superagent.tools.ide import detect, IDE

ide = detect()  # IDE.CLAUDE_CODE | IDE.CURSOR | IDE.UNKNOWN
```

The CLI sub-command `uv run python -m superagent.tools.ide current` prints the detected name. Useful for shell snippets and for the `init` skill's branching logic.

**Why a helper instead of inline checks.** A single library function keeps the detection logic in one place. When Anthropic / Cursor change their env var conventions, we fix one file.

### F. AGENTS.md updates

Three sections need edits. All preserve the existing structure; nothing is renamed at the heading level.

**§ "IDE setup (Cursor)" → "IDE setup (Cursor and Claude Code)".**

Replace the current paragraph with a short symmetric description: the framework targets both IDEs equally; Cursor reads `AGENTS.md` natively; Claude Code reads `CLAUDE.md` which `@`-imports `AGENTS.md`. Document both hook integrations (the existing `.cursor/hooks.json` plus the new `.claude/settings.json`) in one block. Reference `tools/ide.py` for detection.

**§ "Prompt-cache discipline".**

Reword every Cursor-specific sentence to apply to both IDEs (Claude Code also caches a stable prefix). The advice itself ("don't edit AGENTS.md mid-session, don't open many framework files mid-session") works for both IDEs without modification.

**§ "Custom overlay" — minor mention.**

Add a one-liner: `workspace/_custom/` overlay applies under both IDEs identically; no Claude-Code-specific overlay path exists.

### G. `init` skill — step 12½ and 12¾

Insert two new steps between current step 11 (folder scaffolding) and step 12 (questionnaire-driven config writes). Order parallel to NV-Co-SA's structure for consistency.

**Step 12½. IDE-specific support files.**

If missing, create:

- `CLAUDE.md` (idempotent — leave alone if it exists; user may have customized it).
- `.claude/settings.json` (idempotent — leave alone if it exists).
- `.claudeignore` (idempotent — leave alone if it exists).
- `.cursor/hooks.json` (idempotent — already there in shipped repos).

**Step 12¾. MCP config bootstrap.**

For each IDE's runtime config:

- If `.mcp.json` does not exist: copy `.mcp.json.claude` → `.mcp.json`. Substitute any `<workspace>` placeholders (e.g. in a future `--user-data-dir` Playwright value) with the absolute workspace path.
- If `.cursor/mcp.json` does not exist or is a symlink (legacy state from before this release): copy `.cursor/mcp.json.cursor` → `.cursor/mcp.json`. Same placeholder substitution.
- If either runtime file already exists as a regular file: leave it alone (user owns it after init).

The two runtime files are gitignored; templates are committed; init keeps them as **regular-file copies**, never symlinks (iCloud rationale documented above).

**Step 12¾ (continued).** Print one short post-init pointer: *"Set up MCP servers in either IDE: edit `.mcp.json` or `.cursor/mcp.json` (they share content; keep them in sync). Each ingestor's own auth procedure lives in `superagent/docs/data-sources.md`."*

No batch-OAuth skill — see "Explicitly NOT in scope" below.

### H. `.gitignore` updates

Three small additions:

```gitignore
# Claude Code — keep settings tracked, ignore per-machine local overrides
!.claude/
.claude/*
!.claude/settings.json

# MCP project config — runtime files carry auth tokens, never committed.
# Templates (.mcp.json.claude, .cursor/mcp.json.cursor) ARE committed.
.mcp.json
!.mcp.json.claude
.cursor/mcp.json
!.cursor/mcp.json.cursor
```

Removes the old `.mcp.json.example` allow-rule (the file is renamed). Adds the new template names. Keeps `.claude/settings.json` tracked while letting `.claude/settings.local.json` stay untracked (Claude Code writes that file as a per-machine override).

---

## Explicitly NOT in scope

Listed so future readers know these were considered and deferred, not forgotten.

### No batch `auth-mcps` skill

NV-Co-SA's `auth-mcps` skill batches `authenticate` tool calls across ~14 HTTP MCP servers sharing one OAuth gateway. Superagent's MCP picture is structurally different:

- Most ingestors auth via their own native flow (Gmail OAuth + Google API client, Plaid OAuth, Apple Health is a SQLite file, Apple Reminders is osascript).
- The only MCP shipped today is Playwright (stdio, no OAuth).
- No shared gateway exists; no NVIDIA ECI body-data classification allowlist applies.

If Superagent ever ships its own OAuth-shaped MCPs that share a gateway, revisit. Until then, each ingestor's setup procedure in `docs/data-sources.md` is the right place.

### No cost-tracking subsystem in this release

NV-Co-SA's `cost-tracker.py` + `log-session-cost.py` + the per-turn `Stop` hook add real value — but Superagent has none of it today even for Cursor. Adding it now would bundle two independent features into one release.

Track as a follow-up roadmap item: **LOE-S — Add session-cost tracking (both IDEs)**. Owner can lift the NV-Co-SA implementation almost verbatim; the only Superagent-specific edits are paths and the pricing-table.

### No cross-IDE token re-extraction

NV-Co-SA's `maas_mcp.py` decrypts tokens from Cursor's Electron safeStorage **and** reads the macOS Keychain entry Claude Code uses. The point is to refresh expired MaaS OAuth tokens from whichever IDE has a fresh grant.

Superagent's MCPs each manage their own token storage (Gmail keeps its token in `~/.gmail-mcp/`, Plaid in its own config dir, etc.). There's nothing analogous to centralize. Per-MCP auth procedures already work under either IDE — Claude Code and Cursor both invoke the MCP binary, which reads its own token cache.

If a future Superagent skill needs cross-IDE token sharing (e.g. a unified-search MCP that both IDEs auth independently), open a follow-up. Until then, not relevant.

---

## Migration plan — `0.5.0`

`workspace/.version` advances from `0.4.0` to `0.5.0` via a marker migration mirroring NV-Co-SA's `1.35.0.md`. **No workspace data changes**; this is purely a release-version record.

Migration file at `superagent/migrations/0.5.0.md`:

```yaml
---
to_version: 0.5.0
from_version: 0.4.0
title: "Marker migration for first-class Claude Code support"
breaking: false
revertible: true
estimated_duration: "<1 second"
touches:
  - workspace/.version
helper_scripts: {}
---
```

Migration body (~80 lines): summarizes the release content; lists pre-flight checks (`.version` reads `0.4.0`; `config.yaml` is well-formed); migrate step advances `.version`; validate step confirms; revert step reverses.

**Note about runtime files.** The migration does NOT touch `.cursor/mcp.json` or `.mcp.json` even if a previous workspace had `.cursor/mcp.json` as a symlink (legacy state). Detection + offer-to-replace lives in the `init` re-run path (step 12¾), not in the migration — because `init` is interactive and the migration must run unattended-safe.

After the migration runs, register it in `superagent/migrations/_manifest.yaml` via `uv run python -m superagent.tools.version refresh-manifest`.

---

## Roadmap entry

One line under "Released" in `superagent/docs/roadmap.md`:

```markdown
| **0.5.0** | <date> | First-class Claude Code support: CLAUDE.md shim, .claude/settings.json, .claudeignore, per-IDE MCP templates, tools/ide.py detection helper, init.md step 12½/12¾ ([migration](../migrations/0.5.0.md)). |
```

---

## Effort estimate

LOE-S overall (under a day end-to-end). Breakdown:

| Step | Files touched | LOE |
|---|---|---|
| A. `CLAUDE.md` shim | 1 new | XS |
| B. `.claude/settings.json` | 1 new | XS |
| C. `.claudeignore` | 1 new | XS |
| D. MCP templates split | 2 renamed/new | XS |
| E. `tools/ide.py` | 1 new (~50 lines) + 1 test | S |
| F. AGENTS.md updates | 1 edit, 3 sections | S |
| G. `init` step 12½/12¾ | 1 skill edit | S |
| H. `.gitignore` updates | 1 edit | XS |
| Migration `0.5.0.md` | 1 new | XS |
| Manifest refresh | 1 tool run | XS |
| Roadmap entry | 1 edit | XS |
| Tests | `tests/test_ide.py`, smoke checks for `init` step idempotency | S |
| `pyproject.toml` version bump | 1 edit | XS |
| Lint pass (`uv run ruff check superagent/`) | — | XS |
| Commit hygiene + AI-attribution scrub | — | XS |

**Total**: roughly half a day of focused work. The Supercoder can implement this from a single approved Supertailor brief.

**Risk**: low. No schema changes, no workspace-data migration, no new Python dependencies, no upstream API contracts.

---

## Open questions

Things the Supertailor's brief should resolve before handing to the Supercoder:

1. **Do we want a `tools/mcp_sync.py` helper now**, or defer until users actually hit drift between `.mcp.json` and `.cursor/mcp.json`? Recommendation: defer; init re-run detection is enough until volume justifies a dedicated helper.
2. **Do we want `preferences.ide` in the config template now**, or add it lazily on first use? Recommendation: leave it absent from the template (the detect helper falls through cleanly without an override); add a one-line config snippet to the docs explaining how to set it manually.
3. **Should `CLAUDE.md` include any Claude-Code-specific behavior tweaks**, or stay a pure `@AGENTS.md` re-export? Recommendation: pure re-export. Any divergence belongs in `AGENTS.md` § "IDE setup" with IDE-specific notes inline, not in two parallel canonical docs.
4. **Should the `init` skill's pain-point demo branch ever pick a different demo skill under Claude Code**, given Claude Code's different chat UX? Recommendation: no. The demo skills already work over any chat UX; no IDE-specific branching needed.

---

## Implementation order

If approved, the Supercoder should land this in roughly this sequence (each item is a separate commit; all pass `uv run ruff check superagent/` before commit):

1. `tools/ide.py` + `tests/test_ide.py` (foundational; nothing else depends on it but it's the cleanest place to start).
2. `CLAUDE.md`, `.claude/settings.json`, `.claudeignore` (the three new top-level files; trivial to write; verifies the rest of the proposal in practice).
3. Rename `.mcp.json.example` → `.mcp.json.claude`; add `.cursor/mcp.json.cursor`; update `.gitignore`.
4. AGENTS.md edits (three sections).
5. `init.md` step 12½ + 12¾ insertion.
6. `superagent/migrations/0.5.0.md` + manifest refresh.
7. `roadmap.md` entry + `pyproject.toml` version bump.
8. Final lint pass + commit hygiene (strip any AI-attribution per `AGENTS.md` § "Git commits").

The whole arc is independent of any user data — every workspace just sees a marker migration; no schemas move. Existing Cursor-only setups keep working unchanged.

---

## References

- NV-Co-SA `1.35.0` migration file: `/Users/misha/icloud/NV-Co-SA-MY/co-sa/migrations/1.35.0.md`.
- NV-Co-SA `.claudeignore` reference: `/Users/misha/icloud/NV-Co-SA-MY/.claudeignore`.
- NV-Co-SA `auth-mcps` skill (informative; not adopted): `/Users/misha/icloud/NV-Co-SA-MY/co-sa/skills/auth-mcps.md`.
- NV-Co-SA per-IDE MCP templates: `/Users/misha/icloud/NV-Co-SA-MY/.mcp.json.claude`, `/Users/misha/icloud/NV-Co-SA-MY/.cursor/mcp.json.cursor`.
- Superagent versioning contract: `superagent/contracts/versioning.md`.
- Superagent framework-artifacts contract: `superagent/contracts/framework-artifacts.md`.
