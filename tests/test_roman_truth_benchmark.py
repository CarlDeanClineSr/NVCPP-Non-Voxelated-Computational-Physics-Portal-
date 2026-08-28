import json

import numpy as np

from observatory.roman.synthetic_fixture import generate_fixture
from observatory.roman.truth_benchmark import (
    BENCHMARK_CLASS,
    detection_catalog,
    match_detections,
    run_truth_benchmark,
)


def test_matching_is_one_to_one_and_uses_nearest_distance():
    truth = [
        {
            "source_id": 0,
            "x_pixel": 5.0,
            "y_pixel": 5.0,
            "integrated_flux_electrons": 1000.0,
        },
        {
            "source_id": 1,
            "x_pixel": 15.0,
            "y_pixel": 15.0,
            "integrated_flux_electrons": 2000.0,
        },
    ]
    detections = [
        {
            "detection_id": 0,
            "centroid_x_pixel": 5.2,
            "centroid_y_pixel": 4.9,
            "signal_sum_electrons": 900.0,
        },
        {
            "detection_id": 1,
            "centroid_x_pixel": 14.7,
            "centroid_y_pixel": 15.1,
            "signal_sum_electrons": 1900.0,
        },
    ]

    matches, unmatched_truth, unmatched_detections = match_detections(
        truth, detections, match_radius_pixels=2.0
    )

    assert [item["source_id"] for item in matches] == [0, 1]
    assert unmatched_truth == []
    assert unmatched_detections == []
    assert max(item["distance_pixels"] for item in matches) < 0.5


def test_flagged_cosmic_ray_is_not_a_detection():
    science = np.full((16, 16), 1000.0)
    dq = np.zeros((16, 16), dtype=np.uint32)
    science[4, 4] = 50_000.0
    dq[4, 4] = 1
    science[10:12, 10:12] = 1200.0

    catalog, _background, _sigma = detection_catalog(
        science,
        dq,
        detection_sigma=5.0,
        minimum_pixels=2,
    )

    assert len(catalog) == 1
    assert catalog[0]["centroid_x_pixel"] > 9
    assert catalog[0]["centroid_y_pixel"] > 9


def test_fixture_benchmark_is_deterministic_and_bounded(tmp_path):
    config = {
        "shape": [64, 64],
        "seed": 20260830,
        "source_count": 8,
        "background_electrons": 1000.0,
        "read_noise_electrons": 5.0,
        "source_flux_min": 1000.0,
        "source_flux_max": 8000.0,
        "cosmic_ray_count": 4,
        "cosmic_ray_signal": 20_000.0,
        "psf_sigma_pixels": 1.2,
        "saturation_level": 65_000.0,
        "detection_sigma": 5.0,
    }
    fixture = generate_fixture(config=config, outdir=tmp_path / "fixture")

    first = run_truth_benchmark(
        data_path=fixture.data_path,
        truth_path=fixture.truth_path,
        outdir=tmp_path / "first",
        detection_sigma=5.0,
        match_radius_pixels=4.0,
    )
    second = run_truth_benchmark(
        data_path=fixture.data_path,
        truth_path=fixture.truth_path,
        outdir=tmp_path / "second",
        detection_sigma=5.0,
        match_radius_pixels=4.0,
    )

    assert first.benchmark == second.benchmark
    assert first.benchmark["benchmark_class"] == BENCHMARK_CLASS
    assert first.benchmark["official_roman_data"] is False
    assert 0.0 <= first.benchmark["completeness"] <= 1.0
    assert 0.0 <= first.benchmark["purity"] <= 1.0
    assert first.benchmark["matched_source_count"] <= config["source_count"]
    assert first.benchmark["cosmic_ray_detection_leakage_count"] == 0
    assert first.chart_path.exists()
    assert first.detections_path.exists()
    assert first.matches_path.exists()

    stored = json.loads(first.benchmark_path.read_text())
    assert stored["science_claims_enabled"] is False
