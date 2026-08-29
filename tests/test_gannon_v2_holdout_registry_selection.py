from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from historical.select_gannon_v2_holdout_registry import (
    build_shock_intervals,
    interval_common,
    load_contract,
    parse_gfz_kp,
    parse_ipshocks,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
SELECTION_CONTRACT = ROOT / "config" / "gannon_holdout_v2_selection.v1.json"
PREREGISTRATION = ROOT / "config" / "gannon_holdout_v2.preregistered.json"
SELECTOR_SOURCE = ROOT / "historical" / "select_gannon_v2_holdout_registry.py"


def test_selection_contract_preserves_preregistered_detector_boundary() -> None:
    selection = load_contract(SELECTION_CONTRACT)
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))

    assert selection["status"] == "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL"
    assert prereg["status"] == "PREREGISTERED_BEFORE_HOLDOUT_MAG_INSPECTION"
    assert selection["parent_preregistration"]["contract_id"] == prereg["contract_id"]
    assert selection["global_rules"]["spacecraft_mag_access_allowed"] is False
    assert selection["global_rules"]["gate_output_access_allowed"] is False
    assert selection["global_rules"]["candidate_score_access_allowed"] is False
    assert selection["global_rules"]["clustering_output_access_allowed"] is False
    assert selection["global_rules"]["replacement_after_mag_inspection_allowed"] is False
    assert selection["global_rules"]["excluded_years"] == [2024]
    assert selection["global_rules"]["catalog_coverage_stop_utc_exclusive"] == "2026-07-01T00:00:00Z"
    late = next(era for era in selection["mission_eras"] if era["id"] == "ERA_DSCOVR_ACE_WIND_LATE_2025_2026")
    assert late["stop_utc"] == "2026-07-01T00:00:00Z"

    assert prereg["detector"] == {
        "coordinate_frame": "GSE",
        "canonical_cadence_seconds": 60,
        "required_previous_offset_seconds": 60,
        "rotation_threshold_degrees": 45.0,
        "magnitude_change_threshold_fraction": 0.25,
        "logical_operator": "OR",
        "interpolation_allowed": False,
        "forward_fill_allowed": False,
        "timing_radii_minutes": [1, 2, 3, 5, 10, 15],
    }
    primary = prereg["primary_clustering_hypothesis"]
    assert primary["origin"] == "GANNON_V1_INSPIRED_NOT_BLIND_IN_V1"
    assert primary["nearest_joint_support_radius_minutes_lte"] == 2
    assert primary["strongest_three_spacecraft_span_minutes_lte"] == 3
    assert primary["retuning_after_holdout_inspection_allowed"] is False


def test_selection_targets_are_ten_per_class_and_span_declared_eras() -> None:
    contract = load_contract(SELECTION_CONTRACT)
    assert set(contract["classes"]) == {
        "QUIET_SOLAR_WIND",
        "MODERATE_VARIABILITY",
        "ISOLATED_SHOCK_OR_SHEATH",
        "COMPLEX_INTERACTING_EJECTA",
    }
    for policy in contract["classes"].values():
        assert policy["target_count"] == 10
        assert sum(policy["era_targets"].values()) == 10
        assert sum(value > 0 for value in policy["era_targets"].values()) >= 2


def test_parse_gfz_kp_uses_eight_three_hour_values() -> None:
    raw = (
        "# synthetic GFZ-format row\n"
        "2023 01 02 2023.004 0 0 0 0.000 0.333 0.667 1.000 1.333 1.667 2.000 2.000 0 0 0\n"
    ).encode()
    frame = parse_gfz_kp(raw)
    assert len(frame) == 1
    assert frame.loc[0, "kp_values"] == pytest.approx(
        [0.0, 0.333, 0.667, 1.0, 1.333, 1.667, 2.0, 2.0]
    )
    assert frame.loc[0, "kp_max"] == pytest.approx(2.0)


def test_parse_ipshocks_clusters_catalog_rows_without_gate_data() -> None:
    raw = (
        "Year,Month (1-12),Day (1-31),Hour (0-23),Minute (0-59),Second (0-59),Spacecraft,Shock Type\n"
        "2021,1,1,0,0,0,Wind,FF\n"
        "2021,1,1,0,20,0,ACE,FF\n"
        "2021,1,1,6,0,0,Wind,FF\n"
    ).encode()
    clusters = parse_ipshocks(raw, cluster_minutes=90)
    assert len(clusters) == 2
    assert clusters.loc[0, "named_spacecraft"] == ["ACE", "WIND"]
    assert clusters.loc[0, "catalog_rows"] == 2
    assert clusters.loc[0, "nearest_ipshock_gap_hours"] > 5.0


def test_isolated_shock_ignores_its_own_nearby_richardson_cane_entry() -> None:
    shocks = pd.DataFrame(
        {
            "cluster_id": [1],
            "reference_time_utc": pd.to_datetime(["2021-03-01T12:00:00Z"], utc=True),
            "first_time_utc": pd.to_datetime(["2021-03-01T11:55:00Z"], utc=True),
            "last_time_utc": pd.to_datetime(["2021-03-01T12:05:00Z"], utc=True),
            "catalog_rows": [2],
            "named_spacecraft": [["ACE", "WIND"]],
            "all_source_labels": [["ACE", "WIND"]],
            "shock_types": [["FF"]],
            "catalog_row_numbers": [[10, 11]],
            "nearest_ipshock_gap_hours": [120.0],
        }
    )
    rc = pd.DataFrame(
        {
            "disturbance_time_utc": pd.to_datetime(
                ["2021-03-01T14:00:00Z", "2021-03-06T12:00:00Z"], utc=True
            )
        }
    )
    contract = {
        "classes": {
            "ISOLATED_SHOCK_OR_SHEATH": {
                "era_targets": {"TEST_ERA": 1},
                "eligibility": "synthetic independent-catalog rule",
            }
        },
        "global_rules": {
            "excluded_years": [2024],
            "minimum_spacing_days_within_class": 1,
        },
        "mission_eras": [
            {
                "id": "TEST_ERA",
                "start_utc": "2020-01-01T00:00:00Z",
                "stop_utc": "2022-01-01T00:00:00Z",
            }
        ],
    }
    intervals = build_shock_intervals(shocks, rc, contract=contract)
    assert len(intervals) == 1
    evidence = intervals[0]["independent_selection_evidence"]
    assert evidence["nearest_richardson_cane_disturbance_gap_hours"] == pytest.approx(120.0)
    assert evidence["catalog_reference_time_utc"] == "2021-03-01T12:00:00+00:00"


def test_interval_common_is_fail_closed_before_mag_retrieval() -> None:
    item = interval_common(
        interval_id="V2_TEST",
        class_name="QUIET_SOLAR_WIND",
        start=pd.Timestamp("2021-01-01T00:00:00Z"),
        stop=pd.Timestamp("2021-01-02T00:00:00Z"),
        mission_era="TEST_ERA",
        selection_rule="independent catalog only",
        evidence={"source": "TEST"},
    )
    assert item["spacecraft_mag_retrieved_before_freeze"] is False
    assert item["gate_outputs_inspected_before_freeze"] is False
    assert item["candidate_scores_inspected_before_freeze"] is False
    assert item["replacement_after_later_failure_allowed"] is False
    assert item["initial_retrieval_state"] == "NOT_ATTEMPTED_REGISTRY_ONLY"


def test_registry_validator_rejects_2024_and_accepts_forty_catalog_only_rows() -> None:
    contract = load_contract(SELECTION_CONTRACT)
    intervals = []
    for class_name in contract["classes"]:
        for index in range(10):
            intervals.append(
                interval_common(
                    interval_id=f"{class_name}_{index}",
                    class_name=class_name,
                    start=pd.Timestamp("2021-01-01T00:00:00Z")
                    + pd.Timedelta(days=index * 31),
                    stop=pd.Timestamp("2021-01-02T00:00:00Z")
                    + pd.Timedelta(days=index * 31),
                    mission_era="ERA_DSCOVR_ACE_WIND_2020_2023",
                    selection_rule="independent catalog only",
                    evidence={"source": "TEST"},
                )
            )
    registry = {"intervals": intervals}
    validate_registry(registry, contract)

    registry["intervals"][0]["start_utc"] = "2024-01-01T00:00:00+00:00"
    with pytest.raises(Exception, match="V1 year leaked"):
        validate_registry(registry, contract)


def test_selector_source_has_no_runtime_spacecraft_mag_transport() -> None:
    text = SELECTOR_SOURCE.read_text(encoding="utf-8")
    forbidden_runtime_fragments = (
        "historical.download_dscovr",
        "historical.gannon_gate_density",
        "historical.gannon_multipoint_audit",
        "cdaweb.gsfc.nasa.gov/hapi",
        "cdaweb.gsfc.nasa.gov/WS/cdasr",
        "ncei.noaa.gov/cloud-access/space-weather-portal",
    )
    for fragment in forbidden_runtime_fragments:
        assert fragment not in text
    assert "GFZ Kp" in text
    assert "IPShocks" in text
    assert "Richardson/Cane" in text
