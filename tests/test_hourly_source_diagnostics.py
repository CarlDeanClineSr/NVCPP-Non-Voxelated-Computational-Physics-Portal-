"""Offline source/summary regression fixtures; no operational data acquisition."""

import json
from pathlib import Path

import pandas as pd
import pytest

import observatory.run_hourly as hourly
import sources.noaa_swpc.download_realtime as noaa
import sources.solar1.download_solar1 as solar
from core.exceptions import (
    BaselineWarmupError,
    InsufficientCoverageError,
    SourceEndIncompleteError,
    SourcePrerollIncompleteError,
)


class JsonResponse:
    def __init__(self, payload, url="https://example.test/fixture"):
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8")
        self.url = url
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class NoaaSession:
    def __init__(self, magnetic, plasma):
        self.magnetic = magnetic
        self.plasma = plasma
        self.headers = {}

    def get(self, url, timeout=None):
        return JsonResponse(self.magnetic if "mag" in url else self.plasma, url)


def noaa_fixture(periods, sparse=False):
    times = pd.date_range("2026-01-01T00:00:00Z", periods=periods, freq="1min")
    magnetic, plasma = [], []
    for index, stamp in enumerate(times):
        base = {"time_tag": stamp.isoformat(), "source": "SOLAR1", "active": True}
        plasma.append({**base, "proton_density": 5.0, "proton_speed": 400.0,
                       "proton_temperature": 100000.0})
        if sparse and index % 10 == 5 and index != 5:
            continue
        magnetic.append({**base, "bx_gsm": 5.0, "by_gsm": 0.0, "bz_gsm": 0.0,
                         "bt": 99.0 if sparse and index == 5 else 5.0})
    return times, NoaaSession(magnetic, plasma)


def test_noaa_failed_baseline_preserves_state_quarantine_and_exact_diagnostic(tmp_path):
    times, session = noaa_fixture(1800, sparse=True)
    end = times[-1] + pd.Timedelta(minutes=1)
    state = tmp_path / "state"
    with pytest.raises(InsufficientCoverageError) as caught:
        noaa.run_noaa_realtime_pipeline(
            run_name="fixture", retrieval_start=times[0].isoformat(),
            analysis_start=(end - pd.Timedelta(hours=6)).isoformat(),
            analysis_end=end.isoformat(), outdir=tmp_path,
            state_dir=state, session=session,
        )
    root = tmp_path / "fixture"
    manifest = json.loads((root / "noaa_realtime_run_manifest.json").read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["reason_code"] == "BASELINE_INSUFFICIENT_COVERAGE"
    assert manifest["diagnostics"] == caught.value.diagnostics
    assert manifest["diagnostics"]["required_samples"] == 1368
    assert manifest["diagnostics"]["required_pct"] == 95.0
    assert manifest["sanitization"] == {
        "quarantine_rows": 1, "reason_counts": {"BT_VECTOR_MISMATCH": 1}
    }
    assert len(pd.read_csv(root / "noaa_realtime_quarantine.csv")) == 1
    assert manifest["rolling_state"]["magnetic_state"]["rows"] == 1620
    assert (state / "noaa_mag_active_history.csv").exists()
    assert (state / "noaa_plasma_active_history.csv").exists()
    assert (root / "rtsw_mag_1m_raw.json").exists()
    assert (root / "noaa_realtime_baseline_input.csv").exists()
    assert not (root / "noaa_realtime_canonical.csv").exists()


def test_noaa_actual_short_history_keeps_warmup_classification(tmp_path):
    times, session = noaa_fixture(720)
    end = times[-1] + pd.Timedelta(minutes=1)
    with pytest.raises(BaselineWarmupError):
        noaa.run_noaa_realtime_pipeline(
            run_name="fixture", retrieval_start=(end - pd.Timedelta(hours=30)).isoformat(),
            analysis_start=(end - pd.Timedelta(hours=6)).isoformat(),
            analysis_end=end.isoformat(), outdir=tmp_path, session=session,
        )
    manifest = json.loads((tmp_path / "fixture" / "noaa_realtime_run_manifest.json").read_text())
    assert manifest["reason_code"] == "BASELINE_WARMUP"
    assert manifest["diagnostics"]["full_window_analysis_rows"] == 0


def configure_solar_fixture(monkeypatch, tmp_path, *, bad_start=0, missing_end=0):
    start = pd.Timestamp("2026-09-03T18:00:00Z")
    end = start + pd.Timedelta(hours=30)
    contract = {
        "source": {"product_id": "fixture"},
        "time": {"parameter_id": "time"},
        "vector": {
            "coordinate_frame": "GSE", "units": "nT",
            "components": {axis: {"parameter_id": axis, "fill_values": [-9999.0]}
                           for axis in ("x", "y", "z")},
        },
        "cadence": {"expected_seconds": 60},
        "physics": {"pre_roll_hours": 24, "minimum_baseline_coverage_fraction": 0.95},
    }
    path = tmp_path / "contract.json"
    path.write_text("{}")
    monkeypatch.setattr(solar, "load_contract_or_raise", lambda _: contract)
    monkeypatch.setattr(solar, "classify_solar1_interval", lambda *args: {
        "label": "FIXTURE", "operational_validation_claim_allowed": False,
    })
    info = {"startDate": "2026-04-01T00:00:00Z", "stopDate": "2026-09-04T23:59:00Z"}
    monkeypatch.setattr(solar, "request_hapi_info", lambda *args: {
        "info": info, "resolved_url": "https://example.test/info",
        "raw_sha256": "fixture", "canonical_schema_sha256": "fixture",
    })
    times = pd.date_range(start, end, freq="1min", inclusive="left")
    if missing_end:
        times = times[:-missing_end]
    raw = pd.DataFrame({"_source_row": range(1, len(times) + 1),
                        "time": [t.isoformat() for t in times],
                        "x": 5.0, "y": 0.0, "z": 0.0})
    if bad_start:
        raw.loc[:bad_start - 1, "x"] = 0.0
    monkeypatch.setattr(solar, "request_hapi_csv", lambda *args: (raw.copy(), {
        "resolved_url": "https://example.test/data", "sha256": "fixture",
        "size_bytes": 0, "requested_parameters": ["time", "x", "y", "z"],
    }))
    return start, end, path


@pytest.mark.parametrize(
    "bad_start,missing_end,error_type,reason",
    [(2, 0, SourcePrerollIncompleteError, "SOURCE_PREROLL_INCOMPLETE"),
     (0, 2, SourceEndIncompleteError, "SOURCE_END_INCOMPLETE")],
)
def test_solar_adapter_reports_the_actual_failing_boundary(
    monkeypatch, tmp_path, bad_start, missing_end, error_type, reason,
):
    start, end, contract = configure_solar_fixture(
        monkeypatch, tmp_path, bad_start=bad_start, missing_end=missing_end,
    )
    with pytest.raises(error_type):
        solar.run_solar1_pipeline("fixture", start.isoformat(),
                                 (end - pd.Timedelta(hours=6)).isoformat(),
                                 end.isoformat(), tmp_path, contract)
    root = tmp_path / "fixture"
    manifest = json.loads((root / "solar1_run_manifest.json").read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["reason_code"] == reason
    details = manifest["diagnostics"]
    assert details["earliest_raw_returned"] == start.isoformat()
    assert details["missing_preroll_seconds"] == bad_start * 60
    assert details["missing_end_seconds"] == missing_end * 60
    assert details["quarantined_rows"] == bad_start
    assert manifest["sanitation"]["quarantine_rows"] == bad_start
    assert not (root / "solar1_cline_l1_rows.csv").exists()


def test_solar_existing_one_minute_start_tolerance_remains_accepted(monkeypatch, tmp_path):
    start, end, contract = configure_solar_fixture(monkeypatch, tmp_path, bad_start=1)
    solar.run_solar1_pipeline("fixture", start.isoformat(),
                             (end - pd.Timedelta(hours=6)).isoformat(),
                             end.isoformat(), tmp_path, contract)
    root = tmp_path / "fixture"
    manifest = json.loads((root / "solar1_run_manifest.json").read_text())
    assert manifest["status"] == "SUCCESS"
    assert manifest["baseline"]["valid_rows"] > 0
    assert manifest["source_boundaries"]["missing_preroll_seconds"] == 60
    assert manifest["source_boundaries"]["missing_preroll_beyond_tolerance_seconds"] == 0
    assert (root / "solar1_cline_l1_rows.csv").exists()


def test_solar_failed_hourly_run_retains_requested_and_shifted_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(hourly, "load_contract_or_raise", lambda _: {
        "source": {"api_base": "https://example.test", "hapi_dataset_id": "fixture"},
        "cadence": {"expected_seconds": 60},
    })
    monkeypatch.setattr(hourly.requests, "get", lambda *args, **kwargs: JsonResponse({
        "status": {"code": 1200}, "startDate": "2026-04-01T00:00:00Z",
        "stopDate": "2026-09-04T23:59:00Z",
    }))
    original_error = SourcePrerollIncompleteError(missing_preroll_seconds=420)
    captured = {}

    def fail_pipeline(**kwargs):
        captured.update(kwargs)
        root = kwargs["outdir"] / kwargs["run_name"]
        root.mkdir(parents=True)
        (root / "solar1_run_manifest.json").write_text(json.dumps({
            "status": "FAILED", **original_error.as_dict(),
        }))
        raise original_error

    monkeypatch.setattr(hourly, "run_solar1_pipeline", fail_pipeline)
    window = hourly.build_hourly_window(now="2026-09-05T12:33:00Z")
    with pytest.raises(SourcePrerollIncompleteError) as caught:
        hourly._solar1_hourly_runner(window=window, mission_root=tmp_path / "missions",
                                     contract_path=tmp_path / "unused.json")
    assert caught.value is original_error
    assert captured["start_time"] == "2026-09-03T18:00:00.000Z"
    assert captured["end_time"] == "2026-09-05T00:00:00.000Z"
    manifest = json.loads((tmp_path / "missions/solar1_mag/solar1_run_manifest.json").read_text())
    assert manifest["source_state"] == "DELAYED"
    assert manifest["status"] == "FAILED"
    assert manifest["hourly_requested_window"]["analysis_end"] == "2026-09-05T12:00:00+00:00"
    assert manifest["hourly_effective_window"]["analysis_end"] == "2026-09-05T00:00:00+00:00"
    assert manifest["diagnostics"]["missing_preroll_seconds"] == 420


def test_hourly_failure_stays_failed_and_diagnostics_reach_all_summary_formats(monkeypatch, tmp_path):
    config = {
        "config_version": "1.0.0",
        "timing": {"safety_lag_minutes": 20, "retrieval_hours": 30,
                   "analysis_hours": 6, "event_focus_minutes": 60},
        "sources": {"noaa_operational_l1": {"enabled": True},
                    "solar1_mag": {"enabled": False, "contract": "unused"}},
        "event_detection": {}, "storage": {"drive_parent_folder_id": "fixture"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    error = InsufficientCoverageError(best_valid_minutes=1331, required_samples=1368,
                                      required_pct=95.0, missing_to_qualify_minutes=37)

    def fail_runner(**kwargs):
        root = kwargs["outdir"] / kwargs["run_name"]
        root.mkdir(parents=True)
        (root / "noaa_realtime_run_manifest.json").write_text(json.dumps({
            "status": "FAILED", **error.as_dict(),
        }))
        pd.DataFrame({"reason_code": ["BT_VECTOR_MISMATCH"]}).to_csv(
            root / "noaa_realtime_quarantine.csv", index=False,
        )
        raise error

    def forbidden_analysis(**kwargs):
        pytest.fail("failed source must not reach event analysis")

    monkeypatch.setattr(hourly, "run_noaa_realtime_pipeline", fail_runner)
    monkeypatch.setattr(hourly, "_mission_analyze", forbidden_analysis)
    with pytest.raises(hourly.ObservatoryError, match="all configured hourly sources failed"):
        hourly.run_hourly_observatory(config_path=config_path, output_root=tmp_path / "runs",
                                       now="2026-09-05T12:33:00Z")
    manifest_path = next((tmp_path / "runs").rglob("observatory_run_manifest.json"))
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    latest = json.loads((root / "status/latest.json").read_text())
    record = json.loads((root / "result_index.jsonl").read_text().splitlines()[0])
    assert manifest["status"] == "FAILED"
    for summary in (manifest["missions"]["NOAA_OPERATIONAL_L1"],
                    latest["missions"]["NOAA_OPERATIONAL_L1"], record):
        assert summary["reason_code"] == "BASELINE_INSUFFICIENT_COVERAGE"
        assert summary["diagnostics"]["missing_to_qualify_minutes"] == 37
        assert summary["evaluation_state"] == "UNAVAILABLE"
    assert latest["missions"]["NOAA_OPERATIONAL_L1"]["quarantine_rows"] == 1
    assert latest["missions"]["NOAA_OPERATIONAL_L1"]["latest"] == {}
    markdown = (root / "status/LATEST.md").read_text()
    assert "BASELINE_INSUFFICIENT_COVERAGE" in markdown
    assert "unavailable (source failed)" in markdown
    assert '"missing_to_qualify_minutes": 37' in markdown


def test_unreadable_diagnostic_manifest_does_not_mask_original_failure(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("not json")
    details = hourly._failure_context(path, RuntimeError("original failure"))
    assert details["reason_code"] == "SOURCE_EXCEPTION"
    assert "diagnostic_read_error" in details
    assert details["evaluation_state"] == "UNAVAILABLE"
