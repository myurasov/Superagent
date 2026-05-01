---
name: superagent-add-domain
description: >-
  Bootstrap a new life domain (e.g. Boat, Cabin, Side-business, Volunteer)
  beyond the 10 defaults seeded by init. Creates the folder, scaffolds the
  4-file structure, registers it in `domains-index.yaml`.
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

## 2. Scaffold the folder

1. Create `workspace/Domains/<Name>/`.
2. Copy the four templates from `superagent/templates/domains/` into it:
   - `info.md` with `{{DOMAIN_NAME}}` substitutions; sections empty.
   - `status.md` with `{{DOMAIN_NAME}}` and `{{LAST_UPDATED}}` substitutions; no tasks yet.
   - `history.md` with `{{DOMAIN_NAME}}` and `{{LAST_UPDATED}}`; empty Log.
   - `rolodex.md` with `{{DOMAIN_NAME}}` and `{{LAST_UPDATED}}`; empty tables.
3. Write `sources.md` from the template `superagent/templates/domains/sources.md` (initially with empty tables).
4. **Do not** pre-create `Resources/` — it's lazily created on first working-file write.

## 3. Register the domain

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

## 4. Optional — seed an initial entry

Ask:

> "Want to capture an initial fact, contact, or task while we're here? (yes / no)"

If yes:
- **Fact** → append a bullet to `info.md` § Key Facts.
- **Contact** → invoke `add-contact` with `related_domains: [<slug>]`.
- **Task** → invoke `todo` with `related_domain: <slug>`.

## 5. Confirm

Print:

```
Created domain "<Name>" at <path>.
Files: info.md, status.md, history.md, rolodex.md.
Registered in domains-index.yaml.
```

## 6. Logging

Append to `interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "add-domain"
  summary: "Created domain <slug> (<scope>)."
  related_domain: <slug>
```
