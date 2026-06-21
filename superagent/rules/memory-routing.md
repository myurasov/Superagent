# Memory-routing guardrail — where "remember this" is allowed to write

[Do not change manually — managed by Superagent]

This rule is the memory-write specialization of `superagent/rules/scope-discipline.md`
(which forbids writes outside the project folder) and of the **Framework Artifact
Creation Contract** in `AGENTS.md` (which routes new artifacts between `superagent/`
and `workspace/_custom/`). It governs the specific case of durably recording
something on the user's behalf.

---

## 1. The rule

When the user asks the agent to **remember / save / note / persist / record /
keep track of** something, the agent writes ONLY inside this installation. There
are exactly three valid destinations:

1. **`workspace/_memory/`** — structured personal data and state. This is the
   DEFAULT for facts, mappings, preferences, entity rows, and logs. Use the
   canonical file for the data kind, e.g.:
   - merchant -> category overrides -> `_memory/expense-categories.yaml`
   - people -> `_memory/contacts.yaml`
   - durable model understanding of the user -> `_memory/model-context.yaml`
   - a dated touchpoint -> the right `history.md` / the events stream
2. **`workspace/_custom/`** — user-specific framework artifacts that are not
   generic enough for core: custom `rules/`, `skills/`, `agents/`,
   `templates/`, `tools/`.
3. **`superagent/`** — ONLY when the user EXPLICITLY asks for it to be "part of
   core" / "part of superagent" / "in the framework." Then implement it under
   `superagent/` per the Framework Artifact Creation Contract (whose safeguard
   re-routes any personally-identifying content to `workspace/_custom/`).

---

## 2. NEVER

- NEVER write remembered content to any path OUTSIDE this installation folder
  (the repository root). In particular, NEVER use:
  - `~/.claude/projects/<...>/memory/MEMORY.md` or anything under `~/.claude/`
  - Claude Code's built-in global / project "memory" store
  - `~/.cursor/...`, OS temp dirs, home-dir dotfiles, sibling repos, or any
    other "memory" feature the host IDE offers that writes outside this repo.
- If a host IDE exposes a built-in memory that persists outside this repo, DO
  NOT use it for Superagent state. Route to `_memory` / `_custom` instead.

---

## 3. Routing quick-reference

| User intent | Destination |
|---|---|
| "remember that <fact / mapping / preference>" | `workspace/_memory/<canonical file>` |
| "add a rule / skill / template just for me" | `workspace/_custom/<kind>/` |
| "make this part of core / superagent / the framework" | `superagent/` (framework code) |
| anything implying a file outside this repo | REFUSE, then re-route into the workspace |

---

## 4. On encountering an existing violation

If the agent finds remembered content already written outside the installation
(e.g. a prior session wrote to `~/.claude/.../memory/`):

1. Migrate it to the correct in-workspace home (`_memory` / `_custom`).
2. Ask the user BEFORE deleting the external original — deleting outside the
   repo needs explicit authorization per `scope-discipline`.

---

## Related rules and contracts

- `superagent/rules/scope-discipline.md` — the general "no writes outside the
  project folder" rule this one specializes.
- `AGENTS.md` § "Framework Artifact Creation Contract" — superagent-vs-_custom
  routing for new artifacts, plus the personal-data safeguard.
- `contracts/provenance.md` — schema for the `provenance` field captured facts
  carry.
