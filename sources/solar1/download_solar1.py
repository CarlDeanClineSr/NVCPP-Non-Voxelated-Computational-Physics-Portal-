#!/usr/bin/env python3
"""
NVCPP SOLAR-1 Historical Downloader & Ingestion Engine
Enforces full semantic contract validation, strict raw row-level column checks,
forensic quarantine artifact generation, unclipped vector magnitude computation,
and comprehensive pre/post run manifests.
"""

import argparse
import sys
import json
import hashlib
import subprocess
import csv
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import numpy as np
import requests

try:
    from core.cline_l1_chain_v1 import run_chain, PROTOCOL_ID, PROTOCOL_VERSION
except ImportError:
    PROTOCOL_ID = "CLINE-L1-B24M-TRAIL-v1"
    PROTOCOL_VERSION = "1.0.0"
    def run_chain(df, time_col, b_mag_col):
        raise NotImplementedError("Core math engine required.")

try:
    from sources.solar1.validate_contract import validate_contract
except ImportError:
    def validate_contract(contract_path):
        print(f"[NVCPP-WARNING] Strict validator module not found; falling back to direct JSON check for {contract_path}")
        data = json.loads(Path(contract_path).read_text())
        if data.get("status") != "FROZEN_VERIFIED":
            raise SystemExit("[ERROR] Contract status is not FROZEN_VERIFIED.")

NCEI_API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())

def get_git_commit_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"

def validate_frozen_contract(contract_path: Path):
    print(f"[NVCPP-CONTRACT] Pre-validating contract at {contract_path}...")
    if not contract_path.exists():
        raise SystemExit(f"[ERROR] Frozen contract not found at {contract_path}. Failing closed.")
    
    try:
        validate_contract(str(contract_path))
    except Exception as e:
        raise SystemExit(f"[ERROR] Contract validation failed: {e}. Failing closed.")
    
    print("[NVCPP-CONTRACT] Full semantic contract pre-validation PASSED.")

def fetch_solar1_hapi_data(start_iso: str, stop_iso: str, out_dir: Path) -> pd.DataFrame:
    """
    Queries NOAA/NCEI HAPI data endpoint with strict row-level column count enforcement.
    Explicitly requests parameters: time, b_gse_min_x, b_gse_min_y, b_gse_min_z.
    """
    url = f"{NCEI_API_BASE}/hapi/data"
    params = {
        "dataset": "sci_mag-l3_solar1",
        "start": start_iso,
        "stop": stop_iso,
        "parameters": "time,b_gse_min_x,b_gse_min_y,b_gse_min_z",
        "format": "csv"
    }
    
    print(f"[NVCPP-SOLAR1] Requesting HAPI stream from {start_iso} to {stop_iso}...")
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    
    raw_bytes = response.content
    prov_hash = sha256_bytes(raw_bytes)
    print(f"[NVCPP-SOLAR1] Provenance secured: SHA256 {prov_hash}")
    
    raw_path = out_dir / "solar1_mag_raw.csv"
    raw_path.write_bytes(raw_bytes)
    
    # Strict row-level column count enforcement (fail-closed, no skipping bad lines)
    col_names = ["time", "b_gse_min_x", "b_gse_min_y", "b_gse_min_z"]
    expected_cols = len(col_names)
    
    rows = []
    with open(raw_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for line_num, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) != expected_cols:
                raise SystemExit(f"[ERROR] Strict parse error at line {line_num}: expected {expected_cols} columns, got {len(row)}. Failing closed.")
            rows.append(row)
            
    df = pd.DataFrame(rows, columns=col_names)
    return df

def run_solar1_pipeline(run_name: str, start_time: str, analysis_start: str, end_time: str, outdir: Path, contract_path: Path):
    print(f"[NVCPP] Executing SOLAR-1 MAG pipeline for run: {run_name}")
    run_output_dir = outdir / run_name
    run_output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Pre-validate authoritative contract
    validate_frozen_contract(contract_path)

    # 2. Fetch data with strict parsing
    raw_df = fetch_solar1_hapi_data(start_time, end_time, run_output_dir)
    raw_csv_path = run_output_dir / "solar1_mag_raw.csv"
    raw_sha = sha256_file(raw_csv_path)

    if raw_df.empty:
        raise SystemExit("[ERROR] Retrieved SOLAR-1 telemetry is empty. Failing closed.")

    print(f"[NVCPP] Successfully parsed {len(raw_df)} rows from HAPI stream.")

    # Parse timestamps
    print("[NVCPP] Converting time to UTC datetime...")
    raw_df["time"] = pd.to_datetime(raw_df["time"], errors="coerce", utc=True)
    invalid_time = int(raw_df["time"].isna().sum())

    bx, by, bz = "b_gse_min_x", "b_gse_min_y", "b_gse_min_z"
    print("[NVCPP] Converting magnetic vector components to numeric values...")
    for col in (bx, by, bz):
        raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")

    # 3. Classify and quarantine anomalies into separate tracking DataFrame
    quarantine_records = []

    # Fill value detection (-9999.0)
    fill_sentinel = -9999.0
    fill_mask = (raw_df[bx] <= fill_sentinel) | (raw_df[by] <= fill_sentinel) | (raw_df[bz] <= fill_sentinel)
    if fill_mask.any():
        fill_df = raw_df.loc[fill_mask].copy()
        fill_df["reason_code"] = "PROVIDER_FILL"
        quarantine_records.append(fill_df)
        raw_df.loc[fill_mask, [bx, by, bz]] = np.nan

    # Zero-vector anomaly detection (0.0 nT)
    zero_mask = (raw_df[bx] == 0.0) & (raw_df[by] == 0.0) & (raw_df[bz] == 0.0)
    if zero_mask.any():
        zero_df = raw_df.loc[zero_mask].copy()
        zero_df["reason_code"] = "ZERO_VECTOR_SUSPECT"
        quarantine_records.append(zero_df)
        raw_df.loc[zero_mask, [bx, by, bz]] = np.nan

    fill_count = int(fill_mask.sum())
    zero_count = int(zero_mask.sum())

    # Write standalone quarantine artifact if anomalies exist
    quarantine_path = run_output_dir / "solar1_quarantine.csv"
    if quarantine_records:
        q_combined = pd.concat(quarantine_records)
        q_combined.to_csv(quarantine_path, index=False)
        q_sha = sha256_file(quarantine_path)
        print(f"[NVCPP] Quarantined {len(q_combined)} anomalous record(s) to {quarantine_path}")
    else:
        q_sha = None

    valid_mask = raw_df["time"].notna() & raw_df[[bx, by, bz]].notna().all(axis=1)
    clean_df = raw_df.loc[valid_mask].copy()

    print(
        f"[NVCPP] Sanitization: {len(raw_df)} parsed rows; "
        f"{invalid_time} invalid timestamps; {fill_count} fill-value records (-9999.0); "
        f"{zero_count} zero-vector suspect records; "
        f"{len(clean_df)} valid physical rows."
    )

    if clean_df.empty:
        raise SystemExit("[ERROR] No valid timestamped SOLAR-1 magnetic vectors remain. Failing closed.")

    clean_df.sort_values("time", inplace=True)
    clean_df.reset_index(drop=True, inplace=True)

    # Calculate vector magnitude component-wise: B = sqrt(Bx^2 + By^2 + Bz^2)
    print("[NVCPP] Calculating vector magnitude component-wise...")
    clean_df["B_mag"] = np.sqrt(
        clean_df[bx].pow(2) + clean_df[by].pow(2) + clean_df[bz].pow(2)
    )

    if not np.isfinite(clean_df["B_mag"].to_numpy()).all():
        raise SystemExit("[ERROR] Non-finite B_mag generated from valid SOLAR-1 vectors. Failing closed.")

    print(f"\n[NVCPP] Phase 2: Unclipped Physical Computation ({PROTOCOL_ID})")
    processed_df = run_chain(clean_df, time_col="time", b_mag_col="B_mag")

    if "chi_B24M" not in processed_df.columns:
        raise SystemExit("[ERROR] chi_B24M was not produced. Failing closed.")

    # Slice output to isolate analysis window (dropping 24h pre-roll)
    analysis_df = processed_df[processed_df["time"] >= pd.to_datetime(analysis_start)].copy()
    valid_chi = analysis_df["chi_B24M"].dropna()

    if valid_chi.empty:
        raise SystemExit("[ERROR] No valid chi_B24M values produced in analysis window. Failing closed.")

    print("\n[NVCPP] Phase 3: Persisting Scientific Artifacts & Run Manifest")
    output_csv = run_output_dir / "solar1_cline_l1_rows.csv"
    analysis_df.to_csv(output_csv, index=False)
    processed_sha = sha256_file(output_csv)

    summary_file = run_output_dir / "solar1_cline_l1_report.md"
    max_chi = valid_chi.max()
    max_ratio = analysis_df["ratio_B24M"].max()

    summary_text = (
        f"# NVCPP SOLAR-1 Run: {run_name}\n\n"
        f"- **Dataset**: sci_mag-l3_solar1\n"
        f"- **Pre-Roll Retrieval**: {start_time} to {end_time}\n"
        f"- **Analysis Interval**: {analysis_start} to {end_time}\n"
        f"- **Rows retrieved/parsed**: {len(raw_df)}\n"
        f"- **Fill values (-9999.0) rejected**: {fill_count}\n"
        f"- **Zero-vector suspects quarantined**: {zero_count}\n"
        f"- **Analysis rows with valid chi_B24M**: {len(valid_chi)}\n"
        f"- **Max ratio_B24M (B/B0)**: {max_ratio:.6g}\n"
        f"- **Max chi_B24M (|B-B0|/|B0|)**: {max_chi:.6g}\n"
        f"- **Protocol ID**: {PROTOCOL_ID}\n"
        f"- **Baseline Method**: Prior-Only Trailing 24-Hour Median\n"
        f"- **Coverage Gate**: 95% minimum saturation required\n"
        f"- **Clipping applied**: False\n"
    )
    summary_file.write_text(summary_text)
    report_sha = sha256_file(summary_file)

    # 4. Comprehensive Run Manifest
    manifest = {
        "run_name": run_name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": get_git_commit_sha(),
        "protocol_id": PROTOCOL_ID,
        "protocol_version": PROTOCOL_VERSION,
        "contract_path": str(contract_path),
        "contract_sha256": sha256_file(contract_path),
        "retrieval_window": {
            "start": start_time,
            "end": end_time
        },
        "analysis_window": {
            "start": analysis_start,
            "end": end_time
        },
        "metrics": {
            "raw_rows_retrieved": len(raw_df),
            "invalid_timestamps": invalid_time,
            "fill_values_rejected": fill_count,
            "zero_vectors_quarantined": zero_count,
            "analysis_rows_valid": len(valid_chi),
            "max_ratio_b24m": float(max_ratio),
            "max_chi_b24m": float(max_chi)
        },
        "artifacts": {
            "raw_csv": {"path": raw_csv_path.name, "sha256": raw_sha},
            "processed_csv": {"path": output_csv.name, "sha256": processed_sha},
            "report_md": {"path": summary_file.name, "sha256": report_sha},
            "quarantine_csv": {"path": quarantine_path.name, "sha256": q_sha} if quarantine_records else None
        }
    }

    manifest_path = run_output_dir / "solar1_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[NVCPP] SUCCESS. Production-locked manifest and artifacts written to: {run_output_dir}")

def main():
    parser = argparse.ArgumentParser(description="NVCPP SOLAR-1 Historical Downloader")
    parser.add_argument("--run", default="solar1_sample_2025_2026", help="Run name identifier")
    parser.add_argument("--start", default="2026-06-01T00:00:00.000Z", help="Retrieval start with pre-roll")
    parser.add_argument("--analysis-start", default="2026-06-02T00:00:00.000Z", help="Analysis window start")
    parser.add_argument("--end", default="2026-06-05T00:00:00.000Z", help="Retrieval end")
    parser.add_argument("--outdir", default="runs/historical", help="Output directory")
    parser.add_argument("--contract", default="config/solar1_mag_contract.v1.json", help="Path to authoritative JSON contract")
    args = parser.parse_args()

    run_solar1_pipeline(
        run_name=args.run,
        start_time=args.start,
        analysis_start=args.analysis_start,
        end_time=args.end,
        outdir=Path(args.outdir),
        contract_path=Path(args.contract)
    )

if __name__ == "__main__":
    main()
