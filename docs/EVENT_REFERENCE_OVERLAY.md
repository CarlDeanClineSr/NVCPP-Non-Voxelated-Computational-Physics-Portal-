# NVCPP Event-Reference Overlay

## Purpose

`chi_B24M` remains the canonical rolling observable. It answers:

> How different is the one-minute magnetic-field magnitude from its recent,
> prior-only 24-hour state?

A frozen event reference answers a different retrospective question:

> How different is the magnetic-field magnitude from the last valid pre-gate
> baseline?

The frozen product never replaces or overwrites the canonical rolling fields.

## Canonical magnetic quantity

For the current DSCOVR and SOLAR-1 MAG paths:

```text
B(t) = |<B_GSE>_1min|
```

For DSCOVR, the native GSE components are averaged independently within each
minute before vector magnitude is calculated. For SOLAR-1, the official
one-minute GSE component product is used.

The executable rolling interval is:

```text
[t - 24 h, t)
```

The sample exactly 24 hours earlier is included. The current sample is excluded.

## Automatic event gate

The first frozen-reference implementation uses the named gate:

```text
CHI_ABSB_GSE_1MIN_SEVERE_COMPRESSION
```

The gate requires:

```text
baseline_status == VALID
chi_B24M >= 1.0
delta_B24M > 0
```

`chi >= 1` means only that the canonical magnetic magnitude has departed from the
live trailing median by at least 100%. It is not, by itself, shock confirmation,
geoeffectiveness, southward Bz, or an ICME-phase declaration.

The frozen reference is derived, not supplied manually:

```text
last baseline_status=VALID row before the gate
frozen B = that row's live B0
```

## Separate output names

```text
event_gate_time_utc
event_reference_time_utc
event_reference_B
ratio_event_ref_absB
delta_event_ref_absB
chi_event_ref_absB
rotation_from_event_ref_degrees
rotation_from_previous_minute_degrees
minute_relative_magnitude_change
baseline_regime
```

## Baseline-regime labels

```text
PRE_EVENT
MEDIAN_ADAPTING
EVENT_ABSORBED_BY_LIVE_BASELINE
```

These are metric-state labels, not physical storm phases.

`EVENT_ABSORBED_BY_LIVE_BASELINE` means:

```text
live chi_B24M < research-watch threshold
and
frozen chi_event_ref_absB >= frozen-severe threshold
```

It records that the rolling median has adapted enough to suppress the event's
magnitude relative to the live baseline while the event remains far from the
pre-event reference.

## Later-structure selection

The Gannon audit selects a later structure deterministically after the first
live-baseline absorption row. A row must satisfy:

```text
live chi_B24M < 0.15
frozen chi_event_ref_absB >= 1.0
and either
  one-minute vector rotation >= 45 degrees
  or one-minute magnitude jump >= 0.25
```

If several rows qualify, the largest normalized rotation/jump score wins. The
selected row must also pass an exact centered local-integrity window.

## Coordinate-frame limit

The current DSCOVR historical product is GSE. The audit preserves `Bz_GSE` and
GSE vector rotation but does not relabel either value as a GSM clock angle.

## Gannon reproducibility

```bash
python -m historical.download_dscovr_cdaweb \
  --run dscovr_gannon_may_2024 \
  --start 2024-05-09T00:00:00.000Z \
  --analysis-start 2024-05-10T00:00:00.000Z \
  --end 2024-05-13T00:00:00.000Z \
  --outdir runs/historical

python -m historical.gannon_event_reference_audit \
  --input runs/historical/dscovr_gannon_may_2024/cline_l1_rows.csv \
  --manifest runs/historical/dscovr_gannon_may_2024/dscovr_run_manifest.json \
  --outdir runs/audits/gannon_event_reference
```
