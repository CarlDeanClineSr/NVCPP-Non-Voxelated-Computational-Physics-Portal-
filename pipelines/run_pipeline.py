#!/usr/bin/env python3
"""
NVCPP Pipeline Command Router
Executes historical DSCOVR and observatory telemetry runs with strict source guards
and unclipped physical baseline preservation.
"""

import argparse
import sys
import yaml
from pathlib import Path
import pandas as pd
import numpy as np

try:
    from core.cline_l1_chain_v1 import run_chain
    from historical.download_dscovr_cdaweb import download_cdaweb_data
except ImportError as e:
    print(f"[ERROR] Failed to import NVCPP modules. {e}", file=sys.stderr)
    sys.exit(1)


def run_dscovr_historical(run_name: str, out_path: Path):
    print(f"[NVCPP] Executing unclipped DSCOVR baseline analysis for epoch: {run_name}")

    run_output_dir = out_path / run_name
    run_output_dir.mkdir(parents=True, exist_ok=True)

    config_file = Path("config/dscovr_h0_mag.yaml")
    if not config_file.exists():
        print(f"[ERROR] Missing config: {config_file}", file=sys.stderr)
        sys.exit(1)

    print(f"[NVCPP] Loaded configuration from {config_file}")

    if run_name == "gannon_may_2024_dscovr_mag_only":
        dataset_id = "DSCOVR_H0_MAG"
        # 24-HOUR PRE-ROLL: We pull from May 9th to saturate the 24h trailing baseline
        start_time = "20240509T000000Z"
        analysis_start = "2024-05-10T00:00:00Z"
        end_time = "20240513T000000Z"
        variables = ["B1GSE"]
    else:
        print(f"[ERROR] Unknown run target: {run_name}. Failing closed.", file=sys.stderr)
        sys.exit(1)

    print("\n[NVCPP] Phase 1: Telemetry Acquisition")
    raw_df = download_cdaweb_data(dataset_id, start_time, end_time, variables, run_output_dir)

    if raw_df.empty:
        raise SystemExit("[ERROR] Retrieved telemetry is empty. Failing closed.")

    epoch_cols = [col for col in raw_df.columns if "epoch" in col.lower()]
    bx_cols = [col for col in raw_df.columns if "bx" in col.lower()]
    by_cols = [col for col in raw_df.columns if "by" in col.lower()]
    bz_cols = [col for col in raw_df.columns if "bz" in col.lower()]

    if not epoch_cols or not bx_cols or not by_cols or not bz_cols:
        raise SystemExit(
            "[ERROR] Required DSCOVR EPOCH/BX/BY/BZ columns were not found. "
            f"Available columns: {list(raw_df.columns)}"
        )

    time_col = epoch_cols[0]
    bx, by, bz = bx_cols[0], by_cols[0], bz_cols[0]

    print(f"[NVCPP] Converting {time_col} to datetime...")
    parsed_time = pd.to_datetime(
        raw_df[time_col].astype(str).str.strip(),
        format="%Y%m%dT%H%M%S.%fZ",
        errors="coerce",
        utc=True,
    )
    fallback_mask = parsed_time.isna()
    if fallback_mask.any():
        parsed_time = pd.to_datetime(
            raw_df[time_col].astype(str).str.strip(),
            errors="coerce",
            utc=True,
            format="mixed"
        )
    raw_df[time_col] = parsed_time

    print("[NVCPP] Converting magnetic vectors to numeric values...")
    for col in (bx, by, bz):
        raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")

    invalid_time = int(raw_df[time_col].isna().sum())
    
    # Strip NASA missing-data sentinels (-1.0E31) before checking for NaN
    for col in (bx, by, bz):
        raw_df.loc[raw_df[col] <= -1.0e30, col] = np.nan

    invalid_vector = int(raw_df[[bx, by, bz]].isna().any(axis=1).sum())
    valid_mask = raw_df[time_col].notna() & raw_df[[bx, by, bz]].notna().all(axis=1)
    clean_df = raw_df.loc[valid_mask].copy()

    print(
        f"[NVCPP] Sanitization: {len(raw_df)} parsed rows; "
        f"{invalid_time} invalid timestamps; {invalid_vector} invalid vector/fill rows; "
        f"{len(clean_df)} valid physical rows."
    )

    if clean_df.empty:
        raise SystemExit("[ERROR] No valid timestamped magnetic vectors remain. Failing closed.")

    clean_df.sort_values(time_col, inplace=True)
    clean_df.reset_index(drop=True, inplace=True)

    if clean_df[time_col].isna().any():
        raise SystemExit("[ERROR] NaT remained after timestamp sanitization. Failing closed.")
    if not clean_df[time_col].is_monotonic_increasing:
        raise SystemExit("[ERROR] DSCOVR timestamps are not monotonic after sorting. Failing closed.")

    print("[NVCPP] Calculating vector magnitude from BX, BY, BZ components...")
    clean_df["B_mag"] = np.sqrt(
        clean_df[bx].pow(2) + clean_df[by].pow(2) + clean_df[bz].pow(2)
    )

    if not np.isfinite(clean_df["B_mag"].to_numpy()).all():
        raise SystemExit("[ERROR] Non-finite B_mag generated from valid vectors. Failing closed.")

    print("\n[NVCPP] Phase 2: Unclipped Physical Computation (CLINE-L1-B24M-TRAIL-v1)")
    processed_df = run_chain(clean_df, time_col=time_col, b_mag_col="B_mag")

    if "chi_B24M" not in processed_df.columns:
        raise SystemExit("[ERROR] chi_B24M was not produced. Failing closed.")

    # Slice the dataframe to isolate only the intended analysis window (dropping the 24h pre-roll)
    analysis_df = processed_df[processed_df[time_col] >= pd.to_datetime(analysis_start)].copy()
    valid_chi = analysis_df["chi_B24M"].dropna()
    
    if valid_chi.empty:
        raise SystemExit("[ERROR] No valid chi_B24M values were produced in the analysis window. Failing closed.")

    print("\n[NVCPP] Phase 3: Persisting Scientific Artifacts")
    output_csv = run_output_dir / "cline_l1_rows.csv"
    analysis_df.to_csv(output_csv, index=False)

    summary_file = run_output_dir / "cline_l1_report.md"
    max_chi = valid_chi.max()
    max_ratio = analysis_df["ratio_B24M"].max()

    summary_file.write_text(
        f"# NVCPP Historical Run: {run_name}\n\n"
        f"- **Dataset**: {dataset_id}\n"
        f"- **Pre-Roll Retrieval**: {start_time} to {end_time}\n"
        f"- **Analysis Interval**: {analysis_start} to {end_time}\n"
        f"- **Rows retrieved/parsed**: {len(raw_df)}\n"
        f"- **Invalid timestamps rejected**: {invalid_time}\n"
        f"- **CDAWeb fill / invalid vectors rejected**: {invalid_vector}\n"
        f"- **Analysis rows with valid chi_B24M**: {len(valid_chi)}\n"
        f"- **Max ratio_B24M (B/B0)**: {max_ratio:.6g}\n"
        f"- **Max chi_B24M (|B-B0|/|B0|)**: {max_chi:.6g}\n"
        f"- **Baseline Method**: Prior-Only Trailing 24-Hour Median\n"
        f"- **Coverage Gate**: 95% minimum saturation required\n"
        f"- **Clipping applied**: False\n"
    )
    print(f"[NVCPP] SUCCESS. Run artifacts generated at: {run_output_dir}")

def main():
    parser = argparse.ArgumentParser(description="NVCPP Pipeline Execution Router")
    parser.add_argument("pipeline", choices=["dscovr-historical", "archive-query"], help="Target pipeline")
    parser.add_argument("--run", required=True, help="Specific run name")
    parser.add_argument("--outdir", default="runs/historical", help="Local output directory")
    args = parser.parse_args()

    out_path = Path(args.outdir)
    out_path.mkdir(parents=True, exist_ok=True)

    if args.pipeline == "dscovr-historical":
        run_dscovr_historical(args.run, out_path)
    elif args.pipeline == "archive-query":
        print("[NVCPP] Executing bounded observatory archive query...")

if __name__ == "__main__":
    main()
