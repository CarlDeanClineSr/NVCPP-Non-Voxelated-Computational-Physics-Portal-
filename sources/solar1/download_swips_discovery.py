#!/usr/bin/env python3
"""Discovery-only probe for SOLAR-1 SWiPS in NOAA SPOT/HAPI.

A green transport response is not treated as a discovered dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"
VERSION = "2.0.0"
ALIASES = ("SWIPS", "SWiPS", "swips", "SOLAR-1", "SOL-1", "SWFO-L1")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def request_json(
    session: requests.Session,
    endpoint: str,
    *,
    params: dict[str, str] | None,
    outdir: Path,
    filename: str,
) -> dict[str, Any]:
    response = session.get(f"{API_BASE}{endpoint}", params=params, timeout=60)
    raw = response.content
    path = outdir / filename
    path.write_bytes(raw)
    record = {
        "endpoint": endpoint,
        "params": params or {},
        "http_code": response.status_code,
        "resolved_url": response.url,
        "raw_path": path.name,
        "raw_size_bytes": len(raw),
        "raw_sha256": sha256_bytes(raw),
    }
    try:
        response.raise_for_status()
        record["data"] = response.json()
        record["status"] = "OK"
    except Exception as exc:
        record["status"] = "FAILED"
        record["error"] = str(exc)
    return record


def _walk_strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif value is not None:
        yield str(value)


def contains_swips(value: Any) -> bool:
    text = "\n".join(_walk_strings(value)).lower()
    return "swips" in text or "solar wind plasma sensor" in text


def run_probe(outdir: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": f"NVCPP-SWiPS-HAPI/{VERSION}"})

    requests_log = [
        request_json(
            session,
            "/products",
            params={"sat": "SOLAR-1", "inst": "SWIPS"},
            outdir=outdir,
            filename="swips_products_filtered.json",
        ),
        request_json(
            session,
            "/products",
            params=None,
            outdir=outdir,
            filename="all_products.json",
        ),
        request_json(
            session,
            "/hapi/catalog",
            params=None,
            outdir=outdir,
            filename="swips_hapi_catalog.json",
        ),
    ]

    mandatory_transport_ok = all(record["status"] == "OK" for record in requests_log)
    matching_records = []
    for record in requests_log:
        if record["status"] == "OK" and contains_swips(record.get("data")):
            matching_records.append(record["endpoint"])

    candidate_ids: set[str] = set()
    for record in requests_log:
        if record["status"] != "OK":
            continue
        data = record.get("data")
        if record["endpoint"] == "/products" and isinstance(data, dict):
            for product in data.get("data", []):
                if contains_swips(product):
                    identifier = product.get("product") or product.get("id")
                    if identifier:
                        candidate_ids.add(str(identifier))
        if record["endpoint"] == "/hapi/catalog" and isinstance(data, dict):
            for item in data.get("catalog", []):
                if contains_swips(item):
                    identifier = item.get("id")
                    if identifier:
                        candidate_ids.add(str(identifier))

    info_records = []
    for dataset in sorted(candidate_ids):
        info_records.append(
            request_json(
                session,
                "/hapi/info",
                params={"dataset": dataset},
                outdir=outdir,
                filename=f"hapi_info_{dataset.replace('/', '_')}.json",
            )
        )
    requests_log.extend(info_records)

    if not mandatory_transport_ok:
        state = "PROBE_FAILED"
    elif candidate_ids:
        state = "PUBLIC_SPOT_OR_HAPI_CANDIDATES_FOUND"
    else:
        state = "NO_PUBLIC_SPOT_OR_HAPI_SWIPS_DATASET_FOUND"

    manifest = {
        "discovery_version": VERSION,
        "source": "NOAA/NCEI Space Weather Portal",
        "api_base": API_BASE,
        "mission": "SOLAR-1",
        "instrument": "SWIPS",
        "probe_completed": mandatory_transport_ok,
        "dataset_found": bool(candidate_ids),
        "discovery_state": state,
        "aliases_searched": list(ALIASES),
        "candidate_datasets": sorted(candidate_ids),
        "matching_endpoints": matching_records,
        "requests": [
            {key: value for key, value in record.items() if key != "data"}
            for record in requests_log
        ],
        "science_computation_enabled": False,
        "interpretation_limits": [
            "absence from SPOT/HAPI does not establish absence from other NCEI archives",
            "transport success is distinct from dataset discovery",
        ],
    }
    (outdir / "solar1_swips_discovery_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    if not mandatory_transport_ok:
        raise SystemExit("one or more mandatory SWiPS discovery endpoints failed")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe NOAA SPOT/HAPI for SWiPS")
    parser.add_argument("--outdir", type=Path, default=Path("runs/solar1/swips_probe"))
    args = parser.parse_args()
    print(json.dumps(run_probe(args.outdir), indent=2))


if __name__ == "__main__":
    main()
