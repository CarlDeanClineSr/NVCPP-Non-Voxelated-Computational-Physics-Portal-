"""NOAA SWPC operational real-time solar-wind source.

This source is intentionally identified as an operational L1 composite. The
public endpoint can represent the provider-selected active upstream spacecraft;
it is not treated as a mission-specific DSCOVR measurement without an explicit
spacecraft identity field.
"""

from .download_realtime import run_noaa_realtime_pipeline

__all__ = ["run_noaa_realtime_pipeline"]
