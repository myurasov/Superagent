#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""`ext-source` alias for the watchlist tool.

"ext-source" is the internal synonym for a watcher (`contracts/watchlist.md`
glossary). This module only re-exports the entry point so
`uv run python -m superagent.tools.ext_sources ...` behaves exactly like
`uv run python -m superagent.tools.watchlist ...`.
"""
from __future__ import annotations

from superagent.tools.watchlist import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
