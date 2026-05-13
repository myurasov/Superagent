# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Superagent ingestors.

Every source-specific ingestor lives in this package as `<source>.py` and
exposes the `IngestorBase`-conforming interface defined in `_base.py`.

The orchestrator (`superagent/skills/ingest.md` user-facing; this package
internal) loads the registry from `_registry.py` and runs each requested
source through its lifecycle: `probe()` → `reauth()` (if needed) → `run()`.
"""
