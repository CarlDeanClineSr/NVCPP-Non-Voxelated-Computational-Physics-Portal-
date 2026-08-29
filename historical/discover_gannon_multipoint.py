#!/usr/bin/env python3
"""Discover authoritative ACE and Wind MAG/plasma schemas for the Gannon audit.

This probe performs no cross-spacecraft physics. It preserves the exact HAPI
``/info`` response and a bounded raw CSV sample for each candidate CDAWeb
product so the final contracts can be written from provider metadata rather
than guessed column names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

PROBE_VERSION = "1.0.0"
HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
DEFAULT_START = "2024-05-11T10:30:00Z"
DEFAULT_STOP = "2024-05-11T11:30:00Z"

DATASETS: tuple[dict[str, str], ...] = (
    {
        "mission": "ACE",
        "role": "MAG",
        "dataset_id": "AC_H0_MFI",
        "expected_description": "ACE Magnetic Field 16-Second Level 2 Data",
    },
    {
        "mission": "ACE",
        "role": "PLASMA",
        "dataset_id": "AC_H0_SWE",
        "expected_description": "ACE SWEPAM 64-Second Level 2 Data",
    },
    {
        "mission": "WIND",
        "role": "MAG",
        "dataset_id": "WI_H0_MFI",
        "expected_description": "Wind MFI definitive magnetic-field data",
    },
    {
        "mission": "WIND",
        "role": "PLASMA",
        "dataset_id": "WI_H1_SWE",
        "expected_description": "Wind SWE proton/alpha parameters",
    },
)


class DiscoveryError(RuntimeError):
    """Raised when provider discovery cannot be completed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def hapi_status_ok(payload: dict[str, Any]) -> bool:
    return payload.get("status", {}).get("code") == 1200


def parameter_inventory(info: dict[str, Any]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for parameter in info.get("parameters", []):
        if not isinstance(parameter, dict):
            continue
        inventory.append(
            {
                key: parameter.get(key)
                for key in (
                    "name",
                    "description",
                    "type",
                    "units",
                    "fill",
                    "size",
                    "length",
                )
                if key in parameter
            }
        )
    return inventory


def noncomment_rows(raw: bytes) -> int:
    text = raw.decode("utf-8", errors="replace")
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def request_bytes(
    session: requests.Session,
    endpoint: str,
    *,
    params: dict[str, str],
    timeout: int = 120,
) -> tuple[bytes, str, str | None]:
    response = session.get(endpoint, params=params, timeout=timeout)
    response.raise_for_status()
    return response.content, response.url, response.headers.get("content-type")


def run_probe(*, start: str, stop: str, outdir: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": f"NVCPP-GANNON-DISCOVERY/{PROBE_VERSION}"})

    manifest: dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "runtime": {"python": platform.python_version()},
        "hapi_base": HAPI_BASE,
        "sample_window": {"start": start, "stop": stop},
        "physics_computed": False,
        "datasets": {},
    }
    manifest_path = outdir / "gannon_multipoint_discovery_manifest.json"
    write_json(manifest_path, manifest)

    try:
        for spec in DATASETS:
            dataset_id = spec["dataset_id"]
            dataset_dir = outdir / dataset_id
            dataset_dir.mkdir(parents=True, exist_ok=True)

            info_raw, info_url, info_content_type = request_bytes(
                session,
                f"{HAPI_BASE}/info",
                params={"id": dataset_id},
            )
            info_path = dataset_dir / "hapi_info.json"
            info_path.write_bytes(info_raw)
            try:
                info = json.loads(info_raw)
            except json.JSONDecodeError as exc:
                raise DiscoveryError(
                    f"{dataset_id} /info did not return valid JSON"
                ) from exc
            if not hapi_status_ok(info):
                raise DiscoveryError(
                    f"{dataset_id} /info status is not 1200: {info.get('status')}"
                )

            data_raw, data_url, data_content_type = request_bytes(
                session,
                f"{HAPI_BASE}/data",
                params={
                    "id": dataset_id,
                    "time.min": start,
                    "time.max": stop,
                    "format": "csv",
                },
            )
            data_path = dataset_dir / "bounded_sample.csv"
            data_path.write_bytes(data_raw)
            rows = noncomment_rows(data_raw)
            if rows == 0:
                raise DiscoveryError(
                    f"{dataset_id} returned no bounded sample rows for {start} to {stop}"
                )

            parameters = parameter_inventory(info)
            inventory_path = dataset_dir / "parameter_inventory.json"
            write_json(inventory_path, parameters)

            manifest["datasets"][dataset_id] = {
                **spec,
                "hapi_version": info.get("HAPI"),
                "availability": {
                    "start": info.get("startDate"),
                    "stop": info.get("stopDate"),
                },
                "parameter_count": len(parameters),
                "parameter_names": [item.get("name") for item in parameters],
                "info": {
                    "path": str(info_path),
                    "resolved_url": info_url,
                    "content_type": info_content_type,
                    "sha256": sha256_bytes(info_raw),
                    "size_bytes": len(info_raw),
                },
                "sample": {
                    "path": str(data_path),
                    "resolved_url": data_url,
                    "content_type": data_content_type,
                    "sha256": sha256_bytes(data_raw),
                    "size_bytes": len(data_raw),
                    "noncomment_rows": rows,
                },
                "parameter_inventory": {
                    "path": str(inventory_path),
                    "sha256": sha256_bytes(inventory_path.read_bytes()),
                },
            }
            write_json(manifest_path, manifest)

        manifest["status"] = "SUCCESS"
        manifest["completed_utc"] = utc_now()
        write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["completed_utc"] = utc_now()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_json(manifest_path, manifest)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--stop", default=DEFAULT_STOP)
    parser.add_argument("--outdir", type=Path, default=Path("runs/discovery/gannon_multipoint"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_probe(start=args.start, stop=args.stop, outdir=args.outdir)
    print(json.dumps({"status": manifest["status"], "outdir": str(args.outdir)}))


if __name__ == "__main__":
    main()
