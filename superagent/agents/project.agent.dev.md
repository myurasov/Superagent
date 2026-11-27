---
develops: project
model_tier: dev
---

# Project agent — dev twin

The notes of the agent that develops and maintains `project.agent.md`. The running agent never edits its own role, duties or checks: it files a change request, and this twin builds, tests and deploys the change (`contracts/agents.md` § "Dev twins"). Every edit under `superagent/` follows `rules/framework-changes.md`.

## Purpose and design notes

- One generic role serves every project: an instance is one row in `_memory/agents.yaml` that names the project, and its id is `<prefix>:project:<slug>`, or the one built from the `id_name` on its row when the owner gave the agent another id (one of a group, `<prefix>:project:<group>:<member>`). This one dev twin (`<prefix>:project:dev`) serves them all.
- A project agent is dormant by default: no periodic wake, no duty. A message wakes it.
- Its state lives in the project folder (`info.md`, `status.md`, `history.md`), so a session that starts cold reads the files and carries on.
- It ends with the project: a closing note in `history.md`, then the instance is retired.

## Wakes and checks

- Checks: none (the role has no duty).
- Wakes: none by default. A periodic wake exists only when the owner asked for it and approved its exact terms (trigger, purpose, cost per run, end date); the grant is recorded on the instance's registry row (`wakes`).
- A one-time self-wake needs no grant inside the limits in `contracts/agents.md` § "Wakes".

## Cost targets

- An idle project agent costs nothing: no wake, no model call.
- A wake re-reads the agent's whole context, so the agent writes its state to the project folder and keeps its context small.
- Measured cost per wake: none yet (no project agent has run).

## How to test

- Frontmatter and roster: `uv run python -m superagent.tools.agents list` (a role with a problem is named on stderr).
- Duties: `uv run python -m superagent.tools.agents duties --agent project`.
- Check exit codes: there is no duty to check; `uv run python -m superagent.tools.agents check project <duty>` exits 2.
- The id of an instance: `uv run python -m superagent.tools.agents id project:<slug>` (the id on the row when the row carries an id name; the command takes the id name too).
- Manifest in step with the role files: `uv run python -m superagent.tools.build_agent_manifest --check`.
- Suite: `uv run pytest superagent/tests/test_agent_roles.py superagent/tests/test_agents.py -q`, then the whole suite before a commit.
- Dry-run a duty in a chat session: take the role (`agents` skill, "act as project") and run the duty's skill against a copy of the workspace.

## How to deploy

1. Edit the role file (and this file's changelog), run the tests, regenerate the manifest (`uv run python -m superagent.tools.build_agent_manifest`), commit per `rules/framework-changes.md`.
2. With no separate agents running, nothing else is needed: the next chat session reads the new file.
3. A running agent reads its role once, at its start. After a deploy, send it one message, `Pick up: <the change in one line>`, so it re-reads the role file. One message per deploy.

## How to observe

- What the role did: `uv run python -m superagent.tools.signals log-show --skill <skill> --last 10`.
- What is late: `uv run python -m superagent.tools.agents duties --agent project --overdue`.
- A running agent: its wake list shows, per wake, what the last 7 days cost and how often the check skipped a run. A wake that costs much and mostly finds nothing needs a better check.

## Rollback

`git revert` the commit that changed the role, regenerate the manifest, and send the running agent `Pick up: <what was reverted>`. A role change touches no workspace data, so there is nothing else to restore.

## Pitfalls

- A project slug named `dev` would collide with the dev twin's id; `agents id` refuses it.
- An id that was moved in the runtime with no id name on the row leaves every built id the old one. Messages may still arrive where the runtime forwards the old id, while a start, `autocode mark`, the cap check and the audits compare ids and miss. Record the id name in the turn the id moves.
- A project agent that keeps working notes only in its conversation loses them at the next compaction. The project folder is the memory.
- A park routine that compacts after every piece of work cuts the thread of a session the owner talks with through the day. Compaction is the owner's word (compact, park) or the runtime's at its limit.

## Changelog

- 0.34.1 — the park routine has no compaction step: the agent writes its state and goes idle, and its session is compacted only when the owner says compact or park, on a `Park`, and by the runtime at its limit (the owner's request).
- 0.34.0 — the agent of a parent project records the owner's answers to its sub-projects' asks, when he gives them in its session (`autocode answer`; any other agent is refused) (the owner's request).
- 0.34.0 — only the first start writes the role's interests onto the row; a later start leaves the row as it is, so an interest the agent or the owner dropped stays dropped (the owner's request).
- 0.34.0 — a project agent's row may carry `id_name`: the id the owner gave it (one of a group), which every tool then builds; its name, handle and project stay (the owner's request).
- Unreleased — frontmatter `interests`: its own project by default; it adds and drops its own (a vendor, a page, a tag). Woken by the Updater when something its interests name moved, it writes to other agents directly, and the park routine sends the lead no report (the owner's request).
- 0.27.0 — the role is final for sessions of its own: start, wake rules, the park routine, pause and end. A project agent is created by the `add-agent` skill and retired by the `projects` skill.
- 0.25.0 — role file created: the generic project role.
