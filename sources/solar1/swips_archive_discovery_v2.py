#!/usr/bin/env python3
"""
NVCPP SWiPS Archive Discovery V2
Directly queries NCEI AWS-compatible archive endpoints for SOLAR-1 SWiPS netCDF-4 files,
bypassing incomplete HAPI routes to verify object existence, headers, and metadata.
"""

import argparse
import json
import hashlib
from pathlib import Path
import requests
import xml.etree.ElementTree as ET

ARCHIVE_BASE_URL = "https://archive.data.noaa.gov"
SWIPS_PREFIXES = [
    "SWFO/SOLAR-1/SWIPS/swips-l0b/",
    "SWFO/SOLAR-1/SWIPS/swips-l1a/",
    "SWFO/SOLAR-1/SWIPS/swips-l1b/",
    "SWFO/SOLAR-1/SWIPS/swips-l2/",
    "SWFO/SOLAR-1/SWIPS/swips-l3/",
    "SWFO/docs/SWIPS/"
]

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def probe_archive_prefix(session: requests.Session, prefix: str) -> dict:
    # NCEI S3-compatible bucket listing query using list-type=2
    list_url = f"{ARCHIVE_BASE_URL}/?prefix={prefix}&list-type=2"
    print(f"[NVCPP-ArchiveV2] Probing bucket prefix: {prefix}")
    
    try:
        response = session.get(list_url, timeout=30)
        if response.status_code == 404:
            return {"prefix": prefix, "status": "not_found", "objects": []}
        
        response.raise_for_status()
        xml_content = response.text
        
        # Parse S3 XML response
        objects = []
        try:
            root = ET.fromstring(xml_content)
            # Handle S3 namespace if present
            ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
            for content in root.findall('.//s3:Contents', ns) or root.findall('.//Contents'):
                key_elem = content.find('s3:Key', ns) if content.find('s3:Key', ns) is not None else content.find('Key')
                size_elem = content.find('s3:Size', ns) if content.find('s3:Size', ns) is not None else content.find('Size')
                modified_elem = content.find('s3:LastModified', ns) if content.find('s3:LastModified', ns) is not None else content.find('LastModified')
                
                if key_elem is not None and key_elem.text:
                    objects.append({
                        "key": key_elem.text,
                        "size": int(size_elem.text) if size_elem is not None and size_elem.text else 0,
                        "last_modified": modified_elem.text if modified_elem is not None else "unknown"
                    })
        except Exception as parse_err:
            print(f"[NVCPP-ArchiveV2] XML parse warning for {prefix}: {parse_err}")
            
        return {
            "prefix": prefix,
            "status": "ok",
            "http_code": response.status_code,
            "object_count": len(objects),
            "objects": objects[:50]  # Limit sample size for manifest clarity
        }
    except Exception as e:
        return {
            "prefix": prefix,
            "status": "failed",
            "error": str(e)
        }

def run_swips_archive_discovery(outdir: str = "runs/solar1/swips_archive_v2"):
    out_dir = Path(outdir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[NVCPP-ArchiveV2] Initializing direct NCEI archive crawl for SWiPS...")
    session = requests.Session()
    session.headers.update({"User-Agent": "NVCPP-ArchiveDiscoveryV2/1.0.0"})

    inventory = []
    total_objects = 0

    for prefix in SWIPS_PREFIXES:
        res = probe_archive_prefix(session, prefix)
        inventory.append(res)
        if res["status"] == "ok":
            total_objects += res["object_count"]

    manifest = {
        "source": "NCEI AWS Archive Bucket",
        "archive_base": ARCHIVE_BASE_URL,
        "mission": "SOLAR-1",
        "instrument": "SWIPS",
        "discovery_state": "VERIFIED_ARCHIVE_ACCESSIBLE" if total_objects > 0 else "ARCHIVE_EMPTY_OR_LOCKED",
        "total_objects_discovered": total_objects,
        "prefixes_probed": inventory
    }

    manifest_path = out_dir / "swips_archive_discovery_v2_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[NVCPP-ArchiveV2] Discovery V2 complete. Discovered {total_objects} objects. Manifest written to {manifest_path}")
    return manifest

def main():
    parser = argparse.ArgumentParser(description="SOLAR-1 SWiPS Archive Discovery V2")
    parser.add_argument("--outdir", default="runs/solar1/swips_archive_v2", help="Output directory")
    args = parser.parse_args()
    run_swips_archive_discovery(outdir=args.outdir)

if __name__ == "__main__":
    main()
