from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "historical" / "jan5_2026_retrospective.py"
SPEC = importlib.util.spec_from_file_location("jan5_retrospective", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def load_config() -> dict:
    return json.loads(
        (ROOT / "config" / "jan5_2026_retrospective.v1.json").read_text(
            encoding="utf-8"
        )
    )


def test_vector_rotation_is_geometric() -> None:
    angle = MODULE.vector_rotation_degrees(
        np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    )
    assert math.isclose(angle, 90.0, abs_tol=1e-12)


def test_quaternion_sign_flip_is_same_attitude() -> None:
    times = pd.to_datetime(
        ["2026-01-05T00:00:00Z", "2026-01-05T00:00:01Z"], utc=True
    )
    rows = []
    first = [1.0, 0.0, 0.0, 0.0]
    second = [-1.0, 0.0, 0.0, 0.0]
    mnemonics = ["Q1", "Q2", "Q3", "Q4"]
    for mnemonic, v1, v2 in zip(mnemonics, first, second):
        rows.extend(
            [
                {"mnemonic": mnemonic, "time_utc": times[0], "value": v1},
                {"mnemonic": mnemonic, "time_utc": times[1], "value": v2},
            ]
        )
    result = MODULE.quaternion_angular_steps(
        pd.DataFrame(rows), mnemonics=mnemonics, tolerance_seconds=0.1
    )
    assert len(result) == 1
    assert math.isclose(float(result.iloc[0]["attitude_step_arcsec"]), 0.0)


def test_hapi_vector_parser_rejects_fill_and_preserves_real_vector() -> None:
    payload = {
        "parameters": [
            {"name": "Time", "type": "isotime"},
            {"name": "B1GSE", "type": "double", "size": [3], "units": "nT"},
        ],
        "data": [
            ["2026-01-05T00:00:00Z", [3.0, 4.0, 0.0]],
            ["2026-01-05T00:01:00Z", [-1.0e31, -1.0e31, -1.0e31]],
        ],
    }
    accepted, rejected = MODULE.parse_hapi_vector_data(
        payload,
        source_id="TEST",
        time_parameter="Time",
        vector_parameter="B1GSE",
        vector_info={"name": "B1GSE", "size": [3], "fill": "-1e31"},
    )
    assert len(accepted) == 1
    assert math.isclose(float(accepted.iloc[0]["bmag_nT"]), 5.0)
    assert len(rejected) == 1
    assert rejected.iloc[0]["reason"] == "FILL_VECTOR"


def test_declared_controls_are_frozen_and_exclude_event_day() -> None:
    config = load_config()
    windows = MODULE.declared_windows(config)
    event_count = sum(window["window_role"] == "EVENT" for window in windows)
    control_count = sum(window["window_role"] == "CONTROL" for window in windows)
    assert event_count == len(config["predeclared_event_windows"])
    assert control_count == event_count * len(config["control_dates"])
    assert "2026-01-05" not in config["control_dates"]


def test_candidate_rule_has_no_chi_cap() -> None:
    config = load_config()
    detector = config["detectors"]["l1_magnetic_candidate"]
    frame = pd.DataFrame(
        [
            {
                "source_id": "TEST",
                "time_utc": pd.Timestamp("2026-01-05T00:00:00Z"),
                "bx_gse_nT": 1.0,
                "by_gse_nT": 0.0,
                "bz_gse_nT": 0.0,
                "bmag_nT": 1.0,
            },
            {
                "source_id": "TEST",
                "time_utc": pd.Timestamp("2026-01-05T00:01:00Z"),
                "bx_gse_nT": 0.0,
                "by_gse_nT": 10.0,
                "bz_gse_nT": 0.0,
                "bmag_nT": 10.0,
            },
        ]
    )
    pairs = MODULE.detect_l1_candidates(frame, detector)
    assert len(pairs) == 1
    assert bool(pairs.iloc[0]["candidate"])
    assert float(pairs.iloc[0]["detector_score"]) > 1.0
    assert "chi" not in "|".join(pairs.columns).lower()


def test_fixed_propagation_lag_is_disabled() -> None:
    config = load_config()
    assert config["timing"]["fixed_dscovr_to_jwst_lag_enabled"] is False


def test_fail_closed_prohibitions_are_present() -> None:
    config = load_config()
    forbidden = set(config["forbidden_runtime_behavior"])
    assert {
        "simulation_fallback",
        "demo_fallback",
        "current_feed_relabelled_as_historical",
        "forward_fill_detector_inputs",
        "interpolate_detector_inputs",
        "silent_source_substitution",
        "modify_gannon_v2_holdout",
    }.issubset(forbidden)


def test_empirical_control_result_penalizes_missing_controls() -> None:
    event_time = pd.Timestamp("2026-01-05T00:35:00Z")
    rows = [
        {
            "parent_event_id": "A",
            "source_id": "TEST",
            "window_role": "EVENT",
            "window_id": "A",
            "start_utc": event_time,
            "end_utc": event_time + pd.Timedelta(minutes=50),
            "minute_coverage": 1.0,
            "max_detector_score": 2.0,
        },
        {
            "parent_event_id": "A",
            "source_id": "TEST",
            "window_role": "CONTROL",
            "window_id": "C1",
            "start_utc": event_time - pd.Timedelta(days=1),
            "end_utc": event_time - pd.Timedelta(days=1) + pd.Timedelta(minutes=50),
            "minute_coverage": 1.0,
            "max_detector_score": 1.0,
        },
        {
            "parent_event_id": "A",
            "source_id": "TEST",
            "window_role": "CONTROL",
            "window_id": "C2",
            "start_utc": event_time - pd.Timedelta(days=2),
            "end_utc": event_time - pd.Timedelta(days=2) + pd.Timedelta(minutes=50),
            "minute_coverage": 0.0,
            "max_detector_score": np.nan,
        },
    ]
    result = MODULE.empirical_control_table(pd.DataFrame(rows))
    assert len(result) == 1
    assert int(result.iloc[0]["incomplete_control_count"]) == 1
    assert float(result.iloc[0]["conservative_exceedance_upper_bound"]) > float(
        result.iloc[0]["complete_only_empirical_exceedance_fraction"]
    )
