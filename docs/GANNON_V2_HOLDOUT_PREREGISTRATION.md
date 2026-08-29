# Gannon V2 holdout preregistration

V2 is a prospective holdout calibration. It does not revise V1 and it does not claim that the Gannon-inspired clustering statistic was blind in V1.

## Frozen detector

The detector remains identical to the Gannon development contract: one-minute GSE vectors, exact preceding 60-second row, rotation >=45 degrees OR relative |B| change >=0.25, no interpolation, no forward fill, and timing radii of 1, 2, 3, 5, 10, and 15 minutes.

## Frozen primary clustering hypothesis

The primary V2 holdout statistic is fixed before holdout MAG scoring:

```text
nearest joint ACE+Wind support radius <= 2 minutes
AND
strongest selected three-spacecraft span <= 3 minutes
```

This definition is explicitly labeled `GANNON_V1_INSPIRED_NOT_BLIND_IN_V1`. V2 is the first prospective holdout test of that exact definition. The radii may not be retuned after holdout inspection.

The observed development offsets `(DSCOVR, ACE, WIND) = (0, -2, -3)` minutes remain a frozen reference, not a newly optimized template.

## Holdout registry

The holdout registry must be completed before spacecraft MAG retrieval or clustering inspection. It contains at least ten prospectively selected intervals in each required class: quiet solar wind, moderate variability, isolated shock/sheath, and complex interacting ejecta.

Gannon and every interval inspected during V1 are excluded from the V2 holdout denominator.

Selection may use only declared independent indices or event catalogs. It may not use gate outputs, candidate scores, or clustering outputs.

An interval that later fails retrieval or multipoint completeness remains in the registry. `INCOMPLETE_MULTIPOINT` counts are reported class by class. Failed intervals are not replaced because their gate or clustering result is inconvenient.

## Required reporting

Report both the frozen registry denominator and the evaluable denominator for every class. Pooled results cannot replace class-specific results.

For each class, report per-spacecraft gate density, run lengths, hourly density, joint support at all frozen radii, nearest and strongest offsets, three-spacecraft span, score percentiles, source hashes, quality exclusions, and gaps. The primary clustering event rate is evaluated separately from the descriptive radius table.

Hard nulls remain real-series nulls: circular shifts, mismatched days, date-block permutations, and mission-era-matched mismatches. Independent-minute noise is not a primary null.

## Interpretation boundary

A common short-radius cluster after broken simultaneity supports the result state `PRIMARY_CLUSTERING_COMMON_AFTER_SIMULTANEITY_BREAK` and weakens a common-surface interpretation. A rare cluster under the hard nulls may earn a separately reviewed geometry stage, but does not automatically open geometry and does not establish propagation, a common surface, or a physical class.

V2 must accept negative outcomes without changing the detector or clustering definition.
