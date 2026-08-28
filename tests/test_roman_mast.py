import json

import pytest

from observatory.roman.contracts import validate_contract
from observatory.roman.mast_client import (
    MastClient,
    MastError,
    classify_archive_state,
    extract_count,
    probe_page,
    roman_registered,
)


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        status_code=200,
        url="https://mast.stsci.edu/api/v0/invoke",
        content_type="application/json",
    ):
        if isinstance(payload, bytes):
            self.content = payload
        elif isinstance(payload, str):
            self.content = payload.encode("utf-8")
        else:
            self.content = json.dumps(payload).encode("utf-8")
        self.status_code = status_code
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"


class FakeSession:
    def __init__(self, *, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self.posts:
            raise AssertionError("unexpected POST")
        return self.posts.pop(0)

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.gets:
            raise AssertionError("unexpected GET")
        return self.gets.pop(0)


def test_list_missions_and_count_are_parsed_without_field_position_assumptions():
    session = FakeSession(
        posts=[
            FakeResponse(
                {
                    "status": "COMPLETE",
                    "data": [
                        {"distinctValue": "HST"},
                        {"distinctValue": "JWST"},
                        {"distinctValue": "HST"},
                    ],
                }
            ),
            FakeResponse(
                {
                    "status": "COMPLETE",
                    "data": [{"unexpected_count_label": "17"}],
                }
            ),
        ]
    )
    client = MastClient(session=session)
    missions, mission_response = client.list_missions()
    count, count_response = client.count_collection("Roman")

    assert missions == ["HST", "JWST"]
    assert count == 17
    assert mission_response.raw_sha256
    assert count_response.request["service"] == "Mast.Caom.Filtered"
    assert "request=" in session.post_calls[0][1]["data"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": []}, 0),
        ({"data": [{"COUNT_BIG(*)": 3}]}, 3),
        ({"data": [{"Column1": "1,234"}]}, 1234),
        ({"data": [{"x": 9.0}]}, 9),
    ],
)
def test_extract_count(payload, expected):
    assert extract_count(payload) == expected


def test_extract_count_rejects_non_numeric_rows():
    with pytest.raises(MastError):
        extract_count({"data": [{"value": "not-a-count"}]})


def test_archive_state_is_conservative():
    assert (
        classify_archive_state(
            missions=["HST", "JWST"],
            collection_counts={"Roman": 0, "ROMAN": 0},
        )
        == "PRELAUNCH_NO_ROMAN_CAOM_HOLDINGS"
    )
    assert roman_registered(["Nancy Grace Roman Space Telescope"])
    assert (
        classify_archive_state(
            missions=["Roman"],
            collection_counts={"Roman": 0},
        )
        == "ROMAN_REGISTERED_NO_MATCHING_ROWS"
    )
    assert (
        classify_archive_state(
            missions=[],
            collection_counts={"Roman": 2},
        )
        == "ROMAN_CAOM_HOLDINGS_AVAILABLE"
    )


def test_page_probe_records_hash_and_title_without_interpreting_science():
    session = FakeSession(
        gets=[
            FakeResponse(
                "<html><head><title>Roman Test Page</title></head><body>ready</body></html>",
                url="https://example.test/roman",
                content_type="text/html; charset=utf-8",
            )
        ]
    )
    result = probe_page("https://example.test/roman", session=session)
    assert result.title == "Roman Test Page"
    assert result.http_status == 200
    assert len(result.raw_sha256) == 64


def test_contract_forbids_plasma_relabeling():
    contract = {
        "status": "PRELAUNCH_READINESS",
        "mission": "ROMAN",
        "domain": "ASTRONOMICAL_OBSERVATORY",
        "launch_utc": "2026-08-30T11:26:00Z",
        "physics": {
            "l1_plasma_physics_allowed": True,
            "chi_B24M_allowed": False,
            "science_claims_enabled": False,
        },
        "mast": {
            "invoke_url": "https://mast.stsci.edu/api/v0/invoke",
            "candidate_obs_collections": ["Roman"],
            "sample_row_limit": 25,
        },
        "official_pages": [{"name": "MAST", "url": "https://archive.stsci.edu"}],
        "nexus": {
            "access": "MYST_AUTHENTICATED",
            "automated_public_scrape": False,
        },
        "synthetic_fixture": {
            "shape": [64, 64],
            "seed": 1,
            "source_count": 1,
        },
    }
    errors = validate_contract(contract)
    assert any("L1 plasma" in error for error in errors)
