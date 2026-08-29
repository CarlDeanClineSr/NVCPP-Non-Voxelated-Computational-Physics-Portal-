# Gannon 2024 MAG Gate-Density Result

## Question

The bounded multipoint audit found a qualifying magnetic transition near the
DSCOVR 2024-05-11 10:59 UTC candidate on DSCOVR, ACE, and Wind. This follow-up
asks how selective that result is on the same disturbed day.

The frozen gate is:

```text
previous-minute GSE-vector rotation >= 45 degrees
OR
previous-minute relative |B| change >= 0.25
```

Every diagnostic requires an admitted canonical row exactly 60 seconds earlier.
A gap is never treated as a one-minute transition.

## Frozen run

```text
Workflow: NVCPP Gannon Full-Day MAG Gate Density
Run ID: 33248385873
Commit: 0a4000762f16aebb713b224d980fc398743c329f
Artifact SHA-256: 44f520bc028c2e5557468055f72bc541037740cf8a0ae3c68baffd7273d1f69c
Interval: 2024-05-11 00:00 UTC through 2024-05-12 00:00 UTC
Candidate: 2024-05-11 10:59 UTC
```

## Gate density

| Spacecraft | Evaluable exact-minute rows | Gate rows | Gate fraction | Contiguous gate runs |
|---|---:|---:|---:|---:|
| DSCOVR | 1,433 | 86 | 0.0600 | 46 |
| ACE | 1,439 | 83 | 0.0577 | 50 |
| Wind | 1,408 | 88 | 0.0625 | 59 |

The gate fires on about six percent of evaluable minutes for each spacecraft
across this disturbed day. During the 10:00 UTC hour, the fractions were much
higher: DSCOVR 0.2759, ACE 0.2333, and Wind 0.2414.

## Multipoint support prevalence

There were 82 evaluable DSCOVR gate anchors far enough from the day boundaries
to search complete independent windows.

| Joint ACE-and-Wind support radius | DSCOVR gate anchors | Fraction |
|---|---:|---:|
| <= 1 minute | 22 / 82 | 0.2683 |
| <= 2 minutes | 35 / 82 | 0.4268 |
| <= 3 minutes | 42 / 82 | 0.5122 |
| <= 5 minutes | 53 / 82 | 0.6463 |
| <= 10 minutes | 64 / 82 | 0.7805 |
| <= 15 minutes | 77 / 82 | 0.9390 |

For the 10:59 candidate:

```text
ACE nearest qualifying minute:   10:59 UTC   offset  0 minutes
Wind nearest qualifying minute:  10:57 UTC   offset -2 minutes
nearest joint radius:             2 minutes

ACE strongest qualifying minute: 10:57 UTC   offset -2 minutes
Wind strongest qualifying minute:10:56 UTC   offset -3 minutes
strongest three-spacecraft span:  3 minutes
```

A strongest-candidate span of three minutes or less occurred for 12 of the 82
DSCOVR gate anchors (0.1463). The DSCOVR 10:59 gate score was exceeded or equaled
by 18 of 82 DSCOVR gate anchors (0.2195).

## Result

```text
SHARED_DISTURBED_INTERVAL_SUPPORTED
UNIQUE_COMMON_STRUCTURE_UNRESOLVED
PHYSICAL_CLASS_UNRESOLVED
PROPAGATION_NOT_CALCULATED
```

The candidate is not a DSCOVR-only telemetry wrinkle: ACE and Wind contain
nearby qualifying magnetic structure. However, the 45-degree/25-percent gate is
common enough on 2024-05-11 that existence inside a +/-15-minute window is not a
selective common-structure test. The previous one-bit support label must always
be accompanied by gate density, exact selected times, signed offsets, and the
within-day prevalence table.

The fractions above are descriptive for one highly disturbed day. They are not
a quiet-time false-positive rate, a p-value, or an independent null test.

## Next admissible test

Do not tune the gate after seeing this candidate. Preserve the frozen thresholds
and add a separately versioned geometry test:

1. obtain verified spacecraft positions and units for all three selected times;
2. choose stable intervals on both sides of each candidate;
3. estimate discontinuity normals with uncertainty and degeneracy checks;
4. test timing consistency with one moving surface;
5. retain `UNIQUE_COMMON_STRUCTURE_UNRESOLVED` whenever the normals, timing, or
   plasma ratios disagree.

The ephemeris/MVA stage must not inherit a common-surface conclusion from this
gate-density result.
