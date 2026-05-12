---
name: superagent-scenarios
description: >-
  Run a what-if scenario against current workspace state. Five canned
  scenarios in MVP: cancel-subscriptions, trial-end-impact, bill-shock,
  balance-floor, project-overrun. Outputs to stdout and optionally to
  `Outbox/scenarios/<name>.md`.
triggers:
  - scenario
  - what if
  - what would happen if
  - simulate
  - estimate impact
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent scenarios skill

Implements superagent/docs/_internal/ideas-better-structure.md item #14. Engine: `tools/scenarios.py`.

## 1. Disambiguate

If the user's phrasing maps clearly to one of the five canned scenarios, pick it. Otherwise list them:

| scenario | example question |
|---|---|
| cancel-subscriptions | "If I cancel Adobe and Disney+, how much do I save?" |
| trial-end-impact | "If all my current trials convert, what's the new monthly cost?" |
| bill-shock | "If every bill goes up 8%, what's the monthly impact?" |
| balance-floor | "Given upcoming bills, when does Chase checking dip below $500?" |
| project-overrun | "If kitchen-reno goes 25% over budget, do we still fit?" |

## 2. Collect parameters

| scenario | params |
|---|---|
| cancel-subscriptions | list of subscription ids or names |
| trial-end-impact | (none — reads all trial-status subs) |
| bill-shock | percent (number) |
| balance-floor | account id, starting balance, days (default 60) |
| project-overrun | project id, percent (number) |

## 3. Execute

```
uv run python -m superagent.tools.scenarios <scenario> <args> --out <name>.md
```

Pipe the result back into chat.

## 4. Surface

The scenario output is structured: scenario name, inputs, computed answer, list of contributing entities. Render the markdown the tool produced.

If the scenario reveals a useful action (e.g. "you'd save $1,800/year by cancelling these 3"), offer:

> "Want me to walk through actually cancelling them? (`subscriptions audit` will do it interactively.)"

## 5. Persistence

The tool writes to `Outbox/scenarios/<name>.md` if `--out` was passed. Add to the `outbox-log.yaml` if the resulting artifact should be retained.

Optionally append to `decisions.yaml` if the user follows through on the scenario's suggestion ("Decided to cancel Adobe + Disney+ based on scenario X").

## 6. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "scenarios <scenario>"
  summary: "<scenario> with <params>; result: <one-line>"
  related_domain: <inferred>
```
