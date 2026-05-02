# Superagent — Claude Code entry point

This file is the **Claude Code-specific shim** for the Superagent framework. The canonical, IDE-agnostic always-on agent instructions live in [`AGENTS.md`](AGENTS.md) at the repo root.

@AGENTS.md

> **Why two files?** Claude Code reads `CLAUDE.md` automatically on every turn; the `@AGENTS.md` line above uses Claude Code's [file-import syntax](https://docs.claude.com/en/docs/claude-code/memory) so Claude pulls in the canonical instructions. Cursor users get the same instructions via `.cursor/rules/superagent.mdc` (agent-requestable, not auto-applied — keeps Superagent quiet when the workspace also hosts other frameworks). **Edit `AGENTS.md`, not this file.**

## Claude Code-specific notes

- **MCP servers**: copy [`.mcp.json.example`](.mcp.json.example) to `.mcp.json` and fill in any servers you want. None are required for the quick-start.
- **Hooks**: the Supertailor's user-query log (`workspace/_memory/user-queries.jsonl`) is populated by `.claude/settings.json` on every `UserPromptSubmit`. The same script (`superagent/tools/log_user_query.py`) is invoked from `.cursor/hooks.json` for Cursor users.
- **Workspace**: `workspace/` is created on first `init` and is gitignored. Everything personal lives there; the framework code under `superagent/` never touches it except through the Skills + Tools surface.
