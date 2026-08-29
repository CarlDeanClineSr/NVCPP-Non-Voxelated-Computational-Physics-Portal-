import json

import numpy as np
import pandas as pd
import pytest

from historical.mag_gate_control_harness import (
    ControlHarnessError,
    add_one_lower_tail,
    add_one_upper_tail,
    assessment,
    circular_distance_minutes,
    generate_shift_pairs,
    load_contract,
    rebase_to_development_day,
    shifted_arrays,
    support_metrics,
)


def minimal_arrays():
    return {
        "gate": np.zeros(1440, dtype=bool),
        "score": np.zeros(1440, dtype=float),
        "evaluable": np.ones(1440, dtype=bool),
        "present": np.ones(1440, dtype=bool),
    }


def test_support_metrics_respects_frozen_radii_and_candidate_offsets():
    dscovr = minimal_arrays()
    ace = minimal_arrays()
    wind = minimal_arrays()
    candidate = 100

    dscovr["gate"][candidate] = True
    dscovr["score"][candidate] = 2.0
    ace["gate"][candidate] = True
    ace["score"][candidate] = 1.5
    wind["gate"][candidate + 2] = True
    wind["score"][candidate + 2] = 3.0

    metrics, support = support_metrics(
        dscovr=dscovr,
        ace=ace,
        wind=wind,
        candidate_index=candidate,
        radii=[1, 2, 3, 5, 10, 15],
        half_window=15,
    )

    assert len(support) == 1
    assert metrics["joint_support_fraction_within_1_minutes"] == 0.0
    assert metrics["joint_support_fraction_within_2_minutes"] == 1.0
    assert metrics["strongest_span_fraction_within_3_minutes"] == 1.0
    assert metrics["candidate"]["ace_nearest_offset_minutes"] == 0
    assert metrics["candidate"]["wind_nearest_offset_minutes"] == 2
    assert metrics["candidate"]["nearest_joint_radius_minutes"] == 2
    assert metrics["candidate"][
        "strongest_three_spacecraft_span_minutes"
    ] == 2


def test_circular_shift_pairs_break_all_three_pairwise_simultaneities():
    pairs = generate_shift_pairs(
        iterations=250,
        seed=20240511,
        period=1440,
        minimum_pairwise_separation=16,
    )
    assert pairs == generate_shift_pairs(
        iterations=250,
        seed=20240511,
        period=1440,
        minimum_pairwise_separation=16,
    )
    assert len(pairs) == 250
    for ace_shift, wind_shift in pairs:
        assert circular_distance_minutes(ace_shift, 0) >= 16
        assert circular_distance_minutes(wind_shift, 0) >= 16
        assert circular_distance_minutes(ace_shift, wind_shift) >= 16


def test_shift_preserves_gate_count_and_score_values():
    source = minimal_arrays()
    source["gate"][[10, 11, 40]] = True
    source["score"][[10, 11, 40]] = [1.0, 2.0, 3.0]

    result = shifted_arrays(source, 137)

    assert int(result["gate"].sum()) == 3
    assert sorted(result["score"][result["gate"]].tolist()) == [1.0, 2.0, 3.0]
    assert int(result["evaluable"].sum()) == int(source["evaluable"].sum())


def test_empirical_tail_helpers_use_add_one_and_keep_missing_as_no_support():
    upper = add_one_upper_tail(pd.Series([0.1, 0.2, 0.3]), 0.25)
    assert upper["exceedance_rows"] == 1
    assert upper["add_one_tail_fraction"] == pytest.approx(0.5)

    lower = add_one_lower_tail(
        pd.Series([1.0, np.nan, 3.0, 4.0]), 2.0, total_rows=4
    )
    assert lower["supporting_rows_at_or_below_observed"] == 1
    assert lower["add_one_tail_fraction"] == pytest.approx(0.4)


def test_rebase_preserves_minute_of_day_and_original_time():
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-05-18T01:02:00Z", "2024-05-18T23:59:00Z"],
                utc=True,
            ),
            "gate_pass": [False, True],
            "gate_score": [0.0, 2.0],
            "exact_previous_minute": [True, True],
        }
    )
    result = rebase_to_development_day(
        frame,
        development_start=pd.Timestamp("2024-05-11T00:00:00Z"),
    )
    assert result["time"].dt.strftime("%Y-%m-%dT%H:%MZ").tolist() == [
        "2024-05-11T01:02Z",
        "2024-05-11T23:59Z",
    ]
    assert result["original_time_utc"].dt.day.tolist() == [18, 18]


def test_assessment_does_not_open_geometry_before_event_class_controls():
    comparisons = {
        key: {"add_one_tail_fraction": 0.01}
        for key in (
            "joint_support_fraction_within_2_minutes",
            "strongest_span_fraction_within_3_minutes",
            "candidate_nearest_joint_radius_minutes",
            "candidate_strongest_three_spacecraft_span_minutes",
        )
    }
    result = assessment(
        circular_summary={"comparisons": comparisons},
        mismatched_summary={
            "comparisons": {
                key: {"add_one_tail_fraction": 0.08}
                for key in comparisons
            }
        },
        decision_policy={
            "circular_add_one_tail_fraction_threshold": 0.05,
            "mismatched_add_one_tail_fraction_threshold": 0.10,
        },
        event_class_status=(
            "PENDING_INDEPENDENT_SELECTION_BEFORE_MAG_RETRIEVAL"
        ),
    )
    assert result["hard_null_state"] == (
        "SHORT_RADIUS_CLUSTER_EXCEEDS_CURRENT_HARD_NULLS"
    )
    assert result["geometry_stage_state"] == (
        "BLOCKED_PENDING_EVENT_CLASS_CONTROLS"
    )
    assert result["common_surface_claim_allowed"] is False
    assert result["threshold_retuning_allowed"] is False


def test_contract_rejects_threshold_drift(tmp_path):
    gate = {
        "contract_id": "NVCPP-GANNON-MAG-GATE-DENSITY-v1",
        "contract_version": "1.0.0",
        "analysis_window": {
            "start_utc": "2024-05-11T00:00:00Z",
            "stop_utc": "2024-05-12T00:00:00Z",
            "candidate_utc": "2024-05-11T10:59:00Z",
        },
        "gate": {
            "coordinate_frame": "GSE",
            "canonical_cadence_seconds": 60,
            "required_previous_offset_seconds": 60,
            "rotation_threshold_degrees": 45.0,
            "magnitude_change_threshold_fraction": 0.25,
            "logical_operator": "OR",
            "support_half_window_minutes": 15,
        },
    }
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(gate), encoding="utf-8")

    control = {
        "contract_id": "NVCPP-MAG-GATE-CONTROL-HARNESS-v1",
        "contract_version": "1.0.0",
        "development_gate_contract": {
            "path": str(gate_path),
            "contract_id": gate["contract_id"],
            "contract_version": gate["contract_version"],
        },
        "gate": {**gate["gate"], "rotation_threshold_degrees": 46.0},
        "timing_radii_minutes": [1, 2, 3, 5, 10, 15],
        "circular_shift_null": {
            "iterations": 100,
            "seed": 1,
            "minimum_pairwise_separation_minutes": 16,
        },
        "mismatched_day_null": {
            "ace_day_offsets": [-7, 7],
            "wind_day_offsets": [-7, 7],
        },
        "event_class_controls": {
            "status": "PENDING_INDEPENDENT_SELECTION_BEFORE_MAG_RETRIEVAL"
        },
        "decision_policy": {},
        "interpretation_limits": [],
    }
    control_path = tmp_path / "control.json"
    control_path.write_text(json.dumps(control), encoding="utf-8")

    with pytest.raises(ControlHarnessError, match="differs from frozen"):
        load_contract(control_path)
