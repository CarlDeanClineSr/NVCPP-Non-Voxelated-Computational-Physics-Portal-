#!/usr/bin/env python3
"""Reproducible DSCOVR Gannon live-versus-frozen event-reference audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd

from core.event_reference import (
    EventReferenceColumns,
    EventReferencePolicy,
    attach_event_reference,
    local_integrity_gate,
    select_later_structure_candidate,
)

AUDIT_VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_components(columns: list[str]) -> tuple[str, str, str]:
    resolved: list[str] = []
    for axis in ("BX", "BY", "BZ"):
        matches = [
            column
            for column in columns
            if axis in column.upper()
            and "GSE" in column.upper()
            and "SPHR" not in column.upper()
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one {axis} GSE component; found {matches}")
        resolved.append(matches[0])
    return tuple(resolved)  # type: ignore[return-value]


def _validate_source_manifest(manifest_path: Path, input_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SUCCESS":
        raise ValueError("DSCOVR source manifest is not SUCCESS")
    if manifest.get("source", {}).get("coordinate_frame") != "GSE":
        raise ValueError("DSCOVR source manifest is not GSE")
    expected = None
    for artifact in manifest.get("artifacts", []):
        if isinstance(artifact, dict) and artifact.get("path") == input_path.name:
            expected = artifact.get("sha256")
            break
    observed = sha256_file(input_path)
    if expected != observed:
        raise ValueError(
            f"canonical input hash mismatch: expected {expected!r}, observed {observed}"
        )
    return {
        "path": str(manifest_path),
        "sha256": sha256_file(manifest_path),
        "git_commit": manifest.get("git_commit"),
        "protocol_id": manifest.get("protocol_id"),
        "protocol_version": manifest.get("protocol_version"),
        "canonical_sha256": observed,
    }


def _trigger_evidence(row: pd.Series, policy: EventReferencePolicy) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    rotation = float(row["rotation_from_previous_minute_degrees"])
    jump = float(row["minute_relative_magnitude_change"])
    if rotation >= policy.rotation_candidate_degrees:
        evidence.append(
            {
                "code": "FIELD_ROTATION_CANDIDATE",
                "metric": "rotation_from_previous_minute_degrees",
                "reference": "previous one-minute GSE vector",
                "operator": ">=",
                "threshold": policy.rotation_candidate_degrees,
                "observed": rotation,
            }
        )
    if jump >= policy.magnitude_jump_candidate_fraction:
        evidence.append(
            {
                "code": "MAGNITUDE_JUMP_CANDIDATE",
                "metric": "minute_relative_magnitude_change",
                "reference": "previous one-minute |<B_GSE>_1min|",
                "operator": ">=",
                "threshold": policy.magnitude_jump_candidate_fraction,
                "observed": jump,
            }
        )
    evidence.append(
        {
            "code": "FROZEN_EVENT_DEPARTURE",
            "metric": "chi_event_ref_absB",
            "reference": "last valid pre-gate live B0",
            "operator": ">=",
            "threshold": policy.frozen_severe_chi,
            "observed": float(row["chi_event_ref_absB"]),
        }
    )
    evidence.append(
        {
            "code": "LIVE_CHI_BELOW_RESEARCH_WATCH",
            "metric": "chi_B24M",
            "reference": "live prior-only 24-hour median B0",
            "operator": "<",
            "threshold": policy.research_watch_chi,
            "observed": float(row["chi_B24M"]),
        }
    )
    return evidence


def run_audit(
    input_path: Path,
    manifest_path: Path,
    outdir: Path,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    provenance = _validate_source_manifest(manifest_path, input_path)
    frame = pd.read_csv(input_path)
    bx, by, bz = _resolve_components(list(frame.columns))
    columns = EventReferenceColumns(
        time="EPOCH",
        bx=bx,
        by=by,
        bz=bz,
        native_sample_count="native_sample_count",
    )
    policy = EventReferencePolicy()
    annotated, reference = attach_event_reference(
        frame,
        columns=columns,
        policy=policy,
    )
    selected, selection = select_later_structure_candidate(
        annotated,
        columns=columns,
        policy=policy,
    )
    selected_time = pd.Timestamp(selected[columns.time])
    integrity = local_integrity_gate(
        annotated,
        timestamp=selected_time,
        columns=columns,
        policy=policy,
    )
    if integrity["status"] != "PASS":
        raise ValueError("selected later structure failed the local integrity gate")

    full_path = outdir / "gannon_live_vs_frozen_full.csv"
    annotated.to_csv(full_path, index=False)

    half = pd.Timedelta(minutes=policy.local_integrity_half_window_minutes)
    local = annotated.loc[
        annotated[columns.time].between(selected_time - half, selected_time + half)
    ].copy()
    local["selected_later_structure"] = local[columns.time].eq(selected_time)
    local_path = outdir / "gannon_later_structure_window.csv"
    local.to_csv(local_path, index=False)

    trigger_evidence = _trigger_evidence(selected, policy)
    first_absorbed = annotated.loc[
        annotated["baseline_regime"].eq("EVENT_ABSORBED_BY_LIVE_BASELINE"),
        columns.time,
    ].iloc[0]

    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "SUCCESS",
        "source": provenance,
        "quantity": {
            "B": "magnitude of the component-wise one-minute mean GSE vector",
            "live_reference": "prior-only 24-hour median over [t-24h, t)",
            "frozen_reference": "last valid pre-gate live B0",
            "clipping_applied": False,
        },
        "event_reference": reference,
        "first_live_baseline_absorption_time_utc": pd.Timestamp(first_absorbed).isoformat(),
        "later_structure": {
            **selection,
            "Bz_GSE_nT": float(selected[bz]),
            "B_mag_nT": float(selected[columns.magnitude]),
            "live_B0_nT": float(selected[columns.live_baseline]),
            "live_delta": float(selected[columns.live_delta]),
            "live_chi": float(selected[columns.live_chi]),
            "frozen_delta": float(selected["delta_event_ref_absB"]),
            "frozen_chi": float(selected["chi_event_ref_absB"]),
            "baseline_regime": str(selected["baseline_regime"]),
            "local_integrity": integrity,
            "trigger_evidence": trigger_evidence,
        },
        "artifacts": [],
        "interpretation_limits": [
            "the selected row is data-selected by frozen/live divergence and vector/jump gates",
            "GSE Bz and GSE rotation are not GSM clock-angle or geoeffectiveness claims",
            "the frozen overlay does not replace chi_B24M",
            "an event candidate does not establish a physical boundary or mechanism",
        ],
    }

    plt.figure(figsize=(12, 5))
    plt.plot(annotated[columns.time], annotated[columns.live_chi], label="live chi_B24M")
    plt.plot(
        annotated[columns.time],
        annotated["chi_event_ref_absB"],
        label="frozen event-reference chi",
    )
    plt.axvline(
        pd.Timestamp(reference["gate"]["derived_time_utc"]),
        linestyle="--",
        label="derived gate",
    )
    plt.axvline(selected_time, linestyle=":", label="selected later structure")
    plt.axhline(policy.research_watch_chi, linestyle=":", label="live research watch")
    plt.xlabel("UTC")
    plt.ylabel("dimensionless departure")
    plt.title("DSCOVR Gannon: live and frozen magnetic departure")
    plt.legend()
    plt.tight_layout()
    chart_path = outdir / "gannon_live_vs_frozen.png"
    plt.savefig(chart_path, dpi=180)
    plt.close()

    report_path = outdir / "GANNON_EVENT_REFERENCE_AUDIT.md"
    report_path.write_text(
        "\n".join(
            [
                "# DSCOVR Gannon Event-Reference Audit",
                "",
                f"- Derived gate: `{reference['gate']['derived_time_utc']}`",
                f"- Derived reference time: `{reference['reference']['time_utc']}`",
                f"- Frozen reference B: **{reference['reference']['B_nT']:.6f} nT**",
                f"- First live-baseline absorption row: `{pd.Timestamp(first_absorbed).isoformat()}`",
                f"- Data-selected later structure: `{selected_time.isoformat()}`",
                f"- Bz GSE: **{float(selected[bz]):.6f} nT**",
                f"- B magnitude: **{float(selected[columns.magnitude]):.6f} nT**",
                f"- Live B0: **{float(selected[columns.live_baseline]):.6f} nT**",
                f"- Live chi: **{float(selected[columns.live_chi]):.6f}**",
                f"- Frozen chi: **{float(selected['chi_event_ref_absB']):.6f}**",
                f"- One-minute vector rotation: **{float(selected['rotation_from_previous_minute_degrees']):.6f}°**",
                f"- One-minute magnitude jump: **{float(selected['minute_relative_magnitude_change']):.6f}**",
                f"- Local integrity: **{integrity['status']}**",
                "",
                "The selected row is not hand-picked. It is the strongest normalized",
                "rotation/jump candidate after live chi fell below the watch level while",
                "the frozen pre-event departure remained severe.",
                "",
                "`EVENT_ABSORBED_BY_LIVE_BASELINE` describes metric behavior only; it",
                "is not a declaration that the physical storm ended or entered a named phase.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    for path in (full_path, local_path, chart_path, report_path):
        manifest["artifacts"].append(
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest_path_out = outdir / "gannon_event_reference_manifest.json"
    manifest_path_out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Gannon live and frozen references")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("runs/audits/gannon_event_reference"))
    args = parser.parse_args()
    result = run_audit(args.input, args.manifest, args.outdir)
    print(json.dumps(result["later_structure"], indent=2, default=str))


if __name__ == "__main__":
    main()
