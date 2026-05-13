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
