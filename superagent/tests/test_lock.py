# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/lock.py`: the cycle lock that serializes update cycles on one machine.

Every test takes real kernel locks through real holder processes. The lock
file lives under `$SUPERAGENT_HOME` (a temp folder, `conftest.py`), and each
test frees whatever it took.
"""
from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from superagent.tools import lock
from superagent.tools.home import superagent_home


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Path]:
    """A workspace path; whatever still holds its lock at the end is freed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace
    lock.release(workspace, force=True)


def test_a_fresh_lock_is_free_and_machine_local(ws: Path) -> None:
    info = lock.status(ws)
    assert info["held"] is False and lock.describe(info) == "free"
    path = lock.lock_path(ws)
    assert path.name.endswith(".cycle.lock")
    assert superagent_home() in path.parents, "the lock file never lives in the synced workspace"
    assert ws not in path.parents
    assert lock.release(ws, token="anything") == (True, "the lock was already free")


def test_two_overlapping_runs_serialize(ws: Path) -> None:
    first, info = lock.hold(ws, "run A")
    assert first and info["held"] and info["who"] == "run A"
    assert json.loads(lock.lock_path(ws).read_text())["token"] == first
    second, seen = lock.hold(ws, "run B")
    assert second is None, "a second run never gets the lock while the first holds it"
    assert seen["who"] == "run A"
    refused, why = lock.release(ws, token="not-the-token")
    assert not refused and "another run's hold" in why and "run A" in why
    assert lock.status(ws)["held"], "a wrong token frees nothing"
    done, line = lock.release(ws, token=first)
    assert done and line == "released (held by run A)"
    assert lock.status(ws)["held"] is False
    second, info = lock.hold(ws, "run B")
    assert second and second != first and info["who"] == "run B"


def test_a_second_run_waits_then_gets_the_lock(ws: Path) -> None:
    first, _info = lock.hold(ws, "run A", ttl=2)
    assert first
    started = time.monotonic()
    second, info = lock.hold(ws, "run B", wait=15)
    waited = time.monotonic() - started
    assert second and info["who"] == "run B"
    assert waited >= 0.5, "run B started only after run A's hold ended"


def test_a_second_run_waits_then_skips(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert lock.main(["hold", "--who", "run A", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("token: ") and out[1].startswith("held by run A since ")
    token = out[0].split(": ", 1)[1]
    started = time.monotonic()
    assert lock.main(["hold", "--who", "run B", "--wait", "1", "--workspace", str(ws)]) == 1
    assert time.monotonic() - started >= 1
    assert capsys.readouterr().out.startswith("busy: held by run A since ")
    assert lock.main(["wait", "--timeout", "0.3", "--workspace", str(ws)]) == 1
    assert capsys.readouterr().out.startswith("still held by run A")
    assert lock.main(["status", "--json", "--workspace", str(ws)]) == 0
    assert json.loads(capsys.readouterr().out)["who"] == "run A"
    assert lock.main(["release", "--token", token, "--workspace", str(ws)]) == 0
    assert lock.main(["wait", "--timeout", "0.3", "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "free"


def test_a_hold_ends_at_its_time_limit(ws: Path) -> None:
    token, _info = lock.hold(ws, "short run", ttl=1)
    assert token
    assert lock.wait_free(ws, 10), "the holder lets go when its time limit runs out"
    assert lock.lock_path(ws).read_text() == "", "and clears its note"


def test_a_dead_holder_leaves_no_stale_lock(ws: Path) -> None:
    token, info = lock.hold(ws, "crashed run")
    assert token and isinstance(info["pid"], int)
    os.kill(info["pid"], signal.SIGKILL)
    assert lock.wait_free(ws, 10), "the kernel frees the lock with the process"
    assert lock.lock_path(ws).read_text() != "", "the note stays behind"
    assert lock.status(ws) == {"held": False, "path": str(lock.lock_path(ws))}, "and is ignored"
    again, _info = lock.hold(ws, "next run")
    assert again


def test_force_release_takes_a_lock_from_a_dead_cycle(ws: Path) -> None:
    token, _info = lock.hold(ws, "abandoned run")
    assert token
    done, line = lock.release(ws, force=True)
    assert done and line == "released (forced; it was held by abandoned run)"
    assert lock.status(ws)["held"] is False


@pytest.mark.parametrize("value, seconds", [("90", 90), ("90s", 90), ("45m", 2700), ("2h", 7200),
                                            ("1d", 86400), (30, 30)])
def test_durations(value: str | int, seconds: int) -> None:
    assert lock.parse_ttl(value) == seconds


@pytest.mark.parametrize("value", ["", "soon", "0", "2d", "-5m", "1.5h"])
def test_bad_durations_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        lock.parse_ttl(value)


def test_usage_errors(ws: Path) -> None:
    for argv in (["release", "--workspace", str(ws)],
                 ["hold", "--who", "x", "--ttl", "forever", "--workspace", str(ws)],
                 ["hold", "--workspace", str(ws)]):
        with pytest.raises(SystemExit) as info:
            lock.main(argv)
        assert info.value.code == 2
    assert lock.status(ws)["held"] is False
