"""Base interface every Superagent ingestor must implement.

The contract is the runtime expression of `superagent/procedures.md`
§ "Data Ingestion Contract":

  * `probe()` — lightweight presence check. Returns ProbeResult.
  * `reauth()` — re-authenticate (typically interactive). Returns bool.
  * `run(config_row, dry_run=False)` — pull data for one window, write
    rows to indexes / domain history files, return RunResult.

Ingestors NEVER block on missing optional inputs. They report `unavailable`
via ProbeResult and exit cleanly. The orchestrator decides whether
`unavailable` is a hard error (required source) or a warning (optional).
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


class ProbeStatus:
    """Possible outcomes of a probe."""
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
        """Return True if the source can be ingested right now."""
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
    """Abstract base for every source-specific ingestor.

    Subclasses MUST set `source` (str) and implement `probe()` and `run()`.
    `reauth()` defaults to a no-op (override when relevant).
    """

    source: str = ""
    kind: str = "unknown"  # mcp | cli | api | file
    description: str = ""

    def __init__(self, workspace: Path):
        if not self.source:
            raise NotImplementedError(
                f"{type(self).__name__} must set class attribute `source`"
            )
        self.workspace = workspace

    @abc.abstractmethod
    def probe(self) -> ProbeResult:
        """Lightweight presence check. Must not perform heavy reads."""

    def reauth(self) -> bool:
        """Re-authenticate the source. Default: no-op (always returns True)."""
        return True

    @abc.abstractmethod
    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Pull data for one window and write normalized rows.

        `config_row` is the row from `_memory/data-sources.yaml` for this source.
        On `dry_run`, the ingestor MUST NOT write any files; it should return
        a RunResult with `notes="dry-run; would have inserted N items"`.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} source={self.source!r}>"
