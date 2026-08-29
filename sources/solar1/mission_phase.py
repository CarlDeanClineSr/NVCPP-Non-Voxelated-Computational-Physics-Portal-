#!/usr/bin/env python3
"""Classify SOLAR-1 analysis intervals relative to operational service.

The authoritative operational boundary, labels, and source citation live in the
SOLAR-1 contract. This module is the only implementation of interval-phase
classification. The fixed June 1-5 regression remains useful as a deterministic
integration fixture, but it is explicitly pre-operational.

``apply_phase_label`` remains available for repairing older manifests. New source
runs classify the interval before artifact inventory, so no post-processing step
is required in the normal workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

MISSION_PHASE_VERSION = "1.1.0"
DEFAULT_CONTRACT_PATH = Path("config/solar1_mag_contract.v1.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def _load_contract(path: Path = DEFAULT_CONTRACT_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("SOLAR-1 contract root must be an object")
    _phase_config(data)
    return data


def _phase_config(contract: dict[str, Any]) -> dict[str, Any]:
    phase = contract.get("mission_phase")
    if not isinstance(phase, dict):
        raise ValueError("SOLAR-1 contract has no mission_phase object")
    required = (
        "operational_start_utc",
        "operational_status_source",
        "pre_operational_label",
        "mixed_interval_label",
        "operational_label",
    )
    missing = [name for name in required if not phase.get(name)]
    if missing:
        raise ValueError(f"SOLAR-1 mission_phase is missing: {missing}")
    return phase


def classify_solar1_interval(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable interval label from the authoritative contract."""
    contract = contract or _load_contract()
    config = _phase_config(contract)
    start_time = _utc(start)
    end_time = _utc(end)
    operational_start = _utc(config["operational_start_utc"])
    if not start_time < end_time:
        raise ValueError("SOLAR-1 mission-phase interval must have start < end")

    if end_time <= operational_start:
        label = str(config["pre_operational_label"])
        operational_claim = False
    elif start_time >= operational_start:
        label = str(config["operational_label"])
        operational_claim = True
    else:
        label = str(config["mixed_interval_label"])
        operational_claim = False

    return {
        "mission_phase_version": MISSION_PHASE_VERSION,
        "label": label,
        "analysis_start_utc": start_time.isoformat(),
        "analysis_end_utc": end_time.isoformat(),
        "declared_operational_start_utc": operational_start.isoformat(),
        "operational_status_source": str(config["operational_status_source"]),
        "operational_validation_claim_allowed": operational_claim,
        "interpretation": (
            "phase label describes mission operational status; it does not alter "
            "the provider product quality class or telemetry values"
        ),
    }


def _refresh_artifact_record(manifest: dict[str, Any], path: Path) -> None:
    """Refresh one existing artifact record after a deliberate file mutation."""
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("SOLAR-1 manifest artifacts must be a list")

    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("path") == path.name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one artifact record for {path.name!r}; found {len(matches)}"
        )
    record = matches[0]
    record["size_bytes"] = path.stat().st_size
    record["sha256"] = _sha256(path)


def apply_phase_label(
    manifest_path: Path,
    report_path: Path | None = None,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> dict[str, Any]:
    """Apply the single contract-driven classifier to an older run manifest."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    analysis_window = manifest.get("analysis_window", {})
    start = analysis_window.get("start")
    end = analysis_window.get("end")
    if not start or not end:
        raise ValueError("SOLAR-1 manifest has no complete analysis_window")

    contract = _load_contract(contract_path)
    phase = classify_solar1_interval(start, end, contract)
    manifest["mission_phase"] = phase

    if report_path is not None and report_path.exists():
        original = report_path.read_text(encoding="utf-8")
        phase_lines = "\n".join(
            [
                "",
                "## Mission phase",
                "",
                f"- Phase: `{phase['label']}`",
                f"- NOAA operational start: `{phase['declared_operational_start_utc']}`",
                "- Operational-performance claim enabled: "
                f"**{phase['operational_validation_claim_allowed']}**",
                "- The phase label does not alter the science-quality product class.",
            ]
        )
        if "## Mission phase" not in original:
            report_path.write_text(
                original.rstrip() + "\n" + phase_lines + "\n",
                encoding="utf-8",
            )
        _refresh_artifact_record(manifest, report_path)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return phase


def main() -> None:
    parser = argparse.ArgumentParser(description="Label a SOLAR-1 run by mission phase")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    args = parser.parse_args()
    phase = apply_phase_label(args.manifest, args.report, args.contract)
    print(json.dumps(phase, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
