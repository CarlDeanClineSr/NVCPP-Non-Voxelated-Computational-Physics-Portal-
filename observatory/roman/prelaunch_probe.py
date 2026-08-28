"""Roman prelaunch archive and simulation-readiness probe.

The probe asks only bounded, public questions:

* Is Roman registered in the MAST CAOM mission list?
* Do any configured Roman collection names currently return public rows?
* Are the official NASA/STScI readiness pages reachable?
* Can NVCPP's image-domain fixture and analysis path execute deterministically?

No Roman product is routed through L1 plasma equations, and absence from MAST
before science operations is treated as a normal readiness state rather than a
failure or a negative scientific result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import requests

from .contracts import RomanContractError, load_contract, parse_utc
from .mast_client import (
    MastClient,
    MastError,
    classify_archive_state,
    probe_page,
    safe_slug,
)
from .synthetic_fixture import generate_fixture


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = parse_utc(value)
    return parsed.astimezone(timezone.utc)


def _hours_to_launch(now: datetime, launch: datetime) -> float:
    return (launch - now).total_seconds() / 3600.0


def _archive_report(
    *,
    archive_state: str,
    missions: list[str],
    collection_counts: dict[str, int],
    sample_rows: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [
        "# Roman MAST Archive Readiness",
        "",
        f"- **Archive state:** `{archive_state}`",
        f"- **MAST mission count:** {len(missions)}",
        f"- **Configured Roman collection candidates:** {len(collection_counts)}",
        "",
        "## Candidate collection counts",
        "",
        "| Collection | Rows |",
        "|---|---:|",
    ]
    for name, count in collection_counts.items():
        lines.append(f"| `{name}` | {count} |")
    if sample_rows:
        lines.extend(
            [
                "",
                "## Sample rows",
                "",
                "Samples are bounded metadata records only. No large products were downloaded.",
            ]
        )
        for collection, rows in sample_rows.items():
            lines.append(f"\n### `{collection}`\n")
            lines.append(f"Rows preserved: {len(rows)}")
    lines.extend(
        [
            "",
            "## Interpretation limit",
            "",
            "Absence from the current CAOM mission list or a zero row count is an archive-"
            "availability result, not a statement about Roman hardware, launch status, "
            "commissioning, or scientific performance.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_probe(
    *,
    config_path: Path,
    outdir: Path,
    now_value: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    contract = load_contract(config_path)
    now = _parse_now(now_value)
    launch = parse_utc(contract["launch_utc"]).astimezone(timezone.utc)

    outdir.mkdir(parents=True, exist_ok=True)
    raw_dir = outdir / "raw"
    reports_dir = outdir / "reports"
    fixture_dir = outdir / "synthetic_fixture"
    raw_dir.mkdir(exist_ok=True)
    reports_dir.mkdir(exist_ok=True)

    mast_config = contract["mast"]
    mast = MastClient(
        invoke_url=mast_config["invoke_url"],
        timeout_seconds=float(mast_config.get("timeout_seconds", 45)),
        max_poll_seconds=float(mast_config.get("max_poll_seconds", 60)),
        session=session,
    )

    mast_errors: list[str] = []
    missions: list[str] = []
    mission_response_summary: dict[str, Any] | None = None
    collection_counts: dict[str, int] = {}
    collection_response_summaries: dict[str, Any] = {}
    sample_rows: dict[str, list[dict[str, Any]]] = {}

    try:
        missions, response = mast.list_missions()
        response.write_raw(raw_dir / "mast_missions_list.json")
        mission_response_summary = response.summary()
        _write_json(outdir / "mast_missions.json", missions)
    except MastError as exc:
        mast_errors.append(str(exc))

    for collection in mast_config["candidate_obs_collections"]:
        slug = safe_slug(collection)
        try:
            count, response = mast.count_collection(collection)
            response.write_raw(raw_dir / f"mast_count_{slug}.json")
            collection_counts[collection] = count
            collection_response_summaries[collection] = response.summary()
            if count > 0:
                sample = mast.sample_collection(
                    collection,
                    pagesize=int(mast_config["sample_row_limit"]),
                )
                sample.write_raw(raw_dir / f"mast_sample_{slug}.json")
                data = sample.payload.get("data", [])
                if not isinstance(data, list):
                    raise MastError(f"sample data for {collection} is not a list")
                rows = [row for row in data if isinstance(row, dict)]
                sample_rows[collection] = rows
                _write_json(outdir / f"roman_mast_sample_{slug}.json", rows)
                collection_response_summaries[collection]["sample"] = sample.summary()
        except MastError as exc:
            collection_counts.setdefault(collection, 0)
            mast_errors.append(f"{collection}: {exc}")

    if mission_response_summary is None and not collection_response_summaries:
        archive_state = "MAST_TRANSPORT_FAILED"
    else:
        archive_state = classify_archive_state(
            missions=missions,
            collection_counts=collection_counts,
        )

    page_summaries: list[dict[str, Any]] = []
    page_errors: list[str] = []
    for page in contract["official_pages"]:
        try:
            probed = probe_page(
                page["url"],
                timeout_seconds=float(page.get("timeout_seconds", 30)),
                session=session,
            )
            slug = safe_slug(page["name"])
            raw_path = raw_dir / f"official_{slug}.html"
            raw_path.write_bytes(probed.raw_bytes)
            summary = probed.summary()
            summary["name"] = page["name"]
            summary["raw_path"] = str(raw_path.relative_to(outdir))
            page_summaries.append(summary)
        except (MastError, ValueError) as exc:
            page_errors.append(f"{page['name']}: {exc}")

    fixture = generate_fixture(
        config=contract["synthetic_fixture"],
        outdir=fixture_dir,
    )

    hours_to_launch = _hours_to_launch(now, launch)
    if hours_to_launch > 0:
        mission_phase = "PRELAUNCH"
    elif hours_to_launch > -24 * 30:
        mission_phase = "LAUNCH_OR_COMMISSIONING_WINDOW"
    else:
        mission_phase = "POSTLAUNCH_ARCHIVE_WATCH"

    transport_ok = mission_response_summary is not None or bool(
        collection_response_summaries
    )
    overall_status = "READY"
    if not transport_ok:
        overall_status = "FAILED"
    elif mast_errors or page_errors:
        overall_status = "PARTIAL"

    manifest: dict[str, Any] = {
        "manifest_version": "1.0.0",
        "status": overall_status,
        "mission": "ROMAN",
        "domain": "ASTRONOMICAL_OBSERVATORY",
        "mission_phase": mission_phase,
        "checked_utc": now.isoformat(),
        "launch_utc": launch.isoformat(),
        "hours_to_launch": hours_to_launch,
        "archive_state": archive_state,
        "flight_science_data_processed": False,
        "science_claims_enabled": False,
        "l1_plasma_physics_allowed": False,
        "chi_B24M_allowed": False,
        "mast": {
            "invoke_url": mast_config["invoke_url"],
            "mission_list": missions,
            "mission_response": mission_response_summary,
            "collection_counts": collection_counts,
            "collection_responses": collection_response_summaries,
            "sample_row_counts": {
                key: len(value) for key, value in sample_rows.items()
            },
            "errors": mast_errors,
        },
        "official_pages": {
            "results": page_summaries,
            "errors": page_errors,
        },
        "roman_research_nexus": {
            "url": contract["nexus"]["url"],
            "access": contract["nexus"]["access"],
            "automated_public_scrape": False,
            "status": "MANUAL_OR_AUTHENTICATED_NEXUS_PATH",
        },
        "synthetic_fixture": {
            "status": "SUCCESS",
            "fixture_class": fixture.metrics["fixture_class"],
            "official_roman_data": False,
            "metrics": fixture.metrics,
            "products": {
                "data": str(fixture.data_path.relative_to(outdir)),
                "truth": str(fixture.truth_path.relative_to(outdir)),
                "metrics": str(fixture.metrics_path.relative_to(outdir)),
                "chart": str(fixture.chart_path.relative_to(outdir)),
            },
        },
        "next_state_triggers": [
            "Roman appears in Mast.Missions.List",
            "a configured Roman obs_collection returns one or more CAOM rows",
            "an authenticated Roman Research Nexus export is supplied to NVCPP",
            "an official Roman public product contract is frozen",
        ],
        "interpretation_limits": [
            "Roman is an L2 astronomical observatory and is never an L1 plasma source.",
            "The local deterministic fixture is not Roman I-Sim output or flight data.",
            "No MAST archive result establishes launch or commissioning success.",
            "No absence from MAST is interpreted as an instrument failure.",
        ],
    }

    manifest_path = _write_json(outdir / "roman_readiness_manifest.json", manifest)
    contract_copy = outdir / "roman_prelaunch_contract.json"
    contract_copy.write_text(
        json.dumps(contract, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    report = [
        "# NVCPP Roman Readiness Watch",
        "",
        f"- **Status:** `{overall_status}`",
        f"- **Mission phase:** `{mission_phase}`",
        f"- **Archive state:** `{archive_state}`",
        f"- **Checked UTC:** {now.isoformat()}",
        f"- **Scheduled launch UTC:** {launch.isoformat()}",
        f"- **Hours to scheduled launch:** {hours_to_launch:.3f}",
        f"- **MAST transport available:** {transport_ok}",
        f"- **Official page successes:** {len(page_summaries)}",
        f"- **Synthetic fixture:** `{fixture.metrics['fixture_class']}`",
        "",
        "## What ran",
        "",
        "1. Public MAST mission-list discovery.",
        "2. Bounded CAOM row counts for configured Roman collection names.",
        "3. Bounded metadata sampling only when public Roman rows exist.",
        "4. Official NASA/STScI page reachability and exact response hashing.",
        "5. A deterministic Roman-like image fixture through NVCPP's image checks.",
        "",
        "## What did not run",
        "",
        "- No authenticated scraping of the Roman Research Nexus.",
        "- No bulk WFI detector-test download.",
        "- No Roman flight-data calibration.",
        "- No L1 plasma equations or `chi_B24M` labeling.",
        "",
        "See `roman_readiness_manifest.json` for machine-readable evidence.",
    ]
    (reports_dir / "ROMAN_READINESS.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )
    (reports_dir / "ROMAN_MAST_ARCHIVE.md").write_text(
        _archive_report(
            archive_state=archive_state,
            missions=missions,
            collection_counts=collection_counts,
            sample_rows=sample_rows,
        ),
        encoding="utf-8",
    )

    inventory: list[dict[str, Any]] = []
    for path in sorted(outdir.rglob("*")):
        if path.is_file() and path != manifest_path:
            inventory.append(
                {
                    "path": str(path.relative_to(outdir)),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    manifest["artifact_inventory"] = inventory
    _write_json(manifest_path, manifest)

    if overall_status == "FAILED":
        raise MastError("Roman readiness probe could not reach MAST")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the NVCPP Roman prelaunch/archive readiness probe"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/roman_prelaunch.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/roman/readiness"),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional deterministic UTC timestamp for replay tests",
    )
    args = parser.parse_args()
    try:
        manifest = run_probe(
            config_path=args.config,
            outdir=args.outdir,
            now_value=args.now,
        )
    except (RomanContractError, MastError, ValueError) as exc:
        print(f"[NVCPP-ROMAN-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(
        {
            "status": manifest["status"],
            "mission_phase": manifest["mission_phase"],
            "archive_state": manifest["archive_state"],
            "hours_to_launch": manifest["hours_to_launch"],
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
