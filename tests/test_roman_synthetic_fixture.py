import json

import numpy as np

from observatory.roman.synthetic_fixture import (
    FIXTURE_CLASS,
    analyze_arrays,
    generate_fixture,
    load_fixture,
)


def fixture_config():
    return {
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


def test_fixture_is_deterministic_and_evidence_preserving(tmp_path):
    first = generate_fixture(config=fixture_config(), outdir=tmp_path / "first")
    second = generate_fixture(config=fixture_config(), outdir=tmp_path / "second")

    sci1, err1, dq1 = load_fixture(first.data_path)
    sci2, err2, dq2 = load_fixture(second.data_path)

    np.testing.assert_array_equal(sci1, sci2)
    np.testing.assert_array_equal(err1, err2)
    np.testing.assert_array_equal(dq1, dq2)

    assert first.metrics["fixture_class"] == FIXTURE_CLASS
    assert first.metrics["official_roman_data"] is False
    assert first.metrics["clipping_applied"] is False
    assert first.metrics["cosmic_ray_flagged_pixels"] == 4
    assert first.metrics["detected_components"] > 0
    assert first.chart_path.exists()

    truth = json.loads(first.truth_path.read_text())
    assert truth["roman_isim_output"] is False
    assert truth["mast_product"] is False
    assert truth["instrument_context"]["instrument"] == "WFI"


def test_analysis_keeps_extreme_values_and_reports_saturation():
    science = np.full((8, 8), 1000.0)
    error = np.full((8, 8), 5.0)
    dq = np.zeros((8, 8), dtype=np.uint32)
    science[2, 2] = 100_000.0
    science[5, 5] = 1010.0

    metrics = analyze_arrays(science, error, dq, saturation_level=65_000.0)

    assert metrics["maximum_value"] == 100_000.0
    assert metrics["saturated_pixels"] == 1
    assert metrics["clipping_applied"] is False


def test_analysis_rejects_shape_mismatch():
    science = np.ones((4, 4))
    error = np.ones((4, 3))
    dq = np.zeros((4, 4), dtype=np.uint32)

    try:
        analyze_arrays(science, error, dq)
    except ValueError as exc:
        assert "identical shapes" in str(exc)
    else:
        raise AssertionError("shape mismatch was accepted")
