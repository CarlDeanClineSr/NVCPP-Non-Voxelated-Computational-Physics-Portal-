"""Event-local frozen magnetic references for canonical NVCPP records.

This module does not replace ``B0``, ``delta_B24M``, or ``chi_B24M``. It opens a
separate event overlay after a named gate, freezes the last valid pre-gate live
baseline, and compares later magnetic magnitude against that fixed reference.

The baseline-regime labels describe the relationship between the two metrics;
they are not declarations of physical storm phase.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

EVENT_REFERENCE_VERSION = "1.0.0"


class EventReferenceError(RuntimeError):
    """Raised when an event-local reference cannot be derived safely."""


@dataclass(frozen=True)
class EventReferenceColumns:
    time: str
    bx: str
    by: str
    bz: str
    magnitude: str = "B_mag"
    live_baseline: str = "B0"
    live_delta: str = "delta_B24M"
    live_chi: str = "chi_B24M"
    baseline_status: str = "baseline_status"
    native_sample_count: str | None = "native_sample_count"


@dataclass(frozen=True)
class EventReferencePolicy:
    gate_id: str = "CHI_ABSB_GSE_1MIN_SEVERE_COMPRESSION"
    gate_threshold: float = 1.0
    require_positive_delta: bool = True
    research_watch_chi: float = 0.15
    frozen_severe_chi: float = 1.0
    cadence_seconds: float = 60.0
    local_integrity_half_window_minutes: int = 5
    minimum_native_samples_per_minute: int = 57
    rotation_candidate_degrees: float = 45.0
    magnitude_jump_candidate_fraction: float = 0.25

    def manifest(self) -> dict[str, Any]:
        return {
            "policy_version": EVENT_REFERENCE_VERSION,
            **asdict(self),
            "gate_quantity": "chi_B24M(|<B_GSE>_1min|)",
            "gate_reference": "live prior-only 24-hour median B0",
            "gate_operator": ">=",
            "reference_selection": "last baseline_status=VALID row before gate",
            "frozen_quantity": "|<B_GSE>_1min|",
            "regime_definitions": {
                "PRE_EVENT": "timestamp precedes the derived gate",
                "MEDIAN_ADAPTING": (
                    "timestamp is at or after the gate and the live metric has not "
                    "suppressed a still-severe frozen departure"
                ),
                "EVENT_ABSORBED_BY_LIVE_BASELINE": (
                    "live chi is below the research-watch level while frozen chi "
                    "remains at or above the frozen-severe level"
                ),
            },
            "interpretation_limits": [
                "the frozen overlay does not replace the canonical rolling metric",
                "baseline regime is a metric-state label, not a physical storm-phase claim",
                "GSE vector rotation is not a GSM clock angle",
                "a gate is a reproducible analysis opening condition, not shock proof",
            ],
        }


def _prepare(frame: pd.DataFrame, columns: EventReferenceColumns) -> pd.DataFrame:
    required = [
        columns.time,
        columns.bx,
        columns.by,
        columns.bz,
        columns.magnitude,
        columns.live_baseline,
        columns.live_delta,
        columns.live_chi,
        columns.baseline_status,
    ]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise EventReferenceError(f"event-reference input is missing columns: {missing}")

    result = frame.copy()
    result[columns.time] = pd.to_datetime(result[columns.time], utc=True, errors="coerce")
    if result[columns.time].isna().any():
        raise EventReferenceError("event-reference input contains invalid timestamps")

    for name in (
        columns.bx,
        columns.by,
        columns.bz,
        columns.magnitude,
        columns.live_baseline,
        columns.live_delta,
        columns.live_chi,
    ):
        result[name] = pd.to_numeric(result[name], errors="coerce")

    result.sort_values(columns.time, inplace=True)
    if result[columns.time].duplicated().any():
        raise EventReferenceError("event-reference input contains duplicate timestamps")
    if result.empty:
        raise EventReferenceError("event-reference input is empty")
    return result.reset_index(drop=True)


def _derive_gate_time(
    frame: pd.DataFrame,
    columns: EventReferenceColumns,
    policy: EventReferencePolicy,
) -> pd.Timestamp:
    valid = frame[columns.baseline_status].astype(str).eq("VALID")
    gate = valid & frame[columns.live_chi].ge(policy.gate_threshold)
    if policy.require_positive_delta:
        gate &= frame[columns.live_delta].gt(0)
    candidates = frame.loc[gate, columns.time]
    if candidates.empty:
        raise EventReferenceError(
            f"no row satisfies gate {policy.gate_id} at threshold {policy.gate_threshold}"
        )
    return pd.Timestamp(candidates.iloc[0])


def _derive_reference_row(
    frame: pd.DataFrame,
    columns: EventReferenceColumns,
    gate_time: pd.Timestamp,
) -> pd.Series:
    candidates = frame.loc[
        (frame[columns.time] < gate_time)
        & frame[columns.baseline_status].astype(str).eq("VALID")
        & np.isfinite(frame[columns.live_baseline])
        & frame[columns.live_baseline].gt(0)
    ]
    if candidates.empty:
        raise EventReferenceError("no finite positive valid baseline exists before the gate")
    return candidates.iloc[-1]


def _rotation_from_reference(
    frame: pd.DataFrame,
    columns: EventReferenceColumns,
    reference_row: pd.Series,
) -> np.ndarray:
    vectors = frame[[columns.bx, columns.by, columns.bz]].to_numpy(dtype=float)
    reference = reference_row[[columns.bx, columns.by, columns.bz]].to_numpy(dtype=float)
    reference_norm = float(np.linalg.norm(reference))
    norms = np.linalg.norm(vectors, axis=1)
    result = np.full(len(frame), np.nan, dtype=float)
    valid = np.isfinite(vectors).all(axis=1) & np.isfinite(norms) & (norms > 0)
    if not np.isfinite(reference_norm) or reference_norm <= 0:
        return result
    if valid.any():
        cosine = np.sum(vectors[valid] * reference, axis=1) / (norms[valid] * reference_norm)
        result[valid] = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    return result


def _rotation_from_previous(
    frame: pd.DataFrame,
    columns: EventReferenceColumns,
) -> np.ndarray:
    vectors = frame[[columns.bx, columns.by, columns.bz]].to_numpy(dtype=float)
    norms = np.linalg.norm(vectors, axis=1)
    result = np.full(len(frame), np.nan, dtype=float)
    if len(frame) < 2:
        return result
    previous = vectors[:-1]
    current = vectors[1:]
    previous_norms = norms[:-1]
    current_norms = norms[1:]
    valid = (
        np.isfinite(previous).all(axis=1)
        & np.isfinite(current).all(axis=1)
        & (previous_norms > 0)
        & (current_norms > 0)
    )
    if valid.any():
        dot = np.einsum("ij,ij->i", previous[valid], current[valid])
        cosine = dot / (previous_norms[valid] * current_norms[valid])
        result[np.flatnonzero(valid) + 1] = np.degrees(
            np.arccos(np.clip(cosine, -1.0, 1.0))
        )
    return result


def attach_event_reference(
    frame: pd.DataFrame,
    *,
    columns: EventReferenceColumns,
    policy: EventReferencePolicy | None = None,
    gate_time: str | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach a reproducible frozen event reference to canonical rows."""
    policy = policy or EventReferencePolicy()
    prepared = _prepare(frame, columns)
    derived_gate = (
        _derive_gate_time(prepared, columns, policy)
        if gate_time is None
        else pd.Timestamp(gate_time)
    )
    if derived_gate.tzinfo is None:
        derived_gate = derived_gate.tz_localize("UTC")
    else:
        derived_gate = derived_gate.tz_convert("UTC")

    gate_rows = prepared.loc[prepared[columns.time].eq(derived_gate)]
    if len(gate_rows) != 1:
        raise EventReferenceError(
            f"gate timestamp must match exactly one row; found {len(gate_rows)}"
        )
    reference_row = _derive_reference_row(prepared, columns, derived_gate)
    reference_time = pd.Timestamp(reference_row[columns.time])
    reference_B = float(reference_row[columns.live_baseline])

    prepared["event_gate_time_utc"] = derived_gate
    prepared["event_reference_time_utc"] = reference_time
    prepared["event_reference_B"] = reference_B
    prepared["ratio_event_ref_absB"] = prepared[columns.magnitude] / reference_B
    prepared["delta_event_ref_absB"] = (
        prepared[columns.magnitude] - reference_B
    ) / reference_B
    prepared["chi_event_ref_absB"] = prepared["delta_event_ref_absB"].abs()
    prepared["rotation_from_event_ref_degrees"] = _rotation_from_reference(
        prepared, columns, reference_row
    )
    prepared["rotation_from_previous_minute_degrees"] = _rotation_from_previous(
        prepared, columns
    )
    previous = prepared[columns.magnitude].shift(1)
    prepared["minute_relative_magnitude_change"] = np.where(
        previous.abs() > 0,
        (prepared[columns.magnitude] - previous).abs() / previous.abs(),
        np.nan,
    )

    regime = np.full(len(prepared), "MEDIAN_ADAPTING", dtype=object)
    regime[prepared[columns.time] < derived_gate] = "PRE_EVENT"
    absorbed = (
        prepared[columns.time].ge(derived_gate)
        & prepared[columns.live_chi].lt(policy.research_watch_chi)
        & prepared["chi_event_ref_absB"].ge(policy.frozen_severe_chi)
    )
    regime[absorbed] = "EVENT_ABSORBED_BY_LIVE_BASELINE"
    prepared["baseline_regime"] = regime

    gate_row = gate_rows.iloc[0]
    metadata = {
        "event_reference_version": EVENT_REFERENCE_VERSION,
        "status": "SUCCESS",
        "gate": {
            "gate_id": policy.gate_id,
            "derived_time_utc": derived_gate.isoformat(),
            "observed_live_chi": float(gate_row[columns.live_chi]),
            "observed_live_delta": float(gate_row[columns.live_delta]),
        },
        "reference": {
            "selection": "last valid pre-gate live baseline",
            "time_utc": reference_time.isoformat(),
            "B_nT": reference_B,
        },
        "policy": policy.manifest(),
        "regime_counts": {
            str(key): int(value)
            for key, value in prepared["baseline_regime"].value_counts().items()
        },
    }
    return prepared, metadata


def local_integrity_gate(
    frame: pd.DataFrame,
    *,
    timestamp: str | pd.Timestamp,
    columns: EventReferenceColumns,
    policy: EventReferencePolicy | None = None,
) -> dict[str, Any]:
    """Check exact local cadence and baseline/native coverage around one time."""
    policy = policy or EventReferencePolicy()
    prepared = _prepare(frame, columns)
    target = pd.Timestamp(timestamp)
    target = target.tz_localize("UTC") if target.tzinfo is None else target.tz_convert("UTC")
    step = pd.Timedelta(seconds=policy.cadence_seconds)
    half = policy.local_integrity_half_window_minutes
    expected = pd.date_range(
        target - pd.Timedelta(minutes=half),
        target + pd.Timedelta(minutes=half),
        freq=step,
        tz="UTC",
    )
    local = prepared.loc[
        prepared[columns.time].between(expected[0], expected[-1], inclusive="both")
    ].copy()
    observed = pd.DatetimeIndex(local[columns.time])
    missing = expected.difference(observed)
    baseline_valid = bool(local[columns.baseline_status].astype(str).eq("VALID").all())
    native_ok = True
    minimum_native: int | None = None
    if columns.native_sample_count and columns.native_sample_count in local.columns:
        native = pd.to_numeric(local[columns.native_sample_count], errors="coerce")
        minimum_native = int(native.min()) if len(native) and native.notna().all() else None
        native_ok = bool(
            native.notna().all()
            and native.ge(policy.minimum_native_samples_per_minute).all()
        )
    passed = bool(
        len(local) == len(expected)
        and not len(missing)
        and baseline_valid
        and native_ok
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "target_time_utc": target.isoformat(),
        "half_window_minutes": half,
        "expected_rows": int(len(expected)),
        "observed_rows": int(len(local)),
        "missing_timestamps": [value.isoformat() for value in missing],
        "baseline_valid": baseline_valid,
        "minimum_native_samples": minimum_native,
        "minimum_required_native_samples": policy.minimum_native_samples_per_minute,
    }


def select_later_structure_candidate(
    frame: pd.DataFrame,
    *,
    columns: EventReferenceColumns,
    policy: EventReferencePolicy | None = None,
    after: str | pd.Timestamp | None = None,
) -> tuple[pd.Series, dict[str, Any]]:
    """Select a later vector/magnitude structure hidden by the live chi state.

    Selection is deterministic: among rows where live chi is below the research
    watch, frozen chi remains severe, and either the one-minute rotation or
    magnitude-jump threshold fires, choose the largest normalized trigger score.
    """
    policy = policy or EventReferencePolicy()
    required = {
        "chi_event_ref_absB",
        "rotation_from_previous_minute_degrees",
        "minute_relative_magnitude_change",
        "baseline_regime",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise EventReferenceError(f"annotated event-reference rows are missing: {missing}")

    prepared = _prepare(frame, columns)
    for name in required:
        prepared[name] = frame[name].to_numpy()

    if after is None:
        absorbed = prepared.loc[
            prepared["baseline_regime"].eq("EVENT_ABSORBED_BY_LIVE_BASELINE"),
            columns.time,
        ]
        if absorbed.empty:
            raise EventReferenceError("no live-baseline absorption row exists")
        after_time = pd.Timestamp(absorbed.iloc[0])
    else:
        after_time = pd.Timestamp(after)
        after_time = (
            after_time.tz_localize("UTC")
            if after_time.tzinfo is None
            else after_time.tz_convert("UTC")
        )

    candidates = prepared.loc[
        prepared[columns.time].ge(after_time)
        & prepared[columns.live_chi].lt(policy.research_watch_chi)
        & prepared["chi_event_ref_absB"].ge(policy.frozen_severe_chi)
        & (
            prepared["rotation_from_previous_minute_degrees"].ge(
                policy.rotation_candidate_degrees
            )
            | prepared["minute_relative_magnitude_change"].ge(
                policy.magnitude_jump_candidate_fraction
            )
        )
    ].copy()
    if candidates.empty:
        raise EventReferenceError("no later structure satisfies the frozen/live selection")

    rotation_score = (
        candidates["rotation_from_previous_minute_degrees"]
        / policy.rotation_candidate_degrees
    ).fillna(0.0)
    magnitude_score = (
        candidates["minute_relative_magnitude_change"]
        / policy.magnitude_jump_candidate_fraction
    ).fillna(0.0)
    candidates["_selection_score"] = np.maximum(rotation_score, magnitude_score)
    selected = candidates.sort_values(
        ["_selection_score", columns.time], ascending=[False, True]
    ).iloc[0]
    evidence = {
        "selection_rule": (
            "after first live-baseline absorption; live chi below watch; frozen chi "
            "severe; vector rotation or magnitude jump; maximum normalized trigger score"
        ),
        "selected_time_utc": pd.Timestamp(selected[columns.time]).isoformat(),
        "selection_score": float(selected["_selection_score"]),
        "live_chi": float(selected[columns.live_chi]),
        "frozen_chi": float(selected["chi_event_ref_absB"]),
        "rotation_degrees": float(selected["rotation_from_previous_minute_degrees"]),
        "magnitude_jump_fraction": float(selected["minute_relative_magnitude_change"]),
    }
    return selected, evidence
