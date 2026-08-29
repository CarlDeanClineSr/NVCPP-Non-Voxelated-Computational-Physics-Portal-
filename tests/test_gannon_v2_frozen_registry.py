from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = ROOT / "provenance" / "gannon_v2_holdout_registry"
REGISTRY_JSON = REGISTRY_ROOT / "gannon_v2_holdout_registry.v1.json"
REGISTRY_CSV = REGISTRY_ROOT / "gannon_v2_holdout_registry.v1.csv"
SELECTION_MANIFEST = REGISTRY_ROOT / "selection_manifest.json"
INVENTORY = REGISTRY_ROOT / "FROZEN_INVENTORY.json"
PREREGISTRATION = ROOT / "config" / "gannon_holdout_v2.preregistered.json"
SELECTION_CONTRACT = ROOT / "config" / "gannon_holdout_v2_selection.v1.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_compact_file_hashes_match_inventory() -> None:
    inventory = load(INVENTORY)
    expected = inventory["compact_files"]
    for name in (
        "gannon_v2_holdout_registry.v1.json",
        "gannon_v2_holdout_registry.v1.csv",
        "selection_manifest.json",
        "README.md",
    ):
        path = REGISTRY_ROOT / name
        assert path.is_file(), name
        assert sha256(path) == expected[name]["sha256"]
        assert path.stat().st_size == expected[name]["size_bytes"]


def test_registry_has_exactly_ten_intervals_per_class() -> None:
    registry = load(REGISTRY_JSON)
    intervals = registry["intervals"]
    counts = Counter(item["class"] for item in intervals)
    assert registry["status"] == "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL"
    assert len(intervals) == 40
    assert counts == {
        "QUIET_SOLAR_WIND": 10,
        "MODERATE_VARIABILITY": 10,
        "ISOLATED_SHOCK_OR_SHEATH": 10,
        "COMPLEX_INTERACTING_EJECTA": 10,
    }
    ids = [item["interval_id"] for item in intervals]
    assert len(ids) == len(set(ids))


def test_registry_is_catalog_only_and_geometry_remains_closed() -> None:
    registry = load(REGISTRY_JSON)
    firewall = registry["selection_firewall"]
    assert firewall == {
        "spacecraft_mag_retrieved": False,
        "gate_outputs_inspected": False,
        "candidate_scores_inspected": False,
        "clustering_outputs_inspected": False,
    }
    assert registry["physical_interpretation_reopened"] is False
    assert registry["geometry_state"] == "CLOSED"
    for item in registry["intervals"]:
        assert item["spacecraft_mag_retrieved_before_freeze"] is False
        assert item["gate_outputs_inspected_before_freeze"] is False
        assert item["candidate_scores_inspected_before_freeze"] is False
        assert item["replacement_after_later_failure_allowed"] is False
        assert item["initial_retrieval_state"] == "NOT_ATTEMPTED_REGISTRY_ONLY"
        assert item["independent_selection_evidence"]["source"] in {
            "GFZ_KP",
            "IPSHOCKS_ZENODO",
            "RICHARDSON_CANE_ICME_TABLE",
        }


def test_registry_excludes_v1_and_respects_catalog_coverage() -> None:
    registry = load(REGISTRY_JSON)
    selection = load(SELECTION_CONTRACT)
    cutoff = datetime.fromisoformat(
        selection["global_rules"]["catalog_coverage_stop_utc_exclusive"].replace(
            "Z", "+00:00"
        )
    )
    v1_dates = set(selection["global_rules"]["explicit_v1_dates"])
    for item in registry["intervals"]:
        start = datetime.fromisoformat(item["start_utc"].replace("Z", "+00:00"))
        assert start.year != 2024
        assert start < cutoff
        assert start.date().isoformat() not in v1_dates


def test_json_and_csv_registries_have_identical_rows() -> None:
    registry = load(REGISTRY_JSON)
    csv = pd.read_csv(REGISTRY_CSV)
    json_ids = [item["interval_id"] for item in registry["intervals"]]
    assert csv["interval_id"].tolist() == json_ids
    assert csv["class"].tolist() == [item["class"] for item in registry["intervals"]]
    assert csv["start_utc"].tolist() == [item["start_utc"] for item in registry["intervals"]]
    assert csv["stop_utc"].tolist() == [item["stop_utc"] for item in registry["intervals"]]
    assert csv["mission_era"].tolist() == [item["mission_era"] for item in registry["intervals"]]


def test_selection_manifest_identifies_exact_registry_and_sources() -> None:
    registry = load(REGISTRY_JSON)
    manifest = load(SELECTION_MANIFEST)
    inventory = load(INVENTORY)
    assert manifest["status"] == "SUCCESS"
    assert manifest["spacecraft_mag_retrieved"] is False
    assert manifest["physics_computed"] is False
    assert manifest["geometry_opened"] is False
    assert manifest["registry_sha256"] == sha256(REGISTRY_JSON)
    assert inventory["selection_workflow"]["run_id"] == 33260766315
    assert inventory["selection_workflow"]["artifact_id"] == 9717190524
    assert inventory["counts"]["intervals_total"] == 40
    for key, value in registry["source_metadata"].items():
        assert inventory["raw_catalogs"][key]["sha256"] == value["sha256"]
        assert inventory["raw_catalogs"][key]["committed_to_git"] is False
        assert inventory["raw_catalogs"][key]["preserved_in_actions_artifact"] is True


def test_v1_detector_and_gannon_inspired_primary_statistic_are_unchanged() -> None:
    prereg = load(PREREGISTRATION)
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
    assert prereg["geometry_gate"]["default_state"] == "CLOSED"
    assert prereg["geometry_gate"]["automatic_open_on_favorable_null_result"] is False
