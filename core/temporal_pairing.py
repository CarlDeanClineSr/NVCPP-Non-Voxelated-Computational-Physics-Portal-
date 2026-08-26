#!/usr/bin/env python3
"""
NVCPP Temporal Pairing Engine: MAG-to-MAG Spatial Coherence
Aligns DSCOVR and SOLAR-1 datasets and computes the optimal propagation lag
using rolling cross-correlation on the chi_B24M deviation metric.
"""

import argparse
import pandas as pd
import numpy as np
import json
from pathlib import Path

PAIRING_POLICY = {
    "common_coordinate_frame": "GSE",
    "timestamp_semantics": "interval-center UTC",
    "clock_tolerance_seconds": 30,
    "cadence_and_resampling": "1-minute strict outer join",
    "missing_data_policy": "NaN forward fill up to 2 minutes; otherwise drop",
    "lag_search_range_minutes": [-60, 60]
}

def load_and_align(dscovr_path: Path, solar1_path: Path):
    print(f"[NVCPP-PAIRING] Loading DSCOVR artifact: {dscovr_path.name}")
    df_d = pd.read_csv(dscovr_path)
    
    # 1. Convert DSCOVR time and set as index
    df_d['time_utc'] = pd.to_datetime(df_d['EPOCH'], utc=True)
    df_d.set_index('time_utc', inplace=True)
    
    # 2. FIX: Resample the 1-second high-resolution data into strict 1-minute averages
    print("[NVCPP-PAIRING] Resampling DSCOVR 1-second telemetry to 1-minute cadence...")
    df_d = df_d[['chi_B24M']].resample('1min').mean()
    df_d.rename(columns={'chi_B24M': 'chi_DSCOVR'}, inplace=True)
    
    print(f"[NVCPP-PAIRING] Loading SOLAR-1 artifact: {solar1_path.name}")
    df_s = pd.read_csv(solar1_path)
    
    # SOLAR-1 is already 1-minute cadence from HAPI
    df_s['time_utc'] = pd.to_datetime(df_s['time'], utc=True).dt.round('min')
    df_s = df_s.set_index('time_utc')[['chi_B24M']].rename(columns={'chi_B24M': 'chi_SOLAR1'})
    
    # 3. Strict alignment with 2-minute gap tolerance
    print("[NVCPP-PAIRING] Aligning timestamps and applying missing-data policy...")
    merged = df_d.join(df_s, how='outer')
    merged = merged.ffill(limit=2).dropna()
    
    print(f"[NVCPP-PAIRING] Alignment complete. {len(merged)} overlapping physics rows valid.")
    return merged

def compute_lag_correlation(df: pd.DataFrame, max_lag: int = 60):
    print(f"[NVCPP-PAIRING] Executing lag-search over ±{max_lag} minute window...")
    lags = np.arange(-max_lag, max_lag + 1)
    corrs = []
    
    for lag in lags:
        # Shift SOLAR-1 relative to DSCOVR to simulate propagation delay
        shifted_solar = df['chi_SOLAR1'].shift(-lag)
        corr = df['chi_DSCOVR'].corr(shifted_solar)
        corrs.append(corr)
        
    optimal_idx = np.argmax(corrs)
    optimal_lag = lags[optimal_idx]
    max_corr = corrs[optimal_idx]
    
    print(f"[NVCPP-PAIRING] Optimal Propagation Lag: {optimal_lag} minutes (Pearson r = {max_corr:.4f})")
    
    return {
        "optimal_lag_minutes": int(optimal_lag),
        "maximum_correlation": float(max_corr),
        "lag_distribution": dict(zip([int(l) for l in lags], [float(c) if not np.isnan(c) else 0.0 for c in corrs]))
    }

def run_pairing_engine(dscovr_csv, solar1_csv, outdir):
    out_path = Path(outdir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    aligned_df = load_and_align(Path(dscovr_csv), Path(solar1_csv))
    
    if aligned_df.empty:
        print("[NVCPP-PAIRING] WARNING: No overlapping rows found between the datasets. Exiting cleanly.")
        return
        
    lag_metrics = compute_lag_correlation(aligned_df)
    
    manifest = {
        "policy": PAIRING_POLICY,
        "metrics": lag_metrics,
        "overlapping_rows": len(aligned_df)
    }
    
    aligned_df.to_csv(out_path / "mag_paired_l1.csv")
    (out_path / "pairing_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[NVCPP-PAIRING] Coherence artifacts successfully saved to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NVCPP Temporal Pairing Engine")
    parser.add_argument("--dscovr", required=True, help="Path to DSCOVR processed CSV")
    parser.add_argument("--solar1", required=True, help="Path to SOLAR-1 processed CSV")
    parser.add_argument("--outdir", default="runs/pairing", help="Output directory")
    args = parser.parse_args()
    
    run_pairing_engine(args.dscovr, args.solar1, outdir=args.outdir)
