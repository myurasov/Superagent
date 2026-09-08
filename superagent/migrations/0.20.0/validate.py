#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Post-migration checks for 0.20.0 (every check is blocking).

Mirrors the ``## Validate`` bullets of ``superagent/migrations/0.20.0.md``:

- every registry ref parses with ``ref_version: 2``, carries no legacy
  top-level key and has a ``watch:`` mapping;
- ``superagent.tools.watchlist.load_registry`` loads the registry with zero
  errors;
- every FRAMEWORK-WRITTEN registry ref (``added_by`` ``watch`` / ``init`` /
  ``migrate-*``) is named the Title_Case form of its lowercase id; a user-named
  ref keeps whatever casing it has; ids are unique (case-insensitively);
- no ``.ref.md`` exists outside the registry (scan roots ``Sources/``,
  ``Projects/*/Sources/``, ``Projects/*/Resources/``);
- every ``.meta.md`` sidecar has its document (a payment confirmation whose
  document was never filed is accepted with a note);
- no renamed ref or sidecar path from the ledger remains in any
  ``Domains/*/sources.md`` / ``Projects/*/sources.md`` catalogue;
- every renamed row in ``_memory/sources-index.yaml`` kept its id;
- the SimpleFIN ref reads ``schedule: daily``, ``capture_mode: automatic``,
  ``cycles: [daily-update]`` (pass with a note when no SimpleFIN ref exists);
- ``Sources/_cache/`` is absent or non-empty;
- ``.version`` reads ``0.20.0`` and ``tools/version.py check`` agrees (skipped
  with a note while the framework's ``pyproject.toml`` still predates 0.20.0);
- ``watchlist check --cycle daily-update --dry-run --no-harvest`` exits 0 with
  ``summary.errors == 0`` and ``summary.harvested == 0``. ``--no-harvest`` keeps
  the check offline-safe: with automatic capture the SimpleFIN harvest-as-detect
  would otherwise make a real API request (budget spent, counters persisted,
  failure on a machine without network or credentials);
- re-running ``migrate.py`` (dry run) reports "nothing to do".

When every check passes, ``_memory/_checkpoints/0.20.0/`` is relocated to
``_memory/_retired/0.20.0-originals/`` (``--keep-checkpoints`` skips this):
the checkpoint folder only exists while the migration is incomplete, while the
originals stay parked next to the ledger so ``revert.py`` remains byte-for-byte.

Usage::

    uv run python superagent/migrations/0.20.0/validate.py --workspace <path> [--keep-checkpoints]

Exit codes: 0 all checks pass, 1 at least one check failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from superagent.tools import sources_index as si  # noqa: E402

Check = tuple[bool, str]


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_20_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _config(ws: Path) -> dict[str, Any]:
    path = ws / "_memory" / "config.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _ledger(mig: ModuleType, ws: Path) -> dict[str, Any] | None:
    """The move ledger, `{}` when absent, None when it does not parse."""
    path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_schema(mig: ModuleType, ws: Path, registry: Path) -> list[Check]:
    results: list[Check] = []
    refs = mig.registry_refs(registry)
    for ref in refs:
        rel = ref.relative_to(ws).as_posix()
        fm = mig.frontmatter_of(ref)
        if fm is None:
            results.append((False, f"schema: {rel} has no canonical frontmatter"))
            continue
        if fm.get("ref_version") != mig.REF_VERSION:
            results.append((False, f"schema: {rel} ref_version is {fm.get('ref_version')!r}, "
                                   f"expected {mig.REF_VERSION}"))
        legacy = [k for k in mig.LEGACY_TOP_KEYS if k in fm]
        if legacy:
            results.append((False, f"schema: {rel} still carries legacy key(s) {', '.join(legacy)}"))
        unknown = sorted(str(k) for k in fm if k not in mig.REF_TOP_KEYS and k not in mig.LEGACY_REF_KEYS)
        if unknown:
            results.append((False, f"schema: {rel} has key(s) outside ref_version "
                                   f"{mig.REF_VERSION}: {', '.join(unknown)}"))
        if not isinstance(fm.get("watch"), dict):
            results.append((False, f"schema: {rel} has no `watch:` mapping"))
    if not results:
        results.append((True, f"schema: {len(refs)} registry ref(s) at ref_version "
                              f"{mig.REF_VERSION} with no legacy keys"))
    return results


def check_loader(mig: ModuleType, ws: Path, framework: Path, registry: Path) -> list[Check]:
    """`superagent.tools.watchlist.load_registry` reports zero errors and one watcher per ref file."""
    if importlib.util.find_spec("superagent.tools.watchlist") is None:
        return [(False, "loader: superagent.tools.watchlist is not importable")]
    wl = importlib.import_module("superagent.tools.watchlist")
    try:
        cfg = wl.load_config(ws)
        packs, pack_errors = wl.discover_packs(framework, ws, announce=lambda _m: None)
        watchers, errors = wl.load_registry(ws, cfg, packs)
    except Exception as exc:  # noqa: BLE001 - a loader crash is a failed check, not a traceback
        return [(False, f"loader: {type(exc).__name__}: {exc}")]
    results: list[Check] = [(False, f"loader: pack error: {e}") for e in pack_errors]
    results.extend((False, f"loader: {e.get('error') or e}") for e in errors)
    expected = len(mig.registry_refs(registry))
    if len(watchers) != expected:
        results.append((False, f"loader: {len(watchers)} watcher(s) loaded for {expected} "
                               "registry ref file(s)"))
    if not results:
        results.append((True, f"loader: {len(watchers)} watcher(s) load with zero errors "
                              f"({expected} ref file(s))"))
    return results


def check_filenames(mig: ModuleType, ws: Path, registry: Path) -> list[Check]:
    results: list[Check] = []
    seen: dict[str, str] = {}
    refs = mig.registry_refs(registry)
    framework_named = user_named = 0
    for ref in refs:
        rel = ref.relative_to(ws).as_posix()
        stem = ref.name[: -len(mig.REF_SUFFIX)]
        wid = mig.id_from_stem(stem)
        # Title_Case is checked only where the framework chose the name; a ref
        # the user named keeps its casing and is never flagged for it.
        if mig.framework_written(mig.frontmatter_of(ref)):
            framework_named += 1
            want = mig.title_case(wid) + mig.REF_SUFFIX
            if ref.name != want:
                results.append((False, f"filename: {rel} should be {want} (Title_Case of id "
                                       f"{wid!r}; framework-written ref)"))
        else:
            user_named += 1
        if wid in seen:
            results.append((False, f"filename: {rel} and {seen[wid]} share the id {wid!r}"))
        seen.setdefault(wid, rel)
    if not results:
        results.append((True, f"filename: {len(refs)} registry ref(s) with unique ids "
                              f"({framework_named} framework-written, Title_Case; "
                              f"{user_named} user-named, casing respected)"))
    return results


def check_outside_refs(mig: ModuleType, ws: Path, registry: Path) -> list[Check]:
    strays = mig.outside_refs(ws, registry)
    results: list[Check] = [(False, f"stray: {p.relative_to(ws).as_posix()} is a .ref.md outside "
                                    f"{registry.relative_to(ws).as_posix()}/") for p in strays]
    if not strays:
        results.append((True, "stray: no .ref.md outside the registry"))
    txt = mig.outside_refs(ws, registry, suffix=mig.LEGACY_TXT_SUFFIX)
    if txt:
        results.append((True, f"note: {len(txt)} .ref.txt file(s) left in place (not recognized "
                              f"since {mig.TO_VERSION}): "
                              + ", ".join(p.relative_to(ws).as_posix() for p in txt)))
    return results


def check_sidecars(mig: ModuleType, ws: Path) -> list[Check]:
    results: list[Check] = []
    metas = mig.meta_sidecars(ws)
    orphans = 0
    for meta in metas:
        rel = meta.relative_to(ws).as_posix()
        if mig.meta_document(meta) is not None:
            continue
        fm = mig.frontmatter_of(meta)
        if isinstance(fm, dict) and any(k in fm for k in mig.PAYMENT_KEYS):
            results.append((True, f"sidecar: {rel} is a payment confirmation with no document "
                                  "beside it (accepted)"))
            continue
        orphans += 1
        results.append((False, f"sidecar: {rel} has no document {meta.name[: -len(mig.META_SUFFIX)]}"))
    if not orphans:
        results.append((True, f"sidecar: {len(metas)} .meta.md sidecar(s), every one beside its document"))
    return results


def check_catalogues(mig: ModuleType, ws: Path, ledger: dict[str, Any]) -> list[Check]:
    results: list[Check] = []
    moves = [m for key in ("renamed", "sidecars", "index_paths")
             for m in (ledger.get(key) or []) if isinstance(m, dict) and m.get("from")]
    catalogues = [(p, p.read_text(encoding="utf-8")) for p in mig.catalogue_files(ws)]
    dangling = 0
    for m in moves:
        old_rel = str(m["from"])
        for cat, text in catalogues:
            cat_rel = cat.relative_to(ws).as_posix()
            proj_rel = mig._project_relative(old_rel, cat_rel)
            if old_rel in text or (proj_rel and f"`{proj_rel}`" in text):
                dangling += 1
                results.append((False, f"catalogue: {cat_rel} still names {old_rel}"))
    if not dangling:
        results.append((True, f"catalogue: no dangling path ({len(moves)} rename(s) checked)"))
    return results


def check_index_ids(mig: ModuleType, ws: Path, ledger: dict[str, Any]) -> list[Check]:
    results: list[Check] = []
    idx_path = si.index_path(ws)
    moves = [m for key in ("renamed", "sidecars", "index_paths")
             for m in (ledger.get(key) or []) if isinstance(m, dict) and m.get("index_id")]
    if not moves:
        return [(True, "index: no indexed row was renamed")]
    if not idx_path.exists():
        return [(False, "index: _memory/sources-index.yaml missing although rows were renamed")]
    rows = [r for r in si.load_index(ws).get("sources") or [] if isinstance(r, dict)]
    by_path = {r.get("path"): r for r in rows if r.get("id")}
    bad = 0
    for m in moves:
        row = by_path.get(str(m["to"]))
        if row is None or row.get("id") != m["index_id"]:
            bad += 1
            got = row.get("id") if row else "no row"
            results.append((False, f"index: {m['to']} should keep id {m['index_id']} (got {got})"))
        live_old = by_path.get(str(m["from"]))
        if live_old is not None and live_old.get("present", True) is not False:
            bad += 1
            results.append((False, f"index: {m['from']} still has a present row"))
    if not bad:
        results.append((True, f"index: {len(moves)} renamed row(s) kept their ids"))
    return results


def check_simplefin(mig: ModuleType, ws: Path, registry: Path) -> list[Check]:
    hits: list[Check] = []
    for ref in mig.registry_refs(registry):
        fm = mig.frontmatter_of(ref)
        watch = fm.get("watch") if isinstance(fm, dict) else None
        if not isinstance(watch, dict) or watch.get("pack") != mig.SIMPLEFIN_PACK:
            continue
        rel = ref.relative_to(ws).as_posix()
        actual = {k: watch.get(k) for k in mig.SIMPLEFIN_CADENCE}
        ok = actual == mig.SIMPLEFIN_CADENCE
        hits.append((ok, f"simplefin: {rel} schedule {actual['schedule']!r}, capture_mode "
                         f"{actual['capture_mode']!r}, cycles {actual['cycles']!r}"
                         + ("" if ok else " -- expected daily / automatic / ['daily-update']")))
    return hits or [(True, "simplefin: no SimpleFIN pack ref in the registry; nothing to check")]


def check_cache_dir(mig: ModuleType, ws: Path) -> Check:
    cache = ws / mig.CACHE_REL
    if not cache.exists():
        return True, f"cache: {mig.CACHE_REL}/ absent"
    entries = [p for p in cache.iterdir() if p.name != ".DS_Store"] if cache.is_dir() else ["file"]
    if entries:
        return True, f"cache: {mig.CACHE_REL}/ kept ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'})"
    return False, f"cache: {mig.CACHE_REL}/ exists and is empty (migration should have removed it)"


def check_version_tool(mig: ModuleType, ws: Path) -> Check:
    """`tools/version.py check` agrees with `.version`.

    Skipped while the framework's pyproject predates 0.20.0 (release bump
    pending). When the framework is already PAST 0.20.0 the tool rightly
    reports the next migration as pending; this step is then complete iff the
    workspace reads exactly 0.20.0 (the chain's next step takes it further).
    """
    from superagent.tools.version import compare, current_version, workspace_version
    cur = current_version()
    if compare(cur, mig.TO_VERSION) < 0:
        return True, (f"version check skipped: framework pyproject at {cur} < {mig.TO_VERSION} "
                      "(release bump pending)")
    if compare(cur, mig.TO_VERSION) > 0:
        ws_v = workspace_version(ws)
        detail = ("this step is complete; the next migration is pending (mid-chain)"
                  if ws_v == mig.TO_VERSION else f"expected {mig.TO_VERSION}")
        return ws_v == mig.TO_VERSION, (f"version check: framework at {cur} is ahead of "
                                        f"{mig.TO_VERSION}; workspace reads {ws_v} -- {detail}")
    res = subprocess.run([sys.executable, "-m", "superagent.tools.version", "check",
                          "--workspace", str(ws)], cwd=_REPO_ROOT, capture_output=True,
                         text=True, check=False)
    tail = (res.stdout or res.stderr).strip().splitlines()[-1:] or ["(no output)"]
    return res.returncode == 0, f"version check exit {res.returncode}: {tail[0]}"


def _dry_run_failure(stdout: str, stderr: str) -> str:
    """Why a `watchlist check` run failed: its first `errors[]` entry when the
    payload is JSON, else the last line of output."""
    out = (stdout or "").strip()
    payload: Any = None
    if out:
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        errs = payload.get("errors") or []
        if errs:
            first = errs[0]
            msg = first.get("error") if isinstance(first, dict) else first
            if msg:
                return str(msg)
    lines = (stderr or "").strip().splitlines() or out.splitlines()
    return lines[-1] if lines else "(no output)"


def check_watchlist_dry_run(ws: Path) -> Check:
    """`watchlist check --cycle daily-update --dry-run --no-harvest` runs clean.

    Exit 0, `summary.errors == 0`, `summary.harvested == 0`. `--no-harvest` is
    what keeps this offline-safe: SimpleFIN's harvest IS its detect, so a plain
    dry run would call the API (spending budget and persisting counters).
    """
    if importlib.util.find_spec("superagent.tools.watchlist") is None:
        return False, "watchlist dry-run: superagent.tools.watchlist is not importable"
    args = ["--workspace", str(ws), "check", "--cycle", "daily-update", "--dry-run", "--no-harvest"]
    try:
        res = subprocess.run([sys.executable, "-m", "superagent.tools.watchlist", *args],
                             cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
                             timeout=180)
    except subprocess.TimeoutExpired:
        return False, "watchlist dry-run: timed out after 180s"
    out = (res.stdout or "").strip()
    if res.returncode != 0:
        return False, f"watchlist dry-run: exit {res.returncode}: {_dry_run_failure(res.stdout, res.stderr)}"
    try:
        payload = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return False, f"watchlist dry-run: output is not JSON: {out[:120]!r}"
    summary = payload.get("summary") if isinstance(payload, dict) else None
    summary = summary if isinstance(summary, dict) else {}
    errors = int(summary.get("errors") or 0)
    harvested = int(summary.get("harvested") or 0)
    checked = int(summary.get("checked") or 0)
    dispatched = int(summary.get("dispatch") or 0)
    withheld = len(payload.get("skipped_harvest") or []) if isinstance(payload, dict) else 0
    return errors == 0 and harvested == 0, (
        f"watchlist dry-run: summary.errors={errors} summary.harvested={harvested} "
        f"summary.checked={checked} summary.dispatch={dispatched} skipped_harvest={withheld} "
        "(--no-harvest; offline-safe)")


def check_rerun(mig: ModuleType, ws: Path, framework: Path) -> Check:
    lines: list[str] = []
    code = mig.run_migration(ws, framework=framework, dry_run=True, skip_world=True, out=lines.append)
    if code != 0:
        return False, "rerun: migrate.py --dry-run exited " + str(code) + ": " + (lines[-1] if lines else "")
    if any("nothing to do" in ln for ln in lines):
        return True, "rerun: migrate.py --dry-run reports nothing to do"
    pending = [ln for ln in lines if ln.startswith("[dry-run] ") and "would be changed" not in ln]
    return False, "rerun: migrate.py --dry-run still wants to change: " + (pending[0] if pending else "?")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_checks(workspace: Path, framework: Path | None = None) -> list[Check]:
    """Return one (passed, message) row per check."""
    mig = _load_migrate()
    ws = Path(workspace)
    framework = Path(framework) if framework else mig.default_framework_root()
    registry = ws / mig.watchlist_rel_path(_config(ws))
    results: list[Check] = []

    results.extend(check_schema(mig, ws, registry))
    results.extend(check_loader(mig, ws, framework, registry))
    results.extend(check_filenames(mig, ws, registry))
    results.extend(check_outside_refs(mig, ws, registry))
    results.extend(check_sidecars(mig, ws))
    ledger = _ledger(mig, ws)
    if ledger is None:
        results.append((False, "ledger: _memory/_retired/0.20.0-moves.yaml does not parse"))
        ledger = {}
    results.extend(check_catalogues(mig, ws, ledger))
    results.extend(check_index_ids(mig, ws, ledger))
    results.extend(check_simplefin(mig, ws, registry))
    results.append(check_cache_dir(mig, ws))

    from superagent.tools.version import workspace_version
    current = workspace_version(ws)
    results.append((current == mig.TO_VERSION, f".version reads {current}"))
    results.append(check_version_tool(mig, ws))
    results.append(check_watchlist_dry_run(ws))
    results.append(check_rerun(mig, ws, framework))
    return results


def finalize_checkpoints(ws: Path, mig: ModuleType | None = None, out=print) -> bool:
    """Park `_memory/_checkpoints/0.20.0/` as `_memory/_retired/0.20.0-originals/`.

    An earlier original at the destination wins; the seed record is merged.
    Returns True when something was relocated.
    """
    mig = mig or _load_migrate()
    ws = Path(ws)
    ckpt = ws / mig.CHECKPOINT_REL
    if not ckpt.is_dir():
        return False
    dest = ws / mig.ORIGINALS_REL
    dest.mkdir(parents=True, exist_ok=True)
    for src in sorted(p for p in ckpt.rglob("*") if p.is_file()):
        target = dest / src.relative_to(ckpt)
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.name == mig.SEEDED_MARKER_NAME and target.exists():
            lines = target.read_text(encoding="utf-8").splitlines()
            for ln in src.read_text(encoding="utf-8").splitlines():
                if ln.strip() and ln not in lines:
                    lines.append(ln)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            continue
        if target.exists():
            continue
        shutil.copy2(src, target)
    shutil.rmtree(ckpt)
    parent = ckpt.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    ledger_path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    if ledger_path.exists():
        try:
            ledger = yaml.safe_load(ledger_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            ledger = None
        if isinstance(ledger, dict):
            ledger["originals"] = mig.ORIGINALS_REL.as_posix()
            head = "".join(ln for ln in ledger_path.read_text(encoding="utf-8").splitlines(True)
                           if ln.startswith("#"))
            ledger_path.write_text(head + yaml.safe_dump(ledger, sort_keys=False,
                                                          allow_unicode=True), encoding="utf-8")
    out(f"checkpoints: {mig.CHECKPOINT_REL.as_posix()}/ -> {mig.ORIGINALS_REL.as_posix()}/ "
        "(migration complete; originals kept for revert)")
    return True


def run_validate(workspace: Path, framework: Path | None = None, out=print, *,
                 finalize: bool = True) -> int:
    """Print every check and return 0 iff all pass.

    On success (and unless `finalize=False` / `--keep-checkpoints`) the
    checkpoint folder is relocated to `_memory/_retired/0.20.0-originals/`.
    """
    results = run_checks(workspace, framework)
    for ok, msg in results:
        out(f"{'PASS' if ok else 'FAIL'}  {msg}")
    if not all(ok for ok, _ in results):
        return 1
    if finalize:
        finalize_checkpoints(Path(workspace), out=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="validate-0.20.0",
                                     description="Validate the 0.20.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--keep-checkpoints", action="store_true",
                        help="Leave _memory/_checkpoints/0.20.0/ in place after a pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace, args.framework, finalize=not args.keep_checkpoints)


if __name__ == "__main__":
    sys.exit(main())
