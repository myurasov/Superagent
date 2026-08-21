<!--
Template for a per-web-app browserctl plugin. Copy to
workspace/_custom/skills/browserctl.<app>.md the first time Superagent
successfully drives a recurring web app, and fill in what was actually
learned. These files are user-specific (they name providers/accounts) and
NEVER live under superagent/. No credentials — vault_ref only.

Structure is interchange-compatible with the sibling `co-sa` framework's
browserctl plugins: same filename pattern, same frontmatter keys, same
section spine. Moving a plugin between frameworks should require rewriting
only the tool path prefix and repo-relative links.

KEEP IT LIVE: per the browserctl skill Step 5a, whenever a documented flow
breaks or a better path is found while driving the site, update this file in
the SAME turn — dated bullet in Pitfalls Log, plus an in-place edit of any
procedure that changed.
-->
---
name: browserctl.<app>
description: >-
  <One folded paragraph: what application this drives through browserctl,
  which capture/write capabilities it provides, where output lands, and what
  it deliberately refuses to do.>
triggers:
  - <user phrasing that should dispatch here>
  - <...>
mcp_required: []
mcp_optional: []
extends: superagent-browserctl
profile: <profile name>
entry_url: <https://...>
port: <CDP port from `browserctl status --json`>
---

# <App> via browserctl

<One or two sentences: what this plugin does and why the browser path exists
(no API, richer access, session-only data). Add a Table of Contents per the
markdown-TOC rule once the file has more than a handful of sections.>

## When to Use

<The requests this plugin answers — and what routes elsewhere: an MCP tool, a
local archive that already holds the answer more cheaply, another skill.>

## Profile and Session

- **Profile**: <dedicated `<app>` profile, or `default`; say which and why>.
- **Mode**: <headed/headless; when to flip>.
- **Auth**: <how login is verified; interactive SSO/2FA is always the user's —
  never automate credential entry. Include the cheap signed-in check.>

## 1. <First Step>

<Numbered procedure steps. Exact commands
(`uv run superagent/tools/browserctl.py ...`), `attach()` snippets, selectors,
waits, and per-page timing budgets. Prefer direct URLs over click chains.>

## Capture

<How externally-originated content this plugin reads reaches the local stores
in the same pass it is seen — index rows, domain/project artifacts, the email
archive, Sources filing. Per AGENTS.md § "Knowledge discipline": a read-only
plugin still captures; read-only means no upstream writes, not no local
writes. Note what was deliberately NOT captured and why, so the next session
does not re-litigate it.>

## Verification

<How the plugin proves its actions landed — re-read the state that should have
changed (count, total, row presence), not just the command's return value.
Note which verification affordances are unavailable here (e.g. screenshot
timeouts) and what to use instead.>

## Guardrails

<Hard limits: what this plugin must never click or submit, which affordances
are dangerous (one-click buy/pay/send buttons adjacent to safe ones), the
money/irreversibility threshold that hands control back to the user, and the
governing rule file.>

## Pitfalls Log

- <YYYY-MM-DD — dated, app-specific gotchas, kept current as they are learned.
  Write each so it PREVENTS the failure: the symptom the next agent observes
  first, then the cause, then the fix.>

## Changelog

- <YYYY-MM-DD> — created after first successful drive (<what was done>).
