#!/usr/bin/env python3
"""Measure how prevalent the Gannon multipoint MAG gate is across a frozen day.

This module does not classify a discontinuity or estimate propagation. It asks
how often the already-declared magnetic gate fires on each spacecraft and how
often independently selected ACE and Wind gate minutes occur near each DSCOVR
anchor. All vector diagnostics require an exact preceding canonical minute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.download_dscovr_cdaweb import (
    canonicalize_one_minute as canonicalize_dscovr,
    download_cdaweb as download_dscovr,
    format_cdaweb_date,
)
from historical.gannon_multipoint_audit import (
    canonicalize_vector_minutes,
    fetch_hapi,
    parse_cdas_rows,
    request_cdas_text,
    resolve_dscovr_components,
)

AUDIT_VERSION = "1.0.0"


class GateDensityError(RuntimeError):
    """Raised when gate prevalence cannot be measured fail-closed."""


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


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "contract_id",
        "contract_version",
        "analysis_window",
        "gate",
        "sources",
        "interpretation_limits",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise GateDensityError(f"gate-density contract lacks keys: {missing}")

    window = contract["analysis_window"]
    start = pd.Timestamp(window["start_utc"])
    stop = pd.Timestamp(window["stop_utc"])
    candidate = pd.Timestamp(window["candidate_utc"])
    if start.tzinfo is None or stop.tzinfo is None or candidate.tzinfo is None:
        raise GateDensityError("contract times must include UTC offsets")
    start = start.tz_convert("UTC")
    stop = stop.tz_convert("UTC")
    candidate = candidate.tz_convert("UTC")
    if not start < candidate < stop:
        raise GateDensityError("require start < candidate < stop")
    if stop - start < pd.Timedelta(hours=23):
        raise GateDensityError("gate-density audit must span nearly a full day")

    gate = contract["gate"]
    if int(gate["canonical_cadence_seconds"]) != 60:
        raise GateDensityError("this contract requires exact one-minute cadence")
    if float(gate["rotation_threshold_degrees"]) <= 0:
        raise GateDensityError("rotation threshold must be positive")
    if float(gate["magnitude_change_threshold_fraction"]) <= 0:
        raise GateDensityError("magnitude-change threshold must be positive")
    if int(gate["support_half_window_minutes"]) <= 0:
        raise GateDensityError("support half-window must be positive")
    return contract


def add_exact_minute_diagnostics(
    frame: pd.DataFrame,
    *,
    rotation_threshold_degrees: float,
    magnitude_change_threshold_fraction: float,
) -> pd.DataFrame:
    """Calculate diagnostics only when the prior row is exactly 60 seconds old."""

    required = {"time", "bx_gse_nT", "by_gse_nT", "bz_gse_nT", "B_mag_nT"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise GateDensityError(f"canonical MAG table lacks columns: {missing}")

    output = frame.copy()
    output["time"] = pd.to_datetime(
        output["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if output["time"].isna().any():
        raise GateDensityError("canonical MAG table contains invalid timestamps")
    output.sort_values("time", inplace=True)
    output.reset_index(drop=True, inplace=True)
    if output["time"].duplicated().any():
        raise GateDensityError("canonical MAG table contains duplicate minutes")

    time_delta = output["time"].diff().dt.total_seconds()
    exact_previous = time_delta.eq(60.0)
    output["previous_offset_seconds"] = time_delta
    output["exact_previous_minute"] = exact_previous

    vectors = output[["bx_gse_nT", "by_gse_nT", "bz_gse_nT"]].to_numpy(
        dtype=float
    )
    magnitudes = output["B_mag_nT"].to_numpy(dtype=float)
    rotation = np.full(len(output), np.nan, dtype=float)
    magnitude_change = np.full(len(output), np.nan, dtype=float)

    if len(output) > 1:
        previous = vectors[:-1]
        current = vectors[1:]
        previous_norm = np.linalg.norm(previous, axis=1)
        current_norm = np.linalg.norm(current, axis=1)
        valid = (
            exact_previous.iloc[1:].to_numpy(dtype=bool)
            & np.isfinite(previous).all(axis=1)
            & np.isfinite(current).all(axis=1)
            & np.isfinite(previous_norm)
            & np.isfinite(current_norm)
            & (previous_norm > 0.0)
            & (current_norm > 0.0)
        )
        dot = np.einsum("ij,ij->i", previous[valid], current[valid])
        cosine = dot / (previous_norm[valid] * current_norm[valid])
        rotation_values = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
        indices = np.flatnonzero(valid) + 1
        rotation[indices] = rotation_values

        valid_magnitude = (
            exact_previous.iloc[1:].to_numpy(dtype=bool)
            & np.isfinite(magnitudes[:-1])
            & np.isfinite(magnitudes[1:])
            & (np.abs(magnitudes[:-1]) > 0.0)
        )
        magnitude_indices = np.flatnonzero(valid_magnitude) + 1
        magnitude_change[magnitude_indices] = (
            np.abs(
                magnitudes[1:][valid_magnitude]
                - magnitudes[:-1][valid_magnitude]
            )
            / np.abs(magnitudes[:-1][valid_magnitude])
        )

    output["rotation_from_exact_previous_minute_degrees"] = rotation
    output["magnitude_change_from_exact_previous_minute_fraction"] = (
        magnitude_change
    )
    output["gate_rotation_pass"] = (
        output["rotation_from_exact_previous_minute_degrees"]
        >= rotation_threshold_degrees
    )
    output["gate_magnitude_pass"] = (
        output["magnitude_change_from_exact_previous_minute_fraction"]
        >= magnitude_change_threshold_fraction
    )
    output["gate_pass"] = (
        output["exact_previous_minute"]
        & (output["gate_rotation_pass"] | output["gate_magnitude_pass"])
    )
    output["gate_score"] = np.maximum(
        output["rotation_from_exact_previous_minute_degrees"].fillna(0.0)
        / rotation_threshold_degrees,
        output[
            "magnitude_change_from_exact_previous_minute_fraction"
        ].fillna(0.0)
        / magnitude_change_threshold_fraction,
    )
    return output


def standardize_dscovr(
    canonical: pd.DataFrame,
    *,
    rotation_threshold_degrees: float,
    magnitude_change_threshold_fraction: float,
) -> pd.DataFrame:
    """Preserve DSCOVR's real native sample count while standardizing columns."""

    bx, by, bz = resolve_dscovr_components(list(canonical.columns))
    required = {
        "EPOCH",
        bx,
        by,
        bz,
        "native_sample_count",
        "native_coverage_fraction",
    }
    missing = sorted(required - set(canonical.columns))
    if missing:
        raise GateDensityError(
            f"DSCOVR canonical table lacks provenance columns: {missing}"
        )
    output = canonical[
        [
            "EPOCH",
            bx,
            by,
            bz,
            "native_sample_count",
            "native_coverage_fraction",
        ]
    ].copy()
    output.rename(
        columns={
            "EPOCH": "time",
            bx: "bx_gse_nT",
            by: "by_gse_nT",
            bz: "bz_gse_nT",
            "native_sample_count": "native_samples",
        },
        inplace=True,
    )
    output["B_mag_nT"] = np.sqrt(
        output["bx_gse_nT"].pow(2)
        + output["by_gse_nT"].pow(2)
        + output["bz_gse_nT"].pow(2)
    )
    output["source"] = "DSCOVR_H0_MAG"
    return add_exact_minute_diagnostics(
        output,
        rotation_threshold_degrees=rotation_threshold_degrees,
        magnitude_change_threshold_fraction=magnitude_change_threshold_fraction,
    )


def cluster_gate_events(events: pd.DataFrame) -> pd.DataFrame:
    """Assign contiguous one-minute gate runs without inventing a gap tolerance."""

    output = events.sort_values(["mission", "time"]).copy()
    if output.empty:
        output["gate_cluster_id"] = pd.Series(dtype="Int64")
        output["gate_cluster_rows"] = pd.Series(dtype="Int64")
        return output
    output["gate_cluster_id"] = 0
    for _, index in output.groupby("mission", sort=False).groups.items():
        mission_times = output.loc[index, "time"]
        starts = mission_times.diff().dt.total_seconds().ne(60.0)
        cluster = starts.cumsum().astype(int)
        output.loc[index, "gate_cluster_id"] = cluster.to_numpy()
    cluster_sizes = output.groupby(
        ["mission", "gate_cluster_id"], sort=False
    ).size()
    output["gate_cluster_rows"] = [
        int(cluster_sizes.loc[(mission, cluster_id)])
        for mission, cluster_id in zip(
            output["mission"], output["gate_cluster_id"]
        )
    ]
    output["gate_cluster_id"] = output["gate_cluster_id"].astype(int)
    output["gate_cluster_rows"] = output["gate_cluster_rows"].astype(int)
    return output


def summarize_mission(frame: pd.DataFrame, mission: str) -> dict[str, Any]:
    evaluable = frame["exact_previous_minute"]
    gates = frame["gate_pass"]
    gate_rows = frame.loc[gates]
    clustered = cluster_gate_events(
        gate_rows.assign(mission=mission)[["mission", "time"]]
    )
    return {
        "mission": mission,
        "canonical_rows": int(len(frame)),
        "evaluable_exact_previous_rows": int(evaluable.sum()),
        "gate_rows": int(gates.sum()),
        "gate_fraction_of_evaluable": (
            float(gates.sum() / evaluable.sum()) if evaluable.sum() else None
        ),
        "rotation_gate_rows": int(frame["gate_rotation_pass"].sum()),
        "magnitude_gate_rows": int(frame["gate_magnitude_pass"].sum()),
        "both_gate_rows": int(
            (frame["gate_rotation_pass"] & frame["gate_magnitude_pass"]).sum()
        ),
        "contiguous_gate_runs": int(
            clustered["gate_cluster_id"].nunique() if len(clustered) else 0
        ),
        "longest_contiguous_gate_run_minutes": int(
            clustered["gate_cluster_rows"].max() if len(clustered) else 0
        ),
        "minimum_native_samples": (
            int(frame["native_samples"].min())
            if "native_samples" in frame and len(frame)
            else None
        ),
        "maximum_gap_seconds": (
            float(frame["previous_offset_seconds"].dropna().max())
            if frame["previous_offset_seconds"].notna().any()
            else None
        ),
    }


def gate_choice(
    gates: pd.DataFrame,
    *,
    center: pd.Timestamp,
    half_window_minutes: int,
    mode: str,
) -> dict[str, Any] | None:
    lower = center - pd.Timedelta(minutes=half_window_minutes)
    upper = center + pd.Timedelta(minutes=half_window_minutes)
    candidates = gates.loc[
        gates["time"].between(lower, upper, inclusive="both")
    ].copy()
    if candidates.empty:
        return None
    candidates["offset_minutes"] = (
        candidates["time"] - center
    ).dt.total_seconds() / 60.0
    candidates["absolute_offset_minutes"] = candidates[
        "offset_minutes"
    ].abs()
    if mode == "nearest":
        selected = candidates.sort_values(
            ["absolute_offset_minutes", "time"],
            ascending=[True, True],
        ).iloc[0]
    elif mode == "strongest":
        selected = candidates.sort_values(
            ["gate_score", "absolute_offset_minutes", "time"],
            ascending=[False, True, True],
        ).iloc[0]
    else:
        raise GateDensityError(f"unsupported gate-choice mode: {mode}")
    return {
        "time_utc": selected["time"].isoformat(),
        "offset_minutes": float(selected["offset_minutes"]),
        "absolute_offset_minutes": float(selected["absolute_offset_minutes"]),
        "gate_score": float(selected["gate_score"]),
        "rotation_degrees": float(
            selected["rotation_from_exact_previous_minute_degrees"]
        ),
        "magnitude_change_fraction": float(
            selected[
                "magnitude_change_from_exact_previous_minute_fraction"
            ]
        ),
    }


def build_anchor_support(
    dscovr: pd.DataFrame,
    independent: dict[str, pd.DataFrame],
    *,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    half_window_minutes: int,
) -> pd.DataFrame:
    margin = pd.Timedelta(minutes=half_window_minutes)
    anchors = dscovr.loc[
        dscovr["exact_previous_minute"]
        & (dscovr["time"] >= start + margin)
        & (dscovr["time"] < stop - margin)
    ].copy()
    gate_tables = {
        mission: frame.loc[frame["gate_pass"]].copy()
        for mission, frame in independent.items()
    }
    rows: list[dict[str, Any]] = []
    for _, anchor in anchors.iterrows():
        center = anchor["time"]
        row: dict[str, Any] = {
            "anchor_time_utc": center.isoformat(),
            "dscovr_gate_pass": bool(anchor["gate_pass"]),
            "dscovr_gate_score": float(anchor["gate_score"]),
        }
        nearest_offsets: list[float] = []
        strongest_times: list[pd.Timestamp] = [center]
        for mission, gates in gate_tables.items():
            nearest = gate_choice(
                gates,
                center=center,
                half_window_minutes=half_window_minutes,
                mode="nearest",
            )
            strongest = gate_choice(
                gates,
                center=center,
                half_window_minutes=half_window_minutes,
                mode="strongest",
            )
            prefix = mission.lower()
            row[f"{prefix}_nearest_time_utc"] = (
                nearest["time_utc"] if nearest else None
            )
            row[f"{prefix}_nearest_offset_minutes"] = (
                nearest["offset_minutes"] if nearest else np.nan
            )
            row[f"{prefix}_nearest_gate_score"] = (
                nearest["gate_score"] if nearest else np.nan
            )
            row[f"{prefix}_strongest_time_utc"] = (
                strongest["time_utc"] if strongest else None
            )
            row[f"{prefix}_strongest_offset_minutes"] = (
                strongest["offset_minutes"] if strongest else np.nan
            )
            row[f"{prefix}_strongest_gate_score"] = (
                strongest["gate_score"] if strongest else np.nan
            )
            if nearest:
                nearest_offsets.append(abs(float(nearest["offset_minutes"])))
            if strongest:
                strongest_times.append(pd.Timestamp(strongest["time_utc"]))
        row["both_independent_support_within_window"] = (
            len(nearest_offsets) == len(independent)
        )
        row["nearest_joint_radius_minutes"] = (
            max(nearest_offsets) if len(nearest_offsets) == len(independent) else np.nan
        )
        row["strongest_three_spacecraft_span_minutes"] = (
            (max(strongest_times) - min(strongest_times)).total_seconds() / 60.0
            if len(strongest_times) == len(independent) + 1
            else np.nan
        )
        rows.append(row)
    return pd.DataFrame(rows)


def prevalence_summary(
    support: pd.DataFrame,
    *,
    candidate: pd.Timestamp,
) -> dict[str, Any]:
    if support.empty:
        raise GateDensityError("anchor support table is empty")
    support_times = pd.to_datetime(
        support["anchor_time_utc"], format="ISO8601", utc=True
    )
    selected = support.loc[support_times == candidate]
    if len(selected) != 1:
        raise GateDensityError("candidate minute is absent from support table")
    candidate_row = selected.iloc[0]

    def summarize_scope(scope: pd.DataFrame) -> dict[str, Any]:
        total = len(scope)
        joint = scope["both_independent_support_within_window"]
        result: dict[str, Any] = {
            "anchor_rows": int(total),
            "joint_support_rows_within_15_minutes": int(joint.sum()),
            "joint_support_fraction_within_15_minutes": (
                float(joint.mean()) if total else None
            ),
        }
        for radius in (1, 2, 3, 5, 10, 15):
            inside = scope["nearest_joint_radius_minutes"].le(radius)
            result[f"joint_support_rows_within_{radius}_minutes"] = int(
                inside.sum()
            )
            result[f"joint_support_fraction_within_{radius}_minutes"] = (
                float(inside.mean()) if total else None
            )
        return result

    all_anchors = summarize_scope(support)
    dscovr_gate_anchors = summarize_scope(
        support.loc[support["dscovr_gate_pass"]]
    )
    candidate_record = candidate_row.to_dict()
    candidate_record["anchor_time_utc"] = str(candidate_record["anchor_time_utc"])
    return {
        "candidate": candidate_record,
        "all_evaluable_dscovr_anchors": all_anchors,
        "dscovr_gate_anchors": dscovr_gate_anchors,
        "meaning": (
            "within-day prevalence, not an independent null probability or p-value"
        ),
    }


def hourly_gate_counts(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for mission, frame in frames.items():
        working = frame.copy()
        working["hour_utc"] = working["time"].dt.floor("h")
        for hour, group in working.groupby("hour_utc", sort=True):
            evaluable = int(group["exact_previous_minute"].sum())
            gate_rows = int(group["gate_pass"].sum())
            rows.append(
                {
                    "mission": mission,
                    "hour_utc": hour.isoformat(),
                    "canonical_rows": int(len(group)),
                    "evaluable_rows": evaluable,
                    "gate_rows": gate_rows,
                    "gate_fraction_of_evaluable": (
                        gate_rows / evaluable if evaluable else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_gate_timeline(
    gate_events: pd.DataFrame,
    *,
    candidate: pd.Timestamp,
    output: Path,
) -> None:
    mission_y = {"DSCOVR": 0, "ACE": 1, "WIND": 2}
    plt.figure(figsize=(12, 4.5))
    for mission, group in gate_events.groupby("mission", sort=False):
        plt.scatter(
            group["time"],
            [mission_y[mission]] * len(group),
            label=mission,
            s=18,
        )
    plt.axvline(candidate, linestyle="--", label="DSCOVR 10:59 candidate")
    plt.yticks(list(mission_y.values()), list(mission_y.keys()))
    plt.xlabel("UTC")
    plt.ylabel("Spacecraft")
    plt.title("Gannon 2024 Full-Day Magnetic Gate Occurrence")
    plt.legend()
    plt.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output, dpi=180)
    plt.close()


def run_audit(*, contract_path: Path, outdir: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    window = contract["analysis_window"]
    start = pd.Timestamp(window["start_utc"]).tz_convert("UTC")
    stop = pd.Timestamp(window["stop_utc"]).tz_convert("UTC")
    candidate = pd.Timestamp(window["candidate_utc"]).tz_convert("UTC")
    gate = contract["gate"]
    rotation_threshold = float(gate["rotation_threshold_degrees"])
    magnitude_threshold = float(gate["magnitude_change_threshold_fraction"])
    half_window = int(gate["support_half_window_minutes"])

    outdir.mkdir(parents=True, exist_ok=True)
    raw_root = outdir / "raw"
    canonical_root = outdir / "canonical"
    quarantine_root = outdir / "quarantine"
    report_root = outdir / "reports"
    chart_root = outdir / "charts"
    for path in (raw_root, canonical_root, quarantine_root, report_root, chart_root):
        path.mkdir(parents=True, exist_ok=True)

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
        },
        "analysis_window": window,
        "gate": gate,
        "source_metadata": {},
        "interpretation_limits": contract["interpretation_limits"],
        "physical_mechanism_classified": False,
        "common_surface_test_completed": False,
        "ephemeris_propagation_test_completed": False,
    }
    manifest_path = outdir / "gannon_gate_density_manifest.json"
    write_json(manifest_path, manifest)

    try:
        session = requests.Session()
        session.headers.update(
            {"User-Agent": f"NVCPP-GANNON-GATE-DENSITY/{AUDIT_VERSION}"}
        )

        dscovr_raw_dir = raw_root / "DSCOVR_H0_MAG"
        dscovr_raw_dir.mkdir(parents=True, exist_ok=True)
        dscovr_raw, dscovr_source = download_dscovr(
            format_cdaweb_date(window["start_utc"]),
            format_cdaweb_date(window["stop_utc"]),
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
        manifest["source_metadata"]["DSCOVR"] = {
            "dataset_id": "DSCOVR_H0_MAG",
            "variables": ["B1GSE"],
            "coordinate_frame": "GSE",
            "canonicalization": dscovr_metrics,
            **{
                key: str(value) if isinstance(value, Path) else value
                for key, value in dscovr_source.items()
                if key != "raw_path"
            },
        }

        ace_raw, ace_source, _ = fetch_hapi(
            session,
            dataset_id="AC_H0_MFI",
            parameters=["Magnitude", "BGSEc", "SC_pos_GSE"],
            start=window["start_utc"],
            stop=window["stop_utc"],
            outdir=raw_root / "AC_H0_MFI",
        )
        ace, ace_quarantine = canonicalize_vector_minutes(
            ace_raw,
            components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
            position_components=(
                "SC_pos_GSE_x",
                "SC_pos_GSE_y",
                "SC_pos_GSE_z",
            ),
            minimum_samples=3,
            source="AC_H0_MFI",
        )
        ace = add_exact_minute_diagnostics(
            ace,
            rotation_threshold_degrees=rotation_threshold,
            magnitude_change_threshold_fraction=magnitude_threshold,
        )
        manifest["source_metadata"]["ACE"] = ace_source

        wind_raw, wind_source = request_cdas_text(
            session,
            dataset_id="WI_H0_MFI",
            variables=["B3GSE", "B3F1"],
            start=window["start_utc"],
            stop=window["stop_utc"],
            outdir=raw_root / "WI_H0_MFI",
        )
        wind_table = parse_cdas_rows(
            wind_raw,
            columns=[
                "time",
                "reported_B3F1_nT",
                "B3GSE_x",
                "B3GSE_y",
                "B3GSE_z",
            ],
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
        manifest["source_metadata"]["WIND"] = wind_source

        frames = {"DSCOVR": dscovr, "ACE": ace, "WIND": wind}
        for mission, frame in frames.items():
            frame.to_csv(
                canonical_root / f"{mission.lower()}_mag_gate_density.csv",
                index=False,
            )
        ace_quarantine.to_csv(
            quarantine_root / "ace_mag_quarantine.csv", index=False
        )
        wind_quarantine.to_csv(
            quarantine_root / "wind_mag_quarantine.csv", index=False
        )

        event_parts: list[pd.DataFrame] = []
        for mission, frame in frames.items():
            selected = frame.loc[frame["gate_pass"]].copy()
            selected["mission"] = mission
            event_parts.append(selected)
        gate_events = cluster_gate_events(
            pd.concat(event_parts, ignore_index=True, sort=False)
        )
        gate_events.to_csv(outdir / "gate_events.csv", index=False)

        mission_summary = pd.DataFrame(
            [summarize_mission(frame, mission) for mission, frame in frames.items()]
        )
        mission_summary.to_csv(outdir / "gate_density_by_spacecraft.csv", index=False)

        hourly = hourly_gate_counts(frames)
        hourly.to_csv(outdir / "hourly_gate_counts.csv", index=False)

        support = build_anchor_support(
            dscovr,
            {"ACE": ace, "WIND": wind},
            start=start,
            stop=stop,
            half_window_minutes=half_window,
        )
        support.to_csv(outdir / "dscovr_anchor_support.csv", index=False)
        prevalence = prevalence_summary(support, candidate=candidate)

        timeline_path = chart_root / "full_day_gate_timeline.png"
        build_gate_timeline(
            gate_events,
            candidate=candidate,
            output=timeline_path,
        )

        candidate_record = prevalence["candidate"]
        candidate_summary = pd.DataFrame([candidate_record])
        candidate_summary.to_csv(
            report_root / "candidate_support_summary.csv", index=False
        )

        report_path = report_root / "GANNON_GATE_DENSITY_AUDIT.md"
        report_lines = [
            "# Gannon 2024 Full-Day Magnetic Gate-Density Audit",
            "",
            "This audit measures how frequently the frozen 45-degree/25-percent",
            "MAG gate fires. It does not classify a discontinuity, estimate a lag,",
            "or assign statistical significance.",
            "",
            "## Per-spacecraft density",
            "",
            "```text",
            mission_summary.to_string(index=False),
            "```",
            "",
            "## Candidate timing",
            "",
            f"DSCOVR anchor: `{candidate.isoformat()}`",
            f"ACE nearest gate offset: `{candidate_record.get('ace_nearest_offset_minutes')}` minutes",
            f"Wind nearest gate offset: `{candidate_record.get('wind_nearest_offset_minutes')}` minutes",
            f"Nearest joint radius: `{candidate_record.get('nearest_joint_radius_minutes')}` minutes",
            f"Strongest three-spacecraft span: `{candidate_record.get('strongest_three_spacecraft_span_minutes')}` minutes",
            "",
            "## Within-day prevalence",
            "",
            "```json",
            json.dumps(prevalence, indent=2, default=str),
            "```",
            "",
            "The prevalence fractions are descriptive for this disturbed day. They",
            "are not a p-value and are not an independent quiet-time background.",
            "",
            "## Interpretation limits",
            "",
            *[f"- {item}" for item in contract["interpretation_limits"]],
        ]
        report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "mission_gate_density": mission_summary.to_dict(orient="records"),
                "support_prevalence": prevalence,
                "candidate_result_state": (
                    "WITHIN_DAY_GATE_PREVALENCE_MEASURED_"
                    "COMMON_SURFACE_UNRESOLVED"
                ),
            }
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
        default=Path("config/gannon_gate_density.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/gannon_gate_density"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_audit(contract_path=args.config, outdir=args.outdir)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "candidate_result_state": manifest.get("candidate_result_state"),
                "outdir": str(args.outdir),
            }
        )
    )


if __name__ == "__main__":
    main()
