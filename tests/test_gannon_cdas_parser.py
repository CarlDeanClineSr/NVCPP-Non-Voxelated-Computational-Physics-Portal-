from historical.gannon_multipoint_audit import parse_cdas_rows


def test_cdas_parser_starts_at_first_numeric_timestamp():
    raw = b"""# provider metadata
EPOCH                               B         BX_(GSE)         BY_(GSE)         BZ_(GSE)
                                      (@_x-component_) (@_y-component_) (@_z-component_)
dd-mm-yyyy hh:mm:ss.ms      nT_(3sec)        nT_(3sec)        nT_(3sec)        nT_(3sec)
11-05-2024 10:58:58.500       20.0000          1.00000          2.00000          3.00000
11-05-2024 10:59:01.500       30.0000         -1.00000         -2.00000         -3.00000
"""
    frame = parse_cdas_rows(
        raw,
        columns=[
            "time",
            "reported_B_nT",
            "bx_gse_nT",
            "by_gse_nT",
            "bz_gse_nT",
        ],
    )
    assert len(frame) == 2
    assert frame.loc[0, "time"].isoformat() == "2024-05-11T10:58:58.500000+00:00"
    assert frame.loc[1, "bz_gse_nT"] == -3.0
