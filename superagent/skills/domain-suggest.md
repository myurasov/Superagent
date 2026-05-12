---
name: superagent-domain-suggest
description: >-
  Detection-driven domain suggestions per `contracts/domains-and-assets.md`
  § 6.4b. Walks workspace signals (off-domain tags, contact-role clusters,
  off-domain project clusters, source folder clusters) and proposes new life
  domains the user hasn't already accepted / declined / deferred. Asks the
  user ONCE per cluster (yes / not now / never); persists the answer to
  `_memory/domain-suggestions.yaml`. On `yes`, delegates to `add-domain` with
  pre-filled name / scope. Also reachable as a sub-step of `monthly-review`.
triggers:
  - domain suggest
  - suggest a domain
  - any new domains?
  - what should I add as a domain
  - run the domain detector
  - I keep tracking <topic>; should that be a domain?
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent domain-suggest skill

This skill is the user-facing front-end to `superagent/tools/domain_detector.py`
and the `_memory/domain-suggestions.yaml` history file. Two modes:

1. **`run`** (default) — invoke the detector, surface candidates, ask once
   per candidate.
2. **`list`** — show the current state of all four buckets (suggested,
   accepted, declined, deferred) without re-running detection.
3. **`forget <theme>`** — remove a theme from declined/deferred so it can
   surface again on the next run.

## 1. Read configuration

Read `_memory/config.yaml.preferences.domain_detection`:

```yaml
preferences:
  domain_detection:
    enabled: true        # master switch; default true
    min_score: 5         # threshold below which candidates are dropped
    top_n: 3             # max candidates surfaced per run
    defer_days: 90       # how long "not now" hides a theme
```

If `enabled: false`, print "Domain detection disabled in config." and exit.

## 2. Run the detector

```
uv run python -m superagent.tools.domain_detector run --json \
    --min-score <min_score> --top-n <top_n>
```

Parse the JSON. If empty, print "No new domain candidates above threshold."
and exit.

## 3. Surface each candidate via AskQuestion

For each candidate (max 3), present a single `AskQuestion` with three
options:

```
title: Proposed new domain: <Proposed Name>

prompt: |
  I noticed <one-line rationale>. Want me to add a `<Proposed Name>` domain
  to track this separately?

  - Why I'm asking: <evidence summary>
  - Proposed scope: <draft scope; you can edit on accept>
  - Default priority: <P0..P3>

options:
  - id: yes      label: Yes — create the domain now
  - id: not_now  label: Not now — ask again in 90 days
  - id: never    label: Never — don't suggest this again
```

Batch all candidates into ONE `AskQuestion` call when there are multiple,
to minimize prompt-thrash.

## 4. Per-answer side-effects

For each candidate's answer:

### yes → invoke `add-domain`

1. Materialize the row by invoking the `add-domain` skill with the
   pre-filled `proposed_name` / `proposed_scope` / `proposed_priority`.
   Confirm with the user that they're happy with the name/scope (offer to
   edit) before finalizing.
2. After `add-domain` writes the new domain to `_memory/domains-index.yaml`,
   record the acceptance:

   ```
   uv run python -m superagent.tools.domain_detector record <theme> yes
   ```

   Then patch `_memory/domain-suggestions.yaml.accepted[]` to set the
   `domain_id` field to the actual slug `add-domain` chose.

### not_now → defer for 90 days

```
uv run python -m superagent.tools.domain_detector record <theme> not_now
```

The detector will skip this theme until `today + defer_days` has passed.

### never → suppress permanently

```
uv run python -m superagent.tools.domain_detector record <theme> never
```

The detector will never re-surface this theme until the user clears it via
`forget <theme>`.

## 5. Confirm

Print a short summary:

```
Domain detection — <today>
  Candidates surfaced: <N>
  Accepted: <list of new domain ids>
  Deferred: <list of themes>
  Declined: <list of themes>
```

## 6. Logging

Append to `_memory/interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "domain-suggest"
  summary: |
    Ran domain detector. Surfaced <N> candidates (<themes>). User
    accepted <M> (<accepted_themes>), deferred <K>, declined <J>.
  related_domain: null
  action_items: []
```

## 7. Sub-modes

### list

```
uv run python -m superagent.tools.domain_detector list
```

Renders the four buckets. No user prompts.

### forget <theme>

```
uv run python -m superagent.tools.domain_detector forget <theme>
```

Clears the theme from `declined[]` and `deferred[]`. Confirms the theme is
now eligible for re-detection.

## 8. Ambient surfacing convention (for the agent in non-skill turns)

Per `contracts/domains-and-assets.md` § 6.4b, the agent MAY surface a
candidate ambiently mid-conversation when:

- It observes a strong cluster signal in the user's recent captures (e.g.
  the user has mentioned the same off-domain theme three times this
  session, OR is creating multiple entities the agent can't naturally
  route).
- No prior surfacing has happened in this session (check
  `domain-suggestions.yaml.surfaced[]` for any row with today's session_id).
- The current turn is NOT high-friction (active triage, payment
  processing, emergency).

Format:

> Quick observation — I notice <evidence summary>. Want me to add a
> `<Name>` domain to track that separately? (yes / not now / never)

Use the same record-answer mechanism as step 4.
