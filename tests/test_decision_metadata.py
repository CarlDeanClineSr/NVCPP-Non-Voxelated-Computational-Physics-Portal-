import json
from pathlib import Path

import pandas as pd

from core.temporal_pairing import classification_policy
from sources.solar1.download_solar1 import classify_mission_phase


def contract():
    return json.loads(Path("config/solar1_mag_contract.v1.json").read_text())


def test_june_regression_is_machine_labeled_pre_operational():
    phase = classify_mission_phase(
        pd.Timestamp("2026-06-02T00:00:00Z"),
        pd.Timestamp("2026-06-05T00:00:00Z"),
        contract(),
    )
    assert phase["interval_classification"] == "PRE_OPERATIONAL_COMMISSIONING_REGRESSION"
    assert phase["operational_validation_claim_allowed"] is False


def test_operational_and_boundary_crossing_intervals_are_distinct():
    mixed = classify_mission_phase(
        pd.Timestamp("2026-06-09T00:00:00Z"),
        pd.Timestamp("2026-06-11T00:00:00Z"),
        contract(),
    )
    operational = classify_mission_phase(
        pd.Timestamp("2026-06-10T00:00:00Z"),
        pd.Timestamp("2026-06-12T00:00:00Z"),
        contract(),
    )
    assert mixed["interval_classification"] == "CROSSES_OPERATIONAL_BOUNDARY"
    assert mixed["operational_validation_claim_allowed"] is False
    assert operational["interval_classification"] == "OPERATIONAL"
    assert operational["operational_validation_claim_allowed"] is True


def test_pairing_classification_thresholds_are_public_data():
    policy = classification_policy()
    assert policy == {
        "coherence": {
            "best_pearson_r_minimum": 0.70,
            "look_elsewhere_p_value_maximum": 0.01,
        },
        "lag_candidate": {
            "improvement_over_zero_lag_minimum": 0.02,
            "peak_plateau_99_5_percent_max_lags": 3,
            "bootstrap_mode_fraction_minimum": 0.60,
            "bootstrap_95_percent_span_max_minutes": 2.0,
            "daily_segment_span_max_minutes": 2,
            "ephemeris_still_required": True,
        },
    }
