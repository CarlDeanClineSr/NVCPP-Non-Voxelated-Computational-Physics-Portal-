#!/usr/bin/env python3
"""Validate the frozen NOAA SOLAR-1 MAG source contract.

The validator has one purpose: prevent discovery metadata from becoming science
input until the source contract is complete, internally consistent, and marked
FROZEN_VERIFIED. It performs no physics calculations.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_PROTOCOL = "CLINE-L1-B24M-TRAIL-v1"
LOCKED_WORDS = {"", "unverified", "unknown", "tbd", "todo", "none", "null"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _get(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _is_verified_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in LOCKED_WORDS
    return True


def validate_contract(data: dict[str, Any], *, allow_locked: bool = False) -> list[str]:
    errors: list[str] = []

    status = str(data.get("status", "")).strip()
    if status not in {"DISCOVERY_LOCKED", "FROZEN_VERIFIED"}:
        errors.append("status must be DISCOVERY_LOCKED or FROZEN_VERIFIED")
    if status != "FROZEN_VERIFIED" and not allow_locked:
        errors.append("contract is not FROZEN_VERIFIED; science admission remains locked")

    expected_identity = {
        "mission": "SOLAR-1",
        "provider": "NOAA/NCEI",
        "instrument": "MAG",
        "source.product_id": "sci_mag-l3_solar1",
        "source.product_level": "L3",
        "physics.protocol_id": EXPECTED_PROTOCOL,
    }
    for path, expected in expected_identity.items():
        actual = _get(data, path)
        if actual != expected:
            errors.append(f"{path} must equal {expected!r}; found {actual!r}")

    required_verified = [
        "source.api_build",
        "source.product_title",
        "source.hapi_dataset_id",
        "source.availability_start_utc",
        "time.parameter_id",
        "time.format",
        "time.timestamp_semantics",
        "time.leap_second_policy",
        "vector.coordinate_frame",
        "vector.units",
        "cadence.native_iso8601",
    ]
    for path in required_verified:
        if not _is_verified_scalar(_get(data, path)):
            errors.append(f"{path} is missing or unverified")

    if str(_get(data, "time.timezone") or "").upper() != "UTC":
        errors.append("time.timezone must be UTC")

    units = str(_get(data, "vector.units") or "").strip().lower()
    if units not in {"nt", "nanotesla", "nanoteslas"}:
        errors.append("vector.units must explicitly identify nanotesla (nT)")

    component_ids: list[str] = []
    for axis in ("x", "y", "z"):
        base = f"vector.components.{axis}"
        parameter_id = _get(data, f"{base}.parameter_id")
        if not _is_verified_scalar(parameter_id):
            errors.append(f"{base}.parameter_id is missing or unverified")
        else:
            component_ids.append(str(parameter_id))
        if not _is_verified_scalar(_get(data, f"{base}.label")):
            errors.append(f"{base}.label is missing or unverified")
        if not isinstance(_get(data, f"{base}.fill_values"), list):
            errors.append(f"{base}.fill_values must be an explicit list")
        if not _is_verified_scalar(_get(data, f"{base}.valid_min")):
            errors.append(f"{base}.valid_min is missing")
        if not _is_verified_scalar(_get(data, f"{base}.valid_max")):
            errors.append(f"{base}.valid_max is missing")

    if len(component_ids) == 3 and len(set(component_ids)) != 3:
        errors.append("x/y/z vector parameter IDs must be distinct")

    hashes = _get(data, "source.metadata_sha256")
    if not isinstance(hashes, list) or not hashes:
        errors.append("source.metadata_sha256 must contain at least one raw metadata hash")
    else:
        for item in hashes:
            if not isinstance(item, str) or not SHA256_RE.fullmatch(item.lower()):
                errors.append(f"invalid SHA-256 value: {item!r}")

    if _get(data, "cadence.canonical_iso8601") != "PT1M":
        errors.append("cadence.canonical_iso8601 must be PT1M")

    official_one_minute = _get(data, "cadence.official_one_minute_product")
    if not isinstance(official_one_minute, bool):
        errors.append("cadence.official_one_minute_product must be true or false")
    if official_one_minute is False:
        coverage = _get(data, "cadence.minimum_native_coverage_fraction")
        aggregation = _get(data, "cadence.aggregation")
        if not isinstance(coverage, (int, float)) or not (0 < float(coverage) <= 1):
            errors.append("derived one-minute data require a coverage fraction in (0, 1]")
        if not _is_verified_scalar(aggregation):
            errors.append("derived one-minute data require an explicit aggregation method")

    quality_ids = _get(data, "quality.parameter_ids")
    reject_rules = _get(data, "quality.reject_rules")
    if not isinstance(quality_ids, list) or not quality_ids:
        errors.append("quality.parameter_ids must identify NOAA quality fields")
    if not isinstance(reject_rules, list) or not reject_rules:
        errors.append("quality.reject_rules must be explicit and nonempty")

    if _get(data, "physics.baseline") != "prior-only trailing 24-hour median":
        errors.append("physics.baseline does not match the frozen protocol")
    if _get(data, "physics.minimum_baseline_coverage_fraction") != 0.95:
        errors.append("minimum baseline coverage must equal 0.95")
    if _get(data, "physics.pre_roll_hours") != 24:
        errors.append("pre-roll must equal 24 hours")
    if _get(data, "physics.clipping_allowed") is not False:
        errors.append("clipping_allowed must be false")

    enabled = _get(data, "physics.science_computation_enabled")
    if enabled is not False and enabled is not True:
        errors.append("physics.science_computation_enabled must be boolean")
    if enabled is True and status != "FROZEN_VERIFIED":
        errors.append("science computation cannot be enabled while the contract is locked")

    # A verified contract must pass every check before science is enabled.
    if status == "FROZEN_VERIFIED" and enabled is not True:
        errors.append("a FROZEN_VERIFIED contract must explicitly enable science computation")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a SOLAR-1 MAG source contract")
    parser.add_argument("contract", type=Path)
    parser.add_argument(
        "--allow-locked",
        action="store_true",
        help="validate template structure without granting science admission",
    )
    args = parser.parse_args()

    data = json.loads(args.contract.read_text(encoding="utf-8"))
    errors = validate_contract(data, allow_locked=args.allow_locked)

    report = {
        "contract": str(args.contract),
        "status": data.get("status"),
        "science_computation_enabled": _get(data, "physics.science_computation_enabled"),
        "valid": not errors,
        "errors": errors,
    }
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if not errors else 2)


if __name__ == "__main__":
    main()
