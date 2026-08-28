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
    assert manifest["archive_state"] == "PRELAUNCH_NO_ROMAN_CAOM_HOLDINGS"
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
