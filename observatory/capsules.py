"""Evidence-first Markdown and JSON capsules for NVCPP candidates and runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def write_event_capsules(
    events: list[dict[str, Any]],
    *,
    mission: str,
    source_manifest: dict[str, Any],
    outdir: Path,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for event in events:
        capsule = {
            "capsule_version": "1.0.0",
            "kind": "NVCPP_EVENT_CANDIDATE",
            "mission": mission,
            "event": event,
            "source_provenance": {
                "run_status": source_manifest.get("status"),
                "git_commit": source_manifest.get("git_commit"),
                "protocol_id": source_manifest.get("protocol_id")
                or source_manifest.get("protocol", {}).get("id"),
                "protocol_version": source_manifest.get("protocol_version")
                or source_manifest.get("protocol", {}).get("version"),
                "source": source_manifest.get("source"),
                "dataset": source_manifest.get("dataset"),
            },
            "teaching_frame": {
                "what_was_observed": (
                    f"{event['dominant_type']} with maximum chi_B24M "
                    f"{event['max_chi_B24M']:.6g}."
                ),
                "what_is_not_established": event.get("interpretation_limits", []),
                "next_tests": [
                    "inspect signed delta and all three vector components",
                    "compare an independently identified L1 spacecraft",
                    "add plasma density, speed, and temperature when available",
                    "inspect coronagraph or solar-imagery context",
                    "use spacecraft ephemeris before interpreting a fitted lag physically",
                ],
            },
        }
        stem = event["event_id"]
        json_path = outdir / f"{stem}.json"
        md_path = outdir / f"{stem}.md"
        _json(json_path, capsule)
        md_path.write_text(
            "\n".join(
                [
                    f"# {stem}",
                    "",
                    f"**State:** `{event['status']}`  ",
                    f"**Mission/source:** `{mission}`  ",
                    f"**Start:** `{event['start_utc']}`  ",
                    f"**End:** `{event['end_utc']}`  ",
                    f"**Dominant candidate:** `{event['dominant_type']}`  ",
                    f"**Severity:** `{event['severity']}`",
                    "",
                    "## Numerical evidence",
                    "",
                    f"- Maximum χB24M: **{event['max_chi_B24M']:.9g}**",
                    f"- Minimum signed ΔB24M: **{event['min_delta_B24M']:.9g}**",
                    f"- Maximum signed ΔB24M: **{event['max_delta_B24M']:.9g}**",
                    f"- Magnetic magnitude range: **{event['min_B_nT']:.9g}–{event['max_B_nT']:.9g} nT**",
                    f"- Maximum vector rotation: **{event['max_rotation_degrees']}°**",
                    f"- Trigger codes: `{', '.join(event['trigger_codes'])}`",
                    "",
                    "## What this may represent",
                    "",
                    "A compression, depression, field rotation, discontinuity, or other magnetic structure candidate. The event type remains unresolved until independent context is added.",
                    "",
                    "## What does not follow",
                    "",
                    *[f"- {item}" for item in event.get("interpretation_limits", [])],
                    "",
                    "## Next tests",
                    "",
                    "- Inspect Bx, By, Bz, |B|, signed Δ, and χ together.",
                    "- Compare another independently identified L1 instrument.",
                    "- Add plasma state, imagery, and ephemeris where available.",
                    "- Preserve contrary results and source-quality anomalies.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        written.extend([json_path, md_path])
    return written


def write_run_lesson(
    *,
    run_id: str,
    window: dict[str, str],
    mission_summaries: dict[str, Any],
    outdir: Path,
) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "RUN_LESSON.md"
    lines = [
        f"# NVCPP Run Lesson — {run_id}",
        "",
        "This report is generated from the run manifests and numerical outputs. It is not an automatic claim of mechanism or discovery.",
        "",
        "## Window",
        "",
        *[f"- {key}: `{value}`" for key, value in window.items()],
        "",
        "## Sources",
        "",
    ]
    for mission, summary in mission_summaries.items():
        lines.extend(
            [
                f"### {mission}",
                "",
                f"- Status: **{summary.get('status')}**",
                f"- Latest sample: `{summary.get('latest', {}).get('time_utc')}`",
                f"- Latest B: `{summary.get('latest', {}).get('B_nT')}` nT",
                f"- Latest signed Δ: `{summary.get('latest', {}).get('delta_B24M')}`",
                f"- Latest χ: `{summary.get('latest', {}).get('chi_B24M')}`",
                f"- Candidate events: **{summary.get('event_count', 0)}**",
                f"- Quarantine rows: **{summary.get('quarantine_rows', 0)}**",
                "",
            ]
        )
    lines.extend(
        [
            "## Teaching rule",
            "",
            "A candidate is a time and structure worth further testing. It becomes stronger when independent instruments, signed vector behavior, plasma state, imagery, and geometry agree. A disagreement is retained as evidence rather than removed.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
