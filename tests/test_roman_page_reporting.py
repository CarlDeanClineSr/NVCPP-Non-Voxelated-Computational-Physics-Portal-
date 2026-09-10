"""Offline page-status regressions: response capture is not authenticated access."""

from copy import deepcopy
import hashlib

import pytest

from observatory.roman.mast_client import PageProbe
from observatory.roman.page_reporting import (
    classify_page_capture,
    summarize_page_captures,
)


@pytest.mark.parametrize("status", [301, 302, 304, 401, 403, 404, 500, 503])
def test_non_2xx_is_never_a_successful_page_capture(status):
    assert classify_page_capture(status, "https://example.test/page") == "HTTP_NON_2XX_CAPTURE"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/hub/login?next=%2Fhub%2F",
        "https://example.test/LOGIN/",
        "https://example.test/signin",
        "https://example.test/sign-in",
        "https://example.test/log-in",
        "https://example.test/hub/%6Cogin",
    ],
)
def test_explicit_final_login_path_is_separate_from_http_status(url):
    assert classify_page_capture(200, url) == "LOGIN_PAGE_CAPTURE"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/roman",
        "https://example.test/login-instructions",
        "https://example.test/roman?next=/hub/login",
        "https://example.test/roman#login",
        "https://[malformed",
    ],
)
def test_other_2xx_does_not_claim_content_or_authentication(url):
    assert classify_page_capture(200, url) == "HTTP_2XX_ACCESS_UNVERIFIED"


def test_five_captures_are_four_2xx_one_http_error_and_one_login_subset():
    rows = [
        {"http_status": 404, "final_url": "https://example.test/countdown"},
        {"http_status": 200, "final_url": "https://example.test/mast"},
        {"http_status": 200, "final_url": "https://example.test/triplet"},
        {"http_status": 200, "final_url": "https://example.test/hub/login?next=/hub/"},
        {"http_status": 200, "final_url": "https://example.test/isim"},
    ]
    before = deepcopy(rows)
    counts = summarize_page_captures(rows)
    assert rows == before
    assert counts == {
        "captured_response_count": 5,
        "http_2xx_count": 4,
        "http_non_2xx_count": 1,
        "login_page_capture_count": 1,
        "non_login_http_2xx_count": 3,
        "transport_error_count": 0,
    }


def test_transport_failure_is_not_a_captured_response():
    counts = summarize_page_captures([], transport_error_count=2)
    assert counts["transport_error_count"] == 2
    assert counts["captured_response_count"] == 0
    assert counts["http_2xx_count"] == 0


@pytest.mark.parametrize(
    ("status", "final_url", "state"),
    [
        (404, "https://example.test/page", "HTTP_NON_2XX_CAPTURE"),
        (200, "https://example.test/hub/login", "LOGIN_PAGE_CAPTURE"),
    ],
)
def test_status_reporting_preserves_exact_response_and_hash(status, final_url, state):
    body = b"<html><title>Captured evidence</title>\xff\r\n</html>"
    probe = PageProbe(
        requested_url="https://example.test/requested",
        final_url=final_url,
        http_status=status,
        content_type="text/html",
        retrieved_utc="2026-09-10T06:52:23Z",
        raw_bytes=body,
        title="Captured evidence",
        last_modified=None,
    )
    summary = probe.summary()
    assert probe.raw_bytes == body
    assert summary["raw_sha256"] == hashlib.sha256(body).hexdigest()
    assert summary["raw_size_bytes"] == len(body)
    assert summary["http_status"] == status
    assert summary["requested_url"] == probe.requested_url
    assert summary["final_url"] == final_url
    assert summary["capture_state"] == state
