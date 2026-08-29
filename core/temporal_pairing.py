#!/usr/bin/env python3
"""NVCPP MAG-to-MAG coherence analysis.

This module tests cross-spacecraft coherence without interpolation or forward
fill. A data-selected lag is reported as a candidate only when its peak is
narrow and stable under segment and block-resampling checks. It never labels a
correlation alone as proof of propagation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PAIRING_VERSION = "2.1.0"
EXPECTED_PROTOCOL = "CLINE-L1-B24M-TRAIL-v1"
POSITIVE_LAG_DEFINITION = (
    "positive lag L compares DSCOVR(t) with SOLAR-1(t+L); "
    "positive values mean the SOLAR-1 feature occurs later"
)
COHERENCE_MIN_R = 0.70
LOOK_ELSEWHERE_MAX_P = 0.01
LAG_IMPROVEMENT_MIN = 0.02
PLATEAU_995_MAX_LAGS = 3
BOOTSTRAP_MODE_MIN_FRACTION = 0.60
BOOTSTRAP_95_MAX_SPAN_MINUTES = 2.0
SEGMENT_MAX_SPAN_MINUTES = 2


def classification_policy() -> dict[str, Any]:
    return {
        "coherence": {
            "best_pearson_r_minimum": COHERENCE_MIN_R,
            "look_elsewhere_p_value_maximum": LOOK_ELSEWHERE_MAX_P,
        },
        "lag_candidate": {
            "improvement_over_zero_lag_minimum": LAG_IMPROVEMENT_MIN,
            "peak_plateau_99_5_percent_max_lags": PLATEAU_995_MAX_LAGS,
            "bootstrap_mode_fraction_minimum": BOOTSTRAP_MODE_MIN_FRACTION,
            "bootstrap_95_percent_span_max_minutes": BOOTSTRAP_95_MAX_SPAN_MINUTES,
            "daily_segment_span_max_minutes": SEGMENT_MAX_SPAN_MINUTES,
            "ephemeris_still_required": True,
        },
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_run_manifest(
    manifest_path: Path,
    *,
    mission: str,
    data_path: Path,
) -> dict[str, Any]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("status") != "SUCCESS":
        raise ValueError(f"{mission} run manifest is not SUCCESS")
    if data.get("protocol_id") != EXPECTED_PROTOCOL:
        raise ValueError(
            f"{mission} protocol mismatch: {data.get('protocol_id')!r}"
        )
    coordinate_frame = data.get("source", {}).get("coordinate_frame")
    if coordinate_frame != "GSE":
        raise ValueError(
            f"{mission} coordinate frame must be GSE; found {coordinate_frame!r}"
        )

    expected_hash = None
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if isinstance(artifact, dict) and artifact.get("path") == data_path.name:
                expected_hash = artifact.get("sha256")
                break
    elif isinstance(artifacts, dict):
        for artifact in artifacts.values():
            if isinstance(artifact, dict) and artifact.get("path") == data_path.name:
                expected_hash = artifact.get("sha256")
                break

    observed_hash = sha256_file(data_path)
    if not expected_hash:
        raise ValueError(
            f"{mission} manifest does not hash the supplied data artifact {data_path.name}"
        )
    if observed_hash != expected_hash:
        raise ValueError(
            f"{mission} data artifact hash mismatch: "
            f"expected {expected_hash}, observed {observed_hash}"
        )
    return {
        "path": str(manifest_path),
        "sha256": sha256_file(manifest_path),
        "git_commit": data.get("git_commit"),
        "protocol_id": data.get("protocol_id"),
        "protocol_version": data.get("protocol_version"),
        "coordinate_frame": coordinate_frame,
        "data_sha256": observed_hash,
    }


def _time_column(frame: pd.DataFrame, mission: str) -> str:
    candidates = {
        "DSCOVR": ("time_utc", "EPOCH", "time"),
        "SOLAR-1": ("time_utc", "time", "EPOCH"),
    }[mission]
    found = [name for name in candidates if name in frame.columns]
    if not found:
        raise ValueError(f"{mission} file has no recognized UTC time column")
    return found[0]


def load_canonical_table(path: Path, mission: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    time_col = _time_column(frame, mission)
    required = ["B_mag", "delta_B24M", "chi_B24M"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"{mission} file is missing canonical columns: {missing}")

    frame["time_utc"] = pd.to_datetime(frame[time_col], utc=True, errors="coerce")
    if frame["time_utc"].isna().any():
        raise ValueError(f"{mission} file contains invalid timestamps")
    if frame["time_utc"].duplicated().any():
        raise ValueError(f"{mission} file contains duplicate timestamps")

    not_minute = (
        (frame["time_utc"].dt.second != 0)
        | (frame["time_utc"].dt.microsecond != 0)
    )
    if not_minute.any():
        raise ValueError(
            f"{mission} pairing input is not the canonical exact one-minute product"
        )

    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "baseline_status" in frame.columns:
        frame = frame.loc[frame["baseline_status"] == "VALID"].copy()

    frame = frame.dropna(subset=required).sort_values("time_utc")
    if frame.empty:
        raise ValueError(f"{mission} has no valid canonical rows")

    renamed = frame.set_index("time_utc")[required].rename(
        columns={name: f"{name}_{mission.replace('-', '')}" for name in required}
    )
    return renamed


def align_exact(dscovr: pd.DataFrame, solar1: pd.DataFrame) -> pd.DataFrame:
    merged = dscovr.join(solar1, how="inner")
    if merged.empty:
        raise ValueError("no exact one-minute overlap exists")
    return merged


def pearson_at_lag(
    x: np.ndarray,
    y: np.ndarray,
    lag: int,
    *,
    min_pairs: int = 100,
) -> tuple[float, int]:
    if lag > 0:
        a, b = x[:-lag], y[lag:]
    elif lag < 0:
        a, b = x[-lag:], y[:lag]
    else:
        a, b = x, y

    valid = np.isfinite(a) & np.isfinite(b)
    n = int(valid.sum())
    if n < min_pairs:
        return np.nan, n
    a = a[valid]
    b = b[valid]
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan, n
    return float(np.corrcoef(a, b)[0, 1]), n


def lag_scan(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_lag: int,
    min_pairs: int = 100,
) -> pd.DataFrame:
    rows = []
    for lag in range(-max_lag, max_lag + 1):
        correlation, pairs = pearson_at_lag(x, y, lag, min_pairs=min_pairs)
        rows.append({"lag_minutes": lag, "pearson_r": correlation, "pairs": pairs})
    result = pd.DataFrame(rows)
    if not result["pearson_r"].notna().any():
        raise ValueError("lag scan produced no finite correlation")
    return result


def best_from_scan(scan: pd.DataFrame) -> tuple[int, float]:
    finite = scan.dropna(subset=["pearson_r"])
    row = finite.loc[finite["pearson_r"].idxmax()]
    return int(row["lag_minutes"]), float(row["pearson_r"])


def moving_block_bootstrap(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_lag: int,
    block_minutes: int,
    iterations: int,
    seed: int,
) -> list[int]:
    if iterations <= 0:
        return []
    n = len(x)
    block = min(max(10, block_minutes), n)
    rng = np.random.default_rng(seed)
    starts = np.arange(0, max(1, n - block + 1))
    winners: list[int] = []
    blocks_needed = int(np.ceil(n / block))
    for _ in range(iterations):
        indices = np.concatenate(
            [
                np.arange(start, start + block)
                for start in rng.choice(starts, size=blocks_needed, replace=True)
            ]
        )[:n]
        scan = lag_scan(x[indices], y[indices], max_lag=max_lag)
        winners.append(best_from_scan(scan)[0])
    return winners


def circular_shift_null(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_lag: int,
    block_minutes: int,
    iterations: int,
    seed: int,
) -> list[float]:
    if iterations <= 0:
        return []
    n = len(x)
    lower = min(n - 1, max(max_lag + block_minutes, 2 * max_lag + 1))
    upper = max(lower + 1, n - lower)
    if upper <= lower:
        return []
    rng = np.random.default_rng(seed)
    maxima: list[float] = []
    for shift in rng.integers(lower, upper, size=iterations):
        shifted = np.roll(y, int(shift))
        scan = lag_scan(x, shifted, max_lag=max_lag)
        maxima.append(best_from_scan(scan)[1])
    return maxima


def segment_stability(
    frame: pd.DataFrame,
    x_column: str,
    y_column: str,
    *,
    max_lag: int,
) -> pd.DataFrame:
    rows = []
    for date, group in frame.groupby(frame.index.date):
        if len(group) < 240:
            continue
        scan = lag_scan(
            group[x_column].to_numpy(dtype=float),
            group[y_column].to_numpy(dtype=float),
            max_lag=max_lag,
        )
        lag, correlation = best_from_scan(scan)
        zero = float(scan.loc[scan["lag_minutes"] == 0, "pearson_r"].iloc[0])
        dx = np.diff(group[x_column].to_numpy(dtype=float))
        dy = np.diff(group[y_column].to_numpy(dtype=float))
        diff_scan = lag_scan(dx, dy, max_lag=max_lag, min_pairs=60)
        diff_lag, diff_correlation = best_from_scan(diff_scan)
        rows.append(
            {
                "date": str(date),
                "rows": len(group),
                "best_lag_minutes": lag,
                "best_pearson_r": correlation,
                "zero_lag_pearson_r": zero,
                "first_difference_best_lag_minutes": diff_lag,
                "first_difference_best_pearson_r": diff_correlation,
            }
        )
    return pd.DataFrame(rows)


def classify(
    *,
    best_r: float,
    zero_r: float,
    plateau_995: list[int],
    bootstrap_lags: list[int],
    segment_lags: list[int],
    null_p: float | None,
) -> str:
    coherent = (
        best_r >= COHERENCE_MIN_R
        and null_p is not None
        and null_p <= LOOK_ELSEWHERE_MAX_P
    )
    if not coherent:
        return "NO_STABLE_COHERENCE"

    improvement = best_r - zero_r
    counts = Counter(bootstrap_lags)
    mode_fraction = max(counts.values()) / len(bootstrap_lags) if counts else 0.0
    bootstrap_span = (
        float(np.percentile(bootstrap_lags, 97.5) - np.percentile(bootstrap_lags, 2.5))
        if bootstrap_lags
        else np.inf
    )
    segment_span = max(segment_lags) - min(segment_lags) if segment_lags else np.inf

    lag_stable = (
        improvement >= LAG_IMPROVEMENT_MIN
        and len(plateau_995) <= PLATEAU_995_MAX_LAGS
        and mode_fraction >= BOOTSTRAP_MODE_MIN_FRACTION
        and bootstrap_span <= BOOTSTRAP_95_MAX_SPAN_MINUTES
        and segment_span <= SEGMENT_MAX_SPAN_MINUTES
    )
    return (
        "LAG_CANDIDATE_REQUIRES_EPHEMERIS"
        if lag_stable
        else "COHERENT_BUT_LAG_UNRESOLVED"
    )


def run_pairing_engine(
    dscovr_csv: Path,
    solar1_csv: Path,
    dscovr_manifest: Path,
    solar1_manifest: Path,
    outdir: Path,
    *,
    max_lag: int = 60,
    block_minutes: int = 240,
    bootstrap_iterations: int = 300,
    null_iterations: int = 300,
    seed: int = 1729,
) -> dict[str, Any]:
    outdir.mkdir(parents=True, exist_ok=True)
    dscovr_provenance = validate_run_manifest(
        dscovr_manifest, mission="DSCOVR", data_path=dscovr_csv
    )
    solar1_provenance = validate_run_manifest(
        solar1_manifest, mission="SOLAR-1", data_path=solar1_csv
    )
    dscovr = load_canonical_table(dscovr_csv, "DSCOVR")
    solar1 = load_canonical_table(solar1_csv, "SOLAR-1")
    aligned = align_exact(dscovr, solar1)

    x_col = "chi_B24M_DSCOVR"
    y_col = "chi_B24M_SOLAR1"
    x = aligned[x_col].to_numpy(dtype=float)
    y = aligned[y_col].to_numpy(dtype=float)

    scan = lag_scan(x, y, max_lag=max_lag)
    best_lag, best_r = best_from_scan(scan)
    zero_r = float(scan.loc[scan["lag_minutes"] == 0, "pearson_r"].iloc[0])
    plateau_99 = scan.loc[scan["pearson_r"] >= best_r * 0.99, "lag_minutes"].astype(int).tolist()
    plateau_995 = scan.loc[scan["pearson_r"] >= best_r * 0.995, "lag_minutes"].astype(int).tolist()

    bootstrap_lags = moving_block_bootstrap(
        x,
        y,
        max_lag=max_lag,
        block_minutes=block_minutes,
        iterations=bootstrap_iterations,
        seed=seed,
    )
    null_maxima = circular_shift_null(
        x,
        y,
        max_lag=max_lag,
        block_minutes=block_minutes,
        iterations=null_iterations,
        seed=seed + 1,
    )
    null_p = (
        (1 + sum(value >= best_r for value in null_maxima)) / (1 + len(null_maxima))
        if null_maxima
        else None
    )

    segments = segment_stability(aligned, x_col, y_col, max_lag=max_lag)
    segment_lags = (
        segments["best_lag_minutes"].astype(int).tolist() if not segments.empty else []
    )
    interpretation = classify(
        best_r=best_r,
        zero_r=zero_r,
        plateau_995=plateau_995,
        bootstrap_lags=bootstrap_lags,
        segment_lags=segment_lags,
        null_p=null_p,
    )

    aligned.reset_index().to_csv(outdir / "mag_paired_exact.csv", index=False)
    scan.to_csv(outdir / "lag_scan.csv", index=False)
    segments.to_csv(outdir / "segment_stability.csv", index=False)

    bootstrap_counts = {
        str(lag): int(count)
        for lag, count in sorted(Counter(bootstrap_lags).items())
    }
    manifest = {
        "pairing_version": PAIRING_VERSION,
        "status": "SUCCESS",
        "interpretation": interpretation,
        "interpretation_limits": [
            "correlation does not by itself prove propagation or physical mechanism",
            "a positive fitted lag is defined explicitly and still requires spacecraft ephemeris",
            "no forward fill or interpolation was used",
            "chi is an absolute deviation; signed delta and vector components should also be inspected",
        ],
        "policy": {
            "coordinate_frame_required": "GSE",
            "cadence": "exact one-minute canonical rows",
            "alignment": "inner join on exact UTC timestamp",
            "imputation": "none",
            "lag_definition": POSITIVE_LAG_DEFINITION,
            "lag_search_range_minutes": [-max_lag, max_lag],
            "look_elsewhere_control": "circular-shift null scans the same lag range",
            "block_bootstrap_minutes": block_minutes,
            "classification_thresholds": classification_policy(),
        },
        "inputs": {
            "dscovr": {
                "path": str(dscovr_csv),
                "rows_admitted": int(len(dscovr)),
                "run_manifest": dscovr_provenance,
            },
            "solar1": {
                "path": str(solar1_csv),
                "rows_admitted": int(len(solar1)),
                "run_manifest": solar1_provenance,
            },
        },
        "overlap": {
            "exact_rows": int(len(aligned)),
            "start_utc": aligned.index.min().isoformat(),
            "stop_utc": aligned.index.max().isoformat(),
        },
        "metrics": {
            "zero_lag_pearson_r": zero_r,
            "best_fit_lag_minutes": best_lag,
            "best_fit_pearson_r": best_r,
            "improvement_over_zero_lag": best_r - zero_r,
            "peak_plateau_99_percent_lags": plateau_99,
            "peak_plateau_99_5_percent_lags": plateau_995,
            "bootstrap_best_lag_counts": bootstrap_counts,
            "bootstrap_lag_95_percent_interval": (
                [
                    float(np.percentile(bootstrap_lags, 2.5)),
                    float(np.percentile(bootstrap_lags, 97.5)),
                ]
                if bootstrap_lags
                else None
            ),
            "circular_shift_null_iterations": len(null_maxima),
            "look_elsewhere_p_value": null_p,
            "null_maximum_99th_percentile": (
                float(np.percentile(null_maxima, 99)) if null_maxima else None
            ),
        },
    }
    (outdir / "pairing_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="NVCPP exact MAG coherence analysis")
    parser.add_argument("--dscovr", type=Path, required=True)
    parser.add_argument("--solar1", type=Path, required=True)
    parser.add_argument("--dscovr-manifest", type=Path, required=True)
    parser.add_argument("--solar1-manifest", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("runs/pairing"))
    parser.add_argument("--max-lag", type=int, default=60)
    parser.add_argument("--block-minutes", type=int, default=240)
    parser.add_argument("--bootstrap-iterations", type=int, default=300)
    parser.add_argument("--null-iterations", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    manifest = run_pairing_engine(
        args.dscovr,
        args.solar1,
        args.dscovr_manifest,
        args.solar1_manifest,
        args.outdir,
        max_lag=args.max_lag,
        block_minutes=args.block_minutes,
        bootstrap_iterations=args.bootstrap_iterations,
        null_iterations=args.null_iterations,
        seed=args.seed,
    )
    print(json.dumps({"interpretation": manifest["interpretation"], "metrics": manifest["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
