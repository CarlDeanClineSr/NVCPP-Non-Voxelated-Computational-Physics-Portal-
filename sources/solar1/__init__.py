"""NOAA SOLAR-1 source adapter package."""

from .download_ncei import NCEI_API_BASE, probe_solar1_mag

__all__ = ["NCEI_API_BASE", "probe_solar1_mag"]
