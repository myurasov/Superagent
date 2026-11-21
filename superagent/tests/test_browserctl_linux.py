# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""browserctl on Linux: where a headed launch finds its display.

A shell reached over SSH has no display, so a headed launch borrows the
logged-in desktop session's variables. These tests cover that lookup and the
launch environment built from it; no browser is started.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "browserctl.py"


@pytest.fixture(scope="module")
def bc() -> ModuleType:
    """browserctl loaded by path (it is a script, and it edits sys.path on import)."""
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("browserctl_under_test", TOOL)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


def _fake_systemctl(monkeypatch, bc: ModuleType, stdout: str = "", returncode: int = 0,
                    raises: Exception | None = None) -> list[dict]:
    calls: list[dict] = []

    def run(cmd, **kwargs):
        calls.append({"cmd": cmd, "env": kwargs.get("env")})
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(bc.subprocess, "run", run)
    return calls


SESSION = (
    "HOME=/home/user\n"
    "DISPLAY=:1\n"
    "XAUTHORITY=/run/user/1000/gdm/Xauthority\n"
    "XDG_SESSION_TYPE=x11\n"
    "XDG_CURRENT_DESKTOP=ubuntu:GNOME\n"
    "SOMETHING_ELSE=ignored\n"
)


def test_desktop_session_env_keeps_only_display_variables(monkeypatch, bc):
    calls = _fake_systemctl(monkeypatch, bc, SESSION)
    env = bc.desktop_session_env({"PATH": "/usr/bin"})
    assert env == {"DISPLAY": ":1", "XAUTHORITY": "/run/user/1000/gdm/Xauthority",
                   "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
    assert calls[0]["cmd"] == ["systemctl", "--user", "show-environment"]
    # The user manager is reachable even from a shell with no login session.
    assert calls[0]["env"]["XDG_RUNTIME_DIR"].startswith("/run/user/")


def test_desktop_session_env_respects_an_existing_runtime_dir(monkeypatch, bc):
    calls = _fake_systemctl(monkeypatch, bc, SESSION)
    bc.desktop_session_env({"XDG_RUNTIME_DIR": "/custom/run"})
    assert calls[0]["env"]["XDG_RUNTIME_DIR"] == "/custom/run"


def test_desktop_session_env_unquotes_systemd_values(monkeypatch, bc):
    _fake_systemctl(monkeypatch, bc, "DISPLAY=:0\nXAUTHORITY=$'/run/user/1000/my auth'\n")
    assert bc.desktop_session_env({})["XAUTHORITY"] == "/run/user/1000/my auth"


@pytest.mark.parametrize("stdout", ["", "HOME=/home/user\nXAUTHORITY=/x\n", "DISPLAY=\n"])
def test_desktop_session_env_is_empty_without_a_display(monkeypatch, bc, stdout):
    _fake_systemctl(monkeypatch, bc, stdout)
    assert bc.desktop_session_env({}) == {}


def test_desktop_session_env_accepts_a_wayland_session(monkeypatch, bc):
    _fake_systemctl(monkeypatch, bc, "WAYLAND_DISPLAY=wayland-0\nXDG_SESSION_TYPE=wayland\n")
    assert bc.desktop_session_env({}) == {"WAYLAND_DISPLAY": "wayland-0",
                                          "XDG_SESSION_TYPE": "wayland"}


@pytest.mark.parametrize("failure", [
    {"returncode": 1},
    {"raises": FileNotFoundError("systemctl")},
    {"raises": subprocess.TimeoutExpired("systemctl", 5)},
])
def test_desktop_session_env_never_raises(monkeypatch, bc, failure):
    _fake_systemctl(monkeypatch, bc, SESSION, **failure)
    assert bc.desktop_session_env({}) == {}


def test_launch_env_is_untouched_off_linux_and_when_headless(monkeypatch, bc):
    calls = _fake_systemctl(monkeypatch, bc, SESSION)
    base = {"PATH": "/usr/bin"}
    assert bc.launch_env(False, base, platform="darwin") == base
    assert bc.launch_env(True, base, platform="linux") == base
    assert calls == []  # no lookup when none is needed


def test_launch_env_keeps_a_display_the_shell_already_has(monkeypatch, bc):
    calls = _fake_systemctl(monkeypatch, bc, SESSION)
    base = {"DISPLAY": "localhost:10.0"}
    assert bc.launch_env(False, base, platform="linux") == base
    assert calls == []


def test_launch_env_borrows_the_desktop_session_for_a_headed_linux_launch(monkeypatch, bc):
    _fake_systemctl(monkeypatch, bc, SESSION)
    env = bc.launch_env(False, {"PATH": "/usr/bin"}, platform="linux")
    assert env["PATH"] == "/usr/bin"
    assert env["DISPLAY"] == ":1"
    assert env["XAUTHORITY"] == "/run/user/1000/gdm/Xauthority"


def test_launch_env_says_so_when_no_desktop_session_is_logged_in(monkeypatch, bc):
    _fake_systemctl(monkeypatch, bc, "HOME=/home/user\n")
    with pytest.raises(RuntimeError, match="needs a display"):
        bc.launch_env(False, {"PATH": "/usr/bin"}, platform="linux")


def test_exit_hint_points_at_the_apparmor_profile(tmp_path, bc):
    log = tmp_path / "browser.log"
    log.write_text("[1:1:FATAL:zygote_host_impl_linux.cc] No usable sandbox! ...\n")
    assert "AppArmor" in bc._exit_hint(log)
    log.write_text("some other failure\n")
    assert bc._exit_hint(log) == ""
    assert bc._exit_hint(tmp_path / "missing.log") == ""
