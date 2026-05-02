"""Tests for `tools/workspace_init.py`.

Verifies the scaffolder produces the expected structure, every YAML loads,
the 10 default domains are present with the 4-file shape, and re-running
is idempotent.
"""
from __future__ import annotations

from pathlib import Path

import yaml


EXPECTED_MEMORY_FILES = [
    "config.yaml", "context.yaml", "model-context.yaml", "interaction-log.yaml",
    "todo.yaml", "domains-index.yaml", "projects-index.yaml", "sources-index.yaml",
    "contacts.yaml", "assets-index.yaml",
    "accounts-index.yaml", "bills.yaml", "subscriptions.yaml",
    "appointments.yaml", "important-dates.yaml", "documents-index.yaml",
    "health-records.yaml", "data-sources.yaml", "ingestion-log.yaml",
    "insights.yaml", "procedures.yaml", "personal-signals.yaml",
    "action-signals.yaml", "supertailor-suggestions.yaml",
    "world.yaml", "decisions.yaml", "tags.yaml", "notification-policy.yaml",
    "outbox-log.yaml", "events.yaml", "working-sets.yaml", "upstream-writes.yaml",
]

EXPECTED_DEFAULT_DOMAINS = [
    "Health", "Finance", "Home", "Vehicles", "Pets",
    "Family", "Travel", "Career", "Hobbies", "Self",
]

DOMAIN_FILES = ["info.md", "status.md", "history.md", "rolodex.md", "sources.md"]


def test_init_creates_memory_dir(initialized_workspace: Path) -> None:
    mem = initialized_workspace / "_memory"
    assert mem.is_dir()
    for fname in EXPECTED_MEMORY_FILES:
        assert (mem / fname).is_file(), f"missing _memory/{fname}"


def test_init_memory_files_parse(initialized_workspace: Path) -> None:
    mem = initialized_workspace / "_memory"
    for fname in EXPECTED_MEMORY_FILES:
        with (mem / fname).open() as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict), f"{fname} did not parse to dict"
        assert "schema_version" in data, f"{fname} missing schema_version after init"


def test_init_creates_default_domains(initialized_workspace: Path) -> None:
    domains = initialized_workspace / "Domains"
    assert domains.is_dir()
    for name in EXPECTED_DEFAULT_DOMAINS:
        d = domains / name
        assert d.is_dir(), f"missing default domain folder: {name}"
        for fname in DOMAIN_FILES:
            assert (d / fname).is_file(), f"{name}/{fname} missing"


def test_init_substitutes_domain_name_placeholder(initialized_workspace: Path) -> None:
    """The {{DOMAIN_NAME}} placeholder must be filled in for every domain file."""
    domains = initialized_workspace / "Domains"
    for name in EXPECTED_DEFAULT_DOMAINS:
        for fname in DOMAIN_FILES:
            body = (domains / name / fname).read_text()
            assert "{{DOMAIN_NAME}}" not in body, (
                f"{name}/{fname} still has unsubstituted {{{{DOMAIN_NAME}}}}"
            )
            assert name in body, f"{name}/{fname} should mention the domain name"


def test_init_creates_top_level_folders(initialized_workspace: Path) -> None:
    for folder in ["Inbox", "Outbox", "Archive", "Projects", "Sources"]:
        f = initialized_workspace / folder
        assert f.is_dir(), f"missing top-level {folder}/"
        assert (f / "README.md").is_file(), f"missing README in {folder}/"
    domains_readme = initialized_workspace / "Domains" / "README.md"
    assert domains_readme.is_file(), "missing Domains/README.md"


def test_init_creates_sources_subfolders(initialized_workspace: Path) -> None:
    """Sources/ ships with documents/, references/, _cache/ subfolders."""
    sources = initialized_workspace / "Sources"
    for sub in ["documents", "references", "_cache"]:
        assert (sources / sub).is_dir(), f"missing Sources/{sub}/"


def test_init_creates_outbox_lifecycle_subfolders(initialized_workspace: Path) -> None:
    """Outbox/ ships with the four lifecycle stages (item #13)."""
    outbox = initialized_workspace / "Outbox"
    for sub in ["drafts", "staging", "sent", "sealed"]:
        assert (outbox / sub).is_dir(), f"missing Outbox/{sub}/"


def test_init_creates_internal_memory_dirs(initialized_workspace: Path) -> None:
    """The internal _memory sub-directories the new tools rely on are scaffolded."""
    memory = initialized_workspace / "_memory"
    for sub in ["_briefings", "_artifacts", "_session", "_telemetry",
                "_checkpoints", "sensitive", "events"]:
        assert (memory / sub).is_dir(), f"missing _memory/{sub}/"


def test_init_creates_custom_overlay_scaffold(initialized_workspace: Path) -> None:
    custom = initialized_workspace / "_custom"
    for sub in ["rules", "skills", "agents", "templates", "templates/memory", "tools"]:
        assert (custom / sub).is_dir(), f"missing _custom/{sub}/"


def test_init_creates_workspace_todo(initialized_workspace: Path) -> None:
    todo = initialized_workspace / "todo.md"
    assert todo.is_file()
    body = todo.read_text()
    assert "P0" in body and "P1" in body, "workspace todo.md missing priority sections"


def test_init_is_idempotent(framework_dir: Path, initialized_workspace: Path) -> None:
    """A second run does not change file contents."""
    from superagent.tools.workspace_init import main as init_main

    sample = initialized_workspace / "Domains" / "Health" / "info.md"
    sample.write_text(sample.read_text() + "\n<!-- user edit -->\n")
    user_text = sample.read_text()
    rc = init_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0
    assert sample.read_text() == user_text, "init clobbered a user edit"
