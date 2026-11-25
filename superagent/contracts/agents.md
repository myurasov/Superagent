# Agents Contract (roles, instances, ids, dev twins, duties)

<!-- Citation form: `contracts/agents.md`. -->

Superagent's work is divided among **agents**. An agent is a **role** that runs **skills**: skills stay the single source of behavior, and no logic lives only in an agent. This contract defines what a role file is, how a role becomes a running instance, how ids are built, who may change a role, and what a duty is. What an agent may do on its own is `contracts/autonomy.md`.

Agents do not need a runtime. With no separate agents running (the default), one chat session takes whichever role the work needs, and everything in this contract still holds. Autocode is an optional runtime that keeps agents running as registered sessions (§ 9).

## 1. Glossary

| Term | Meaning |
|---|---|
| **role** | What an agent is for: a markdown file `<name>.agent.md` with frontmatter (§ 2). Generic and committed, or custom in the overlay. |
| **instance** | One agent of a role: where it runs and what it was granted. A row in `_memory/agents.yaml` (§ 5). |
| **lead** | The one role the owner talks to (`superagent`). It talks with the owner and briefs, decides what the agents cannot settle among themselves, coordinates the agents and the workspace, and asks the owner for decisions. It relays no messages (§ 8a). |
| **durable role** | A role with standing work (`updater`, `bookkeeper`). One instance. |
| **project role** | The generic role for work inside one Project (`project`). One instance per project that has an agent. |
| **dev twin** | The agent that develops and maintains a role: `<name>.agent.dev.md` beside the role file (§ 6). |
| **duty** | A skill a role runs on a schedule, behind a zero-cost check (§ 3). |
| **id** | `<prefix>:<instance name>`, the name an agent registers under (§ 4). |

## 2. Role files

```
superagent/agents/<name>.agent.md            framework role (committed)
superagent/agents/<name>.agent.dev.md        its dev twin
superagent/agents/_manifest.yaml             the roster, generated (§ 10)
workspace/_custom/agents/<name>.agent.md     custom role, or an overlay on a
workspace/_custom/agents/<name>.agent.dev.md   framework role of the same name (§ 7)
```

A role file opens with YAML frontmatter. Every field is required except `interests`; an unknown field is an error.

| Field | Value | Meaning |
|---|---|---|
| `name` | `^[a-z][a-z0-9-]{0,30}$`, equal to the filename stem | The role's name. Reserved: `dev`, `owner`, `autocode`, `me`. |
| `kind` | `lead` \| `durable` \| `project` | Exactly one role is the lead. |
| `description` | one or two lines | What the role is for and where it stops. |
| `skills` | list of skill stems, or `all` (lead only) | The skills the role runs as its job. Each must exist (`superagent/skills/` or `workspace/_custom/skills/`). A durable or project agent stays inside this list and hands anything else to the lead. |
| `owns` | list of workspace-relative paths | The stores the role is the main writer of (`_memory/bills.yaml`, `Projects/<slug>/`). Another role that needs a change there asks the owner of the store. |
| `authority` | `act` \| `approve` \| `prepare` \| `owner` | The loosest autonomy tier the role may use (`contracts/autonomy.md` § 2). |
| `model_tier` | `runner` \| `dev` | Which default model the role uses (`config.preferences.autocode.models.<tier>`). |
| `duties` | list (may be empty) | The role's duties (§ 3). |
| `interests` | list (optional) | What the role's agents ask the update cycle to tell them about (§ 8a): one mapping each, `pattern`, `why`, and optionally `when` and `fire`. A project role writes `project:<slug>` for the project of each instance. |

The body of the file is the role in words: purpose, what it does, boundaries, hand-offs. Templates: `templates/agents/agent.md` and `templates/agents/agent.dev.md`.

The helper personas that have no file of their own (Concierge, Quartermaster, Medic, Coach, Archivist) are sections of `agents/superagent.agent.md`: modes of the lead, with no frontmatter and no instance.

## 3. Duties

A duty is one mapping in the role's `duties` list. Every field is required.

| Field | Value | Meaning |
|---|---|---|
| `id` | `^[a-z][a-z0-9-]{0,40}$`, unique in the role | The duty's name (`agents check <agent> <id>`). |
| `skill` | a stem from the role's `skills` | The skill a run of the duty executes. |
| `tier` | `act` \| `approve` \| `prepare` \| `owner` | The autonomy tier of the duty, never looser than the role's `authority`. |
| `schedule` | a key of `config.preferences.cadences`, `watchlist`, or `on-request` | When the duty runs. Schedule **values** stay in `preferences.cadences` (`weekly_review: "Sunday 18:00"`); the role names the key. The key also names the duty's stamp, `context.yaml.last_runs.<key>`. `watchlist` means each watcher's own cycle and `on-request` means the duty has no schedule; such a duty is stamped under its id (`_` for `-`). |
| `check` | `cadence` \| `update` \| `watchers` \| `none`, or `tool:<name>` in a custom role | The zero-cost check that says whether the duty is due. |

**A duty's state** is one of `ok`, `due`, `overdue`, `unfinished` and `on-request`. **Due** means the scheduled time has come and the duty has not run since, or its check found work: it is what a scheduler acts on. **Overdue** means late: it is what an attended session is told at open. `contracts/update-loop.md` § 6 defines both, the stamps and the schedule values.

**Checks** are Python and cost no model call:

- `cadence` — due by the stamp: the duty's stamp is older than the latest scheduled time of its `preferences.cadences` value (`tools/cadence.py` `duty_due`). Overdue by the nag rules for the duty's skill (`overdue_cadences`; for `daily-update`, a `last_check` older than 24 hours). A run that began and never finished is `unfinished`.
- `update` — the update duty's check. Due at every look: every slot of the duty's wake runs an update. Its reason names the watchers that are due (`tools/watchlist.py` `due_rows`) and the documents that wait in a drop folder (`tools/inbox.py`). What holds such a run is outside the check: the update's work hours and off days, the grant and the cost cap. Overdue when a healthy watcher's last success is older than its fastest cycle's period plus one day (`overdue_watchers`).
- `watchers` — the overdue half of `update` alone, for a role that watches and does not drain.
- `none` — never due by a check. An `on-request` duty uses it.
- `tool:<name>` — a custom role's own check: the script `workspace/_custom/tools/<name>.py`, run with `--workspace <path>`. Exit 0 means due and what it prints is the reason; exit 1 means not due; any other exit is a failed check (exit 3 from `agents check`). It reads local files and writes nothing. A listing shows the state `custom`; only `check` runs the script. A framework role cannot name one.

**Held.** A duty whose skill is `update` is held outside `config.preferences.update.work_hours` and on its `off_days`: its state stays what it is, and `agents check` exits 1 (`contracts/update-loop.md` § 6).

`uv run python -m superagent.tools.agents check <agent> <duty>` runs the check of one duty: **exit 0** when the duty is due, overdue or unfinished (the reason is printed), **exit 1** when it is not (nothing is printed), 2 for an unknown agent or duty, 3 when the check could not run. A scheduled wake runs this first, so a wake with nothing to do costs nothing.

`uv run python -m superagent.tools.agents duties` lists every duty with its schedule, last run and state; `--due` keeps what a scheduler would run now, `--overdue` only what is late. `uv run python -m superagent.tools.cadence status` prints the late findings as lines, each starting with the agent it belongs to in brackets, grouped by agent. A finding no role claims belongs to the lead.

## 4. Ids and the prefix

An id is `<prefix>:<instance name>`.

- **Prefix**: `config.preferences.autocode.id_prefix`, default `sa`. It lets several users share one Autocode server. A lowercase letter first, then lowercase letters, digits or `-`, 16 characters at most. Reserved and refused: `owner`, `autocode`, `me`, `project`, `dev`.
- **Instance name**: the role name for a lead or durable role (`updater`); `project:<slug>` for a project agent, where the slug is the Project's slug; `<role>:dev` for a dev twin. One dev twin serves every agent of the project role (`project:dev`), so `dev` is refused as a project slug.
- **Nothing hardcodes a prefix.** Every id, lock name and settings pattern is built from the configured prefix: `uv run python -m superagent.tools.agents id <name>` prints an id (`--dev` for the dev twin). Role files, skills and docs write `<prefix>:<name>`.
- **Handle**: `agent:<instance name>` (`agent:updater`, `agent:project:<slug>`), per `contracts/operational-handles.md`. Messages and logs name an agent by its handle.

## 5. The registry: `_memory/agents.yaml`

One file holds every instance (template `templates/memory/agents.yaml`; entity-shape per `contracts/memory-taxonomy.md`). A row carries `name` (the instance name), `role`, `handle`, `project` (for a project agent), `host` (the machine that runs it), `model` / `variant` / `daily_usd` (each overrides the default in `preferences.autocode`), `wakes` (the granted periodic wakes, § 8), `interests` (what the agent wants to hear about, § 8a), `status`, `notes`, `provenance`.

- **Ids are never stored**: a row holds the name, and the id is built from the prefix.
- **A lead or durable role is one agent with or without a row.** It needs a row only to carry a setting (a host, a model, a grant).
- **A project agent exists only through its row.** No role file is written per project.
- **`status`**: `off` (no session runs it; a chat session takes the role; the default), `active` (a registered session runs it with its granted wakes), `dormant` (a registered session holds it and wakes on a message), `paused`, `retired`.
- `uv run python -m superagent.tools.agents list` shows the roster: every role, its instances, their ids, status, host and dev twin, and names any row that fits no role.
- A workspace without the file has an empty registry.

## 6. Dev twins

Every role has a dev twin, framework and custom alike; a role file without one is an error.

- **File**: `<name>.agent.dev.md` beside the role file, with frontmatter `develops: <name>` and `model_tier: dev`. One `project.agent.dev.md` serves the generic project role.
- **Content**: purpose and design notes, the wake and check design, cost targets, how to test (dry-run a duty, check exit codes), how to deploy, how to observe (what the role did, what a wake cost), rollback, a dated pitfalls log, a changelog.
- **The dev twin is the only writer of the role file, its duties and its checks.** The running agent never edits its own instructions.
- **Change requests**: the running agent files an action signal with `target: tailor`; the `supertailor-review` skill turns signals and its own findings into suggestions, each naming the agent it concerns. The dev twin of that agent builds an approved suggestion.
- **Rules**: a framework role follows `rules/framework-changes.md` (the gate, the personal-data safeguard, tests, commit rules). A custom role is edited in `workspace/_custom/` directly.
- **Deploy**: build, test, deploy, then send the running agent one message, `Pick up: <the change in one line>`, so it re-reads its role file.
- **Lifecycle**: dormant, with no wakes. "Develop the <role>" starts it. With no separate agents running, the chat session takes the dev role: it reads `<name>.agent.dev.md` and `rules/framework-changes.md` and works as the twin.

## 7. Overlay and custom roles

Per `contracts/custom-overlay.md`:

- A file in `workspace/_custom/agents/` named like a framework role is an **overlay** on it: extra boundaries and preferences, merged on top of the framework role. It never weakens the role's safety rules, needs no frontmatter, and is not a second role.
- Any other `<name>.agent.md` there is a **custom role**. It needs the full frontmatter and its own dev twin, and its name differs from every framework role. Its checks live in `workspace/_custom/tools/` and its skills in `workspace/_custom/skills/`.
- A custom role carries workspace-specific content by design. The Framework Artifact Creation Contract (`contracts/framework-artifacts.md`) decides whether a new role is generic (`superagent/agents/`) or custom; the default is custom.

## 8. Wakes

A wake is a prompt a runtime types into an agent's session later. The rules hold whatever the runtime:

- **Every periodic wake is a recorded grant the owner approved**: its exact trigger, purpose, cost per run and end date, on the instance's registry row (`wakes`). A periodic wake with no grant must not run.
- **`agents grant <agent> --wake <name> --trigger ... --purpose ... --approval "<the owner's words>"`** records a grant, after the owner said yes to those terms; the tool cannot see an approval, so the skill asks first. `--suspend` / `--resume` switch grants off and on (a paused project), `agents revoke` removes one.
- **The check of a granted wake** is `agents check <agent> [<duty>] --wake <name>`: it exits 1 when the row has no grant of that name, or the grant is suspended or past its end, and otherwise runs the duty's check. A wake with no duty is due whenever its grant holds. So a wake that was set without a grant never costs a model call, and an audit names it (`tools/autocode.py wakes`; an agent compares its own wakes with its grants). Under Autocode the wake's check line is `sh superagent/tools/wake_check.sh <agent> [<duty>] --wake <name>`: it finds `uv` on any `PATH` and runs `tools/autocode.py check`, which puts the daily cost cap in front of this check.
- **A runtime may have a gate of its own** (Autocode's `wake_mode`). It is separate from the grant and never replaces it.
- **Every periodic wake runs a check first** (§ 3). Nothing due means no model call.
- **A project agent and a dev twin have no periodic wake by default.** They wake on a message.
- **A one-time self-wake needs no grant** when all of these hold: the owner started the work it belongs to; it fires once and belongs to that task; it lies at most 24 hours ahead; at most 3 follow one another for the same task. The agent names each one when it sets it.

## 8a. Interests, and who hears what

- **An interest** is one kind of update an agent wants to hear about. It is a mapping on the agent's registry row (`interests`): a `pattern` (a handle or a handle glob: `project:<slug>`, `domain:finances`, `contact:<slug>`, `tag:<tag>`, `watch:<id>`, `bill:*`), its `why`, an optional `when` that narrows it to one kind of update (`changed`: a watcher saw a change; `harvest`: a harvest brought new rows; `filed`: a document was filed; `captured`: a fact landed in a store), and an optional `fire` (a granted `fire-only` wake to fire in place of a message).
- **The agent sets its own.** `agents interest <agent> --add <pattern> --why "..."` records one, `--remove <pattern>` drops one, and `--defaults` writes the ones its role file names. Twelve per agent at most. The role that runs the update cycle and a dev twin keep none. An interest needs no approval; a wake it fires is a grant like any other (§ 8). The owner sees every agent's interests in the daily briefing (`agents heartbeat`) and can have any dropped.
- **The Updater tells.** After a cycle it asks, for each thing that moved, who is interested: `agents interested --when <kind> --handle <its handle> --handle <each handle it relates to>`. Each such agent gets one message per cycle that names what moved by handle and path, or the wake its interest names is fired. Only an agent a session holds (`active` or `dormant`) is told; with no separate agents the run report is the hand-off.
- **Agents write to each other directly**, by id. Nothing is relayed through the lead.
- **The lead is told only** what needs a decision the agents cannot settle among themselves or one about the workspace, what the owner must hear before the next briefing, and an agent's start, end and reached cost cap. Everything else the lead reads from the stores when it briefs: no agent reports to it after a piece of work, and the Updater sends it no run report.
- **The owner's answer is passed on by the tool.** `tools/autocode.py answer <ask-id> --answer "..."` records the answer on the ask and tells the agent that asked, when a session holds it.

## 9. With and without Autocode

- **Off (the default).** No agent runs as a session of its own. The chat session takes the lead's role and any other role the work needs: it reads that role's file (and its overlay) and works inside its boundaries. The on-open check (`cadence status --stamp`) names what is late and whose duty it is. Every tool in this contract works in this mode.
- **On** (`config.preferences.autocode.enabled: true`). The owner turns it on by saying "start superagent": the `start` skill asks in question cards (the switch; the settings, to approve or change; which other agents to start), the tool writes what was approved (`tools/autocode.py setup enable`, `setup apply`), the chat session registers as the lead, and the other agents start in the background. The owner edits no file and runs no command, and nothing is turned on unasked (`init` asks once, where Autocode can run). An agent runs as a session registered with an Autocode server under its id (§ 4), and its duties run from granted wakes behind their checks. `rules/autocode.md` is the rule such a session follows. `tools/autocode.py` is the one module that calls the runtime: `start` (a session with the start prompt and the model of the agent's tier), `park`, `stop`, `retire` (a message to the agent, then the row), `wakes` and `status` (audits, in the owner's shell), `settings` (prints the owner-side lines). The registry row says what is meant to run: `active` (a session with granted wakes), `dormant` (a session that wakes on a message), `off`, `paused`, `retired`.
- **One machine fetches.** The `active` row of the role that runs the update names its `host`; `agents may-fetch` exits 1 on every other machine (`contracts/update-loop.md` § 3).
- **The setup is proposed from the roles.** A duty the role may run on its own (tier `act`) with a schedule in `preferences.cadences` and a check gets a proposed wake named after the duty, at the schedule's times; the owner approves or changes the terms, and each becomes a grant carrying the owner's words (§ 8). A time the owner changes is written to the grant and to the cadence value together (`setup apply --wake <agent>:<wake>=<time>`), since the duty is due by its cadence value; a wake that is already granted keeps its trigger until the owner changes it. The Bookkeeper's wake is fired by the Updater. A project agent gets none.
- **Autocode's own settings are not needed.** Agents run on its defaults. The daily cost cap is Superagent's: `preferences.autocode.daily_usd`, or `daily_usd` on a registry row, kept by each wake's check (at or past it the scheduled runs are skipped until the next day, with one notice a day). An agent can never set an Autocode setting; `tools/autocode.py settings` prints optional hardening lines for the owner.
- **The framework never needs Autocode.** No other tool imports `tools/autocode.py` (a test enforces it), and the test suite passes without the runtime.
- **Compaction** is the harness's own (and Autocode's for a registered agent). The framework ships no compaction mechanism.

## 10. Manifest and tooling

- `agents/_manifest.yaml` is the roster in one small file: one row per role with its kind, description, skills, duties and dev twin. It is generated by `uv run python -m superagent.tools.build_agent_manifest` (`--check` exits 1 when it is out of date) and carries no timestamp. The tools read the role files themselves, so a stale manifest never changes behavior.
- `tools/agent_roles.py` is the library (frontmatter, validation, ids, the prefix). `tools/agents.py` is the CLI (`list`, `id`, `duties`, `check`, `set`, `grant`, `revoke`, `may-fetch`, `heartbeat`). `tools/autocode.py` is the runtime side (§ 9), and the `start` skill is how the owner starts and stops it. The `add-agent` skill creates a project agent or a custom role. The `agents` skill is the way in for a person.
- Tests keep the contract: the manifest is in step with the role files, every frontmatter is valid, every role has a dev twin, every referenced skill exists, and no role file or agent tool carries personal data or a hardcoded prefix.
