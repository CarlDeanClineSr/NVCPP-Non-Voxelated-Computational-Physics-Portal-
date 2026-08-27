"""Deterministic UTC windows for scheduled NVCPP observatory runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd


@dataclass(frozen=True)
class ObservatoryWindow:
    retrieval_start: pd.Timestamp
    analysis_start: pd.Timestamp
    analysis_end: pd.Timestamp
    focus_start: pd.Timestamp

    def as_dict(self) -> dict[str, str]:
        return {
            "retrieval_start": self.retrieval_start.isoformat(),
            "analysis_start": self.analysis_start.isoformat(),
            "analysis_end": self.analysis_end.isoformat(),
            "focus_start": self.focus_start.isoformat(),
        }


def _utc_timestamp(value: datetime | str | pd.Timestamp | None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp(datetime.now(timezone.utc))
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def build_hourly_window(
    *,
    now: datetime | str | pd.Timestamp | None = None,
    safety_lag_minutes: int = 20,
    retrieval_hours: int = 30,
    analysis_hours: int = 6,
    focus_minutes: int = 60,
) -> ObservatoryWindow:
    """Return a complete-hour window with enough prior data for the 24-hour baseline.

    The analysis end is the latest whole UTC hour that was complete before the
    configured provider safety lag. The retrieval window must be longer than
    the 24-hour baseline plus the requested analysis span.
    """

    if safety_lag_minutes < 0:
        raise ValueError("safety_lag_minutes must be nonnegative")
    if analysis_hours <= 0:
        raise ValueError("analysis_hours must be positive")
    if focus_minutes <= 0:
        raise ValueError("focus_minutes must be positive")
    if retrieval_hours < 24 + analysis_hours:
        raise ValueError(
            "retrieval_hours must contain the 24-hour baseline plus analysis_hours"
        )

    current = _utc_timestamp(now)
    end = (current - pd.Timedelta(minutes=safety_lag_minutes)).floor("h")
    analysis_start = end - pd.Timedelta(hours=analysis_hours)
    retrieval_start = end - pd.Timedelta(hours=retrieval_hours)
    focus_start = max(analysis_start, end - pd.Timedelta(minutes=focus_minutes))
    if not retrieval_start < analysis_start < end:
        raise ValueError("computed observatory window is not strictly ordered")
    return ObservatoryWindow(
        retrieval_start=retrieval_start,
        analysis_start=analysis_start,
        analysis_end=end,
        focus_start=focus_start,
    )
