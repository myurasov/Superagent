# Info — {{DOMAIN_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Domain info file (4-file structure):
    info.md     — this file: narrative overview, current state, key facts
    status.md   — RAG status + open / done task lists
    history.md  — chronological log of touchpoints / events / decisions
    rolodex.md  — contact directory scoped to this domain
  Plus:
    sources.md  — curated catalogue of Sources/ entries for this domain
    Resources/  — optional, lazily created — drafts, working files,
                  agent-generated artifacts (not for sending out).
  Source documents NEVER live directly under Domains/. They live in
  `Sources/documents/<category>/` and are pointed at from `sources.md`.

  Sub-section contract:
    ## Overview         — one-paragraph narrative of what this domain covers
                          for the user specifically.
    ## Current State    — what's true right now (open items, dates, balances,
                          conditions, on-going engagements).
    ## Key Facts        — bullet list of "things to remember": specs,
                          credentials (last-4 only), reference numbers,
                          model numbers, dosages, schedules.
    ## Routines         — recurring practices for this domain (weekly meal
                          plan, monthly pet med, annual physical).
    ## Stakeholders     — humans with skin in this domain (lives in rolodex.md
                          long-form; this section is the headline list).
    ## Open Questions   — things you mean to figure out / decide.

  Skills auto-update specific named sections only (the maintenance sync
  contract is in procedures.md). Hand-edits are respected — skills MUST
  diff and merge, not blindly clobber.
-->

---

## Table of Contents

- [Info — {{DOMAIN_NAME}}](#info--domain_name)
  - [Overview](#overview)
  - [Current State](#current-state)
  - [Key Facts](#key-facts)
  - [Routines](#routines)
  - [Stakeholders](#stakeholders)
  - [Open Questions](#open-questions)

---

## Overview

<!-- One paragraph: what this domain covers for YOU specifically. -->

{{OVERVIEW}}

---

## Current State

<!-- What's true right now. Open commitments, current providers, current
     balances, current conditions, anything in flight. Update when reality
     shifts; this is the "what would I tell a friend who needed to take
     over this domain for a month" view. -->

{{CURRENT_STATE}}

---

## Key Facts

<!-- Bullet list. Specs, model numbers, last-4 of accounts, dosages,
     schedules, reference numbers, anything you'd otherwise have to look
     up. Skills append here when they ingest a new fact. Sensitive items
     (full account numbers, full SSN, raw passwords) NEVER go here —
     reference vault entries instead. -->

{{KEY_FACTS}}

---

## Routines

<!-- Recurring practices for this domain (weekly meal plan, monthly pet
     med, quarterly subscription audit, annual physical, biannual eye
     exam, seasonal HVAC service). The Concierge / Quartermaster / Medic
     persona uses this section to know what cadences are in flight. -->

{{ROUTINES}}

---

## Stakeholders

<!-- Headline list of humans involved. Full contact details live in
     rolodex.md. Format:
       - **{{NAME}}** ({{ROLE}}) — {{ONE_LINE_NOTE}}
-->

{{STAKEHOLDERS}}

---

## Open Questions

<!-- Things you mean to figure out / decide. Move to action items when
     ready by filing a task via the `todo` skill (with this domain set
     as `related_domain`). -->

{{OPEN_QUESTIONS}}
