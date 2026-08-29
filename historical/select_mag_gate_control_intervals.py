#!/usr/bin/env python3
"""Select MAG-gate control intervals before retrieving spacecraft-specific gates.

Low- and middle-activity dates are selected deterministically from a frozen NASA
OMNI search pool.  The selector never reads DSCOVR, ACE, or Wind gate outputs.
Fixed shock/event controls come from predeclared external NASA event metadata.
The resulting interval registry is evidence, not a physical classification.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
SELECTOR_VERSION = "1.0.0"


class ControlSelectionError(RuntimeError):
    """Raised when the independent interval selector cannot proceed safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "contract_id",
        "contract_version",
        "selection_source",
        "search_window",
        "eligibility",
        "ranking",
        "fixed_event_controls",
        "selection_limits",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise ControlSelectionError(
            f"control-selection contract lacks keys: {missing}"
        )
    source = contract["selection_source"]
    if source.get("dataset_id") != "OMNI_HRO2_1MIN":
        raise ControlSelectionError("selector dataset must remain OMNI_HRO2_1MIN")
    parameters = source.get("parameters")
    if parameters != ["percent_interp", "F", "flow_speed", "SYM_H"]:
        raise ControlSelectionError(
            "selector parameters changed from the frozen v1 inventory"
        )
    search = contract["search_window"]
    start = pd.Timestamp(search["start_utc"])
    stop = pd.Timestamp(search["stop_utc"])
    if start.tzinfo is None or stop.tzinfo is None or not start < stop:
        raise ControlSelectionError("search window must be increasing UTC")
    ranking = contract["ranking"]
    expected_components = [
        "abs_daily_min_SYM_H",
        "daily_SYM_H_interdecile_range",
        "daily_F_interdecile_range",
        "daily_flow_speed_interdecile_range",
    ]
    if ranking.get("components") != expected_components:
        raise ControlSelectionError("frozen ranking components changed")
    if int(ranking["quiet_count"]) < 1 or int(ranking["moderate_count"]) < 1:
        raise ControlSelectionError("selector requires quiet and moderate dates")
    if int(ranking["minimum_spacing_days"]) < 1:
        raise ControlSelectionError("spacing must be positive")
    return contract


def request_required(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str],
    timeout: int = 180,
) -> requests.Response:
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response


def fetch_info(
    session: requests.Session,
    *,
    dataset_id: str,
    outdir: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    response = request_required(
        session,
        f"{HAPI_BASE}/info",
        params={"id": dataset_id},
        timeout=60,
    )
    raw = response.content
    path = outdir / "omni_hapi_info.json"
    path.write_bytes(raw)
    try:
        info = response.json()
    except ValueError as exc:
        raise ControlSelectionError("OMNI HAPI /info is not JSON") from exc
    if info.get("status", {}).get("code") != 1200:
        raise ControlSelectionError(
            f"OMNI HAPI /info status is not 1200: {info.get('status')}"
        )
    parameter_map = {
        item["name"]: item
        for item in info.get("parameters", [])
        if isinstance(item, dict) and item.get("name")
    }
    return info, parameter_map, {
        "path": str(path),
        "resolved_url": response.url,
        "sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def chunk_bounds(
    start: pd.Timestamp,
    stop: pd.Timestamp,
    *,
    days: int,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if days < 1:
        raise ControlSelectionError("chunk_days must be positive")
    result: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while cursor < stop:
        end = min(cursor + pd.Timedelta(days=days), stop)
        result.append((cursor, end))
        cursor = end
    return result


def hapi_time(value: pd.Timestamp) -> str:
    return value.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_chunk(
    raw: bytes,
    *,
    parameters: list[str],
    parameter_map: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    rows = [
        row
        for row in csv.reader(io.StringIO(raw.decode("utf-8-sig")))
        if row
    ]
    expected_columns = 1 + len(parameters)
    bad = [
        index
        for index, row in enumerate(rows, start=1)
        if len(row) != expected_columns
    ]
    if bad:
        raise ControlSelectionError(
            f"OMNI strict field-count mismatch at rows {bad[:10]}"
        )
    if not rows:
        return pd.DataFrame(columns=["time", *parameters])
    frame = pd.DataFrame(rows, columns=["time", *parameters])
    frame["time"] = pd.to_datetime(
        frame["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if frame["time"].isna().any():
        raise ControlSelectionError("OMNI selector contains invalid timestamps")
    for name in parameters:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
        fill = parameter_map[name].get("fill")
        try:
            fill_value = float(fill) if fill is not None else None
        except (TypeError, ValueError):
            fill_value = None
        if fill_value is not None:
            frame.loc[frame[name] == fill_value, name] = np.nan
        frame.loc[~np.isfinite(frame[name]), name] = np.nan
    return frame


def fetch_search_pool(
    session: requests.Session,
    *,
    dataset_id: str,
    parameters: list[str],
    start: pd.Timestamp,
    stop: pd.Timestamp,
    chunk_days: int,
    parameter_map: dict[str, dict[str, Any]],
    outdir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    records: list[dict[str, Any]] = []
    for index, (chunk_start, chunk_stop) in enumerate(
        chunk_bounds(start, stop, days=chunk_days), start=1
    ):
        response = request_required(
            session,
            f"{HAPI_BASE}/data",
            params={
                "id": dataset_id,
                "time.min": hapi_time(chunk_start),
                "time.max": hapi_time(chunk_stop),
                "parameters": ",".join(parameters),
                "format": "csv",
            },
            timeout=240,
        )
        raw = response.content
        path = outdir / f"omni_chunk_{index:02d}.csv"
        path.write_bytes(raw)
        frame = parse_chunk(
            raw,
            parameters=parameters,
            parameter_map=parameter_map,
        )
        frames.append(frame)
        records.append(
            {
                "chunk": index,
                "start_utc": hapi_time(chunk_start),
                "stop_utc": hapi_time(chunk_stop),
                "resolved_url": response.url,
                "path": str(path),
                "sha256": sha256_bytes(raw),
                "size_bytes": len(raw),
                "rows": int(len(frame)),
            }
        )
    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values("time", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    if combined["time"].duplicated().any():
        duplicates = combined.loc[
            combined["time"].duplicated(keep=False), "time"
        ].head(10)
        raise ControlSelectionError(
            f"OMNI chunks overlap at timestamps: {duplicates.tolist()}"
        )
    return combined, records


def interdecile_range(series: pd.Series) -> float:
    finite = series.dropna()
    if finite.empty:
        return math.nan
    return float(finite.quantile(0.9) - finite.quantile(0.1))


def daily_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    working["day_utc"] = working["time"].dt.floor("D")
    rows: list[dict[str, Any]] = []
    for day, group in working.groupby("day_utc", sort=True):
        rows.append(
            {
                "day_utc": day,
                "rows": int(len(group)),
                "finite_F_minutes": int(group["F"].notna().sum()),
                "finite_flow_speed_minutes": int(
                    group["flow_speed"].notna().sum()
                ),
                "finite_SYM_H_minutes": int(group["SYM_H"].notna().sum()),
                "daily_min_SYM_H_nT": (
                    float(group["SYM_H"].min())
                    if group["SYM_H"].notna().any()
                    else math.nan
                ),
                "daily_max_SYM_H_nT": (
                    float(group["SYM_H"].max())
                    if group["SYM_H"].notna().any()
                    else math.nan
                ),
                "abs_daily_min_SYM_H": (
                    abs(float(group["SYM_H"].min()))
                    if group["SYM_H"].notna().any()
                    else math.nan
                ),
                "daily_SYM_H_interdecile_range": interdecile_range(
                    group["SYM_H"]
                ),
                "daily_F_median_nT": (
                    float(group["F"].median())
                    if group["F"].notna().any()
                    else math.nan
                ),
                "daily_F_interdecile_range": interdecile_range(group["F"]),
                "daily_flow_speed_median_km_s": (
                    float(group["flow_speed"].median())
                    if group["flow_speed"].notna().any()
                    else math.nan
                ),
                "daily_flow_speed_interdecile_range": interdecile_range(
                    group["flow_speed"]
                ),
                "median_percent_interp": (
                    float(group["percent_interp"].median())
                    if group["percent_interp"].notna().any()
                    else math.nan
                ),
                "maximum_percent_interp": (
                    float(group["percent_interp"].max())
                    if group["percent_interp"].notna().any()
                    else math.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def eligible_days(
    metrics: pd.DataFrame,
    *,
    contract: dict[str, Any],
) -> pd.DataFrame:
    rules = contract["eligibility"]
    output = metrics.loc[
        (metrics["finite_F_minutes"] >= int(rules["minimum_minutes_with_finite_F"]))
        & (
            metrics["finite_flow_speed_minutes"]
            >= int(rules["minimum_minutes_with_finite_flow_speed"])
        )
        & (
            metrics["finite_SYM_H_minutes"]
            >= int(rules["minimum_minutes_with_finite_SYM_H"])
        )
        & (
            metrics["daily_min_SYM_H_nT"].abs()
            <= float(rules["maximum_abs_SYM_H_nT"])
        )
    ].copy()
    components = contract["ranking"]["components"]
    if output.empty:
        raise ControlSelectionError("no OMNI days satisfy eligibility")
    if output[components].isna().any().any():
        raise ControlSelectionError("eligible ranking metrics contain NaN")
    for component in components:
        output[f"rank_{component}"] = output[component].rank(
            method="average", pct=True, ascending=True
        )
    output["activity_rank_sum"] = output[
        [f"rank_{component}" for component in components]
    ].sum(axis=1)
    output.sort_values(["activity_rank_sum", "day_utc"], inplace=True)
    output.reset_index(drop=True, inplace=True)
    return output


def far_enough(
    day: pd.Timestamp,
    selected: list[pd.Timestamp],
    *,
    spacing_days: int,
) -> bool:
    return all(abs((day - other).days) >= spacing_days for other in selected)


def select_spaced(
    candidates: pd.DataFrame,
    *,
    count: int,
    spacing_days: int,
    already_selected: list[pd.Timestamp] | None = None,
) -> list[pd.Series]:
    chosen_days = list(already_selected or [])
    chosen_rows: list[pd.Series] = []
    for _, row in candidates.iterrows():
        day = pd.Timestamp(row["day_utc"])
        if not far_enough(day, chosen_days, spacing_days=spacing_days):
            continue
        chosen_rows.append(row)
        chosen_days.append(day)
        if len(chosen_rows) == count:
            break
    if len(chosen_rows) != count:
        raise ControlSelectionError(
            f"could select only {len(chosen_rows)} of {count} spaced days"
        )
    return chosen_rows


def build_selected_registry(
    eligible: pd.DataFrame,
    *,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    ranking = contract["ranking"]
    quiet_rows = select_spaced(
        eligible.sort_values(["activity_rank_sum", "day_utc"]),
        count=int(ranking["quiet_count"]),
        spacing_days=int(ranking["minimum_spacing_days"]),
    )
    quiet_days = [pd.Timestamp(row["day_utc"]) for row in quiet_rows]

    remaining = eligible.loc[~eligible["day_utc"].isin(quiet_days)].copy()
    median_score = float(remaining["activity_rank_sum"].median())
    remaining["distance_from_activity_median"] = (
        remaining["activity_rank_sum"] - median_score
    ).abs()
    remaining.sort_values(
        ["distance_from_activity_median", "day_utc"], inplace=True
    )
    moderate_rows = select_spaced(
        remaining,
        count=int(ranking["moderate_count"]),
        spacing_days=int(ranking["minimum_spacing_days"]),
        already_selected=quiet_days,
    )

    records: list[dict[str, Any]] = []
    for index, row in enumerate(quiet_rows, start=1):
        day = pd.Timestamp(row["day_utc"])
        records.append(
            {
                "interval_id": f"OMNI_LOW_ACTIVITY_{index}_{day:%Y_%m_%d}",
                "class": "LOW_ACTIVITY_OMNI_SELECTED_CONTROL",
                "start_utc": day.isoformat().replace("+00:00", "Z"),
                "stop_utc": (day + pd.Timedelta(days=1)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "selection_metrics": {
                    key: (
                        float(row[key])
                        if isinstance(row[key], (float, np.floating))
                        else int(row[key])
                        if isinstance(row[key], (int, np.integer))
                        else str(row[key])
                    )
                    for key in (
                        "activity_rank_sum",
                        "daily_min_SYM_H_nT",
                        "daily_max_SYM_H_nT",
                        "daily_SYM_H_interdecile_range",
                        "daily_F_median_nT",
                        "daily_F_interdecile_range",
                        "daily_flow_speed_median_km_s",
                        "daily_flow_speed_interdecile_range",
                    )
                },
                "selection_basis": (
                    "lowest independent OMNI activity-rank sum under frozen "
                    "eligibility and spacing rules"
                ),
            }
        )
    for index, row in enumerate(moderate_rows, start=1):
        day = pd.Timestamp(row["day_utc"])
        records.append(
            {
                "interval_id": f"OMNI_MODERATE_ACTIVITY_{index}_{day:%Y_%m_%d}",
                "class": "MODERATE_ACTIVITY_OMNI_SELECTED_CONTROL",
                "start_utc": day.isoformat().replace("+00:00", "Z"),
                "stop_utc": (day + pd.Timedelta(days=1)).isoformat().replace(
                    "+00:00", "Z"
                ),
                "selection_metrics": {
                    key: (
                        float(row[key])
                        if isinstance(row[key], (float, np.floating))
                        else int(row[key])
                        if isinstance(row[key], (int, np.integer))
                        else str(row[key])
                    )
                    for key in (
                        "activity_rank_sum",
                        "distance_from_activity_median",
                        "daily_min_SYM_H_nT",
                        "daily_max_SYM_H_nT",
                        "daily_SYM_H_interdecile_range",
                        "daily_F_median_nT",
                        "daily_F_interdecile_range",
                        "daily_flow_speed_median_km_s",
                        "daily_flow_speed_interdecile_range",
                    )
                },
                "selection_basis": (
                    "closest to independent OMNI activity-rank median after "
                    "quiet-date removal and frozen spacing rules"
                ),
            }
        )
    records.extend(contract["fixed_event_controls"])
    return records


def run_selection(*, contract_path: Path, outdir: Path) -> dict[str, Any]:
    contract = load_contract(contract_path)
    outdir.mkdir(parents=True, exist_ok=True)
    raw_dir = outdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = outdir / "control_interval_selection_manifest.json"
    manifest: dict[str, Any] = {
        "selector_version": SELECTOR_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "runtime": {"python": platform.python_version()},
        "contract": {
            "path": str(contract_path),
            "contract_id": contract["contract_id"],
            "contract_version": contract["contract_version"],
            "sha256": sha256_file(contract_path),
        },
        "spacecraft_gate_data_retrieved": False,
        "selection_limits": contract["selection_limits"],
    }
    write_json(manifest_path, manifest)

    try:
        source = contract["selection_source"]
        search = contract["search_window"]
        start = pd.Timestamp(search["start_utc"]).tz_convert("UTC")
        stop = pd.Timestamp(search["stop_utc"]).tz_convert("UTC")
        parameters = list(source["parameters"])

        session = requests.Session()
        session.headers.update(
            {"User-Agent": f"NVCPP-CONTROL-SELECTOR/{SELECTOR_VERSION}"}
        )
        info, parameter_map, info_record = fetch_info(
            session,
            dataset_id=source["dataset_id"],
            outdir=raw_dir,
        )
        missing = [name for name in parameters if name not in parameter_map]
        if missing:
            raise ControlSelectionError(
                f"OMNI schema lacks selector parameters: {missing}"
            )
        frame, chunks = fetch_search_pool(
            session,
            dataset_id=source["dataset_id"],
            parameters=parameters,
            start=start,
            stop=stop,
            chunk_days=int(search["chunk_days"]),
            parameter_map=parameter_map,
            outdir=raw_dir,
        )
        metrics = daily_metrics(frame)
        metrics_path = outdir / "omni_daily_selection_metrics.csv"
        metrics.to_csv(metrics_path, index=False)
        eligible = eligible_days(metrics, contract=contract)
        eligible_path = outdir / "omni_eligible_ranked_days.csv"
        eligible.to_csv(eligible_path, index=False)
        selected = build_selected_registry(eligible, contract=contract)

        registry = {
            "registry_id": "NVCPP-MAG-GATE-CONTROL-INTERVALS-v1",
            "registry_version": "1.0.0",
            "created_utc": utc_now(),
            "selection_contract_id": contract["contract_id"],
            "selection_contract_sha256": sha256_file(contract_path),
            "gate_outputs_inspected_before_selection": False,
            "intervals": selected,
            "interpretation_limits": contract["selection_limits"],
        }
        registry_path = outdir / "selected_control_intervals.v1.json"
        write_json(registry_path, registry)

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "source": {
                    "provider": source["provider"],
                    "dataset_id": source["dataset_id"],
                    "parameters": parameters,
                    "hapi_startDate": info.get("startDate"),
                    "hapi_stopDate": info.get("stopDate"),
                    "info": info_record,
                    "chunks": chunks,
                },
                "search_rows": int(len(frame)),
                "daily_metric_rows": int(len(metrics)),
                "eligible_day_rows": int(len(eligible)),
                "selected_interval_count": int(len(selected)),
                "selected_interval_ids": [
                    item["interval_id"] for item in selected
                ],
                "registry": {
                    "path": str(registry_path),
                    "sha256": sha256_file(registry_path),
                },
                "spacecraft_gate_data_retrieved": False,
            }
        )
        artifacts = []
        for path in sorted(outdir.rglob("*")):
            if path.is_file() and path != manifest_path:
                artifacts.append(
                    {
                        "path": path.relative_to(outdir).as_posix(),
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                    }
                )
        manifest["artifacts"] = artifacts
        write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(
            {
                "status": "FAILED",
                "completed_utc": utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(manifest_path, manifest)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/mag_gate_control_selection.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/controls/mag_gate_interval_selection"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_selection(contract_path=args.config, outdir=args.outdir)
    print(
        json.dumps(
            {
                "status": result["status"],
                "selected_interval_count": result.get(
                    "selected_interval_count"
                ),
                "spacecraft_gate_data_retrieved": result.get(
                    "spacecraft_gate_data_retrieved"
                ),
                "outdir": str(args.outdir),
            }
        )
    )


if __name__ == "__main__":
    main()
