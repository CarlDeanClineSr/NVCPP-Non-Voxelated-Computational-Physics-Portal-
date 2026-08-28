"""Evidence-first intake for Roman simulation and test-data exports.

The intake layer inventories files and validates only formats NVCPP can identify
without guessing. Large Roman I-Sim, Research Nexus, MAST, and detector-test
products remain byte-preserved external inputs until a format-specific contract
and reader are frozen.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable

import numpy as np

from .contracts import RomanContractError, load_contract
from .synthetic_fixture import analyze_arrays


class RomanIntakeError(RuntimeError):
    """Raised when an intake source cannot be admitted."""


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def identify_container(path: Path) -> str:
    suffix = path.suffix.lower()
    prefix = path.read_bytes()[:16]
    if prefix.startswith(b"#ASDF"):
        return "ASDF"
    if prefix.startswith(b"SIMPLE  ="):
        return "FITS"
    if suffix == ".npz":
        return "NPZ"
    if suffix == ".json":
        return "JSON"
    if suffix in {".fits", ".fit", ".fts"}:
        return "FITS_SUFFIX_UNVERIFIED"
    if suffix == ".asdf":
        return "ASDF_SUFFIX_UNVERIFIED"
    return "OPAQUE"


def inspect_npz(path: Path) -> dict[str, Any]:
    with np.load(path, allow_pickle=False) as payload:
        arrays = {
            key: {
                "shape": [int(value) for value in payload[key].shape],
                "dtype": str(payload[key].dtype),
                "finite_fraction": float(
                    np.mean(np.isfinite(payload[key]))
                    if np.issubdtype(payload[key].dtype, np.number)
                    else 1.0
                ),
            }
            for key in sorted(payload.files)
        }
        result: dict[str, Any] = {
            "reader": "numpy",
            "arrays": arrays,
        }
        required = {"SCI", "ERR", "DQ"}
        if required <= set(payload.files):
            result["image_metrics"] = analyze_arrays(
                np.asarray(payload["SCI"]),
                np.asarray(payload["ERR"]),
                np.asarray(payload["DQ"]),
            )
        else:
            result["image_metrics"] = None
            result["missing_image_arrays"] = sorted(required - set(payload.files))
        return result


def inspect_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RomanIntakeError(f"invalid JSON file: {path.name}") from exc
    if isinstance(payload, dict):
        root_type = "object"
        keys = sorted(str(key) for key in payload)
    elif isinstance(payload, list):
        root_type = "array"
        keys = []
    else:
        root_type = type(payload).__name__
        keys = []
    return {
        "reader": "json",
        "root_type": root_type,
        "top_level_keys": keys,
    }


def inspect_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RomanIntakeError(f"input is not a file: {path}")
    container = identify_container(path)
    record: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "container": container,
    }
    if container == "NPZ":
        record["inspection"] = inspect_npz(path)
    elif container == "JSON":
        record["inspection"] = inspect_json(path)
    elif container in {"ASDF", "FITS", "ASDF_SUFFIX_UNVERIFIED", "FITS_SUFFIX_UNVERIFIED"}:
        record["inspection"] = {
            "reader": "not_loaded",
            "status": "FORMAT_SPECIFIC_READER_CONTRACT_REQUIRED",
            "note": (
                "The exact bytes and container signature are preserved. Detailed "
                "Roman datamodel inspection requires a separately pinned ASDF/"
                "roman_datamodels or FITS reader environment."
            ),
        }
    else:
        record["inspection"] = {
            "reader": "none",
            "status": "OPAQUE_PRESERVED",
        }
    return record


def collect_inputs(items: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for item in items:
        if item.is_dir():
            files.extend(path for path in item.rglob("*") if path.is_file())
        elif item.is_file():
            files.append(item)
        else:
            raise RomanIntakeError(f"input path does not exist: {item}")
    unique = sorted({path.resolve() for path in files})
    if not unique:
        raise RomanIntakeError("no input files were found")
    return unique


def build_intake_manifest(
    *,
    inputs: Iterable[Path],
    source_class: str,
    config_path: Path,
    outdir: Path,
    copy_files: bool = False,
    copy_limit_bytes: int = 50 * 1024 * 1024,
) -> dict[str, Any]:
    contract = load_contract(config_path)
    accepted = set(contract["nexus"].get("accepted_source_classes", []))
    if source_class not in accepted:
        raise RomanIntakeError(
            f"source class {source_class!r} is not allowed; accepted={sorted(accepted)}"
        )

    paths = collect_inputs(inputs)
    outdir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    copied_dir = outdir / "preserved_inputs"
    for path in paths:
        record = inspect_file(path)
        if copy_files:
            if record["size_bytes"] > copy_limit_bytes:
                record["copy_status"] = "SKIPPED_SIZE_LIMIT"
            else:
                copied_dir.mkdir(exist_ok=True)
                destination = copied_dir / path.name
                if destination.exists():
                    raise RomanIntakeError(f"copy destination already exists: {destination}")
                shutil.copy2(path, destination)
                if sha256_file(destination) != record["sha256"]:
                    raise RomanIntakeError(f"copied hash mismatch: {path.name}")
                record["copy_status"] = "COPIED_AND_HASH_VERIFIED"
                record["preserved_path"] = str(destination.relative_to(outdir))
        records.append(record)

    manifest = {
        "intake_version": "1.0.0",
        "status": "SUCCESS",
        "mission": "ROMAN",
        "domain": "ASTRONOMICAL_OBSERVATORY",
        "source_class": source_class,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(records),
        "files": records,
        "flight_science_data_assumed": False,
        "science_claims_enabled": False,
        "l1_plasma_physics_allowed": False,
        "chi_B24M_allowed": False,
        "interpretation_limits": [
            "Container identification is not equivalent to Roman schema validation.",
            "ASDF and FITS files require a pinned format-specific reader contract.",
            "Research Nexus or Roman I-Sim labels come from explicit operator provenance.",
            "No telescope detector product enters L1 plasma calculations.",
        ],
    }
    manifest_path = outdir / "roman_intake_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    report = [
        "# Roman Simulation/Test-Data Intake",
        "",
        f"- **Status:** `{manifest['status']}`",
        f"- **Source class:** `{source_class}`",
        f"- **Files:** {len(records)}",
        f"- **Copied into package:** {sum(r.get('copy_status') == 'COPIED_AND_HASH_VERIFIED' for r in records)}",
        "",
        "This intake preserves identity and hashes before interpretation. It does not "
        "declare a product to be flight data or an official Roman datamodel.",
    ]
    (outdir / "ROMAN_INTAKE.md").write_text(
        "\n".join(report) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Roman simulation/test exports")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--source-class", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/roman_prelaunch.v1.json"),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("runs/roman/intake"),
    )
    parser.add_argument("--copy-small-files", action="store_true")
    args = parser.parse_args()

    try:
        manifest = build_intake_manifest(
            inputs=args.input,
            source_class=args.source_class,
            config_path=args.config,
            outdir=args.outdir,
            copy_files=args.copy_small_files,
        )
    except (RomanContractError, RomanIntakeError, ValueError) as exc:
        print(f"[NVCPP-ROMAN-INTAKE-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(
        {
            "status": manifest["status"],
            "source_class": manifest["source_class"],
            "file_count": manifest["file_count"],
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
