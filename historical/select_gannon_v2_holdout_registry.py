#!/usr/bin/env python3
"""Build the Gannon V2 holdout registry without retrieving spacecraft MAG.

Selection inputs are limited to GFZ Kp, the Helsinki IPShocks catalog, and the
Richardson/Cane near-Earth ICME catalog. The output freezes dates, classes,
selection evidence, mission-era tags, source hashes, and replacement policy.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests

SELECTOR_VERSION = "1.0.0"
RETRY_STATUS = {429, 500, 502, 503, 504}
FORBIDDEN_MAG_TOKENS = (
    "DSCOVR_H0_MAG",
    "AC_H0_MFI",
    "WI_H0_MFI",
    "gate_pass",
    "chi_B24M",
)


class HoldoutSelectionError(RuntimeError):
    """Raised when a prospective registry cannot be frozen fail-closed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def to_utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )


def request_bytes(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 180,
    attempts: int = 5,
) -> tuple[bytes, str, int]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code in RETRY_STATUS and attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 12))
                continue
            response.raise_for_status()
            return response.content, response.url, response.status_code
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 12))
                continue
            break
    raise HoldoutSelectionError(f"source request failed for {url}: {last_error}")


def load_contract(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "selection_contract_id",
        "selection_contract_version",
        "status",
        "parent_preregistration",
        "sources",
        "global_rules",
        "mission_eras",
        "classes",
        "freeze_policy",
    }
    missing = sorted(required - set(data))
    if missing:
        raise HoldoutSelectionError(f"selection contract lacks keys: {missing}")
    if data["status"] != "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL":
        raise HoldoutSelectionError("selection contract is not frozen")
    for class_name, policy in data["classes"].items():
        target = int(policy["target_count"])
        if sum(int(value) for value in policy["era_targets"].values()) != target:
            raise HoldoutSelectionError(
                f"{class_name} era targets do not sum to target_count"
            )
    return data


def parse_gfz_kp(raw: bytes) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw.decode("utf-8", errors="strict").splitlines(), start=1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 15:
            raise HoldoutSelectionError(
                f"GFZ Kp row {line_number} has fewer than 15 fields"
            )
        try:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
            values = [float(item) for item in parts[7:15]]
        except ValueError as exc:
            raise HoldoutSelectionError(
                f"GFZ Kp row {line_number} cannot be parsed"
            ) from exc
        if len(values) != 8 or not all(
            math.isfinite(item) and 0.0 <= item <= 9.0 for item in values
        ):
            raise HoldoutSelectionError(
                f"GFZ Kp row {line_number} contains invalid Kp values"
            )
        date = pd.Timestamp(year=year, month=month, day=day, tz="UTC")
        rows.append(
            {
                "date_utc": date,
                "kp_values": values,
                "kp_max": max(values),
                "kp_mean": sum(values) / 8.0,
            }
        )
    frame = pd.DataFrame(rows).sort_values("date_utc").reset_index(drop=True)
    if frame.empty or frame["date_utc"].duplicated().any():
        raise HoldoutSelectionError("GFZ Kp data are empty or contain duplicate days")
    return frame


def normalize_spacecraft(value: Any) -> str:
    text = str(value).strip().upper().replace("-", "")
    mapping = {
        "WIND": "WIND",
        "ACE": "ACE",
        "DSCOVR": "DSCOVR",
        "OMNI": "OMNI",
    }
    return mapping.get(text, text)


def parse_ipshocks(raw: bytes, *, cluster_minutes: int) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(raw))
    required = {
        "Year",
        "Month (1-12)",
        "Day (1-31)",
        "Hour (0-23)",
        "Minute (0-59)",
        "Second (0-59)",
        "Spacecraft",
        "Shock Type",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise HoldoutSelectionError(f"IPShocks schema lacks columns: {missing}")
    frame["time"] = pd.to_datetime(
        {
            "year": frame["Year"],
            "month": frame["Month (1-12)"],
            "day": frame["Day (1-31)"],
            "hour": frame["Hour (0-23)"],
            "minute": frame["Minute (0-59)"],
            "second": frame["Second (0-59)"],
        },
        utc=True,
        errors="coerce",
    )
    if frame["time"].isna().any():
        raise HoldoutSelectionError("IPShocks contains invalid timestamps")
    frame["spacecraft_norm"] = frame["Spacecraft"].map(normalize_spacecraft)
    frame = frame.loc[
        frame["spacecraft_norm"].isin({"ACE", "WIND", "DSCOVR", "OMNI"})
    ].sort_values("time").reset_index(drop=True)
    if frame.empty:
        raise HoldoutSelectionError("IPShocks contains no L1/OMNI rows")

    threshold = pd.Timedelta(minutes=cluster_minutes)
    cluster_ids: list[int] = []
    cluster_id = 0
    previous: pd.Timestamp | None = None
    for timestamp in frame["time"]:
        if previous is None or timestamp - previous > threshold:
            cluster_id += 1
        cluster_ids.append(cluster_id)
        previous = timestamp
    frame["cluster_id"] = cluster_ids

    records: list[dict[str, Any]] = []
    for cluster, group in frame.groupby("cluster_id", sort=True):
        first_time = group["time"].min()
        last_time = group["time"].max()
        reference = first_time + (last_time - first_time) / 2
        named = sorted(
            item for item in set(group["spacecraft_norm"]) if item != "OMNI"
        )
        records.append(
            {
                "cluster_id": int(cluster),
                "reference_time_utc": reference,
                "first_time_utc": group["time"].min(),
                "last_time_utc": group["time"].max(),
                "catalog_rows": int(len(group)),
                "named_spacecraft": named,
                "all_source_labels": sorted(set(group["spacecraft_norm"])),
                "shock_types": sorted(set(group["Shock Type"].astype(str))),
                "catalog_row_numbers": [int(index) + 2 for index in group.index],
            }
        )
    clusters = pd.DataFrame(records).sort_values("reference_time_utc").reset_index(drop=True)
    separations: list[float] = []
    for index, timestamp in enumerate(clusters["reference_time_utc"]):
        gaps: list[float] = []
        if index:
            gaps.append(
                (timestamp - clusters.loc[index - 1, "reference_time_utc"]).total_seconds()
                / 3600.0
            )
        if index + 1 < len(clusters):
            gaps.append(
                (clusters.loc[index + 1, "reference_time_utc"] - timestamp).total_seconds()
                / 3600.0
            )
        separations.append(min(gaps) if gaps else float("inf"))
    clusters["nearest_ipshock_gap_hours"] = separations
    return clusters


_DATETIME_RE = re.compile(
    r"(?P<year>20\d{2})[/'](?P<month>\d{2})/(?P<day>\d{2})\s+(?P<hhmm>\d{4})"
)


def extract_datetimes(text: Any) -> list[pd.Timestamp]:
    values: list[pd.Timestamp] = []
    for match in _DATETIME_RE.finditer(str(text)):
        hhmm = match.group("hhmm")
        try:
            values.append(
                pd.Timestamp(
                    year=int(match.group("year")),
                    month=int(match.group("month")),
                    day=int(match.group("day")),
                    hour=int(hhmm[:2]),
                    minute=int(hhmm[2:]),
                    tz="UTC",
                )
            )
        except ValueError:
            continue
    return values


def parse_richardson_cane(raw: bytes) -> pd.DataFrame:
    try:
        tables = pd.read_html(io.BytesIO(raw))
    except Exception as exc:
        raise HoldoutSelectionError(
            f"Richardson/Cane HTML table cannot be parsed: {exc}"
        ) from exc
    chosen: pd.DataFrame | None = None
    for table in tables:
        if table.shape[1] >= 10 and table.astype(str).apply(
            lambda column: column.str.contains(r"20\d{2}/\d{2}/\d{2}", regex=True).any()
        ).any():
            chosen = table
            break
    if chosen is None:
        raise HoldoutSelectionError("Richardson/Cane ICME table was not found")
    chosen.columns = [
        " | ".join(str(part) for part in column if str(part) != "nan")
        if isinstance(column, tuple)
        else str(column)
        for column in chosen.columns
    ]
    records: list[dict[str, Any]] = []
    for source_row, (_, row) in enumerate(chosen.iterrows(), start=1):
        cells = [str(value) for value in row.tolist()]
        first_times = extract_datetimes(cells[0] if cells else "")
        if not first_times:
            continue
        disturbance = first_times[0]
        second_times = extract_datetimes(cells[1] if len(cells) > 1 else "")
        cme_times = extract_datetimes(cells[-1] if cells else "")
        records.append(
            {
                "source_row": source_row,
                "disturbance_time_utc": disturbance,
                "icme_start_utc": second_times[0] if second_times else pd.NaT,
                "icme_end_utc": second_times[1] if len(second_times) > 1 else pd.NaT,
                "linked_cme_times": [value.isoformat() for value in cme_times],
                "linked_cme_count": len({value.isoformat() for value in cme_times}),
                "row_excerpt": " | ".join(cells)[:2000],
            }
        )
    frame = pd.DataFrame(records).sort_values("disturbance_time_utc").reset_index(drop=True)
    if frame.empty:
        raise HoldoutSelectionError("Richardson/Cane parsing produced no events")
    return frame


def build_complex_clusters(rc: pd.DataFrame, *, gap_hours: int) -> pd.DataFrame:
    threshold = pd.Timedelta(hours=gap_hours)
    cluster_ids: list[int] = []
    cluster_id = 0
    previous: pd.Timestamp | None = None
    for timestamp in rc["disturbance_time_utc"]:
        if previous is None or timestamp - previous > threshold:
            cluster_id += 1
        cluster_ids.append(cluster_id)
        previous = timestamp
    working = rc.copy()
    working["complex_cluster_id"] = cluster_ids
    records: list[dict[str, Any]] = []
    for cluster, group in working.groupby("complex_cluster_id", sort=True):
        linked = sorted(
            {
                value
                for values in group["linked_cme_times"]
                for value in values
            }
        )
        records.append(
            {
                "complex_cluster_id": int(cluster),
                "first_disturbance_utc": group["disturbance_time_utc"].min(),
                "last_disturbance_utc": group["disturbance_time_utc"].max(),
                "catalog_entry_count": int(len(group)),
                "linked_cme_count": int(len(linked)),
                "linked_cme_times": linked,
                "source_rows": [int(value) for value in group["source_row"]],
                "row_excerpts": group["row_excerpt"].tolist(),
                "qualifies_complex": bool(len(group) >= 2 or len(linked) >= 2),
            }
        )
    return pd.DataFrame(records)


def era_for(timestamp: pd.Timestamp, eras: list[dict[str, Any]]) -> str | None:
    value = to_utc(timestamp)
    for era in eras:
        if to_utc(era["start_utc"]) <= value < to_utc(era["stop_utc"]):
            return str(era["id"])
    return None


def is_excluded_year(timestamp: pd.Timestamp, excluded_years: set[int]) -> bool:
    return int(to_utc(timestamp).year) in excluded_years


def event_near_day(
    day: pd.Timestamp,
    event_times: Iterable[pd.Timestamp],
    *,
    half_width_hours: int,
) -> bool:
    lower = day - pd.Timedelta(hours=half_width_hours)
    upper = day + pd.Timedelta(days=1, hours=half_width_hours)
    return any(lower <= to_utc(event) < upper for event in event_times)


def select_spaced(
    candidates: pd.DataFrame,
    *,
    target: int,
    timestamp_column: str,
    minimum_spacing_days: int,
) -> pd.DataFrame:
    selected: list[pd.Series] = []
    for _, row in candidates.iterrows():
        timestamp = to_utc(row[timestamp_column])
        if all(
            abs((timestamp - to_utc(existing[timestamp_column])).total_seconds())
            >= minimum_spacing_days * 86400
            for existing in selected
        ):
            selected.append(row)
        if len(selected) == target:
            break
    if len(selected) != target:
        raise HoldoutSelectionError(
            f"only {len(selected)} candidates met spacing; required {target}"
        )
    return pd.DataFrame(selected).reset_index(drop=True)


def select_by_era(
    candidates: pd.DataFrame,
    *,
    era_targets: dict[str, int],
    timestamp_column: str,
    sort_columns: list[str],
    ascending: list[bool],
    minimum_spacing_days: int,
) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    for era, target in era_targets.items():
        target = int(target)
        if target == 0:
            continue
        pool = candidates.loc[candidates["mission_era"] == era].sort_values(
            sort_columns, ascending=ascending
        )
        selected.append(
            select_spaced(
                pool,
                target=target,
                timestamp_column=timestamp_column,
                minimum_spacing_days=minimum_spacing_days,
            )
        )
    result = pd.concat(selected, ignore_index=True, sort=False)
    if len(result) != sum(int(value) for value in era_targets.values()):
        raise HoldoutSelectionError("era selection count mismatch")
    return result


def interval_common(
    *,
    interval_id: str,
    class_name: str,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    mission_era: str,
    selection_rule: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "interval_id": interval_id,
        "class": class_name,
        "start_utc": to_utc(start).isoformat(),
        "stop_utc": to_utc(stop).isoformat(),
        "mission_era": mission_era,
        "mission_availability_expectation": "DSCOVR_ACE_WIND_EXPECTED_UNVERIFIED",
        "selection_rule": selection_rule,
        "independent_selection_evidence": evidence,
        "spacecraft_mag_retrieved_before_freeze": False,
        "gate_outputs_inspected_before_freeze": False,
        "candidate_scores_inspected_before_freeze": False,
        "replacement_after_later_failure_allowed": False,
        "initial_retrieval_state": "NOT_ATTEMPTED_REGISTRY_ONLY",
    }


def build_kp_intervals(
    kp: pd.DataFrame,
    *,
    class_name: str,
    contract: dict[str, Any],
    event_times: list[pd.Timestamp],
) -> list[dict[str, Any]]:
    policy = contract["classes"][class_name]
    rules = contract["global_rules"]
    start_year = int(rules["selection_year_start"])
    stop_year = int(rules["selection_year_stop_inclusive"])
    excluded_years = set(int(value) for value in rules["excluded_years"])
    pool = kp.loc[
        kp["date_utc"].dt.year.between(start_year, stop_year)
        & ~kp["date_utc"].dt.year.isin(excluded_years)
    ].copy()
    half_width = int(rules["event_exclusion_half_width_hours_for_kp_classes"])
    pool["catalog_event_near_day"] = pool["date_utc"].map(
        lambda day: event_near_day(day, event_times, half_width_hours=half_width)
    )
    pool = pool.loc[~pool["catalog_event_near_day"]].copy()
    if class_name == "QUIET_SOLAR_WIND":
        pool = pool.loc[pool["kp_max"] <= 2.0].copy()
        pool["rank_target"] = pool["kp_mean"]
        sort_columns = ["rank_target", "kp_max", "date_utc"]
        ascending = [True, True, True]
    else:
        pool = pool.loc[(pool["kp_max"] >= 3.0) & (pool["kp_max"] <= 4.0)].copy()
        pool["rank_target"] = (pool["kp_mean"] - 1.75).abs()
        sort_columns = ["rank_target", "kp_max", "date_utc"]
        ascending = [True, True, True]
    pool["mission_era"] = pool["date_utc"].map(
        lambda value: era_for(value, contract["mission_eras"])
    )
    pool = pool.dropna(subset=["mission_era"])
    selected = select_by_era(
        pool,
        era_targets=policy["era_targets"],
        timestamp_column="date_utc",
        sort_columns=sort_columns,
        ascending=ascending,
        minimum_spacing_days=int(rules["minimum_spacing_days_within_class"]),
    )
    records: list[dict[str, Any]] = []
    prefix = "quiet" if class_name == "QUIET_SOLAR_WIND" else "moderate"
    for _, row in selected.sort_values("date_utc").iterrows():
        day = to_utc(row["date_utc"])
        records.append(
            interval_common(
                interval_id=f"V2_{prefix.upper()}_{day.strftime('%Y%m%d')}",
                class_name=class_name,
                start=day,
                stop=day + pd.Timedelta(days=1),
                mission_era=str(row["mission_era"]),
                selection_rule=str(policy["eligibility"]),
                evidence={
                    "source": "GFZ_KP",
                    "date_utc": day.isoformat(),
                    "kp_values": [float(value) for value in row["kp_values"]],
                    "kp_max": float(row["kp_max"]),
                    "kp_mean": float(row["kp_mean"]),
                    "catalog_event_exclusion_half_width_hours": half_width,
                    "ipshocks_or_richardson_cane_event_in_exclusion_window": False,
                },
            )
        )
    return records


def build_shock_intervals(
    shocks: pd.DataFrame,
    rc: pd.DataFrame,
    *,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = contract["classes"]["ISOLATED_SHOCK_OR_SHEATH"]
    rules = contract["global_rules"]
    excluded_years = set(int(value) for value in rules["excluded_years"])
    rc_times = [to_utc(value) for value in rc["disturbance_time_utc"]]
    candidates = shocks.copy()
    candidates = candidates.loc[
        ~candidates["reference_time_utc"].map(
            lambda value: is_excluded_year(value, excluded_years)
        )
        & candidates["shock_types"].map(
            lambda values: any(str(value).upper().startswith("FF") for value in values)
        )
        & candidates["named_spacecraft"].map(bool)
        & (candidates["nearest_ipshock_gap_hours"] >= 72.0)
    ].copy()
    def nearest_other_rc_gap_hours(value: object) -> float:
        reference = to_utc(value)
        gaps = [
            abs((reference - event).total_seconds()) / 3600.0
            for event in rc_times
        ]
        # A Richardson/Cane entry within twelve hours can describe the
        # same shock/ICME association. Isolation is measured against the
        # nearest other catalog disturbance, not the event itself.
        other = [gap for gap in gaps if gap > 12.0]
        return min(other) if other else float("inf")

    candidates["nearest_other_rc_gap_hours"] = candidates[
        "reference_time_utc"
    ].map(nearest_other_rc_gap_hours)
    candidates = candidates.loc[
        candidates["nearest_other_rc_gap_hours"] >= 72.0
    ].copy()
    candidates["isolation_hours"] = candidates[
        ["nearest_ipshock_gap_hours", "nearest_other_rc_gap_hours"]
    ].min(axis=1)
    candidates["spacecraft_count"] = candidates["named_spacecraft"].map(len)
    candidates["mission_era"] = candidates["reference_time_utc"].map(
        lambda value: era_for(value, contract["mission_eras"])
    )
    candidates = candidates.dropna(subset=["mission_era"])
    selected = select_by_era(
        candidates,
        era_targets=policy["era_targets"],
        timestamp_column="reference_time_utc",
        sort_columns=["isolation_hours", "spacecraft_count", "reference_time_utc"],
        ascending=[False, False, True],
        minimum_spacing_days=int(rules["minimum_spacing_days_within_class"]),
    )
    records: list[dict[str, Any]] = []
    for _, row in selected.sort_values("reference_time_utc").iterrows():
        center = to_utc(row["reference_time_utc"])
        records.append(
            interval_common(
                interval_id=f"V2_SHOCK_{center.strftime('%Y%m%dT%H%M%SZ')}",
                class_name="ISOLATED_SHOCK_OR_SHEATH",
                start=center - pd.Timedelta(hours=12),
                stop=center + pd.Timedelta(hours=12),
                mission_era=str(row["mission_era"]),
                selection_rule=str(policy["eligibility"]),
                evidence={
                    "source": "IPSHOCKS_ZENODO_19730292",
                    "catalog_cluster_id": int(row["cluster_id"]),
                    "catalog_reference_time_utc": center.isoformat(),
                    "catalog_first_time_utc": to_utc(row["first_time_utc"]).isoformat(),
                    "catalog_last_time_utc": to_utc(row["last_time_utc"]).isoformat(),
                    "named_spacecraft": list(row["named_spacecraft"]),
                    "all_source_labels": list(row["all_source_labels"]),
                    "shock_types": list(row["shock_types"]),
                    "catalog_rows": int(row["catalog_rows"]),
                    "catalog_row_numbers": list(row["catalog_row_numbers"]),
                    "nearest_ipshock_gap_hours": float(row["nearest_ipshock_gap_hours"]),
                    "nearest_richardson_cane_disturbance_gap_hours": float(row["nearest_other_rc_gap_hours"]),
                },
            )
        )
    return records


def build_complex_intervals(
    complex_clusters: pd.DataFrame,
    *,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    policy = contract["classes"]["COMPLEX_INTERACTING_EJECTA"]
    rules = contract["global_rules"]
    excluded_years = set(int(value) for value in rules["excluded_years"])
    candidates = complex_clusters.loc[complex_clusters["qualifies_complex"]].copy()
    candidates = candidates.loc[
        ~candidates["first_disturbance_utc"].map(
            lambda value: is_excluded_year(value, excluded_years)
        )
    ].copy()
    candidates["mission_era"] = candidates["first_disturbance_utc"].map(
        lambda value: era_for(value, contract["mission_eras"])
    )
    candidates = candidates.dropna(subset=["mission_era"])
    selected = select_by_era(
        candidates,
        era_targets=policy["era_targets"],
        timestamp_column="first_disturbance_utc",
        sort_columns=[
            "catalog_entry_count",
            "linked_cme_count",
            "first_disturbance_utc",
        ],
        ascending=[False, False, True],
        minimum_spacing_days=int(rules["minimum_spacing_days_within_class"]),
    )
    records: list[dict[str, Any]] = []
    for _, row in selected.sort_values("first_disturbance_utc").iterrows():
        first = to_utc(row["first_disturbance_utc"])
        start = first - pd.Timedelta(hours=6)
        records.append(
            interval_common(
                interval_id=f"V2_COMPLEX_{first.strftime('%Y%m%dT%H%M%SZ')}",
                class_name="COMPLEX_INTERACTING_EJECTA",
                start=start,
                stop=start + pd.Timedelta(hours=36),
                mission_era=str(row["mission_era"]),
                selection_rule=str(policy["eligibility"]),
                evidence={
                    "source": "RICHARDSON_CANE_ICME_CATALOG",
                    "catalog_complex_cluster_id": int(row["complex_cluster_id"]),
                    "first_disturbance_utc": first.isoformat(),
                    "last_disturbance_utc": to_utc(row["last_disturbance_utc"]).isoformat(),
                    "catalog_entry_count": int(row["catalog_entry_count"]),
                    "linked_cme_count": int(row["linked_cme_count"]),
                    "linked_cme_times": list(row["linked_cme_times"]),
                    "source_rows": list(row["source_rows"]),
                    "row_excerpts": list(row["row_excerpts"]),
                },
            )
        )
    return records


def validate_registry(registry: dict[str, Any], contract: dict[str, Any]) -> None:
    intervals = registry.get("intervals", [])
    if not intervals:
        raise HoldoutSelectionError("registry contains no intervals")
    ids = [item["interval_id"] for item in intervals]
    if len(ids) != len(set(ids)):
        raise HoldoutSelectionError("registry interval IDs are not unique")
    counts = pd.Series([item["class"] for item in intervals]).value_counts().to_dict()
    for class_name, policy in contract["classes"].items():
        if counts.get(class_name, 0) != int(policy["target_count"]):
            raise HoldoutSelectionError(
                f"{class_name} registry count is {counts.get(class_name, 0)}"
            )
    for item in intervals:
        if to_utc(item["start_utc"]).year == 2024:
            raise HoldoutSelectionError(f"V1 year leaked into V2: {item['interval_id']}")
        for key in (
            "spacecraft_mag_retrieved_before_freeze",
            "gate_outputs_inspected_before_freeze",
            "candidate_scores_inspected_before_freeze",
            "replacement_after_later_failure_allowed",
        ):
            if item.get(key) is not False:
                raise HoldoutSelectionError(
                    f"registry firewall failed at {item['interval_id']}:{key}"
                )
    text = json.dumps(registry)
    for token in FORBIDDEN_MAG_TOKENS:
        if token in text:
            raise HoldoutSelectionError(
                f"registry unexpectedly contains forbidden MAG/gate token {token}"
            )


def run_selection(*, config_path: Path, prereg_path: Path, outdir: Path) -> dict[str, Any]:
    contract = load_contract(config_path)
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    if prereg.get("status") != "PREREGISTERED_BEFORE_HOLDOUT_MAG_INSPECTION":
        raise HoldoutSelectionError("parent V2 preregistration is not frozen")
    outdir.mkdir(parents=True, exist_ok=True)
    raw_dir = outdir / "raw_catalogs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-V2-REGISTRY/{SELECTOR_VERSION}"}
    )

    source_metadata: dict[str, Any] = {}
    raw_by_source: dict[str, bytes] = {}
    for source_name, source in contract["sources"].items():
        raw, resolved, status = request_bytes(session, source["url"])
        suffix = {
            "gfz_kp": ".txt",
            "ipshocks": ".csv",
            "richardson_cane": ".html",
        }[source_name]
        path = raw_dir / f"{source_name}{suffix}"
        path.write_bytes(raw)
        raw_by_source[source_name] = raw
        source_metadata[source_name] = {
            "requested_url": source["url"],
            "resolved_url": resolved,
            "http_status": status,
            "path": str(path.relative_to(outdir)),
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "role": source["role"],
            "record": source.get("record"),
            "citation": source.get("citation"),
        }

    kp = parse_gfz_kp(raw_by_source["gfz_kp"])
    shocks = parse_ipshocks(
        raw_by_source["ipshocks"],
        cluster_minutes=90,
    )
    rc = parse_richardson_cane(raw_by_source["richardson_cane"])
    complex_clusters = build_complex_clusters(rc, gap_hours=48)

    event_times = [
        to_utc(value) for value in shocks["reference_time_utc"]
    ] + [
        to_utc(value) for value in rc["disturbance_time_utc"]
    ] + [
        to_utc(value) for value in rc["icme_start_utc"].dropna()
    ]

    intervals: list[dict[str, Any]] = []
    intervals.extend(
        build_kp_intervals(
            kp,
            class_name="QUIET_SOLAR_WIND",
            contract=contract,
            event_times=event_times,
        )
    )
    intervals.extend(
        build_kp_intervals(
            kp,
            class_name="MODERATE_VARIABILITY",
            contract=contract,
            event_times=event_times,
        )
    )
    intervals.extend(build_shock_intervals(shocks, rc, contract=contract))
    intervals.extend(build_complex_intervals(complex_clusters, contract=contract))
    intervals.sort(key=lambda item: (item["class"], item["start_utc"]))

    registry = {
        "registry_id": "NVCPP-GANNON-V2-PROSPECTIVE-HOLDOUT-REGISTRY-v1",
        "registry_version": "1.0.0",
        "status": "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL",
        "created_utc": utc_now(),
        "selector_version": SELECTOR_VERSION,
        "selection_contract": {
            "path": str(config_path),
            "sha256": sha256_file(config_path),
            "id": contract["selection_contract_id"],
            "version": contract["selection_contract_version"],
        },
        "parent_preregistration": {
            "path": str(prereg_path),
            "sha256": sha256_file(prereg_path),
            "id": prereg["contract_id"],
            "version": prereg["contract_version"],
        },
        "selection_firewall": {
            "spacecraft_mag_accessed": False,
            "gate_outputs_accessed": False,
            "candidate_scores_accessed": False,
            "clustering_outputs_accessed": False,
            "geometry_opened": False,
            "selection_inputs": ["GFZ_KP", "IPSHOCKS", "RICHARDSON_CANE_ICME"],
        },
        "excluded_development_scope": {
            "entire_years": contract["global_rules"]["excluded_years"],
            "explicit_v1_dates": contract["global_rules"]["explicit_v1_dates"],
            "reason": contract["global_rules"]["entire_2024_exclusion_reason"],
        },
        "source_metadata": source_metadata,
        "denominator_policy": {
            "frozen_registry_count": len(intervals),
            "future_evaluable_count": "NOT_YET_KNOWN",
            "future_incomplete_state": contract["freeze_policy"]["failed_later_acquisition_state"],
            "late_era_ace_failure_state": contract["freeze_policy"]["late_era_ace_failure_state"],
            "replacement_after_failure_allowed": False,
        },
        "class_counts": pd.Series([item["class"] for item in intervals]).value_counts().sort_index().to_dict(),
        "intervals": intervals,
        "next_allowed_action": "MERGE_AND_FREEZE_REGISTRY_THEN_RETRIEVE_PINNED_MAG_PRODUCTS",
        "physical_interpretation_reopened": False,
        "geometry_state": "CLOSED",
    }
    validate_registry(registry, contract)

    registry_path = outdir / "gannon_v2_holdout_registry.v1.json"
    write_json(registry_path, registry)
    table_rows: list[dict[str, Any]] = []
    for item in intervals:
        evidence = item["independent_selection_evidence"]
        table_rows.append(
            {
                "interval_id": item["interval_id"],
                "class": item["class"],
                "start_utc": item["start_utc"],
                "stop_utc": item["stop_utc"],
                "mission_era": item["mission_era"],
                "selection_source": evidence.get("source"),
                "selection_rule": item["selection_rule"],
                "independent_evidence_summary": json.dumps(evidence, sort_keys=True),
                "initial_retrieval_state": item["initial_retrieval_state"],
            }
        )
    pd.DataFrame(table_rows).to_csv(
        outdir / "gannon_v2_holdout_registry.v1.csv", index=False
    )

    manifest = {
        "status": "SUCCESS",
        "selector_version": SELECTOR_VERSION,
        "completed_utc": utc_now(),
        "registry_path": str(registry_path),
        "registry_sha256": sha256_file(registry_path),
        "registry_count": len(intervals),
        "class_counts": registry["class_counts"],
        "source_metadata": source_metadata,
        "spacecraft_mag_retrieved": False,
        "physics_computed": False,
        "geometry_opened": False,
    }
    write_json(outdir / "selection_manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/gannon_holdout_v2_selection.v1.json"),
    )
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=Path("config/gannon_holdout_v2.preregistered.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/holdout/gannon_v2_registry_selection"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = run_selection(
        config_path=args.config,
        prereg_path=args.preregistration,
        outdir=args.outdir,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
