#!/usr/bin/env python3
"""Build a secondary frozen-reference audit from a canonical DSCOVR run.

The command consumes the evidence-preserving output of
``historical.download_dscovr_cdaweb``.  It never changes the canonical rolling
metrics and never treats an event-local magnitude jump as a proved shock or
magnetic-ejecta boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.event_reference import (
    EVENT_REFERENCE_VERSION,
    EventReferenceConfig,
    add_frozen_event_reference,
    event_local_integrity,
)

AUDIT_VERSION = "1.0.0"
OBSERVABLE_ID = "chi_B24M_absB_GSE_1min"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False),
        encoding="utf-8",
    )


def _validate_source_manifest(path: Path, data_path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SUCCESS":
        raise ValueError("source DSCOVR manifest is not SUCCESS")

    expected = None
    for artifact in manifest.get("artifacts", []):
        if isinstance(artifact, dict) and artifact.get("path") == data_path.name:
            expected = artifact.get("sha256")
            break
    observed = _sha256(data_path)
    if not expected:
        raise ValueError("source manifest does not hash the supplied canonical CSV")
    if expected != observed:
        raise ValueError(
            f"canonical CSV hash mismatch: expected {expected}, observed {observed}"
        )
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "canonical_sha256": observed,
        "git_commit": manifest.get("git_commit"),
        "protocol_id": manifest.get("protocol_id"),
        "protocol_version": manifest.get("protocol_version"),
        "coordinate_frame": manifest.get("source", {}).get("coordinate_frame"),
    }


def run_audit(
    input_csv: Path,
    outdir: Path,
    *,
    reference_time: str,
    checkpoints: list[str],
    source_manifest: Path | None = None,
    local_half_window_minutes: int = 5,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "dscovr_event_reference_manifest.json"
    manifest: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "event_reference_version": EVENT_REFERENCE_VERSION,
        "status": "STARTED",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "input_csv": str(input_csv),
        "input_sha256": _sha256(input_csv),
        "observable_id": OBSERVABLE_ID,
        "reference_time_utc": pd.Timestamp(reference_time).isoformat(),
    }
    _write_json(manifest_path, manifest)

    try:
        source_provenance = (
            _validate_source_manifest(source_manifest, input_csv)
            if source_manifest
            else None
        )
        frame = pd.read_csv(input_csv)
        audited, reference = add_frozen_event_reference(
            frame,
            reference_time=reference_time,
            time_col="EPOCH",
            b_mag_col="B_mag",
            baseline_col="B0",
            baseline_status_col="baseline_status",
            coordinate_frame="GSE",
            by_col="BY_(GSE)",
            bz_col="BZ_(GSE)",
        )

        reference_timestamp = pd.Timestamp(reference_time)
        reference_timestamp = (
            reference_timestamp.tz_localize("UTC")
            if reference_timestamp.tzinfo is None
            else reference_timestamp.tz_convert("UTC")
        )
        active = audited.loc[audited["EPOCH"] >= reference_timestamp].copy()
        active_path = outdir / "dscovr_event_reference_rows.csv"
        active.to_csv(active_path, index=False)

        config = EventReferenceConfig(
            expected_cadence_seconds=60.0,
            local_half_window_minutes=local_half_window_minutes,
            minimum_native_coverage_fraction=0.95,
        )
        checkpoint_rows: list[dict[str, Any]] = []
        for checkpoint in checkpoints or [reference_time]:
            timestamp = pd.Timestamp(checkpoint)
            timestamp = (
                timestamp.tz_localize("UTC")
                if timestamp.tzinfo is None
                else timestamp.tz_convert("UTC")
            )
            selected = audited.loc[audited["EPOCH"] == timestamp]
            if len(selected) != 1:
                raise ValueError(
                    f"checkpoint {timestamp.isoformat()} does not match exactly one row"
                )
            row = selected.iloc[0]
            checkpoint_rows.append(
                {
                    "time_utc": timestamp.isoformat(),
                    "Bx_GSE_nT": float(row["BX_(GSE)"]),
                    "By_GSE_nT": float(row["BY_(GSE)"]),
                    "Bz_GSE_nT": float(row["BZ_(GSE)"]),
                    "clock_angle_gse_yz_deg": float(
                        row["clock_angle_gse_yz_deg"]
                    ),
                    "B_mag_nT": float(row["B_mag"]),
                    "B0_live_nT": float(row["B0"]),
                    "event_reference_B_nT": float(row["event_reference_B_nT"]),
                    "delta_live_B24M": float(row["delta_B24M"]),
                    "chi_live_B24M": float(row["chi_B24M"]),
                    "delta_event_reference": float(row["delta_event_reference"]),
                    "chi_event_reference": float(row["chi_event_reference"]),
                    "baseline_coverage_fraction": float(
                        row["baseline_coverage_fraction"]
                    ),
                    "native_coverage_fraction": float(
                        row["native_coverage_fraction"]
                    ),
                    **event_local_integrity(
                        audited,
                        center_time=timestamp,
                        time_col="EPOCH",
                        config=config,
                        native_coverage_col="native_coverage_fraction",
                        baseline_status_col="baseline_status",
                    ),
                }
            )

        checkpoint_table = pd.DataFrame(checkpoint_rows)
        checkpoint_path = outdir / "dscovr_event_reference_checkpoints.csv"
        checkpoint_table.to_csv(checkpoint_path, index=False)

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "source_provenance": source_provenance,
                "reference": reference,
                "policy": {
                    "canonical_observable_id": OBSERVABLE_ID,
                    "canonical_metrics_replaced": False,
                    "local_integrity_window": "centered retrospective",
                    "local_half_window_minutes": local_half_window_minutes,
                    "minimum_native_coverage_fraction": 0.95,
                    "mechanism_claim_allowed": False,
                },
                "metrics": {
                    "active_rows": int(len(active)),
                    "checkpoint_rows": int(len(checkpoint_table)),
                    "checkpoints_passing_local_integrity": int(
                        checkpoint_table["event_local_integrity_pass"].sum()
                    ),
                    "maximum_live_chi_B24M": float(active["chi_B24M"].max()),
                    "maximum_frozen_chi_event": float(
                        active["chi_event_reference"].max()
                    ),
                },
                "artifacts": [
                    {
                        "path": active_path.name,
                        "sha256": _sha256(active_path),
                        "size_bytes": active_path.stat().st_size,
                    },
                    {
                        "path": checkpoint_path.name,
                        "sha256": _sha256(checkpoint_path),
                        "size_bytes": checkpoint_path.stat().st_size,
                    },
                ],
            }
        )
        _write_json(manifest_path, manifest)
        return manifest
    except BaseException as exc:
        manifest.update(
            {
                "status": "FAILED",
                "completed_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        _write_json(manifest_path, manifest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a frozen-reference audit from canonical DSCOVR rows"
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--reference-time", required=True)
    parser.add_argument("--checkpoint", action="append", default=[])
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--local-half-window-minutes", type=int, default=5)
    parser.add_argument("--outdir", required=True, type=Path)
    args = parser.parse_args()

    result = run_audit(
        args.input,
        args.outdir,
        reference_time=args.reference_time,
        checkpoints=args.checkpoint,
        source_manifest=args.source_manifest,
        local_half_window_minutes=args.local_half_window_minutes,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
