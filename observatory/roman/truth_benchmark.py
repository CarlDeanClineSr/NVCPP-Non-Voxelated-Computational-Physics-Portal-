"""Truth-recovery scoring for Roman-like readiness fixtures.

This module never interprets a synthetic fixture as Roman flight data. It asks a
narrow engineering question: given a scene whose injected sources and cosmic-ray
locations are known, how well does the current detection path recover that truth?

The benchmark provides a stable regression surface that can later be applied to
Roman I-Sim exports after those products pass separate source and schema gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from math import hypot
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .synthetic_fixture import load_fixture


BENCHMARK_CLASS = "NVCPP_ROMAN_TRUTH_RECOVERY_BENCHMARK"


@dataclass(frozen=True)
class BenchmarkProducts:
    benchmark_path: Path
    detections_path: Path
    matches_path: Path
    chart_path: Path
    benchmark: dict[str, Any]


def _robust_background(science: np.ndarray, dq: np.ndarray) -> tuple[float, float]:
    good = dq == 0
    if not good.any():
        raise ValueError("no unflagged pixels are available for background estimation")
    values = science[good]
    background = float(np.median(values))
    mad = float(np.median(np.abs(values - background)))
    sigma = 1.4826 * mad
    if not np.isfinite(sigma) or sigma <= 0:
        sigma = float(np.std(values))
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError("unable to estimate a positive background sigma")
    return background, sigma


def detection_catalog(
    science: np.ndarray,
    dq: np.ndarray,
    *,
    detection_sigma: float,
    minimum_pixels: int = 2,
) -> tuple[list[dict[str, Any]], float, float]:
    """Return an 8-neighbor detection catalog and its background estimate."""

    if science.ndim != 2 or dq.shape != science.shape:
        raise ValueError("SCI and DQ must be matching two-dimensional arrays")
    if detection_sigma <= 0 or minimum_pixels <= 0:
        raise ValueError("detection_sigma and minimum_pixels must be positive")

    background, robust_sigma = _robust_background(science, dq)
    mask = (dq == 0) & (science > background + detection_sigma * robust_sigma)
    visited = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    detections: list[dict[str, Any]] = []

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            pixels: list[tuple[int, int]] = []
            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            stack.append((ny, nx))

            if len(pixels) < minimum_pixels:
                continue
            yy = np.array([item[0] for item in pixels], dtype=float)
            xx = np.array([item[1] for item in pixels], dtype=float)
            weights = np.array(
                [max(float(science[py, px] - background), 0.0) for py, px in pixels],
                dtype=float,
            )
            if float(weights.sum()) > 0:
                centroid_x = float(np.average(xx, weights=weights))
                centroid_y = float(np.average(yy, weights=weights))
            else:
                centroid_x = float(xx.mean())
                centroid_y = float(yy.mean())

            detections.append(
                {
                    "detection_id": len(detections),
                    "centroid_x_pixel": centroid_x,
                    "centroid_y_pixel": centroid_y,
                    "pixel_count": len(pixels),
                    "peak_electrons": float(max(science[py, px] for py, px in pixels)),
                    "signal_sum_electrons": float(weights.sum()),
                    "bbox_x_min": int(xx.min()),
                    "bbox_x_max": int(xx.max()),
                    "bbox_y_min": int(yy.min()),
                    "bbox_y_max": int(yy.max()),
                }
            )

    return detections, background, robust_sigma


def match_detections(
    truth_sources: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    *,
    match_radius_pixels: float,
) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    """Greedily produce one-to-one truth/detection matches by smallest distance."""

    if match_radius_pixels <= 0:
        raise ValueError("match_radius_pixels must be positive")
    candidates: list[tuple[float, int, int]] = []
    for truth_index, source in enumerate(truth_sources):
        tx = float(source["x_pixel"])
        ty = float(source["y_pixel"])
        for detection_index, detection in enumerate(detections):
            distance = hypot(
                tx - float(detection["centroid_x_pixel"]),
                ty - float(detection["centroid_y_pixel"]),
            )
            if distance <= match_radius_pixels:
                candidates.append((distance, truth_index, detection_index))

    candidates.sort(key=lambda item: item[0])
    used_truth: set[int] = set()
    used_detection: set[int] = set()
    matches: list[dict[str, Any]] = []
    for distance, truth_index, detection_index in candidates:
        if truth_index in used_truth or detection_index in used_detection:
            continue
        used_truth.add(truth_index)
        used_detection.add(detection_index)
        source = truth_sources[truth_index]
        detection = detections[detection_index]
        matches.append(
            {
                "source_id": int(source.get("source_id", truth_index)),
                "detection_id": int(detection["detection_id"]),
                "distance_pixels": float(distance),
                "truth_x_pixel": float(source["x_pixel"]),
                "truth_y_pixel": float(source["y_pixel"]),
                "detected_x_pixel": float(detection["centroid_x_pixel"]),
                "detected_y_pixel": float(detection["centroid_y_pixel"]),
                "truth_flux_electrons": float(source["integrated_flux_electrons"]),
                "detection_signal_sum_electrons": float(
                    detection["signal_sum_electrons"]
                ),
            }
        )

    unmatched_truth = [
        int(source.get("source_id", index))
        for index, source in enumerate(truth_sources)
        if index not in used_truth
    ]
    unmatched_detections = [
        int(detection["detection_id"])
        for index, detection in enumerate(detections)
        if index not in used_detection
    ]
    return matches, unmatched_truth, unmatched_detections


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def run_truth_benchmark(
    *,
    data_path: Path,
    truth_path: Path,
    outdir: Path,
    detection_sigma: float,
    match_radius_pixels: float = 4.0,
    minimum_pixels: int = 2,
) -> BenchmarkProducts:
    outdir.mkdir(parents=True, exist_ok=True)
    science, _error, dq = load_fixture(data_path)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    if truth.get("official_roman_data") is not False:
        raise ValueError("truth benchmark accepts only explicitly non-flight fixtures")
    truth_sources = truth.get("sources")
    if not isinstance(truth_sources, list):
        raise ValueError("truth source list is missing")

    detections, background, robust_sigma = detection_catalog(
        science,
        dq,
        detection_sigma=detection_sigma,
        minimum_pixels=minimum_pixels,
    )
    matches, unmatched_truth, unmatched_detections = match_detections(
        truth_sources,
        detections,
        match_radius_pixels=match_radius_pixels,
    )

    injected_count = len(truth_sources)
    detected_count = len(detections)
    matched_count = len(matches)
    centroid_errors = np.array(
        [float(row["distance_pixels"]) for row in matches], dtype=float
    )
    completeness = float(matched_count / injected_count) if injected_count else 0.0
    purity = float(matched_count / detected_count) if detected_count else 0.0

    cosmic_rays = truth.get("cosmic_rays", [])
    leakage = 0
    for detection in detections:
        dx = float(detection["centroid_x_pixel"])
        dy = float(detection["centroid_y_pixel"])
        if any(
            hypot(dx - float(item["x_pixel"]), dy - float(item["y_pixel"])) <= 1.5
            for item in cosmic_rays
        ):
            leakage += 1

    benchmark: dict[str, Any] = {
        "benchmark_version": "1.0.0",
        "benchmark_class": BENCHMARK_CLASS,
        "fixture_class": truth.get("fixture_class"),
        "official_roman_data": False,
        "roman_isim_output": bool(truth.get("roman_isim_output", False)),
        "science_claims_enabled": False,
        "detection_sigma": float(detection_sigma),
        "match_radius_pixels": float(match_radius_pixels),
        "minimum_component_pixels": int(minimum_pixels),
        "background_median": background,
        "background_robust_sigma": robust_sigma,
        "injected_source_count": injected_count,
        "detected_component_count": detected_count,
        "matched_source_count": matched_count,
        "unmatched_source_ids": unmatched_truth,
        "unmatched_detection_ids": unmatched_detections,
        "completeness": completeness,
        "purity": purity,
        "false_positive_count": len(unmatched_detections),
        "cosmic_ray_detection_leakage_count": leakage,
        "centroid_error_pixels": {
            "count": int(centroid_errors.size),
            "median": float(np.median(centroid_errors))
            if centroid_errors.size
            else None,
            "rms": float(np.sqrt(np.mean(centroid_errors**2)))
            if centroid_errors.size
            else None,
            "maximum": float(np.max(centroid_errors))
            if centroid_errors.size
            else None,
        },
        "interpretation": (
            "ENGINEERING_READINESS_BENCHMARK_ONLY; completeness below one may reflect "
            "threshold loss, source blending, or a deliberately simple detector and is "
            "not a statement about Roman flight performance."
        ),
    }

    benchmark_path = outdir / "roman_truth_benchmark.json"
    benchmark_path.write_text(
        json.dumps(benchmark, indent=2, sort_keys=True), encoding="utf-8"
    )
    detections_path = outdir / "roman_detection_catalog.csv"
    matches_path = outdir / "roman_truth_matches.csv"
    _write_csv(detections_path, detections)
    _write_csv(matches_path, matches)

    chart_path = outdir / "roman_truth_recovery_overlay.png"
    fig, ax = plt.subplots(figsize=(8, 6))
    display = ax.imshow(science, origin="lower")
    if truth_sources:
        ax.scatter(
            [float(item["x_pixel"]) for item in truth_sources],
            [float(item["y_pixel"]) for item in truth_sources],
            facecolors="none",
            edgecolors="white",
            marker="o",
            s=55,
            label="injected truth",
        )
    if detections:
        ax.scatter(
            [float(item["centroid_x_pixel"]) for item in detections],
            [float(item["centroid_y_pixel"]) for item in detections],
            c="red",
            marker="x",
            s=45,
            label="detected component",
        )
    ax.set_title(
        f"Roman-like truth recovery: {matched_count}/{injected_count} matched"
    )
    ax.set_xlabel("X pixel")
    ax.set_ylabel("Y pixel")
    ax.legend(loc="upper right")
    fig.colorbar(display, ax=ax, label="simulated electrons")
    fig.tight_layout()
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)

    return BenchmarkProducts(
        benchmark_path=benchmark_path,
        detections_path=detections_path,
        matches_path=matches_path,
        chart_path=chart_path,
        benchmark=benchmark,
    )
