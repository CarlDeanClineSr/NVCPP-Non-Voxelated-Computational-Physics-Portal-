import json

import numpy as np
import pytest

from observatory.roman.intake import (
    RomanIntakeError,
    build_intake_manifest,
    identify_container,
)


def write_contract(path):
    path.write_text(
        json.dumps(
            {
                "contract_version": "1.0.0",
                "status": "PRELAUNCH_READINESS",
                "mission": "ROMAN",
                "domain": "ASTRONOMICAL_OBSERVATORY",
                "launch_utc": "2026-08-30T11:26:00Z",
                "physics": {
                    "l1_plasma_physics_allowed": False,
                    "chi_B24M_allowed": False,
                    "science_claims_enabled": False,
                },
                "mast": {
                    "invoke_url": "https://mast.stsci.edu/api/v0/invoke",
                    "candidate_obs_collections": ["Roman"],
                    "sample_row_limit": 25,
                },
                "official_pages": [
                    {"name": "MAST", "url": "https://archive.stsci.edu"}
                ],
                "nexus": {
                    "url": "https://roman.science.stsci.edu/hub/",
                    "access": "MYST_AUTHENTICATED",
                    "automated_public_scrape": False,
                    "accepted_source_classes": [
                        "ROMAN_RESEARCH_NEXUS_EXPORT",
                        "NVCPP_SYNTHETIC_FIXTURE",
                    ],
                },
                "synthetic_fixture": {
                    "shape": [64, 64],
                    "seed": 1,
                    "source_count": 1,
                },
            }
        )
    )


def test_intake_inspects_npz_and_preserves_hash(tmp_path):
    config = tmp_path / "contract.json"
    write_contract(config)
    data = tmp_path / "fixture.npz"
    sci = np.full((8, 8), 1000.0)
    err = np.full((8, 8), 5.0)
    dq = np.zeros((8, 8), dtype=np.uint32)
    np.savez_compressed(data, SCI=sci, ERR=err, DQ=dq)

    manifest = build_intake_manifest(
        inputs=[data],
        source_class="NVCPP_SYNTHETIC_FIXTURE",
        config_path=config,
        outdir=tmp_path / "out",
        copy_files=True,
    )

    assert manifest["status"] == "SUCCESS"
    assert manifest["file_count"] == 1
    record = manifest["files"][0]
    assert record["container"] == "NPZ"
    assert len(record["sha256"]) == 64
    assert record["inspection"]["image_metrics"]["clipping_applied"] is False
    assert record["copy_status"] == "COPIED_AND_HASH_VERIFIED"


def test_intake_rejects_unapproved_source_class(tmp_path):
    config = tmp_path / "contract.json"
    write_contract(config)
    data = tmp_path / "x.json"
    data.write_text("{}")

    with pytest.raises(RomanIntakeError):
        build_intake_manifest(
            inputs=[data],
            source_class="INVENTED_SOURCE",
            config_path=config,
            outdir=tmp_path / "out",
        )


def test_container_signatures_override_suffix(tmp_path):
    asdf = tmp_path / "mystery.bin"
    asdf.write_bytes(b"#ASDF 1.0.0\nrest")
    fits = tmp_path / "other.data"
    fits.write_bytes(b"SIMPLE  =                    T")
    assert identify_container(asdf) == "ASDF"
    assert identify_container(fits) == "FITS"
