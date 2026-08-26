#!/usr/bin/env python3
"""Fail-closed metadata monitor for the SOLAR-1 MAG source contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

from sources.solar1.download_solar1 import schema_fingerprint
from sources.solar1.validate_contract import load_contract_or_raise

VERSION = "2.0.0"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fetch_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None,
    outdir: Path,
    filename: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = session.get(url, params=params, timeout=60)
    raw = response.content
    path = outdir / filename
    path.write_bytes(raw)
    response.raise_for_status()
    data = response.json()
    return data, {
        "resolved_url": response.url,
        "http_code": response.status_code,
        "raw_path": path.name,
        "raw_size_bytes": len(raw),
        "raw_sha256": sha256_bytes(raw),
    }


def run_probe(outdir: Path, contract_path: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    contract = load_contract_or_raise(contract_path)
    base = contract["source"]["api_base"]
    session = requests.Session()
    session.headers.update({"User-Agent": f"NVCPP-SOLAR1-MAG-Metadata/{VERSION}"})

    products, products_meta = fetch_json(
        session,
        f"{base}/products",
        params={"sat": "SOLAR-1", "inst": "MAG"},
        outdir=outdir,
        filename="products_filtered.json",
    )
    catalog, catalog_meta = fetch_json(
        session,
        f"{base}/hapi/catalog",
        params=None,
        outdir=outdir,
        filename="hapi_catalog.json",
    )
    info, info_meta = fetch_json(
        session,
        f"{base}/hapi/info",
        params={"dataset": contract["source"]["hapi_dataset_id"]},
        outdir=outdir,
        filename="hapi_info_sci_mag_l3.json",
    )

    product_id = contract["source"]["product_id"]
    product_found = any(
        isinstance(item, dict)
        and (item.get("product") == product_id or item.get("id") == product_id)
        for item in products.get("data", [])
    )
    catalog_found = any(
        isinstance(item, dict) and item.get("id") == product_id
        for item in catalog.get("catalog", [])
    )
    observed_fingerprint, canonical = schema_fingerprint(info, contract)
    expected_fingerprint = contract["source"]["schema_fingerprint_sha256"]
    schema_match = observed_fingerprint == expected_fingerprint
    hapi_ok = info.get("status", {}).get("code") == 1200

    passed = product_found and catalog_found and schema_match and hapi_ok
    manifest = {
        "probe_version": VERSION,
        "probe_passed": passed,
        "science_computation_enabled": False,
        "product_id": product_id,
        "checks": {
            "product_found": product_found,
            "hapi_catalog_found": catalog_found,
            "hapi_status_1200": hapi_ok,
            "schema_fingerprint_match": schema_match,
        },
        "expected_schema_fingerprint_sha256": expected_fingerprint,
        "observed_schema_fingerprint_sha256": observed_fingerprint,
        "canonical_schema": canonical,
        "contract_sha256": sha256_bytes(contract_path.read_bytes()),
        "requests": {
            "products": products_meta,
            "catalog": catalog_meta,
            "info": info_meta,
        },
    }
    (outdir / "solar1_mag_discovery_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    if not passed:
        raise SystemExit("SOLAR-1 MAG metadata no longer matches the frozen contract")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor SOLAR-1 MAG metadata")
    parser.add_argument("--outdir", type=Path, default=Path("runs/solar1/probe"))
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("config/solar1_mag_contract.v1.json"),
    )
    args = parser.parse_args()
    print(json.dumps(run_probe(args.outdir, args.contract), indent=2))


if __name__ == "__main__":
    main()
