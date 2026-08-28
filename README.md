# NVCPP — Non-Voxelated Computational Physics Portal

NVCPP is an evidence-preserving telemetry, observatory-readiness, and cross-mission
analysis system. It keeps native source bytes, mission contracts, quality
exclusions, transformation rules, uncertainty limits, and output hashes beside
every result.

## Current implementation status

- Hourly observatory: scheduled solar-wind acquisition, physics evaluation,
  candidate-event detection, charts, capsules, status, and immutable run packages.
- NOAA SWPC operational L1 feed: near-real-time magnetic and plasma context,
  explicitly labeled provider-selected rather than mission-specific.
- NOAA operational rolling state: a bounded 36-hour cache is restored across
  hourly GitHub runners so the frozen 24-hour baseline is not shortened when the
  live provider response alone is too brief. Provider revisions are counted.
- Google Drive boundary: credentials and destination permissions are checked by
  a non-writing preflight before telemetry. Telemetry and temporary GitHub
  artifacts still run when durable storage is unavailable.
- DSCOVR MAG historical ingestion: canonical one-minute GSE product.
- SOLAR-1 MAG historical and hourly ingestion: canonical one-minute GSE product.
- `CLINE-L1-B24M-TRAIL-v1`: prior-only 24-hour median with a 95% coverage gate.
- MAG-to-MAG comparison: exact UTC overlap, no interpolation, lag uncertainty and
  look-elsewhere controls.
- SOLAR-1 SWiPS: discovery only; plasma physics remains disabled until a public
  product and schema are verified.
- Roman readiness: six-hour public MAST/archive watch, bounded metadata queries,
  deterministic image fixtures, truth-recovery scoring, and authenticated export
  intake. Roman is never routed through L1 plasma equations.
- JWST and HST: reserved for the separate astronomical-observatory domain; live
  mission adapters are not yet implemented in this repository.

## Non-negotiable rules

- No clipping, capping, winsorizing, saturation replacement, or hidden denominator
  floor.
- No generic `chi` column in the clean path.
- `ratio_B24M`, signed `delta_B24M`, and absolute `chi_B24M` remain separate.
- Baselines use only prior samples.
- Source identity, units, frame, cadence, schema, and quality policy must pass
  before physics.
- Missing and suspect records are preserved in quarantine artifacts.
- Correlation is evidence of similarity, not proof of propagation or mechanism.
- Telescope arrays are never labeled `chi_B24M` and never enter L1 plasma
  equations.
- A scheduled launch time, changed webpage hash, or archive registration is not
  automatically evidence of launch success, commissioning, or calibrated science
  readiness.
- GitHub stores code and compact audit records; heavy run products belong in
  immutable Actions/Drive artifacts or mission cloud environments.

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
core/        canonical baseline math, event detection, and coherence analysis
historical/  NASA CDAWeb historical acquisition
sources/     NOAA/NCEI, NOAA/SWPC, and mission-specific adapters
observatory/ hourly orchestration plus separate Roman readiness/image analysis
pipelines/   command routing, Drive preflight, and create-only publication
config/      one authoritative source/observatory contract per product
capsules/    compact evidence-first lessons when committed deliberately
tests/       offline unit, integration, contract, security, and integrity tests
tools/       repository security and maintenance helpers
docs/        audit decisions, operating protocols, and unresolved test triggers
.github/     CI, hourly, regression, discovery, coherence, and Roman workflows
```

## Install and validate

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m compileall -q core historical observatory pipelines sources tests tools
python tools/repository_security_scan.py
python -m sources.solar1.validate_contract \
  config/solar1_mag_contract.v1.json
python -m json.tool config/hourly_observatory.v1.json >/dev/null
python -c "from pathlib import Path; from observatory.roman.contracts import load_contract; load_contract(Path('config/roman_prelaunch.v1.json'))"
python -m pytest -q
```

## Hourly observatory

The scheduled workflow runs at minute 17 of every UTC hour. It restores the
bounded NOAA operational cache, retrieves the newest provider records, preserves
provider revisions, and keeps only the configured retention window. Sources that
can supply the complete requested interval are queried directly. The frozen
24-hour warm-up and coverage requirements are never reduced to manufacture a
current result.

The orchestrator evaluates the latest six hours and focuses candidate detection
on the most recent hour.

Manual local run:

```bash
python -m observatory.run_hourly \
  --config config/hourly_observatory.v1.json \
  --outdir runs/hourly
```

Each immutable run can preserve:

```text
raw provider responses
canonical magnetic/plasma rows
rolling-state input and revision counts
quarantine records and reason codes
run and source manifests
signed-departure, magnitude, vector, and plasma charts
candidate-event JSON/CSV
teaching capsules
latest status JSON/Markdown
result index
sanitized Drive preflight result
Drive publication or dry-run receipt
```

The NOAA operational source is intentionally labeled as a provider-selected L1
feed. It can catch current events and compute plasma context, but it is not
counted as an independently identified DSCOVR, ACE, IMAP, or SOLAR-1 measurement
unless source identity is resolved separately.

### Drive preflight and durable publication

The workflow checks Google authentication and destination-folder capabilities
before telemetry without writing to Drive. An absent credential enters safe
`NOT_CONFIGURED`/dry-run mode. A configured but invalid credential is reported
clearly; telemetry is still processed and preserved in the temporary GitHub
artifact, while the workflow remains red so the storage failure is visible.

Manual no-write preflight:

```bash
python -m pipelines.drive_preflight \
  --output drive_preflight_result.json
```

See:

- `docs/HOURLY_OBSERVATORY.md`
- `docs/TEACHING_ENGINE.md`
- `docs/DRIVE_PREFLIGHT.md`
- `docs/DRIVE_VAULT_SETUP.md`

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

## Roman prelaunch, archive readiness, and truth recovery

Roman is monitored through a separate astronomical-observatory path. The GitHub
workflow runs every six hours at minute 37 UTC and can also be dispatched
manually.

```bash
python -m observatory.roman.prelaunch_probe \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/readiness
```

The readiness watch:

```text
queries the public MAST mission list
performs bounded CAOM counts for configured Roman collection candidates
samples only bounded metadata when rows exist
hashes official NASA/STScI page responses
builds a deterministic Roman-like SCI/ERR/DQ fixture
scores detections against known injected truth
```

The truth benchmark records:

```text
injected, detected, and matched source counts
completeness and purity
false positives
centroid-error distribution
unmatched truth and detections
cosmic-ray leakage
detection catalog, match table, benchmark JSON, and overlay chart
```

A green fixture run proves only that the current NVCPP detector behaves
reproducibly on a known synthetic scene. It is not Roman flight data, Roman I-Sim
performance, launch evidence, or a mission-performance claim.

When the scheduled launch clock passes without separately frozen mission-status
evidence, the watcher uses:

```text
SCHEDULED_LAUNCH_WINDOW_UNVERIFIED
```

It does not infer launch or commissioning success from time alone.

Authenticated Nexus or Roman I-Sim exports can be inventoried with:

```bash
python -m observatory.roman.intake \
  --input /path/to/export \
  --source-class ROMAN_RESEARCH_NEXUS_EXPORT \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/intake \
  --copy-small-files
```

The intake hashes original bytes, identifies supported container signatures, and
refuses to claim detailed Roman schema validation until a separate pinned Roman
reader/calibration environment exists.

Roman products are never routed through L1 plasma equations and are never labeled
`chi_B24M`.

See:

- `docs/ROMAN_PRELAUNCH_READINESS.md`
- `docs/ROMAN_UNTHOUGHT_OFS.md`

## Explicitly not enabled yet

The following are planned or discovery-stage domains, not current production
science paths:

```text
verified SOLAR-1 SWiPS plasma ingestion
IMAP mission-specific canonical ingestion
JWST and HST archive adapters
Roman flight-product calibration and science claims
nearby-star evidence/life-target registry and navigator bridge
```

## Provenance

Every successful or failed mission run should preserve:

```text
source URL and raw SHA-256
contract and schema fingerprint
Git commit and Actions run identity
requested and returned intervals
rolling-state revision counts when applicable
quarantine reasons and hashes
cadence/gap/duplicate statistics
baseline status counts
Drive preflight/publication state
truth-recovery catalogs and benchmark metrics when applicable
output inventory and SHA-256
```

Git commit objects provide repository integrity. Static whole-tree checksum files
are not maintained because they become stale on every commit.
