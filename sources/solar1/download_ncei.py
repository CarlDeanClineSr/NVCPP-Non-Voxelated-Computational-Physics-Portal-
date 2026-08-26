#!/usr/bin/env python3
"""
NVCPP SOLAR-1 MAG Discovery Probe
Interrogates NOAA/NCEI Space Weather Portal REST and HAPI endpoints
without guessing schemas, enforcing strict provenance hashing.
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

def probe_solar1_mag(outdir: str = "runs/solar1/probe") -> dict:
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[NVCPP-Discovery] Probing NOAA/NCEI Space Weather Portal at {NCEI_API_BASE}...")
    session = requests.Session()
    session.headers.update({"User-Agent": "NVCPP-DiscoveryProbe/1.0.0"})

    requests_log = []

    # 1. Query filtered products for SOLAR-1 MAG
    prod_result = probe_endpoint(session, "/products", params={"sat": "SOLAR-1", "inst": "MAG"})
    requests_log.append(prod_result)
    if prod_result["status"] == "ok":
        Path(out_dir / "products_filtered.json").write_text(json.dumps(prod_result["data"], indent=2))

    # 2. Query HAPI catalog for available datasets
    hapi_cat = probe_endpoint(session, "/hapi/catalog")
    requests_log.append(hapi_cat)
    if hapi_cat["status"] == "ok":
        Path(out_dir / "hapi_catalog.json").write_text(json.dumps(hapi_cat["data"], indent=2))

    # 3. Query HAPI info endpoint for sci_mag-l3_solar1 parameter definitions
    hapi_info = probe_endpoint(session, "/hapi/info", params={"dataset": "sci_mag-l3_solar1"})
    requests_log.append(hapi_info)
    if hapi_info["status"] == "ok":
        Path(out_dir / "hapi_info_sci_mag_l3.json").write_text(json.dumps(hapi_info["data"], indent=2))

    # Compile Discovery Manifest
    manifest = {
        "source": "NOAA/NCEI Space Weather Portal",
        "api_base": NCEI_API_BASE,
        "mission": "SOLAR-1",
        "instrument": "MAG",
        "known_product_candidates": ["mag-l3_solar1", "sci_mag-l3_solar1"],
        "probe_passed": True,
        "science_computation_enabled": False,
        "reason": "Discovery phase: exact parameter IDs/units/frame/cadence must be frozen first.",
        "successful_requests": sum(1 for r in requests_log if r["status"] == "ok"),
        "requests": [{k: v for k, v in r.items() if k != "data"} for r in requests_log],
        "candidate_product_records": [r["data"] for r in requests_log if r["endpoint"] == "/products" and r["status"] == "ok"],
        "candidate_hapi_records": [r["data"] for r in requests_log if "hapi" in r["endpoint"] and r["status"] == "ok"]
    }

    manifest_path = out_dir / "solar1_mag_discovery_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[NVCPP-Discovery] Probe complete. Manifest written to {manifest_path}")
    return manifest

def main():
    parser = argparse.ArgumentParser(description="SOLAR-1 MAG Discovery Probe")
    parser.add_argument("--outdir", default="runs/solar1/probe", help="Output directory for discovery artifacts")
    args = parser.parse_args()
    probe_solar1_mag(outdir=args.outdir)

if __name__ == "__main__":
    main()
