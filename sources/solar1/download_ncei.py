#!/usr/bin/env python3
"""NVCPP NOAA SOLAR-1 NCEI source discovery adapter.

This module intentionally performs discovery before science computation.
It interrogates the NOAA/NCEI Space Weather Portal API, preserves the raw
catalog responses with SHA-256 hashes, and identifies candidate SOLAR-1 MAG
products/parameters. It does NOT calculate B0 or chi_B24M and it does NOT
silently guess parameter names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import requests

NCEI_API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"
KNOWN_MAG_PRODUCTS = ("mag-l3_solar1", "sci_mag-l3_solar1")
REQUEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ProbeRequest:
    name: str
    endpoint: str
    params: dict[str, str]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_json(session: requests.Session, request: ProbeRequest) -> tuple[Any, bytes, str]:
    url = f"{NCEI_API_BASE}{request.endpoint}"
    response = session.get(url, params=request.params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    raw = response.content
    try:
        payload = response.json()
    except Exception as exc:  # fail closed: unexpected API response is not science input
        raise RuntimeError(f"NCEI endpoint {response.url} did not return JSON: {exc}") from exc
    return payload, raw, response.url


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _contains_all(value: Any, *needles: str) -> bool:
    text = "\n".join(_walk_strings(value)).lower()
    return all(needle.lower() in text for needle in needles)


def _extract_matching_records(payload: Any, *needles: str) -> list[Any]:
    """Return dict/list records whose serialized text contains all needles."""
    matches: list[Any] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if _contains_all(node, *needles):
                matches.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(payload)
    # Deduplicate by stable JSON encoding.
    unique: dict[str, Any] = {}
    for item in matches:
        try:
            key = json.dumps(item, sort_keys=True, default=str)
        except TypeError:
            key = repr(item)
        unique[key] = item
    return list(unique.values())


def probe_solar1_mag(out_dir: Path) -> dict[str, Any]:
    """Probe NOAA/NCEI for current SOLAR-1 MAG product and parameter metadata.

    The probe is deliberately discovery-only. A future science adapter must
    freeze explicit parameter IDs, units, coordinate frame, cadence, quality
    flags, and product maturity before data are admitted to the NVCPP core.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "NVCPP/1.0 provenance-first SOLAR-1 probe"})

    requests_to_try = [
        ProbeRequest("products_filtered", "/products", {"sat": "SOLAR-1", "inst": "MAG"}),
        ProbeRequest("parameters_operational_l3", "/parameters", {"products": "mag-l3_solar1"}),
        ProbeRequest("parameters_science_l3", "/parameters", {"products": "sci_mag-l3_solar1"}),
        ProbeRequest("hapi_catalog", "/hapi/catalog", {}),
    ]

    manifest: dict[str, Any] = {
        "mission": "SOLAR-1",
        "instrument": "MAG",
        "source": "NOAA/NCEI Space Weather Portal",
        "api_base": NCEI_API_BASE,
        "known_product_candidates": list(KNOWN_MAG_PRODUCTS),
        "science_computation_enabled": False,
        "reason": "Discovery phase: exact parameter IDs/units/frame/cadence must be frozen first.",
        "requests": [],
        "candidate_product_records": [],
        "candidate_parameter_records": [],
        "candidate_hapi_records": [],
    }

    successful = 0
    for spec in requests_to_try:
        record = {"request": asdict(spec), "status": "failed"}
        try:
            payload, raw, resolved_url = _request_json(session, spec)
            successful += 1
            raw_path = out_dir / f"{spec.name}.json"
            raw_path.write_bytes(raw)
            record.update(
                {
                    "status": "ok",
                    "resolved_url": resolved_url,
                    "bytes": len(raw),
                    "sha256": _sha256_bytes(raw),
                    "artifact": raw_path.name,
                }
            )

            if spec.name.startswith("products"):
                manifest["candidate_product_records"].extend(
                    _extract_matching_records(payload, "solar", "mag")
                )
            elif spec.name.startswith("parameters"):
                manifest["candidate_parameter_records"].extend(
                    _extract_matching_records(payload, "mag")
                )
            elif spec.name == "hapi_catalog":
                manifest["candidate_hapi_records"].extend(
                    _extract_matching_records(payload, "solar")
                )
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        manifest["requests"].append(record)

    # Keep manifest compact while retaining raw API responses as separate artifacts.
    for key in ("candidate_product_records", "candidate_parameter_records", "candidate_hapi_records"):
        values = manifest[key]
        dedup: dict[str, Any] = {}
        for item in values:
            encoded = json.dumps(item, sort_keys=True, default=str)
            dedup[encoded] = item
        manifest[key] = list(dedup.values())[:100]

    manifest["successful_requests"] = successful
    manifest["probe_passed"] = successful > 0 and bool(manifest["candidate_product_records"] or manifest["candidate_hapi_records"])

    manifest_path = out_dir / "solar1_mag_discovery_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    if not manifest["probe_passed"]:
        raise SystemExit(
            "[NVCPP-SOLAR1] Discovery did not verify a SOLAR-1 MAG catalog record. "
            f"Inspect {manifest_path} and raw probe artifacts; science computation remains disabled."
        )

    print(f"[NVCPP-SOLAR1] Discovery passed. Manifest: {manifest_path}")
    print("[NVCPP-SOLAR1] Science computation remains LOCKED pending parameter-contract review.")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe NOAA/NCEI SOLAR-1 MAG source metadata")
    parser.add_argument("--outdir", default="runs/solar1/probe", help="Probe artifact directory")
    args = parser.parse_args()
    probe_solar1_mag(Path(args.outdir))


if __name__ == "__main__":
    main()
