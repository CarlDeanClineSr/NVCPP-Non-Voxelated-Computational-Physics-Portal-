"""Offline fixtures for diagnostics; none are observational evidence."""

import json

import pandas as pd
import pytest

from core.cline_l1_chain_v1 import ProtocolConfig, run_chain
from core.diagnostics import baseline_failure, source_boundary_diagnostics
from core.exceptions import (
    BaselineNonpositiveError,
    BaselineUnavailableError,
    BaselineWarmupError,
    InsufficientCoverageError,
)


def failed_baseline(valid_prior=1331, cadence=60, magnitude=5.0):
    expected = int(86400 / cadence)
    times = pd.date_range("2026-01-01T00:00:00Z", periods=expected + 1, freq=f"{cadence}s")
    frame = pd.DataFrame({"time": times, "B_mag": magnitude})
    frame = frame.iloc[[*range(valid_prior), expected]].copy()
    processed = run_chain(frame, "time", "B_mag", expected_cadence_seconds=cadence)
    return processed, processed.iloc[-1:].copy()


def describe(processed, analysis, cadence=60):
    config = ProtocolConfig()
    return baseline_failure(
        processed, analysis, time_col="time", window_hours=config.window_hours,
        min_coverage=config.min_coverage, cadence_seconds=cadence,
    )


def test_exact_deficit_comes_from_unchanged_prior_only_core():
    processed, analysis = failed_baseline()
    original = processed.copy(deep=True)
    exc = describe(processed, analysis)
    assert isinstance(exc, InsufficientCoverageError)
    assert exc.reason_code == "BASELINE_INSUFFICIENT_COVERAGE"
    assert exc.diagnostics["best_valid_minutes"] == 1331
    assert exc.diagnostics["required_samples"] == 1368
    assert exc.diagnostics["required_pct"] == 95.0
    assert exc.diagnostics["best_coverage_pct"] == pytest.approx(1331 / 1440 * 100)
    assert exc.diagnostics["missing_to_qualify_minutes"] == 37
    assert exc.diagnostics["best_prior_window_end_exclusive"] == analysis.iloc[0]["time"].isoformat()
    assert analysis["chi_B24M"].isna().all()
    pd.testing.assert_frame_equal(processed, original)
    assert json.loads(json.dumps(exc.as_dict(), allow_nan=False))["reason_code"] == exc.reason_code


@pytest.mark.parametrize("prior_count,expected_status", [(1367, "INSUFFICIENT_COVERAGE"), (1368, "VALID")])
def test_95_percent_boundary_is_not_relaxed(prior_count, expected_status):
    processed, analysis = failed_baseline(valid_prior=prior_count)
    assert analysis.iloc[0]["baseline_status"] == expected_status
    if expected_status == "VALID":
        with pytest.raises(ValueError, match="no VALID rows"):
            describe(processed, analysis)
    else:
        assert describe(processed, analysis).diagnostics["missing_to_qualify_samples"] == 1


def test_true_warmup_is_not_called_insufficient_coverage():
    frame = pd.DataFrame({
        "time": pd.date_range("2026-01-01T00:00:00Z", periods=1440, freq="1min"),
        "B_mag": 5.0,
    })
    processed = run_chain(frame, "time", "B_mag", expected_cadence_seconds=60)
    exc = describe(processed, processed.iloc[-1:])
    assert isinstance(exc, BaselineWarmupError)
    assert exc.diagnostics["available_span_hours"] == 24.0
    assert exc.diagnostics["elapsed_span_hours"] < 24.0
    assert exc.diagnostics["full_window_analysis_rows"] == 0


def test_nonpositive_baseline_is_not_misreported_as_missing_samples():
    processed, analysis = failed_baseline(valid_prior=1440, magnitude=0.0)
    exc = describe(processed, analysis)
    assert isinstance(exc, BaselineNonpositiveError)
    assert exc.diagnostics["missing_to_qualify_samples"] == 0


def test_empty_analysis_stays_unknown_not_warmup():
    processed, analysis = failed_baseline()
    exc = describe(processed, analysis.iloc[0:0])
    assert isinstance(exc, BaselineUnavailableError)
    assert exc.diagnostics["analysis_rows"] == 0
    assert "best_valid_samples" not in exc.diagnostics


def test_other_cadences_do_not_mislabel_samples_as_minutes():
    processed, analysis = failed_baseline(valid_prior=2662, cadence=30)
    exc = describe(processed, analysis, cadence=30)
    assert exc.diagnostics["expected_samples"] == 2880
    assert exc.diagnostics["missing_to_qualify_samples"] == 74
    assert "best_valid_minutes" not in exc.diagnostics


def test_best_window_is_restricted_to_the_failed_analysis():
    processed, analysis = failed_baseline()
    extra = processed.iloc[-1:].copy()
    extra["baseline_sample_count"] = 1400
    extra["baseline_coverage_fraction"] = 1400 / 1440
    # An out-of-analysis row must not inflate the reported best analysis window.
    exc = describe(pd.concat([processed, extra], ignore_index=True), analysis)
    assert exc.diagnostics["best_valid_samples"] == 1331


def test_boundary_report_distinguishes_raw_rows_from_quarantined_start():
    start = pd.Timestamp("2026-09-03T18:00:00Z")
    end = pd.Timestamp("2026-09-05T00:00:00Z")
    raw = pd.Series(pd.date_range(start, end, freq="1min", inclusive="left"))
    details = source_boundary_diagnostics(
        raw, raw.iloc[2:], requested_start=start, requested_end=end,
        cadence_seconds=60, quarantined_rows=2,
        provider_info={"stopDate": "2026-09-04T23:59:00.000Z"},
    )
    assert details["earliest_raw_returned"] == start.isoformat()
    assert details["earliest_returned"] == (start + pd.Timedelta(minutes=2)).isoformat()
    assert details["missing_preroll_seconds"] == 120
    assert details["missing_preroll_beyond_tolerance_seconds"] == 60
    assert details["missing_end_seconds"] == 0
    assert details["quarantined_rows"] == 2


def test_exclusive_end_accounts_for_the_existing_one_cadence_tolerance():
    start = pd.Timestamp("2026-09-03T18:00:00Z")
    end = start + pd.Timedelta(hours=30)
    raw = pd.Series(pd.date_range(start, end, freq="1min", inclusive="left"))
    details = source_boundary_diagnostics(
        raw.iloc[:-2], raw.iloc[:-2], requested_start=start, requested_end=end,
        cadence_seconds=60, quarantined_rows=0, provider_info={},
    )
    assert details["missing_preroll_seconds"] == 0
    assert details["missing_end_seconds"] == 120
