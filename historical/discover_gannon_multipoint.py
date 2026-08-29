#!/usr/bin/env python3
"""Discover authoritative ACE and Wind MAG/plasma schemas for the Gannon audit.

This probe performs no cross-spacecraft physics.  It preserves exact provider
metadata and a bounded raw sample for each candidate product so later contracts
are written from the source schema rather than guessed column names.

CDAWeb HAPI exposes only a subset of CDAWeb holdings.  ACE products used here
are available through HAPI.  Wind is discovered through the authoritative CDAS
REST service, with the HAPI absence retained as metadata rather than treated as
missing mission data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PROBE_VERSION = "1.1.0"
HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
CDAS_BASE = (
    "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys"
)
DEFAULT_START = "2024-05-11T10:30:00Z"
DEFAULT_STOP = "2024-05-11T11:30:00Z"

DATASETS: tuple[dict[str, Any], ...] = (
    {
        "mission": "ACE",
        "role": "MAG",
        "dataset_id": "AC_H0_MFI",
        "expected_description": "ACE Magnetic Field 16-Second Level 2 Data",
        "sample_transport": "HAPI_CSV",
        "sample_variables": None,
        "hapi_required": True,
    },
    {
        "mission": "ACE",
        "role": "PLASMA",
        "dataset_id": "AC_H0_SWE",
        "expected_description": "ACE SWEPAM 64-Second Level 2 Data",
        "sample_transport": "HAPI_CSV",
        "sample_variables": None,
        "hapi_required": True,
    },
    {
        "mission": "WIND",
        "role": "MAG",
        "dataset_id": "WI_H0_MFI",
        "expected_description": (
            "Wind MFI definitive 3-second, 1-minute, and hourly data"
        ),
        "sample_transport": "CDAS_TEXT",
        "sample_variables": ["B3GSE", "B3F1"],
        "hapi_required": False,
    },
    {
        "mission": "WIND",
        "role": "PLASMA",
        "dataset_id": "WI_PM_3DP",
        "expected_description": (
            "Wind 3DP PESA-Low onboard ion moments at spin cadence"
        ),
        "sample_transport": "CDAS_TEXT",
        "sample_variables": ["P_DENS", "P_VELS", "P_TEMP"],
        "hapi_required": False,
    },
    {
        "mission": "WIND",
        "role": "PLASMA_DEFINITIVE_SCHEMA_CANDIDATE",
        "dataset_id": "WI_H1_SWE",
        "expected_description": (
            "Wind SWE fitted proton and alpha parameters, including "
            "anisotropic temperatures"
        ),
        "sample_transport": "METADATA_ONLY",
        "sample_variables": [],
        "hapi_required": False,
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


def hapi_parameter_inventory(info: dict[str, Any]) -> list[dict[str, Any]]:
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


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def cdas_variable_inventory(raw: bytes) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DiscoveryError("CDAS variable inventory is not valid XML") from exc

    inventory: list[dict[str, str]] = []
    for element in root.iter():
        if _local_name(element.tag) != "VariableDescription":
            continue
        record: dict[str, str] = {}
        for child in element:
            name = _local_name(child.tag)
            if name in {"Name", "ShortDescription", "LongDescription"}:
                record[name] = child.text or ""
        if record.get("Name"):
            inventory.append(record)
    if not inventory:
        raise DiscoveryError("CDAS variable inventory contains no variables")
    return inventory


def noncomment_rows(raw: bytes) -> int:
    text = raw.decode("utf-8", errors="replace")
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def request_required(
    session: requests.Session,
    endpoint: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> requests.Response:
    response = session.get(
        endpoint,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def request_optional_hapi_info(
    session: requests.Session,
    dataset_id: str,
    dataset_dir: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    response = session.get(
        f"{HAPI_BASE}/info",
        params={"id": dataset_id},
        timeout=120,
    )
    raw = response.content
    path = dataset_dir / (
        "hapi_info.json" if response.ok else "hapi_info_error.txt"
    )
    path.write_bytes(raw)
    metadata = {
        "state": "AVAILABLE" if response.ok else "NOT_EXPOSED",
        "http_status": response.status_code,
        "resolved_url": response.url,
        "content_type": response.headers.get("content-type"),
        "path": str(path),
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }
    if not response.ok:
        return None, metadata
    try:
        payload = response.json()
    except ValueError as exc:
        raise DiscoveryError(
            f"{dataset_id} HAPI /info did not return JSON"
        ) from exc
    if not hapi_status_ok(payload):
        raise DiscoveryError(
            f"{dataset_id} HAPI /info status is not 1200: "
            f"{payload.get('status')}"
        )
    return payload, metadata


def request_cdas_variables(
    session: requests.Session,
    dataset_id: str,
    dataset_dir: Path,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    response = request_required(
        session,
        f"{CDAS_BASE}/datasets/{dataset_id}/variables",
        headers={"Accept": "application/xml"},
    )
    raw = response.content
    raw_path = dataset_dir / "cdas_variables.xml"
    raw_path.write_bytes(raw)
    inventory = cdas_variable_inventory(raw)
    inventory_path = dataset_dir / "cdas_variable_inventory.json"
    write_json(inventory_path, inventory)
    return inventory, {
        "resolved_url": response.url,
        "content_type": response.headers.get("content-type"),
        "raw_path": str(raw_path),
        "raw_sha256": sha256_bytes(raw),
        "raw_size_bytes": len(raw),
        "inventory_path": str(inventory_path),
        "inventory_sha256": sha256_bytes(inventory_path.read_bytes()),
        "variable_count": len(inventory),
        "variable_names": [record["Name"] for record in inventory],
    }


def request_hapi_sample(
    session: requests.Session,
    dataset_id: str,
    start: str,
    stop: str,
    dataset_dir: Path,
) -> dict[str, Any]:
    response = request_required(
        session,
        f"{HAPI_BASE}/data",
        params={
            "id": dataset_id,
            "time.min": start,
            "time.max": stop,
            "format": "csv",
        },
    )
    raw = response.content
    path = dataset_dir / "bounded_sample.csv"
    path.write_bytes(raw)
    rows = noncomment_rows(raw)
    if rows == 0:
        raise DiscoveryError(
            f"{dataset_id} returned no HAPI rows for {start} to {stop}"
        )
    return {
        "transport": "HAPI_CSV",
        "resolved_url": response.url,
        "content_type": response.headers.get("content-type"),
        "path": str(path),
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "noncomment_rows": rows,
    }


def _cdas_time(value: str) -> str:
    return pd.to_datetime(value, utc=True).strftime("%Y%m%dT%H%M%SZ")


def request_cdas_text_sample(
    session: requests.Session,
    dataset_id: str,
    variables: list[str],
    start: str,
    stop: str,
    dataset_dir: Path,
) -> dict[str, Any]:
    if not variables:
        raise DiscoveryError(
            f"{dataset_id} CDAS sample requires explicit variables"
        )
    descriptor_url = (
        f"{CDAS_BASE}/datasets/{dataset_id}/data/"
        f"{_cdas_time(start)},{_cdas_time(stop)}/"
        f"{','.join(variables)}?format=text"
    )
    descriptor_response = request_required(
        session,
        descriptor_url,
        headers={"Accept": "application/json"},
    )
    descriptor_raw = descriptor_response.content
    descriptor_path = dataset_dir / "cdas_descriptor.json"
    descriptor_path.write_bytes(descriptor_raw)
    try:
        descriptor = descriptor_response.json()
    except ValueError as exc:
        raise DiscoveryError(
            f"{dataset_id} CDAS descriptor did not return JSON"
        ) from exc

    descriptions = descriptor.get("FileDescription")
    if descriptions is None:
        descriptions = descriptor.get("DataResult", {}).get("FileDescription")
    if not descriptions:
        raise DiscoveryError(
            f"{dataset_id} CDAS descriptor has no FileDescription"
        )
    data_url = descriptions[0].get("Name")
    if not data_url:
        raise DiscoveryError(
            f"{dataset_id} CDAS FileDescription has no data URL"
        )

    data_response = request_required(session, data_url, timeout=180)
    raw = data_response.content
    data_path = dataset_dir / "bounded_sample.csv"
    data_path.write_bytes(raw)
    rows = noncomment_rows(raw)
    if rows == 0:
        raise DiscoveryError(
            f"{dataset_id} returned no CDAS rows for {start} to {stop}"
        )
    return {
        "transport": "CDAS_TEXT",
        "variables": variables,
        "descriptor": {
            "resolved_url": descriptor_response.url,
            "path": str(descriptor_path),
            "sha256": sha256_bytes(descriptor_raw),
            "size_bytes": len(descriptor_raw),
        },
        "resolved_url": data_response.url,
        "content_type": data_response.headers.get("content-type"),
        "path": str(data_path),
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "noncomment_rows": rows,
    }


def run_probe(*, start: str, stop: str, outdir: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-DISCOVERY/{PROBE_VERSION}"}
    )

    manifest: dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "runtime": {"python": platform.python_version()},
        "provider_interfaces": {
            "hapi": HAPI_BASE,
            "cdas_rest": CDAS_BASE,
        },
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

            hapi_info, hapi_meta = request_optional_hapi_info(
                session,
                dataset_id,
                dataset_dir,
            )
            if spec["hapi_required"] and hapi_info is None:
                raise DiscoveryError(
                    f"{dataset_id} is required through HAPI but /info is unavailable"
                )

            cdas_inventory, cdas_meta = request_cdas_variables(
                session,
                dataset_id,
                dataset_dir,
            )
            inventory_names = {item["Name"] for item in cdas_inventory}
            requested_variables = list(spec["sample_variables"] or [])
            missing_variables = sorted(set(requested_variables) - inventory_names)
            if missing_variables:
                raise DiscoveryError(
                    f"{dataset_id} CDAS inventory lacks explicit variables: "
                    f"{missing_variables}"
                )

            hapi_inventory = (
                hapi_parameter_inventory(hapi_info)
                if hapi_info is not None
                else []
            )
            hapi_inventory_path = dataset_dir / "hapi_parameter_inventory.json"
            write_json(hapi_inventory_path, hapi_inventory)

            transport = spec["sample_transport"]
            if transport == "HAPI_CSV":
                sample = request_hapi_sample(
                    session,
                    dataset_id,
                    start,
                    stop,
                    dataset_dir,
                )
            elif transport == "CDAS_TEXT":
                sample = request_cdas_text_sample(
                    session,
                    dataset_id,
                    requested_variables,
                    start,
                    stop,
                    dataset_dir,
                )
            elif transport == "METADATA_ONLY":
                sample = {
                    "transport": "METADATA_ONLY",
                    "reason": (
                        "schema candidate retained without physics until exact "
                        "fit variables and comparison policy are frozen"
                    ),
                }
            else:
                raise DiscoveryError(
                    f"unsupported sample transport {transport!r}"
                )

            manifest["datasets"][dataset_id] = {
                **spec,
                "hapi": {
                    **hapi_meta,
                    "hapi_version": (
                        hapi_info.get("HAPI") if hapi_info is not None else None
                    ),
                    "availability": (
                        {
                            "start": hapi_info.get("startDate"),
                            "stop": hapi_info.get("stopDate"),
                        }
                        if hapi_info is not None
                        else None
                    ),
                    "parameter_count": len(hapi_inventory),
                    "parameter_names": [
                        item.get("name") for item in hapi_inventory
                    ],
                    "inventory_path": str(hapi_inventory_path),
                    "inventory_sha256": sha256_bytes(
                        hapi_inventory_path.read_bytes()
                    ),
                },
                "cdas_variables": cdas_meta,
                "sample": sample,
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
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/discovery/gannon_multipoint"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_probe(
        start=args.start,
        stop=args.stop,
        outdir=args.outdir,
    )
    print(
        json.dumps(
            {"status": manifest["status"], "outdir": str(args.outdir)}
        )
    )


if __name__ == "__main__":
    main()
