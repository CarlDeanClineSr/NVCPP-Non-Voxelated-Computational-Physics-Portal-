import json

import pandas as pd
import pytest

from historical.select_gannon_control_intervals import (
    ControlSelectionError,
    parse_donki_ips,
    parse_gfz_kp,
    select_isolated_ips_controls,
    select_kp_controls,
)


def gfz_row(date: str, kp: list[float]) -> str:
    timestamp = pd.Timestamp(date)
    return (
        f"{timestamp.year} {timestamp.month} {timestamp.day} 0 0 0 0 "
        + " ".join(str(value) for value in kp)
        + " 0 0 0 0 0 0 0 0"
    )


def test_gfz_parser_requires_eight_valid_kp_values():
    raw = (gfz_row("2020-01-01", [0.0] * 8) + "\n").encode()
    parsed = parse_gfz_kp(raw)
    assert parsed.loc[0, "kp_max"] == 0.0
    assert parsed.loc[0, "kp_mean"] == 0.0

    with pytest.raises(ControlSelectionError):
        parse_gfz_kp(b"2020 1 1 0 0 0 0 0 0\n")


def test_donki_parser_computes_nearest_event_spacing():
    payload = json.dumps(
        [
            {"activityID": "A", "eventTime": "2023-01-01T00:00Z", "location": "Earth"},
            {"activityID": "B", "eventTime": "2023-01-03T00:00Z", "location": "Earth"},
            {"activityID": "C", "eventTime": "2023-01-10T00:00Z", "location": "Earth"},
        ]
    ).encode()
    parsed = parse_donki_ips([payload])
    assert parsed["nearest_ips_gap_hours"].tolist() == [48.0, 48.0, 168.0]


def test_kp_selection_uses_only_independent_index_values():
    rows = []
    for year in (2019, 2020, 2021, 2022, 2023):
        rows.append(gfz_row(f"{year}-01-01", [0.0] * 8))
        rows.append(
            gfz_row(
                f"{year}-07-01",
                [1.5, 1.5, 1.5, 3.0, 1.5, 1.5, 1.5, 1.5],
            )
        )
    kp = parse_gfz_kp(("\n".join(rows) + "\n").encode())
    quiet, moderate = select_kp_controls(
        kp,
        start=pd.Timestamp("2019-01-01T00:00:00Z"),
        stop=pd.Timestamp("2024-01-01T00:00:00Z"),
        quiet_count=3,
        moderate_count=3,
        exclusion_dates=[],
    )
    assert len(quiet) == 3
    assert len(moderate) == 3
    assert (quiet["kp_max"] <= 1.0).all()
    assert (
        (moderate["kp_max"] >= 3.0)
        & (moderate["kp_max"] <= 4.33)
    ).all()
    assert not any("mag" in value.lower() for value in quiet["selection_rule"])


def test_isolated_ips_selection_requires_declared_separation():
    payload = json.dumps(
        [
            {"activityID": "A", "eventTime": "2022-01-01T00:00Z", "location": "Earth"},
            {"activityID": "B", "eventTime": "2022-04-01T00:00Z", "location": "Earth"},
            {"activityID": "C", "eventTime": "2022-07-01T00:00Z", "location": "Earth"},
            {"activityID": "D", "eventTime": "2022-07-01T12:00Z", "location": "Earth"},
        ]
    ).encode()
    ips = parse_donki_ips([payload])
    selected = select_isolated_ips_controls(
        ips,
        start=pd.Timestamp("2022-01-01T00:00:00Z"),
        stop=pd.Timestamp("2023-01-01T00:00:00Z"),
        count=2,
    )
    assert len(selected) == 2
    assert (selected["nearest_ips_gap_hours"] >= 36.0).all()
