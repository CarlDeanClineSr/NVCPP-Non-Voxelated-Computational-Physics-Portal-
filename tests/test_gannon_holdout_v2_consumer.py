import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from historical.gannon_holdout_v2_consumer import (
    canonical_window,
    deterministic_mismatched_triplets,
    minute_arrays,
    plan_matrix,
    score_support,
    verify_registry,
)

CONTRACT = Path("config/gannon_holdout_v2_consumer.v1.json")


def load_contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_consumer_accepts_only_the_committed_amended_registry():
    contract, registry, inventory = verify_registry(CONTRACT)
    assert contract["registry"]["expected_file_sha256"] == (
        "8c1510e026aa68dca21d42181bbe8a2fe1876a9738426be1378605f8bfe947af"
    )
    assert contract["registry"]["expected_content_sha256"] == (
        "d6db5682347b958d2f3f9b26c404563c6cb97f1c514525ae30c7c797fa2b8e7b"
    )
    assert Counter(item["class"] for item in registry["intervals"]) == {
        "QUIET_SOLAR_WIND": 12,
        "MODERATE_VARIABILITY": 12,
        "ISOLATED_SHOCK_OR_SHEATH": 12,
        "COMPLEX_INTERACTING_EJECTA": 7,
    }
    assert len(registry["intervals"]) == 43
    assert inventory["spacecraft_mag_retrieved"] is False


def test_consumer_does_not_change_detector_or_primary_radii():
    contract = load_contract()
    assert contract["detector"] == {
        "coordinate_frame": "GSE",
        "canonical_cadence_seconds": 60,
        "required_previous_offset_seconds": 60,
        "rotation_threshold_degrees": 45.0,
        "magnitude_change_threshold_fraction": 0.25,
        "logical_operator": "OR",
        "timing_radii_minutes": [1, 2, 3, 5, 10, 15],
        "interpolation": "NONE",
        "forward_fill": "NONE",
    }
    primary = contract["primary_clustering_hypothesis"]
    assert primary["nearest_joint_support_radius_minutes_lte"] == 2
    assert primary["strongest_three_spacecraft_span_minutes_lte"] == 3
    assert primary["retuning_allowed"] is False



def test_source_products_are_pinned_before_holdout_opening():
    contract = load_contract()
    sources = contract["source_products"]
    assert sources["DSCOVR"]["dataset_id"] == "DSCOVR_H0_MAG"
    assert sources["DSCOVR"]["variables"] == ["B1GSE"]
    assert sources["ACE"]["dataset_id"] == "AC_H0_MFI"
    assert sources["ACE"]["parameters"] == [
        "Magnitude",
        "BGSEc",
        "SC_pos_GSE",
    ]
    assert sources["WIND"]["dataset_id"] == "WI_H0_MFI"
    assert sources["WIND"]["variables"] == ["B3GSE", "B3F1"]
    assert all(item["coordinate_frame"] == "GSE" for item in sources.values())

def test_half_minute_registry_boundary_maps_to_exact_1440_minute_grid():
    contract = load_contract()
    interval = {
        "interval_id": "example",
        "start_utc": "2023-12-15T21:43:30Z",
        "stop_utc": "2023-12-16T21:43:30Z",
    }
    window = canonical_window(interval, contract)
    assert window["grid_start"] == pd.Timestamp("2023-12-15T21:44:00Z")
    assert window["grid_stop"] == pd.Timestamp("2023-12-16T21:44:00Z")
    assert window["retrieval_start"] == pd.Timestamp("2023-12-15T21:43:00Z")
    assert int((window["grid_stop"] - window["grid_start"]).total_seconds() / 60) == 1440


def synthetic_bundle(*, gate_minutes=(), scores=None, missing_minutes=()):
    length = 1440
    present = np.ones(length, dtype=bool)
    present[list(missing_minutes)] = False
    evaluable = present.copy()
    gate = np.zeros(length, dtype=bool)
    gate[list(gate_minutes)] = True
    gate &= present
    score = np.zeros(length, dtype=float)
    if scores:
        for minute, value in scores.items():
            score[minute] = value
    score[~present] = np.nan
    return {
        "present": present,
        "evaluable": evaluable,
        "gate": gate,
        "score": score,
    }


def test_primary_statistic_requires_both_frozen_conditions():
    dscovr = synthetic_bundle(gate_minutes=[600], scores={600: 2.0})
    ace = synthetic_bundle(gate_minutes=[601], scores={601: 3.0})
    wind = synthetic_bundle(gate_minutes=[598], scores={598: 4.0})
    table, summary, eligible, primary = score_support(
        dscovr=dscovr,
        ace=ace,
        wind=wind,
        support_half_window_minutes=15,
        minimum_independent_window_coverage_fraction=0.8,
        timing_radii_minutes=[1, 2, 3, 5, 10, 15],
        nearest_primary_radius_minutes=2,
        strongest_primary_span_minutes=3,
    )
    assert len(table) == 1
    assert table.iloc[0]["nearest_joint_radius_minutes"] == 2
    assert table.iloc[0]["strongest_three_spacecraft_span_minutes"] == 3
    assert bool(table.iloc[0]["primary_clustering_pass"]) is True
    assert summary["primary_clustering_pass_rows"] == 1
    assert bool(eligible[600]) is True
    assert bool(primary[600]) is True

    wind_far = synthetic_bundle(gate_minutes=[597], scores={597: 4.0})
    table_far, summary_far, _, _ = score_support(
        dscovr=dscovr,
        ace=ace,
        wind=wind_far,
        support_half_window_minutes=15,
        minimum_independent_window_coverage_fraction=0.8,
        timing_radii_minutes=[1, 2, 3, 5, 10, 15],
        nearest_primary_radius_minutes=2,
        strongest_primary_span_minutes=3,
    )
    assert table_far.iloc[0]["nearest_joint_radius_minutes"] == 3
    assert bool(table_far.iloc[0]["primary_clustering_pass"]) is False
    assert summary_far["primary_clustering_pass_rows"] == 0


def test_missing_independent_window_is_not_scored_as_no_support():
    dscovr = synthetic_bundle(gate_minutes=[600], scores={600: 2.0})
    missing = range(585, 616)
    ace = synthetic_bundle(gate_minutes=[600], scores={600: 2.0}, missing_minutes=missing)
    wind = synthetic_bundle(gate_minutes=[600], scores={600: 2.0})
    table, summary, _, _ = score_support(
        dscovr=dscovr,
        ace=ace,
        wind=wind,
        support_half_window_minutes=15,
        minimum_independent_window_coverage_fraction=0.8,
        timing_radii_minutes=[1, 2, 3, 5, 10, 15],
        nearest_primary_radius_minutes=2,
        strongest_primary_span_minutes=3,
    )
    assert bool(table.iloc[0]["support_evaluable"]) is False
    assert summary["support_evaluable_anchors"] == 0
    assert summary["coverage_excluded_anchors"] == 1


def test_minute_arrays_fail_closed_on_nonminute_rows():
    frame = pd.DataFrame(
        {
            "time": ["2020-01-01T00:00:30Z"],
            "gate_pass": [False],
            "gate_score": [0.0],
            "exact_previous_minute": [False],
        }
    )
    try:
        minute_arrays(frame, grid_start=pd.Timestamp("2020-01-01T00:00:00Z"))
    except Exception as exc:
        assert "minute grid" in str(exc)
    else:
        raise AssertionError("non-minute row was admitted")


def test_mismatched_triplets_are_distinct_and_separated():
    candidates = [
        {
            "interval_id": f"I{index}",
            "registered_start_utc": f"2020-{month:02d}-01T00:00:00Z",
            "mission_era_tag": "ERA",
        }
        for index, month in enumerate((1, 3, 5, 7, 9, 11), start=1)
    ]
    triplets = deterministic_mismatched_triplets(
        candidates=candidates,
        iterations=25,
        seed=7,
        minimum_date_separation_days=7,
    )
    assert triplets
    for dscovr, ace, wind in triplets:
        assert len({dscovr["interval_id"], ace["interval_id"], wind["interval_id"]}) == 3


def test_plan_matrix_contains_every_registry_row_once():
    matrix = plan_matrix(contract_path=CONTRACT, output=None)
    ids = [item["interval_id"] for item in matrix["include"]]
    assert len(ids) == len(set(ids)) == 43


def test_execution_workflow_is_manual_only_and_has_no_development_note():
    workflow = Path(".github/workflows/gannon_holdout_v2_consumer.yml").read_text(
        encoding="utf-8"
    )
    source = Path("historical/gannon_holdout_v2_consumer.py").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "\n  push:" not in workflow
    assert "development_candidate_utc" not in workflow
    assert "development_candidate_utc" not in source
