import numpy as np
import pandas as pd
import pytest

from historical.gannon_gate_density import (
    add_exact_minute_diagnostics,
    cluster_gate_events,
    gate_choice,
    prevalence_summary,
    standardize_dscovr,
)


def base_frame(times):
    return pd.DataFrame(
        {
            "time": pd.to_datetime(times, utc=True),
            "bx_gse_nT": [1.0, 1.0, -1.0],
            "by_gse_nT": [0.0, 0.0, 0.0],
            "bz_gse_nT": [0.0, 0.0, 0.0],
            "B_mag_nT": [1.0, 1.0, 1.0],
            "native_samples": [60, 60, 60],
        }
    )


def test_exact_minute_diagnostics_refuse_to_bridge_gap():
    frame = base_frame(
        [
            "2024-05-11T00:00:00Z",
            "2024-05-11T00:01:00Z",
            "2024-05-11T00:03:00Z",
        ]
    )
    result = add_exact_minute_diagnostics(
        frame,
        rotation_threshold_degrees=45.0,
        magnitude_change_threshold_fraction=0.25,
    )
    assert result.loc[1, "exact_previous_minute"]
    assert not result.loc[2, "exact_previous_minute"]
    assert np.isnan(
        result.loc[2, "rotation_from_exact_previous_minute_degrees"]
    )
    assert np.isnan(
        result.loc[
            2, "magnitude_change_from_exact_previous_minute_fraction"
        ]
    )
    assert not result.loc[2, "gate_pass"]


def test_standardize_dscovr_preserves_real_native_sample_count():
    frame = pd.DataFrame(
        {
            "EPOCH": pd.to_datetime(
                ["2024-05-11T10:58:00Z", "2024-05-11T10:59:00Z"],
                utc=True,
            ),
            "BX_(GSE)": [1.0, -1.0],
            "BY_(GSE)": [0.0, 0.0],
            "BZ_(GSE)": [0.0, 0.0],
            "native_sample_count": [60, 59],
            "native_coverage_fraction": [1.0, 59.0 / 60.0],
        }
    )
    result = standardize_dscovr(
        frame,
        rotation_threshold_degrees=45.0,
        magnitude_change_threshold_fraction=0.25,
    )
    assert result["native_samples"].tolist() == [60, 59]
    assert result.loc[
        1, "rotation_from_exact_previous_minute_degrees"
    ] == pytest.approx(180.0)


def test_contiguous_clusters_do_not_bridge_missing_minute():
    events = pd.DataFrame(
        {
            "mission": ["DSCOVR"] * 4,
            "time": pd.to_datetime(
                [
                    "2024-05-11T00:01:00Z",
                    "2024-05-11T00:02:00Z",
                    "2024-05-11T00:04:00Z",
                    "2024-05-11T00:05:00Z",
                ],
                utc=True,
            ),
        }
    )
    result = cluster_gate_events(events)
    assert result["gate_cluster_id"].tolist() == [1, 1, 2, 2]
    assert result["gate_cluster_rows"].tolist() == [2, 2, 2, 2]


def test_gate_choice_separates_nearest_from_strongest():
    center = pd.Timestamp("2024-05-11T10:59:00Z")
    gates = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-05-11T10:58:00Z", "2024-05-11T11:10:00Z"],
                utc=True,
            ),
            "gate_score": [1.1, 8.0],
            "rotation_from_exact_previous_minute_degrees": [49.5, 80.0],
            "magnitude_change_from_exact_previous_minute_fraction": [0.1, 2.0],
        }
    )
    nearest = gate_choice(
        gates, center=center, half_window_minutes=15, mode="nearest"
    )
    strongest = gate_choice(
        gates, center=center, half_window_minutes=15, mode="strongest"
    )
    assert nearest["offset_minutes"] == -1.0
    assert strongest["offset_minutes"] == 11.0


def test_prevalence_reports_fraction_without_calling_it_a_p_value():
    support = pd.DataFrame(
        {
            "anchor_time_utc": [
                "2024-05-11T10:58:00+00:00",
                "2024-05-11T10:59:00+00:00",
                "2024-05-11T11:00:00+00:00",
            ],
            "dscovr_gate_pass": [False, True, True],
            "dscovr_gate_score": [0.1, 2.0, 1.2],
            "both_independent_support_within_window": [False, True, True],
            "nearest_joint_radius_minutes": [np.nan, 2.0, 10.0],
            "ace_nearest_offset_minutes": [np.nan, 0.0, 4.0],
            "wind_nearest_offset_minutes": [np.nan, -2.0, 10.0],
            "strongest_three_spacecraft_span_minutes": [np.nan, 3.0, 20.0],
        }
    )
    result = prevalence_summary(
        support, candidate=pd.Timestamp("2024-05-11T10:59:00Z")
    )
    assert result["all_evaluable_dscovr_anchors"][
        "joint_support_fraction_within_15_minutes"
    ] == pytest.approx(2.0 / 3.0)
    assert result["dscovr_gate_anchors"][
        "joint_support_fraction_within_3_minutes"
    ] == pytest.approx(0.5)
    assert "not an independent null" in result["meaning"]
