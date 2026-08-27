"""Deterministic chart generation for NVCPP run packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class ChartError(RuntimeError):
    pass


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _event_spans(axis: Any, events: list[dict[str, Any]]) -> None:
    for event in events:
        start = pd.Timestamp(event["start_utc"])
        end = pd.Timestamp(event["end_utc"]) + pd.Timedelta(minutes=1)
        axis.axvspan(start, end, alpha=0.12)


def _finish(plt: Any, figure: Any, axis: Any, path: Path) -> None:
    axis.grid(True, alpha=0.25)
    figure.autofmt_xdate()
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_magnetic_magnitude(
    frame: pd.DataFrame,
    *,
    time_col: str,
    magnitude_col: str,
    baseline_col: str,
    mission: str,
    events: list[dict[str, Any]],
    path: Path,
) -> Path:
    plt = _plt()
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(frame[time_col], frame[magnitude_col], label="B magnitude")
    if baseline_col in frame.columns:
        axis.plot(frame[time_col], frame[baseline_col], label="Prior 24-hour median B0")
    _event_spans(axis, events)
    axis.set_title(f"{mission} magnetic magnitude")
    axis.set_xlabel("UTC")
    axis.set_ylabel("nT")
    axis.legend()
    _finish(plt, figure, axis, path)
    return path


def plot_signed_departure(
    frame: pd.DataFrame,
    *,
    time_col: str,
    delta_col: str,
    chi_col: str,
    mission: str,
    events: list[dict[str, Any]],
    research_watch_chi: float,
    path: Path,
) -> Path:
    plt = _plt()
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(frame[time_col], frame[delta_col], label="Signed delta_B24M")
    axis.plot(frame[time_col], frame[chi_col], label="Absolute chi_B24M", alpha=0.75)
    axis.axhline(research_watch_chi, linestyle="--", label="Research watch +chi")
    axis.axhline(-research_watch_chi, linestyle="--", label="Research watch -delta")
    _event_spans(axis, events)
    axis.set_title(f"{mission} signed and absolute baseline departure")
    axis.set_xlabel("UTC")
    axis.set_ylabel("Dimensionless")
    axis.legend()
    _finish(plt, figure, axis, path)
    return path


def plot_vector_components(
    frame: pd.DataFrame,
    *,
    time_col: str,
    component_columns: tuple[str, str, str],
    mission: str,
    events: list[dict[str, Any]],
    path: Path,
) -> Path:
    plt = _plt()
    figure, axis = plt.subplots(figsize=(12, 5))
    for column in component_columns:
        axis.plot(frame[time_col], frame[column], label=column)
    _event_spans(axis, events)
    axis.set_title(f"{mission} magnetic vector components")
    axis.set_xlabel("UTC")
    axis.set_ylabel("nT")
    axis.legend()
    _finish(plt, figure, axis, path)
    return path


def plot_plasma_state(
    frame: pd.DataFrame,
    *,
    time_col: str,
    mission: str,
    path: Path,
) -> Path | None:
    available = [
        column
        for column in ("speed", "density", "dynamic_pressure_nPa", "proton_beta", "alfven_mach")
        if column in frame.columns and pd.to_numeric(frame[column], errors="coerce").notna().any()
    ]
    if not available:
        return None
    plt = _plt()
    figure, axis = plt.subplots(figsize=(12, 5))
    for column in available:
        values = pd.to_numeric(frame[column], errors="coerce")
        finite = values.replace([np.inf, -np.inf], np.nan)
        if finite.notna().any():
            scale = finite.abs().median()
            plotted = finite / scale if np.isfinite(scale) and scale > 0 else finite
            label = f"{column} / median" if np.isfinite(scale) and scale > 0 else column
            axis.plot(frame[time_col], plotted, label=label)
    axis.set_title(f"{mission} operational plasma-state context")
    axis.set_xlabel("UTC")
    axis.set_ylabel("Normalized context")
    axis.legend()
    _finish(plt, figure, axis, path)
    return path


def generate_mission_charts(
    frame: pd.DataFrame,
    *,
    mission: str,
    time_col: str,
    magnitude_col: str,
    baseline_col: str,
    delta_col: str,
    chi_col: str,
    components: tuple[str, str, str],
    events: list[dict[str, Any]],
    research_watch_chi: float,
    outdir: Path,
) -> list[Path]:
    if frame.empty:
        raise ChartError("cannot chart an empty mission frame")
    outdir.mkdir(parents=True, exist_ok=True)
    paths = [
        plot_magnetic_magnitude(
            frame,
            time_col=time_col,
            magnitude_col=magnitude_col,
            baseline_col=baseline_col,
            mission=mission,
            events=events,
            path=outdir / "magnetic_magnitude.png",
        ),
        plot_signed_departure(
            frame,
            time_col=time_col,
            delta_col=delta_col,
            chi_col=chi_col,
            mission=mission,
            events=events,
            research_watch_chi=research_watch_chi,
            path=outdir / "signed_delta_and_chi.png",
        ),
        plot_vector_components(
            frame,
            time_col=time_col,
            component_columns=components,
            mission=mission,
            events=events,
            path=outdir / "vector_components.png",
        ),
    ]
    plasma = plot_plasma_state(
        frame,
        time_col=time_col,
        mission=mission,
        path=outdir / "plasma_state.png",
    )
    if plasma is not None:
        paths.append(plasma)
    return paths
