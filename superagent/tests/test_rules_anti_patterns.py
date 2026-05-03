"""Tests for the YAML-driven anti-pattern rule loader."""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml


def test_anti_patterns_yaml_loads_into_module_globals(framework_dir: Path) -> None:
    """The catalogue YAML is loaded at import time and matches contracts/anti-patterns.md."""
    from superagent.tools.anti_patterns import PATTERNS, MITIGATIONS

    rules_path = framework_dir / "rules" / "anti-patterns.yaml"
    assert rules_path.exists(), "framework rules YAML must ship at superagent/rules/"
    with rules_path.open() as fh:
        doc = yaml.safe_load(fh)
    expected_ids = {row["id"] for row in doc["rules"]}
    loaded_ids = {pid for pid, _, _, _ in PATTERNS}
    assert loaded_ids == expected_ids, (
        f"compiled patterns ({loaded_ids}) drifted from YAML ({expected_ids})"
    )
    for rid in expected_ids:
        assert MITIGATIONS.get(rid), f"every rule needs a mitigation; {rid} missing"


def test_anti_patterns_user_overlay_extends_framework_rules(
    tmp_path: Path, framework_dir: Path
) -> None:
    """`load_rules` concatenates framework + user-overlay rules."""
    from superagent.tools.anti_patterns import load_rules

    user_rules = tmp_path / "anti-patterns.yaml"
    user_rules.write_text(textwrap.dedent("""
        schema_version: 1
        rules:
          - id: AP-USER-1
            severity: warning
            description: "User-defined rule for testing."
            pattern: 'magic-test-string-XYZ'
            flags: [IGNORECASE]
            mitigation: "Don't write magic-test-string-XYZ."
    """).strip())
    framework_yaml = framework_dir / "rules" / "anti-patterns.yaml"
    patterns, mitigations = load_rules(framework_yaml, user_rules)
    ids = [pid for pid, _, _, _ in patterns]
    assert "AP-USER-1" in ids, "user overlay rule must appear in compiled list"
    assert ids.index("AP-USER-1") > ids.index("AP-1"), (
        "user rules must come AFTER framework rules"
    )
    assert mitigations["AP-USER-1"] == "Don't write magic-test-string-XYZ."


def test_anti_patterns_user_overlay_missing_file_is_silent(
    tmp_path: Path, framework_dir: Path
) -> None:
    """Pointing at a non-existent overlay returns just the framework rules."""
    from superagent.tools.anti_patterns import load_rules

    framework_yaml = framework_dir / "rules" / "anti-patterns.yaml"
    patterns, _ = load_rules(framework_yaml, tmp_path / "does-not-exist.yaml")
    ids = {pid for pid, _, _, _ in patterns}
    assert ids and "AP-1" in ids
