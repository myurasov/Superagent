---
develops: superagent
model_tier: dev
---

# Superagent — dev twin

The notes of the agent that develops and maintains `superagent.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). Every edit under `superagent/` follows `rules/framework-changes.md`.

## Purpose and design notes

- The lead is the one agent the owner talks to. It briefs, reviews and routes; fetching belongs to the Updater and the money side to the Bookkeeper.
- `skills: all` is allowed for the lead only. Its duties name the five cadence skills; every other skill it runs on request.
- The lead also carries the Quartermaster and Archivist duties. They stay sections of the role file until they grow enough to justify a role of their own.
- Heavy duties (`doctor`, `supertailor-review`, long sweeps) run in a subagent, so the lead's context stays small (`rules/subagents.md`).
- The lead is the only role that asks the owner. The other roles hand it what needs a decision.

## Wakes and checks

- Checks: every duty uses the built-in `cadence` check. It reads `context.yaml.last_runs` and `preferences.cadences`. A duty is **due** when its stamp is older than its latest scheduled time (`tools/cadence.py` `duty_due`: a missed Sunday review is due on Monday), and **overdue** by the nag rules (`overdue_cadences`, and the 24-hour context rule for `daily-update`). The `brief` skill writes the stamps of the three briefing duties.
- Wakes: none are set by the framework. The on-open check (`cadence status --stamp`) is what tells an attended session which duty is late.
- A periodic wake of this role is added only with the owner's approval of its exact terms, and each one runs `agents check superagent <duty>` first.

## Cost targets

- A scheduled run with nothing to do costs no model call: every periodic wake sits behind a check.
- A wake re-reads the agent's whole context, so the role keeps its state in files and its context small.
- Measured cost per wake: none yet (no wake of this role has run).

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list` (a role with a problem is named on stderr).
- Duties: `uv run python -m superagent.tools.agents duties --agent superagent`.
- Check exit codes: `uv run python -m superagent.tools.agents check superagent weekly-review; echo $?` (0 due, 1 not due, 2 unknown agent or duty).
- Manifest in step with the role files: `uv run python -m superagent.tools.build_agent_manifest --check`.
- Suite: `uv run pytest superagent/tests/test_agent_roles.py superagent/tests/test_agents.py -q`, then the whole suite before a commit.
- Dry-run a duty in a chat session: take the role (`agents` skill, "act as superagent") and run the duty's skill against a copy of the workspace.

## How to deploy

1. Edit the role file (and this file's changelog), run the tests, regenerate the manifest (`uv run python -m superagent.tools.build_agent_manifest`), commit per `rules/framework-changes.md`.
2. With no separate agents running, nothing else is needed: the next chat session reads the new file.
3. A running agent reads its role once, at its start. After a deploy, send it one message, `Pick up: <the change in one line>`, so it re-reads the role file. One message per deploy.

## How to observe

- What the role did: `uv run python -m superagent.tools.signals log-show --skill <skill> --last 10`.
- What is late: `uv run python -m superagent.tools.agents duties --agent superagent --overdue`.
- A running agent: its wake list shows, per wake, what the last 7 days cost and how often the check skipped a run. A wake that costs much and mostly finds nothing needs a better check.

## Rollback

`git revert` the commit that changed the role, regenerate the manifest, and send the running agent `Pick up: <what was reverted>`. A role change touches no workspace data, so there is nothing else to restore.

## Pitfalls

- A briefing that fetches and tells in one pass grows with every source added. Keep the fetch in the Updater's run and let the briefing read the stores.
- A marker written to a shared file at session open lets a stale copy from another machine win (`rules/concurrent-sessions.md`). The on-open check writes machine-local state only.

## Changelog

- Unreleased — the lead is no messenger: agents write to each other, the Updater tells each agent what moved for it (interests), and the lead hears only what needs a decision, what the owner must hear, and starts and ends. It records answers with `tools/autocode.py answer`, which tells the agent that asked (the owner's request).
- Unreleased — an agent whose work is stopped adds a blocking ask and raises its card in the owner's Inbox itself; the answer goes to that agent, which records it. The lead stays the role that asks in briefings and the one that writes reports (the owner's request, after an ask sat in the queue unseen).
- Unreleased — `setup apply` keeps a duty's cadence value in step with a changed wake time, and a granted wake is never offered again or replaced by a plain apply (change request from the lead after the first live start).
- Unreleased — the lead is started by the `start` skill: the owner's chat session registers as the lead after the question cards; wakes are proposed from the duties and go through `tools/wake_check.sh` (cost cap first).
- 0.27.0 — the lead as a registered session: briefing duties from granted wakes, `brief` run directly while the Updater is active, one report per briefing, the heartbeat lines.
- 0.26.0 — the four cadence skills became entry points over `update` and `brief`; the lead now owns `_memory/asks.yaml` and records the owner's answers; a duty is `due` by its stamp before it is `overdue`, and `agents check` exits 0 for both.
- 0.25.0 — frontmatter added; the Bookkeeper and the Ingestor persona moved to role files of their own; the framework review became a skill and a rule.
