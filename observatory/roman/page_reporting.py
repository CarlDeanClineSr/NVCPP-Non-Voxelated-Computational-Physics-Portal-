"""Offline descriptions of captured pages, not authentication or mission evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit


_LOGIN_SEGMENTS = frozenset({"login", "signin", "sign-in", "log-in"})


def classify_page_capture(http_status: int, final_url: str) -> str:
    """Describe the response without equating HTTP 2xx with requested content.

    Login detection is deliberately limited to explicit final-URL path segments.
    A non-login URL does not prove authentication, science access, or content
    correctness. No body is changed and no extra request is made.
    """
    if not 200 <= http_status < 300:
        return "HTTP_NON_2XX_CAPTURE"
    try:
        segments = unquote(urlsplit(final_url).path).casefold().split("/")
    except ValueError:
        return "HTTP_2XX_ACCESS_UNVERIFIED"
    if any(segment in _LOGIN_SEGMENTS for segment in segments):
        return "LOGIN_PAGE_CAPTURE"
    return "HTTP_2XX_ACCESS_UNVERIFIED"


def summarize_page_captures(
    results: Iterable[Mapping[str, Any]],
    *,
    transport_error_count: int = 0,
) -> dict[str, int]:
    """Count HTTP outcomes separately from login captures and transport errors."""
    counts = {
        "captured_response_count": 0,
        "http_2xx_count": 0,
        "http_non_2xx_count": 0,
        "login_page_capture_count": 0,
        "non_login_http_2xx_count": 0,
        "transport_error_count": transport_error_count,
    }
    for result in results:
        counts["captured_response_count"] += 1
        state = classify_page_capture(result["http_status"], result["final_url"])
        if state == "HTTP_NON_2XX_CAPTURE":
            counts["http_non_2xx_count"] += 1
        else:
            counts["http_2xx_count"] += 1
            if state == "LOGIN_PAGE_CAPTURE":
                counts["login_page_capture_count"] += 1
            else:
                counts["non_login_http_2xx_count"] += 1
    return counts
