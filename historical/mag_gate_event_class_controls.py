#!/usr/bin/env python3
"""Run the frozen L1 magnetic gate on independently selected control days.

The interval selector is upstream of this module.  This module refuses to choose
or rank dates using DSCOVR, ACE, or Wind gate results.  It consumes frozen
selection evidence, applies the unchanged 45-degree/25-percent gate, preserves
source bytes and per-interval results, and leaves geometry and physical class
unresolved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.download_dscovr_cdaweb import (
    canonicalize_one_minute as canonicalize_dscovr,
    download_cdaweb as download_dscovr,
    format_cdaweb_date,
)
from historical.gannon_gate_density import (
    add_exact_minute_diagnostics,
    build_anchor_support,
    standardize_dscovr,
    summarize_mission,
)
from historical.gannon_multipoint_audit import (
    canonicalize_vector_minutes,
    fetch_hapi,
    parse_cdas_rows,
    request_cdas_text,
)

AUDIT_VERSION = "1.0.0"
CONTROL_CLASS_ORDER = (
    "LOW_ACTIVITY",
    "MODERATE_ACTIVITY",
    "ISOLATED_SHOCK",
    "MILD_OR_GLANCING_STRUCTURE",
    "GANNON_DEVELOPMENT_EVENT",
)
RADIUS_VALUES = (1, 2, 3, 5, 10, 15)


class EventClassControlError(RuntimeError):
    """Raised when event-class controls cannot be evaluated fail-closed."""


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


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "contract_id",
        "contract_version",
        "selection_evidence_root",
        "gate_contract",
        "frozen_gate",
        "required_control_classes",
        "source_contracts",
        "result_states",
        "interpretation_limits",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise EventClassControlError(f"control contract lacks keys: {missing}")

    gate = contract["frozen_gate"]
    expected = {
        "coordinate_frame": "GSE",
        "canonical_cadence_seconds": 60,
        "required_previous_offset_seconds": 60,
        "rotation_threshold_degrees": 45.0,
        "magnitude_change_threshold_fraction": 0.25,
        "logical_operator": "OR",
        "timing_radii_minutes": list(RADIUS_VALUES),
    }
    for key, value in expected.items():
        if gate.get(key) != value:
            raise EventClassControlError(
                f"frozen gate field {key!r} changed: {gate.get(key)!r} != {value!r}"
            )

    gate_contract_path = Path(contract["gate_contract"])
    gate_contract = json.loads(gate_contract_path.read_text(encoding="utf-8"))
    declared = gate_contract["gate"]
    cross_checks = {
        "canonical_cadence_seconds": 60,
        "required_previous_offset_seconds": 60,
        "rotation_threshold_degrees": 45.0,
        "magnitude_change_threshold_fraction": 0.25,
        "logical_operator": "OR",
        "support_half_window_minutes": 15,
    }
    for key, value in cross_checks.items():
        if declared.get(key) != value:
            raise EventClassControlError(
                f"gate contract {gate_contract_path} disagrees at {key!r}"
            )
    contract["_gate_contract_sha256"] = sha256_file(gate_contract_path)
    return contract


def normalize_class(value: Any) -> str | None:
    text = re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")
    if not text:
        return None
    if "GANNON" in text or "DEVELOPMENT_EVENT" in text:
        return "GANNON_DEVELOPMENT_EVENT"
    if "GLANC" in text or "MILD" in text:
        return "MILD_OR_GLANCING_STRUCTURE"
    if "ISOLATED" in text and "SHOCK" in text:
        return "ISOLATED_SHOCK"
    if "CLEAR" in text and "SHOCK" in text:
        return "ISOLATED_SHOCK"
    if text in {"SHOCK", "SHOCK_EVENT", "CLEAR_SHOCK_EVENT"}:
        return "ISOLATED_SHOCK"
    if "MODERATE" in text:
        return "MODERATE_ACTIVITY"
    if "LOW_ACTIVITY" in text or "QUIET" in text or text == "LOW":
        return "LOW_ACTIVITY"
    return None


def parse_utc(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def record_to_interval(record: dict[str, Any], source: str) -> dict[str, Any] | None:
    if record.get("selected") is False or record.get("admitted") is False:
        return None

    class_candidates = [
        record.get(key)
        for key in (
            "control_class",
            "selection_class",
            "activity_class",
            "interval_class",
            "event_class",
            "classification",
            "category",
            "label",
            "name",
            "id",
        )
    ]
    control_class = next(
        (normalized for value in class_candidates if (normalized := normalize_class(value))),
        None,
    )
    if control_class is None:
        control_class = normalize_class(json.dumps(record, default=str)[:2000])
    if control_class is None:
        return None

    start = next(
        (
            parse_utc(record.get(key))
            for key in (
                "start_utc",
                "analysis_start_utc",
                "start",
                "start_time",
                "day_start_utc",
            )
            if record.get(key) not in (None, "")
        ),
        None,
    )
    stop = next(
        (
            parse_utc(record.get(key))
            for key in (
                "stop_utc",
                "end_utc",
                "analysis_stop_utc",
                "stop",
                "end",
                "stop_time",
                "day_stop_utc",
            )
            if record.get(key) not in (None, "")
        ),
        None,
    )
    if start is None:
        date_value = next(
            (
                record.get(key)
                for key in (
                    "date",
                    "date_utc",
                    "selected_date",
                    "day",
                    "utc_day",
                    "start_date",
                )
                if record.get(key) not in (None, "")
            ),
            None,
        )
        start = parse_utc(date_value)
        if start is not None:
            start = start.floor("D")
    if start is None:
        return None
    if stop is None:
        stop = start.floor("D") + pd.Timedelta(days=1)

    start = start.floor("D")
    stop = stop.ceil("D")
    if stop - start != pd.Timedelta(days=1):
        # This calibration is deliberately one complete UTC day per interval.
        stop = start + pd.Timedelta(days=1)

    label = next(
        (
            str(record.get(key))
            for key in ("label", "name", "id", "event_id")
            if record.get(key) not in (None, "")
        ),
        f"{control_class}_{start.date().isoformat()}",
    )
    rank_value = next(
        (
            record.get(key)
            for key in ("selection_rank", "rank", "activity_rank")
            if record.get(key) is not None
        ),
        None,
    )
    return {
        "control_class": control_class,
        "label": label,
        "start_utc": start.isoformat(),
        "stop_utc": stop.isoformat(),
        "selection_rank": rank_value,
        "selection_source": source,
    }


def walk_records(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_records(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_records(item)


def discover_json_intervals(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    output: list[dict[str, Any]] = []
    for record in walk_records(value):
        interval = record_to_interval(record, path.as_posix())
        if interval is not None:
            output.append(interval)
    return output


def discover_csv_intervals(path: Path) -> list[dict[str, Any]]:
    frame = pd.read_csv(path)
    output: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        interval = record_to_interval(record, path.as_posix())
        if interval is not None:
            output.append(interval)
    return output


def discover_control_intervals(
    selection_root: Path,
    required: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not selection_root.is_dir():
        raise EventClassControlError(
            f"frozen selection evidence is absent: {selection_root}"
        )
    inventory_path = selection_root / "FROZEN_INVENTORY.json"
    if not inventory_path.is_file():
        raise EventClassControlError("selection evidence lacks FROZEN_INVENTORY.json")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("spacecraft_gate_outputs_used") is not False:
        raise EventClassControlError(
            "selection inventory does not explicitly exclude spacecraft gate outputs"
        )
    if inventory.get("status") != "FROZEN_BEFORE_SPACECRAFT_GATE_RETRIEVAL":
        raise EventClassControlError(
            f"unexpected selection inventory state: {inventory.get('status')!r}"
        )

    candidates: list[dict[str, Any]] = []
    for path in sorted(selection_root.rglob("*")):
        if not path.is_file() or path.name == "FROZEN_INVENTORY.json":
            continue
        try:
            if path.suffix.lower() == ".json":
                candidates.extend(discover_json_intervals(path))
            elif path.suffix.lower() == ".csv":
                candidates.extend(discover_csv_intervals(path))
        except Exception as exc:
            raise EventClassControlError(
                f"cannot parse selection evidence {path}: {exc}"
            ) from exc

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for interval in candidates:
        key = (interval["control_class"], interval["start_utc"])
        current = unique.get(key)
        if current is None:
            unique[key] = interval
            continue
        # Prefer an explicitly ranked record over a generic nested duplicate.
        if current.get("selection_rank") is None and interval.get("selection_rank") is not None:
            unique[key] = interval

    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for control_class in CONTROL_CLASS_ORDER:
        class_rows = [
            value for (kind, _), value in unique.items() if kind == control_class
        ]
        class_rows.sort(
            key=lambda row: (
                row.get("selection_rank") is None,
                row.get("selection_rank") if row.get("selection_rank") is not None else 10**9,
                row["start_utc"],
            )
        )
        needed = int(required.get(control_class, 0))
        if len(class_rows) < needed:
            raise EventClassControlError(
                f"selection evidence has {len(class_rows)} {control_class} intervals; require {needed}"
            )
        # Keep all explicitly selected records, capped at two per activity class
        # to bound workflow cost. Fixed event classes remain one interval each.
        limit = 2 if control_class in {"LOW_ACTIVITY", "MODERATE_ACTIVITY"} else 1
        chosen = class_rows[: max(needed, limit)]
        selected.extend(chosen)
        counts[control_class] = len(chosen)

    selected.sort(key=lambda row: (CONTROL_CLASS_ORDER.index(row["control_class"]), row["start_utc"]))
    provenance = {
        "inventory_path": inventory_path.as_posix(),
        "inventory_sha256": sha256_file(inventory_path),
        "selection_root": selection_root.as_posix(),
        "discovered_candidate_records": len(candidates),
        "deduplicated_class_day_records": len(unique),
        "selected_counts": counts,
        "spacecraft_gate_outputs_used": False,
    }
    return selected, provenance


def support_scope_metrics(scope: pd.DataFrame) -> dict[str, Any]:
    total = int(len(scope))
    result: dict[str, Any] = {
        "anchor_rows": total,
        "joint_support_rows_within_15_minutes": int(
            scope["both_independent_support_within_window"].sum()
        ) if total else 0,
    }
    result["joint_support_fraction_within_15_minutes"] = (
        result["joint_support_rows_within_15_minutes"] / total if total else None
    )
    for radius in RADIUS_VALUES:
        inside = scope["nearest_joint_radius_minutes"].le(radius) if total else pd.Series(dtype=bool)
        count = int(inside.sum())
        result[f"joint_support_rows_within_{radius}_minutes"] = count
        result[f"joint_support_fraction_within_{radius}_minutes"] = (
            count / total if total else None
        )
    finite_span = scope["strongest_three_spacecraft_span_minutes"].dropna()
    result["strongest_span_rows"] = int(len(finite_span))
    result["strongest_span_le_3_minutes_rows"] = int((finite_span <= 3.0).sum())
    result["strongest_span_le_3_minutes_fraction"] = (
        float((finite_span <= 3.0).mean()) if len(finite_span) else None
    )
    result["strongest_span_median_minutes"] = (
        float(finite_span.median()) if len(finite_span) else None
    )
    return result


def run_interval(
    *,
    interval: dict[str, Any],
    root: Path,
    gate: dict[str, Any],
    session: requests.Session,
) -> dict[str, Any]:
    label_slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", interval["label"]).strip("_")
    date_slug = pd.Timestamp(interval["start_utc"]).strftime("%Y%m%d")
    interval_id = f"{interval['control_class']}__{date_slug}__{label_slug[:80]}"
    interval_root = root / interval_id
    raw_root = interval_root / "raw"
    canonical_root = interval_root / "canonical"
    quarantine_root = interval_root / "quarantine"
    for path in (raw_root, canonical_root, quarantine_root):
        path.mkdir(parents=True, exist_ok=True)

    start = pd.Timestamp(interval["start_utc"]).tz_convert("UTC")
    stop = pd.Timestamp(interval["stop_utc"]).tz_convert("UTC")
    rotation_threshold = float(gate["rotation_threshold_degrees"])
    magnitude_threshold = float(gate["magnitude_change_threshold_fraction"])
    half_window = int(max(gate["timing_radii_minutes"]))

    source_metadata: dict[str, Any] = {}

    dscovr_raw_dir = raw_root / "DSCOVR_H0_MAG"
    dscovr_raw_dir.mkdir(parents=True, exist_ok=True)
    dscovr_raw, dscovr_source = download_dscovr(
        format_cdaweb_date(start.isoformat()),
        format_cdaweb_date(stop.isoformat()),
        dscovr_raw_dir,
    )
    dscovr_canonical, dscovr_metrics = canonicalize_dscovr(
        dscovr_raw,
        dscovr_raw_dir,
    )
    dscovr = standardize_dscovr(
        dscovr_canonical,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    source_metadata["DSCOVR"] = json_safe({
        **dscovr_source,
        "dataset_id": "DSCOVR_H0_MAG",
        "variables": ["B1GSE"],
        "canonicalization": dscovr_metrics,
    })

    ace_raw, ace_source, _ = fetch_hapi(
        session,
        dataset_id="AC_H0_MFI",
        parameters=["Magnitude", "BGSEc", "SC_pos_GSE"],
        start=start.isoformat(),
        stop=stop.isoformat(),
        outdir=raw_root / "AC_H0_MFI",
    )
    ace, ace_quarantine = canonicalize_vector_minutes(
        ace_raw,
        components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
        position_components=("SC_pos_GSE_x", "SC_pos_GSE_y", "SC_pos_GSE_z"),
        minimum_samples=3,
        source="AC_H0_MFI",
    )
    ace = add_exact_minute_diagnostics(
        ace,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    source_metadata["ACE"] = json_safe(ace_source)

    wind_raw, wind_source = request_cdas_text(
        session,
        dataset_id="WI_H0_MFI",
        variables=["B3GSE", "B3F1"],
        start=start.isoformat(),
        stop=stop.isoformat(),
        outdir=raw_root / "WI_H0_MFI",
    )
    wind_table = parse_cdas_rows(
        wind_raw,
        columns=["time", "reported_B3F1_nT", "B3GSE_x", "B3GSE_y", "B3GSE_z"],
    )
    wind, wind_quarantine = canonicalize_vector_minutes(
        wind_table,
        components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
        minimum_samples=18,
        source="WI_H0_MFI",
    )
    wind = add_exact_minute_diagnostics(
        wind,
        rotation_threshold_degrees=rotation_threshold,
        magnitude_change_threshold_fraction=magnitude_threshold,
    )
    source_metadata["WIND"] = json_safe(wind_source)

    frames = {"DSCOVR": dscovr, "ACE": ace, "WIND": wind}
    for mission, frame in frames.items():
        frame.to_csv(canonical_root / f"{mission.lower()}_mag_control.csv", index=False)
    ace_quarantine.to_csv(quarantine_root / "ace_mag_quarantine.csv", index=False)
    wind_quarantine.to_csv(quarantine_root / "wind_mag_quarantine.csv", index=False)

    mission_rows = [summarize_mission(frame, mission) for mission, frame in frames.items()]
    mission_table = pd.DataFrame(mission_rows)
    mission_table.to_csv(interval_root / "spacecraft_gate_density.csv", index=False)

    support = build_anchor_support(
        dscovr,
        {"ACE": ace, "WIND": wind},
        start=start,
        stop=stop,
        half_window_minutes=half_window,
    )
    support.to_csv(interval_root / "dscovr_anchor_support.csv", index=False)
    dscovr_gate_support = support.loc[support["dscovr_gate_pass"]].copy()
    support_metrics = {
        "all_evaluable_dscovr_anchors": support_scope_metrics(support),
        "dscovr_gate_anchors": support_scope_metrics(dscovr_gate_support),
    }

    interval_manifest = {
        "status": "SUCCESS",
        "interval_id": interval_id,
        "control_class": interval["control_class"],
        "label": interval["label"],
        "start_utc": start.isoformat(),
        "stop_utc": stop.isoformat(),
        "selection_source": interval["selection_source"],
        "selection_rank": interval.get("selection_rank"),
        "gate": gate,
        "mission_gate_density": mission_rows,
        "support_metrics": support_metrics,
        "source_metadata": source_metadata,
        "claims": {
            "physical_mechanism_classified": False,
            "common_surface_test_completed": False,
            "ephemeris_propagation_test_completed": False,
        },
    }
    manifest_path = interval_root / "interval_manifest.json"
    write_json(manifest_path, interval_manifest)
    artifacts = []
    for path in sorted(interval_root.rglob("*")):
        if path.is_file() and path != manifest_path:
            artifacts.append({
                "path": path.relative_to(interval_root).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            })
    interval_manifest["artifacts"] = artifacts
    write_json(manifest_path, interval_manifest)
    return interval_manifest


def flatten_interval_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    mission = {row["mission"]: row for row in manifest["mission_gate_density"]}
    support = manifest["support_metrics"]["dscovr_gate_anchors"]
    row: dict[str, Any] = {
        "interval_id": manifest["interval_id"],
        "control_class": manifest["control_class"],
        "label": manifest["label"],
        "start_utc": manifest["start_utc"],
        "stop_utc": manifest["stop_utc"],
        "dscovr_gate_fraction": mission["DSCOVR"]["gate_fraction_of_evaluable"],
        "ace_gate_fraction": mission["ACE"]["gate_fraction_of_evaluable"],
        "wind_gate_fraction": mission["WIND"]["gate_fraction_of_evaluable"],
        "dscovr_gate_anchor_rows": support["anchor_rows"],
        "strongest_span_le_3_minutes_fraction": support[
            "strongest_span_le_3_minutes_fraction"
        ],
        "strongest_span_median_minutes": support["strongest_span_median_minutes"],
    }
    for radius in RADIUS_VALUES:
        row[f"joint_support_fraction_within_{radius}_minutes"] = support[
            f"joint_support_fraction_within_{radius}_minutes"
        ]
    return row


def class_aggregate(interval_summary: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "dscovr_gate_fraction",
        "ace_gate_fraction",
        "wind_gate_fraction",
        *[f"joint_support_fraction_within_{radius}_minutes" for radius in RADIUS_VALUES],
        "strongest_span_le_3_minutes_fraction",
        "strongest_span_median_minutes",
    ]
    rows: list[dict[str, Any]] = []
    for control_class, group in interval_summary.groupby("control_class", sort=False):
        for metric in metrics:
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            rows.append({
                "control_class": control_class,
                "metric": metric,
                "interval_count": int(len(values)),
                "median": float(values.median()) if len(values) else np.nan,
                "minimum": float(values.min()) if len(values) else np.nan,
                "maximum": float(values.max()) if len(values) else np.nan,
            })
    return pd.DataFrame(rows)


def build_charts(interval_summary: pd.DataFrame, outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    labels = [f"{row.control_class}\n{str(row.start_utc)[:10]}" for row in interval_summary.itertuples()]
    x = np.arange(len(interval_summary))
    width = 0.25
    plt.figure(figsize=(max(11, len(labels) * 1.5), 5.5))
    plt.bar(x - width, interval_summary["dscovr_gate_fraction"], width, label="DSCOVR")
    plt.bar(x, interval_summary["ace_gate_fraction"], width, label="ACE")
    plt.bar(x + width, interval_summary["wind_gate_fraction"], width, label="Wind")
    plt.xticks(x, labels, rotation=35, ha="right")
    plt.ylabel("Gate fraction of exact-minute evaluable rows")
    plt.title("Frozen 45-degree/25-percent MAG Gate by Control Interval")
    plt.legend()
    plt.tight_layout()
    path = outdir / "event_class_spacecraft_gate_density.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)

    plt.figure(figsize=(max(11, len(labels) * 1.5), 5.5))
    for radius in (2, 3, 5, 15):
        plt.plot(
            x,
            interval_summary[f"joint_support_fraction_within_{radius}_minutes"],
            marker="o",
            label=f"<= {radius} min",
        )
    plt.xticks(x, labels, rotation=35, ha="right")
    plt.ylabel("Fraction of DSCOVR gate anchors with ACE+Wind support")
    plt.title("Three-Spacecraft Joint-Support Prevalence by Control Interval")
    plt.legend()
    plt.tight_layout()
    path = outdir / "event_class_joint_support.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)
    return paths


def run_controls(*, contract_path: Path, outdir: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    selection_root = Path(contract["selection_evidence_root"])
    intervals, selection_provenance = discover_control_intervals(
        selection_root,
        {key: int(value) for key, value in contract["required_control_classes"].items()},
    )

    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "mag_gate_event_class_controls_manifest.json"
    manifest: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
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
            "gate_contract_sha256": contract["_gate_contract_sha256"],
        },
        "selection_provenance": selection_provenance,
        "selected_intervals": intervals,
        "frozen_gate": contract["frozen_gate"],
        "interpretation_limits": contract["interpretation_limits"],
        "event_interpretation_reopened": False,
        "geometry_blocked": True,
        "physical_mechanism_classified": False,
    }
    write_json(manifest_path, manifest)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-MAG-EVENT-CLASS-CONTROLS/{AUDIT_VERSION}"}
    )
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    intervals_root = outdir / "intervals"

    for interval in intervals:
        try:
            result = run_interval(
                interval=interval,
                root=intervals_root,
                gate=contract["frozen_gate"],
                session=session,
            )
            successes.append(result)
        except Exception as exc:
            failures.append({
                "control_class": interval["control_class"],
                "label": interval["label"],
                "start_utc": interval["start_utc"],
                "stop_utc": interval["stop_utc"],
                "error": f"{type(exc).__name__}: {exc}",
            })

    summary_rows = [flatten_interval_summary(item) for item in successes]
    interval_summary = pd.DataFrame(summary_rows)
    interval_summary_path = outdir / "event_class_interval_summary.csv"
    interval_summary.to_csv(interval_summary_path, index=False)
    failures_path = outdir / "event_class_failures.json"
    write_json(failures_path, failures)

    required = {key: int(value) for key, value in contract["required_control_classes"].items()}
    successful_counts = {
        control_class: sum(item["control_class"] == control_class for item in successes)
        for control_class in CONTROL_CLASS_ORDER
    }
    complete = all(successful_counts.get(key, 0) >= value for key, value in required.items())

    if not interval_summary.empty:
        aggregate = class_aggregate(interval_summary)
    else:
        aggregate = pd.DataFrame(
            columns=["control_class", "metric", "interval_count", "median", "minimum", "maximum"]
        )
    aggregate_path = outdir / "event_class_aggregate.csv"
    aggregate.to_csv(aggregate_path, index=False)

    chart_paths = build_charts(interval_summary, outdir / "charts") if not interval_summary.empty else []
    result_states = [
        "EVENT_CLASS_CONTROL_DISTRIBUTIONS_MEASURED" if complete else "BACKGROUND_CALIBRATION_INCOMPLETE",
        "GEOMETRY_REMAINS_BLOCKED",
    ]

    report_path = outdir / "MAG_GATE_EVENT_CLASS_CONTROLS.md"
    report_lines = [
        "# Frozen MAG Gate Event-Class Controls",
        "",
        f"Status: **{'COMPLETE' if complete else 'INCOMPLETE'}**",
        "",
        "The 45-degree rotation / 25-percent magnitude-change gate and all timing",
        "radii are unchanged. Control dates were frozen from OMNI/event metadata",
        "before this module retrieved any DSCOVR, ACE, or Wind gate output.",
        "",
        "## Interval results",
        "",
        "```text",
        interval_summary.to_string(index=False) if not interval_summary.empty else "No successful intervals",
        "```",
        "",
        "## Failed intervals",
        "",
        "```json",
        json.dumps(failures, indent=2),
        "```",
        "",
        "## Result states",
        "",
        *[f"- `{state}`" for state in result_states],
        "",
        "These distributions do not reopen the Gannon 10:59 interpretation. Hard",
        "circular-shift/mismatched-day nulls remain separate, and geometry remains",
        "blocked until both control stages are complete.",
        "",
        "## Interpretation limits",
        "",
        *[f"- {item}" for item in contract["interpretation_limits"]],
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    manifest.update({
        "status": "SUCCESS" if complete else "PARTIAL",
        "completed_utc": utc_now(),
        "successful_interval_counts": successful_counts,
        "failed_intervals": failures,
        "result_states": result_states,
        "chart_paths": [str(path) for path in chart_paths],
        "hard_null_combination_completed": False,
        "common_surface_test_completed": False,
        "ephemeris_propagation_test_completed": False,
    })
    artifacts = []
    for path in sorted(outdir.rglob("*")):
        if path.is_file() and path != manifest_path:
            artifacts.append({
                "path": path.relative_to(outdir).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            })
    manifest["artifacts"] = artifacts
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/mag_gate_event_class_controls.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/mag_gate_event_class_controls"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_controls(contract_path=args.config, outdir=args.outdir)
    print(json.dumps({
        "status": result["status"],
        "result_states": result["result_states"],
        "successful_interval_counts": result["successful_interval_counts"],
        "failed_interval_count": len(result["failed_intervals"]),
        "outdir": str(args.outdir),
    }))


if __name__ == "__main__":
    main()
