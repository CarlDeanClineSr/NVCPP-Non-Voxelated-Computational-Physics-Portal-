#!/usr/bin/env python3
"""Preserve the NOAA/NCEI f1m_dscovr HAPI schema and a bounded sample."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"
DATASET_ID = "f1m_dscovr"
DEFAULT_START = "2024-05-11T10:30:00Z"
DEFAULT_STOP = "2024-05-11T11:30:00Z"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def run_discovery(*, start: str, stop: str, outdir: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "NVCPP-DSCOVR-F1M-DISCOVERY/1.0.0"})

    info_response = session.get(
        f"{API_BASE}/hapi/info",
        params={"dataset": DATASET_ID},
        timeout=60,
    )
    info_response.raise_for_status()
    info_raw = info_response.content
    info_path = outdir / "hapi_info.json"
    info_path.write_bytes(info_raw)
    info = info_response.json()
    if info.get("status", {}).get("code") != 1200:
        raise RuntimeError(f"HAPI info status is not 1200: {info.get('status')}")

    parameters = [
        item.get("name")
        for item in info.get("parameters", [])
        if isinstance(item, dict) and item.get("name")
    ]
    if not parameters or parameters[0].lower() != "time":
        raise RuntimeError(f"unexpected parameter inventory: {parameters}")

    data_response = session.get(
        f"{API_BASE}/hapi/data",
        params={
            "dataset": DATASET_ID,
            "start": start,
            "stop": stop,
            "parameters": ",".join(parameters),
            "format": "csv",
        },
        timeout=120,
    )
    data_response.raise_for_status()
    raw = data_response.content
    raw_path = outdir / "bounded_sample.csv"
    raw_path.write_bytes(raw)

    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    rows = [row for row in rows if row]
    if not rows:
        raise RuntimeError("f1m_dscovr returned no rows")
    bad = [index for index, row in enumerate(rows, start=1) if len(row) != len(parameters)]
    if bad:
        raise RuntimeError(f"strict field-count mismatch at rows {bad[:10]}")

    inventory = [
        {
            key: item.get(key)
            for key in ("name", "description", "type", "units", "fill", "size")
            if key in item
        }
        for item in info.get("parameters", [])
        if isinstance(item, dict)
    ]
    inventory_path = outdir / "parameter_inventory.json"
    write_json(inventory_path, inventory)

    manifest = {
        "status": "SUCCESS",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "NOAA/NCEI",
        "dataset_id": DATASET_ID,
        "sample_window": {"start": start, "stop": stop},
        "parameter_names": parameters,
        "parameter_count": len(parameters),
        "rows": len(rows),
        "info": {
            "path": str(info_path),
            "resolved_url": info_response.url,
            "sha256": sha256_bytes(info_raw),
            "size_bytes": len(info_raw),
            "startDate": info.get("startDate"),
            "stopDate": info.get("stopDate"),
        },
        "sample": {
            "path": str(raw_path),
            "resolved_url": data_response.url,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
        },
        "inventory": {
            "path": str(inventory_path),
            "sha256": sha256_bytes(inventory_path.read_bytes()),
        },
        "physics_computed": False,
    }
    write_json(outdir / "dscovr_f1m_discovery_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--stop", default=DEFAULT_STOP)
    parser.add_argument("--outdir", type=Path, default=Path("runs/discovery/dscovr_f1m"))
    args = parser.parse_args()
    result = run_discovery(start=args.start, stop=args.stop, outdir=args.outdir)
    print(json.dumps({"status": result["status"], "parameters": result["parameter_names"]}))


if __name__ == "__main__":
    main()
