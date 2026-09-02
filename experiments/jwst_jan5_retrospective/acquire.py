#!/usr/bin/env python3
"""
Evidence-only acquisition for the retrospective JWST January 5, 2026 audit.

This program does not classify a re-addressing, expansion, substrate shift,
spacecraft jolt, CME impact, or any other physical mechanism. It preserves
official-source responses and writes an acquisition inventory for later audit.

No simulated or synthetic fallback is permitted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import pandas as pd
import requests


USER_AGENT = (
    "NVCPP-JWST-Jan5-Retrospective/1.0 "
    "(evidence acquisition; contact repository owner)"
)

MAST_EDP_BASE = "https://mast.stsci.edu/edp/api/v0.1/mnemonics"
MAST_INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
HAPI_BASE = "https://cdaweb.gsfc.nasa.gov/hapi"
DONKI_BASE = "https://api.nasa.gov/DONKI"

PRIMARY_WINDOW = {
    "id": "JAN5_PRIMARY",
    "start": "2026-01-05T00:15:00.000",
    "end": "2026-01-05T02:00:00.000",
}
SECONDARY_WINDOW = {
    "id": "JAN5_SECONDARY",
    "start": "2026-01-05T14:30:00.000",
    "end": "2026-01-05T16:00:00.000",
}
CANDIDATE_WINDOWS = [PRIMARY_WINDOW, SECONDARY_WINDOW]

ENV_START = "2026-01-01T00:00:00.000Z"
ENV_END = "2026-01-10T00:00:00.000Z"

EDP_EXACT_MNEMONICS = [
    "SA_ZATTEST1",
    "SA_ZATTEST2",
    "SA_ZATTEST3",
    "SA_ZATTEST4",
    "SA_ZADUCMDX",
    "SA_ZADUCMDY",
    "SA_ZFGGSCMDX",
    "SA_ZFGGSCMDY",
    "SA_ZFGGSPOSX",
    "SA_ZFGGSPOSY",
    "SA_ZFGDETID",
    "IFGS_ID_XPOSG",
    "IFGS_ID_YPOSG",
    "IFGS_ACQ_XPOSG",
    "IFGS_ACQ_YPOSG",
    "IFGS_CTDGS_X",
    "IFGS_CTDGS_Y",
]
EDP_PARTIAL_SEARCHES = ["SA_ZATT", "SA_ZADU", "SA_ZFG", "IFGS_"]
EDP_ACCESS_TABLES = ["fqa", "spa"]

HAPI_DATASETS = [
    "OMNI_HRO2_1MIN",
    "DSCOVR_H0_MAG",
    "DSCOVR_H1_FC",
    "AC_H0_MFI",
    "AC_H0_SWE",
    "WI_H0_MFI",
    "WI_K0_SWE",
]

OMNI_PARAMETERS_WANTED = [
    "IMF",
    "PLS",
    "F",
    "BX_GSE",
    "BY_GSE",
    "BZ_GSE",
    "BY_GSM",
    "BZ_GSM",
    "flow_speed",
    "Vx",
    "Vy",
    "Vz",
    "proton_density",
    "T",
    "Pressure",
    "E",
    "SYM_D",
    "SYM_H",
    "ASY_D",
    "ASY_H",
    "AE_INDEX",
    "AL_INDEX",
    "AU_INDEX",
]

DONKI_TYPES = ["CME", "FLR", "GST", "IPS", "SEP", "MPC", "RBE", "HSS"]


@dataclass
class InventoryRecord:
    source: str
    request_name: str
    method: str
    url: str
    retrieved_at_utc: str
    http_status: int | None
    content_type: str
    byte_count: int
    sha256: str
    relative_path: str
    error: str


class EvidenceSession:
    def __init__(self, outdir: Path, timeout: int = 120) -> None:
        self.outdir = outdir
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
            }
        )
        self.records: list[InventoryRecord] = []

    @staticmethod
    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_response(
        self,
        *,
        source: str,
        request_name: str,
        method: str,
        url: str,
        relative_path: str,
        response: requests.Response | None,
        body: bytes,
        error: str,
    ) -> InventoryRecord:
        target = self.outdir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

        digest = hashlib.sha256(body).hexdigest()
        headers = dict(response.headers) if response is not None else {}
        meta = {
            "source": source,
            "request_name": request_name,
            "method": method,
            "requested_url": url,
            "final_url": response.url if response is not None else "",
            "retrieved_at_utc": self.utc_now(),
            "http_status": response.status_code if response is not None else None,
            "response_headers": headers,
            "byte_count": len(body),
            "sha256": digest,
            "error": error,
        }
        meta_path = target.with_suffix(target.suffix + ".request.json")
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")

        record = InventoryRecord(
            source=source,
            request_name=request_name,
            method=method,
            url=url,
            retrieved_at_utc=meta["retrieved_at_utc"],
            http_status=meta["http_status"],
            content_type=headers.get("Content-Type", ""),
            byte_count=len(body),
            sha256=digest,
            relative_path=str(target.relative_to(self.outdir)),
            error=error,
        )
        self.records.append(record)
        return record

    def get(
        self,
        *,
        source: str,
        request_name: str,
        base_url: str,
        params: dict[str, Any] | None,
        relative_path: str,
        timeout: int | None = None,
        attempts: int = 3,
    ) -> tuple[requests.Response | None, bytes]:
        prepared = requests.Request("GET", base_url, params=params).prepare()
        requested_url = prepared.url or base_url
        last_error = ""
        response: requests.Response | None = None
        body = b""

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(
                    base_url,
                    params=params,
                    timeout=timeout or self.timeout,
                    allow_redirects=True,
                )
                body = response.content
                if response.status_code < 500:
                    last_error = ""
                    break
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                response = None
                body = b""

            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))

        self._write_response(
            source=source,
            request_name=request_name,
            method="GET",
            url=requested_url,
            relative_path=relative_path,
            response=response,
            body=body,
            error=last_error,
        )
        return response, body

    def post_form(
        self,
        *,
        source: str,
        request_name: str,
        url: str,
        form: dict[str, str],
        relative_path: str,
        timeout: int | None = None,
        attempts: int = 3,
    ) -> tuple[requests.Response | None, bytes]:
        last_error = ""
        response: requests.Response | None = None
        body = b""

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.post(
                    url,
                    data=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=timeout or self.timeout,
                    allow_redirects=True,
                )
                body = response.content
                if response.status_code < 500:
                    last_error = ""
                    break
                last_error = f"HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                response = None
                body = b""

            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))

        visible_url = f"{url}?{urlencode({'request': '<JSON payload preserved in sidecar>'})}"
        record = self._write_response(
            source=source,
            request_name=request_name,
            method="POST",
            url=visible_url,
            relative_path=relative_path,
            response=response,
            body=body,
            error=last_error,
        )
        sidecar = self.outdir / (record.relative_path + ".payload.json")
        sidecar.write_text(
            json.dumps({"url": url, "form": form}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return response, body


def parse_json_bytes(body: bytes) -> Any | None:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def extract_numeric_values(value: Any, key_hint: str = "") -> list[float]:
    values: list[float] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"count", "total", "records", "record_count", "data_count"}:
                try:
                    values.append(float(item))
                except (TypeError, ValueError):
                    pass
            values.extend(extract_numeric_values(item, lower))
    elif isinstance(value, list):
        for item in value:
            values.extend(extract_numeric_values(item, key_hint))
    elif key_hint in {"count", "total", "records", "record_count", "data_count"}:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            pass
    return values


def extract_count(body: bytes) -> int | None:
    payload = parse_json_bytes(body)
    if payload is None:
        text = body.decode("utf-8", errors="ignore").strip()
        try:
            return int(float(text))
        except ValueError:
            return None
    values = extract_numeric_values(payload)
    if not values:
        return None
    nonnegative = [value for value in values if value >= 0]
    if not nonnegative:
        return None
    return int(max(nonnegative))


def safe_slug(text: str) -> str:
    allowed = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "request"


def utc_to_mjd(utc_text: str) -> float:
    dt = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
    return dt.timestamp() / 86400.0 + 40587.0


def recursively_find_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            data = payload["data"]
            if all(isinstance(row, dict) for row in data):
                return data
        for value in payload.values():
            rows = recursively_find_rows(value)
            if rows:
                return rows
    elif isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
        return payload
    return []


def save_rows_csv(rows: Iterable[dict[str, Any]], path: Path) -> int:
    rows_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows_list:
        path.write_text("", encoding="utf-8")
        return 0
    frame = pd.json_normalize(rows_list)
    frame.to_csv(path, index=False)
    return len(frame)


def acquire_mast_edp(evidence: EvidenceSession) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metadata_requests": 0,
        "count_requests": 0,
        "data_files_http_200": 0,
        "data_files_nonempty": 0,
        "counts": [],
    }

    # Metadata discovery is intentionally broader than the exact old list.
    metadata_queries = list(dict.fromkeys(EDP_PARTIAL_SEARCHES + EDP_EXACT_MNEMONICS))
    for access in EDP_ACCESS_TABLES:
        for mnemonic in metadata_queries:
            response, body = evidence.get(
                source="STSCI_MAST_EDP",
                request_name=f"metadata_{access}_{mnemonic}",
                base_url=f"{MAST_EDP_BASE}/{access}/jwst/metadata",
                params={"mnemonic": mnemonic, "result_format": "json"},
                relative_path=(
                    f"raw/mast_edp/metadata/{access}/"
                    f"{safe_slug(mnemonic)}.json"
                ),
            )
            result["metadata_requests"] += 1

    # Preserve exact candidate-window telemetry only. Controls are selected later
    # using non-anomaly operational metadata under the frozen protocol.
    for window in CANDIDATE_WINDOWS:
        for mnemonic in EDP_EXACT_MNEMONICS:
            for access in EDP_ACCESS_TABLES:
                count_response, count_body = evidence.get(
                    source="STSCI_MAST_EDP",
                    request_name=f"count_{window['id']}_{access}_{mnemonic}",
                    base_url=f"{MAST_EDP_BASE}/{access}/jwst/data/count",
                    params={
                        "mnemonic": mnemonic,
                        "s_time": window["start"],
                        "e_time": window["end"],
                        "result_format": "json",
                    },
                    relative_path=(
                        f"raw/mast_edp/count/{window['id']}/{access}/"
                        f"{mnemonic}.json"
                    ),
                )
                result["count_requests"] += 1
                count = extract_count(count_body)
                result["counts"].append(
                    {
                        "window": window["id"],
                        "access": access,
                        "mnemonic": mnemonic,
                        "http_status": (
                            count_response.status_code if count_response is not None else None
                        ),
                        "count": count,
                    }
                )

                # A positive provider count is required before downloading. If the
                # count schema cannot be parsed, an HTTP-200 response is allowed one
                # bounded data attempt so the raw provider response can resolve it.
                count_http_ok = (
                    count_response is not None and count_response.status_code == 200
                )
                if not count_http_ok or count == 0:
                    continue

                data_response, data_body = evidence.get(
                    source="STSCI_MAST_EDP",
                    request_name=f"data_{window['id']}_{access}_{mnemonic}",
                    base_url=f"{MAST_EDP_BASE}/{access}/jwst/data",
                    params={
                        "mnemonic": mnemonic,
                        "s_time": window["start"],
                        "e_time": window["end"],
                        "result_format": "csv",
                    },
                    relative_path=(
                        f"raw/mast_edp/data/{window['id']}/{access}/"
                        f"{mnemonic}.csv"
                    ),
                    timeout=240,
                )
                if data_response is not None and data_response.status_code == 200:
                    result["data_files_http_200"] += 1
                    if len(data_body.strip()) > 0:
                        result["data_files_nonempty"] += 1

    counts_path = evidence.outdir / "normalized/mast_edp_counts.csv"
    counts_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(result["counts"]).to_csv(counts_path, index=False)
    return result


def acquire_mast_observations(evidence: EvidenceSession) -> dict[str, Any]:
    result: dict[str, Any] = {}
    start_mjd = utc_to_mjd(ENV_START)
    end_mjd = utc_to_mjd(ENV_END)

    queries = {
        "jwst_all_observations": [
            {
                "paramName": "obs_collection",
                "values": ["JWST"],
            },
            {
                "paramName": "t_min",
                "values": [{"min": start_mjd, "max": end_mjd}],
                "separator": "range",
            },
        ],
        "jwst_fgs_guider_observations": [
            {
                "paramName": "obs_collection",
                "values": ["JWST"],
            },
            {
                "paramName": "instrument_name",
                "values": ["FGS/GUIDER1", "FGS/GUIDER2", "FGS"],
            },
            {
                "paramName": "t_min",
                "values": [{"min": start_mjd, "max": end_mjd}],
                "separator": "range",
            },
        ],
    }

    for name, filters in queries.items():
        payload = {
            "service": "Mast.Caom.Filtered",
            "format": "json",
            "params": {
                "columns": "*",
                "filters": filters,
            },
            "pagesize": 50000,
            "page": 1,
        }
        response, body = evidence.post_form(
            source="STSCI_MAST_CAOM",
            request_name=name,
            url=MAST_INVOKE_URL,
            form={"request": json.dumps(payload, separators=(",", ":"))},
            relative_path=f"raw/mast_caom/{name}.json",
            timeout=240,
        )
        payload_json = parse_json_bytes(body)
        rows = recursively_find_rows(payload_json)
        row_count = save_rows_csv(
            rows,
            evidence.outdir / f"normalized/{name}.csv",
        )
        result[name] = {
            "http_status": response.status_code if response is not None else None,
            "rows": row_count,
        }
    return result


def hapi_parameter_ids(info_payload: Any) -> list[str]:
    if not isinstance(info_payload, dict):
        return []
    parameters = info_payload.get("parameters", [])
    ids = []
    if isinstance(parameters, list):
        for parameter in parameters:
            if isinstance(parameter, dict) and parameter.get("name"):
                ids.append(str(parameter["name"]))
    return ids


def acquire_hapi(evidence: EvidenceSession) -> dict[str, Any]:
    result: dict[str, Any] = {"datasets": {}}
    for dataset_id in HAPI_DATASETS:
        info_response, info_body = evidence.get(
            source="NASA_SPDF_CDAWEB_HAPI",
            request_name=f"info_{dataset_id}",
            base_url=f"{HAPI_BASE}/info",
            params={"id": dataset_id},
            relative_path=f"raw/cdaweb_hapi/info/{dataset_id}.json",
            timeout=180,
        )
        info_payload = parse_json_bytes(info_body)
        available_params = hapi_parameter_ids(info_payload)
        dataset_record = {
            "info_http_status": (
                info_response.status_code if info_response is not None else None
            ),
            "parameter_count": len(available_params),
            "data_requests": [],
        }
        result["datasets"][dataset_id] = dataset_record

        if info_response is None or info_response.status_code != 200:
            continue

        if dataset_id == "OMNI_HRO2_1MIN":
            exact_by_lower = {name.lower(): name for name in available_params}
            selected = [
                exact_by_lower[name.lower()]
                for name in OMNI_PARAMETERS_WANTED
                if name.lower() in exact_by_lower
            ]
            # HAPI includes Time automatically. Using provider-resolved IDs avoids
            # silently requesting misspelled or nonexistent columns.
            params: dict[str, Any] = {
                "id": dataset_id,
                "time.min": ENV_START,
                "time.max": ENV_END,
                "format": "csv",
            }
            if selected:
                params["parameters"] = ",".join(selected)
            response, body = evidence.get(
                source="NASA_SPDF_CDAWEB_HAPI",
                request_name=f"data_{dataset_id}_environment",
                base_url=f"{HAPI_BASE}/data",
                params=params,
                relative_path=f"raw/cdaweb_hapi/data/{dataset_id}_environment.csv",
                timeout=300,
            )
            dataset_record["data_requests"].append(
                {
                    "window": "ENVIRONMENT",
                    "http_status": response.status_code if response is not None else None,
                    "bytes": len(body),
                    "parameters": selected,
                }
            )
        else:
            for window in CANDIDATE_WINDOWS:
                response, body = evidence.get(
                    source="NASA_SPDF_CDAWEB_HAPI",
                    request_name=f"data_{dataset_id}_{window['id']}",
                    base_url=f"{HAPI_BASE}/data",
                    params={
                        "id": dataset_id,
                        "time.min": window["start"] + "Z",
                        "time.max": window["end"] + "Z",
                        "format": "csv",
                    },
                    relative_path=(
                        f"raw/cdaweb_hapi/data/{window['id']}/{dataset_id}.csv"
                    ),
                    timeout=300,
                )
                dataset_record["data_requests"].append(
                    {
                        "window": window["id"],
                        "http_status": (
                            response.status_code if response is not None else None
                        ),
                        "bytes": len(body),
                        "parameters": "ALL_PROVIDER_FIELDS",
                    }
                )
    return result


def acquire_donki(evidence: EvidenceSession) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for event_type in DONKI_TYPES:
        response, body = evidence.get(
            source="NASA_CCMC_DONKI",
            request_name=f"donki_{event_type}_2026_01_01_09",
            base_url=f"{DONKI_BASE}/{event_type}",
            params={
                "startDate": "2026-01-01",
                "endDate": "2026-01-09",
                "api_key": "DEMO_KEY",
            },
            relative_path=f"raw/donki/{event_type}.json",
            timeout=180,
        )
        payload = parse_json_bytes(body)
        count = len(payload) if isinstance(payload, list) else None
        result[event_type] = {
            "http_status": response.status_code if response is not None else None,
            "records": count,
        }
    return result


def write_inventory(evidence: EvidenceSession) -> None:
    rows = [asdict(record) for record in evidence.records]
    inventory_csv = evidence.outdir / "source_inventory.csv"
    inventory_csv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(inventory_csv, index=False)

    # Include every response, sidecar, normalized table, and report in a checksum
    # inventory. SHA256SUMS itself is excluded to avoid self-reference.
    entries: list[tuple[str, str]] = []
    for path in sorted(evidence.outdir.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((digest, str(path.relative_to(evidence.outdir))))
    sums_path = evidence.outdir / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{digest}  {relative}\n" for digest, relative in entries),
        encoding="utf-8",
    )


def classify_acquisition(
    edp: dict[str, Any],
    mast: dict[str, Any],
    hapi: dict[str, Any],
) -> tuple[str, list[str]]:
    limitations: list[str] = []

    edp_data = int(edp.get("data_files_nonempty", 0))
    if edp_data == 0:
        limitations.append("No nonempty JWST EDP telemetry file was acquired.")

    mast_rows = sum(
        int(record.get("rows", 0))
        for record in mast.values()
        if isinstance(record, dict)
    )
    if mast_rows == 0:
        limitations.append("No JWST CAOM observation rows were normalized.")

    omni = hapi.get("datasets", {}).get("OMNI_HRO2_1MIN", {})
    omni_ok = any(
        request.get("http_status") == 200 and request.get("bytes", 0) > 0
        for request in omni.get("data_requests", [])
    )
    if not omni_ok:
        limitations.append("Definitive OMNI environmental context was not acquired.")

    if edp_data > 0 and omni_ok:
        return "ACQUISITION_COMPLETE_NO_SCIENTIFIC_CONCLUSION", limitations
    return "ACQUISITION_PARTIAL_SOURCE_FAILURE", limitations


def write_reports(
    evidence: EvidenceSession,
    *,
    edp: dict[str, Any],
    mast: dict[str, Any],
    hapi: dict[str, Any],
    donki: dict[str, Any],
) -> str:
    state, limitations = classify_acquisition(edp, mast, hapi)
    report = {
        "schema": "nvcpp.jwst_jan5.acquisition_status.v1",
        "generated_at_utc": EvidenceSession.utc_now(),
        "state": state,
        "scientific_conclusion": None,
        "simulation_used": False,
        "candidate_windows": CANDIDATE_WINDOWS,
        "environment_window": {"start": ENV_START, "end": ENV_END},
        "git": {
            "repository": os.getenv("GITHUB_REPOSITORY", ""),
            "sha": os.getenv("GITHUB_SHA", ""),
            "ref": os.getenv("GITHUB_REF", ""),
            "run_id": os.getenv("GITHUB_RUN_ID", ""),
            "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        },
        "mast_edp": edp,
        "mast_observations": mast,
        "hapi": hapi,
        "donki": donki,
        "limitations": limitations,
        "prohibited_interpretations": [
            "re-addressing confirmed",
            "expansion confirmed",
            "substrate shift",
            "CME impact on JWST",
            "micrometeoroid impact",
            "common plasma parcel",
            "propagation from L1 to JWST",
        ],
        "next_gate": (
            "Inspect provider metadata and raw time semantics. Then freeze "
            "mode-matched control intervals before scoring candidate telemetry."
        ),
    }

    reports_dir = evidence.outdir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "acquisition_status.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    md_lines = [
        "# JWST January 5 retrospective acquisition status",
        "",
        f"**State:** `{state}`",
        "",
        "**Scientific conclusion:** none. This run acquired and inventoried source evidence only.",
        "",
        "## Fixed candidate windows",
        "",
        "| ID | Start UTC | End UTC |",
        "|---|---|---|",
    ]
    for window in CANDIDATE_WINDOWS:
        md_lines.append(f"| `{window['id']}` | {window['start']}Z | {window['end']}Z |")

    md_lines.extend(
        [
            "",
            "## Acquisition summary",
            "",
            f"- Nonempty MAST EDP telemetry files: **{edp.get('data_files_nonempty', 0)}**",
            f"- MAST observation queries: **{len(mast)}**",
            f"- HAPI datasets attempted: **{len(hapi.get('datasets', {}))}**",
            f"- DONKI event classes attempted: **{len(donki)}**",
            "- Simulation or synthetic fallback: **none**",
            "",
            "## Limitations",
            "",
        ]
    )
    if limitations:
        md_lines.extend(f"- {item}" for item in limitations)
    else:
        md_lines.append("- No source-level acquisition limitation was detected by the bounded checks.")

    md_lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "This package does not establish a spacecraft anomaly, a heliospheric event at JWST, a common moving structure, re-addressing, expansion, or any mechanism. Provider metadata, cadence, units, command state, observation state, geometry, and matched controls must be examined before candidate scoring.",
            "",
        ]
    )
    (reports_dir / "acquisition_status.md").write_text(
        "\n".join(md_lines),
        encoding="utf-8",
    )
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire official evidence for the JWST January 5 retrospective audit."
    )
    parser.add_argument("--outdir", default="runs/jwst_jan5_retrospective")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": "nvcpp.jwst_jan5.run_manifest.v1",
        "created_at_utc": EvidenceSession.utc_now(),
        "purpose": "evidence-only retrospective source acquisition",
        "simulation_permitted": False,
        "candidate_windows_frozen": CANDIDATE_WINDOWS,
        "environment_window": {"start": ENV_START, "end": ENV_END},
        "protocol_path": "docs/JWST_JAN5_RETROSPECTIVE_PROTOCOL.md",
        "git_sha": os.getenv("GITHUB_SHA", ""),
    }
    (outdir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    evidence = EvidenceSession(outdir=outdir, timeout=args.timeout)

    print("=" * 72)
    print("NVCPP JWST JANUARY 5 RETROSPECTIVE — EVIDENCE ACQUISITION")
    print("=" * 72)
    print("No simulation. No mechanism classification. No scientific conclusion.")

    edp = acquire_mast_edp(evidence)
    mast = acquire_mast_observations(evidence)
    hapi = acquire_hapi(evidence)
    donki = acquire_donki(evidence)

    state = write_reports(
        evidence,
        edp=edp,
        mast=mast,
        hapi=hapi,
        donki=donki,
    )
    write_inventory(evidence)

    print()
    print("=" * 72)
    print(state)
    print(f"Evidence directory: {outdir}")
    print(f"HTTP source responses: {len(evidence.records)}")
    print("Scientific conclusion: NONE")
    print("=" * 72)

    # A partial run is deliberately nonzero after writing the package so the
    # workflow cannot appear green merely because it preserved a failure.
    return 0 if state == "ACQUISITION_COMPLETE_NO_SCIENTIFIC_CONCLUSION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
