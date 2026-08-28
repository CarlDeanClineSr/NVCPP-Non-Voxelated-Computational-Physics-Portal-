import base64
import json

import pytest

from pipelines.drive_preflight import (
    DrivePreflightError,
    credential_shape,
    sanitize_auth_error,
)


def encode(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def test_authorized_user_shape_passes_without_returning_secrets():
    result = credential_shape(
        encode(
            {
                "type": "authorized_user",
                "client_id": "example.apps.googleusercontent.com",
                "client_secret": "secret-value",
                "refresh_token": "refresh-value",
            }
        )
    )
    assert result == {
        "credential_type": "authorized_user",
        "required_fields_present": True,
    }
    assert "client_secret" not in result
    assert "refresh_token" not in result


def test_authorized_user_requires_matched_bundle_fields():
    with pytest.raises(DrivePreflightError, match="client_secret"):
        credential_shape(
            encode(
                {
                    "type": "authorized_user",
                    "client_id": "example.apps.googleusercontent.com",
                    "refresh_token": "refresh-value",
                }
            )
        )


def test_strict_base64_rejects_garbage():
    with pytest.raises(DrivePreflightError, match="strict base64"):
        credential_shape("not base64 !!!")


def test_invalid_client_message_is_actionable_and_sanitized():
    message = sanitize_auth_error(
        RuntimeError(
            "invalid_client: The provided client secret is invalid. "
            "refresh_token=do-not-repeat-this"
        )
    )
    assert "invalid_client" in message
    assert "same OAuth client" in message
    assert "do-not-repeat-this" not in message


def test_invalid_grant_message_does_not_repeat_provider_payload():
    message = sanitize_auth_error(
        RuntimeError("invalid_grant: token=do-not-repeat-this")
    )
    assert "invalid_grant" in message
    assert "do-not-repeat-this" not in message
