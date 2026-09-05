"""Read-only diagnostics from existing canonical results and source timestamps.

No resampling, interpolation, data admission, or baseline recomputation belongs
here. In particular, a pre-roll-start gap is not inferred from an end-date lag.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core.exceptions import (
    BaselineNonpositiveError,
    BaselineUnavailableError,
    BaselineWarmupError,
    InsufficientCoverageError,
    SourceDiagnosticError,
)


def baseline_failure(
    processed: pd.DataFrame,
    analysis: pd.DataFrame,
    *,
    time_col: str,
    window_hours: float,
    min_coverage: float,
    cadence_seconds: float,
) -> SourceDiagnosticError:
    """Describe the failed analysis using the core's prior-only sample counts.

    The best window is selected only among evaluated analysis rows, preferring
    rows with a full elapsed window. It is not a newly searched science window.
    """
    counts = {
        str(key): int(value)
        for key, value in analysis["baseline_status"].value_counts().items()
    }
    if counts.get("VALID", 0):
        raise ValueError("baseline_failure requires an analysis with no VALID rows")
    full = analysis.loc[analysis["baseline_status"] != "WARMUP"]
    full_states = set(full["baseline_status"])
    error_class: type[SourceDiagnosticError] = BaselineUnavailableError
    if not analysis.empty and full.empty:
        error_class = BaselineWarmupError
    elif full_states == {"INSUFFICIENT_COVERAGE"}:
        error_class = InsufficientCoverageError
    elif full_states == {"BASELINE_NONPOSITIVE"}:
        error_class = BaselineNonpositiveError

    first = pd.Timestamp(processed[time_col].min())
    last = pd.Timestamp(processed[time_col].max())
    expected = float(processed["baseline_expected_samples"].iloc[0])
    required = int(math.ceil(expected * min_coverage))
    details: dict[str, Any] = {
        "available_start": first.isoformat(),
        "available_end": last.isoformat(),
        "available_span_hours": (last - first).total_seconds() / 3600.0
        + cadence_seconds / 3600.0,
        "elapsed_span_hours": (last - first).total_seconds() / 3600.0,
        "available_rows": int(len(processed)),
        "window_hours": float(window_hours),
        "cadence_seconds": float(cadence_seconds),
        "expected_samples": expected,
        "required_samples": required,
        "required_pct": float(100.0 * min_coverage),
        "analysis_rows": int(len(analysis)),
        "full_window_analysis_rows": int(len(full)),
        "baseline_status_counts": counts,
        "window_boundary": "left (prior only; current sample excluded)",
    }
    pool = full if not full.empty else analysis
    if not pool.empty:
        position = pool["baseline_sample_count"].fillna(0).to_numpy().argmax()
        best = pool.iloc[position]
        count = int(best["baseline_sample_count"])
        end = pd.Timestamp(best[time_col])
        details.update(
            {
                "best_prior_window_start": (end - pd.Timedelta(hours=window_hours)).isoformat(),
                "best_prior_window_end_exclusive": end.isoformat(),
                "best_valid_samples": count,
                "best_coverage_pct": float(best["baseline_coverage_fraction"]) * 100.0,
                "missing_to_qualify_samples": max(0, required - count),
            }
        )
        # At the operational one-minute cadence these are sample-minute counts,
        # not a resampled or filled grid. Other cadences retain sample units.
        if cadence_seconds == 60.0:
            details["best_valid_minutes"] = count
            details["missing_to_qualify_minutes"] = max(0, required - count)
    return error_class(**details)


def source_boundary_diagnostics(
    raw_times: pd.Series,
    retained_times: pd.Series,
    *,
    requested_start: pd.Timestamp,
    requested_end: pd.Timestamp,
    cadence_seconds: float,
    quarantined_rows: int,
    provider_info: dict[str, Any],
) -> dict[str, Any]:
    """Report raw and retained bounds separately, without applying a new gate."""
    raw = pd.to_datetime(raw_times, utc=True, errors="coerce").dropna()
    first = pd.Timestamp(retained_times.min())
    last = pd.Timestamp(retained_times.max())
    tolerance = pd.Timedelta(seconds=cadence_seconds)
    missing_start = max(pd.Timedelta(0), first - requested_start)
    missing_end = max(pd.Timedelta(0), requested_end - tolerance - last)
    return {
        "boundary_basis": "retained_physical_rows_after_quarantine",
        "requested_start": requested_start.isoformat(),
        "requested_end_exclusive": requested_end.isoformat(),
        "earliest_returned": first.isoformat(),
        "latest_returned": last.isoformat(),
        "earliest_raw_returned": raw.min().isoformat() if len(raw) else None,
        "latest_raw_returned": raw.max().isoformat() if len(raw) else None,
        "cadence_tolerance_seconds": float(cadence_seconds),
        "missing_duration": str(missing_start),
        "missing_preroll_seconds": missing_start.total_seconds(),
        "missing_preroll_beyond_tolerance_seconds": max(
            0.0, (missing_start - tolerance).total_seconds()
        ),
        "missing_end_seconds": missing_end.total_seconds(),
        "quarantined_rows": int(quarantined_rows),
        "provider_advertised_start": provider_info.get("startDate"),
        "provider_advertised_stop": provider_info.get("stopDate"),
    }
