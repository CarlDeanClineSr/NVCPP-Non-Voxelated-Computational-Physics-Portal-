import json

import numpy as np
import pandas as pd
import pytest

from historical.gannon_gate_controls import (
    ControlHarnessError,
    circular_distance,
    deterministic_shift_pairs,
    load_contract,
    minute_arrays,
    summarize_null,
    support_metrics,
)


def synthetic_arrays():
    gate = np.zeros(1440, dtype=bool)
    evaluable = np.ones(1440, dtype=bool)
    score = np.full(1440, np.nan)
    return {"gate": gate, "evaluable": evaluable, "score": score}


def test_frozen_contract_rejects_changed_timing_radii(tmp_path):
    contract = {
        "contract_id": "test",
        "contract_version": "1",
        "gate_density_contract": {},
        "circular_shift_null": {
            "iterations": 100,
            "seed": 1,
            "minimum_pairwise_separation_minutes": 16,
            "support_radii_minutes": [1, 2, 3, 5, 10, 14],
        },
        "mismatched_day_null": {
            "ace_day_offsets": [3],
            "wind_day_offsets": [5],
            "pairing": "cartesian_product",
        },
        "interpretation_limits": [],
    }
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract))
    with pytest.raises(ControlHarnessError, match="timing radii changed"):
        load_contract(path)


def test_shift_pairs_are_deterministic_and_break_all_pairwise_simultaneity():
    first = deterministic_shift_pairs(
        iterations=200,
        seed=20240511,
        minimum_pairwise_separation_minutes=16,
    )
    second = deterministic_shift_pairs(
        iterations=200,
        seed=20240511,
        minimum_pairwise_separation_minutes=16,
    )
    assert first == second
    assert len(set(first)) == 200
    for ace_shift, wind_shift in first:
        assert circular_distance(ace_shift) >= 16
        assert circular_distance(wind_shift) >= 16
        assert circular_distance(ace_shift - wind_shift) >= 16


def test_minute_arrays_preserve_missing_minutes_as_nonevaluable():
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2024-05-11T00:00:00Z",
                    "2024-05-11T00:02:00Z",
                ],
                utc=True,
            ),
            "gate_pass": [False, True],
            "gate_score": [0.2, 2.0],
            "exact_previous_minute": [False, False],
            "native_samples": [60, 60],
        }
    )
    result = minute_arrays(
        frame,
        day_start=pd.Timestamp("2024-05-11T00:00:00Z"),
    )
    assert not result["evaluable"][1]
    assert not result["gate"][1]
    assert result["gate"][2]
    assert result["score"][2] == pytest.approx(2.0)


def test_support_metrics_keep_frozen_radii_and_candidate_offsets():
    dscovr = synthetic_arrays()
    ace = synthetic_arrays()
    wind = synthetic_arrays()

    candidate = 659
    dscovr["gate"][candidate] = True
    dscovr["evaluable"][candidate] = True
    dscovr["score"][candidate] = 2.0

    ace["gate"][candidate] = True
    ace["score"][candidate] = 1.1
    ace["gate"][candidate - 2] = True
    ace["score"][candidate - 2] = 4.0

    wind["gate"][candidate - 2] = True
    wind["score"][candidate - 2] = 1.2
    wind["gate"][candidate - 3] = True
    wind["score"][candidate - 3] = 5.0

    result = support_metrics(
        dscovr_gate=dscovr["gate"],
        dscovr_evaluable=dscovr["evaluable"],
        ace_gate=ace["gate"],
        ace_score=ace["score"],
        wind_gate=wind["gate"],
        wind_score=wind["score"],
        candidate_minute=candidate,
        support_radii=[1, 2, 3, 5, 10, 15],
        half_window=15,
        strongest_span_threshold=3,
    )
    assert result["dscovr_gate_anchor_rows"] == 1
    assert result["support_fractions"]["1"] == 0.0
    assert result["support_fractions"]["2"] == 1.0
    assert result["candidate"]["ace_nearest_offset_minutes"] == 0
    assert result["candidate"]["wind_nearest_offset_minutes"] == -2
    assert result["candidate"]["nearest_joint_radius_minutes"] == 2
    assert result["candidate"]["ace_strongest_offset_minutes"] == -2
    assert result["candidate"]["wind_strongest_offset_minutes"] == -3
    assert result["candidate"]["strongest_three_spacecraft_span_minutes"] == 3


def test_null_summary_keeps_no_support_controls_in_tail_denominator():
    observed = {
        "dscovr_gate_anchor_rows": 1,
        "support_fractions": {
            "1": 1.0,
            "2": 1.0,
            "3": 1.0,
            "5": 1.0,
            "10": 1.0,
            "15": 1.0,
        },
        "strongest_span_fraction": 1.0,
        "candidate": {
            "nearest_joint_radius_minutes": 2,
            "strongest_three_spacecraft_span_minutes": 3,
        },
    }
    controls = pd.DataFrame(
        {
            "dscovr_gate_anchor_rows": [1, 1, 1, 1],
            "strongest_span_fraction": [0.0, 0.0, 0.0, 0.0],
            "candidate_nearest_joint_radius_minutes": [1.0, np.nan, np.nan, 4.0],
            "candidate_strongest_three_spacecraft_span_minutes": [
                2.0,
                np.nan,
                np.nan,
                8.0,
            ],
            **{
                f"joint_support_fraction_within_{radius}_minutes": [
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ]
                for radius in (1, 2, 3, 5, 10, 15)
            },
        }
    )
    summary = summarize_null(observed, controls)
    nearest = summary["candidate_nearest_joint_radius_minutes"]
    assert nearest["controls"] == 4
    assert nearest["finite_controls"] == 2
    assert nearest["controls_without_joint_support"] == 2
    assert nearest["equal_or_more_extreme_controls"] == 1
    assert nearest["empirical_equal_or_more_extreme_fraction"] == pytest.approx(0.25)
    assert nearest["plus_one_tail_estimator"] == pytest.approx(0.4)


def test_circular_roll_preserves_gate_density_and_score_pairing():
    arrays = synthetic_arrays()
    arrays["gate"][[10, 11, 40]] = True
    arrays["score"][[10, 11, 40]] = [1.0, 2.0, 3.0]
    shift = 137
    shifted_gate = np.roll(arrays["gate"], shift)
    shifted_score = np.roll(arrays["score"], shift)
    assert shifted_gate.sum() == arrays["gate"].sum()
    assert shifted_score[(10 + shift) % 1440] == pytest.approx(1.0)
    assert shifted_score[(40 + shift) % 1440] == pytest.approx(3.0)
