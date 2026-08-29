#!/usr/bin/env python3
"""Freeze the Gannon V2 holdout registry without retrieving spacecraft MAG.

Allowed evidence is limited to the definitive GFZ daily Kp file and the
Richardson-Cane near-Earth ICME catalog. The selector does not import a magnetic
adapter, call a spacecraft MAG endpoint, calculate a gate, or inspect a
clustering result. Its output is a prospective date registry; later source
failures remain INCOMPLETE_MULTIPOINT and cannot trigger substitutions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

SELECTOR_VERSION = "1.0.0"
GFZ_KP_URL = "https://kp.gfz.de/app/files/Kp_ap_Ap_SN_F107_since_1932.txt"
RICHARDSON_CANE_URLS = [
    "https://izw1.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm",
    "https://www.srl.caltech.edu/ACE/ASC/DATA/level3/icmetable2.htm",
]
DEFAULT_CONTRACT = Path("config/gannon_holdout_v2_selection.v1.json")
DEFAULT_OUTDIR = Path("runs/registry/gannon_v2")


class HoldoutSelectionError(RuntimeError):
    """Raised when the prospective registry cannot be frozen fail-closed."""


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


def request_bytes(
    session: requests.Session,
    url: str,
    *,
    attempts: int = 5,
    timeout: int = 90,
) -> tuple[bytes, str]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            if response.status_code >= 500:
                raise requests.HTTPError(
                    f"provider returned {response.status_code}", response=response
                )
            response.raise_for_status()
            return response.content, response.url
        except (requests.RequestException, OSError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise HoldoutSelectionError(f"source unavailable after retries: {url}: {last}")


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("status") != "SELECTION_RULES_FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL":
        raise HoldoutSelectionError("selection contract is not frozen")
    required_classes = contract.get("required_classes", {})
    expected = {
        "QUIET_SOLAR_WIND",
        "MODERATE_VARIABILITY",
        "ISOLATED_SHOCK_OR_SHEATH",
        "COMPLEX_INTERACTING_EJECTA",
    }
    if set(required_classes) != expected:
        raise HoldoutSelectionError("selection contract has unexpected classes")
    for name, item in required_classes.items():
        if int(item.get("count", 0)) < 10:
            raise HoldoutSelectionError(f"{name} has fewer than ten slots")
    gate = contract["frozen_detector"]
    if gate.get("rotation_threshold_degrees") != 45.0:
        raise HoldoutSelectionError("45-degree detector changed")
    if gate.get("magnitude_change_threshold_fraction") != 0.25:
        raise HoldoutSelectionError("25-percent detector changed")
    if gate.get("timing_radii_minutes") != [1, 2, 3, 5, 10, 15]:
        raise HoldoutSelectionError("frozen timing radii changed")
    hypothesis = gate["primary_clustering_hypothesis"]
    if hypothesis.get("nearest_joint_ace_wind_support_radius_minutes_max") != 2:
        raise HoldoutSelectionError("two-minute hypothesis changed")
    if hypothesis.get("strongest_three_spacecraft_span_minutes_max") != 3:
        raise HoldoutSelectionError("three-minute hypothesis changed")
    return contract


class HtmlRows(HTMLParser):
    """Minimal table-row parser that avoids an additional HTML dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(" ".join(" ".join(self.cell_parts).split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False


def parse_gfz_kp(raw: bytes) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw.decode("utf-8", errors="strict").splitlines(), start=1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 15:
            continue
        try:
            year, month, day = map(int, fields[:3])
            values = [float(value) for value in fields[7:15]]
        except (TypeError, ValueError):
            continue
        if len(values) != 8 or not all(
            math.isfinite(value) and 0.0 <= value <= 9.0 for value in values
        ):
            raise HoldoutSelectionError(
                f"GFZ Kp row {line_number} contains invalid values"
            )
        rows.append(
            {
                "date": pd.Timestamp(year=year, month=month, day=day, tz="UTC"),
                "kp_values": values,
                "kp_max": max(values),
                "kp_mean": sum(values) / 8.0,
                "kp_sum": sum(values),
            }
        )
    frame = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if frame.empty or frame["date"].duplicated().any():
        raise HoldoutSelectionError("GFZ Kp parse is empty or duplicated")
    return frame


_TIMESTAMP_PATTERNS = [
    re.compile(
        r"(?P<year>20\d{2})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
        r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    ),
    re.compile(
        r"(?P<year>20\d{2})[/-](?P<month>\d{1,2})[/-](?P<day>\d{1,2})"
        r"\s+(?P<hour>\d{2})(?P<minute>\d{2})"
    ),
]


def timestamps_in_text(text: str) -> list[pd.Timestamp]:
    found: list[tuple[int, pd.Timestamp]] = []
    for pattern in _TIMESTAMP_PATTERNS:
        for match in pattern.finditer(text):
            try:
                timestamp = pd.Timestamp(
                    year=int(match["year"]),
                    month=int(match["month"]),
                    day=int(match["day"]),
                    hour=int(match["hour"]),
                    minute=int(match["minute"]),
                    tz="UTC",
                )
            except ValueError:
                continue
            found.append((match.start(), timestamp))
    result: list[pd.Timestamp] = []
    for _, timestamp in sorted(found):
        if timestamp not in result:
            result.append(timestamp)
    return result


def parse_richardson_cane(raw: bytes) -> pd.DataFrame:
    parser = HtmlRows()
    parser.feed(raw.decode("latin-1", errors="replace"))
    rows: list[dict[str, Any]] = []
    for row_index, cells in enumerate(parser.rows):
        row_text = " | ".join(cells)
        timestamps = timestamps_in_text(row_text)
        if len(timestamps) < 2:
            continue
        disturbance = timestamps[0]
        icme_start = timestamps[1]
        icme_end = (
            timestamps[2]
            if len(timestamps) >= 3 and timestamps[2] > icme_start
            else pd.NaT
        )
        if not 1996 <= disturbance.year <= 2035:
            continue
        rows.append(
            {
                "catalog_id": (
                    f"RC-{disturbance.strftime('%Y%m%dT%H%M')}-{row_index:04d}"
                ),
                "catalog_row_index": row_index,
                "disturbance_time": disturbance,
                "icme_start": icme_start,
                "icme_end": icme_end,
                "catalog_row_text": row_text,
                "catalog_row_sha256": sha256_bytes(row_text.encode("utf-8")),
            }
        )
    frame = pd.DataFrame(rows).sort_values("disturbance_time").reset_index(drop=True)
    if len(frame) < 100:
        raise HoldoutSelectionError(
            f"Richardson-Cane parse yielded only {len(frame)} event rows"
        )
    if frame["catalog_id"].duplicated().any():
        raise HoldoutSelectionError("Richardson-Cane IDs are duplicated")
    return frame


def all_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


def collect_v1_exclusions(contract: dict[str, Any]) -> tuple[set[pd.Timestamp], list[str]]:
    policy = contract["v1_exclusion_policy"]
    known = {
        pd.Timestamp(value, tz="UTC").floor("D")
        for value in policy["known_minimum_exclusions"]
    }
    scanned: list[str] = []
    date_pattern = re.compile(r"20\d{2}-\d{2}-\d{2}")
    for value in policy["exclude_every_date_named_or_derived_in"]:
        path = Path(value)
        if not path.is_file():
            continue
        scanned.append(value)
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for text in all_strings(content):
            for match in date_pattern.findall(text):
                known.add(pd.Timestamp(match, tz="UTC").floor("D"))
        development = pd.Timestamp("2024-05-11T00:00:00Z")
        for key in ("ace_day_offsets", "wind_day_offsets", "day_offsets"):
            def walk(node: Any) -> Iterable[list[Any]]:
                if isinstance(node, dict):
                    for item_key, item_value in node.items():
                        if item_key == key and isinstance(item_value, list):
                            yield item_value
                        yield from walk(item_value)
                elif isinstance(node, list):
                    for item in node:
                        yield from walk(item)
            for offsets in walk(content):
                for offset in offsets:
                    if isinstance(offset, (int, float)):
                        known.add((development + pd.Timedelta(days=offset)).floor("D"))
    padding = int(policy["exclusion_padding_days"])
    expanded: set[pd.Timestamp] = set()
    for value in known:
        for offset in range(-padding, padding + 1):
            expanded.add(value + pd.Timedelta(days=offset))
    return expanded, scanned


def deterministic_diverse_selection(
    candidates: pd.DataFrame,
    *,
    count: int,
    score_columns: list[str],
    minimum_spacing_days: int,
    maximum_per_year: int = 2,
) -> pd.DataFrame:
    if candidates.empty:
        raise HoldoutSelectionError("candidate table is empty")
    working = candidates.sort_values(score_columns + ["date"]).copy()
    selected: list[pd.Series] = []
    per_year: dict[int, int] = {}

    for year in sorted(working["date"].dt.year.unique()):
        for _, row in working.loc[working["date"].dt.year == year].iterrows():
            if all(
                abs((row["date"] - item["date"]).days) >= minimum_spacing_days
                for item in selected
            ):
                selected.append(row)
                per_year[year] = 1
                break

    for _, row in working.iterrows():
        if len(selected) >= count:
            break
        year = int(row["date"].year)
        if per_year.get(year, 0) >= maximum_per_year:
            continue
        if any(row["date"] == item["date"] for item in selected):
            continue
        if all(
            abs((row["date"] - item["date"]).days) >= minimum_spacing_days
            for item in selected
        ):
            selected.append(row)
            per_year[year] = per_year.get(year, 0) + 1

    for _, row in working.iterrows():
        if len(selected) >= count:
            break
        if any(row["date"] == item["date"] for item in selected):
            continue
        if all(
            abs((row["date"] - item["date"]).days) >= minimum_spacing_days
            for item in selected
        ):
            selected.append(row)

    if len(selected) < count:
        raise HoldoutSelectionError(
            f"only {len(selected)} candidates satisfy the frozen spacing; need {count}"
        )
    return pd.DataFrame(selected[:count]).reset_index(drop=True)


def overlaps_excluded(
    start: pd.Timestamp, stop: pd.Timestamp, excluded: set[pd.Timestamp]
) -> bool:
    return any(start < day + pd.Timedelta(days=1) and stop > day for day in excluded)


def make_interval(
    *,
    class_name: str,
    index: int,
    start: pd.Timestamp,
    stop: pd.Timestamp,
    selection_rule: str,
    evidence: dict[str, Any],
    source_ids: list[str],
) -> dict[str, Any]:
    return {
        "interval_id": (
            f"V2-{class_name}-{index:02d}-{start.strftime('%Y%m%dT%H%MZ')}"
        ),
        "class": class_name,
        "start_utc": start.isoformat().replace("+00:00", "Z"),
        "stop_utc": stop.isoformat().replace("+00:00", "Z"),
        "selection_rule": selection_rule,
        "independent_evidence": evidence,
        "source_ids": source_ids,
        "mission_era_tag": "DSCOVR_ACE_WIND_EXPECTED",
        "mission_product_availability_verified_by_metadata_only": False,
        "mag_inspected_before_freeze": False,
        "gate_output_inspected_before_freeze": False,
        "clustering_output_inspected_before_freeze": False,
        "v1_inspected_window": False,
        "replacement_after_scoring_allowed": False,
        "failure_policy": "INCOMPLETE_MULTIPOINT_RETAIN_IN_DENOMINATOR",
    }


def build_registry(
    *,
    contract_path: Path,
    outdir: Path,
) -> dict[str, Any]:
    contract = load_contract(contract_path)
    start = pd.Timestamp(contract["selection_pool"]["start_utc"])
    stop = pd.Timestamp(contract["selection_pool"]["stop_utc"])
    if start.tzinfo is None or stop.tzinfo is None:
        raise HoldoutSelectionError("selection pool must be UTC aware")
    start, stop = start.tz_convert("UTC"), stop.tz_convert("UTC")
    excluded, scanned_exclusion_files = collect_v1_exclusions(contract)

    outdir.mkdir(parents=True, exist_ok=True)
    raw_dir = outdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": f"NVCPP-GANNON-V2-HOLDOUT-SELECTOR/{SELECTOR_VERSION}"}
    )

    kp_raw, kp_url = request_bytes(session, GFZ_KP_URL)
    (raw_dir / "gfz_kp.txt").write_bytes(kp_raw)
    kp = parse_gfz_kp(kp_raw)

    rc_raw: bytes | None = None
    rc_url: str | None = None
    failures: list[str] = []
    for candidate_url in RICHARDSON_CANE_URLS:
        try:
            rc_raw, rc_url = request_bytes(session, candidate_url)
            break
        except HoldoutSelectionError as exc:
            failures.append(str(exc))
    if rc_raw is None or rc_url is None:
        raise HoldoutSelectionError(
            "all Richardson-Cane source URLs failed: " + " | ".join(failures)
        )
    (raw_dir / "richardson_cane_icme.html").write_bytes(rc_raw)
    rc = parse_richardson_cane(rc_raw)
    rc_pool = rc.loc[
        (rc["disturbance_time"] >= start) & (rc["disturbance_time"] < stop)
    ].copy()

    catalog_excluded_days: set[pd.Timestamp] = set(excluded)
    for _, row in rc_pool.iterrows():
        interval_start = (row["disturbance_time"] - pd.Timedelta(days=1)).floor("D")
        end_source = (
            row["icme_end"]
            if pd.notna(row["icme_end"])
            else row["icme_start"] + pd.Timedelta(days=2)
        )
        interval_stop = (end_source + pd.Timedelta(days=1)).ceil("D")
        for day in pd.date_range(interval_start, interval_stop, freq="D"):
            catalog_excluded_days.add(day)

    kp_pool = kp.loc[(kp["date"] >= start) & (kp["date"] < stop)].copy()
    kp_pool = kp_pool.loc[~kp_pool["date"].isin(catalog_excluded_days)].copy()

    quiet = kp_pool.loc[
        (kp_pool["kp_max"] <= 2.0) & (kp_pool["kp_mean"] <= 1.0)
    ].copy()
    quiet["score_1"] = quiet["kp_max"]
    quiet["score_2"] = quiet["kp_mean"]

    moderate = kp_pool.loc[
        (kp_pool["kp_max"] >= 3.0)
        & (kp_pool["kp_max"] <= 4.0)
        & (kp_pool["kp_mean"] >= 1.0)
        & (kp_pool["kp_mean"] <= 2.5)
    ].copy()
    moderate["score_1"] = (moderate["kp_mean"] - 1.75).abs()
    moderate["score_2"] = (moderate["kp_max"] - 3.5).abs()

    quiet_selected = deterministic_diverse_selection(
        quiet,
        count=int(contract["required_classes"]["QUIET_SOLAR_WIND"]["count"]),
        score_columns=["score_1", "score_2"],
        minimum_spacing_days=60,
    )
    moderate_selected = deterministic_diverse_selection(
        moderate,
        count=int(contract["required_classes"]["MODERATE_VARIABILITY"]["count"]),
        score_columns=["score_1", "score_2"],
        minimum_spacing_days=60,
    )

    rc_pool = rc_pool.sort_values("disturbance_time").reset_index(drop=True)
    nearest_gap: list[float] = []
    for index, timestamp in enumerate(rc_pool["disturbance_time"]):
        neighbors: list[float] = []
        if index:
            neighbors.append(
                (timestamp - rc_pool.loc[index - 1, "disturbance_time"])
                .total_seconds()
                / 3600.0
            )
        if index + 1 < len(rc_pool):
            neighbors.append(
                (rc_pool.loc[index + 1, "disturbance_time"] - timestamp)
                .total_seconds()
                / 3600.0
            )
        nearest_gap.append(min(neighbors) if neighbors else float("inf"))
    rc_pool["nearest_event_gap_hours"] = nearest_gap
    rc_pool["duration_hours"] = [
        (
            (
                end
                if pd.notna(end)
                else interval_start + pd.Timedelta(days=2)
            )
            - interval_start
        ).total_seconds()
        / 3600.0
        for interval_start, end in zip(rc_pool["icme_start"], rc_pool["icme_end"])
    ]

    isolated = rc_pool.loc[
        (rc_pool["nearest_event_gap_hours"] >= 120.0)
        & rc_pool["duration_hours"].between(6.0, 96.0)
    ].copy()
    isolated["date"] = isolated["disturbance_time"].dt.floor("D")
    isolated = isolated.loc[
        ~isolated.apply(
            lambda row: overlaps_excluded(
                row["disturbance_time"] - pd.Timedelta(hours=6),
                row["disturbance_time"] + pd.Timedelta(hours=18),
                excluded,
            ),
            axis=1,
        )
    ].copy()
    isolated["score_1"] = -isolated["nearest_event_gap_hours"]
    isolated["score_2"] = (isolated["duration_hours"] - 30.0).abs()
    isolated_selected = deterministic_diverse_selection(
        isolated,
        count=int(
            contract["required_classes"]["ISOLATED_SHOCK_OR_SHEATH"]["count"]
        ),
        score_columns=["score_1", "score_2"],
        minimum_spacing_days=75,
    )

    complex_rows: list[dict[str, Any]] = []
    used_catalog_rows: set[int] = set()
    for row_index, row in rc_pool.iterrows():
        if row_index in used_catalog_rows:
            continue
        first_time = row["disturbance_time"]
        cluster = rc_pool.loc[
            (rc_pool["disturbance_time"] >= first_time)
            & (
                rc_pool["disturbance_time"]
                <= first_time + pd.Timedelta(hours=48)
            )
        ].copy()
        if len(cluster) < 2 or any(
            value in used_catalog_rows for value in cluster.index
        ):
            continue
        times = list(cluster["disturbance_time"])
        center = times[0] + (times[1] - times[0]) / 2
        interval_start = center - pd.Timedelta(hours=12)
        interval_stop = center + pd.Timedelta(hours=12)
        if overlaps_excluded(interval_start, interval_stop, excluded):
            continue
        members = cluster[
            [
                "catalog_id",
                "disturbance_time",
                "icme_start",
                "icme_end",
                "catalog_row_sha256",
            ]
        ].to_dict(orient="records")
        complex_rows.append(
            {
                "date": center.floor("D"),
                "center": center,
                "member_count": int(len(cluster)),
                "span_hours": (
                    times[-1] - times[0]
                ).total_seconds()
                / 3600.0,
                "members": members,
                "score_1": -int(len(cluster)),
                "score_2": (
                    times[-1] - times[0]
                ).total_seconds()
                / 3600.0,
            }
        )
        used_catalog_rows.update(int(value) for value in cluster.index)
    complex_frame = pd.DataFrame(complex_rows)
    complex_selected = deterministic_diverse_selection(
        complex_frame,
        count=int(
            contract["required_classes"]["COMPLEX_INTERACTING_EJECTA"]["count"]
        ),
        score_columns=["score_1", "score_2"],
        minimum_spacing_days=75,
    )

    records: list[dict[str, Any]] = []
    for index, row in quiet_selected.iterrows():
        start_time = row["date"]
        records.append(
            make_interval(
                class_name="QUIET_SOLAR_WIND",
                index=index + 1,
                start=start_time,
                stop=start_time + pd.Timedelta(days=1),
                selection_rule=contract["required_classes"]["QUIET_SOLAR_WIND"]["criteria"],
                evidence={
                    "kp_date_utc": start_time.isoformat(),
                    "kp_values": [float(value) for value in row["kp_values"]],
                    "kp_max": float(row["kp_max"]),
                    "kp_mean": float(row["kp_mean"]),
                    "catalog_exclusion": (
                        "no Richardson-Cane disturbance/ICME interval in the "
                        "one-day-expanded exclusion window"
                    ),
                },
                source_ids=["GFZ_KP", "RICHARDSON_CANE_ICME"],
            )
        )
    for index, row in moderate_selected.iterrows():
        start_time = row["date"]
        records.append(
            make_interval(
                class_name="MODERATE_VARIABILITY",
                index=index + 1,
                start=start_time,
                stop=start_time + pd.Timedelta(days=1),
                selection_rule=contract["required_classes"]["MODERATE_VARIABILITY"]["criteria"],
                evidence={
                    "kp_date_utc": start_time.isoformat(),
                    "kp_values": [float(value) for value in row["kp_values"]],
                    "kp_max": float(row["kp_max"]),
                    "kp_mean": float(row["kp_mean"]),
                    "catalog_exclusion": (
                        "no Richardson-Cane disturbance/ICME interval in the "
                        "one-day-expanded exclusion window"
                    ),
                },
                source_ids=["GFZ_KP", "RICHARDSON_CANE_ICME"],
            )
        )
    for index, row in isolated_selected.iterrows():
        center = row["disturbance_time"]
        records.append(
            make_interval(
                class_name="ISOLATED_SHOCK_OR_SHEATH",
                index=index + 1,
                start=center - pd.Timedelta(hours=6),
                stop=center + pd.Timedelta(hours=18),
                selection_rule=contract["required_classes"]["ISOLATED_SHOCK_OR_SHEATH"]["criteria"],
                evidence={
                    "catalog_id": row["catalog_id"],
                    "disturbance_time_utc": center.isoformat(),
                    "icme_start_utc": row["icme_start"].isoformat(),
                    "icme_end_utc": (
                        row["icme_end"].isoformat()
                        if pd.notna(row["icme_end"])
                        else None
                    ),
                    "nearest_catalog_disturbance_gap_hours": float(
                        row["nearest_event_gap_hours"]
                    ),
                    "catalog_row_sha256": row["catalog_row_sha256"],
                    "catalog_spacecraft_context": "near-Earth ACE/Wind ICME catalog",
                },
                source_ids=["RICHARDSON_CANE_ICME"],
            )
        )
    for index, row in complex_selected.iterrows():
        center = row["center"]
        members = []
        for member in row["members"]:
            members.append(
                {
                    key: value.isoformat() if isinstance(value, pd.Timestamp) else value
                    for key, value in member.items()
                }
            )
        records.append(
            make_interval(
                class_name="COMPLEX_INTERACTING_EJECTA",
                index=index + 1,
                start=center - pd.Timedelta(hours=12),
                stop=center + pd.Timedelta(hours=12),
                selection_rule=contract["required_classes"]["COMPLEX_INTERACTING_EJECTA"]["criteria"],
                evidence={
                    "cluster_member_count": int(row["member_count"]),
                    "cluster_span_hours": float(row["span_hours"]),
                    "catalog_members": members,
                    "catalog_spacecraft_context": "near-Earth ACE/Wind ICME catalog",
                },
                source_ids=["RICHARDSON_CANE_ICME"],
            )
        )

    required_counts = {
        class_name: int(item["count"])
        for class_name, item in contract["required_classes"].items()
    }
    actual_counts = {
        class_name: sum(record["class"] == class_name for record in records)
        for class_name in required_counts
    }
    if actual_counts != required_counts:
        raise HoldoutSelectionError(
            f"class counts differ from frozen contract: {actual_counts}"
        )
    for record in records:
        interval_start = pd.Timestamp(record["start_utc"])
        interval_stop = pd.Timestamp(record["stop_utc"])
        if overlaps_excluded(interval_start, interval_stop, excluded):
            raise HoldoutSelectionError(
                f"selected interval overlaps V1 exclusion: {record['interval_id']}"
            )

    source_manifest = {
        "GFZ_KP": {
            "resolved_url": kp_url,
            "sha256": sha256_bytes(kp_raw),
            "size_bytes": len(kp_raw),
        },
        "RICHARDSON_CANE_ICME": {
            "resolved_url": rc_url,
            "sha256": sha256_bytes(rc_raw),
            "size_bytes": len(rc_raw),
            "parsed_event_rows": int(len(rc)),
            "selection_pool_event_rows": int(len(rc_pool)),
        },
    }
    registry = {
        "registry_id": "NVCPP-GANNON-V2-PROSPECTIVE-HOLDOUT-REGISTRY-v1",
        "registry_version": "1.0.0",
        "status": "FROZEN_BEFORE_HOLDOUT_MAG_RETRIEVAL",
        "created_utc": utc_now(),
        "selector_version": SELECTOR_VERSION,
        "selector_commit": os.environ.get("GITHUB_SHA", "LOCAL"),
        "parent_preregistration": "config/gannon_holdout_v2.preregistered.json",
        "selection_contract": {
            "path": str(contract_path),
            "sha256": sha256_file(contract_path),
        },
        "selection_sources": source_manifest,
        "selection_firewall": {
            "spacecraft_mag_retrieved": False,
            "mag_values_inspected": False,
            "gate_outputs_inspected": False,
            "clustering_outputs_inspected": False,
            "allowed_inputs": ["GFZ_KP", "RICHARDSON_CANE_ICME"],
        },
        "v1_exclusion_files_scanned": scanned_exclusion_files,
        "v1_excluded_dates_with_padding": sorted(
            value.strftime("%Y-%m-%d") for value in excluded
        ),
        "class_denominators": actual_counts,
        "intervals": records,
        "future_evaluation_policy": {
            "registered_denominator_is_immutable": True,
            "failed_fetch_state": "INCOMPLETE_MULTIPOINT",
            "substitution_after_mag_or_gate_inspection_allowed": False,
            "class_specific_reporting_required": True,
            "geometry_default_state": "CLOSED",
        },
        "recent_era_status": (
            "2025-2026 intervals are not counted in this primary registry because "
            "complete independent event-catalog coverage was not frozen here; no "
            "recent date was inferred quiet merely from absent metadata"
        ),
        "interpretation_limits": contract["interpretation_limits"],
    }
    hash_view = json.loads(json.dumps(registry, default=str))
    hash_view.pop("created_utc", None)
    registry["registry_content_sha256"] = sha256_bytes(
        json.dumps(
            hash_view, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )

    compact = pd.DataFrame(
        [
            {
                "interval_id": record["interval_id"],
                "class": record["class"],
                "start_utc": record["start_utc"],
                "stop_utc": record["stop_utc"],
                "mission_era_tag": record["mission_era_tag"],
                "source_ids": "|".join(record["source_ids"]),
                "selection_rule": record["selection_rule"],
            }
            for record in records
        ]
    )
    write_json(outdir / "gannon_holdout_v2.registry.json", registry)
    compact.to_csv(outdir / "gannon_holdout_v2.registry.csv", index=False)
    write_json(outdir / "source_manifest.json", source_manifest)

    lines = [
        "# Gannon V2 Frozen Prospective Holdout Date List",
        "",
        f"Status: **{registry['status']}**",
        "",
        f"Registry content SHA-256: `{registry['registry_content_sha256']}`",
        "",
        "No DSCOVR, ACE, or Wind magnetic measurement was retrieved or inspected",
        "during selection. The 45-degree/25-percent detector and the Gannon-inspired",
        "2/3-minute clustering statistic remain unchanged.",
        "",
    ]
    for class_name in required_counts:
        lines.extend([f"## {class_name}", ""])
        for record in records:
            if record["class"] == class_name:
                lines.append(
                    f"- `{record['interval_id']}` — `{record['start_utc']}` to "
                    f"`{record['stop_utc']}` — `{record['mission_era_tag']}`"
                )
        lines.append("")
    lines.extend(
        [
            "## Frozen failure policy",
            "",
            "A later provider/schema failure is recorded as `INCOMPLETE_MULTIPOINT`",
            "and remains in both the registry and class denominator. No interval may",
            "be substituted after MAG, gate, or clustering inspection.",
            "",
            "Geometry, MVA, propagation, common-surface, and physical-class work",
            "remain closed until the prospective holdout capsule exists.",
        ]
    )
    (outdir / "GANNON_V2_FROZEN_DATE_LIST.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    registry = build_registry(contract_path=args.contract, outdir=args.outdir)
    print(
        json.dumps(
            {
                "status": registry["status"],
                "class_denominators": registry["class_denominators"],
                "registry_content_sha256": registry["registry_content_sha256"],
                "outdir": str(args.outdir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
