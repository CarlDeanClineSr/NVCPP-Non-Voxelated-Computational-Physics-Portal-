#!/usr/bin/env python3
"""Fail-closed NOAA SWPC operational real-time solar-wind ingestion.

The endpoint is used to catch current solar-wind events and evaluate magnetic
and plasma state. It is deliberately labeled an operational L1 composite,
because the endpoint can represent whichever upstream spacecraft NOAA has
selected. It must not be used as independent mission proof unless the source
identity is separately resolved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from core.cline_l1_chain_v1 import PROTOCOL_ID, PROTOCOL_VERSION, run_chain

PIPELINE_VERSION = "1.0.0"
MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-7-day.json"
PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
MU0 = 4.0e-7 * np.pi
PROTON_MASS_KG = 1.67262192369e-27
BOLTZMANN_J_K = 1.380649e-23


class NoaaRealtimeError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": path.name, "size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}


def _download_json(session: requests.Session, url: str, path: Path) -> tuple[Any, dict[str, Any]]:
    response = session.get(url, timeout=90)
    response.raise_for_status()
    raw = response.content
    path.write_bytes(raw)
    try:
        payload = response.json()
    except ValueError as exc:
        raise NoaaRealtimeError(f"{url} did not return JSON") from exc
    return payload, {
        "url": url,
        "resolved_url": response.url,
        "content_type": response.headers.get("Content-Type"),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "size_bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "path": path.name,
    }


def _table(payload: Any, *, required: list[str], source_name: str) -> pd.DataFrame:
    if not isinstance(payload, list) or len(payload) < 2:
        raise NoaaRealtimeError(f"{source_name} payload is not a header-plus-rows array")
    header = payload[0]
    if not isinstance(header, list) or not all(isinstance(value, str) for value in header):
        raise NoaaRealtimeError(f"{source_name} header is invalid")
    if len(header) != len(set(header)):
        raise NoaaRealtimeError(f"{source_name} header contains duplicate names")
    missing = [name for name in required if name not in header]
    if missing:
        raise NoaaRealtimeError(f"{source_name} is missing required columns: {missing}")

    rows: list[list[Any]] = []
    for source_row, row in enumerate(payload[1:], start=2):
        if not isinstance(row, list) or len(row) != len(header):
            raise NoaaRealtimeError(
                f"{source_name} row {source_row} has "
                f"{len(row) if isinstance(row, list) else 'non-list'} fields; expected {len(header)}"
            )
        rows.append([source_row, *row])
    frame = pd.DataFrame(rows, columns=["_source_row", *header])
    if frame.empty:
        raise NoaaRealtimeError(f"{source_name} contains no data rows")
    return frame


def _append_quarantine(
    records: list[pd.DataFrame],
    original: pd.DataFrame,
    mask: pd.Series,
    reason: str,
    source: str,
) -> None:
    if bool(mask.any()):
        selected = original.loc[mask].copy()
        selected.insert(0, "source_product", source)
        selected["reason_code"] = reason
        records.append(selected)


def _deduplicate(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    value_columns: list[str],
    quarantine: list[pd.DataFrame],
    source: str,
) -> pd.DataFrame:
    drop_indices: list[int] = []
    conflicts: list[pd.DataFrame] = []
    duplicates = frame["time"].duplicated(keep=False)
    for _, group in frame.loc[duplicates].groupby("time", sort=False):
        unique = group[value_columns].drop_duplicates()
        if len(unique) == 1:
            extras = group.iloc[1:]
            selected = original.loc[extras.index].copy()
            selected.insert(0, "source_product", source)
            selected["reason_code"] = "DUPLICATE_IDENTICAL"
            quarantine.append(selected)
            drop_indices.extend(extras.index.tolist())
        else:
            selected = original.loc[group.index].copy()
            selected.insert(0, "source_product", source)
            selected["reason_code"] = "DUPLICATE_CONFLICT"
            conflicts.append(selected)
    if conflicts:
        quarantine.extend(conflicts)
        raise NoaaRealtimeError(f"{source} contains conflicting duplicate timestamps")
    return frame.drop(index=drop_indices) if drop_indices else frame


def _sanitize_magnetic(raw: pd.DataFrame, quarantine: list[pd.DataFrame]) -> pd.DataFrame:
    original = raw.copy()
    parsed = raw.copy()
    parsed["time"] = pd.to_datetime(parsed["time_tag"], utc=True, errors="coerce")
    for column in ("bx_gsm", "by_gsm", "bz_gsm", "bt"):
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    invalid_time = parsed["time"].isna()
    nonnumeric = parsed[["bx_gsm", "by_gsm", "bz_gsm", "bt"]].isna().any(axis=1)
    nonnumeric &= ~invalid_time
    zero = (parsed[["bx_gsm", "by_gsm", "bz_gsm"]] == 0.0).all(axis=1)
    zero &= ~(invalid_time | nonnumeric)

    vector_magnitude = np.sqrt(
        parsed["bx_gsm"].pow(2) + parsed["by_gsm"].pow(2) + parsed["bz_gsm"].pow(2)
    )
    tolerance = np.maximum(0.05, 0.01 * vector_magnitude.abs())
    mismatch = (parsed["bt"] - vector_magnitude).abs() > tolerance
    mismatch &= ~(invalid_time | nonnumeric | zero)

    _append_quarantine(quarantine, original, invalid_time, "INVALID_TIMESTAMP", "NOAA_SWPC_MAG_7_DAY")
    _append_quarantine(quarantine, original, nonnumeric, "NONNUMERIC_VECTOR", "NOAA_SWPC_MAG_7_DAY")
    _append_quarantine(quarantine, original, zero, "ZERO_VECTOR_SUSPECT", "NOAA_SWPC_MAG_7_DAY")
    _append_quarantine(quarantine, original, mismatch, "BT_VECTOR_MISMATCH", "NOAA_SWPC_MAG_7_DAY")

    admitted = parsed.loc[~(invalid_time | nonnumeric | zero | mismatch)].copy()
    admitted["B_mag"] = vector_magnitude.loc[admitted.index]
    admitted = _deduplicate(
        admitted,
        original,
        ["bx_gsm", "by_gsm", "bz_gsm", "bt"],
        quarantine,
        "NOAA_SWPC_MAG_7_DAY",
    )
    admitted.sort_values("time", inplace=True)
    if admitted.empty:
        raise NoaaRealtimeError("no NOAA SWPC magnetic rows remain after quarantine")
    return admitted


def _sanitize_plasma(raw: pd.DataFrame, quarantine: list[pd.DataFrame]) -> pd.DataFrame:
    original = raw.copy()
    parsed = raw.copy()
    parsed["time"] = pd.to_datetime(parsed["time_tag"], utc=True, errors="coerce")
    for column in ("density", "speed", "temperature"):
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    invalid_time = parsed["time"].isna()
    nonnumeric = parsed[["density", "speed", "temperature"]].isna().any(axis=1)
    nonnumeric &= ~invalid_time
    nonphysical = (
        (parsed["density"] <= 0)
        | (parsed["speed"] <= 0)
        | (parsed["temperature"] <= 0)
    )
    nonphysical &= ~(invalid_time | nonnumeric)

    _append_quarantine(quarantine, original, invalid_time, "INVALID_TIMESTAMP", "NOAA_SWPC_PLASMA_7_DAY")
    _append_quarantine(quarantine, original, nonnumeric, "NONNUMERIC_PLASMA", "NOAA_SWPC_PLASMA_7_DAY")
    _append_quarantine(quarantine, original, nonphysical, "NONPOSITIVE_PLASMA", "NOAA_SWPC_PLASMA_7_DAY")

    admitted = parsed.loc[~(invalid_time | nonnumeric | nonphysical)].copy()
    admitted = _deduplicate(
        admitted,
        original,
        ["density", "speed", "temperature"],
        quarantine,
        "NOAA_SWPC_PLASMA_7_DAY",
    )
    admitted.sort_values("time", inplace=True)
    if admitted.empty:
        raise NoaaRealtimeError("no NOAA SWPC plasma rows remain after quarantine")
    return admitted


def _add_plasma_physics(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    density_m3 = result["density"] * 1.0e6
    speed_ms = result["speed"] * 1.0e3
    magnetic_t = result["B_mag"] * 1.0e-9

    result["dynamic_pressure_nPa"] = density_m3 * PROTON_MASS_KG * speed_ms.pow(2) * 1.0e9
    result["alfven_speed_km_s"] = (
        magnetic_t / np.sqrt(MU0 * PROTON_MASS_KG * density_m3)
    ) / 1.0e3
    result["alfven_mach"] = result["speed"] / result["alfven_speed_km_s"]
    result["proton_beta"] = (
        2.0 * MU0 * density_m3 * BOLTZMANN_J_K * result["temperature"]
    ) / magnetic_t.pow(2)

    finite_columns = [
        "dynamic_pressure_nPa",
        "alfven_speed_km_s",
        "alfven_mach",
        "proton_beta",
    ]
    for column in finite_columns:
        result.loc[~np.isfinite(result[column]), column] = np.nan
    return result


def run_noaa_realtime_pipeline(
    *,
    run_name: str,
    retrieval_start: str,
    analysis_start: str,
    analysis_end: str,
    outdir: Path,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    run_dir = outdir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "noaa_realtime_run_manifest.json"
    manifest: dict[str, Any] = {
        "manifest_version": "1.0.0",
        "pipeline_version": PIPELINE_VERSION,
        "status": "STARTED",
        "started_utc": _utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "runtime": {"python": platform.python_version()},
        "source": {
            "provider": "NOAA/SWPC",
            "product": "Real-Time Solar Wind 7-day operational feed",
            "coordinate_frame": "GSM",
            "spacecraft_identity": "PROVIDER_SELECTED_ACTIVE_UPSTREAM_SPACECRAFT",
            "mission_specific": False,
            "interpretation_limit": (
                "This endpoint is operational L1 context and is not independent "
                "mission proof unless spacecraft identity is separately resolved."
            ),
        },
        "protocol_id": PROTOCOL_ID,
        "protocol_version": PROTOCOL_VERSION,
        "retrieval_window": {"start": retrieval_start, "end": analysis_end},
        "analysis_window": {"start": analysis_start, "end": analysis_end},
        "artifacts": [],
    }
    _write_json(manifest_path, manifest)

    try:
        session = session or requests.Session()
        session.headers.update({"User-Agent": f"NVCPP-NOAA-RT/{PIPELINE_VERSION}"})
        mag_payload, mag_meta = _download_json(session, MAG_URL, run_dir / "mag_7_day_raw.json")
        plasma_payload, plasma_meta = _download_json(
            session, PLASMA_URL, run_dir / "plasma_7_day_raw.json"
        )
        mag_raw = _table(
            mag_payload,
            required=["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
            source_name="NOAA SWPC magnetic",
        )
        plasma_raw = _table(
            plasma_payload,
            required=["time_tag", "density", "speed", "temperature"],
            source_name="NOAA SWPC plasma",
        )

        quarantine_records: list[pd.DataFrame] = []
        mag = _sanitize_magnetic(mag_raw, quarantine_records)
        plasma = _sanitize_plasma(plasma_raw, quarantine_records)
        start = pd.Timestamp(retrieval_start)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        analysis_start_ts = pd.Timestamp(analysis_start)
        analysis_start_ts = (
            analysis_start_ts.tz_localize("UTC")
            if analysis_start_ts.tzinfo is None
            else analysis_start_ts.tz_convert("UTC")
        )
        end = pd.Timestamp(analysis_end)
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")

        mag = mag.loc[(mag["time"] >= start) & (mag["time"] < end)].copy()
        plasma = plasma.loc[(plasma["time"] >= start) & (plasma["time"] < end)].copy()
        if mag.empty:
            raise NoaaRealtimeError("NOAA SWPC magnetic data do not overlap the requested window")

        processed = run_chain(
            mag,
            time_col="time",
            b_mag_col="B_mag",
            expected_cadence_seconds=60.0,
        )
        canonical = processed.merge(
            plasma[["time", "density", "speed", "temperature"]],
            on="time",
            how="left",
            validate="one_to_one",
        )
        canonical = _add_plasma_physics(canonical)
        analysis = canonical.loc[
            (canonical["time"] >= analysis_start_ts) & (canonical["time"] < end)
        ].copy()
        valid = analysis["chi_B24M"].notna()
        if not valid.any():
            raise NoaaRealtimeError("no baseline-valid NOAA real-time rows in analysis window")

        quarantine = (
            pd.concat(quarantine_records, ignore_index=True, sort=False)
            if quarantine_records
            else pd.DataFrame(columns=["source_product", "reason_code"])
        )
        quarantine_path = run_dir / "noaa_realtime_quarantine.csv"
        quarantine.to_csv(quarantine_path, index=False)
        canonical_path = run_dir / "noaa_realtime_canonical.csv"
        analysis.to_csv(canonical_path, index=False)

        latest_time = analysis.loc[valid, "time"].max()
        freshness_minutes = max(0.0, (end - latest_time).total_seconds() / 60.0)
        source_state = "CURRENT" if freshness_minutes <= 20 else "STALE"
        report_path = run_dir / "noaa_realtime_report.md"
        report_path.write_text(
            "\n".join(
                [
                    f"# NOAA SWPC Operational L1 Run: {run_name}",
                    "",
                    f"- Source state: **{source_state}**",
                    f"- Latest admitted time: `{latest_time.isoformat()}`",
                    f"- Freshness at analysis end: **{freshness_minutes:.1f} minutes**",
                    f"- Analysis rows: **{len(analysis):,}**",
                    f"- Baseline-valid rows: **{int(valid.sum()):,}**",
                    f"- Plasma-paired rows: **{int(analysis['density'].notna().sum()):,}**",
                    f"- Maximum chi_B24M: **{analysis['chi_B24M'].max():.9g}**",
                    f"- Minimum delta_B24M: **{analysis['delta_B24M'].min():.9g}**",
                    f"- Maximum delta_B24M: **{analysis['delta_B24M'].max():.9g}**",
                    f"- Quarantined source rows: **{len(quarantine):,}**",
                    "- Clipping applied: **False**",
                    "",
                    "This is an operational provider-selected L1 feed. It is not "
                    "treated as an independently identified DSCOVR or ACE record.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": _utc_now(),
                "source_state": source_state,
                "freshness_minutes": freshness_minutes,
                "downloads": {"magnetic": mag_meta, "plasma": plasma_meta},
                "sanitization": {
                    "quarantine_rows": int(len(quarantine)),
                    "reason_counts": {
                        str(key): int(value)
                        for key, value in quarantine.get("reason_code", pd.Series(dtype=str))
                        .value_counts()
                        .to_dict()
                        .items()
                    },
                },
                "analysis": {
                    "rows": int(len(analysis)),
                    "baseline_valid_rows": int(valid.sum()),
                    "plasma_paired_rows": int(analysis["density"].notna().sum()),
                    "max_chi_b24m": float(analysis["chi_B24M"].max()),
                    "min_delta_b24m": float(analysis["delta_B24M"].min()),
                    "max_delta_b24m": float(analysis["delta_B24M"].max()),
                },
                "artifacts": [
                    _artifact(run_dir / "mag_7_day_raw.json"),
                    _artifact(run_dir / "plasma_7_day_raw.json"),
                    _artifact(quarantine_path),
                    _artifact(canonical_path),
                    _artifact(report_path),
                ],
            }
        )
        _write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(
            {
                "status": "FAILED",
                "failed_utc": _utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        manifest["artifacts"] = [
            _artifact(path)
            for path in sorted(run_dir.iterdir())
            if path.is_file() and path != manifest_path
        ]
        _write_json(manifest_path, manifest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="NVCPP NOAA SWPC operational L1 runner")
    parser.add_argument("--run", required=True)
    parser.add_argument("--retrieval-start", required=True)
    parser.add_argument("--analysis-start", required=True)
    parser.add_argument("--analysis-end", required=True)
    parser.add_argument("--outdir", default="runs/hourly")
    args = parser.parse_args()
    try:
        run_noaa_realtime_pipeline(
            run_name=args.run,
            retrieval_start=args.retrieval_start,
            analysis_start=args.analysis_start,
            analysis_end=args.analysis_end,
            outdir=Path(args.outdir),
        )
    except (NoaaRealtimeError, requests.RequestException, ValueError) as exc:
        print(f"[NVCPP-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
