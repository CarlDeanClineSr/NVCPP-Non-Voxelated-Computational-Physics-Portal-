import pytest

from sources.solar1.swips_archive_discovery_v2 import BUCKET_NAME, parse_s3_listing


def xml(prefix, *, keys=()):
    contents = "".join(
        f"<Contents><Key>{key}</Key><LastModified>2026-01-01T00:00:00Z</LastModified>"
        f"<ETag>\"abc\"</ETag><Size>12</Size></Contents>"
        for key in keys
    )
    return (
        f"<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\">"
        f"<Name>{BUCKET_NAME}</Name><Prefix>{prefix}</Prefix>"
        f"<KeyCount>{len(keys)}</KeyCount><MaxKeys>1000</MaxKeys>"
        f"<IsTruncated>false</IsTruncated>{contents}</ListBucketResult>"
    ).encode()


def test_empty_prefix_is_valid_negative_evidence():
    prefix = "SWFO/SOLAR-1/SWIPS/swips-l3/"
    parsed = parse_s3_listing(xml(prefix), prefix)
    assert parsed["bucket"] == BUCKET_NAME
    assert parsed["objects"] == []
    assert parsed["is_truncated"] is False


def test_object_listing_is_parsed():
    prefix = "SWFO/SOLAR-1/SWIPS/swips-l3/"
    parsed = parse_s3_listing(xml(prefix, keys=(prefix + "one.nc",)), prefix)
    assert parsed["objects"][0]["key"].endswith("one.nc")


def test_bucket_identity_mismatch_fails():
    prefix = "SWFO/SOLAR-1/SWIPS/swips-l3/"
    bad = xml(prefix).replace(BUCKET_NAME.encode(), b"wrong-bucket")
    with pytest.raises(ValueError, match="bucket identity mismatch"):
        parse_s3_listing(bad, prefix)
