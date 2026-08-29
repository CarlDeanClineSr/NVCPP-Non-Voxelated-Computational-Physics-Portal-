import json
from pathlib import Path


def load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_v2_detector_matches_v1_gate_contract():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    v1 = load_json("config/gannon_gate_density.v1.json")

    gate = v1["gate"]
    detector = v2["detector"]
    assert detector["coordinate_frame"] == gate["coordinate_frame"] == "GSE"
    assert detector["canonical_cadence_seconds"] == gate["canonical_cadence_seconds"] == 60
    assert detector["required_previous_offset_seconds"] == gate["required_previous_offset_seconds"] == 60
    assert detector["rotation_threshold_degrees"] == gate["rotation_threshold_degrees"] == 45.0
    assert detector["magnitude_change_threshold_fraction"] == gate["magnitude_change_threshold_fraction"] == 0.25
    assert detector["logical_operator"] == gate["logical_operator"] == "OR"
    assert detector["interpolation_allowed"] is False
    assert detector["forward_fill_allowed"] is False
    assert detector["timing_radii_minutes"] == [1, 2, 3, 5, 10, 15]


def test_v2_primary_clustering_definition_is_frozen_and_not_claimed_blind_in_v1():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    primary = v2["primary_clustering_hypothesis"]

    assert primary["origin"] == "GANNON_V1_INSPIRED_NOT_BLIND_IN_V1"
    assert primary["frozen_before_holdout_scoring"] is True
    assert primary["logical_operator"] == "AND"
    assert primary["nearest_joint_support_radius_minutes_lte"] == 2
    assert primary["strongest_three_spacecraft_span_minutes_lte"] == 3
    assert primary["retuning_after_holdout_inspection_allowed"] is False
    assert primary["development_observation_offsets_minutes"] == {
        "DSCOVR": 0,
        "ACE": -2,
        "WIND": -3,
    }


def test_v2_requires_prospective_registry_and_visible_failed_intervals():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    selection = v2["holdout_selection"]
    denominator = v2["denominator_policy"]

    assert selection["selection_must_complete_before_spacecraft_mag_retrieval"] is True
    assert selection["selection_may_use_gate_outputs"] is False
    assert selection["selection_may_use_candidate_scores"] is False
    assert selection["selection_may_use_clustering_outputs"] is False
    assert selection["minimum_intervals_per_class"] >= 10
    assert set(selection["required_classes"]) == {
        "QUIET_SOLAR_WIND",
        "MODERATE_VARIABILITY",
        "ISOLATED_SHOCK_OR_SHEATH",
        "COMPLEX_INTERACTING_EJECTA",
    }
    assert all(
        item["minimum_intervals"] >= 10
        for item in selection["required_classes"].values()
    )
    assert denominator["required_failure_state"] == "INCOMPLETE_MULTIPOINT"
    assert "INCOMPLETE_MULTIPOINT_count_by_class" in denominator["report"]


def test_v2_hard_nulls_keep_real_serial_structure():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    hard_nulls = v2["hard_nulls"]

    assert hard_nulls["circular_shift"]["preserve_real_serial_structure"] is True
    assert hard_nulls["circular_shift"]["minimum_shift_beyond_support_window_minutes"] == 16
    assert hard_nulls["circular_shift"]["primary_statistic_recomputed_each_realization"] is True
    assert hard_nulls["circular_shift"]["independent_minute_noise_prohibited_as_primary_null"] is True
    assert hard_nulls["mismatched_day"]["preserve_real_serial_structure"] is True
    assert hard_nulls["mismatched_day"]["physical_simultaneity_broken_by_construction"] is True
    assert hard_nulls["mismatched_day"]["primary_statistic_recomputed_each_pairing"] is True


def test_v2_geometry_stays_closed_even_for_favorable_null_result():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    geometry = v2["geometry_gate"]

    assert geometry["default_state"] == "CLOSED"
    assert geometry["common_surface_claim_allowed"] is False
    assert geometry["physical_class_claim_allowed"] is False
    assert geometry["propagation_claim_allowed"] is False
    assert geometry["automatic_open_on_favorable_null_result"] is False


def test_v2_forbids_radius_retuning():
    v2 = load_json("config/gannon_holdout_v2.preregistered.json")
    prohibited = "\n".join(v2["prohibited_actions"])
    assert "nearest-support primary radius from 2 minutes" in prohibited
    assert "strongest-span primary radius from 3 minutes" in prohibited
    assert "retune 45 degree threshold" in prohibited
    assert "retune 25 percent threshold" in prohibited
