#!/usr/bin/env python3
"""Finalize the bounded Gannon multipoint artifact without changing its physics.

The first audit implementation standardized an already-canonical DSCOVR minute
through a shared vector helper.  Its selected-structure ``native_samples`` field
therefore counted one canonical input row rather than the underlying admitted
one-second samples.  This finalizer restores the true per-minute DSCOVR source
count from the preserved raw CDAWeb bytes, verifies that every selected vector
change uses an exact preceding minute, refreshes the summary/report, and rebuilds
artifact hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from historical.download_dscovr_cdaweb import canonicalize_one_minute

FINALIZER_VERSION = "1.0.0"


class FinalizationError(RuntimeError):
    """Raised when a bounded multipoint artifact cannot be finalized safely."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_preserved_dscovr_raw(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FinalizationError(f"preserved DSCOVR raw file is absent: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FinalizationError("preserved DSCOVR raw file is not UTF-8") from exc
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise FinalizationError("preserved DSCOVR raw file has no table rows")
    frame = pd.read_csv(io.StringIO("\n".join(lines)), sep=r"\s{2,}", engine="python")
    if frame.empty:
        raise FinalizationError("preserved DSCOVR raw table parsed as empty")
    return frame


def verify_exact_previous(selected: dict[str, Any]) -> dict[str, Any]:
    integrity: dict[str, Any] = {}
    for mission in ("DSCOVR", "ACE", "WIND"):
        structure = selected.get(mission)
        if not isinstance(structure, dict):
            raise FinalizationError(f"selected structure is absent for {mission}")
        previous = structure.get("previous")
        if not isinstance(previous, dict):
            raise FinalizationError(f"selected {mission} structure has no previous row")
        current_time = pd.Timestamp(structure["time_utc"])
        previous_time = pd.Timestamp(previous["time_utc"])
        if current_time.tzinfo is None or previous_time.tzinfo is None:
            raise FinalizationError(f"selected {mission} timestamps lack UTC offsets")
        current_time = current_time.tz_convert("UTC")
        previous_time = previous_time.tz_convert("UTC")
        offset_seconds = (current_time - previous_time).total_seconds()
        if offset_seconds != 60.0:
            raise FinalizationError(
                f"selected {mission} transition bridges {offset_seconds} seconds"
            )
        integrity[mission] = {
            "selected_time_utc": current_time.isoformat(),
            "previous_time_utc": previous_time.isoformat(),
            "previous_offset_seconds": offset_seconds,
            "exact_previous_minute": True,
        }
    return integrity


def restore_dscovr_native_provenance(
    selected: dict[str, Any],
    *,
    raw_path: Path,
    workdir: Path,
) -> dict[str, Any]:
    raw = parse_preserved_dscovr_raw(raw_path)
    canonical, metrics = canonicalize_one_minute(raw, workdir)
    canonical["EPOCH"] = pd.to_datetime(
        canonical["EPOCH"], format="ISO8601", utc=True, errors="coerce"
    )
    if canonical["EPOCH"].isna().any():
        raise FinalizationError("reconstructed DSCOVR canonical table has invalid UTC")

    structure = selected["DSCOVR"]
    selected_time = pd.Timestamp(structure["time_utc"]).tz_convert("UTC")
    previous_time = pd.Timestamp(structure["previous"]["time_utc"]).tz_convert("UTC")
    selected_row = canonical.loc[canonical["EPOCH"] == selected_time]
    previous_row = canonical.loc[canonical["EPOCH"] == previous_time]
    if len(selected_row) != 1 or len(previous_row) != 1:
        raise FinalizationError(
            "selected DSCOVR minute or exact predecessor is absent from reconstructed source"
        )

    old_secondary_count = int(structure.get("native_samples", 0))
    current = selected_row.iloc[0]
    previous = previous_row.iloc[0]
    true_count = int(current["native_sample_count"])
    true_coverage = float(current["native_coverage_fraction"])
    previous_count = int(previous["native_sample_count"])
    previous_coverage = float(previous["native_coverage_fraction"])
    if true_count < 57 or previous_count < 57:
        raise FinalizationError("selected DSCOVR transition lacks 57/60 native coverage")

    structure["secondary_standardization_input_rows"] = old_secondary_count
    structure["native_samples"] = true_count
    structure["native_coverage_fraction"] = true_coverage
    structure["previous"]["native_samples"] = previous_count
    structure["previous"]["native_coverage_fraction"] = previous_coverage
    structure["native_sample_semantics"] = (
        "admitted DSCOVR_H0_MAG one-second samples in the canonical UTC minute"
    )
    structure["secondary_standardization_input_row_semantics"] = (
        "already-canonical one-minute rows presented to the shared vector helper"
    )
    return {
        "selected_native_samples": true_count,
        "selected_native_coverage_fraction": true_coverage,
        "previous_native_samples": previous_count,
        "previous_native_coverage_fraction": previous_coverage,
        "secondary_standardization_input_rows_before_correction": old_secondary_count,
        "reconstruction_metrics": metrics,
        "raw_path": str(raw_path),
        "raw_sha256": sha256_file(raw_path),
    }


def rebuild_summary(root: Path, manifest: dict[str, Any]) -> pd.DataFrame:
    selected = manifest["selected_structures"]
    plasma_cuts = manifest.get("plasma_cuts", {})
    rows: list[dict[str, Any]] = []
    for mission in ("DSCOVR", "ACE", "WIND"):
        structure = selected[mission]
        cut = plasma_cuts.get(mission)
        ratios = cut.get("post_to_pre_ratio", {}) if isinstance(cut, dict) else {}
        rows.append(
            {
                "mission": mission,
                "structure_found": True,
                **{
                    key: value
                    for key, value in structure.items()
                    if key not in {
                        "previous",
                        "native_sample_semantics",
                        "secondary_standardization_input_row_semantics",
                    }
                },
                "previous_bz_gse_nT": structure["previous"].get("bz_gse_nT"),
                "previous_native_samples": structure["previous"].get("native_samples"),
                "previous_offset_seconds": 60.0,
                "density_post_to_pre": ratios.get("density_cm3"),
                "speed_post_to_pre": ratios.get("speed_km_s"),
                "temperature_post_to_pre": ratios.get("temperature_native"),
                "dynamic_pressure_post_to_pre": ratios.get("dynamic_pressure_nPa"),
            }
        )
    table = pd.DataFrame(rows)
    table.to_csv(root / "reports" / "gannon_multipoint_summary.csv", index=False)
    return table


def rebuild_report(root: Path, manifest: dict[str, Any], table: pd.DataFrame) -> None:
    classification = manifest.get("classification")
    integrity = manifest["selected_structure_integrity"]
    limits = manifest.get("interpretation_limits", [])
    lines = [
        "# Gannon 2024 Three-Spacecraft MAG-plus-Plasma Audit",
        "",
        f"Classification: **{classification}**",
        "",
        "The classification means that DSCOVR, ACE, and Wind each recorded",
        "qualifying GSE-vector or magnitude structure in the bounded comparison",
        "window. It does not identify one discontinuity or establish propagation.",
        "",
        "## Selected magnetic structures",
        "",
        "```text",
        table.to_string(index=False),
        "```",
        "",
        "## Exact-minute integrity",
        "",
    ]
    for mission in ("DSCOVR", "ACE", "WIND"):
        record = integrity[mission]
        lines.append(
            f"- {mission}: selected `{record['selected_time_utc']}`; previous "
            f"`{record['previous_time_utc']}`; exact offset "
            f"`{record['previous_offset_seconds']}` seconds."
        )
    lines.extend(
        [
            "",
            "For DSCOVR, `native_samples` now means admitted underlying",
            "one-second source samples in the selected canonical minute. The",
            "earlier value of one was the count of already-canonical rows entering",
            "a secondary standardization helper, not native instrument samples.",
            "",
            "## Interpretation limits",
            "",
            *[f"- {item}" for item in limits],
            "- Full-day gate density is evaluated separately before treating bounded support as selective evidence.",
        ]
    )
    (root / "reports" / "GANNON_MULTIPOINT_AUDIT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def rebuild_artifact_inventory(root: Path, manifest_path: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != manifest_path:
            artifacts.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return artifacts


def finalize(root: Path) -> dict[str, Any]:
    manifest_path = root / "gannon_multipoint_manifest.json"
    if not manifest_path.is_file():
        raise FinalizationError(f"multipoint manifest is absent: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SUCCESS":
        raise FinalizationError(
            f"cannot finalize multipoint status {manifest.get('status')!r}"
        )
    selected = manifest.get("selected_structures")
    if not isinstance(selected, dict):
        raise FinalizationError("multipoint manifest lacks selected structures")

    exact_integrity = verify_exact_previous(selected)
    dscovr_provenance = restore_dscovr_native_provenance(
        selected,
        raw_path=(
            root
            / "raw"
            / "DSCOVR_H0_MAG"
            / "DSCOVR_H0_MAG_raw_bytes.csv"
        ),
        workdir=root / "raw" / "DSCOVR_H0_MAG",
    )
    for mission, record in exact_integrity.items():
        structure = selected[mission]
        record["native_samples"] = int(structure["native_samples"])
        if mission == "DSCOVR":
            record["native_sample_semantics"] = structure[
                "native_sample_semantics"
            ]
        else:
            record["native_sample_semantics"] = (
                "admitted source samples averaged into the canonical UTC minute"
            )

    manifest["selected_structures"] = selected
    manifest["selected_structure_integrity"] = exact_integrity
    manifest["dscovr_native_provenance_correction"] = dscovr_provenance
    manifest["artifact_finalizer"] = {
        "version": FINALIZER_VERSION,
        "completed_utc": utc_now(),
        "physics_recomputed": False,
        "selected_times_changed": False,
        "classification_changed": False,
        "purpose": (
            "restore source-native DSCOVR minute counts, require exact previous "
            "minutes, and refresh report/hash provenance"
        ),
    }

    table = rebuild_summary(root, manifest)
    rebuild_report(root, manifest, table)
    manifest["artifacts"] = rebuild_artifact_inventory(root, manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("runs/audits/gannon_multipoint"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = finalize(args.root)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "classification": manifest.get("classification"),
                "dscovr_native_samples": manifest["selected_structures"][
                    "DSCOVR"
                ]["native_samples"],
                "exact_previous_minutes": all(
                    item["exact_previous_minute"]
                    for item in manifest["selected_structure_integrity"].values()
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
