"""Secondary event-relative diagnostics for canonical NVCPP magnetic records.

This module does not modify or replace ``CLINE-L1-B24M-TRAIL-v1``.  The
canonical rolling baseline answers how far the field has moved from its recent
prior state.  A frozen event reference answers a different retrospective
question: how far the field remains from a selected, baseline-valid pre-event
state.

Every output is explicitly namespaced as ``event_reference`` so it cannot be
silently confused with ``B0``, ``delta_B24M``, or ``chi_B24M``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

EVENT_REFERENCE_VERSION = "1.0.0"


class EventReferenceError(RuntimeError):
    """Raised when an event-reference calculation cannot be audited safely."""


@dataclass(frozen=True)
class EventReferenceConfig:
    expected_cadence_seconds: float = 60.0
    local_half_window_minutes: int = 5
    minimum_native_coverage_fraction: float = 0.95


def _utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def add_frozen_event_reference(
    frame: pd.DataFrame,
    *,
    reference_time: str | pd.Timestamp,
    time_col: str,
    b_mag_col: str,
    baseline_col: str = "B0",
    baseline_status_col: str = "baseline_status",
    coordinate_frame: str | None = None,
    by_col: str | None = None,
    bz_col: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Add a frozen pre-event reference without altering canonical metrics.

    The exact reference row must exist and must have a finite, positive, valid
    rolling baseline.  The frozen value is the canonical ``B0`` at that row,
    not the instantaneous field magnitude.  Event-relative values are emitted
    only at and after the reference timestamp.

    When ``by_col``, ``bz_col``, and ``coordinate_frame`` are supplied, the
    function also emits an explicitly framed Y-Z clock angle:

    ``atan2(By, Bz)`` normalized to ``[0, 360)`` degrees.

    A GSE angle remains labeled GSE; this module never silently represents it
    as a GSM geoeffectiveness quantity.
    """

    required = [time_col, b_mag_col, baseline_col]
    if baseline_status_col:
        required.append(baseline_status_col)
    if by_col is not None or bz_col is not None or coordinate_frame is not None:
        if not (by_col and bz_col and coordinate_frame):
            raise EventReferenceError(
                "coordinate_frame, by_col, and bz_col must be supplied together"
            )
        required.extend([by_col, bz_col])

    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise EventReferenceError(f"event-reference input is missing columns: {missing}")

    result = frame.copy()
    result[time_col] = pd.to_datetime(result[time_col], utc=True, errors="coerce")
    if result[time_col].isna().any():
        raise EventReferenceError("event-reference input contains invalid timestamps")
    if result[time_col].duplicated().any():
        raise EventReferenceError("event-reference input contains duplicate timestamps")

    for column in [b_mag_col, baseline_col, by_col, bz_col]:
        if column is not None:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    if not np.isfinite(result[b_mag_col].to_numpy(dtype=float)).all():
        raise EventReferenceError("event-reference magnetic magnitude is non-finite")

    result.sort_values(time_col, inplace=True)
    reference = _utc_timestamp(reference_time)
    reference_rows = result.loc[result[time_col] == reference]
    if len(reference_rows) != 1:
        raise EventReferenceError(
            f"reference timestamp must match exactly one row; found {len(reference_rows)}"
        )

    reference_row = reference_rows.iloc[0]
    if baseline_status_col and str(reference_row[baseline_status_col]) != "VALID":
        raise EventReferenceError("reference row does not have a VALID canonical baseline")

    reference_b = float(reference_row[baseline_col])
    if not np.isfinite(reference_b) or reference_b <= 0:
        raise EventReferenceError("frozen event reference must be finite and positive")

    active = result[time_col] >= reference
    result["event_reference_active"] = active
    result["event_reference_time_utc"] = reference.isoformat()
    result["event_reference_B_nT"] = reference_b
    result["delta_event_reference"] = np.nan
    result["chi_event_reference"] = np.nan
    result.loc[active, "delta_event_reference"] = (
        result.loc[active, b_mag_col] - reference_b
    ) / reference_b
    result.loc[active, "chi_event_reference"] = result.loc[
        active, "delta_event_reference"
    ].abs()

    computed = result.loc[
        active, ["delta_event_reference", "chi_event_reference"]
    ].to_numpy(dtype=float)
    if not np.isfinite(computed).all():
        raise EventReferenceError("non-finite event-reference metrics were generated")

    clock_angle_column = None
    if by_col and bz_col and coordinate_frame:
        vectors = result[[by_col, bz_col]].to_numpy(dtype=float)
        if not np.isfinite(vectors).all():
            raise EventReferenceError("clock-angle inputs contain non-finite values")
        clock_angle_column = f"clock_angle_{coordinate_frame.lower()}_yz_deg"
        result[clock_angle_column] = (
            np.degrees(np.arctan2(result[by_col], result[bz_col])) + 360.0
        ) % 360.0

    metadata = {
        "event_reference_version": EVENT_REFERENCE_VERSION,
        "reference_time_utc": reference.isoformat(),
        "reference_B_nT": reference_b,
        "reference_source": baseline_col,
        "reference_policy": "exact baseline-valid pre-event row",
        "delta_definition": "(B - event_reference_B) / event_reference_B",
        "chi_definition": "abs(delta_event_reference)",
        "canonical_metrics_replaced": False,
        "coordinate_frame": coordinate_frame,
        "clock_angle_column": clock_angle_column,
        "clock_angle_definition": (
            "atan2(By, Bz), normalized to [0, 360) degrees"
            if clock_angle_column
            else None
        ),
    }
    return result, metadata


def event_local_integrity(
    frame: pd.DataFrame,
    *,
    center_time: str | pd.Timestamp,
    time_col: str,
    config: EventReferenceConfig | None = None,
    native_coverage_col: str | None = None,
    baseline_status_col: str | None = "baseline_status",
) -> dict[str, Any]:
    """Evaluate a centered retrospective data-integrity window.

    This gate establishes continuity and declared native coverage only.  It does
    not classify a shock, ejecta boundary, or physical mechanism.
    """

    config = config or EventReferenceConfig()
    if config.expected_cadence_seconds <= 0:
        raise EventReferenceError("expected cadence must be positive")
    if config.local_half_window_minutes < 0:
        raise EventReferenceError("local half-window cannot be negative")
    if not 0 < config.minimum_native_coverage_fraction <= 1:
        raise EventReferenceError("minimum native coverage must be in (0, 1]")

    required = [time_col]
    if native_coverage_col:
        required.append(native_coverage_col)
    if baseline_status_col:
        required.append(baseline_status_col)
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise EventReferenceError(f"local-integrity input is missing columns: {missing}")

    working = frame.copy()
    working[time_col] = pd.to_datetime(working[time_col], utc=True, errors="coerce")
    if working[time_col].isna().any():
        raise EventReferenceError("local-integrity input contains invalid timestamps")
    if working[time_col].duplicated().any():
        raise EventReferenceError("local-integrity input contains duplicate timestamps")
    working.set_index(time_col, inplace=True)

    center = _utc_timestamp(center_time)
    half = pd.Timedelta(minutes=config.local_half_window_minutes)
    cadence = pd.Timedelta(seconds=config.expected_cadence_seconds)
    expected = pd.date_range(center - half, center + half, freq=cadence, tz="UTC")
    local = working.reindex(expected)

    present = local.index.to_series().map(lambda value: value in working.index)
    if native_coverage_col:
        coverage = pd.to_numeric(local[native_coverage_col], errors="coerce")
        native_ok = coverage >= config.minimum_native_coverage_fraction
        minimum_coverage = float(coverage.min()) if coverage.notna().any() else None
    else:
        native_ok = pd.Series(True, index=local.index)
        minimum_coverage = None

    if baseline_status_col:
        baseline_ok = local[baseline_status_col].astype("string") == "VALID"
    else:
        baseline_ok = pd.Series(True, index=local.index)

    gate_pass = bool(present.all() and native_ok.all() and baseline_ok.all())
    return {
        "center_time_utc": center.isoformat(),
        "window_type": "centered retrospective",
        "half_window_minutes": config.local_half_window_minutes,
        "expected_cadence_seconds": config.expected_cadence_seconds,
        "expected_rows": int(len(expected)),
        "present_rows": int(present.sum()),
        "all_rows_present": bool(present.all()),
        "minimum_native_coverage_fraction": minimum_coverage,
        "all_native_coverage_valid": bool(native_ok.all()),
        "all_canonical_baselines_valid": bool(baseline_ok.all()),
        "event_local_integrity_pass": gate_pass,
        "interpretation": "data-integrity gate only; no mechanism classification",
    }
