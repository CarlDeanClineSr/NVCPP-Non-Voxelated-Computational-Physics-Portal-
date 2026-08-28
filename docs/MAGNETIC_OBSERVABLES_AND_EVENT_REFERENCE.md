# NVCPP Magnetic Observables and Frozen Event References

## Canonical magnetic scalar

For DSCOVR, the canonical one-minute vector is formed by averaging the native GSE
components within each admitted minute and then calculating magnitude:

```text
Bx_1m = mean(native Bx_GSE)
By_1m = mean(native By_GSE)
Bz_1m = mean(native Bz_GSE)
B_abs_GSE_1min = sqrt(Bx_1m^2 + By_1m^2 + Bz_1m^2)
```

A DSCOVR minute requires at least 57 of 60 native samples.  SOLAR-1 uses NOAA's
science-quality official one-minute GSE component product and calculates the same
vector magnitude from the named X, Y, and Z fields.

The canonical observable identifier is:

```text
chi_B24M_absB_GSE_1min
```

This identifier names the quantity, coordinate frame, cadence, and transformation.
It is not a generic plasma-state or geoeffectiveness index.

## Rolling baseline boundary

The implemented baseline interval is:

```text
[t - 24 hours, t)
```

or equivalently:

```text
B0(t) = median{B(tau): t - 24 h <= tau < t}
```

The left endpoint is included and the current sample is excluded.  At one-minute
cadence the declared window contains 1,440 prior samples.  A row is canonical only
after a full elapsed window, at least 95% declared-cadence coverage, and a finite
positive baseline.

## Canonical outputs

```text
ratio_B24M = B / B0
delta_B24M = (B - B0) / B0
chi_B24M = abs(delta_B24M)
```

The three outputs are never substituted for one another.

## Named thresholds

Thresholds are candidate-state labels, not mechanism declarations:

```text
CHI_B24M_ABSB_GSE_1MIN_RESEARCH_WATCH
  chi_B24M >= 0.15

DELTA_B24M_ABSB_GSE_1MIN_COMPRESSION_CANDIDATE
  delta_B24M >= 0.50

DELTA_B24M_ABSB_GSE_1MIN_DEPRESSION_CANDIDATE
  delta_B24M <= -0.50

CHI_B24M_ABSB_GSE_1MIN_SEVERE_DEPARTURE
  chi_B24M >= 1.00
```

For a positive compression, `delta_B24M = 1` means `B = 2 * B0`.  Because chi is
absolute, `chi_B24M = 1` is not uniquely a doubling rule: a sufficiently deep
magnetic depression can also approach that value.  Signed delta must accompany
chi in every interpretation.

The 0.15 level is a research watch, not a unique shock-onset detector.

## What magnitude cannot say

`chi_B24M_absB_GSE_1min` measures field-magnitude departure.  It does not by itself
represent:

```text
southward Bz
clock angle
field rotation
shock identity
magnetic-ejecta boundaries
geoeffectiveness
storm phase
```

Those require the separately preserved GSE components, signed delta, vector
rotation, plasma variables, event timing, and where appropriate conversion to an
explicitly verified GSM product.

## Frozen event reference

A long-lived event can occupy enough of the trailing 24-hour window that the live
median changes regime.  NVCPP therefore permits a secondary retrospective
reference:

```text
event_reference_B = canonical B0 at an exact, baseline-valid pre-event row
delta_event_reference = (B - event_reference_B) / event_reference_B
chi_event_reference = abs(delta_event_reference)
```

The frozen reference answers persistence relative to the selected pre-event state.
It never replaces `B0`, `delta_B24M`, or `chi_B24M`, and its outputs retain the
`event_reference` namespace.

Reference selection must preserve:

```text
exact UTC timestamp
canonical source artifact and SHA-256
VALID rolling baseline state
positive finite reference value
selection rationale
```

## Clock angle

When a Y-Z angle is emitted from GSE components, its name is explicit:

```text
clock_angle_gse_yz_deg = atan2(By_GSE, Bz_GSE), normalized to [0, 360)
```

A GSE Y-Z angle is not silently relabeled as a GSM geoeffectiveness quantity.

## Event-local integrity gate

A minute-level timing statement requires a local integrity gate in addition to
24-hour baseline coverage.  The retrospective default uses a centered 11-minute
window and requires:

```text
every canonical minute present
each DSCOVR minute retains at least 95% native coverage
every row has a VALID canonical baseline
no duplicate timestamp
```

Passing this gate establishes data continuity only.  It does not classify a
shock, sheath, ejecta, or physical mechanism.

## Gannon interpretation

The May 2024 DSCOVR test shows both properties of the architecture:

1. The rolling metric responds at the L1 shock-scale increase rather than hours
   later.
2. After sustained elevated field occupies enough of the trailing window, the
   live median adapts and live chi can become small even while a frozen pre-shock
   comparison remains large.

That is expected behavior for a moving relative-departure measure.  Event-phase
analysis must preserve both live and frozen diagnostics rather than forcing one
number to answer both questions.

## SOLAR-1 mission phase

The fixed June 1-5, 2026 SOLAR-1 regression precedes NOAA's declared operational
start on June 10, 2026.  Its manifest/report phase is therefore:

```text
PRE_OPERATIONAL_COMMISSIONING_REGRESSION
```

The product may still be NOAA science-quality telemetry, but that interval cannot
be used as evidence of post-transition operational performance.  Intervals wholly
after the declared date are labeled `OPERATIONAL`; intervals crossing it are
labeled `TRANSITION_SPANNING_OPERATIONAL_START`.

## Pairing decision gates

Current MAG coherence classification uses frozen numerical rules:

```text
coherence candidate:
  best Pearson r >= 0.70
  max-lag circular-shift null p <= 0.01

resolved lag candidate additionally requires:
  improvement over zero lag >= 0.02
  99.5% peak plateau width <= 3 lag values
  bootstrap winning-lag mode fraction >= 0.60
  bootstrap 95% lag span <= 2 minutes
  daily segment lag span <= 2 minutes
```

Anything coherent that fails the lag-stability gates remains
`COHERENT_BUT_LAG_UNRESOLVED`.  Ephemeris is still required before interpreting a
lag candidate as propagation.
