#!/usr/bin/env python3
"""
NVCPP CDAWeb Historical Downloader & DSCOVR Ingestion Engine
Strict, fail-closed REST-CSV ingestion for NASA CDAWeb datasets.
Enforces header-based parsing, raw byte preservation, and SHA-256 provenance.
"""

import argparse
import requests
import pandas as pd
import numpy as np
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO

try:
    from core.cline_l1_chain_v1 import run_chain
except ImportError:
    def run_chain(df, time_col, b_mag_col):
        raise NotImplementedError("Core math engine required to process DSCOVR.")

def format_cdaweb_date(iso_str: str) -> str:
    """
    Translates standard ISO 8601 (e.g., 2026-06-01T00:00:00.000Z)
    to NASA CDAWeb's required URL format (e.g., 20260601T000000Z).
    """
    dt = pd.to_datetime(iso_str)
    return dt.strftime("%Y%m%dT%H%M%SZ")

def download_cdaweb_data(dataset_id: str, start_time: str, end_time: str, variables: list, out_dir: Path) -> pd.DataFrame:
    """
    Downloads telemetry from NASA CDAWeb, strictly verifying dataset and variables.
    Preserves raw bytes and generates SHA-256 checksums for provenance.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    var_string = ",".join(variables)
    url = f"https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets/{dataset_id}/data/{start_time},{end_time}/{var_string}?format=text"
    
    print(f"[NVCPP-Historical] Requesting {dataset_id} from {start_time} to {end_time}...")
    
    try:
        headers = {"Accept": "application/json"}
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        data_json = response.json()
        
        file_url = None
        if "FileDescription" in data_json:
            file_url = data_json["FileDescription"][0].get("Name")
        elif "DataResult" in data_json and "FileDescription" in data_json["DataResult"]:
            file_url = data_json["DataResult"]["FileDescription"][0].get("Name")
            
        if not file_url:
            raise ValueError("Could not locate FileDescription URL in NASA's response.")
            
        print(f"[NVCPP-Historical] NASA generated the data at: {file_url}")
        
        data_response = requests.get(file_url, timeout=60)
        data_response.raise_for_status()
        raw_data = data_response.content
        
    except Exception as e:
        print(f"[ERROR] CDAWeb retrieval failed: {e}", file=sys.stderr)
        raise SystemExit(f"Fail-closed: Cannot retrieve telemetry for {dataset_id}")

    sha256_hash = hashlib.sha256(raw_data).hexdigest()
    
    raw_file = out_dir / f"{dataset_id}_raw_bytes.csv"
    raw_file.write_bytes(raw_data)
    
    manifest = {
        "dataset": dataset_id,
        "start_time": start_time,
        "end_time": end_time,
        "variables": variables,
        "sha256": sha256_hash,
        "bytes": len(raw_data),
        "source_url": url,
        "download_url": file_url
    }
    manifest_file = out_dir / f"{dataset_id}_download_manifest.json"
    with open(manifest_file, "w") as f:
        json.dump(manifest, f, indent=4)
        
    print(f"[NVCPP-Historical] Provenance secured: SHA256 {sha256_hash}")
    
    # Parse with explicit headers (Fail-closed on bad layout)
    try:
        text = raw_data.decode('utf-8')
        lines = [line.strip() for line in text.split('\n') if line.strip() and not line.startswith('#')]
        
        if not lines:
            raise ValueError("No data rows found after filtering comments.")
            
        df = pd.read_csv(StringIO('\n'.join(lines)), sep=r'\s{2,}', engine='python')
        
    except Exception as e:
        raise SystemExit(f"[ERROR] Strict header parsing failed: {e}")
        
    print(f"[NVCPP-Historical] Successfully parsed {len(df)} rows from {dataset_id}.")
    return df

def run_dscovr_pipeline(run_name: str, start_iso: str, analysis_start: str, end_iso: str, outdir: str):
    print(f"[NVCPP] Executing DSCOVR MAG pipeline for run: {run_name}")
    out_path = Path(outdir) / run_name
    out_path.mkdir(parents=True, exist_ok=True)
    
    # Translate standard ISO to CDAWeb URL format
    cdaweb_start = format_cdaweb_date(start_iso)
    cdaweb_end = format_cdaweb_date(end_iso)
    
    # 1. Download DSCOVR_H0_MAG
    raw_df = download_cdaweb_data("DSCOVR_H0_MAG", cdaweb_start, cdaweb_end, ["B1GSE"], out_path)
    
    # 2. Clean and Identify Columns
    raw_df['EPOCH'] = pd.to_datetime(raw_df['EPOCH'], dayfirst=True, errors='coerce', utc=True) 
    invalid_time = raw_df['EPOCH'].isna().sum()
    
    # NASA CDAWeb headers for B1GSE typically look like BX_(GSE), BY_(GSE), BZ_(GSE)
    bx_col = [c for c in raw_df.columns if 'BX' in c.upper()][0]
    by_col = [c for c in raw_df.columns if 'BY' in c.upper()][0]
    bz_col = [c for c in raw_df.columns if 'BZ' in c.upper()][0]
    
    for col in (bx_col, by_col, bz_col):
        raw_df[col] = pd.to_numeric(raw_df[col], errors='coerce')
        
    # Drop massive fill values and invalid times
    valid_mask = raw_df['EPOCH'].notna() & (raw_df[bx_col].abs() < 9999.0)
    clean_df = raw_df.loc[valid_mask].copy()
    
    print(f"[NVCPP] Sanitization: {invalid_time} invalid timestamps, {len(raw_df) - len(clean_df)} fill/invalid vectors rejected.")
    
    clean_df.sort_values('EPOCH', inplace=True)
    clean_df.reset_index(drop=True, inplace=True)
    
    # 3. Calculate Vector Magnitude
    clean_df['B_mag'] = np.sqrt(clean_df[bx_col]**2 + clean_df[by_col]**2 + clean_df[bz_col]**2)
    
    # 4. Run Physics Engine
    print("[NVCPP] Running CLINE-L1 trailing baseline core...")
    processed_df = run_chain(clean_df, time_col='EPOCH', b_mag_col='B_mag')
    
    # 5. Isolate Analysis Window and Persist
    analysis_df = processed_df[processed_df['EPOCH'] >= pd.to_datetime(analysis_start, utc=True)].copy()
    
    output_csv = out_path / "cline_l1_rows.csv"
    analysis_df.to_csv(output_csv, index=False)
    
    print(f"[NVCPP] DSCOVR phase complete! Output written to {output_csv}")

def main():
    parser = argparse.ArgumentParser(description="NVCPP DSCOVR CDAWeb Downloader")
    parser.add_argument("--run", required=True, help="Run identifier (e.g., dscovr_overlap)")
    parser.add_argument("--start", required=True, help="Retrieval start ISO timestamp")
    parser.add_argument("--analysis-start", required=True, help="Analysis start ISO timestamp")
    parser.add_argument("--end", required=True, help="Retrieval end ISO timestamp")
    parser.add_argument("--outdir", default="runs/historical", help="Base output directory")
    args = parser.parse_args()

    run_dscovr_pipeline(
        run_name=args.run,
        start_iso=args.start,
        analysis_start=args.analysis_start,
        end_iso=args.end,
        outdir=args.outdir
    )

if __name__ == "__main__":
    main()
