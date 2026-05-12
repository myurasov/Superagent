---
name: superagent-add-domain
description: >-
  Register a new life domain (e.g. Boat, Cabin, Estate, Volunteer) beyond the
  12 defaults seeded by init. Appends a row to `_memory/domains-index.yaml`.
  Per `contracts/domains-and-assets.md` § 6.4a, the folder under
  `Domains/<Name>/` is LAZY — it materializes the first time real data lands
  for the domain (via add-contact / add-asset / log-event / etc., or via the
  optional initial-entry step in this skill).
triggers:
  - add a domain
  - new domain
  - create a domain
  - I want to track <X> as a domain
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent add-domain skill

## 1. Resolve the domain

Ask the user (one batch via `AskQuestion` or inline if obvious):

- **Domain name** (Title-cased, e.g. "Boat", "Cabin", "Volunteer-Soup-Kitchen", "Side-Business").
- **Scope** — one-line description of what this domain covers.
- **Priority** (P0 / P1 / P2 / P3) — default P2.
- **Tags** — short labels.

Auto-derive **slug** from the name: lowercase, hyphens for spaces, no punctuation (e.g. "Side Business" → `side-business`). Confirm if the slug is non-obvious.

Check `_memory/domains-index.yaml`:
- If the slug already exists, ask whether to **rename**, **use the existing one**, or **cancel**.

## 2. Register the domain (no folder creation)

Append a row to `_memory/domains-index.yaml`:

```yaml
- id: "<slug>"
  name: "<Name>"
  scope: "<one-line description>"
  priority: "<P0..P3>"
  status: "active"
  path: "workspace/Domains/<Name>"
  created: <now ISO 8601>
  last_updated: <now ISO 8601>
  tags: <user-supplied + ["custom"]>
  notes: ""
```

**Do NOT** pre-create `workspace/Domains/<Name>/` here. Per
`contracts/domains-and-assets.md` § 6.4a, default- and custom-domain folders
are lazy: they materialize on the first real data write via
`uv run python -m superagent.tools.domains ensure <slug>` (called automatically
by capture skills, or by step 3 below if the user opts in).

## 3. Optional — seed an initial entry (this is what materializes the folder)

Ask:

> "Want to capture an initial fact, contact, or task while we're here? (yes / no)"

If yes — invoke the matching capture skill, which will materialize the folder
as a side-effect of writing the first row:

- **Fact** → call `uv run python -m superagent.tools.domains ensure <slug>` first,
  then append a bullet to `info.md` § Key Facts.
- **Contact** → invoke `add-contact` with `related_domains: [<slug>]`. The
  add-contact skill calls `ensure` itself before touching `rolodex.md`.
- **Task** → invoke `todo` with `related_domain: <slug>`. The todo skill
  calls `ensure` before syncing the per-domain `status.md`.

If the user picks **no**, the domain stays registered-but-not-materialized.
That's fine — the folder will appear the first time you log something there.

## 4. Confirm

If folder was materialized in step 3:

```
Registered domain "<Name>" in domains-index.yaml.
Materialized Domains/<Name>/ (info.md, status.md, history.md, rolodex.md, sources.md).
```

If folder is still lazy:

```
Registered domain "<Name>" in domains-index.yaml.
Domains/<Name>/ will appear when the first row of data lands (via
add-contact / add-asset / log-event / health-log / ... or by re-running this
skill and saying "yes" to the initial-entry prompt).
```

## 5. Logging

Append to `interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "add-domain"
  summary: "Registered domain <slug> (<scope>). Folder materialized: <yes|deferred>."
  related_domain: <slug>
```
