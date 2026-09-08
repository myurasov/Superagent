#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Post-migration checks for 0.19.0 (every check is blocking).

Mirrors the ``## Validate`` bullets of ``superagent/migrations/0.19.0.md``:

- ``_memory/watchlist-state.yaml`` parses and carries a ``watchers`` mapping;
- every ref in the registry (``Sources/Watchlist/`` or
  ``config.preferences.watchlist.path``) parses and its ``watch:`` block
  names a known pack or a shipped detect type (``tools/validate.py`` schema);
- B4 equality: for every folded ``data-sources.yaml`` row (read from
  ``_memory/_retired/``) the effective ``schedule`` and ``capture_mode`` --
  registry override, else the pack default -- equal the pre-migration values;
- rows with no shipped pack are reported as unfolded (informational);
- ``_memory/data-sources.yaml`` no longer sits under ``_memory/`` directly;
- no sidecar was moved: for each recorded move, the original folder holds no
  document the ref was describing; every remaining sidecar still has its
  sibling;
- no moved ref's old path remains in any ``Domains/*/sources.md`` /
  ``Projects/*/sources.md`` catalogue;
- ``.version`` reads ``0.19.0`` and ``tools/version.py check`` agrees (skipped
  with a note while the framework's ``pyproject.toml`` still predates 0.19.0);
- when ``superagent.tools.watchlist`` is importable, ``check --cycle
  daily-update --dry-run`` runs clean and dispatches no harvest
  (``summary.harvested == 0``); skipped with a note when it is not shipped.
  A failed run reports the tool's first ``errors[]`` entry.

When every check passes, ``_memory/_checkpoints/0.19.0/`` is relocated to
``_memory/_retired/0.19.0-originals/`` (``--keep-checkpoints`` skips this):
the checkpoint folder only exists while the migration is incomplete, while the
originals stay parked next to the ledger so ``revert.py`` remains byte-for-byte.

Usage::

    uv run python superagent/migrations/0.19.0/validate.py --workspace <path> [--keep-checkpoints]

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
from superagent.tools import validate as tv  # noqa: E402


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_19_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _pack_defaults(framework: Path, workspace: Path, pack: str) -> dict[str, Any]:
    """`defaults:` of a pack.yaml (custom overlay wins), or {} when the pack tree is absent."""
    for root in (workspace / "_custom" / "watchers", framework / "watchers"):
        path = root / pack / "pack.yaml"
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                return {}
            defaults = data.get("defaults") if isinstance(data, dict) else None
            return defaults if isinstance(defaults, dict) else {}
    return {}


def _effective(watch: dict[str, Any], defaults: dict[str, Any], key: str) -> Any:
    v = watch.get(key)
    if v in (None, ""):
        v = defaults.get(key)
    return v if v not in (None, "") else None


def check_b4(mig: ModuleType, ws: Path, framework: Path, registry: Path) -> list[tuple[bool, str]]:
    """Per folded row: effective schedule / capture_mode equal the pre-migration values."""
    results: list[tuple[bool, str]] = []
    retired = ws / mig.RETIRED_REL / mig.DATA_SOURCES_NAME
    live = ws / "_memory" / mig.DATA_SOURCES_NAME
    source = retired if retired.exists() else live if live.exists() else None
    if source is None:
        return [(True, "B4: no data-sources.yaml (live or retired); nothing was folded")]
    try:
        rows = mig.data_source_rows(source)
    except yaml.YAMLError as exc:
        return [(False, f"B4: {source.relative_to(ws).as_posix()} does not parse: {exc}")]
    for row in rows:
        rid = str(row["id"]).strip()
        if rid not in mig.FOLDABLE_PACKS:
            results.append((True, f"B4: {rid} unfolded (no shipped pack) -- stays in _retired/"))
            continue
        ref = registry / f"{rid}.ref.md"
        if not ref.is_file():
            results.append((False, f"B4: {rid} folded ref missing at {ref.relative_to(ws).as_posix()}"))
            continue
        fm, _ = si.parse_canonical_ref(ref)
        watch = fm.get("watch") if isinstance(fm, dict) else None
        if not isinstance(watch, dict) or watch.get("pack") != rid:
            results.append((False, f"B4: {rid} ref lacks `watch.pack: {rid}`"))
            continue
        defaults = _pack_defaults(framework, ws, rid)
        pre_sched = row.get("schedule") if isinstance(row.get("schedule"), str) and row["schedule"].strip() else None
        pre_cm = mig.normalize_capture_mode(row.get("capture_mode"))
        post_sched = _effective(watch, defaults, "schedule")
        post_cm = mig.normalize_capture_mode(_effective(watch, defaults, "capture_mode"))
        ok_sched = pre_sched is None or post_sched == pre_sched
        ok_cm = pre_cm is None or post_cm == pre_cm
        results.append((ok_sched and ok_cm,
                        f"B4: {rid} schedule {pre_sched!r} -> {post_sched!r}, "
                        f"capture_mode {pre_cm!r} -> {post_cm!r}"))
    return results


def check_moves(mig: ModuleType, ws: Path) -> list[tuple[bool, str]]:
    """No sidecar moved; no dangling old path in the catalogues; remaining sidecars intact."""
    results: list[tuple[bool, str]] = []
    manifest_path = ws / mig.RETIRED_REL / mig.MOVES_MANIFEST_NAME
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            return [(False, f"moves ledger does not parse: {exc}")]
    moved = [m for m in (manifest.get("moved_refs") or []) if isinstance(m, dict)]
    catalogues = [(p, p.read_text(encoding="utf-8")) for p in mig.catalogue_files(ws)]
    bad_sidecar = 0
    dangling = 0
    for m in moved:
        old_rel = str(m.get("from") or "")
        if not old_rel:
            continue
        old = ws / old_rel
        stem = si.ref_stem(old.name)
        if stem and old.parent.is_dir():
            siblings = [p for p in old.parent.iterdir() if p.is_file() and not si.is_ref_file(p)
                        and (p.name == stem or p.stem == stem)]
            if siblings:
                bad_sidecar += 1
                results.append((False, f"sidecar moved: {old_rel} described {siblings[0].name}"))
        for cat, text in catalogues:
            if old_rel in text:
                dangling += 1
                results.append((False, f"dangling ref path {old_rel} in "
                                       f"{cat.relative_to(ws).as_posix()}"))
    if not bad_sidecar:
        results.append((True, f"no sidecar moved ({len(moved)} standalone ref(s) relocated)"))
    if not dangling:
        results.append((True, "no dangling old ref paths in sources.md catalogues"))
    registry = ws / mig.watchlist_rel_path(_config(ws))
    orphans = 0
    for ref in mig.candidate_refs(ws, registry):
        verdict, _reason, fm = mig.classify_ref(ref, ws)
        if verdict == "sidecar" and si.companion_document(ref) is None and isinstance(fm, dict) \
                and not any(k in fm for k in mig.PAYMENT_KEYS):
            orphans += 1
            results.append((False, f"sidecar without sibling: {ref.relative_to(ws).as_posix()}"))
    if not orphans:
        results.append((True, "every remaining sidecar ref still has its document"))
    return results


def _config(ws: Path) -> dict[str, Any]:
    path = ws / "_memory" / "config.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def check_version_tool(mig: ModuleType, ws: Path) -> tuple[bool, str]:
    """`tools/version.py check` exits 0 (skipped while the framework predates 0.19.0)."""
    from superagent.tools.version import compare, current_version
    cur = current_version()
    if compare(cur, mig.TO_VERSION) < 0:
        return True, (f"version check skipped: framework pyproject at {cur} < {mig.TO_VERSION} "
                      "(release bump pending)")
    res = subprocess.run([sys.executable, "-m", "superagent.tools.version", "check",
                          "--workspace", str(ws)], cwd=_REPO_ROOT, capture_output=True,
                         text=True, check=False)
    tail = (res.stdout or res.stderr).strip().splitlines()[-1:] or ["(no output)"]
    return res.returncode == 0, f"version check exit {res.returncode}: {tail[0]}"


def _dry_run_failure(stdout: str, stderr: str) -> str:
    """Why a `watchlist check` run failed: its first `errors[]` entry when the
    payload is JSON (the loader's `<file>:<line>: <message>` form), else the
    last line of output -- never a truncated closing brace."""
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


def check_watchlist_dry_run(ws: Path) -> tuple[bool, str]:
    """`watchlist check --cycle daily-update --dry-run` dispatches no harvest."""
    if importlib.util.find_spec("superagent.tools.watchlist") is None:
        return True, "watchlist dry-run skipped: superagent.tools.watchlist not shipped yet"
    attempts = (
        ["--workspace", str(ws), "check", "--cycle", "daily-update", "--dry-run"],
        ["check", "--cycle", "daily-update", "--dry-run", "--workspace", str(ws)],
    )
    last = "(no output)"
    for args in attempts:
        res = subprocess.run([sys.executable, "-m", "superagent.tools.watchlist", *args],
                             cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
                             timeout=120)
        out = (res.stdout or "").strip()
        if res.returncode != 0:
            last = _dry_run_failure(res.stdout, res.stderr)
            continue
        try:
            payload = json.loads(out) if out else {}
        except json.JSONDecodeError:
            return False, f"watchlist dry-run output is not JSON: {out[:120]!r}"
        summary = payload.get("summary") if isinstance(payload, dict) else None
        harvested = (summary or {}).get("harvested", 0)
        return harvested == 0, f"watchlist dry-run: summary.harvested={harvested}"
    return False, f"watchlist dry-run failed to run: {last}"


def run_checks(workspace: Path, framework: Path | None = None) -> list[tuple[bool, str]]:
    """Return one (passed, message) row per check."""
    mig = _load_migrate()
    ws = Path(workspace)
    framework = Path(framework) if framework else mig.default_framework_root()
    results: list[tuple[bool, str]] = []

    state = ws / "_memory" / mig.STATE_NAME
    if not state.is_file():
        results.append((False, f"_memory/{mig.STATE_NAME} missing"))
    else:
        data, err = tv.load_yaml(state)
        if err:
            results.append((False, f"_memory/{mig.STATE_NAME} does not parse: {err}"))
        elif not isinstance(data, dict):
            results.append((False, f"_memory/{mig.STATE_NAME} is not a mapping"))
        else:
            errs = tv.check_watchlist_state(data, mig.STATE_NAME)
            results.extend((False, e) for e in errs)
            if not errs:
                n = len(data.get("watchers") or {})
                results.append((True, f"_memory/{mig.STATE_NAME} parses ({n} watcher row(s))"))

    registry = ws / mig.watchlist_rel_path(_config(ws))
    oks, errs = tv.validate_watchlist_refs(ws, framework)
    results.extend((False, e) for e in errs)
    results.append((True, f"{registry.relative_to(ws).as_posix()}/: {len(oks)} ref(s) valid"
                    if not errs else f"{len(errs)} invalid ref(s) in {registry.relative_to(ws).as_posix()}/"))
    if errs:
        results[-1] = (False, results[-1][1])

    results.extend(check_b4(mig, ws, framework, registry))

    live = ws / "_memory" / mig.DATA_SOURCES_NAME
    results.append((not live.exists(), f"_memory/{mig.DATA_SOURCES_NAME} "
                    f"{'still present' if live.exists() else 'retired'}"))

    results.extend(check_moves(mig, ws))

    cfg = _config(ws)
    wl = (cfg.get("preferences") or {}).get("watchlist") if isinstance(cfg.get("preferences"), dict) else None
    results.append((isinstance(wl, dict) or not (ws / "_memory" / "config.yaml").exists(),
                    "config.yaml: preferences.watchlist present" if isinstance(wl, dict)
                    else "config.yaml: preferences.watchlist missing"))

    from superagent.tools.version import workspace_version
    current = workspace_version(ws)
    results.append((current == mig.TO_VERSION, f".version reads {current}"))
    results.append(check_version_tool(mig, ws))
    results.append(check_watchlist_dry_run(ws))
    return results


def finalize_checkpoints(ws: Path, mig: ModuleType | None = None, out=print) -> bool:
    """Park `_memory/_checkpoints/0.19.0/` as `_memory/_retired/0.19.0-originals/`.

    The checkpoint folder exists only while the migration is incomplete; once
    every check passes the pre-migration originals move next to the ledger so
    `revert.py` stays byte-for-byte (it reads either location). An earlier
    original at the destination wins; the seed record is merged. Returns True
    when something was relocated.
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
    checkpoint folder is relocated to `_memory/_retired/0.19.0-originals/`.
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
    parser = argparse.ArgumentParser(prog="validate-0.19.0",
                                     description="Validate the 0.19.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--keep-checkpoints", action="store_true",
                        help="Leave _memory/_checkpoints/0.19.0/ in place after a pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace, args.framework, finalize=not args.keep_checkpoints)


if __name__ == "__main__":
    sys.exit(main())
