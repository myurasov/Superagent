---
develops: bookkeeper
model_tier: dev
---

# Bookkeeper — dev twin

The notes of the agent that develops and maintains `bookkeeper.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). Every edit under `superagent/` follows `rules/framework-changes.md`.

## Purpose and design notes

- The Bookkeeper reconciles and flags. Paying, cancelling and changing a plan are the owner's, at any amount.
- It reads the local stores. Fresh transactions come from the Updater's finance harvest; the Bookkeeper does not fetch.
- Messages and logs carry handles (`bill:<slug>`, `account:<slug>`), never account numbers or balances.
- The monthly close and the tax packet are prepared for the owner to read, and the tax packet is sensitive output.

## Wakes and checks

- Checks: none. The three duties run on request, so no duty is ever reported late.
- Wakes: none are set by the framework. The cadence skills call `bills`, `subscriptions` and `expenses` inside the briefings.
- A natural first trigger is "a finance harvest brought new rows": the Updater's run would fire it, and the check would be "are there unreconciled transactions". It is not built.

## Cost targets

- A scheduled run with nothing to do costs no model call: every periodic wake sits behind a check.
- A wake re-reads the agent's whole context, so the role keeps its state in files and its context small.
- Measured cost per wake: none yet (no wake of this role has run).

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list` (a role with a problem is named on stderr).
- Duties: `uv run python -m superagent.tools.agents duties --agent bookkeeper`.
- Check exit codes: `uv run python -m superagent.tools.agents check bookkeeper bills; echo $?` (1: an on-request duty is never due by a check).
- Manifest in step with the role files: `uv run python -m superagent.tools.build_agent_manifest --check`.
- Suite: `uv run pytest superagent/tests/test_agent_roles.py superagent/tests/test_agents.py -q`, then the whole suite before a commit.
- Dry-run a duty in a chat session: take the role (`agents` skill, "act as bookkeeper") and run the duty's skill against a copy of the workspace.

## How to deploy

1. Edit the role file (and this file's changelog), run the tests, regenerate the manifest (`uv run python -m superagent.tools.build_agent_manifest`), commit per `rules/framework-changes.md`.
2. With no separate agents running, nothing else is needed: the next chat session reads the new file.
3. A running agent reads its role once, at its start. After a deploy, send it one message, `Pick up: <the change in one line>`, so it re-reads the role file. One message per deploy.

## How to observe

- What the role did: `uv run python -m superagent.tools.signals log-show --skill <skill> --last 10`.
- What is late: `uv run python -m superagent.tools.agents duties --agent bookkeeper --overdue`.
- A running agent: its wake list shows, per wake, what the last 7 days cost and how often the check skipped a run. A wake that costs much and mostly finds nothing needs a better check.

## Rollback

`git revert` the commit that changed the role, regenerate the manifest, and send the running agent `Pick up: <what was reverted>`. A role change touches no workspace data, so there is nothing else to restore.

## Pitfalls

- A reconcile that guesses a match marks a bill paid that was not. Match on amount, date window and payee together, and leave a doubtful row for the owner.
- A price change found during an audit is news for the owner, never an action for the agent.

## Changelog

- 0.25.0 — role file created from the Bookkeeper persona of `superagent.agent.md`.
