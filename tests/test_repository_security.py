from tools.repository_security_scan import CREDENTIAL_PATTERNS, scan_repository


def test_repository_security_scan_passes():
    assert scan_repository() == []


def test_google_credential_shapes_are_detected_without_storing_credentials():
    examples = {
        "google_access_token": "ya29." + ("A" * 32),
        "google_refresh_token": "1" + "//" + ("B" * 32),
        "google_oauth_client_secret": "GOC" + "SPX-" + ("C" * 24),
        "private_key": "-----BEGIN " + "PRIVATE KEY-----",
    }
    for label, value in examples.items():
        assert CREDENTIAL_PATTERNS[label].search(value)


def test_placeholders_do_not_trigger_credential_patterns():
    placeholders = ["ya29....", "1//...", "GOCSPX-...", "<private-key>"]
    for pattern in CREDENTIAL_PATTERNS.values():
        assert all(pattern.search(value) is None for value in placeholders)
