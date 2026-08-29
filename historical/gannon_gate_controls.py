#!/usr/bin/env python3
"""Run hard null controls for the frozen Gannon three-spacecraft MAG gate.

The gate, cadence, exact-previous-minute requirement, and timing radii are read
from frozen contracts. This module does not tune thresholds, classify a physical
discontinuity, or calculate propagation. It measures:

1. deterministic circular-shift controls that preserve each spacecraft's gate
   density, gaps, clustering, and autocorrelation while breaking simultaneity;
2. predeclared mismatched-day controls selected by date offsets before retrieval.

The output is a background-calibration artifact, not a common-surface result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.gannon_gate_density import add_exact_minute_diagnostics
from historical.gannon_multipoint_audit import (
    canonicalize_vector_minutes,
    fetch_hapi,
    parse_cdas_rows,
    request_cdas_text,
)

CONTROL_VERSION = "1.0.0"
DAY_MINUTES = 1440


class ControlHarnessError(RuntimeError):
    """Raised when a frozen control cannot be evaluated without repair."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ControlHarnessError(f"required JSON file is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ControlHarnessError(f"expected JSON object in {path}")
    return value


def load_contract(path: Path) -> dict[str, Any]:
    contract = load_json(path)
    required = {
        "contract_id",
        "contract_version",
        "gate_density_contract",
        "circular_shift_null",
        "mismatched_day_null",
        "interpretation_limits",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ControlHarnessError(f"control contract lacks keys: {missing}")

    circular = contract["circular_shift_null"]
    if int(circular["iterations"]) < 100:
        raise ControlHarnessError("circular-shift null requires at least 100 iterations")
    if int(circular["seed"]) < 0:
        raise ControlHarnessError("circular-shift seed must be nonnegative")
    if int(circular["minimum_pairwise_separation_minutes"]) <= 15:
        raise ControlHarnessError(
            "circular shifts must exceed the frozen 15-minute support radius"
        )
    radii = [int(value) for value in circular["support_radii_minutes"]]
    if radii != [1, 2, 3, 5, 10, 15]:
        raise ControlHarnessError(f"frozen timing radii changed: {radii}")

    mismatch = contract["mismatched_day_null"]
    ace_offsets = [int(value) for value in mismatch["ace_day_offsets"]]
    wind_offsets = [int(value) for value in mismatch["wind_day_offsets"]]
    if not ace_offsets or not wind_offsets:
        raise ControlHarnessError("mismatched-day offsets must not be empty")
    if any(value == 0 for value in ace_offsets + wind_offsets):
        raise ControlHarnessError("mismatched-day offsets must break simultaneity")
    if mismatch.get("pairing") != "cartesian_product":
        raise ControlHarnessError("mismatched-day pairing must be cartesian_product")
    return contract


def validate_frozen_gate(
    control_contract: dict[str, Any],
    gate_manifest: dict[str, Any],
) -> dict[str, Any]:
    expected = control_contract["gate_density_contract"]
    actual_contract = gate_manifest.get("contract", {})
    actual_gate = gate_manifest.get("gate", {})
    mismatches: list[str] = []

    comparisons = {
        "contract_id": (
            expected.get("contract_id"),
            actual_contract.get("contract_id"),
        ),
        "contract_version": (
            expected.get("contract_version"),
            actual_contract.get("contract_version"),
        ),
        "rotation_threshold_degrees": (
            float(expected["rotation_threshold_degrees"]),
            float(actual_gate.get("rotation_threshold_degrees", np.nan)),
        ),
        "magnitude_change_threshold_fraction": (
            float(expected["magnitude_change_threshold_fraction"]),
            float(actual_gate.get("magnitude_change_threshold_fraction", np.nan)),
        ),
        "canonical_cadence_seconds": (
            int(expected["canonical_cadence_seconds"]),
            int(actual_gate.get("canonical_cadence_seconds", -1)),
        ),
        "support_half_window_minutes": (
            int(expected["support_half_window_minutes"]),
            int(actual_gate.get("support_half_window_minutes", -1)),
        ),
    }
    for name, (wanted, found) in comparisons.items():
        if wanted != found:
            mismatches.append(f"{name}: expected {wanted!r}, found {found!r}")
    if actual_gate.get("logical_operator") != "OR":
        mismatches.append(
            f"logical_operator: expected 'OR', found {actual_gate.get('logical_operator')!r}"
        )
    if mismatches:
        raise ControlHarnessError(
            "frozen gate does not match the control contract: " + "; ".join(mismatches)
        )
    return {
        name: {"expected": wanted, "actual": found, "matched": True}
        for name, (wanted, found) in comparisons.items()
    }


def load_gate_table(path: Path, *, mission: str) -> pd.DataFrame:
    if not path.is_file():
        raise ControlHarnessError(f"{mission} canonical table is absent: {path}")
    frame = pd.read_csv(path)
    required = {
        "time",
        "gate_pass",
        "gate_score",
        "exact_previous_minute",
        "native_samples",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ControlHarnessError(f"{mission} gate table lacks columns: {missing}")
    frame["time"] = pd.to_datetime(
        frame["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if frame["time"].isna().any():
        raise ControlHarnessError(f"{mission} gate table has invalid timestamps")
    if frame["time"].duplicated().any():
        raise ControlHarnessError(f"{mission} gate table has duplicate timestamps")
    frame["gate_pass"] = frame["gate_pass"].astype(bool)
    frame["exact_previous_minute"] = frame["exact_previous_minute"].astype(bool)
    frame["gate_score"] = pd.to_numeric(frame["gate_score"], errors="coerce")
    return frame.sort_values("time").reset_index(drop=True)


def minute_arrays(
    frame: pd.DataFrame,
    *,
    day_start: pd.Timestamp,
) -> dict[str, np.ndarray]:
    if day_start.tzinfo is None:
        raise ControlHarnessError("day_start must be timezone-aware")
    day_start = day_start.tz_convert("UTC")
    gate = np.zeros(DAY_MINUTES, dtype=bool)
    evaluable = np.zeros(DAY_MINUTES, dtype=bool)
    score = np.full(DAY_MINUTES, np.nan, dtype=float)
    minutes = (frame["time"] - day_start).dt.total_seconds() / 60.0
    if not np.allclose(minutes, np.round(minutes), equal_nan=False):
        raise ControlHarnessError("canonical gate table contains non-minute timestamps")
    indices = np.round(minutes).astype(int)
    valid = (indices >= 0) & (indices < DAY_MINUTES)
    if int(valid.sum()) != len(frame):
        raise ControlHarnessError("canonical gate table extends outside frozen day")
    if len(set(indices.tolist())) != len(indices):
        raise ControlHarnessError("canonical gate table maps duplicate minute indices")
    gate[indices] = frame["gate_pass"].to_numpy(dtype=bool)
    evaluable[indices] = frame["exact_previous_minute"].to_numpy(dtype=bool)
    score[indices] = frame["gate_score"].to_numpy(dtype=float)
    return {"gate": gate, "evaluable": evaluable, "score": score}


def circular_distance(value: int, *, period: int = DAY_MINUTES) -> int:
    normalized = value % period
    return min(normalized, period - normalized)


def deterministic_shift_pairs(
    *,
    iterations: int,
    seed: int,
    minimum_pairwise_separation_minutes: int,
) -> list[tuple[int, int]]:
    candidates = np.array(
        [
            value
            for value in range(1, DAY_MINUTES)
            if circular_distance(value) >= minimum_pairwise_separation_minutes
        ],
        dtype=int,
    )
    if len(candidates) < 2:
        raise ControlHarnessError("no admissible circular shifts")
    rng = np.random.default_rng(seed)
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    maximum_unique = len(candidates) * (len(candidates) - 1)
    if iterations > maximum_unique:
        raise ControlHarnessError("requested more unique shift pairs than available")
    while len(pairs) < iterations:
        ace_shift = int(rng.choice(candidates))
        wind_shift = int(rng.choice(candidates))
        if circular_distance(ace_shift - wind_shift) < minimum_pairwise_separation_minutes:
            continue
        pair = (ace_shift, wind_shift)
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return pairs


def _nearest_index(
    gate: np.ndarray,
    *,
    center: int,
    half_window: int,
) -> int | None:
    lower = max(0, center - half_window)
    upper = min(DAY_MINUTES - 1, center + half_window)
    indices = np.flatnonzero(gate[lower : upper + 1]) + lower
    if len(indices) == 0:
        return None
    offsets = np.abs(indices - center)
    return int(indices[np.lexsort((indices, offsets))[0]])


def _strongest_index(
    gate: np.ndarray,
    score: np.ndarray,
    *,
    center: int,
    half_window: int,
) -> int | None:
    lower = max(0, center - half_window)
    upper = min(DAY_MINUTES - 1, center + half_window)
    indices = np.flatnonzero(gate[lower : upper + 1]) + lower
    if len(indices) == 0:
        return None
    values = np.nan_to_num(score[indices], nan=-np.inf)
    offsets = np.abs(indices - center)
    return int(indices[np.lexsort((indices, offsets, -values))[0]])


def support_metrics(
    *,
    dscovr_gate: np.ndarray,
    dscovr_evaluable: np.ndarray,
    ace_gate: np.ndarray,
    ace_score: np.ndarray,
    wind_gate: np.ndarray,
    wind_score: np.ndarray,
    candidate_minute: int,
    support_radii: Iterable[int],
    half_window: int,
    strongest_span_threshold: int,
) -> dict[str, Any]:
    minute_index = np.arange(DAY_MINUTES)
    anchors = np.flatnonzero(
        dscovr_evaluable
        & dscovr_gate
        & (minute_index >= half_window)
        & (minute_index < DAY_MINUTES - half_window)
    )
    if len(anchors) == 0:
        raise ControlHarnessError("no DSCOVR gate anchors are evaluable")
    if candidate_minute not in anchors:
        raise ControlHarnessError("frozen candidate is not an evaluable DSCOVR gate")

    nearest_radii = np.full(len(anchors), np.nan, dtype=float)
    strongest_spans = np.full(len(anchors), np.nan, dtype=float)
    for index, center in enumerate(anchors):
        ace_nearest = _nearest_index(ace_gate, center=int(center), half_window=half_window)
        wind_nearest = _nearest_index(wind_gate, center=int(center), half_window=half_window)
        if ace_nearest is not None and wind_nearest is not None:
            nearest_radii[index] = max(
                abs(ace_nearest - center),
                abs(wind_nearest - center),
            )

        ace_strongest = _strongest_index(
            ace_gate, ace_score, center=int(center), half_window=half_window
        )
        wind_strongest = _strongest_index(
            wind_gate, wind_score, center=int(center), half_window=half_window
        )
        if ace_strongest is not None and wind_strongest is not None:
            strongest_spans[index] = (
                max(center, ace_strongest, wind_strongest)
                - min(center, ace_strongest, wind_strongest)
            )

    result: dict[str, Any] = {
        "dscovr_gate_anchor_rows": int(len(anchors)),
        "support_fractions": {},
        "strongest_span_threshold_minutes": int(strongest_span_threshold),
        "strongest_span_fraction": float(
            np.mean(strongest_spans <= strongest_span_threshold)
        ),
    }
    for radius in support_radii:
        result["support_fractions"][str(int(radius))] = float(
            np.mean(nearest_radii <= int(radius))
        )

    ace_nearest = _nearest_index(ace_gate, center=candidate_minute, half_window=half_window)
    wind_nearest = _nearest_index(wind_gate, center=candidate_minute, half_window=half_window)
    ace_strongest = _strongest_index(
        ace_gate, ace_score, center=candidate_minute, half_window=half_window
    )
    wind_strongest = _strongest_index(
        wind_gate, wind_score, center=candidate_minute, half_window=half_window
    )
    result["candidate"] = {
        "minute_of_day": int(candidate_minute),
        "ace_nearest_offset_minutes": (
            int(ace_nearest - candidate_minute) if ace_nearest is not None else None
        ),
        "wind_nearest_offset_minutes": (
            int(wind_nearest - candidate_minute) if wind_nearest is not None else None
        ),
        "nearest_joint_radius_minutes": (
            int(max(abs(ace_nearest - candidate_minute), abs(wind_nearest - candidate_minute)))
            if ace_nearest is not None and wind_nearest is not None
            else None
        ),
        "ace_strongest_offset_minutes": (
            int(ace_strongest - candidate_minute) if ace_strongest is not None else None
        ),
        "wind_strongest_offset_minutes": (
            int(wind_strongest - candidate_minute)
            if wind_strongest is not None
            else None
        ),
        "strongest_three_spacecraft_span_minutes": (
            int(
                max(candidate_minute, ace_strongest, wind_strongest)
                - min(candidate_minute, ace_strongest, wind_strongest)
            )
            if ace_strongest is not None and wind_strongest is not None
            else None
        ),
    }
    return result


def flatten_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dscovr_gate_anchor_rows": metrics["dscovr_gate_anchor_rows"],
        "strongest_span_fraction": metrics["strongest_span_fraction"],
        "candidate_nearest_joint_radius_minutes": metrics["candidate"][
            "nearest_joint_radius_minutes"
        ],
        "candidate_strongest_three_spacecraft_span_minutes": metrics["candidate"][
            "strongest_three_spacecraft_span_minutes"
        ],
    }
    for radius, value in metrics["support_fractions"].items():
        result[f"joint_support_fraction_within_{radius}_minutes"] = value
    return result


def summarize_null(
    observed: dict[str, Any],
    controls: pd.DataFrame,
) -> dict[str, Any]:
    observed_flat = flatten_metrics(observed)
    summary: dict[str, Any] = {}
    lower_is_more_compact = {
        "candidate_nearest_joint_radius_minutes",
        "candidate_strongest_three_spacecraft_span_minutes",
    }
    for field, observed_value in observed_flat.items():
        if field == "dscovr_gate_anchor_rows":
            continue
        all_values = pd.to_numeric(controls[field], errors="coerce")
        finite_values = all_values.dropna()
        control_count = int(len(all_values))
        finite_count = int(len(finite_values))
        if control_count == 0:
            summary[field] = {
                "observed": observed_value,
                "controls": 0,
                "state": "NO_CONTROL_VALUES",
            }
            continue
        if field in lower_is_more_compact:
            equal_or_more_extreme = int(
                (all_values <= float(observed_value)).fillna(False).sum()
            )
            tail_direction = "less_than_or_equal"
        else:
            equal_or_more_extreme = int(
                (all_values >= float(observed_value)).fillna(False).sum()
            )
            tail_direction = "greater_than_or_equal"
        summary[field] = {
            "observed": observed_value,
            "controls": control_count,
            "finite_controls": finite_count,
            "controls_without_joint_support": control_count - finite_count,
            "control_minimum": float(finite_values.min()) if finite_count else None,
            "control_q05": float(finite_values.quantile(0.05)) if finite_count else None,
            "control_median": float(finite_values.median()) if finite_count else None,
            "control_q95": float(finite_values.quantile(0.95)) if finite_count else None,
            "control_maximum": float(finite_values.max()) if finite_count else None,
            "equal_or_more_extreme_controls": equal_or_more_extreme,
            "empirical_equal_or_more_extreme_fraction": float(
                equal_or_more_extreme / control_count
            ),
            "plus_one_tail_estimator": float(
                (equal_or_more_extreme + 1) / (control_count + 1)
            ),
            "tail_direction": tail_direction,
            "meaning": (
                "empirical frequency under this frozen control construction; "
                "controls with no joint support remain in the denominator; "
                "not an independent-minute probability and not a common-surface proof"
            ),
        }
    return summary


def circular_shift_controls(
    *,
    observed: dict[str, Any],
    arrays: dict[str, dict[str, np.ndarray]],
    settings: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pairs = deterministic_shift_pairs(
        iterations=int(settings["iterations"]),
        seed=int(settings["seed"]),
        minimum_pairwise_separation_minutes=int(
            settings["minimum_pairwise_separation_minutes"]
        ),
    )
    radii = [int(value) for value in settings["support_radii_minutes"]]
    candidate_minute = int(settings["candidate_minute_of_day"])
    half_window = int(settings["support_half_window_minutes"])
    span_threshold = int(settings["strongest_span_threshold_minutes"])

    rows: list[dict[str, Any]] = []
    for iteration, (ace_shift, wind_shift) in enumerate(pairs):
        metrics = support_metrics(
            dscovr_gate=arrays["DSCOVR"]["gate"],
            dscovr_evaluable=arrays["DSCOVR"]["evaluable"],
            ace_gate=np.roll(arrays["ACE"]["gate"], ace_shift),
            ace_score=np.roll(arrays["ACE"]["score"], ace_shift),
            wind_gate=np.roll(arrays["WIND"]["gate"], wind_shift),
            wind_score=np.roll(arrays["WIND"]["score"], wind_shift),
            candidate_minute=candidate_minute,
            support_radii=radii,
            half_window=half_window,
            strongest_span_threshold=span_threshold,
        )
        rows.append(
            {
                "control_type": "CIRCULAR_SHIFT",
                "iteration": iteration,
                "ace_shift_minutes": ace_shift,
                "wind_shift_minutes": wind_shift,
                "ace_shift_circular_distance_minutes": circular_distance(ace_shift),
                "wind_shift_circular_distance_minutes": circular_distance(wind_shift),
                "ace_wind_relative_circular_distance_minutes": circular_distance(
                    ace_shift - wind_shift
                ),
                **flatten_metrics(metrics),
            }
        )
    controls = pd.DataFrame(rows)
    return controls, summarize_null(observed, controls)


def canonical_control_day(
    *,
    mission: str,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    raw_root: Path,
    rotation_threshold: float,
    magnitude_threshold: float,
    session: requests.Session,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    day_label = start.strftime("%Y%m%d")
    if mission == "ACE":
        raw, metadata, _ = fetch_hapi(
            session,
            dataset_id="AC_H0_MFI",
            parameters=["Magnitude", "BGSEc", "SC_pos_GSE"],
            start=start.isoformat().replace("+00:00", "Z"),
            stop=stop.isoformat().replace("+00:00", "Z"),
            outdir=raw_root / f"ACE_{day_label}",
        )
        canonical, quarantine = canonicalize_vector_minutes(
            raw,
            components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
            position_components=("SC_pos_GSE_x", "SC_pos_GSE_y", "SC_pos_GSE_z"),
            minimum_samples=3,
            source=f"AC_H0_MFI_{day_label}",
        )
    elif mission == "WIND":
        raw_bytes, metadata = request_cdas_text(
            session,
            dataset_id="WI_H0_MFI",
            variables=["B3GSE", "B3F1"],
            start=start.isoformat().replace("+00:00", "Z"),
            stop=stop.isoformat().replace("+00:00", "Z"),
            outdir=raw_root / f"WIND_{day_label}",
        )
        raw = parse_cdas_rows(
            raw_bytes,
            columns=[
                "time",
                "reported_B3F1_nT",
                "B3GSE_x",
                "B3GSE_y",
                "B3GSE_z",
            ],
        )
        canonical, quarantine = canonicalize_vector_minutes(
            raw,
            components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
            minimum_samples=18,
            source=f"WI_H0_MFI_{day_label}",
        )
    else:
        raise ControlHarnessError(f"unsupported mismatched-day mission: {mission}")

    canonical = add_exact_minute_diagnostics(
        canonical,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    metadata["control_date_utc"] = start.date().isoformat()
    metadata["selection_state"] = "PREDECLARED_DATE_OFFSET_BEFORE_RETRIEVAL"
    return canonical, metadata, quarantine


def mismatched_day_controls(
    *,
    observed: dict[str, Any],
    dscovr_arrays: dict[str, np.ndarray],
    settings: dict[str, Any],
    gate: dict[str, Any],
    raw_root: Path,
    canonical_root: Path,
    quarantine_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    base_day = pd.Timestamp(settings["base_day_utc"]).tz_convert("UTC")
    candidate_minute = int(settings["candidate_minute_of_day"])
    radii = [int(value) for value in settings["support_radii_minutes"]]
    half_window = int(settings["support_half_window_minutes"])
    span_threshold = int(settings["strongest_span_threshold_minutes"])
    minimum_evaluable = int(settings["minimum_evaluable_rows_per_day"])
    rotation_threshold = float(gate["rotation_threshold_degrees"])
    magnitude_threshold = float(gate["magnitude_change_threshold_fraction"])

    # Every output directory is part of the executable contract. Local and
    # Actions runs must not depend on a workflow-created parent directory.
    for directory in (raw_root, canonical_root, quarantine_root):
        directory.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-HARD-NULLS/{CONTROL_VERSION}"}
    )
    frames: dict[str, dict[int, pd.DataFrame]] = {"ACE": {}, "WIND": {}}
    metadata: dict[str, Any] = {"ACE": {}, "WIND": {}}
    offsets_by_mission = {
        "ACE": [int(value) for value in settings["ace_day_offsets"]],
        "WIND": [int(value) for value in settings["wind_day_offsets"]],
    }
    for mission, offsets in offsets_by_mission.items():
        for offset in offsets:
            start = base_day + pd.Timedelta(days=offset)
            stop = start + pd.Timedelta(days=1)
            canonical, source, quarantine = canonical_control_day(
                mission=mission,
                start=start,
                stop=stop,
                raw_root=raw_root,
                rotation_threshold=rotation_threshold,
                magnitude_threshold=magnitude_threshold,
                session=session,
            )
            evaluable = int(canonical["exact_previous_minute"].sum())
            if evaluable < minimum_evaluable:
                raise ControlHarnessError(
                    f"{mission} offset {offset} has {evaluable} evaluable rows; "
                    f"requires {minimum_evaluable}"
                )
            frames[mission][offset] = canonical
            metadata[mission][str(offset)] = {
                "start_utc": start.isoformat(),
                "stop_utc": stop.isoformat(),
                "evaluable_exact_previous_rows": evaluable,
                "gate_rows": int(canonical["gate_pass"].sum()),
                "gate_fraction": float(canonical["gate_pass"].sum() / evaluable),
                "source": source,
            }
            canonical.to_csv(
                canonical_root / f"{mission.lower()}_offset_{offset:+d}_gate_table.csv",
                index=False,
            )
            quarantine.to_csv(
                quarantine_root / f"{mission.lower()}_offset_{offset:+d}_quarantine.csv",
                index=False,
            )

    rows: list[dict[str, Any]] = []
    for ace_offset, ace_frame in frames["ACE"].items():
        ace_arrays = minute_arrays(
            ace_frame,
            day_start=base_day + pd.Timedelta(days=ace_offset),
        )
        for wind_offset, wind_frame in frames["WIND"].items():
            wind_arrays = minute_arrays(
                wind_frame,
                day_start=base_day + pd.Timedelta(days=wind_offset),
            )
            metrics = support_metrics(
                dscovr_gate=dscovr_arrays["gate"],
                dscovr_evaluable=dscovr_arrays["evaluable"],
                ace_gate=ace_arrays["gate"],
                ace_score=ace_arrays["score"],
                wind_gate=wind_arrays["gate"],
                wind_score=wind_arrays["score"],
                candidate_minute=candidate_minute,
                support_radii=radii,
                half_window=half_window,
                strongest_span_threshold=span_threshold,
            )
            rows.append(
                {
                    "control_type": "MISMATCHED_DAY",
                    "ace_day_offset": ace_offset,
                    "ace_date_utc": (base_day + pd.Timedelta(days=ace_offset)).date().isoformat(),
                    "wind_day_offset": wind_offset,
                    "wind_date_utc": (base_day + pd.Timedelta(days=wind_offset)).date().isoformat(),
                    **flatten_metrics(metrics),
                }
            )
    controls = pd.DataFrame(rows)
    return controls, summarize_null(observed, controls), metadata


def make_charts(
    *,
    circular: pd.DataFrame,
    mismatch: pd.DataFrame,
    observed: dict[str, Any],
    outdir: Path,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    observed_flat = flatten_metrics(observed)
    outputs: list[Path] = []

    for field, title, xlabel in (
        (
            "candidate_nearest_joint_radius_minutes",
            "10:59 Candidate Nearest Joint Radius Under Hard Nulls",
            "Nearest joint ACE/Wind radius (minutes)",
        ),
        (
            "candidate_strongest_three_spacecraft_span_minutes",
            "10:59 Candidate Strongest Three-Spacecraft Span Under Hard Nulls",
            "Strongest three-spacecraft span (minutes)",
        ),
    ):
        plt.figure(figsize=(10, 5))
        plt.hist(
            circular[field].dropna(),
            bins=np.arange(-0.5, 31.5, 1.0),
            alpha=0.65,
            label="Circular shifts",
        )
        if not mismatch.empty:
            plt.hist(
                mismatch[field].dropna(),
                bins=np.arange(-0.5, 31.5, 1.0),
                alpha=0.65,
                label="Mismatched days",
            )
        plt.axvline(
            observed_flat[field],
            linestyle="--",
            linewidth=2,
            label=f"Observed = {observed_flat[field]}",
        )
        plt.xlabel(xlabel)
        plt.ylabel("Control realizations")
        plt.title(title)
        plt.legend()
        plt.tight_layout()
        path = outdir / f"{field}.png"
        plt.savefig(path, dpi=180)
        plt.close()
        outputs.append(path)

    radii = [1, 2, 3, 5, 10, 15]
    observed_values = [
        observed_flat[f"joint_support_fraction_within_{radius}_minutes"]
        for radius in radii
    ]
    circular_median = [
        circular[f"joint_support_fraction_within_{radius}_minutes"].median()
        for radius in radii
    ]
    circular_q95 = [
        circular[f"joint_support_fraction_within_{radius}_minutes"].quantile(0.95)
        for radius in radii
    ]
    plt.figure(figsize=(10, 5))
    plt.plot(radii, observed_values, marker="o", label="Observed event day")
    plt.plot(radii, circular_median, marker="o", label="Circular-shift median")
    plt.plot(
        radii,
        circular_q95,
        marker="o",
        label="Circular-shift 95th percentile",
    )
    if not mismatch.empty:
        mismatch_median = [
            mismatch[f"joint_support_fraction_within_{radius}_minutes"].median()
            for radius in radii
        ]
        plt.plot(radii, mismatch_median, marker="o", label="Mismatched-day median")
    plt.xlabel("Joint support radius (minutes)")
    plt.ylabel("Fraction of DSCOVR gate anchors")
    plt.title("Frozen MAG Gate: Observed Versus Hard-Null Support")
    plt.legend()
    plt.tight_layout()
    path = outdir / "support_fraction_by_radius.png"
    plt.savefig(path, dpi=180)
    plt.close()
    outputs.append(path)
    return outputs


def build_report(*, manifest: dict[str, Any], path: Path) -> None:
    circular = manifest["circular_shift_null"]["summary"]
    mismatch = manifest["mismatched_day_null"]["summary"]
    observed = manifest["observed"]
    candidate = observed["candidate"]

    lines = [
        "# Gannon Frozen MAG Gate Hard-Null Calibration",
        "",
        "## Frozen state",
        "",
        "The 45-degree GSE-vector rotation OR 25-percent relative |B| change gate,",
        "exact previous-minute requirement, one-minute cadence, and timing radii",
        "were not changed.",
        "",
        "The 10:59 interpretation remains frozen:",
        "",
        "```text",
        "SHARED_DISTURBED_INTERVAL_SUPPORTED",
        "UNIQUE_COMMON_STRUCTURE_UNRESOLVED",
        "PHYSICAL_CLASS_UNRESOLVED",
        "PROPAGATION_NOT_CALCULATED",
        "```",
        "",
        "## Observed event-day timing",
        "",
        f"- DSCOVR gate anchors: {observed['dscovr_gate_anchor_rows']}",
        f"- Candidate nearest joint radius: {candidate['nearest_joint_radius_minutes']} minutes",
        f"- Candidate strongest three-spacecraft span: {candidate['strongest_three_spacecraft_span_minutes']} minutes",
        f"- Fraction of DSCOVR gate anchors with joint support within 2 minutes: {observed['support_fractions']['2']:.6f}",
        f"- Fraction with strongest span <=3 minutes: {observed['strongest_span_fraction']:.6f}",
        "",
        "## Circular-shift hard null",
        "",
        "The shifts preserve each spacecraft's gate density, clustering, gaps, and",
        "within-series autocorrelation while breaking pairwise simultaneity by more",
        "than the frozen support window.",
        "",
    ]
    for field in (
        "candidate_nearest_joint_radius_minutes",
        "candidate_strongest_three_spacecraft_span_minutes",
        "joint_support_fraction_within_2_minutes",
        "strongest_span_fraction",
    ):
        record = circular[field]
        lines.append(
            f"- `{field}`: observed={record['observed']}; "
            f"control median={record['control_median']:.6f}; "
            f"equal-or-more-extreme fraction="
            f"{record['empirical_equal_or_more_extreme_fraction']:.6f}; "
            f"plus-one estimator={record['plus_one_tail_estimator']:.6f}."
        )
    lines.extend(
        [
            "",
            "## Mismatched-day hard null",
            "",
            "ACE and Wind days are selected by predeclared nonzero date offsets and",
            "paired by Cartesian product. Their minute-of-day sequences are compared",
            "with the fixed Gannon DSCOVR anchors without interpolation.",
            "",
        ]
    )
    for field in (
        "candidate_nearest_joint_radius_minutes",
        "candidate_strongest_three_spacecraft_span_minutes",
        "joint_support_fraction_within_2_minutes",
        "strongest_span_fraction",
    ):
        record = mismatch[field]
        lines.append(
            f"- `{field}`: observed={record['observed']}; "
            f"control median={record['control_median']:.6f}; "
            f"equal-or-more-extreme fraction="
            f"{record['empirical_equal_or_more_extreme_fraction']:.6f}; "
            f"plus-one estimator={record['plus_one_tail_estimator']:.6f}."
        )
    lines.extend(
        [
            "",
            "## Result boundary",
            "",
            f"```text\n{manifest['result_state']}\n```",
            "",
            "These empirical fractions describe the frozen hard-null constructions.",
            "They are not independent-minute probabilities, quiet-time false-positive",
            "rates, or proof that the three spacecraft crossed one moving surface.",
            "",
            "Geometry remains blocked until quiet, moderate, and isolated-structure",
            "event-class controls are completed under the same frozen gate.",
            "",
            "## Interpretation limits",
            "",
            *[f"- {item}" for item in manifest["interpretation_limits"]],
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def artifact_inventory(root: Path, manifest_path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != manifest_path:
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return result


def run_controls(
    *,
    contract_path: Path,
    gate_density_root: Path,
    outdir: Path,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    gate_manifest_path = gate_density_root / "gannon_gate_density_manifest.json"
    gate_manifest = load_json(gate_manifest_path)
    if gate_manifest.get("status") != "SUCCESS":
        raise ControlHarnessError(
            f"gate-density input status is {gate_manifest.get('status')!r}"
        )
    frozen_validation = validate_frozen_gate(contract, gate_manifest)
    gate_settings = gate_manifest["gate"]
    window = gate_manifest["analysis_window"]
    day_start = pd.Timestamp(window["start_utc"]).tz_convert("UTC")
    day_stop = pd.Timestamp(window["stop_utc"]).tz_convert("UTC")
    if day_stop - day_start != pd.Timedelta(days=1):
        raise ControlHarnessError("gate-density input must span exactly one UTC day")

    source_files = {
        "DSCOVR": gate_density_root / "canonical" / "dscovr_mag_gate_density.csv",
        "ACE": gate_density_root / "canonical" / "ace_mag_gate_density.csv",
        "WIND": gate_density_root / "canonical" / "wind_mag_gate_density.csv",
    }
    frames = {
        mission: load_gate_table(path, mission=mission)
        for mission, path in source_files.items()
    }
    arrays = {
        mission: minute_arrays(frame, day_start=day_start)
        for mission, frame in frames.items()
    }

    circular_settings = contract["circular_shift_null"]
    mismatch_settings = contract["mismatched_day_null"]
    candidate_minute = int(circular_settings["candidate_minute_of_day"])
    radii = [int(value) for value in circular_settings["support_radii_minutes"]]
    half_window = int(circular_settings["support_half_window_minutes"])
    span_threshold = int(circular_settings["strongest_span_threshold_minutes"])
    observed = support_metrics(
        dscovr_gate=arrays["DSCOVR"]["gate"],
        dscovr_evaluable=arrays["DSCOVR"]["evaluable"],
        ace_gate=arrays["ACE"]["gate"],
        ace_score=arrays["ACE"]["score"],
        wind_gate=arrays["WIND"]["gate"],
        wind_score=arrays["WIND"]["score"],
        candidate_minute=candidate_minute,
        support_radii=radii,
        half_window=half_window,
        strongest_span_threshold=span_threshold,
    )

    outdir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "control_version": CONTROL_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "runtime": {"python": platform.python_version()},
        "contract": {
            "path": str(contract_path),
            "contract_id": contract["contract_id"],
            "contract_version": contract["contract_version"],
            "sha256": sha256_file(contract_path),
        },
        "gate_density_input": {
            "root": str(gate_density_root),
            "manifest_sha256": sha256_file(gate_manifest_path),
            "frozen_validation": frozen_validation,
        },
        "observed": observed,
        "interpretation_limits": contract["interpretation_limits"],
        "geometry_allowed": False,
        "ephemeris_test_completed": False,
        "physical_mechanism_classified": False,
    }
    manifest_path = outdir / "gannon_gate_controls_manifest.json"
    write_json(manifest_path, manifest)

    try:
        circular, circular_summary = circular_shift_controls(
            observed=observed,
            arrays=arrays,
            settings=circular_settings,
        )
        circular.to_csv(outdir / "circular_shift_controls.csv", index=False)

        mismatch, mismatch_summary, mismatch_metadata = mismatched_day_controls(
            observed=observed,
            dscovr_arrays=arrays["DSCOVR"],
            settings=mismatch_settings,
            gate=gate_settings,
            raw_root=outdir / "raw" / "mismatched_days",
            canonical_root=outdir / "canonical" / "mismatched_days",
            quarantine_root=outdir / "quarantine" / "mismatched_days",
        )
        mismatch.to_csv(outdir / "mismatched_day_controls.csv", index=False)

        chart_paths = make_charts(
            circular=circular,
            mismatch=mismatch,
            observed=observed,
            outdir=outdir / "charts",
        )

        result_state = (
            "HARD_NULLS_MEASURED_EVENT_CLASS_CONTROLS_PENDING_"
            "COMMON_SURFACE_UNRESOLVED"
        )
        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "result_state": result_state,
                "circular_shift_null": {
                    "settings": circular_settings,
                    "rows": int(len(circular)),
                    "summary": circular_summary,
                },
                "mismatched_day_null": {
                    "settings": mismatch_settings,
                    "rows": int(len(mismatch)),
                    "summary": mismatch_summary,
                    "source_metadata": mismatch_metadata,
                },
                "chart_paths": [str(path) for path in chart_paths],
                "event_class_controls_completed": False,
                "background_calibration_complete": False,
            }
        )
        build_report(
            manifest=manifest,
            path=outdir / "reports" / "GANNON_HARD_NULL_CONTROLS.md",
        )
        manifest["artifacts"] = artifact_inventory(outdir, manifest_path)
        write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(
            {
                "status": "FAILED",
                "completed_utc": utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(manifest_path, manifest)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/gannon_gate_controls.v1.json"),
    )
    parser.add_argument(
        "--gate-density-root",
        type=Path,
        default=Path("runs/audits/gannon_gate_density"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/gannon_gate_controls"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_controls(
        contract_path=args.config,
        gate_density_root=args.gate_density_root,
        outdir=args.outdir,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "result_state": result.get("result_state"),
                "background_calibration_complete": result.get(
                    "background_calibration_complete"
                ),
                "outdir": str(args.outdir),
            }
        )
    )


if __name__ == "__main__":
    main()
