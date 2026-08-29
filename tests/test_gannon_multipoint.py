import numpy as np
import pandas as pd
import pytest

from historical.gannon_multipoint_audit import (
    add_plasma_physics,
    canonicalize_plasma_minutes,
    canonicalize_vector_minutes,
    classify_multipoint,
    normalize_temperature_unit,
    select_structure,
)


def test_vector_components_are_averaged_before_magnitude():
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2024-05-11T10:58:01Z",
                    "2024-05-11T10:58:31Z",
                    "2024-05-11T10:59:01Z",
                    "2024-05-11T10:59:31Z",
                ],
                utc=True,
            ),
            "bx": [1.0, 0.0, 0.5, 0.5],
            "by": [0.0, 1.0, 0.5, 0.5],
            "bz": [0.0, 0.0, 0.0, 0.0],
        }
    )
    canonical, quarantine = canonicalize_vector_minutes(
        frame,
        components=("bx", "by", "bz"),
        minimum_samples=2,
        source="TEST",
    )
    assert quarantine.empty
    assert canonical.loc[0, "bx_gse_nT"] == pytest.approx(0.5)
    assert canonical.loc[0, "by_gse_nT"] == pytest.approx(0.5)
    assert canonical.loc[0, "B_mag_nT"] == pytest.approx(np.sqrt(0.5))
    assert canonical.loc[0, "B_mag_nT"] != pytest.approx(1.0)


def test_plasma_quality_and_minute_coverage_are_fail_closed():
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2024-05-11T10:58:01Z",
                    "2024-05-11T10:58:21Z",
                    "2024-05-11T10:58:41Z",
                    "2024-05-11T10:59:01Z",
                ],
                utc=True,
            ),
            "density": [5.0, 6.0, 7.0, 8.0],
            "speed": [500.0, 510.0, 520.0, 530.0],
            "temperature": [100000.0, 110000.0, 120000.0, 130000.0],
        }
    )
    quality = pd.Series([True, False, True, True], index=frame.index)
    canonical, quarantine = canonicalize_plasma_minutes(
        frame,
        density_col="density",
        speed_col="speed",
        velocity_components=None,
        temperature_col="temperature",
        temperature_unit="Kelvin",
        minimum_samples=2,
        source="TEST_PLASMA",
        quality_mask=quality,
    )
    assert len(canonical) == 1
    assert canonical.loc[0, "native_samples"] == 2
    assert set(quarantine["reason_code"]) == {
        "SOURCE_QUALITY_REJECTED",
        "INSUFFICIENT_PLASMA_MINUTE_COVERAGE",
    }


def test_plasma_physics_preserves_temperature_semantics():
    time = pd.to_datetime(["2024-05-11T10:59:00Z"], utc=True)
    magnetic = pd.DataFrame({"time": time, "B_mag_nT": [10.0]})
    plasma_k = pd.DataFrame(
        {
            "time": time,
            "density_cm3": [5.0],
            "speed_km_s": [500.0],
            "temperature_native": [100000.0],
            "temperature_unit": ["Kelvin"],
        }
    )
    result_k = add_plasma_physics(
        plasma_k,
        magnetic,
        beta_label="proton_beta_radial_temperature_proxy",
    )
    assert result_k.loc[0, "temperature_unit"] == "K"
    assert np.isfinite(result_k.loc[0, "dynamic_pressure_nPa"])
    assert np.isfinite(
        result_k.loc[0, "proton_beta_radial_temperature_proxy"]
    )

    plasma_ev = plasma_k.copy()
    plasma_ev["temperature_native"] = 10.0
    plasma_ev["temperature_unit"] = "eV"
    result_ev = add_plasma_physics(
        plasma_ev,
        magnetic,
        beta_label="proton_beta_3dp_temperature",
    )
    assert result_ev.loc[0, "temperature_unit"] == "eV"
    assert np.isfinite(result_ev.loc[0, "proton_beta_3dp_temperature"])
    assert normalize_temperature_unit("Kelvin") == "K"


def test_structure_selection_is_deterministic_and_retains_previous_vector():
    center = pd.Timestamp("2024-05-11T10:59:00Z")
    frame = pd.DataFrame(
        {
            "time": pd.date_range(
                "2024-05-11T10:56:00Z", periods=6, freq="1min"
            ),
            "bx_gse_nT": [1.0] * 6,
            "by_gse_nT": [0.0] * 6,
            "bz_gse_nT": [1.0, 1.0, -1.0, -1.0, 1.0, 1.0],
            "B_mag_nT": [2.0] * 6,
            "rotation_from_previous_minute_degrees": [
                np.nan,
                20.0,
                100.0,
                50.0,
                80.0,
                10.0,
            ],
            "minute_relative_magnitude_change": [
                np.nan,
                0.1,
                0.2,
                0.3,
                0.1,
                0.1,
            ],
            "native_samples": [60] * 6,
        }
    )
    selected = select_structure(frame, center=center, half_window_minutes=5)
    assert selected is not None
    assert selected["time_utc"] == "2024-05-11T10:58:00+00:00"
    assert selected["rotation_degrees"] == pytest.approx(100.0)
    assert selected["previous"]["time_utc"] == "2024-05-11T10:57:00+00:00"


def test_multipoint_classification_requires_both_independent_offsets():
    selected = {
        "DSCOVR": {"offset_from_dscovr_minutes": 0.0},
        "ACE": {"offset_from_dscovr_minutes": -2.0},
        "WIND": {"offset_from_dscovr_minutes": 3.0},
    }
    assert classify_multipoint(selected) == (
        "MULTIPOINT_COMPLEX_VECTOR_STRUCTURE_CANDIDATE_TIMING_UNRESOLVED"
    )
    selected["WIND"] = None
    assert classify_multipoint(selected) == (
        "PARTIAL_MULTIPOINT_VECTOR_STRUCTURE_CANDIDATE_TIMING_UNRESOLVED"
    )
