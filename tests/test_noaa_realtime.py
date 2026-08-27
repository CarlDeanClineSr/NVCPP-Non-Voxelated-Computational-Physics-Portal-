import numpy as np
import pandas as pd
import pytest

from sources.noaa_swpc.download_realtime import (
    _add_plasma_physics,
    _sanitize_magnetic,
    _merge_operational_history,
    _sanitize_plasma,
    _select_active_operational_rows,
    _table,
)


def test_header_mapping_is_by_name_not_position():
    payload = [
        ["bz_gsm", "time_tag", "bt", "by_gsm", "bx_gsm"],
        ["0", "2026-01-01T00:00:00Z", "5", "4", "3"],
    ]
    frame = _table(
        payload,
        required=["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
        source_name="test",
    )
    quarantine = []
    clean, frame_name = _sanitize_magnetic(frame, quarantine)
    assert frame_name == "GSM"
    assert clean.iloc[0]["B_mag"] == pytest.approx(5.0)
    assert quarantine == []


def test_zero_vector_and_bt_mismatch_are_quarantined():
    payload = [
        ["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
        ["2026-01-01T00:00:00Z", "0", "0", "0", "0"],
        ["2026-01-01T00:01:00Z", "3", "4", "0", "99"],
        ["2026-01-01T00:02:00Z", "3", "4", "0", "5"],
    ]
    frame = _table(
        payload,
        required=["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"],
        source_name="test",
    )
    quarantine = []
    clean, frame_name = _sanitize_magnetic(frame, quarantine)
    assert frame_name == "GSM"
    reasons = set(pd.concat(quarantine)["reason_code"])
    assert reasons == {"ZERO_VECTOR_SUSPECT", "BT_VECTOR_MISMATCH"}
    assert len(clean) == 1


def test_plasma_physics_is_finite_for_valid_inputs():
    frame = pd.DataFrame(
        {
            "density": [5.0],
            "speed": [400.0],
            "temperature": [100000.0],
            "B_mag": [5.0],
        }
    )
    output = _add_plasma_physics(frame)
    for column in ("dynamic_pressure_nPa", "alfven_speed_km_s", "alfven_mach", "proton_beta"):
        assert np.isfinite(output.loc[0, column])
        assert output.loc[0, column] > 0


def test_plasma_nonpositive_values_are_quarantined():
    payload = [
        ["time_tag", "density", "speed", "temperature"],
        ["2026-01-01T00:00:00Z", "5", "400", "100000"],
        ["2026-01-01T00:01:00Z", "0", "400", "100000"],
    ]
    frame = _table(
        payload,
        required=["time_tag", "density", "speed", "temperature"],
        source_name="test",
    )
    quarantine = []
    clean = _sanitize_plasma(frame, quarantine)
    assert len(clean) == 1
    assert pd.concat(quarantine).iloc[0]["reason_code"] == "NONPOSITIVE_PLASMA"


def test_current_object_schema_and_proton_names_are_normalized():
    magnetic = [
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "SOLAR-1",
            "active": True,
            "bx_gsm": 3.0,
            "by_gsm": 4.0,
            "bz_gsm": 0.0,
            "bt": 5.0,
        }
    ]
    plasma = [
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "SOLAR-1",
            "active": True,
            "proton_density": 5.0,
            "proton_speed": 400.0,
            "proton_temperature": 100000.0,
        }
    ]
    mag_frame = _table(magnetic, required=["time_tag"], source_name="mag")
    wind_frame = _table(plasma, required=["time_tag"], source_name="wind")
    quarantine = []
    clean_mag, frame_name = _sanitize_magnetic(mag_frame, quarantine)
    clean_wind = _sanitize_plasma(wind_frame, quarantine)
    assert frame_name == "GSM"
    assert clean_mag.iloc[0]["B_mag"] == pytest.approx(5.0)
    assert clean_wind.iloc[0]["density"] == pytest.approx(5.0)
    assert clean_wind.iloc[0]["speed"] == pytest.approx(400.0)
    assert clean_wind.iloc[0]["temperature"] == pytest.approx(100000.0)
    assert quarantine == []

class FakeResponse:
    def __init__(self, payload, url):
        import json

        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, magnetic, plasma):
        self.magnetic = magnetic
        self.plasma = plasma
        self.headers = {}

    def get(self, url, timeout=None):
        return FakeResponse(self.magnetic if "mag" in url else self.plasma, url)


def test_end_to_end_operational_pipeline_preserves_unclipped_state(tmp_path):
    from sources.noaa_swpc.download_realtime import run_noaa_realtime_pipeline

    times = pd.date_range("2026-01-01T00:00:00Z", periods=1800, freq="1min")
    mag = [["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "bt"]]
    plasma = [["time_tag", "density", "speed", "temperature"]]
    for index, time in enumerate(times):
        bx = 10.0 if index >= 1740 else 5.0
        mag.append([time.isoformat().replace("+00:00", "Z"), str(bx), "0", "0", str(bx)])
        plasma.append([time.isoformat().replace("+00:00", "Z"), "5", "400", "100000"])

    manifest = run_noaa_realtime_pipeline(
        run_name="fixture",
        retrieval_start="2026-01-01T00:00:00Z",
        analysis_start="2026-01-02T00:00:00Z",
        analysis_end="2026-01-02T06:00:00Z",
        outdir=tmp_path,
        session=FakeSession(mag, plasma),
    )
    assert manifest["status"] == "SUCCESS"
    assert manifest["analysis"]["rows"] == 360
    assert manifest["analysis"]["max_chi_b24m"] == pytest.approx(1.0)
    canonical = pd.read_csv(tmp_path / "fixture" / "noaa_realtime_canonical.csv")
    assert canonical["chi_B24M"].max() == pytest.approx(1.0)
    assert canonical["proton_beta"].notna().all()


def test_stale_provider_window_is_preserved_and_labeled(tmp_path):
    from sources.noaa_swpc.download_realtime import run_noaa_realtime_pipeline

    times = pd.date_range("2026-01-01T00:00:00Z", periods=1800, freq="1min")
    mag = []
    plasma = []
    for time in times:
        stamp = time.isoformat().replace("+00:00", "Z")
        mag.append(
            {
                "time_tag": stamp,
                "source": "DSCOVR",
                "active": True,
                "bx_gsm": 5.0,
                "by_gsm": 0.0,
                "bz_gsm": 0.0,
                "bt": 5.0,
            }
        )
        plasma.append(
            {
                "time_tag": stamp,
                "source": "DSCOVR",
                "active": True,
                "proton_density": 5.0,
                "proton_speed": 400.0,
                "proton_temperature": 100000.0,
            }
        )

    manifest = run_noaa_realtime_pipeline(
        run_name="stale-fixture",
        retrieval_start="2026-01-02T00:00:00Z",
        analysis_start="2026-01-03T00:00:00Z",
        analysis_end="2026-01-03T06:00:00Z",
        outdir=tmp_path,
        session=FakeSession(mag, plasma),
    )
    assert manifest["status"] == "SUCCESS"
    assert manifest["source_state"] == "STALE"
    assert manifest["freshness_minutes"] > 20
    assert manifest["source"]["source_identity_counts"] == {"DSCOVR": 1800}
    assert manifest["effective_analysis_window"]["end"].startswith("2026-01-02T06:00:00")



def test_multi_spacecraft_operational_rows_use_provider_active_selection():
    frame = pd.DataFrame(
        [
            {"time_tag": "2026-01-01T00:00:00Z", "source": "SOLAR1", "active": True},
            {"time_tag": "2026-01-01T00:00:00Z", "source": "ACE", "active": False},
            {"time_tag": "2026-01-01T00:01:00Z", "source": "SOLAR1", "active": "true"},
            {"time_tag": "2026-01-01T00:01:00Z", "source": "IMAP", "active": "false"},
        ]
    )
    selected = _select_active_operational_rows(frame, source_name="test")
    assert selected["source"].tolist() == ["SOLAR1", "SOLAR1"]


def test_operational_history_current_response_supersedes_revision():
    cached = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-01-01T00:00:00Z"]),
            "source": ["SOLAR1"],
            "B_mag": [5.0],
        }
    )
    current = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"]),
            "source": ["SOLAR1", "SOLAR1"],
            "B_mag": [6.0, 7.0],
        }
    )
    merged, metrics = _merge_operational_history(
        cached, current, compare_columns=["source", "B_mag"]
    )
    assert merged["B_mag"].tolist() == [6.0, 7.0]
    assert metrics["revised_overlap_rows"] == 1
    assert metrics["identical_overlap_rows"] == 0


def test_rolling_state_supplies_full_prior_baseline(tmp_path):
    from sources.noaa_swpc.download_realtime import run_noaa_realtime_pipeline

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    current_start = pd.Timestamp("2026-01-01T06:00:00Z")
    cached_times = pd.date_range(
        current_start - pd.Timedelta(hours=6),
        periods=360,
        freq="1min",
    )
    current_times = pd.date_range(current_start, periods=1434, freq="1min")

    pd.DataFrame(
        {
            "time": cached_times,
            "time_tag": [value.isoformat().replace("+00:00", "Z") for value in cached_times],
            "source": "SOLAR1",
            "active": True,
            "bx_gsm": 5.0,
            "by_gsm": 0.0,
            "bz_gsm": 0.0,
            "bt": 5.0,
            "B_mag": 5.0,
        }
    ).to_csv(state_dir / "noaa_mag_active_history.csv", index=False)
    pd.DataFrame(
        {
            "time": cached_times,
            "time_tag": [value.isoformat().replace("+00:00", "Z") for value in cached_times],
            "source": "SOLAR1",
            "active": True,
            "density": 5.0,
            "speed": 400.0,
            "temperature": 100000.0,
        }
    ).to_csv(state_dir / "noaa_plasma_active_history.csv", index=False)

    magnetic = []
    plasma = []
    for index, value in enumerate(current_times):
        stamp = value.isoformat().replace("+00:00", "Z")
        field = 10.0 if index >= 1374 else 5.0
        magnetic.append(
            {
                "time_tag": stamp,
                "source": "SOLAR1",
                "active": True,
                "bx_gsm": field,
                "by_gsm": 0.0,
                "bz_gsm": 0.0,
                "bt": field,
            }
        )
        plasma.append(
            {
                "time_tag": stamp,
                "source": "SOLAR1",
                "active": True,
                "proton_density": 5.0,
                "proton_speed": 400.0,
                "proton_temperature": 100000.0,
            }
        )

    effective_end = current_times[-1] + pd.Timedelta(minutes=1)
    manifest = run_noaa_realtime_pipeline(
        run_name="cached-fixture",
        retrieval_start=(effective_end - pd.Timedelta(hours=30)).isoformat(),
        analysis_start=(effective_end - pd.Timedelta(hours=6)).isoformat(),
        analysis_end=effective_end.isoformat(),
        outdir=tmp_path,
        state_dir=state_dir,
        session=FakeSession(magnetic, plasma),
    )
    assert manifest["status"] == "SUCCESS"
    assert manifest["analysis"]["baseline_valid_rows"] > 0
    assert manifest["rolling_state"]["magnetic_merge"]["cached_rows"] == 360
    assert (tmp_path / "cached-fixture" / "noaa_realtime_baseline_input.csv").exists()
    canonical = pd.read_csv(tmp_path / "cached-fixture" / "noaa_realtime_canonical.csv")
    assert canonical["chi_B24M"].max() == pytest.approx(1.0)
