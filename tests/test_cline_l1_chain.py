import numpy as np
import pandas as pd
import pytest

from core.cline_l1_chain_v1 import run_chain


def minute_frame(hours=30, value=5.0):
    periods = hours * 60
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=periods, freq="1min", tz="UTC"),
        "B_mag": np.full(periods, value, dtype=float),
    })


def test_prior_only_median_and_unclipped_excursion():
    frame = minute_frame()
    frame.loc[24 * 60, "B_mag"] = 15.0
    out = run_chain(frame, "time", "B_mag", expected_cadence_seconds=60)
    row = out.iloc[24 * 60]
    assert row["baseline_status"] == "VALID"
    assert row["B0"] == pytest.approx(5.0)
    assert row["ratio_B24M"] == pytest.approx(3.0)
    assert row["delta_B24M"] == pytest.approx(2.0)
    assert row["chi_B24M"] == pytest.approx(2.0)


def test_warmup_and_coverage_are_explicit():
    out = run_chain(minute_frame(hours=25), "time", "B_mag", expected_cadence_seconds=60)
    assert out.iloc[1439]["baseline_status"] == "WARMUP"
    assert out.iloc[1440]["baseline_status"] == "VALID"


def test_duplicates_fail_closed():
    frame = minute_frame(hours=25)
    frame = pd.concat([frame, frame.iloc[[100]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate timestamps"):
        run_chain(frame, "time", "B_mag", expected_cadence_seconds=60)


def test_nonpositive_baseline_is_invalid_without_denominator_floor():
    out = run_chain(minute_frame(value=0.0), "time", "B_mag", expected_cadence_seconds=60)
    row = out.iloc[24 * 60]
    assert row["baseline_status"] == "BASELINE_NONPOSITIVE"
    assert np.isnan(row["B0"])
    assert np.isnan(row["chi_B24M"])


def test_missing_minutes_trigger_coverage_rejection():
    frame = minute_frame(hours=30)
    frame = frame.loc[~frame.index.isin(range(15 * 60, 15 * 60 + 150))].reset_index(drop=True)
    out = run_chain(frame, "time", "B_mag", expected_cadence_seconds=60)
    target = out.loc[out["time"] == pd.Timestamp("2026-01-02 01:00:00+00:00")].iloc[0]
    assert target["baseline_status"] == "INSUFFICIENT_COVERAGE"
