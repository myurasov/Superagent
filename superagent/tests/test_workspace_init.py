# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/workspace_init.py`.

Verifies the scaffolder produces the expected structure, every YAML loads,
the 13 default domains are REGISTERED in `_memory/domains-index.yaml` (their
folders under `Domains/<Name>/` are LAZY per
`contracts/domains-and-assets.md` § 6.4a — see `test_domains_lazy.py`), and
re-running is idempotent.
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
    "domain-suggestions.yaml",
]

EXPECTED_DEFAULT_DOMAINS = [
    "Health", "Finances", "Home", "Vehicles", "Assets", "Pets",
    "Family", "Travel", "Career", "Business", "Education", "Hobbies", "Self",
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


def test_init_creates_domains_dir_but_no_subfolders(initialized_workspace: Path) -> None:
    """Init creates `Domains/` + `README.md` only — no per-domain folders.

    Per-domain folders are LAZY (per `contracts/domains-and-assets.md` § 6.4a);
    they materialize on first data write via
    `superagent.tools.domains.ensure_folder`.
    """
    domains = initialized_workspace / "Domains"
    assert domains.is_dir()
    assert (domains / "README.md").is_file()
    for name in EXPECTED_DEFAULT_DOMAINS:
        assert not (domains / name).exists(), (
            f"{name}/ should NOT exist at init time (lazy materialization). "
            "It materializes when the first row of data lands."
        )


def test_init_registers_all_default_domains_in_index(
    initialized_workspace: Path,
) -> None:
    """The 12 default domains must be REGISTERED in `_memory/domains-index.yaml`.

    Folders are lazy, but the registry is populated so capture skills know
    where to route data (and so `add-domain` can detect slug collisions).
    """
    with (initialized_workspace / "_memory" / "domains-index.yaml").open() as fh:
        data = yaml.safe_load(fh)
    registered_names = {d.get("name") for d in (data.get("domains") or [])}
    for name in EXPECTED_DEFAULT_DOMAINS:
        assert name in registered_names, f"default domain {name} not registered"


def test_init_creates_top_level_folders(initialized_workspace: Path) -> None:
    for folder in ["Inbox", "Outbox", "Archive", "Projects", "Sources"]:
        f = initialized_workspace / folder
        assert f.is_dir(), f"missing top-level {folder}/"
        assert (f / "README.md").is_file(), f"missing README in {folder}/"
    domains_readme = initialized_workspace / "Domains" / "README.md"
    assert domains_readme.is_file(), "missing Domains/README.md"


def test_init_does_not_force_sources_subfolders(initialized_workspace: Path) -> None:
    """Sources/ ships with ONLY README.md; layout is user-defined.

    The agent reserves only `Sources/_cache/` (created lazily on first fetch)
    and `Sources/README.md`. Documents and references live wherever the user
    puts them. See contracts/sources.md \u00a7 15.1.
    """
    sources = initialized_workspace / "Sources"
    assert sources.is_dir()
    assert (sources / "README.md").is_file()
    assert not (sources / "documents").exists(), "Sources/documents/ no longer auto-created"
    assert not (sources / "references").exists(), "Sources/references/ no longer auto-created"
    assert not (sources / "_cache").exists(), "Sources/_cache/ should be lazy-created on first fetch"


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
    """A second run does not change file contents.

    Materializes a domain folder via the lazy helper, scribbles a user-edit
    line into it, then re-runs init and asserts the user-edit survives. (The
    init flow itself does not pre-create per-domain folders any more, so we
    have to materialize one explicitly to test the no-clobber behavior.)
    """
    from superagent.tools.domains import ensure_folder
    from superagent.tools.workspace_init import main as init_main

    ensure_folder(initialized_workspace, framework_dir, "health")
    sample = initialized_workspace / "Domains" / "Health" / "info.md"
    sample.write_text(sample.read_text() + "\n<!-- user edit -->\n")
    user_text = sample.read_text()
    rc = init_main([
        "--workspace", str(initialized_workspace),
        "--framework", str(framework_dir),
    ])
    assert rc == 0
    assert sample.read_text() == user_text, "init clobbered a user edit"
