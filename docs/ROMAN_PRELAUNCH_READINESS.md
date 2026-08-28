# Roman Prelaunch and Archive Readiness

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

## Launch posture

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
the clock alone. Later states remain unverified until an authoritative source or
archive transition supports them.

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
PRELAUNCH_NO_ROMAN_CAOM_HOLDINGS
ROMAN_REGISTERED_NO_MATCHING_ROWS
ROMAN_CAOM_HOLDINGS_AVAILABLE
MAST_TRANSPORT_FAILED
```

A zero result is an archive-availability state. It is not interpreted as a
hardware, launch, commissioning, or science failure.

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
dispatched manually.

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
