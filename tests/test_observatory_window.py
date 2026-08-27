import pandas as pd
import pytest

from observatory.time_windows import build_hourly_window


def test_hourly_window_uses_latest_complete_hour_after_safety_lag():
    window = build_hourly_window(
        now="2026-08-26T20:17:00Z",
        safety_lag_minutes=20,
        retrieval_hours=30,
        analysis_hours=6,
        focus_minutes=60,
    )
    assert window.analysis_end == pd.Timestamp("2026-08-26T19:00:00Z")
    assert window.analysis_start == pd.Timestamp("2026-08-26T13:00:00Z")
    assert window.retrieval_start == pd.Timestamp("2026-08-25T13:00:00Z")
    assert window.focus_start == pd.Timestamp("2026-08-26T18:00:00Z")


def test_window_rejects_missing_baseline_span():
    with pytest.raises(ValueError, match="24-hour baseline"):
        build_hourly_window(retrieval_hours=20, analysis_hours=6)
