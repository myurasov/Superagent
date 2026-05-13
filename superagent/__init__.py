# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Superagent — personal-life AI assistant framework.

The framework code lives here; user data lives in `workspace/`
(gitignored, local-only, never committed).

Entry points:
  - `superagent/tools/workspace_init.py` — scaffold a fresh workspace.
  - `superagent/tools/ingest/_orchestrator.py` — run ingestors.
  - `superagent/tools/validate.py` — schema-check the workspace.
  - `superagent/tools/render_status.py` — regenerate scoped status.md / todo.md.

Skills (markdown instruction sets) live in `superagent/skills/`.
"""
