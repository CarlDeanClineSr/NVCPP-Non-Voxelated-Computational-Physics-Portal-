#!/usr/bin/env python3
"""Create-only Google Drive vault publication for immutable NVCPP run packages.

Authentication is intentionally external to the repository. The workflow may
supply one base64-encoded Google credential JSON through the
`NVCPP_GOOGLE_AUTH_B64` secret. Both dedicated service-account credentials
(appropriate for a Shared Drive) and an OAuth authorized-user credential
(appropriate for the user's My Drive) are supported.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
FOLDER_MIME = "application/vnd.google-apps.folder"
VAULT_SINK_VERSION = "1.0.0"


class DriveVaultError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_inventory(source: Path) -> list[dict[str, Any]]:
    if not source.is_dir():
        raise DriveVaultError(f"source is not a directory: {source}")
    records = []
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.name != "drive_upload_receipt.json":
            records.append(
                {
                    "relative_path": path.relative_to(source).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    if not records:
        raise DriveVaultError("source directory contains no files")
    return records


def decode_credentials(encoded: str) -> tuple[Any, str]:
    try:
        info = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise DriveVaultError("NVCPP_GOOGLE_AUTH_B64 is not valid base64 JSON") from exc

    credential_type = info.get("type")
    if credential_type == "service_account":
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[DRIVE_SCOPE]
        )
    elif credential_type == "authorized_user":
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_info(info, scopes=[DRIVE_SCOPE])
    else:
        raise DriveVaultError(
            "credential JSON type must be service_account or authorized_user"
        )
    return credentials, credential_type


def drive_service(encoded_credentials: str) -> tuple[Any, str]:
    from googleapiclient.discovery import build

    credentials, credential_type = decode_credentials(encoded_credentials)
    return (
        build("drive", "v3", credentials=credentials, cache_discovery=False),
        credential_type,
    )


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def find_children(service: Any, parent_id: str, name: str) -> list[dict[str, Any]]:
    query = (
        f"name = '{_escape_query(name)}' and "
        f"'{_escape_query(parent_id)}' in parents and trashed = false"
    )
    result = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id,name,mimeType,webViewLink)",
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return result.get("files", [])


def create_folder(service: Any, parent_id: str, name: str) -> dict[str, Any]:
    existing = find_children(service, parent_id, name)
    if existing:
        raise DriveVaultError(
            f"immutable vault collision: {name!r} already exists under {parent_id}"
        )
    return (
        service.files()
        .create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


def upload_file(
    service: Any,
    *,
    path: Path,
    parent_id: str,
    sha256: str,
    run_name: str,
) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    existing = find_children(service, parent_id, path.name)
    if existing:
        raise DriveVaultError(
            f"immutable vault collision: file {path.name!r} already exists"
        )
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(
        str(path),
        mimetype=mimetype,
        resumable=path.stat().st_size >= 5 * 1024 * 1024,
    )
    result = (
        service.files()
        .create(
            body={
                "name": path.name,
                "parents": [parent_id],
                "appProperties": {
                    "nvcpp_sha256": sha256,
                    "nvcpp_run": run_name,
                },
            },
            media_body=media,
            fields="id,name,mimeType,size,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    result["sha256"] = sha256
    return result


def publish_directory(
    *,
    source: Path,
    parent_folder_id: str,
    run_name: str,
    encoded_credentials: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    inventory = build_inventory(source)
    plan = {
        "vault_sink_version": VAULT_SINK_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "parent_folder_id": parent_folder_id,
        "run_name": run_name,
        "file_count": len(inventory),
        "total_bytes": sum(item["size_bytes"] for item in inventory),
        "inventory": inventory,
        "dry_run": dry_run,
    }
    receipt_path = source / "drive_upload_receipt.json"
    if dry_run:
        plan["status"] = "DRY_RUN"
        receipt_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
        return plan

    if not encoded_credentials:
        raise DriveVaultError("NVCPP_GOOGLE_AUTH_B64 is not configured")
    if not parent_folder_id:
        raise DriveVaultError("NVCPP_DRIVE_PARENT_FOLDER_ID is not configured")

    service, credential_type = drive_service(encoded_credentials)
    run_folder = create_folder(service, parent_folder_id, run_name)
    folder_ids: dict[str, str] = {".": run_folder["id"]}
    uploaded: list[dict[str, Any]] = []

    for item in inventory:
        relative = Path(item["relative_path"])
        parent_key = "."
        current_parent = run_folder["id"]
        for part in relative.parts[:-1]:
            key = part if parent_key == "." else f"{parent_key}/{part}"
            if key not in folder_ids:
                folder = create_folder(service, current_parent, part)
                folder_ids[key] = folder["id"]
            current_parent = folder_ids[key]
            parent_key = key
        result = upload_file(
            service,
            path=source / relative,
            parent_id=current_parent,
            sha256=item["sha256"],
            run_name=run_name,
        )
        uploaded.append(
            {
                "relative_path": item["relative_path"],
                "drive_id": result.get("id"),
                "web_view_link": result.get("webViewLink"),
                "size": result.get("size"),
                "sha256": item["sha256"],
            }
        )

    receipt = {
        **plan,
        "status": "SUCCESS",
        "credential_type": credential_type,
        "drive_run_folder": run_folder,
        "uploaded": uploaded,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    # Upload the receipt last so the remote package contains its own remote IDs.
    receipt_result = upload_file(
        service,
        path=receipt_path,
        parent_id=run_folder["id"],
        sha256=sha256_file(receipt_path),
        run_name=run_name,
    )
    receipt["receipt_drive_id"] = receipt_result.get("id")
    # The remote receipt is intentionally immutable. The returned in-memory
    # result carries its Drive ID without rewriting the already-uploaded file.
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish an immutable NVCPP run to Google Drive")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument(
        "--parent-folder-id",
        default=os.environ.get("NVCPP_DRIVE_PARENT_FOLDER_ID", ""),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = publish_directory(
            source=args.source,
            parent_folder_id=args.parent_folder_id,
            run_name=args.run_name,
            encoded_credentials=os.environ.get("NVCPP_GOOGLE_AUTH_B64", ""),
            dry_run=args.dry_run,
        )
        print(json.dumps({key: result.get(key) for key in ("status", "run_name", "file_count")}, indent=2))
    except Exception as exc:
        print(f"[NVCPP-DRIVE-ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
