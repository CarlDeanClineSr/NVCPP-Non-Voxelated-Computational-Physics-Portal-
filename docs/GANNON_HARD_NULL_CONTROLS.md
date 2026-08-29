# Gannon 2024 Frozen MAG Hard-Null Controls

## Purpose

The May 11 gate-density audit established that the frozen magnetic gate

```text
previous-minute GSE-vector rotation >= 45 degrees
OR
previous-minute relative |B| change >= 0.25
```

fires frequently during interacting ejecta. This control stage asks whether the
observed three-spacecraft timing survives two harder comparisons that preserve
time-series structure while breaking physical simultaneity.

The gate, one-minute cadence, exact `t-1 minute` requirement, and support radii
`1, 2, 3, 5, 10, 15 minutes` were not changed.

## Frozen observed result

```text
DSCOVR candidate:                    2024-05-11 10:59 UTC
ACE nearest gate:                    10:59 UTC  (0 minutes)
Wind nearest gate:                   10:57 UTC  (-2 minutes)
nearest joint radius:                2 minutes
ACE strongest gate:                  10:57 UTC  (-2 minutes)
Wind strongest gate:                 10:56 UTC  (-3 minutes)
strongest three-spacecraft span:     3 minutes
DSCOVR gate anchors in full-day set: 82
```

Across the observed event day:

```text
joint ACE-and-Wind support <=2 minutes: 35 / 82 = 0.426829
strongest three-craft span <=3 minutes: 12 / 82 = 0.146341
```

The physical interpretation remains frozen:

```text
SHARED_DISTURBED_INTERVAL_SUPPORTED
UNIQUE_COMMON_STRUCTURE_UNRESOLVED
PHYSICAL_CLASS_UNRESOLVED
PROPAGATION_NOT_CALCULATED
```

## Circular-shift hard null

Five thousand deterministic, unique ACE/Wind shift pairs were generated with
seed `20240511`. Each spacecraft was circularly displaced by more than the
15-minute support radius, and ACE and Wind were also displaced from one another
by more than 15 minutes. The construction preserves each series' gate density,
clustering, gaps, score sequence, and autocorrelation while breaking pairwise
simultaneity. The periodic day boundary is an explicit limitation.

| Metric | Observed | Control median | Equal or more extreme | Plus-one estimator |
|---|---:|---:|---:|---:|
| Candidate nearest joint radius | 2 min | 8 min | 129 / 5,000 = 0.0258 | 0.025995 |
| Candidate strongest three-craft span | 3 min | 13 min | 27 / 5,000 = 0.0054 | 0.005599 |
| Full-day joint-support fraction within 2 min | 0.426829 | 0.012195 | 0 / 5,000 | 0.000200 |
| Full-day strongest-span <=3 min fraction | 0.146341 | 0.000000 | 1 / 5,000 = 0.0002 | 0.000400 |

The observed full-day support fractions exceeded every circular-shift control at
radii 1, 2, 3, 5, 10, and 15 minutes. That is evidence that the spacecraft were
responding to a genuinely shared disturbed interval rather than three unrelated
shifted sequences.

The candidate-specific result is narrower. A joint radius of two minutes or less
occurred in 2.58% of the circular controls, and a strongest span of three minutes
or less occurred in 0.54%. Those frequencies make the 10:59 timing compact under
this construction, but they do not identify one discontinuity.

Controls with no ACE-and-Wind support at the candidate minute remain in the
denominator. The analysis does not condition on a hit after seeing the result.

## Predeclared mismatched-day hard null

The mismatched dates were selected before retrieval by fixed nonzero prime-number
day offsets from May 11. Three ACE days and three Wind days were combined by
Cartesian product, producing nine controls aligned only by minute of UTC day.
Dates remain recorded and are never described as simultaneous.

### Source-day gate density

| Spacecraft | UTC date | Evaluable rows | Gate rows | Gate fraction |
|---|---|---:|---:|---:|
| ACE | 2024-05-14 | 1,439 | 36 | 0.025017 |
| ACE | 2024-05-22 | 1,439 | 15 | 0.010424 |
| ACE | 2024-06-03 | 1,439 | 32 | 0.022238 |
| Wind | 2024-05-16 | 1,389 | 40 | 0.028798 |
| Wind | 2024-05-28 | 1,404 | 16 | 0.011396 |
| Wind | 2024-06-09 | 1,362 | 34 | 0.024963 |

No mismatched-day pair equaled the observed candidate radius, strongest span,
full-day two-minute support fraction, or strongest-span fraction. With only nine
Cartesian controls, the plus-one estimator is limited to `1 / 10 = 0.1`; this
small sample is a direction check, not a precise tail estimate.

The mismatched days also have lower gate densities than Gannon. They therefore
break simultaneity but do not yet constitute activity-matched controls.

## Result state

```text
HARD_NULLS_MEASURED
EVENT_CLASS_CONTROLS_PENDING
COMMON_SURFACE_UNRESOLVED
```

The hard nulls strengthen two separate observations:

1. The day-wide multipoint clustering is not reproduced when physical
   simultaneity is deliberately broken.
2. The 10:59 cluster is compact relative to the circular controls, but it is not
   uniquely diagnostic of one moving surface.

The empirical frequencies are conditional on the declared control
constructions. They are not independent-minute probabilities, a quiet-time
false-positive rate, or a physical propagation result.

## Geometry gate

Ephemeris, minimum-variance analysis, discontinuity normals, and predicted
crossing times remain blocked until the unchanged detector is also evaluated on
independently selected:

```text
quiet solar-wind intervals
moderate-variable intervals
isolated shock/sheath intervals
complex interacting-ejecta intervals
```

Those event classes must be selected without looking at the frozen MAG gate
output. Negative results receive the same manifests, hashes, tables, and reports
as positive candidates.

## Reproduction

```bash
python -m historical.gannon_gate_density \
  --config config/gannon_gate_density.v1.json \
  --outdir runs/audits/gannon_gate_density

python -m historical.gannon_gate_controls \
  --config config/gannon_gate_controls.v1.json \
  --gate-density-root runs/audits/gannon_gate_density \
  --outdir runs/audits/gannon_gate_controls
```

Verified branch run:

```text
Workflow: NVCPP Gannon Frozen MAG Hard-Null Controls
Run ID: 33252518475
Commit: 6e1249639b22a7a909103fc26928bc4a8907221c
Artifact digest: sha256:8a39c7f5f3cef2c5f80efe348312a5d413b4da41c48dc409e88ef23b536f1d55
Result: SUCCESS
```
