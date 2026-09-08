"""Trigger-hygiene regressions for the real skill frontmatter (0.18.0).

Pins both directions for the skills whose ``triggers`` were tightened or
widened in the 0.18.0 hygiene pass: bare single-noun triggers (``tags``,
``audit``, ``timeline``, ``sources``) and the placeholder-only
``log a <maintenance task>`` must no longer fire on unrelated prompts, while
the legitimate phrasings they used to cover keep loading the right skill.
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


# --- tags: bare "tags" must not route git-release prompts -------------------

@pytest.mark.parametrize(
    "prompt",
    [
        "publish all tags",
        "push the tags",
        "push the git tags",
        "release tags",
        "git tag v1.0 and push",
        "please audit the git tags before release",
    ],
)
def test_tags_ignores_git_flavored_prompts(skills: list[dict], prompt: str) -> None:
    assert "superagent-tags" not in _fired(prompt, skills)


@pytest.mark.parametrize(
    "prompt",
    [
        "list tags",
        "show all tags",
        "show me the tags on my bills",
        "what tags exist",
        "tag this bill as urgent",
        "tag this as tax-deductible",
        "rename tag foo to bar",
        "merge tags a and b",
    ],
)
def test_tags_still_fires_on_taxonomy_prompts(skills: list[dict], prompt: str) -> None:
    assert "superagent-tags" in _fired(prompt, skills)


# --- home-maintenance vs. the other "log a ..." skills ----------------------

@pytest.mark.parametrize(
    "prompt",
    ["log a vet visit", "log a symptom", "log a car service", "log a doctor visit"],
)
def test_home_maintenance_does_not_hijack_log_a_prompts(
    skills: list[dict], prompt: str
) -> None:
    assert "superagent-home-maintenance" not in _fired(prompt, skills)


@pytest.mark.parametrize(
    "prompt",
    ["log a filter change", "log a home repair", "log HVAC service", "log gutter cleaning"],
)
def test_home_maintenance_fires_on_house_prompts(skills: list[dict], prompt: str) -> None:
    assert "superagent-home-maintenance" in _fired(prompt, skills)


def test_home_repair_does_not_fire_vehicle_log(skills: list[dict]) -> None:
    assert "superagent-vehicle-log" not in _fired("log a home repair", skills)


# --- vehicle-log: an intervening vehicle word ------------------------------

@pytest.mark.parametrize(
    "prompt", ["log a car service", "log a car repair", "log a truck oil change"]
)
def test_vehicle_log_accepts_intervening_vehicle_word(
    skills: list[dict], prompt: str
) -> None:
    assert "superagent-vehicle-log" in _fired(prompt, skills)


# --- audit / events / sources: bare nouns ------------------------------------

def test_bare_nouns_do_not_fire(skills: list[dict]) -> None:
    assert "superagent-audit" not in _fired("please audit the git tags before release", skills)
    assert "superagent-audit" not in _fired("audit the subscriptions", skills)
    assert "superagent-events" not in _fired("timeline", skills)
    assert "superagent-sources" not in _fired("which sources cite this paper", skills)
    assert "superagent-sources" not in _fired("sources", skills)


def test_phrase_forms_still_fire(skills: list[dict]) -> None:
    assert "superagent-audit" in _fired("show the audit trail for that bill", skills)
    assert "superagent-audit" in _fired("audit history of the rent bill", skills)
    assert "superagent-events" in _fired("show me the timeline for the solar project", skills)
    assert "superagent-events" in _fired("show timeline", skills)
    assert "superagent-sources" in _fired("what sources do we have on solar", skills)
    assert "superagent-sources" in _fired("list sources", skills)
    assert "superagent-sources" in _fired("my sources", skills)


# --- baton: observed handoff phrasings ---------------------------------------

@pytest.mark.parametrize(
    "prompt",
    [
        "hand this off to the next session",
        "give me plain text prompt to kick start a session with new agent",
        "give me a prompt to continue work on taxes in a new session",
        "rather give me prompt to start new session that only has relevant info",
        "this should be a pretty compact prompt to another superagent session",
        "gimme prompt for another more powerful agent to re-do this",
    ],
)
def test_baton_fires_on_observed_handoff_phrasings(skills: list[dict], prompt: str) -> None:
    assert "superagent-baton" in _fired(prompt, skills)


@pytest.mark.parametrize(
    "prompt", ["what bills are due", "can you continue with the taxes", "prompt me tomorrow"]
)
def test_baton_does_not_over_fire(skills: list[dict], prompt: str) -> None:
    assert "superagent-baton" not in _fired(prompt, skills)


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
