#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Machine-local transient-state root — `~/.superagent/` helper.

Single source of truth for the machine-local root defined in
`rules/machine-local-home.md`. The repo lives in iCloud Drive, so transient
high-churn state (scratch, sentinels, session markers) must live outside the
synced tree. Everything under the root is disposable and reconstructible.

CLI:
    uv run python -m superagent.tools.home            # ensure root + subdirs
    uv run python -m superagent.tools.home --check    # exit 1 if anything missing
    uv run python -m superagent.tools.home --json     # machine-readable report

Python:
    from superagent.tools.home import superagent_home, tmp_dir
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: Managed subdirectories under the root. New transient dirs are added here.
SUBDIRS = ("tmp", "tools")


def superagent_home() -> Path:
    """The machine-local root: `$SUPERAGENT_HOME` or `~/.superagent/`."""
    env = os.environ.get("SUPERAGENT_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".superagent"


def tmp_dir(ensure: bool = True) -> Path:
    """The canonical transient scratch tree, `<home>/tmp/`."""
    d = superagent_home() / "tmp"
    if ensure:
        d.mkdir(parents=True, exist_ok=True)
    return d


def tools_dir(ensure: bool = True) -> Path:
    """Machine-local install root for non-Python tools, `<home>/tools/`."""
    d = superagent_home() / "tools"
    if ensure:
        d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_all() -> Path:
    """Create the root and every managed subdir (idempotent)."""
    root = superagent_home()
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="home", description=__doc__.split("\n", 1)[0]
    )
    parser.add_argument("--check", action="store_true",
                        help="report only; exit 1 if root or any subdir is missing")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = superagent_home()
    if not args.check:
        ensure_all()

    missing = [name for name in SUBDIRS if not (root / name).is_dir()]
    if args.json:
        print(json.dumps({
            "home": str(root),
            "subdirs": {name: (root / name).is_dir() for name in SUBDIRS},
            "ok": not missing,
        }))
    elif missing:
        print(f"superagent home {root}: missing {', '.join(missing)}")
    else:
        print(f"superagent home {root}: ok ({', '.join(SUBDIRS)})")
    return 1 if (args.check and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
