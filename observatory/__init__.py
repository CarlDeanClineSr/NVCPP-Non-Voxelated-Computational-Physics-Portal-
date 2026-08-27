"""NVCPP continuous observatory orchestration and reporting."""

from .time_windows import ObservatoryWindow, build_hourly_window

__all__ = ["ObservatoryWindow", "build_hourly_window"]
