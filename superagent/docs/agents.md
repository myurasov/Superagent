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

Say **"start superagent"** in a session. You answer three question cards, and Superagent does the rest; you edit no file and run no command.

1. **The switch.** "Run Superagent's agents in the background?" A yes turns the mode on.
2. **The settings.** You see the proposed schedule (the briefings at their cadence times, the light update every 30 minutes, the full update once a day), the work hours of the scheduled update, the daily cost cap per agent and the model. Approve them, change any of them, or start idle with no schedule.
3. **The other agents.** Pick which ones start with the lead: the Updater (recommended), the Bookkeeper, a project agent.

Then the session you are in becomes the lead, and the agents you picked start in the background. Each wake you approved is recorded as a grant with your words. "Change the agents' schedule" changes a time later; "stop superagent" stops everything.

Autocode's own settings stay at their defaults. `uv run python -m superagent.tools.autocode settings` prints a few optional lines for an owner who wants more (a hard cap enforced by Autocode, no expiry for idle agents).

## What keeps it cheap and safe

- **A check runs before every wake.** Nothing due means no model call.
- **A daily cost cap per agent**, when you set one: at the cap an agent's scheduled runs are skipped until the next day, and you are told once.
- **A wake with no grant never runs**, and an audit names it.
- **A run with nobody present never asks in chat, never sends, never pays.** Its questions wait in the asks queue, and the briefing shows them under "Waiting on your call".
- **One update cycle at a time, on one machine.**
- **State lives in files.** An agent parks after each piece of work: it writes its state, compacts and goes idle.
- **Work hours and off days** (`preferences.update.work_hours`, `off_days`) hold the scheduled update.

## Turning it off

Say **"stop superagent"**. Superagent asks which agents to stop, stops them, and offers to turn the mode off. Everything then works as before: the chat session takes the roles again. The approved settings stay recorded for the next start.
