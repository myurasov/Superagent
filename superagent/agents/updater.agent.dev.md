---
develops: updater
model_tier: dev
---

# Updater — dev twin

The notes of the agent that develops and maintains `updater.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). Every edit under `superagent/` follows `rules/framework-changes.md`.

## Purpose and design notes

- Two duties, both the `update` skill: `update-light` (the watchers that are due, the drop folders, capture, reconcile, tasks) and `update-full` (adds the transaction reconcile, project drift and the derived views). Detect is cheap and declarative; harvest runs only where a pack has a handler.
- The Updater never asks and never sends. A run that needs a decision writes an ask (`tools/asks.py`) or a task for the lead and goes on.
- One cycle at a time per machine: the cycle lock (`tools/lock.py`) is a kernel lock held by a small background process, because a cycle spans many commands.
- Every step is idempotent, so a run that was cut off is safe to start again.
- The role does not brief. Its report is a list of facts for the lead.

## Wakes and checks

- `update-light`: the built-in `update` check. Due at every look (`tools/agents.py` `evaluate`): every slot runs a light update, and the reason names the watchers `tools/watchlist.py` `due_rows` finds due and the settled documents `tools/inbox.py` finds in a drop folder. A daily watcher is due at every update run (`due_state`); a weekly or monthly one when its last success lies a full period back; backoff, throttle and budget hold any of them. Overdue (the on-open nag) is `tools/cadence.py` `overdue_watchers`: a healthy watcher past its period plus one day. `manual` watchers are never due and never late.
- `update-full`: the built-in `cadence` check, by the stamp `last_runs.update_full` against `preferences.cadences.update_full`.
- Wakes: none are set by the framework. With no separate agents running, the cadence skills run the update cycle as their first step.
- A periodic wake of this role is added only with the owner's approval of its exact terms, and it runs `agents check updater update-light` first: exit 1 skips the run at no cost when it is held (work hours, an off day, the grant, the cost cap).

## Cost targets

- `update-light` runs at every slot inside work hours, whether or not something moved (the owner's decision of 2026-11-25: a simple loop and a cheaper model over a per-source interval). A slot costs a model run; the owner was shown about $2-4 a day for six slots. The checks that cost nothing are the ones that hold a run: work hours, off days, the grant, the cost cap. `update-full` goes by its stamp, once a day.
- A wake re-reads the agent's whole context, so the role keeps its state in files and its context small.
- Measured cost per wake: none yet (no wake of this role has run).

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list` (a role with a problem is named on stderr).
- Duties: `uv run python -m superagent.tools.agents duties --agent updater`.
- Check exit codes: `uv run python -m superagent.tools.agents check updater update-light; echo $?` (0 there is work, 1 nothing is due, 2 unknown agent or duty).
- What is due, without fetching: `uv run python -m superagent.tools.watchlist due --json`. The watchlist itself: `uv run python -m superagent.tools.watchlist check --due --report --dry-run` against a copy of the workspace.
- The lock: `uv run python -m superagent.tools.lock status`; two `hold` calls in a row show the second one waiting, then skipping.
- Suite for the cycle: `uv run pytest superagent/tests/test_watchlist_due.py superagent/tests/test_lock.py superagent/tests/test_asks.py superagent/tests/test_inbox.py superagent/tests/test_update_and_brief.py -q`.
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
- A watcher that errors is reported by its own check (`error_streak`), and the overdue rule leaves it out on purpose. The due rule retries it with a wait that doubles per failure (60 minutes, up to the watcher's period), so a source that is down does not wake the agent every run.
- A `subagent` watcher that was dispatched and never stamped is offered again after 60 minutes, and each offer costs a model run. The skill stamps every outcome, `--unreachable` included.

## Changelog

- Unreleased — every `update-light` slot inside work hours runs (the check no longer skips a slot with nothing due), and every update run checks every daily watcher inside its throttle and budget; weekly and monthly watchers keep their periods. The briefing's quiet-loop line counts from the start of work hours and allows the light period plus half an hour (the owner's request; settles the two heartbeat signals and the request for a per-watcher interval).
- Unreleased — hand-offs go by interests: after a cycle the Updater asks `agents interested` who wants to hear about each thing that moved and tells those agents directly (a message, or the wake the interest names). No run report to the lead; a message to the lead only when something is broken (the owner's request).
- 0.27.0 — "As a session of its own": two granted wakes behind their checks, the registry row names the fetching machine, work hours and off days hold the duties, the Bookkeeper's wake is fired after a bank harvest.
- 0.26.0 — the duty `watchlist` (skill `watch`, check `watchers`) became `update-light` and `update-full` (skill `update`); the cycle lock, the asks queue and `watchlist check --due` came with it. The role no longer asks even with the owner present.
- 0.25.0 — role file created from the Ingestor persona of `superagent.agent.md`.
