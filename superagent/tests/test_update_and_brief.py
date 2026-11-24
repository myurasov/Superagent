# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Fetch and tell are separate skills (contracts/update-loop.md).

`update` fetches, captures and reconciles and renders no briefing. `brief`
reads the stores and fetches nothing. The four cadence skills are thin entry
points: `update`, then `brief`, and no step of their own. These tests keep the
three apart, so a fetch step cannot drift back into a briefing or into an
entry point.

Inputs: `superagent/skills/*.md` in the repo checkout. No workspace reads.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from superagent.tools import skill_loader

ENTRY_POINTS = {
    "daily-update": ("auto", "daily"),
    "whatsup": ("light", "daily"),
    "weekly-review": ("auto", "weekly"),
    "monthly-review": ("auto", "monthly"),
}
#: Anything that reaches outside the local stores, or that belongs to the fetch side.
FETCH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a watcher check", re.compile(r"watchlist\s+check")),
    ("a harvest", re.compile(r"watchlist\s+harvest|harvest\s+--id")),
    ("a watcher stamp", re.compile(r"watchlist\s+stamp")),
    ("an MCP call", re.compile(r"\bmcp_[a-z]")),
    ("a mailbox read", re.compile(r"\b(read_email|search_emails|send_email)\b")),
    ("the transaction reconcile tool", re.compile(r"reconcile_transactions")),
    ("the cycle lock", re.compile(r"tools\.lock\b")),
    ("a drop-folder drain", re.compile(r"tools\.inbox\b")),
    ("the conflict sweep", re.compile(r"tools\.heal\b")),
)
#: The fetch commands a briefing must never run (it may run read-only analysis over stores).
BRIEF_FORBIDDEN = tuple(p for p in FETCH_PATTERNS if p[0] not in (
    "the transaction reconcile tool",))


def _body(framework_dir: Path, stem: str) -> str:
    text = (framework_dir / "skills" / f"{stem}.md").read_text(encoding="utf-8")
    return text.split("---", 2)[2]


def _lines(body: str) -> list[tuple[int, str]]:
    return list(enumerate(body.splitlines(), start=1))


@pytest.mark.parametrize("stem", sorted(ENTRY_POINTS))
def test_entry_points_hold_no_fetch_step(framework_dir: Path, stem: str) -> None:
    body = _body(framework_dir, stem)
    offenders = [f"{stem}.md:{n}: {label}: {line.strip()[:90]}"
                 for n, line in _lines(body) for label, pattern in FETCH_PATTERNS if pattern.search(line)]
    assert not offenders, "an entry point runs `update` then `brief` and nothing else:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("stem", sorted(ENTRY_POINTS))
def test_entry_points_are_thin_and_run_update_then_brief(framework_dir: Path, stem: str) -> None:
    body = _body(framework_dir, stem)
    tier, kind = ENTRY_POINTS[stem]
    assert "```" not in body and "uv run" not in body, f"{stem}.md carries a command of its own"
    headings = re.findall(r"^## (.+)$", body, re.MULTILINE)
    assert headings == ["1. Update", "2. Brief"], f"{stem}.md has steps of its own: {headings}"
    assert len(body.splitlines()) <= 20, f"{stem}.md is no longer thin"
    update_at, brief_at = body.index("skills/update.md"), body.index("skills/brief.md")
    assert update_at < brief_at, "update comes first, then brief"
    assert f"tier **`{tier}`**" in body and f"kind **`{kind}`**" in body
    assert "contracts/update-loop.md" in body


def test_whatsup_asks_for_the_short_form(framework_dir: Path) -> None:
    assert "**short form**" in _body(framework_dir, "whatsup")
    assert "short form" not in _body(framework_dir, "daily-update").replace("full form", "")


def test_the_brief_fetches_nothing(framework_dir: Path) -> None:
    body = _body(framework_dir, "brief")
    offenders = [f"brief.md:{n}: {label}: {line.strip()[:90]}"
                 for n, line in _lines(body) for label, pattern in BRIEF_FORBIDDEN
                 if pattern.search(line) and "watchlist harvest --id <id>`, per the `watch` skill" not in line]
    assert not offenders, "the briefing reads stores only:\n  " + "\n  ".join(offenders)
    assert "superagent.tools.watchlist recap --since" in body, "it reads back what the update found"
    assert "superagent.tools.asks list --open" in body, "and what waits on the owner"
    for kind in ("daily-update", "weekly-review", "monthly-review", "whatsup"):
        assert f"cadence finish {kind}" in body or f"|{kind}" in body, f"the {kind} duty is stamped by the brief"


def test_the_one_harvest_the_brief_names_is_an_answered_ask(framework_dir: Path) -> None:
    """The brief may carry out a harvest only as the owner's answer to an ask, in the follow-ups."""
    body = _body(framework_dir, "brief")
    hits = [line for _n, line in _lines(body) if re.search(r"watchlist\s+harvest", line)]
    assert len(hits) == 1 and "owner said yes" in hits[0]
    followups = body.split("## 11. Follow-ups with the owner", 1)[1].split("\n## ", 1)[0]
    assert hits[0] in followups


def test_the_update_renders_no_briefing_and_asks_nothing(framework_dir: Path) -> None:
    body = _body(framework_dir, "update")
    for marker in ("Good morning", "## Today", "## This week", "Waiting on your call\n<"):
        assert marker not in body, f"update.md carries a piece of the briefing: {marker!r}"
    assert "asks no question in chat" in body
    assert "superagent.tools.asks add --agent updater" in body, "what needs the owner's word is queued"
    assert body.index("agents may-fetch") < body.index("tools.lock hold"), "one machine fetches: asked first"
    for command in ("tools.lock hold", "tools.lock release --token", "watchlist check --due",
                    "tools.inbox list", "tools.heal sweep", "cadence start update-",
                    "cadence finish update-", "signals log --skill update"):
        assert command in body, f"update.md lost `{command}`"
    assert body.index("tools.lock hold") < body.index("watchlist check --due") < body.index(
        "tools.lock release --token"), "the cycle runs inside the lock"
    full = body.split("## 9. Full tier", 1)[1].split("\n## ", 1)[0]
    for tool in ("project_drift", "events_derive rebuild", "world rebuild", "Reconcile"):
        assert tool in full, f"the full tier lost `{tool}`"
    light = body.split("## 9. Full tier", 1)[0]
    assert "project_drift" not in light and "events_derive" not in light


@pytest.mark.parametrize("prompt, skill", [
    ("daily update", "superagent-daily-update"),
    ("update now", "superagent-daily-update"),
    ("what's up", "superagent-whatsup"),
    ("weekly review", "superagent-weekly-review"),
    ("monthly review", "superagent-monthly-review"),
    ("run the update", "superagent-update"),
    ("update only, no briefing", "superagent-update"),
    ("do a full update", "superagent-update"),
    ("brief me", "superagent-brief"),
    ("weekly brief please", "superagent-brief"),
])
def test_triggers_route_to_the_right_skill(prompt: str, skill: str) -> None:
    fired = {s["name"] for s in skill_loader.match_skills(prompt, skill_loader.discover_skills())}
    assert skill in fired, f"{prompt!r} fired {sorted(fired)}"


@pytest.mark.parametrize("prompt", ["update the bill amount", "a brief note for the plumber",
                                    "update my phone number", "in brief, what happened"])
def test_new_triggers_do_not_over_fire(prompt: str) -> None:
    fired = {s["name"] for s in skill_loader.match_skills(prompt, skill_loader.discover_skills())}
    assert not {"superagent-update", "superagent-brief"} & fired, f"{prompt!r} fired {sorted(fired)}"


def test_hand_offs_go_by_interests_and_not_through_the_lead(framework_dir: Path) -> None:
    """After a cycle the Updater tells the agents that asked; the lead gets no run report."""
    body = (framework_dir / "skills" / "update.md").read_text(encoding="utf-8")
    assert "superagent.tools.agents interested --when <kind> --handle" in body
    assert "One message per agent per cycle" in body and "The lead gets no run report" in body
    assert "sends the lead one message" not in body
    rule = (framework_dir / "rules" / "autocode.md").read_text(encoding="utf-8")
    assert "The lead is no messenger" in rule and "Say what you want to hear about" in rule
    assert "tell the lead what changed" not in rule
    for role in ("project", "bookkeeper"):
        text = (framework_dir / "agents" / f"{role}.agent.md").read_text(encoding="utf-8")
        assert "Tell the lead in one message" not in text and "tells the lead in one message" not in text
