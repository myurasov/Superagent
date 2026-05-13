# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Ingest orchestrator — front-end to every ingestor.

Used by the `ingest` skill. CLI:

  uv run python -m superagent.tools.ingest._orchestrator status
  uv run python -m superagent.tools.ingest._orchestrator setup
  uv run python -m superagent.tools.ingest._orchestrator run [--source S] [--all] [--backfill] [--dry-run]

Lifecycle:
  1. Load `_memory/data-sources.yaml`.
  2. For each requested source: import its module (or fall back to a stub),
     instantiate `<Source>Ingestor(workspace)`, call probe / run.
  3. Persist updated row back to data-sources.yaml.
  4. Append a per-run row to ingestion-log.yaml.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso
from ._registry import REGISTRY, find as find_spec
from ._stubs import StubIngestor


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML; return empty dict on failure or missing file."""
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def load_ingestor(workspace: Path, source: str) -> IngestorBase | None:
    """Resolve a source name to an IngestorBase instance.

    Tries to import `superagent.tools.ingest.<source>` and find a class
    named `<Source>Ingestor` (camelcase). Falls back to a stub.
    """
    spec = find_spec(source)
    if spec is None:
        return None
    module_name = f"superagent.tools.ingest.{spec.module}"
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return StubIngestor(workspace, spec)
    class_name_candidates = [
        "".join(part.capitalize() for part in source.split("_")) + "Ingestor",
        spec.module.capitalize() + "Ingestor",
    ]
    for cls_name in class_name_candidates:
        cls = getattr(module, cls_name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, IngestorBase):
            return cls(workspace)
    return StubIngestor(workspace, spec)


def cmd_status(workspace: Path) -> int:
    """Print the per-source status table."""
    data_sources = load_yaml(workspace / "_memory" / "data-sources.yaml")
    sources = {row.get("id"): row for row in (data_sources.get("sources") or []) if isinstance(row, dict)}
    print(f"{'Source':<24}{'Status':<14}{'Last run':<24}{'Items':<10}{'Failures':<10}")
    print("-" * 82)
    for spec in REGISTRY:
        row = sources.get(spec.source) or {}
        enabled = bool(row.get("enabled"))
        capture = row.get("capture_mode") or "—"
        if not enabled:
            status_label = "disabled"
        else:
            status_label = capture
        last_run = row.get("last_run") or {}
        last_at = row.get("last_ingest") or "never"
        items = (last_run.get("items_pulled") if isinstance(last_run, dict) else None) or 0
        failures = row.get("failure_streak", 0)
        print(f"{spec.source:<24}{status_label:<14}{last_at[:23]:<24}{items:<10}{failures:<10}")
    print()
    enabled_count = sum(1 for r in sources.values() if r.get("enabled"))
    print(f"Enabled: {enabled_count} / {len(REGISTRY)}.")
    print("Run `... setup` to enable more, or `... run --all` to refresh all enabled sources now.")
    return 0


def cmd_setup(workspace: Path) -> int:
    """Probe every source and print availability."""
    print(f"{'Source':<24}{'Probe':<22}{'Detail':<60}")
    print("-" * 106)
    available, not_detected, needs_setup = 0, 0, 0
    for spec in REGISTRY:
        ingestor = load_ingestor(workspace, spec.source)
        if ingestor is None:
            print(f"{spec.source:<24}{'unknown':<22}{'(no ingestor module)':<60}")
            continue
        probe = ingestor.probe()
        detail = (probe.detail or "")[:58]
        print(f"{spec.source:<24}{probe.status:<22}{detail:<60}")
        if probe.status == ProbeStatus.AVAILABLE:
            available += 1
        elif probe.status == ProbeStatus.NOT_DETECTED:
            not_detected += 1
        else:
            needs_setup += 1
    print()
    print(f"Available: {available}; not detected: {not_detected}; needs setup: {needs_setup}.")
    print("Setup hints for each `needs_setup` source live in superagent/docs/data-sources.md.")
    return 0


def cmd_run(
    workspace: Path,
    source: str | None,
    run_all: bool,
    backfill: bool,
    dry_run: bool,
    extra_kwargs: dict[str, Any],
) -> int:
    """Run one ingestor or all enabled ones."""
    ds_path = workspace / "_memory" / "data-sources.yaml"
    log_path = workspace / "_memory" / "ingestion-log.yaml"
    data_sources = load_yaml(ds_path) or {"schema_version": 1, "sources": []}
    rows_by_id = {r.get("id"): r for r in data_sources.get("sources", []) if isinstance(r, dict)}

    if source and not run_all:
        spec = find_spec(source)
        if spec is None:
            print(f"unknown source: {source}", file=sys.stderr)
            return 2
        sources_to_run = [source]
    else:
        if not run_all:
            print("specify --source <name> or --all", file=sys.stderr)
            return 2
        sources_to_run = [
            spec.source for spec in REGISTRY
            if (rows_by_id.get(spec.source) or {}).get("enabled")
        ]
        if not sources_to_run:
            print("no enabled sources; run `... setup` first.")
            return 0

    log_data = load_yaml(log_path) or {"schema_version": 1, "runs": []}
    log_data.setdefault("runs", [])
    today = dt.date.today().strftime("%Y%m%d")
    next_id_n = 1 + max(
        (
            int(row.get("id", "ingest-00000000-000").rsplit("-", 1)[-1])
            for row in log_data["runs"]
            if isinstance(row, dict) and (row.get("id") or "").startswith(f"ingest-{today}-")
        ),
        default=0,
    )

    summary_lines: list[str] = []
    overall_ok = True
    for src in sources_to_run:
        ingestor = load_ingestor(workspace, src)
        if ingestor is None:
            summary_lines.append(f"{src}: skipped (unknown source)")
            continue
        config_row = rows_by_id.get(src) or {}
        if backfill and "backfill_window_days" in config_row:
            config_row = {**config_row, "recency_window_days": config_row["backfill_window_days"]}
        config_row = {**config_row, **extra_kwargs}

        probe = ingestor.probe()
        if probe.status != ProbeStatus.AVAILABLE:
            summary_lines.append(f"{src}: SKIP ({probe.status} — {probe.detail or 'unavailable'})")
            row = rows_by_id.setdefault(src, {"id": src})
            row["failure_streak"] = (row.get("failure_streak") or 0) + 1
            continue
        t0 = time.time()
        result = ingestor.run(config_row, dry_run=dry_run)
        result.duration_ms = result.duration_ms or int((time.time() - t0) * 1000)

        run_id = f"ingest-{today}-{next_id_n:03d}"
        next_id_n += 1
        log_row = result.to_log_row(
            run_id=run_id,
            trigger="manual" if not run_all else "scheduled",
            window=None,
        )
        log_data["runs"].append(log_row)

        row = rows_by_id.setdefault(src, {"id": src})
        if result.errors:
            row["failure_streak"] = (row.get("failure_streak") or 0) + 1
            overall_ok = False
        else:
            row["failure_streak"] = 0
            row["last_ingest"] = result.finished_at
        row["last_run"] = {
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "items_pulled": result.items_pulled,
            "items_inserted": result.items_inserted,
            "items_updated": result.items_updated,
            "items_skipped": result.items_skipped,
            "errors": result.errors,
            "truncated": result.truncated,
            "duration_ms": result.duration_ms,
            "run_log_id": run_id,
        }
        verb = "DRY-RUN" if dry_run else "run"
        summary_lines.append(
            f"{src}: {verb} pulled={result.items_pulled} inserted={result.items_inserted} "
            f"errors={len(result.errors)}"
        )

    data_sources["sources"] = list(rows_by_id.values())
    data_sources["last_updated"] = now_iso()
    if not dry_run:
        save_yaml(ds_path, data_sources)
        save_yaml(log_path, log_data)

    print()
    for line in summary_lines:
        print(line)
    print()
    return 0 if overall_ok else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(prog="ingest-orchestrator")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show per-source status table.")
    sub.add_parser("setup", help="Probe every source and report availability.")
    run = sub.add_parser("run", help="Run ingestor(s).")
    run.add_argument("--source", type=str, default=None)
    run.add_argument("--all", action="store_true")
    run.add_argument("--backfill", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--file", type=str, default=None,
                     help="Pass-through for source-specific args (e.g. csv --file).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"
    if args.cmd == "status":
        return cmd_status(workspace)
    if args.cmd == "setup":
        return cmd_setup(workspace)
    if args.cmd == "run":
        extras: dict[str, Any] = {}
        if args.file:
            extras["file"] = args.file
        return cmd_run(workspace, args.source, args.all, args.backfill, args.dry_run, extras)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
