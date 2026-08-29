import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from historical.select_mag_gate_control_intervals import (
    ControlSelectionError,
    build_selected_registry,
    daily_metrics,
    eligible_days,
    load_contract,
    parse_chunk,
    select_spaced,
)


PARAMETERS = ["percent_interp", "F", "flow_speed", "SYM_H"]


def frozen_contract() -> dict:
    return {
        "contract_id": "NVCPP-MAG-GATE-CONTROL-SELECTION-v1",
        "contract_version": "1.0.0",
        "selection_source": {
            "provider": "NASA CDAWeb HAPI",
            "dataset_id": "OMNI_HRO2_1MIN",
            "parameters": PARAMETERS,
            "role": "selection only",
        },
        "search_window": {
            "start_utc": "2024-01-01T00:00:00Z",
            "stop_utc": "2024-02-01T00:00:00Z",
            "chunk_days": 10,
        },
        "eligibility": {
            "minimum_minutes_with_finite_F": 2,
            "minimum_minutes_with_finite_flow_speed": 2,
            "minimum_minutes_with_finite_SYM_H": 2,
            "maximum_abs_SYM_H_nT": 250.0,
        },
        "ranking": {
            "method": "sum_of_ascending_fractional_ranks",
            "components": [
                "abs_daily_min_SYM_H",
                "daily_SYM_H_interdecile_range",
                "daily_F_interdecile_range",
                "daily_flow_speed_interdecile_range",
            ],
            "quiet_count": 1,
            "moderate_count": 1,
            "minimum_spacing_days": 2,
            "quiet_rule": "lowest",
            "moderate_rule": "median",
        },
        "fixed_event_controls": [],
        "selection_limits": ["no gate inspection"],
    }


def test_load_contract_rejects_parameter_drift(tmp_path: Path):
    contract = frozen_contract()
    contract["selection_source"]["parameters"] = ["F", "SYM_H"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ControlSelectionError, match="selector parameters changed"):
        load_contract(path)


def test_parse_chunk_is_strict_and_applies_fill_values():
    metadata = {
        "percent_interp": {"fill": "999"},
        "F": {"fill": "9999.99"},
        "flow_speed": {"fill": "99999.9"},
        "SYM_H": {"fill": "99999"},
    }
    raw = (
        b"2024-01-01T00:00:00.000Z,0,5.0,400.0,-5\n"
        b"2024-01-01T00:01:00.000Z,999,9999.99,410.0,-6\n"
    )
    frame = parse_chunk(
        raw,
        parameters=PARAMETERS,
        parameter_map=metadata,
    )
    assert len(frame) == 2
    assert np.isnan(frame.loc[1, "F"])
    assert np.isnan(frame.loc[1, "percent_interp"])

    with pytest.raises(ControlSelectionError, match="field-count mismatch"):
        parse_chunk(
            b"2024-01-01T00:00:00Z,0,5.0\n",
            parameters=PARAMETERS,
            parameter_map=metadata,
        )


def test_daily_metrics_and_eligibility_do_not_require_gate_columns():
    times = pd.date_range("2024-01-01", periods=6, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {
            "time": times,
            "F": [4.0, 4.2, 4.1, 4.3, 4.0, 4.1],
            "flow_speed": [390, 392, 391, 393, 390, 392],
            "SYM_H": [-4, -5, -3, -4, -5, -4],
            "percent_interp": [0, 0, 0, 0, 0, 0],
        }
    )
    metrics = daily_metrics(frame)
    eligible = eligible_days(metrics, contract=frozen_contract())
    assert len(eligible) == 1
    assert "gate_pass" not in eligible.columns
    assert eligible.loc[0, "activity_rank_sum"] == pytest.approx(4.0)


def test_select_spaced_refuses_adjacent_days():
    candidates = pd.DataFrame(
        {
            "day_utc": pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-10"], utc=True
            ),
            "activity_rank_sum": [1.0, 1.1, 1.2],
        }
    )
    selected = select_spaced(
        candidates,
        count=2,
        spacing_days=7,
    )
    assert [row["day_utc"].day for row in selected] == [1, 10]


def test_registry_selects_quiet_then_median_without_gate_results():
    contract = frozen_contract()
    contract["ranking"]["minimum_spacing_days"] = 1
    rows = []
    for index, day in enumerate(
        pd.date_range("2024-01-01", periods=5, freq="7D", tz="UTC"), start=1
    ):
        rows.append(
            {
                "day_utc": day,
                "activity_rank_sum": float(index),
                "daily_min_SYM_H_nT": -float(index),
                "daily_max_SYM_H_nT": float(index),
                "daily_SYM_H_interdecile_range": float(index),
                "daily_F_median_nT": 5.0,
                "daily_F_interdecile_range": float(index),
                "daily_flow_speed_median_km_s": 400.0,
                "daily_flow_speed_interdecile_range": float(index),
            }
        )
    eligible = pd.DataFrame(rows)
    registry = build_selected_registry(eligible, contract=contract)
    assert registry[0]["class"] == "LOW_ACTIVITY_OMNI_SELECTED_CONTROL"
    assert registry[0]["start_utc"].startswith("2024-01-01")
    assert registry[1]["class"] == "MODERATE_ACTIVITY_OMNI_SELECTED_CONTROL"
    assert "gate" not in json.dumps(registry).lower()
