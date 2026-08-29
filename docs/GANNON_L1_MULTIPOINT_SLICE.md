# Gannon 10:59 UTC L1 Multipoint Slice

## Purpose

This audit tests whether the unresolved DSCOVR GSE vector transition selected by
the Gannon event-reference analysis has threshold-level magnetic counterparts in
ACE and Wind. It is deliberately narrower than a discontinuity classifier.

The reference is the automatically selected DSCOVR row near
`2024-05-11T10:59:00Z`, where the live rolling-median chi had fallen below its
research-watch level while the frozen pre-event departure remained large.

## Frozen boundaries

- NASA CDAWeb HAPI products and parameter names are declared in
  `config/gannon_l1_multipoint_slice.v1.json`.
- Every HAPI information response and CSV response is preserved and hashed.
- Magnetic components are averaged component-by-component into exact UTC
  one-minute rows before magnitude and rotation are calculated.
- ACE and Wind are searched independently inside a declared ±15-minute window.
- No spacecraft-propagation correction, interpolation, or forward fill is used.
- A support gate requires either a previous-minute vector rotation of at least
  45 degrees or a magnitude change of at least 0.25.
- A support gate is evidence of a spacecraft-local MAG transition, not proof
  that two spacecraft crossed the same surface.

## Plasma quantities

When the frozen source contract supplies density, speed or a GSE velocity
vector, and proton temperature or a declared thermal-speed proxy, the audit
reports:

- proton density;
- solar-wind speed;
- proton temperature;
- proton beta;
- dynamic pressure;
- Alfven speed and Alfven Mach number;
- three-minute pre/post medians.

A Wind thermal-speed conversion is kept explicit in the manifest. It is never
represented as a provider-supplied temperature.

## Interpretation states

```text
THREE_SPACECRAFT_MAG_SUPPORTED
TWO_SPACECRAFT_MAG_SUPPORTED
DSCOVR_ONLY_IN_BOUNDED_WINDOW
```

These names describe whether threshold-level MAG transitions were found inside
the declared windows. They do not classify a shock, current sheet, rotational
discontinuity, flux-rope edge, propagation speed, or geoeffectiveness.

## Run

The workflow entry point is `NVCPP Gannon L1 Three-Spacecraft Slice`.

A local rerun is:

```bash
python -m historical.gannon_l1_multipoint_slice \
  --config config/gannon_l1_multipoint_slice.v1.json \
  --dscovr-event-csv runs/historical/gannon_may_2024_dscovr_mag_only/cline_l1_rows.csv \
  --outdir runs/audits/gannon_l1_multipoint
```

## Evidence products

```text
raw/                                  exact NASA HAPI info and CSV bytes
canonical/                            source-specific one-minute products
three_spacecraft_candidate_summary.csv
three_spacecraft_search_windows.csv
exact_1059_utc_snapshot.csv
THREE_SPACECRAFT_SLICE_REPORT.md
three_spacecraft_slice_manifest.json
```
