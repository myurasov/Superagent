# Development tooling policy

[Do not change manually — managed by Superagent]

This rule governs how Python and non-Python tooling is installed, located, and executed in this repository. It applies to every contributor (human or agent) working under the project root.

The four rules are non-negotiable defaults; surface a request to the user before deviating.

---

## 1. Python tooling — single shared `uv` venv at the repo root

- ALL Python-based tools in this repository (under `superagent/tools/`, `superagent/tests/`, anywhere else) MUST use the single shared `uv` virtual environment at the repository root: `./.venv/`.
- ALL Python tools MUST be invoked through `uv run`, e.g.:
  - `uv run python superagent/tools/foo.py`
  - `uv run python -m superagent.tools.sources_index refresh`
  - `uv run pytest`
  - `uv run ruff check superagent/`
- Do NOT call `python3 …` or `python …` directly. Do NOT activate the venv with `source .venv/bin/activate`; rely on `uv run`.
- Do NOT create per-tool venvs (`pipx`, `venv` in subdirs, `poetry env use`, `conda env`). One repo, one venv.
- Dependencies are declared in the root `pyproject.toml` and locked in `uv.lock`. Both files are committed; `uv.lock` is the source of truth for reproducible installs.

### Common commands

```bash
uv sync                      # create / refresh ./.venv from pyproject + uv.lock
uv sync --upgrade            # refresh lock against latest compatible versions
uv add <package>             # add a runtime dependency to pyproject + lock
uv add --dev <package>       # add a dev-only dependency
uv remove <package>          # drop a dependency
uv run python <script>.py    # run a script in the venv
uv run pytest -q             # run the test suite
uv lock                      # regenerate uv.lock without touching .venv
```

### Direct execution of Python tool scripts

Tool scripts under `superagent/tools/*.py` use a `uv run`-aware shebang so direct execution (`./superagent/tools/foo.py`) routes through the shared venv automatically. If a platform rejects the `-S` form of `env`, fall back to `uv run python <path>` and treat direct execution as unsupported on that platform.

---

## 2. Non-Python tools — install under `./.tools/`

- Any non-Python binary, CLI, helper, or self-contained tool installed to support this project MUST land under `./.tools/` at the repository root. The directory is gitignored.
- Layout convention: `./.tools/<tool-name>/` (one folder per tool, plus a top-level `bin/` symlink directory if helpful).
- Do NOT install non-Python tools system-wide (`brew install`, `npm install -g`, `cargo install --root /usr/local`, `apt install`, etc.) from inside this project. Doing so silently mutates the host machine and violates Rule #4 below.
- Document the install command in this rule doc or the project README rather than mutating system state. If the user wants a system-wide install, they will run it themselves.

---

## 3. Temporary files — `./.tmp/` only

- All scratch / temporary files produced by tools, scripts, agents, or interactive sessions MUST go under `./.tmp/` at the repository root. The directory is gitignored.
- Do NOT write transient files to `/tmp`, `~/`, `$TMPDIR`, OS temp dirs (`TemporaryDirectory()` without an explicit `dir=` pointing inside the project), sibling repo dirs, iCloud paths, or any other path outside the project root.
- When using `tempfile.TemporaryDirectory` / `tempfile.NamedTemporaryFile` in Python, pass `dir=Path(__file__).resolve().parents[N] / ".tmp"` (resolve N to the repo root) and ensure the directory exists.
- `./.tmp/` is created lazily on first use; the agent does not pre-create it.

---

## 4. Scope discipline — never write outside the project (safety rule)

- The agent MUST NOT install software, create files, or modify files outside the project folder unless the user explicitly asks for that specific action.
- Forbidden write targets by default: the home directory, dotfiles in `$HOME`, `/usr/`, `/opt/`, `/etc/`, system paths, other repos on the same machine, iCloud-synced folders outside this repo, the user's global git config, shell rc files, etc.
- Read access outside the project is fine. Write access outside is forbidden until the user explicitly authorizes it for the specific path or action.
- When in doubt, refuse and ask — surface the proposed change to the user with the exact path and command, and proceed only after confirmation.

---

## Enforcement

This is a policy doc consumed by humans and by the Superagent / Supercoder agents. It has no automatic enforcement at this time; violations are caught in review or by the user.

Future work (tracked in `docs/roadmap.md` if escalated):
- Pre-commit hook that fails on `python3 superagent/tools/` invocations in committed files.
- `tools/anti_patterns.py` rule that flags direct `python3 …` usage in skill markdown.
- Self-test that asserts `./.tmp/` is the only writable temp path used by tools.

---

## Override / user overlay

Users may override or extend this policy at `workspace/_custom/rules/development-tooling.md` (same shape). On collision, the custom file is read after the framework file and treated as additive.

Skills that introduce a new Python tool, non-Python binary, or temp-file usage pattern MUST cite this file and follow its conventions.
