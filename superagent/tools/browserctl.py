#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.45", "pillow>=10"]
# ///
"""browserctl - per-project Chromium lifecycle + drive layer (CLI, no MCP).

Replaces the Playwright MCP as Superagent's browser layer. Playwright stays
the automation engine: browserctl launches the Playwright-managed Chromium
binary directly, one persistent profile per purpose, each on a stable CDP
port, so any number of clients (this CLI, Python scripts via
``connect_over_cdp``, another agent session) can drive the same browser
without profile-lock conflicts. Chromium is the only supported engine.

Profile model (per project, clean by default):

- Every profile belongs to a **project** namespace. The project id comes from
  ``--project`` / ``$BROWSERCTL_PROJECT``, or is auto-derived from the git
  root (else CWD) folder name.
- A missing profile is created **clean** (empty user-data dir) on first
  launch - no logins are inherited from anywhere. The project's standing
  profile is ``default`` (create it explicitly with ``init``, or implicitly
  on first ``launch``).
- **Ephemeral profiles on demand**: ``launch --ephemeral`` marks the profile
  disposable - ``stop`` deletes it immediately and ``prune`` sweeps any that
  are stopped. ``launch --fresh`` spins up a clean ephemeral sibling
  (``<name>-2``, ...) when the requested profile is already running.
  ``persist``/``persist --forget`` flips the flag on an existing profile.

State lives outside any repo (never in an iCloud-synced checkout):
``~/.browserctl/`` ($BROWSERCTL_HOME aware) - ``profiles/<project>/<name>/``
user-data dirs, ``state.json`` registry (profile -> port/color/pid),
``logs/``, and ``out/<project>/`` for bare-filename snapshots/screenshots.
Ports are assigned once per profile (starting at 9400) and never change.
The home is shared machine-wide across every browserctl-carrying framework;
project namespaces keep them from colliding.

Lifecycle commands (all take ``--project P``; omitted = auto-derived):

    init    [--profile default]                 # ensure the project's clean standing profile
    launch  --profile <name> [--headless|--headed] [--minimized] [--url U]
            [--color '#RRGGBB'] [--ephemeral] [--fresh]
    stop    --profile <name>                    # graceful close (deletes an ephemeral)
    status  [--json] [--all]                    # this project's profiles (--all: every project)
    show    --profile <name>                    # restore/raise the window
    hide    --profile <name>                    # minimize the window
    clone   --from <name|project/name|dir> --to <name> [--color C]   # login-preserving slim copy
    theme   --profile <name> --color '#RRGGBB'  # browser must be closed
    persist --profile <name> [--forget]         # mark long-lived / revert to ephemeral
    prune   [--all]                             # delete stopped ephemeral profiles
    remove  --profile <name>                    # delete a profile (must be stopped)
    cdp-url --profile <name>                    # print http://localhost:<port> for attaching
    icon    [--png F | --icns F] [--color C] [--name "Superagent Browser"]
                                                # dedicated app bundle: own Dock icon/name
    doctor  [--install-profile]                 # Chromium is installed; on Linux its sandbox starts

Drive commands (replace the Playwright MCP tools; attach over CDP to a
running profile and leave the browser open):

    tabs       --profile <name>                     # list open tabs (index, title, url)
    navigate   --profile <name> --url U [--tab N | --new-tab]
    snapshot   --profile <name> --out FILE [--tab N]    # aria snapshot YAML
    screenshot --profile <name> --out FILE [--tab N] [--full-page]
    eval       --profile <name> --js EXPR [--tab N]     # page.evaluate, JSON result

``--tab`` defaults to the last (most recently opened) tab. For interactions
beyond these (hover, click, type, downloads, waiting on selectors), write a
short Python script against ``attach()`` - full Playwright API, same session:

    from browserctl import attach
    with attach("default") as (pw, browser):
        page = browser.contexts[0].pages[-1]

Known constraints: a live browser cannot flip between headless and headed -
stop and relaunch instead (the profile persists); clone only from a closed
source; browserctl is the ONLY launch path for its profiles (it launches with
``--use-mock-keychain`` - launching the same profile without it makes
Chromium silently purge every cookie).
"""

from __future__ import annotations

import sys
from pathlib import Path

# superagent/tools/ contains an `email/` package; with this script's own
# directory at sys.path[0] it shadows the stdlib `email` that http.client
# needs. Scrub the script dir from sys.path before any network import.
_HERE = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != _HERE]

import argparse  # noqa: E402
import contextlib  # noqa: E402
import fcntl  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402

BASE_PORT = 9400
DEFAULT_PROFILE = "default"
# Rotating defaults for new profiles so headed windows are distinguishable.
# Teal first (Superagent's brand color, matching the app icon); rotate after.
PALETTE = ["#14B8A6", "#2E7D32", "#1565C0", "#6A1B9A", "#E65100", "#AD1457"]
# The slim state set a `clone` carries over so logins survive (cookie
# encryption uses the mock keychain's fixed key, so copies decrypt fine).
# Everything else - caches, Service Worker state, GPU state - is rebuilt.
CLONE_ITEMS = [
    "Local State",
    "Default/Cookies",
    "Default/Local Storage",
    "Default/IndexedDB",
]


# ---------------------------------------------------------------- paths


def home() -> Path:
    """Superagent's private browserctl root: ~/.superagent/browserctl
    ($SUPERAGENT_HOME aware). NOT the machine-shared ~/.browserctl — sharing
    state.json across frameworks let one framework's `app_bundle` hijack
    another's launches. $BROWSERCTL_HOME stays the explicit override (e.g.
    point it at legacy ~/.browserctl to keep old profiles/logins)."""
    env = os.environ.get("BROWSERCTL_HOME")
    if env:
        return Path(env).expanduser()
    sa = os.environ.get("SUPERAGENT_HOME")
    root = Path(sa).expanduser() if sa else Path.home() / ".superagent"
    return root / "browserctl"


def sanitize_id(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a project/profile id from {raw!r}")
    return slug


def resolve_project(explicit: str | None) -> str:
    """Project id: --project > $BROWSERCTL_PROJECT > git root name > CWD name."""
    if explicit:
        return sanitize_id(explicit)
    env = os.environ.get("BROWSERCTL_PROJECT")
    if env:
        return sanitize_id(env)
    cwd = Path.cwd()
    for p in (cwd, *cwd.parents):
        if (p / ".git").exists():
            return sanitize_id(p.name)
    return sanitize_id(cwd.name)


def key(project: str, profile: str) -> str:
    return f"{project}/{profile}"


def profile_dir(project: str, profile: str) -> Path:
    return home() / "profiles" / project / profile


def state_path() -> Path:
    return home() / "state.json"


def out_dir(project: str) -> Path:
    p = home() / "out" / project
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- state


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {"profiles": {}}
    data = json.loads(p.read_text() or "{}")
    data.setdefault("profiles", {})
    return data


def save_state(state: dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
    tmp = home() / f".state.{os.getpid()}.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, state_path())


def mutate_state(fn):
    """Locked read-modify-write on the registry.

    Every writer goes through this (never hold a stale snapshot across a
    browser boot): an exclusive flock serializes concurrent browserctl
    processes, the state is re-read under the lock, ``fn(state)`` mutates it,
    and the result is saved before the lock drops. ``fn``'s return value is
    passed through. ``fn`` must not call launch/stop/clone (the lock is not
    re-entrant)."""
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / ".state.lock", "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        state = load_state()
        result = fn(state)
        save_state(state)
        return result


def register_profile(state: dict, project: str, profile: str,
                     color: str | None = None,
                     cloned_from: str | None = None) -> dict:
    """Ensure a registry entry with a stable port; return the entry."""
    profs = state["profiles"]
    k = key(project, profile)
    if k not in profs:
        used = {e.get("port") for e in profs.values()}
        port = BASE_PORT
        while port in used:
            port += 1
        profs[k] = {
            "port": port,
            "color": color or PALETTE[len(profs) % len(PALETTE)],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if cloned_from:
            profs[k]["cloned_from"] = cloned_from
    elif color:
        profs[k]["color"] = color
    return profs[k]


# ---------------------------------------------------------------- liveness


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, TypeError):
        return False


def cdp_alive(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(
                f"http://localhost:{port}/json/version", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def is_running(entry: dict) -> bool:
    return pid_alive(entry.get("pid")) and cdp_alive(entry["port"]) is not None


def _cdp_pages(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(
                f"http://localhost:{port}/json/list", timeout=3) as r:
            return [t for t in json.loads(r.read()) if t.get("type") == "page"]
    except Exception:
        return []


def _cdp_close_target(port: int, target_id: str) -> None:
    with contextlib.suppress(Exception):
        urllib.request.urlopen(
            f"http://localhost:{port}/json/close/{target_id}", timeout=3).read()


def _close_extra_tabs(port: int, keep_url: str | None) -> int:
    """Tab hygiene on launch: keep exactly one page (the requested URL when
    it can be identified, else the first listed) and close everything else a
    boot may have auto-opened (restored session, crash restore, stray
    about:blank). Returns the number of tabs closed."""
    pages = _cdp_pages(port)
    if len(pages) <= 1:
        return 0
    keep = None
    if keep_url:
        for t in pages:
            if t.get("url", "").rstrip("/") == keep_url.rstrip("/"):
                keep = t
                break
    if keep is None:
        keep = pages[0]
    closed = 0
    for t in pages:
        if t["id"] != keep["id"]:
            _cdp_close_target(port, t["id"])
            closed += 1
    return closed


def get_entry(project: str, profile: str) -> dict:
    entry = load_state()["profiles"].get(key(project, profile))
    if not entry:
        raise KeyError(f"unknown profile: {key(project, profile)}")
    return entry


def cdp_url(profile: str, project: str | None = None) -> str:
    """Attach endpoint for a registered profile (for scripts)."""
    return f"http://localhost:{get_entry(resolve_project(project), profile)['port']}"


@contextlib.contextmanager
def attach(profile: str, project: str | None = None):
    """Yield ``(playwright, browser)`` connected over CDP to a running profile."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url(profile, project))
        try:
            yield pw, browser
        finally:
            browser.close()  # disconnects; the browser itself keeps running


# ---------------------------------------------------------------- theme


def argb_signed(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #RRGGBB, got {hex_color!r}")
    val = 0xFF000000 | int(h, 16)
    return val - 0x100000000 if val > 0x7FFFFFFF else val


def _write_prefs(project: str, profile: str, mutate) -> None:
    """Locked-file-free read-modify-write of Default/Preferences (browser
    must be closed)."""
    prefs_path = profile_dir(project, profile) / "Default" / "Preferences"
    prefs = {}
    if prefs_path.exists():
        prefs = json.loads(prefs_path.read_text() or "{}")
    mutate(prefs)
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = prefs_path.with_name(f".prefs.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(prefs))
    os.replace(tmp, prefs_path)


def write_theme(project: str, profile: str, hex_color: str) -> None:
    """Write the theme color into Default/Preferences. Browser must be closed."""
    def _mutate(prefs: dict) -> None:
        prefs.setdefault("autogenerated", {}).setdefault("theme", {})["color"] = argb_signed(hex_color)
        prefs.setdefault("browser", {}).setdefault("theme", {})["user_color2"] = argb_signed(hex_color)

    _write_prefs(project, profile, _mutate)


def sanitize_session_prefs(project: str, profile: str) -> None:
    """Pre-launch tab hygiene: mark the previous exit clean and set startup
    to a single new-tab page, so a relaunch never restores a pile of old
    tabs (session restore or crash restore). Browser must be closed."""
    def _mutate(prefs: dict) -> None:
        prof = prefs.setdefault("profile", {})
        prof["exit_type"] = "Normal"
        prof["exited_cleanly"] = True
        prefs.setdefault("session", {})["restore_on_startup"] = 5  # NTP only

    _write_prefs(project, profile, _mutate)


# ---------------------------------------------------------------- launch/stop


def _install_command() -> str:
    """The command that installs the Chromium build THIS run's Playwright wants.

    browserctl's script environment resolves its own Playwright, and each
    Playwright release pins one Chromium build. A bare `--with playwright`
    install can resolve another release (a cached one, say) and fetch a build
    this run cannot use, so the command names the exact version."""
    try:
        from importlib.metadata import version
        pin = f'"playwright=={version("playwright")}"'
    except Exception:  # metadata missing: fall back to the unpinned form
        pin = "playwright"
    return f"uv run --no-project --with {pin} python -m playwright install chromium"


def chromium_executable() -> str:
    """The launch binary: the custom app bundle when one was built with the
    ``icon`` command (distinct Dock icon/name vs the user's main Chrome), else
    Playwright's Chromium - cached in the registry after the first resolution
    (spinning up the Playwright driver just to read the path is slow and noisy
    on teardown, so it runs once, in a child process)."""
    state = load_state()
    bundle = state.get("app_bundle")
    if bundle:
        exe_dir = Path(bundle) / "Contents" / "MacOS"
        if exe_dir.is_dir():
            # The binary name varies by Playwright build (Chromium /
            # Google Chrome for Testing) - take the bundle's executable.
            for p in sorted(exe_dir.iterdir()):
                if p.is_file() and os.access(p, os.X_OK):
                    return str(p)
    cached = state.get("chromium_exe")
    if cached and Path(cached).exists():
        return cached
    r = subprocess.run(
        [sys.executable, "-c",
         "from playwright.sync_api import sync_playwright\n"
         "with sync_playwright() as pw: print(pw.chromium.executable_path)"],
        capture_output=True, text=True)
    exe = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if exe is None:
        raise RuntimeError(f"could not resolve Playwright's Chromium:\n{r.stderr.strip()}")
    if not exe.exists():
        raise RuntimeError(
            f"Playwright's Chromium is not installed ({exe}) - run: "
            f"{_install_command()}")

    def _cache(state: dict) -> None:
        state["chromium_exe"] = str(exe)

    mutate_state(_cache)
    return str(exe)


def _playwright_chromium_bundle() -> Path:
    """The Playwright Chromium .app bundle (resolved via the same child-process
    trick as chromium_executable, bypassing any app_bundle preference)."""
    r = subprocess.run(
        [sys.executable, "-c",
         "from playwright.sync_api import sync_playwright\n"
         "with sync_playwright() as pw: print(pw.chromium.executable_path)"],
        capture_output=True, text=True)
    exe = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if exe is None or not exe.exists():
        raise RuntimeError(
            "Playwright's Chromium is not installed - run: "
            f"{_install_command()}")
    bundle = exe.parents[2]
    if bundle.suffix != ".app":
        raise RuntimeError(f"unexpected Chromium layout: {bundle}")
    return bundle


def build_app_bundle(png: str | None = None, icns: str | None = None,
                     color: str = "#14B8A6",
                     name: str = "Superagent Browser") -> Path:
    """Make a dedicated Chromium app bundle with its own name + Dock icon so
    browserctl windows are visually distinct from the user's main Chrome.

    APFS clonefile copy (`cp -Rc`) - instant and near-zero extra disk. The
    icon comes from --icns, --png (converted), or a generated rounded square
    in `color`. The bundle is ad-hoc re-signed; `launch` prefers it
    automatically once built (registry key `app_bundle`)."""
    if sys.platform != "darwin":
        raise RuntimeError("app bundles are a macOS feature")
    src_bundle = _playwright_chromium_bundle()

    app_dir = home() / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    bundle = app_dir / f"{name}.app"
    shutil.rmtree(bundle, ignore_errors=True)
    r = subprocess.run(["cp", "-Rc", str(src_bundle), str(bundle)],
                       capture_output=True, text=True)
    if r.returncode != 0:  # non-APFS fallback
        subprocess.run(["cp", "-R", str(src_bundle), str(bundle)], check=True)

    icns_out = bundle / "Contents" / "Resources" / "app.icns"
    if icns:
        shutil.copy2(Path(icns).expanduser(), icns_out)
    else:
        png_path = (Path(png).expanduser() if png
                    else _tinted_chrome_png(color) or _generate_icon_png(color))
        _png_to_icns(png_path, icns_out)

    plist = bundle / "Contents" / "Info.plist"
    for k, val in (("CFBundleDisplayName", name), ("CFBundleName", name),
                   ("CFBundleIdentifier", "io.superagent.browser")):
        subprocess.run(["plutil", "-replace", k, "-string", val, str(plist)],
                       check=True, capture_output=True)
    # macOS prefers CFBundleIconName (Assets.car asset catalog) over
    # CFBundleIconFile, so the replaced app.icns would never show. Drop the
    # catalog reference and the catalog itself.
    subprocess.run(["plutil", "-remove", "CFBundleIconName", str(plist)],
                   capture_output=True)
    (bundle / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(bundle)],
                   check=True, capture_output=True)

    def _set(state: dict) -> None:
        state["app_bundle"] = str(bundle)
        state["app_bundle_src"] = str(src_bundle)

    mutate_state(_set)
    return bundle


def ensure_app_bundle() -> None:
    """Lazily (re)build the branded bundle before a launch (macOS only), so a
    fresh install gets the Superagent Browser name/icon with zero manual
    steps. Builds when no bundle is registered or its dir is gone; rebuilds
    when the recorded Chromium source was removed (a Playwright update that
    cleaned the old build). Never blocks a launch — any failure just means
    the stock Chromium binary is used.

    Cheap by design: when a healthy bundle is registered, this is two stat
    calls. The full Playwright resolution (a ~1s child process) only runs on
    the build path. An update that leaves the old Chromium dir in place is
    NOT detected here — re-run `icon` (documented refresh path) to re-tint."""
    if sys.platform != "darwin":
        return
    state = load_state()
    bundle = state.get("app_bundle")
    src = state.get("app_bundle_src")
    if bundle and Path(bundle).is_dir() and (src is None or Path(src).is_dir()):
        return
    try:
        build_app_bundle()
    except Exception as exc:
        print(f"note: app bundle build failed ({exc}); using stock Chromium",
              file=sys.stderr)


def _tinted_chrome_png(color: str) -> Path | None:
    """The stock Chromium icon, desaturated and tinted `color` (luminance
    preserved: shadows go to a dark shade of the tint, highlights stay
    near-white). Returns None when the source icon can't be extracted."""
    import tempfile

    from PIL import Image, ImageOps

    # Prefer the user's real Chrome icon (no "TEST" ribbon) over the
    # Playwright Chrome-for-Testing artwork; both top out at 256 px.
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/Resources/app.icns"),
        _playwright_chromium_bundle() / "Contents" / "Resources" / "app.icns",
    ]
    src_icns = next((p for p in candidates if p.exists()), None)
    if src_icns is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        src_png = Path(td) / "src.png"
        r = subprocess.run(["sips", "-s", "format", "png", str(src_icns),
                            "--out", str(src_png)], capture_output=True)
        if r.returncode != 0 or not src_png.exists():
            return None
        img = Image.open(src_png).convert("RGBA")
        alpha = img.getchannel("A")
        tinted = ImageOps.colorize(ImageOps.grayscale(img),
                                   black="#03302b", mid=color,
                                   white="#dcfaf4").convert("RGBA")
        tinted.putalpha(alpha)
    out = home() / "app" / "icon-src.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    tinted.save(out)
    return out


def _generate_icon_png(color: str) -> Path:
    from PIL import Image, ImageDraw

    # The COLOR goes into the glyph, not a background plate: macOS 26+ icon
    # theming (dark / tinted styles) strips a full-bleed background and
    # re-plates the glyph on its own squircle, so color baked into the plate
    # renders gray. A colored glyph on transparency survives every style
    # (macOS supplies a light plate in the default style, dark otherwise).
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # A simple browser cue: a globe (ring + horizon + meridian), no text
    # (crisp at 16 px). Thick strokes so the glyph carries the color.
    cx, cy, r = size // 2, size // 2, int(size * 0.34)
    w = size // 14
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=w)
    d.line([cx - r + w // 2, cy, cx + r - w // 2, cy], fill=color, width=w)
    d.ellipse([cx - r // 2, cy - r, cx + r // 2, cy + r], outline=color,
              width=int(w * 0.75))
    out = home() / "app" / "icon-src.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def _png_to_icns(png: Path, icns_out: Path) -> None:
    import tempfile

    from PIL import Image

    src_px = Image.open(png).width
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "icon.iconset"
        iconset.mkdir()
        for pts in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = pts * scale
                if px > src_px:  # never upscale past the source (blurry)
                    continue
                suffix = f"{pts}x{pts}" + ("@2x" if scale == 2 else "")
                subprocess.run(["sips", "-z", str(px), str(px), str(png),
                                "--out", str(iconset / f"icon_{suffix}.png")],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset),
                        "-o", str(icns_out)], check=True, capture_output=True)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("localhost", port)) != 0


# ------------------------------------------------- linux desktop session

# What a headed browser needs from a graphical login on Linux. A shell reached
# over SSH (remote editors and agent harnesses included) carries none of it.
_DESKTOP_ENV_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
                     "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DBUS_SESSION_BUS_ADDRESS")
_NO_SANDBOX_MARK = "No usable sandbox"


def _unquote_systemd(value: str) -> str:
    """Undo the `$'...'` quoting `systemctl show-environment` applies to values
    that hold special characters; a plain value comes back unchanged."""
    if len(value) >= 3 and value.startswith("$'") and value.endswith("'"):
        return value[2:-1].replace("\\'", "'").replace("\\\\", "\\")
    return value


def desktop_session_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Display variables published by the logged-in desktop session (Linux).

    A graphical login (GNOME, KDE) imports its display variables into the
    user's systemd manager, and `systemctl --user show-environment` prints them
    to any shell of the same user. Returns {} when the command is missing,
    fails, or lists no display - never raises."""
    env = dict(os.environ if environ is None else environ)
    # An SSH shell without a login session may lack the runtime dir the user
    # manager is reached through; its location is fixed per uid.
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        r = subprocess.run(["systemctl", "--user", "show-environment"],
                           capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in r.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in _DESKTOP_ENV_KEYS and value:
            found[name] = _unquote_systemd(value)
    if not (found.get("DISPLAY") or found.get("WAYLAND_DISPLAY")):
        return {}
    return found


def launch_env(headless: bool, environ: dict[str, str] | None = None,
               platform: str | None = None) -> dict[str, str]:
    """Environment for the browser process.

    Unchanged on macOS, for headless launches, and for a shell that already
    has a display (X11 forwarding included). A headed launch on Linux from a
    shell with no display borrows the desktop session's variables; with no
    desktop session logged in it raises RuntimeError, because Chromium would
    otherwise die at startup with an unhelpful message."""
    env = dict(os.environ if environ is None else environ)
    if headless or not (platform or sys.platform).startswith("linux"):
        return env
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env
    session = desktop_session_env(env)
    if not session:
        raise RuntimeError(
            "a headed launch needs a display, and neither this shell nor a logged-in "
            "desktop session has one - log in on the machine's desktop (or enable "
            "automatic login), set DISPLAY and XAUTHORITY yourself, or launch --headless")
    env.update(session)
    return env


def _exit_hint(log_path: Path) -> str:
    """Extra guidance for a browser that died at startup, from its log tail."""
    try:
        tail = log_path.read_bytes()[-4000:].decode("utf-8", "replace")
    except OSError:
        return ""
    if _NO_SANDBOX_MARK in tail:
        return ("; Chromium found no usable sandbox - run `browserctl doctor` for the cause "
                "and the exact fix (on Ubuntu 23.10+ an AppArmor profile for Playwright's "
                "Chromium; skills/browserctl.md, \"Linux\")")
    return ""


# ------------------------------------------------- linux sandbox check

# Chromium's sandbox needs unprivileged user namespaces. Ubuntu 23.10 and later
# grant them per AppArmor profile, so Playwright's Chromium started from an
# unconfined shell dies with "No usable sandbox!" until a profile covers it.
SANDBOX_PROFILE_NAME = "playwright-chromium"
SANDBOX_PROFILE_DIR = Path("/etc/apparmor.d")
SANDBOX_PROFILE_GLOB = "/home/*/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"
_USERNS_SYSCTL = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
_PLAIN_PATH = re.compile(r"/[A-Za-z0-9_./+@-]+")


def _glob_covers(glob: str, path: str) -> bool:
    """Whether an AppArmor path glob built from literals and `*` matches a path
    (`*` stands for any run of characters inside one path component)."""
    rx = "".join("[^/]*" if part == "*" else re.escape(part)
                 for part in re.split(r"(\*)", glob))
    return re.fullmatch(rx, path) is not None


def _resolved(path: str) -> str:
    """The path with its symlinks resolved: AppArmor attaches a profile to the
    file a binary really is, whatever path it was started by."""
    return os.path.realpath(path)


def sandbox_attachment(exe: str) -> str:
    """The path expression the AppArmor profile attaches to: the standard glob
    for a Playwright cache under /home/<user>/.cache (it follows Chromium
    updates), else the binary's own resolved path."""
    real = _resolved(exe)
    return SANDBOX_PROFILE_GLOB if _glob_covers(SANDBOX_PROFILE_GLOB, real) else real


def sandbox_profile(exe: str) -> tuple[Path, str]:
    """The AppArmor profile that lets Playwright's Chromium create the user
    namespaces its sandbox needs, and nothing else (the profile is otherwise
    unconfined). Returns (the file it belongs in, its text). A binary outside
    the standard cache gets a profile and a file of its own (`-local`), so it
    never replaces the standard one."""
    attachment = sandbox_attachment(exe)
    name = SANDBOX_PROFILE_NAME + ("" if attachment == SANDBOX_PROFILE_GLOB else "-local")
    path = SANDBOX_PROFILE_DIR / name
    return path, (
        f"# {path}\n"
        "abi <abi/4.0>,\n"
        "include <tunables/global>\n"
        "\n"
        f"profile {name} {attachment} flags=(unconfined) {{\n"
        "  userns,\n"
        "\n"
        f"  include if exists <local/{name}>\n"
        "}\n")


def userns_restricted(sysctl: Path | None = None) -> bool:
    """Whether this kernel grants unprivileged user namespaces per AppArmor
    profile only (Ubuntu 23.10 and later). False when the setting is absent."""
    try:
        return (sysctl or _USERNS_SYSCTL).read_text().strip() == "1"
    except OSError:
        return False


def sandbox_probe(exe: str, timeout: float = 30.0) -> tuple[str, str]:
    """Start Chromium once, headless, on a throwaway profile, to learn whether
    its sandbox can start. Returns (state, detail): state is "ok" (detail: the
    time it took), "no-sandbox" (Chromium refused for want of a sandbox) or
    "failed" (anything else; detail: the reason). It opens no window, touches no
    browserctl profile, and takes a fraction of a second."""
    scratch_root = home() / "tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="probe-", dir=scratch_root))
    started = time.time()
    try:
        r = subprocess.run(
            [exe, "--headless=new", f"--user-data-dir={scratch}", "--no-first-run",
             "--use-mock-keychain", "--password-store=basic", "--dump-dom", "about:blank"],
            capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "failed", f"no answer within {timeout:.0f} s"
    except OSError as e:
        return "failed", str(e)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if r.returncode == 0:
        return "ok", f"{time.time() - started:.2f} s"
    err = r.stderr or ""
    if _NO_SANDBOX_MARK in err:
        return "no-sandbox", f"exit code {r.returncode}"
    tail = " | ".join(line.strip()[:200] for line in err.strip().splitlines()[-3:])
    return "failed", f"exit code {r.returncode}" + (f": {tail}" if tail else "")


def _sandbox_undo(path: Path) -> str:
    return f"sudo apparmor_parser -R {path} && sudo rm {path}"


def install_sandbox_profile(exe: str) -> tuple[int, list[str]]:
    """Write the AppArmor profile for Playwright's Chromium, load it, and check
    that Chromium now starts. A system change made with `sudo -n`: the caller
    has the owner's yes. Returns (exit code, lines to print). Where sudo needs
    a password the lines carry the commands to run in a terminal."""
    attachment = sandbox_attachment(exe)
    if attachment != SANDBOX_PROFILE_GLOB and not _PLAIN_PATH.fullmatch(attachment):
        return 1, [f"profile: {attachment} holds characters an AppArmor path needs quoted; "
                   "write the profile by hand (skills/browserctl.md, \"Linux\")"]
    path, text = sandbox_profile(exe)
    lines: list[str] = []
    try:
        earlier = path.read_text()
    except OSError:
        earlier = None
    staged = home() / f"{path.name}.apparmor"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(text)
    if earlier is not None and earlier != text:
        backup = home() / "backups" / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(earlier)
        lines.append(f"profile: the earlier {path} differs; a copy is kept at {backup}")
    steps: list[list[str]] = []
    if earlier != text:
        steps.append(["install", "-m", "0644", "-o", "root", "-g", "root",
                      str(staged), str(path)])
    steps.append(["apparmor_parser", "-r", str(path)])
    for n, step in enumerate(steps):
        try:
            r = subprocess.run(["sudo", "-n", *step], capture_output=True, text=True)
            failure = "" if r.returncode == 0 else (r.stderr or r.stdout or "").strip()[:300]
            failed = r.returncode != 0
        except OSError as e:
            failure, failed = str(e), True
        if failed:
            lines.append(f"profile: `sudo {' '.join(step)}` failed: {failure}")
            lines.append("  nothing more was changed. Where sudo asks for a password, run "
                         "these in a terminal, then `browserctl doctor` again:")
            lines.extend(f"      sudo {' '.join(s)}" for s in steps[n:])
            return 1, lines
    state, detail = sandbox_probe(exe)
    if state != "ok":
        lines.append(f"profile: {path} is installed and loaded, and Chromium "
                     f"still does not start ({state}: {detail})")
        lines.append(f"  undo: {_sandbox_undo(path)}")
        return 1, lines
    lines.append(f"profile: installed {path} and loaded it; Chromium now starts ({detail})")
    lines.append(f"  undo: {_sandbox_undo(path)}")
    return 0, lines


def doctor(install_profile: bool = False, platform: str | None = None) -> tuple[int, list[str]]:
    """Check what a launch needs on this machine: Playwright's Chromium is
    installed and, on Linux, its sandbox can start. Returns (exit code, lines
    to print): 0 when a launch can work, 1 with the cause and the exact fix
    when it cannot. With install_profile the AppArmor profile is written and
    loaded, and only when the sandbox refusal is the kind that profile fixes."""
    try:
        exe = chromium_executable()
    except RuntimeError as e:
        return 1, [f"chromium: {e}"]
    lines = [f"chromium: {exe}"]
    if not (platform or sys.platform).startswith("linux"):
        lines.append("sandbox: nothing to check on this system (the check is for Linux)")
        return 0, lines
    state, detail = sandbox_probe(exe)
    if state == "ok":
        lines.append(f"sandbox: ok (Chromium started headless in {detail})")
        if install_profile:
            lines.append("profile: nothing to install, the sandbox already works")
        return 0, lines
    if state == "failed":
        lines.append(f"sandbox: Chromium did not start ({detail})")
        lines.append("  this is another failure than the missing sandbox, and the AppArmor "
                     "profile does not fix it")
        return 1, lines
    lines.append("sandbox: Chromium found no usable sandbox")
    if not userns_restricted():
        lines.append("  cause: this system gives Chromium no user namespaces, and the AppArmor "
                     f"setting behind the usual case is off or absent ({_USERNS_SYSCTL})")
        lines.append("  the profile `--install-profile` writes does not apply here; see "
                     "Chromium's Linux sandbox notes for this distribution")
        lines.append("  never start Chromium with --no-sandbox to get past this")
        return 1, lines
    if install_profile:
        code, more = install_sandbox_profile(exe)
        return code, lines + more
    path, text = sandbox_profile(exe)
    lines.append("  cause: this system grants user namespaces per AppArmor profile "
                 f"({_USERNS_SYSCTL.name} = 1), and no loaded profile covers "
                 f"{_resolved(exe)}")
    lines.append("  fix (a system change, so ask the owner first): "
                 "browserctl doctor --install-profile")
    lines.append(f"       it writes {path} with sudo and reloads AppArmor")
    if path.exists():
        lines.append(f"       {path} exists already: it is reloaded, and rewritten when "
                     "its text differs (a copy of the earlier text is kept)")
    lines.append(f"  undo: {_sandbox_undo(path)}")
    lines.append("  the profile:")
    lines.extend(f"      {line}" for line in text.splitlines())
    lines.append("  never start Chromium with --no-sandbox to get past this")
    return 1, lines


def _fresh_name_in(state: dict, project: str, requested: str) -> str:
    n = 2
    while (key(project, f"{requested}-{n}") in state["profiles"]
           or profile_dir(project, f"{requested}-{n}").is_dir()):
        n += 1
    return f"{requested}-{n}"


def launch(project: str, profile: str, *, headless: bool,
           minimized: bool = False, url: str | None = None,
           color: str | None = None, ephemeral: bool = False,
           fresh: bool = False) -> dict:
    profile = sanitize_id(profile)

    # Phase 1 (locked): resolve --fresh renaming against current liveness.
    def _resolve(state: dict) -> str:
        entry = state["profiles"].get(key(project, profile))
        if fresh and entry and is_running(entry):
            # The requested profile is busy - spin up a clean temporary
            # sibling on the fly instead of sharing or failing.
            return _fresh_name_in(state, project, profile)
        return profile

    resolved = mutate_state(_resolve)
    sibling = resolved != profile
    profile = resolved

    # Phase 2 (locked): register + allocate the port; re-check liveness so a
    # concurrent launch of the same profile is reported, not double-booted.
    def _register(state: dict) -> dict:
        entry = register_profile(state, project, profile, color=color)
        # A --fresh sibling is always disposable; otherwise the flag is set
        # on creation and can be flipped later with `persist`.
        if "ephemeral" not in entry:
            entry["ephemeral"] = ephemeral or sibling
        elif ephemeral:
            entry["ephemeral"] = True
        return dict(entry)

    entry = mutate_state(_register)
    pdir = profile_dir(project, profile)
    pdir.mkdir(parents=True, exist_ok=True)  # clean first-use profile
    if is_running(entry):
        return {**entry, "profile": profile, "project": project,
                "already_running": True}
    if not port_free(entry["port"]):
        raise RuntimeError(
            f"port {entry['port']} is in use by another process; "
            f"free it or remove the profile entry from {state_path()}")

    # Stale locks from an unclean shutdown block relaunch; safe to clear
    # when nothing answers on the profile's CDP port.
    for lock in pdir.glob("Singleton*"):
        lock.unlink(missing_ok=True)
    if entry.get("color"):
        write_theme(project, profile, entry["color"])
    sanitize_session_prefs(project, profile)

    logs = home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{project}--{profile}.log"

    env = launch_env(headless)  # raises early when a headed launch has no display
    ensure_app_bundle()
    cmd = [
        chromium_executable(),
        f"--user-data-dir={pdir}",
        f"--remote-debugging-port={entry['port']}",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-crash-restore-bubble",
        # Playwright-style launches always use the mock keychain, so cookies
        # are encrypted with its fixed key. Launching the same profile
        # without it makes Chromium purge every cookie it cannot decrypt -
        # sessions silently vanish. Keep it, always.
        "--use-mock-keychain",
        "--password-store=basic",
    ]
    if headless:
        cmd.append("--headless=new")
        # headless=new defaults to 800x600, which cripples scroll-and-snapshot
        # sweeps on virtualized panes; give headless sessions a tall window.
        cmd.append("--window-size=1440,2400")
    else:
        # Headed windows otherwise inherit whatever tiny size the last
        # session left behind; open at a proper working size.
        cmd.append("--window-size=1680,1050")
        if minimized:
            cmd.append("--start-minimized")
    cmd.append(url or "about:blank")

    with open(log_path, "ab") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log,
                                start_new_session=True, cwd=str(pdir), env=env)

    deadline = time.time() + 60
    while time.time() < deadline:
        if cdp_alive(entry["port"]) is not None:
            break
        if proc.poll() is not None:
            raise RuntimeError(
                f"browser exited (code {proc.returncode}); see {log_path}"
                f"{_exit_hint(log_path)}")
        time.sleep(0.3)
    else:
        raise RuntimeError(
            f"chromium did not come up on port {entry['port']} within 60s")

    # Tab hygiene: a boot may auto-open tabs beyond the requested URL
    # (restored session, stray about:blank). Keep exactly one.
    time.sleep(0.5)  # let the initial target(s) register
    _close_extra_tabs(entry["port"], url)

    # Phase 3 (locked): record the boot on a fresh read - never write back a
    # snapshot held across the (slow) browser start.
    def _record(state: dict) -> dict:
        e = state["profiles"][key(project, profile)]
        e.update(pid=proc.pid, mode="headless" if headless else "headed",
                 started=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        if url:
            e["url"] = url
        return dict(e)

    entry = mutate_state(_record)
    return {**entry, "profile": profile, "project": project,
            "already_running": False}


def _remove_profile(project: str, profile: str) -> None:
    shutil.rmtree(profile_dir(project, profile), ignore_errors=True)
    with contextlib.suppress(OSError):  # drop the project dir once empty
        profile_dir(project, profile).parent.rmdir()
    (home() / "logs" / f"{project}--{profile}.log").unlink(missing_ok=True)

    def _drop(state: dict) -> None:
        state["profiles"].pop(key(project, profile), None)

    mutate_state(_drop)


def stop(project: str, profile: str) -> str:
    """Stop a profile's browser. Returns 'stopped', 'stopped+removed' (an
    ephemeral profile is deleted right away), 'not-running', or 'failed'
    (still alive - the registry keeps its pid so nothing downstream treats a
    live browser as gone)."""
    entry = load_state()["profiles"].get(key(project, profile))
    if not entry:
        return "not-running"
    if not is_running(entry):
        if entry.get("ephemeral") and profile != DEFAULT_PROFILE:
            _remove_profile(project, profile)
            return "stopped+removed"
        return "not-running"
    # Tab hygiene: close every tab before closing the browser, so nothing
    # lingers for a future restore. Closing the last tab may already quit
    # Chromium; the explicit Browser.close below is then a no-op.
    for t in _cdp_pages(entry["port"]):
        _cdp_close_target(entry["port"], t["id"])
    with contextlib.suppress(Exception):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(
                f"http://localhost:{entry['port']}")
            session = browser.new_browser_cdp_session()
            session.send("Browser.close")

    def _dead_within(seconds: float) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if not pid_alive(entry.get("pid")):
                return True
            time.sleep(0.3)
        return False

    if not _dead_within(8):
        with contextlib.suppress(ProcessLookupError):
            os.kill(entry["pid"], signal.SIGTERM)
        if not _dead_within(8):
            return "failed"

    if entry.get("ephemeral") and profile != DEFAULT_PROFILE:
        _remove_profile(project, profile)
        return "stopped+removed"

    def _clear(state: dict) -> None:
        e = state["profiles"].get(key(project, profile))
        if e:
            for k in ("pid", "started", "mode"):
                e.pop(k, None)

    mutate_state(_clear)
    return "stopped"


def set_window_state(project: str, profile: str, window_state: str) -> None:
    """CDP window control: 'normal' (show) or 'minimized' (hide)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url(profile, project))
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        session = ctx.new_cdp_session(page)
        win = session.send("Browser.getWindowForTarget")
        # A maximized/fullscreen window must pass through 'normal' first.
        session.send("Browser.setWindowBounds",
                     {"windowId": win["windowId"],
                      "bounds": {"windowState": "normal"}})
        if window_state != "normal":
            session.send("Browser.setWindowBounds",
                         {"windowId": win["windowId"],
                          "bounds": {"windowState": window_state}})
        if window_state == "normal":
            with contextlib.suppress(Exception):
                page.bring_to_front()
        browser.close()


# ---------------------------------------------------------------- drive


@contextlib.contextmanager
def _page(project: str, profile: str, tab: int | None = None):
    """Yield the selected page of a running profile (default: last tab)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url(profile, project))
        try:
            ctx = browser.contexts[0]
            pages = ctx.pages
            if not pages:
                pages = [ctx.new_page()]
            if tab is not None and not (0 <= tab < len(pages)):
                raise RuntimeError(
                    f"tab {tab} out of range - profile '{profile}' has "
                    f"{len(pages)} tab(s) (0..{len(pages) - 1})")
            yield pages[tab if tab is not None else -1]
        finally:
            browser.close()


def tabs(project: str, profile: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url(profile, project))
        try:
            return [{"tab": i, "title": p.title(), "url": p.url}
                    for i, p in enumerate(browser.contexts[0].pages)]
        finally:
            browser.close()


def navigate(project: str, profile: str, url: str, tab: int | None = None,
             new_tab: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url(profile, project))
        try:
            ctx = browser.contexts[0]
            page = ctx.new_page() if new_tab or not ctx.pages else \
                ctx.pages[tab if tab is not None else -1]
            # domcontentloaded, not load: SPA-heavy sites often never fire load.
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=10000)
            return {"title": page.title(), "url": page.url}
        finally:
            browser.close()


def snapshot(project: str, profile: str, out: Path, tab: int | None = None) -> Path:
    """Aria snapshot of the page body (YAML) - the same content the Playwright
    MCP's browser_snapshot produced."""
    with _page(project, profile, tab) as page:
        text = page.locator("body").aria_snapshot()
        header = f"# url: {page.url}\n# title: {page.title()}\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(header + text + "\n", encoding="utf-8")
    return out


def screenshot(project: str, profile: str, out: Path, tab: int | None = None,
               full_page: bool = False) -> Path:
    with _page(project, profile, tab) as page:
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=full_page)
    return out


def evaluate(project: str, profile: str, js: str, tab: int | None = None):
    with _page(project, profile, tab) as page:
        return page.evaluate(js)


# ---------------------------------------------------------------- clone


def _resolve_src_dir(project: str, src: str) -> tuple[Path, str | None]:
    """Source for a clone: a path, '<project>/<profile>', or a profile name
    in the current project. Returns (dir, registry key or None)."""
    if "/" in src and (src.startswith("~") or src.startswith("/") or src.startswith(".")):
        return Path(src).expanduser(), None
    if "/" in src:
        proj, prof = src.split("/", 1)
        return profile_dir(proj, prof), key(proj, prof)
    return profile_dir(project, src), key(project, src)


def clone(project: str, src: str, dst: str, color: str | None = None) -> Path:
    dst = sanitize_id(dst)
    src_dir, src_key = _resolve_src_dir(project, src)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"source profile not found: {src_dir}")

    state = load_state()
    src_entry = state["profiles"].get(src_key) if src_key else None
    if src_entry and is_running(src_entry):
        raise RuntimeError(f"source profile '{src}' is running - stop it before cloning")
    if list(src_dir.glob("SingletonSocket")):
        # A live browser launched outside browserctl still holds it.
        raise RuntimeError(f"{src_dir} appears to be in use (SingletonSocket "
                           "present); close that browser before cloning")

    dst_dir = profile_dir(project, dst)
    if dst_dir.exists() and any(dst_dir.iterdir()):
        raise FileExistsError(f"destination profile already exists: {dst_dir}")

    # Copy into a staging dir and rename into place so an interrupted clone
    # never leaves a half-profile that a later launch would silently boot.
    staging = home() / "profiles" / project / f".clone.{dst}.{os.getpid()}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True)
        for item in CLONE_ITEMS:
            s = src_dir / item
            if not s.exists():
                continue
            d = staging / item
            d.parent.mkdir(parents=True, exist_ok=True)
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
                # SQLite sidecars (Cookies-journal / -wal) keep the copy consistent.
                for side in s.parent.glob(s.name + "-*"):
                    shutil.copy2(side, d.parent / side.name)
        for lock in staging.glob("Singleton*"):
            lock.unlink(missing_ok=True)
        if dst_dir.exists():
            dst_dir.rmdir()  # empty dir from an earlier ensure; rename needs it gone
        os.rename(staging, dst_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    def _register_clone(st: dict) -> dict:
        return dict(register_profile(st, project, dst, color=color,
                                     cloned_from=src_key or str(src_dir)))

    entry = mutate_state(_register_clone)
    write_theme(project, dst, entry["color"])
    return dst_dir


# ---------------------------------------------------------------- status


def status(project: str, all_projects: bool = False) -> list[dict]:
    def _scan(state: dict) -> list[dict]:
        rows = []
        for k, entry in sorted(state["profiles"].items()):
            proj, _, prof = k.partition("/")
            if not all_projects and proj != project:
                continue
            running = is_running(entry)
            if not running and entry.get("pid"):
                for stale in ("pid", "started", "mode"):
                    entry.pop(stale, None)
            rows.append({
                "project": proj,
                "profile": prof,
                "port": entry["port"],
                "color": entry.get("color"),
                "running": running,
                "mode": entry.get("mode"),
                "pid": entry.get("pid"),
                "ephemeral": entry.get("ephemeral", False),
                "dir_exists": profile_dir(proj, prof).is_dir(),
            })
        return rows

    return mutate_state(_scan)


def set_persistent(project: str, profile: str, persistent: bool = True) -> None:
    """Flip a profile's ephemeral flag (persist survives `prune`)."""
    def _flip(state: dict) -> None:
        entry = state["profiles"].get(key(project, profile))
        if not entry:
            raise KeyError(f"unknown profile: {key(project, profile)}")
        entry["ephemeral"] = not persistent

    mutate_state(_flip)


def prune(project: str, all_projects: bool = False) -> list[str]:
    """Remove stopped ephemeral profiles (dir + registry + log)."""
    def _pick(state: dict) -> list[tuple[str, str]]:
        victims = []
        for k, entry in state["profiles"].items():
            proj, _, prof = k.partition("/")
            if not all_projects and proj != project:
                continue
            if prof == DEFAULT_PROFILE or not entry.get("ephemeral"):
                continue
            if is_running(entry):
                continue
            victims.append((proj, prof))
        return victims

    removed = []
    for proj, prof in mutate_state(_pick):
        _remove_profile(proj, prof)
        removed.append(key(proj, prof))
    return removed


def remove(project: str, profile: str) -> None:
    """Delete a profile explicitly (any profile, `default` included).
    Refuses while its browser is running."""
    entry = load_state()["profiles"].get(key(project, profile))
    if not entry and not profile_dir(project, profile).is_dir():
        raise KeyError(f"unknown profile: {key(project, profile)}")
    if entry and is_running(entry):
        raise RuntimeError(
            f"profile '{key(project, profile)}' is running - stop it first")
    _remove_profile(project, profile)


def init(project: str, profile: str = DEFAULT_PROFILE) -> dict:
    """Ensure the project's standing profile exists (clean) without launching:
    registry entry + empty user-data dir. Idempotent - meant for project init."""
    def _register(state: dict) -> dict:
        entry = register_profile(state, project, profile)
        entry.setdefault("ephemeral", False)
        return dict(entry)

    entry = mutate_state(_register)
    profile_dir(project, profile).mkdir(parents=True, exist_ok=True)
    return {**entry, "profile": profile, "project": project}


# ---------------------------------------------------------------- CLI


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project",
                        help="project namespace (default: auto-derived from "
                             "the git root / CWD folder name)")

    ap = argparse.ArgumentParser(prog="browserctl",
                                 description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", parents=[common],
                       help="ensure the project's clean standing profile (no launch)")
    p.add_argument("--profile", default=DEFAULT_PROFILE)

    p = sub.add_parser("launch", parents=[common],
                       help="launch (or report) a profile's browser; a missing "
                            "profile is created clean")
    p.add_argument("--profile", required=True)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true")
    mode.add_argument("--headed", action="store_true")
    p.add_argument("--minimized", action="store_true",
                   help="headed launch starts minimized (interactive-but-quiet)")
    p.add_argument("--url")
    p.add_argument("--color", help="#RRGGBB theme override (persisted)")
    p.add_argument("--ephemeral", action="store_true",
                   help="mark the profile disposable: deleted on stop / prune")
    p.add_argument("--fresh", action="store_true",
                   help="if the requested profile is already running, create "
                        "and launch a clean ephemeral sibling (<name>-2, ...)")

    p = sub.add_parser("stop", parents=[common],
                       help="gracefully close a profile's browser "
                            "(an ephemeral profile is deleted)")
    p.add_argument("--profile", required=True)

    for name in ("show", "hide"):
        p = sub.add_parser(name, parents=[common],
                           help=f"{name} the profile's window (CDP)")
        p.add_argument("--profile", required=True)

    p = sub.add_parser("clone", parents=[common],
                       help="slim-copy logins from an existing profile")
    p.add_argument("--from", dest="src", required=True,
                   help="profile name, <project>/<profile>, or a user-data dir path")
    p.add_argument("--to", dest="dst", required=True)
    p.add_argument("--color", help="#RRGGBB for the new profile")

    p = sub.add_parser("theme", parents=[common],
                       help="set theme color (browser must be closed)")
    p.add_argument("--profile", required=True)
    p.add_argument("--color", required=True)

    p = sub.add_parser("status", parents=[common],
                       help="list this project's profiles + liveness")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true", help="every project's profiles")

    p = sub.add_parser("persist", parents=[common],
                       help="mark a profile long-lived (survives prune)")
    p.add_argument("--profile", required=True)
    p.add_argument("--forget", action="store_true", help="revert to ephemeral")

    p = sub.add_parser("prune", parents=[common],
                       help="remove stopped ephemeral profiles")
    p.add_argument("--all", action="store_true", help="across every project")

    p = sub.add_parser("remove", parents=[common],
                       help="delete a profile (must be stopped; default included)")
    p.add_argument("--profile", required=True)

    p = sub.add_parser("cdp-url", parents=[common],
                       help="print the profile's CDP attach endpoint")
    p.add_argument("--profile", required=True)

    p = sub.add_parser("icon",
                       help="build the dedicated app bundle (own Dock "
                            "icon/name for browserctl windows; macOS only)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--png", help="1024px PNG to convert into the icon")
    src.add_argument("--icns", help="ready-made .icns to use as-is")
    p.add_argument("--color", default="#14B8A6",
                   help="fill for the generated icon (default teal)")
    p.add_argument("--name", default="Superagent Browser", help="app/Dock name")

    p = sub.add_parser("doctor", parents=[common],
                       help="check that Chromium is installed and, on Linux, that "
                            "its sandbox can start")
    p.add_argument("--install-profile", action="store_true",
                   help="Linux: write and load the AppArmor profile Chromium's sandbox "
                        "needs (uses sudo; a system change, so only after the owner's yes)")

    p = sub.add_parser("tabs", parents=[common], help="list open tabs")
    p.add_argument("--profile", required=True)

    p = sub.add_parser("navigate", parents=[common],
                       help="navigate a tab (or open a new one)")
    p.add_argument("--profile", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--tab", type=int)
    p.add_argument("--new-tab", action="store_true")

    p = sub.add_parser("snapshot", parents=[common],
                       help="save an aria snapshot (YAML)")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True,
                   help="output path (bare filenames land in "
                        "~/.browserctl/out/<project>/, never the CWD)")
    p.add_argument("--tab", type=int)

    p = sub.add_parser("screenshot", parents=[common], help="save a PNG screenshot")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tab", type=int)
    p.add_argument("--full-page", action="store_true")

    p = sub.add_parser("eval", parents=[common],
                       help="evaluate JS in the page; print JSON result")
    p.add_argument("--profile", required=True)
    p.add_argument("--js", required=True)
    p.add_argument("--tab", type=int)

    args = ap.parse_args()
    project = resolve_project(getattr(args, "project", None))

    def resolve_out(raw: str) -> Path:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else out_dir(project) / p

    if args.cmd == "init":
        info = init(project, sanitize_id(args.profile))
        print(f"{info['project']}/{info['profile']}: ready (clean profile, "
              f"port {info['port']})")
        return 0

    if args.cmd == "launch":
        info = launch(project, args.profile,
                      headless=args.headless and not args.headed,
                      minimized=args.minimized, url=args.url, color=args.color,
                      ephemeral=args.ephemeral, fresh=args.fresh)
        verb = "already running" if info["already_running"] else "launched"
        eph = " (ephemeral)" if info.get("ephemeral") else ""
        print(f"{info['project']}/{info['profile']}: {verb} "
              f"({info.get('mode', '?')}){eph} - "
              f"port {info['port']} pid {info.get('pid')}")
        return 0

    if args.cmd == "stop":
        outcome = stop(project, args.profile)
        print(f"{project}/{args.profile}: {outcome}")
        return 1 if outcome == "failed" else 0

    if args.cmd in ("show", "hide"):
        set_window_state(project, args.profile,
                         "normal" if args.cmd == "show" else "minimized")
        print(f"{project}/{args.profile}: window "
              f"{'restored' if args.cmd == 'show' else 'minimized'}")
        return 0

    if args.cmd == "clone":
        dst_dir = clone(project, args.src, args.dst, color=args.color)
        port = load_state()["profiles"][key(project, args.dst)]["port"]
        print(f"cloned {args.src} -> {project}/{args.dst} ({dst_dir}) - port {port}")
        return 0

    if args.cmd == "theme":
        entry = load_state()["profiles"].get(key(project, args.profile))
        if not profile_dir(project, args.profile).is_dir():
            print(f"{project}/{args.profile}: no such profile", file=sys.stderr)
            return 1
        if (entry and is_running(entry)) or \
                list(profile_dir(project, args.profile).glob("SingletonSocket")):
            print(f"{project}/{args.profile}: browser is running - stop it first",
                  file=sys.stderr)
            return 1
        write_theme(project, args.profile, args.color)
        if entry:
            def _recolor(state: dict) -> None:
                e = state["profiles"].get(key(project, args.profile))
                if e:
                    e["color"] = args.color
            mutate_state(_recolor)
        print(f"{project}/{args.profile}: theme set to {args.color}")
        return 0

    if args.cmd == "persist":
        set_persistent(project, args.profile, persistent=not args.forget)
        print(f"{project}/{args.profile}: "
              f"{'ephemeral' if args.forget else 'persistent'}")
        return 0

    if args.cmd == "prune":
        removed = prune(project, all_projects=args.all)
        print(f"pruned: {', '.join(removed) if removed else 'nothing'}")
        return 0

    if args.cmd == "remove":
        remove(project, args.profile)
        print(f"{project}/{args.profile}: removed")
        return 0

    if args.cmd == "status":
        rows = status(project, all_projects=args.all)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            scope = "all projects" if args.all else f"project {project}"
            if not rows:
                print(f"no profiles registered ({scope})")
            for r in rows:
                run = (f"RUNNING {r['mode']} pid {r['pid']}" if r["running"]
                       else "stopped")
                eph = " (ephemeral)" if r["ephemeral"] else ""
                print(f"{r['project'] + '/' + r['profile']:<32} "
                      f"port {r['port']}  {r['color'] or '-':<8} {run}{eph}")
        return 0

    if args.cmd == "cdp-url":
        print(cdp_url(args.profile, project))
        return 0

    if args.cmd == "icon":
        bundle = build_app_bundle(png=args.png, icns=args.icns,
                                  color=args.color, name=args.name)
        print(f"app bundle built: {bundle} - future chromium launches use it "
              "(relaunch running profiles to pick it up)")
        return 0

    if args.cmd == "doctor":
        code, lines = doctor(install_profile=args.install_profile)
        print("\n".join(lines))
        return code

    if args.cmd == "tabs":
        for t in tabs(project, args.profile):
            print(f"[{t['tab']}] {t['title']!r} {t['url']}")
        return 0

    if args.cmd == "navigate":
        info = navigate(project, args.profile, args.url, tab=args.tab,
                        new_tab=args.new_tab)
        print(f"{info['title']!r} {info['url']}")
        return 0

    if args.cmd == "snapshot":
        print(snapshot(project, args.profile, resolve_out(args.out), tab=args.tab))
        return 0

    if args.cmd == "screenshot":
        print(screenshot(project, args.profile, resolve_out(args.out),
                         tab=args.tab, full_page=args.full_page))
        return 0

    if args.cmd == "eval":
        print(json.dumps(evaluate(project, args.profile, args.js, tab=args.tab),
                         indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
