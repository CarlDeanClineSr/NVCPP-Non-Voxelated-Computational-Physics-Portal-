# January 5, 2026 JWST / L1 Retrospective — Protocol V1

## Status

**RETROSPECTIVE, EXPLORATORY, NOT YET EXECUTED UNDER THIS PROTOCOL**

This protocol reopens an old question without importing the old conclusion. The historical words **“re-addressing”** and **“expansion event”** are retained only as labels for the idea that motivated the search. They are not accepted mechanisms, classifications, or results.

The archived repository `JWST-Solar-System-Re-address-Jan-5th` remains untouched as a historical record. This audit is performed in NVCPP so that current provenance, failure, and interpretation rules apply.

## Historical trigger

Old LUFT material contains several mutually inconsistent January 5 descriptions:

1. a 00:41–01:13 UTC solar-wind-compression narrative;
2. a partly overlapping 01:10–01:30 UTC “re-addressing” narrative;
3. a separate 15:04–15:06 UTC plasma discontinuity labeled ambiguous;
4. incompatible reported chi values, including 0.1498, 0.1500, and 0.9170;
5. unverified statements that JWST FGS lost lock and other spacecraft experienced simultaneous effects.

Those statements are historical leads only. None is imported as evidence.

## Audit findings that force a clean restart

The old `cme_heartbeat_logger.py` estimated chi from fixed reference values and then explicitly clamped the result to a maximum of 0.15. Therefore a pile-up at 0.15 and “zero violations” cannot establish a natural 0.15 boundary.

The archived JWST script requested NOAA rolling one-day products when it was executed months after January 5, but saved them with `Jan5` filenames. It did not establish that those NOAA rows came from January 5, and it did not calculate chi in its JWST/DSCOVR merge.

The old JWST Action artifact has expired. No prior CSV is accepted as evidence unless its original bytes and timestamps can be recovered and independently validated.

## Primary question

> Did independently archived measurements show an unusual heliospheric disturbance on January 5, 2026, and did JWST exhibit a temporally compatible attitude or guidance disturbance that remains unusual after ordinary spacecraft operations and control intervals are considered?

## Questions deliberately not answered by V1

V1 does not test or assert:

- expansion of space;
- a vacuum or substrate shift;
- a signal from Tabby’s Star or any “Schmidt Node”;
- superluminal propagation;
- a universal chi boundary;
- a CME striking JWST;
- a micrometeoroid strike;
- any physical mechanism.

A temporal coincidence, if found, is only a **candidate association**.

## Predeclared historical windows

The windows are frozen before retrieving new JWST telemetry:

### Window A — primary historical interval

`2026-01-05T00:35:00Z` through `2026-01-05T01:25:00Z`

This single 50-minute window contains the older 00:41–01:13 and 01:10–01:30 stories. They are not treated as two independent discoveries.

### Window B — secondary historical interval

`2026-01-05T14:55:00Z` through `2026-01-05T15:15:00Z`

This contains the older 15:04–15:06 “ambiguous” plasma-discontinuity report.

### Full-day audit

`2026-01-05T00:00:00Z` through `2026-01-06T00:00:00Z`

Any event found outside A or B is post-hoc within the day and must be labeled accordingly. A whole-day scan requires a multiple-search correction before statistical interpretation.

## Frozen controls

For each event-window shape, use the same UTC clock interval on these dates:

`2025-12-29`, `2025-12-30`, `2025-12-31`, `2026-01-01`, `2026-01-02`, `2026-01-03`, `2026-01-04`, `2026-01-06`, `2026-01-07`, `2026-01-08`, `2026-01-09`, `2026-01-10`, `2026-01-11`, `2026-01-12`.

No control date may be replaced because its result is inconvenient. Source failure remains in the denominator as `INCOMPLETE_CONTROL`.

## Evidence streams

### 1. Historical L1 magnetic field

Retrieve immutable historical data from NASA CDAWeb/HAPI, not NOAA rolling-current JSON:

- DSCOVR `DSCOVR_H0_MAG`, vector `B1GSE`;
- ACE `AC_H0_MFI`, vector `BGSEc`;
- Wind `WI_H0_MFI`, vector `B3GSE`.

For every source:

- preserve native response bytes;
- preserve endpoint, query, retrieval time, and SHA-256;
- require an explicit time field and three-vector field;
- calculate canonical magnitude from the vector components;
- preserve provider scalar magnitude only as audit information;
- never interpolate or forward-fill a detector input;
- preserve exclusions with reason codes.

The first-pass magnetic candidate rule is the already documented NVCPP rule: consecutive one-minute GSE vectors with rotation at least 45 degrees **or** relative magnitude change at least 25 percent. This is a candidate detector, not a physical classification.

### 2. JWST Engineering Database

Attempt MAST EDB retrieval for the historical mnemonic names:

- `SA_ZATTEST1` through `SA_ZATTEST4`;
- `SA_ZADUCMDX` and `SA_ZADUCMDY`.

Before interpreting a mnemonic, preserve and inspect its EDB dictionary entry. A name supplied by an old AI is not sufficient proof of engineering meaning.

Quaternion analysis must use normalized quaternion angular separation and must treat `q` and `-q` as the same attitude. Raw component z-scores alone are not an attitude disturbance detector.

FSM-command interpretation is permitted only if the EDB dictionary confirms the channel meaning. A command excursion is not, by itself, proof of an external force.

### 3. JWST observation and guide-star context

Query MAST observation metadata for the full day and preserve any available guide-star products or logs. Short exposure duration alone must not be called “loss of lock.” Planned slews, dithers, guide-star acquisition, visit transitions, momentum-management activity, instrument configuration, and ordinary guiding errors must be considered before an event is labeled unexplained.

### 4. Additional environment/context

GOES, geomagnetic indices, mission status records, and other spacecraft may be added only with source-specific contracts. They cannot be mixed into the primary result without documenting cadence, coordinate frame, timing semantics, quality flags, and independence.

## Timing and propagation

V1 does **not** assume that DSCOVR-to-JWST delay is exactly two hours. L1 and L2 separation, front orientation, solar-wind speed, spacecraft ephemerides, and Earth’s intervening environment must be modeled before a propagation lag is frozen.

V1 therefore reports absolute UTC event times first. A lagged association test is a later, separately declared stage.

## Candidate metrics

### L1 magnetic metrics

- vector rotation angle;
- relative magnitude change;
- data completeness;
- event count per frozen window;
- nearest support among DSCOVR, ACE, and Wind;
- strongest three-spacecraft span.

### JWST attitude metrics

- quaternion angular step in arcseconds;
- angular rate using actual time separation;
- robust median/MAD score;
- simultaneous support across quaternion and confirmed control channels;
- whether a candidate falls inside a documented operation transition.

### Association states

Only these states are allowed:

- `NO_PUBLIC_JWST_DATA`;
- `INCOMPLETE_SOURCE`;
- `NO_L1_CANDIDATE`;
- `L1_ONLY`;
- `JWST_ONLY`;
- `TEMPORAL_COINCIDENCE_CANDIDATE`;
- `EXPLAINED_BY_OPERATIONS`;
- `UNRESOLVED_AFTER_CONTROLS`.

V1 has no state named `READDRESSING_CONFIRMED`, `EXPANSION_CONFIRMED`, or equivalent.

## Statistical rules

- Event windows are compared with all frozen control windows.
- Report event rank, empirical percentile, and raw effect sizes.
- Preserve incomplete controls in the declared denominator.
- Do not convert a single 3-sigma point into a discovery.
- Do not search many lags, channels, and thresholds and then report only the best result.
- Any later lag search or threshold optimization requires a new protocol version and a new holdout.

## Conventional explanations to examine first

- commanded slew or target transition;
- guide-star acquisition or reacquisition;
- dither or instrument activity;
- momentum management;
- ordinary FGS/ACS anomaly;
- telemetry dropout, time conversion, or schema error;
- solar-wind structure with no JWST response;
- micrometeoroid impact;
- coincidence.

These are tests, not assumptions.

## Provenance and failure policy

- No simulation or demo data may enter an observational output directory.
- No current feed may be renamed as historical data.
- No missing source may be replaced silently.
- Every raw response receives a hash and manifest record.
- Every parser decision and exclusion receives a reason code.
- A successful GitHub Action means only that the declared steps completed; it does not mean the scientific hypothesis passed.
- The sealed Gannon V2 holdout, its registry, denominators, detector, and geometry state must remain untouched.

## Interpretation ladder

A result may move only one step at a time:

1. source bytes acquired;
2. source schema verified;
3. canonical measurements produced;
4. within-source candidate found;
5. multi-source temporal support found;
6. controls completed;
7. ordinary operational explanations evaluated;
8. unresolved association reported.

Only after step 8 would a new mechanism study be justified. Even then, “re-addressing” remains one hypothesis among alternatives until it yields a unique quantitative prediction and survives a prospective test.
