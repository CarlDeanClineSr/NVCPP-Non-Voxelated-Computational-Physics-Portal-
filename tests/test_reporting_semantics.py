from pathlib import Path

from observatory.capsules import write_run_lesson


def _lesson(tmp_path: Path, summary: dict) -> str:
    path = write_run_lesson(
        run_id="test-run",
        window={"analysis_start": "2026-09-10T05:00:00Z"},
        mission_summaries={"TEST": summary},
        outdir=tmp_path,
    )
    return path.read_text(encoding="utf-8")


def test_failed_unavailable_source_is_not_reported_as_zero_events(tmp_path):
    text = _lesson(
        tmp_path,
        {
            "status": "FAILED",
            "evaluation_state": "UNAVAILABLE",
            "event_count": 0,
            "latest": {},
            "quarantine_rows": 244,
        },
    )
    assert "Candidate events: **unavailable (not evaluated)**" in text
    assert "Candidate events: **0**" not in text


def test_successful_evaluated_zero_remains_zero(tmp_path):
    text = _lesson(
        tmp_path,
        {
            "status": "SUCCESS",
            "evaluation_state": "EVALUATED",
            "event_count": 0,
            "latest": {},
            "quarantine_rows": 0,
        },
    )
    assert "Candidate events: **0**" in text


def test_failed_source_without_explicit_evaluation_state_is_unavailable(tmp_path):
    text = _lesson(
        tmp_path,
        {
            "status": "FAILED",
            "event_count": 0,
            "latest": {},
            "quarantine_rows": 0,
        },
    )
    assert "Candidate events: **unavailable (source failed)**" in text
