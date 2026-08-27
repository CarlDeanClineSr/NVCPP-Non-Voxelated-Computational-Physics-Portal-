import json
from pathlib import Path

import numpy as np
import pandas as pd

import observatory.run_hourly as hourly


def test_hourly_orchestrator_builds_teaching_package(monkeypatch, tmp_path: Path):
    config = {
        "config_version": "1.0.0",
        "timing": {
            "safety_lag_minutes": 20,
            "retrieval_hours": 30,
            "analysis_hours": 6,
            "event_focus_minutes": 60,
        },
        "sources": {
            "noaa_operational_l1": {"enabled": True},
            "solar1_mag": {"enabled": False, "contract": "unused"},
        },
        "event_detection": {
            "research_watch_chi": 0.15,
            "significant_chi": 0.5,
            "severe_chi": 1.0,
            "rotation_degrees": 45.0,
            "severe_rotation_degrees": 120.0,
            "minute_relative_magnitude_change": 0.25,
            "minimum_field_nT_for_rotation": 0.1,
            "merge_gap_minutes": 2,
        },
        "storage": {"drive_parent_folder_id": "folder"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    def fake_runner(*, run_name, retrieval_start, analysis_start, analysis_end, outdir):
        run_dir = outdir / run_name
        run_dir.mkdir(parents=True)
        times = pd.date_range("2026-08-26T13:00:00Z", periods=360, freq="1min")
        delta = np.zeros(360)
        delta[-3:] = 0.75
        frame = pd.DataFrame(
            {
                "time": times,
                "bx_gsm": 5.0,
                "by_gsm": 0.0,
                "bz_gsm": 0.0,
                "B_mag": 5.0 * (1.0 + delta),
                "B0": 5.0,
                "ratio_B24M": 1.0 + delta,
                "delta_B24M": delta,
                "chi_B24M": np.abs(delta),
                "baseline_valid": True,
                "baseline_reason": "VALID",
            }
        )
        frame.to_csv(run_dir / "noaa_realtime_canonical.csv", index=False)
        pd.DataFrame(columns=["reason_code"]).to_csv(
            run_dir / "noaa_realtime_quarantine.csv", index=False
        )
        manifest = {
            "status": "SUCCESS",
            "source_state": "CURRENT",
            "git_commit": "fixture",
            "protocol_id": "CLINE-L1-B24M-TRAIL-v1",
            "protocol_version": "1.1.0",
            "source": {"coordinate_frame": "GSM"},
        }
        (run_dir / "noaa_realtime_run_manifest.json").write_text(json.dumps(manifest))
        return manifest

    monkeypatch.setattr(hourly, "run_noaa_realtime_pipeline", fake_runner)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    result = hourly.run_hourly_observatory(
        config_path=config_path,
        output_root=tmp_path / "runs",
        now="2026-08-26T19:17:00Z",
    )
    root = Path(result["run_root"])
    assert result["manifest"]["status"] == "SUCCESS"
    assert (root / "status" / "LATEST.md").exists()
    assert (root / "RUN_LESSON.md").exists()
    events = json.loads(
        (
            root
            / "missions"
            / "noaa_operational_l1"
            / "observatory"
            / "event_candidates.json"
        ).read_text()
    )
    assert events[0]["dominant_type"] == "MAG_COMPRESSION_CANDIDATE"
    assert (root / "missions" / "noaa_operational_l1" / "observatory" / "charts" / "magnetic_magnitude.png").exists()


def test_solar1_hourly_uses_latest_provider_complete_window(monkeypatch, tmp_path: Path):
    contract_path = tmp_path / "contract.json"
    contract_path.write_text("{}")
    contract = {
        "source": {"api_base": "https://example.test", "hapi_dataset_id": "solar1"},
        "cadence": {"expected_seconds": 60},
    }
    monkeypatch.setattr(hourly, "load_contract_or_raise", lambda path: contract)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": {"code": 1200},
                "startDate": "2026-04-01T00:00:00Z",
                "stopDate": "2026-08-25T23:59:00Z",
            }

    monkeypatch.setattr(hourly.requests, "get", lambda *args, **kwargs: Response())
    captured = {}

    def fake_solar1_pipeline(**kwargs):
        captured.update(kwargs)
        run_dir = kwargs["outdir"] / kwargs["run_name"]
        run_dir.mkdir(parents=True)
        (run_dir / "solar1_run_manifest.json").write_text(
            json.dumps({"status": "SUCCESS"})
        )

    monkeypatch.setattr(hourly, "run_solar1_pipeline", fake_solar1_pipeline)
    window = hourly.build_hourly_window(now="2026-08-27T02:17:00Z")
    manifest = hourly._solar1_hourly_runner(
        window=window,
        mission_root=tmp_path / "missions",
        contract_path=contract_path,
    )
    assert captured["end_time"] == "2026-08-26T00:00:00.000Z"
    assert captured["analysis_start"] == "2026-08-25T18:00:00.000Z"
    assert manifest["source_state"] == "DELAYED"
    assert manifest["freshness_minutes"] > 20


def test_successful_but_stale_source_makes_observatory_degraded(monkeypatch, tmp_path: Path):
    config = {
        "config_version": "1.0.0",
        "timing": {
            "safety_lag_minutes": 20,
            "retrieval_hours": 30,
            "analysis_hours": 6,
            "event_focus_minutes": 60,
        },
        "sources": {
            "noaa_operational_l1": {"enabled": True},
            "solar1_mag": {"enabled": False, "contract": "unused"},
        },
        "event_detection": {},
        "storage": {"drive_parent_folder_id": "folder"},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    def stale_runner(*, run_name, retrieval_start, analysis_start, analysis_end, outdir):
        run_dir = outdir / run_name
        run_dir.mkdir(parents=True)
        times = pd.date_range("2026-08-25T12:00:00Z", periods=360, freq="1min")
        frame = pd.DataFrame(
            {
                "time": times,
                "bx_gsm": 5.0,
                "by_gsm": 0.0,
                "bz_gsm": 0.0,
                "B_mag": 5.0,
                "B0": 5.0,
                "ratio_B24M": 1.0,
                "delta_B24M": 0.0,
                "chi_B24M": 0.0,
                "baseline_valid": True,
                "baseline_reason": "VALID",
            }
        )
        frame.to_csv(run_dir / "noaa_realtime_canonical.csv", index=False)
        pd.DataFrame(columns=["reason_code"]).to_csv(
            run_dir / "noaa_realtime_quarantine.csv", index=False
        )
        (run_dir / "noaa_realtime_run_manifest.json").write_text(
            json.dumps({"status": "SUCCESS", "source_state": "STALE"})
        )
        return {"status": "SUCCESS", "source_state": "STALE"}

    monkeypatch.setattr(hourly, "run_noaa_realtime_pipeline", stale_runner)
    result = hourly.run_hourly_observatory(
        config_path=config_path,
        output_root=tmp_path / "runs",
        now="2026-08-27T02:17:00Z",
    )
    assert result["manifest"]["status"] == "DEGRADED"
    assert result["status"]["missions"]["NOAA_OPERATIONAL_L1"]["source_state"] == "STALE"
