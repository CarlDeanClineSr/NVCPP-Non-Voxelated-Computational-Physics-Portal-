import json
from pathlib import Path

import pandas as pd
import pytest

from sources.solar1.download_solar1 import sanitize_rows, schema_fingerprint

CONTRACT = json.loads(Path("config/solar1_mag_contract.v1.json").read_text())


def frame(rows):
    return pd.DataFrame(
        rows,
        columns=["time", "b_gse_min_x", "b_gse_min_y", "b_gse_min_z"],
    )


def test_fill_zero_range_and_bad_time_are_preserved_in_quarantine():
    raw = frame([
        ["2026-06-01T00:00:00Z", "1", "2", "3"],
        ["2026-06-01T00:01:00Z", "-9999", "2", "3"],
        ["2026-06-01T00:02:00Z", "0", "0", "0"],
        ["2026-06-01T00:03:00Z", "999", "2", "3"],
        ["not-a-time", "1", "2", "3"],
    ])
    clean, quarantine, metrics = sanitize_rows(raw, CONTRACT)
    assert len(clean) == 1
    assert set(quarantine["reason_code"]) == {
        "PROVIDER_FILL",
        "ZERO_VECTOR_SUSPECT",
        "OUT_OF_RANGE_SUSPECT",
        "INVALID_TIMESTAMP",
    }
    assert metrics["quarantined_rows"] == 4


def test_exact_fill_does_not_absorb_more_negative_out_of_range_value():
    raw = frame([
        ["2026-06-01T00:00:00Z", "1", "2", "3"],
        ["2026-06-01T00:01:00Z", "-10000", "2", "3"],
    ])
    _, quarantine, _ = sanitize_rows(raw, CONTRACT)
    assert quarantine.iloc[0]["reason_code"] == "OUT_OF_RANGE_SUSPECT"


def test_conflicting_duplicate_timestamp_fails():
    raw = frame([
        ["2026-06-01T00:00:00Z", "1", "2", "3"],
        ["2026-06-01T00:00:00Z", "2", "2", "3"],
    ])
    with pytest.raises(ValueError, match="conflicting duplicate"):
        sanitize_rows(raw, CONTRACT)


def test_schema_fingerprint_is_stable_to_volatile_dates():
    info = {
        "startDate": "one",
        "stopDate": "two",
        "parameters": [
            {"name": "time", "type": "isotime", "units": "UTC", "length": 24},
            {"name": "b_gse_min_x", "type": "double", "units": "nT", "fill": "-9999.0", "description": "x"},
            {"name": "b_gse_min_y", "type": "double", "units": "nT", "fill": "-9999.0", "description": "y"},
            {"name": "b_gse_min_z", "type": "double", "units": "nT", "fill": "-9999.0", "description": "z"},
        ],
    }
    ids = ["time", "b_gse_min_x", "b_gse_min_y", "b_gse_min_z"]
    first, _ = schema_fingerprint(info, "sci_mag-l3_solar1", ids)
    info["stopDate"] = "changed"
    second, _ = schema_fingerprint(info, "sci_mag-l3_solar1", ids)
    assert first == second
