<!--
Template for a per-web-app browserctl skill. Copy to
workspace/_custom/skills/browserctl-<app>.skill.md the first time Superagent
successfully drives a recurring web app, and fill in what was actually
learned. These files are user-specific (they name providers/accounts) and
NEVER live under superagent/. No credentials — vault_ref only.
-->
---
name: browserctl-<app>
description: Drive <App Name> (<entry URL>) via browserctl — login flow, navigation map, quirks.
triggers:
  - <app>
  - "log into <app>"
  - "check <thing this app is for>"
extends: superagent-browserctl
---

# browserctl-<app> — driving <App Name>

## Basics

- **Entry URL**: <https://...>
- **Profile**: `default` (or a dedicated `<app>` profile if isolation is needed)
- **Account**: <which account / username hint — no secrets; vault: `<vault_ref>`>
- **Related**: domain `<domain:...>`, bill `<bill:...>`, account `<account:...>`

## Login flow

1. `uv run superagent/tools/browserctl.py launch --profile default --headed --url <login URL>`
2. <who types the password — usually the user, once; session persists in the profile>
3. **2FA**: <none / SMS to user / authenticator — always requires the user; plan for it>
4. Signed-in check: `eval --js "<expression that is truthy only when logged in>"`

## Navigation map

<The paths that matter, as URL patterns or click paths. Examples:>

- Billing history: <URL or "Account menu → Billing">
- Statements/PDF download: <path; downloads need an `attach()` script with `expect_download`>

## Selectors that work

| Target | Selector | Notes |
|---|---|---|
| <e.g. amount due> | `<css / text= / role=>` | <stability notes> |

## Quirks

- <SPA never fires `load` → always `domcontentloaded`; iframes; cookie banners; session
  timeout behavior; rate limits; anything that bit us once>

## Changelog

- <YYYY-MM-DD> — created after first successful drive (<what was done>).
