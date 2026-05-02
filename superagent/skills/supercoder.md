---
name: superagent-supercoder
description: >-
  Build standalone coding projects under workspace/Code/<slug>/. Subcommands:
  new / list / open / status / work / close / archive. Self-contained per
  procedures.md § 40; the Supercoder cannot write outside the active project.
triggers:
  - supercoder
  - supercoder new
  - supercoder open
  - supercoder list
  - supercoder status
  - supercoder work
  - supercoder close
  - supercoder archive
  - new code project
  - start a code project
  - work on <project>
  - what code projects am I running
  - list code projects
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent supercoder skill

This is the Mode 2 (project build) entry point. The full role definition is in `superagent/supercoder.agent.md`; the data contract is in `superagent/procedures.md` § 40 ("Code Projects Contract"); the `.supercoder/` template lives in `superagent/templates/code-projects/`.

**Always-on safeguard**: every file write in this skill MUST pass the path-scope check (`procedures.md` § 40.4). Refuse-and-explain on any attempted write outside the active project's folder, with the documented exception list. The user CANNOT disable this safeguard from within a `supercoder work` session.

## 1. Branch on intent

Parse the user's request into one of: `new` / `list` / `open` / `status` / `work` / `close` / `archive`. Default when no subcommand is given:

- If the user names a slug → treat as `open <slug>`.
- If the user describes a new effort ("start a project that…") → `new`.
- Otherwise → `list`.

### `new <slug> "<purpose>"` — bootstrap a new project

1. **Validate the slug**. Kebab-case, ≤ 40 chars, unique within `_memory/code-projects-index.yaml.projects[].slug`, and `workspace/Code/<slug>/` does not yet exist. Refuse with a clear message on any violation.
2. **Charter dialogue (4–6 questions)**:
   - Goal (one sentence — the user already gave a one-line purpose; expand to a sentence).
   - Scope: 3 in-scope bullets, 3 out-of-scope bullets.
   - Success criteria: 3 checkable items.
   - Language stack: `python | typescript | javascript | rust | go | mixed | docs`.
   - Test command (e.g. `pytest`, `npm test`, `cargo test`, or `(none)`).
   - Target completion date (or "ongoing").
3. **Scaffold**. Create `workspace/Code/<slug>/` and:
   - Render the `.supercoder/info.md`, `.supercoder/status.md`, `.supercoder/history.md` from `superagent/templates/code-projects/.supercoder/*.md`, substituting `{{PROJECT_NAME}}`, `{{SLUG}}`, `{{GOAL}}`, `{{IN_*}}`, `{{OUT_*}}`, `{{SUCCESS_*}}`, `{{LANGUAGE}}`, `{{TEST_COMMAND}}`, `{{REPO}}`, `{{LICENSE}}`, `{{CREATED_AT}}`, `{{TARGET_COMPLETION_AT}}`, `{{LAST_UPDATED}}`, `{{RAG}}`, `{{RAG_NOTE}}`, `{{OPEN_PLACEHOLDER}}`, `{{DONE_PLACEHOLDER}}`, `{{CREATED_AT_DATE}}`.
   - Copy the empty `.supercoder/decisions.yaml` (set `last_updated`).
   - Copy `.gitignore` (Python default — the user can swap it).
   - Render `README.md` from the template.
4. **Optional scaffold**. Ask the user once: "Want a starter scaffold for `<language>`? (Python: `pyproject.toml` + `src/<slug>/__init__.py` + `tests/`; Node: `package.json` + `src/`; …)". If yes, scaffold; if no, leave the source tree empty.
5. **Register**. Append a row to `workspace/_memory/code-projects-index.yaml.projects[]` with `status: planning`. Update `last_updated`.
6. **Set active**. Update `workspace/_memory/context.yaml.active_code_project: <slug>` and `last_updated`.
7. **Log**. Append a row to `workspace/_memory/interaction-log.yaml` with `kind: code-project`, `action: new`, `slug`, `outcome: scaffolded`.
8. **Print**:

   ```
   Project `<slug>` ready.
     Charter: workspace/Code/<slug>/.supercoder/info.md
     Status:  planning
     Active:  yes
   Run `supercoder work "<instruction>"` to start.
   ```

### `list` — list every code project

1. Read `workspace/_memory/code-projects-index.yaml.projects[]`.
2. Filter by status (default: exclude `archived`).
3. Group by status: `active` first, then `planning`, then `paused`, then `completed`.
4. Within each group, sort by `last_touched_at` descending.
5. Render one line per project:

   ```
   <slug>  [<status>]  <name>  (<language>, last touched <relative-time>)
     <summary>
   ```

6. Mark the active project with a leading `*`.
7. Footer line: "Active: `<slug>`. Run `supercoder open <slug>` to switch, `supercoder new <slug> '<purpose>'` to create."

### `open <slug>` — set active

1. Verify the slug exists in the index. Refuse with the available list if not.
2. Update `workspace/_memory/context.yaml.active_code_project: <slug>`.
3. Read the project's `.supercoder/info.md` (charter) + `status.md` (open / done) and surface a one-paragraph "where you left off" summary.
4. Log to `interaction-log.yaml` (action: `open`).

### `status [<slug>]` — charter + RAG + tasks

1. Resolve target: explicit slug, else active.
2. Read the project's `.supercoder/info.md` (Goal, Stack, Dates) + `.supercoder/status.md` (RAG, Open, Done).
3. Render in this order:

   ```
   <slug> — <name> (<language>)
   Goal: <one-sentence goal>
   Stack: <language>, tests `<test_command>`, repo: <repo>
   Status: <RAG> — <RAG_NOTE>
   Open: <count>
     [P0] …
     [P1] …
   Done (this week): <count>
     …
   Target completion: <date or "ongoing">
   Last touched: <relative-time>
   ```

### `work "<instruction>"` — iterate on the active project

1. **Resolve active**. Read `context.yaml.active_code_project`. Refuse with `list` output if none active.
2. **Read state**. Charter (`info.md` — only the Goal, Scope, Stack, Test command sections; skip Notes unless the instruction mentions a note). Status (`status.md`). The user's instruction.
3. **Plan**. List the file changes you'll make, scoped to the project folder. Surface the plan; ask for confirmation if the plan touches > 3 files OR involves any deletion.
4. **Implement** with the **path-scope safeguard** active on every write. Allowed paths:
   - `workspace/Code/<active-slug>/**` — yes.
   - `workspace/_memory/code-projects-index.yaml` — only the active project's row.
   - `workspace/_memory/context.yaml.active_code_project` — only this field.
   - `workspace/_memory/interaction-log.yaml` — append-only.
   - `workspace/_memory/decisions.yaml` — append-only AND only if the user explicitly says "log this decision" or equivalent.
   - **Anything else → REFUSE**. Surface:

     ```
     Refusing to write outside project scope.
       active project: <slug>
       attempted write: <path>
     That path belongs to <framework | another project | personal data>.
     Route via the right skill (tailor-review for framework changes;
     supercoder open <other> to switch projects; the relevant capture
     skill for personal data).
     ```

5. **Test**. Run the project's test command (`info.md` Stack section). If `(none)`, ask the user whether to declare one (and persist their answer into `info.md`). Skip tests if the user explicitly says "skip tests this time".
6. **Commit** (only if `Code/<slug>/.git/` exists):
   - One imperative sentence, ≤ 72 chars, ASCII only.
   - Strip the auto-injected Cursor `Made-with` trailer per `AGENTS.md` § "Strip-after-commit". (Or rely on `<project>/.githooks/commit-msg` if installed.)
7. **Update state**:
   - Append H4 entry to `.supercoder/history.md`: `#### <YYYY-MM-DD> — <one-line summary>` + bullets for files touched, test result, commit sha (if any).
   - In `.supercoder/status.md`: move completed tasks from Open to Done; surface any new tasks the user mentioned.
   - In `_memory/code-projects-index.yaml`: update only the active project's `last_touched_at` and (if the user signaled completion) `status` and `summary`.
   - Append to `interaction-log.yaml` with `action: work`, files-touched count, test result, commit sha.
8. **Print**:

   ```
   Worked on `<slug>`: <one-line summary of what changed>.
     Files touched: <N>
     Tests: <pass | fail | skipped | none>
     Commit:  <short-sha or "(no repo)">
   ```

### `close <slug>` — mark complete

1. Verify the slug exists and is in `active` or `paused`. Refuse otherwise.
2. Update the index row: `status: completed`, refresh `last_touched_at`, prompt for an updated `summary` (default: keep current).
3. Append to `.supercoder/history.md`: `#### <YYYY-MM-DD> — Project closed`.
4. Log to `interaction-log.yaml` (action: `close`).
5. Print: "`<slug>` marked completed. The folder stays under Code/; run `supercoder open <slug>` to reopen."

### `archive <slug>` — move to Archive/

1. Verify the slug exists and is in `completed` (or `cancelled`).
2. Move the entire project folder from `workspace/Code/<slug>/` to `workspace/Archive/code/<slug>/`. Create `workspace/Archive/code/` if missing.
3. Update the index row: `status: archived`, `path: Archive/code/<slug>`.
4. If the active project was this one, clear `context.yaml.active_code_project`.
5. Log to `interaction-log.yaml` (action: `archive`).
6. Print: "`<slug>` archived. Folder now at `workspace/Archive/code/<slug>/`. Reversible — `supercoder open <slug>` will move it back."

## 2. Cross-cutting rules

- The Supercoder in this skill is in **Mode 2**. It MUST NOT touch `superagent/`, `workspace/Domains/`, `workspace/Projects/`, `workspace/Sources/`, or any other `workspace/Code/<other-slug>/` folder.
- The Supercoder does NOT auto-init git inside the project folder. The user runs `git init` themselves when they want history.
- If the project's `info.md.repo` is `none`, skip the commit step on every `work` cycle. The `.supercoder/history.md` log is the only history.
- If a `work` instruction would require reading personal data (e.g. "use my contact list" / "pull my expenses"), refuse with: "That requires personal-life data. Capture it via the right Superagent skill (e.g. `add-contact`, `expenses`) and re-state the project request without referencing personal data."

## 3. Logging

- Every subcommand appends one row to `workspace/_memory/interaction-log.yaml`.
- The summary line in the response is the user-visible artifact; the index + history.md updates are the persistent artifact.
- Failures append a row with `outcome: failed` and a one-line cause. Do not retry silently.
