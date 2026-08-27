import json
from pathlib import Path

from pipelines.drive_vault import build_inventory, publish_directory


def test_drive_dry_run_builds_immutable_inventory(tmp_path: Path):
    source = tmp_path / "run"
    (source / "raw").mkdir(parents=True)
    (source / "raw" / "a.json").write_text('{"x": 1}')
    (source / "report.md").write_text("# report")
    result = publish_directory(
        source=source,
        parent_folder_id="folder-id",
        run_name="test-run",
        encoded_credentials="",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"
    assert result["file_count"] == 2
    receipt = json.loads((source / "drive_upload_receipt.json").read_text())
    assert receipt["run_name"] == "test-run"
    assert {item["relative_path"] for item in build_inventory(source)} == {
        "raw/a.json",
        "report.md",
    }
