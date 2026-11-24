# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Agent roles: the frontmatter of `agents/<name>.agent.md`, ids and the id prefix.

Implements contracts/agents.md. This module is the library every other agent
tool builds on (`tools/agents.py`, `tools/build_agent_manifest.py`,
`tools/cadence.py`); it imports none of them.

A ROLE is a markdown file with YAML frontmatter:

  superagent/agents/<name>.agent.md            framework role (committed)
  workspace/_custom/agents/<name>.agent.md     custom role, or, when a framework
                                               role has the same name, an overlay
                                               on that role (extra boundaries)

Beside every role file sits its dev twin `<name>.agent.dev.md`: the notes of
the agent that develops and maintains the role.

An ID is `<prefix>:<instance name>`. The prefix comes from
`config.preferences.autocode.id_prefix` (default `sa`), so several users can
share one Autocode server. The instance name is the role name for a lead or
durable role, `project:<slug>` for a project agent, and `<role>:dev` for a dev
twin. Nothing in the framework hardcodes a prefix.

Inputs: the role files, and (for the prefix) `<workspace>/_memory/config.yaml`.
Outputs: none; this module only reads.
"""
from __future__ import annotations

import dataclasses as dc
import re
from pathlib import Path
from typing import Any

import yaml

from superagent.tools import handles
from superagent.tools.syncnames import is_sync_artifact

FRAMEWORK = Path(__file__).resolve().parent.parent

ROLE_SUFFIX = ".agent.md"
DEV_SUFFIX = ".agent.dev.md"

KINDS = ("lead", "durable", "project")
#: Autonomy tiers (contracts/autonomy.md), loosest first. A stricter tier
#: means more of the owner's hand in the action.
TIERS = ("act", "approve", "prepare", "owner")
MODEL_TIERS = ("runner", "dev")
#: Built-in zero-cost checks a duty may name (contracts/agents.md "Duties").
CHECKS = ("cadence", "update", "watchers", "none")
#: Checks that own the "watchers overdue for a check" finding, first match wins.
WATCHER_CHECKS = ("update", "watchers")
#: A custom role's own check: `tool:<name>` runs `workspace/_custom/tools/<name>.py`.
TOOL_CHECK_RE = re.compile(r"^tool:([a-z][a-z0-9_]{0,40})$")
CUSTOM_TOOLS_REL = "_custom/tools"
#: Schedule values that are not a key of `preferences.cadences`.
SCHEDULE_WORDS = ("watchlist", "on-request")
#: `skills:` value that means "any skill"; a lead role only.
ALL_SKILLS = "all"

DEFAULT_PREFIX = "sa"
PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
#: Words a prefix may not be: Autocode's own names, and the two words that are
#: segments of this id scheme.
RESERVED_PREFIXES = frozenset({"owner", "autocode", "me", "project", "dev"})

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
#: Names a role may not take (they are segments or Autocode's own names).
RESERVED_NAMES = frozenset({"dev", "owner", "autocode", "me"})
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
DUTY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
OWNS_RE = re.compile(r"^(_memory|Domains|Projects|Sources|Outbox|Inbox|Archive)/[A-Za-z0-9_<>./-]*$")

REQUIRED_FIELDS = ("name", "kind", "description", "skills", "owns", "duties", "authority", "model_tier")
#: Fields a role file may leave out.
OPTIONAL_FIELDS = ("interests",)
DUTY_FIELDS = ("id", "skill", "tier", "schedule", "check")
#: An interest says which updates an agent wants to hear about: a handle or a
#: handle glob (`project:kitchen-reno`, `domain:finances`, `bill:*`), optionally only
#: for one kind of update, and optionally delivered by a wake that is fired.
INTEREST_FIELDS = ("pattern", "when", "why", "fire")
#: What an update cycle can move: a watcher saw a change, a harvest brought new
#: rows, a document was filed, a fact was captured into a store.
INTEREST_WHEN = ("changed", "harvest", "filed", "captured")
#: In a project role's file: stands for the project of each instance.
PROJECT_PLACEHOLDER = "project:<slug>"
MAX_INTERESTS = 12
INTEREST_RE = re.compile(r"^([a-z_]+):([A-Za-z0-9*][A-Za-z0-9_.*/-]{0,79})$")
WAKE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class AgentError(ValueError):
    """A bad prefix, name or id request. The message is written for the user."""


@dc.dataclass(frozen=True)
class Duty:
    """One duty of a role: a skill run on a schedule, behind a zero-cost check."""

    id: str
    skill: str
    tier: str
    schedule: str
    check: str


@dc.dataclass(frozen=True)
class Interest:
    """One kind of update a role's agents want to hear about."""

    pattern: str
    when: str | None
    why: str
    fire: str | None


@dc.dataclass(frozen=True)
class Role:
    """A parsed role file."""

    name: str
    kind: str
    description: str
    skills: tuple[str, ...] | str
    owns: tuple[str, ...]
    authority: str
    model_tier: str
    duties: tuple[Duty, ...]
    path: Path
    dev_path: Path | None
    origin: str  # "framework" | "custom"
    overlay: Path | None = None  # a same-named file in _custom/agents/ on a framework role
    interests: tuple[Interest, ...] = ()  # what its agents ask the update cycle to tell them

    def duty(self, duty_id: str) -> Duty | None:
        """The duty with this id, or None."""
        return next((d for d in self.duties if d.id == duty_id), None)

    def runs(self, skill: str) -> bool:
        """True when this role lists `skill` (a lead with `skills: all` runs every skill)."""
        return self.skills == ALL_SKILLS or skill in self.skills


# ---------------------------------------------------------------- interests

def interest_problem(pattern: Any, when: Any = None, fire: Any = None, *,
                     placeholder: bool = False) -> str | None:
    """What is wrong with an interest, as one line; None when it is well-formed.

    A pattern is a handle or a handle glob: a known handle kind, a colon, and a
    slug that may hold `*`. `placeholder` allows `project:<slug>`, which a
    project role's file uses for the project of each instance.
    """
    if placeholder and pattern == PROJECT_PLACEHOLDER:
        pass
    elif not isinstance(pattern, str) or not (match := INTEREST_RE.match(pattern)):
        return (f"pattern {pattern!r} is not a handle or a handle glob "
                "(`project:kitchen-reno`, `domain:finances`, `bill:*`)")
    elif match.group(1) not in handles.KINDS:
        return f"pattern {pattern!r}: `{match.group(1)}` is not a handle kind"
    if when is not None and when not in INTEREST_WHEN:
        return f"`when` {when!r} is not one of {', '.join(INTEREST_WHEN)}"
    if fire is not None and not (isinstance(fire, str) and WAKE_NAME_RE.match(fire)):
        return f"`fire` {fire!r} is not a wake name"
    return None


# ------------------------------------------------------------------- prefix

def validate_prefix(value: Any) -> str:
    """Return `value` as a valid id prefix. Raises AgentError otherwise."""
    if not isinstance(value, str) or not PREFIX_RE.match(value):
        raise AgentError(
            f"id prefix {value!r} is not valid: use 1 to 16 characters, a lowercase letter first, "
            "then lowercase letters, digits or `-` (set `preferences.autocode.id_prefix`)")
    if value in RESERVED_PREFIXES:
        raise AgentError(
            f"id prefix {value!r} is reserved ({', '.join(sorted(RESERVED_PREFIXES))} cannot be "
            "used); pick another `preferences.autocode.id_prefix`")
    return value


def load_preferences(workspace: Path | None) -> dict[str, Any]:
    """`config.yaml.preferences` (read-only; {} when missing or unreadable)."""
    if workspace is None:
        return {}
    try:
        cfg = yaml.load((workspace / "_memory" / "config.yaml").read_text(encoding="utf-8"),
                        Loader=_LOADER)  # a safe loader (C or Python)
    except (OSError, yaml.YAMLError):
        return {}
    prefs = cfg.get("preferences") if isinstance(cfg, dict) else None
    return prefs if isinstance(prefs, dict) else {}


def autocode_preferences(workspace: Path | None) -> dict[str, Any]:
    """`preferences.autocode` ({} when the block is absent)."""
    block = load_preferences(workspace).get("autocode")
    return block if isinstance(block, dict) else {}


def autocode_enabled(workspace: Path | None) -> bool:
    """`preferences.autocode.enabled`: True when agents run as registered sessions (default False)."""
    return autocode_preferences(workspace).get("enabled") is True


def custom_check_name(check: Any) -> str | None:
    """`water_due` for the check value `tool:water_due`; None for a built-in check."""
    match = TOOL_CHECK_RE.match(check) if isinstance(check, str) else None
    return match.group(1) if match else None


def custom_check_path(workspace: Path, check: str) -> Path | None:
    """The script a `tool:<name>` check runs, `<workspace>/_custom/tools/<name>.py`; None for a built-in."""
    name = custom_check_name(check)
    return None if name is None else workspace / CUSTOM_TOOLS_REL / f"{name}.py"


def id_prefix(workspace: Path | None) -> str:
    """The configured id prefix (default `sa`). Raises AgentError when it is not valid."""
    value = autocode_preferences(workspace).get("id_prefix")
    return validate_prefix(DEFAULT_PREFIX if value is None else value)


# ----------------------------------------------------------------------- ids

def split_instance(name: str) -> tuple[str, str | None, bool]:
    """Split an instance name into (role, project slug, is the dev twin).

    `updater` -> ("updater", None, False); `updater:dev` -> ("updater", None, True);
    `project:solar` -> ("project", "solar", False); `project:dev` -> ("project", None, True).
    Raises AgentError for any other shape.
    """
    parts = name.split(":") if isinstance(name, str) else []
    if not parts or not all(parts) or len(parts) > 2:
        raise AgentError(f"{name!r} is not an agent name: use `<role>`, `<role>:dev` or "
                         "`project:<slug>`")
    role = parts[0]
    if not NAME_RE.match(role):
        raise AgentError(f"{role!r} is not a role name (lowercase letters, digits and `-`)")
    if len(parts) == 1:
        return role, None, False
    if parts[1] == "dev":
        return role, None, True
    if not SLUG_RE.match(parts[1]):
        raise AgentError(f"{parts[1]!r} is not a project slug (lowercase letters, digits and `-`)")
    return role, parts[1], False


def instance_name(role: Role, *, slug: str | None = None, dev: bool = False) -> str:
    """The instance name of `role`: its name, `<name>:<slug>` for a project, `<name>:dev`."""
    if dev:
        if slug:
            raise AgentError(f"one dev twin serves every {role.name} agent: ask for "
                             f"`{role.name}:dev`, with no slug")
        return f"{role.name}:dev"
    if role.kind == "project":
        if not slug:
            raise AgentError(f"a {role.name} agent needs a slug: `{role.name}:<slug>`")
        if slug == "dev":
            raise AgentError("`dev` is reserved for the dev twin; pick another slug")
        return f"{role.name}:{slug}"
    if slug:
        raise AgentError(f"{role.name!r} is a {role.kind} role and takes no slug")
    return role.name


def agent_id(prefix: str, name: str) -> str:
    """`<prefix>:<instance name>`."""
    return f"{validate_prefix(prefix)}:{name}"


def resolve_id(name: str, roles: list[Role], prefix: str, *, dev: bool = False) -> str:
    """The id for an instance name (`updater`, `updater:dev`, `project:<slug>`).

    Raises AgentError for an unknown role or a name that does not fit its kind.
    """
    role_name, slug, is_dev = split_instance(name)
    role = next((r for r in roles if r.name == role_name), None)
    if role is None:
        known = ", ".join(r.name for r in roles) or "none"
        raise AgentError(f"no role named {role_name!r} (roles: {known})")
    return agent_id(prefix, instance_name(role, slug=slug, dev=dev or is_dev))


# ------------------------------------------------------------------ parsing

def read_frontmatter(path: Path) -> dict[str, Any] | None:
    """The YAML frontmatter of a markdown file as a mapping; None when absent or unparseable."""
    try:
        match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    if not match:
        return None
    try:
        data = yaml.load(match.group(1), Loader=_LOADER)  # a safe loader (C or Python)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def role_stem(path: Path) -> str:
    """`updater` for `updater.agent.md`."""
    return path.name[: -len(ROLE_SUFFIX)]


def role_files(folder: Path) -> list[Path]:
    """The role files directly inside `folder`, sorted; sync leftovers and dev twins skipped."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob(f"*{ROLE_SUFFIX}")
                  if p.is_file() and not is_sync_artifact(p))


def cadence_keys(framework: Path, workspace: Path | None) -> set[str]:
    """Keys a duty's `schedule` may name: `preferences.cadences` of the template and the workspace."""
    keys: set[str] = set()
    try:
        template = yaml.load((framework / "templates" / "memory" / "config.yaml")
                             .read_text(encoding="utf-8"), Loader=_LOADER)  # a safe loader
    except (OSError, yaml.YAMLError):
        template = None
    for prefs in ((template or {}).get("preferences") if isinstance(template, dict) else None,
                  load_preferences(workspace)):
        cadences = prefs.get("cadences") if isinstance(prefs, dict) else None
        if isinstance(cadences, dict):
            keys.update(str(k) for k in cadences)
    return keys


def skill_stems(framework: Path, workspace: Path | None) -> set[str]:
    """Stems of every skill file, framework and overlay."""
    folders = [framework / "skills"]
    if workspace is not None:
        folders.append(workspace / "_custom" / "skills")
    stems: set[str] = set()
    for folder in folders:
        if folder.is_dir():
            stems.update(p.stem for p in folder.glob("*.md")
                         if not p.name.startswith("_") and not is_sync_artifact(p))
    return stems


def check_frontmatter(fm: dict[str, Any] | None, path: Path, *, skills: set[str],
                      schedules: set[str], tools_dir: Path | None = None) -> list[str]:
    """Every problem with a role file's frontmatter, as readable lines ([] = valid).

    `tools_dir` is given for a custom role only (`workspace/_custom/tools/`):
    its duties may then name a check script there, `check: tool:<name>`.
    """
    label = path.name
    if fm is None:
        return [f"{label}: missing or unparseable YAML frontmatter (`---` block)"]
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"{label}: missing `{field}`")
    unknown = sorted(set(fm) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        errors.append(f"{label}: unknown field(s) {unknown} (contracts/agents.md lists the fields)")
    name = fm.get("name")
    if "name" in fm:
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{label}: `name` {name!r} is not lowercase letters, digits and `-`")
        elif name in RESERVED_NAMES:
            errors.append(f"{label}: `name` {name!r} is reserved")
        elif name != role_stem(path):
            errors.append(f"{label}: `name` {name!r} does not match the filename")
    kind = fm.get("kind")
    if "kind" in fm and kind not in KINDS:
        errors.append(f"{label}: `kind` {kind!r} is not one of {', '.join(KINDS)}")
    if "description" in fm and not (isinstance(fm["description"], str) and fm["description"].strip()):
        errors.append(f"{label}: `description` is empty")
    authority = fm.get("authority")
    if "authority" in fm and authority not in TIERS:
        errors.append(f"{label}: `authority` {authority!r} is not one of {', '.join(TIERS)}")
    if "model_tier" in fm and fm["model_tier"] not in MODEL_TIERS:
        errors.append(f"{label}: `model_tier` {fm['model_tier']!r} is not one of "
                      f"{', '.join(MODEL_TIERS)}")

    role_skills = fm.get("skills")
    listed: set[str] = set()
    if "skills" in fm:
        if role_skills == ALL_SKILLS:
            if kind != "lead":
                errors.append(f"{label}: `skills: all` is for the lead role only")
        elif not isinstance(role_skills, list) or not all(isinstance(s, str) for s in role_skills):
            errors.append(f"{label}: `skills` is not a list of skill names (or `all` for the lead)")
        else:
            listed = set(role_skills)
            for skill in sorted(listed - skills):
                errors.append(f"{label}: skill `{skill}` does not exist")

    owns = fm.get("owns")
    if "owns" in fm:
        if not isinstance(owns, list) or not all(isinstance(o, str) for o in owns):
            errors.append(f"{label}: `owns` is not a list of workspace paths")
        else:
            for entry in owns:
                if not OWNS_RE.match(entry) or ".." in entry:
                    errors.append(f"{label}: `owns` entry {entry!r} is not a workspace-relative path "
                                  "under _memory/, Domains/, Projects/, Sources/, Outbox/, Inbox/ "
                                  "or Archive/")

    duties = fm.get("duties")
    if "duties" in fm:
        if not isinstance(duties, list):
            errors.append(f"{label}: `duties` is not a list (use `duties: []` for none)")
        else:
            seen: set[str] = set()
            for index, duty in enumerate(duties):
                where = f"{label}: duty #{index + 1}"
                if not isinstance(duty, dict):
                    errors.append(f"{where} is not a mapping")
                    continue
                missing = [f for f in DUTY_FIELDS if f not in duty]
                extra = sorted(set(duty) - set(DUTY_FIELDS))
                if missing:
                    errors.append(f"{where}: missing {missing}")
                if extra:
                    errors.append(f"{where}: unknown field(s) {extra}")
                duty_id = duty.get("id")
                if "id" in duty:
                    if not isinstance(duty_id, str) or not DUTY_ID_RE.match(duty_id):
                        errors.append(f"{where}: `id` {duty_id!r} is not lowercase letters, digits "
                                      "and `-`")
                    elif duty_id in seen:
                        errors.append(f"{where}: `id` {duty_id!r} is used twice")
                    else:
                        seen.add(duty_id)
                skill = duty.get("skill")
                if "skill" in duty:
                    if not isinstance(skill, str) or skill not in skills:
                        errors.append(f"{where}: skill `{skill}` does not exist")
                    elif role_skills != ALL_SKILLS and skill not in listed:
                        errors.append(f"{where}: skill `{skill}` is not in the role's `skills`")
                tier = duty.get("tier")
                if "tier" in duty:
                    if tier not in TIERS:
                        errors.append(f"{where}: `tier` {tier!r} is not one of {', '.join(TIERS)}")
                    elif authority in TIERS and TIERS.index(tier) < TIERS.index(authority):
                        errors.append(f"{where}: `tier` {tier!r} is looser than the role's "
                                      f"`authority` {authority!r}")
                schedule = duty.get("schedule")
                if "schedule" in duty and (not isinstance(schedule, str) or (
                        schedule not in SCHEDULE_WORDS and schedule not in schedules)):
                    errors.append(f"{where}: `schedule` {schedule!r} is not a key of "
                                  f"`preferences.cadences` nor one of {', '.join(SCHEDULE_WORDS)}")
                if "check" in duty and duty["check"] not in CHECKS:
                    script = custom_check_name(duty["check"])
                    if script is None or tools_dir is None:
                        errors.append(f"{where}: `check` {duty['check']!r} is not one of "
                                      f"{', '.join(CHECKS)}"
                                      + (" (a custom role may also say `tool:<name>`)"
                                         if tools_dir is None and script else ""))
                    elif not (tools_dir / f"{script}.py").is_file():
                        errors.append(f"{where}: check script `{CUSTOM_TOOLS_REL}/{script}.py` "
                                      "does not exist")
    interests = fm.get("interests")
    if "interests" in fm:
        if not isinstance(interests, list):
            errors.append(f"{label}: `interests` is not a list")
        else:
            if len(interests) > MAX_INTERESTS:
                errors.append(f"{label}: {len(interests)} interests; {MAX_INTERESTS} at most")
            for i, entry in enumerate(interests):
                where = f"{label}: interests[{i}]"
                if not isinstance(entry, dict):
                    errors.append(f"{where} is not a mapping")
                    continue
                extra = sorted(set(entry) - set(INTEREST_FIELDS))
                if extra:
                    errors.append(f"{where}: unknown field(s) {extra}")
                if not (isinstance(entry.get("why"), str) and entry["why"].strip()):
                    errors.append(f"{where}: `why` is empty")
                problem = interest_problem(entry.get("pattern"), entry.get("when"), entry.get("fire"),
                                           placeholder=kind == "project")
                if problem:
                    errors.append(f"{where}: {problem}")
    return errors


def check_dev_twin(role_path: Path) -> list[str]:
    """Problems with the dev twin of a role file ([] = present and well-formed)."""
    name = role_stem(role_path)
    dev = role_path.with_name(name + DEV_SUFFIX)
    if not dev.is_file():
        return [f"{role_path.name}: no dev twin `{dev.name}` beside it"]
    fm = read_frontmatter(dev)
    if fm is None:
        return [f"{dev.name}: missing or unparseable YAML frontmatter (`---` block)"]
    errors: list[str] = []
    if fm.get("develops") != name:
        errors.append(f"{dev.name}: `develops` is {fm.get('develops')!r}, expected {name!r}")
    if fm.get("model_tier") != "dev":
        errors.append(f"{dev.name}: `model_tier` is {fm.get('model_tier')!r}, expected 'dev'")
    unknown = sorted(set(fm) - {"develops", "model_tier"})
    if unknown:
        errors.append(f"{dev.name}: unknown field(s) {unknown}")
    return errors


def _build(fm: dict[str, Any], path: Path, origin: str) -> Role:
    skills = fm["skills"]
    dev = path.with_name(role_stem(path) + DEV_SUFFIX)
    return Role(
        name=fm["name"],
        kind=fm["kind"],
        description=" ".join(str(fm["description"]).split()),
        skills=skills if skills == ALL_SKILLS else tuple(skills),
        owns=tuple(fm["owns"]),
        authority=fm["authority"],
        model_tier=fm["model_tier"],
        duties=tuple(Duty(**{f: d[f] for f in DUTY_FIELDS}) for d in fm["duties"]),
        path=path,
        dev_path=dev if dev.is_file() else None,
        origin=origin,
        interests=tuple(Interest(pattern=e["pattern"], when=e.get("when"),
                                 why=" ".join(str(e["why"]).split()), fire=e.get("fire"))
                        for e in fm.get("interests") or []),
    )


def collect(framework: Path = FRAMEWORK, workspace: Path | None = None
            ) -> tuple[list[Role], list[str]]:
    """Every valid role, framework first then custom, and the problems found.

    Order: the lead first, then the other roles by name. A custom file named
    like a framework role is that role's overlay (`Role.overlay`), never a
    second role. A role with a problem is left out and reported.
    """
    skills = skill_stems(framework, workspace)
    schedules = cadence_keys(framework, workspace)
    roles: list[Role] = []
    errors: list[str] = []
    for path in role_files(framework / "agents"):
        fm = read_frontmatter(path)
        problems = check_frontmatter(fm, path, skills=skills, schedules=schedules)
        problems += check_dev_twin(path)
        if problems:
            errors += problems
        else:
            roles.append(_build(fm, path, "framework"))
    if workspace is not None:
        by_name = {r.name: i for i, r in enumerate(roles)}
        for path in role_files(workspace / "_custom" / "agents"):
            stem = role_stem(path)
            if stem in by_name:
                roles[by_name[stem]] = dc.replace(roles[by_name[stem]], overlay=path)
                continue
            fm = read_frontmatter(path)
            problems = check_frontmatter(fm, path, skills=skills, schedules=schedules,
                                         tools_dir=workspace / CUSTOM_TOOLS_REL)
            problems += check_dev_twin(path)
            if problems:
                errors += problems
            else:
                roles.append(_build(fm, path, "custom"))
    leads = [r for r in roles if r.kind == "lead"]
    if len(leads) > 1:
        errors.append("more than one lead role: " + ", ".join(r.name for r in leads))
    roles.sort(key=lambda r: (r.kind != "lead", r.origin != "framework", r.name))
    return roles, errors


def lead(roles: list[Role]) -> Role | None:
    """The lead role (None when the roster has none)."""
    return next((r for r in roles if r.kind == "lead"), None)


def duty_owner(roles: list[Role], skill: str) -> str | None:
    """Name of the role with a duty that runs `skill`; the lead when no role has one."""
    for role in roles:
        if any(d.skill == skill for d in role.duties):
            return role.name
    head = lead(roles)
    return head.name if head else None


def check_owner(roles: list[Role], check: str) -> str | None:
    """Name of the role with a duty behind the built-in `check`; the lead when none has one."""
    for role in roles:
        if any(d.check == check for d in role.duties):
            return role.name
    head = lead(roles)
    return head.name if head else None


def watchers_owner(roles: list[Role]) -> str | None:
    """Name of the role that owns watcher findings: a duty behind `update`, else `watchers`; else the lead."""
    for check in WATCHER_CHECKS:
        for role in roles:
            if any(d.check == check for d in role.duties):
                return role.name
    head = lead(roles)
    return head.name if head else None


def duty_stamp_key(duty: Duty) -> str:
    """The `context.yaml.last_runs` key of a duty's stamp.

    The duty's `schedule` when that names a key of `preferences.cadences`,
    else (`watchlist`, `on-request`) the duty's id with `_` for `-`.
    """
    return duty.id.replace("-", "_") if duty.schedule in SCHEDULE_WORDS else duty.schedule


def stamp_owner(roles: list[Role], key: str) -> tuple[Role, Duty] | None:
    """The (role, duty) whose stamp is `last_runs.<key>`; None when no duty has that key."""
    for role in roles:
        for duty in role.duties:
            if duty_stamp_key(duty) == key:
                return role, duty
    return None
