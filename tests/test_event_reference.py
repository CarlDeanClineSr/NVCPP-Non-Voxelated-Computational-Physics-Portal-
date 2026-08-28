import numpy as np
import pandas as pd
import pytest

from core.event_reference import (
    EventReferenceConfig,
    EventReferenceError,
    add_frozen_event_reference,
    event_local_integrity,
)


def canonical_frame(periods: int = 30) -> pd.DataFrame:
    time = pd.date_range("2024-05-10T16:20:00Z", periods=periods, freq="1min")
    frame = pd.DataFrame(
        {
            "EPOCH": time,
            "BX_(GSE)": -5.0,
            "BY_(GSE)": 0.0,
            "BZ_(GSE)": 5.0,
            "B_mag": 10.0,
            "B0": 10.0,
            "delta_B24M": 0.0,
            "chi_B24M": 0.0,
            "baseline_status": "VALID",
            "native_coverage_fraction": 1.0,
        }
    )
    return frame


def test_frozen_reference_is_secondary_and_does_not_replace_canonical_metrics():
    frame = canonical_frame()
    reference_time = frame.loc[14, "EPOCH"]
    frame.loc[20, "B_mag"] = 30.0
    frame.loc[20, "B0"] = 25.0
    frame.loc[20, "delta_B24M"] = 0.2
    frame.loc[20, "chi_B24M"] = 0.2

    result, metadata = add_frozen_event_reference(
        frame,
        reference_time=reference_time,
        time_col="EPOCH",
        b_mag_col="B_mag",
        coordinate_frame="GSE",
        by_col="BY_(GSE)",
        bz_col="BZ_(GSE)",
    )

    row = result.loc[20]
    assert row["B0"] == pytest.approx(25.0)
    assert row["chi_B24M"] == pytest.approx(0.2)
    assert row["event_reference_B_nT"] == pytest.approx(10.0)
    assert row["delta_event_reference"] == pytest.approx(2.0)
    assert row["chi_event_reference"] == pytest.approx(2.0)
    assert row["clock_angle_gse_yz_deg"] == pytest.approx(0.0)
    assert metadata["canonical_metrics_replaced"] is False


def test_reference_must_be_exact_baseline_valid_and_positive():
    frame = canonical_frame()
    frame.loc[10, "baseline_status"] = "INSUFFICIENT_COVERAGE"
    with pytest.raises(EventReferenceError, match="VALID"):
        add_frozen_event_reference(
            frame,
            reference_time=frame.loc[10, "EPOCH"],
            time_col="EPOCH",
            b_mag_col="B_mag",
        )

    with pytest.raises(EventReferenceError, match="exactly one row"):
        add_frozen_event_reference(
            frame,
            reference_time="2024-05-10T00:00:00Z",
            time_col="EPOCH",
            b_mag_col="B_mag",
        )


def test_local_integrity_gate_passes_complete_window_and_fails_gap():
    frame = canonical_frame()
    center = frame.loc[14, "EPOCH"]
    config = EventReferenceConfig(
        expected_cadence_seconds=60.0,
        local_half_window_minutes=5,
        minimum_native_coverage_fraction=0.95,
    )
    passed = event_local_integrity(
        frame,
        center_time=center,
        time_col="EPOCH",
        config=config,
        native_coverage_col="native_coverage_fraction",
    )
    assert passed["expected_rows"] == 11
    assert passed["present_rows"] == 11
    assert passed["event_local_integrity_pass"] is True

    missing = frame.drop(index=14).reset_index(drop=True)
    failed = event_local_integrity(
        missing,
        center_time=center,
        time_col="EPOCH",
        config=config,
        native_coverage_col="native_coverage_fraction",
    )
    assert failed["present_rows"] == 10
    assert failed["all_rows_present"] is False
    assert failed["event_local_integrity_pass"] is False


def test_clock_angle_requires_explicit_frame_and_both_components():
    frame = canonical_frame()
    with pytest.raises(EventReferenceError, match="supplied together"):
        add_frozen_event_reference(
            frame,
            reference_time=frame.loc[10, "EPOCH"],
            time_col="EPOCH",
            b_mag_col="B_mag",
            coordinate_frame="GSE",
            by_col="BY_(GSE)",
        )


def test_nonfinite_event_metrics_are_rejected():
    frame = canonical_frame()
    frame.loc[20, "B_mag"] = np.nan
    with pytest.raises(EventReferenceError, match="non-finite"):
        add_frozen_event_reference(
            frame,
            reference_time=frame.loc[10, "EPOCH"],
            time_col="EPOCH",
            b_mag_col="B_mag",
        )
