"""Roman prelaunch/readiness contract validation."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class RomanContractError(ValueError):
    """Raised when the Roman readiness contract is incomplete or unsafe."""


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RomanContractError("UTC timestamp must include a timezone")
    return parsed


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    expected = {
        "status": "PRELAUNCH_READINESS",
        "mission": "ROMAN",
        "domain": "ASTRONOMICAL_OBSERVATORY",
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            errors.append(f"{key} must equal {value!r}")

    try:
        parse_utc(str(contract.get("launch_utc", "")))
    except (ValueError, RomanContractError):
        errors.append("launch_utc must be an ISO-8601 UTC timestamp")

    physics = contract.get("physics")
    if not isinstance(physics, dict):
        errors.append("physics must be an object")
    else:
        if physics.get("l1_plasma_physics_allowed") is not False:
            errors.append("Roman must never be admitted to L1 plasma physics")
        if physics.get("chi_B24M_allowed") is not False:
            errors.append("Roman detector products must not be labeled chi_B24M")
        if physics.get("science_claims_enabled") is not False:
            errors.append("prelaunch readiness must not enable science claims")

    mast = contract.get("mast")
    if not isinstance(mast, dict):
        errors.append("mast must be an object")
    else:
        invoke_url = mast.get("invoke_url")
        if not isinstance(invoke_url, str) or not invoke_url.startswith("https://"):
            errors.append("mast.invoke_url must be an HTTPS URL")
        candidates = mast.get("candidate_obs_collections")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not all(isinstance(item, str) and item.strip() for item in candidates)
        ):
            errors.append("mast.candidate_obs_collections must be a nonempty string list")
        max_rows = mast.get("sample_row_limit")
        if not isinstance(max_rows, int) or not (1 <= max_rows <= 500):
            errors.append("mast.sample_row_limit must be between 1 and 500")

    pages = contract.get("official_pages")
    if (
        not isinstance(pages, list)
        or not pages
        or not all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("url"), str)
            and item["url"].startswith("https://")
            for item in pages
        )
    ):
        errors.append("official_pages must contain named HTTPS sources")

    nexus = contract.get("nexus")
    if not isinstance(nexus, dict):
        errors.append("nexus must be an object")
    else:
        if nexus.get("access") != "MYST_AUTHENTICATED":
            errors.append("nexus.access must explicitly state MYST_AUTHENTICATED")
        if nexus.get("automated_public_scrape") is not False:
            errors.append("the authenticated Nexus must not be scraped as a public API")

    fixture = contract.get("synthetic_fixture")
    if not isinstance(fixture, dict):
        errors.append("synthetic_fixture must be an object")
    else:
        shape = fixture.get("shape")
        if (
            not isinstance(shape, list)
            or len(shape) != 2
            or not all(isinstance(item, int) and 32 <= item <= 512 for item in shape)
        ):
            errors.append("synthetic_fixture.shape must contain two integers from 32 to 512")
        if not isinstance(fixture.get("seed"), int):
            errors.append("synthetic_fixture.seed must be an integer")
        if not isinstance(fixture.get("source_count"), int) or fixture["source_count"] < 1:
            errors.append("synthetic_fixture.source_count must be positive")

    return errors


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RomanContractError(f"unable to load Roman contract: {exc}") from exc
    if not isinstance(contract, dict):
        raise RomanContractError("Roman contract root must be an object")
    errors = validate_contract(contract)
    if errors:
        raise RomanContractError(
            "Roman contract validation failed:\n- " + "\n- ".join(errors)
        )
    return contract
