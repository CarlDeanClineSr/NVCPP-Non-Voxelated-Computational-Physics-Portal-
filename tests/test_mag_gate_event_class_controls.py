import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from historical.mag_gate_event_class_controls import (
    EventClassControlError,
    discover_control_intervals,
    normalize_class,
    record_to_interval,
    support_scope_metrics,
)


def test_normalize_control_classes():
    assert normalize_class("quiet control") == "LOW_ACTIVITY"
    assert normalize_class("LOW_ACTIVITY") == "LOW_ACTIVITY"
    assert normalize_class("moderate solar wind") == "MODERATE_ACTIVITY"
    assert normalize_class("clear shock") == "ISOLATED_SHOCK"
    assert normalize_class("mild glancing event") == "MILD_OR_GLANCING_STRUCTURE"
    assert normalize_class("Gannon development event") == "GANNON_DEVELOPMENT_EVENT"


def test_record_to_interval_normalizes_complete_utc_day():
    result = record_to_interval(
        {
            "selection_class": "LOW_ACTIVITY",
            "selected_date": "2024-02-03",
            "selection_rank": 1,
            "selected": True,
        },
        "selection.json",
    )
    assert result["start_utc"] == "2024-02-03T00:00:00+00:00"
    assert result["stop_utc"] == "2024-02-04T00:00:00+00:00"
    assert result["control_class"] == "LOW_ACTIVITY"
    assert result["selection_rank"] == 1


def test_record_to_interval_rejects_explicitly_unselected_row():
    assert record_to_interval(
        {
            "selection_class": "LOW_ACTIVITY",
            "date": "2024-02-03",
            "selected": False,
        },
        "daily_metrics.csv",
    ) is None


def test_discovery_requires_frozen_independence_record(tmp_path: Path):
    root = tmp_path / "selection"
    root.mkdir()
    (root / "FROZEN_INVENTORY.json").write_text(
        json.dumps(
            {
                "status": "FROZEN_BEFORE_SPACECRAFT_GATE_RETRIEVAL",
                "spacecraft_gate_outputs_used": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EventClassControlError, match="exclude spacecraft gate"):
        discover_control_intervals(root, {"LOW_ACTIVITY": 1})


def test_discovery_loads_each_required_class_without_gate_data(tmp_path: Path):
    root = tmp_path / "selection"
    root.mkdir()
    (root / "FROZEN_INVENTORY.json").write_text(
        json.dumps(
            {
                "status": "FROZEN_BEFORE_SPACECRAFT_GATE_RETRIEVAL",
                "spacecraft_gate_outputs_used": False,
            }
        ),
        encoding="utf-8",
    )
    records = [
        {"selection_class": "LOW_ACTIVITY", "date": "2024-01-03", "selected": True},
        {"selection_class": "MODERATE_ACTIVITY", "date": "2024-02-03", "selected": True},
        {"selection_class": "ISOLATED_SHOCK", "date": "2024-06-28", "selected": True},
        {"selection_class": "MILD_OR_GLANCING_STRUCTURE", "date": "2024-09-28", "selected": True},
        {"selection_class": "GANNON_DEVELOPMENT_EVENT", "date": "2024-05-11", "selected": True},
    ]
    (root / "selected_intervals.json").write_text(
        json.dumps({"selected_intervals": records}), encoding="utf-8"
    )
    required = {
        "LOW_ACTIVITY": 1,
        "MODERATE_ACTIVITY": 1,
        "ISOLATED_SHOCK": 1,
        "MILD_OR_GLANCING_STRUCTURE": 1,
        "GANNON_DEVELOPMENT_EVENT": 1,
    }
    intervals, provenance = discover_control_intervals(root, required)
    assert {item["control_class"] for item in intervals} == set(required)
    assert provenance["spacecraft_gate_outputs_used"] is False
    assert provenance["selected_counts"] == required


def test_support_metrics_report_all_frozen_radii_and_span():
    support = pd.DataFrame(
        {
            "both_independent_support_within_window": [True, True, False, True],
            "nearest_joint_radius_minutes": [1.0, 2.0, np.nan, 12.0],
            "strongest_three_spacecraft_span_minutes": [2.0, 4.0, np.nan, 3.0],
        }
    )
    result = support_scope_metrics(support)
    assert result["anchor_rows"] == 4
    assert result["joint_support_rows_within_1_minutes"] == 1
    assert result["joint_support_rows_within_2_minutes"] == 2
    assert result["joint_support_rows_within_15_minutes"] == 3
    assert result["strongest_span_le_3_minutes_rows"] == 2
    assert result["strongest_span_le_3_minutes_fraction"] == pytest.approx(2.0 / 3.0)


def test_event_class_source_contains_no_threshold_retuning():
    source = Path("historical/mag_gate_event_class_controls.py").read_text(
        encoding="utf-8"
    )
    assert "rotation_threshold_degrees=rotation_threshold" in source
    assert "magnitude_change_threshold_fraction=magnitude_threshold" in source
    assert "45.0" in Path("config/mag_gate_event_class_controls.v1.json").read_text()
    assert "0.25" in Path("config/mag_gate_event_class_controls.v1.json").read_text()
    assert "common_surface_test_completed" in source
    assert "ephemeris_propagation_test_completed" in source
