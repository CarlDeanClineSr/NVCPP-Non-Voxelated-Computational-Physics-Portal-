"""Offline checks for the two maintained provider-workflow definitions."""

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["hourly_observatory.yml", "roman_prelaunch_readiness.yml"])
def test_maintained_workflows_select_node24_action_majors(name):
    text = (ROOT / ".github/workflows" / name).read_text()
    expected = {
        "actions/checkout": "v6",
        "actions/setup-python": "v6",
        "actions/upload-artifact": "v7",
        "actions/cache/restore": "v5",
        "actions/cache/save": "v5",
    }
    actions = dict(re.findall(r"uses:\s+(actions/[^@\s]+)@(\S+)", text))
    for action, version in actions.items():
        if action in expected:
            assert version == expected[action]
    assert {"actions/checkout", "actions/setup-python", "actions/upload-artifact"} <= actions.keys()
    assert 'python-version: "3.12"' in text
    assert "retention-days: 90" in text
    assert "contents: read" in text


def test_runtime_maintenance_does_not_enable_hourly_or_holdout_automation():
    hourly = (ROOT / ".github/workflows/hourly_observatory.yml").read_text()
    triggers = hourly.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    assert "workflow_dispatch:" in triggers
    assert "schedule:" not in triggers
    assert "push:" not in triggers
    assert "pull_request:" not in triggers
    roman = (ROOT / ".github/workflows/roman_prelaunch_readiness.yml").read_text()
    assert 'cron: "37 */6 * * *"' in roman  # retain, do not widen the existing watch
    assert "tests/test_roman_page_reporting.py" in roman
    for text in (hourly, roman):
        assert "execute_frozen_holdout" not in text
        assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in text
