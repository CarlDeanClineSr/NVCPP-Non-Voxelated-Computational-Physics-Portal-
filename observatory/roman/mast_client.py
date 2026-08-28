"""Minimal evidence-preserving client for the public MAST Portal API.

This module deliberately uses the documented language-agnostic MAST ``invoke``
endpoint rather than hiding the wire request behind a mission-specific helper.
Every response can therefore be preserved byte-for-byte and hashed before any
archive interpretation occurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import quote

import requests


MAST_INVOKE_URL = "https://mast.stsci.edu/api/v0/invoke"
DEFAULT_USER_AGENT = "NVCPP-Roman-Readiness/1.0"


class MastError(RuntimeError):
    """Raised when MAST transport or response validation fails."""


@dataclass(frozen=True)
class MastInvocation:
    """One completed MAST service invocation."""

    request: dict[str, Any]
    payload: dict[str, Any]
    raw_bytes: bytes
    http_status: int
    final_url: str
    content_type: str
    retrieved_utc: str
    attempts: int

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "service": self.request.get("service"),
            "http_status": self.http_status,
            "final_url": self.final_url,
            "content_type": self.content_type,
            "retrieved_utc": self.retrieved_utc,
            "attempts": self.attempts,
            "raw_size_bytes": len(self.raw_bytes),
            "raw_sha256": self.raw_sha256,
            "mast_status": self.payload.get("status"),
            "row_count": len(self.payload.get("data", []))
            if isinstance(self.payload.get("data"), list)
            else None,
        }

    def write_raw(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.raw_bytes)
        return path


@dataclass(frozen=True)
class PageProbe:
    """Sanitized metadata and exact bytes for an official HTTP page."""

    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    retrieved_utc: str
    raw_bytes: bytes
    title: str | None
    last_modified: str | None

    @property
    def raw_sha256(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "retrieved_utc": self.retrieved_utc,
            "raw_size_bytes": len(self.raw_bytes),
            "raw_sha256": self.raw_sha256,
            "title": self.title,
            "last_modified": self.last_modified,
        }


class MastClient:
    """Small MAST client with exact request/response provenance."""

    def __init__(
        self,
        *,
        invoke_url: str = MAST_INVOKE_URL,
        timeout_seconds: float = 45.0,
        poll_interval_seconds: float = 1.0,
        max_poll_seconds: float = 60.0,
        session: requests.Session | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if not invoke_url.startswith("https://"):
            raise ValueError("MAST invoke URL must use HTTPS")
        self.invoke_url = invoke_url
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_poll_seconds = float(max_poll_seconds)
        self.session = session or requests.Session()
        self.user_agent = user_agent

    def invoke(self, request_object: dict[str, Any]) -> MastInvocation:
        """Invoke one MAST service and poll while the response is EXECUTING."""

        if not isinstance(request_object, dict) or not request_object.get("service"):
            raise ValueError("request_object must contain a nonempty service")
        request_copy = json.loads(json.dumps(request_object))
        request_copy.setdefault("format", "json")
        request_copy.setdefault(
            "cachebreaker",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )

        encoded = quote(
            json.dumps(request_copy, sort_keys=True, separators=(",", ":")),
            safe="",
        )
        body = "request=" + encoded
        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "application/json,text/plain;q=0.9",
            "User-Agent": self.user_agent,
        }

        start = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                response = self.session.post(
                    self.invoke_url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                raise MastError(f"MAST transport failed: {exc.__class__.__name__}") from exc

            if response.status_code != 200:
                raise MastError(f"MAST returned HTTP {response.status_code}")

            raw = bytes(response.content)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MastError("MAST returned non-JSON content") from exc
            if not isinstance(payload, dict):
                raise MastError("MAST response root is not an object")

            status = str(payload.get("status", "COMPLETE")).upper()
            if status in {"ERROR", "FAILED"}:
                message = payload.get("msg") or payload.get("message") or "unspecified error"
                raise MastError(f"MAST service failed: {message}")
            if status != "EXECUTING":
                return MastInvocation(
                    request=request_copy,
                    payload=payload,
                    raw_bytes=raw,
                    http_status=response.status_code,
                    final_url=str(response.url),
                    content_type=response.headers.get("Content-Type", ""),
                    retrieved_utc=datetime.now(timezone.utc).isoformat(),
                    attempts=attempts,
                )
            if time.monotonic() - start >= self.max_poll_seconds:
                raise MastError("MAST long-poll timeout")
            time.sleep(self.poll_interval_seconds)

    def list_missions(self) -> tuple[list[str], MastInvocation]:
        response = self.invoke(
            {
                "service": "Mast.Missions.List",
                "params": {},
                "format": "json",
            }
        )
        missions: list[str] = []
        data = response.payload.get("data", [])
        if not isinstance(data, list):
            raise MastError("Mast.Missions.List data is not a list")
        for row in data:
            if not isinstance(row, dict):
                continue
            value = row.get("distinctValue")
            if isinstance(value, str) and value.strip():
                missions.append(value.strip())
        return sorted(set(missions), key=str.casefold), response

    def count_collection(self, collection: str) -> tuple[int, MastInvocation]:
        if not collection.strip():
            raise ValueError("collection must not be empty")
        response = self.invoke(
            {
                "service": "Mast.Caom.Filtered",
                "format": "json",
                "params": {
                    "columns": "COUNT_BIG(*)",
                    "filters": [
                        {
                            "paramName": "obs_collection",
                            "values": [collection],
                        }
                    ],
                    "obstype": "all",
                },
            }
        )
        return extract_count(response.payload), response

    def sample_collection(
        self,
        collection: str,
        *,
        pagesize: int = 25,
    ) -> MastInvocation:
        if pagesize < 1 or pagesize > 500:
            raise ValueError("pagesize must be between 1 and 500")
        return self.invoke(
            {
                "service": "Mast.Caom.Filtered",
                "format": "json",
                "pagesize": pagesize,
                "page": 1,
                "removenullcolumns": True,
                "params": {
                    "columns": "*",
                    "filters": [
                        {
                            "paramName": "obs_collection",
                            "values": [collection],
                        }
                    ],
                    "obstype": "all",
                },
            }
        )


def extract_count(payload: dict[str, Any]) -> int:
    """Extract a count from a MAST count query without assuming its field label."""

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return 0
    row = data[0]
    if not isinstance(row, dict):
        raise MastError("MAST count row is not an object")
    for value in row.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float) and value.is_integer():
            return max(0, int(value))
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            if re.fullmatch(r"[+-]?\d+", cleaned):
                return max(0, int(cleaned))
    raise MastError("MAST count response did not contain an integer")


def normalize_mission_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def roman_registered(missions: Iterable[str]) -> bool:
    """Return True only when the archive explicitly lists a Roman-like mission."""

    normalized = {normalize_mission_name(item) for item in missions}
    accepted = {
        "ROMAN",
        "NANCYGRACEROMAN",
        "NANCYGRACEROMANSPACETELESCOPE",
        "WFIRST",
    }
    return bool(normalized & accepted)


def classify_archive_state(
    *,
    missions: Iterable[str],
    collection_counts: dict[str, int],
) -> str:
    if any(count > 0 for count in collection_counts.values()):
        return "ROMAN_CAOM_HOLDINGS_AVAILABLE"
    if roman_registered(missions):
        return "ROMAN_REGISTERED_NO_MATCHING_ROWS"
    return "PRELAUNCH_NO_ROMAN_CAOM_HOLDINGS"


def probe_page(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    session: requests.Session | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> PageProbe:
    if not url.startswith("https://"):
        raise ValueError("official page URL must use HTTPS")
    client = session or requests.Session()
    try:
        response = client.get(
            url,
            timeout=timeout_seconds,
            headers={"User-Agent": user_agent, "Accept": "text/html,*/*;q=0.5"},
        )
    except requests.RequestException as exc:
        raise MastError(f"official page transport failed: {exc.__class__.__name__}") from exc
    raw = bytes(response.content)
    title: str | None = None
    content_type = response.headers.get("Content-Type", "")
    if "html" in content_type.lower():
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip()
    return PageProbe(
        requested_url=url,
        final_url=str(response.url),
        http_status=response.status_code,
        content_type=content_type,
        retrieved_utc=datetime.now(timezone.utc).isoformat(),
        raw_bytes=raw,
        title=title,
        last_modified=response.headers.get("Last-Modified"),
    )


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "page"
