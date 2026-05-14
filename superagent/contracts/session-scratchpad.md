# Per-session Scratchpad Contract

<!-- Migrated from `procedures.md § 33`. Citation form: `contracts/session-scratchpad.md`. -->

> **Status: infrastructure-only.** `tools/session_scratch.py` ships (`derive_session_id` / `record_read` / `is_already_loaded` / `record_mcp` / `record_tool` / `list_sessions` / `cleanup` all work and are covered by tests), and `_memory/_session/<id>.yaml` is created lazily by the first `record_*` call. **No skill or hook currently opens a session and writes to it**; the scratchpad is dormant under normal use. Activating this contract requires either an IDE-level per-turn hook (Cursor doesn't expose one today) or a per-skill prologue that calls `derive_session_id()` once and threads the id through. Until then, treat the rest of this contract as a spec.

Implements superagent/docs/_internal/perf-improvement-ideas.md MI-1. Backed by `_memory/_session/<session-id>.yaml` + `tools/session_scratch.py`.

**Pattern (when wired up)**: before any read, the agent (or skill orchestrator) checks the scratchpad. If the file's mtime + hash hasn't changed since the recorded `at`, the read is skipped.

**Writes**: each read records: path, ts, size, mtime, hash. Each MCP call records: server, tool, args summary.

**Cleanup**: `config.preferences.session.expire_days: 30` + `keep_recent_sessions: 20`. The `tools/session_scratch.py cleanup` command runs as part of `doctor` daily.
