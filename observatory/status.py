"""Latest-status and immutable run-index records for the NVCPP observatory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in excluded:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return records


def write_latest_status(
    status: dict[str, Any],
    *,
    outdir: Path,
) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / "latest.json"
    md_path = outdir / "LATEST.md"
    json_path.write_text(json.dumps(status, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# NVCPP — Hourly Observatory Status",
        "",
        f"**Run:** `{status['run_id']}`  ",
        f"**State:** `{status['status']}`  ",
        f"**Completed:** `{status.get('completed_utc')}`",
        "",
        "## Observation window",
        "",
        *[f"- {key}: `{value}`" for key, value in status["window"].items()],
        "",
        "## Mission/source state",
        "",
    ]
    for mission, summary in status.get("missions", {}).items():
        failed = summary.get("status") == "FAILED"
        event_count = "unavailable (source failed)" if failed else summary.get("event_count", 0)
        watch_rows = "unavailable (source failed)" if failed else summary.get("watch_rows", 0)
        lines.extend(
            [
                f"### {mission}",
                "",
                f"- Status: **{summary.get('status')}**",
                f"- Source state: **{summary.get('source_state', 'not reported')}**",
                f"- Latest sample: `{summary.get('latest', {}).get('time_utc')}`",
                f"- B: `{summary.get('latest', {}).get('B_nT')}` nT",
                f"- Signed ΔB24M: `{summary.get('latest', {}).get('delta_B24M')}`",
                f"- χB24M: `{summary.get('latest', {}).get('chi_B24M')}`",
                f"- Candidate events: **{event_count}**",
                f"- Research-watch rows: **{watch_rows}**",
                f"- Quarantine rows: **{summary.get('quarantine_rows', 0)}**",
                "",
            ]
        )
        if failed:
            lines.append(f"- Failure reason: **{summary.get('reason_code', 'SOURCE_EXCEPTION')}**")
            if not summary.get("diagnostics"):
                lines.append(f"- Error: `{summary.get('error', 'not reported')}`")
            context = {
                key: summary[key]
                for key in (
                    "diagnostics", "provider_availability", "hourly_requested_window",
                    "hourly_effective_window", "effective_retrieval_window",
                    "effective_analysis_window", "latest_physical_sample_utc",
                    "diagnostic_read_error",
                )
                if key in summary
            }
            if context:
                lines.extend(["", "```json", json.dumps(context, indent=2, sort_keys=True, default=str), "```"])
            lines.append("")
    lines.extend(
        [
            "## Storage",
            "",
            f"- GitHub artifact: **{status.get('storage', {}).get('github_artifact', 'planned')}**",
            f"- Google Drive vault: **{status.get('storage', {}).get('drive_state', 'NOT_CONFIGURED')}**",
            "",
            "## Interpretation limit",
            "",
            "Candidate detection and correlation identify times for further physics evaluation. They do not by themselves establish a mechanism, universal law, or propagation speed.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
