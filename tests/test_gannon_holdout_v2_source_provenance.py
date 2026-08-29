import pandas as pd
import pytest

from historical.gannon_holdout_v2_consumer import canonical_magnitude_provenance
from historical.gannon_multipoint_audit import canonicalize_vector_minutes


def test_ace_canonical_magnitude_ignores_provider_magnitude_column():
    raw = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01T00:00:00Z", "2024-01-01T00:00:16Z"],
                utc=True,
            ),
            "Magnitude": [999.0, 999.0],
            "BGSEc_x": [3.0, 3.0],
            "BGSEc_y": [4.0, 4.0],
            "BGSEc_z": [0.0, 0.0],
            "SC_pos_GSE_x": [1.0, 1.0],
            "SC_pos_GSE_y": [2.0, 2.0],
            "SC_pos_GSE_z": [3.0, 3.0],
        }
    )
    canonical, _ = canonicalize_vector_minutes(
        raw,
        components=("BGSEc_x", "BGSEc_y", "BGSEc_z"),
        position_components=("SC_pos_GSE_x", "SC_pos_GSE_y", "SC_pos_GSE_z"),
        minimum_samples=2,
        source="AC_H0_MFI_TEST",
    )
    assert canonical.loc[0, "B_mag_nT"] == pytest.approx(5.0)
    assert canonical.loc[0, "B_mag_nT"] != pytest.approx(999.0)


def test_wind_canonical_magnitude_ignores_b3f1_column():
    raw = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2024-01-01T00:00:00Z", "2024-01-01T00:00:03Z"],
                utc=True,
            ),
            "reported_B3F1_nT": [1234.0, 1234.0],
            "B3GSE_x": [6.0, 6.0],
            "B3GSE_y": [8.0, 8.0],
            "B3GSE_z": [0.0, 0.0],
        }
    )
    canonical, _ = canonicalize_vector_minutes(
        raw,
        components=("B3GSE_x", "B3GSE_y", "B3GSE_z"),
        minimum_samples=2,
        source="WI_H0_MFI_TEST",
    )
    assert canonical.loc[0, "B_mag_nT"] == pytest.approx(10.0)
    assert canonical.loc[0, "B_mag_nT"] != pytest.approx(1234.0)


def test_manifest_provenance_names_nonhomologous_magnitude_roles():
    ace = canonical_magnitude_provenance("ACE")
    wind = canonical_magnitude_provenance("WIND")
    dscovr = canonical_magnitude_provenance("DSCOVR")

    assert ace["component_source"] == "BGSEc"
    assert ace["provider_reported_magnitude_parameter"] == "Magnitude"
    assert ace["provider_reported_magnitude_used_for_canonical_B"] is False
    assert ace["provider_reported_magnitude_role"] == "AUDIT_ONLY"

    assert wind["component_source"] == "B3GSE"
    assert wind["provider_reported_magnitude_parameter"] == "B3F1"
    assert wind["provider_reported_magnitude_used_for_canonical_B"] is False
    assert wind["provider_reported_magnitude_role"] == "AUDIT_ONLY"

    assert dscovr["component_source"] == "B1GSE"
    assert dscovr["provider_reported_magnitude_parameter"] is None
    assert dscovr["provider_reported_magnitude_used_for_canonical_B"] is False

    for record in (ace, wind, dscovr):
        assert record["coordinate_frame"] == "GSE"
        assert record["operation_order"] == [
            "average native vector components within each canonical UTC minute",
            "calculate Euclidean norm from the three component means",
        ]
        assert record["formula"] == (
            "B_mag_nT = sqrt(mean(Bx_GSE)^2 + mean(By_GSE)^2 + mean(Bz_GSE)^2)"
        )
