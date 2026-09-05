#!/usr/bin/env python3
"""Fail-closed NOAA SWPC operational real-time solar-wind ingestion.

The current SWPC Real-Time Solar Wind JSON feeds are provider-selected
operational L1 products. They may contain DSCOVR, ACE, SOLAR-1, or another
provider-selected upstream spacecraft. NVCPP therefore preserves the per-row
``source`` and ``active`` fields when supplied and never represents this stream
as independent mission-specific evidence unless identity is separately proven.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests

from core.cline_l1_chain_v1 import PROTOCOL_ID, PROTOCOL_VERSION, ProtocolConfig, run_chain
from core.diagnostics import baseline_failure
from core.exceptions import SourceDiagnosticError

PIPELINE_VERSION = "1.2.0"
MAG_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json"
PLASMA_URL = "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json"
CADENCE_SECONDS = 60.0
CURRENT_FRESHNESS_MINUTES = 20.0
MU0 = 4.0e-7 * np.pi
PROTON_MASS_KG = 1.67262192369e-27
BOLTZMANN_J_K = 1.380649e-23


class NoaaRealtimeError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False),
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _download_json(
    session: requests.Session,
    url: str,
    path: Path,
) -> tuple[Any, dict[str, Any]]:
    response = session.get(url, timeout=90)
    response.raise_for_status()
    raw = response.content
    path.write_bytes(raw)
    try:
        payload = response.json()
    except ValueError as exc:
        raise NoaaRealtimeError(f"{url} did not return JSON") from exc
    return payload, {
        "url": url,
        "resolved_url": response.url,
        "content_type": response.headers.get("Content-Type"),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "size_bytes": len(raw),
        "sha256": _sha256_bytes(raw),
        "path": path.name,
    }


def _table(payload: Any, *, required: list[str], source_name: str) -> pd.DataFrame:
    """Parse either the current list-of-objects schema or legacy header rows.

    Column matching is always by name. The legacy form remains supported only
    to keep historical fixtures reproducible; production currently uses the
    list-of-objects form.
    """

    if not isinstance(payload, list) or not payload:
        raise NoaaRealtimeError(f"{source_name} payload is not a nonempty array")

    if all(isinstance(row, dict) for row in payload):
        headers: list[str] = []
        seen: set[str] = set()
        for row in payload:
            for key in row:
                if not isinstance(key, str):
                    raise NoaaRealtimeError(f"{source_name} contains a non-string key")
                if key not in seen:
                    seen.add(key)
                    headers.append(key)
        missing = [name for name in required if name not in seen]
        if missing:
            raise NoaaRealtimeError(f"{source_name} is missing required columns: {missing}")
        records: list[dict[str, Any]] = []
        for source_row, row in enumerate(payload, start=1):
            absent = [name for name in required if name not in row]
            if absent:
                raise NoaaRealtimeError(
                    f"{source_name} object {source_row} is missing required keys: {absent}"
                )
            records.append({"_source_row": source_row, **row})
        frame = pd.DataFrame.from_records(records, columns=["_source_row", *headers])
    else:
        if len(payload) < 2:
            raise NoaaRealtimeError(
                f"{source_name} legacy payload is not a header-plus-rows array"
            )
        header = payload[0]
        if not isinstance(header, list) or not all(isinstance(value, str) for value in header):
            raise NoaaRealtimeError(f"{source_name} header is invalid")
        if len(header) != len(set(header)):
            raise NoaaRealtimeError(f"{source_name} header contains duplicate names")
        missing = [name for name in required if name not in header]
        if missing:
            raise NoaaRealtimeError(f"{source_name} is missing required columns: {missing}")
        rows: list[list[Any]] = []
        for source_row, row in enumerate(payload[1:], start=2):
            if not isinstance(row, list) or len(row) != len(header):
                raise NoaaRealtimeError(
                    f"{source_name} row {source_row} has "
                    f"{len(row) if isinstance(row, list) else 'non-list'} fields; "
                    f"expected {len(header)}"
                )
            rows.append([source_row, *row])
        frame = pd.DataFrame(rows, columns=["_source_row", *header])

    if frame.empty:
        raise NoaaRealtimeError(f"{source_name} contains no data rows")
    return frame



def _select_active_operational_rows(
    frame: pd.DataFrame,
    *,
    source_name: str,
) -> pd.DataFrame:
    """Select NOAA's provider-designated active upstream stream.

    Current RTSW products can include simultaneous rows from SOLAR1, IMAP,
    ACE, or another upstream spacecraft. Those are independent provider rows,
    not duplicate measurements to average together. When the ``active`` field
    exists, only rows explicitly designated active enter the operational
    canonical stream. All rows remain preserved in the raw response.
    """

    if "active" not in frame.columns:
        return frame.copy()

    def parse_active(value: Any) -> bool:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if pd.isna(value):
            raise NoaaRealtimeError(f"{source_name} contains a missing active flag")
        if isinstance(value, (int, np.integer, float, np.floating)):
            if float(value) in (0.0, 1.0):
                return bool(int(value))
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "active"}:
                return True
            if normalized in {"false", "0", "no", "inactive"}:
                return False
        raise NoaaRealtimeError(
            f"{source_name} contains an unrecognized active flag: {value!r}"
        )

    active_mask = frame["active"].map(parse_active)
    selected = frame.loc[active_mask].copy()
    if selected.empty:
        raise NoaaRealtimeError(
            f"{source_name} exposes an active field but designates no active rows"
        )
    return selected

def _first_present(frame: pd.DataFrame, names: Iterable[str]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _normalize_magnetic_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    result = frame.copy()
    time = _first_present(result, ("time_tag", "time"))
    bt = _first_present(result, ("bt", "b_total", "b_mag"))
    component_sets = [
        ("GSM", ("bx_gsm", "by_gsm", "bz_gsm")),
        ("GSE", ("bx_gse", "by_gse", "bz_gse")),
    ]
    chosen: tuple[str, tuple[str, str, str]] | None = None
    for coordinate_frame, names in component_sets:
        if all(name in result.columns for name in names):
            chosen = (coordinate_frame, names)
            break
    if time is None or bt is None or chosen is None:
        raise NoaaRealtimeError(
            "NOAA magnetic schema lacks a supported time, total-field, or "
            "complete GSM/GSE vector set"
        )
    coordinate_frame, (bx, by, bz) = chosen
    rename = {time: "time_tag", bt: "bt", bx: "bx_gsm", by: "by_gsm", bz: "bz_gsm"}
    result.rename(columns=rename, inplace=True)
    return result, coordinate_frame


def _normalize_plasma_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    mapping = {
        "time_tag": ("time_tag", "time"),
        "density": ("proton_density", "density"),
        "speed": ("proton_speed", "speed"),
        "temperature": ("proton_temperature", "temperature"),
    }
    rename: dict[str, str] = {}
    for target, candidates in mapping.items():
        source = _first_present(result, candidates)
        if source is None:
            if target == "temperature":
                result[target] = np.nan
                continue
            raise NoaaRealtimeError(f"NOAA wind schema lacks required field {target!r}")
        rename[source] = target
    result.rename(columns=rename, inplace=True)
    return result


def _append_quarantine(
    records: list[pd.DataFrame],
    original: pd.DataFrame,
    mask: pd.Series,
    reason: str,
    source: str,
) -> None:
    if bool(mask.any()):
        selected = original.loc[mask].copy()
        selected.insert(0, "source_product", source)
        selected["reason_code"] = reason
        records.append(selected)


def _deduplicate(
    frame: pd.DataFrame,
    original: pd.DataFrame,
    value_columns: list[str],
    quarantine: list[pd.DataFrame],
    source: str,
) -> pd.DataFrame:
    drop_indices: list[int] = []
    conflicts: list[pd.DataFrame] = []
    duplicates = frame["time"].duplicated(keep=False)
    for _, group in frame.loc[duplicates].groupby("time", sort=False):
        unique = group[value_columns].drop_duplicates()
        if len(unique) == 1:
            extras = group.iloc[1:]
            selected = original.loc[extras.index].copy()
            selected.insert(0, "source_product", source)
            selected["reason_code"] = "DUPLICATE_IDENTICAL"
            quarantine.append(selected)
            drop_indices.extend(extras.index.tolist())
        else:
            selected = original.loc[group.index].copy()
            selected.insert(0, "source_product", source)
            selected["reason_code"] = "DUPLICATE_CONFLICT"
            conflicts.append(selected)
    if conflicts:
        quarantine.extend(conflicts)
        raise NoaaRealtimeError(f"{source} contains conflicting duplicate timestamps")
    return frame.drop(index=drop_indices) if drop_indices else frame


def _sanitize_magnetic(
    raw: pd.DataFrame,
    quarantine: list[pd.DataFrame],
) -> tuple[pd.DataFrame, str]:
    normalized, coordinate_frame = _normalize_magnetic_columns(raw)
    original = normalized.copy()
    parsed = normalized.copy()
    parsed["time"] = pd.to_datetime(parsed["time_tag"], utc=True, errors="coerce")
    for column in ("bx_gsm", "by_gsm", "bz_gsm", "bt"):
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    invalid_time = parsed["time"].isna()
    nonnumeric = parsed[["bx_gsm", "by_gsm", "bz_gsm", "bt"]].isna().any(axis=1)
    nonnumeric &= ~invalid_time
    zero = (parsed[["bx_gsm", "by_gsm", "bz_gsm"]] == 0.0).all(axis=1)
    zero &= ~(invalid_time | nonnumeric)

    vector_magnitude = np.sqrt(
        parsed["bx_gsm"].pow(2) + parsed["by_gsm"].pow(2) + parsed["bz_gsm"].pow(2)
    )
    tolerance = np.maximum(0.05, 0.01 * vector_magnitude.abs())
    mismatch = (parsed["bt"] - vector_magnitude).abs() > tolerance
    mismatch &= ~(invalid_time | nonnumeric | zero)

    product = "NOAA_SWPC_RTSW_MAG_1M"
    _append_quarantine(quarantine, original, invalid_time, "INVALID_TIMESTAMP", product)
    _append_quarantine(quarantine, original, nonnumeric, "NONNUMERIC_VECTOR", product)
    _append_quarantine(quarantine, original, zero, "ZERO_VECTOR_SUSPECT", product)
    _append_quarantine(quarantine, original, mismatch, "BT_VECTOR_MISMATCH", product)

    admitted = parsed.loc[~(invalid_time | nonnumeric | zero | mismatch)].copy()
    admitted["B_mag"] = vector_magnitude.loc[admitted.index]
    admitted = _deduplicate(
        admitted,
        original,
        ["bx_gsm", "by_gsm", "bz_gsm", "bt"],
        quarantine,
        product,
    )
    admitted.sort_values("time", inplace=True)
    if admitted.empty:
        raise NoaaRealtimeError("no NOAA SWPC magnetic rows remain after quarantine")
    return admitted, coordinate_frame


def _sanitize_plasma(raw: pd.DataFrame, quarantine: list[pd.DataFrame]) -> pd.DataFrame:
    normalized = _normalize_plasma_columns(raw)
    original = normalized.copy()
    parsed = normalized.copy()
    parsed["time"] = pd.to_datetime(parsed["time_tag"], utc=True, errors="coerce")
    for column in ("density", "speed", "temperature"):
        parsed[column] = pd.to_numeric(parsed[column], errors="coerce")

    invalid_time = parsed["time"].isna()
    missing_required = parsed[["density", "speed"]].isna().any(axis=1)
    missing_required &= ~invalid_time
    nonpositive_required = (parsed["density"] <= 0) | (parsed["speed"] <= 0)
    nonpositive_required &= ~(invalid_time | missing_required)
    bad_temperature = parsed["temperature"].notna() & (parsed["temperature"] <= 0)
    bad_temperature &= ~(invalid_time | missing_required | nonpositive_required)

    product = "NOAA_SWPC_RTSW_WIND_1M"
    _append_quarantine(quarantine, original, invalid_time, "INVALID_TIMESTAMP", product)
    _append_quarantine(quarantine, original, missing_required, "NONNUMERIC_PLASMA", product)
    _append_quarantine(
        quarantine, original, nonpositive_required, "NONPOSITIVE_PLASMA", product
    )
    _append_quarantine(
        quarantine, original, bad_temperature, "NONPOSITIVE_TEMPERATURE", product
    )

    excluded = invalid_time | missing_required | nonpositive_required
    admitted = parsed.loc[~excluded].copy()
    admitted.loc[bad_temperature.loc[admitted.index], "temperature"] = np.nan
    admitted = _deduplicate(
        admitted,
        original,
        ["density", "speed", "temperature"],
        quarantine,
        product,
    )
    admitted.sort_values("time", inplace=True)
    if admitted.empty:
        raise NoaaRealtimeError("no NOAA SWPC plasma rows remain after quarantine")
    return admitted


def _add_plasma_physics(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    density_m3 = result["density"] * 1.0e6
    speed_ms = result["speed"] * 1.0e3
    magnetic_t = result["B_mag"] * 1.0e-9

    result["dynamic_pressure_nPa"] = density_m3 * PROTON_MASS_KG * speed_ms.pow(2) * 1.0e9
    result["alfven_speed_km_s"] = (
        magnetic_t / np.sqrt(MU0 * PROTON_MASS_KG * density_m3)
    ) / 1.0e3
    result["alfven_mach"] = result["speed"] / result["alfven_speed_km_s"]
    result["proton_beta"] = (
        2.0 * MU0 * density_m3 * BOLTZMANN_J_K * result["temperature"]
    ) / magnetic_t.pow(2)

    for column in (
        "dynamic_pressure_nPa",
        "alfven_speed_km_s",
        "alfven_mach",
        "proton_beta",
    ):
        result.loc[~np.isfinite(result[column]), column] = np.nan
    return result


def _to_utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _source_counts(frame: pd.DataFrame) -> dict[str, int]:
    if "source" not in frame.columns:
        return {}
    return {
        str(key): int(value)
        for key, value in frame["source"].fillna("UNKNOWN").value_counts().to_dict().items()
    }


STATE_RETENTION_HOURS = 36.0
MAG_STATE_FILE = "noaa_mag_active_history.csv"
PLASMA_STATE_FILE = "noaa_plasma_active_history.csv"


def _state_path(state_dir: Path, filename: str) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / filename


def _read_state_frame(path: Path, *, required: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=required)
    frame = pd.read_csv(path)
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise NoaaRealtimeError(f"state file {path} is missing columns: {missing}")
    frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    if frame["time"].isna().any():
        raise NoaaRealtimeError(f"state file {path} contains invalid timestamps")
    if frame["time"].duplicated().any():
        raise NoaaRealtimeError(f"state file {path} contains duplicate timestamps")
    return frame.sort_values("time").reset_index(drop=True)


def _bootstrap_state_from_raw(
    state_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    bootstrap = state_dir / "bootstrap"
    mag_path = bootstrap / "rtsw_mag_1m_raw.json"
    plasma_path = bootstrap / "rtsw_wind_1m_raw.json"
    if not mag_path.exists() or not plasma_path.exists():
        return pd.DataFrame(), pd.DataFrame(), {"used": False}

    mag_payload = json.loads(mag_path.read_text(encoding="utf-8"))
    plasma_payload = json.loads(plasma_path.read_text(encoding="utf-8"))
    mag_raw = _table(
        mag_payload, required=["time_tag"], source_name="NOAA cached magnetic bootstrap"
    )
    plasma_raw = _table(
        plasma_payload, required=["time_tag"], source_name="NOAA cached plasma bootstrap"
    )
    mag_active = _select_active_operational_rows(
        mag_raw, source_name="NOAA cached magnetic bootstrap"
    )
    plasma_active = _select_active_operational_rows(
        plasma_raw, source_name="NOAA cached plasma bootstrap"
    )
    ignored_quarantine: list[pd.DataFrame] = []
    magnetic, _ = _sanitize_magnetic(mag_active, ignored_quarantine)
    plasma = _sanitize_plasma(plasma_active, ignored_quarantine)
    return magnetic, plasma, {
        "used": True,
        "magnetic_path": str(mag_path),
        "plasma_path": str(plasma_path),
        "magnetic_sha256": _sha256_file(mag_path),
        "plasma_sha256": _sha256_file(plasma_path),
        "magnetic_rows": int(len(magnetic)),
        "plasma_rows": int(len(plasma)),
    }


def _merge_operational_history(
    cached: pd.DataFrame,
    current: pd.DataFrame,
    *,
    compare_columns: list[str],
    retention_hours: float = STATE_RETENTION_HOURS,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Merge an operational cache with the newest provider response.

    The newest response wins at overlapping timestamps because operational
    providers can revise recent rows. Revisions are counted, not hidden. Raw
    responses remain independently preserved in each immutable run package.
    """

    cached = cached.copy()
    current = current.copy()
    for frame, name in ((cached, "cached"), (current, "current")):
        if frame.empty:
            continue
        frame["time"] = pd.to_datetime(frame["time"], utc=True, errors="coerce")
        if frame["time"].isna().any():
            raise NoaaRealtimeError(f"{name} operational history contains invalid time")
        if frame["time"].duplicated().any():
            raise NoaaRealtimeError(f"{name} operational history contains duplicate time")

    revised = 0
    identical_overlap = 0
    if not cached.empty and not current.empty:
        left = cached.set_index("time")
        right = current.set_index("time")
        common = left.index.intersection(right.index)
        if len(common):
            left_values = left.loc[common, compare_columns].sort_index()
            right_values = right.loc[common, compare_columns].sort_index()
            equal = (
                left_values.eq(right_values)
                | (left_values.isna() & right_values.isna())
            ).all(axis=1)
            identical_overlap = int(equal.sum())
            revised = int((~equal).sum())
            cached = cached.loc[~cached["time"].isin(common)].copy()

    merged = pd.concat([cached, current], ignore_index=True, sort=False)
    if merged.empty:
        return merged, {
            "cached_rows": 0,
            "current_rows": 0,
            "merged_rows": 0,
            "identical_overlap_rows": 0,
            "revised_overlap_rows": 0,
        }
    merged.sort_values("time", inplace=True)
    if merged["time"].duplicated().any():
        raise NoaaRealtimeError("merged operational history contains duplicate timestamps")
    latest = merged["time"].max()
    cutoff = latest - pd.Timedelta(hours=float(retention_hours))
    merged = merged.loc[merged["time"] >= cutoff].reset_index(drop=True)
    return merged, {
        "cached_rows": int(len(cached) + identical_overlap + revised),
        "current_rows": int(len(current)),
        "merged_rows": int(len(merged)),
        "identical_overlap_rows": identical_overlap,
        "revised_overlap_rows": revised,
    }


def _write_state_frame(path: Path, frame: pd.DataFrame) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = frame.copy()
    output["time"] = pd.to_datetime(output["time"], utc=True).map(
        lambda value: value.isoformat()
    )
    output.to_csv(path, index=False)
    return {
        "path": str(path),
        "rows": int(len(output)),
        "start": frame["time"].min().isoformat() if len(frame) else None,
        "end": frame["time"].max().isoformat() if len(frame) else None,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def run_noaa_realtime_pipeline(
    *,
    run_name: str,
    retrieval_start: str,
    analysis_start: str,
    analysis_end: str,
    outdir: Path,
    session: requests.Session | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    run_dir = outdir / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "noaa_realtime_run_manifest.json"
    requested_start = _to_utc(retrieval_start)
    requested_analysis_start = _to_utc(analysis_start)
    requested_end = _to_utc(analysis_end)
    retrieval_duration = requested_end - requested_start
    analysis_duration = requested_end - requested_analysis_start
    manifest: dict[str, Any] = {
        "manifest_version": "1.2.0",
        "pipeline_version": PIPELINE_VERSION,
        "status": "STARTED",
        "started_utc": _utc_now(),
        "git_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "runtime": {"python": platform.python_version()},
        "source": {
            "provider": "NOAA/SWPC",
            "product": "Real-Time Solar Wind 1-minute operational JSON",
            "spacecraft_identity": "PROVIDER_SELECTED_ACTIVE_UPSTREAM_SPACECRAFT",
            "mission_specific": False,
            "interpretation_limit": (
                "This provider-selected operational L1 stream is not independent "
                "mission proof unless spacecraft identity is separately resolved."
            ),
        },
        "protocol_id": PROTOCOL_ID,
        "protocol_version": PROTOCOL_VERSION,
        "requested_retrieval_window": {"start": retrieval_start, "end": analysis_end},
        "requested_analysis_window": {"start": analysis_start, "end": analysis_end},
        "artifacts": [],
    }
    _write_json(manifest_path, manifest)
    quarantine_records: list[pd.DataFrame] = []

    try:
        session = session or requests.Session()
        session.headers.update({"User-Agent": f"NVCPP-NOAA-RT/{PIPELINE_VERSION}"})
        mag_payload, mag_meta = _download_json(
            session, MAG_URL, run_dir / "rtsw_mag_1m_raw.json"
        )
        plasma_payload, plasma_meta = _download_json(
            session, PLASMA_URL, run_dir / "rtsw_wind_1m_raw.json"
        )
        manifest["downloads"] = {"magnetic": mag_meta, "plasma": plasma_meta}
        # Current production feeds are lists of objects. Legacy header fixtures
        # are also accepted by _table for reproducibility.
        mag_raw = _table(mag_payload, required=["time_tag"], source_name="NOAA SWPC magnetic")
        plasma_raw = _table(
            plasma_payload, required=["time_tag"], source_name="NOAA SWPC plasma"
        )

        mag_active = _select_active_operational_rows(
            mag_raw, source_name="NOAA SWPC magnetic"
        )
        plasma_active = _select_active_operational_rows(
            plasma_raw, source_name="NOAA SWPC plasma"
        )
        mag_current, coordinate_frame = _sanitize_magnetic(
            mag_active, quarantine_records
        )
        plasma_current = _sanitize_plasma(plasma_active, quarantine_records)

        configured_state = state_dir
        if configured_state is None:
            state_text = os.environ.get("NVCPP_NOAA_STATE_DIR", "").strip()
            configured_state = Path(state_text) if state_text else None

        cache_meta: dict[str, Any] = {"configured": configured_state is not None}
        if configured_state is not None:
            configured_state.mkdir(parents=True, exist_ok=True)
            mag_state_path = _state_path(configured_state, MAG_STATE_FILE)
            plasma_state_path = _state_path(configured_state, PLASMA_STATE_FILE)
            cached_mag = _read_state_frame(
                mag_state_path,
                required=["time", "bx_gsm", "by_gsm", "bz_gsm", "bt", "B_mag"],
            )
            cached_plasma = _read_state_frame(
                plasma_state_path,
                required=["time", "density", "speed", "temperature"],
            )
            bootstrap_meta = {"used": False}
            if cached_mag.empty or cached_plasma.empty:
                bootstrap_mag, bootstrap_plasma, bootstrap_meta = _bootstrap_state_from_raw(
                    configured_state
                )
                if cached_mag.empty and not bootstrap_mag.empty:
                    cached_mag = bootstrap_mag
                if cached_plasma.empty and not bootstrap_plasma.empty:
                    cached_plasma = bootstrap_plasma

            mag_all, mag_merge = _merge_operational_history(
                cached_mag,
                mag_current,
                compare_columns=["source", "bx_gsm", "by_gsm", "bz_gsm", "bt", "B_mag"],
            )
            plasma_all, plasma_merge = _merge_operational_history(
                cached_plasma,
                plasma_current,
                compare_columns=["source", "density", "speed", "temperature"],
            )
            cache_meta.update(
                {
                    "bootstrap": bootstrap_meta,
                    "magnetic_merge": mag_merge,
                    "plasma_merge": plasma_merge,
                    "magnetic_state": _write_state_frame(mag_state_path, mag_all),
                    "plasma_state": _write_state_frame(plasma_state_path, plasma_all),
                }
            )
        else:
            mag_all = mag_current
            plasma_all = plasma_current
        manifest["rolling_state"] = cache_meta

        latest_mag = mag_current["time"].max()
        effective_end = min(requested_end, latest_mag + pd.Timedelta(seconds=CADENCE_SECONDS))
        effective_start = effective_end - retrieval_duration
        effective_analysis_start = effective_end - analysis_duration
        manifest["latest_physical_sample_utc"] = latest_mag.isoformat()
        manifest["effective_retrieval_window"] = {
            "start": effective_start.isoformat(), "end": effective_end.isoformat()
        }
        manifest["effective_analysis_window"] = {
            "start": effective_analysis_start.isoformat(), "end": effective_end.isoformat()
        }
        mag = mag_all.loc[
            (mag_all["time"] >= effective_start) & (mag_all["time"] < effective_end)
        ].copy()
        plasma = plasma_all.loc[
            (plasma_all["time"] >= effective_start) & (plasma_all["time"] < effective_end)
        ].copy()
        if mag.empty:
            raise NoaaRealtimeError("NOAA SWPC magnetic data contain no usable provider window")

        magnetic_input_path = run_dir / "noaa_realtime_baseline_input.csv"
        plasma_input_path = run_dir / "noaa_realtime_plasma_input.csv"
        mag.to_csv(magnetic_input_path, index=False)
        plasma.to_csv(plasma_input_path, index=False)

        processed = run_chain(
            mag,
            time_col="time",
            b_mag_col="B_mag",
            expected_cadence_seconds=CADENCE_SECONDS,
        )
        canonical = processed.merge(
            plasma[["time", "density", "speed", "temperature"]],
            on="time",
            how="left",
            validate="one_to_one",
        )
        canonical = _add_plasma_physics(canonical)
        analysis = canonical.loc[
            (canonical["time"] >= effective_analysis_start)
            & (canonical["time"] < effective_end)
        ].copy()
        valid = analysis["chi_B24M"].notna()
        if not valid.any():
            protocol = ProtocolConfig()
            raise baseline_failure(
                processed, analysis, time_col="time",
                window_hours=protocol.window_hours,
                min_coverage=protocol.min_coverage,
                cadence_seconds=CADENCE_SECONDS,
            )

        quarantine = (
            pd.concat(quarantine_records, ignore_index=True, sort=False)
            if quarantine_records
            else pd.DataFrame(columns=["source_product", "reason_code"])
        )
        quarantine_path = run_dir / "noaa_realtime_quarantine.csv"
        quarantine.to_csv(quarantine_path, index=False)
        canonical_path = run_dir / "noaa_realtime_canonical.csv"
        analysis.to_csv(canonical_path, index=False)

        latest_time = analysis.loc[valid, "time"].max()
        freshness_minutes = max(
            0.0, (requested_end - latest_time).total_seconds() / 60.0
        )
        source_state = (
            "CURRENT" if freshness_minutes <= CURRENT_FRESHNESS_MINUTES else "STALE"
        )
        report_path = run_dir / "noaa_realtime_report.md"
        report_path.write_text(
            "\n".join(
                [
                    f"# NOAA SWPC Operational L1 Run: {run_name}",
                    "",
                    f"- Source state: **{source_state}**",
                    f"- Selected vector frame: **{coordinate_frame}**",
                    f"- Latest admitted time: `{latest_time.isoformat()}`",
                    f"- Freshness at requested analysis end: **{freshness_minutes:.1f} minutes**",
                    f"- Effective provider analysis: {effective_analysis_start.isoformat()} to {effective_end.isoformat()}",
                    f"- Analysis rows: **{len(analysis):,}**",
                    f"- Baseline-valid rows: **{int(valid.sum()):,}**",
                    f"- Plasma-paired rows: **{int(analysis['density'].notna().sum()):,}**",
                    f"- Maximum chi_B24M: **{analysis['chi_B24M'].max():.9g}**",
                    f"- Minimum delta_B24M: **{analysis['delta_B24M'].min():.9g}**",
                    f"- Maximum delta_B24M: **{analysis['delta_B24M'].max():.9g}**",
                    f"- Quarantined source rows: **{len(quarantine):,}**",
                    "- Clipping applied: **False**",
                    "",
                    "A stale state is a provider-latency observation, not a current solar event.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": _utc_now(),
                "source_state": source_state,
                "freshness_minutes": freshness_minutes,
                "effective_retrieval_window": {
                    "start": effective_start.isoformat(),
                    "end": effective_end.isoformat(),
                },
                "effective_analysis_window": {
                    "start": effective_analysis_start.isoformat(),
                    "end": effective_end.isoformat(),
                },
                "source": {
                    **manifest["source"],
                    "coordinate_frame": coordinate_frame,
                    "available_source_identity_counts": _source_counts(mag_raw),
                    "active_source_identity_counts": _source_counts(mag_current),
                    "source_identity_counts": _source_counts(mag_current),
                },
                "downloads": {"magnetic": mag_meta, "plasma": plasma_meta},
                "rolling_state": cache_meta,
                "sanitization": {
                    "quarantine_rows": int(len(quarantine)),
                    "reason_counts": {
                        str(key): int(value)
                        for key, value in quarantine.get(
                            "reason_code", pd.Series(dtype=str)
                        )
                        .value_counts()
                        .to_dict()
                        .items()
                    },
                },
                "analysis": {
                    "rows": int(len(analysis)),
                    "baseline_valid_rows": int(valid.sum()),
                    "plasma_paired_rows": int(analysis["density"].notna().sum()),
                    "max_chi_b24m": float(analysis["chi_B24M"].max()),
                    "min_delta_b24m": float(analysis["delta_B24M"].min()),
                    "max_delta_b24m": float(analysis["delta_B24M"].max()),
                },
                "artifacts": [
                    _artifact(run_dir / "rtsw_mag_1m_raw.json"),
                    _artifact(run_dir / "rtsw_wind_1m_raw.json"),
                    _artifact(magnetic_input_path),
                    _artifact(plasma_input_path),
                    _artifact(quarantine_path),
                    _artifact(canonical_path),
                    _artifact(report_path),
                ],
            }
        )
        _write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest.update(
            {
                "status": "FAILED",
                "failed_utc": _utc_now(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        if isinstance(exc, SourceDiagnosticError):
            manifest.update(exc.as_dict())
        # Persist existing quarantine decisions even when baseline admission fails.
        quarantine = (
            pd.concat(quarantine_records, ignore_index=True, sort=False)
            if quarantine_records
            else pd.DataFrame(columns=["source_product", "reason_code"])
        )
        quarantine.to_csv(run_dir / "noaa_realtime_quarantine.csv", index=False)
        manifest["sanitization"] = {
            "quarantine_rows": int(len(quarantine)),
            "reason_counts": {
                str(key): int(value)
                for key, value in quarantine["reason_code"].value_counts().items()
            },
        }
        manifest["artifacts"] = [
            _artifact(path)
            for path in sorted(run_dir.iterdir())
            if path.is_file() and path != manifest_path
        ]
        _write_json(manifest_path, manifest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="NVCPP NOAA SWPC operational L1 runner")
    parser.add_argument("--run", required=True)
    parser.add_argument("--retrieval-start", required=True)
    parser.add_argument("--analysis-start", required=True)
    parser.add_argument("--analysis-end", required=True)
    parser.add_argument("--outdir", default="runs/hourly")
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args()
    try:
        run_noaa_realtime_pipeline(
            run_name=args.run,
            retrieval_start=args.retrieval_start,
            analysis_start=args.analysis_start,
            analysis_end=args.analysis_end,
            outdir=Path(args.outdir),
            state_dir=Path(args.state_dir) if args.state_dir else None,
        )
    except (NoaaRealtimeError, requests.RequestException, ValueError) as exc:
        print(f"[NVCPP-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
