#!/usr/bin/env python3
"""Audited NOAA/NCEI SOLAR-1 MAG ingestion and CLINE L1 execution."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from core.cline_l1_chain_v1 import PROTOCOL_ID, PROTOCOL_VERSION, run_chain
from core.diagnostics import baseline_failure, source_boundary_diagnostics
from core.exceptions import (
    SourceDiagnosticError, SourceEndIncompleteError, SourcePrerollIncompleteError,
)
from sources.solar1.mission_phase import classify_solar1_interval
from sources.solar1.validate_contract import load_contract_or_raise

RUNNER_VERSION = "2.0.0"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return os.environ.get("GITHUB_SHA", "unknown")


def dependency_versions() -> dict[str, str]:
    result: dict[str, str] = {"python": platform.python_version()}
    for package in ("requests", "pandas", "numpy"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "not-installed"
    return result


def _identity_from_info(info: dict[str, Any], vector_parameter: str) -> dict[str, Any]:
    for record in info.get("additionalMetadata", []):
        content = record.get("content")
        if isinstance(content, dict) and isinstance(content.get(vector_parameter), dict):
            metadata = content[vector_parameter]
            return {
                key: metadata.get(key)
                for key in ("product", "instrument", "satellite")
            }
    return {"product": None, "instrument": None, "satellite": None}


def canonical_schema(info: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    required = contract["acquisition"]["explicit_parameters"]
    parameter_map = {
        parameter.get("name"): parameter
        for parameter in info.get("parameters", [])
        if isinstance(parameter, dict)
    }
    missing = [name for name in required if name not in parameter_map]
    if missing:
        raise ValueError(f"HAPI /info is missing required parameters: {missing}")

    parameters = []
    for name in required:
        source = parameter_map[name]
        parameters.append(
            {
                key: source.get(key)
                for key in ("name", "type", "units", "fill")
                if key in source
            }
        )
    return {
        "hapi_version": info.get("HAPI"),
        "parameters": parameters,
        "identity": _identity_from_info(info, required[1]),
    }


def schema_fingerprint(info: dict[str, Any], contract: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    canonical = canonical_schema(info, contract)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded), canonical


def request_hapi_info(
    session: requests.Session,
    contract: dict[str, Any],
    outdir: Path,
) -> dict[str, Any]:
    endpoint = f'{contract["source"]["api_base"]}/hapi/info'
    params = {"dataset": contract["source"]["hapi_dataset_id"]}
    response = session.get(endpoint, params=params, timeout=60)
    response.raise_for_status()
    raw = response.content
    path = outdir / "solar1_hapi_info.json"
    path.write_bytes(raw)
    try:
        info = response.json()
    except ValueError as exc:
        raise ValueError("HAPI /info did not return JSON") from exc

    if info.get("status", {}).get("code") != 1200:
        raise ValueError(f"HAPI /info status is not 1200: {info.get('status')}")

    observed, canonical = schema_fingerprint(info, contract)
    expected = contract["source"]["schema_fingerprint_sha256"]
    if observed != expected:
        raise ValueError(
            "HAPI schema fingerprint changed; "
            f"expected {expected}, observed {observed}"
        )

    return {
        "info": info,
        "path": path,
        "raw_sha256": sha256_bytes(raw),
        "canonical_schema": canonical,
        "canonical_schema_sha256": observed,
        "resolved_url": response.url,
    }


def request_hapi_csv(
    session: requests.Session,
    contract: dict[str, Any],
    start_iso: str,
    stop_iso: str,
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    endpoint = f'{contract["source"]["api_base"]}/hapi/data'
    parameters = contract["acquisition"]["explicit_parameters"]
    params = {
        "dataset": contract["source"]["hapi_dataset_id"],
        "start": start_iso,
        "stop": stop_iso,
        "parameters": ",".join(parameters),
        "format": contract["acquisition"]["response_format"],
    }
    response = session.get(endpoint, params=params, timeout=120)
    response.raise_for_status()
    raw = response.content
    raw_path = outdir / "solar1_mag_raw.csv"
    raw_path.write_bytes(raw)

    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    for source_row, row in enumerate(reader, start=1):
        if not row:
            continue
        if len(row) != len(parameters):
            raise ValueError(
                f"strict parse failure at source row {source_row}: "
                f"expected {len(parameters)} fields, found {len(row)}"
            )
        rows.append([str(source_row), *row])
    if not rows:
        raise ValueError("HAPI data response contains no rows")

    dataframe = pd.DataFrame(rows, columns=["_source_row", *parameters])
    return dataframe, {
        "path": raw_path,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "resolved_url": response.url,
        "requested_parameters": parameters,
        "content_type": response.headers.get("Content-Type"),
    }


def _append_quarantine(
    records: list[pd.DataFrame],
    source: pd.DataFrame,
    mask: pd.Series,
    reason: str,
) -> None:
    if bool(mask.any()):
        selected = source.loc[mask].copy()
        selected["reason_code"] = reason
        records.append(selected)


def sanitize(
    raw: pd.DataFrame,
    contract: dict[str, Any],
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    time_col = contract["time"]["parameter_id"]
    component_ids = [
        contract["vector"]["components"][axis]["parameter_id"]
        for axis in ("x", "y", "z")
    ]
    original = raw.copy()
    parsed = raw.copy()
    parsed[time_col] = pd.to_datetime(parsed[time_col], utc=True, errors="coerce")
    for column in component_ids:
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    quarantine: list[pd.DataFrame] = []
    invalid_time = parsed[time_col].isna()
    _append_quarantine(quarantine, original, invalid_time, "INVALID_TIMESTAMP")

    nonnumeric = pd.Series(False, index=parsed.index)
    for column in component_ids:
        nonnumeric |= parsed[column].isna()
    nonnumeric &= ~invalid_time
    _append_quarantine(quarantine, original, nonnumeric, "NONNUMERIC_VECTOR")

    fill_mask = pd.Series(False, index=parsed.index)
    for axis, column in zip(("x", "y", "z"), component_ids):
        fills = contract["vector"]["components"][axis]["fill_values"]
        fill_mask |= parsed[column].isin(fills)
    fill_mask &= ~(invalid_time | nonnumeric)
    _append_quarantine(quarantine, original, fill_mask, "PROVIDER_FILL")

    zero_mask = (parsed[component_ids] == 0.0).all(axis=1)
    zero_mask &= ~(invalid_time | nonnumeric | fill_mask)
    _append_quarantine(quarantine, original, zero_mask, "ZERO_VECTOR_SUSPECT")

    excluded = invalid_time | nonnumeric | fill_mask | zero_mask
    candidate = parsed.loc[~excluded].copy()
    candidate.sort_values(time_col, inplace=True)

    duplicate_any = candidate[time_col].duplicated(keep=False)
    duplicate_identical_count = 0
    duplicate_conflict_count = 0
    drop_indices: list[int] = []
    if duplicate_any.any():
        for _, group in candidate.loc[duplicate_any].groupby(time_col, sort=False):
            vectors = group[component_ids].drop_duplicates()
            if len(vectors) == 1:
                duplicate_identical_count += len(group) - 1
                duplicate_rows = group.iloc[1:]
                original_rows = original.loc[duplicate_rows.index].copy()
                original_rows["reason_code"] = "DUPLICATE_IDENTICAL"
                quarantine.append(original_rows)
                drop_indices.extend(duplicate_rows.index.tolist())
            else:
                duplicate_conflict_count += len(group)
                original_rows = original.loc[group.index].copy()
                original_rows["reason_code"] = "DUPLICATE_CONFLICT"
                quarantine.append(original_rows)

    if drop_indices:
        candidate.drop(index=drop_indices, inplace=True)

    quarantine_path = outdir / "solar1_quarantine.csv"
    if quarantine:
        quarantine_df = pd.concat(quarantine, ignore_index=True)
    else:
        quarantine_df = pd.DataFrame(columns=[*original.columns, "reason_code"])
    quarantine_df.to_csv(quarantine_path, index=False)
    quarantine_sha = sha256_file(quarantine_path)

    if duplicate_conflict_count:
        raise ValueError(
            f"{duplicate_conflict_count} rows have conflicting duplicate timestamps"
        )

    candidate.reset_index(drop=True, inplace=True)
    if candidate.empty:
        raise ValueError("no physical rows remain after quarantine")

    diffs = candidate[time_col].diff().dt.total_seconds().dropna()
    expected = float(contract["cadence"]["expected_seconds"])
    cadence_deviations = int((~np.isclose(diffs, expected)).sum())
    gap_mask = diffs > expected
    metrics = {
        "raw_rows": int(len(raw)),
        "valid_rows": int(len(candidate)),
        "invalid_timestamps": int(invalid_time.sum()),
        "nonnumeric_vectors": int(nonnumeric.sum()),
        "provider_fill_rows": int(fill_mask.sum()),
        "zero_vector_suspects": int(zero_mask.sum()),
        "duplicate_identical_quarantined": int(duplicate_identical_count),
        "duplicate_conflicts": int(duplicate_conflict_count),
        "cadence_deviation_intervals": cadence_deviations,
        "gap_intervals": int(gap_mask.sum()),
        "longest_gap_seconds": float(diffs.max()) if len(diffs) else None,
        "actual_start_utc": candidate[time_col].min().isoformat(),
        "actual_stop_utc": candidate[time_col].max().isoformat(),
        "quarantine_rows": int(len(quarantine_df)),
        "quarantine_path": quarantine_path.name,
        "quarantine_sha256": quarantine_sha,
    }
    return candidate, metrics


def _inventory(outdir: Path) -> list[dict[str, Any]]:
    items = []
    for path in sorted(outdir.iterdir()):
        if path.is_file() and path.name != "solar1_run_manifest.json":
            items.append(
                {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return items


def run_solar1_pipeline(
    run_name: str,
    start_time: str,
    analysis_start: str,
    end_time: str,
    outdir: Path,
    contract_path: Path,
) -> None:
    run_output = outdir / run_name
    run_output.mkdir(parents=True, exist_ok=True)
    manifest_path = run_output / "solar1_run_manifest.json"

    manifest: dict[str, Any] = {
        "manifest_version": "2.0.0",
        "status": "STARTED",
        "run_name": run_name,
        "runner_version": RUNNER_VERSION,
        "started_utc": utc_now(),
        "git_commit": git_commit(),
        "github": {
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "ref": os.environ.get("GITHUB_REF"),
        },
        "dependencies": dependency_versions(),
        "contract_path": str(contract_path),
        "retrieval_window": {"start": start_time, "end": end_time},
        "analysis_window": {"start": analysis_start, "end": end_time},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    try:
        contract = load_contract_or_raise(contract_path)
        manifest["contract_sha256"] = sha256_file(contract_path)
        manifest["protocol_id"] = PROTOCOL_ID
        manifest["protocol_version"] = PROTOCOL_VERSION

        start = pd.to_datetime(start_time, utc=True)
        analysis = pd.to_datetime(analysis_start, utc=True)
        end = pd.to_datetime(end_time, utc=True)
        phase = classify_solar1_interval(analysis, end, contract)
        manifest["mission_phase"] = phase
        pre_roll = pd.Timedelta(hours=contract["physics"]["pre_roll_hours"])
        if not start < analysis < end:
            raise ValueError("require retrieval start < analysis start < retrieval end")
        if analysis - start < pre_roll:
            raise ValueError("retrieval window does not contain the required 24-hour pre-roll")

        session = requests.Session()
        session.headers.update({"User-Agent": f"NVCPP-SOLAR1/{RUNNER_VERSION}"})
        info_meta = request_hapi_info(session, contract, run_output)
        manifest["provider_availability"] = {
            "start": info_meta["info"].get("startDate"),
            "stop": info_meta["info"].get("stopDate"),
        }
        raw, acquisition = request_hapi_csv(
            session, contract, start_time, end_time, run_output
        )
        clean, sanitation = sanitize(raw, contract, run_output)
        manifest["sanitation"] = sanitation

        first_time = clean[contract["time"]["parameter_id"]].min()
        last_time = clean[contract["time"]["parameter_id"]].max()
        tolerance = pd.Timedelta(seconds=contract["cadence"]["expected_seconds"])
        boundaries = source_boundary_diagnostics(
            raw[contract["time"]["parameter_id"]],
            clean[contract["time"]["parameter_id"]],
            requested_start=start, requested_end=end,
            cadence_seconds=float(contract["cadence"]["expected_seconds"]),
            quarantined_rows=sanitation["quarantine_rows"],
            provider_info=info_meta["info"],
        )
        manifest["source_boundaries"] = boundaries
        if first_time > start + tolerance:
            raise SourcePrerollIncompleteError(**boundaries)
        if last_time < end - tolerance:
            raise SourceEndIncompleteError(**boundaries)

        components = [
            contract["vector"]["components"][axis]["parameter_id"]
            for axis in ("x", "y", "z")
        ]
        clean["B_mag"] = np.sqrt(sum(clean[column].pow(2) for column in components))
        if not np.isfinite(clean["B_mag"].to_numpy(dtype=float)).all():
            raise ValueError("non-finite B_mag generated")

        processed = run_chain(
            clean,
            time_col=contract["time"]["parameter_id"],
            b_mag_col="B_mag",
            expected_cadence_seconds=float(contract["cadence"]["expected_seconds"]),
            window_hours=contract["physics"]["pre_roll_hours"],
            min_coverage=contract["physics"]["minimum_baseline_coverage_fraction"],
        )

        time_col = contract["time"]["parameter_id"]
        analysis_df = processed[
            (processed[time_col] >= analysis) & (processed[time_col] < end)
        ].copy()
        valid = analysis_df["baseline_status"] == "VALID"
        if not valid.any():
            raise baseline_failure(
                processed, analysis_df, time_col=time_col,
                window_hours=contract["physics"]["pre_roll_hours"],
                min_coverage=contract["physics"]["minimum_baseline_coverage_fraction"],
                cadence_seconds=float(contract["cadence"]["expected_seconds"]),
            )

        output_csv = run_output / "solar1_cline_l1_rows.csv"
        analysis_df.to_csv(output_csv, index=False)

        valid_chi = analysis_df.loc[valid, "chi_B24M"]
        valid_ratio = analysis_df.loc[valid, "ratio_B24M"]
        report = run_output / "solar1_cline_l1_report.md"
        report.write_text(
            "\n".join(
                [
                    f"# NVCPP SOLAR-1 Run: {run_name}",
                    "",
                    f"- Dataset: `{contract['source']['product_id']}`",
                    f"- Protocol: `{PROTOCOL_ID}` version `{PROTOCOL_VERSION}`",
                    f"- Mission phase: `{phase['label']}`",
                    f"- Operational validation claim allowed: **{phase['operational_validation_claim_allowed']}**",
                    f"- Retrieval: {start_time} to {end_time}",
                    f"- Analysis: {analysis_start} to {end_time}",
                    f"- Raw rows: {len(raw):,}",
                    f"- Valid physical rows before baseline: {len(clean):,}",
                    f"- Quarantine rows: {sanitation['quarantine_rows']:,}",
                    f"- Valid canonical analysis rows: {int(valid.sum()):,}",
                    f"- Maximum ratio_B24M: {valid_ratio.max():.9g}",
                    f"- Maximum chi_B24M: {valid_chi.max():.9g}",
                    "- Clipping applied: **False**",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "source": {
                    "product_id": contract["source"]["product_id"],
                    "coordinate_frame": contract["vector"]["coordinate_frame"],
                    "units": contract["vector"]["units"],
                    "hapi_info_url": info_meta["resolved_url"],
                    "hapi_info_raw_sha256": info_meta["raw_sha256"],
                    "schema_fingerprint_sha256": info_meta[
                        "canonical_schema_sha256"
                    ],
                    "data_url": acquisition["resolved_url"],
                    "raw_sha256": acquisition["sha256"],
                    "raw_size_bytes": acquisition["size_bytes"],
                    "requested_parameters": acquisition["requested_parameters"],
                },
                "sanitation": sanitation,
                "baseline": {
                    "status_counts": {
                        str(key): int(value)
                        for key, value in analysis_df["baseline_status"]
                        .value_counts(dropna=False)
                        .items()
                    },
                    "valid_rows": int(valid.sum()),
                    "invalid_rows": int((~valid).sum()),
                },
                "metrics": {
                    "max_ratio_b24m": float(valid_ratio.max()),
                    "max_chi_b24m": float(valid_chi.max()),
                    "median_chi_b24m": float(valid_chi.median()),
                    "clipping_applied": False,
                },
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["artifacts"] = _inventory(run_output)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"[NVCPP] SUCCESS: {run_output}")

    except BaseException as exc:
        manifest.update(
            {
                "status": "FAILED",
                "completed_utc": utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "artifacts": _inventory(run_output),
            }
        )
        if isinstance(exc, SourceDiagnosticError):
            manifest.update(exc.as_dict())
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Audited SOLAR-1 MAG pipeline")
    parser.add_argument("--run", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--analysis-start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--outdir", default="runs/historical")
    parser.add_argument(
        "--contract",
        default="config/solar1_mag_contract.v1.json",
    )
    args = parser.parse_args()
    run_solar1_pipeline(
        args.run,
        args.start,
        args.analysis_start,
        args.end,
        Path(args.outdir),
        Path(args.contract),
    )


if __name__ == "__main__":
    main()
