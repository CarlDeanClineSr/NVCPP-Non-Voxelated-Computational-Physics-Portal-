import numpy as np
import pandas as pd

from core.event_detection import CanonicalColumns, EventThresholds, detect_events


def frame():
    times = pd.date_range("2026-01-01", periods=12, freq="1min", tz="UTC")
    bx = np.ones(12) * 5.0
    by = np.zeros(12)
    bz = np.zeros(12)
    delta = np.zeros(12)
    delta[4:6] = 0.7
    delta[8:10] = -0.8
    # Rotate the vector at minute 7.
    bx[7] = 0.0
    by[7] = 5.0
    magnitude = np.sqrt(bx**2 + by**2 + bz**2)
    return pd.DataFrame(
        {
            "time": times,
            "bx": bx,
            "by": by,
            "bz": bz,
            "B_mag": magnitude,
            "B0": 5.0,
            "delta_B24M": delta,
            "chi_B24M": np.abs(delta),
            "baseline_valid": True,
        }
    )


def test_signed_compression_depression_and_rotation_are_retained():
    prepared, events, metrics = detect_events(
        frame(),
        mission="TEST",
        columns=CanonicalColumns(time="time", bx="bx", by="by", bz="bz"),
        thresholds=EventThresholds(merge_gap_minutes=1),
        focus_start="2026-01-01T00:00:00Z",
    )
    codes = {code for event in events for code in event["trigger_codes"]}
    assert "MAG_COMPRESSION_CANDIDATE" in codes
    assert "MAG_DEPRESSION_CANDIDATE" in codes
    assert "FIELD_ROTATION_CANDIDATE" in codes
    assert prepared["rotation_degrees"].max() == 90.0
    assert metrics["event_count"] >= 2


def test_watch_threshold_does_not_create_event_by_itself():
    data = frame().iloc[:3].copy()
    data["delta_B24M"] = 0.2
    data["chi_B24M"] = 0.2
    _, events, metrics = detect_events(
        data,
        mission="TEST",
        columns=CanonicalColumns(time="time", bx="bx", by="by", bz="bz"),
        focus_start=data["time"].min(),
    )
    assert events == []
    assert metrics["watch_rows"] == 3
