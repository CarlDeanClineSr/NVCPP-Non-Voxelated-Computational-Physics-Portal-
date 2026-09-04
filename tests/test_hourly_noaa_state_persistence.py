from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/hourly_observatory.yml")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _step_block(text: str, step_name: str, next_step_name: str) -> str:
    start = text.index(f"      - name: {step_name}")
    end = text.index(f"      - name: {next_step_name}", start)
    return text[start:end]


def test_noaa_state_uses_explicit_restore_and_save_actions() -> None:
    text = _workflow_text()

    assert "uses: actions/cache/restore@v4" in text
    assert "uses: actions/cache/save@v4" in text
    assert "uses: actions/cache@v4" not in text

    key = "nvcpp-noaa-state-v1-${{ github.run_id }}-${{ github.run_attempt }}"
    assert text.count(key) == 2


def test_noaa_state_is_saved_before_the_job_can_be_marked_failed() -> None:
    text = _workflow_text()

    restore_position = text.index("uses: actions/cache/restore@v4")
    observatory_position = text.index("      - name: Run hourly observatory")
    save_position = text.index(
        "      - name: Save NOAA rolling state before result enforcement"
    )
    enforcement_position = text.index(
        "      - name: Enforce observatory and configured-vault result"
    )

    assert restore_position < observatory_position < save_position < enforcement_position

    save_block = _step_block(
        text,
        "Save NOAA rolling state before result enforcement",
        "Publish immutable package to Google Drive",
    )
    assert "id: noaa_state_save" in save_block
    assert "if: ${{ always() }}" in save_block
    assert "continue-on-error: true" in save_block


def test_final_enforcement_checks_the_explicit_cache_save_result() -> None:
    text = _workflow_text()

    assert (
        "NOAA_STATE_SAVE_OUTCOME: ${{ steps.noaa_state_save.outcome }}" in text
    )
    assert (
        'if os.environ.get("NOAA_STATE_SAVE_OUTCOME") != "success":' in text
    )
    assert "NOAA rolling-state persistence failed" in text


def test_repair_does_not_change_scientific_configuration_or_detector_code() -> None:
    """The persistence regression test is workflow-only by construction.

    Science values remain sourced from the same committed configuration and Python
    modules. This test guards against moving the repair into an alternate config or
    changing the hourly command line while fixing cache behavior.
    """

    text = _workflow_text()

    assert "--config config/hourly_observatory.v1.json" in text
    assert "python -m observatory.run_hourly" in text
    assert "--outdir runs/hourly" in text
    assert "core/event_detection.py" in text
    assert "sources/noaa_swpc/**" in text
    assert "sources/solar1/**" in text
