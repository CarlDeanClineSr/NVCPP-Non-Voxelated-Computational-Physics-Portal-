# SOLAR-1 MAG Metadata Freeze Checklist

**Status:** discovery only. No SOLAR-1 record may enter `CLINE-L1-B24M-TRAIL-v1` until every required field below is frozen from a NOAA/NCEI artifact.

## 1. Source identity

- NOAA/NCEI API build/version
- satellite identifier exactly as returned
- instrument identifier exactly as returned
- product ID
- product title
- operational versus retrospective science-quality status
- product level
- product version/revision
- first and last available timestamps
- source endpoint and resolved request URL
- raw response byte count and SHA-256

## 2. Time contract

- timestamp parameter ID
- timestamp format
- timezone/UTC declaration
- native cadence
- available one-second versus one-minute streams
- duplicate timestamp policy
- leap-second handling
- gap representation
- whether timestamps mark interval start, center, or end

## 3. Magnetic-vector contract

For every vector field:

- exact API parameter ID
- human label
- component meaning
- coordinate frame
- units
- numeric type
- fill/missing value
- valid minimum and maximum
- scale factor and offset, if any
- quality dependency

Required canonical mapping:

```text
NOAA time       -> time_utc
NOAA component 1 -> bx_nT
NOAA component 2 -> by_nT
NOAA component 3 -> bz_nT
```

No component is mapped by column position or name similarity alone.

## 4. Quality contract

- quality-flag parameter IDs
- bit definitions or categorical meanings
- records that must be rejected
- records that may be retained with warnings
- instrument-mode or calibration-state fields
- spacecraft-field contamination flags
- provisional versus final-quality indicators

## 5. One-minute canonical product

Before physics:

- select the official one-minute product when NOAA supplies one;
- otherwise document component-wise one-minute aggregation from native cadence;
- require a declared minimum native-sample coverage per minute;
- calculate vector magnitude only after component aggregation;
- preserve native data separately from the canonical one-minute table.

## 6. NVCPP physics admission

The contract may be unlocked only when:

- source identity passes;
- coordinate frame and units are explicit;
- fill values are removed as non-measurements, never clipped;
- quality filtering is explicit and counted;
- a 24-hour pre-roll is retrieved;
- the analysis interval is separated from pre-roll;
- `ratio_B24M`, `delta_B24M`, and `chi_B24M` remain distinct;
- the prior-only trailing 24-hour median and 95% coverage gate are unchanged;
- all raw and transformed artifacts carry SHA-256 provenance.

## 7. First artifact review order

1. `solar1_mag_discovery_manifest.json`
2. `products_filtered.json`
3. `parameters_science_l3.json`
4. `parameters_operational_l3.json`
5. `hapi_catalog.json`
6. HAPI `/info` response for the chosen dataset
7. a small bounded `/values` or HAPI `/data` sample

The first science computation remains disabled until this checklist is completed and committed.
