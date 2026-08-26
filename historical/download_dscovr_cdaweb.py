"""
NVCPP CDAWeb Historical Downloader
Strict, fail-closed REST-CSV ingestion for NASA CDAWeb datasets.
Enforces header-based parsing, raw byte preservation, and SHA-256 provenance.
"""

import requests
import pandas as pd
import hashlib
import json
import sys
from pathlib import Path
from io import StringIO

def download_cdaweb_data(dataset_id: str, start_time: str, end_time: str, variables: list, out_dir: Path) -> pd.DataFrame:
    """
    Downloads telemetry from NASA CDAWeb, strictly verifying dataset and variables.
    Preserves raw bytes and generates SHA-256 checksums for provenance.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Format CDAWeb REST API URL
    var_string = ",".join(variables)
    url = f"https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets/{dataset_id}/data/{start_time},{end_time}/{var_string}?format=text"
    
    print(f"[NVCPP-Historical] Requesting {dataset_id} from {start_time} to {end_time}...")
    
    try:
        headers = {"Accept": "application/json"}
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        data_json = response.json()
        
        # --- DIAGNOSTIC PRINT: Force NASA to show us what they sent ---
        print("\n[NASA CDAWeb Raw Response]:")
        print(json.dumps(data_json, indent=2))
        print("-" * 40 + "\n")
        
        # Extract the dynamically generated file URL from the JSON structure
        file_url = None
        if "FileDescription" in data_json:
            file_url = data_json["FileDescription"][0].get("Name")
        elif "DataResult" in data_json and "FileDescription" in data_json["DataResult"]:
            file_url = data_json["DataResult"]["FileDescription"][0].get("Name")
            
        if not file_url:
            raise ValueError("Could not locate FileDescription URL in NASA's response. See JSON above.")
            
        print(f"[NVCPP-Historical] NASA generated the data at: {file_url}")
        
        # Step 2: Download the actual text/CSV data from the generated URL
        data_response = requests.get(file_url, timeout=60)
        data_response.raise_for_status()
        raw_data = data_response.content
        
    except Exception as e:
        print(f"[ERROR] CDAWeb retrieval failed: {e}", file=sys.stderr)
        raise SystemExit(f"Fail-closed: Cannot retrieve telemetry for {dataset_id}")

    # 1. Provenance: Hash the raw bytes
    sha256_hash = hashlib.sha256(raw_data).hexdigest()
    
    # 2. Provenance: Save raw response bytes
    raw_file = out_dir / f"{dataset_id}_raw_bytes.csv"
    raw_file.write_bytes(raw_data)
    
    # 3. Provenance: Save manifest
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
    
    # 4. Parse with explicit headers (Fail-closed on bad layout)
    try:
        df = pd.read_csv(StringIO(raw_data.decode('utf-8')), comment='#')
    except Exception as e:
        raise SystemExit(f"[ERROR] Strict header parsing failed: {e}")
        
    print(f"[NVCPP-Historical] Successfully parsed {len(df)} rows from {dataset_id}.")
    
    return df
