import json

from observatory.roman.prelaunch_probe import run_probe


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code=200,
        url="https://mast.stsci.edu/api/v0/invoke",
        content_type="application/json",
    ):
        if isinstance(payload, str):
            self.content = payload.encode()
        else:
            self.content = json.dumps(payload).encode()
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, posts, gets):
        self.posts = list(posts)
        self.gets = list(gets)

    def post(self, *_args, **_kwargs):
        return self.posts.pop(0)

    def get(self, url, *_args, **_kwargs):
        response = self.gets.pop(0)
        response.url = url
        return response


def _config():
    return {
        "contract_version": "1.1.0",
        "status": "PRELAUNCH_READINESS",
        "mission": "ROMAN",
        "official_name": "Nancy Grace Roman Space Telescope",
        "domain": "ASTRONOMICAL_OBSERVATORY",
        "launch_utc": "2026-08-30T11:26:00Z",
        "orbit_context": "SUN_EARTH_L2",
        "physics": {
            "l1_plasma_physics_allowed": False,
            "chi_B24M_allowed": False,
            "science_claims_enabled": False,
        },
        "mast": {
            "invoke_url": "https://mast.stsci.edu/api/v0/invoke",
            "candidate_obs_collections": ["Roman", "ROMAN"],
            "sample_row_limit": 10,
            "timeout_seconds": 1,
            "max_poll_seconds": 1,
        },
        "official_pages": [
            {"name": "NASA", "url": "https://example.test/nasa"},
            {"name": "MAST", "url": "https://example.test/mast"},
        ],
        "nexus": {
            "url": "https://roman.science.stsci.edu/hub/",
            "access": "MYST_AUTHENTICATED",
            "automated_public_scrape": False,
        },
        "synthetic_fixture": {
            "shape": [64, 64],
            "seed": 7,
            "source_count": 5,
            "detection_sigma": 5.0,
        },
        "truth_benchmark": {
            "detection_sigma": 5.0,
            "match_radius_pixels": 4.0,
            "minimum_component_pixels": 2,
            "use_limit": "Engineering readiness only; not Roman flight performance.",
        },
    }


def _session():
    return FakeSession(
        posts=[
            FakeResponse(
                {
                    "status": "COMPLETE",
                    "data": [
                        {"distinctValue": "HST"},
                        {"distinctValue": "JWST"},
                    ],
                }
            ),
            FakeResponse({"status": "COMPLETE", "data": [{"count": 0}]}),
            FakeResponse({"status": "COMPLETE", "data": [{"count": 0}]}),
        ],
        gets=[
            FakeResponse(
                "<html><title>NASA Roman</title></html>",
                content_type="text/html",
            ),
            FakeResponse(
                "<html><title>MAST Roman</title></html>",
                content_type="text/html",
            ),
        ],
    )


def test_prelaunch_probe_treats_zero_roman_rows_as_readiness_state(tmp_path):
    config_path = tmp_path / "roman.json"
    config_path.write_text(json.dumps(_config()))

    manifest = run_probe(
        config_path=config_path,
        outdir=tmp_path / "run",
        now_value="2026-08-28T12:00:00Z",
        session=_session(),
    )

    assert manifest["status"] == "READY"
    assert manifest["mission_phase"] == "PRELAUNCH"
    assert manifest["archive_state"] == "NO_MATCHING_ROMAN_CAOM_ROWS"
    assert manifest["flight_science_data_processed"] is False
    assert manifest["l1_plasma_physics_allowed"] is False
    assert manifest["synthetic_fixture"]["status"] == "SUCCESS"
    assert manifest["truth_recovery_benchmark"]["status"] == "RECORDED"
    assert 0.0 <= manifest["truth_recovery_benchmark"]["metrics"]["completeness"] <= 1.0
    assert (tmp_path / "run" / "roman_readiness_manifest.json").exists()
    assert (tmp_path / "run" / "reports" / "ROMAN_READINESS.md").exists()
    assert (
        tmp_path / "run" / "truth_benchmark" / "roman_truth_benchmark.json"
    ).exists()


def test_scheduled_launch_time_does_not_claim_launch_success(tmp_path):
    config_path = tmp_path / "roman.json"
    config_path.write_text(json.dumps(_config()))

    manifest = run_probe(
        config_path=config_path,
        outdir=tmp_path / "run_after_time",
        now_value="2026-08-30T12:00:00Z",
        session=_session(),
    )

    assert manifest["mission_phase"] == "SCHEDULED_LAUNCH_WINDOW_UNVERIFIED"
    assert manifest["flight_science_data_processed"] is False
    assert any(
        "does not establish that launch occurred" in item
        for item in manifest["interpretation_limits"]
    )


class RedirectSession(FakeSession):
    """Keep the simulated final URL instead of replacing it with the request."""

    def get(self, _url, *_args, **_kwargs):
        return self.gets.pop(0)


def test_captured_404_and_login_are_reported_without_changing_fixture(tmp_path, monkeypatch):
    import hashlib
    import requests

    def no_network(*_args, **_kwargs):
        raise AssertionError("reporting regression must remain offline")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    config = _config()
    config["official_pages"] = [
        {"name": name, "url": "https://example.test/" + name.lower()}
        for name in ["Countdown", "MAST", "Triplet", "Nexus", "ISim"]
    ]
    config_path = tmp_path / "roman.json"
    config_path.write_text(json.dumps(config))
    bodies = [
        "<html><title>Page not found</title>404 evidence</html>",
        "<html><title>MAST</title>metadata</html>",
        "<html><title>Triplet</title>ground test</html>",
        "<html><title>JupyterHub</title>login</html>",
        "<html><title>I-Sim</title>documentation</html>",
    ]
    gets = [
        FakeResponse(
            body,
            status_code=404 if i == 0 else 200,
            url="https://example.test/hub/login?next=%2Fhub%2F"
            if i == 3 else config["official_pages"][i]["url"],
            content_type="text/html",
        )
        for i, body in enumerate(bodies)
    ]
    session = RedirectSession(posts=_session().posts, gets=gets)
    run_dir = tmp_path / "partial"
    manifest = run_probe(
        config_path=config_path,
        outdir=run_dir,
        now_value="2026-09-10T06:52:23Z",
        session=session,
    )
    assert not session.posts and not session.gets
    assert manifest["manifest_version"] == "1.2.0"
    assert manifest["status"] == "PARTIAL"
    assert manifest["mission_phase"] == "POSTLAUNCH_STATUS_UNVERIFIED"
    assert manifest["archive_state"] == "NO_MATCHING_ROMAN_CAOM_ROWS"
    assert manifest["official_pages"]["errors"] == []  # no transport error
    assert manifest["official_pages"]["summary"] == {
        "captured_response_count": 5,
        "http_2xx_count": 4,
        "http_non_2xx_count": 1,
        "login_page_capture_count": 1,
        "non_login_http_2xx_count": 3,
        "transport_error_count": 0,
    }
    for row, body in zip(manifest["official_pages"]["results"], bodies):
        data = (run_dir / row["raw_path"]).read_bytes()
        assert data == body.encode()
        assert row["raw_sha256"] == hashlib.sha256(data).hexdigest()
    for row in manifest["artifact_inventory"]:
        data = (run_dir / row["path"]).read_bytes()
        assert len(data) == row["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
    report = (run_dir / "reports" / "ROMAN_READINESS.md").read_text()
    assert "Official page successes" not in report
    assert "Official responses captured:** 5" in report
    assert "HTTP 2xx responses:** 4" in report
    assert "HTTP non-2xx responses:** 1" in report
    assert "Login-page captures (within HTTP 2xx):** 1" in report

    # The same source-generation and detection settings still give the same math.
    config["official_pages"] = _config()["official_pages"]
    config_path.write_text(json.dumps(config))
    control = run_probe(
        config_path=config_path,
        outdir=tmp_path / "control",
        now_value="2026-09-10T06:52:23Z",
        session=_session(),
    )
    assert control["status"] == "READY"
    assert control["synthetic_fixture"]["metrics"]["array_sha256"] == (
        manifest["synthetic_fixture"]["metrics"]["array_sha256"]
    )
    assert control["truth_recovery_benchmark"]["metrics"] == (
        manifest["truth_recovery_benchmark"]["metrics"]
    )


def test_page_transport_error_is_separate_from_captured_http_error(tmp_path, monkeypatch):
    import requests

    def no_network(*_args, **_kwargs):
        raise AssertionError("reporting regression must remain offline")

    monkeypatch.setattr(requests.sessions.Session, "request", no_network)

    class OneMissingPageSession(FakeSession):
        def get(self, url, *_args, **_kwargs):
            if url.endswith("/nasa"):
                raise requests.ConnectionError("offline transport fixture")
            return super().get(url, *_args, **_kwargs)

    config_path = tmp_path / "roman.json"
    config_path.write_text(json.dumps(_config()))
    session = OneMissingPageSession(posts=_session().posts, gets=_session().gets[1:])
    manifest = run_probe(
        config_path=config_path,
        outdir=tmp_path / "run",
        now_value="2026-09-10T06:52:23Z",
        session=session,
    )
    assert manifest["status"] == "PARTIAL"
    counts = manifest["official_pages"]["summary"]
    assert counts["captured_response_count"] == 1
    assert counts["http_2xx_count"] == 1
    assert counts["http_non_2xx_count"] == 0
    assert counts["transport_error_count"] == 1
    assert len(manifest["official_pages"]["errors"]) == 1
