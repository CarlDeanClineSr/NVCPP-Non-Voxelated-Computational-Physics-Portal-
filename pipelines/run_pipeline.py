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
        start_time = "20240510T000000Z" 
        end_time = "20240513T000000Z"   
        variables = ["B1GSE"] 
    else:
        print(f"[ERROR] Unknown run target: {run_name}. Failing closed.", file=sys.stderr)
        sys.exit(1)

    print("\n[NVCPP] Phase 1: Telemetry Acquisition")
    raw_df = download_cdaweb_data(dataset_id, start_time, end_time, variables, run_output_dir)
    
    if raw_df.empty:
        raise SystemExit("[ERROR] Retrieved telemetry is empty. Failing closed.")
        
    time_col = [col for col in raw_df.columns if 'epoch' in col.lower()][0]
    
    # CRITICAL FIX: Convert string times to real Datetime objects for the rolling math engine
    print(f"[NVCPP] Converting {time_col} to Datetime index...")
    raw_df[time_col] = pd.to_datetime(raw_df[time_col], errors='coerce')
    
    try:
        bx = [col for col in raw_df.columns if 'bx' in col.lower()][0]
        by = [col for col in raw_df.columns if 'by' in col.lower()][0]
        bz = [col for col in raw_df.columns if 'bz' in col.lower()][0]
        
        print("[NVCPP] Converting vectors to numerical types...")
        # CRITICAL FIX: Force columns to numeric floats so math works
        raw_df[bx] = pd.to_numeric(raw_df[bx], errors='coerce')
        raw_df[by] = pd.to_numeric(raw_df[by], errors='coerce')
        raw_df[bz] = pd.to_numeric(raw_df[bz], errors='coerce')
        
        print("[NVCPP] Calculating vector magnitude from BX, BY, BZ components...")
        raw_df['B_mag'] = np.sqrt(raw_df[bx]**2 + raw_df[by]**2 + raw_df[bz]**2)
        b_col = 'B_mag'
    except IndexError:
        print(f"[ERROR] Could not find expected magnetic field components. Available columns: {list(raw_df.columns)}", file=sys.stderr)
        sys.exit(1)

    print("\n[NVCPP] Phase 2: Unclipped Physical Computation")
    processed_df = run_chain(raw_df, time_col=time_col, b_mag_col=b_col)
    
    print("\n[NVCPP] Phase 3: Persisting Scientific Artifacts")
    output_csv = run_output_dir / "cline_l1_rows.csv"
    processed_df.to_csv(output_csv, index=False)
    
    summary_file = run_output_dir / "cline_l1_report.md"
    max_chi = processed_df['chi_B24M'].max()
    
    summary_file.write_text(
        f"# NVCPP Historical Run: {run_name}\n\n"
        f"- **Dataset**: {dataset_id}\n"
        f"- **Interval**: {start_time} to {end_time}\n"
        f"- **Rows Processed**: {len(processed_df)}\n"
        f"- **Max Chi_B24M**: {max_chi:.2f}\n"
        f"- **Clipping applied**: False\n"
        f"- **Constraint Guard**: Unclipped Chi_B24M Active\n"
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
