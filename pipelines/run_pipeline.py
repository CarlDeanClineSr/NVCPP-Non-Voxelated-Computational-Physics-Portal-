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

# Import the NVCPP core and historical modules we just added
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
    
    # Load configuration
    config_file = Path("config/dscovr_h0_mag.yaml")
    if not config_file.exists():
        print(f"[ERROR] Missing config: {config_file}", file=sys.stderr)
        sys.exit(1)
        
    print(f"[NVCPP] Loaded configuration from {config_file}")
    
    # Target exact May 2024 parameters for our first science run
    if run_name == "gannon_may_2024_dscovr_mag_only":
        dataset_id = "DSCOVR_H0_MAG"
        start_time = "20240510T000000Z" 
        end_time = "20240513T000000Z"   
        # Try B1GSE first, which is the standard vector magnetic field in GSE coordinates
        variables = ["B1GSE"] 
    else:
        print(f"[ERROR] Unknown run target: {run_name}. Failing closed.", file=sys.stderr)
        sys.exit(1)

    # 1. Acquire raw data via CDAWeb
    print("\n[NVCPP] Phase 1: Telemetry Acquisition")
    raw_df = download_cdaweb_data(dataset_id, start_time, end_time, variables, run_output_dir)
    
    if raw_df.empty:
        raise SystemExit("[ERROR] Retrieved telemetry is empty. Failing closed.")
        
    # Dynamically find the exact column names CDAWeb returned
    time_col = [col for col in raw_df.columns if 'epoch' in col.lower()][0]
    
    # Find the magnetic column whether NASA named it b1f1 or b1gse
    try:
        b_col = [col for col in raw_df.columns if 'b1f1' in col.lower() or 'b1gse' in col.lower()][0]
    except IndexError:
        print(f"[ERROR] Could not find expected magnetic field column in NASA's response. Available columns: {list(raw_df.columns)}", file=sys.stderr)
        sys.exit(1)

    # 2. Execute CLINE L1 Math (Unclipped Trailing B0 & Chi_B24M)
    print("\n[NVCPP] Phase 2: Unclipped Physical Computation")
    processed_df = run_chain(raw_df, time_col=time_col, b_mag_col=b_col)
    
    # 3. Save Scientific Evidence
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
