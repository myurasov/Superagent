---
# `.ref.md` template — points at where a piece of data actually lives.
# Place under `Sources/references/<category>/<name>.ref.md`.
# Skills resolve this file to fetch the underlying data via the matching ingestor.

ref_version: 1

# --- What this points at ---
title: "<short title>"
description: "<one-line description of what this contains>"

# --- Where to get it (REQUIRED) ---
kind: ""
  # one of:
  #   mcp     — fetched via a configured MCP tool call
  #   cli     — fetched by running a shell command
  #   url     — fetched by HTTP GET (no auth or simple token)
  #   api     — fetched via authenticated API call (token in vault)
  #   file    — read from a local file path (outside Sources/)
  #   vault   — pulled from a password manager / secure notes vault
  #   manual  — must be fetched by the user (e.g. portal that requires 2FA);
  #             ingest is impossible programmatically; skill prompts user
source: ""
  # The source identifier. Format depends on `kind`:
  #   mcp:    "<server>/<tool>?param1=value1&param2=value2"
  #            e.g. "user-MaaS Outlook/outlook_get_message?id=AAMkAG..."
  #   cli:    full shell command to run
  #            e.g. "rem list --json --list 'Errands'"
  #   url:    full URL
  #            e.g. "https://example.gov/forms/1040.pdf"
  #   api:    URL + a hint of auth (full creds in vault, never here)
  #            e.g. "https://api.fidelity.com/v1/accounts/<acct>/balances"
  #   file:   absolute or ~-relative path
  #            e.g. "~/Documents/old-tax-returns/2020.pdf"
  #   vault:  vault item reference
  #            e.g. "1Password://Personal/2024-tax-pin"
  #   manual: human-readable instructions
  #            e.g. "Log in to mychart.kp.org → Records → Visit notes → Apr 2026"

# --- Optional: auth / parameters ---
auth_ref: ""
  # Vault reference to credentials needed (e.g. "1Password://Personal/fidelity-api").
  # Skills resolve via the password manager; never store creds in this file.

params: {}
  # Additional source-specific parameters (key/value).

# --- Caching policy ---
ttl_minutes: 1440         # how long the cached fetch stays fresh (default 24h)
sensitive: false          # if true, cache is encrypted (when sensitive-store is enabled)
chunk_for_large: true     # split fetched data into chunks if > config.sources.chunk_threshold_kb

# --- Cross-references ---
related_domain: ""        # domain id from domains-index.yaml
related_project: ""       # project id from projects-index.yaml
related_asset: ""         # asset id from assets-index.yaml
related_account: ""       # account id from accounts-index.yaml

# --- Provenance ---
added_by: "user"          # user | <skill-name> | <ingestor-name>
added_at: null            # ISO 8601 datetime

# --- Tagging ---
category: ""              # vehicles | taxes | medical | warranties | legal | …
tags: []
---

# Notes

<!-- Free-text. Why this exists, what to look for in it, how to interpret. -->
<!-- The agent reads this section AFTER the frontmatter, BEFORE fetching, to
     decide whether to fetch at all (sometimes the notes answer the question). -->
