#!/usr/bin/env python3
"""Run frozen hard-null controls for the NVCPP Gannon MAG gate.

This harness leaves the 45 degree / 25 percent detector unchanged. It measures
how the observed Gannon timing behaves after physical simultaneity is broken by
(1) deterministic circular shifts of the May 11 gate trains and (2) fixed,
predeclared mismatched spacecraft days. Quiet/moderate/isolated-event controls
remain a separately declared pending stage.

Nothing in this module estimates a propagation lag, discontinuity normal, or
physical class.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.gannon_gate_density import (
    add_exact_minute_diagnostics,
    run_audit as run_gate_density_audit,
    summarize_mission,
)
from historical.gannon_multipoint_audit import (
    canonicalize_vector_minutes,
    fetch_hapi,
    parse_cdas_rows,
    request_cdas_text,
)

HARNESS_VERSION = "1.0.0"
DAY_MINUTES = 1440


class ControlHarnessError(RuntimeError):
    """Raised when a frozen control cannot be evaluated safely."""


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


def to_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def circular_distance_minutes(
    left: int, right: int, period: int = DAY_MINUTES
) -> int:
    delta = abs(int(left) - int(right)) % period
    return int(min(delta, period - delta))


def signed_circular_shift(value: int, period: int = DAY_MINUTES) -> int:
    normalized = int(value) % period
    return normalized if normalized <= period // 2 else normalized - period


def load_contract(path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "contract_id",
        "contract_version",
        "development_gate_contract",
        "gate",
        "timing_radii_minutes",
        "circular_shift_null",
        "mismatched_day_null",
        "event_class_controls",
        "decision_policy",
        "interpretation_limits",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ControlHarnessError(f"control contract lacks keys: {missing}")

    gate_path = Path(contract["development_gate_contract"]["path"])
    gate_contract = json.loads(gate_path.read_text(encoding="utf-8"))
    expected_id = contract["development_gate_contract"]["contract_id"]
    expected_version = contract["development_gate_contract"][
        "contract_version"
    ]
    if gate_contract.get("contract_id") != expected_id:
        raise ControlHarnessError("frozen gate contract ID changed")
    if gate_contract.get("contract_version") != expected_version:
        raise ControlHarnessError("frozen gate contract version changed")

    frozen_keys = (
        "canonical_cadence_seconds",
        "required_previous_offset_seconds",
        "rotation_threshold_degrees",
        "magnitude_change_threshold_fraction",
        "logical_operator",
        "support_half_window_minutes",
    )
    for key in frozen_keys:
        if contract["gate"].get(key) != gate_contract["gate"].get(key):
            raise ControlHarnessError(
                f"control gate {key!r} differs from frozen development contract"
            )

    if int(contract["gate"]["canonical_cadence_seconds"]) != 60:
        raise ControlHarnessError("controls require exact one-minute cadence")
    if int(contract["gate"]["required_previous_offset_seconds"]) != 60:
        raise ControlHarnessError("controls require exact t-1 minute")
    if contract["gate"]["logical_operator"] != "OR":
        raise ControlHarnessError("frozen detector must remain an OR gate")

    radii = [int(value) for value in contract["timing_radii_minutes"]]
    if radii != [1, 2, 3, 5, 10, 15]:
        raise ControlHarnessError(
            "timing radii changed from the frozen sequence"
        )
    if int(contract["gate"]["support_half_window_minutes"]) != max(radii):
        raise ControlHarnessError(
            "support window must equal the maximum frozen radius"
        )

    circular = contract["circular_shift_null"]
    if int(circular["iterations"]) < 100:
        raise ControlHarnessError("circular-shift null is too small")
    if int(circular["minimum_pairwise_separation_minutes"]) <= max(radii):
        raise ControlHarnessError(
            "circular shifts must exceed the largest support radius"
        )

    mismatched = contract["mismatched_day_null"]
    ace_offsets = [int(value) for value in mismatched["ace_day_offsets"]]
    wind_offsets = [int(value) for value in mismatched["wind_day_offsets"]]
    if not ace_offsets or not wind_offsets:
        raise ControlHarnessError("mismatched-day offsets are empty")
    if 0 in ace_offsets or 0 in wind_offsets:
        raise ControlHarnessError("mismatched-day controls cannot use day zero")
    if len(set(ace_offsets)) != len(ace_offsets):
        raise ControlHarnessError(
            "ACE mismatched-day offsets are duplicated"
        )
    if len(set(wind_offsets)) != len(wind_offsets):
        raise ControlHarnessError(
            "Wind mismatched-day offsets are duplicated"
        )

    if contract["event_class_controls"].get("status") != (
        "PENDING_INDEPENDENT_SELECTION_BEFORE_MAG_RETRIEVAL"
    ):
        raise ControlHarnessError(
            "event-class control status is not explicitly pending"
        )

    return contract, gate_contract, gate_path


def minute_index(timestamp: pd.Timestamp, day_start: pd.Timestamp) -> int:
    return int((timestamp - day_start).total_seconds() // 60)


def day_arrays(
    frame: pd.DataFrame, *, day_start: pd.Timestamp
) -> dict[str, np.ndarray]:
    required = {"time", "gate_pass", "gate_score", "exact_previous_minute"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ControlHarnessError(
            f"canonical gate table lacks columns: {missing}"
        )

    working = frame.copy()
    working["time"] = pd.to_datetime(
        working["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if working["time"].isna().any():
        raise ControlHarnessError(
            "canonical gate table contains invalid timestamps"
        )
    working.sort_values("time", inplace=True)
    if working["time"].duplicated().any():
        raise ControlHarnessError(
            "canonical gate table contains duplicate timestamps"
        )

    gate = np.zeros(DAY_MINUTES, dtype=bool)
    evaluable = np.zeros(DAY_MINUTES, dtype=bool)
    score = np.zeros(DAY_MINUTES, dtype=float)
    present = np.zeros(DAY_MINUTES, dtype=bool)
    for row in working.itertuples(index=False):
        index = minute_index(row.time, day_start)
        if not 0 <= index < DAY_MINUTES:
            continue
        if present[index]:
            raise ControlHarnessError(f"duplicate minute index {index}")
        present[index] = True
        gate[index] = bool(row.gate_pass)
        evaluable[index] = bool(row.exact_previous_minute)
        numeric_score = float(row.gate_score)
        score[index] = numeric_score if math.isfinite(numeric_score) else 0.0
    return {
        "gate": gate,
        "score": score,
        "evaluable": evaluable,
        "present": present,
    }


def choose_gate(
    gate: np.ndarray,
    score: np.ndarray,
    *,
    center: int,
    half_window: int,
    mode: str,
) -> int | None:
    lower = max(0, center - half_window)
    upper = min(len(gate) - 1, center + half_window)
    indices = np.flatnonzero(gate[lower : upper + 1]) + lower
    if len(indices) == 0:
        return None
    offsets = np.abs(indices - center)
    if mode == "nearest":
        order = np.lexsort((indices, offsets))
    elif mode == "strongest":
        order = np.lexsort((indices, offsets, -score[indices]))
    else:
        raise ControlHarnessError(
            f"unsupported gate choice mode: {mode}"
        )
    return int(indices[order[0]])


def support_metrics(
    *,
    dscovr: dict[str, np.ndarray],
    ace: dict[str, np.ndarray],
    wind: dict[str, np.ndarray],
    candidate_index: int,
    radii: list[int],
    half_window: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    minute_numbers = np.arange(DAY_MINUTES)
    anchor_mask = (
        dscovr["evaluable"]
        & (minute_numbers >= half_window)
        & (minute_numbers < DAY_MINUTES - half_window)
    )
    gate_anchors = np.flatnonzero(anchor_mask & dscovr["gate"])
    if len(gate_anchors) == 0:
        raise ControlHarnessError("no DSCOVR gate anchors are evaluable")

    rows: list[dict[str, Any]] = []
    for center in gate_anchors:
        ace_nearest = choose_gate(
            ace["gate"],
            ace["score"],
            center=center,
            half_window=half_window,
            mode="nearest",
        )
        wind_nearest = choose_gate(
            wind["gate"],
            wind["score"],
            center=center,
            half_window=half_window,
            mode="nearest",
        )
        ace_strongest = choose_gate(
            ace["gate"],
            ace["score"],
            center=center,
            half_window=half_window,
            mode="strongest",
        )
        wind_strongest = choose_gate(
            wind["gate"],
            wind["score"],
            center=center,
            half_window=half_window,
            mode="strongest",
        )
        nearest_radius = (
            max(abs(ace_nearest - center), abs(wind_nearest - center))
            if ace_nearest is not None and wind_nearest is not None
            else np.nan
        )
        strongest_span = (
            max(center, ace_strongest, wind_strongest)
            - min(center, ace_strongest, wind_strongest)
            if ace_strongest is not None and wind_strongest is not None
            else np.nan
        )
        rows.append(
            {
                "anchor_minute_index": int(center),
                "dscovr_gate_score": float(dscovr["score"][center]),
                "ace_nearest_offset_minutes": (
                    int(ace_nearest - center)
                    if ace_nearest is not None
                    else np.nan
                ),
                "wind_nearest_offset_minutes": (
                    int(wind_nearest - center)
                    if wind_nearest is not None
                    else np.nan
                ),
                "nearest_joint_radius_minutes": nearest_radius,
                "ace_strongest_offset_minutes": (
                    int(ace_strongest - center)
                    if ace_strongest is not None
                    else np.nan
                ),
                "wind_strongest_offset_minutes": (
                    int(wind_strongest - center)
                    if wind_strongest is not None
                    else np.nan
                ),
                "strongest_three_spacecraft_span_minutes": strongest_span,
            }
        )
    support = pd.DataFrame(rows)

    metrics: dict[str, Any] = {
        "dscovr_gate_anchor_rows": int(len(support)),
        "dscovr_gate_score_candidate": float(
            dscovr["score"][candidate_index]
        ),
        "dscovr_candidate_score_percentile_within_gate_anchors": float(
            100.0
            * support["dscovr_gate_score"]
            .le(float(dscovr["score"][candidate_index]))
            .mean()
        ),
    }
    for radius in radii:
        inside = support["nearest_joint_radius_minutes"].le(radius)
        metrics[f"joint_support_rows_within_{radius}_minutes"] = int(
            inside.sum()
        )
        metrics[f"joint_support_fraction_within_{radius}_minutes"] = float(
            inside.mean()
        )
    span_inside = support[
        "strongest_three_spacecraft_span_minutes"
    ].le(3)
    metrics["strongest_span_rows_within_3_minutes"] = int(
        span_inside.sum()
    )
    metrics["strongest_span_fraction_within_3_minutes"] = float(
        span_inside.mean()
    )

    candidate_row = support.loc[
        support["anchor_minute_index"] == candidate_index
    ]
    if len(candidate_row) != 1:
        raise ControlHarnessError(
            "candidate minute is absent from DSCOVR gate anchors"
        )
    metrics["candidate"] = candidate_row.iloc[0].to_dict()
    return metrics, support


def valid_circular_shifts(
    *, period: int, minimum_pairwise_separation: int
) -> np.ndarray:
    return np.array(
        [
            shift
            for shift in range(1, period)
            if circular_distance_minutes(shift, 0, period)
            >= minimum_pairwise_separation
        ],
        dtype=int,
    )


def generate_shift_pairs(
    *,
    iterations: int,
    seed: int,
    period: int,
    minimum_pairwise_separation: int,
) -> list[tuple[int, int]]:
    valid = valid_circular_shifts(
        period=period,
        minimum_pairwise_separation=minimum_pairwise_separation,
    )
    if len(valid) < 2:
        raise ControlHarnessError("no valid circular shifts")
    generator = np.random.default_rng(seed)
    result: list[tuple[int, int]] = []
    maximum_attempts = iterations * 100
    attempts = 0
    while len(result) < iterations:
        attempts += 1
        if attempts > maximum_attempts:
            raise ControlHarnessError(
                "could not generate constrained shift pairs"
            )
        ace_shift = int(generator.choice(valid))
        wind_shift = int(generator.choice(valid))
        if (
            circular_distance_minutes(ace_shift, wind_shift, period)
            < minimum_pairwise_separation
        ):
            continue
        result.append((ace_shift, wind_shift))
    return result


def shifted_arrays(
    source: dict[str, np.ndarray], shift: int
) -> dict[str, np.ndarray]:
    return {key: np.roll(value, int(shift)) for key, value in source.items()}


def add_one_upper_tail(values: pd.Series, observed: float) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    count = int(finite.ge(observed).sum())
    return {
        "direction": "greater_or_equal_is_as_or_more_extreme",
        "observed": float(observed),
        "null_rows": int(len(finite)),
        "exceedance_rows": count,
        "add_one_tail_fraction": float((count + 1) / (len(finite) + 1)),
        "label": (
            "empirical under the frozen control generator; not an "
            "independent-minute p-value"
        ),
    }


def add_one_lower_tail(
    values: pd.Series, observed: float, total_rows: int
) -> dict[str, Any]:
    numeric = pd.to_numeric(values, errors="coerce")
    count = int(numeric.le(observed).fillna(False).sum())
    return {
        "direction": "less_or_equal_is_as_or_more_extreme",
        "observed": float(observed),
        "null_rows_including_no_support": int(total_rows),
        "supporting_rows_at_or_below_observed": count,
        "add_one_tail_fraction": float((count + 1) / (total_rows + 1)),
        "label": (
            "empirical under the frozen control generator; missing support "
            "counts as not extreme"
        ),
    }


def null_quantiles(values: pd.Series) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if finite.empty:
        return {"rows": 0}
    return {
        "rows": int(len(finite)),
        "minimum": float(finite.min()),
        "q01": float(finite.quantile(0.01)),
        "q05": float(finite.quantile(0.05)),
        "median": float(finite.median()),
        "q95": float(finite.quantile(0.95)),
        "q99": float(finite.quantile(0.99)),
        "maximum": float(finite.max()),
    }


def run_circular_controls(
    *,
    dscovr: dict[str, np.ndarray],
    ace: dict[str, np.ndarray],
    wind: dict[str, np.ndarray],
    observed: dict[str, Any],
    candidate_index: int,
    radii: list[int],
    half_window: int,
    iterations: int,
    seed: int,
    minimum_pairwise_separation: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    pairs = generate_shift_pairs(
        iterations=iterations,
        seed=seed,
        period=DAY_MINUTES,
        minimum_pairwise_separation=minimum_pairwise_separation,
    )
    rows: list[dict[str, Any]] = []
    for iteration, (ace_shift, wind_shift) in enumerate(pairs, start=1):
        metrics, _ = support_metrics(
            dscovr=dscovr,
            ace=shifted_arrays(ace, ace_shift),
            wind=shifted_arrays(wind, wind_shift),
            candidate_index=candidate_index,
            radii=radii,
            half_window=half_window,
        )
        candidate = metrics.pop("candidate")
        row: dict[str, Any] = {
            "iteration": iteration,
            "ace_shift_minutes": signed_circular_shift(ace_shift),
            "wind_shift_minutes": signed_circular_shift(wind_shift),
            "ace_wind_circular_separation_minutes": (
                circular_distance_minutes(ace_shift, wind_shift)
            ),
            "candidate_nearest_joint_radius_minutes": candidate[
                "nearest_joint_radius_minutes"
            ],
            "candidate_strongest_three_spacecraft_span_minutes": candidate[
                "strongest_three_spacecraft_span_minutes"
            ],
        }
        row.update(metrics)
        rows.append(row)
    table = pd.DataFrame(rows)

    comparisons: dict[str, Any] = {}
    for radius in radii:
        key = f"joint_support_fraction_within_{radius}_minutes"
        comparisons[key] = add_one_upper_tail(
            table[key], float(observed[key])
        )
    span_key = "strongest_span_fraction_within_3_minutes"
    comparisons[span_key] = add_one_upper_tail(
        table[span_key], float(observed[span_key])
    )
    observed_candidate = observed["candidate"]
    comparisons["candidate_nearest_joint_radius_minutes"] = (
        add_one_lower_tail(
            table["candidate_nearest_joint_radius_minutes"],
            float(observed_candidate["nearest_joint_radius_minutes"]),
            len(table),
        )
    )
    comparisons[
        "candidate_strongest_three_spacecraft_span_minutes"
    ] = add_one_lower_tail(
        table["candidate_strongest_three_spacecraft_span_minutes"],
        float(
            observed_candidate[
                "strongest_three_spacecraft_span_minutes"
            ]
        ),
        len(table),
    )

    summary = {
        "iterations": int(iterations),
        "seed": int(seed),
        "minimum_pairwise_separation_minutes": int(
            minimum_pairwise_separation
        ),
        "all_three_series_pairwise_separated_beyond_support_window": True,
        "gate_counts_preserved_each_iteration": {
            "ACE": int(ace["gate"].sum()),
            "WIND": int(wind["gate"].sum()),
        },
        "comparisons": comparisons,
        "quantiles": {
            column: null_quantiles(table[column])
            for column in [
                *[
                    f"joint_support_fraction_within_{radius}_minutes"
                    for radius in radii
                ],
                span_key,
                "candidate_nearest_joint_radius_minutes",
                "candidate_strongest_three_spacecraft_span_minutes",
            ]
        },
    }
    return table, summary


def day_bounds(
    development_start: pd.Timestamp, offset_days: int
) -> tuple[str, str]:
    start = development_start + pd.Timedelta(days=int(offset_days))
    stop = start + pd.Timedelta(days=1)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        stop.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def rebase_to_development_day(
    frame: pd.DataFrame, *, development_start: pd.Timestamp
) -> pd.DataFrame:
    output = frame.copy()
    output["time"] = pd.to_datetime(
        output["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if output["time"].isna().any():
        raise ControlHarnessError("mismatched-day table has invalid times")
    minute_of_day = output["time"].dt.hour * 60 + output["time"].dt.minute
    output["original_time_utc"] = output["time"]
    output["time"] = development_start + pd.to_timedelta(
        minute_of_day, unit="min"
    )
    if output["time"].duplicated().any():
        raise ControlHarnessError(
            "mismatched-day rebasing produced duplicate minutes"
        )
    return output


def fetch_ace_control_day(
    session: requests.Session,
    *,
    start: str,
    stop: str,
    outdir: Path,
    rotation_threshold: float,
    magnitude_threshold: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, metadata, _ = fetch_hapi(
        session,
        dataset_id="AC_H0_MFI",
        parameters=["Magnitude", "BGSEc", "SC_pos_GSE"],
        start=start,
        stop=stop,
        outdir=outdir,
    )
    canonical, quarantine = canonicalize_vector_minutes(
        raw,
        components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
        position_components=(
            "SC_pos_GSE_x",
            "SC_pos_GSE_y",
            "SC_pos_GSE_z",
        ),
        minimum_samples=3,
        source="AC_H0_MFI",
    )
    canonical = add_exact_minute_diagnostics(
        canonical,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    canonical.to_csv(outdir / "canonical_gate_table.csv", index=False)
    quarantine.to_csv(outdir / "quarantine.csv", index=False)
    metadata["canonical_sha256"] = sha256_file(
        outdir / "canonical_gate_table.csv"
    )
    metadata["quarantine_sha256"] = sha256_file(
        outdir / "quarantine.csv"
    )
    return canonical, metadata


def fetch_wind_control_day(
    session: requests.Session,
    *,
    start: str,
    stop: str,
    outdir: Path,
    rotation_threshold: float,
    magnitude_threshold: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, metadata = request_cdas_text(
        session,
        dataset_id="WI_H0_MFI",
        variables=["B3GSE", "B3F1"],
        start=start,
        stop=stop,
        outdir=outdir,
    )
    parsed = parse_cdas_rows(
        raw,
        columns=[
            "time",
            "reported_B3F1_nT",
            "B3GSE_x",
            "B3GSE_y",
            "B3GSE_z",
        ],
    )
    canonical, quarantine = canonicalize_vector_minutes(
        parsed,
        components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
        minimum_samples=18,
        source="WI_H0_MFI",
    )
    canonical = add_exact_minute_diagnostics(
        canonical,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    canonical.to_csv(outdir / "canonical_gate_table.csv", index=False)
    quarantine.to_csv(outdir / "quarantine.csv", index=False)
    metadata["canonical_sha256"] = sha256_file(
        outdir / "canonical_gate_table.csv"
    )
    metadata["quarantine_sha256"] = sha256_file(
        outdir / "quarantine.csv"
    )
    return canonical, metadata


def fetch_mismatched_days(
    *,
    development_start: pd.Timestamp,
    ace_offsets: list[int],
    wind_offsets: list[int],
    raw_root: Path,
    rotation_threshold: float,
    magnitude_threshold: float,
    minimum_evaluable_fraction: float,
) -> tuple[
    dict[int, pd.DataFrame],
    dict[int, pd.DataFrame],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-MAG-GATE-CONTROLS/{HARNESS_VERSION}"}
    )
    ace_frames: dict[int, pd.DataFrame] = {}
    wind_frames: dict[int, pd.DataFrame] = {}
    metadata: list[dict[str, Any]] = []
    density_rows: list[dict[str, Any]] = []

    for mission, offsets in (("ACE", ace_offsets), ("WIND", wind_offsets)):
        for offset in offsets:
            start, stop = day_bounds(development_start, offset)
            day_dir = raw_root / mission.lower() / f"offset_{offset:+d}"
            day_dir.mkdir(parents=True, exist_ok=True)
            record: dict[str, Any] = {
                "mission": mission,
                "offset_days": int(offset),
                "start_utc": start,
                "stop_utc": stop,
                "status": "STARTED",
            }
            try:
                if mission == "ACE":
                    frame, source = fetch_ace_control_day(
                        session,
                        start=start,
                        stop=stop,
                        outdir=day_dir,
                        rotation_threshold=rotation_threshold,
                        magnitude_threshold=magnitude_threshold,
                    )
                else:
                    frame, source = fetch_wind_control_day(
                        session,
                        start=start,
                        stop=stop,
                        outdir=day_dir,
                        rotation_threshold=rotation_threshold,
                        magnitude_threshold=magnitude_threshold,
                    )
                summary = summarize_mission(frame, mission)
                evaluable_fraction = (
                    summary["evaluable_exact_previous_rows"] / DAY_MINUTES
                )
                record.update(
                    {
                        "status": (
                            "ADMITTED"
                            if evaluable_fraction >= minimum_evaluable_fraction
                            else "INSUFFICIENT_EVALUABLE_COVERAGE"
                        ),
                        "evaluable_fraction_of_day": float(
                            evaluable_fraction
                        ),
                        "source": source,
                        "gate_summary": summary,
                    }
                )
                density_rows.append(
                    {
                        **summary,
                        "offset_days": int(offset),
                        "start_utc": start,
                        "admitted_for_mismatched_pairs": (
                            evaluable_fraction >= minimum_evaluable_fraction
                        ),
                    }
                )
                if evaluable_fraction >= minimum_evaluable_fraction:
                    rebased = rebase_to_development_day(
                        frame, development_start=development_start
                    )
                    if mission == "ACE":
                        ace_frames[int(offset)] = rebased
                    else:
                        wind_frames[int(offset)] = rebased
            except Exception as exc:
                record.update(
                    {
                        "status": "FAILED",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            metadata.append(record)
    return ace_frames, wind_frames, metadata, density_rows


def run_mismatched_controls(
    *,
    dscovr: dict[str, np.ndarray],
    ace_frames: dict[int, pd.DataFrame],
    wind_frames: dict[int, pd.DataFrame],
    development_start: pd.Timestamp,
    candidate_index: int,
    radii: list[int],
    half_window: int,
    exclude_equal_offsets: bool,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ace_offset, ace_frame in sorted(ace_frames.items()):
        for wind_offset, wind_frame in sorted(wind_frames.items()):
            if exclude_equal_offsets and ace_offset == wind_offset:
                continue
            ace_arrays = day_arrays(
                ace_frame, day_start=development_start
            )
            wind_arrays = day_arrays(
                wind_frame, day_start=development_start
            )
            metrics, _ = support_metrics(
                dscovr=dscovr,
                ace=ace_arrays,
                wind=wind_arrays,
                candidate_index=candidate_index,
                radii=radii,
                half_window=half_window,
            )
            candidate = metrics.pop("candidate")
            row: dict[str, Any] = {
                "ace_offset_days": int(ace_offset),
                "wind_offset_days": int(wind_offset),
                "ace_wind_day_separation": int(
                    abs(ace_offset - wind_offset)
                ),
                "candidate_nearest_joint_radius_minutes": candidate[
                    "nearest_joint_radius_minutes"
                ],
                "candidate_strongest_three_spacecraft_span_minutes": (
                    candidate[
                        "strongest_three_spacecraft_span_minutes"
                    ]
                ),
            }
            row.update(metrics)
            rows.append(row)
    return pd.DataFrame(rows)


def compare_mismatched(
    table: pd.DataFrame,
    *,
    observed: dict[str, Any],
    radii: list[int],
) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for radius in radii:
        key = f"joint_support_fraction_within_{radius}_minutes"
        comparisons[key] = add_one_upper_tail(
            table[key], float(observed[key])
        )
    span_key = "strongest_span_fraction_within_3_minutes"
    comparisons[span_key] = add_one_upper_tail(
        table[span_key], float(observed[span_key])
    )
    observed_candidate = observed["candidate"]
    comparisons["candidate_nearest_joint_radius_minutes"] = (
        add_one_lower_tail(
            table["candidate_nearest_joint_radius_minutes"],
            float(observed_candidate["nearest_joint_radius_minutes"]),
            len(table),
        )
    )
    comparisons[
        "candidate_strongest_three_spacecraft_span_minutes"
    ] = add_one_lower_tail(
        table["candidate_strongest_three_spacecraft_span_minutes"],
        float(
            observed_candidate[
                "strongest_three_spacecraft_span_minutes"
            ]
        ),
        len(table),
    )
    return {
        "pair_rows": int(len(table)),
        "comparisons": comparisons,
        "quantiles": {
            column: null_quantiles(table[column])
            for column in [
                *[
                    f"joint_support_fraction_within_{radius}_minutes"
                    for radius in radii
                ],
                span_key,
                "candidate_nearest_joint_radius_minutes",
                "candidate_strongest_three_spacecraft_span_minutes",
            ]
        },
        "label": (
            "fixed mismatched-day controls preserve within-day structure but "
            "destroy simultaneity; the small finite set is descriptive"
        ),
    }


def assessment(
    *,
    circular_summary: dict[str, Any],
    mismatched_summary: dict[str, Any] | None,
    decision_policy: dict[str, Any],
    event_class_status: str,
) -> dict[str, Any]:
    metric_keys = [
        "joint_support_fraction_within_2_minutes",
        "strongest_span_fraction_within_3_minutes",
        "candidate_nearest_joint_radius_minutes",
        "candidate_strongest_three_spacecraft_span_minutes",
    ]
    circular_threshold = float(
        decision_policy["circular_add_one_tail_fraction_threshold"]
    )
    mismatched_threshold = float(
        decision_policy["mismatched_add_one_tail_fraction_threshold"]
    )
    circular_pass = all(
        float(
            circular_summary["comparisons"][key][
                "add_one_tail_fraction"
            ]
        )
        <= circular_threshold
        for key in metric_keys
    )
    mismatched_pass = (
        mismatched_summary is not None
        and all(
            float(
                mismatched_summary["comparisons"][key][
                    "add_one_tail_fraction"
                ]
            )
            <= mismatched_threshold
            for key in metric_keys
        )
    )
    if circular_pass and mismatched_pass:
        hard_null_state = "SHORT_RADIUS_CLUSTER_EXCEEDS_CURRENT_HARD_NULLS"
    elif not circular_pass or (
        mismatched_summary is not None and not mismatched_pass
    ):
        hard_null_state = (
            "JOINT_TIMING_NOT_DISTINGUISHABLE_FROM_CURRENT_HARD_NULLS"
        )
    else:
        hard_null_state = "HARD_NULL_CALIBRATION_INCOMPLETE"
    return {
        "hard_null_state": hard_null_state,
        "circular_add_one_tail_fraction_threshold": circular_threshold,
        "mismatched_add_one_tail_fraction_threshold": (
            mismatched_threshold
        ),
        "circular_metrics_pass": circular_pass,
        "mismatched_day_metrics_pass": mismatched_pass,
        "event_class_control_state": event_class_status,
        "overall_calibration_state": (
            "BACKGROUND_CALIBRATION_PARTIAL_HARD_NULLS_COMPLETE_"
            "EVENT_CLASS_CONTROLS_PENDING"
        ),
        "geometry_stage_state": "BLOCKED_PENDING_EVENT_CLASS_CONTROLS",
        "common_surface_claim_allowed": False,
        "physical_class_claim_allowed": False,
        "threshold_retuning_allowed": False,
    }


def build_charts(
    *,
    circular: pd.DataFrame,
    mismatched: pd.DataFrame,
    observed: dict[str, Any],
    outdir: Path,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    metric = "joint_support_fraction_within_2_minutes"
    plt.figure(figsize=(10, 5))
    plt.hist(circular[metric].dropna(), bins=35)
    plt.axvline(
        float(observed[metric]), linestyle="--", label="Observed Gannon"
    )
    plt.xlabel(
        "Fraction of DSCOVR gate anchors with ACE and Wind within 2 min"
    )
    plt.ylabel("Circular-shift iterations")
    plt.title("Frozen MAG Gate: Circular-Shift Hard Null")
    plt.legend()
    plt.tight_layout()
    path = outdir / "circular_shift_joint_support_2min.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)

    metric = "strongest_span_fraction_within_3_minutes"
    plt.figure(figsize=(10, 5))
    plt.hist(circular[metric].dropna(), bins=35)
    plt.axvline(
        float(observed[metric]), linestyle="--", label="Observed Gannon"
    )
    plt.xlabel(
        "Fraction of DSCOVR gate anchors with strongest span <= 3 min"
    )
    plt.ylabel("Circular-shift iterations")
    plt.title("Frozen MAG Gate: Strongest-Span Hard Null")
    plt.legend()
    plt.tight_layout()
    path = outdir / "circular_shift_strongest_span_3min.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)

    if not mismatched.empty:
        plt.figure(figsize=(10, 5))
        x_values = np.arange(len(mismatched))
        plt.scatter(
            x_values,
            mismatched["joint_support_fraction_within_2_minutes"],
        )
        plt.axhline(
            float(observed["joint_support_fraction_within_2_minutes"]),
            linestyle="--",
            label="Observed Gannon",
        )
        plt.xlabel("Fixed ACE/Wind mismatched-day pair")
        plt.ylabel("Joint support fraction within 2 min")
        plt.title("Frozen MAG Gate: Mismatched-Day Controls")
        plt.legend()
        plt.tight_layout()
        path = outdir / "mismatched_day_joint_support_2min.png"
        plt.savefig(path, dpi=180)
        plt.close()
        paths.append(path)
    return paths


def build_report(*, path: Path, manifest: dict[str, Any]) -> None:
    observed = manifest["observed_development_metrics"]
    circular = manifest["circular_shift_null"]
    mismatched = manifest.get("mismatched_day_null")
    result = manifest["assessment"]
    candidate = observed["candidate"]

    lines = [
        "# NVCPP Frozen MAG Gate Control Harness",
        "",
        f"Status: **{manifest['status']}**",
        "",
        "This audit leaves the detector unchanged and breaks physical",
        "simultaneity through circular shifts and fixed mismatched spacecraft",
        "days.",
        "",
        "## Frozen gate",
        "",
        "```text",
        "one-minute GSE-vector rotation >= 45 degrees",
        "OR",
        "one-minute relative |B| change >= 0.25",
        "exact previous row = t-1 minute",
        "no interpolation or forward fill",
        "```",
        "",
        "## Observed Gannon timing",
        "",
        (
            "- Joint ACE+Wind support within 2 minutes: "
            f"`{observed['joint_support_fraction_within_2_minutes']:.6f}`."
        ),
        (
            "- Strongest three-spacecraft span <=3 minutes: "
            f"`{observed['strongest_span_fraction_within_3_minutes']:.6f}`."
        ),
        (
            "- 10:59 candidate nearest joint radius: "
            f"`{candidate['nearest_joint_radius_minutes']}` minutes."
        ),
        (
            "- 10:59 candidate strongest span: "
            f"`{candidate['strongest_three_spacecraft_span_minutes']}` minutes."
        ),
        "",
        "## Circular-shift hard null",
        "",
        f"- Iterations: `{circular['iterations']}`.",
        (
            "- Minimum pairwise circular separation: "
            f"`{circular['minimum_pairwise_separation_minutes']}` minutes."
        ),
    ]
    for key, record in circular["comparisons"].items():
        lines.append(
            f"- `{key}` add-one empirical tail fraction: "
            f"`{record['add_one_tail_fraction']:.8f}`."
        )
    lines.extend(
        [
            "",
            "These are empirical fractions under the frozen shift generator.",
            "They are not independent-minute probabilities.",
            "",
            "## Mismatched-day controls",
            "",
        ]
    )
    if mismatched is None:
        lines.append("- Insufficient admitted mismatched-day pairs.")
    else:
        lines.append(f"- Admitted pair rows: `{mismatched['pair_rows']}`.")
        for key, record in mismatched["comparisons"].items():
            lines.append(
                f"- `{key}` add-one empirical tail fraction: "
                f"`{record['add_one_tail_fraction']:.8f}`."
            )
    lines.extend(
        [
            "",
            "## Current bounded result",
            "",
            "```text",
            result["hard_null_state"],
            result["overall_calibration_state"],
            result["geometry_stage_state"],
            "COMMON_SURFACE_CLAIM_NOT_ALLOWED",
            "PHYSICAL_CLASS_CLAIM_NOT_ALLOWED",
            "```",
            "",
            "Quiet, moderate, and isolated-structure event-class controls",
            "remain pending. Geometry is not opened by this partial hard-null",
            "result.",
            "",
            "## Interpretation limits",
            "",
            *[
                f"- {item}"
                for item in manifest["interpretation_limits"]
            ],
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_harness(*, contract_path: Path, outdir: Path) -> dict[str, Any]:
    contract, gate_contract, gate_contract_path = load_contract(contract_path)
    outdir.mkdir(parents=True, exist_ok=True)
    development_root = outdir / "development"
    controls_root = outdir / "controls"
    reports_root = outdir / "reports"
    charts_root = outdir / "charts"
    for path in (
        development_root,
        controls_root,
        reports_root,
        charts_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    manifest_path = outdir / "mag_gate_control_manifest.json"
    manifest: dict[str, Any] = {
        "harness_version": HARNESS_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "runtime": {"python": platform.python_version()},
        "contract": {
            "path": str(contract_path),
            "sha256": sha256_file(contract_path),
            "contract_id": contract["contract_id"],
            "contract_version": contract["contract_version"],
        },
        "frozen_gate_contract": {
            "path": str(gate_contract_path),
            "sha256": sha256_file(gate_contract_path),
            "contract_id": gate_contract["contract_id"],
            "contract_version": gate_contract["contract_version"],
        },
        "gate": contract["gate"],
        "timing_radii_minutes": contract["timing_radii_minutes"],
        "interpretation_limits": contract["interpretation_limits"],
        "common_surface_test_completed": False,
        "ephemeris_test_completed": False,
        "physical_mechanism_classified": False,
    }
    write_json(manifest_path, manifest)

    try:
        development_manifest = run_gate_density_audit(
            contract_path=gate_contract_path,
            outdir=development_root / "gannon_gate_density",
        )
        if development_manifest.get("status") != "SUCCESS":
            raise ControlHarnessError(
                "development gate-density audit failed"
            )

        canonical_root = (
            development_root / "gannon_gate_density" / "canonical"
        )
        frames = {
            "DSCOVR": pd.read_csv(
                canonical_root / "dscovr_mag_gate_density.csv"
            ),
            "ACE": pd.read_csv(
                canonical_root / "ace_mag_gate_density.csv"
            ),
            "WIND": pd.read_csv(
                canonical_root / "wind_mag_gate_density.csv"
            ),
        }
        development_start = to_utc(
            gate_contract["analysis_window"]["start_utc"]
        )
        candidate_time = to_utc(
            gate_contract["analysis_window"]["candidate_utc"]
        )
        candidate_index = minute_index(candidate_time, development_start)
        arrays = {
            mission: day_arrays(frame, day_start=development_start)
            for mission, frame in frames.items()
        }
        radii = [
            int(value) for value in contract["timing_radii_minutes"]
        ]
        half_window = int(
            contract["gate"]["support_half_window_minutes"]
        )
        observed, observed_support = support_metrics(
            dscovr=arrays["DSCOVR"],
            ace=arrays["ACE"],
            wind=arrays["WIND"],
            candidate_index=candidate_index,
            radii=radii,
            half_window=half_window,
        )
        observed_support.to_csv(
            controls_root / "observed_gannon_gate_anchor_support.csv",
            index=False,
        )

        circular_config = contract["circular_shift_null"]
        circular_table, circular_summary = run_circular_controls(
            dscovr=arrays["DSCOVR"],
            ace=arrays["ACE"],
            wind=arrays["WIND"],
            observed=observed,
            candidate_index=candidate_index,
            radii=radii,
            half_window=half_window,
            iterations=int(circular_config["iterations"]),
            seed=int(circular_config["seed"]),
            minimum_pairwise_separation=int(
                circular_config["minimum_pairwise_separation_minutes"]
            ),
        )
        circular_table.to_csv(
            controls_root / "circular_shift_iterations.csv", index=False
        )
        write_json(
            controls_root / "circular_shift_summary.json",
            circular_summary,
        )

        mismatch_config = contract["mismatched_day_null"]
        ace_frames, wind_frames, source_metadata, density_rows = (
            fetch_mismatched_days(
                development_start=development_start,
                ace_offsets=[
                    int(value)
                    for value in mismatch_config["ace_day_offsets"]
                ],
                wind_offsets=[
                    int(value)
                    for value in mismatch_config["wind_day_offsets"]
                ],
                raw_root=controls_root / "mismatched_days",
                rotation_threshold=float(
                    contract["gate"]["rotation_threshold_degrees"]
                ),
                magnitude_threshold=float(
                    contract["gate"][
                        "magnitude_change_threshold_fraction"
                    ]
                ),
                minimum_evaluable_fraction=float(
                    mismatch_config["minimum_evaluable_fraction"]
                ),
            )
        )
        density_table = pd.DataFrame(density_rows)
        density_table.to_csv(
            controls_root / "mismatched_day_gate_density.csv",
            index=False,
        )
        mismatched_table = run_mismatched_controls(
            dscovr=arrays["DSCOVR"],
            ace_frames=ace_frames,
            wind_frames=wind_frames,
            development_start=development_start,
            candidate_index=candidate_index,
            radii=radii,
            half_window=half_window,
            exclude_equal_offsets=bool(
                mismatch_config["exclude_equal_offsets"]
            ),
        )
        mismatched_table.to_csv(
            controls_root / "mismatched_day_pairs.csv", index=False
        )
        minimum_pairs = int(
            mismatch_config["minimum_admitted_pair_rows"]
        )
        mismatched_summary = (
            compare_mismatched(
                mismatched_table,
                observed=observed,
                radii=radii,
            )
            if len(mismatched_table) >= minimum_pairs
            else None
        )
        write_json(
            controls_root / "mismatched_day_summary.json",
            {
                "summary": mismatched_summary,
                "minimum_required_pair_rows": minimum_pairs,
                "admitted_ace_offsets": sorted(ace_frames),
                "admitted_wind_offsets": sorted(wind_frames),
                "source_days": source_metadata,
            },
        )

        current_assessment = assessment(
            circular_summary=circular_summary,
            mismatched_summary=mismatched_summary,
            decision_policy=contract["decision_policy"],
            event_class_status=contract["event_class_controls"]["status"],
        )
        chart_paths = build_charts(
            circular=circular_table,
            mismatched=mismatched_table,
            observed=observed,
            outdir=charts_root,
        )

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "observed_development_metrics": observed,
                "development_gate_density_manifest": {
                    "path": str(
                        development_root
                        / "gannon_gate_density"
                        / "gannon_gate_density_manifest.json"
                    ),
                    "sha256": sha256_file(
                        development_root
                        / "gannon_gate_density"
                        / "gannon_gate_density_manifest.json"
                    ),
                },
                "circular_shift_null": circular_summary,
                "mismatched_day_null": mismatched_summary,
                "mismatched_day_source_metadata": source_metadata,
                "mismatched_day_gate_density": density_rows,
                "event_class_controls": contract["event_class_controls"],
                "assessment": current_assessment,
                "chart_paths": [str(path) for path in chart_paths],
            }
        )
        build_report(
            path=reports_root / "MAG_GATE_CONTROL_HARNESS.md",
            manifest=manifest,
        )
        artifacts: list[dict[str, Any]] = []
        for path in sorted(outdir.rglob("*")):
            if path.is_file() and path != manifest_path:
                artifacts.append(
                    {
                        "path": path.relative_to(outdir).as_posix(),
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
        manifest["artifacts"] = artifacts
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
        default=Path("config/mag_gate_controls.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/mag_gate_controls"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_harness(
        contract_path=args.config, outdir=args.outdir
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "hard_null_state": manifest["assessment"][
                    "hard_null_state"
                ],
                "overall_calibration_state": manifest["assessment"][
                    "overall_calibration_state"
                ],
                "geometry_stage_state": manifest["assessment"][
                    "geometry_stage_state"
                ],
                "outdir": str(args.outdir),
            }
        )
    )


if __name__ == "__main__":
    main()
