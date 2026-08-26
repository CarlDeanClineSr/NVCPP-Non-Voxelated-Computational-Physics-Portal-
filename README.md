# NVCPP — Non-Voxelated Computational Physics Portal

NVCPP is an evidence-preserving telemetry and cross-mission analysis system. It
keeps native source bytes, mission contracts, quality exclusions, transformation
rules, and output hashes beside every result.

## Current implementation status

- DSCOVR MAG historical ingestion: canonical one-minute GSE product.
- SOLAR-1 MAG historical ingestion: canonical one-minute GSE product.
- `CLINE-L1-B24M-TRAIL-v1`: prior-only 24-hour median with a 95% coverage gate.
- MAG-to-MAG comparison: exact UTC overlap, no interpolation, lag uncertainty and
  look-elsewhere controls.
- SOLAR-1 SWiPS: discovery only; plasma physics remains disabled until a public
  product and schema are verified.
- Roman, JWST, and HST: separate observatory domain; never routed through L1
  plasma equations.

## Non-negotiable rules

- No clipping, capping, winsorizing, saturation, or hidden denominator floor.
- No generic `chi` column in the clean path.
- `ratio_B24M`, signed `delta_B24M`, and absolute `chi_B24M` remain separate.
- Baselines use only prior samples.
- Source identity, units, frame, cadence, schema, and quality policy must pass
  before physics.
- Missing and suspect records are preserved in quarantine artifacts.
- Correlation is evidence of similarity, not proof of propagation or mechanism.
- GitHub stores code and compact audit records; heavy run products belong in
  immutable Actions/Drive artifacts.

## Canonical observable

```text
B0(t) = median{B(τ): t - 24 h < τ < t}
ratio_B24M = B / B0
delta_B24M = (B - B0) / B0
chi_B24M = abs(delta_B24M)
```

A row is valid only after a full 24-hour warm-up, at least 95% declared-cadence
coverage, and a finite positive baseline. Invalid baselines are named; they are
not repaired silently.

## Repository layout

```text
core/        canonical baseline math and cross-mission coherence analysis
historical/  NASA CDAWeb mission adapters
sources/     NOAA/NCEI and other source adapters
config/      one authoritative source contract per product
tests/       offline unit and integrity tests
docs/        audit decisions and operating protocols
.github/     CI, regression, discovery, and coherence workflows
```

## Install and validate

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m sources.solar1.validate_contract \
  config/solar1_mag_contract.v1.json
python -m pytest -q
```

## SOLAR-1 MAG regression

```bash
python -m sources.solar1.download_solar1 \
  --run solar1_regression_june_2026 \
  --start 2026-06-01T00:00:00.000Z \
  --analysis-start 2026-06-02T00:00:00.000Z \
  --end 2026-06-05T00:00:00.000Z \
  --outdir runs/historical
```

## DSCOVR historical run

```bash
python -m historical.download_dscovr_cdaweb \
  --run dscovr_gannon_may_2024 \
  --start 2024-05-09T00:00:00.000Z \
  --analysis-start 2024-05-10T00:00:00.000Z \
  --end 2024-05-13T00:00:00.000Z \
  --outdir runs/historical
```

DSCOVR native components are averaged by minute first; vector magnitude and the
24-hour baseline are then calculated from the canonical one-minute product. The
pipeline never averages an already-derived chi value and calls it canonical.

## L1 MAG coherence audit

```bash
python -m core.temporal_pairing \
  --dscovr runs/historical/dscovr_overlap/cline_l1_rows.csv \
  --solar1 runs/historical/solar1_overlap/solar1_cline_l1_rows.csv \
  --dscovr-manifest runs/historical/dscovr_overlap/dscovr_run_manifest.json \
  --solar1-manifest runs/historical/solar1_overlap/solar1_run_manifest.json \
  --outdir runs/pairing
```

The pairing engine uses exact one-minute inner joins with no forward fill. It
reports zero-lag correlation, a lag scan, peak width, day-by-day stability,
moving-block lag uncertainty, and a circular-shift null that repeats the same
lag search. Results are classified conservatively:

```text
NO_STABLE_COHERENCE
COHERENT_BUT_LAG_UNRESOLVED
LAG_CANDIDATE_REQUIRES_EPHEMERIS
```

## SWiPS discovery

Two separate probes are maintained:

```bash
python -m sources.solar1.download_swips_discovery
python -m sources.solar1.swips_archive_discovery_v2
```

The first checks NOAA SPOT/HAPI. The second checks the documented
`/satellite-spaceweather` archive bucket with ListObjectsV2 pagination and raw XML
hashes. Neither enables plasma calculations.

## Provenance

Every successful or failed mission run should preserve:

```text
source URL and raw SHA-256
contract and schema fingerprint
Git commit and Actions run identity
requested and returned intervals
quarantine reasons and hashes
cadence/gap/duplicate statistics
baseline status counts
output inventory and SHA-256
```

Git commit objects provide repository integrity. Static whole-tree checksum files
are not maintained because they become stale on every commit.
