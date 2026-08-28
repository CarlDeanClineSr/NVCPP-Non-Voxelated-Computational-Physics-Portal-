# Roman Unthought-Ofs and Test Triggers

This document preserves questions that are easy to lose while code is moving
quickly. They are not claims. Each item names the evidence needed before NVCPP
changes state or interpretation.

## 1. Scheduled time is not an observed launch

A clock crossing `launch_utc` proves only that the scheduled time passed.

Required evidence for a launch-state change:

```text
authoritative NASA launch status
mission operations status
or another explicitly frozen official source
```

Until then, use `SCHEDULED_LAUNCH_WINDOW_UNVERIFIED`.

## 2. Archive registration is not calibrated science readiness

Roman may appear in a MAST mission list before calibrated public products exist.
A product may appear before its schema, calibration context, or quality policy is
frozen.

Required sequence:

```text
mission registration
→ first metadata row
→ first bounded product
→ schema quarantine
→ reader and calibration versions pinned
→ contract frozen
→ science ingestion enabled
```

## 3. Pipeline execution is not recovery quality

The first deterministic fixture injected 24 sources while the simple connected-
component detector returned fewer components. A green workflow therefore does
not prove that the scene was recovered correctly.

The truth benchmark must track:

```text
completeness
purity
false positives
centroid error
cosmic-ray leakage
unmatched truth
unmatched detections
```

Later work should separate isolated sources from blends before reporting flux
recovery.

## 4. Raw page changes are not automatically mission-state changes

Official pages can change counters, scripts, timestamps, navigation, or layout.
Raw SHA-256 changes remain useful provenance, but they are not semantic evidence
by themselves.

A future semantic watcher should extract and compare only frozen fields such as:

```text
scheduled launch UTC
explicit go/no-go status
explicit launch outcome
mission phase wording
archive availability statement
```

## 5. The first public product is a quarantine event

The first Roman product should be preserved before being parsed deeply.

Record first:

```text
source URL
retrieval UTC
exact bytes and SHA-256
content type and size
MAST metadata row
schema signature
software environment
```

Do not silently adapt the parser to an unfamiliar file.

## 6. Large public files require a budget contract

Before downloading any WFI detector-test or flight product, query or preserve:

```text
file size
product type
calibration level
expected checksum when available
runner free space
time budget
per-run byte ceiling
```

A metadata-only result is preferable to an uncontrolled multi-gigabyte download.

## 7. Simulation tools need their own frozen environment

Roman I-Sim, ASDF, `roman_datamodels`, CRDS, and pipeline versions can change.
A simulation result is reproducible only when the environment and reference-data
context are pinned beside it.

## 8. Anomalies need labeled controlled fixtures

Build one fixture per anomaly class:

```text
hot pixel
dead pixel
cosmic-ray track
persistence
striping
bias drift
saturation
missing metadata
truncation
shape mismatch
schema drift
```

Each fixture should define the expected evidence state before the detector runs.

## 9. Cross-observatory agreement needs controls

Future HST, JWST, and Roman comparisons should match sky position, time, filter,
calibration level, and source class. A mismatch in any one of these can look like
variability.

## 10. L1 space weather is an external covariate

L1 magnetic and plasma data may be compared with detector-background or cosmic-
ray metrics, but telescope arrays remain outside `chi_B24M` and plasma equations.
A temporal association is a candidate relationship, not a mechanism.

## 11. Negative results are useful states

Examples:

```text
no Roman CAOM rows
no source recovered below a flux range
no detector response correlated with an L1 event
no schema change after launch
```

Preserve these as bounded results rather than treating them as failed research.

## Immediate order

1. Keep the six-hour public readiness watch running.
2. Score the deterministic fixture against its truth on every run.
3. Export one small Roman I-Sim/Nexus product when available.
4. Quarantine and inventory it before installing new readers.
5. Add isolated-source photometry and blend classification.
6. Add semantic launch-status history without inferring success from time alone.
7. Add a bounded first-public-product downloader only after metadata appears.
