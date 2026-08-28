import hashlib
import json
from pathlib import Path

import pytest

from sources.solar1.mission_phase import (
    apply_phase_label,
    classify_solar1_interval,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preoperational_regression_is_named_explicitly():
    phase = classify_solar1_interval(
        "2026-06-02T00:00:00Z",
        "2026-06-05T00:00:00Z",
    )
    assert phase["label"] == "PRE_OPERATIONAL_COMMISSIONING_REGRESSION"
    assert phase["operational_validation_claim_allowed"] is False


def test_operational_interval_is_named_explicitly():
    phase = classify_solar1_interval(
        "2026-06-11T00:00:00Z",
        "2026-06-12T00:00:00Z",
    )
    assert phase["label"] == "OPERATIONAL"
    assert phase["operational_validation_claim_allowed"] is True


def test_transition_interval_does_not_enable_operational_claim():
    phase = classify_solar1_interval(
        "2026-06-09T12:00:00Z",
        "2026-06-10T12:00:00Z",
    )
    assert phase["label"] == "TRANSITION_SPANNING_OPERATIONAL_START"
    assert phase["operational_validation_claim_allowed"] is False


def test_phase_is_written_and_report_artifact_hash_is_refreshed(tmp_path: Path):
    manifest_path = tmp_path / "solar1_run_manifest.json"
    report_path = tmp_path / "solar1_cline_l1_report.md"
    report_path.write_text("# Existing report\n", encoding="utf-8")
    old_hash = sha256(report_path)
    manifest_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "analysis_window": {
                    "start": "2026-06-02T00:00:00Z",
                    "end": "2026-06-05T00:00:00Z",
                },
                "artifacts": [
                    {
                        "path": report_path.name,
                        "size_bytes": report_path.stat().st_size,
                        "sha256": old_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    phase = apply_phase_label(manifest_path, report_path)
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    report_artifact = updated["artifacts"][0]

    assert updated["mission_phase"] == phase
    assert "PRE_OPERATIONAL_COMMISSIONING_REGRESSION" in report
    assert "Operational-performance claim enabled: **False**" in report
    assert report_artifact["sha256"] == sha256(report_path)
    assert report_artifact["sha256"] != old_hash
    assert report_artifact["size_bytes"] == report_path.stat().st_size


def test_phase_label_fails_when_report_artifact_record_is_missing(tmp_path: Path):
    manifest_path = tmp_path / "solar1_run_manifest.json"
    report_path = tmp_path / "solar1_cline_l1_report.md"
    report_path.write_text("# Existing report\n", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "analysis_window": {
                    "start": "2026-06-02T00:00:00Z",
                    "end": "2026-06-05T00:00:00Z",
                },
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one artifact record"):
        apply_phase_label(manifest_path, report_path)
