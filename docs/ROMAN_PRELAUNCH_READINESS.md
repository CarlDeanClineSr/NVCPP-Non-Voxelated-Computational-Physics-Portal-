# Roman Archive Readiness and Dated Mission Context

## Purpose

NVCPP has a separate Roman observatory-readiness path. It does not treat the
Nancy Grace Roman Space Telescope as an L1 solar-wind monitor and it does not
route detector products through `CLINE-L1-B24M-TRAIL-v1`.

The readiness path has five immediate jobs:

1. monitor the public MAST mission list and bounded Roman CAOM queries;
2. preserve exact hashes for official NASA/STScI readiness pages;
3. exercise a deterministic image-domain fixture through NVCPP's artifact,
   anomaly, and chart machinery;
4. score that fixture against its known injected truth;
5. define the intake boundary for Roman Research Nexus and Roman I-Sim exports.

## Launch schedule versus reported mission status

The frozen readiness contract records the scheduled launch time as:

```text
2026-08-30T11:26:00Z
```

The timestamp is operational metadata, not a guarantee. NASA remains the source
of truth for launch updates. Crossing the scheduled time changes NVCPP to:

```text
SCHEDULED_LAUNCH_WINDOW_UNVERIFIED
```

It does not declare launch, deployment, commissioning, or mission success from
the clock alone. An archive transition cannot establish those milestones either.

The latest reviewed mission-status reference is the [NASA report dated
October 22, 2026](https://science.nasa.gov/blogs/roman/2026/10/22/nasas-roman-space-telescope-enters-final-commissioning-phase/).
NASA reports that Roman entered its science commissioning phase after the
observatory and primary-instrument checkout. The report says the observatory can
guide, take exposures, slew between targets, and return data. The team is still
conducting planned test observations to characterize instrument response and
refine image quality before routine science operations.

`config/roman_prelaunch.v1.json` records this under `mission_status_reference`
with phase `COMMISSIONING_REPORTED`, source date `2026-10-22`, source URL, and an
interpretation limit. The September 15 report remains in `official_pages` as a
historical source capture; the October 22 report is the newer dated reference.
Neither report classifies a MAST catalog row as flight science data.

Manifest version 1.3.0 separates:

- `schedule_based_watch_state`: the unchanged clock-only calculation;
- `mission_phase_basis`: `SCHEDULE_ONLY` or `REVIEWED_OFFICIAL_REPORT`;
- `mission_status_reference`: the dated reviewed report, or null for a replay
  earlier than the report's calendar date;
- `archive_state`: MAST availability, independent of the mission report.

The phase is **reported as of the source date**, not a live spacecraft-status
measurement. Dates are evaluated at calendar-day precision. Advancing the clock,
capturing an HTTP 200 page, or finding MAST records cannot promote the reference
to L2 arrival or routine science operations. Later phases require a new reviewed
source. Commissioning context never classifies a catalog record as flight data
and never enables science claims. Legacy `prelaunch` filenames and the contract's
`PRELAUNCH_READINESS` safety-profile name are retained for compatibility; they
are not assertions about the spacecraft's current location.

## Public MAST path

The workflow uses the documented MAST Portal API endpoint:

```text
https://mast.stsci.edu/api/v0/invoke
```

It calls:

```text
Mast.Missions.List
Mast.Caom.Filtered
```

The first asks which missions are currently registered in CAOM. The second makes
bounded count queries for configured Roman collection names. It requests a small
metadata sample only when a count is positive. No bulk product download occurs.

Possible archive states are:

```text
NO_MATCHING_ROMAN_CAOM_ROWS
ROMAN_REGISTERED_NO_MATCHING_ROWS
ROMAN_CAOM_HOLDINGS_AVAILABLE
MAST_TRANSPORT_FAILED
```

A zero result is an archive-availability state. It is not interpreted as a
hardware, launch, commissioning, or science failure.

### Observed public MAST holdings — November 2, 2026

The post-merge readiness run `41454886341` queried the two configured collection
spellings, `Roman` and `ROMAN`. Each returned 285,345 CAOM rows. Both count
responses had the same SHA-256
(`de465fa5d6c4023326fe53116447d83cf587107f1bf6f7e09d501d1e2d9c7ffd`), so the
two spellings are aliases for this query result and must not be added together.

The bounded sample preserved 25 metadata rows for each spelling. In the
`Roman` sample, all 25 rows had:

- `obs_collection = ROMAN`
- `provenance_name = ROMAN TEST`
- `instrument_name = WFI`
- `intentType = science`
- `dataproduct_type = image`
- `dataRights = PUBLIC`

The 25-row sample included 12 Level 3 records, 12 Level 2 records, and 1 Level 1
record. The metadata included DARK frames and imaging filters F062, F087, F106,
F129, F146, F158, and F184. The sampled observation times ran from
2026-09-15 through 2026-10-23. Some proposal types were marked `COM`; six
sample rows had no proposal type recorded.

This is a real change from an empty archive watch: MAST now returns a substantial
Roman WFI catalog and public product paths. It is not yet a claim that 285,345
independent exposures exist, nor that routine Roman survey operations have
started. The count is a CAOM row count, not a count of unique exposures. The
sample's `ROMAN TEST` provenance and commissioning-era dates must remain visible
alongside the archive's science-intent and product-level fields. The readiness
run sampled metadata only; it did **not** download or inspect the ASDF product
bytes. Product-level interpretation and byte validation remain the next step.

The sampled product paths included ASDF products ending in `_uncal.asdf`,
`_cal.asdf`, and `_coadd.asdf`. These suffixes identify materially different
processing levels; do not treat them as interchangeable or label all of them
"raw."

### What "raw" means for public Roman WFI data

STScI's WFI data-level documentation distinguishes the instrument packet stream
from the lowest public science product:

- **Level 0 (L0):** raw, packetized data received from WFI. STScI states that L0
  products are not publicly accessible.
- **Level 1 (L1):** uncalibrated detector data, including science ramps in
  `_uncal.asdf` files. L1 is constructed from L0; data are reoriented into the
  science coordinate frame and essential metadata are populated. It is the
  lowest public WFI science-data level, but it is not byte-for-byte raw L0.
- **Level 2 (L2):** calibrated two-dimensional detector rate images in DN/s.
  The pipeline fits count-rate slopes and applies detector-level calibrations;
  uncertainty information is propagated into the product.
- **Level 3 (L3):** co-additions/mosaics made from L2 products. The pipeline
  corrects geometric distortion, converts to surface-brightness units, and uses
  data-quality information to screen detector effects.

Consequently, NVCPP must call L1 "uncalibrated public detector data," not
"unprocessed raw telemetry." Preserve the exact L1 source bytes as received,
including all ramp, reference-pixel, uncertainty, and data-quality arrays
present in the product. Preserve L2 and L3 as separate source products when
used; do not replace L1 with them or silently reverse-engineer a processed
product into a claimed raw record.

For Roman WFI product definitions and public-access limits, see:
- https://roman-docs.stsci.edu/data-handbook/wfi-data-levels-and-products
- https://roman-docs.stsci.edu/data-handbook/accessing-wfi-data/the-roman-archive-in-mast
- https://archive.stsci.edu/missions-and-data/roman

The run's full raw MAST responses, sampled metadata, and hashes are in the
90-day GitHub artifact
[roman-readiness-41454886341](https://github.com/CarlDeanClineSr/NVCPP-Non-Voxelated-Computational-Physics-Portal-/actions/runs/41454886341).
The artifact SHA-256 is
`417c2b247ac0611571e14084a9e8bc04719207cc2c3ccba1e96e199632e4f4ae`.

## Roman Research Nexus

The Roman Research Nexus is an authenticated myST cloud environment. NVCPP does
not impersonate it as a public anonymous API.

Current policy:

```text
Nexus browsing and simulation: performed by an authenticated user in Nexus
Export to NVCPP: explicit file transfer with source class and hashes
Automated public scraping: prohibited
```

Accepted future source classes are:

```text
ROMAN_RESEARCH_NEXUS_EXPORT
ROMAN_ISIM
MAST_ROMAN_PUBLIC
WFI_TRIPLET_TEST
NVCPP_SYNTHETIC_FIXTURE
```

Every imported file must preserve its original bytes, source label, retrieval or
export time, and SHA-256 before analysis.

## Deterministic local fixture

The workflow creates a small NumPy fixture with arrays named:

```text
SCI
ERR
DQ
```

It contains a seeded background, PSF-like sources, and flagged cosmic-ray-like
pixels. The first-pass analysis records:

```text
background median
robust background sigma
connected source-like components
flagged cosmic-ray pixels
saturated pixels
minimum and maximum values
array SHA-256
clipping_applied = false
```

This fixture proves the local orchestration, chart, anomaly, and evidence path.
It is **not** Roman flight data, an official Roman datamodel, Roman I-Sim output,
a MAST observation, or evidence about Roman performance.

## Truth-recovery benchmark

A pipeline can run without crashing and still recover the scene poorly. The
truth benchmark therefore compares detected components with the known injected
source catalog using one-to-one nearest matches within a frozen pixel radius.

It records:

```text
injected source count
detected component count
matched source count
completeness
purity
false-positive count
unmatched source IDs
unmatched detection IDs
centroid-error median, RMS, and maximum
cosmic-ray detection leakage
```

The overlay chart marks injected positions and detected centroids separately.
Completeness below one may indicate threshold loss, source blending, or the
limitations of the deliberately simple detector. It must not be interpreted as
Roman flight performance.

The benchmark is useful because the answer is known before the code runs. If a
future code change alters the score while the fixture seed and contract remain
unchanged, the change is a regression or a deliberate algorithm change that
requires explanation.

Roman I-Sim remains the preferred high-fidelity simulator for WFI L1/L2 products.
Its outputs should enter NVCPP through a separately frozen intake contract.

## Immediate experiments

### 1. Flux and blend recovery

Extend the truth score with isolated-source aperture photometry and explicit
blend classification. Keep blended and isolated populations separate rather
than averaging them into one misleading error number.

### 2. Detector-anomaly fingerprints

Build labeled fixtures for:

```text
cosmic-ray-like hits
persistence
hot pixels
dead pixels
striping
bias drifts
saturation
missing metadata
truncated files
```

The first result should be a classifier of evidence state, not a physical claim.

### 3. Schema-drift watch

Hash the MAST response schema and future Roman product metadata. A new field is
recorded and reviewed before the importer changes. Missing required fields fail
closed.

### 4. HST/JWST/Roman cross-observatory context

Once Roman products exist, match observations by:

```text
sky footprint
time interval
filter or wavelength
target
calibration level
```

The comparison can study source variability, detector response, background, and
cross-calibration. It must not reinterpret telescope pixels as L1 plasma data.

### 5. Space-weather context as an external covariate

Future Roman detector-background or cosmic-ray metrics may be compared with
independently measured L1 conditions. The two domains remain separate:

```text
L1 magnetic/plasma evidence
        versus
Roman detector/background evidence
```

A correlation would identify a time worth investigation. It would not by itself
prove a causal mechanism.

## Commands

Run the offline readiness tests:

```bash
python -m pytest -q \
  tests/test_roman_mast.py \
  tests/test_roman_synthetic_fixture.py \
  tests/test_roman_truth_benchmark.py \
  tests/test_roman_prelaunch_probe.py \
  tests/test_roman_intake.py
```

Run the live public probe:

```bash
python -m observatory.roman.prelaunch_probe \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/readiness
```

The GitHub workflow runs every six hours at minute 37 UTC and can also be
dispatched manually. Its runner is pinned to `ubuntu-24.04`; existing Node 24
actions, Python 3.12, schedule, metadata limits, and synthetic fixture settings
are unchanged.

## Evidence products

```text
roman_readiness_manifest.json
roman_prelaunch_contract.json
mast_missions.json
raw/mast_*.json
raw/official_*.html
reports/ROMAN_READINESS.md
reports/ROMAN_MAST_ARCHIVE.md
synthetic_fixture/roman_synthetic_l2_like_fixture.npz
synthetic_fixture/roman_synthetic_truth.json
synthetic_fixture/roman_synthetic_metrics.json
synthetic_fixture/roman_synthetic_preview.png
truth_benchmark/roman_truth_benchmark.json
truth_benchmark/roman_detection_catalog.csv
truth_benchmark/roman_truth_matches.csv
truth_benchmark/roman_truth_recovery_overlay.png
```

## Import a Roman Research Nexus or Roman I-Sim export

After exporting files through your authenticated myST/Nexus session, inventory
them before any format-specific analysis:

```bash
python -m observatory.roman.intake \
  --input /path/to/exported/files \
  --source-class ROMAN_RESEARCH_NEXUS_EXPORT \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/intake
```

For small files that should be copied into the evidence package:

```bash
python -m observatory.roman.intake \
  --input /path/to/exported/files \
  --source-class ROMAN_ISIM \
  --copy-small-files
```

The intake identifies exact ASDF/FITS signatures, preserves SHA-256, and refuses
to claim detailed schema validation until a pinned Roman reader environment is
added. This prevents filename extensions or operator descriptions from silently
becoming scientific metadata.
