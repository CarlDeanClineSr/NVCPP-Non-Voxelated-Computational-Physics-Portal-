#!/usr/bin/env python3
"""Audit the 2024-05-11 10:59 UTC DSCOVR feature across DSCOVR, ACE, and Wind.

The audit is intentionally bounded.  It preserves source metadata and raw bytes,
canonicalizes magnetic vectors by component before calculating magnitude, keeps
all spacecraft on their native timing, and reports plasma cuts without
interpolation.  A multipoint label means that independent spacecraft recorded a
complex vector-structure interval; it does not establish one-to-one propagation
or a physical mechanism.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from historical.download_dscovr_cdaweb import (
    canonicalize_one_minute as canonicalize_dscovr,
    download_cdaweb as download_dscovr,
    format_cdaweb_date,
)

AUDIT_VERSION = "1.2.0"
HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
NCEI_API_BASE = "https://www.ncei.noaa.gov/cloud-access/space-weather-portal/api/v1"
CDAS_BASE = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys"
DEFAULT_START = "2024-05-11T10:30:00Z"
DEFAULT_STOP = "2024-05-11T11:30:00Z"
DEFAULT_CANDIDATE = "2024-05-11T10:59:00Z"
FILL_ABS = 1.0e30
MU0 = 4.0e-7 * math.pi
PROTON_MASS_KG = 1.67262192369e-27
BOLTZMANN_J_K = 1.380649e-23
ELEMENTARY_CHARGE_C = 1.602176634e-19
EARTH_RADIUS_KM = 6378.137


class MultipointAuditError(RuntimeError):
    """Raised when a multipoint input cannot be admitted safely."""


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


def to_utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def request_required(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
) -> requests.Response:
    response = session.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def hapi_parameter_map(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        parameter["name"]: parameter
        for parameter in info.get("parameters", [])
        if isinstance(parameter, dict) and parameter.get("name")
    }


def flattened_parameter_columns(
    parameter: dict[str, Any],
) -> list[str]:
    name = str(parameter["name"])
    size = parameter.get("size")
    if not size:
        return [name]
    count = int(np.prod(size))
    if count == 3:
        return [f"{name}_x", f"{name}_y", f"{name}_z"]
    return [f"{name}_{index}" for index in range(count)]


def fetch_hapi(
    session: requests.Session,
    *,
    dataset_id: str,
    parameters: list[str],
    start: str,
    stop: str,
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, dict[str, Any]]]:
    outdir.mkdir(parents=True, exist_ok=True)
    info_response = request_required(
        session,
        f"{HAPI_BASE}/info",
        params={"id": dataset_id},
    )
    info_raw = info_response.content
    info_path = outdir / "hapi_info.json"
    info_path.write_bytes(info_raw)
    try:
        info = info_response.json()
    except ValueError as exc:
        raise MultipointAuditError(
            f"{dataset_id} HAPI /info is not JSON"
        ) from exc
    if info.get("status", {}).get("code") != 1200:
        raise MultipointAuditError(
            f"{dataset_id} HAPI /info status is not 1200"
        )
    parameter_map = hapi_parameter_map(info)
    missing = [name for name in parameters if name not in parameter_map]
    if missing:
        raise MultipointAuditError(
            f"{dataset_id} HAPI schema lacks parameters: {missing}"
        )

    query = {
        "id": dataset_id,
        "time.min": start,
        "time.max": stop,
        "parameters": ",".join(parameters),
        "format": "csv",
    }
    data_response = request_required(
        session,
        f"{HAPI_BASE}/data",
        params=query,
    )
    raw = data_response.content
    raw_path = outdir / "raw.csv"
    raw_path.write_bytes(raw)
    lines = [
        line
        for line in raw.decode("utf-8", errors="strict").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise MultipointAuditError(f"{dataset_id} returned no HAPI rows")

    columns = ["time"]
    for name in parameters:
        columns.extend(flattened_parameter_columns(parameter_map[name]))
    frame = pd.read_csv(
        io.StringIO("\n".join(lines)),
        header=None,
        names=columns,
    )
    if len(frame.columns) != len(columns):
        raise MultipointAuditError(
            f"{dataset_id} HAPI column-count mismatch"
        )
    frame["time"] = pd.to_datetime(
        frame["time"], format="ISO8601", utc=True, errors="coerce"
    )
    if frame["time"].isna().any():
        raise MultipointAuditError(
            f"{dataset_id} HAPI data contain invalid timestamps"
        )
    if frame["time"].duplicated().any():
        raise MultipointAuditError(
            f"{dataset_id} HAPI data contain duplicate timestamps"
        )

    for name in parameters:
        metadata = parameter_map[name]
        fill = metadata.get("fill")
        for column in flattened_parameter_columns(metadata):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            if fill is not None:
                try:
                    fill_number = float(fill)
                except (TypeError, ValueError):
                    fill_number = None
                if fill_number is not None:
                    frame.loc[frame[column] == fill_number, column] = np.nan
            frame.loc[frame[column].abs() >= FILL_ABS, column] = np.nan

    metadata = {
        "dataset_id": dataset_id,
        "transport": "HAPI_CSV",
        "requested_parameters": parameters,
        "info": {
            "path": str(info_path),
            "resolved_url": info_response.url,
            "sha256": sha256_bytes(info_raw),
            "size_bytes": len(info_raw),
        },
        "raw": {
            "path": str(raw_path),
            "resolved_url": data_response.url,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "rows": int(len(frame)),
        },
        "schema": {
            name: {
                key: parameter_map[name].get(key)
                for key in ("description", "units", "fill", "size", "type")
                if key in parameter_map[name]
            }
            for name in parameters
        },
    }
    return frame, metadata, parameter_map


def fetch_ncei_hapi(
    session: requests.Session,
    *,
    dataset_id: str,
    parameters: list[str],
    start: str,
    stop: str,
    outdir: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, dict[str, Any]]]:
    """Fetch a NOAA/NCEI HAPI product with strict provider field counts."""

    outdir.mkdir(parents=True, exist_ok=True)
    info_response = request_required(
        session,
        f"{NCEI_API_BASE}/hapi/info",
        params={"dataset": dataset_id},
        timeout=60,
    )
    info_raw = info_response.content
    info_path = outdir / "hapi_info.json"
    info_path.write_bytes(info_raw)
    try:
        info = info_response.json()
    except ValueError as exc:
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI /info is not JSON"
        ) from exc
    if info.get("status", {}).get("code") != 1200:
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI /info status is not 1200"
        )
    parameter_map = hapi_parameter_map(info)
    missing = [name for name in parameters if name not in parameter_map]
    if missing:
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI schema lacks parameters: {missing}"
        )

    data_response = request_required(
        session,
        f"{NCEI_API_BASE}/hapi/data",
        params={
            "dataset": dataset_id,
            "start": start,
            "stop": stop,
            "parameters": ",".join(parameters),
            "format": "csv",
        },
        timeout=120,
    )
    raw = data_response.content
    raw_path = outdir / "raw.csv"
    raw_path.write_bytes(raw)
    rows = [
        row
        for row in csv.reader(io.StringIO(raw.decode("utf-8-sig")))
        if row
    ]
    if not rows:
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI returned no rows"
        )
    bad_rows = [
        index
        for index, row in enumerate(rows, start=1)
        if len(row) != len(parameters)
    ]
    if bad_rows:
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI strict field-count mismatch "
            f"at rows {bad_rows[:10]}"
        )

    frame = pd.DataFrame(rows, columns=parameters)
    time_col = parameters[0]
    frame[time_col] = pd.to_datetime(
        frame[time_col], format="ISO8601", utc=True, errors="coerce"
    )
    if frame[time_col].isna().any():
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI contains invalid timestamps"
        )
    if frame[time_col].duplicated().any():
        raise MultipointAuditError(
            f"{dataset_id} NCEI HAPI contains duplicate timestamps"
        )
    if time_col != "time":
        frame.rename(columns={time_col: "time"}, inplace=True)

    for name in parameters[1:]:
        metadata = parameter_map[name]
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
        fill = metadata.get("fill")
        try:
            fill_number = float(fill) if fill is not None else None
        except (TypeError, ValueError):
            fill_number = None
        if fill_number is not None:
            frame.loc[frame[name] == fill_number, name] = np.nan
        frame.loc[frame[name].abs() >= FILL_ABS, name] = np.nan

    metadata = {
        "provider": "NOAA/NCEI",
        "dataset_id": dataset_id,
        "transport": "NCEI_HAPI_CSV",
        "requested_parameters": parameters,
        "availability": {
            "start": info.get("startDate"),
            "stop": info.get("stopDate"),
        },
        "info": {
            "path": str(info_path),
            "resolved_url": info_response.url,
            "sha256": sha256_bytes(info_raw),
            "size_bytes": len(info_raw),
        },
        "raw": {
            "path": str(raw_path),
            "resolved_url": data_response.url,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "rows": int(len(frame)),
        },
        "schema": {
            name: {
                key: parameter_map[name].get(key)
                for key in ("description", "units", "fill", "size", "type")
                if key in parameter_map[name]
            }
            for name in parameters
        },
    }
    return frame, metadata, parameter_map


def request_cdas_text(
    session: requests.Session,
    *,
    dataset_id: str,
    variables: list[str],
    start: str,
    stop: str,
    outdir: Path,
) -> tuple[bytes, dict[str, Any]]:
    outdir.mkdir(parents=True, exist_ok=True)
    start_text = to_utc(start).strftime("%Y%m%dT%H%M%SZ")
    stop_text = to_utc(stop).strftime("%Y%m%dT%H%M%SZ")
    descriptor_url = (
        f"{CDAS_BASE}/datasets/{dataset_id}/data/"
        f"{start_text},{stop_text}/{','.join(variables)}?format=text"
    )
    descriptor_response = request_required(
        session,
        descriptor_url,
        headers={"Accept": "application/json"},
    )
    descriptor_raw = descriptor_response.content
    descriptor_path = outdir / "descriptor.json"
    descriptor_path.write_bytes(descriptor_raw)
    descriptor = descriptor_response.json()
    descriptions = descriptor.get("FileDescription")
    if descriptions is None:
        descriptions = descriptor.get("DataResult", {}).get("FileDescription")
    if not descriptions or not descriptions[0].get("Name"):
        raise MultipointAuditError(
            f"{dataset_id} descriptor lacks a data file"
        )
    data_response = request_required(
        session,
        descriptions[0]["Name"],
        timeout=240,
    )
    raw = data_response.content
    raw_path = outdir / "raw.txt"
    raw_path.write_bytes(raw)
    return raw, {
        "dataset_id": dataset_id,
        "transport": "CDAS_TEXT",
        "requested_variables": variables,
        "descriptor": {
            "path": str(descriptor_path),
            "resolved_url": descriptor_response.url,
            "sha256": sha256_bytes(descriptor_raw),
            "size_bytes": len(descriptor_raw),
        },
        "raw": {
            "path": str(raw_path),
            "resolved_url": data_response.url,
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
        },
    }


def parse_cdas_rows(
    raw: bytes,
    *,
    columns: list[str],
) -> pd.DataFrame:
    lines = [
        line.rstrip()
        for line in raw.decode("utf-8", errors="strict").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    first_data = None
    for index, line in enumerate(lines):
        if len(line) >= 10 and line[2:3] == "-" and line[5:6] == "-":
            first_data = index
            break
    if first_data is None:
        raise MultipointAuditError("CDAS text contains no timestamped rows")
    frame = pd.read_csv(
        io.StringIO("\n".join(lines[first_data:])),
        sep=r"\s{2,}",
        engine="python",
        header=None,
        names=columns,
    )
    if len(frame.columns) != len(columns):
        raise MultipointAuditError("CDAS text column-count mismatch")
    frame["time"] = pd.to_datetime(
        frame["time"],
        dayfirst=True,
        utc=True,
        errors="coerce",
    )
    if frame["time"].isna().any():
        raise MultipointAuditError("CDAS text contains invalid timestamps")
    if frame["time"].duplicated().any():
        raise MultipointAuditError("CDAS text contains duplicate timestamps")
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.loc[frame[column].abs() >= FILL_ABS, column] = np.nan
    return frame


def resolve_dscovr_components(columns: list[str]) -> tuple[str, str, str]:
    result: list[str] = []
    for axis in ("BX", "BY", "BZ"):
        matches = [
            column
            for column in columns
            if axis in column.upper()
            and "GSE" in column.upper()
            and "SPHR" not in column.upper()
        ]
        if len(matches) != 1:
            raise MultipointAuditError(
                f"DSCOVR expected one {axis} GSE column; found {matches}"
            )
        result.append(matches[0])
    return result[0], result[1], result[2]


def canonicalize_vector_minutes(
    frame: pd.DataFrame,
    *,
    components: tuple[str, str, str],
    minimum_samples: int,
    source: str,
    position_components: tuple[str, str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = ["time", *components]
    if position_components:
        required.extend(position_components)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise MultipointAuditError(
            f"{source} vector input is missing columns: {missing}"
        )
    admitted = frame.dropna(subset=list(components)).copy()
    admitted["_minute"] = admitted["time"].dt.floor("min")
    grouped = admitted.groupby("_minute", sort=True)
    output = grouped[list(components)].mean()
    output["native_samples"] = grouped.size()
    if position_components:
        for column in position_components:
            output[column] = grouped[column].mean()
    output["minute_admitted"] = output["native_samples"] >= minimum_samples
    low = output.loc[~output["minute_admitted"]].reset_index()
    low["reason_code"] = "INSUFFICIENT_NATIVE_MINUTE_COVERAGE"
    output = output.loc[output["minute_admitted"]].copy()
    output.index.name = "time"
    output.reset_index(inplace=True)
    output.rename(
        columns={
            components[0]: "bx_gse_nT",
            components[1]: "by_gse_nT",
            components[2]: "bz_gse_nT",
        },
        inplace=True,
    )
    output["B_mag_nT"] = np.sqrt(
        output["bx_gse_nT"].pow(2)
        + output["by_gse_nT"].pow(2)
        + output["bz_gse_nT"].pow(2)
    )
    vectors = output[
        ["bx_gse_nT", "by_gse_nT", "bz_gse_nT"]
    ].to_numpy(dtype=float)
    rotation = np.full(len(output), np.nan)
    if len(output) > 1:
        previous = vectors[:-1]
        current = vectors[1:]
        denominator = (
            np.linalg.norm(previous, axis=1)
            * np.linalg.norm(current, axis=1)
        )
        cosine = np.einsum("ij,ij->i", previous, current) / denominator
        rotation[1:] = np.degrees(
            np.arccos(np.clip(cosine, -1.0, 1.0))
        )
    output["rotation_from_previous_minute_degrees"] = rotation
    output["minute_relative_magnitude_change"] = (
        output["B_mag_nT"].diff().abs()
        / output["B_mag_nT"].shift(1).abs()
    )
    output["source"] = source
    return output, low


def canonicalize_plasma_minutes(
    frame: pd.DataFrame,
    *,
    density_col: str,
    speed_col: str | None,
    velocity_components: tuple[str, str, str] | None,
    temperature_col: str,
    temperature_unit: str,
    minimum_samples: int,
    source: str,
    quality_mask: pd.Series | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    working = frame.copy()
    if quality_mask is not None:
        rejected = working.loc[~quality_mask].copy()
        rejected["reason_code"] = "SOURCE_QUALITY_REJECTED"
        working = working.loc[quality_mask].copy()
    else:
        rejected = pd.DataFrame()
    required = ["time", density_col, temperature_col]
    if speed_col:
        required.append(speed_col)
    if velocity_components:
        required.extend(velocity_components)
    missing = [column for column in required if column not in working.columns]
    if missing:
        raise MultipointAuditError(
            f"{source} plasma input is missing columns: {missing}"
        )
    numeric_required = [density_col, temperature_col]
    if speed_col:
        numeric_required.append(speed_col)
    if velocity_components:
        numeric_required.extend(velocity_components)
    working = working.dropna(subset=numeric_required).copy()
    working = working.loc[
        (working[density_col] > 0)
        & (working[temperature_col] > 0)
    ].copy()
    working["_minute"] = working["time"].dt.floor("min")
    grouped = working.groupby("_minute", sort=True)
    columns_to_average = [density_col, temperature_col]
    if speed_col:
        columns_to_average.append(speed_col)
    if velocity_components:
        columns_to_average.extend(velocity_components)
    output = grouped[columns_to_average].mean()
    output["native_samples"] = grouped.size()
    output["minute_admitted"] = output["native_samples"] >= minimum_samples
    low = output.loc[~output["minute_admitted"]].reset_index()
    low["reason_code"] = "INSUFFICIENT_PLASMA_MINUTE_COVERAGE"
    output = output.loc[output["minute_admitted"]].copy()
    output.index.name = "time"
    output.reset_index(inplace=True)
    output.rename(
        columns={
            density_col: "density_cm3",
            temperature_col: "temperature_native",
        },
        inplace=True,
    )
    if speed_col:
        output.rename(columns={speed_col: "speed_km_s"}, inplace=True)
    else:
        assert velocity_components is not None
        output["speed_km_s"] = np.sqrt(
            sum(output[column].pow(2) for column in velocity_components)
        )
    output["temperature_unit"] = temperature_unit
    output["source"] = source
    quarantine = pd.concat(
        [part for part in (rejected, low) if not part.empty],
        ignore_index=True,
        sort=False,
    ) if (not rejected.empty or not low.empty) else pd.DataFrame()
    return output, quarantine


def normalize_temperature_unit(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"k", "kelvin", "deg k", "degrees k"}:
        return "K"
    if normalized in {"ev", "electron volt", "electron volts"}:
        return "eV"
    raise MultipointAuditError(
        f"unsupported plasma temperature unit {value!r}"
    )


def add_plasma_physics(
    plasma: pd.DataFrame,
    magnetic: pd.DataFrame,
    *,
    beta_label: str,
) -> pd.DataFrame:
    merged = plasma.merge(
        magnetic[["time", "B_mag_nT"]],
        on="time",
        how="inner",
        validate="one_to_one",
    )
    density_m3 = merged["density_cm3"] * 1.0e6
    speed_ms = merged["speed_km_s"] * 1.0e3
    magnetic_t = merged["B_mag_nT"] * 1.0e-9
    if merged["temperature_unit"].nunique() != 1:
        raise MultipointAuditError("mixed plasma temperature units")
    unit = normalize_temperature_unit(
        str(merged["temperature_unit"].iloc[0])
    )
    merged["temperature_unit"] = unit
    if unit == "K":
        thermal_energy_j = BOLTZMANN_J_K * merged["temperature_native"]
    elif unit == "eV":
        thermal_energy_j = ELEMENTARY_CHARGE_C * merged["temperature_native"]
    else:
        raise MultipointAuditError(
            f"unsupported plasma temperature unit {unit!r}"
        )
    merged["dynamic_pressure_nPa"] = (
        density_m3 * PROTON_MASS_KG * speed_ms.pow(2) * 1.0e9
    )
    merged[beta_label] = (
        2.0 * MU0 * density_m3 * thermal_energy_j / magnetic_t.pow(2)
    )
    merged.loc[~np.isfinite(merged[beta_label]), beta_label] = np.nan
    return merged


def select_structure(
    magnetic: pd.DataFrame,
    *,
    center: pd.Timestamp,
    half_window_minutes: int = 10,
) -> dict[str, Any] | None:
    lower = center - pd.Timedelta(minutes=half_window_minutes)
    upper = center + pd.Timedelta(minutes=half_window_minutes)
    candidates = magnetic.loc[
        magnetic["time"].between(lower, upper, inclusive="both")
        & (
            (
                magnetic["rotation_from_previous_minute_degrees"]
                >= 45.0
            )
            | (
                magnetic["minute_relative_magnitude_change"]
                >= 0.25
            )
        )
    ].copy()
    if candidates.empty:
        return None
    candidates["selection_score"] = np.maximum(
        candidates[
            "rotation_from_previous_minute_degrees"
        ].fillna(0.0)
        / 45.0,
        candidates[
            "minute_relative_magnitude_change"
        ].fillna(0.0)
        / 0.25,
    )
    candidates["absolute_offset_minutes"] = (
        candidates["time"] - center
    ).abs().dt.total_seconds() / 60.0
    selected = candidates.sort_values(
        ["selection_score", "absolute_offset_minutes", "time"],
        ascending=[False, True, True],
    ).iloc[0]
    previous = magnetic.loc[
        magnetic["time"]
        == selected["time"] - pd.Timedelta(minutes=1)
    ]
    previous_row = previous.iloc[0] if len(previous) == 1 else None
    return {
        "time_utc": selected["time"].isoformat(),
        "offset_from_dscovr_minutes": (
            selected["time"] - center
        ).total_seconds()
        / 60.0,
        "selection_score": float(selected["selection_score"]),
        "bx_gse_nT": float(selected["bx_gse_nT"]),
        "by_gse_nT": float(selected["by_gse_nT"]),
        "bz_gse_nT": float(selected["bz_gse_nT"]),
        "B_mag_nT": float(selected["B_mag_nT"]),
        "rotation_degrees": float(
            selected["rotation_from_previous_minute_degrees"]
        ),
        "magnitude_change_fraction": float(
            selected["minute_relative_magnitude_change"]
        ),
        "native_samples": int(selected["native_samples"]),
        "previous": (
            {
                "time_utc": previous_row["time"].isoformat(),
                "bx_gse_nT": float(previous_row["bx_gse_nT"]),
                "by_gse_nT": float(previous_row["by_gse_nT"]),
                "bz_gse_nT": float(previous_row["bz_gse_nT"]),
                "B_mag_nT": float(previous_row["B_mag_nT"]),
            }
            if previous_row is not None
            else None
        ),
    }


def classify_multipoint(
    selected: dict[str, dict[str, Any] | None],
    *,
    maximum_independent_offset_minutes: float = 5.0,
) -> str:
    independent_offsets = [
        abs(float(selected[mission]["offset_from_dscovr_minutes"]))
        for mission in ("ACE", "WIND")
        if selected.get(mission) is not None
    ]
    if (
        len(independent_offsets) == 2
        and all(
            offset <= maximum_independent_offset_minutes
            for offset in independent_offsets
        )
    ):
        return (
            "MULTIPOINT_COMPLEX_VECTOR_STRUCTURE_CANDIDATE_"
            "TIMING_UNRESOLVED"
        )
    if independent_offsets:
        return (
            "PARTIAL_MULTIPOINT_VECTOR_STRUCTURE_CANDIDATE_"
            "TIMING_UNRESOLVED"
        )
    return "DSCOVR_ONLY_VECTOR_STRUCTURE_CANDIDATE"


def plasma_cut(
    plasma: pd.DataFrame,
    *,
    center: pd.Timestamp,
    beta_column: str,
) -> dict[str, Any]:
    pre = plasma.loc[
        (plasma["time"] >= center - pd.Timedelta(minutes=3))
        & (plasma["time"] < center)
    ]
    post = plasma.loc[
        (plasma["time"] >= center)
        & (plasma["time"] < center + pd.Timedelta(minutes=3))
    ]
    fields = [
        "density_cm3",
        "speed_km_s",
        "temperature_native",
        "dynamic_pressure_nPa",
        beta_column,
    ]
    result: dict[str, Any] = {
        "center_time_utc": center.isoformat(),
        "temperature_unit": (
            str(plasma["temperature_unit"].iloc[0])
            if len(plasma)
            else None
        ),
        "pre_rows": int(len(pre)),
        "post_rows": int(len(post)),
        "pre_median": {},
        "post_median": {},
        "post_to_pre_ratio": {},
    }
    for field in fields:
        pre_value = (
            float(pre[field].median()) if pre[field].notna().any() else None
        )
        post_value = (
            float(post[field].median()) if post[field].notna().any() else None
        )
        result["pre_median"][field] = pre_value
        result["post_median"][field] = post_value
        result["post_to_pre_ratio"][field] = (
            post_value / pre_value
            if (
                pre_value is not None
                and post_value is not None
                and pre_value != 0
            )
            else None
        )
    return result


def build_chart(
    magnetic_by_mission: dict[str, pd.DataFrame],
    *,
    candidate: pd.Timestamp,
    outdir: Path,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    plt.figure(figsize=(12, 5))
    for mission, frame in magnetic_by_mission.items():
        plt.plot(frame["time"], frame["bz_gse_nT"], label=mission)
    plt.axvline(candidate, linestyle="--", label="DSCOVR candidate")
    plt.xlabel("UTC")
    plt.ylabel("Bz GSE (nT)")
    plt.title("Gannon 2024 Multipoint Bz GSE")
    plt.legend()
    plt.tight_layout()
    path = outdir / "multipoint_bz_gse.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)

    plt.figure(figsize=(12, 5))
    for mission, frame in magnetic_by_mission.items():
        plt.plot(
            frame["time"],
            frame["rotation_from_previous_minute_degrees"],
            label=mission,
        )
    plt.axhline(45.0, linestyle=":", label="45 degree candidate gate")
    plt.axvline(candidate, linestyle="--", label="DSCOVR candidate")
    plt.xlabel("UTC")
    plt.ylabel("One-minute GSE vector rotation (degrees)")
    plt.title("Gannon 2024 Multipoint Vector Rotations")
    plt.legend()
    plt.tight_layout()
    path = outdir / "multipoint_vector_rotation.png"
    plt.savefig(path, dpi=180)
    plt.close()
    paths.append(path)
    return paths


def run_audit(
    *,
    start: str,
    stop: str,
    candidate_time: str,
    outdir: Path,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    raw_root = outdir / "raw"
    canonical_root = outdir / "canonical"
    quarantine_root = outdir / "quarantine"
    report_root = outdir / "reports"
    chart_root = outdir / "charts"
    for path in (
        raw_root,
        canonical_root,
        quarantine_root,
        report_root,
        chart_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-MULTIPOINT/{AUDIT_VERSION}"}
    )
    candidate = to_utc(candidate_time)
    manifest: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "status": "STARTED",
        "started_utc": utc_now(),
        "runtime": {"python": platform.python_version()},
        "window": {"start": start, "stop": stop},
        "dscovr_candidate_time_utc": candidate.isoformat(),
        "source_metadata": {},
        "classification": None,
        "interpretation_limits": [
            "same-window structures do not prove one-to-one propagation",
            "spacecraft are not co-located",
            "DSCOVR MAG is NASA CDAWeb while DSCOVR plasma is NOAA/NCEI f1m",
            "GSE rotation is not a GSM clock-angle claim",
            "plasma beta values inherit each product's temperature definition",
            "Wind 3DP plasma is context-only because HAPI omits CDAS VALID/GAP flags",
            "no interpolation or forward fill is used",
        ],
    }
    manifest_path = outdir / "gannon_multipoint_manifest.json"
    write_json(manifest_path, manifest)

    try:
        dscovr_dir = raw_root / "DSCOVR_H0_MAG"
        dscovr_dir.mkdir(parents=True, exist_ok=True)
        dscovr_raw, dscovr_meta = download_dscovr(
            format_cdaweb_date(start),
            format_cdaweb_date(stop),
            dscovr_dir,
        )
        dscovr_canonical, dscovr_metrics = canonicalize_dscovr(
            dscovr_raw,
            dscovr_dir,
        )
        bx, by, bz = resolve_dscovr_components(
            list(dscovr_canonical.columns)
        )
        dscovr_mag_input = dscovr_canonical.rename(
            columns={
                "EPOCH": "time",
                bx: "bx",
                by: "by",
                bz: "bz",
            }
        )
        dscovr_mag, _ = canonicalize_vector_minutes(
            dscovr_mag_input,
            components=("bx", "by", "bz"),
            minimum_samples=1,
            source="DSCOVR_H0_MAG",
        )
        manifest["source_metadata"]["DSCOVR_H0_MAG"] = {
            **dscovr_meta,
            "canonicalization_metrics": dscovr_metrics,
        }

        dscovr_plasma_raw, dscovr_plasma_meta, dscovr_plasma_schema = (
            fetch_ncei_hapi(
                session,
                dataset_id="f1m_dscovr",
                parameters=[
                    "time",
                    "overall_quality",
                    "proton_density",
                    "proton_speed",
                    "proton_temperature",
                    "proton_vx_gse",
                    "proton_vy_gse",
                    "proton_vz_gse",
                ],
                start=start,
                stop=stop,
                outdir=raw_root / "f1m_dscovr",
            )
        )
        dscovr_quality = dscovr_plasma_raw["overall_quality"] == 0
        dscovr_plasma, dscovr_plasma_quarantine = (
            canonicalize_plasma_minutes(
                dscovr_plasma_raw,
                density_col="proton_density",
                speed_col="proton_speed",
                velocity_components=None,
                temperature_col="proton_temperature",
                temperature_unit=str(
                    dscovr_plasma_schema["proton_temperature"].get("units")
                ),
                minimum_samples=1,
                source="NOAA_NCEI_f1m_dscovr",
                quality_mask=dscovr_quality,
            )
        )
        dscovr_plasma_meta["quality_scope"] = {
            "admission": "overall_quality == 0",
            "meaning": "provider normal sample; suspect and error rows rejected",
            "role": (
                "current 2024 DSCOVR one-minute plasma context; the canonical "
                "magnetic path remains NASA CDAWeb DSCOVR_H0_MAG"
            ),
        }
        manifest["source_metadata"]["f1m_dscovr"] = dscovr_plasma_meta

        ace_mag_raw, ace_mag_meta, _ = fetch_hapi(
            session,
            dataset_id="AC_H0_MFI",
            parameters=["Magnitude", "BGSEc", "SC_pos_GSE"],
            start=start,
            stop=stop,
            outdir=raw_root / "AC_H0_MFI",
        )
        ace_mag, ace_mag_quarantine = canonicalize_vector_minutes(
            ace_mag_raw,
            components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
            position_components=(
                "SC_pos_GSE_x",
                "SC_pos_GSE_y",
                "SC_pos_GSE_z",
            ),
            minimum_samples=3,
            source="AC_H0_MFI",
        )
        manifest["source_metadata"]["AC_H0_MFI"] = ace_mag_meta

        ace_plasma_raw, ace_plasma_meta, ace_plasma_schema = fetch_hapi(
            session,
            dataset_id="AC_H0_SWE",
            parameters=["Np", "Vp", "Tpr", "V_GSE", "SC_pos_GSE"],
            start=start,
            stop=stop,
            outdir=raw_root / "AC_H0_SWE",
        )
        ace_plasma, ace_plasma_quarantine = canonicalize_plasma_minutes(
            ace_plasma_raw,
            density_col="Np",
            speed_col="Vp",
            velocity_components=None,
            temperature_col="Tpr",
            temperature_unit=str(ace_plasma_schema["Tpr"].get("units")),
            minimum_samples=1,
            source="AC_H0_SWE",
        )
        manifest["source_metadata"]["AC_H0_SWE"] = ace_plasma_meta

        wind_mag_raw, wind_mag_meta = request_cdas_text(
            session,
            dataset_id="WI_H0_MFI",
            variables=["B3GSE", "B3F1"],
            start=start,
            stop=stop,
            outdir=raw_root / "WI_H0_MFI",
        )
        wind_mag_table = parse_cdas_rows(
            wind_mag_raw,
            columns=[
                "time",
                "reported_B3F1_nT",
                "B3GSE_x",
                "B3GSE_y",
                "B3GSE_z",
            ],
        )
        wind_mag, wind_mag_quarantine = canonicalize_vector_minutes(
            wind_mag_table,
            components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
            minimum_samples=18,
            source="WI_H0_MFI",
        )
        manifest["source_metadata"]["WI_H0_MFI"] = wind_mag_meta

        wind_3dp_raw, wind_3dp_meta, wind_3dp_schema = fetch_hapi(
            session,
            dataset_id="WI_PM_3DP",
            parameters=["P_DENS", "P_VELS", "P_TEMP"],
            start=start,
            stop=stop,
            outdir=raw_root / "WI_PM_3DP",
        )
        wind_plasma, wind_plasma_quarantine = (
            canonicalize_plasma_minutes(
                wind_3dp_raw,
                density_col="P_DENS",
                speed_col=None,
                velocity_components=(
                    "P_VELS_x",
                    "P_VELS_y",
                    "P_VELS_z",
                ),
                temperature_col="P_TEMP",
                temperature_unit=str(
                    wind_3dp_schema["P_TEMP"].get("units")
                ),
                minimum_samples=18,
                source="WI_PM_3DP",
            )
        )
        wind_3dp_meta["quality_scope"] = {
            "state": "CONTEXT_ONLY",
            "reason": (
                "the HAPI projection does not expose the CDAS VALID/GAP "
                "quality variables; the fitted WI_H1_SWE record is retained "
                "as an independent quality context"
            ),
        }
        manifest["source_metadata"]["WI_PM_3DP"] = wind_3dp_meta

        wind_swe_raw, wind_swe_meta, _ = fetch_hapi(
            session,
            dataset_id="WI_H1_SWE",
            parameters=[
                "fit_flag",
                "Proton_V_nonlin",
                "Proton_W_nonlin",
                "Proton_Np_nonlin",
                "xgse",
                "ygse",
                "zgse",
            ],
            start=start,
            stop=stop,
            outdir=raw_root / "WI_H1_SWE",
        )
        wind_swe_raw["proton_fit_admitted"] = (
            wind_swe_raw["fit_flag"] > 1
        )
        wind_swe_raw.to_csv(
            canonical_root / "wind_swe_fitted_context.csv",
            index=False,
        )
        wind_swe_meta["quality_scope"] = {
            "fit_admission": "fit_flag > 1 (provider definition: P_OK)",
            "thermal_speed_conversion_applied": False,
        }
        manifest["source_metadata"]["WI_H1_SWE"] = wind_swe_meta

        dscovr_physics = add_plasma_physics(
            dscovr_plasma,
            dscovr_mag,
            beta_label="proton_beta_ncei_temperature",
        )
        ace_physics = add_plasma_physics(
            ace_plasma,
            ace_mag,
            beta_label="proton_beta_radial_temperature_proxy",
        )
        wind_physics = add_plasma_physics(
            wind_plasma,
            wind_mag,
            beta_label="proton_beta_3dp_temperature",
        )

        magnetic_by_mission = {
            "DSCOVR": dscovr_mag,
            "ACE": ace_mag,
            "WIND": wind_mag,
        }
        physics_by_mission = {
            "DSCOVR": (
                dscovr_physics,
                "proton_beta_ncei_temperature",
            ),
            "ACE": (
                ace_physics,
                "proton_beta_radial_temperature_proxy",
            ),
            "WIND": (
                wind_physics,
                "proton_beta_3dp_temperature",
            ),
        }

        for mission, frame in magnetic_by_mission.items():
            frame.to_csv(
                canonical_root / f"{mission.lower()}_mag_one_minute.csv",
                index=False,
            )
        for mission, (frame, _) in physics_by_mission.items():
            frame.to_csv(
                canonical_root / f"{mission.lower()}_plasma_one_minute.csv",
                index=False,
            )

        quarantine_frames = {
            "dscovr_plasma": dscovr_plasma_quarantine,
            "ace_mag": ace_mag_quarantine,
            "ace_plasma": ace_plasma_quarantine,
            "wind_mag": wind_mag_quarantine,
            "wind_plasma": wind_plasma_quarantine,
        }
        for name, frame in quarantine_frames.items():
            frame.to_csv(
                quarantine_root / f"{name}_quarantine.csv",
                index=False,
            )

        selected: dict[str, Any] = {}
        for mission, frame in magnetic_by_mission.items():
            if mission == "DSCOVR":
                row = frame.loc[frame["time"] == candidate]
                if len(row) != 1:
                    raise MultipointAuditError(
                        "DSCOVR candidate minute is absent"
                    )
                selected[mission] = select_structure(
                    frame,
                    center=candidate,
                    half_window_minutes=0,
                )
            else:
                selected[mission] = select_structure(
                    frame,
                    center=candidate,
                    half_window_minutes=10,
                )

        classification = classify_multipoint(selected)

        plasma_cuts: dict[str, Any] = {}
        for mission, selection in selected.items():
            if selection is None:
                plasma_cuts[mission] = None
                continue
            center = to_utc(selection["time_utc"])
            physics, beta_column = physics_by_mission[mission]
            plasma_cuts[mission] = plasma_cut(
                physics,
                center=center,
                beta_column=beta_column,
            )

        wind_fit_context = None
        if selected["WIND"] is not None:
            wind_center = to_utc(selected["WIND"]["time_utc"])
            fitted = wind_swe_raw.loc[
                wind_swe_raw["proton_fit_admitted"]
            ].copy()
            if not fitted.empty:
                fitted["offset_seconds"] = (
                    fitted["time"] - wind_center
                ).abs().dt.total_seconds()
                nearest = fitted.sort_values("offset_seconds").iloc[0]
                wind_fit_context = {
                    "time_utc": nearest["time"].isoformat(),
                    "offset_seconds": float(nearest["offset_seconds"]),
                    "fit_flag": int(nearest["fit_flag"]),
                    "Proton_V_nonlin_km_s": float(
                        nearest["Proton_V_nonlin"]
                    ),
                    "Proton_W_nonlin_km_s": float(
                        nearest["Proton_W_nonlin"]
                    ),
                    "Proton_Np_nonlin_cm3": float(
                        nearest["Proton_Np_nonlin"]
                    ),
                    "position_gse_Re": [
                        float(nearest["xgse"]),
                        float(nearest["ygse"]),
                        float(nearest["zgse"]),
                    ],
                    "temperature_conversion_applied": False,
                }

        chart_paths = build_chart(
            magnetic_by_mission,
            candidate=candidate,
            outdir=chart_root,
        )

        manifest.update(
            {
                "status": "SUCCESS",
                "completed_utc": utc_now(),
                "classification": classification,
                "selected_structures": selected,
                "plasma_cuts": plasma_cuts,
                "wind_fitted_swe_context": wind_fit_context,
                "chart_paths": [str(path) for path in chart_paths],
                "mechanism_classification": "UNRESOLVED",
                "ephemeris_propagation_test_completed": False,
            }
        )

        rows: list[dict[str, Any]] = []
        for mission, selection in selected.items():
            if selection is None:
                rows.append(
                    {
                        "mission": mission,
                        "structure_found": False,
                    }
                )
                continue
            cut = plasma_cuts[mission]
            rows.append(
                {
                    "mission": mission,
                    "structure_found": True,
                    **{
                        key: value
                        for key, value in selection.items()
                        if key != "previous"
                    },
                    "previous_bz_gse_nT": (
                        selection["previous"]["bz_gse_nT"]
                        if selection.get("previous")
                        else None
                    ),
                    "density_post_to_pre": (
                        cut["post_to_pre_ratio"]["density_cm3"]
                        if cut
                        else None
                    ),
                    "speed_post_to_pre": (
                        cut["post_to_pre_ratio"]["speed_km_s"]
                        if cut
                        else None
                    ),
                    "temperature_post_to_pre": (
                        cut["post_to_pre_ratio"]["temperature_native"]
                        if cut
                        else None
                    ),
                    "dynamic_pressure_post_to_pre": (
                        cut["post_to_pre_ratio"]["dynamic_pressure_nPa"]
                        if cut
                        else None
                    ),
                }
            )
        table = pd.DataFrame(rows)
        table_path = report_root / "gannon_multipoint_summary.csv"
        table.to_csv(table_path, index=False)

        report_path = report_root / "GANNON_MULTIPOINT_AUDIT.md"
        report_lines = [
            "# Gannon 2024 Three-Spacecraft MAG-plus-Plasma Audit",
            "",
            f"Classification: **{classification}**",
            "",
            "The classification means that DSCOVR, ACE, and Wind each recorded",
            "large GSE-vector structure within five minutes of the named DSCOVR",
            "candidate. It does not yet identify one discontinuity or establish",
            "propagation.",
            "",
            "## Selected magnetic structures",
            "",
            "```text",
            table.to_string(index=False),
            "```",
            "",
            "## Interpretation limits",
            "",
            "- GSE rotations are not GSM clock angles.",
            "- The spacecraft are not co-located.",
            "- DSCOVR MAG and plasma use separately identified NASA/NOAA products.",
            "- Plasma products use different temperature definitions.",
            "- Wind 3DP plasma is context-only because HAPI omits CDAS quality flags.",
            "- No interpolation or forward fill was used.",
            "- Ephemeris-based propagation remains unresolved.",
        ]
        report_path.write_text(
            "\n".join(report_lines) + "\n",
            encoding="utf-8",
        )

        artifacts: list[dict[str, Any]] = []
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
        manifest["status"] = "FAILED"
        manifest["completed_utc"] = utc_now()
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        write_json(manifest_path, manifest)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--stop", default=DEFAULT_STOP)
    parser.add_argument("--candidate-time", default=DEFAULT_CANDIDATE)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/audits/gannon_multipoint"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_audit(
        start=args.start,
        stop=args.stop,
        candidate_time=args.candidate_time,
        outdir=args.outdir,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "classification": manifest["classification"],
                "outdir": str(args.outdir),
            }
        )
    )


if __name__ == "__main__":
    main()
