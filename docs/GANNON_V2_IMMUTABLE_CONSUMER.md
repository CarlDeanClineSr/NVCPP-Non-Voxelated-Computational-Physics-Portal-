# Gannon V2 Immutable Holdout Consumer

## State before execution

The consumer is bound to exactly one committed registry:

```text
registry file SHA-256   8c1510e026aa68dca21d42181bbe8a2fe1876a9738426be1378605f8bfe947af
registry content hash  d6db5682347b958d2f3f9b26c404563c6cb97f1c514525ae30c7c797fa2b8e7b
class denominators      12 / 12 / 12 / 7
total intervals         43
```

The unequal COMPLEX denominator is the result of the catalog-plus-spacing
selector and Amendment 1. It is not filled with substitute dates.

## What the consumer may do

After manual authorization on `main`, it may:

1. verify the registry, amendment, publication state, inventory, and hashes;
2. emit the exact 43-row matrix without adding, deleting, or replacing a date;
3. retrieve the pinned DSCOVR, ACE, and Wind magnetic products;
4. preserve source responses, metadata, canonical rows, exclusions, and hashes;
5. apply the unchanged one-minute GSE detector;
6. score the unchanged nearest-support and strongest-span definition;
7. run deterministic circular-shift, moving-block, and registered-day mismatch
   controls;
8. report registered and evaluable denominators class by class;
9. write a machine-readable capsule and compact tables.

## What it may not do

The consumer may not:

- change a registry date or class;
- replace a failed product with a neighboring date;
- change the 45-degree or 25-percent gate;
- change exact `t-1 minute` timing;
- change the radii `[1, 2, 3, 5, 10, 15]`;
- change the primary `nearest <=2 AND strongest span <=3` rule;
- interpolate or forward-fill;
- silently discard an unavailable interval;
- calculate ephemeris, MVA, propagation, or a common surface;
- assign a physical discontinuity class;
- commit raw telemetry or heavy run products to Git.

A provider failure or inadequate predeclared completeness becomes
`INCOMPLETE_MULTIPOINT` and stays in its frozen class denominator.

## Canonical interval rule

Registry timestamps are preserved exactly. The canonical one-minute analysis
starts at the first whole UTC minute at or after the registered start and ends
before the correspondingly rounded stop. One predecessor minute is retrieved so
the first analysis minute may satisfy the exact `t-1` rule. The predecessor is
not itself scored.

This rule matters for registry entries whose independently selected midpoint
falls on a half-minute.

## Completeness declared before MAG inspection

An interval is evaluable only when:

```text
per-mission exact-previous-minute fraction >= 0.90
three-mission common exact-previous fraction >= 0.80
ACE coverage in an anchor's +/-15 minute window >= 0.80
Wind coverage in an anchor's +/-15 minute window >= 0.80
```

An anchor failing the window-coverage rule is excluded from the support
statistic and reported as coverage-excluded. It is not counted as a negative
support observation.

A class is sufficient for the first capsule only when at least 60 percent of its
registered intervals are evaluable. The registered denominator never changes.

## Manual-only Actions workflow

The workflow has no `push` or `pull_request` trigger. Pull-request CI therefore
cannot retrieve holdout MAG.

To execute after merge, open **Actions → NVCPP Gannon V2 Immutable Holdout
Consumer → Run workflow**, select `main`, set the execution confirmation to
`true`, and paste the full registry file SHA-256 shown above.

The workflow runs at most three interval retrieval jobs concurrently, packages
each interval independently, reconciles all 43 registry rows, and writes the
final capsule as an Actions artifact. It does not write results back to the
repository.

## Interpretation boundary

The capsule reports empirical control frequencies under the declared
constructions. Those frequencies are not independent-minute probabilities.
Neither a favorable nor an unfavorable comparison identifies one moving
surface. Geometry remains `CLOSED` after this workflow.
