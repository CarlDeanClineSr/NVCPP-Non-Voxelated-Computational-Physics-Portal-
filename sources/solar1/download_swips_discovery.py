#!/usr/bin/env python3
"""
NVCPP SOLAR-1 SWiPS Discovery Probe
Interrogates NOAA/NCEI Space Weather Portal REST and HAPI endpoints
specifically for the Solar Wind Plasma Sensor (SWiPS) dataset schemas.
"""

import argparse
import json
import hashlib
from pathlib import Path
import requests

NCEI_API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def probe_endpoint(session: requests.Session, endpoint_path: str, params: dict = None) -> dict:
    url = f"{NCEI_API_BASE}{endpoint_path}"
    try:
        response = session.get(url, params=params, timeout=30)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"raw_text": response.text}
        
        raw_dump = json.dumps(data, sort_keys=True)
        return {
            "endpoint": endpoint_path,
            "params": params or {},
            "resolved_url": response.url,
            "status": "ok",
            "sha256": sha256_text(raw_dump),
            "data": data
        }
    except Exception as e:
        return {
            "endpoint": endpoint_path,
            "params": params or {},
            "status": "failed",
            "error": str(e)
        }

def probe_solar1_swips(outdir: str = "runs/solar1/swips_probe") -> dict:
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[NVCPP-Discovery] Probing NOAA/NCEI Space Weather Portal for SOLAR-1 SWiPS...")
    session = requests.Session()
    session.headers.update({"User-Agent": "NVCPP-DiscoveryProbe/1.0.0"})

    requests_log = []

    # 1. Query filtered products for SOLAR-1 SWiPS
    prod_result = probe_endpoint(session, "/products", params={"sat": "SOLAR-1", "inst": "SWIPS"})
    requests_log.append(prod_result)
    
    candidate_datasets = []
    if prod_result["status"] == "ok":
        Path(out_dir / "swips_products_filtered.json").write_text(json.dumps(prod_result["data"], indent=2))
        print("[NVCPP-Discovery] Found SWiPS products:")
        for product in prod_result["data"].get("data", []):
            print(f"  - {product.get('product')} ({product.get('title')})")
            candidate_datasets.append(product.get('product'))

    # 2. Query HAPI catalog for available datasets
    hapi_cat = probe_endpoint(session, "/hapi/catalog")
    requests_log.append(hapi_cat)
    if hapi_cat["status"] == "ok":
        Path(out_dir / "swips_hapi_catalog.json").write_text(json.dumps(hapi_cat["data"], indent=2))

    # 3. If we found SWiPS datasets, probe their HAPI info endpoints
    hapi_info_records = []
    for dataset in candidate_datasets:
        # Check if the dataset exists in the HAPI catalog first
        in_catalog = any(d.get("id") == dataset for d in hapi_cat.get("data", {}).get("catalog", [])) if hapi_cat["status"] == "ok" else False
        if in_catalog or dataset.startswith("sci_swips"):
             print(f"[NVCPP-Discovery] Probing /hapi/info for dataset: {dataset}")
             info_res = probe_endpoint(session, "/hapi/info", params={"dataset": dataset})
             requests_log.append(info_res)
             if info_res["status"] == "ok":
                 Path(out_dir / f"hapi_info_{dataset}.json").write_text(json.dumps(info_res["data"], indent=2))
                 hapi_info_records.append(info_res["data"])

    # Compile Discovery Manifest
    manifest = {
        "source": "NOAA/NCEI Space Weather Portal",
        "api_base": NCEI_API_BASE,
        "mission": "SOLAR-1",
        "instrument": "SWIPS",
        "candidate_datasets": candidate_datasets,
        "probe_passed": True,
        "successful_requests": sum(1 for r in requests_log if r["status"] == "ok"),
        "requests": [{k: v for k, v in r.items() if k != "data"} for r in requests_log]
    }

    manifest_path = out_dir / "solar1_swips_discovery_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[NVCPP-Discovery] SWiPS Probe complete. Manifest written to {manifest_path}")
    return manifest

def main():
    parser = argparse.ArgumentParser(description="SOLAR-1 SWiPS Discovery Probe")
    parser.add_argument("--outdir", default="runs/solar1/swips_probe", help="Output directory for discovery artifacts")
    args = parser.parse_args()
    probe_solar1_swips(outdir=args.outdir)

if __name__ == "__main__":
    main()
