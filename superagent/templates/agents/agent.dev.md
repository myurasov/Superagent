---
develops: <name>
model_tier: dev
---

# <Name> — dev twin

The notes of the agent that develops and maintains `<name>.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). A framework role follows `rules/framework-changes.md`; a custom role is edited in `workspace/_custom/` directly.

## Purpose and design notes

- <Why the role exists and the decisions behind its shape.>

## Wakes and checks

- Checks: <the check of each duty and what it looks at>.
- Wakes: <none, or each granted wake: trigger, purpose, cost per run, end date>.

## Cost targets

- A scheduled run with nothing to do costs no model call: every periodic wake sits behind a check.
- Measured cost per wake: <fill in once a wake has run>.

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list --workspace <path>`.
- Duties: `uv run python -m superagent.tools.agents duties --agent <name>`.
- Check exit codes: `uv run python -m superagent.tools.agents check <name> <duty>; echo $?` (0 due, 1 not due, 2 unknown agent or duty).
- Dry-run a duty in a chat session: take the role and run the duty's skill against a copy of the workspace.

## How to deploy

1. Edit the role file (and this file's changelog) and run the tests.
2. With no separate agents running, the next chat session reads the new file.
3. A running agent reads its role once, at its start. After a deploy, send it one message, `Pick up: <the change in one line>`, so it re-reads the role file.

## How to observe

- What the role did: `uv run python -m superagent.tools.signals log-show --skill <skill> --last 10`.
- What is late: `uv run python -m superagent.tools.agents duties --agent <name> --overdue`.

## Rollback

<How to put the previous version back, and what to tell the running agent.>

## Pitfalls

- <Dated lessons: what went wrong and what to do about it.>

## Changelog

- <date or version> — created.
