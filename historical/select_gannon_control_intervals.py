#!/usr/bin/env python3
"""Select independent control intervals without reading magnetic gate results.

The selector uses only externally defined geomagnetic activity and interplanetary
shock records. It never downloads DSCOVR, ACE, or Wind magnetometer data and
therefore cannot choose controls because their frozen 45-degree/25-percent gate
happens to look favorable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

SELECTOR_VERSION = "1.0.0"
GFZ_KP_URL = "https://kp.gfz.de/app/files/Kp_ap_Ap_SN_F107_since_1932.txt"
DONKI_URL = "https://api.nasa.gov/DONKI/IPS"
DEVELOPMENT_START = pd.Timestamp("2024-05-11T00:00:00Z")
DEVELOPMENT_STOP = pd.Timestamp("2024-05-12T00:00:00Z")


class ControlSelectionError(RuntimeError):
    """Raised when independent control selection cannot be completed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def request_bytes(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[bytes, str]:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.content, response.url


def parse_gfz_kp(raw: bytes) -> pd.DataFrame:
    """Parse the definitive GFZ fixed-column daily Kp file fail-closed."""

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw.decode("utf-8", errors="strict").splitlines(), start=1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 15:
            raise ControlSelectionError(
                f"GFZ Kp row {line_number} has fewer than 15 fields"
            )
        try:
            year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
            kp_values = [float(value) for value in parts[7:15]]
        except (TypeError, ValueError) as exc:
            raise ControlSelectionError(
                f"GFZ Kp row {line_number} cannot be parsed"
            ) from exc
        if len(kp_values) != 8:
            raise ControlSelectionError(
                f"GFZ Kp row {line_number} lacks eight 3-hour values"
            )
        if not all(
            math.isfinite(value) and 0.0 <= value <= 9.0
            for value in kp_values
        ):
            raise ControlSelectionError(
                f"GFZ Kp row {line_number} contains invalid Kp values"
            )
        timestamp = pd.Timestamp(year=year, month=month, day=day, tz="UTC")
        rows.append(
            {
                "date_utc": timestamp,
                "kp_max": max(kp_values),
                "kp_mean": sum(kp_values) / 8.0,
                "kp_sum": sum(kp_values),
                "kp_values": kp_values,
            }
        )
    if not rows:
        raise ControlSelectionError("GFZ Kp source yielded no data rows")
    frame = pd.DataFrame(rows).sort_values("date_utc").reset_index(drop=True)
    if frame["date_utc"].duplicated().any():
        raise ControlSelectionError("GFZ Kp source contains duplicate UTC dates")
    return frame


def parse_donki_ips(payloads: Iterable[bytes]) -> pd.DataFrame:
    """Combine NASA DONKI IPS responses and derive nearest-event separation."""

    records: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in payloads:
        try:
            data = json.loads(payload.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlSelectionError(
                "NASA DONKI IPS response is not valid JSON"
            ) from exc
        if not isinstance(data, list):
            raise ControlSelectionError("NASA DONKI IPS response is not a list")
        for item in data:
            if not isinstance(item, dict):
                continue
            event_time = item.get("eventTime")
            if not event_time:
                continue
            timestamp = pd.Timestamp(event_time)
            timestamp = (
                timestamp.tz_localize("UTC")
                if timestamp.tzinfo is None
                else timestamp.tz_convert("UTC")
            )
            activity_id = str(item.get("activityID") or "UNKNOWN")
            records[(activity_id, timestamp.isoformat())] = {
                "activity_id": activity_id,
                "event_time_utc": timestamp,
                "location": item.get("location"),
                "catalog": item.get("catalog"),
                "instruments": item.get("instruments"),
                "linked_events": item.get("linkedEvents"),
            }
    if not records:
        raise ControlSelectionError("NASA DONKI yielded no IPS events")
    frame = (
        pd.DataFrame(records.values())
        .sort_values("event_time_utc")
        .reset_index(drop=True)
    )
    gaps: list[float] = []
    for index, timestamp in enumerate(frame["event_time_utc"]):
        neighbors: list[float] = []
        if index:
            neighbors.append(
                (
                    timestamp - frame.loc[index - 1, "event_time_utc"]
                ).total_seconds()
                / 3600.0
            )
        if index + 1 < len(frame):
            neighbors.append(
                (
                    frame.loc[index + 1, "event_time_utc"] - timestamp
                ).total_seconds()
                / 3600.0
            )
        gaps.append(min(neighbors) if neighbors else float("inf"))
    frame["nearest_ips_gap_hours"] = gaps
    return frame


def spaced_selection(
    candidates: pd.DataFrame,
    *,
    count: int,
    minimum_spacing_days: int,
) -> pd.DataFrame:
    """Select deterministic rows in supplied sort order with date spacing."""

    selected: list[pd.Series] = []
    for _, row in candidates.iterrows():
        timestamp = pd.Timestamp(row["date_utc"])
        if all(
            abs((timestamp - pd.Timestamp(existing["date_utc"])).days)
            >= minimum_spacing_days
            for existing in selected
        ):
            selected.append(row)
        if len(selected) == count:
            break
    if len(selected) < count:
        raise ControlSelectionError(
            f"only {len(selected)} controls met spacing; required {count}"
        )
    return pd.DataFrame(selected).reset_index(drop=True)


def select_kp_controls(
    kp: pd.DataFrame,
    *,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    quiet_count: int,
    moderate_count: int,
    exclusion_dates: list[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = kp.loc[
        (kp["date_utc"] >= start) & (kp["date_utc"] < stop)
    ].copy()
    for excluded in exclusion_dates:
        pool = pool.loc[
            (pool["date_utc"] - excluded).abs() >= pd.Timedelta(days=14)
        ]

    quiet_candidates = pool.loc[
        (pool["kp_max"] <= 1.0) & (pool["kp_mean"] <= 0.50)
    ].sort_values(["kp_max", "kp_mean", "date_utc"])
    quiet = spaced_selection(
        quiet_candidates,
        count=quiet_count,
        minimum_spacing_days=60,
    )
    quiet["control_class"] = "QUIET_KP_SELECTED"
    quiet["selection_rule"] = (
        "kp_max<=1.0; kp_mean<=0.50; rank low then early; >=60d spacing"
    )

    moderate_candidates = pool.loc[
        (pool["kp_max"] >= 3.0)
        & (pool["kp_max"] <= 4.33)
        & (pool["kp_mean"] >= 0.75)
        & (pool["kp_mean"] <= 2.25)
    ].copy()
    moderate_candidates["target_distance"] = (
        moderate_candidates["kp_mean"] - 1.50
    ).abs()
    moderate_candidates.sort_values(
        ["target_distance", "kp_max", "date_utc"], inplace=True
    )
    moderate = spaced_selection(
        moderate_candidates,
        count=moderate_count,
        minimum_spacing_days=60,
    )
    moderate["control_class"] = "MODERATE_KP_SELECTED"
    moderate["selection_rule"] = (
        "3.0<=kp_max<=4.33; 0.75<=kp_mean<=2.25; "
        "rank |mean-1.5| then early; >=60d spacing"
    )
    return quiet, moderate


def select_isolated_ips_controls(
    ips: pd.DataFrame,
    *,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    count: int,
) -> pd.DataFrame:
    candidates = ips.loc[
        (ips["event_time_utc"] >= start)
        & (ips["event_time_utc"] < stop)
        & (ips["nearest_ips_gap_hours"] >= 36.0)
    ].copy()
    location = candidates["location"].astype(str).str.upper()
    earth = candidates.loc[location.str.contains("EARTH", na=False)]
    if not earth.empty:
        candidates = earth
    candidates["date_utc"] = candidates["event_time_utc"].dt.floor("D")
    candidates.sort_values(
        ["nearest_ips_gap_hours", "event_time_utc"],
        ascending=[False, False],
        inplace=True,
    )
    selected = spaced_selection(
        candidates,
        count=count,
        minimum_spacing_days=60,
    )
    selected["control_class"] = "ISOLATED_DONKI_IPS_SELECTED"
    selected["selection_rule"] = (
        "NASA DONKI Earth IPS; >=36h to nearest listed IPS; "
        "rank isolation then recent; >=60d spacing"
    )
    return selected


def interval_record(row: pd.Series) -> dict[str, Any]:
    control_class = str(row["control_class"])
    if control_class == "ISOLATED_DONKI_IPS_SELECTED":
        event_time = pd.Timestamp(row["event_time_utc"]).tz_convert("UTC")
        start = event_time - pd.Timedelta(hours=12)
        stop = event_time + pd.Timedelta(hours=12)
        identifier = f"ips_{event_time.strftime('%Y%m%dT%H%MZ')}"
        independent_evidence = {
            "activity_id": row.get("activity_id"),
            "event_time_utc": event_time.isoformat(),
            "nearest_ips_gap_hours": float(row["nearest_ips_gap_hours"]),
            "location": row.get("location"),
            "catalog": row.get("catalog"),
        }
    else:
        start = pd.Timestamp(row["date_utc"]).tz_convert("UTC")
        stop = start + pd.Timedelta(days=1)
        prefix = "quiet" if control_class.startswith("QUIET") else "moderate"
        identifier = f"{prefix}_{start.strftime('%Y%m%d')}"
        independent_evidence = {
            "date_utc": start.isoformat(),
            "kp_max": float(row["kp_max"]),
            "kp_mean": float(row["kp_mean"]),
            "kp_values": [float(value) for value in row["kp_values"]],
        }
    return {
        "control_id": identifier,
        "control_class": control_class,
        "start_utc": start.isoformat(),
        "stop_utc": stop.isoformat(),
        "selection_rule": row["selection_rule"],
        "independent_selection_evidence": independent_evidence,
        "magnetic_gate_data_read_during_selection": False,
    }


def run_selection(*, outdir: Path, api_key: str) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    raw_dir = outdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-CONTROL-SELECTOR/{SELECTOR_VERSION}"}
    )

    gfz_raw, gfz_resolved = request_bytes(session, GFZ_KP_URL)
    gfz_path = raw_dir / "gfz_kp.txt"
    gfz_path.write_bytes(gfz_raw)
    kp = parse_gfz_kp(gfz_raw)

    donki_payloads: list[bytes] = []
    donki_sources: list[dict[str, Any]] = []
    for year in (2022, 2023, 2024):
        end = f"{year}-12-31" if year < 2024 else "2024-04-30"
        raw, resolved = request_bytes(
            session,
            DONKI_URL,
            params={
                "startDate": f"{year}-01-01",
                "endDate": end,
                "location": "Earth",
                "catalog": "ALL",
                "api_key": api_key,
            },
        )
        path = raw_dir / f"donki_ips_{year}.json"
        path.write_bytes(raw)
        donki_payloads.append(raw)
        donki_sources.append(
            {
                "year": year,
                "path": str(path),
                "resolved_url": resolved.replace(api_key, "REDACTED"),
                "sha256": sha256_bytes(raw),
                "size_bytes": len(raw),
            }
        )
    ips = parse_donki_ips(donki_payloads)

    quiet, moderate = select_kp_controls(
        kp,
        start=pd.Timestamp("2019-01-01T00:00:00Z"),
        stop=pd.Timestamp("2024-05-01T00:00:00Z"),
        quiet_count=3,
        moderate_count=3,
        exclusion_dates=[DEVELOPMENT_START],
    )
    isolated = select_isolated_ips_controls(
        ips,
        start=pd.Timestamp("2022-01-01T00:00:00Z"),
        stop=pd.Timestamp("2024-05-01T00:00:00Z"),
        count=3,
    )

    controls = [
        interval_record(row)
        for frame in (quiet, moderate, isolated)
        for _, row in frame.iterrows()
    ]
    registry = {
        "registry_id": "NVCPP-GANNON-CONTROL-REGISTRY-PROPOSAL-v1",
        "registry_version": "1.0.0",
        "created_utc": utc_now(),
        "status": "PROPOSED_SELECTION_REQUIRES_FREEZE",
        "development_interval": {
            "control_id": "gannon_20240511_development",
            "control_class": "DISTURBED_DEVELOPMENT_EVENT",
            "start_utc": DEVELOPMENT_START.isoformat(),
            "stop_utc": DEVELOPMENT_STOP.isoformat(),
            "candidate_utc": "2024-05-11T10:59:00+00:00",
        },
        "controls": controls,
        "selection_firewall": {
            "magnetic_data_accessed": False,
            "gate_thresholds_accessed": False,
            "gate_results_accessed": False,
            "selection_inputs": ["GFZ_KP", "NASA_DONKI_IPS"],
        },
    }
    registry_path = outdir / "proposed_control_registry.json"
    write_json(registry_path, registry)

    manifest = {
        "selector_version": SELECTOR_VERSION,
        "status": "SUCCESS",
        "completed_utc": utc_now(),
        "sources": {
            "GFZ_KP": {
                "url": GFZ_KP_URL,
                "resolved_url": gfz_resolved,
                "path": str(gfz_path),
                "sha256": sha256_bytes(gfz_raw),
                "size_bytes": len(gfz_raw),
                "parsed_daily_rows": int(len(kp)),
            },
            "NASA_DONKI_IPS": donki_sources,
        },
        "selected_counts": {
            "quiet": int(len(quiet)),
            "moderate": int(len(moderate)),
            "isolated_ips": int(len(isolated)),
        },
        "registry": {
            "path": str(registry_path),
            "sha256": sha256_bytes(registry_path.read_bytes()),
        },
        "magnetic_data_accessed": False,
        "physics_computed": False,
    }
    write_json(outdir / "control_selection_manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/gannon_control_selection"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY") or "DEMO_KEY"
    result = run_selection(outdir=args.outdir, api_key=api_key)
    print(json.dumps({"status": result["status"], **result["selected_counts"]}))


if __name__ == "__main__":
    main()
