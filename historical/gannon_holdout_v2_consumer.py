#!/usr/bin/env python3
"""Consume the frozen Gannon V2 holdout registry without changing its dates.

The module has three deliberately separate commands:

``plan``
    Verify the committed registry and emit the exact 43-row Actions matrix.
``score-interval``
    Retrieve the pinned DSCOVR, ACE, and Wind magnetic products for one frozen
    interval, apply the unchanged one-minute GSE gate, and run within-interval
    circular-shift and moving-block controls. Provider or completeness failures
    become ``INCOMPLETE_MULTIPOINT`` evidence rather than substitutions.
``aggregate``
    Reconcile all frozen registry rows, run registered-interval mismatched-day
    controls, produce class-specific summaries, and write the holdout capsule.

No command estimates geometry, propagation, or a physical discontinuity class.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.download_dscovr_cdaweb import (
    canonicalize_one_minute as canonicalize_dscovr,
    download_cdaweb as download_dscovr,
    format_cdaweb_date,
)
from historical.gannon_gate_controls import deterministic_shift_pairs
from historical.gannon_gate_density import (
    add_exact_minute_diagnostics,
    cluster_gate_events,
    hourly_gate_counts,
    standardize_dscovr,
    summarize_mission,
)
from historical.gannon_multipoint_audit import (
    canonicalize_vector_minutes,
    fetch_hapi,
    parse_cdas_rows,
    request_cdas_text,
)

CONSUMER_VERSION = "1.0.1"
MINUTES_PER_DAY = 1440
DEFAULT_CONTRACT = Path("config/gannon_holdout_v2_consumer.v1.json")


class HoldoutConsumerError(RuntimeError):
    """Raised when immutable holdout consumption cannot proceed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise HoldoutConsumerError(f"required JSON file is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HoldoutConsumerError(f"expected JSON object in {path}")
    return value


def safe_id(value: str) -> str:
    output = "".join(character if character.isalnum() else "-" for character in value)
    return "-".join(part for part in output.split("-") if part)


def _registry_content_hash(registry: dict[str, Any]) -> str:
    view = json.loads(json.dumps(registry))
    view.pop("registry_content_sha256", None)
    view.pop("created_utc", None)
    return sha256_bytes(
        json.dumps(view, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise HoldoutConsumerError(
            f"{label} drifted: expected {expected!r}, found {actual!r}"
        )


def verify_registry(
    contract_path: Path = DEFAULT_CONTRACT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Fail closed unless the exact committed registry is being consumed."""

    contract = load_json(contract_path)
    _assert_equal(
        contract.get("status"),
        "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL",
        "consumer contract status",
    )
    registry_spec = contract["registry"]
    registry_path = Path(registry_spec["path"])
    status_path = Path(registry_spec["status_path"])
    inventory_path = Path(registry_spec["inventory_path"])
    registry = load_json(registry_path)
    registry_status = load_json(status_path)
    inventory = load_json(inventory_path)

    _assert_equal(
        sha256_file(registry_path),
        registry_spec["expected_file_sha256"],
        "registry file SHA-256",
    )
    _assert_equal(
        registry.get("registry_content_sha256"),
        registry_spec["expected_content_sha256"],
        "registry embedded content SHA-256",
    )
    _assert_equal(
        _registry_content_hash(registry),
        registry_spec["expected_content_sha256"],
        "recomputed registry content SHA-256",
    )
    _assert_equal(
        registry.get("status"),
        "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL",
        "registry status",
    )
    _assert_equal(
        registry_status.get("status"),
        "HOLD_OUT_REGISTRY_PUBLISHED",
        "registry publication status",
    )

    expected_counts = {
        name: int(count)
        for name, count in registry_spec["expected_class_denominators"].items()
    }
    actual_counts = Counter(item["class"] for item in registry["intervals"])
    _assert_equal(dict(actual_counts), expected_counts, "registry class denominators")
    _assert_equal(
        len(registry["intervals"]),
        int(registry_spec["expected_total_intervals"]),
        "registry total interval count",
    )
    _assert_equal(
        registry.get("class_denominators"),
        expected_counts,
        "embedded class denominators",
    )
    _assert_equal(
        registry_status.get("class_denominators"),
        expected_counts,
        "published-state class denominators",
    )

    ids = [item["interval_id"] for item in registry["intervals"]]
    if len(ids) != len(set(ids)):
        raise HoldoutConsumerError("registry contains duplicate interval IDs")

    firewall = registry["selection_firewall"]
    for key in (
        "spacecraft_mag_retrieved",
        "mag_values_inspected",
        "gate_outputs_inspected",
        "clustering_outputs_inspected",
    ):
        if firewall.get(key) is not False:
            raise HoldoutConsumerError(f"registry selection firewall changed at {key}")
    for item in registry["intervals"]:
        if item.get("v1_inspected_window") is not False:
            raise HoldoutConsumerError(
                f"registry interval is marked V1-inspected: {item['interval_id']}"
            )
        for key in (
            "mag_inspected_before_freeze",
            "gate_output_inspected_before_freeze",
            "clustering_output_inspected_before_freeze",
            "replacement_after_scoring_allowed",
        ):
            if item.get(key) is not False:
                raise HoldoutConsumerError(
                    f"registry immutability flag changed: {item['interval_id']} {key}"
                )
        _assert_equal(
            item.get("failure_policy"),
            "INCOMPLETE_MULTIPOINT_RETAIN_IN_DENOMINATOR",
            f"failure policy for {item['interval_id']}",
        )

    _assert_equal(
        inventory.get("status"),
        "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL",
        "inventory status",
    )
    for key in (
        "spacecraft_mag_retrieved",
        "gate_outputs_inspected",
        "clustering_outputs_inspected",
    ):
        if inventory.get(key) is not False:
            raise HoldoutConsumerError(f"inventory pre-MAG state changed at {key}")
    inventory_files = {item["path"]: item for item in inventory.get("files", [])}
    registry_inventory = inventory_files.get(registry_path.as_posix())
    if registry_inventory is None:
        raise HoldoutConsumerError("inventory does not contain the frozen registry")
    _assert_equal(
        registry_inventory.get("sha256"),
        registry_spec["expected_file_sha256"],
        "inventory registry SHA-256",
    )
    for item in inventory.get("files", []):
        path = Path(item["path"])
        if not path.is_file():
            raise HoldoutConsumerError(f"inventory file is absent: {path}")
        _assert_equal(sha256_file(path), item["sha256"], f"inventory hash for {path}")

    prereg = load_json(Path(contract["preregistration_path"]))
    effective = load_json(Path(contract["effective_selection_contract_path"]))
    _assert_equal(
        prereg.get("status"),
        "PREREGISTERED_BEFORE_HOLDOUT_MAG_INSPECTION",
        "V2 preregistration status",
    )
    detector = contract["detector"]
    for source, label in ((prereg["detector"], "preregistration"), (effective["frozen_detector"], "effective selection contract")):
        for key in (
            "coordinate_frame",
            "canonical_cadence_seconds",
            "required_previous_offset_seconds",
            "rotation_threshold_degrees",
            "magnitude_change_threshold_fraction",
            "logical_operator",
            "timing_radii_minutes",
        ):
            _assert_equal(source.get(key), detector.get(key), f"{label} detector {key}")
    hypothesis = prereg["primary_clustering_hypothesis"]
    primary = contract["primary_clustering_hypothesis"]
    _assert_equal(
        hypothesis.get("nearest_joint_support_radius_minutes_lte"),
        primary.get("nearest_joint_support_radius_minutes_lte"),
        "nearest-support primary radius",
    )
    _assert_equal(
        hypothesis.get("strongest_three_spacecraft_span_minutes_lte"),
        primary.get("strongest_three_spacecraft_span_minutes_lte"),
        "strongest-span primary radius",
    )
    if hypothesis.get("retuning_after_holdout_inspection_allowed") is not False:
        raise HoldoutConsumerError("preregistration now allows radius retuning")

    return contract, registry, inventory


def canonical_window(interval: dict[str, Any], contract: dict[str, Any]) -> dict[str, pd.Timestamp]:
    start = pd.Timestamp(interval["start_utc"])
    stop = pd.Timestamp(interval["stop_utc"])
    if start.tzinfo is None or stop.tzinfo is None:
        raise HoldoutConsumerError(f"interval is not UTC-aware: {interval['interval_id']}")
    start, stop = start.tz_convert("UTC"), stop.tz_convert("UTC")
    if stop - start != pd.Timedelta(days=1):
        raise HoldoutConsumerError(
            f"registered interval is not 24 hours: {interval['interval_id']}"
        )
    grid_start = start.ceil("min")
    grid_stop = stop.ceil("min")
    expected = int((grid_stop - grid_start).total_seconds() // 60)
    _assert_equal(expected, MINUTES_PER_DAY, "canonical interval length")
    preroll = int(contract["canonical_window"]["predecessor_preroll_minutes"])
    return {
        "registered_start": start,
        "registered_stop": stop,
        "grid_start": grid_start,
        "grid_stop": grid_stop,
        "retrieval_start": grid_start - pd.Timedelta(minutes=preroll),
        "retrieval_stop": grid_stop,
    }


def _iso(value: pd.Timestamp) -> str:
    return value.isoformat().replace("+00:00", "Z")


def canonical_magnitude_provenance(mission: str) -> dict[str, Any]:
    """Describe exactly how canonical |B| is constructed for one source."""

    records: dict[str, dict[str, Any]] = {
        "DSCOVR": {
  "dataset_id": "DSCOVR_H0_MAG",
  "component_source": "B1GSE",
  "component_columns": [
      "resolved B1GSE Bx GSE component",
      "resolved B1GSE By GSE component",
      "resolved B1GSE Bz GSE component",
  ],
  "provider_reported_magnitude_parameter": None,
  "provider_reported_magnitude_role": "NOT_PRESENT_IN_REQUEST",
        },
        "ACE": {
  "dataset_id": "AC_H0_MFI",
  "component_source": "BGSEc",
  "component_columns": ["BGSEc_x", "BGSEc_y", "BGSEc_z"],
  "provider_reported_magnitude_parameter": "Magnitude",
  "provider_reported_magnitude_role": "AUDIT_ONLY",
        },
        "WIND": {
  "dataset_id": "WI_H0_MFI",
  "component_source": "B3GSE",
  "component_columns": ["B3GSE_x", "B3GSE_y", "B3GSE_z"],
  "provider_reported_magnitude_parameter": "B3F1",
  "provider_reported_magnitude_role": "AUDIT_ONLY",
        },
    }
    if mission not in records:
        raise HoldoutConsumerError(
  f"unsupported canonical magnitude provenance mission: {mission}"
        )
    return {
        **records[mission],
        "coordinate_frame": "GSE",
        "canonical_quantity": "B_mag_nT",
        "provider_reported_magnitude_used_for_canonical_B": False,
        "operation_order": [
  "average native vector components within each canonical UTC minute",
  "calculate Euclidean norm from the three component means",
        ],
        "formula": (
  "B_mag_nT = sqrt(mean(Bx_GSE)^2 + mean(By_GSE)^2 + "
  "mean(Bz_GSE)^2)"
        ),
        "nonhomologous_source_warning": (
  "provider scalar magnitude fields are retained only for source audit; "
  "they are not averaged into or substituted for canonical B_mag_nT"
        ),
    }

def _slice_analysis(frame: pd.DataFrame, window: dict[str, pd.Timestamp]) -> pd.DataFrame:
    output = frame.copy()
    output["time"] = pd.to_datetime(output["time"], format="ISO8601", utc=True, errors="coerce")
    if output["time"].isna().any():
        raise HoldoutConsumerError("canonical table contains invalid timestamps")
    output = output.loc[
        (output["time"] >= window["grid_start"])
        & (output["time"] < window["grid_stop"])
    ].copy()
    output.sort_values("time", inplace=True)
    output.reset_index(drop=True, inplace=True)
    if output["time"].duplicated().any():
        raise HoldoutConsumerError("canonical table contains duplicate analysis minutes")
    return output


def retrieve_dscovr(
    *,
    window: dict[str, pd.Timestamp],
    source: dict[str, Any],
    detector: dict[str, Any],
    outdir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    outdir.mkdir(parents=True, exist_ok=True)
    _assert_equal(source["dataset_id"], "DSCOVR_H0_MAG", "DSCOVR dataset")
    _assert_equal(source["variables"], ["B1GSE"], "DSCOVR variables")
    raw, metadata = download_dscovr(
        format_cdaweb_date(_iso(window["retrieval_start"])),
        format_cdaweb_date(_iso(window["retrieval_stop"])),
        outdir,
    )
    canonical, metrics = canonicalize_dscovr(raw, outdir)
    standardized = standardize_dscovr(
        canonical,
        rotation_threshold_degrees=float(detector["rotation_threshold_degrees"]),
        magnitude_change_threshold_fraction=float(
            detector["magnitude_change_threshold_fraction"]
        ),
    )
    standardized = _slice_analysis(standardized, window)
    quarantine_path = outdir / "dscovr_quarantine.csv"
    quarantine = (
        pd.read_csv(quarantine_path)
        if quarantine_path.is_file()
        else pd.DataFrame()
    )
    metadata = {
        **{
            key: str(value) if isinstance(value, Path) else value
            for key, value in metadata.items()
        },
        "dataset_id": source["dataset_id"],
        "variables": source["variables"],
        "coordinate_frame": "GSE",
        "canonicalization": metrics,
        "canonical_magnitude_provenance": canonical_magnitude_provenance("DSCOVR"),
    }
    return standardized, quarantine, metadata


def retrieve_ace(
    *,
    session: requests.Session,
    window: dict[str, pd.Timestamp],
    source: dict[str, Any],
    detector: dict[str, Any],
    outdir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    _assert_equal(source["dataset_id"], "AC_H0_MFI", "ACE dataset")
    _assert_equal(
        source["parameters"],
        ["Magnitude", "BGSEc", "SC_pos_GSE"],
        "ACE parameters",
    )
    raw, metadata, _ = fetch_hapi(
        session,
        dataset_id=source["dataset_id"],
        parameters=source["parameters"],
        start=_iso(window["retrieval_start"]),
        stop=_iso(window["retrieval_stop"]),
        outdir=outdir,
    )
    canonical, quarantine = canonicalize_vector_minutes(
        raw,
        components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
        position_components=("SC_pos_GSE_x", "SC_pos_GSE_y", "SC_pos_GSE_z"),
        minimum_samples=int(source["minimum_native_samples_per_minute"]),
        source=source["dataset_id"],
    )
    canonical = add_exact_minute_diagnostics(
        canonical,
        rotation_threshold_degrees=float(detector["rotation_threshold_degrees"]),
        magnitude_change_threshold_fraction=float(
            detector["magnitude_change_threshold_fraction"]
        ),
    )
    metadata = {
        **metadata,
        "canonical_magnitude_provenance": canonical_magnitude_provenance("ACE"),
    }
    return _slice_analysis(canonical, window), quarantine, metadata


def retrieve_wind(
    *,
    session: requests.Session,
    window: dict[str, pd.Timestamp],
    source: dict[str, Any],
    detector: dict[str, Any],
    outdir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    _assert_equal(source["dataset_id"], "WI_H0_MFI", "Wind dataset")
    _assert_equal(source["variables"], ["B3GSE", "B3F1"], "Wind variables")
    raw, metadata = request_cdas_text(
        session,
        dataset_id=source["dataset_id"],
        variables=source["variables"],
        start=_iso(window["retrieval_start"]),
        stop=_iso(window["retrieval_stop"]),
        outdir=outdir,
    )
    table = parse_cdas_rows(
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
        table,
        components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
        minimum_samples=int(source["minimum_native_samples_per_minute"]),
        source=source["dataset_id"],
    )
    canonical = add_exact_minute_diagnostics(
        canonical,
        rotation_threshold_degrees=float(detector["rotation_threshold_degrees"]),
        magnitude_change_threshold_fraction=float(
            detector["magnitude_change_threshold_fraction"]
        ),
    )
    metadata = {
        **metadata,
        "canonical_magnitude_provenance": canonical_magnitude_provenance("WIND"),
    }
    return _slice_analysis(canonical, window), quarantine, metadata


def minute_arrays(
    frame: pd.DataFrame,
    *,
    grid_start: pd.Timestamp,
    expected_minutes: int = MINUTES_PER_DAY,
) -> dict[str, np.ndarray]:
    required = {"time", "gate_pass", "gate_score", "exact_previous_minute"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise HoldoutConsumerError(f"canonical gate table lacks columns: {missing}")
    times = pd.to_datetime(frame["time"], format="ISO8601", utc=True, errors="coerce")
    if times.isna().any() or times.duplicated().any():
        raise HoldoutConsumerError("canonical gate table has invalid or duplicate times")
    offsets = (times - grid_start).dt.total_seconds() / 60.0
    if not np.allclose(offsets, np.round(offsets), equal_nan=False):
        raise HoldoutConsumerError("canonical table is not aligned to the frozen minute grid")
    indices = np.round(offsets).astype(int).to_numpy()
    if np.any(indices < 0) or np.any(indices >= expected_minutes):
        raise HoldoutConsumerError("canonical table extends outside the frozen interval")

    output = {
        "present": np.zeros(expected_minutes, dtype=bool),
        "evaluable": np.zeros(expected_minutes, dtype=bool),
        "gate": np.zeros(expected_minutes, dtype=bool),
        "score": np.full(expected_minutes, np.nan, dtype=float),
    }
    output["present"][indices] = True
    output["evaluable"][indices] = frame["exact_previous_minute"].astype(bool).to_numpy()
    output["gate"][indices] = frame["gate_pass"].astype(bool).to_numpy()
    output["score"][indices] = pd.to_numeric(frame["gate_score"], errors="coerce").to_numpy()
    return output


def _nearest_gate(gate: np.ndarray, center: int, radius: int) -> int | None:
    lower = max(0, center - radius)
    upper = min(len(gate) - 1, center + radius)
    indices = np.flatnonzero(gate[lower : upper + 1]) + lower
    if not len(indices):
        return None
    distances = np.abs(indices - center)
    return int(indices[np.lexsort((indices, distances))[0]])


def _strongest_gate(
    gate: np.ndarray,
    score: np.ndarray,
    center: int,
    radius: int,
) -> int | None:
    lower = max(0, center - radius)
    upper = min(len(gate) - 1, center + radius)
    indices = np.flatnonzero(gate[lower : upper + 1]) + lower
    if not len(indices):
        return None
    values = np.nan_to_num(score[indices], nan=-np.inf)
    distances = np.abs(indices - center)
    return int(indices[np.lexsort((indices, distances, -values))[0]])


def score_support(
    *,
    dscovr: dict[str, np.ndarray],
    ace: dict[str, np.ndarray],
    wind: dict[str, np.ndarray],
    support_half_window_minutes: int,
    minimum_independent_window_coverage_fraction: float,
    timing_radii_minutes: Sequence[int],
    nearest_primary_radius_minutes: int,
    strongest_primary_span_minutes: int,
) -> tuple[pd.DataFrame, dict[str, Any], np.ndarray, np.ndarray]:
    radius = int(support_half_window_minutes)
    minute_axis = np.arange(len(dscovr["gate"]))
    anchors = np.flatnonzero(
        dscovr["gate"]
        & dscovr["evaluable"]
        & (minute_axis >= radius)
        & (minute_axis < len(minute_axis) - radius)
    )
    rows: list[dict[str, Any]] = []
    eligible_by_minute = np.zeros(len(minute_axis), dtype=bool)
    primary_by_minute = np.zeros(len(minute_axis), dtype=bool)
    dscovr_scores = dscovr["score"][anchors]
    finite_dscovr_scores = dscovr_scores[np.isfinite(dscovr_scores)]

    for center in anchors:
        lower, upper = center - radius, center + radius + 1
        ace_coverage = float(np.mean(ace["present"][lower:upper]))
        wind_coverage = float(np.mean(wind["present"][lower:upper]))
        support_evaluable = (
            ace_coverage >= minimum_independent_window_coverage_fraction
            and wind_coverage >= minimum_independent_window_coverage_fraction
        )
        ace_nearest = _nearest_gate(ace["gate"], int(center), radius) if support_evaluable else None
        wind_nearest = _nearest_gate(wind["gate"], int(center), radius) if support_evaluable else None
        ace_strongest = (
            _strongest_gate(ace["gate"], ace["score"], int(center), radius)
            if support_evaluable
            else None
        )
        wind_strongest = (
            _strongest_gate(wind["gate"], wind["score"], int(center), radius)
            if support_evaluable
            else None
        )
        nearest_joint = (
            int(max(abs(ace_nearest - center), abs(wind_nearest - center)))
            if ace_nearest is not None and wind_nearest is not None
            else None
        )
        strongest_span = (
            int(max(center, ace_strongest, wind_strongest) - min(center, ace_strongest, wind_strongest))
            if ace_strongest is not None and wind_strongest is not None
            else None
        )
        primary = bool(
            support_evaluable
            and nearest_joint is not None
            and strongest_span is not None
            and nearest_joint <= nearest_primary_radius_minutes
            and strongest_span <= strongest_primary_span_minutes
        )
        if support_evaluable:
            eligible_by_minute[center] = True
            primary_by_minute[center] = primary
        score_value = float(dscovr["score"][center])
        percentile = (
            float(np.mean(finite_dscovr_scores <= score_value))
            if len(finite_dscovr_scores) and math.isfinite(score_value)
            else None
        )
        row: dict[str, Any] = {
            "minute_index": int(center),
            "dscovr_gate_score": score_value,
            "dscovr_gate_score_percentile_within_interval": percentile,
            "ace_window_coverage_fraction": ace_coverage,
            "wind_window_coverage_fraction": wind_coverage,
            "support_evaluable": support_evaluable,
            "ace_nearest_offset_minutes": int(ace_nearest - center) if ace_nearest is not None else None,
            "wind_nearest_offset_minutes": int(wind_nearest - center) if wind_nearest is not None else None,
            "nearest_joint_radius_minutes": nearest_joint,
            "ace_strongest_offset_minutes": int(ace_strongest - center) if ace_strongest is not None else None,
            "wind_strongest_offset_minutes": int(wind_strongest - center) if wind_strongest is not None else None,
            "strongest_three_spacecraft_span_minutes": strongest_span,
            "primary_clustering_pass": primary,
        }
        for timing_radius in timing_radii_minutes:
            row[f"joint_support_within_{int(timing_radius)}_minutes"] = bool(
                support_evaluable
                and nearest_joint is not None
                and nearest_joint <= int(timing_radius)
            )
        rows.append(row)

    columns = [
        "minute_index",
        "dscovr_gate_score",
        "dscovr_gate_score_percentile_within_interval",
        "ace_window_coverage_fraction",
        "wind_window_coverage_fraction",
        "support_evaluable",
        "ace_nearest_offset_minutes",
        "wind_nearest_offset_minutes",
        "nearest_joint_radius_minutes",
        "ace_strongest_offset_minutes",
        "wind_strongest_offset_minutes",
        "strongest_three_spacecraft_span_minutes",
        "primary_clustering_pass",
        *[
            f"joint_support_within_{int(value)}_minutes"
            for value in timing_radii_minutes
        ],
    ]
    table = pd.DataFrame(rows, columns=columns)
    eligible = table.loc[table["support_evaluable"].astype(bool)] if len(table) else table
    summary: dict[str, Any] = {
        "dscovr_gate_anchors_with_full_edge_window": int(len(table)),
        "support_evaluable_anchors": int(len(eligible)),
        "coverage_excluded_anchors": int(len(table) - len(eligible)),
        "primary_clustering_pass_rows": int(eligible["primary_clustering_pass"].sum()) if len(eligible) else 0,
        "primary_clustering_event_rate": float(eligible["primary_clustering_pass"].mean()) if len(eligible) else None,
        "support_fractions": {},
    }
    for timing_radius in timing_radii_minutes:
        field = f"joint_support_within_{int(timing_radius)}_minutes"
        summary["support_fractions"][str(int(timing_radius))] = (
            float(eligible[field].mean()) if len(eligible) else None
        )
    return table, summary, eligible_by_minute, primary_by_minute


def roll_array(value: np.ndarray, shift: int, *, fill: Any) -> tuple[np.ndarray, int]:
    rolled = np.roll(value, shift)
    width = min(abs(int(shift)), len(value))
    wrapped = int(np.count_nonzero(value[-width:] if shift > 0 else value[:width])) if width else 0
    if width:
        if shift > 0:
            rolled[:width] = fill
        else:
            rolled[-width:] = fill
    return rolled, wrapped


def _rolled_bundle(bundle: dict[str, np.ndarray], shift: int, *, no_wrap: bool) -> tuple[dict[str, np.ndarray], dict[str, int]]:
    if not no_wrap:
        return (
            {key: np.roll(value, shift) for key, value in bundle.items()},
            {"wrapped_gate_rows": int(np.count_nonzero(bundle["gate"][-abs(shift):] if shift > 0 else bundle["gate"][:abs(shift)])) if shift else 0},
        )
    output: dict[str, np.ndarray] = {}
    wrapped_gate_rows = 0
    for key, value in bundle.items():
        fill: Any = np.nan if value.dtype.kind == "f" else False
        output[key], wrapped = roll_array(value, shift, fill=fill)
        if key == "gate":
            wrapped_gate_rows = wrapped
    return output, {"wrapped_gate_rows": wrapped_gate_rows}


def circular_shift_null(
    *,
    observed_summary: dict[str, Any],
    dscovr: dict[str, np.ndarray],
    ace: dict[str, np.ndarray],
    wind: dict[str, np.ndarray],
    contract: dict[str, Any],
    interval_seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = contract["nulls"]["circular_shift"]
    pairs = deterministic_shift_pairs(
        iterations=int(settings["iterations_per_interval"]),
        seed=int(settings["seed"] + interval_seed),
        minimum_pairwise_separation_minutes=int(
            settings["minimum_pairwise_separation_minutes"]
        ),
    )
    rows: list[dict[str, Any]] = []
    for iteration, (ace_shift, wind_shift) in enumerate(pairs):
        ace_roll, ace_wrap = _rolled_bundle(ace, ace_shift, no_wrap=False)
        wind_roll, wind_wrap = _rolled_bundle(wind, wind_shift, no_wrap=False)
        _, summary, _, _ = score_support(
            dscovr=dscovr,
            ace=ace_roll,
            wind=wind_roll,
            support_half_window_minutes=int(contract["completeness"]["support_half_window_minutes"]),
            minimum_independent_window_coverage_fraction=float(
                contract["completeness"]["minimum_independent_window_coverage_fraction"]
            ),
            timing_radii_minutes=contract["detector"]["timing_radii_minutes"],
            nearest_primary_radius_minutes=int(
                contract["primary_clustering_hypothesis"]["nearest_joint_support_radius_minutes_lte"]
            ),
            strongest_primary_span_minutes=int(
                contract["primary_clustering_hypothesis"]["strongest_three_spacecraft_span_minutes_lte"]
            ),
        )
        ace_no_wrap, _ = _rolled_bundle(ace, ace_shift, no_wrap=True)
        wind_no_wrap, _ = _rolled_bundle(wind, wind_shift, no_wrap=True)
        _, no_wrap_summary, _, _ = score_support(
            dscovr=dscovr,
            ace=ace_no_wrap,
            wind=wind_no_wrap,
            support_half_window_minutes=int(contract["completeness"]["support_half_window_minutes"]),
            minimum_independent_window_coverage_fraction=float(
                contract["completeness"]["minimum_independent_window_coverage_fraction"]
            ),
            timing_radii_minutes=contract["detector"]["timing_radii_minutes"],
            nearest_primary_radius_minutes=int(
                contract["primary_clustering_hypothesis"]["nearest_joint_support_radius_minutes_lte"]
            ),
            strongest_primary_span_minutes=int(
                contract["primary_clustering_hypothesis"]["strongest_three_spacecraft_span_minutes_lte"]
            ),
        )
        rows.append(
            {
                "iteration": iteration,
                "ace_shift_minutes": int(ace_shift),
                "wind_shift_minutes": int(wind_shift),
                "ace_wrapped_gate_rows": ace_wrap["wrapped_gate_rows"],
                "wind_wrapped_gate_rows": wind_wrap["wrapped_gate_rows"],
                "support_evaluable_anchors": summary["support_evaluable_anchors"],
                "primary_clustering_pass_rows": summary["primary_clustering_pass_rows"],
                "primary_clustering_event_rate": summary["primary_clustering_event_rate"],
                "primary_clustering_event_rate_no_wrap": no_wrap_summary[
                    "primary_clustering_event_rate"
                ],
                "joint_support_fraction_within_2_minutes": summary["support_fractions"]["2"],
                "joint_support_fraction_within_3_minutes": summary["support_fractions"]["3"],
            }
        )
    table = pd.DataFrame(rows)
    observed = observed_summary["primary_clustering_event_rate"]
    finite = pd.to_numeric(table["primary_clustering_event_rate"], errors="coerce").dropna()
    extreme = int((finite >= float(observed)).sum()) if observed is not None else 0
    summary = {
        "iterations": int(len(table)),
        "observed_primary_clustering_event_rate": observed,
        "null_finite_rows": int(len(finite)),
        "null_median": float(finite.median()) if len(finite) else None,
        "null_q95": float(finite.quantile(0.95)) if len(finite) else None,
        "null_q99": float(finite.quantile(0.99)) if len(finite) else None,
        "equal_or_greater_rows": extreme,
        "plus_one_upper_tail_frequency": (
            float((extreme + 1) / (len(finite) + 1)) if len(finite) else None
        ),
        "label": "EMPIRICAL_CONTROL_FREQUENCY_NOT_INDEPENDENT_MINUTE_P_VALUE",
    }
    return table, summary


def moving_block_bootstrap(
    *,
    eligible: np.ndarray,
    primary: np.ndarray,
    iterations: int,
    block_minutes: int,
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if len(eligible) != len(primary):
        raise HoldoutConsumerError("moving-block arrays differ in length")
    rng = np.random.default_rng(seed)
    block_count = int(math.ceil(len(eligible) / block_minutes))
    rows: list[dict[str, Any]] = []
    for iteration in range(iterations):
        indices: list[int] = []
        for _ in range(block_count):
            start = int(rng.integers(0, len(eligible)))
            indices.extend((start + np.arange(block_minutes)) % len(eligible))
        chosen = np.asarray(indices[: len(eligible)], dtype=int)
        denominator = int(eligible[chosen].sum())
        numerator = int(primary[chosen].sum())
        rows.append(
            {
                "iteration": iteration,
                "eligible_anchor_rows": denominator,
                "primary_pass_rows": numerator,
                "primary_event_rate": numerator / denominator if denominator else np.nan,
            }
        )
    table = pd.DataFrame(rows)
    finite = table["primary_event_rate"].dropna()
    summary = {
        "iterations": int(len(table)),
        "block_minutes": int(block_minutes),
        "finite_rows": int(len(finite)),
        "q025": float(finite.quantile(0.025)) if len(finite) else None,
        "median": float(finite.median()) if len(finite) else None,
        "q975": float(finite.quantile(0.975)) if len(finite) else None,
    }
    return table, summary


def source_file_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "interval_manifest.json"
    ]


def _mission_completeness(
    frame: pd.DataFrame,
    *,
    expected_minutes: int,
) -> dict[str, Any]:
    evaluable = int(frame["exact_previous_minute"].astype(bool).sum())
    gate_rows = int(frame["gate_pass"].astype(bool).sum())
    times = pd.to_datetime(frame["time"], format="ISO8601", utc=True, errors="coerce")
    deltas = times.sort_values().diff().dt.total_seconds().dropna()
    return {
        "canonical_rows": int(len(frame)),
        "missing_canonical_rows": int(max(0, expected_minutes - len(frame))),
        "evaluable_exact_previous_rows": evaluable,
        "evaluable_fraction": evaluable / expected_minutes,
        "gate_rows": gate_rows,
        "gate_fraction_of_evaluable": gate_rows / evaluable if evaluable else None,
        "gap_intervals": int((deltas > 60.0).sum()),
        "maximum_gap_seconds": float(deltas.max()) if len(deltas) else None,
    }


def score_interval(
    *,
    contract_path: Path,
    interval_id: str,
    outdir: Path,
) -> dict[str, Any]:
    contract, registry, _ = verify_registry(contract_path)
    matches = [item for item in registry["intervals"] if item["interval_id"] == interval_id]
    if len(matches) != 1:
        raise HoldoutConsumerError(f"interval ID is not uniquely registered: {interval_id}")
    interval = matches[0]
    window = canonical_window(interval, contract)
    outdir.mkdir(parents=True, exist_ok=True)
    raw_root = outdir / "raw"
    canonical_root = outdir / "canonical"
    quarantine_root = outdir / "quarantine"
    summary_root = outdir / "summary"
    null_root = outdir / "nulls"
    for path in (raw_root, canonical_root, quarantine_root, summary_root, null_root):
        path.mkdir(parents=True, exist_ok=True)

    manifest_path = outdir / "interval_manifest.json"
    manifest: dict[str, Any] = {
        "consumer_version": CONSUMER_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "interval_id": interval_id,
        "registered_class": interval["class"],
        "registered_interval": {
            "start_utc": interval["start_utc"],
            "stop_utc": interval["stop_utc"],
            "mission_era_tag": interval["mission_era_tag"],
            "failure_policy": interval["failure_policy"],
            "replacement_after_scoring_allowed": interval[
                "replacement_after_scoring_allowed"
            ],
        },
        "canonical_window": {key: _iso(value) for key, value in window.items()},
        "registry": {
            "path": contract["registry"]["path"],
            "file_sha256": contract["registry"]["expected_file_sha256"],
            "content_sha256": contract["registry"]["expected_content_sha256"],
            "total_registered_intervals": contract["registry"][
                "expected_total_intervals"
            ],
        },
        "git_commit": os.environ.get("GITHUB_SHA", "LOCAL"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "runtime": {"python": platform.python_version()},
        "detector": contract["detector"],
        "primary_clustering_hypothesis": contract[
            "primary_clustering_hypothesis"
        ],
        "source_products": contract["source_products"],
        "canonical_magnitude_provenance": {
            mission: canonical_magnitude_provenance(mission)
            for mission in ("DSCOVR", "ACE", "WIND")
        },
        "source_status": {},
        "geometry_state": "CLOSED",
        "geometry_calculated": False,
        "propagation_calculated": False,
        "physical_class_assigned": False,
    }
    write_json(manifest_path, manifest)

    frames: dict[str, pd.DataFrame] = {}
    arrays: dict[str, dict[str, np.ndarray]] = {}
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-V2-HOLDOUT-CONSUMER/{CONSUMER_VERSION}"}
    )
    retrievals = {
        "DSCOVR": lambda: retrieve_dscovr(
            window=window,
            source=contract["source_products"]["DSCOVR"],
            detector=contract["detector"],
            outdir=raw_root / "DSCOVR_H0_MAG",
        ),
        "ACE": lambda: retrieve_ace(
            session=session,
            window=window,
            source=contract["source_products"]["ACE"],
            detector=contract["detector"],
            outdir=raw_root / "AC_H0_MFI",
        ),
        "WIND": lambda: retrieve_wind(
            session=session,
            window=window,
            source=contract["source_products"]["WIND"],
            detector=contract["detector"],
            outdir=raw_root / "WI_H0_MFI",
        ),
    }

    for mission, retrieve in retrievals.items():
        try:
            frame, quarantine, metadata = retrieve()
            frames[mission] = frame
            frame.to_csv(canonical_root / f"{mission.lower()}_mag_gate.csv", index=False)
            quarantine.to_csv(
                quarantine_root / f"{mission.lower()}_quarantine.csv", index=False
            )
            completeness = _mission_completeness(
                frame, expected_minutes=MINUTES_PER_DAY
            )
            manifest["source_status"][mission] = {
                "status": "RETRIEVED",
                "metadata": metadata,
                "completeness": completeness,
                "quarantine_rows": int(len(quarantine)),
            }
        except Exception as exc:  # provider failures are retained evidence
            manifest["source_status"][mission] = {
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
            }

    minimum_fraction = float(
        contract["completeness"]["minimum_evaluable_fraction_per_mission"]
    )
    incomplete_reasons: list[str] = []
    for mission in ("DSCOVR", "ACE", "WIND"):
        source_state = manifest["source_status"].get(mission, {})
        if source_state.get("status") != "RETRIEVED":
            incomplete_reasons.append(f"{mission}_SOURCE_UNAVAILABLE")
            continue
        fraction = source_state["completeness"]["evaluable_fraction"]
        if fraction < minimum_fraction:
            incomplete_reasons.append(
                f"{mission}_EVALUABLE_FRACTION_{fraction:.6f}_BELOW_{minimum_fraction:.6f}"
            )

    if not incomplete_reasons:
        for mission, frame in frames.items():
            arrays[mission] = minute_arrays(frame, grid_start=window["grid_start"])
        common_evaluable = (
            arrays["DSCOVR"]["evaluable"]
            & arrays["ACE"]["evaluable"]
            & arrays["WIND"]["evaluable"]
        )
        common_fraction = float(np.mean(common_evaluable))
        manifest["common_exact_previous_rows"] = int(common_evaluable.sum())
        manifest["common_exact_previous_fraction"] = common_fraction
        required_common = float(
            contract["completeness"]["minimum_common_exact_previous_fraction"]
        )
        if common_fraction < required_common:
            incomplete_reasons.append(
                f"COMMON_EXACT_PREVIOUS_FRACTION_{common_fraction:.6f}_BELOW_{required_common:.6f}"
            )

    if incomplete_reasons:
        manifest.update(
            {
                "status": "INCOMPLETE_MULTIPOINT",
                "completed_utc": utc_now(),
                "evaluable_for_holdout_scoring": False,
                "incomplete_reasons": incomplete_reasons,
                "registered_denominator_retained": True,
                "replacement_authorized": False,
                "artifacts": source_file_inventory(outdir),
            }
        )
        write_json(manifest_path, manifest)
        return manifest

    support, support_summary, eligible_by_minute, primary_by_minute = score_support(
        dscovr=arrays["DSCOVR"],
        ace=arrays["ACE"],
        wind=arrays["WIND"],
        support_half_window_minutes=int(
            contract["completeness"]["support_half_window_minutes"]
        ),
        minimum_independent_window_coverage_fraction=float(
            contract["completeness"]["minimum_independent_window_coverage_fraction"]
        ),
        timing_radii_minutes=contract["detector"]["timing_radii_minutes"],
        nearest_primary_radius_minutes=int(
            contract["primary_clustering_hypothesis"][
                "nearest_joint_support_radius_minutes_lte"
            ]
        ),
        strongest_primary_span_minutes=int(
            contract["primary_clustering_hypothesis"][
                "strongest_three_spacecraft_span_minutes_lte"
            ]
        ),
    )
    support["time_utc"] = [
        _iso(window["grid_start"] + pd.Timedelta(minutes=int(value)))
        for value in support["minute_index"]
    ] if len(support) else []
    support.to_csv(summary_root / "anchor_support.csv", index=False)

    mission_summary = pd.DataFrame(
        [summarize_mission(frame, mission) for mission, frame in frames.items()]
    )
    mission_summary.to_csv(summary_root / "mission_gate_density.csv", index=False)
    hourly_gate_counts(frames).to_csv(
        summary_root / "hourly_gate_density.csv", index=False
    )
    gate_parts: list[pd.DataFrame] = []
    for mission, frame in frames.items():
        selected = frame.loc[frame["gate_pass"]].copy()
        selected["mission"] = mission
        gate_parts.append(selected[["mission", "time"]])
    gate_runs = cluster_gate_events(
        pd.concat(gate_parts, ignore_index=True, sort=False)
        if gate_parts
        else pd.DataFrame(columns=["mission", "time"])
    )
    gate_runs.to_csv(summary_root / "contiguous_gate_runs.csv", index=False)

    interval_seed = int(sha256_bytes(interval_id.encode("utf-8"))[:8], 16)
    circular, circular_summary = circular_shift_null(
        observed_summary=support_summary,
        dscovr=arrays["DSCOVR"],
        ace=arrays["ACE"],
        wind=arrays["WIND"],
        contract=contract,
        interval_seed=interval_seed,
    )
    circular.to_csv(null_root / "circular_shift.csv", index=False)

    block = contract["nulls"]["moving_block_uncertainty"]
    bootstrap, bootstrap_summary = moving_block_bootstrap(
        eligible=eligible_by_minute,
        primary=primary_by_minute,
        iterations=int(block["iterations_per_interval"]),
        block_minutes=int(block["block_minutes"]),
        seed=int(block["seed"] + interval_seed),
    )
    bootstrap.to_csv(null_root / "moving_block_bootstrap.csv", index=False)

    manifest.update(
        {
            "status": "SUCCESS",
            "completed_utc": utc_now(),
            "evaluable_for_holdout_scoring": True,
            "registered_denominator_retained": True,
            "replacement_authorized": False,
            "mission_gate_density": mission_summary.to_dict(orient="records"),
            "support_summary": support_summary,
            "circular_shift_null": circular_summary,
            "moving_block_uncertainty": bootstrap_summary,
            "artifacts": source_file_inventory(outdir),
        }
    )
    write_json(manifest_path, manifest)
    return manifest


def plan_matrix(
    *,
    contract_path: Path,
    output: Path | None,
) -> dict[str, Any]:
    _, registry, _ = verify_registry(contract_path)
    matrix = {
        "include": [
            {
                "interval_id": item["interval_id"],
                "safe_id": safe_id(item["interval_id"]),
                "class_name": item["class"],
            }
            for item in registry["intervals"]
        ]
    }
    if output is not None:
        write_json(output, matrix)
    return matrix


def _load_interval_outputs(
    *,
    registry: dict[str, Any],
    input_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    manifests: dict[str, dict[str, Any]] = {}
    for path in input_root.rglob("interval_manifest.json"):
        data = load_json(path)
        interval_id = data.get("interval_id")
        if interval_id in manifests:
            raise HoldoutConsumerError(f"duplicate interval artifact: {interval_id}")
        data["_manifest_path"] = str(path)
        data["_interval_root"] = str(path.parent)
        manifests[str(interval_id)] = data

    rows: list[dict[str, Any]] = []
    for item in registry["intervals"]:
        interval_id = item["interval_id"]
        manifest = manifests.get(interval_id)
        if manifest is None:
            rows.append(
                {
                    "interval_id": interval_id,
                    "class": item["class"],
                    "mission_era_tag": item["mission_era_tag"],
                    "registered_start_utc": item["start_utc"],
                    "registered_stop_utc": item["stop_utc"],
                    "status": "INCOMPLETE_MULTIPOINT",
                    "evaluable_for_holdout_scoring": False,
                    "reason": "MISSING_INTERVAL_ARTIFACT_RETAINED_IN_DENOMINATOR",
                }
            )
            continue
        if manifest.get("registered_class") != item["class"]:
            raise HoldoutConsumerError(f"artifact class drift: {interval_id}")
        rows.append(
            {
                "interval_id": interval_id,
                "class": item["class"],
                "mission_era_tag": item["mission_era_tag"],
                "registered_start_utc": item["start_utc"],
                "registered_stop_utc": item["stop_utc"],
                "status": manifest.get("status"),
                "evaluable_for_holdout_scoring": bool(
                    manifest.get("evaluable_for_holdout_scoring", False)
                ),
                "reason": "|".join(manifest.get("incomplete_reasons", [])),
            }
        )
    unknown = sorted(set(manifests) - {item["interval_id"] for item in registry["intervals"]})
    if unknown:
        raise HoldoutConsumerError(f"unregistered interval artifacts found: {unknown}")
    return manifests, rows


def _read_bundle(
    manifest: dict[str, Any],
    mission: str,
) -> tuple[dict[str, np.ndarray], pd.Timestamp]:
    root = Path(manifest["_interval_root"])
    frame = pd.read_csv(root / "canonical" / f"{mission.lower()}_mag_gate.csv")
    grid_start = pd.Timestamp(manifest["canonical_window"]["grid_start"])
    if grid_start.tzinfo is None:
        grid_start = grid_start.tz_localize("UTC")
    else:
        grid_start = grid_start.tz_convert("UTC")
    return minute_arrays(frame, grid_start=grid_start), grid_start


def deterministic_mismatched_triplets(
    *,
    candidates: list[dict[str, Any]],
    iterations: int,
    seed: int,
    minimum_date_separation_days: int,
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    admissible: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for dscovr in candidates:
        d_time = pd.Timestamp(dscovr["registered_start_utc"])
        for ace in candidates:
            if ace["interval_id"] == dscovr["interval_id"]:
                continue
            a_time = pd.Timestamp(ace["registered_start_utc"])
            if abs((a_time - d_time).total_seconds()) < minimum_date_separation_days * 86400:
                continue
            for wind in candidates:
                if wind["interval_id"] in {dscovr["interval_id"], ace["interval_id"]}:
                    continue
                if wind["mission_era_tag"] != dscovr["mission_era_tag"] or ace["mission_era_tag"] != dscovr["mission_era_tag"]:
                    continue
                w_time = pd.Timestamp(wind["registered_start_utc"])
                separations = (
                    abs((w_time - d_time).total_seconds()),
                    abs((w_time - a_time).total_seconds()),
                )
                if min(separations) < minimum_date_separation_days * 86400:
                    continue
                admissible.append((dscovr, ace, wind))
    if not admissible:
        return []
    rng = np.random.default_rng(seed)
    if len(admissible) <= iterations:
        order = rng.permutation(len(admissible))
    else:
        order = rng.choice(len(admissible), size=iterations, replace=False)
    return [admissible[int(index)] for index in order]


def _null_summary(observed: float | None, values: pd.Series) -> dict[str, Any]:
    finite = pd.to_numeric(values, errors="coerce").dropna()
    if observed is None or not len(finite):
        return {"observed": observed, "finite_controls": int(len(finite)), "state": "NO_COMPARISON"}
    extreme = int((finite >= observed).sum())
    return {
        "observed": observed,
        "finite_controls": int(len(finite)),
        "control_minimum": float(finite.min()),
        "control_median": float(finite.median()),
        "control_q95": float(finite.quantile(0.95)),
        "control_q99": float(finite.quantile(0.99)),
        "control_maximum": float(finite.max()),
        "equal_or_greater_controls": extreme,
        "plus_one_upper_tail_frequency": float((extreme + 1) / (len(finite) + 1)),
        "label": "EMPIRICAL_CONTROL_FREQUENCY_NOT_COMMON_SURFACE_PROOF",
    }


def build_aggregate_charts(
    *,
    class_summary: pd.DataFrame,
    circular: pd.DataFrame,
    mismatch: pd.DataFrame,
    outdir: Path,
) -> list[str]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    if not class_summary.empty:
        plt.figure(figsize=(11, 5))
        labels = class_summary["class"].tolist()
        values = class_summary["primary_clustering_event_rate"].tolist()
        plt.bar(labels, values)
        plt.xticks(rotation=20, ha="right")
        plt.ylabel("Primary clustering event rate")
        plt.title("Prospective V2 Holdout by Catalog-Selected Class")
        plt.tight_layout()
        path = outdir / "primary_rate_by_class.png"
        plt.savefig(path, dpi=180)
        plt.close()
        paths.append(path.as_posix())
    for table, name, title in (
        (circular, "circular_shift_primary_rate.png", "Circular-Shift Primary-Rate Controls"),
        (mismatch, "mismatched_day_primary_rate.png", "Mismatched Registered-Day Primary-Rate Controls"),
    ):
        if table.empty:
            continue
        values = pd.to_numeric(table["primary_clustering_event_rate"], errors="coerce").dropna()
        if values.empty:
            continue
        plt.figure(figsize=(9, 5))
        plt.hist(values, bins=30)
        plt.xlabel("Primary clustering event rate")
        plt.ylabel("Control realizations")
        plt.title(title)
        plt.tight_layout()
        path = outdir / name
        plt.savefig(path, dpi=180)
        plt.close()
        paths.append(path.as_posix())
    return paths


def aggregate(
    *,
    contract_path: Path,
    input_root: Path,
    outdir: Path,
) -> dict[str, Any]:
    contract, registry, _ = verify_registry(contract_path)
    outdir.mkdir(parents=True, exist_ok=True)
    manifests, status_rows = _load_interval_outputs(
        registry=registry, input_root=input_root
    )
    status = pd.DataFrame(status_rows)
    status.to_csv(outdir / "interval_status.csv", index=False)

    evaluable_ids = set(
        status.loc[status["evaluable_for_holdout_scoring"], "interval_id"].tolist()
    )
    metric_rows: list[dict[str, Any]] = []
    circular_parts: list[pd.DataFrame] = []
    moving_block_parts: list[pd.DataFrame] = []
    for interval_id in sorted(evaluable_ids):
        manifest = manifests[interval_id]
        root = Path(manifest["_interval_root"])
        support = pd.read_csv(root / "summary" / "anchor_support.csv")
        eligible = support.loc[support["support_evaluable"].astype(bool)]
        row: dict[str, Any] = {
            "interval_id": interval_id,
            "class": manifest["registered_class"],
            "mission_era_tag": manifest["registered_interval"]["mission_era_tag"],
            "support_evaluable_anchors": int(len(eligible)),
            "primary_pass_rows": int(eligible["primary_clustering_pass"].astype(bool).sum()),
            "primary_clustering_event_rate": float(eligible["primary_clustering_pass"].astype(bool).mean()) if len(eligible) else np.nan,
        }
        for radius in contract["detector"]["timing_radii_minutes"]:
            field = f"joint_support_within_{int(radius)}_minutes"
            row[f"joint_support_fraction_within_{int(radius)}_minutes"] = float(eligible[field].astype(bool).mean()) if len(eligible) else np.nan
        for mission_record in manifest["mission_gate_density"]:
            mission = mission_record["mission"].lower()
            row[f"{mission}_evaluable_rows"] = mission_record["evaluable_exact_previous_rows"]
            row[f"{mission}_gate_rows"] = mission_record["gate_rows"]
            row[f"{mission}_gate_fraction"] = mission_record["gate_fraction_of_evaluable"]
        metric_rows.append(row)

        circular_path = root / "nulls" / "circular_shift.csv"
        circular = pd.read_csv(circular_path)
        circular["interval_id"] = interval_id
        circular["class"] = manifest["registered_class"]
        circular_parts.append(circular)

        moving_path = root / "nulls" / "moving_block_bootstrap.csv"
        moving = pd.read_csv(moving_path)
        moving["interval_id"] = interval_id
        moving["class"] = manifest["registered_class"]
        moving_block_parts.append(moving)

    metric_columns = [
        "interval_id",
        "class",
        "mission_era_tag",
        "support_evaluable_anchors",
        "primary_pass_rows",
        "primary_clustering_event_rate",
        *[
            f"joint_support_fraction_within_{int(value)}_minutes"
            for value in contract["detector"]["timing_radii_minutes"]
        ],
        *[
            f"{mission}_{field}"
            for mission in ("dscovr", "ace", "wind")
            for field in ("evaluable_rows", "gate_rows", "gate_fraction")
        ],
    ]
    metrics = pd.DataFrame(metric_rows, columns=metric_columns)
    metrics.to_csv(outdir / "per_interval_metrics.csv", index=False)
    circular_all = pd.concat(circular_parts, ignore_index=True) if circular_parts else pd.DataFrame()
    circular_all.to_csv(outdir / "circular_shift_controls.csv", index=False)
    moving_all = (
        pd.concat(moving_block_parts, ignore_index=True)
        if moving_block_parts
        else pd.DataFrame()
    )
    moving_all.to_csv(outdir / "moving_block_controls.csv", index=False)

    class_rows: list[dict[str, Any]] = []
    expected_counts = contract["registry"]["expected_class_denominators"]
    minimum_class_fraction = float(
        contract["completeness"]["minimum_evaluable_fraction_per_class"]
    )
    all_classes_complete = True
    for class_name, registered_count in expected_counts.items():
        class_status = status.loc[status["class"] == class_name]
        class_metrics = metrics.loc[metrics["class"] == class_name] if len(metrics) else metrics
        evaluable_count = int(class_status["evaluable_for_holdout_scoring"].sum())
        required_count = int(math.ceil(int(registered_count) * minimum_class_fraction))
        if evaluable_count < required_count:
            all_classes_complete = False
        anchors = int(class_metrics["support_evaluable_anchors"].sum()) if len(class_metrics) else 0
        passes = int(class_metrics["primary_pass_rows"].sum()) if len(class_metrics) else 0
        row = {
            "class": class_name,
            "registered_intervals": int(registered_count),
            "evaluable_intervals": evaluable_count,
            "incomplete_multipoint_intervals": int(registered_count) - evaluable_count,
            "minimum_required_evaluable_intervals": required_count,
            "support_evaluable_anchors": anchors,
            "primary_pass_rows": passes,
            "primary_clustering_event_rate": passes / anchors if anchors else np.nan,
        }
        for mission in ("dscovr", "ace", "wind"):
            denominator = int(class_metrics[f"{mission}_evaluable_rows"].sum()) if len(class_metrics) else 0
            numerator = int(class_metrics[f"{mission}_gate_rows"].sum()) if len(class_metrics) else 0
            row[f"{mission}_gate_fraction"] = numerator / denominator if denominator else np.nan
        for radius in contract["detector"]["timing_radii_minutes"]:
            field = f"joint_support_fraction_within_{int(radius)}_minutes"
            if len(class_metrics):
                values = pd.to_numeric(class_metrics[field], errors="coerce")
                weights = pd.to_numeric(
                    class_metrics["support_evaluable_anchors"], errors="coerce"
                )
                valid = values.notna() & weights.notna() & weights.gt(0)
                weighted = (
                    float(np.average(values[valid], weights=weights[valid]))
                    if valid.any()
                    else np.nan
                )
            else:
                weighted = np.nan
            row[field] = weighted
        class_rows.append(row)
    class_summary = pd.DataFrame(class_rows)
    class_summary.to_csv(outdir / "class_summary.csv", index=False)

    aligned_anchors = int(metrics["support_evaluable_anchors"].sum()) if len(metrics) else 0
    aligned_passes = int(metrics["primary_pass_rows"].sum()) if len(metrics) else 0
    aligned_rate = aligned_passes / aligned_anchors if aligned_anchors else None

    circular_aggregate_rows: list[dict[str, Any]] = []
    if not circular_all.empty:
        for iteration, group in circular_all.groupby("iteration", sort=True):
            anchors = int(group["support_evaluable_anchors"].sum())
            passes = int(group["primary_clustering_pass_rows"].sum())
            circular_aggregate_rows.append(
                {
                    "iteration": int(iteration),
                    "support_evaluable_anchors": anchors,
                    "primary_pass_rows": passes,
                    "primary_clustering_event_rate": passes / anchors if anchors else np.nan,
                    "mean_no_wrap_primary_rate": pd.to_numeric(
                        group["primary_clustering_event_rate_no_wrap"], errors="coerce"
                    ).mean(),
                }
            )
    circular_aggregate = pd.DataFrame(circular_aggregate_rows)
    circular_aggregate.to_csv(outdir / "circular_shift_aggregate.csv", index=False)

    moving_aggregate_rows: list[dict[str, Any]] = []
    if not moving_all.empty:
        for iteration, group in moving_all.groupby("iteration", sort=True):
            anchors = int(group["eligible_anchor_rows"].sum())
            passes = int(group["primary_pass_rows"].sum())
            moving_aggregate_rows.append(
                {
                    "iteration": int(iteration),
                    "eligible_anchor_rows": anchors,
                    "primary_pass_rows": passes,
                    "primary_clustering_event_rate": passes / anchors if anchors else np.nan,
                }
            )
    moving_aggregate = pd.DataFrame(moving_aggregate_rows)
    moving_aggregate.to_csv(outdir / "moving_block_aggregate.csv", index=False)

    moving_by_class: dict[str, Any] = {}
    if not moving_all.empty:
        for class_name, group in moving_all.groupby("class", sort=True):
            finite = pd.to_numeric(group["primary_event_rate"], errors="coerce").dropna()
            moving_by_class[class_name] = {
                "finite_replicates": int(len(finite)),
                "q025": float(finite.quantile(0.025)) if len(finite) else None,
                "median": float(finite.median()) if len(finite) else None,
                "q975": float(finite.quantile(0.975)) if len(finite) else None,
            }

    candidate_records = [
        row
        for row in status_rows
        if row["interval_id"] in evaluable_ids
    ]
    mismatch_settings = contract["nulls"]["mismatched_registered_days"]
    triplets = deterministic_mismatched_triplets(
        candidates=candidate_records,
        iterations=int(mismatch_settings["iterations"]),
        seed=int(mismatch_settings["seed"]),
        minimum_date_separation_days=int(
            mismatch_settings["minimum_pairwise_date_separation_days"]
        ),
    )
    bundle_cache: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    mismatch_rows: list[dict[str, Any]] = []
    for iteration, (d_row, a_row, w_row) in enumerate(triplets):
        keys = (
            (d_row["interval_id"], "DSCOVR"),
            (a_row["interval_id"], "ACE"),
            (w_row["interval_id"], "WIND"),
        )
        for interval_id, mission in keys:
            key = (interval_id, mission)
            if key not in bundle_cache:
                bundle_cache[key], _ = _read_bundle(manifests[interval_id], mission)
        _, summary, _, _ = score_support(
            dscovr=bundle_cache[keys[0]],
            ace=bundle_cache[keys[1]],
            wind=bundle_cache[keys[2]],
            support_half_window_minutes=int(
                contract["completeness"]["support_half_window_minutes"]
            ),
            minimum_independent_window_coverage_fraction=float(
                contract["completeness"]["minimum_independent_window_coverage_fraction"]
            ),
            timing_radii_minutes=contract["detector"]["timing_radii_minutes"],
            nearest_primary_radius_minutes=int(
                contract["primary_clustering_hypothesis"][
                    "nearest_joint_support_radius_minutes_lte"
                ]
            ),
            strongest_primary_span_minutes=int(
                contract["primary_clustering_hypothesis"][
                    "strongest_three_spacecraft_span_minutes_lte"
                ]
            ),
        )
        mismatch_rows.append(
            {
                "iteration": iteration,
                "generator": "MISSION_ERA_MATCHED_REGISTERED_DATE_BLOCK_PERMUTATION",
                "dscovr_interval_id": d_row["interval_id"],
                "ace_interval_id": a_row["interval_id"],
                "wind_interval_id": w_row["interval_id"],
                "support_evaluable_anchors": summary["support_evaluable_anchors"],
                "primary_pass_rows": summary["primary_clustering_pass_rows"],
                "primary_clustering_event_rate": summary[
                    "primary_clustering_event_rate"
                ],
                "joint_support_fraction_within_2_minutes": summary[
                    "support_fractions"
                ]["2"],
                "joint_support_fraction_within_3_minutes": summary[
                    "support_fractions"
                ]["3"],
            }
        )
    mismatch = pd.DataFrame(mismatch_rows)
    mismatch.to_csv(outdir / "mismatched_registered_day_controls.csv", index=False)

    circular_summary = _null_summary(
        aligned_rate,
        circular_aggregate["primary_clustering_event_rate"]
        if len(circular_aggregate)
        else pd.Series(dtype=float),
    )
    mismatch_summary = _null_summary(
        aligned_rate,
        mismatch["primary_clustering_event_rate"]
        if len(mismatch)
        else pd.Series(dtype=float),
    )
    moving_summary = _null_summary(
        aligned_rate,
        moving_aggregate["primary_clustering_event_rate"]
        if len(moving_aggregate)
        else pd.Series(dtype=float),
    )
    wrap_diagnostic: dict[str, Any]
    if len(circular_all):
        wrapped = pd.to_numeric(
            circular_all["primary_clustering_event_rate"], errors="coerce"
        )
        no_wrap = pd.to_numeric(
            circular_all["primary_clustering_event_rate_no_wrap"], errors="coerce"
        )
        differences = (wrapped - no_wrap).abs().dropna()
        wrap_diagnostic = {
            "finite_comparisons": int(len(differences)),
            "median_absolute_rate_difference": float(differences.median()) if len(differences) else None,
            "q95_absolute_rate_difference": float(differences.quantile(0.95)) if len(differences) else None,
            "maximum_absolute_rate_difference": float(differences.max()) if len(differences) else None,
        }
    else:
        wrap_diagnostic = {"finite_comparisons": 0}

    result_state = (
        "V2_HOLDOUT_NULLS_MEASURED_GEOMETRY_CLOSED"
        if all_classes_complete and aligned_anchors
        else "V2_BACKGROUND_INCOMPLETE"
    )
    charts = build_aggregate_charts(
        class_summary=class_summary,
        circular=circular_aggregate,
        mismatch=mismatch,
        outdir=outdir / "charts",
    )
    capsule = {
        "capsule_id": "NVCPP-GANNON-V2-PROSPECTIVE-HOLDOUT-CAPSULE-v1",
        "consumer_version": CONSUMER_VERSION,
        "status": "COMPLETE",
        "result_state": result_state,
        "created_utc": utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "LOCAL"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "registry": {
            "path": contract["registry"]["path"],
            "expected_file_sha256": contract["registry"]["expected_file_sha256"],
            "expected_content_sha256": contract["registry"]["expected_content_sha256"],
            "registered_class_denominators": expected_counts,
            "registered_total": contract["registry"]["expected_total_intervals"],
        },
        "denominators": {
            "registered_by_class": expected_counts,
            "evaluable_by_class": {
                row["class"]: int(row["evaluable_intervals"])
                for row in class_rows
            },
            "incomplete_multipoint_by_class": {
                row["class"]: int(row["incomplete_multipoint_intervals"])
                for row in class_rows
            },
            "registered_total": int(sum(expected_counts.values())),
            "evaluable_total": int(status["evaluable_for_holdout_scoring"].sum()),
        },
        "detector": contract["detector"],
        "primary_clustering_hypothesis": contract[
            "primary_clustering_hypothesis"
        ],
        "aligned_holdout": {
            "support_evaluable_anchors": aligned_anchors,
            "primary_pass_rows": aligned_passes,
            "primary_clustering_event_rate": aligned_rate,
            "class_summary": class_rows,
        },
        "charts": charts,
        "hard_nulls": {
            "circular_shift": circular_summary,
            "circular_wrap_diagnostic": wrap_diagnostic,
            "mismatched_registered_days": mismatch_summary,
            "moving_block_uncertainty": moving_summary,
            "moving_block_uncertainty_by_class": moving_by_class,
        },
        "geometry_state": "CLOSED",
        "geometry_calculated": False,
        "propagation_calculated": False,
        "common_surface_claim_allowed": False,
        "physical_class_assigned": False,
        "interpretation_limits": contract["interpretation_limits"],
        "artifacts": [],
    }
    capsule_path = outdir / "gannon_holdout_v2_capsule.json"
    report_path = outdir / "GANNON_V2_HOLDOUT_REPORT.md"
    report = [
        "# Gannon V2 Prospective Holdout Capsule",
        "",
        f"Result state: **{result_state}**",
        "",
        "The committed registry was consumed without editing any date or class.",
        "Provider and completeness failures remain in the registered denominator",
        "as `INCOMPLETE_MULTIPOINT`. The detector and primary 2/3-minute clustering",
        "definition were not changed.",
        "",
        "## Denominators",
        "",
        "```text",
        class_summary[[
            "class",
            "registered_intervals",
            "evaluable_intervals",
            "incomplete_multipoint_intervals",
        ]].to_string(index=False),
        "```",
        "",
        "## Class-specific measurements",
        "",
        "```text",
        class_summary.to_string(index=False),
        "```",
        "",
        "## Hard-null comparisons",
        "",
        "```json",
        json.dumps(capsule["hard_nulls"], indent=2, sort_keys=True),
        "```",
        "",
        "These are empirical frequencies under the frozen control generators.",
        "They are not independent-minute probabilities and do not identify one",
        "moving surface or a physical mechanism.",
        "",
        "## Geometry boundary",
        "",
        "Geometry, MVA, propagation, common-surface, and physical-class analysis",
        "remain closed regardless of whether the null comparison is favorable.",
    ]
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    capsule["artifacts"] = [
        {
            "path": path.relative_to(outdir).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(outdir.rglob("*"))
        if path.is_file() and path != capsule_path
    ]
    write_json(capsule_path, capsule)
    return capsule


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="verify registry and emit matrix")
    plan.add_argument("--output", type=Path)

    score = subparsers.add_parser("score-interval", help="score one frozen interval")
    score.add_argument("--interval-id", required=True)
    score.add_argument("--outdir", type=Path, required=True)

    combine = subparsers.add_parser("aggregate", help="aggregate interval artifacts")
    combine.add_argument("--input-root", type=Path, required=True)
    combine.add_argument("--outdir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "plan":
        result = plan_matrix(contract_path=args.contract, output=args.output)
    elif args.command == "score-interval":
        result = score_interval(
            contract_path=args.contract,
            interval_id=args.interval_id,
            outdir=args.outdir,
        )
    elif args.command == "aggregate":
        result = aggregate(
            contract_path=args.contract,
            input_root=args.input_root,
            outdir=args.outdir,
        )
    else:  # pragma: no cover
        raise HoldoutConsumerError(f"unsupported command: {args.command}")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
