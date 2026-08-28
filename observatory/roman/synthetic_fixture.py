"""Deterministic Roman-like image fixture for pipeline readiness tests.

This fixture is intentionally *not* presented as flight data, Roman I-Sim output,
or an official Roman datamodel. It gives NVCPP a small, reproducible image-domain
test surface while authenticated Nexus simulations and future public Roman
products remain external inputs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


FIXTURE_CLASS = "NVCPP_DETERMINISTIC_ROMAN_LIKE_FIXTURE"
DQ_COSMIC_RAY = np.uint32(1)


@dataclass(frozen=True)
class FixtureProducts:
    data_path: Path
    truth_path: Path
    metrics_path: Path
    chart_path: Path
    metrics: dict[str, Any]


def _array_sha256(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        contiguous = np.ascontiguousarray(array)
        digest.update(str(contiguous.dtype).encode("ascii"))
        digest.update(str(contiguous.shape).encode("ascii"))
        digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _add_gaussian(
    image: np.ndarray,
    *,
    x: float,
    y: float,
    flux: float,
    sigma: float,
) -> None:
    height, width = image.shape
    radius = max(3, int(np.ceil(5 * sigma)))
    x0 = max(0, int(np.floor(x)) - radius)
    x1 = min(width, int(np.floor(x)) + radius + 1)
    y0 = max(0, int(np.floor(y)) - radius)
    y1 = min(height, int(np.floor(y)) + radius + 1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    kernel = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))
    kernel_sum = float(kernel.sum())
    if kernel_sum > 0:
        image[y0:y1, x0:x1] += flux * kernel / kernel_sum


def _connected_components(mask: np.ndarray) -> list[int]:
    """Return component sizes using an 8-neighbor flood fill."""

    if mask.ndim != 2:
        raise ValueError("mask must be two-dimensional")
    visited = np.zeros(mask.shape, dtype=bool)
    sizes: list[int] = []
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(y, x)])
            visited[y, x] = True
            size = 0
            while queue:
                cy, cx = queue.popleft()
                size += 1
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < height
                            and 0 <= nx < width
                            and mask[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            queue.append((ny, nx))
            sizes.append(size)
    return sizes


def analyze_arrays(
    science: np.ndarray,
    error: np.ndarray,
    dq: np.ndarray,
    *,
    detection_sigma: float = 5.0,
    saturation_level: float = 65_000.0,
) -> dict[str, Any]:
    if science.ndim != 2:
        raise ValueError("science array must be two-dimensional")
    if science.shape != error.shape or science.shape != dq.shape:
        raise ValueError("SCI, ERR, and DQ arrays must have identical shapes")
    if not np.isfinite(science).all() or not np.isfinite(error).all():
        raise ValueError("SCI and ERR arrays must be finite")
    if (error <= 0).any():
        raise ValueError("ERR values must be positive")

    good = dq == 0
    if not good.any():
        raise ValueError("fixture contains no unflagged pixels")
    good_values = science[good]
    background = float(np.median(good_values))
    mad = float(np.median(np.abs(good_values - background)))
    robust_sigma = 1.4826 * mad
    if not np.isfinite(robust_sigma) or robust_sigma <= 0:
        robust_sigma = float(np.std(good_values))
    if not np.isfinite(robust_sigma) or robust_sigma <= 0:
        robust_sigma = float(np.median(error[good]))
    if not np.isfinite(robust_sigma) or robust_sigma <= 0:
        raise ValueError("unable to estimate a positive background sigma")

    detection_mask = good & (science > background + detection_sigma * robust_sigma)
    components = _connected_components(detection_mask)
    significant_components = [size for size in components if size >= 2]

    return {
        "fixture_class": FIXTURE_CLASS,
        "official_roman_data": False,
        "science_claims_enabled": False,
        "shape": [int(science.shape[0]), int(science.shape[1])],
        "background_median": background,
        "background_robust_sigma": robust_sigma,
        "detection_sigma": float(detection_sigma),
        "detected_components": len(significant_components),
        "detected_component_sizes": significant_components,
        "cosmic_ray_flagged_pixels": int(np.count_nonzero(dq & DQ_COSMIC_RAY)),
        "saturation_level": float(saturation_level),
        "saturated_pixels": int(np.count_nonzero(science >= saturation_level)),
        "saturated_fraction": float(np.mean(science >= saturation_level)),
        "minimum_value": float(np.min(science)),
        "maximum_value": float(np.max(science)),
        "array_sha256": _array_sha256(science, error, dq),
        "clipping_applied": False,
    }


def generate_fixture(
    *,
    config: dict[str, Any],
    outdir: Path,
) -> FixtureProducts:
    outdir.mkdir(parents=True, exist_ok=True)
    shape = tuple(int(item) for item in config["shape"])
    seed = int(config["seed"])
    source_count = int(config["source_count"])
    background = float(config.get("background_electrons", 1000.0))
    read_noise = float(config.get("read_noise_electrons", 6.0))
    source_flux_min = float(config.get("source_flux_min", 600.0))
    source_flux_max = float(config.get("source_flux_max", 8000.0))
    cosmic_ray_count = int(config.get("cosmic_ray_count", 8))
    cosmic_ray_signal = float(config.get("cosmic_ray_signal", 25_000.0))
    sigma = float(config.get("psf_sigma_pixels", 1.35))
    saturation_level = float(config.get("saturation_level", 65_000.0))
    detection_sigma = float(config.get("detection_sigma", 5.0))

    if source_flux_min <= 0 or source_flux_max <= source_flux_min:
        raise ValueError("invalid source flux interval")
    if read_noise <= 0 or sigma <= 0:
        raise ValueError("read noise and PSF sigma must be positive")

    rng = np.random.default_rng(seed)
    science = rng.normal(background, read_noise, size=shape).astype(np.float64)
    truth_sources: list[dict[str, float]] = []
    margin = max(6.0, 5.0 * sigma)
    height, width = shape
    for source_id in range(source_count):
        x = float(rng.uniform(margin, width - margin))
        y = float(rng.uniform(margin, height - margin))
        flux = float(rng.uniform(source_flux_min, source_flux_max))
        _add_gaussian(science, x=x, y=y, flux=flux, sigma=sigma)
        truth_sources.append(
            {
                "source_id": source_id,
                "x_pixel": x,
                "y_pixel": y,
                "integrated_flux_electrons": flux,
                "psf_sigma_pixels": sigma,
            }
        )

    dq = np.zeros(shape, dtype=np.uint32)
    cosmic_rays: list[dict[str, int]] = []
    for _ in range(cosmic_ray_count):
        y = int(rng.integers(0, height))
        x = int(rng.integers(0, width))
        science[y, x] += cosmic_ray_signal
        dq[y, x] |= DQ_COSMIC_RAY
        cosmic_rays.append({"x_pixel": x, "y_pixel": y})

    signal = np.maximum(science - background, 0.0)
    error = np.sqrt(read_noise**2 + signal).astype(np.float64)

    data_path = outdir / "roman_synthetic_l2_like_fixture.npz"
    np.savez_compressed(
        data_path,
        SCI=science,
        ERR=error,
        DQ=dq,
    )

    metrics = analyze_arrays(
        science,
        error,
        dq,
        detection_sigma=detection_sigma,
        saturation_level=saturation_level,
    )
    metrics["data_file"] = data_path.name
    metrics["data_file_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()

    truth = {
        "fixture_version": "1.0.0",
        "fixture_class": FIXTURE_CLASS,
        "official_roman_data": False,
        "roman_isim_output": False,
        "mast_product": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "instrument_context": {
            "mission": "ROMAN",
            "instrument": "WFI",
            "data_level": "L2-LIKE",
            "detector": "SCA01_SIM",
            "optical_element": "F158_SIM",
            "array_roles": ["SCI", "ERR", "DQ"],
        },
        "sources": truth_sources,
        "cosmic_rays": cosmic_rays,
        "clipping_applied": False,
        "use_limit": (
            "Pipeline readiness only. This file is not an official Roman datamodel, "
            "flight product, MAST observation, or Roman I-Sim output."
        ),
    }
    truth_path = outdir / "roman_synthetic_truth.json"
    truth_path.write_text(
        json.dumps(truth, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    metrics_path = outdir / "roman_synthetic_metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    chart_path = outdir / "roman_synthetic_preview.png"
    fig, ax = plt.subplots(figsize=(8, 6))
    display = ax.imshow(science, origin="lower")
    ax.set_title("NVCPP deterministic Roman-like WFI fixture")
    ax.set_xlabel("X pixel")
    ax.set_ylabel("Y pixel")
    fig.colorbar(display, ax=ax, label="simulated electrons")
    fig.tight_layout()
    fig.savefig(chart_path, dpi=150)
    plt.close(fig)

    return FixtureProducts(
        data_path=data_path,
        truth_path=truth_path,
        metrics_path=metrics_path,
        chart_path=chart_path,
        metrics=metrics,
    )


def load_fixture(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as payload:
        required = {"SCI", "ERR", "DQ"}
        missing = required - set(payload.files)
        if missing:
            raise ValueError(f"fixture is missing arrays: {sorted(missing)}")
        return (
            np.array(payload["SCI"], copy=True),
            np.array(payload["ERR"], copy=True),
            np.array(payload["DQ"], copy=True),
        )
