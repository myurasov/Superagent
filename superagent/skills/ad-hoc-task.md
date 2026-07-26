---
name: superagent-ad-hoc-task
description: >-
  Start or resume an ad-hoc task — engineering scratch, system setup,
  investigation, or research that is not (yet) a tracked Project and has no
  Domain home — under a dated folder `tasks/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/`
  at the repo root (sibling of `workspace/`, outside it). No 4-file structure, no
  index registration, no versioning: just a folder with a `notes.md`
  (What/why, Steps, Findings, Outcome) that is the durable record, plus any
  scratch files the work produces. Supports resuming an existing task folder
  and graduating work that turns durable into a tracked Project
  (`add-project`) or a Sources import.
triggers:
  - new task / start a task / ad-hoc task
  - 'ad-hoc: <anything>'
  - resume task / work on tasks/<slug> / open tasks/<slug>
  - investigate X / research X / figure out X (when it has no Domain or Project home)
  - set up <host / tool / thing>
  - one-off engineering or debugging work that doesn't belong to a Domains/ or Projects/ entity
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent ad-hoc-task skill

The catch-all for work that is **not a project and not domain data**: a
one-off investigation, a host or tool setup, a debugging session, a research
question, an experiment. No 4-file structure, no `projects-index.yaml`
registration, no versioning — just a dated folder with a `notes.md` and
whatever scratch the work produces. The folder listing **is** the index;
there is no `_index.yaml`.

**Location: `tasks/` at the repo root** — a sibling of `workspace/` and
`superagent/`, deliberately **outside** the workspace. Resolve it as
`<workspace_path>/../tasks/` after reading `_memory/config.yaml`
(`preferences.workspace_path`; default sibling layout gives
`<repo-root>/tasks/`). The folder is gitignored and lazily created on first
use — nothing to scaffold beforehand.

Boundaries — route elsewhere when one of these fits better:

- **Tied to a Domain or tracked Project** → work under
  `workspace/Domains/<domain>/Resources/` or
  `workspace/Projects/<slug>/Resources/` and log to that entity's
  `history.md`. A task folder is for work with no such home.
- **A to-do item to track, not work to do now** → the `todo` skill
  (`_memory/todo.yaml`). `tasks/` holds *working folders*, not the task
  tracker; the two are unrelated despite the name.
- **Personal-life data captured along the way** (a contact, a bill, a
  receipt, an event) → the normal capture skills and workspace homes;
  opportunistic retention applies as everywhere else. The task folder is
  never the permanent home for workspace data.
- **Reference material worth keeping** → file into
  `workspace/Sources/<your-folders>/` per `contracts/sources.md`, never
  stashed loose in the task folder as its permanent home.

## 1. Start or resume

1. Read `workspace/_memory/config.yaml` (standard preflight) and resolve
   the tasks root as `<workspace_path>/../tasks/`.
2. Pick a short **kebab-case slug** for the work (e.g.
   `nas-disk-cleanup`, `router-firmware-research`). Folder:
   `tasks/<YYYY>/<MM>/<YYYY-MM-DD>-<slug>/` — year and zero-padded month
   partitions, then the fully-dated folder name; the date is the **start**
   date. Example: `tasks/2026/07/2026-07-26-nas-disk-cleanup/`.
3. **Resume check**: list the current and previous month's partitions
   (`tasks/<YYYY>/<MM>/`) for a folder whose slug matches the request
   (same topic, different wording counts). If found, resume it — read its
   `notes.md` first, then continue appending. Do not create a second
   folder for the same piece of work.
4. Otherwise create the folder (lazily creating `tasks/<YYYY>/<MM>/` on
   first use) and seed `notes.md`:

   ```markdown
   # <Title>

   <!-- Agent: this is an ad-hoc task. Load and follow the `ad-hoc-task`
        skill (superagent/skills/ad-hoc-task.md) before working in this
        folder. -->

   What / why: <one or two lines>

   ## Steps

   ## Findings

   ## Outcome
   ```

   `notes.md` is agent/user working notes, not a regenerated living
   document — append freely.

5. **Constraints accrete.** When the user adds or changes a constraint or
   preference after the task has started ("must be X", "actually prefer
   Y"), append it as a dated line under `What / why` — a resumed task
   must read the *current* rules, not the original ones.

## 2. Do the work

- **Engineering / scratch**: throwaway scripts, outputs, and small data
  files live **inside the task folder** — that keeps the record
  self-contained. Python scratch runs through the root venv (`uv run
  python …`) or `uv run --no-project python …` for standalone snippets,
  per `rules/development-tooling.md`. Do **not** create a venv inside the
  task folder — the repo usually sits in iCloud; if an isolated env or a
  large intermediate (model download, big log capture) is truly needed,
  stage it under `./.tmp/` and promote only the keepers into the task
  folder.
- **System setup / remote hosts**: show mutating or destructive commands
  before running them and confirm first — the scope-discipline safety rule
  applies with full force here since ad-hoc work often reaches outside the
  repo.
- **Research**: local-first read order applies as everywhere else; capture
  key findings in `notes.md` as you go, with links / citations.
- **Time-sensitive items get a todo immediately.** A deadline, auction
  close, expiring offer, or dated follow-up discovered mid-task goes into
  `_memory/todo.yaml` (with a due date) via the `todo` skill the moment it
  surfaces — not at close. The task folder is a working area, not a
  reminder system.

## 3. Capture and close

1. Keep `notes.md` current — steps tried (including dead ends), findings,
   and the outcome. It is the durable record; a task folder with stale
   notes is just clutter.
2. When the work concludes (done, blocked, or abandoned), fill in
   `## Outcome` with one short paragraph: what was achieved / decided /
   learned, and any follow-up that was spun off (todo entry, project).
3. Append an entry to `workspace/_memory/interaction-log.yaml`,
   `skill: ad-hoc-task`, per its schema — **once per significant working
   session**, not only at final close (a multi-session task gets a row
   each time real work happens). Set `related_domain` when the task
   obviously touches a domain (e.g. a vehicle purchase search →
   `vehicles`) so the domain's timeline can find it later.
4. Opportunistic retention applies as usual: anything legitimately
   encountered that belongs in the workspace (a contact, an account, a
   document) gets captured to its proper home with `provenance`.

## 4. Graduate (optional)

If the task turns into something durable, offer to promote it — the task
folder **stays in place as history** (optionally with a one-line pointer to
the new home appended to `## Outcome`):

- **A time-bounded effort with a goal and target date** →
  [`add-project`](add-project.md) (4-file structure +
  `projects-index.yaml` registration).
- **Reference material worth keeping** → file into `workspace/Sources/`
  and refresh the sources index.
- **Recurring follow-ups** → `todo` skill entries linked to the new
  entity.

Graduation is a copy/promote, not a move — do not gut the task folder.

## 5. Archival

Stale or concluded task folders archive to
`tasks/_archive/<YYYY>/<MM>/<folder>/` (preserving the year/month
partition). Archival is manual / on-request — nothing auto-deletes or
auto-archives task folders.
