# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Pack-handler contract: the one module a watcher pack's `handler.py` imports.

Since the watchlist tier (`superagent/contracts/watchlist.md`) took over
scheduling, probing, and run state, this module is deliberately small. A
pack's `handler.py` is loaded BY FILE PATH by `tools/watchlist.py` (never as
a package import) and may expose:

  * `detect(ctx: DetectContext) -> DetectResult` — a pack-defined detect type.
    Raise `DetectError` when the source cannot be reached. Any `detect.type`
    that is not built into the tool (`url`, `path`, `cmd`, `subagent`,
    `harvest`) is valid iff the pack's handler exposes this function.
  * `harvest(config_row, dry_run=False) -> RunResult`, or a class deriving
    `IngestorBase` whose `run(config_row, dry_run=False)` does the same —
    pull data for one window, write rows to indexes / domain history files,
    return a `RunResult`.

Also here: `RunResult.to_log_row()` (the `ingestion-log.yaml` row) and
`ProbeResult` / `ProbeStatus` (the vocabulary the declarative pack probes
report in — handlers never implement `probe()` themselves).

Handlers NEVER block on missing optional inputs: record the problem on
`RunResult.errors` and return, so a failed refresh still reaches the log.
"""
from __future__ import annotations

import abc
import dataclasses as dc
import datetime as dt
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class DetectError(Exception):
    """Detect could not reach the source; the message is the reason (-> `unreachable`)."""


@dc.dataclass
class DetectResult:
    """One detect result.

    `fingerprint=None` means "not modified — keep the stamped fingerprint".
    `state` is merged into the watcher's state row on success (e.g. HTTP
    validators a later check should send back).
    """
    fingerprint: str | None
    detail: str = ""
    state: dict[str, Any] = dc.field(default_factory=dict)


@dc.dataclass
class DetectContext:
    """What a pack's `detect(ctx)` receives. `row` is a read-only copy of the state row."""
    watcher_id: str
    detect: dict[str, Any]
    workspace: Path
    framework: Path
    timeout: int
    dry_run: bool
    row: dict[str, Any]
    now: dt.datetime


class ProbeStatus:
    """Possible outcomes of a declarative pack probe."""
    AVAILABLE = "available"
    NOT_DETECTED = "not_detected"
    NEEDS_SETUP = "needs_setup"
    AUTH_EXPIRED = "auth_expired"
    PERMISSION_DENIED = "permission_denied"


@dc.dataclass
class ProbeResult:
    """Result of a presence / health probe."""
    source: str
    status: str
    detail: str = ""
    setup_hint: str = ""

    def is_usable(self) -> bool:
        """Return True if the source can be harvested right now."""
        return self.status == ProbeStatus.AVAILABLE


@dc.dataclass
class RunResult:
    """Result of one ingestor invocation."""
    source: str
    started_at: str
    finished_at: str
    items_pulled: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    items_skipped: int = 0
    errors: list[str] = dc.field(default_factory=list)
    truncated: bool = False
    destination_summary: dict[str, Any] = dc.field(default_factory=dict)
    duration_ms: int = 0
    notes: str = ""

    def to_log_row(self, run_id: str, trigger: str, window: dict | None) -> dict[str, Any]:
        """Produce the dict that appends to ingestion-log.yaml."""
        return {
            "id": run_id,
            "source": self.source,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "trigger": trigger,
            "window": window,
            "items_pulled": self.items_pulled,
            "items_inserted": self.items_inserted,
            "items_updated": self.items_updated,
            "items_skipped": self.items_skipped,
            "errors": self.errors,
            "truncated": self.truncated,
            "destination_summary": self.destination_summary,
            "duration_ms": self.duration_ms,
            "notes": self.notes,
        }


class IngestorBase(abc.ABC):
    """Abstract base for every source-specific harvest handler.

    Subclasses MUST set `source` (str) and implement `run()`.
    """

    source: str = ""
    kind: str = "unknown"  # mcp | cli | api | file
    description: str = ""
    # Domains whose info.md / history.md surface this ingestor's data.
    # Used by the post-run hook to refresh auto-managed marker blocks
    # per `contracts/domain-reflection.md`. Empty tuple = skip refresh.
    affected_domains: tuple[str, ...] = ()

    def __init__(self, workspace: Path):
        if not self.source:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute `source`"
            )
        self.workspace = workspace

    def _refresh_domains(self) -> list[str]:
        """Refresh auto-managed blocks in `affected_domains`. Best-effort.

        Returns the list of error strings (empty on success). Never raises.
        Per `contracts/domain-reflection.md`, ingestors call this at the end
        of a successful `run()`. Failure here MUST NOT fail the ingest
        itself — the data is already in `_memory/`; rendering is derived.
        """
        if not self.affected_domains:
            return []
        try:
            from superagent.tools import render_domain
        except ImportError as exc:
            return [f"render_domain import failed: {exc}"]
        try:
            summary = render_domain.refresh(
                self.workspace, list(self.affected_domains)
            )
        except Exception as exc:  # noqa: BLE001
            return [f"render_domain.refresh failed: {exc}"]
        return list(summary.get("errors", []))

    @abc.abstractmethod
    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Pull data for one window and write normalized rows.

        `config_row` is built by the watchlist from the pack's
        `harvest.defaults`, the watcher's `params`, plus `last_ingest` and
        `id`. On `dry_run`, the ingestor MUST NOT write any files; it should
        return a RunResult with `notes="dry-run; would have inserted N items"`.

        Example (the shape every shipped ingestor follows)::

            def run(self, config_row, dry_run=False) -> RunResult:
                started = now_iso()
                t0 = time.time()
                result = RunResult(
                    source=self.source, started_at=started, finished_at=started
                )
                days = int(config_row.get("recency_window_days") or 30)
                since = dt.date.today() - dt.timedelta(days=days)

                rows = [self._normalize(r) for r in self._fetch(since)]
                result.items_pulled = len(rows)

                index_path = self.workspace / "_memory" / "example-index.yaml"
                index = self._load_index(index_path)
                known = {r["external_id"] for r in index["items"]}
                new_rows = [r for r in rows if r["external_id"] not in known]
                result.items_inserted = len(new_rows)
                result.items_skipped = len(rows) - len(new_rows)

                if dry_run:
                    result.notes = f"dry-run; would have inserted {len(new_rows)} items"
                else:
                    index["items"].extend(new_rows)
                    self._save_index(index_path, index)
                    result.errors.extend(self._refresh_domains())

                result.finished_at = now_iso()
                result.duration_ms = int((time.time() - t0) * 1000)
                return result

        Key every row by a stable `external_id` so re-runs over the same
        window insert nothing (idempotency per `contracts/ingestion.md`);
        record fetch failures on `result.errors` rather than raising, so a
        failed refresh still reaches `ingestion-log.yaml`.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} source={self.source!r}>"
