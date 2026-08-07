# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/skill_loader.py` (prompt-submit skill auto-loader hook)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def local_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sa-home"
    monkeypatch.setenv("SUPERAGENT_HOME", str(home))
    return home


def _skill(name: str, triggers: list[str], body: str) -> dict:
    return {
        "name": name,
        "triggers": triggers,
        "description": f"{name} description.",
        "body": body,
        "path": Path(f"/repo/superagent/skills/{name}.md"),
    }


# --- trigger compilation -------------------------------------------------

def test_phrases_split_and_parenthetical_strip() -> None:
    from superagent.tools.skill_loader import _phrases

    assert _phrases("new task / start a task / ad-hoc task") == [
        "new task", "start a task", "ad-hoc task",
    ]
    assert _phrases("investigate X (when it has no Domain home)") == ["investigate X"]


def test_phrases_single_word_alternates_substitute_last_word() -> None:
    from superagent.tools.skill_loader import _phrases

    # single-word segments substitute the previous phrase's last word,
    # never becoming bare standalone words like "will" or "month"
    assert _phrases("track this document / passport / will") == [
        "track this document", "track this passport", "track this will",
    ]
    assert _phrases("dates this week / month") == [
        "dates this week", "dates this month",
    ]
    # a leading single word stays standalone
    assert _phrases("bills / expenses") == ["bills", "expenses"]


def test_phrases_protect_placeholder_spans_before_split() -> None:
    from superagent.tools.skill_loader import _phrases

    # the " / " inside <...> must not shred the phrase
    assert _phrases("set up <host / tool / thing>") == ["set up <arg>"]


def test_pronoun_i_is_not_a_placeholder() -> None:
    from superagent.tools.skill_loader import trigger_to_regex

    pat = trigger_to_regex("I started")
    assert not re.search(pat, "the server started", re.IGNORECASE)
    assert re.search(pat, "I started a new job", re.IGNORECASE)


def test_everyday_prompts_do_not_over_fire(framework_dir: Path) -> None:
    from superagent.tools import skill_loader

    skills = skill_loader.discover_skills()

    def fired(prompt: str) -> set:
        return {s["name"] for s in skill_loader.match_skills(prompt, skills)}

    # QA regression set (2026-08-07 review): these previously injected
    # unrelated skills via bare-word alternates and the "I" placeholder.
    assert "superagent-add-document" not in fired("I will pay the rent next month")
    assert "superagent-important-dates" not in fired("I will pay the rent next month")
    assert "superagent-world" not in fired("what's happening in the world right now")
    assert "superagent-add-account" not in fired("my insurance card came in the mail")
    assert "superagent-add-bill" not in fired("my insurance card came in the mail")
    # sane prompts still fire the right skill
    assert "superagent-refresh" in fired("update superagent please")
    assert "superagent-daily-update" in fired("give me my daily update")


def test_trigger_regex_placeholders() -> None:
    from superagent.tools.skill_loader import trigger_to_regex

    pat = trigger_to_regex("work on tasks/<slug>")
    assert re.search(pat, "let's work on tasks/2026-08-07-foo please", re.IGNORECASE)
    pat = trigger_to_regex("investigate X")
    assert re.search(pat, "investigate propane", re.IGNORECASE)
    # literal word boundaries: "status" must not match "statuses"
    pat = trigger_to_regex("status")
    assert not re.search(pat, "statuses", re.IGNORECASE)


def test_match_skills_and_synthetic_skip() -> None:
    from superagent.tools.skill_loader import match_skills

    skills = [
        _skill("superagent-daily-update", ["daily update", "morning briefing"], "b"),
        _skill("superagent-bills", ["add a bill", "bills due"], "b"),
    ]
    matched = match_skills("give me my daily update", skills)
    assert [s["name"] for s in matched] == ["superagent-daily-update"]
    assert match_skills("what bills due this week", skills)
    assert match_skills("", skills) == []
    assert match_skills(
        "<task-notification>agent said daily update done</task-notification>", skills
    ) == []


# --- parsing real skill files --------------------------------------------

def test_discover_real_skills_and_match(framework_dir: Path) -> None:
    from superagent.tools import skill_loader

    skills = skill_loader.discover_skills()
    names = {s["name"] for s in skills}
    assert "superagent-daily-update" in names
    assert "superagent-refresh" in names
    matched = skill_loader.match_skills("update superagent please", skills)
    assert "superagent-refresh" in {s["name"] for s in matched}


def test_parse_skill_rejects_frontmatterless(tmp_path: Path) -> None:
    from superagent.tools.skill_loader import parse_skill

    p = tmp_path / "x.md"
    p.write_text("# just a doc\n")
    assert parse_skill(p) is None


# --- rendering ------------------------------------------------------------

def test_render_full_body_for_short_skill() -> None:
    from superagent.tools.skill_loader import render

    skill = _skill("superagent-tiny", ["tiny"], "## 1. Do the thing\nDo it.")
    text, fresh = render([skill], set())
    assert "SUPERAGENT SKILL: superagent-tiny" in text
    assert "Do the thing" in text
    assert fresh == ["superagent-tiny"]


def test_render_compact_block_for_long_skill() -> None:
    from superagent.tools.skill_loader import MAX_FULL_BODY_LINES, render

    body = (
        "<!-- step-index:start -->\n| # | Step |\n| 1 | Alpha |\n"
        "<!-- step-index:end -->\n" + "filler line\n" * (MAX_FULL_BODY_LINES + 10)
    )
    skill = _skill("superagent-long", ["long"], body)
    text, _ = render([skill], set())
    assert "step-index:start" in text
    assert "Read" in text
    assert text.count("filler line") == 0  # body not injected whole


def test_render_repeat_is_one_liner() -> None:
    from superagent.tools.skill_loader import render

    skill = _skill("superagent-tiny", ["tiny"], "body")
    text, fresh = render([skill], {"superagent-tiny"})
    assert fresh == []
    assert "Already loaded this session" in text


# --- session state + config gate ------------------------------------------

def test_state_roundtrip(local_home: Path) -> None:
    from superagent.tools.skill_loader import load_injected, save_injected

    assert load_injected("sess-1") == set()
    save_injected("sess-1", {"a", "b"})
    assert load_injected("sess-1") == {"a", "b"}
    assert load_injected("sess-2") == set()


def test_autoload_disabled_via_config(tmp_path: Path) -> None:
    from superagent.tools.skill_loader import autoload_enabled

    cfg = tmp_path / "config.yaml"
    assert autoload_enabled(cfg) is True  # missing file -> enabled
    cfg.write_text("preferences:\n  skill_autoload: false\n")
    assert autoload_enabled(cfg) is False
    cfg.write_text("preferences: {}\n")
    assert autoload_enabled(cfg) is True


def test_main_refuses_cli_invocation(capsys: pytest.CaptureFixture) -> None:
    from superagent.tools.skill_loader import main

    # exit 1, never 2: Claude Code treats exit 2 from UserPromptSubmit as
    # "block the prompt", the opposite of this hook's fail-safe goal
    assert main(["--oops"]) == 1
    assert "not a command-line tool" in capsys.readouterr().err
