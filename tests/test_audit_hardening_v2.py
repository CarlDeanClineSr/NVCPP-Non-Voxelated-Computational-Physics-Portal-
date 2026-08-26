import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.cline_l1_chain_v1 import run_chain
from core.temporal_pairing import (
    align_exact,
    best_from_scan,
    classify,
    lag_scan,
    load_canonical_table,
)
from sources.solar1.download_solar1 import schema_fingerprint
from sources.solar1.swips_archive_discovery_v2 import BUCKET_URL, parse_list_objects


def minute_frame(periods=3000, value=10.0):
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=periods, freq="1min", tz="UTC"),
            "B": np.full(periods, value, dtype=float),
        }
    )


def test_prior_only_full_window_and_unclipped_metrics():
    frame = minute_frame()
    frame.loc[1440, "B"] = 30.0
    out = run_chain(frame, "time", "B", expected_cadence_seconds=60)
    assert out.loc[1439, "baseline_status"] == "WARMUP"
    assert out.loc[1440, "baseline_status"] == "VALID"
    assert out.loc[1440, "B0"] == pytest.approx(10.0)
    assert out.loc[1440, "ratio_B24M"] == pytest.approx(3.0)
    assert out.loc[1440, "delta_B24M"] == pytest.approx(2.0)
    assert out.loc[1440, "chi_B24M"] == pytest.approx(2.0)


def test_duplicate_timestamps_fail_closed():
    frame = minute_frame(20)
    frame.loc[10, "time"] = frame.loc[9, "time"]
    with pytest.raises(ValueError, match="duplicate"):
        run_chain(frame, "time", "B", expected_cadence_seconds=60)


def test_insufficient_coverage_is_named_not_filled():
    frame = minute_frame(3000)
    frame = frame.drop(index=range(100, 400)).reset_index(drop=True)
    out = run_chain(frame, "time", "B", expected_cadence_seconds=60)
    target = out.loc[
        out["time"] >= pd.Timestamp("2026-01-02 00:00:00", tz="UTC")
    ].iloc[0]
    assert target["baseline_status"] == "INSUFFICIENT_COVERAGE"
    assert pd.isna(target["B0"])
    assert pd.isna(target["chi_B24M"])


def test_nonpositive_baseline_is_invalid_without_denominator_floor():
    frame = minute_frame(value=0.0)
    frame.loc[1440, "B"] = 5.0
    out = run_chain(frame, "time", "B", expected_cadence_seconds=60)
    row = out.loc[1440]
    assert row["baseline_status"] == "BASELINE_NONPOSITIVE"
    assert pd.isna(row["ratio_B24M"])
    assert pd.isna(row["chi_B24M"])


def canonical_csv(path: Path, times, values):
    frame = pd.DataFrame(
        {
            "time": times,
            "B_mag": 5.0 + values,
            "delta_B24M": values,
            "chi_B24M": np.abs(values),
            "baseline_status": "VALID",
        }
    )
    frame.to_csv(path, index=False)


def test_positive_lag_definition_recovers_later_solar_feature():
    rng = np.random.default_rng(4)
    x = rng.normal(size=1000)
    y = np.full_like(x, np.nan)
    y[3:] = x[:-3]
    scan = lag_scan(x, y, max_lag=10)
    lag, correlation = best_from_scan(scan)
    assert lag == 3
    assert correlation == pytest.approx(1.0)


def test_exact_alignment_never_forward_fills(tmp_path):
    times = pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC")
    d_path = tmp_path / "d.csv"
    s_path = tmp_path / "s.csv"
    canonical_csv(d_path, times, np.linspace(-1, 1, 10))
    canonical_csv(s_path, times.delete(5), np.linspace(-1, 1, 9))
    d = load_canonical_table(d_path, "DSCOVR")
    s = load_canonical_table(s_path, "SOLAR-1")
    aligned = align_exact(d, s)
    assert len(aligned) == 9
    assert times[5] not in aligned.index


def test_smooth_broad_peak_is_not_called_resolved_lag():
    result = classify(
        best_r=0.906,
        zero_r=0.901,
        plateau_995=[1, 2, 3],
        bootstrap_lags=[0] * 50 + [1] * 40 + [2] * 10,
        segment_lags=[3, 3, 0],
        null_p=0.003,
    )
    assert result == "COHERENT_BUT_LAG_UNRESOLVED"


def test_documented_bucket_path_is_used():
    assert BUCKET_URL.endswith("/satellite-spaceweather")


def test_namespaced_s3_xml_is_parsed():
    xml = b"""<?xml version='1.0' encoding='UTF-8'?>
<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>abc</NextContinuationToken>
  <CommonPrefixes><Prefix>SWFO/SOLAR-1/SWIPS/</Prefix></CommonPrefixes>
  <Contents>
    <Key>SWFO/SOLAR-1/SWIPS/swips-l3/example.nc</Key>
    <LastModified>2026-08-01T00:00:00.000Z</LastModified>
    <ETag>\"etag\"</ETag><Size>123</Size>
  </Contents>
</ListBucketResult>"""
    parsed = parse_list_objects(xml)
    assert parsed["is_truncated"] is True
    assert parsed["next_continuation_token"] == "abc"
    assert parsed["common_prefixes"] == ["SWFO/SOLAR-1/SWIPS/"]
    assert parsed["objects"][0]["size"] == 123


def test_hapi_schema_fingerprint_matches_authoritative_contract():
    contract = json.loads(Path("config/solar1_mag_contract.v1.json").read_text())
    info = {
        "HAPI": "3.2.0",
        "status": {"code": 1200, "message": "OK"},
        "parameters": [
            {"name": "time", "type": "isotime", "units": "UTC", "length": 24, "fill": None},
            {"name": "b_gse_min_x", "type": "double", "units": "nT", "fill": "-9999.0"},
            {"name": "b_gse_min_y", "type": "double", "units": "nT", "fill": "-9999.0"},
            {"name": "b_gse_min_z", "type": "double", "units": "nT", "fill": "-9999.0"},
        ],
        "additionalMetadata": [
            {
                "content": {
                    "b_gse_min_x": {
                        "product": "sci_mag-l3_solar1",
                        "instrument": "MAG",
                        "satellite": "SOLAR-1",
                    }
                }
            }
        ],
    }
    observed, _ = schema_fingerprint(info, contract)
    assert observed == contract["source"]["schema_fingerprint_sha256"]
