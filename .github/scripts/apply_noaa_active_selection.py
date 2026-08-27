#!/usr/bin/env python3
"""Apply the audited NOAA provider-active selection patch exactly once."""

from pathlib import Path


source_path = Path("sources/noaa_swpc/download_realtime.py")
text = source_path.read_text(encoding="utf-8")

insertion_point = "\ndef _first_present(frame: pd.DataFrame, names: Iterable[str]) -> str | None:\n"
helper = '''

def _select_active_operational_rows(
    frame: pd.DataFrame,
    *,
    source_name: str,
) -> pd.DataFrame:
    """Select NOAA's provider-designated active upstream stream.

    Current RTSW products can include simultaneous rows from SOLAR1, IMAP,
    ACE, or another upstream spacecraft. Those are independent provider rows,
    not duplicate measurements to average together. When the ``active`` field
    exists, only rows explicitly designated active enter the operational
    canonical stream. All rows remain preserved in the raw response.
    """

    if "active" not in frame.columns:
        return frame.copy()

    def parse_active(value: Any) -> bool:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if pd.isna(value):
            raise NoaaRealtimeError(f"{source_name} contains a missing active flag")
        if isinstance(value, (int, np.integer, float, np.floating)):
            if float(value) in (0.0, 1.0):
                return bool(int(value))
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "active"}:
                return True
            if normalized in {"false", "0", "no", "inactive"}:
                return False
        raise NoaaRealtimeError(
            f"{source_name} contains an unrecognized active flag: {value!r}"
        )

    active_mask = frame["active"].map(parse_active)
    selected = frame.loc[active_mask].copy()
    if selected.empty:
        raise NoaaRealtimeError(
            f"{source_name} exposes an active field but designates no active rows"
        )
    return selected
'''
if text.count(insertion_point) != 1:
    raise SystemExit("could not locate _first_present insertion point exactly once")
text = text.replace(insertion_point, helper + insertion_point)

old_sanitize = '''        quarantine_records: list[pd.DataFrame] = []
        mag_all, coordinate_frame = _sanitize_magnetic(mag_raw, quarantine_records)
        plasma_all = _sanitize_plasma(plasma_raw, quarantine_records)
'''
new_sanitize = '''        quarantine_records: list[pd.DataFrame] = []
        mag_active = _select_active_operational_rows(
            mag_raw, source_name="NOAA SWPC magnetic"
        )
        plasma_active = _select_active_operational_rows(
            plasma_raw, source_name="NOAA SWPC plasma"
        )
        mag_all, coordinate_frame = _sanitize_magnetic(mag_active, quarantine_records)
        plasma_all = _sanitize_plasma(plasma_active, quarantine_records)
'''
if text.count(old_sanitize) != 1:
    raise SystemExit("could not locate sanitation block exactly once")
text = text.replace(old_sanitize, new_sanitize)

old_counts = '                    "source_identity_counts": _source_counts(mag_all),\n'
new_counts = '''                    "available_source_identity_counts": _source_counts(mag_raw),
                    "active_source_identity_counts": _source_counts(mag_all),
                    "source_identity_counts": _source_counts(mag_all),
'''
if text.count(old_counts) != 1:
    raise SystemExit("could not locate source identity count exactly once")
text = text.replace(old_counts, new_counts)
source_path.write_text(text, encoding="utf-8")

test_path = Path("tests/test_noaa_realtime.py")
tests = test_path.read_text(encoding="utf-8")
old_import = '''    _sanitize_plasma,
    _table,
)'''
new_import = '''    _sanitize_plasma,
    _select_active_operational_rows,
    _table,
)'''
if tests.count(old_import) != 1:
    raise SystemExit("could not locate NOAA test import exactly once")
tests = tests.replace(old_import, new_import)

marker = "def test_multi_spacecraft_operational_rows_use_provider_active_selection():"
if marker in tests:
    raise SystemExit("active-selection test already exists")
tests += '''


def test_multi_spacecraft_operational_rows_use_provider_active_selection():
    magnetic = [
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "SOLAR1",
            "active": True,
            "bx_gsm": 3.0,
            "by_gsm": 4.0,
            "bz_gsm": 0.0,
            "bt": 5.0,
        },
        {
            "time_tag": "2026-01-01T00:00:00Z",
            "source": "ACE",
            "active": False,
            "bx_gsm": 9.0,
            "by_gsm": 0.0,
            "bz_gsm": 0.0,
            "bt": 9.0,
        },
        {
            "time_tag": "2026-01-01T00:01:00Z",
            "source": "SOLAR1",
            "active": "true",
            "bx_gsm": 0.0,
            "by_gsm": 6.0,
            "bz_gsm": 8.0,
            "bt": 10.0,
        },
        {
            "time_tag": "2026-01-01T00:01:00Z",
            "source": "IMAP",
            "active": "false",
            "bx_gsm": 12.0,
            "by_gsm": 0.0,
            "bz_gsm": 0.0,
            "bt": 12.0,
        },
    ]
    raw = _table(magnetic, required=["time_tag"], source_name="mag")
    selected = _select_active_operational_rows(raw, source_name="mag")
    assert selected["source"].tolist() == ["SOLAR1", "SOLAR1"]
    assert selected["time_tag"].is_unique

    quarantine = []
    clean, coordinate_frame = _sanitize_magnetic(selected, quarantine)
    assert coordinate_frame == "GSM"
    assert clean["B_mag"].tolist() == pytest.approx([5.0, 10.0])
    assert quarantine == []
'''
test_path.write_text(tests, encoding="utf-8")
