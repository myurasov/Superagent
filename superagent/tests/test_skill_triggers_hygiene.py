"""Trigger-hygiene regressions for the real skill frontmatter (0.18.0).

Pins both directions for the skills whose ``triggers`` were tightened or
widened in the 0.18.0 hygiene pass: the bare single-noun trigger
``sources`` must not fire on unrelated prompts, while the legitimate
phrasings it used to cover keep loading the right skill. (The ``tags``,
``audit``, ``events``, ``home-maintenance``, ``vehicle-log`` and ``baton``
blocks that lived here were retired with those skills in 0.21.0.)
Runs against ``superagent/skills/*.md`` through the same ``discover_skills``
/ ``match_skills`` path the UserPromptSubmit hook uses.
"""

from __future__ import annotations

import pytest

from superagent.tools import skill_loader


@pytest.fixture(scope="module")
def skills() -> list[dict]:
    return skill_loader.discover_skills()


def _fired(prompt: str, skills: list[dict]) -> set[str]:
    return {s["name"] for s in skill_loader.match_skills(prompt, skills)}


# --- sources: bare noun ------------------------------------------------------

def test_bare_nouns_do_not_fire(skills: list[dict]) -> None:
    assert "superagent-sources" not in _fired("which sources cite this paper", skills)
    assert "superagent-sources" not in _fired("sources", skills)


def test_phrase_forms_still_fire(skills: list[dict]) -> None:
    assert "superagent-sources" in _fired("what sources do we have on solar", skills)
    assert "superagent-sources" in _fired("list sources", skills)
    assert "superagent-sources" in _fired("my sources", skills)


# --- log-event / todo: phrasings the retired log skills used to own (0.21.0) --

@pytest.mark.parametrize(
    "prompt", ["log a car service", "log a home repair", "log this oil change"]
)
def test_vehicle_and_home_events_route_to_log_event(skills: list[dict], prompt: str) -> None:
    fired = _fired(prompt, skills)
    assert "superagent-log-event" in fired
    assert "superagent-vehicle-log" not in fired
    assert "superagent-home-maintenance" not in fired


@pytest.mark.parametrize("prompt", ["triage overdue", "clean up overdue tasks"])
def test_overdue_triage_routes_to_todo(skills: list[dict], prompt: str) -> None:
    fired = _fired(prompt, skills)
    assert "superagent-todo" in fired
    assert "superagent-triage-overdue" not in fired


def test_retired_skills_are_gone(skills: list[dict]) -> None:
    names = {s["name"] for s in skills}
    for retired in ("handoff", "baton", "workbooks", "inbox-triage", "email-refresh",
                    "triage-overdue", "audit", "events", "tags", "follow-up",
                    "home-maintenance", "vehicle-log"):
        assert f"superagent-{retired}" not in names


# --- watch: inherits every trigger the retired ingest skill owned (0.19.0) ---

@pytest.mark.parametrize(
    "prompt",
    [
        # phrases the retired `ingest` skill used to own
        "ingest",
        "run ingest",
        "import data",
        "sync from simplefin",
        "set up data sources",
        "refresh my data",
        "pull new data",
        "refresh email",
        "refresh transactions",
        # the watchlist's own vocabulary
        "add an ext-source for the permit portal",
        "list my ext-sources",
    ],
)
def test_watch_inherits_ingest_triggers(skills: list[dict], prompt: str) -> None:
    fired = _fired(prompt, skills)
    assert "superagent-watch" in fired
    assert "superagent-ingest" not in fired


def test_ingest_skill_is_retired(skills: list[dict]) -> None:
    assert "superagent-ingest" not in {s["name"] for s in skills}


# --- add-* fold (0.21.0): Add modes + the `add` kind switch ------------------

RETIRED_ADD_SKILLS = {
    "superagent-add-bill",
    "superagent-add-subscription",
    "superagent-add-appointment",
    "superagent-add-important-date",
    "superagent-add-account",
    "superagent-add-asset",
    "superagent-add-document",
}

FOLD_TARGETS = {
    "superagent-add",
    "superagent-bills",
    "superagent-subscriptions",
    "superagent-appointments",
    "superagent-important-dates",
}


def test_retired_add_skills_are_gone(skills: list[dict]) -> None:
    assert not RETIRED_ADD_SKILLS & {s["name"] for s in skills}


@pytest.mark.parametrize(
    ("prompt", "skill"),
    [
        ("add a bill", "superagent-bills"),
        ("I have a new insurance bill", "superagent-bills"),
        ("add a subscription", "superagent-subscriptions"),
        ("just signed up for Spotify Family", "superagent-subscriptions"),
        ("add an appointment", "superagent-appointments"),
        ("book an appointment", "superagent-appointments"),
        ("schedule a dentist visit", "superagent-appointments"),
        ("add a birthday", "superagent-important-dates"),
        ("add an anniversary", "superagent-important-dates"),
        ("add an important date", "superagent-important-dates"),
        ("add an account", "superagent-add"),
        ("register a brokerage account", "superagent-add"),
        ("add an asset", "superagent-add"),
        ("new asset", "superagent-add"),
        ("I bought a new car", "superagent-add"),
        ("add a document", "superagent-add"),
        ("track this passport", "superagent-add"),
    ],
)
def test_fold_targets_inherit_add_triggers(
    skills: list[dict], prompt: str, skill: str
) -> None:
    assert skill in _fired(prompt, skills)


@pytest.mark.parametrize(
    "prompt",
    [
        "book a flight to Denver",
        "I started the migration",
        "register the domain name",
        "track this issue in the todo",
        "I will pay the rent next month",
        "my insurance card came in the mail",
    ],
)
def test_folded_add_triggers_do_not_over_fire(skills: list[dict], prompt: str) -> None:
    assert not FOLD_TARGETS & _fired(prompt, skills)
