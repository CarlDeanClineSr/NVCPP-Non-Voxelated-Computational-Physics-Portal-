# Frozen MAG Gate Event-Class Controls

Status: **COMPLETE**

The 45-degree rotation / 25-percent magnitude-change gate and all timing
radii are unchanged. Control dates were frozen from OMNI/event metadata
before this module retrieved any DSCOVR, ACE, or Wind gate output.

## Interval results

```text
                                                                interval_id              control_class                                 label                 start_utc                  stop_utc  dscovr_gate_fraction  ace_gate_fraction  wind_gate_fraction  dscovr_gate_anchor_rows  strongest_span_le_3_minutes_fraction  strongest_span_median_minutes  joint_support_fraction_within_1_minutes  joint_support_fraction_within_2_minutes  joint_support_fraction_within_3_minutes  joint_support_fraction_within_5_minutes  joint_support_fraction_within_10_minutes  joint_support_fraction_within_15_minutes
                            LOW_ACTIVITY__20240113__LOW_ACTIVITY_2024-01-13               LOW_ACTIVITY               LOW_ACTIVITY_2024-01-13 2024-01-13T00:00:00+00:00 2024-01-14T00:00:00+00:00              0.003953           0.003475            0.001436                        5                              0.000000                           15.0                                 0.000000                                 0.000000                                 0.000000                                 0.000000                                  0.000000                                  0.200000
                            LOW_ACTIVITY__20240203__LOW_ACTIVITY_2024-02-03               LOW_ACTIVITY               LOW_ACTIVITY_2024-02-03 2024-02-03T00:00:00+00:00 2024-02-04T00:00:00+00:00              0.012186           0.015983            0.007077                       17                              0.000000                           13.0                                 0.000000                                 0.058824                                 0.117647                                 0.176471                                  0.529412                                  0.647059
                  MODERATE_ACTIVITY__20240104__MODERATE_ACTIVITY_2024-01-04          MODERATE_ACTIVITY          MODERATE_ACTIVITY_2024-01-04 2024-01-04T00:00:00+00:00 2024-01-05T00:00:00+00:00              0.009456           0.013899            0.009901                       12                              0.000000                           13.0                                 0.000000                                 0.000000                                 0.000000                                 0.083333                                  0.250000                                  0.416667
                  MODERATE_ACTIVITY__20240123__MODERATE_ACTIVITY_2024-01-23          MODERATE_ACTIVITY          MODERATE_ACTIVITY_2024-01-23 2024-01-23T00:00:00+00:00 2024-01-24T00:00:00+00:00              0.039725           0.037283            0.040000                       52                              0.068966                           14.0                                 0.076923                                 0.192308                                 0.288462                                 0.384615                                  0.500000                                  0.557692
                        ISOLATED_SHOCK__20240628__ISOLATED_SHOCK_2024-06-28             ISOLATED_SHOCK             ISOLATED_SHOCK_2024-06-28 2024-06-28T00:00:00+00:00 2024-06-29T00:00:00+00:00              0.040362           0.043780            0.043412                       57                              0.021739                           12.0                                 0.228070                                 0.350877                                 0.403509                                 0.438596                                  0.701754                                  0.807018
MILD_OR_GLANCING_STRUCTURE__20240928__MILD_OR_GLANCING_STRUCTURE_2024-09-28 MILD_OR_GLANCING_STRUCTURE MILD_OR_GLANCING_STRUCTURE_2024-09-28 2024-09-28T00:00:00+00:00 2024-09-29T00:00:00+00:00              0.032233           0.027797            0.028633                       42                              0.000000                           11.0                                 0.000000                                 0.000000                                 0.023810                                 0.095238                                  0.357143                                  0.452381
    GANNON_DEVELOPMENT_EVENT__20240511__GANNON_DEVELOPMENT_EVENT_2024-05-11   GANNON_DEVELOPMENT_EVENT   GANNON_DEVELOPMENT_EVENT_2024-05-11 2024-05-11T00:00:00+00:00 2024-05-12T00:00:00+00:00              0.060014           0.057679            0.062500                       82                              0.155844                            9.0                                 0.268293                                 0.426829                                 0.512195                                 0.646341                                  0.780488                                  0.939024
```

## Failed intervals

```json
[]
```

## Result states

- `EVENT_CLASS_CONTROL_DISTRIBUTIONS_MEASURED`
- `GEOMETRY_REMAINS_BLOCKED`

These distributions do not reopen the Gannon 10:59 interpretation. Hard
circular-shift/mismatched-day nulls remain separate, and geometry remains
blocked until both control stages are complete.

## Interpretation limits

- the 45-degree and 25-percent thresholds are unchanged from the Gannon development event
- control dates were selected before spacecraft MAG gate retrieval
- within-day and cross-day prevalence values are descriptive empirical distributions, not independent-minute binomial probabilities
- no interpolation or forward fill is allowed
- each vector diagnostic requires an exact preceding canonical minute
- GSE rotation is not a GSM clock angle or geoeffectiveness claim
- mismatched-day and circular-shift controls remain a separate hard-null stage
- event-class controls do not establish one common moving surface
- ephemeris, MVA, propagation, and physical classification remain blocked
