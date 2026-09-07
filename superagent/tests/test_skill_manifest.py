# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/build_skill_manifest.py`."""
from __future__ import annotations

from pathlib import Path

import yaml


def test_manifest_generation(framework_dir: Path, tmp_path: Path) -> None:
    from superagent.tools.build_skill_manifest import main as build_main

    out = tmp_path / "_manifest.yaml"
    rc = build_main(["--framework", str(framework_dir), "--output", str(out)])
    assert rc == 0
    data = yaml.safe_load(out.read_text())
    assert isinstance(data, dict)
    assert data["schema_version"] == 1
    assert data["skill_count"] >= 30
    skills = data["skills"]
    assert isinstance(skills, list)
    for row in skills:
        for field in ("name", "stem", "path", "one_line", "triggers",
                      "lines", "typical_token_cost"):
            assert field in row, f"manifest row missing {field}"
        assert row["name"].startswith("superagent-")
        if row["origin"] == "framework":
            # The manifest is committed: no machine-absolute prefix allowed.
            assert not Path(row["path"]).is_absolute(), row["path"]
            assert row["path"] == f"superagent/skills/{row['stem']}.md"
        for entry in row["typical_files_read"]:
            assert "`" not in entry, (row["stem"], entry)
            assert not entry.startswith("workspace/"), (row["stem"], entry)
            assert not entry.endswith(("/", ";", ",")), (row["stem"], entry)


def test_infer_files_read_normalizes_markdown_noise() -> None:
    from superagent.tools.build_skill_manifest import infer_files_read

    body = (
        "Read `workspace/_memory/config.yaml` first, then `_memory/config.yaml`.\n"
        "The agent owns `Sources/_cache/`; grep `Sources/_cache/<*>/_summary.md`\n"
        "(see `Sources/documents/receipt.pdf`), and [Sources/references/x.ref.md],\n"
        "then `Domains/Health/history.md`, workspace/Projects/tax-2026/status.md.\n"
    )
    files = infer_files_read(body)
    assert files == [
        "_memory/config.yaml",
        "Sources/_cache/<*>/_summary.md",
        "Sources/documents/receipt.pdf",
        "Sources/references/x.ref.md",
        "Domains/Health/history.md",
        "Projects/tax-2026/status.md",
    ]


def test_display_path_relativizes_inside_root_only(tmp_path: Path) -> None:
    from superagent.tools.build_skill_manifest import display_path

    root = tmp_path / "repo"
    inside = root / "superagent" / "skills" / "todo.md"
    outside = tmp_path / "elsewhere" / "_custom" / "skills" / "mine.md"
    assert display_path(inside, root) == "superagent/skills/todo.md"
    assert display_path(outside, root) == str(outside)
    assert display_path(inside, None) == str(inside)


def test_manifest_includes_workspace_overlay(
    framework_dir: Path, initialized_workspace: Path, tmp_path: Path
) -> None:
    from superagent.tools.build_skill_manifest import main as build_main

    overlay = initialized_workspace / "_custom" / "skills"
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "my-custom.md").write_text(
        "---\n"
        "name: superagent-my-custom\n"
        "description: A custom skill for testing.\n"
        "triggers: [my-custom]\n"
        "mcp_required: []\n"
        "---\n\n"
        "# My custom skill body.\n"
    )
    out = tmp_path / "_manifest.yaml"
    rc = build_main([
        "--framework", str(framework_dir),
        "--workspace", str(initialized_workspace),
        "--output", str(out),
    ])
    assert rc == 0
    data = yaml.safe_load(out.read_text())
    custom_rows = [r for r in data["skills"] if r.get("origin") == "custom"]
    assert any(r["name"] == "superagent-my-custom" for r in custom_rows)
    # This workspace lives outside the repo, so the overlay row keeps its
    # raw path instead of an unrelatable relative one.
    (custom,) = [r for r in custom_rows if r["name"] == "superagent-my-custom"]
    assert custom["path"] == str(overlay / "my-custom.md")


def test_overlay_inside_repo_emits_repo_relative_path(tmp_path: Path) -> None:
    from superagent.tools.build_skill_manifest import main as build_main

    repo = tmp_path / "repo"
    framework = repo / "superagent"
    overlay = repo / "workspace" / "_custom" / "skills"
    overlay.mkdir(parents=True)
    (overlay / "mine.md").write_text(
        "---\nname: superagent-mine\ndescription: Overlay skill.\ntriggers: [mine]\n---\n\nBody.\n"
    )
    out = tmp_path / "_manifest.yaml"
    rc = build_main([
        "--framework", str(framework),
        "--workspace", str(repo / "workspace"),
        "--output", str(out),
    ])
    assert rc == 0
    data = yaml.safe_load(out.read_text())
    (row,) = data["skills"]
    assert row["origin"] == "custom"
    assert row["path"] == "workspace/_custom/skills/mine.md"
