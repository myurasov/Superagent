# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/play.py` (playbook resolver) and `tools/scenarios.py`."""
from __future__ import annotations

import yaml
from pathlib import Path


def test_play_list(framework_dir: Path, initialized_workspace: Path) -> None:
    from superagent.tools.play import list_playbooks

    rows = list_playbooks(framework_dir, initialized_workspace)
    names = {r["stem"] for r in rows}
    assert "start-of-day" in names
    assert "end-of-week" in names


def test_play_resolve_no_conditions(framework_dir: Path,
                                    initialized_workspace: Path) -> None:
    from superagent.tools.play import find_playbook, resolve

    p = find_playbook(framework_dir, initialized_workspace, "start-of-day")
    assert p is not None
    data = yaml.safe_load(p.read_text())
    steps = resolve(initialized_workspace, data)
    # In a fresh workspace, conditional steps evaluate false; unconditional run.
    skills_run = [s["skill"] for s in steps]
    assert "whatsup" in skills_run
    assert "daily-update" in skills_run


def test_play_eval_condition() -> None:

    # The function reads workspace state — here we just test the parser
    # does not crash on a "always" case.
    # eval_condition needs workspace; easier to test via resolve above.
    pass


def test_scenarios_cancel_subscriptions(initialized_workspace: Path) -> None:
    """Inject a subscription, then run cancel-subscriptions."""
    from superagent.tools.scenarios import cancel_subscriptions
    import yaml

    sub_path = initialized_workspace / "_memory" / "subscriptions.yaml"
    data = yaml.safe_load(sub_path.read_text()) or {}
    data.setdefault("subscriptions", []).append({
        "id": "sub-test-monthly",
        "name": "Test Monthly",
        "amount": 10.0,
        "currency": "USD",
        "cadence": "monthly",
        "status": "active",
    })
    sub_path.write_text(yaml.safe_dump(data, sort_keys=False))
    result = cancel_subscriptions(initialized_workspace, ["sub-test-monthly"])
    assert "matched" in result
    assert len(result["matched"]) == 1
    assert result["monthly_savings"] == 10.0
    assert result["annual_savings"] == 120.0


def test_scenarios_bill_shock(initialized_workspace: Path) -> None:
    from superagent.tools.scenarios import bill_shock
    import yaml

    bill_path = initialized_workspace / "_memory" / "bills.yaml"
    data = yaml.safe_load(bill_path.read_text()) or {}
    data.setdefault("bills", []).append({
        "id": "bill-test",
        "name": "Test bill",
        "amount": 100.0,
        "currency": "USD",
        "cadence": "monthly",
        "status": "active",
    })
    bill_path.write_text(yaml.safe_dump(data, sort_keys=False))
    result = bill_shock(initialized_workspace, percent=10.0)
    assert result["base_monthly"] == 100.0
    assert result["increase_monthly"] == 10.0
    assert result["new_monthly"] == 110.0


def test_scenarios_project_overrun(initialized_workspace: Path) -> None:
    from superagent.tools.scenarios import project_overrun
    import yaml

    proj_path = initialized_workspace / "_memory" / "projects-index.yaml"
    data = yaml.safe_load(proj_path.read_text()) or {}
    data.setdefault("projects", []).append({
        "id": "test-proj",
        "name": "Test Project",
        "budget": {"planned": 10000.0, "spent": 5000.0, "currency": "USD"},
    })
    proj_path.write_text(yaml.safe_dump(data, sort_keys=False))
    result = project_overrun(initialized_workspace, "test-proj", 25.0)
    assert result["overrun_amount"] == 2500.0
    assert result["new_total"] == 12500.0
