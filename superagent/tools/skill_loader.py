#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Prompt-submit hook: auto-load matching Superagent skills into context.

Superagent skills are invoked by trigger phrases, not slash commands — which
means loading the right one used to depend on the model recognizing the
trigger and opening the file by hand, the exact step that gets skipped. This
hook makes the load deterministic: on every prompt it matches the text
against each skill's declared frontmatter ``triggers`` and emits the skill
(or a compact pointer for long skills), so the harness — not the model —
puts the procedure in context.

Shape (ported from a sibling framework's skill loader):

- First time a skill matches in a session -> emit its body (skills over
  ``MAX_FULL_BODY_LINES`` lines emit a compact block instead: description +
  step index + a read instruction, honoring the read budget in AGENTS.md).
- Later turns where the same skill matches again -> a one-line reminder.
- Synthetic turns (task notifications, command transcripts, system
  reminders) are skipped: they quote skill names without requesting them.
- Session de-dup via the harness ``session_id`` and a JSON marker under
  ``~/.superagent/tmp/skill-loader/``.

Trigger matching is data-driven. Each trigger string is split on `` / ``
into alternate phrases; parenthetical qualifiers are dropped; ``<...>``
spans and bare capital-letter placeholders match one argument word; literal
words match on word boundaries. So ``"work on tasks/<slug>"`` matches
"work on tasks/2026-08-07-foo".

Wired to Claude Code's ``UserPromptSubmit`` in ``.claude/settings.json``
(injection is Claude-only: Cursor's ``beforeSubmitPrompt`` cannot add
context). Searches ``superagent/skills/`` AND ``workspace/_custom/skills/``
per the custom-overlay contract. Disable via
``_memory/config.yaml preferences.skill_autoload: false``.

Fail-safe: never raises, always exits 0, tolerates missing files — a
context-loading hook must never block the user's turn. As a footgun guard
it refuses to run as a hand-called CLI (args present, or an interactive tty
with no piped payload).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "superagent" / "skills"
CUSTOM_SKILLS_DIR = REPO_ROOT / "workspace" / "_custom" / "skills"
CONFIG_PATH = REPO_ROOT / "workspace" / "_memory" / "config.yaml"

#: Skills at or under this body length inject whole; longer ones inject a
#: compact pointer block (description + step index) per the read budget.
MAX_FULL_BODY_LINES = 150

_PH = "\x00"  # sentinel for a matched ``<...>`` span while tokenizing a trigger

STEP_INDEX_RE = re.compile(
    r"<!-- step-index:start -->.*?<!-- step-index:end -->", re.DOTALL
)


def read_payload(stream) -> dict:
    """Parse the hook's JSON stdin payload; any problem yields ``{}``."""
    try:
        raw = stream.read()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def autoload_enabled(config_path: Path = CONFIG_PATH) -> bool:
    """Kill switch: ``preferences.skill_autoload: false`` disables the hook."""
    if not config_path.exists():
        return True
    try:
        import yaml

        config = yaml.safe_load(config_path.read_text()) or {}
        prefs = config.get("preferences", {}) or {}
        return bool(prefs.get("skill_autoload", True))
    except Exception:
        return True


def parse_skill(path: Path) -> dict | None:
    """Extract ``{name, triggers, description, body, path}``; None if unparseable."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        import yaml

        front = yaml.safe_load(parts[1]) or {}
    except Exception:
        return None
    if not isinstance(front, dict):
        return None
    name = str(front.get("name") or "").strip()
    triggers = front.get("triggers") or []
    if not name or not isinstance(triggers, list):
        return None
    triggers = [t for t in triggers if isinstance(t, str) and t.strip()]
    if not triggers:
        return None
    return {
        "name": name,
        "triggers": triggers,
        "description": str(front.get("description") or "").strip(),
        "body": parts[2].strip(),
        "path": path,
    }


def discover_skills() -> list[dict]:
    """All parseable skills, framework first then custom overlay, name-sorted."""
    out: list[dict] = []
    for skills_dir in (SKILLS_DIR, CUSTOM_SKILLS_DIR):
        try:
            files = sorted(skills_dir.glob("*.md"))
        except Exception:
            continue
        for f in files:
            if f.name.startswith("_"):
                continue
            skill = parse_skill(f)
            if skill:
                out.append(skill)
    return out


def _phrases(trigger: str) -> list[str]:
    """Split a trigger into alternate phrases.

    Corpus semantics for `` / ``: a multi-word segment is a standalone
    alternate phrase (``"new task / start a task"``); a single-word segment
    substitutes the LAST word of the previous phrase (``"track this
    document / passport / will"`` means "track this passport", "track this
    will" — never a bare ``will``). ``<...>`` placeholder spans are protected
    before splitting (``"set up <host / tool / thing>"`` is one phrase), and
    parenthetical qualifiers are dropped.
    """
    cleaned = re.sub(r"\([^)]*\)", " ", trigger)
    cleaned = re.sub(r"<[^>]*>", "<arg>", cleaned)
    phrases: list[str] = []
    for seg in cleaned.split(" / "):
        seg = seg.strip()
        if not seg:
            continue
        if phrases and " " not in seg and " " in phrases[-1]:
            base = phrases[-1].rsplit(" ", 1)[0]
            phrases.append(base + " " + seg)
        else:
            phrases.append(seg)
    return phrases


def trigger_to_regex(phrase: str) -> str:
    """Compile one trigger phrase into a regex source.

    ``<...>`` spans and the bare placeholder letters ``X`` / ``N`` become
    ``\\S+`` (one argument word; the pronoun ``I`` is NOT a placeholder);
    every other word matches literally with word boundaries at literal
    word-character ends.
    """
    protected = re.sub(r"<[^>]*>", _PH, phrase.strip())
    tokens = protected.split()
    if not tokens:
        return ""
    parts = []
    for tok in tokens:
        if tok == _PH or tok in ("X", "N"):
            parts.append(r"\S+")
        else:
            parts.append("".join(r"\S+" if ch == _PH else re.escape(ch) for ch in tok))
    pattern = r"\s+".join(parts)
    if re.match(r"\w", tokens[0][0]):
        pattern = r"\b" + pattern
    if tokens[-1] not in (_PH, "X", "N") and re.search(r"\w$", tokens[-1]):
        pattern = pattern + r"\b"
    return pattern


def _any_match(triggers: list[str], prompt: str) -> bool:
    for trig in triggers:
        for phrase in _phrases(trig):
            pat = trigger_to_regex(phrase)
            if not pat:
                continue
            try:
                if re.search(pat, prompt, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False


# Markers identifying a synthetic (harness-generated) turn rather than a human
# prompt. Such turns freely quote skill names, so matching them over-fires.
_SYNTHETIC_MARKERS = (
    "[SYSTEM NOTIFICATION",
    "<task-notification>",
    "<system-reminder>",
    "<command-name>",
    "<local-command-stdout>",
)


def is_synthetic_prompt(prompt: str) -> bool:
    return any(m in prompt for m in _SYNTHETIC_MARKERS)


def match_skills(prompt: str, skills: list[dict]) -> list[dict]:
    if not prompt or is_synthetic_prompt(prompt):
        return []
    return [s for s in skills if _any_match(s["triggers"], prompt)]


def state_path(session_id: str) -> Path:
    """Per-session marker recording which skills already loaded this session."""
    from superagent.tools.home import tmp_dir

    sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "nosession")[:128]
    return tmp_dir(ensure=False) / "skill-loader" / (sid + ".json")


def load_injected(session_id: str) -> set[str]:
    try:
        data = json.loads(state_path(session_id).read_text(encoding="utf-8"))
        inj = data.get("injected", [])
        return set(inj) if isinstance(inj, list) else set()
    except Exception:
        return set()


def save_injected(session_id: str, names: set[str]) -> None:
    try:
        p = state_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"injected": sorted(names)}), encoding="utf-8")
    except Exception:
        pass  # losing the marker only risks re-loading a body, never a broken turn


def _rel_path(skill: dict) -> str:
    try:
        return str(skill["path"].relative_to(REPO_ROOT))
    except Exception:
        return str(skill["path"])


def _skill_block(skill: dict) -> str:
    """Full body for short skills; compact pointer block for long ones."""
    name, rel = skill["name"], _rel_path(skill)
    header = (
        f"\n=== SUPERAGENT SKILL: {name} (auto-loaded by the skill_loader hook; "
        "your prompt matched a trigger) ===\n"
    )
    body = skill["body"]
    if body.count("\n") + 1 <= MAX_FULL_BODY_LINES:
        return header + f"Follow this procedure in full. Source: {rel}\n\n" + body + "\n"
    step_index = STEP_INDEX_RE.search(body)
    parts = [header]
    if skill["description"]:
        parts.append(skill["description"] + "\n")
    if step_index:
        parts.append("\n" + step_index.group(0) + "\n")
    parts.append(
        f"\nThis skill is long; per the read budget, Read {rel} "
        "(the relevant step ranges above) and follow it in full.\n"
    )
    return "".join(parts)


def render(matched: list[dict], already: set[str]) -> tuple[str, list[str]]:
    """(text, newly_loaded_names): blocks for first matches, one-liner for repeats."""
    fresh = [s for s in matched if s["name"] not in already]
    repeats = [s["name"] for s in matched if s["name"] in already]
    parts = [_skill_block(s) for s in fresh]
    if repeats:
        parts.append(
            "[Superagent skills] Already loaded this session (still in effect): "
            + ", ".join(repeats) + ".\n"
        )
    return "".join(parts), [s["name"] for s in fresh]


_NOT_A_CLI = (
    "superagent.tools.skill_loader is the prompt-submit skill-loader HOOK (it reads a "
    "JSON payload on stdin); it is not a command-line tool and takes no arguments. "
    "Do not call it by hand."
)


def main(argv: list[str] | None = None) -> int:
    # Guard exits use 1, not 2: for UserPromptSubmit hooks Claude Code treats
    # exit 2 as "block the prompt", the opposite of this hook's fail-safe goal.
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(_NOT_A_CLI, file=sys.stderr)
        return 1
    try:
        if sys.stdin is None or sys.stdin.isatty():
            print(_NOT_A_CLI, file=sys.stderr)
            return 1
    except Exception:
        pass
    try:
        if not autoload_enabled():
            return 0
        payload = read_payload(sys.stdin)
        prompt = payload.get("prompt") or payload.get("user_prompt") or ""
        if not isinstance(prompt, str):
            prompt = str(prompt)
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "nosession")
        matched = match_skills(prompt, discover_skills())
        if not matched:
            return 0
        already = load_injected(session_id)
        text, fresh = render(matched, already)
        if text:
            sys.stdout.write(text)
        if fresh:
            save_injected(session_id, already | set(fresh))
    except Exception:
        pass  # fail-safe: a context-loading hook must never break the user's turn
    return 0


if __name__ == "__main__":
    sys.exit(main())
