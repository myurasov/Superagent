# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""browserctl on Linux: where a headed launch finds its display, and the
check that Chromium's sandbox can start.

A shell reached over SSH has no display, so a headed launch borrows the
logged-in desktop session's variables. Ubuntu grants the user namespaces
Chromium's sandbox needs per AppArmor profile, so `doctor` tests a start and
names the fix. These tests cover both; no browser is started and no system
file is written.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
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
    hint = bc._exit_hint(log)
    assert "AppArmor" in hint
    assert "browserctl doctor" in hint
    log.write_text("some other failure\n")
    assert bc._exit_hint(log) == ""
    assert bc._exit_hint(tmp_path / "missing.log") == ""


def test_install_command_pins_the_running_playwright_version(bc):
    cmd = bc._install_command()
    assert cmd.startswith("uv run --no-project --with ")
    assert cmd.endswith("python -m playwright install chromium")
    try:
        from importlib.metadata import version
        assert f'"playwright=={version("playwright")}"' in cmd
    except Exception:  # Playwright absent from the test environment
        assert "--with playwright " in cmd


# ---------------------------------------------------------------- sandbox check

SKILL = Path(__file__).resolve().parents[1] / "skills" / "browserctl.md"
STANDARD_EXE = "/home/user/.cache/ms-playwright/chromium-1300/chrome-linux-arm64/chrome"
REFUSAL = "[1:1:FATAL:zygote_host_impl_linux.cc(145)] No usable sandbox! If you are ...\n"


@pytest.fixture(autouse=True)
def _paths_as_written(monkeypatch, bc):
    """These tests name Linux paths. Another system may resolve `/home` to a
    place of its own (macOS does), so the tool's symlink resolution is left out."""
    monkeypatch.setattr(bc, "_resolved", lambda path: path)


def test_the_profile_attaches_to_the_resolved_binary(monkeypatch, tmp_path, bc):
    monkeypatch.undo()  # this test is about the resolution itself
    real = tmp_path / "builds" / "chrome"
    real.parent.mkdir()
    real.write_text("")
    link = tmp_path / "link"
    link.symlink_to(real)
    assert bc.sandbox_attachment(str(link)) == str(real.resolve())


def _fake_run(monkeypatch, bc: ModuleType, results: list) -> list[list[str]]:
    """Replace subprocess.run: each call takes the next result (an exception is raised)."""
    calls: list[list[str]] = []
    queue = list(results)

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(bc.subprocess, "run", run)
    return calls


def _done(returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


@pytest.fixture
def sandbox_home(monkeypatch, tmp_path, bc) -> Path:
    """A browserctl home and an AppArmor folder of the test's own."""
    monkeypatch.setenv("BROWSERCTL_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(bc, "SANDBOX_PROFILE_DIR", tmp_path / "apparmor.d")
    (tmp_path / "apparmor.d").mkdir()
    return tmp_path


def _doctor_with(monkeypatch, bc: ModuleType, probes: list[tuple[str, str]],
                 restricted: bool = True, exe: str = STANDARD_EXE) -> list[str]:
    """Stub what `doctor` looks at; returns the list the probe calls land in."""
    seen: list[str] = []
    queue = list(probes)

    def probe(path, timeout=30.0):
        seen.append(path)
        return queue.pop(0)

    monkeypatch.setattr(bc, "chromium_executable", lambda: exe)
    monkeypatch.setattr(bc, "sandbox_probe", probe)
    monkeypatch.setattr(bc, "userns_restricted", lambda sysctl=None: restricted)
    return seen


def test_sandbox_probe_reports_a_working_start(monkeypatch, sandbox_home, bc):
    calls = _fake_run(monkeypatch, bc, [_done(0)])
    state, detail = bc.sandbox_probe(STANDARD_EXE)
    assert state == "ok" and detail.endswith(" s")
    cmd = calls[0]
    assert cmd[0] == STANDARD_EXE and "--headless=new" in cmd
    assert "--no-sandbox" not in cmd
    scratch = [a for a in cmd if a.startswith("--user-data-dir=")][0].split("=", 1)[1]
    assert Path(scratch).parent == sandbox_home / "home" / "tmp"
    assert not Path(scratch).exists()  # the throwaway profile is removed


def test_sandbox_probe_recognizes_the_sandbox_refusal(monkeypatch, sandbox_home, bc):
    _fake_run(monkeypatch, bc, [_done(134, REFUSAL)])
    assert bc.sandbox_probe(STANDARD_EXE)[0] == "no-sandbox"


@pytest.mark.parametrize("result", [
    _done(1, "some other failure\n"),
    subprocess.TimeoutExpired(cmd="chrome", timeout=30),
    FileNotFoundError("chrome"),
])
def test_sandbox_probe_reports_any_other_failure(monkeypatch, sandbox_home, bc, result):
    _fake_run(monkeypatch, bc, [result])
    state, detail = bc.sandbox_probe(STANDARD_EXE)
    assert state == "failed" and detail
    assert not list((sandbox_home / "home" / "tmp").iterdir())


def test_userns_restricted_reads_the_kernel_setting(tmp_path, bc):
    setting = tmp_path / "apparmor_restrict_unprivileged_userns"
    assert bc.userns_restricted(setting) is False  # absent: another kind of system
    setting.write_text("0\n")
    assert bc.userns_restricted(setting) is False
    setting.write_text("1\n")
    assert bc.userns_restricted(setting) is True


def test_the_profile_glob_covers_every_build_folder_and_nothing_nested(bc):
    cache = "/home/user/.cache/ms-playwright/chromium-1300"
    for folder in ("chrome-linux", "chrome-linux64", "chrome-linux-arm64"):
        assert bc.sandbox_attachment(f"{cache}/{folder}/chrome") == bc.SANDBOX_PROFILE_GLOB
    nested = f"{cache}/chrome-linux/extra/chrome"
    assert bc.sandbox_attachment(nested) == nested


def test_the_profile_in_the_skill_is_the_one_the_tool_writes(bc):
    path, text = bc.sandbox_profile(STANDARD_EXE)
    assert path == Path("/etc/apparmor.d/playwright-chromium")
    blocks: list[list[str]] = []
    inside = False
    for line in SKILL.read_text().splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            if inside:
                blocks.append([])
        elif inside:
            blocks[-1].append(line)
    in_skill = [b for b in blocks if any("profile playwright-chromium" in line for line in b)]
    assert len(in_skill) == 1
    assert textwrap.dedent("\n".join(in_skill[0])) + "\n" == text


def test_a_binary_outside_the_standard_cache_gets_a_profile_of_its_own(bc):
    path, text = bc.sandbox_profile("/opt/browsers/chromium-1300/chrome-linux/chrome")
    assert path.name == "playwright-chromium-local"
    assert "profile playwright-chromium-local /opt/browsers/chromium-1300/chrome-linux/chrome " \
        in text


def test_doctor_checks_nothing_but_the_binary_off_linux(monkeypatch, bc):
    seen = _doctor_with(monkeypatch, bc, [])
    code, lines = bc.doctor(platform="darwin")
    assert code == 0 and seen == []
    assert lines[0] == f"chromium: {STANDARD_EXE}"


def test_doctor_reports_a_missing_chromium(monkeypatch, bc):
    def missing():
        raise RuntimeError("Playwright's Chromium is not installed (/x) - run: uv run ...")

    monkeypatch.setattr(bc, "chromium_executable", missing)
    code, lines = bc.doctor(platform="linux")
    assert code == 1 and "not installed" in lines[0]


def test_doctor_passes_when_the_sandbox_starts(monkeypatch, bc):
    seen = _doctor_with(monkeypatch, bc, [("ok", "0.25 s")])
    code, lines = bc.doctor(platform="linux")
    assert code == 0 and seen == [STANDARD_EXE]
    assert any(line.startswith("sandbox: ok") for line in lines)


def test_doctor_names_the_cause_the_fix_and_the_undo(monkeypatch, sandbox_home, bc):
    _doctor_with(monkeypatch, bc, [("no-sandbox", "exit code 134")])
    code, lines = bc.doctor(platform="linux")
    out = "\n".join(lines)
    profile = sandbox_home / "apparmor.d" / "playwright-chromium"
    assert code == 1
    assert "browserctl doctor --install-profile" in out
    assert "ask the owner first" in out
    assert f"undo: sudo apparmor_parser -R {profile} && sudo rm {profile}" in out
    assert f"profile playwright-chromium {bc.SANDBOX_PROFILE_GLOB} flags=(unconfined) {{" in out
    assert "never start Chromium with --no-sandbox" in out
    assert not profile.exists()  # a check writes nothing


def test_doctor_offers_no_profile_where_apparmor_is_not_the_cause(monkeypatch, bc):
    _doctor_with(monkeypatch, bc, [("no-sandbox", "exit code 134")], restricted=False)
    code, lines = bc.doctor(platform="linux", install_profile=True)
    out = "\n".join(lines)
    assert code == 1
    assert "does not apply here" in out and "sudo" not in out


def test_doctor_keeps_another_failure_apart_from_the_sandbox(monkeypatch, bc):
    _doctor_with(monkeypatch, bc, [("failed", "exit code 1: missing library")])
    code, lines = bc.doctor(platform="linux", install_profile=True)
    assert code == 1 and "missing library" in "\n".join(lines)
    assert "sudo" not in "\n".join(lines)


def test_install_profile_installs_nothing_when_the_sandbox_works(monkeypatch, sandbox_home, bc):
    _doctor_with(monkeypatch, bc, [("ok", "0.25 s")])
    calls = _fake_run(monkeypatch, bc, [])
    code, lines = bc.doctor(platform="linux", install_profile=True)
    assert code == 0 and calls == []
    assert "nothing to install" in lines[-1]


def test_install_profile_writes_loads_and_checks_again(monkeypatch, sandbox_home, bc):
    seen = _doctor_with(monkeypatch, bc, [("no-sandbox", "exit code 134"), ("ok", "0.25 s")])
    calls = _fake_run(monkeypatch, bc, [_done(0), _done(0)])
    code, lines = bc.doctor(platform="linux", install_profile=True)
    profile = sandbox_home / "apparmor.d" / "playwright-chromium"
    staged = sandbox_home / "home" / "playwright-chromium.apparmor"
    assert code == 0 and len(seen) == 2
    assert calls == [
        ["sudo", "-n", "install", "-m", "0644", "-o", "root", "-g", "root",
         str(staged), str(profile)],
        ["sudo", "-n", "apparmor_parser", "-r", str(profile)],
    ]
    assert staged.read_text() == bc.sandbox_profile(STANDARD_EXE)[1]
    assert f"undo: sudo apparmor_parser -R {profile} && sudo rm {profile}" in lines[-1]


def test_install_profile_only_reloads_a_file_that_is_already_right(monkeypatch, sandbox_home, bc):
    profile = sandbox_home / "apparmor.d" / "playwright-chromium"
    profile.write_text(bc.sandbox_profile(STANDARD_EXE)[1])
    calls = _fake_run(monkeypatch, bc, [_done(0)])
    monkeypatch.setattr(bc, "sandbox_probe", lambda path, timeout=30.0: ("ok", "0.25 s"))
    code, _ = bc.install_sandbox_profile(STANDARD_EXE)
    assert code == 0
    assert calls == [["sudo", "-n", "apparmor_parser", "-r", str(profile)]]
    assert not (sandbox_home / "home" / "backups").exists()


def test_install_profile_keeps_a_copy_of_a_differing_earlier_file(monkeypatch, sandbox_home, bc):
    profile = sandbox_home / "apparmor.d" / "playwright-chromium"
    profile.write_text("# an earlier, hand-written profile\n")
    _fake_run(monkeypatch, bc, [_done(0), _done(0)])
    monkeypatch.setattr(bc, "sandbox_probe", lambda path, timeout=30.0: ("ok", "0.25 s"))
    code, lines = bc.install_sandbox_profile(STANDARD_EXE)
    kept = list((sandbox_home / "home" / "backups").iterdir())
    assert code == 0 and len(kept) == 1
    assert kept[0].read_text() == "# an earlier, hand-written profile\n"
    assert str(kept[0]) in lines[0]


def test_install_profile_hands_over_the_commands_when_sudo_needs_a_password(
        monkeypatch, sandbox_home, bc):
    calls = _fake_run(monkeypatch, bc, [_done(1, "sudo: a password is required\n")])
    code, lines = bc.install_sandbox_profile(STANDARD_EXE)
    out = "\n".join(lines)
    profile = sandbox_home / "apparmor.d" / "playwright-chromium"
    assert code == 1 and len(calls) == 1  # it stops at the first refusal
    assert "a password is required" in out
    assert f"sudo apparmor_parser -r {profile}" in out
    assert "sudo install -m 0644 -o root -g root" in out


def test_install_profile_refuses_a_path_that_needs_quoting(monkeypatch, sandbox_home, bc):
    calls = _fake_run(monkeypatch, bc, [])
    code, lines = bc.install_sandbox_profile("/opt/my browsers/chrome")
    assert code == 1 and calls == []
    assert "by hand" in lines[0]


def test_install_profile_says_so_when_chromium_still_fails(monkeypatch, sandbox_home, bc):
    _fake_run(monkeypatch, bc, [_done(0), _done(0)])
    monkeypatch.setattr(bc, "sandbox_probe",
                        lambda path, timeout=30.0: ("no-sandbox", "exit code 134"))
    code, lines = bc.install_sandbox_profile(STANDARD_EXE)
    assert code == 1 and "still does not start" in lines[0]
