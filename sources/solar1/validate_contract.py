#!/usr/bin/env python3
"""Fail-closed validation for the authoritative NOAA SOLAR-1 MAG contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

EXPECTED_PROTOCOL = "CLINE-L1-B24M-TRAIL-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCKED_WORDS = {"", "unverified", "unknown", "tbd", "todo", "none", "null"}


class ContractValidationError(ValueError):
    """Raised when a source contract is incomplete or internally inconsistent."""


def _get(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _verified(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in LOCKED_WORDS
    return True


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value.lower()) is not None


def validate_contract(data: dict[str, Any]) -> list[str]:
    """Return all contract errors. An empty list means the contract is admissible."""
    errors: list[str] = []

    exact = {
        "status": "FROZEN_VERIFIED",
        "mission": "SOLAR-1",
        "provider": "NOAA/NCEI",
        "instrument": "MAG",
        "source.product_id": "sci_mag-l3_solar1",
        "source.product_level": "L3",
        "source.hapi_dataset_id": "sci_mag-l3_solar1",
        "vector.coordinate_frame": "GSE",
        "vector.units": "nT",
        "cadence.canonical_iso8601": "PT1M",
        "physics.protocol_id": EXPECTED_PROTOCOL,
        "physics.baseline": "prior-only trailing 24-hour median",
    }
    for path, expected in exact.items():
        actual = _get(data, path)
        if actual != expected:
            errors.append(f"{path} must equal {expected!r}; found {actual!r}")

    required = [
        "contract_version",
        "source.api_base",
        "source.hapi_version",
        "source.product_title",
        "source.availability_start_utc",
        "source.schema_fingerprint_algorithm",
        "source.schema_fingerprint_sha256",
        "time.parameter_id",
        "time.format",
        "time.timestamp_semantics",
        "time.duplicate_policy",
        "cadence.native_iso8601",
        "cadence.expected_seconds",
        "acquisition.response_format",
    ]
    for path in required:
        if not _verified(_get(data, path)):
            errors.append(f"{path} is missing or unverified")

    if str(_get(data, "time.timezone") or "").upper() != "UTC":
        errors.append("time.timezone must be UTC")

    if not _sha256(_get(data, "source.schema_fingerprint_sha256")):
        errors.append("source.schema_fingerprint_sha256 must be a 64-character SHA-256")

    metadata_hashes = _get(data, "source.metadata_sha256")
    if not isinstance(metadata_hashes, list) or not metadata_hashes:
        errors.append("source.metadata_sha256 must contain at least one hash")
    else:
        for value in metadata_hashes:
            if not _sha256(value):
                errors.append(f"invalid source.metadata_sha256 value: {value!r}")

    expected_parameters = _get(data, "acquisition.explicit_parameters")
    component_ids: list[str] = []
    for axis in ("x", "y", "z"):
        base = f"vector.components.{axis}"
        parameter_id = _get(data, f"{base}.parameter_id")
        component_ids.append(str(parameter_id) if parameter_id is not None else "")
        if not _verified(parameter_id):
            errors.append(f"{base}.parameter_id is missing or unverified")
        fills = _get(data, f"{base}.fill_values")
        if not isinstance(fills, list) or not fills:
            errors.append(f"{base}.fill_values must be an explicit nonempty list")
        for optional in ("provider_valid_min", "provider_valid_max"):
            value = _get(data, f"{base}.{optional}")
            if value is not None and not isinstance(value, (int, float)):
                errors.append(f"{base}.{optional} must be numeric or null")

    if len(set(component_ids)) != 3 or "" in component_ids:
        errors.append("x/y/z vector parameter IDs must be three distinct values")

    expected_exact = [_get(data, "time.parameter_id"), *component_ids]
    if expected_parameters != expected_exact:
        errors.append(
            "acquisition.explicit_parameters must exactly equal "
            "[time, x, y, z] from the contract"
        )

    expected_seconds = _get(data, "cadence.expected_seconds")
    if not isinstance(expected_seconds, (int, float)) or float(expected_seconds) <= 0:
        errors.append("cadence.expected_seconds must be positive")
    elif float(expected_seconds) != 60.0:
        errors.append("the frozen SOLAR-1 L3 contract requires 60-second cadence")

    quality = data.get("quality")
    if not isinstance(quality, dict):
        errors.append("quality must be an object")
    else:
        available = quality.get("quality_parameter_available")
        ids = quality.get("parameter_ids")
        basis = quality.get("quality_basis")
        reject = quality.get("reject_rules")
        if not isinstance(available, bool):
            errors.append("quality.quality_parameter_available must be boolean")
        if not isinstance(ids, list):
            errors.append("quality.parameter_ids must be a list")
        if available is True and not ids:
            errors.append("provider quality fields are declared available but parameter_ids is empty")
        if available is False and (not isinstance(basis, list) or not basis):
            errors.append("when no provider quality field exists, quality_basis must be documented")
        if not isinstance(reject, list) or not reject:
            errors.append("quality.reject_rules must be explicit and nonempty")

    if _get(data, "physics.minimum_baseline_coverage_fraction") != 0.95:
        errors.append("physics.minimum_baseline_coverage_fraction must equal 0.95")
    if _get(data, "physics.pre_roll_hours") != 24:
        errors.append("physics.pre_roll_hours must equal 24")
    if _get(data, "physics.clipping_allowed") is not False:
        errors.append("physics.clipping_allowed must be false")
    if _get(data, "physics.science_computation_enabled") is not True:
        errors.append("a FROZEN_VERIFIED contract must explicitly enable science computation")

    return errors


def validate_contract_or_raise(data: dict[str, Any]) -> None:
    errors = validate_contract(data)
    if errors:
        raise ContractValidationError(
            "SOLAR-1 contract validation failed:\n- " + "\n- ".join(errors)
        )


def load_contract_or_raise(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContractValidationError(f"unable to parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractValidationError("contract root must be a JSON object")
    validate_contract_or_raise(data)
    return data


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate the SOLAR-1 MAG source contract")
    parser.add_argument("contract", type=Path)
    args = parser.parse_args()

    try:
        data = load_contract_or_raise(args.contract)
    except ContractValidationError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=2))
        raise SystemExit(2)

    print(
        json.dumps(
            {
                "valid": True,
                "contract": str(args.contract),
                "status": data["status"],
                "product_id": data["source"]["product_id"],
                "schema_fingerprint_sha256": data["source"]["schema_fingerprint_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
