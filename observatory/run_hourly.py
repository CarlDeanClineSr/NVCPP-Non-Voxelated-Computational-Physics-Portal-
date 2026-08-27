#!/usr/bin/env python3
"""Run the NVCPP hourly observatory and produce one immutable evidence package."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from core.event_detection import CanonicalColumns, EventThresholds, detect_events
from observatory.capsules import write_event_capsules, write_run_lesson
from observatory.charts import generate_mission_charts
from observatory.status import inventory, write_latest_status
from observatory.time_windows import ObservatoryWindow, build_hourly_window
from sources.noaa_swpc.download_realtime import run_noaa_realtime_pipeline
from sources.solar1.download_solar1 import run_solar1_pipeline

OBSERVATORY_VERSION = "1.0.0"


class ObservatoryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("config_version") != "1.0.0":
        raise ObservatoryError("unsupported hourly observatory config version")
    return data


def _run_id(window: ObservatoryWindow) -> str:
    suffix = os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}"
    return f"nvcpp-hourly-{window.analysis_end.strftime('%Y%m%dT%H%MZ')}-run-{suffix}"


def _iso(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _quarantine_rows(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        return int(len(pd.read_csv(path)))
    except pd.errors.EmptyDataError:
        return 0


def _mission_analyze(
    *,
    mission: str,
    canonical_path: Path,
    manifest_path: Path,
    quarantine_path: Path,
    columns: CanonicalColumns,
    thresholds: EventThresholds,
    focus_start: pd.Timestamp,
    outdir: Path,
) -> dict[str, Any]:
    frame = pd.read_csv(canonical_path)
    frame[columns.time] = pd.to_datetime(frame[columns.time], utc=True, errors="coerce")
    prepared, events, metrics = detect_events(
        frame,
        mission=mission,
        columns=columns,
        thresholds=thresholds,
        focus_start=focus_start,
    )
    manifest = _read_json(manifest_path)
    chart_dir = outdir / "charts"
    chart_paths = generate_mission_charts(
        prepared,
        mission=mission,
        time_col="_time",
        magnitude_col="_B",
        baseline_col="B0",
        delta_col="_delta",
        chi_col="_chi",
        components=(columns.bx, columns.by, columns.bz),
        events=events,
        research_watch_chi=thresholds.research_watch_chi,
        outdir=chart_dir,
    )
    capsule_paths = write_event_capsules(
        events,
        mission=mission,
        source_manifest=manifest,
        outdir=outdir / "capsules",
    )
    event_path = outdir / "event_candidates.json"
    event_path.write_text(json.dumps(events, indent=2, sort_keys=True, default=str), encoding="utf-8")
    if events:
        pd.DataFrame(events).to_csv(outdir / "event_candidates.csv", index=False)

    summary = {
        "status": "SUCCESS",
        "source_state": manifest.get("source_state", "RETRIEVED"),
        "event_count": len(events),
        "watch_rows": metrics["watch_rows"],
        "candidate_rows": metrics["candidate_rows"],
        "quarantine_rows": _quarantine_rows(quarantine_path),
        "latest": metrics["latest"],
        "canonical_path": canonical_path.relative_to(outdir.parent.parent).as_posix()
        if canonical_path.is_relative_to(outdir.parent.parent)
        else str(canonical_path),
        "manifest_path": str(manifest_path),
        "chart_paths": [str(path) for path in chart_paths],
        "capsule_paths": [str(path) for path in capsule_paths],
        "events": events,
    }
    (outdir / "mission_observatory_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return summary


def _write_result_index(
    run_root: Path,
    mission_summaries: dict[str, Any],
    window: ObservatoryWindow,
) -> Path:
    path = run_root / "result_index.jsonl"
    records: list[dict[str, Any]] = []
    for mission, summary in mission_summaries.items():
        records.append(
            {
                "record_type": "MISSION_RUN",
                "mission": mission,
                "status": summary.get("status"),
                "analysis_end_utc": window.analysis_end.isoformat(),
                "latest": summary.get("latest"),
                "event_count": summary.get("event_count", 0),
                "quarantine_rows": summary.get("quarantine_rows", 0),
            }
        )
        for event in summary.get("events", []):
            records.append({"record_type": "EVENT_CANDIDATE", **event})
    path.write_text(
        "".join(json.dumps(record, sort_keys=True, default=str) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def run_hourly_observatory(
    *,
    config_path: Path,
    output_root: Path,
    now: str | pd.Timestamp | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    timing = config["timing"]
    window = build_hourly_window(
        now=now,
        safety_lag_minutes=int(timing["safety_lag_minutes"]),
        retrieval_hours=int(timing["retrieval_hours"]),
        analysis_hours=int(timing["analysis_hours"]),
        focus_minutes=int(timing["event_focus_minutes"]),
    )
    run_id = _run_id(window)
    run_root = (
        output_root
        / window.analysis_end.strftime("%Y")
        / window.analysis_end.strftime("%m")
        / window.analysis_end.strftime("%d")
        / window.analysis_end.strftime("%H")
        / run_id
    )
    mission_root = run_root / "missions"
    run_root.mkdir(parents=True, exist_ok=True)
    mission_root.mkdir(parents=True, exist_ok=True)

    observatory_manifest: dict[str, Any] = {
        "manifest_version": "1.0.0",
        "observatory_version": OBSERVATORY_VERSION,
        "status": "STARTED",
        "run_id": run_id,
        "started_utc": utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "github": {
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "ref": os.environ.get("GITHUB_REF"),
        },
        "runtime": {"python": platform.python_version()},
        "config_path": str(config_path),
        "window": window.as_dict(),
        "missions": {},
        "storage": {
            "github_artifact": "PLANNED",
            "drive_state": "PENDING_OPTIONAL_UPLOAD",
            "drive_parent_folder_id": config["storage"].get("drive_parent_folder_id"),
        },
    }
    manifest_path = run_root / "observatory_run_manifest.json"
    manifest_path.write_text(
        json.dumps(observatory_manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    thresholds = EventThresholds.from_mapping(config.get("event_detection"))
    mission_summaries: dict[str, Any] = {}
    errors: dict[str, str] = {}

    source_specs: list[dict[str, Any]] = []
    if config["sources"]["noaa_operational_l1"]["enabled"]:
        source_specs.append(
            {
                "name": "NOAA_OPERATIONAL_L1",
                "runner": lambda: run_noaa_realtime_pipeline(
                    run_name="noaa_operational_l1",
                    retrieval_start=_iso(window.retrieval_start),
                    analysis_start=_iso(window.analysis_start),
                    analysis_end=_iso(window.analysis_end),
                    outdir=mission_root,
                ),
                "canonical": mission_root / "noaa_operational_l1" / "noaa_realtime_canonical.csv",
                "manifest": mission_root / "noaa_operational_l1" / "noaa_realtime_run_manifest.json",
                "quarantine": mission_root / "noaa_operational_l1" / "noaa_realtime_quarantine.csv",
                "columns": CanonicalColumns(
                    time="time", bx="bx_gsm", by="by_gsm", bz="bz_gsm"
                ),
                "output": mission_root / "noaa_operational_l1" / "observatory",
            }
        )
    if config["sources"]["solar1_mag"]["enabled"]:
        source_specs.append(
            {
                "name": "SOLAR1_MAG",
                "runner": lambda: run_solar1_pipeline(
                    run_name="solar1_mag",
                    start_time=_iso(window.retrieval_start),
                    analysis_start=_iso(window.analysis_start),
                    end_time=_iso(window.analysis_end),
                    outdir=mission_root,
                    contract_path=Path(config["sources"]["solar1_mag"]["contract"]),
                ),
                "canonical": mission_root / "solar1_mag" / "solar1_cline_l1_rows.csv",
                "manifest": mission_root / "solar1_mag" / "solar1_run_manifest.json",
                "quarantine": mission_root / "solar1_mag" / "solar1_quarantine.csv",
                "columns": CanonicalColumns(
                    time="time",
                    bx="b_gse_min_x",
                    by="b_gse_min_y",
                    bz="b_gse_min_z",
                ),
                "output": mission_root / "solar1_mag" / "observatory",
            }
        )

    for spec in source_specs:
        name = spec["name"]
        try:
            spec["runner"]()
            summary = _mission_analyze(
                mission=name,
                canonical_path=spec["canonical"],
                manifest_path=spec["manifest"],
                quarantine_path=spec["quarantine"],
                columns=spec["columns"],
                thresholds=thresholds,
                focus_start=window.focus_start,
                outdir=spec["output"],
            )
            mission_summaries[name] = summary
            observatory_manifest["missions"][name] = {"status": "SUCCESS"}
        except Exception as exc:  # preserve one source failure without erasing other evidence
            errors[name] = f"{type(exc).__name__}: {exc}"
            mission_summaries[name] = {
                "status": "FAILED",
                "error": errors[name],
                "event_count": 0,
                "watch_rows": 0,
                "quarantine_rows": _quarantine_rows(spec["quarantine"]),
                "latest": {},
                "events": [],
            }
            observatory_manifest["missions"][name] = {
                "status": "FAILED",
                "error": errors[name],
            }

    success_count = sum(summary.get("status") == "SUCCESS" for summary in mission_summaries.values())
    overall_status = "SUCCESS" if success_count == len(source_specs) else (
        "DEGRADED" if success_count > 0 else "FAILED"
    )
    observatory_manifest["status"] = overall_status
    observatory_manifest["completed_utc"] = utc_now()
    observatory_manifest["errors"] = errors

    write_run_lesson(
        run_id=run_id,
        window=window.as_dict(),
        mission_summaries=mission_summaries,
        outdir=run_root,
    )
    _write_result_index(run_root, mission_summaries, window)
    status = {
        "status_version": "1.0.0",
        "run_id": run_id,
        "status": overall_status,
        "completed_utc": observatory_manifest["completed_utc"],
        "window": window.as_dict(),
        "missions": mission_summaries,
        "storage": observatory_manifest["storage"],
    }
    write_latest_status(status, outdir=run_root / "status")

    observatory_manifest["artifact_inventory"] = inventory(
        run_root, exclude={manifest_path.name}
    )
    manifest_path.write_text(
        json.dumps(observatory_manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(run_root)
    if overall_status == "FAILED":
        raise ObservatoryError("all configured hourly sources failed; evidence package was preserved")
    return {"run_root": str(run_root), "manifest": observatory_manifest, "status": status}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NVCPP hourly observatory")
    parser.add_argument("--config", type=Path, default=Path("config/hourly_observatory.v1.json"))
    parser.add_argument("--outdir", type=Path, default=Path("runs/hourly"))
    parser.add_argument("--now", default=None, help="Optional deterministic UTC time for testing")
    args = parser.parse_args()
    try:
        run_hourly_observatory(config_path=args.config, output_root=args.outdir, now=args.now)
    except Exception as exc:
        print(f"[NVCPP-HOURLY-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
