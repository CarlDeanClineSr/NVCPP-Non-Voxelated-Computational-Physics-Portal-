# NVCPP Science and Technology Research Watch

Review date: 2026-11-02

This document records dated external developments that may affect NVCPP's
research priorities, source contracts, data access, or interpretation. It is a
review log, not an automatic authorization to change a scientific method or
run a sealed experiment.

## November 2, 2026 — reviewed developments

### 1. Nancy Grace Roman Space Telescope

NASA's October 22, 2026 report says Roman entered its science commissioning
phase after observatory and primary-instrument checkout. NASA reports that the
observatory can guide, take exposures, slew between targets, and return data.
The team is continuing planned test observations to characterize instrument
response and refine image quality before routine science operations.

NVCPP action:
- The dated mission-status reference in
  `config/roman_prelaunch.v1.json` now points to the October 22 report.
- The earlier September 15 report remains listed as a historical official page.
- The bounded MAST readiness workflow, synthetic fixture, truth benchmark,
  authenticated Nexus intake boundary, and prohibition on L1 equations remain
  unchanged.
- Do not label a test observation, synthetic fixture, or catalog registration
  as routine Roman science data without product-level evidence.

Source:
- NASA, October 22, 2026:
  https://science.nasa.gov/blogs/roman/2026/10/22/nasas-roman-space-telescope-enters-final-commissioning-phase/

### 1a. Public MAST holdings observed on November 2

The merged Roman readiness workflow queried MAST at approximately
2026-11-02T13:40Z. It returned 285,345 CAOM rows for `Roman` and the same
285,345 rows for `ROMAN`. The count-response bytes and SHA-256 were identical,
so the two case variants are aliases for this result, not two additive
collections.

A bounded 25-row metadata sample from the `Roman` query had 25/25 records
labelled `provenance_name=ROMAN TEST`, `instrument_name=WFI`,
`intentType=science`, `dataproduct_type=image`, and `dataRights=PUBLIC`.
The sample included 12 Level 3 records, 12 Level 2 records, and 1 Level 1
record. Sampled observation times ranged from September 15 to October 23,
2026. Filters included DARK and F062, F087, F106, F129, F146, F158, and F184.
Nineteen records had proposal type `COM`; six had no proposal type recorded.

These fields must be read together. The archive labels the sampled records as
science intent and public image products, while the provenance field says
`ROMAN TEST` and NASA's current mission report describes continuing test
observations during commissioning. The correct description at this stage is
**public WFI catalog/product metadata from the ROMAN TEST provenance**, not a
claim that routine survey science operations have begun. The 285,345 value is
a CAOM row count, not a count of unique exposures. The readiness workflow
sampled metadata only and did not download or inspect the ASDF product bytes.

The metadata contained public product paths with `_uncal.asdf`,
`_cal.asdf`, and `_coadd.asdf` suffixes. This makes a bounded product-level
inspection the next useful Roman step.

### 1b. Roman's public data floor and the unfiltered-data boundary

STScI's WFI data handbook states that Level 0 consists of raw packetized WFI
data and is not publicly accessible. Level 1 is the lowest public WFI science
product. It contains uncalibrated detector ramps, but it is constructed from
L0: detector data are reoriented into the science coordinate frame and
essential metadata are populated. Therefore L1 is the lowest public
uncalibrated product, not the original packet stream.

The documented levels have different meanings:

- L1 `_uncal.asdf`: uncalibrated detector ramp data.
- L2 `_cal.asdf`: calibrated two-dimensional rate images in DN/s, with
  count-rate fitting, detector calibrations, and propagated uncertainty.
- L3 `_coadd.asdf`: repixelated/co-added products or mosaics derived from L2,
  including geometric correction, surface-brightness conversion, and
  data-quality screening.

For NVCPP, the exact original bytes of each publicly accessible level must be
retained independently. L1 must not be called raw L0 telemetry; L2 and L3 must
not be treated as raw or substituted for L1. Preserve all arrays and metadata
in each ASDF product, including science, reference-pixel, error/variance, and
data-quality arrays when present. Any later selection or transformation must
be recorded as a separate derived product with the original product untouched.

The Roman readiness artifact records the MAST count and sample response hashes.
Its metadata-only sample SHA-256 is
`8d3052422264f851d65933d8c979c6e50c8f12f6bc76916b4e3f633354903221`;
the count-response SHA-256 is
`de465fa5d6c4023326fe53116447d83cf587107f1bf6f7e09d501d1e2d9c7ffd`.
The complete artifact is retained by the workflow for 90 days:
https://github.com/CarlDeanClineSr/NVCPP-Non-Voxelated-Computational-Physics-Portal-/actions/runs/41454886341

Sources:
- STScI Roman WFI data levels and products:
  https://roman-docs.stsci.edu/data-handbook/wfi-data-levels-and-products
- STScI, The Roman Archive in MAST:
  https://roman-docs.stsci.edu/data-handbook/accessing-wfi-data/the-roman-archive-in-mast
- MAST Roman mission archive:
  https://archive.stsci.edu/missions-and-data/roman

### 2. IMAP Release 2

The Princeton IMAP mission science-data page states that Release 2 data became
available on October 28, 2026. The same page lists Release 1 as available
July 29, 2026 and points users to NASA's Space Physics Data Facility (SPDF).

SPDF's August 2026 announcement says IMAP in-situ and sample-instrument science
data for CoDICE, HIT, SWAPI, SWE, IDEX, GLOWS, and MAG through April 30, 2026
were available in SPDF and CDAWeb at that time. This is a dated archive notice;
it must not be treated as proof that every Release 2 product, later date, or
instrument product is present in a particular SPDF collection today.

NVCPP action:
- Keep the IMAP mission-specific production adapter disabled.
- Start with a product inventory, not physics code.
- For each candidate MAG product, record dataset identifier, product level,
  data version, time coverage, cadence, vector variables, units, coordinate
  frame, fill values, quality flags, calibration references, and source URL.
- Download a small bounded sample only after the dataset identity and access
  method are documented. Preserve the original CDF or other source bytes and
  metadata, then hash them before parsing.
- Compare the Princeton Release 2 publication inventory with SPDF/CDAWeb
  availability. Record disagreements or time-coverage differences rather than
  silently choosing one catalog.
- Do not mix IMAP I-ALiRT real-time products with science-grade MAG products;
  record the specific product and intended use.

Sources:
- Princeton IMAP mission, Science Data page (Release 2 availability date):
  https://imap.princeton.edu/data
- NASA SPDF, current archive and dated August 2026 IMAP availability notice:
  https://spdf.gsfc.nasa.gov/

### 3. NOAA operational solar-wind source priority

NOAA's Space Weather Prediction Center notice dated June 30, 2026 says that
DSCOVR ingestion and processing were stopped for its operational display after
SOLAR-1 entered operations. The notice identifies SOLAR-1 as the primary
operational solar-wind source and ACE as backup until IMAP I-ALiRT has 24/7
coverage.

Interpretation boundary:
- This is a statement about NOAA's operational feed and display.
- It does not invalidate historical DSCOVR data or the NVCPP historical
  DSCOVR/Gannon work.
- It does not make NOAA's provider feed interchangeable with a separately
  acquired and independently identified spacecraft science product.
- ACE, Wind, and DSCOVR may remain relevant in historical or comparative work
  under their own source contracts and product limitations.
- Do not infer that IMAP I-ALiRT has reached continuous operational coverage
  merely because IMAP Release 2 science data have been announced.

NVCPP action:
- Distinguish operational source priority from historical research availability.
- Keep spacecraft identity, upstream provider, product level, retrieval time,
  cadence, and quality status explicit.
- Before any future live-source reconfiguration, inspect the actual endpoint
  payload and its published contract. Do not change detector thresholds or
  historical source contracts based only on a provider display change.

Source:
- NOAA SWPC, Solar Wind Data and Display Changes, June 30, 2026:
  https://www.spaceweather.gov/news/solar-wind-data-and-display-changes

## Data-integrity rule: preserve before processing

The original source object is evidence. Processing must create new, traceable
products rather than replace the evidence.

For every new acquisition or imported product, aim to preserve:

- Exact raw response body or original file bytes.
- Request URL, parameters, retrieval time, provider, dataset/product identifier,
  version, and response metadata.
- Original timestamps, native cadence, units, coordinate labels, quality and
  fill-value conventions, and the provider's documentation revision.
- SHA-256 of the original bytes and of each derived artifact.
- Counts and identities of admitted, rejected, missing, duplicate, suspect, and
  quarantined records, with explicit reasons.
- All transformation parameters and code/contract versions needed to reproduce
  derived values.

Never silently clip, cap, smooth, interpolate, fill, relabel, or overwrite a
valid raw measurement. If a transformation is scientifically justified, retain
the raw measurement and write the transformed result as a separate field or
product with a documented operation. If a datum is unusable for a particular
calculation, exclude it from that calculation transparently while preserving
the original record and exclusion reason.

A versioning caveat matters: SPDF states that many datasets are updated or
reprocessed and that SPDF preserves the latest version. Therefore, a reproducible
NVCPP run must retain the exact downloaded source bytes, product version,
retrieval date, and hashes used for that run; a URL alone may later resolve to
different bytes.

## Interpretation rule: multiple hypotheses, common evidence

NVCPP is not restricted to one institutional, academic, or historical
interpretation. It may preserve competing explanations when they are physically
admissible and the available evidence does not decide between them.

For each important result, keep these layers separate:

1. **Observation:** what the instrument or provider actually returned.
2. **Data treatment:** parsing, calibration state, coordinate conversion,
   resampling, filtering, exclusions, and uncertainty handling.
3. **Derived result:** the mathematical quantity calculated from the stated
   inputs and operations.
4. **Interpretation:** what the result may physically mean.
5. **Alternatives:** other physically admissible explanations, including
   instrument, sampling, geometry, propagation, or source-identification
   effects where relevant.
6. **Discriminating tests:** additional measurements, controls, or predictions
   that could support one explanation over another.
7. **Unresolved items:** what the evidence cannot currently establish.

No interpretation receives a pass because it is conventional, prestigious,
unconventional, or preferred. All must face the same source checks, dimensional
analysis, mathematical consistency, uncertainty accounting, independent
measurements, negative controls, and reproducibility tests. This is not a
requirement to treat every claim as equally likely; it is a requirement to make
the basis for preferring or rejecting an explanation explicit.

## Monthly review procedure

At each monthly review:

1. Check mission and instrument announcements from primary mission teams,
   NASA, NOAA, ESA, instrument teams, and data archives.
2. Record the announcement date separately from the event date and the date
   NVCPP reviewed it.
3. Verify data availability at the actual archive/product level; do not infer
   public data availability from a mission milestone.
4. Compare new metadata and documentation with existing source contracts.
5. Identify whether the development requires documentation, a test, a new
   adapter, or no repository change.
6. Preserve the decision and the reason for waiting when evidence is
   insufficient.
7. Do not change frozen registries, detector definitions, thresholds, or
   execution controls as a side effect of a general research review.

## November 2026 work queue

| Priority | Investigation | Entry condition | Current decision |
|---|---|---|---|
| 1 | IMAP Release 2 MAG product inventory | Confirm product IDs, variables, coverage, version, coordinates, cadence, quality flags, calibration documentation | Discovery only; adapter disabled |
| 2 | Roman WFI product-level intake | Select a bounded, matched observation/association; inspect L1 uncalibrated ramp bytes, corresponding L2/L3 products where available, ASDF schemas, DQ/ERR/variance arrays, calibration/reference files, and provenance | Public WFI metadata found; product bytes not yet downloaded or validated |
| 3 | NOAA live-source contract comparison | Compare the actual operational endpoint payload with the existing SOLAR-1 and NOAA source contracts | No source-contract change until payload audit |
| 4 | Cross-mission comparison design | Establish time alignment, location/propagation context, instrument independence, and product compatibility | Design only; no shared-surface or mechanism claim |

## Explicitly unchanged

- Gannon V2 registry: frozen, hash-locked, manual-only, and not dispatched.
- Gannon V2 selection firewall and geometry restrictions: unchanged.
- Hourly Observatory: manual-only; no schedule reactivation.
- Roman: no L1 plasma calculations, no `chi_B24M`, and no science claims from
  synthetic fixtures or archive registration.
- IMAP: no mission-specific production adapter enabled by this review.
- No detector thresholds or historical source contracts were changed.
