#!/usr/bin/env python3
"""Audited DSCOVR_H0_MAG acquisition and canonical one-minute pipeline."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from core.cline_l1_chain_v1 import PROTOCOL_ID, PROTOCOL_VERSION, run_chain

DATASET_ID = "DSCOVR_H0_MAG"
VARIABLES = ["B1GSE"]
NATIVE_FILL_ABS_THRESHOLD = 1e30
EXPECTED_NATIVE_SAMPLES_PER_MINUTE = 60
MIN_NATIVE_SAMPLES_PER_MINUTE = 57
RUNNER_VERSION = "2.0.0"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def format_cdaweb_date(value: str) -> str:
    return pd.to_datetime(value, utc=True).strftime("%Y%m%dT%H%M%SZ")


def download_cdaweb(
    start: str,
    end: str,
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    variable_string = ",".join(VARIABLES)
    descriptor_url = (
        "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/"
        f"datasets/{DATASET_ID}/data/{start},{end}/{variable_string}?format=text"
    )
    descriptor_response = requests.get(
        descriptor_url,
        headers={"Accept": "application/json"},
        timeout=120,
    )
    descriptor_response.raise_for_status()
    descriptor_bytes = descriptor_response.content
    (outdir / f"{DATASET_ID}_descriptor.json").write_bytes(descriptor_bytes)
    descriptor = descriptor_response.json()

    descriptions = descriptor.get("FileDescription")
    if descriptions is None:
        descriptions = descriptor.get("DataResult", {}).get("FileDescription")
    if not descriptions:
        raise ValueError("CDAWeb descriptor did not contain FileDescription")
    file_url = descriptions[0].get("Name")
    if not file_url:
        raise ValueError("CDAWeb FileDescription has no download URL")

    data_response = requests.get(file_url, timeout=180)
    data_response.raise_for_status()
    raw = data_response.content
    raw_path = outdir / f"{DATASET_ID}_raw_bytes.csv"
    raw_path.write_bytes(raw)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CDAWeb response is not UTF-8") from exc
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError("CDAWeb response contains no parseable rows")

    frame = pd.read_csv(io.StringIO("\n".join(lines)), sep=r"\s{2,}", engine="python")
    if frame.empty:
        raise ValueError("CDAWeb table parsed as empty")

    return frame, {
        "descriptor_url": descriptor_url,
        "descriptor_resolved_url": descriptor_response.url,
        "descriptor_sha256": sha256_bytes(descriptor_bytes),
        "data_url": file_url,
        "data_resolved_url": data_response.url,
        "raw_path": raw_path,
        "raw_sha256": sha256_bytes(raw),
        "raw_size_bytes": len(raw),
    }


def _resolve_components(columns: list[str]) -> tuple[str, str, str]:
    def exact(axis: str) -> list[str]:
        return [
            column
            for column in columns
            if axis in column.upper()
            and "GSE" in column.upper()
            and "SPHR" not in column.upper()
        ]

    resolved = []
    for axis in ("BX", "BY", "BZ"):
        matches = exact(axis)
        if len(matches) != 1:
            raise ValueError(
                f"expected one {axis} GSE component, found {matches}; columns={columns}"
            )
        resolved.append(matches[0])
    return tuple(resolved)


def canonicalize_one_minute(
    raw: pd.DataFrame,
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if "EPOCH" not in raw.columns:
        raise ValueError(f"CDAWeb table has no EPOCH column: {list(raw.columns)}")
    bx, by, bz = _resolve_components(list(raw.columns))
    original = raw.copy()
    parsed = raw.copy()
    parsed["EPOCH"] = pd.to_datetime(
        parsed["EPOCH"],
        dayfirst=True,
        utc=True,
        errors="coerce",
    )
    for column in (bx, by, bz):
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    invalid_time = parsed["EPOCH"].isna()
    nonnumeric = parsed[[bx, by, bz]].isna().any(axis=1) & ~invalid_time
    fill = (parsed[[bx, by, bz]].abs() >= NATIVE_FILL_ABS_THRESHOLD).any(axis=1)
    fill &= ~(invalid_time | nonnumeric)
    zero = (parsed[[bx, by, bz]] == 0.0).all(axis=1)
    zero &= ~(invalid_time | nonnumeric | fill)

    quarantine_parts = []
    for mask, reason in (
        (invalid_time, "INVALID_TIMESTAMP"),
        (nonnumeric, "NONNUMERIC_VECTOR"),
        (fill, "CDAWEB_FILL_SENTINEL"),
        (zero, "ZERO_VECTOR_SUSPECT"),
    ):
        if mask.any():
            part = original.loc[mask].copy()
            part["reason_code"] = reason
            quarantine_parts.append(part)

    admitted = parsed.loc[
        ~(invalid_time | nonnumeric | fill | zero),
        ["EPOCH", bx, by, bz],
    ].copy()
    admitted.sort_values("EPOCH", inplace=True)
    duplicate_conflict = admitted["EPOCH"].duplicated().any()
    if duplicate_conflict:
        duplicate_rows = admitted.loc[admitted["EPOCH"].duplicated(keep=False)]
        part = original.loc[duplicate_rows.index].copy()
        part["reason_code"] = "DUPLICATE_TIMESTAMP"
        quarantine_parts.append(part)

    admitted["_minute"] = admitted["EPOCH"].dt.floor("min")
    grouped = admitted.groupby("_minute", sort=True)
    minute = grouped[[bx, by, bz]].mean()
    minute["native_sample_count"] = grouped.size()
    minute["native_coverage_fraction"] = (
        minute["native_sample_count"] / EXPECTED_NATIVE_SAMPLES_PER_MINUTE
    )
    low_coverage = minute["native_sample_count"] < MIN_NATIVE_SAMPLES_PER_MINUTE
    low_coverage_minutes = minute.loc[low_coverage].reset_index()
    if not low_coverage_minutes.empty:
        low_coverage_minutes["reason_code"] = "INSUFFICIENT_NATIVE_MINUTE_COVERAGE"
        quarantine_parts.append(low_coverage_minutes)
    minute = minute.loc[~low_coverage].copy()
    minute.index.name = "EPOCH"
    minute.reset_index(inplace=True)
    minute["B_mag"] = np.sqrt(minute[bx].pow(2) + minute[by].pow(2) + minute[bz].pow(2))

    quarantine_path = outdir / "dscovr_quarantine.csv"
    if quarantine_parts:
        quarantine = pd.concat(quarantine_parts, ignore_index=True, sort=False)
    else:
        quarantine = pd.DataFrame(columns=[*original.columns, "reason_code"])
    quarantine.to_csv(quarantine_path, index=False)

    if duplicate_conflict:
        raise ValueError("DSCOVR contains duplicate native timestamps")
    if minute.empty:
        raise ValueError("no canonical DSCOVR one-minute rows remain")
    cadence = minute["EPOCH"].diff().dt.total_seconds().dropna()
    metrics = {
        "raw_rows": int(len(raw)),
        "native_rows_admitted": int(len(admitted)),
        "canonical_minutes": int(len(minute)),
        "invalid_timestamps": int(invalid_time.sum()),
        "nonnumeric_vectors": int(nonnumeric.sum()),
        "fill_rows": int(fill.sum()),
        "zero_vector_suspects": int(zero.sum()),
        "low_coverage_minutes": int(low_coverage.sum()),
        "gap_intervals": int((cadence > 60).sum()),
        "longest_gap_seconds": float(cadence.max()) if len(cadence) else None,
        "quarantine_rows": int(len(quarantine)),
        "quarantine_sha256": sha256_file(quarantine_path),
    }
    return minute, metrics


def run_pipeline(
    run_name: str,
    start_iso: str,
    analysis_start: str,
    end_iso: str,
    outdir: Path,
) -> None:
    run_output = outdir / run_name
    run_output.mkdir(parents=True, exist_ok=True)
    manifest_path = run_output / "dscovr_run_manifest.json"
    manifest: dict[str, Any] = {
        "manifest_version": "2.0.0",
        "status": "STARTED",
        "run_name": run_name,
        "runner_version": RUNNER_VERSION,
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "retrieval_window": {"start": start_iso, "end": end_iso},
        "analysis_window": {"start": analysis_start, "end": end_iso},
        "protocol_id": PROTOCOL_ID,
        "protocol_version": PROTOCOL_VERSION,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        start = pd.to_datetime(start_iso, utc=True)
        analysis = pd.to_datetime(analysis_start, utc=True)
        end = pd.to_datetime(end_iso, utc=True)
        if not start < analysis < end:
            raise ValueError("require retrieval start < analysis start < retrieval end")
        if analysis - start < pd.Timedelta(hours=24):
            raise ValueError("DSCOVR retrieval must include at least 24 hours of pre-roll")

        raw, acquisition = download_cdaweb(
            format_cdaweb_date(start_iso),
            format_cdaweb_date(end_iso),
            run_output,
        )
        canonical, sanitation = canonicalize_one_minute(raw, run_output)
        processed = run_chain(
            canonical,
            time_col="EPOCH",
            b_mag_col="B_mag",
            expected_cadence_seconds=60,
            window_hours=24,
            min_coverage=0.95,
        )
        analysis_df = processed[
            (processed["EPOCH"] >= analysis) & (processed["EPOCH"] < end)
        ].copy()
        valid = analysis_df["baseline_status"] == "VALID"
        if not valid.any():
            raise ValueError("DSCOVR analysis interval has no valid baseline rows")

        output = run_output / "cline_l1_rows.csv"
        analysis_df.to_csv(output, index=False)
        valid_chi = analysis_df.loc[valid, "chi_B24M"]
        output_sha = sha256_file(output)
        quarantine_path = run_output / "dscovr_quarantine.csv"

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "dataset": DATASET_ID,
                    "variables": VARIABLES,
                    "coordinate_frame": "GSE",
                    **{key: value for key, value in acquisition.items() if key != "raw_path"},
                },
                "canonicalization": {
                    "method": "component-wise one-minute means, then vector magnitude",
                    "minimum_native_samples_per_minute": MIN_NATIVE_SAMPLES_PER_MINUTE,
                    **sanitation,
                },
                "baseline": {
                    "status_counts": {
                        str(key): int(value)
                        for key, value in analysis_df["baseline_status"].value_counts().items()
                    }
                },
                "metrics": {
                    "valid_rows": int(valid.sum()),
                    "max_chi_b24m": float(valid_chi.max()),
                    "median_chi_b24m": float(valid_chi.median()),
                    "clipping_applied": False,
                },
                "artifacts": [
                    {
                        "path": output.name,
                        "size_bytes": output.stat().st_size,
                        "sha256": output_sha,
                    },
                    {
                        "path": quarantine_path.name,
                        "size_bytes": quarantine_path.stat().st_size,
                        "sha256": sha256_file(quarantine_path),
                    },
                    {
                        "path": acquisition["raw_path"].name,
                        "size_bytes": acquisition["raw_path"].stat().st_size,
                        "sha256": acquisition["raw_sha256"],
                    },
                ],
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[NVCPP] DSCOVR SUCCESS: {output}")
    except BaseException as exc:
        manifest.update(
            {
                "status": "FAILED",
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Audited DSCOVR one-minute pipeline")
    parser.add_argument("--run", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--analysis-start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--outdir", default="runs/historical")
    args = parser.parse_args()
    run_pipeline(
        args.run,
        args.start,
        args.analysis_start,
        args.end,
        Path(args.outdir),
    )


if __name__ == "__main__":
    main()
