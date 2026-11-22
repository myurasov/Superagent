#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""The cycle lock: one update cycle at a time on this machine.

An update cycle (the `update` skill) fetches, files and reconciles across many
separate commands. Two cycles running at once on one machine would check the
same watchers twice and file the same document twice. This tool serializes
them with a KERNEL lock (`flock`) on a machine-local file:

    ~/.superagent/state/workspace-<hash>.cycle.lock

The file is machine-local on purpose: the workspace may sync between machines,
and a lock file inside it would travel to machines it says nothing about. Two
machines are kept apart by other means (`rules/concurrent-sessions.md`).

A kernel lock belongs to a process, and a cycle spans many commands, so `hold`
starts a small background holder that keeps the lock until `release`, or until
its time limit (`--ttl`, 2 hours by default) runs out. A holder that dies takes
the lock with it: there is no stale lock to clean up, only a leftover note in
the file, which `status` ignores once the kernel says the lock is free.

  hold      Take the lock. Prints a token; `release` needs it. When the lock is
            taken, `--wait SECONDS` waits that long for it, then gives up:
            exit 1 means another cycle is running and this one should be
            skipped.
  release   Free the lock taken with `--token TOKEN`. `--force` frees whatever
            holds it (for a cycle that died without releasing).
  status    Who holds the lock, since when and until when; or that it is free.
  wait      Block until the lock is free, at most `--timeout SECONDS`: exit 0
            when it is free, exit 1 when it is still taken.

Only the cycle orchestrator takes this lock. A briefing reads stores and takes
none, and the watchlist keeps its own short lock around its state file.

Exit codes: 0 ok (`hold`: taken; `wait`: free); 1 the lock is held by another
run (`hold`, `wait`), or `release` was refused; 2 usage error.

CLI:
    uv run python -m superagent.tools.lock hold --who update [--ttl 2h] [--wait 120]
    uv run python -m superagent.tools.lock release --token TOKEN
    uv run python -m superagent.tools.lock status [--json]
    uv run python -m superagent.tools.lock wait [--timeout 120]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from superagent.tools import machines

FRAMEWORK = Path(__file__).resolve().parent.parent
LOCK_SUFFIX = ".cycle.lock"
DEFAULT_TTL_S = 2 * 3600
MAX_TTL_S = 24 * 3600
DEFAULT_WAIT_S = 120
POLL_S = 0.25
#: How long `hold` waits for the holder it started to say whether it got the lock.
HANDSHAKE_S = 15
#: How long `release` waits for a stopped holder to let go.
RELEASE_S = 10
_TTL_RE = re.compile(r"^(\d+)\s*([smhd]?)$")
_TTL_UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


# --------------------------------------------------------------------- helpers

def lock_path(workspace: Path) -> Path:
    """`~/.superagent/state/workspace-<hash>.cycle.lock` for `workspace`."""
    state = machines.local_state_path(workspace)
    return state.with_name(state.stem + LOCK_SUFFIX)


def parse_ttl(value: str | int) -> int:
    """Seconds from `90`, `90s`, `45m`, `2h` or `1d`. Raises ValueError otherwise."""
    match = _TTL_RE.match(str(value).strip().lower())
    if not match:
        raise ValueError(f"not a duration: {value!r} (use 90s, 45m, 2h or 1d)")
    seconds = int(match.group(1)) * _TTL_UNIT[match.group(2)]
    if not 0 < seconds <= MAX_TTL_S:
        raise ValueError(f"a hold lasts between 1 second and 24 hours, not {value!r}")
    return seconds


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def _read_note(path: Path) -> dict[str, Any]:
    """The holder's note in the lock file; {} when there is none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_free(path: Path) -> bool:
    """Ask the kernel: True when nobody holds the lock right now."""
    if not path.exists():
        return True
    with path.open("a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(fh, fcntl.LOCK_UN)
    return True


def status(workspace: Path) -> dict[str, Any]:
    """`{"held": bool, "path": ..., and the holder's who / since / until / pid when held}`."""
    path = lock_path(workspace)
    if is_free(path):
        return {"held": False, "path": str(path)}
    note = _read_note(path)
    return {"held": True, "path": str(path),
            **{k: note.get(k) for k in ("who", "since", "until", "pid", "machine")}}


def describe(info: dict[str, Any]) -> str:
    """One line for a status mapping."""
    if not info["held"]:
        return "free"
    who = info.get("who") or "an unnamed run"
    since = str(info.get("since") or "?")[:16].replace("T", " ")
    until = str(info.get("until") or "?")[:16].replace("T", " ")
    return f"held by {who} since {since} (until {until} at the latest)"


def wait_free(workspace: Path, timeout: float) -> bool:
    """Block until the lock is free, at most `timeout` seconds. True when it is free."""
    path = lock_path(workspace)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if is_free(path):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)


# ---------------------------------------------------------------------- holder

def _holder(path: Path, who: str, ttl: int, token: str) -> int:
    """The background process that keeps the lock. Prints `held` or `busy`, then goes quiet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("busy", flush=True)
        return 1
    since = _now()
    note = {"who": who, "token": token, "pid": os.getpid(), "machine": machines.machine_name(create=False),
            "since": since.isoformat(), "until": (since + dt.timedelta(seconds=ttl)).isoformat()}
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(note))
    fh.flush()
    stop = {"now": False}

    def _stop(_signum: int, _frame: Any) -> None:
        stop["now"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print("held", flush=True)
    # The caller stops reading after this line: never write to stdout again.
    with contextlib.suppress(OSError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    deadline = time.monotonic() + ttl
    while not stop["now"] and time.monotonic() < deadline:
        time.sleep(POLL_S)
    with contextlib.suppress(OSError):
        if _read_note(path).get("token") == token:
            fh.seek(0)
            fh.truncate()
            fh.flush()
    fcntl.flock(fh, fcntl.LOCK_UN)
    fh.close()
    return 0


def _start_holder(path: Path, who: str, ttl: int, token: str) -> bool:
    """Start a holder; True when it took the lock, False when the lock was taken."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(FRAMEWORK.parent)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    proc = subprocess.Popen(  # noqa: S603 - a fixed argument list, no shell
        [sys.executable, "-m", "superagent.tools.lock", "_holder", "--path", str(path),
         "--who", who, "--ttl", str(ttl), "--token", token],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=str(FRAMEWORK.parent), env=env, start_new_session=True, text=True)
    assert proc.stdout is not None
    answer = ""
    try:
        # One line, or the end of the stream when the holder died first.
        if select.select([proc.stdout], [], [], HANDSHAKE_S)[0]:
            answer = proc.stdout.readline().strip()
    finally:
        proc.stdout.close()
    if answer == "held":
        return True
    if proc.poll() is None and answer != "busy":
        proc.terminate()  # it never answered: do not leave it behind
    return False


def hold(workspace: Path, who: str, *, ttl: int = DEFAULT_TTL_S, wait: float = 0
         ) -> tuple[str | None, dict[str, Any]]:
    """Take the cycle lock. Returns (token, status): the token is None when the lock stayed taken.

    `wait` is how many seconds to wait for a taken lock before giving up.
    """
    path = lock_path(workspace)
    token = secrets.token_hex(4)
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        if _start_holder(path, who, ttl, token):
            return token, status(workspace)
        if time.monotonic() >= deadline:
            return None, status(workspace)
        time.sleep(min(1.0, max(POLL_S, deadline - time.monotonic())))


def _is_holder(pid: int) -> bool:
    """Best effort: is process `pid` one of this tool's holders? True when it cannot be told."""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], check=False,
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "superagent.tools.lock" in out if out.strip() else False


def release(workspace: Path, *, token: str | None = None, force: bool = False) -> tuple[bool, str]:
    """Free the lock. Returns (done, one line saying what happened).

    The lock is freed when `token` is the holder's, or when `force` is set.
    A lock that is already free counts as done.
    """
    path = lock_path(workspace)
    if is_free(path):
        return True, "the lock was already free"
    note = _read_note(path)
    if not force and (not token or token != note.get("token")):
        return False, f"not released: {describe(status(workspace))}; that is another run's hold"
    pid = note.get("pid")
    if not isinstance(pid, int) or not _is_holder(pid):
        return False, "not released: the lock is taken and its holder could not be identified"
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)
    if not wait_free(workspace, RELEASE_S):
        return False, f"not released: the holder (pid {pid}) did not let go"
    who = note.get("who") or "an unnamed run"
    return True, f"released ({'forced; it was ' if force and token != note.get('token') else ''}held by {who})"


# ------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    """The `lock` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    parser = argparse.ArgumentParser(prog="lock", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ho = sub.add_parser("hold", parents=[common], help="Take the cycle lock; prints a token.")
    ho.add_argument("--who", required=True, help="Who takes it, in a few words (shown by `status`).")
    ho.add_argument("--ttl", default=str(DEFAULT_TTL_S),
                    help="The longest the hold may last: 90s, 45m, 2h (default), 1d at most.")
    ho.add_argument("--wait", type=float, default=0,
                    help="Seconds to wait for a taken lock before giving up (default 0).")
    re_ = sub.add_parser("release", parents=[common], help="Free the lock taken with a token.")
    re_.add_argument("--token", default=None, help="The token `hold` printed.")
    re_.add_argument("--force", action="store_true", help="Free it whoever holds it.")
    st = sub.add_parser("status", parents=[common], help="Who holds the lock, or that it is free.")
    st.add_argument("--json", action="store_true", help="Print the status as JSON.")
    wa = sub.add_parser("wait", parents=[common], help="Block until the lock is free.")
    wa.add_argument("--timeout", type=float, default=DEFAULT_WAIT_S,
                    help=f"Seconds to wait at most (default {DEFAULT_WAIT_S}).")
    hd = sub.add_parser("_holder", help=argparse.SUPPRESS)
    for name in ("--path", "--who", "--ttl", "--token"):
        hd.add_argument(name, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "_holder":
        return _holder(Path(args.path), args.who, int(args.ttl), args.token)
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    if args.command == "hold":
        try:
            ttl = parse_ttl(args.ttl)
        except ValueError as exc:
            parser.error(str(exc))
        token, info = hold(workspace, args.who, ttl=ttl, wait=args.wait)
        if token is None:
            print(f"busy: {describe(info)}; skip this cycle")
            return 1
        print(f"token: {token}")
        print(describe(info))
        return 0
    if args.command == "release":
        if not args.token and not args.force:
            parser.error("give --token TOKEN (printed by `hold`), or --force")
        done, line = release(workspace, token=args.token, force=args.force)
        print(line)
        return 0 if done else 1
    if args.command == "status":
        info = status(workspace)
        print(json.dumps(info, indent=2) if args.json else describe(info))
        return 0
    free = wait_free(workspace, args.timeout)
    print("free" if free else f"still {describe(status(workspace))}")
    return 0 if free else 1


if __name__ == "__main__":
    sys.exit(main())
