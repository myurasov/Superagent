# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ingest registry and stub fall-back."""
from __future__ import annotations

from pathlib import Path


def test_registry_is_non_empty() -> None:
    from superagent.tools.ingest._registry import REGISTRY

    assert len(REGISTRY) >= 20


def test_registry_ids_unique() -> None:
    from superagent.tools.ingest._registry import REGISTRY

    seen = set()
    for spec in REGISTRY:
        assert spec.source not in seen, f"duplicate source id: {spec.source}"
        seen.add(spec.source)


def test_every_registry_entry_has_required_fields() -> None:
    from superagent.tools.ingest._registry import REGISTRY

    for spec in REGISTRY:
        assert spec.source, "missing source"
        assert spec.module, f"{spec.source}: missing module"
        assert spec.kind in ("mcp", "cli", "api", "file"), f"{spec.source}: bad kind {spec.kind}"
        assert spec.description, f"{spec.source}: missing description"
        assert spec.install_hint, f"{spec.source}: missing install_hint"
        assert spec.docs_anchor, f"{spec.source}: missing docs_anchor"
        assert isinstance(spec.writes_destinations, tuple), f"{spec.source}: writes_destinations must be tuple"


def test_orchestrator_loads_stub_for_unimplemented(initialized_workspace: Path) -> None:
    """A source with no real module gets a stub ingestor."""
    from superagent.tools.ingest._base import IngestorBase
    from superagent.tools.ingest._orchestrator import load_ingestor
    from superagent.tools.ingest._stubs import StubIngestor

    ing = load_ingestor(initialized_workspace, "whoop")  # no whoop.py shipped (yet)
    assert ing is not None
    assert isinstance(ing, IngestorBase)
    assert isinstance(ing, StubIngestor)


def test_orchestrator_loads_real_for_implemented(initialized_workspace: Path) -> None:
    """`csv` is shipped as a real ingestor and must NOT load as a stub."""
    from superagent.tools.ingest._base import IngestorBase
    from superagent.tools.ingest._orchestrator import load_ingestor
    from superagent.tools.ingest._stubs import StubIngestor

    ing = load_ingestor(initialized_workspace, "csv")
    assert ing is not None
    assert isinstance(ing, IngestorBase)
    assert not isinstance(ing, StubIngestor), "csv should be a real ingestor"


def test_csv_ingestor_dry_run(tmp_path: Path, initialized_workspace: Path) -> None:
    """The csv ingestor parses a small CSV in dry-run mode without writing."""
    from superagent.tools.ingest.csv import CsvIngestor

    sample = tmp_path / "sample.csv"
    sample.write_text(
        "Date,Description,Amount,Category\n"
        "2026-04-01,Whole Foods,-87.42,Groceries\n"
        "2026-04-02,Shell,-42.10,Gas\n"
        "2026-04-05,Direct Deposit,5500.00,Income\n"
    )
    ing = CsvIngestor(initialized_workspace)
    probe = ing.probe()
    assert probe.is_usable()
    result = ing.run(
        {"file": str(sample), "account_label": "test", "currency": "USD",
         "max_items_per_run": 100},
        dry_run=True,
    )
    assert result.items_pulled == 3
    assert result.items_inserted == 3
    assert not result.errors
    txn_path = initialized_workspace / "_memory" / "transactions.yaml"
    assert not txn_path.exists(), "dry-run must not write transactions.yaml"
