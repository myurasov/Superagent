# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for the shipped agent roles and `tools/agent_roles.py` (contracts/agents.md).

Pins the contract for every role file under `superagent/agents/`: valid
frontmatter, a dev twin beside it, skills that exist, a manifest in step with
the files, and no personal data or hardcoded id prefix anywhere in the agent
layer. Then the library itself: prefix rules, ids, frontmatter problems, custom
roles and overlays.

Inputs: the repo checkout, and tmp workspaces. No real workspace reads.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from superagent.tools import agent_roles as ar
from superagent.tools import build_agent_manifest as bam

FRAMEWORK_ROLES = {"superagent", "updater", "bookkeeper", "project"}

ROLE_BODY = """---
name: {name}
kind: durable
description: A custom role for testing.
skills: [todo]
owns:
  - _memory/todo.yaml
authority: act
model_tier: runner
duties:
  - id: tasks
    skill: todo
    tier: act
    schedule: on-request
    check: none
---

# {name}
"""
DEV_BODY = "---\ndevelops: {name}\nmodel_tier: dev\n---\n\n# {name} dev twin\n"


def _custom_role(workspace: Path, name: str, *, body: str = ROLE_BODY, dev: bool = True) -> Path:
    folder = workspace / "_custom" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.agent.md"
    path.write_text(body.format(name=name), encoding="utf-8")
    if dev:
        (folder / f"{name}.agent.dev.md").write_text(DEV_BODY.format(name=name), encoding="utf-8")
    return path


# ------------------------------------------------------------ shipped roles

def test_framework_roles_are_valid(framework_dir: Path) -> None:
    roles, errors = ar.collect(framework_dir)
    assert errors == []
    assert {r.name for r in roles} == FRAMEWORK_ROLES
    assert [r.name for r in roles if r.kind == "lead"] == ["superagent"]
    assert roles[0].kind == "lead", "the lead comes first"
    assert all(r.origin == "framework" for r in roles)


def test_every_role_file_has_a_dev_twin(framework_dir: Path) -> None:
    files = ar.role_files(framework_dir / "agents")
    assert {ar.role_stem(p) for p in files} == FRAMEWORK_ROLES
    for path in files:
        assert ar.check_dev_twin(path) == [], path.name
        dev = path.with_name(ar.role_stem(path) + ar.DEV_SUFFIX)
        body = dev.read_text(encoding="utf-8")
        for section in ("## Purpose and design notes", "## Wakes and checks", "## Cost targets",
                        "## How to test", "## How to deploy", "## How to observe", "## Rollback",
                        "## Pitfalls", "## Changelog"):
            assert section in body, f"{dev.name}: missing {section}"


def test_every_markdown_file_in_agents_is_a_role_or_a_dev_twin(framework_dir: Path) -> None:
    for path in (framework_dir / "agents").glob("*.md"):
        assert path.name.endswith((ar.ROLE_SUFFIX, ar.DEV_SUFFIX)), path.name


def test_referenced_skills_exist(framework_dir: Path) -> None:
    stems = ar.skill_stems(framework_dir, None)
    roles, _errors = ar.collect(framework_dir)
    for role in roles:
        if role.skills != ar.ALL_SKILLS:
            assert set(role.skills) <= stems, role.name
        for duty in role.duties:
            assert duty.skill in stems, (role.name, duty.id)
            assert role.runs(duty.skill), (role.name, duty.id)


def test_duty_schedules_are_cadence_keys_of_the_config_template(framework_dir: Path) -> None:
    keys = ar.cadence_keys(framework_dir, None)
    assert {"daily_update", "weekly_review", "monthly_review", "tailor_review", "doctor"} <= keys
    roles, _errors = ar.collect(framework_dir)
    for role in roles:
        for duty in role.duties:
            assert duty.schedule in keys or duty.schedule in ar.SCHEDULE_WORDS, (role.name, duty.id)


def test_cadence_skills_each_have_one_owner(framework_dir: Path) -> None:
    from superagent.tools.cadence import CYCLE_PERIOD_DAYS

    roles, _errors = ar.collect(framework_dir)
    for skill in (*CYCLE_PERIOD_DAYS, "supertailor-review", "doctor"):
        owners = [r.name for r in roles if any(d.skill == skill for d in r.duties)]
        assert owners == ["superagent"], skill
    assert ar.check_owner(roles, "watchers") == "updater"
    assert ar.duty_owner(roles, "whatsup") == "superagent", "an unclaimed skill goes to the lead"


def test_manifest_is_in_sync(framework_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bam.main(["--framework", str(framework_dir), "--check"]) == 0, capsys.readouterr().err


def test_manifest_rows_and_determinism(framework_dir: Path, tmp_path: Path) -> None:
    import yaml

    out = tmp_path / "_manifest.yaml"
    assert bam.main(["--framework", str(framework_dir), "--output", str(out)]) == 0
    first = out.read_text(encoding="utf-8")
    assert bam.main(["--framework", str(framework_dir), "--output", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == first, "no timestamp: same files, same bytes"
    data = yaml.safe_load(first)
    assert data["role_count"] == len(FRAMEWORK_ROLES)
    for row in data["roles"]:
        assert row["path"] == f"superagent/agents/{row['name']}.agent.md"
        assert row["dev_twin"] == f"superagent/agents/{row['name']}.agent.dev.md"
        assert not Path(row["path"]).is_absolute()
    assert bam.main(["--framework", str(framework_dir), "--output", str(out), "--check"]) == 0
    out.write_text(first + "# drift\n", encoding="utf-8")
    assert bam.main(["--framework", str(framework_dir), "--output", str(out), "--check"]) == 1


AGENT_LAYER = (
    "agents", "templates/agents", "skills/agents.md", "contracts/agents.md", "contracts/autonomy.md",
    "rules/framework-changes.md", "tools/agent_roles.py", "tools/agents.py",
    "tools/build_agent_manifest.py", "templates/memory/agents.yaml",
)
#: An id written out in full: `<something>:<framework role>`. Only a placeholder
#: prefix or the `agent:` handle kind may stand in front of a role name.
LITERAL_ID_RE = re.compile(r"([A-Za-z0-9<>_-]+):(superagent|updater|bookkeeper|project:[a-z<])")
ALLOWED_LEFT = {"<prefix>", "<id_prefix>", "agent"}
PERSONAL_PATTERNS = (
    ("home path", re.compile(r"/(Users|home)/[A-Za-z]")),
    ("email address", re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.[a-z]{2,}")),
    ("IP address", re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
)


def _agent_layer_files(framework_dir: Path) -> list[Path]:
    files: list[Path] = []
    for rel in AGENT_LAYER:
        path = framework_dir / rel
        files += sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]
    return files


def test_no_personal_data_in_the_agent_layer(framework_dir: Path) -> None:
    offenders: list[str] = []
    for path in _agent_layer_files(framework_dir):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for label, pattern in PERSONAL_PATTERNS:
                if pattern.search(line):
                    offenders.append(f"{path.name}:{number}: {label}")
    assert not offenders, offenders


def test_no_hardcoded_prefix_in_the_agent_layer(framework_dir: Path) -> None:
    offenders: list[str] = []
    for path in _agent_layer_files(framework_dir):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in LITERAL_ID_RE.finditer(line):
                if match.group(1) not in ALLOWED_LEFT:
                    offenders.append(f"{path.name}:{number}: {match.group(0)}")
    assert not offenders, f"ids must be written `<prefix>:<name>`: {offenders}"


def test_templates_carry_the_documented_shape(framework_dir: Path) -> None:
    role = (framework_dir / "templates" / "agents" / "agent.md").read_text(encoding="utf-8")
    assert role.startswith("---\n")
    for field in ar.REQUIRED_FIELDS:
        assert f"\n{field}:" in role, field
    dev = (framework_dir / "templates" / "agents" / "agent.dev.md").read_text(encoding="utf-8")
    assert "develops: <name>" in dev and "model_tier: dev" in dev


# ------------------------------------------------------------------- prefix

@pytest.mark.parametrize("value", ["sa", "fam", "x1-y", "a", "a234567890123456"])
def test_valid_prefixes(value: str) -> None:
    assert ar.validate_prefix(value) == value


@pytest.mark.parametrize("value", ["", "Sa", "1a", "a:b", "a_b", "a b", "a2345678901234567", None, 7,
                                   "owner", "autocode", "me", "project", "dev"])
def test_bad_and_reserved_prefixes_are_refused(value: object) -> None:
    with pytest.raises(ar.AgentError):
        ar.validate_prefix(value)


def _set_autocode(workspace: Path, block: str) -> None:
    config = workspace / "_memory" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    start = text.index("  autocode:\n")
    end = text.index("\n# --- Data sources", start)
    config.write_text(text[:start] + block + text[end:], encoding="utf-8")


def test_prefix_comes_from_config_with_a_default(initialized_workspace: Path, tmp_path: Path) -> None:
    ws = initialized_workspace
    assert ar.id_prefix(ws) == "sa", "the template default"
    assert ar.id_prefix(tmp_path / "nowhere") == "sa", "no config, no block: the default"
    _set_autocode(ws, '  autocode:\n    id_prefix: "fam"\n')
    assert ar.id_prefix(ws) == "fam"
    _set_autocode(ws, '  autocode:\n    id_prefix: "owner"\n')
    with pytest.raises(ar.AgentError, match="reserved"):
        ar.id_prefix(ws)


# ---------------------------------------------------------------------- ids

def test_ids(framework_dir: Path) -> None:
    roles, _errors = ar.collect(framework_dir)
    assert ar.resolve_id("updater", roles, "fam") == "fam:updater"
    assert ar.resolve_id("updater:dev", roles, "fam") == "fam:updater:dev"
    assert ar.resolve_id("updater", roles, "fam", dev=True) == "fam:updater:dev"
    assert ar.resolve_id("project:tax-2026", roles, "fam") == "fam:project:tax-2026"
    assert ar.resolve_id("project:dev", roles, "fam") == "fam:project:dev"
    assert ar.resolve_id("project", roles, "fam", dev=True) == "fam:project:dev"


@pytest.mark.parametrize("name,match", [
    ("project", "needs a slug"),
    ("updater:tax-2026", "takes no slug"),
    ("nobody", "no role named"),
    ("project:a:b", "not an agent name"),
    ("Updater", "not a role name"),
    ("project:Tax", "not a project slug"),
    ("", "not an agent name"),
])
def test_bad_id_requests(framework_dir: Path, name: str, match: str) -> None:
    roles, _errors = ar.collect(framework_dir)
    with pytest.raises(ar.AgentError, match=match):
        ar.resolve_id(name, roles, "fam")


def test_one_dev_twin_serves_every_project_agent(framework_dir: Path) -> None:
    roles, _errors = ar.collect(framework_dir)
    project = next(r for r in roles if r.name == "project")
    with pytest.raises(ar.AgentError, match="one dev twin"):
        ar.instance_name(project, slug="tax-2026", dev=True)
    with pytest.raises(ar.AgentError, match="reserved"):
        ar.instance_name(project, slug="dev")


# -------------------------------------------------------------- frontmatter

def _problems(tmp_path: Path, name: str, body: str) -> list[str]:
    path = tmp_path / f"{name}.agent.md"
    path.write_text(body, encoding="utf-8")
    return ar.check_frontmatter(ar.read_frontmatter(path), path, skills={"todo", "watch"},
                                schedules={"weekly_review"})


def test_frontmatter_problems_are_named(tmp_path: Path) -> None:
    assert _problems(tmp_path, "helper", ROLE_BODY.format(name="helper")) == []
    assert _problems(tmp_path, "plain", "# no frontmatter\n") == [
        "plain.agent.md: missing or unparseable YAML frontmatter (`---` block)"]
    body = ROLE_BODY.format(name="helper")
    cases = {
        "name: helper": ("name: other", "does not match the filename"),
        "kind: durable": ("kind: boss", "`kind` 'boss'"),
        "skills: [todo]": ("skills: [todo, nope]", "skill `nope` does not exist"),
        "authority: act": ("authority: prepare", "looser than the role's `authority`"),
        "model_tier: runner": ("model_tier: big", "`model_tier` 'big'"),
        "    skill: todo": ("    skill: watch", "is not in the role's `skills`"),
        "    schedule: on-request": ("    schedule: sometimes", "`schedule` 'sometimes'"),
        "    check: none": ("    check: script.py", "`check` 'script.py'"),
        "  - _memory/todo.yaml": ("  - ../outside.yaml", "`owns` entry"),
        "description: A custom role for testing.": ("extra: 1", "missing `description`"),
    }
    for old, (new, expected) in cases.items():
        problems = _problems(tmp_path, "helper", body.replace(old, new))
        assert any(expected in line for line in problems), (new, problems)
    assert any("unknown field(s) ['extra']" in line
               for line in _problems(tmp_path, "helper", body.replace("kind: durable", "kind: durable\nextra: 1")))
    assert any("`skills: all` is for the lead" in line
               for line in _problems(tmp_path, "helper", body.replace("skills: [todo]", "skills: all")))
    twice = body + ""
    twice = twice.replace("    check: none\n", "    check: none\n  - id: tasks\n    skill: todo\n"
                          "    tier: act\n    schedule: weekly_review\n    check: cadence\n")
    assert any("used twice" in line for line in _problems(tmp_path, "helper", twice))
    assert any("reserved" in line for line in _problems(tmp_path, "dev", ROLE_BODY.format(name="dev")))


# ------------------------------------------------- custom roles and overlays

def test_custom_role_is_collected_with_its_dev_twin(framework_dir: Path,
                                                    initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _custom_role(ws, "gardener")
    roles, errors = ar.collect(framework_dir, ws)
    assert errors == []
    custom = next(r for r in roles if r.name == "gardener")
    assert custom.origin == "custom" and custom.dev_path is not None
    assert roles[0].name == "superagent" and roles[-1].name == "gardener", "framework roles first"
    assert ar.resolve_id("gardener", roles, "fam", dev=True) == "fam:gardener:dev"


def test_custom_role_without_a_dev_twin_is_an_error(framework_dir: Path,
                                                    initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _custom_role(ws, "gardener", dev=False)
    roles, errors = ar.collect(framework_dir, ws)
    assert "gardener" not in {r.name for r in roles}
    assert errors == ["gardener.agent.md: no dev twin `gardener.agent.dev.md` beside it"]


def test_same_named_custom_file_is_an_overlay_not_a_role(framework_dir: Path,
                                                         initialized_workspace: Path) -> None:
    ws = initialized_workspace
    overlay = _custom_role(ws, "updater", body="# extra boundaries for {name}\n", dev=False)
    roles, errors = ar.collect(framework_dir, ws)
    assert errors == [], "an overlay needs no frontmatter and no dev twin"
    assert [r.name for r in roles].count("updater") == 1
    assert next(r for r in roles if r.name == "updater").overlay == overlay


def test_sync_leftovers_are_not_roles(framework_dir: Path, initialized_workspace: Path) -> None:
    ws = initialized_workspace
    _custom_role(ws, "gardener")
    folder = ws / "_custom" / "agents"
    (folder / "gardener.sync-conflict-20261008-101010-ABCDEFG.agent.md").write_text("junk")
    (folder / ".syncthing.helper.agent.md.tmp").write_text("junk")
    roles, errors = ar.collect(framework_dir, ws)
    assert errors == [] and [r.name for r in roles if r.origin == "custom"] == ["gardener"]
