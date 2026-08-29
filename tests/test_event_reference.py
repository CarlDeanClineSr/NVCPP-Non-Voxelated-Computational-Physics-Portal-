import numpy as np
import pandas as pd

from core.event_reference import (
    EventReferenceColumns,
    EventReferencePolicy,
    attach_event_reference,
    local_integrity_gate,
    select_later_structure_candidate,
)


def reference_frame() -> pd.DataFrame:
    time = pd.date_range("2026-01-01", periods=12, freq="1min", tz="UTC")
    bx = np.array([10, 10, 10, 10, 11, 25, 28, 30, 20, 0, 30, 30], dtype=float)
    by = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 32, 0, 0], dtype=float)
    bz = np.zeros(12, dtype=float)
    magnitude = np.sqrt(bx**2 + by**2 + bz**2)
    baseline = np.array([10] * 8 + [30] * 4, dtype=float)
    delta = (magnitude - baseline) / baseline
    chi = np.abs(delta)
    return pd.DataFrame(
        {
            "time": time,
            "bx": bx,
            "by": by,
            "bz": bz,
            "B_mag": magnitude,
            "B0": baseline,
            "delta_B24M": delta,
            "chi_B24M": chi,
            "baseline_status": "VALID",
            "native_sample_count": 60,
        }
    )


def columns() -> EventReferenceColumns:
    return EventReferenceColumns(time="time", bx="bx", by="by", bz="bz")


def test_gate_and_reference_are_derived_not_hand_picked():
    annotated, metadata = attach_event_reference(reference_frame(), columns=columns())
    assert metadata["gate"]["derived_time_utc"] == "2026-01-01T00:05:00+00:00"
    assert metadata["reference"]["time_utc"] == "2026-01-01T00:04:00+00:00"
    # The frozen value is the live B0 at the reference row, not its B magnitude (11 nT).
    assert metadata["reference"]["B_nT"] == 10.0
    assert annotated.loc[9, "chi_event_ref_absB"] == 2.2
    assert annotated.loc[9, "baseline_regime"] == "EVENT_ABSORBED_BY_LIVE_BASELINE"
    assert annotated.loc[9, "chi_B24M"] < 0.15


def test_later_structure_is_selected_by_explicit_vector_and_jump_rule():
    policy = EventReferencePolicy(local_integrity_half_window_minutes=1)
    annotated, _ = attach_event_reference(
        reference_frame(), columns=columns(), policy=policy
    )
    selected, evidence = select_later_structure_candidate(
        annotated, columns=columns(), policy=policy
    )
    assert selected["time"] == pd.Timestamp("2026-01-01T00:09:00Z")
    assert selected["chi_B24M"] < policy.research_watch_chi
    assert selected["chi_event_ref_absB"] >= policy.frozen_severe_chi
    assert selected["rotation_from_previous_minute_degrees"] == 90.0
    assert selected["minute_relative_magnitude_change"] == 0.6
    assert evidence["selected_time_utc"] == "2026-01-01T00:09:00+00:00"


def test_local_integrity_gate_requires_exact_rows_and_native_coverage():
    policy = EventReferencePolicy(local_integrity_half_window_minutes=1)
    data = reference_frame()
    result = local_integrity_gate(
        data,
        timestamp="2026-01-01T00:09:00Z",
        columns=columns(),
        policy=policy,
    )
    assert result["status"] == "PASS"
    broken = data.drop(index=8).reset_index(drop=True)
    result = local_integrity_gate(
        broken,
        timestamp="2026-01-01T00:09:00Z",
        columns=columns(),
        policy=policy,
    )
    assert result["status"] == "FAIL"
