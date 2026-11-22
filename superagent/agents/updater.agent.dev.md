---
develops: updater
model_tier: dev
---

# Updater — dev twin

The notes of the agent that develops and maintains `updater.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). Every edit under `superagent/` follows `rules/framework-changes.md`.

## Purpose and design notes

- One duty, `watchlist`: run the watchlist check for the cycle that is due. Detect is cheap and declarative; harvest runs only where a pack has a handler.
- The Updater never asks and never sends. A run that needs a decision writes a task or a signal for the lead and goes on.
- Every step is idempotent, so a run that was cut off is safe to start again.
- The role does not brief. Its report is a list of facts for the lead.

## Wakes and checks

- Check: the built-in `watchers` check. It is `tools/cadence.py` `overdue_watchers`: a healthy watcher (enabled, `status: active`, `error_streak` 0) whose last success is older than its fastest cycle's period plus one day. `manual` watchers are never late.
- Wakes: none are set by the framework. With no separate agents running, the cadence skills run the watchlist check as their fetch step.
- A periodic wake of this role is added only with the owner's approval of its exact terms, and it runs `agents check updater watchlist` first: exit 1 skips the run at no cost.

## Cost targets

- A scheduled run with nothing to do costs no model call: every periodic wake sits behind a check.
- A wake re-reads the agent's whole context, so the role keeps its state in files and its context small.
- Measured cost per wake: none yet (no wake of this role has run).

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list` (a role with a problem is named on stderr).
- Duties: `uv run python -m superagent.tools.agents duties --agent updater`.
- Check exit codes: `uv run python -m superagent.tools.agents check updater watchlist; echo $?` (0 a watcher is overdue, 1 nothing is late, 2 unknown agent or duty).
- The watchlist itself: `uv run python -m superagent.tools.watchlist check --cycle daily-update --report --dry-run` against a copy of the workspace.
- Manifest in step with the role files: `uv run python -m superagent.tools.build_agent_manifest --check`.
- Suite: `uv run pytest superagent/tests/test_agent_roles.py superagent/tests/test_agents.py -q`, then the whole suite before a commit.
- Dry-run a duty in a chat session: take the role (`agents` skill, "act as updater") and run the duty's skill against a copy of the workspace.

## How to deploy

1. Edit the role file (and this file's changelog), run the tests, regenerate the manifest (`uv run python -m superagent.tools.build_agent_manifest`), commit per `rules/framework-changes.md`.
2. With no separate agents running, nothing else is needed: the next chat session reads the new file.
3. A running agent reads its role once, at its start. After a deploy, send it one message, `Pick up: <the change in one line>`, so it re-reads the role file. One message per deploy.

## How to observe

- What the role did: `uv run python -m superagent.tools.signals log-show --skill <skill> --last 10`.
- What is late: `uv run python -m superagent.tools.agents duties --agent updater --overdue`.
- A running agent: its wake list shows, per wake, what the last 7 days cost and how often the check skipped a run. A wake that costs much and mostly finds nothing needs a better check.

## Rollback

`git revert` the commit that changed the role, regenerate the manifest, and send the running agent `Pick up: <what was reverted>`. A role change touches no workspace data, so there is nothing else to restore.

## Pitfalls

- A frequent wake with no check in front re-reads the whole context every time and costs real money for nothing. The check comes first, always.
- A watcher that errors is reported by its own check (`error_streak`), and the `watchers` check leaves it out on purpose: it would otherwise wake the agent every run for something a retry cannot fix.

## Changelog

- 0.25.0 — role file created from the Ingestor persona of `superagent.agent.md`.
