# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Harvest-handler contract (`_base.py`) plus the standalone CSV importer.

Source-specific code no longer lives here: every source is a self-sufficient
watcher pack (`superagent/watchers/<id>/handler.py`, or the user's
`workspace/_custom/watchers/<id>/handler.py`) that `tools/watchlist.py` loads
by file path. `_base.py` is the one module a pack handler imports —
`IngestorBase` / `RunResult` for harvest, `DetectContext` / `DetectResult` /
`DetectError` for a pack-defined detect type. `csv.py` keeps its standalone
`--file` CLI for manual bank-statement imports (its `path` pack is postponed).
"""
