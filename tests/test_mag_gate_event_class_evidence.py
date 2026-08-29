import json
from pathlib import Path


EXPECTED_CLASSES = {
    "LOW_ACTIVITY": 1,
    "MODERATE_ACTIVITY": 1,
    "ISOLATED_SHOCK": 1,
    "MILD_OR_GLANCING_STRUCTURE": 1,
    "GANNON_DEVELOPMENT_EVENT": 1,
}


def test_event_class_control_evidence_is_frozen_before_merge():
    root = Path("provenance/mag_gate_event_class_controls_v1")
    inventory_path = root / "FROZEN_INVENTORY.json"
    result_path = root / "COMPACT_RESULT.json"
    assert inventory_path.is_file(), "event-class controls have not been frozen"
    assert result_path.is_file(), "compact event-class result is absent"

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert inventory["status"] == "EVENT_CLASS_CONTROL_DISTRIBUTIONS_FROZEN"
    assert inventory["detector_thresholds_changed"] is False
    assert inventory["gannon_interpretation_reopened"] is False
    assert inventory["raw_telemetry_committed"] is False

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "SUCCESS"
    assert result["successful_interval_counts"] >= EXPECTED_CLASSES
    assert result["failed_intervals"] == []
    assert result["event_interpretation_reopened"] is False
    assert result["geometry_blocked"] is True
    assert result["physical_mechanism_classified"] is False
    assert result["hard_null_combination_completed"] is False
    assert result["common_surface_test_completed"] is False
    assert result["ephemeris_propagation_test_completed"] is False

    gate = result["frozen_gate"]
    assert gate["coordinate_frame"] == "GSE"
    assert gate["canonical_cadence_seconds"] == 60
    assert gate["required_previous_offset_seconds"] == 60
    assert gate["rotation_threshold_degrees"] == 45.0
    assert gate["magnitude_change_threshold_fraction"] == 0.25
    assert gate["logical_operator"] == "OR"
    assert gate["timing_radii_minutes"] == [1, 2, 3, 5, 10, 15]


def test_each_interval_preserves_bounded_claims_and_source_hashes():
    result = json.loads(
        Path("provenance/mag_gate_event_class_controls_v1/COMPACT_RESULT.json")
        .read_text(encoding="utf-8")
    )
    assert len(result["interval_results"]) >= sum(EXPECTED_CLASSES.values())
    for interval in result["interval_results"]:
        assert interval["claims"] == {
            "physical_mechanism_classified": False,
            "common_surface_test_completed": False,
            "ephemeris_propagation_test_completed": False,
        }
        assert set(interval["source_hashes"]) == {"DSCOVR", "ACE", "WIND"}
        assert interval["gate"]["rotation_threshold_degrees"] == 45.0
        assert interval["gate"]["magnitude_change_threshold_fraction"] == 0.25
