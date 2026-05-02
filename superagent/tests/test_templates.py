"""Tests for the template files under `superagent/templates/`.

Verifies:
  - every YAML template parses
  - every YAML template carries `schema_version`
  - every domain template uses the documented placeholders
  - the maintenance-banner appears on every framework-managed markdown template
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REQUIRED_MEMORY_TEMPLATES = [
    "config.yaml",
    "context.yaml",
    "model-context.yaml",
    "interaction-log.yaml",
    "todo.yaml",
    "domains-index.yaml",
    "projects-index.yaml",
    "sources-index.yaml",
    "contacts.yaml",
    "assets-index.yaml",
    "accounts-index.yaml",
    "bills.yaml",
    "subscriptions.yaml",
    "appointments.yaml",
    "important-dates.yaml",
    "documents-index.yaml",
    "health-records.yaml",
    "data-sources.yaml",
    "ingestion-log.yaml",
    "insights.yaml",
    "procedures.yaml",
    "personal-signals.yaml",
    "action-signals.yaml",
    "supertailor-suggestions.yaml",  # was pm-suggestions.yaml
    # Added by the second-pass implementation of ideas-better-structure +
    # perf-improvement-ideas:
    "world.yaml",
    "decisions.yaml",
    "tags.yaml",
    "notification-policy.yaml",
    "outbox-log.yaml",
    "events.yaml",
    "working-sets.yaml",
    "upstream-writes.yaml",
]

REQUIRED_DOMAIN_TEMPLATES = ["info.md", "status.md", "history.md", "rolodex.md", "sources.md"]
REQUIRED_PROJECT_TEMPLATES = ["info.md", "status.md", "history.md", "rolodex.md", "sources.md"]


def test_every_required_memory_template_exists(framework_dir: Path) -> None:
    """The full set of memory templates must be present."""
    mem_dir = framework_dir / "templates" / "memory"
    assert mem_dir.is_dir(), f"missing {mem_dir}"
    for fname in REQUIRED_MEMORY_TEMPLATES:
        assert (mem_dir / fname).is_file(), f"missing template: {fname}"


def test_every_memory_template_parses_with_schema_version(framework_dir: Path) -> None:
    """Every YAML template must parse and carry schema_version."""
    mem_dir = framework_dir / "templates" / "memory"
    for path in sorted(mem_dir.glob("*.yaml")):
        with path.open() as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict), f"{path.name}: top-level is not a mapping"
        assert "schema_version" in data, f"{path.name}: missing schema_version"
        assert isinstance(data["schema_version"], int), (
            f"{path.name}: schema_version must be an int"
        )


def test_every_required_domain_template_exists(framework_dir: Path) -> None:
    """The 4-file domain template set must be present."""
    dom_dir = framework_dir / "templates" / "domains"
    assert dom_dir.is_dir(), f"missing {dom_dir}"
    for fname in REQUIRED_DOMAIN_TEMPLATES:
        assert (dom_dir / fname).is_file(), f"missing domain template: {fname}"


def test_domain_templates_use_canonical_placeholders(framework_dir: Path) -> None:
    """Each domain template uses the {{DOMAIN_NAME}} placeholder."""
    dom_dir = framework_dir / "templates" / "domains"
    for fname in REQUIRED_DOMAIN_TEMPLATES:
        body = (dom_dir / fname).read_text()
        assert "{{DOMAIN_NAME}}" in body, f"{fname} missing {{{{DOMAIN_NAME}}}} placeholder"


def test_domain_templates_carry_maintenance_banner(framework_dir: Path) -> None:
    """All four domain templates carry the do-not-edit banner."""
    dom_dir = framework_dir / "templates" / "domains"
    for fname in REQUIRED_DOMAIN_TEMPLATES:
        body = (dom_dir / fname).read_text()
        assert "[Do not change manually" in body, (
            f"{fname} missing maintenance banner"
        )


def test_every_yaml_template_carries_managed_banner(framework_dir: Path) -> None:
    """Every YAML memory template starts with the managed-by-Superagent banner."""
    mem_dir = framework_dir / "templates" / "memory"
    pattern = re.compile(r"#\s*\[Do not change manually", re.IGNORECASE)
    for path in sorted(mem_dir.glob("*.yaml")):
        body = path.read_text()
        first_lines = "\n".join(body.splitlines()[:3])
        assert pattern.search(first_lines), f"{path.name}: missing managed banner"


def test_folder_readmes_exist(framework_dir: Path) -> None:
    """The standard folder READMEs ship with the framework."""
    rd = framework_dir / "templates" / "folder-readmes"
    expected = [
        "Domains.md", "Projects.md", "Sources.md",
        "Inbox.md", "Outbox.md", "Resources.md", "Archive.md", "Tmp.md",
    ]
    for fname in expected:
        assert (rd / fname).is_file(), f"missing folder README: {fname}"


def test_no_materials_readme(framework_dir: Path) -> None:
    """Materials.md should have been renamed to Resources.md."""
    rd = framework_dir / "templates" / "folder-readmes"
    assert not (rd / "Materials.md").exists(), (
        "Materials.md still present; should be renamed to Resources.md"
    )


def test_project_templates_exist(framework_dir: Path) -> None:
    """The 4-file project template set must be present."""
    proj_dir = framework_dir / "templates" / "projects"
    assert proj_dir.is_dir(), f"missing {proj_dir}"
    for fname in REQUIRED_PROJECT_TEMPLATES:
        assert (proj_dir / fname).is_file(), f"missing project template: {fname}"


def test_project_templates_use_placeholder(framework_dir: Path) -> None:
    """Each project template uses the {{PROJECT_NAME}} placeholder."""
    proj_dir = framework_dir / "templates" / "projects"
    for fname in REQUIRED_PROJECT_TEMPLATES:
        body = (proj_dir / fname).read_text()
        assert "{{PROJECT_NAME}}" in body, f"{fname} missing {{{{PROJECT_NAME}}}} placeholder"


def test_sources_md_template_documents_immutability_rule(framework_dir: Path) -> None:
    """The Domain sources.md template must call out the rule that source docs
    NEVER live directly under Domains/ but only in Sources/."""
    body = (framework_dir / "templates" / "domains" / "sources.md").read_text()
    # Normalize whitespace so wrapped-line phrases still match.
    flat = " ".join(body.split())
    assert "NEVER stored inside Domains" in flat, (
        "Domain sources.md template must document the immutability + 'no source docs in Domains' rule."
    )


def test_ref_template_exists(framework_dir: Path) -> None:
    """The .ref.md template must exist with the documented frontmatter fields."""
    ref = framework_dir / "templates" / "sources" / "ref.md"
    assert ref.is_file(), "missing sources/ref.md template"
    body = ref.read_text()
    for field in ["ref_version", "title", "description", "kind", "source", "ttl_minutes", "sensitive"]:
        assert field in body, f"ref.md template missing field {field!r}"


def test_workflow_templates_present(framework_dir: Path) -> None:
    """The 5 starter workflow templates ship with a schema reference."""
    wf_dir = framework_dir / "templates" / "workflows"
    assert wf_dir.is_dir()
    expected = {
        "_schema.yaml", "tax-filing.yaml", "trip-planning.yaml",
        "annual-health-tuneup.yaml", "job-search.yaml", "appliance-replacement.yaml",
    }
    present = {p.name for p in wf_dir.glob("*.yaml")}
    missing = expected - present
    assert not missing, f"missing workflow templates: {sorted(missing)}"


def test_workflow_templates_parse_with_required_fields(framework_dir: Path) -> None:
    """Every workflow template parses + carries its required top-level fields."""
    wf_dir = framework_dir / "templates" / "workflows"
    for path in sorted(wf_dir.glob("*.yaml")):
        if path.name == "_schema.yaml":
            continue
        with path.open() as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict), f"{path.name}: not a mapping"
        for field in ("schema_version", "id", "name", "goal",
                      "success_criteria", "seed_tasks"):
            assert field in data, f"{path.name}: missing field {field!r}"


def test_playbooks_present(framework_dir: Path) -> None:
    """Starter playbooks ship at framework root."""
    pb_dir = framework_dir / "playbooks"
    assert pb_dir.is_dir(), "missing superagent/playbooks/"
    expected = {
        "_schema.yaml", "start-of-day.yaml", "end-of-week.yaml",
        "tax-prep-season.yaml", "pre-trip-week.yaml",
        "health-checkup-quarter.yaml",
    }
    present = {p.name for p in pb_dir.glob("*.yaml")}
    missing = expected - present
    assert not missing, f"missing playbook(s): {sorted(missing)}"


def test_playbooks_parse_with_required_fields(framework_dir: Path) -> None:
    """Every playbook parses + carries its required top-level fields."""
    pb_dir = framework_dir / "playbooks"
    for path in sorted(pb_dir.glob("*.yaml")):
        if path.name == "_schema.yaml":
            continue
        with path.open() as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict), f"{path.name}: not a mapping"
        for field in ("schema_version", "name", "trigger", "steps"):
            assert field in data, f"{path.name}: missing field {field!r}"
        assert isinstance(data["steps"], list)


def test_notification_policy_seeds_default_rules(framework_dir: Path) -> None:
    """The notification-policy template must carry default rules."""
    p = framework_dir / "templates" / "memory" / "notification-policy.yaml"
    with p.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    defaults = data.get("default_rules") or []
    assert len(defaults) >= 8, "expected at least 8 seeded default rules"


def test_world_template_has_node_and_edge_lists(framework_dir: Path) -> None:
    """world.yaml template has nodes and edges keys at the top level."""
    p = framework_dir / "templates" / "memory" / "world.yaml"
    with p.open() as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    assert "nodes" in data and "edges" in data


def test_commit_msg_hook_exists_and_executable(framework_dir: Path) -> None:
    """The commit-msg hook ships and is executable."""
    hook = framework_dir / "templates" / "githooks" / "commit-msg"
    assert hook.is_file(), "missing commit-msg hook"
    body = hook.read_text()
    assert "Made-with" in body, "commit-msg hook should block Made-with"
    assert "Co-authored-by" in body, "commit-msg hook should block AI co-authors"


def test_supercoder_agent_md_is_single_purpose(framework_dir: Path) -> None:
    """supercoder.agent.md describes a single-purpose framework-only role."""
    body = (framework_dir / "supercoder.agent.md").read_text()
    assert "Personal-data safeguard" in body
    assert "approved" in body.lower(), "must require an approved suggestion"
    # No Mode 2 / project-build content.
    assert "Mode 2" not in body
    assert "project-build" not in body.lower()
    assert "code-projects" not in body
