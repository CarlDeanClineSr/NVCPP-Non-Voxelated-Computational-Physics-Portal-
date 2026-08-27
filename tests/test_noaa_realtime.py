import numpy as np
import pandas as pd
import pytest

from sources.noaa_swpc.download_realtime import (
    _add_plasma_physics,
    _sanitize_magnetic,
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
    magnetic = [
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "SOLAR1",
            "active": True,
            "bx_gsm": 3.0,
            "by_gsm": 4.0,
            "bz_gsm": 0.0,
            "bt": 5.0,
        },
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "ACE",
            "active": False,
            "bx_gsm": 9.0,
            "by_gsm": 0.0,
            "bz_gsm": 0.0,
            "bt": 9.0,
        },
        {
            "time_tag": "2026-01-01T00:01:00Z",
            "source": "SOLAR1",
            "active": "true",
            "bx_gsm": 0.0,
            "by_gsm": 6.0,
            "bz_gsm": 8.0,
            "bt": 10.0,
        },
        {
            "time_tag": "2026-01-01T00:01:00Z",
            "source": "IMAP",
            "active": "false",
            "bx_gsm": 12.0,
            "by_gsm": 0.0,
            "bz_gsm": 0.0,
            "bt": 12.0,
        },
    ]
    raw = _table(magnetic, required=["time_tag"], source_name="mag")
    selected = _select_active_operational_rows(raw, source_name="mag")
    assert selected["source"].tolist() == ["SOLAR1", "SOLAR1"]
    assert selected["time_tag"].is_unique

    quarantine = []
    clean, coordinate_frame = _sanitize_magnetic(selected, quarantine)
    assert coordinate_frame == "GSM"
    assert clean["B_mag"].tolist() == pytest.approx([5.0, 10.0])
    assert quarantine == []
