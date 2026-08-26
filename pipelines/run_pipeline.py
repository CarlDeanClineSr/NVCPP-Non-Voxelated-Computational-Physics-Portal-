#!/usr/bin/env python3
"""NVCPP command router for audited, implemented pipelines only.

The router contains no duplicate physics. It delegates to the same mission
runners used by GitHub Actions and rejects unknown or unsupported targets.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.temporal_pairing import run_pairing_engine
from historical.download_dscovr_cdaweb import run_pipeline as run_dscovr
from sources.solar1.download_solar1 import run_solar1_pipeline


DSCOVR_EPOCHS = {
    "gannon_may_2024_dscovr_mag_only": {
        "start": "2024-05-09T00:00:00.000Z",
        "analysis_start": "2024-05-10T00:00:00.000Z",
        "end": "2024-05-13T00:00:00.000Z",
    },
    "dscovr_overlap_june_2026": {
        "start": "2026-06-01T00:00:00.000Z",
        "analysis_start": "2026-06-02T00:00:00.000Z",
        "end": "2026-06-05T00:00:00.000Z",
    },
}

SOLAR1_EPOCHS = {
    "solar1_regression_june_2026": {
        "start": "2026-06-01T00:00:00.000Z",
        "analysis_start": "2026-06-02T00:00:00.000Z",
        "end": "2026-06-05T00:00:00.000Z",
    }
}


def _epoch(
    table: dict[str, dict[str, str]],
    run_name: str,
    start: str | None,
    analysis_start: str | None,
    end: str | None,
) -> tuple[str, str, str]:
    supplied = (start, analysis_start, end)
    if any(value is not None for value in supplied):
        if not all(value is not None for value in supplied):
            raise SystemExit(
                "custom intervals require --start, --analysis-start, and --end together"
            )
        return start, analysis_start, end  # type: ignore[return-value]
    if run_name not in table:
        raise SystemExit(
            f"unknown run {run_name!r}; available frozen runs: {sorted(table)}"
        )
    epoch = table[run_name]
    return epoch["start"], epoch["analysis_start"], epoch["end"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NVCPP audited pipeline router")
    subparsers = parser.add_subparsers(dest="pipeline", required=True)

    for name in ("dscovr-historical", "solar1-historical"):
        command = subparsers.add_parser(name)
        command.add_argument("--run", required=True)
        command.add_argument("--start")
        command.add_argument("--analysis-start")
        command.add_argument("--end")
        command.add_argument("--outdir", type=Path, default=Path("runs/historical"))
    subparsers.choices["solar1-historical"].add_argument(
        "--contract",
        type=Path,
        default=Path("config/solar1_mag_contract.v1.json"),
    )

    pair = subparsers.add_parser("pair-mag")
    pair.add_argument("--dscovr", type=Path, required=True)
    pair.add_argument("--solar1", type=Path, required=True)
    pair.add_argument("--dscovr-manifest", type=Path, required=True)
    pair.add_argument("--solar1-manifest", type=Path, required=True)
    pair.add_argument("--outdir", type=Path, default=Path("runs/pairing"))
    pair.add_argument("--max-lag", type=int, default=60)
    pair.add_argument("--bootstrap-iterations", type=int, default=300)
    pair.add_argument("--null-iterations", type=int, default=300)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.pipeline == "dscovr-historical":
        start, analysis_start, end = _epoch(
            DSCOVR_EPOCHS,
            args.run,
            args.start,
            args.analysis_start,
            args.end,
        )
        run_dscovr(args.run, start, analysis_start, end, args.outdir)
        return

    if args.pipeline == "solar1-historical":
        start, analysis_start, end = _epoch(
            SOLAR1_EPOCHS,
            args.run,
            args.start,
            args.analysis_start,
            args.end,
        )
        run_solar1_pipeline(
            args.run,
            start,
            analysis_start,
            end,
            args.outdir,
            args.contract,
        )
        return

    if args.pipeline == "pair-mag":
        manifest = run_pairing_engine(
            args.dscovr,
            args.solar1,
            args.dscovr_manifest,
            args.solar1_manifest,
            args.outdir,
            max_lag=args.max_lag,
            bootstrap_iterations=args.bootstrap_iterations,
            null_iterations=args.null_iterations,
        )
        print(manifest["interpretation"])
        return

    raise SystemExit(f"unsupported pipeline: {args.pipeline}")


if __name__ == "__main__":
    main()
