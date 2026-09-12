# Harness setup (Cursor, Claude Code, and other CLIs)

Superagent runs under **Cursor** and **Claude Code** as first-class hosts, and under any other CLI that reads `AGENTS.md` natively (e.g. Codex CLI) with zero framework changes. This page is the per-file wiring reference; `AGENTS.md` § "Harness setup" carries the binding summary, and the per-harness *capability* matrix (hooks, sandbox, question tool, subagents) lives in [`harness-matrix.md`](harness-matrix.md).

## Three harness classes

- **Cursor** — reads `AGENTS.md` natively at the repo root.
- **Claude Code** — reads [`CLAUDE.md`](../../CLAUDE.md), which `@`-imports `AGENTS.md`.
- **Generic `AGENTS.md`-native CLI** — reads `AGENTS.md` directly; no hooks, no IDE-specific config files. Everything such a CLI needs lives in `AGENTS.md` (see its "Non-Negotiables (Every Harness)").

The `workspace/_custom/` overlay applies identically under every harness; there is no per-harness overlay path.

## Detection

You can switch between harnesses interchangeably — detection is environment-driven and runs on every call (no sticky config). The helper at `superagent/tools/ide.py` centralizes the probe:

```bash
export UV_PROJECT_ENVIRONMENT="${PWD}/.venv.noSync"
uv run python -m superagent.tools.ide current   # prints "claude-code" / "cursor" / "unknown"
uv run python -m superagent.tools.ide is-claude # exit 0 iff Claude Code
uv run python -m superagent.tools.ide is-cursor # exit 0 iff Cursor
```

Detection looks at `CLAUDECODE=1` (Claude Code) and any `CURSOR_*` env var (Cursor); when both are present, Claude Code wins. **`unknown` is a first-class answer** — it is the correct result for generic `AGENTS.md`-native CLIs, which need no detection because they carry no harness-specific wiring. There is intentionally **no** `preferences.ide` override in `_memory/config.yaml` — re-detecting each turn keeps multi-harness workflows frictionless.

## Setup files — who reads what

| Setup file | Cursor reads | Claude Code reads | Generic CLI reads | Committed? |
|---|---|---|---|---|
| `AGENTS.md` | Yes (native) | Via `CLAUDE.md` `@`-import | Yes (native) | Yes |
| `CLAUDE.md` | No (irrelevant; pure re-export) | Yes (loaded every turn) | No | Yes |
| `.claudeignore` | No | Yes (gitignore-syntax exclusions; skips `.cursor/` and `workspace/`) | No | Yes |
| `.cursor/hooks.json` | Yes (Cursor hooks) | No | No | Yes |
| `.claude/settings.json` | No | Yes (Claude Code hooks) | No | Yes |
| `.claude/settings.local.json` | No | Yes (per-machine override) | No | No (gitignored) |
| `.cursor/mcp.json.cursor` | No (template only) | No (template only) | No (template only) | Yes |
| `.cursor/mcp.json` | Yes (runtime; may carry tokens) | No | No | No (gitignored) |
| `.mcp.json.claude` | No (template only) | No (template only) | No (template only) | Yes |
| `.mcp.json` | No | Yes (runtime; may carry tokens) | Varies — point the CLI at it if it accepts MCP JSON config | No (gitignored) |

The committed `.cursor/` tree holds only `hooks.json` and the `mcp.json.cursor` template — there is no `.cursor/rules/` shim, since Cursor reads `AGENTS.md` natively. `.claudeignore` keeps `.cursor/` and `workspace/` out of Claude Code's context.

## Recommended wiring

The `init` skill sets all of these up by default. The first two are hook-based **enhancements** — valuable where the harness supports hooks, absent elsewhere, and nothing critical depends on them (per `AGENTS.md` § "Non-Negotiables (Every Harness)").

- **User-prompt logging** (used by the Supertailor for friction analysis). Both harnesses run `uv run python -m superagent.tools.log_user_query`, but on **differently-named events** — Cursor rejects Claude Code's event names in `.cursor/hooks.json` and loads nothing, silently:
  - Cursor: `.cursor/hooks.json`, event `beforeSubmitPrompt`, schema `"version": 1` (no `--source` flag; defaults to `cursor`).
  - Claude Code: `.claude/settings.json`, event `UserPromptSubmit` (passes `--source claude-code` so the Supertailor can slice by IDE).
  - Disable by setting `_memory/config.yaml.preferences.privacy.log_user_queries: false`; the script reads that flag and exits silently when it's off.
- **Skill auto-loader** (Claude Code only). A second `UserPromptSubmit` hook runs `uv run python -m superagent.tools.skill_loader`: it matches the prompt against every skill's frontmatter `triggers` (framework + `_custom` overlay) and injects the matching skill deterministically — full body for short skills, a compact pointer + step index for long ones (per the read budget). Cursor's hook API cannot add context, and generic CLIs have no hooks — under those harnesses the agent recognizes triggers via the skill manifest (`superagent/skills/_manifest.yaml`). Fail-safe (always exits 0); disable via `_memory/config.yaml.preferences.skill_autoload: false`.
- **Email capture** (mirrors touched Gmail messages into the local archive). Cursor: `.cursor/hooks.json`, event `afterMCPExecution`, ONE entry with no matcher, `archive_hook --kind=auto`. Claude Code: `.claude/settings.json`, event `PostToolUse`, three `mcp__gmail__<tool>` matchers. Details and the per-harness field-name differences are in `contracts/email-capture.md` § 8.1; the hook-free floor that makes both optional is `rules/email-capture-fallback.md`.
- **MCP servers.** Cursor and Claude Code each read their own runtime file (`.cursor/mcp.json` / `.mcp.json`). Both files start as a regular-file copy of the committed templates (`.cursor/mcp.json.cursor` / `.mcp.json.claude`). The templates are content-identical (Superagent has no per-harness OAuth client_id constraint); only the destination path differs. **Regular-file copies, not symlinks** — this repo lives in iCloud Drive, and iCloud occasionally rewrites symlinks as placeholders. Edit one file, re-run `init`, and drift between the two is detected and offered for mirror. A generic CLI that accepts MCP JSON config can be pointed at `.mcp.json` as well.
- **Commit-message hook.** Install a `commit-msg` hook at `.githooks/commit-msg` (`git config core.hooksPath .githooks`) that blocks AI-attribution patterns at commit time. The reference implementation lives at `superagent/templates/githooks/commit-msg`. Harness-agnostic; the policy it enforces is `rules/git-commits.md`.

### Hook command shape

No harness guarantees a hook's working directory — Claude Code has been observed running one from `workspace/` — so **every hook command anchors itself to the repo root before doing anything else**:

```sh
cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}" && unset VIRTUAL_ENV && UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python -m superagent.tools.<module> || exit 1
```

Each clause earns its place, and dropping any one of them fails silently or noisily in a different way:

- **`cd <repo root>`** — the root is what makes `superagent.tools.*` importable (the package is not installed into the venv; `python -m` relies on the cwd being on `sys.path`) and what anchors uv's relative `cache-dir` to `./.tmp.noSync/uv-cache` instead of spawning a stray cache under the cwd (`rules/development-tooling.md`). `CLAUDE_PROJECT_DIR` is Claude Code's own project-root variable; the `git rev-parse` fallback covers every other harness.
- **`unset VIRTUAL_ENV`** — a shell with the deprecated `./.venv` activated otherwise makes uv print a `does not match the project environment path` warning on every turn.
- **`|| exit 1`** — the hook scripts' `main()` already always returns 0, but a process that cannot start at all exits **2**, and 2 is the code Claude Code reads as *block this prompt*. Mapping any failure to 1 keeps hook breakage visible on stderr while never holding up a turn.

## Routing when the repo hosts other frameworks

If the repo also hosts other assistant frameworks, route each turn to the right framework based on the request's evident scope rather than auto-injecting Superagent on every turn (the load triggers are listed in `AGENTS.md` § "How this file is loaded").

## Maintenance

This page is framework knowledge (generic, no user data). Add a row to the setup-file table when a new harness needs a repo-root file; add a capability row or sandbox quirk to `harness-matrix.md`, not here.
