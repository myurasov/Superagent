#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv.noSync uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Post-migration checks for 0.21.0 (every check is blocking).

Mirrors the ``## Validate`` bullets of ``superagent/migrations/0.21.0.md``:

- no rendered workbook (domain-named, or with a ``.xlsx.meta.yaml`` sidecar)
  and no sidecar remains directly inside any ``Domains/<Name>/`` folder (user
  spreadsheets are noted, not failed);
- neither ``_memory/inbox-log.yaml`` nor ``Inbox/_processed.yaml`` exists;
- the ledger parses and every ``moved[]`` row is absent from its live path
  and present, with the recorded sha256, in exactly one store
  (``_memory/_checkpoints/0.21.0/`` or ``_memory/_retired/0.21.0-originals/``);
- ``.version`` reads ``0.21.0`` and ``tools/version.py check`` agrees
  (skipped with a note while the framework's ``pyproject.toml`` still
  predates 0.21.0);
- re-running ``migrate.py`` (dry run) reports "nothing to do".

When every check passes, ``_memory/_checkpoints/0.21.0/`` is relocated to
``_memory/_retired/0.21.0-originals/`` (``--keep-checkpoints`` skips this).

Usage::

    uv run python superagent/migrations/0.21.0/validate.py --workspace <path> [--keep-checkpoints]

Exit codes: 0 all checks pass, 1 at least one check failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import importlib.util
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

Check = tuple[bool, str]


def _load_migrate() -> ModuleType:
    path = Path(__file__).with_name("migrate.py")
    spec = importlib.util.spec_from_file_location("migration_0_21_0_migrate", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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

def check_workbooks(mig: ModuleType, ws: Path) -> list[Check]:
    rendered, strays = mig.classify_workbooks(ws)
    results: list[Check] = [(False, f"workbooks: {p.relative_to(ws).as_posix()} still present "
                                    f"({reason})") for p, _kind, reason in rendered]
    if not rendered:
        results.append((True, "workbooks: no rendered workbook or render-cache sidecar under Domains/"))
    for p in strays:
        results.append((True, f"note: {p.relative_to(ws).as_posix()} kept (user spreadsheet)"))
    return results


def check_inbox_state(mig: ModuleType, ws: Path) -> list[Check]:
    left = mig.inbox_state_files(ws)
    if left:
        return [(False, f"inbox: {p.relative_to(ws).as_posix()} still present") for p in left]
    return [(True, "inbox: no inbox-triage decision log remains (Inbox/ itself untouched)")]


def check_ledger(mig: ModuleType, ws: Path, ledger: dict[str, Any]) -> list[Check]:
    results: list[Check] = []
    stores = [ws / mig.CHECKPOINT_REL, ws / mig.ORIGINALS_REL]
    moved = [m for m in (ledger.get("moved") or []) if isinstance(m, dict) and m.get("path")]
    bad = 0
    for m in moved:
        rel = str(m["path"])
        if (ws / rel).exists():
            bad += 1
            results.append((False, f"ledger: {rel} is back at its live path"))
        parked = [s / rel for s in stores if (s / rel).is_file()]
        if len(parked) != 1:
            bad += 1
            results.append((False, f"ledger: {rel} parked in {len(parked)} store(s), expected 1"))
            continue
        want = m.get("sha256")
        if want and mig.sha256_of(parked[0]) != want:
            bad += 1
            results.append((False, f"ledger: {rel} parked bytes differ from the recorded sha256"))
    if not bad:
        results.append((True, f"ledger: {len(moved)} moved file(s) parked once each, bytes intact"))
    return results


def check_version_tool(mig: ModuleType, ws: Path) -> Check:
    """`tools/version.py check` agrees with `.version`.

    Skipped while the framework's pyproject predates 0.21.0 (release bump
    pending). When the framework is already PAST 0.21.0 the tool rightly
    reports the next migration as pending; this step is then complete iff the
    workspace reads exactly 0.21.0 (the chain's next step takes it further).
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


def check_rerun(mig: ModuleType, ws: Path, framework: Path) -> Check:
    lines: list[str] = []
    code = mig.run_migration(ws, framework=framework, dry_run=True, out=lines.append)
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
    results: list[Check] = []
    results.extend(check_workbooks(mig, ws))
    results.extend(check_inbox_state(mig, ws))
    ledger = _ledger(mig, ws)
    if ledger is None:
        results.append((False, f"ledger: {mig.RETIRED_REL.as_posix()}/{mig.MOVES_MANIFEST_NAME} does not parse"))
        ledger = {}
    results.extend(check_ledger(mig, ws, ledger))
    from superagent.tools.version import workspace_version
    current = workspace_version(ws)
    results.append((current == mig.TO_VERSION, f".version reads {current}"))
    results.append(check_version_tool(mig, ws))
    results.append(check_rerun(mig, ws, framework))
    return results


def finalize_checkpoints(ws: Path, mig: ModuleType | None = None, out=print) -> bool:
    """Park `_memory/_checkpoints/0.21.0/` as `_memory/_retired/0.21.0-originals/`.

    An earlier original at the destination wins. Returns True when something
    was relocated.
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
    checkpoint folder is relocated to `_memory/_retired/0.21.0-originals/`.
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
    parser = argparse.ArgumentParser(prog="validate-0.21.0",
                                     description="Validate the 0.21.0 workspace migration.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--framework", type=Path, default=None)
    parser.add_argument("--keep-checkpoints", action="store_true",
                        help="Leave _memory/_checkpoints/0.21.0/ in place after a pass.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_validate(args.workspace, args.framework, finalize=not args.keep_checkpoints)


if __name__ == "__main__":
    sys.exit(main())
