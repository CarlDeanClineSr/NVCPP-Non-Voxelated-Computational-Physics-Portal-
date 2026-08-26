from pathlib import Path

import numpy as np
import pandas as pd

from core.temporal_pairing import PairingPolicy, align_exact, analyze_pairing


def write_inputs(tmp_path: Path, x, y, missing_solar=()):
    times = pd.date_range("2026-01-01", periods=len(x), freq="1min", tz="UTC")
    dscovr = pd.DataFrame({
        "EPOCH": times,
        "chi_B24M": x,
        "delta_B24M": x,
        "B_mag": 5 + x,
    })
    solar1 = pd.DataFrame({
        "time": times,
        "chi_B24M": y,
        "delta_B24M": y,
        "B_mag": 5 + y,
    }).drop(index=list(missing_solar))
    d_path = tmp_path / "d.csv"
    s_path = tmp_path / "s.csv"
    dscovr.to_csv(d_path, index=False)
    solar1.to_csv(s_path, index=False)
    return d_path, s_path


def test_alignment_is_exact_and_never_forward_fills(tmp_path):
    x = np.linspace(0, 1, 300)
    d_path, s_path = write_inputs(tmp_path, x, x, missing_solar=(10, 11))
    aligned, meta = align_exact(d_path, s_path, PairingPolicy())
    assert len(aligned) == 298
    assert meta["imputed_rows"] == 0
    assert pd.Timestamp("2026-01-01 00:10:00+00:00") not in aligned.index


def test_sharp_known_delay_can_be_resolved(tmp_path):
    rng = np.random.default_rng(4)
    x = rng.normal(size=1800)
    y = np.roll(x, 3)
    y[:3] = rng.normal(size=3)
    d_path, s_path = write_inputs(tmp_path, x, y)
    aligned, _ = align_exact(d_path, s_path, PairingPolicy())
    metrics, _, _ = analyze_pairing(
        aligned,
        PairingPolicy(),
        bootstrap_iterations=40,
        null_iterations=20,
    )
    assert metrics["chi_level"]["candidate_lag_minutes"] == 3
    assert metrics["result_state"] == "HIGH_COHERENCE_LAG_RESOLVED_CANDIDATE"


def test_broad_smooth_co_variation_is_not_called_propagation(tmp_path):
    t = np.linspace(0, 8 * np.pi, 1800)
    x = np.sin(t) + 0.1 * np.sin(0.1 * t)
    y = np.roll(x, 2)
    d_path, s_path = write_inputs(tmp_path, x, y)
    aligned, _ = align_exact(d_path, s_path, PairingPolicy())
    metrics, _, _ = analyze_pairing(
        aligned,
        PairingPolicy(),
        bootstrap_iterations=30,
        null_iterations=10,
    )
    assert metrics["chi_level"]["maximum_correlation"] > 0.9
    assert metrics["result_state"] == "HIGH_COHERENCE_LAG_UNRESOLVED"
    assert "does not by itself prove" in metrics["interpretation"]
