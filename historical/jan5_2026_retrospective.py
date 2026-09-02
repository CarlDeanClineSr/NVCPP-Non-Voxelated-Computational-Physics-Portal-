#!/usr/bin/env python3
"""Fail-closed January 5, 2026 JWST/L1 retrospective audit.

This program acquires immutable historical measurements, preserves native
responses with hashes, produces canonical tables, and reports candidate
structures without assigning a mechanism.

It deliberately does not:
- use NOAA rolling-current products as January 5 history;
- calculate or enforce the historical LUFT chi proxy;
- assume a fixed L1-to-JWST propagation delay;
- substitute simulation or demo values;
- modify or consume the sealed Gannon V2 holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

UTC = timezone.utc
USER_AGENT = "NVCPP-JWST-Jan5-Retrospective/1.0"


# ---------------------------------------------------------------------------
# Basic provenance helpers
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: str | pd.Timestamp | datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "item"


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


@dataclass
class ManifestRecord:
    source_id: str
    source_kind: str
    request_method: str
    request_url: str
    request_payload: dict[str, Any] | None
    retrieved_at_utc: str
    http_status: int | None
    response_sha256: str | None
    response_bytes: int
    raw_path: str | None
    status: str
    detail: str = ""


class EvidenceStore:
    """Write raw evidence and a machine-readable request manifest."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.raw_dir = output_dir / "raw"
        self.canonical_dir = output_dir / "canonical"
        self.tables_dir = output_dir / "tables"
        self.reports_dir = output_dir / "reports"
        for path in (
            self.raw_dir,
            self.canonical_dir,
            self.tables_dir,
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.records: list[ManifestRecord] = []

    def preserve_response(
        self,
        *,
        source_id: str,
        source_kind: str,
        method: str,
        request_url: str,
        request_payload: dict[str, Any] | None,
        response: requests.Response | None,
        raw_relative_path: str,
        status: str,
        detail: str = "",
    ) -> bytes:
        payload = response.content if response is not None else b""
        raw_path: Path | None = None
        digest: str | None = None
        if payload:
            raw_path = self.raw_dir / raw_relative_path
            write_bytes(raw_path, payload)
            digest = sha256_bytes(payload)
        self.records.append(
            ManifestRecord(
                source_id=source_id,
                source_kind=source_kind,
                request_method=method,
                request_url=request_url,
                request_payload=request_payload,
                retrieved_at_utc=utc_now(),
                http_status=response.status_code if response is not None else None,
                response_sha256=digest,
                response_bytes=len(payload),
                raw_path=(
                    str(raw_path.relative_to(self.output_dir)) if raw_path else None
                ),
                status=status,
                detail=detail,
            )
        )
        return payload

    def record_failure(
        self,
        *,
        source_id: str,
        source_kind: str,
        method: str,
        request_url: str,
        request_payload: dict[str, Any] | None,
        detail: str,
    ) -> None:
        self.records.append(
            ManifestRecord(
                source_id=source_id,
                source_kind=source_kind,
                request_method=method,
                request_url=request_url,
                request_payload=request_payload,
                retrieved_at_utc=utc_now(),
                http_status=None,
                response_sha256=None,
                response_bytes=0,
                raw_path=None,
                status="REQUEST_ERROR",
                detail=detail,
            )
        )

    def finalize(self, config_path: Path, config: dict[str, Any]) -> None:
        config_bytes = config_path.read_bytes()
        manifest = {
            "study_id": config["study_id"],
            "protocol_version": config["protocol_version"],
            "generated_at_utc": utc_now(),
            "runtime": {
                "python": sys.version,
                "platform": platform.platform(),
                "pandas": pd.__version__,
                "numpy": np.__version__,
                "requests": requests.__version__,
            },
            "config": {
                "path": str(config_path),
                "sha256": sha256_bytes(config_bytes),
            },
            "simulation_used": False,
            "demo_data_used": False,
            "gannon_v2_holdout_accessed": False,
            "requests": [record.__dict__ for record in self.records],
        }
        write_json(self.output_dir / "manifest.json", manifest)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def request_with_retries(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: int = 90,
    attempts: int = 3,
) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# CDAWeb HAPI acquisition
# ---------------------------------------------------------------------------


def choose_hapi_time_parameter(info: dict[str, Any]) -> str:
    parameters = info.get("parameters", [])
    for item in parameters:
        if str(item.get("type", "")).lower() == "isotime":
            return str(item["name"])
    for item in parameters:
        if str(item.get("name", "")).lower() in {"time", "epoch"}:
            return str(item["name"])
    raise ValueError("HAPI info has no identifiable time parameter")


def parameter_is_three_vector(item: dict[str, Any]) -> bool:
    size = item.get("size")
    if size == 3 or size == [3]:
        return True
    if isinstance(size, list) and int(np.prod(size)) == 3:
        return True
    return False


def choose_hapi_vector_parameter(
    info: dict[str, Any], preferred: Sequence[str]
) -> dict[str, Any]:
    parameters = info.get("parameters", [])
    by_name = {str(item.get("name", "")): item for item in parameters}
    for name in preferred:
        if name in by_name and parameter_is_three_vector(by_name[name]):
            return by_name[name]
    lowered = {key.lower(): value for key, value in by_name.items()}
    for name in preferred:
        item = lowered.get(name.lower())
        if item is not None and parameter_is_three_vector(item):
            return item
    candidates = [
        item
        for item in parameters
        if parameter_is_three_vector(item)
        and "nt" in str(item.get("units", "")).lower()
        and "gse" in (
            str(item.get("name", "")) + " " + str(item.get("description", ""))
        ).lower()
    ]
    if len(candidates) == 1:
        return candidates[0]
    names = [str(item.get("name", "")) for item in candidates]
    raise ValueError(
        "Could not uniquely identify a 3-component GSE magnetic vector; "
        f"candidates={names}"
    )


def flatten_vector(value: Any) -> tuple[float, float, float] | None:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        flat = np.asarray(value, dtype=object).reshape(-1).tolist()
    elif isinstance(value, str):
        cleaned = value.strip().strip("[]()")
        flat = re.split(r"[\s,;]+", cleaned) if cleaned else []
    else:
        return None
    if len(flat) != 3:
        return None
    try:
        xyz = tuple(float(item) for item in flat)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in xyz):
        return None
    return xyz  # type: ignore[return-value]


def is_fill_vector(
    vector: tuple[float, float, float], parameter_info: dict[str, Any]
) -> bool:
    fill = parameter_info.get("fill")
    if fill is None:
        return False
    fill_values: list[float] = []
    if isinstance(fill, list):
        for item in fill:
            try:
                fill_values.append(float(item))
            except (TypeError, ValueError):
                continue
    else:
        try:
            fill_values.append(float(fill))
        except (TypeError, ValueError):
            return False
    return any(all(np.isclose(component, item) for component in vector) for item in fill_values)


def parse_hapi_vector_data(
    payload: dict[str, Any],
    *,
    source_id: str,
    time_parameter: str,
    vector_parameter: str,
    vector_info: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    parameters = payload.get("parameters") or []
    parameter_names = [str(item.get("name", "")) for item in parameters]
    if not parameter_names:
        parameter_names = [time_parameter, vector_parameter]
    try:
        time_index = parameter_names.index(time_parameter)
        vector_index = parameter_names.index(vector_parameter)
    except ValueError as exc:
        raise ValueError(
            f"HAPI response parameters do not contain {time_parameter}/{vector_parameter}: "
            f"{parameter_names}"
        ) from exc

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row_number, row in enumerate(payload.get("data", []), start=1):
        if not isinstance(row, list) or len(row) <= max(time_index, vector_index):
            rejected.append(
                {
                    "source_id": source_id,
                    "row_number": row_number,
                    "reason": "MALFORMED_HAPI_ROW",
                }
            )
            continue
        timestamp = pd.to_datetime(row[time_index], errors="coerce", utc=True)
        vector = flatten_vector(row[vector_index])
        if pd.isna(timestamp):
            rejected.append(
                {
                    "source_id": source_id,
                    "row_number": row_number,
                    "reason": "INVALID_TIME",
                }
            )
            continue
        if vector is None:
            rejected.append(
                {
                    "source_id": source_id,
                    "row_number": row_number,
                    "time_raw": str(row[time_index]),
                    "reason": "INVALID_VECTOR",
                }
            )
            continue
        if is_fill_vector(vector, vector_info):
            rejected.append(
                {
                    "source_id": source_id,
                    "row_number": row_number,
                    "time_utc": timestamp.isoformat(),
                    "reason": "FILL_VECTOR",
                }
            )
            continue
        bx, by, bz = vector
        bmag = float(math.sqrt(bx * bx + by * by + bz * bz))
        if not math.isfinite(bmag) or bmag <= 0:
            rejected.append(
                {
                    "source_id": source_id,
                    "row_number": row_number,
                    "time_utc": timestamp.isoformat(),
                    "reason": "NONPOSITIVE_MAGNITUDE",
                }
            )
            continue
        accepted.append(
            {
                "source_id": source_id,
                "time_utc": timestamp,
                "bx_gse_nT": bx,
                "by_gse_nT": by,
                "bz_gse_nT": bz,
                "bmag_nT": bmag,
                "native_row_number": row_number,
            }
        )
    accepted_df = pd.DataFrame(accepted)
    rejected_df = pd.DataFrame(rejected)
    if not accepted_df.empty:
        accepted_df = accepted_df.sort_values("time_utc").drop_duplicates(
            ["source_id", "time_utc"], keep="last"
        )
    return accepted_df, rejected_df


def acquire_cdaweb_magnetic(
    config: dict[str, Any], store: EvidenceStore
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    hapi_config = config["cdaweb_hapi"]
    base_url = hapi_config["base_url"].rstrip("/")
    start = config["analysis_interval"]["start_utc"]
    end = config["analysis_interval"]["end_utc"]
    canonical_frames: list[pd.DataFrame] = []
    reject_frames: list[pd.DataFrame] = []
    statuses: list[dict[str, Any]] = []

    for source in hapi_config["datasets"]:
        source_id = source["source_id"]
        dataset_id = source["dataset_id"]
        info_url = f"{base_url}/info"
        info_params = {"id": dataset_id}
        try:
            info_response = request_with_retries("GET", info_url, params=info_params)
            info_payload = store.preserve_response(
                source_id=source_id,
                source_kind="CDAWEB_HAPI_INFO",
                method="GET",
                request_url=f"{info_url}?{urlencode(info_params)}",
                request_payload=None,
                response=info_response,
                raw_relative_path=f"cdaweb/{safe_name(source_id)}_info.json",
                status="HTTP_OK",
            )
            info = json.loads(info_payload)
            time_parameter = choose_hapi_time_parameter(info)
            vector_info = choose_hapi_vector_parameter(
                info, source["preferred_vector_parameters"]
            )
            vector_parameter = str(vector_info["name"])
        except Exception as exc:  # source-specific failure must not stop other sources
            store.record_failure(
                source_id=source_id,
                source_kind="CDAWEB_HAPI_INFO",
                method="GET",
                request_url=f"{info_url}?{urlencode(info_params)}",
                request_payload=None,
                detail=repr(exc),
            )
            statuses.append(
                {
                    "source_id": source_id,
                    "status": "INCOMPLETE_SOURCE",
                    "detail": f"HAPI info failed: {exc}",
                }
            )
            continue

        data_url = f"{base_url}/data"
        data_params = {
            "id": dataset_id,
            "parameters": f"{time_parameter},{vector_parameter}",
            "time.min": start,
            "time.max": end,
            "format": "json",
        }
        try:
            data_response = request_with_retries(
                "GET", data_url, params=data_params, timeout=180
            )
            data_payload = store.preserve_response(
                source_id=source_id,
                source_kind="CDAWEB_HAPI_DATA",
                method="GET",
                request_url=f"{data_url}?{urlencode(data_params)}",
                request_payload=None,
                response=data_response,
                raw_relative_path=f"cdaweb/{safe_name(source_id)}_data.json",
                status="HTTP_OK",
            )
            parsed = json.loads(data_payload)
            accepted, rejected = parse_hapi_vector_data(
                parsed,
                source_id=source_id,
                time_parameter=time_parameter,
                vector_parameter=vector_parameter,
                vector_info=vector_info,
            )
            if accepted.empty:
                raise ValueError("Historical HAPI response yielded zero canonical vectors")
            canonical_frames.append(accepted)
            if not rejected.empty:
                reject_frames.append(rejected)
            statuses.append(
                {
                    "source_id": source_id,
                    "status": "CANONICALIZED",
                    "dataset_id": dataset_id,
                    "time_parameter": time_parameter,
                    "vector_parameter": vector_parameter,
                    "accepted_rows": int(len(accepted)),
                    "rejected_rows": int(len(rejected)),
                }
            )
        except Exception as exc:
            store.record_failure(
                source_id=source_id,
                source_kind="CDAWEB_HAPI_DATA",
                method="GET",
                request_url=f"{data_url}?{urlencode(data_params)}",
                request_payload=None,
                detail=repr(exc),
            )
            statuses.append(
                {
                    "source_id": source_id,
                    "status": "INCOMPLETE_SOURCE",
                    "detail": f"HAPI data failed: {exc}",
                }
            )

    canonical = (
        pd.concat(canonical_frames, ignore_index=True)
        if canonical_frames
        else pd.DataFrame()
    )
    rejected = (
        pd.concat(reject_frames, ignore_index=True) if reject_frames else pd.DataFrame()
    )
    return canonical, rejected, statuses


# ---------------------------------------------------------------------------
# Magnetic candidate detector
# ---------------------------------------------------------------------------


def vector_rotation_degrees(previous: np.ndarray, current: np.ndarray) -> float:
    previous_norm = float(np.linalg.norm(previous))
    current_norm = float(np.linalg.norm(current))
    if previous_norm <= 0 or current_norm <= 0:
        return float("nan")
    cosine = float(np.dot(previous, current) / (previous_norm * current_norm))
    return float(math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0)))))


def detect_l1_candidates(
    canonical: pd.DataFrame, detector: dict[str, Any]
) -> pd.DataFrame:
    if canonical.empty:
        return pd.DataFrame()
    output: list[dict[str, Any]] = []
    for source_id, frame in canonical.groupby("source_id", sort=True):
        frame = frame.sort_values("time_utc").reset_index(drop=True)
        previous: pd.Series | None = None
        for _, row in frame.iterrows():
            if previous is None:
                previous = row
                continue
            dt_seconds = float((row["time_utc"] - previous["time_utc"]).total_seconds())
            previous_mag = float(previous["bmag_nT"])
            current_mag = float(row["bmag_nT"])
            if dt_seconds <= 0 or dt_seconds > float(detector["maximum_pair_gap_seconds"]):
                previous = row
                continue
            if previous_mag < float(detector["minimum_previous_magnitude_nT"]):
                previous = row
                continue
            previous_vector = previous[["bx_gse_nT", "by_gse_nT", "bz_gse_nT"]].to_numpy(
                dtype=float
            )
            current_vector = row[["bx_gse_nT", "by_gse_nT", "bz_gse_nT"]].to_numpy(
                dtype=float
            )
            rotation = vector_rotation_degrees(previous_vector, current_vector)
            relative_change = abs(current_mag - previous_mag) / previous_mag
            rotation_ratio = rotation / float(detector["minimum_vector_rotation_deg"])
            magnitude_ratio = relative_change / float(
                detector["minimum_relative_magnitude_change"]
            )
            score = float(max(rotation_ratio, magnitude_ratio))
            candidate = bool(rotation_ratio >= 1.0 or magnitude_ratio >= 1.0)
            output.append(
                {
                    "source_id": source_id,
                    "time_previous_utc": previous["time_utc"],
                    "time_current_utc": row["time_utc"],
                    "pair_gap_seconds": dt_seconds,
                    "previous_bmag_nT": previous_mag,
                    "current_bmag_nT": current_mag,
                    "vector_rotation_deg": rotation,
                    "relative_magnitude_change": relative_change,
                    "detector_score": score,
                    "candidate": candidate,
                }
            )
            previous = row
    return pd.DataFrame(output)


# ---------------------------------------------------------------------------
# Frozen windows and controls
# ---------------------------------------------------------------------------


def window_duration(start: pd.Timestamp, end: pd.Timestamp) -> pd.Timedelta:
    if end <= start:
        raise ValueError(f"Invalid window: {start} through {end}")
    return end - start


def combine_date_and_clock(control_date: str, timestamp: pd.Timestamp) -> pd.Timestamp:
    day = date.fromisoformat(control_date)
    clock = timestamp.tz_convert("UTC").time().replace(tzinfo=None)
    return pd.Timestamp(datetime.combine(day, clock, tzinfo=UTC))


def declared_windows(config: dict[str, Any]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for event in config["predeclared_event_windows"]:
        start = parse_utc(event["start_utc"])
        end = parse_utc(event["end_utc"])
        window_duration(start, end)
        windows.append(
            {
                "window_id": event["id"],
                "window_role": "EVENT",
                "parent_event_id": event["id"],
                "start_utc": start,
                "end_utc": end,
            }
        )
        for control_date in config["control_dates"]:
            control_start = combine_date_and_clock(control_date, start)
            control_end = control_start + (end - start)
            windows.append(
                {
                    "window_id": f"{event['id']}__CONTROL__{control_date}",
                    "window_role": "CONTROL",
                    "parent_event_id": event["id"],
                    "start_utc": control_start,
                    "end_utc": control_end,
                }
            )
    return windows


def minute_coverage(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    expected = max(1, int(math.ceil((end - start).total_seconds() / 60.0)))
    if frame.empty:
        return 0.0
    minutes = frame["time_utc"].dt.floor("min").nunique()
    return min(1.0, float(minutes) / float(expected))


def summarize_l1_windows(
    canonical: pd.DataFrame,
    pairs: pd.DataFrame,
    windows: Sequence[dict[str, Any]],
) -> pd.DataFrame:
    source_ids = sorted(canonical["source_id"].unique()) if not canonical.empty else []
    rows: list[dict[str, Any]] = []
    for window in windows:
        for source_id in source_ids:
            source_samples = canonical[canonical["source_id"] == source_id]
            sample_subset = source_samples[
                (source_samples["time_utc"] >= window["start_utc"])
                & (source_samples["time_utc"] < window["end_utc"])
            ]
            source_pairs = pairs[pairs["source_id"] == source_id] if not pairs.empty else pairs
            pair_subset = source_pairs[
                (source_pairs["time_current_utc"] >= window["start_utc"])
                & (source_pairs["time_current_utc"] < window["end_utc"])
            ] if not source_pairs.empty else source_pairs
            candidate_subset = (
                pair_subset[pair_subset["candidate"]] if not pair_subset.empty else pair_subset
            )
            rows.append(
                {
                    **window,
                    "source_id": source_id,
                    "sample_count": int(len(sample_subset)),
                    "minute_coverage": minute_coverage(
                        sample_subset, window["start_utc"], window["end_utc"]
                    ),
                    "candidate_count": int(len(candidate_subset)),
                    "max_detector_score": (
                        float(pair_subset["detector_score"].max())
                        if not pair_subset.empty
                        else np.nan
                    ),
                    "max_vector_rotation_deg": (
                        float(pair_subset["vector_rotation_deg"].max())
                        if not pair_subset.empty
                        else np.nan
                    ),
                    "max_relative_magnitude_change": (
                        float(pair_subset["relative_magnitude_change"].max())
                        if not pair_subset.empty
                        else np.nan
                    ),
                    "minimum_bmag_nT": (
                        float(sample_subset["bmag_nT"].min())
                        if not sample_subset.empty
                        else np.nan
                    ),
                    "maximum_bmag_nT": (
                        float(sample_subset["bmag_nT"].max())
                        if not sample_subset.empty
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def empirical_control_table(window_summary: pd.DataFrame) -> pd.DataFrame:
    if window_summary.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for (event_id, source_id), group in window_summary.groupby(
        ["parent_event_id", "source_id"], sort=True
    ):
        event_rows = group[group["window_role"] == "EVENT"]
        controls = group[group["window_role"] == "CONTROL"]
        if len(event_rows) != 1:
            continue
        event = event_rows.iloc[0]
        event_score = event["max_detector_score"]
        complete_controls = controls[
            (controls["minute_coverage"] >= 0.95)
            & controls["max_detector_score"].notna()
        ]
        incomplete_count = int(len(controls) - len(complete_controls))
        if pd.isna(event_score):
            exceed_complete = 0
            complete_only_fraction = np.nan
            conservative_fraction = 1.0
        else:
            exceed_complete = int(
                (complete_controls["max_detector_score"] >= float(event_score)).sum()
            )
            complete_only_fraction = (1 + exceed_complete) / (1 + len(complete_controls))
            conservative_fraction = (
                1 + exceed_complete + incomplete_count
            ) / (1 + len(controls))
        rows.append(
            {
                "parent_event_id": event_id,
                "source_id": source_id,
                "event_score": event_score,
                "event_minute_coverage": event["minute_coverage"],
                "declared_control_count": int(len(controls)),
                "complete_control_count": int(len(complete_controls)),
                "incomplete_control_count": incomplete_count,
                "controls_at_or_above_event": exceed_complete,
                "complete_only_empirical_exceedance_fraction": complete_only_fraction,
                "conservative_exceedance_upper_bound": conservative_fraction,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# MAST EDB acquisition and conservative canonicalization
# ---------------------------------------------------------------------------


def mast_post(
    store: EvidenceStore,
    *,
    source_id: str,
    source_kind: str,
    url: str,
    payload: dict[str, Any],
    raw_relative_path: str,
) -> dict[str, Any] | None:
    form = {"request": json.dumps(payload, separators=(",", ":"))}
    try:
        response = request_with_retries("POST", url, data=form, timeout=120)
        raw = store.preserve_response(
            source_id=source_id,
            source_kind=source_kind,
            method="POST",
            request_url=url,
            request_payload=payload,
            response=response,
            raw_relative_path=raw_relative_path,
            status="HTTP_OK",
        )
        return json.loads(raw)
    except Exception as exc:
        store.record_failure(
            source_id=source_id,
            source_kind=source_kind,
            method="POST",
            request_url=url,
            request_payload=payload,
            detail=repr(exc),
        )
        return None


def dictionary_confirms_mnemonic(payload: dict[str, Any] | None, mnemonic: str) -> bool:
    if not payload:
        return False
    text = json.dumps(payload, sort_keys=True).lower()
    return mnemonic.lower() in text


def parse_mast_timeseries(
    payload: dict[str, Any] | None,
    *,
    mnemonic: str,
    dictionary_confirmed: bool,
) -> tuple[pd.DataFrame, str]:
    if not payload:
        return pd.DataFrame(), "NO_RESPONSE"
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return pd.DataFrame(), "NO_DATA_ROWS"
    frame = pd.DataFrame(data)
    if frame.empty:
        return pd.DataFrame(), "NO_DATA_ROWS"
    time_candidates = [
        column
        for column in frame.columns
        if str(column).lower() in {"time", "timestamp", "date", "datetime", "mjd"}
        or "time" in str(column).lower()
    ]
    value_candidates = [
        column
        for column in frame.columns
        if str(column).lower() in {"value", "euvalue", "rawvalue", "engineeringvalue"}
        or str(column).lower().endswith("value")
    ]
    if not time_candidates or not value_candidates:
        return pd.DataFrame(), f"UNRECOGNIZED_SCHEMA:{list(frame.columns)}"
    time_column = time_candidates[0]
    value_column = value_candidates[0]
    raw_time = frame[time_column]
    if str(time_column).lower() == "mjd" or (
        pd.api.types.is_numeric_dtype(raw_time)
        and pd.to_numeric(raw_time, errors="coerce").median() > 40000
    ):
        parsed_time = pd.to_datetime(
            pd.to_numeric(raw_time, errors="coerce"),
            unit="D",
            origin="1858-11-17",
            utc=True,
        )
    else:
        parsed_time = pd.to_datetime(raw_time, errors="coerce", utc=True)
    values = pd.to_numeric(frame[value_column], errors="coerce")
    output = pd.DataFrame(
        {
            "mnemonic": mnemonic,
            "time_utc": parsed_time,
            "value": values,
            "dictionary_confirmed": bool(dictionary_confirmed),
        }
    ).dropna(subset=["time_utc", "value"])
    output = output.sort_values("time_utc").drop_duplicates(
        ["mnemonic", "time_utc"], keep="last"
    )
    return output, "CANONICALIZED" if not output.empty else "NO_NUMERIC_ROWS"


def acquire_mast_edb(
    config: dict[str, Any], store: EvidenceStore
) -> tuple[pd.DataFrame, pd.DataFrame]:
    mast = config["mast"]
    url = mast["invoke_url"]
    start = config["full_day"]["start_utc"]
    end = config["full_day"]["end_utc"]
    frames: list[pd.DataFrame] = []
    status_rows: list[dict[str, Any]] = []

    for mnemonic in mast["edb_mnemonics_to_verify"]:
        dictionary_payload = {
            "service": mast["edb_dictionary_service"],
            "format": "json",
            "params": {"mnemonic": mnemonic},
        }
        dictionary = mast_post(
            store,
            source_id=mnemonic,
            source_kind="MAST_EDB_DICTIONARY",
            url=url,
            payload=dictionary_payload,
            raw_relative_path=f"mast/edb_dictionary_{safe_name(mnemonic)}.json",
        )
        confirmed = dictionary_confirms_mnemonic(dictionary, mnemonic)

        variants = [
            {"mnemonic": mnemonic, "start": start, "end": end},
            {"mnemonic": mnemonic, "starttime": start, "endtime": end},
            {"mnemonic": mnemonic, "startTime": start, "endTime": end},
        ]
        chosen_frame = pd.DataFrame()
        chosen_variant: dict[str, Any] | None = None
        chosen_status = "NO_DATA_ROWS"
        for variant_number, params in enumerate(variants, start=1):
            request_payload = {
                "service": mast["edb_timeseries_service"],
                "format": "json",
                "params": params,
            }
            response_payload = mast_post(
                store,
                source_id=mnemonic,
                source_kind="MAST_EDB_TIMESERIES",
                url=url,
                payload=request_payload,
                raw_relative_path=(
                    f"mast/edb_timeseries_{safe_name(mnemonic)}_"
                    f"variant_{variant_number}.json"
                ),
            )
            candidate_frame, candidate_status = parse_mast_timeseries(
                response_payload,
                mnemonic=mnemonic,
                dictionary_confirmed=confirmed,
            )
            if not candidate_frame.empty:
                chosen_frame = candidate_frame
                chosen_variant = params
                chosen_status = candidate_status
                break
            chosen_status = candidate_status
        if not chosen_frame.empty:
            frames.append(chosen_frame)
        status_rows.append(
            {
                "mnemonic": mnemonic,
                "dictionary_confirmed": confirmed,
                "status": chosen_status,
                "selected_parameter_variant": json.dumps(chosen_variant, sort_keys=True)
                if chosen_variant
                else "",
                "canonical_rows": int(len(chosen_frame)),
            }
        )

    canonical = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return canonical, pd.DataFrame(status_rows)


def robust_zscore(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    median = numeric.median()
    mad = (numeric - median).abs().median()
    if pd.isna(mad) or mad <= 0:
        return pd.Series(np.nan, index=values.index, dtype=float)
    return 0.6744897501960817 * (numeric - median) / mad


def merge_mnemonic_series(
    canonical: pd.DataFrame,
    mnemonics: Sequence[str],
    tolerance_seconds: float,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for mnemonic in mnemonics:
        subset = canonical[canonical["mnemonic"] == mnemonic][
            ["time_utc", "value"]
        ].copy()
        if subset.empty:
            return pd.DataFrame()
        subset = subset.sort_values("time_utc").rename(columns={"value": mnemonic})
        frames.append(subset)
    merged = frames[0]
    for frame in frames[1:]:
        merged = pd.merge_asof(
            merged.sort_values("time_utc"),
            frame.sort_values("time_utc"),
            on="time_utc",
            direction="nearest",
            tolerance=pd.Timedelta(seconds=tolerance_seconds),
        )
    return merged.dropna(subset=list(mnemonics))


def quaternion_angular_steps(
    canonical: pd.DataFrame,
    *,
    mnemonics: Sequence[str],
    tolerance_seconds: float,
) -> pd.DataFrame:
    merged = merge_mnemonic_series(canonical, mnemonics, tolerance_seconds)
    if merged.empty:
        return pd.DataFrame()
    q = merged[list(mnemonics)].to_numpy(dtype=float)
    norms = np.linalg.norm(q, axis=1)
    valid = np.isfinite(norms) & (norms > 0)
    merged = merged.loc[valid].reset_index(drop=True)
    q = q[valid] / norms[valid, None]
    if len(merged) < 2:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for index in range(1, len(merged)):
        dt_seconds = float(
            (merged.loc[index, "time_utc"] - merged.loc[index - 1, "time_utc"])
            .total_seconds()
        )
        if dt_seconds <= 0:
            continue
        # abs(dot) makes q and -q the same physical attitude.
        dot = abs(float(np.dot(q[index - 1], q[index])))
        radians = 2.0 * math.acos(float(np.clip(dot, -1.0, 1.0)))
        arcseconds = radians * 206264.80624709636
        rows.append(
            {
                "time_previous_utc": merged.loc[index - 1, "time_utc"],
                "time_current_utc": merged.loc[index, "time_utc"],
                "pair_gap_seconds": dt_seconds,
                "attitude_step_arcsec": arcseconds,
                "attitude_rate_arcsec_per_second": arcseconds / dt_seconds,
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result["attitude_rate_robust_z"] = robust_zscore(
            result["attitude_rate_arcsec_per_second"]
        )
    return result


def fsm_command_steps(
    canonical: pd.DataFrame,
    *,
    x_mnemonic: str,
    y_mnemonic: str,
    tolerance_seconds: float,
) -> pd.DataFrame:
    merged = merge_mnemonic_series(
        canonical, [x_mnemonic, y_mnemonic], tolerance_seconds
    )
    if merged.empty or len(merged) < 2:
        return pd.DataFrame()
    merged["command_radius"] = np.hypot(merged[x_mnemonic], merged[y_mnemonic])
    merged["command_step"] = merged["command_radius"].diff().abs()
    merged["command_step_robust_z"] = robust_zscore(merged["command_step"])
    return merged


def acquire_mast_observation_context(
    config: dict[str, Any], store: EvidenceStore
) -> pd.DataFrame:
    mast = config["mast"]
    start = parse_utc(config["full_day"]["start_utc"])
    end = parse_utc(config["full_day"]["end_utc"])
    mjd_origin = pd.Timestamp("1858-11-17T00:00:00Z")
    start_mjd = float((start - mjd_origin) / pd.Timedelta(days=1))
    end_mjd = float((end - mjd_origin) / pd.Timedelta(days=1))
    payload = {
        "service": mast["caom_service"],
        "format": "json",
        "params": {
            "columns": "*",
            "filters": [
                {"paramName": "obs_collection", "values": ["JWST"]},
                {
                    "paramName": "t_min",
                    "values": [{"min": start_mjd, "max": end_mjd}],
                    "separator": "range",
                },
            ],
        },
    }
    response = mast_post(
        store,
        source_id="JWST_CAOM_CONTEXT",
        source_kind="MAST_CAOM_OBSERVATION_CONTEXT",
        url=mast["search_url"],
        payload=payload,
        raw_relative_path="mast/jwst_caom_observation_context.json",
    )
    if not response or not isinstance(response.get("data"), list):
        return pd.DataFrame()
    return pd.DataFrame(response["data"])


# ---------------------------------------------------------------------------
# Result classification and reporting
# ---------------------------------------------------------------------------


def rows_in_window(
    frame: pd.DataFrame,
    time_column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if frame.empty or time_column not in frame.columns:
        return pd.DataFrame()
    return frame[(frame[time_column] >= start) & (frame[time_column] < end)]


def build_window_result_states(
    config: dict[str, Any],
    l1_pairs: pd.DataFrame,
    jwst_attitude: pd.DataFrame,
    jwst_status: pd.DataFrame,
) -> pd.DataFrame:
    any_jwst_rows = not jwst_attitude.empty
    if not jwst_status.empty:
        requested = set(config["mast"]["edb_mnemonics_to_verify"][:4])
        available = set(
            jwst_status.loc[jwst_status["canonical_rows"] > 0, "mnemonic"].tolist()
        )
        any_jwst_rows = requested.issubset(available) and not jwst_attitude.empty
    rows: list[dict[str, Any]] = []
    threshold = float(config["detectors"]["jwst_attitude_candidate"]["robust_z_threshold"])
    for event in config["predeclared_event_windows"]:
        start = parse_utc(event["start_utc"])
        end = parse_utc(event["end_utc"])
        l1_subset = rows_in_window(l1_pairs, "time_current_utc", start, end)
        l1_sources = (
            sorted(l1_subset.loc[l1_subset["candidate"], "source_id"].unique())
            if not l1_subset.empty
            else []
        )
        attitude_subset = rows_in_window(
            jwst_attitude, "time_current_utc", start, end
        )
        jwst_candidate_count = (
            int((attitude_subset["attitude_rate_robust_z"].abs() >= threshold).sum())
            if not attitude_subset.empty
            else 0
        )
        if not any_jwst_rows:
            state = "NO_PUBLIC_JWST_DATA"
        elif l1_sources and jwst_candidate_count:
            state = "TEMPORAL_COINCIDENCE_CANDIDATE"
        elif l1_sources:
            state = "L1_ONLY"
        elif jwst_candidate_count:
            state = "JWST_ONLY"
        else:
            state = "NO_L1_CANDIDATE"
        rows.append(
            {
                "window_id": event["id"],
                "start_utc": start,
                "end_utc": end,
                "l1_candidate_sources": "|".join(l1_sources),
                "l1_candidate_source_count": len(l1_sources),
                "jwst_attitude_candidate_count": jwst_candidate_count,
                "result_state": state,
                "mechanism_assigned": False,
            }
        )
    return pd.DataFrame(rows)


def dataframe_to_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def markdown_table_or_message(frame: pd.DataFrame, message: str) -> str:
    if frame.empty:
        return message
    try:
        return frame.to_markdown(index=False)
    except ImportError:
        return "```csv\n" + frame.to_csv(index=False) + "```"


def write_report(
    config: dict[str, Any],
    store: EvidenceStore,
    *,
    source_status: list[dict[str, Any]],
    l1_canonical: pd.DataFrame,
    l1_rejected: pd.DataFrame,
    l1_pairs: pd.DataFrame,
    window_summary: pd.DataFrame,
    control_summary: pd.DataFrame,
    jwst_status: pd.DataFrame,
    jwst_attitude: pd.DataFrame,
    fsm_steps: pd.DataFrame,
    observation_context: pd.DataFrame,
    result_states: pd.DataFrame,
) -> None:
    requested_l1 = len(config["cdaweb_hapi"]["datasets"])
    canonical_l1_sources = int(l1_canonical["source_id"].nunique()) if not l1_canonical.empty else 0
    requested_edb = len(config["mast"]["edb_mnemonics_to_verify"])
    available_edb = int((jwst_status["canonical_rows"] > 0).sum()) if not jwst_status.empty else 0
    summary = {
        "study_id": config["study_id"],
        "generated_at_utc": utc_now(),
        "interpretation": "CANDIDATE_AUDIT_ONLY",
        "simulation_used": False,
        "fixed_l1_to_jwst_lag_used": False,
        "l1": {
            "requested_sources": requested_l1,
            "canonical_sources": canonical_l1_sources,
            "canonical_rows": int(len(l1_canonical)),
            "rejected_rows": int(len(l1_rejected)),
            "candidate_pairs": int(l1_pairs["candidate"].sum()) if not l1_pairs.empty else 0,
            "source_status": source_status,
        },
        "jwst": {
            "requested_edb_mnemonics": requested_edb,
            "available_edb_mnemonics": available_edb,
            "attitude_step_rows": int(len(jwst_attitude)),
            "fsm_step_rows": int(len(fsm_steps)),
            "observation_context_rows": int(len(observation_context)),
        },
        "window_results": (
            result_states.assign(
                start_utc=result_states["start_utc"].astype(str),
                end_utc=result_states["end_utc"].astype(str),
            ).to_dict(orient="records")
            if not result_states.empty
            else []
        ),
    }
    write_json(store.reports_dir / "summary.json", summary)

    report_sections = [
        "# January 5, 2026 JWST / L1 Retrospective — Run Report",
        "",
        f"Generated: `{summary['generated_at_utc']}`",
        "",
        "## Interpretation boundary",
        "",
        "This run reports source acquisition, canonical measurements, candidate detector crossings, and frozen-control comparisons. It does not establish expansion, re-addressing, a vacuum/substrate shift, a CME impact on JWST, or any other mechanism.",
        "",
        "No simulation or demo data were used. No fixed DSCOVR-to-JWST lag was assumed. The Gannon V2 holdout was not accessed.",
        "",
        "## Source completion",
        "",
        f"- Historical L1 magnetic sources canonicalized: **{canonical_l1_sources}/{requested_l1}**",
        f"- JWST EDB mnemonics with canonical numeric rows: **{available_edb}/{requested_edb}**",
        f"- JWST observation-context rows: **{len(observation_context)}**",
        "",
        "## Frozen-window result states",
        "",
        markdown_table_or_message(result_states, "No window states could be produced."),
        "",
        "## L1 control comparison",
        "",
        markdown_table_or_message(control_summary, "No complete L1 control comparison was available."),
        "",
        "## JWST EDB status",
        "",
        markdown_table_or_message(jwst_status, "No JWST EDB response table was available."),
        "",
        "## Required human/operations review",
        "",
        "Any JWST candidate must be checked against planned slews, visit transitions, guide-star acquisition, dithers, momentum management, instrument activity, telemetry gaps, and documented FGS/ACS anomalies before it can be labeled unexplained.",
        "",
        "## Next-stage gate",
        "",
        "A lagged L1/JWST association test is not authorized by Protocol V1. It requires spacecraft ephemerides, a frozen propagation model, front-orientation uncertainty, and a new protocol version before examining the best lag.",
        "",
    ]
    (store.reports_dir / "report.md").write_text(
        "\n".join(report_sections), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    forbidden = set(config.get("forbidden_runtime_behavior", []))
    required_forbidden = {
        "simulation_fallback",
        "demo_fallback",
        "current_feed_relabelled_as_historical",
        "modify_gannon_v2_holdout",
    }
    if not required_forbidden.issubset(forbidden):
        raise ValueError("Config does not contain all mandatory fail-closed prohibitions")
    return config


def run(config_path: Path, output_dir: Path) -> int:
    config = load_config(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(output_dir)

    print("=" * 72)
    print("JANUARY 5, 2026 JWST / L1 RETROSPECTIVE — PROTOCOL V1")
    print("=" * 72)
    print("Historical hypothesis labels only: re-addressing / expansion event")
    print("No mechanism is presumed. No simulation fallback is available.")

    # 1. Historical L1 magnetic evidence.
    l1_canonical, l1_rejected, source_status = acquire_cdaweb_magnetic(config, store)
    dataframe_to_csv(l1_canonical, store.canonical_dir / "l1_magnetic_vectors.csv")
    dataframe_to_csv(l1_rejected, store.tables_dir / "l1_rejected_rows.csv")
    write_json(store.tables_dir / "l1_source_status.json", source_status)

    l1_pairs = detect_l1_candidates(
        l1_canonical, config["detectors"]["l1_magnetic_candidate"]
    )
    dataframe_to_csv(l1_pairs, store.tables_dir / "l1_consecutive_pair_metrics.csv")
    dataframe_to_csv(
        l1_pairs[l1_pairs["candidate"]] if not l1_pairs.empty else l1_pairs,
        store.tables_dir / "l1_candidate_pairs.csv",
    )

    windows = declared_windows(config)
    window_summary = summarize_l1_windows(l1_canonical, l1_pairs, windows)
    dataframe_to_csv(window_summary, store.tables_dir / "l1_window_summary.csv")
    control_summary = empirical_control_table(window_summary)
    dataframe_to_csv(control_summary, store.tables_dir / "l1_control_comparison.csv")

    # 2. JWST EDB and observation context.
    jwst_canonical, jwst_status = acquire_mast_edb(config, store)
    dataframe_to_csv(jwst_canonical, store.canonical_dir / "jwst_edb_numeric.csv")
    dataframe_to_csv(jwst_status, store.tables_dir / "jwst_edb_status.csv")

    attitude_mnemonics = config["mast"]["edb_mnemonics_to_verify"][:4]
    tolerance = float(
        config["detectors"]["jwst_attitude_candidate"][
            "maximum_component_alignment_tolerance_seconds"
        ]
    )
    jwst_attitude = quaternion_angular_steps(
        jwst_canonical,
        mnemonics=attitude_mnemonics,
        tolerance_seconds=tolerance,
    )
    dataframe_to_csv(jwst_attitude, store.tables_dir / "jwst_attitude_steps.csv")

    fsm_steps = fsm_command_steps(
        jwst_canonical,
        x_mnemonic="SA_ZADUCMDX",
        y_mnemonic="SA_ZADUCMDY",
        tolerance_seconds=tolerance,
    )
    dataframe_to_csv(fsm_steps, store.tables_dir / "jwst_fsm_command_steps.csv")

    observation_context = acquire_mast_observation_context(config, store)
    dataframe_to_csv(
        observation_context, store.canonical_dir / "jwst_observation_context.csv"
    )

    # 3. Result states. These are observational states, not mechanisms.
    result_states = build_window_result_states(
        config, l1_pairs, jwst_attitude, jwst_status
    )
    dataframe_to_csv(result_states, store.tables_dir / "window_result_states.csv")

    write_report(
        config,
        store,
        source_status=source_status,
        l1_canonical=l1_canonical,
        l1_rejected=l1_rejected,
        l1_pairs=l1_pairs,
        window_summary=window_summary,
        control_summary=control_summary,
        jwst_status=jwst_status,
        jwst_attitude=jwst_attitude,
        fsm_steps=fsm_steps,
        observation_context=observation_context,
        result_states=result_states,
    )
    store.finalize(config_path, config)

    print("\nRun complete. Scientific status is in reports/summary.json and report.md.")
    print(f"Evidence directory: {output_dir}")
    print("A green process exit means the audit completed, not that a hypothesis passed.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/jan5_2026_retrospective.v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/jan5_2026_retrospective_v1"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(args.config.resolve(), args.output.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
