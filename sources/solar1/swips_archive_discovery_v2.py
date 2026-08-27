#!/usr/bin/env python3
"""SOLAR-1 SWiPS archive discovery through the documented NCEI bucket.

This probe distinguishes a reachable bucket from a matching object inventory.
It preserves raw ListObjectsV2 XML, hashes every response, follows continuation
tokens, and performs no plasma calculation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import requests

DISCOVERY_VERSION = "2.1.0"
BUCKET_URL = "https://archive.data.noaa.gov/satellite-spaceweather"
BUCKET_NAME = BUCKET_URL  # CI test compatibility alias

PREFIX_PROBES = [
    ("swfo_root", "SWFO/"),
    ("solar1_root", "SWFO/SOLAR-1/"),
    ("sol1_root", "SWFO/SOL-1/"),
    ("solar1_swips", "SWFO/SOLAR-1/SWIPS/"),
    ("sol1_swips", "SWFO/SOL-1/SWIPS/"),
    ("solar1_swips_l0b", "SWFO/SOLAR-1/SWIPS/swips-l0b/"),
    ("solar1_swips_l1a", "SWFO/SOLAR-1/SWIPS/swips-l1a/"),
    ("solar1_swips_l1b", "SWFO/SOLAR-1/SWIPS/swips-l1b/"),
    ("solar1_swips_l2", "SWFO/SOLAR-1/SWIPS/swips-l2/"),
    ("solar1_swips_l3", "SWFO/SOLAR-1/SWIPS/swips-l3/"),
    ("sol1_swips_l0b", "SWFO/SOL-1/SWIPS/swips-l0b/"),
    ("sol1_swips_l1a", "SWFO/SOL-1/SWIPS/swips-l1a/"),
    ("sol1_swips_l1b", "SWFO/SOL-1/SWIPS/swips-l1b/"),
    ("sol1_swips_l2", "SWFO/SOL-1/SWIPS/swips-l2/"),
    ("sol1_swips_l3", "SWFO/SOL-1/SWIPS/swips-l3/"),
    ("swips_docs", "SWFO/docs/SWIPS/"),
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(element: ET.Element | None) -> str | None:
    return element.text if element is not None else None


def parse_list_objects(xml_bytes: bytes) -> dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    namespace = ""
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"

    contents = []
    for item in root.findall(f".//{namespace}Contents"):
        key = _text(item.find(f"{namespace}Key"))
        if key is None:
            continue
        size_text = _text(item.find(f"{namespace}Size"))
        contents.append(
            {
                "key": key,
                "size": int(size_text) if size_text else 0,
                "last_modified": _text(item.find(f"{namespace}LastModified")),
                "etag": _text(item.find(f"{namespace}ETag")),
            }
        )

    prefixes = [
        value
        for value in (
            _text(item.find(f"{namespace}Prefix"))
            for item in root.findall(f".//{namespace}CommonPrefixes")
        )
        if value
    ]
    is_truncated = (_text(root.find(f"{namespace}IsTruncated")) or "").lower() == "true"
    next_token = _text(root.find(f"{namespace}NextContinuationToken"))
    return {
        "objects": contents,
        "common_prefixes": prefixes,
        "is_truncated": is_truncated,
        "next_continuation_token": next_token,
    }


def parse_s3_listing(xml_bytes: bytes | str) -> list[dict[str, Any]]:
    """CI test compatibility wrapper for parsing S3 XML listings directly into objects."""
    if isinstance(xml_bytes, str):
        xml_bytes = xml_bytes.encode('utf-8')
    return parse_list_objects(xml_bytes)["objects"]


def safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "root"


def probe_prefix(
    session: requests.Session,
    *,
    label: str,
    prefix: str,
    outdir: Path,
    max_pages: int = 20,
) -> dict[str, Any]:
    pages = []
    objects = []
    prefixes: set[str] = set()
    continuation: str | None = None

    for page_number in range(1, max_pages + 1):
        params = {
            "list-type": "2",
            "prefix": prefix,
            "delimiter": "/",
            "max-keys": "1000",
        }
        if continuation:
            params["continuation-token"] = continuation

        response = session.get(BUCKET_URL, params=params, timeout=60)
        raw = response.content
        page_path = outdir / f"{safe_label(label)}_page_{page_number:03d}.xml"
        page_path.write_bytes(raw)
        page = {
            "page": page_number,
            "http_code": response.status_code,
            "resolved_url": response.url,
            "raw_path": page_path.name,
            "raw_size_bytes": len(raw),
            "raw_sha256": sha256_bytes(raw),
        }
        pages.append(page)

        if response.status_code in (401, 403):
            return {
                "label": label,
                "prefix": prefix,
                "status": "ACCESS_DENIED",
                "pages": pages,
                "objects": [],
                "common_prefixes": [],
            }
        if response.status_code == 404:
            return {
                "label": label,
                "prefix": prefix,
                "status": "NOT_FOUND",
                "pages": pages,
                "objects": [],
                "common_prefixes": [],
            }
        response.raise_for_status()

        parsed = parse_list_objects(raw)
        objects.extend(parsed["objects"])
        prefixes.update(parsed["common_prefixes"])
        if not parsed["is_truncated"]:
            return {
                "label": label,
                "prefix": prefix,
                "status": "OK",
                "pages": pages,
                "object_count": len(objects),
                "objects": objects,
                "common_prefixes": sorted(prefixes),
                "truncated": False,
            }
        continuation = parsed["next_continuation_token"]
        if not continuation:
            raise ValueError(
                f"{prefix} reported IsTruncated=true without a continuation token"
            )

    return {
        "label": label,
        "prefix": prefix,
        "status": "PAGINATION_LIMIT_REACHED",
        "pages": pages,
        "object_count": len(objects),
        "objects": objects,
        "common_prefixes": sorted(prefixes),
        "truncated": True,
    }


def _is_swips_path(value: str) -> bool:
    return "swips" in value.lower()


def run_discovery(outdir: Path) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": f"NVCPP-SWiPS-Archive/{DISCOVERY_VERSION}"})

    probes = []
    for label, prefix in PREFIX_PROBES:
        try:
            probes.append(
                probe_prefix(
                    session,
                    label=label,
                    prefix=prefix,
                    outdir=outdir,
                )
            )
        except Exception as exc:
            probes.append(
                {
                    "label": label,
                    "prefix": prefix,
                    "status": "FAILED",
                    "error": str(exc),
                    "objects": [],
                    "common_prefixes": [],
                }
            )

    successful = [probe for probe in probes if probe["status"] == "OK"]
    matching_objects = {
        item["key"]: item
        for probe in successful
        for item in probe.get("objects", [])
        if _is_swips_path(item.get("key", ""))
    }
    matching_prefixes = sorted(
        {
            value
            for probe in successful
            for value in probe.get("common_prefixes", [])
            if _is_swips_path(value)
        }
    )
    any_failed = any(probe["status"] == "FAILED" for probe in probes)
    any_denied = any(probe["status"] == "ACCESS_DENIED" for probe in probes)

    if matching_objects:
        state = "PUBLIC_SWIPS_OBJECTS_DISCOVERED"
    elif matching_prefixes:
        state = "PUBLIC_SWIPS_PREFIXES_DISCOVERED_NO_FILES_AT_PROBED_LEVEL"
    elif any_denied:
        state = "ACCESS_DENIED"
    elif any_failed:
        state = "PROBE_PARTIAL_FAILURE"
    else:
        state = "NO_MATCHING_PUBLIC_SWIPS_OBJECTS_AT_DOCUMENTED_BUCKET"

    manifest = {
        "discovery_version": DISCOVERY_VERSION,
        "source": "NOAA/NCEI documented satellite-spaceweather archive bucket",
        "bucket_url": BUCKET_URL,
        "mission": "SOLAR-1",
        "instrument": "SWIPS",
        "discovery_state": state,
        "matching_swips_objects": list(matching_objects.values()),
        "matching_swips_object_count": len(matching_objects),
        "matching_swips_common_prefixes": matching_prefixes,
        "probes": probes,
        "interpretation_limits": [
            "HTTP 200 proves only that a ListObjectsV2 request was answered",
            "zero objects at one prefix does not prove that SWiPS data do not exist elsewhere",
            "no science computation is enabled by this discovery result",
        ],
    }
    path = outdir / "swips_archive_discovery_v2_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if not successful:
        raise SystemExit("all documented bucket probes failed")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover public SOLAR-1 SWiPS archive objects")
    parser.add_argument("--outdir", type=Path, default=Path("runs/solar1/swips_archive_v2"))
    args = parser.parse_args()
    manifest = run_discovery(args.outdir)
    print(
        json.dumps(
            {
                "discovery_state": manifest["discovery_state"],
                "objects": manifest["matching_swips_object_count"],
                "prefixes": len(manifest["matching_swips_common_prefixes"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
