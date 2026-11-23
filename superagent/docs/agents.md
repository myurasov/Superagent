# Agents

Superagent's work is divided among **agents**: roles that run skills. This page is the short tour. The rules are in `contracts/agents.md` (roles, ids, duties, wakes), `contracts/autonomy.md` (what an agent may do on its own), `contracts/update-loop.md` (fetch and tell) and `rules/autocode.md` (an agent that runs as a session of its own).

## The roster

| Agent | What it does | Where it stops |
|---|---|---|
| `superagent` (the lead) | Briefs, reviews, routes work, and is the one agent you talk to | It is the only role that asks you for a decision |
| `updater` | The update cycle: watched sources, drop folders, capture, reconcile | It never asks and never sends |
| `bookkeeper` | Bills, subscriptions, spending | It never pays and never cancels |
| `project:<slug>` | One agent for one Project | It works inside its project and ends with it |
| `<name>:dev` | The dev twin of a role: develops and maintains it | It is the only writer of the role file |

`uv run python -m superagent.tools.agents list` shows the roster with ids; `agents duties` shows each duty with its state. A new agent is made with the `add-agent` skill: a project agent is a registry row, a custom agent is a role file and its dev twin under `workspace/_custom/agents/`.

## Two ways to run

**One session, every role (the default).** Nothing runs by itself. The chat session is the lead and takes any other role the work needs ("act as the bookkeeper", "work on <project>"). `daily update` runs the update cycle and then the briefing.

**Agents as sessions of their own (Autocode mode).** Each agent is an OpenCode session registered with an Autocode server, and its duties run from wakes. It is off until you turn it on.

## Turning Autocode mode on

1. Set `preferences.autocode.enabled: true` in `_memory/config.yaml`, and the models and cost cap you want under `preferences.autocode`.
2. Run the owner-side settings in a terminal on the Autocode server's machine. `uv run python -m superagent.tools.autocode settings` prints the lines: cost caps, questions of the other agents routed to the lead, no expiry for idle agents.
3. Grant the wakes. Each periodic wake is yours to approve, with its exact terms (`agents` skill): for example `update-light` every 30 minutes and `update-full` once a day for the Updater, and the 08:30 briefing for the lead.
4. Start the agents: `uv run python -m superagent.tools.autocode start superagent`, then `start updater`. A project agent is started the same way and sits idle until written to.
5. Look at it: `autocode status` and `autocode wakes` (owner's shell), and the heartbeat lines in the daily briefing.

## What keeps it cheap and safe

- **A check runs before every wake.** Nothing due means no model call (`agents check <agent> <duty> --wake <name>`).
- **A wake with no grant never runs**, and an audit names it.
- **A run with nobody present never asks in chat, never sends, never pays.** Its questions wait in the asks queue, and the briefing shows them under "Waiting on your call".
- **One update cycle at a time, on one machine.**
- **State lives in files.** An agent parks after each piece of work: it writes its state, compacts and goes idle.
- **Work hours and off days** (`preferences.update.work_hours`, `off_days`) hold the scheduled update.

## Turning it off

`uv run python -m superagent.tools.autocode stop <agent>` for each agent (or pause them in Autocode), then `preferences.autocode.enabled: false`. Everything works as before: the chat session takes the roles again.
