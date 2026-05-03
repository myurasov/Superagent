---
name: superagent-play
description: >-
  Run a playbook — a named sequence of skills with optional conditions.
  Resolves the playbook (per `tools/play.py`), then invokes each skill in
  the resolved order. Custom playbooks at `_custom/playbooks/` override
  framework playbooks of the same name.
triggers:
  - play
  - run playbook
  - good morning
  - end of week
  - tax prep season
  - pre-trip week
  - quarterly health
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent play skill

Implements superagent/docs/_internal/ideas-better-structure.md item #21. Resolver: `tools/play.py`.

## 1. Resolve

If the user's phrasing matches a known playbook trigger (see `superagent/playbooks/*.yaml` `trigger:` lists), pick that playbook. Otherwise prompt:

> "Available playbooks: <list>. Which one?"

Run `python3 -m superagent.tools.play list` to enumerate.

## 2. Resolve conditions

Run `python3 -m superagent.tools.play resolve <name> --json` to get the ordered list of skill invocations after condition evaluation. The resolver evaluates each `if:` against current workspace state (via small queries against `bills.yaml`, `appointments.yaml`, etc.).

The output is a list like:

```json
[
  {"skill": "whatsup", "args": "", "note": "30-second delta"},
  {"skill": "bills", "args": "list --overdue", "note": "Pay overdue first."},
  {"skill": "daily-update", "args": "", "note": "Full briefing."}
]
```

## 3. Walk the resolved sequence

For each step:

1. Print: `[N/total] <skill> <args>  — <note>`
2. Invoke the skill (read its markdown, follow its steps).
3. After completion, ask the user "continue / pause / skip next / stop":
   - **continue** (default): proceed to next step.
   - **pause**: stop here; the user will resume later.
   - **skip next**: skip the next step, then ask again.
   - **stop**: end the playbook entirely.

## 4. Custom overlays

Per the custom-overlay contract (contracts/custom-overlay.md), if `workspace/_custom/playbooks/<name>.yaml` exists, it OVERRIDES the framework playbook of the same name. Surface the override in chat:

> "Using `_custom/playbooks/<name>.yaml` (overrides framework playbook)."

## 5. Playbook completion

After the last step:

1. Append to events stream: `kind: playbook_run`, `subject: "<name>"`, `summary: "<X> steps run, Y skipped"`.
2. Append to `interaction-log.yaml`.

## 6. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "play <name>"
  summary: "<X> steps executed, Y skipped, completed: <bool>"
```
