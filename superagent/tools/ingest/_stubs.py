"""Stub ingestor implementations.

Every entry in `_registry.py` ships with a corresponding stub here so that
the orchestrator can probe / run / report consistently even before the
real implementation lands. Stubs:

  * `probe()` returns NEEDS_SETUP with the install hint from the registry.
  * `run()` returns a RunResult with notes="stub — implement this ingestor"
    and exits cleanly (no errors).

Real implementations replace these by adding a per-source module
(`superagent/tools/ingest/<source>.py`) that imports `IngestorBase` and
implements probe / reauth / run for real.

The roadmap (`docs/roadmap.md`) tracks which ingestors are stubbed vs
implemented.
"""
from __future__ import annotations

from typing import Any

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso
from ._registry import REGISTRY, IngestorSpec


class StubIngestor(IngestorBase):
    """Minimal IngestorBase implementation for an unimplemented source."""

    def __init__(self, workspace, spec: IngestorSpec):
        self.source = spec.source
        self.kind = spec.kind
        self.description = spec.description
        self._spec = spec
        super().__init__(workspace)

    def probe(self) -> ProbeResult:
        """Stubs always report NEEDS_SETUP — they have no real probe logic yet."""
        return ProbeResult(
            source=self.source,
            status=ProbeStatus.NEEDS_SETUP,
            detail="Ingestor not yet implemented (stub).",
            setup_hint=self._spec.install_hint,
        )

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Return a no-op RunResult."""
        ts = now_iso()
        return RunResult(
            source=self.source,
            started_at=ts,
            finished_at=ts,
            notes="stub — ingestor not yet implemented; see superagent/docs/roadmap.md",
        )


def stubs_for_unimplemented() -> dict[str, type[IngestorBase]]:
    """Return a {source: ingestor_class} map for every registry entry without a real impl.

    Used by the orchestrator to fall back gracefully when a source's real
    module hasn't shipped yet. The orchestrator first tries to import
    `superagent.tools.ingest.<source>`; on ImportError, it falls back
    here.
    """
    return {spec.source: StubIngestor for spec in REGISTRY}
