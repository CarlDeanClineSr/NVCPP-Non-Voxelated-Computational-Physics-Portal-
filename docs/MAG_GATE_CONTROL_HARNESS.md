# Frozen MAG Gate Control Harness

## Purpose

This stage evaluates whether the Gannon 2024 magnetic timing result remains
unusual after physical simultaneity is deliberately broken. It does not alter
the detector and does not run ephemeris, minimum-variance analysis, or a
physical discontinuity classifier.

The frozen detector remains:

```text
one-minute GSE-vector rotation >= 45 degrees
OR
one-minute relative |B| change >= 0.25
exact previous row = t-1 minute
no interpolation or forward fill
radii = 1, 2, 3, 5, 10, and 15 minutes
```

The authoritative contracts are:

```text
config/gannon_gate_density.v1.json
config/mag_gate_controls.v1.json
```

## First hard-null run

```text
Workflow: NVCPP Frozen MAG Gate Control Harness
Run ID: 33250956817
Commit: 8654fbf3d3afcc37c470e2f0c961bdb5d8d68b9a
Artifact SHA-256: 4fb8f990d7fc3d69fca65f5471622f92de4ef3c29410196bec1410918382c093
Circular-shift iterations: 5,000
```

The development-day values reproduced exactly:

| Quantity | Observed Gannon value |
|---|---:|
| Joint ACE-and-Wind support within 1 minute | 0.26829268 |
| Joint support within 2 minutes | 0.42682927 |
| Joint support within 3 minutes | 0.51219512 |
| Joint support within 5 minutes | 0.64634146 |
| Joint support within 10 minutes | 0.78048780 |
| Joint support within 15 minutes | 0.93902439 |
| Strongest three-spacecraft span <=3 minutes | 0.14634146 |
| 10:59 candidate nearest joint radius | 2 minutes |
| 10:59 candidate strongest span | 3 minutes |

## Circular-shift control

ACE and Wind gate trains were shifted independently around the 1,440-minute
day. Every accepted trial placed ACE more than 15 minutes from DSCOVR, Wind more
than 15 minutes from DSCOVR, and ACE more than 15 minutes from Wind. The shift
preserves each spacecraft's gate count, temporal ordering, autocorrelation, and
gap pattern while breaking all three pairwise simultaneities.

The add-one empirical upper-tail fractions were:

| Quantity | Null outcomes at least as large | Add-one tail fraction |
|---|---:|---:|
| Joint support within 1 minute | 0 / 5,000 | 0.00019996 |
| Joint support within 2 minutes | 0 / 5,000 | 0.00019996 |
| Joint support within 3 minutes | 0 / 5,000 | 0.00019996 |
| Joint support within 5 minutes | 0 / 5,000 | 0.00019996 |
| Joint support within 10 minutes | 0 / 5,000 | 0.00019996 |
| Joint support within 15 minutes | 0 / 5,000 | 0.00019996 |
| Strongest-span fraction <=3 minutes | 1 / 5,000 | 0.00039992 |

For the named 10:59 anchor itself, the lower-tail fractions were:

| Quantity | Null outcomes as tight or tighter | Add-one tail fraction |
|---|---:|---:|
| Nearest joint radius <=2 minutes | 129 / 5,000 | 0.02599480 |
| Strongest three-spacecraft span <=3 minutes | 27 / 5,000 | 0.00559888 |

These values are empirical under the frozen circular-shift generator. They are
not independent-minute probabilities and do not prove a common surface.

## Fixed mismatched-day control

The control used predeclared symmetric offsets from 2024-05-11:

```text
-21, -7, +7, and +21 days
```

ACE and Wind were never paired with the same offset, leaving 12 admitted
cross-day pairs. Every source day exceeded the frozen 80% evaluable-coverage
minimum.

None of the 12 pairs equaled or exceeded any observed Gannon joint-support
fraction. None produced support for the named 10:59 anchor at a nearest radius
of 2 minutes or a strongest span of 3 minutes. Because the mismatched sample is
small, every comparison has a minimum add-one empirical fraction of:

```text
1 / 13 = 0.07692308
```

That is descriptive evidence under the declared finite control set, not a
high-resolution tail estimate.

## Current result

```text
SHORT_RADIUS_CLUSTER_EXCEEDS_CURRENT_HARD_NULLS
BACKGROUND_CALIBRATION_PARTIAL_HARD_NULLS_COMPLETE_EVENT_CLASS_CONTROLS_PENDING
GEOMETRY_BLOCKED_PENDING_EVENT_CLASS_CONTROLS
COMMON_SURFACE_CLAIM_NOT_ALLOWED
PHYSICAL_CLASS_CLAIM_NOT_ALLOWED
THRESHOLD_RETUNING_NOT_ALLOWED
```

The hard-null result means the Gannon timing is substantially tighter than the
current broken-simultaneity controls. It does not reverse the existing bounded
physical result:

```text
SHARED_DISTURBED_INTERVAL_SUPPORTED
UNIQUE_COMMON_STRUCTURE_UNRESOLVED
PHYSICAL_CLASS_UNRESOLVED
PROPAGATION_NOT_CALCULATED
```

## Why calibration remains incomplete

Circular shifts and mismatched days answer whether the observed clustering is
unusual after simultaneity is broken. They do not establish how the unchanged
gate behaves across independently selected physical regimes.

The following event-class controls remain pending:

```text
QUIET_SOLAR_WIND
MODERATE_VARIABILITY
ISOLATED_SHOCK_OR_SHEATH
COMPLEX_INTERACTING_EJECTA
```

Class membership must be frozen from an independent catalog or index before the
corresponding MAG gate results are retrieved. The Gannon thresholds and timing
radii must remain unchanged.

## Next admissible stage

1. Freeze an independent event-class selection contract without inspecting the
   resulting MAG gate counts.
2. Run the unchanged detector on multiple days per class.
3. Preserve per-spacecraft gate density, run lengths, support at every frozen
   radius, strongest-span distributions, candidate-score percentiles, source
   hashes, gaps, and quality exclusions.
4. Use mismatched-day and constrained circular-shift controls within that
   multi-interval study.
5. Keep geometry blocked until those distributions exist.

A hard-null exceedance earns further calibration. It does not authorize a
common-surface or propagation claim.
