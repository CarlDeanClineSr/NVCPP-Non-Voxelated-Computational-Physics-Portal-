#!/usr/bin/env python3
"""Classify SOLAR-1 analysis intervals relative to operational service.

The fixed June 1-5 regression is useful as a deterministic integration fixture,
but it precedes NOAA's declared operational date of 2026-06-10.  This module
adds that fact to the machine-readable run manifest and human-readable report;
it does not reject or relabel the provider's science-quality product.

When a report is changed, its artifact size and SHA-256 are refreshed in the
same manifest before the manifest is rewritten.  Phase labeling therefore
cannot leave a stale provenance record behind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

MISSION_PHASE_VERSION = "1.0.1"
SOLAR1_OPERATIONAL_START_UTC = pd.Timestamp("2026-06-10T00:00:00Z")
OPERATIONAL_STATUS_SOURCE = (
    "https://www.ospo.noaa.gov/data/messages/2026/06/MSG_20260610_2105.html"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_solar1_interval(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> dict[str, Any]:
    start_time = pd.Timestamp(start)
    end_time = pd.Timestamp(end)
    start_time = (
        start_time.tz_localize("UTC")
        if start_time.tzinfo is None
        else start_time.tz_convert("UTC")
    )
    end_time = (
        end_time.tz_localize("UTC")
        if end_time.tzinfo is None
        else end_time.tz_convert("UTC")
    )
    if not start_time < end_time:
        raise ValueError("SOLAR-1 mission-phase interval must have start < end")

    if end_time <= SOLAR1_OPERATIONAL_START_UTC:
        label = "PRE_OPERATIONAL_COMMISSIONING_REGRESSION"
        operational_claim = False
    elif start_time >= SOLAR1_OPERATIONAL_START_UTC:
        label = "OPERATIONAL"
        operational_claim = True
    else:
        label = "TRANSITION_SPANNING_OPERATIONAL_START"
        operational_claim = False

    return {
        "mission_phase_version": MISSION_PHASE_VERSION,
        "label": label,
        "analysis_start_utc": start_time.isoformat(),
        "analysis_end_utc": end_time.isoformat(),
        "declared_operational_start_utc": SOLAR1_OPERATIONAL_START_UTC.isoformat(),
        "operational_status_source": OPERATIONAL_STATUS_SOURCE,
        "operational_validation_claim_allowed": operational_claim,
        "interpretation": (
            "phase label describes mission operational status; it does not alter "
            "the provider product quality class or telemetry values"
        ),
    }


def _refresh_artifact_record(
    manifest: dict[str, Any],
    path: Path,
) -> None:
    """Refresh an existing artifact record after a deliberate file mutation.

    The source pipeline stores artifacts as a list of dictionaries.  A missing
    record is treated as a provenance error rather than silently appending a new
    path that the source runner did not originally inventory.
    """

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


def apply_phase_label(manifest_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    analysis_window = manifest.get("analysis_window", {})
    start = analysis_window.get("start")
    end = analysis_window.get("end")
    if not start or not end:
        raise ValueError("SOLAR-1 manifest has no complete analysis_window")

    phase = classify_solar1_interval(start, end)
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
    args = parser.parse_args()
    phase = apply_phase_label(args.manifest, args.report)
    print(json.dumps(phase, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
