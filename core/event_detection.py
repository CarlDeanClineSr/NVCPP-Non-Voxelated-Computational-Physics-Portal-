"""Physics-aware candidate event detection for canonical NVCPP magnetic records.

The detector does not promote an event candidate into a physical mechanism.
It preserves signed departures, vector rotation, magnitude jumps, and the
absolute chi severity so later mission, plasma, imagery, and ephemeris tests
can distinguish compression, depression, rotation, and bad telemetry.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

EVENT_DETECTOR_VERSION = "1.1.0"


@dataclass(frozen=True)
class EventThresholds:
    research_watch_chi: float = 0.15
    significant_chi: float = 0.50
    severe_chi: float = 1.00
    rotation_degrees: float = 45.0
    severe_rotation_degrees: float = 120.0
    minute_relative_magnitude_change: float = 0.25
    minimum_field_nT_for_rotation: float = 0.10
    merge_gap_minutes: int = 2

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "EventThresholds":
        if not values:
            return cls()
        allowed = {field for field in cls.__dataclass_fields__}
        unexpected = sorted(set(values) - allowed)
        if unexpected:
            raise ValueError(f"unknown event-threshold keys: {unexpected}")
        return cls(**values)


@dataclass(frozen=True)
class CanonicalColumns:
    time: str
    bx: str
    by: str
    bz: str
    magnitude: str = "B_mag"
    delta: str = "delta_B24M"
    chi: str = "chi_B24M"
    baseline_valid: str = "baseline_valid"


class EventDetectionError(RuntimeError):
    pass


def _ensure_utc(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    if parsed.isna().any():
        raise EventDetectionError("event input contains invalid timestamps")
    return parsed


def _numeric(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def _rotation_degrees(
    frame: pd.DataFrame,
    columns: CanonicalColumns,
    minimum_field_nT: float,
) -> np.ndarray:
    vectors = frame[[columns.bx, columns.by, columns.bz]].to_numpy(dtype=float)
    norms = np.linalg.norm(vectors, axis=1)
    result = np.full(len(frame), np.nan, dtype=float)
    if len(frame) < 2:
        return result

    previous = vectors[:-1]
    current = vectors[1:]
    prev_norm = norms[:-1]
    cur_norm = norms[1:]
    valid = (
        np.isfinite(previous).all(axis=1)
        & np.isfinite(current).all(axis=1)
        & (prev_norm >= minimum_field_nT)
        & (cur_norm >= minimum_field_nT)
    )
    if valid.any():
        dot = np.einsum("ij,ij->i", previous[valid], current[valid])
        cosine = dot / (prev_norm[valid] * cur_norm[valid])
        cosine = np.clip(cosine, -1.0, 1.0)
        result[np.flatnonzero(valid) + 1] = np.degrees(np.arccos(cosine))
    return result


def _stable_event_id(mission: str, start: pd.Timestamp, end: pd.Timestamp, codes: list[str]) -> str:
    identity = json.dumps(
        {
            "mission": mission,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "codes": sorted(codes),
        },
        sort_keys=True,
    ).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:10]
    return f"NVCPP-{mission}-{start.strftime('%Y%m%dT%H%MZ')}-{suffix}"


def _threshold_contract(
    thresholds: EventThresholds,
    columns: CanonicalColumns,
) -> dict[str, dict[str, Any]]:
    live_reference = "live prior-only 24-hour median B0"
    return {
        "CHI_RESEARCH_WATCH": {
            "metric": columns.chi,
            "source_quantity": columns.magnitude,
            "reference": live_reference,
            "operator": ">=",
            "threshold": thresholds.research_watch_chi,
            "creates_event_by_itself": False,
            "meaning": "low-level absolute magnetic-magnitude departure watch",
            "not_equivalent_to": ["shock confirmation", "geoeffectiveness", "ICME phase"],
        },
        "MAG_COMPRESSION_CANDIDATE": {
            "metric": columns.delta,
            "source_quantity": columns.magnitude,
            "reference": live_reference,
            "operator": ">=",
            "threshold": thresholds.significant_chi,
            "creates_event_by_itself": True,
            "meaning": "positive signed departure from the live magnetic baseline",
        },
        "MAG_DEPRESSION_CANDIDATE": {
            "metric": columns.delta,
            "source_quantity": columns.magnitude,
            "reference": live_reference,
            "operator": "<=",
            "threshold": -thresholds.significant_chi,
            "creates_event_by_itself": True,
            "meaning": "negative signed departure from the live magnetic baseline",
        },
        "SEVERE_MAGNETIC_DEPARTURE": {
            "metric": columns.chi,
            "source_quantity": columns.magnitude,
            "reference": live_reference,
            "operator": ">=",
            "threshold": thresholds.severe_chi,
            "creates_event_by_itself": True,
            "meaning": "severe absolute departure from the live magnetic baseline",
            "not_equivalent_to": ["shock confirmation", "southward Bz", "ICME phase"],
        },
        "FIELD_ROTATION_CANDIDATE": {
            "metric": "rotation_degrees",
            "source_quantity": [columns.bx, columns.by, columns.bz],
            "reference": "previous admitted one-minute vector",
            "operator": ">=",
            "threshold": thresholds.rotation_degrees,
            "creates_event_by_itself": True,
            "meaning": "one-minute vector-direction change",
        },
        "MAGNITUDE_JUMP_CANDIDATE": {
            "metric": "magnitude_relative_change",
            "source_quantity": columns.magnitude,
            "reference": "previous admitted one-minute magnitude",
            "operator": ">=",
            "threshold": thresholds.minute_relative_magnitude_change,
            "creates_event_by_itself": True,
            "meaning": "one-minute absolute relative magnitude change",
        },
    }


def _row_codes(row: pd.Series, thresholds: EventThresholds) -> list[str]:
    codes: list[str] = []
    chi = row.get("_chi")
    delta = row.get("_delta")
    rotation = row.get("rotation_degrees")
    jump = row.get("magnitude_relative_change")

    if np.isfinite(chi) and chi >= thresholds.research_watch_chi:
        codes.append("CHI_RESEARCH_WATCH")
    if np.isfinite(delta) and delta >= thresholds.significant_chi:
        codes.append("MAG_COMPRESSION_CANDIDATE")
    if np.isfinite(delta) and delta <= -thresholds.significant_chi:
        codes.append("MAG_DEPRESSION_CANDIDATE")
    if np.isfinite(chi) and chi >= thresholds.severe_chi:
        codes.append("SEVERE_MAGNETIC_DEPARTURE")
    if np.isfinite(rotation) and rotation >= thresholds.rotation_degrees:
        codes.append("FIELD_ROTATION_CANDIDATE")
    if np.isfinite(jump) and jump >= thresholds.minute_relative_magnitude_change:
        codes.append("MAGNITUDE_JUMP_CANDIDATE")
    return codes


def _dominant_type(group: pd.DataFrame) -> str:
    maximum_delta = float(group["_delta"].max())
    minimum_delta = float(group["_delta"].min())
    maximum_rotation = float(group["rotation_degrees"].max(skipna=True))
    if np.isfinite(maximum_rotation) and maximum_rotation >= 45.0:
        if abs(minimum_delta) < 0.5 and maximum_delta < 0.5:
            return "FIELD_ROTATION_CANDIDATE"
    if abs(minimum_delta) > maximum_delta:
        return "MAG_DEPRESSION_CANDIDATE"
    if maximum_delta > 0:
        return "MAG_COMPRESSION_CANDIDATE"
    return "MAGNETIC_STRUCTURE_CANDIDATE"


def prepare_event_frame(
    frame: pd.DataFrame,
    columns: CanonicalColumns,
    thresholds: EventThresholds,
) -> pd.DataFrame:
    required = [
        columns.time,
        columns.bx,
        columns.by,
        columns.bz,
        columns.magnitude,
        columns.delta,
        columns.chi,
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise EventDetectionError(f"event input is missing canonical columns: {missing}")

    prepared = frame.copy()
    prepared[columns.time] = _ensure_utc(prepared[columns.time])
    _numeric(
        prepared,
        [
            columns.bx,
            columns.by,
            columns.bz,
            columns.magnitude,
            columns.delta,
            columns.chi,
        ],
    )
    if columns.baseline_valid in prepared.columns:
        valid = prepared[columns.baseline_valid].fillna(False).astype(bool)
        prepared = prepared.loc[valid].copy()
    prepared.sort_values(columns.time, inplace=True)
    if prepared[columns.time].duplicated().any():
        raise EventDetectionError("event input contains duplicate timestamps")
    if prepared.empty:
        raise EventDetectionError("event input has no baseline-valid canonical rows")

    prepared["_time"] = prepared[columns.time]
    prepared["_B"] = prepared[columns.magnitude]
    prepared["_delta"] = prepared[columns.delta]
    prepared["_chi"] = prepared[columns.chi]
    prepared["rotation_degrees"] = _rotation_degrees(
        prepared,
        columns,
        thresholds.minimum_field_nT_for_rotation,
    )
    previous = prepared["_B"].shift(1)
    denominator_valid = previous.abs() >= thresholds.minimum_field_nT_for_rotation
    prepared["magnitude_relative_change"] = np.where(
        denominator_valid,
        (prepared["_B"] - previous).abs() / previous.abs(),
        np.nan,
    )
    prepared["trigger_codes"] = prepared.apply(
        lambda row: _row_codes(row, thresholds), axis=1
    )
    prepared["watch"] = prepared["_chi"] >= thresholds.research_watch_chi
    prepared["candidate"] = prepared["trigger_codes"].apply(
        lambda codes: any(code != "CHI_RESEARCH_WATCH" for code in codes)
    )
    return prepared


def detect_events(
    frame: pd.DataFrame,
    *,
    mission: str,
    columns: CanonicalColumns,
    thresholds: EventThresholds | None = None,
    focus_start: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    thresholds = thresholds or EventThresholds()
    threshold_contract = _threshold_contract(thresholds, columns)
    prepared = prepare_event_frame(frame, columns, thresholds)
    if focus_start is not None:
        focus = pd.Timestamp(focus_start)
        focus = focus.tz_localize("UTC") if focus.tzinfo is None else focus.tz_convert("UTC")
    else:
        focus = prepared["_time"].min()

    candidates = prepared.loc[prepared["candidate"] & (prepared["_time"] >= focus)].copy()
    events: list[dict[str, Any]] = []
    if not candidates.empty:
        gap = candidates["_time"].diff() > pd.Timedelta(minutes=thresholds.merge_gap_minutes)
        candidates["_group"] = gap.fillna(False).cumsum()
        for _, group in candidates.groupby("_group", sort=True):
            start = group["_time"].min()
            end = group["_time"].max()
            codes = sorted({code for codes in group["trigger_codes"] for code in codes})
            max_chi = float(group["_chi"].max())
            max_rotation = float(group["rotation_degrees"].max(skipna=True))
            severity = "SEVERE" if (
                max_chi >= thresholds.severe_chi
                or (np.isfinite(max_rotation) and max_rotation >= thresholds.severe_rotation_degrees)
            ) else "SIGNIFICANT"
            max_jump_value = group["magnitude_relative_change"].max(skipna=True)
            max_jump = float(max_jump_value) if np.isfinite(max_jump_value) else None
            event = {
                "event_id": _stable_event_id(mission, start, end, codes),
                "status": "CANDIDATE_UNRESOLVED",
                "mission": mission,
                "start_utc": start.isoformat(),
                "end_utc": end.isoformat(),
                "duration_minutes": int((end - start).total_seconds() // 60) + 1,
                "rows": int(len(group)),
                "severity": severity,
                "dominant_type": _dominant_type(group),
                "trigger_codes": codes,
                "trigger_evidence": [threshold_contract[code] for code in codes],
                "max_chi_B24M": max_chi,
                "min_delta_B24M": float(group["_delta"].min()),
                "max_delta_B24M": float(group["_delta"].max()),
                "min_B_nT": float(group["_B"].min()),
                "max_B_nT": float(group["_B"].max()),
                "max_rotation_degrees": max_rotation if np.isfinite(max_rotation) else None,
                "max_minute_relative_magnitude_change": max_jump,
                "interpretation_limits": [
                    "candidate detection does not establish a physical mechanism",
                    "chi is absolute; signed delta is retained to separate compression and depression",
                    "rotation requires cross-mission, plasma, imagery, and/or ephemeris checks",
                    "quality and source provenance must remain attached to the event",
                ],
            }
            events.append(event)

    metrics = {
        "detector_version": EVENT_DETECTOR_VERSION,
        "mission": mission,
        "rows_evaluated": int(len(prepared)),
        "focus_start_utc": focus.isoformat(),
        "watch_rows": int((prepared["watch"] & (prepared["_time"] >= focus)).sum()),
        "candidate_rows": int(len(candidates)),
        "event_count": int(len(events)),
        "thresholds": thresholds.__dict__,
        "threshold_contract": threshold_contract,
        "latest": {
            "time_utc": prepared["_time"].iloc[-1].isoformat(),
            "B_nT": float(prepared["_B"].iloc[-1]),
            "delta_B24M": float(prepared["_delta"].iloc[-1]),
            "chi_B24M": float(prepared["_chi"].iloc[-1]),
            "rotation_degrees": (
                float(prepared["rotation_degrees"].iloc[-1])
                if np.isfinite(prepared["rotation_degrees"].iloc[-1])
                else None
            ),
        },
    }
    return prepared, events, metrics
