import json
from pathlib import Path

from sources.solar1.mission_phase import (
    apply_phase_label,
    classify_solar1_interval,
)


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


def test_phase_is_written_to_manifest_and_report(tmp_path: Path):
    manifest_path = tmp_path / "solar1_run_manifest.json"
    report_path = tmp_path / "solar1_cline_l1_report.md"
    manifest_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "analysis_window": {
                    "start": "2026-06-02T00:00:00Z",
                    "end": "2026-06-05T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    report_path.write_text("# Existing report\n", encoding="utf-8")

    phase = apply_phase_label(manifest_path, report_path)
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")

    assert updated["mission_phase"] == phase
    assert "PRE_OPERATIONAL_COMMISSIONING_REGRESSION" in report
    assert "Operational-performance claim enabled: **False**" in report
