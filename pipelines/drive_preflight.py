#!/usr/bin/env python3
"""No-write Google Drive credential and destination preflight for NVCPP.

This module deliberately does not create folders or upload files. It verifies
that the encrypted credential can obtain an access token and that the configured
Drive destination exists, is a folder, is not trashed, and permits adding
children. Output is sanitized so OAuth tokens and client secrets never enter
logs or artifacts.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipelines.drive_vault import DRIVE_SCOPE, FOLDER_MIME, decode_credentials

PREFLIGHT_VERSION = "1.0.0"


class DrivePreflightError(RuntimeError):
    """A sanitized, operator-actionable Drive preflight failure."""


def credential_shape(encoded: str) -> dict[str, Any]:
    """Validate credential JSON structure without returning secret material."""
    if not encoded:
        raise DrivePreflightError("NVCPP_GOOGLE_AUTH_B64 is not configured")
    try:
        raw = base64.b64decode(encoded, validate=True)
        info = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise DrivePreflightError(
            "NVCPP_GOOGLE_AUTH_B64 is not strict base64-encoded UTF-8 JSON"
        ) from exc
    if not isinstance(info, dict):
        raise DrivePreflightError("decoded Google credential must be a JSON object")

    credential_type = info.get("type")
    if credential_type == "authorized_user":
        required = ("client_id", "client_secret", "refresh_token")
    elif credential_type == "service_account":
        required = ("project_id", "private_key", "client_email", "token_uri")
    else:
        raise DrivePreflightError(
            "credential JSON type must be authorized_user or service_account"
        )

    missing = [name for name in required if not str(info.get(name, "")).strip()]
    if missing:
        raise DrivePreflightError(
            f"{credential_type} credential is missing required fields: {', '.join(missing)}"
        )

    return {
        "credential_type": credential_type,
        "required_fields_present": True,
    }


def sanitize_auth_error(exc: Exception) -> str:
    """Map provider exceptions to useful messages without echoing credentials."""
    text = str(exc).lower()
    if "invalid_client" in text:
        return (
            "Google OAuth refresh failed with invalid_client: the client_id and "
            "client_secret stored in NVCPP_GOOGLE_AUTH_B64 do not form the same "
            "OAuth client that issued the refresh_token. Revoke that grant and "
            "replace the entire authorized_user JSON as one matched bundle."
        )
    if "invalid_grant" in text:
        return (
            "Google OAuth refresh failed with invalid_grant: the refresh token is "
            "expired, revoked, time-limited, or was issued for another OAuth client. "
            "Create a new offline grant and replace the entire credential bundle."
        )
    if "insufficient" in text and ("scope" in text or "permission" in text):
        return (
            "Google accepted the credential but it lacks the Drive scope or folder "
            "permission required by the NVCPP publisher."
        )
    if "not found" in text or "filenotfound" in text:
        return (
            "Google authenticated successfully, but NVCPP_DRIVE_PARENT_FOLDER_ID "
            "does not identify a folder visible to this credential."
        )
    return f"Google Drive preflight failed: {type(exc).__name__}"


def preflight_drive_access(
    *,
    encoded_credentials: str,
    parent_folder_id: str,
) -> dict[str, Any]:
    """Refresh the credential and inspect the destination without writing."""
    if not parent_folder_id.strip():
        raise DrivePreflightError("NVCPP_DRIVE_PARENT_FOLDER_ID is not configured")

    shape = credential_shape(encoded_credentials)
    try:
        credentials, credential_type = decode_credentials(encoded_credentials)

        # Refresh explicitly so an invalid OAuth bundle fails before Drive service
        # construction and before the hourly observatory performs any publication.
        from google.auth.transport.requests import Request

        credentials.refresh(Request())

        from googleapiclient.discovery import build

        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        folder = (
            service.files()
            .get(
                fileId=parent_folder_id,
                fields=(
                    "id,name,mimeType,trashed,driveId,webViewLink,"
                    "capabilities(canAddChildren,canEdit)"
                ),
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        raise DrivePreflightError(sanitize_auth_error(exc)) from exc

    if folder.get("trashed"):
        raise DrivePreflightError("configured Drive destination is in Trash")
    if folder.get("mimeType") != FOLDER_MIME:
        raise DrivePreflightError("configured Drive destination is not a folder")

    capabilities = folder.get("capabilities") or {}
    if capabilities.get("canAddChildren") is not True:
        raise DrivePreflightError(
            "credential can see the Drive folder but cannot add child files or folders"
        )

    return {
        "preflight_version": PREFLIGHT_VERSION,
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SUCCESS",
        **shape,
        "credential_type": credential_type,
        "scope_required": DRIVE_SCOPE,
        "folder": {
            "id": folder.get("id"),
            "name": folder.get("name"),
            "mime_type": folder.get("mimeType"),
            "shared_drive": bool(folder.get("driveId")),
            "can_add_children": capabilities.get("canAddChildren"),
            "can_edit": capabilities.get("canEdit"),
            "web_view_link": folder.get("webViewLink"),
        },
        "writes_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate NVCPP Google Drive credentials and destination without writing"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("drive_preflight_result.json"),
    )
    args = parser.parse_args()

    try:
        result = preflight_drive_access(
            encoded_credentials=os.environ.get("NVCPP_GOOGLE_AUTH_B64", ""),
            parent_folder_id=os.environ.get("NVCPP_DRIVE_PARENT_FOLDER_ID", ""),
        )
        exit_code = 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DrivePreflightError) else sanitize_auth_error(exc)
        result = {
            "preflight_version": PREFLIGHT_VERSION,
            "checked_utc": datetime.now(timezone.utc).isoformat(),
            "status": "FAILED",
            "error": message,
            "writes_performed": False,
        }
        exit_code = 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
