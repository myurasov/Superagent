# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
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
    "events.yaml",
    "outbox-log.yaml",
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


def test_supercoder_agent_md_points_at_real_migration_mechanism(framework_dir: Path) -> None:
    """Schema-bump guidance names the shipped migration files, not phantom tools."""
    body = (framework_dir / "supercoder.agent.md").read_text()
    assert "tools/migrate.py" not in body, "no such tool; migrations are markdown files"
    assert "test_migrations" not in body, "no such test module; coverage lives in test_version.py"
    assert "superagent/migrations/<to_version>.md" in body
    assert "tests/test_version.py" in body
    assert "refresh-manifest" in body


def _load_config_template(framework_dir: Path) -> dict:
    with (framework_dir / "templates" / "memory" / "config.yaml").open() as fh:
        return yaml.safe_load(fh)


def test_config_template_registers_simplefin(framework_dir: Path) -> None:
    """The shipped finance ingestor appears in both catalogue blocks."""
    cfg = _load_config_template(framework_dir)
    finance = cfg["data_sources_configured"]["finance"]
    assert finance.get("simplefin") is False, "simplefin must be catalogued (off by default)"
    schedule = cfg["preferences"]["ingestion_schedule"]
    assert schedule.get("simplefin") == "weekly"


def test_config_template_declares_outbox_drafts_stale_days(framework_dir: Path) -> None:
    """contracts/outbox-lifecycle.md keys stale-draft surfacing on this setting."""
    cfg = _load_config_template(framework_dir)
    assert cfg["preferences"]["outbox"]["drafts_stale_days"] == 14


def test_config_template_sensitive_comment_does_not_overclaim(framework_dir: Path) -> None:
    """auto_route_files is declared but not enforced; the comment must say so."""
    body = (framework_dir / "templates" / "memory" / "config.yaml").read_text()
    assert "auto_route_files" in body
    assert "NOT yet enforced" in body
    assert "Files routed to sensitive/ by default" not in body


def test_browserctl_app_template_starts_with_frontmatter(framework_dir: Path) -> None:
    """A verbatim copy must parse for skill_loader / build_skill_manifest.

    Both parsers reject any file whose first bytes are not the ``---`` fence,
    so the template's guidance comment has to live BELOW the frontmatter.
    """
    text = (framework_dir / "templates" / "browserctl.app.md").read_text()
    assert text.startswith("---\n"), "template must open with the frontmatter fence"
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    assert match, "frontmatter fence not closed"
    fm = yaml.safe_load(match.group(1))
    assert isinstance(fm, dict)
    for key in ("name", "description", "triggers", "extends"):
        assert key in fm, f"frontmatter missing {key}"
    assert fm["extends"] == "superagent-browserctl"
    assert "DELETE THIS ENTIRE COMMENT BLOCK" in text


def test_ingestion_log_template_has_no_phantom_rotation_tool(framework_dir: Path) -> None:
    """No rotate-logs tool ships; the header must not promise one."""
    body = (framework_dir / "templates" / "memory" / "ingestion-log.yaml").read_text()
    assert "rotate-logs" not in body
    assert "rotate_logs" not in body
    assert "No automatic rotation" in body


def test_inbox_readme_names_real_scaffold_module(framework_dir: Path) -> None:
    """The scaffold tool is workspace_init.py (underscore), not workspace-init.py."""
    body = (framework_dir / "templates" / "folder-readmes" / "Inbox.md").read_text()
    assert "workspace-init.py" not in body
    assert "workspace_init" in body


def test_domain_history_template_documents_opt_in_auto_blocks(framework_dir: Path) -> None:
    """history.md explains render_domain marker blocks without shipping a marker pair."""
    body = (framework_dir / "templates" / "domains" / "history.md").read_text()
    assert "auto:<slug>:start" in body
    assert "render_domain" in body
    assert "domain-reflection.md" in body
    # Markers are opt-in per contracts/domain-reflection.md; the template
    # must NOT carry a live pair (render_domain would flag it as stale).
    assert "<!-- auto:" not in body


def test_contracts_have_no_dangling_docs_pointers(framework_dir: Path) -> None:
    """Contracts must not defer to docs pages that were never written."""
    contracts = framework_dir / "contracts"
    assert "docs/custom-overlay.md" not in (contracts / "custom-overlay.md").read_text()
    assert "docs/outbound-surfaces.md" not in (contracts / "outbound-surface.md").read_text()
    assert not (framework_dir / "docs" / "custom-overlay.md").exists()
    assert not (framework_dir / "docs" / "outbound-surfaces.md").exists()


def test_sensitive_tier_contract_describes_shipped_behaviour(framework_dir: Path) -> None:
    """The contract must not claim an auto-route move or a tier-aware validate.py."""
    body = (framework_dir / "contracts" / "sensitive-tier.md").read_text()
    assert "not tier-aware" in body
    assert "NOT yet enforced" in body
    assert "are physically moved to the sensitive subdir at init" not in body
    assert "schema check is sensitive-tier-aware" not in body
    assert "S-33" in body
    roadmap = (framework_dir / "docs" / "roadmap.md").read_text()
    assert "| S-33 |" in roadmap
