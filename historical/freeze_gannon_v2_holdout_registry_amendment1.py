#!/usr/bin/env python3
"""Run the unchanged V2 holdout selector under Selection Amendment 1.

This module does not implement selection logic. It imports the original
`freeze_gannon_v2_holdout_registry` selector and replaces only its contract
admission function so that the independently demonstrated COMPLEX class
underfill (N=7) can be published without changing spacing, catalog evidence,
ranking, detector thresholds, timing radii, or any MAG-facing code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from historical import freeze_gannon_v2_holdout_registry as base

AMENDMENT = Path("config/gannon_holdout_v2_selection.amendment1.json")


def load_contract_amendment1(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))

    if contract.get("status") != "SELECTION_RULES_FROZEN_WITH_PRE_MAG_AMENDMENT_1":
        raise base.HoldoutSelectionError("effective Amendment-1 contract is not frozen")
    if amendment.get("status") != "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL":
        raise base.HoldoutSelectionError("selection Amendment 1 is not frozen before MAG")
    if amendment.get("amendment", {}).get("type") != "AMEND_N_ONLY":
        raise base.HoldoutSelectionError("unexpected selection amendment type")

    expected_classes = {
        "QUIET_SOLAR_WIND",
        "MODERATE_VARIABILITY",
        "ISOLATED_SHOCK_OR_SHEATH",
        "COMPLEX_INTERACTING_EJECTA",
    }
    required_classes = contract.get("required_classes", {})
    if set(required_classes) != expected_classes:
        raise base.HoldoutSelectionError("effective contract has unexpected classes")

    expected_counts = {
        "QUIET_SOLAR_WIND": 12,
        "MODERATE_VARIABILITY": 12,
        "ISOLATED_SHOCK_OR_SHEATH": 12,
        "COMPLEX_INTERACTING_EJECTA": 7,
    }
    actual_counts = {
        name: int(required_classes[name].get("count", 0))
        for name in expected_classes
    }
    if actual_counts != expected_counts:
        raise base.HoldoutSelectionError(
            f"Amendment-1 class counts changed: {actual_counts}"
        )

    complex_rule = amendment["amendment"]["COMPLEX_INTERACTING_EJECTA"]
    if complex_rule.get("original_target_count") != 12:
        raise base.HoldoutSelectionError("amendment lost original COMPLEX N=12")
    if complex_rule.get("minimum_publishable_count") != 7:
        raise base.HoldoutSelectionError("amendment minimum is not seven")
    if complex_rule.get("expected_count_from_triggering_catalog_snapshot") != 7:
        raise base.HoldoutSelectionError("amendment expected COMPLEX count is not seven")
    if any(
        complex_rule.get(key) is not False
        for key in (
            "selection_criteria_changed",
            "independent_source_changed",
            "cluster_window_hours_changed",
            "minimum_spacing_days_changed",
        )
    ):
        raise base.HoldoutSelectionError("Amendment 1 changes more than class N")

    gate = contract["frozen_detector"]
    if gate.get("coordinate_frame") != "GSE":
        raise base.HoldoutSelectionError("GSE detector frame changed")
    if gate.get("canonical_cadence_seconds") != 60:
        raise base.HoldoutSelectionError("one-minute cadence changed")
    if gate.get("required_previous_offset_seconds") != 60:
        raise base.HoldoutSelectionError("exact t-1 minute rule changed")
    if gate.get("rotation_threshold_degrees") != 45.0:
        raise base.HoldoutSelectionError("45-degree detector changed")
    if gate.get("magnitude_change_threshold_fraction") != 0.25:
        raise base.HoldoutSelectionError("25-percent detector changed")
    if gate.get("timing_radii_minutes") != [1, 2, 3, 5, 10, 15]:
        raise base.HoldoutSelectionError("timing radii changed")
    hypothesis = gate["primary_clustering_hypothesis"]
    if hypothesis.get("nearest_joint_ace_wind_support_radius_minutes_max") != 2:
        raise base.HoldoutSelectionError("two-minute hypothesis changed")
    if hypothesis.get("strongest_three_spacecraft_span_minutes_max") != 3:
        raise base.HoldoutSelectionError("three-minute hypothesis changed")

    return contract


def main() -> None:
    # Preserve every original selector function except contract admission.
    base.load_contract = load_contract_amendment1
    base.main()


if __name__ == "__main__":
    main()
