# Per-session Scratchpad Contract

<!-- Migrated from `procedures.md § 33`. Citation form: `contracts/session-scratchpad.md`. -->

Implements perf-improvement-ideas.md MI-1. Backed by `_memory/_session/<session-id>.yaml` + `tools/session_scratch.py`.

**Pattern**: before any read, the agent (or skill orchestrator) checks the scratchpad. If the file's mtime + hash hasn't changed since the recorded `at`, the read is skipped.

**Writes**: each read records: path, ts, size, mtime, hash. Each MCP call records: server, tool, args summary.

**Cleanup**: `config.preferences.session.expire_days: 30` + `keep_recent_sessions: 20`. The `tools/session_scratch.py cleanup` command runs as part of `doctor` daily.
