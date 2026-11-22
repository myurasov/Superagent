# Rule: Framework changes

How a change to the framework is built. Every **dev twin** follows this rule when it edits `superagent/` (`contracts/agents.md` § "Dev twins"), and so does a chat session that takes a dev role because no separate agent is running. The same workflow, minus the safeguard and the commit, applies to an approved change that lands in the overlay `workspace/_custom/`.

This rule replaces the Supercoder role of earlier versions: implementing is a discipline every dev twin keeps, and no agent of its own.

## 1. The gate

A framework change starts from one of two things, and nothing else:

- **An approved suggestion**: a row in `_memory/supertailor-suggestions.yaml` with `status: approved` and an explicit `destination` (`superagent` or `_custom`). The `supertailor-review` skill writes those rows; the dev twin of the agent named in the row's `agent` field builds it.
- **The owner's direct request** in the session ("add X to the framework", "fix Y in the watch skill").

A change with neither is refused. A running agent that wants its own role, duties or checks changed files a change request (an action signal with `target: tailor`); it does not edit them.

**Scope is what the suggestion or the request names.** When the work needs more files or a wider change than that, stop, say what is missing, and ask for a wider request or a follow-up suggestion. No refactor on the side and no "while I am in here".

## 2. Personal-data safeguard (re-run at the write)

Applies to every write under `superagent/`. A `_custom` write is workspace-specific by definition, so the safeguard does not apply there.

The review already ran this scan when it wrote the suggestion. The dev twin runs it **again** at the moment of implementation, on the suggestion (or the request) and on the text it is about to write:

1. Read the suggestion's `problem`, `evidence`, `suggestion` and `implementation_sketch` fields again.
2. Scan them, and the new file content, for every name in `_memory/contacts.yaml` (names and aliases), every domain slug in `domains-index.yaml`, every asset name and serial in `assets-index.yaml`, every account label in `accounts-index.yaml`, and address fragments, account-number patterns and license-plate patterns from those files.
3. **On any match: refuse.** Write no file. Say which token matched and from which file, and that the content belongs in `workspace/_custom/`.
4. For a suggestion, set the row to `status: deferred` with `notes: "Safeguard refusal: <token>. Re-route to _custom/."`. Do not mark it `declined`: the intent may be right and only the destination wrong.
5. The next `supertailor-review` surfaces the refusal first, so the row can be re-classified.

The safeguard is not optional and the owner cannot waive it in the session. The two ways out are to rewrite the content so it carries no personal data, or to route it to `_custom/`. This is the guarantee behind `contracts/framework-artifacts.md`: personal data cannot reach committed framework code.

## 3. Coding conventions

### Python

- **Standard library first.** A heavy dependency is an explicit choice in `pyproject.toml`, never a casual import.
- **`pyyaml`** is the only mandatory framework dependency; a harvest handler or watcher pack that needs extras declares them under `pyproject.toml.optional-dependencies.<source>`.
- **Shell commands**: `subprocess.run([...], check=True, capture_output=True, text=True)` with an explicit argument list (no `shell=True` on user-supplied input).
- **Type hints** on every public function, and `from __future__ import annotations` at the top of every module.
- **`pathlib.Path`** everywhere; never raw `os.path.join` strings.
- **Docstrings**: one paragraph on every public function; a top-of-file docstring on every module saying what it does and where its inputs and outputs live.
- **CLI entry points**: `argparse` (no Click or Typer), `if __name__ == "__main__": sys.exit(main())`.
- **Exit codes**: `0` success, `1` runtime error, `2` usage error, `3` source unavailable.
- **Output**: `logging` for diagnostics; `print` only for the summary the calling skill shows the user.
- **Tooling**: `rules/development-tooling.md` (one `uv` venv, `uv run`, scratch under `~/.superagent/tmp/`).

### Markdown skill files

Skills live in `superagent/skills/<name>.md` with frontmatter (`name`, `description`, `triggers`, `mcp_required`, `mcp_optional`, `cli_required`, `cli_optional`) and a body of numbered `## N.` sections. A new frontmatter field needs a matching `contracts/` update first. After any skill edit, refresh the step index (`uv run python -m superagent.tools.add_step_index`) and the skill manifest (`uv run python -m superagent.tools.build_skill_manifest`).

### Agent role files

Roles live in `superagent/agents/<name>.agent.md` with the frontmatter in `contracts/agents.md`, and each has a dev twin `<name>.agent.dev.md` beside it. After any role edit, regenerate the roster (`uv run python -m superagent.tools.build_agent_manifest`) and add a line to the dev twin's changelog.

### Memory templates

A template under `superagent/templates/memory/` carries the `# [Do not change manually — managed by Superagent]` banner, a comment block describing the file's role, `schema_version: <int>` first, and one fully commented example row showing every field. A change that touches workspace data needs a migration file `superagent/migrations/<to_version>.md` (copy `_template.md`; rules in `contracts/versioning.md` § 3.2 "Migration authoring rules"; optional idempotent helpers at `superagent/migrations/<to_version>/migrate.py` / `revert.py`), then `uv run python -m superagent.tools.version refresh-manifest` to register it in `_manifest.yaml`, a `docs/roadmap.md` entry, and test coverage in `tests/test_version.py` for any version or manifest logic touched.

## 4. Testing discipline

Tests live in `superagent/tests/`. Run `uv run pytest -q` after every implementation. **A commit lands only on a green run.** No "the test is flaky, ship it", no commented-out assertion.

- A new feature gets its test in the **same commit**. Untested code is not ready to commit.
- A change to existing behavior updates the affected tests in the same commit.
- A test that fails for a reason unrelated to the change (a flake, an older bug the new code exposed, an environment problem) is a stop: say what failed and ask how to proceed. Never skip, `xfail`, delete or comment out a test to get a green run.
- A `_custom` change is tested too, whenever the overlay carries tests of its own.

## 5. Git practices

Full policy: `rules/git-commits.md` (`AGENTS.md` § "Git commits" is the floor). What matters here:

- **One sentence, imperative, 72 characters or fewer, ASCII, no body.** The message describes the change; it never cites a suggestion id.
- **No AI attribution**, and strip any trailer a harness injected before a push (`rules/git-commits.md` § 3).
- **Atomic.** One suggestion or request is one commit. Work large enough for several logical units is split into several suggestions up front.
- **Framework files only.** Never `workspace/` data, and nothing personally identifying in a message.
- **Lint first.** `uv run ruff check superagent/` passes before every commit.
- **Commit or push only when the owner asked for it.** A push always needs the owner's word.
- A `_custom` change is never committed: `workspace/` is gitignored.

## 6. What a framework change never does

- It never modifies workspace **data** (`workspace/Domains/`, `workspace/Projects/`, `workspace/_memory/<index>.yaml`). The operational skills own that. A change that needs existing workspace data converted ships a migration, and the `migrate` skill applies it.
- It never invents a convention on the side. A new memory schema, YAML field or folder is discussed first and goes through a suggestion or the owner's request.
- It never widens its scope without asking (§ 1).
- It never skips, `xfail`s or deletes a test to turn a run green (§ 4).
- It never pushes without the owner's approval (§ 5).
- A running agent never edits its own role file, duties or checks. Its dev twin does.

## 7. Workflow

1. **Read the gate.** Open the approved suggestion row in full, or restate the owner's request in one line. Read every cited file once. Note the destination.
2. **Re-run the safeguard** (§ 2) for a `superagent` destination. Refuse on a match.
3. **Plan.** List every file to create or change, inside the destination tree. Show the plan and get a yes before the first write, unless the owner's request already spelled the change out.
4. **Implement.** Make the changes in the plan, tests included. No scope creep.
5. **Verify.** `uv run ruff check superagent/` and `uv run pytest -q`. Fix what the change broke; stop and ask when a fix would widen the scope.
6. **Commit** for a `superagent` destination, when the owner asked for commits: one commit, one sentence. Nothing to commit for `_custom`.
7. **Report.** One line: what was implemented, the destination, files created and changed, tests passing, the commit (short SHA) or "(workspace, not committed)".
8. **Close the suggestion.** Set the row to `status: implemented`, `resolved_at: <now>`, `implementation_notes: "<one-line summary + commit SHA or '(workspace)'>"`. Log the change through `signals log`.
9. **Tell the running agent** when the change touched a role file, a duty or a check: one message, `Pick up: <the change in one line>` (`contracts/agents.md` § "Dev twins"). With no separate agent running there is nobody to tell: the next session reads the new files.
