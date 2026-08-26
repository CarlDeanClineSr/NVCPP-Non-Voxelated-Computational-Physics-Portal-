"""NVCPP core: CLINE-L1-B24M-TRAIL-v1.

The engine preserves finite measurements, uses a prior-only trailing 24-hour
median, and fails closed on ambiguous timebases. It never clips or floors chi.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

PROTOCOL_ID = "CLINE-L1-B24M-TRAIL-v1"
PROTOCOL_VERSION = "1.1.0"


@dataclass(frozen=True)
class ProtocolConfig:
    id: str = PROTOCOL_ID
    version: str = PROTOCOL_VERSION
    baseline_type: str = "trailing_median"
    window_hours: int = 24
    closed_boundary: str = "left"
    min_coverage: float = 0.95
    expected_cadence_seconds: float | None = None
    clipping_allowed: bool = False


def _validate_input(
    df: pd.DataFrame,
    *,
    time_col: str,
    b_mag_col: str,
) -> pd.DataFrame:
    missing = [name for name in (time_col, b_mag_col) if name not in df.columns]
    if missing:
        raise ValueError(f"missing required telemetry columns: {missing}")

    out = df.copy()
    out[time_col] = pd.to_datetime(out[time_col], utc=True, errors="coerce")
    if out[time_col].isna().any():
        raise ValueError("time column contains invalid or NaT values")

    out[b_mag_col] = pd.to_numeric(out[b_mag_col], errors="coerce")
    values = out[b_mag_col].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("magnetic magnitude contains non-finite values")
    if (values < 0).any():
        raise ValueError("magnetic magnitude cannot be negative")

    out.sort_values(time_col, inplace=True)
    if out[time_col].duplicated().any():
        raise ValueError("duplicate telemetry timestamps are not admitted to the core")
    if not out[time_col].is_monotonic_increasing:
        raise ValueError("telemetry timestamps must be strictly increasing")
    return out


def calculate_trailing_median_baseline(
    df: pd.DataFrame,
    time_col: str,
    b_mag_col: str,
    *,
    window_hours: int = 24,
    min_coverage: float = 0.95,
    expected_cadence_seconds: float | None = None,
) -> pd.DataFrame:
    """Compute B0, ratio, signed delta, and unclipped absolute chi.

    Baseline status values:
    - WARMUP: a full elapsed window is unavailable.
    - INSUFFICIENT_COVERAGE: full window exists but too many samples are absent.
    - BASELINE_NONPOSITIVE: median baseline is zero or negative.
    - VALID: ratio/delta/chi are admissible.
    """
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")

    out = _validate_input(df, time_col=time_col, b_mag_col=b_mag_col)
    if len(out) < 2:
        raise ValueError("at least two telemetry rows are required")

    indexed = out.set_index(time_col)
    diffs = indexed.index.to_series().diff().dt.total_seconds().dropna()
    if (diffs <= 0).any():
        raise ValueError("telemetry cadence must be strictly positive")

    if expected_cadence_seconds is None:
        expected_cadence_seconds = float(diffs.median())
    if not np.isfinite(expected_cadence_seconds) or expected_cadence_seconds <= 0:
        raise ValueError("expected_cadence_seconds must be finite and positive")

    expected_samples = (window_hours * 3600.0) / float(expected_cadence_seconds)
    if expected_samples < 1:
        raise ValueError("declared cadence is longer than the baseline window")

    rolling = indexed[b_mag_col].rolling(f"{window_hours}h", closed="left")
    rolling_median = rolling.median()
    rolling_count = rolling.count()
    coverage = rolling_count / expected_samples

    full_window = (indexed.index - indexed.index[0]) >= pd.Timedelta(hours=window_hours)
    sufficient = coverage >= min_coverage
    positive = rolling_median > 0

    status = np.full(len(indexed), "WARMUP", dtype=object)
    status[full_window & ~sufficient] = "INSUFFICIENT_COVERAGE"
    status[full_window & sufficient & ~positive] = "BASELINE_NONPOSITIVE"
    valid = full_window & sufficient & positive
    status[valid] = "VALID"

    indexed["baseline_sample_count"] = rolling_count.astype("Int64")
    indexed["baseline_expected_samples"] = float(expected_samples)
    indexed["baseline_coverage_fraction"] = coverage
    indexed["baseline_status"] = status
    indexed["B0"] = rolling_median.where(valid)

    indexed["ratio_B24M"] = np.nan
    indexed["delta_B24M"] = np.nan
    indexed["chi_B24M"] = np.nan
    indexed.loc[valid, "ratio_B24M"] = (
        indexed.loc[valid, b_mag_col] / indexed.loc[valid, "B0"]
    )
    indexed.loc[valid, "delta_B24M"] = (
        indexed.loc[valid, b_mag_col] - indexed.loc[valid, "B0"]
    ) / indexed.loc[valid, "B0"]
    indexed.loc[valid, "chi_B24M"] = np.abs(indexed.loc[valid, "delta_B24M"])

    computed = indexed.loc[valid, ["ratio_B24M", "delta_B24M", "chi_B24M"]]
    if not np.isfinite(computed.to_numpy(dtype=float)).all():
        raise ValueError("non-finite canonical metrics were generated")

    return indexed.reset_index()


def run_chain(
    telemetry_df: pd.DataFrame,
    time_col: str,
    b_mag_col: str,
    *,
    expected_cadence_seconds: float | None = None,
    window_hours: int = 24,
    min_coverage: float = 0.95,
) -> pd.DataFrame:
    return calculate_trailing_median_baseline(
        telemetry_df,
        time_col,
        b_mag_col,
        window_hours=window_hours,
        min_coverage=min_coverage,
        expected_cadence_seconds=expected_cadence_seconds,
    )
