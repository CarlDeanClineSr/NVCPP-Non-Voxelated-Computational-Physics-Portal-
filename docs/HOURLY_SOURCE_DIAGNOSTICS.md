# Hourly source failure diagnostics

This is a diagnostic-only change. The canonical baseline algorithm, source
admission rules, one-cadence SOLAR-1 boundary tolerance, 24-hour prior-only
window, 95% coverage requirement, detector thresholds, NOAA rolling-state
persistence workflow and Gannon V2 holdout are unchanged.

## Stable reason codes

- `BASELINE_WARMUP`: existing canonical output has no full elapsed prior window.
- `BASELINE_INSUFFICIENT_COVERAGE`: full-window analysis rows exist, but their
  existing canonical sample counts fail the coverage requirement.
- `BASELINE_NONPOSITIVE`: sufficient coverage, but the baseline is not positive.
- `BASELINE_UNAVAILABLE`: empty analysis or mixed baseline failure states;
  individual status counts are retained rather than guessing one explanation.
- `SOURCE_PREROLL_INCOMPLETE`: first retained physical sample violates the
  existing requested-start boundary and its cadence tolerance.
- `SOURCE_END_INCOMPLETE`: last retained sample violates the existing exclusive
  end boundary and its cadence tolerance.
- `SOURCE_EXCEPTION`: unclassified failure; not automatically blamed on a
  provider or relabeled as a coverage problem.

`reason_code` and JSON-native `diagnostics` are carried from shared exceptions
into mission manifests, the observatory manifest, `status/latest.json`,
`result_index.jsonl`, and the human Actions summary. NOAA's already-recorded
quarantine decisions are saved on failure too; absent output must not imply
that no rows were quarantined. Human event counts read "unavailable (source
failed)" on failure. Legacy numeric counts remain for compatibility, with
`evaluation_state: UNAVAILABLE`; they are not a measured absence of events.

The coverage report reads the core's existing `baseline_sample_count`,
`baseline_expected_samples`, `baseline_coverage_fraction`, and `baseline_status`.
It does not compute a second baseline, fill timestamps, or change row admission.
Its best window is restricted to evaluated analysis rows and prefers rows with
a full elapsed baseline. It explicitly excludes the current sample, reports
the prior window's boundaries, and keeps sample units for non-minute cadences.

## Requested, effective, raw and retained time boundaries

The hourly SOLAR-1 wrapper already shifts its fixed-duration window to the
provider-advertised end. This selection is unchanged. The wrapper now retains
its global requested window, shifted effective window, provider availability
and freshness on failed runs as well as successful ones.

Provider `stopDate` alone does not explain a missing pre-roll start. The report
separates raw-response timestamps from retained physical-row timestamps after
quarantine. Start gaps and end gaps are measured independently against the
actual effective request, preserving the existing one-cadence tolerance.

## Run #219 read-only replay

Source: GitHub Actions run `33966339056`, artifact `9969540914`, original commit
`0803fb266a42b9ce3618aa6136de1b1b8abd3200`. The following is a retrospective
read-only diagnostic replay of saved inputs, not a new acquisition or science
result.

NOAA's saved baseline input has 1,626 rows over the requested 30-hour span. The
unchanged core produces 318 evaluated analysis rows, all with
`INSUFFICIENT_COVERAGE`. The best evaluated prior-only window is
`[2026-09-04T07:52:00Z, 2026-09-05T07:52:00Z)`: 1,331/1,440 samples (92.4306%),
requiring 1,368 for 95%, a deficit of 37 one-minute samples.

SOLAR-1 advertises through `2026-09-04T23:59:00Z`. The wrapper's effective
retrieval was `[2026-09-03T18:00:00Z, 2026-09-05T00:00:00Z)`, not an unshifted
request through noon. Raw rows begin at 18:00; after the original 135 quarantined
rows are excluded, the first retained row is 18:07. The retained-start gap is
420 seconds, 360 seconds beyond the existing 60-second tolerance. The last
retained row is September 4 at 23:59, so the effective end is covered. Provider
lag and this retained-start gap are distinct facts.

Original artifact SHA-256 references:

```
NOAA baseline input:
722aeac1cea5a4fc7c6ce9fadc24ee957c7857e2a7e08e7d2dfea704bc8d09a6
SOLAR-1 raw CSV:
dae3b2827f499493b5e9852d4234e7590a6926a8c2eaf72b6cbae6d80674e2c0
SOLAR-1 quarantine CSV:
bbdc3605fe0901cc2cf3f7686774e23b999876f70ba1666b20d06eaa5dc067ca
SOLAR-1 HAPI info:
54fad72c91b06cacd4da0ab9df8fa22209cd56a4c7ae077b167bc49f3427f9f5
```

## Regression scope

Offline fixtures cover exact deficits; 1,367-fails/1,368-passes at 95%; real
warm-up; nonpositive and empty baselines; non-minute units; prior-only boundary
semantics; raw versus retained timestamps; start and end gaps; unchanged
one-minute SOLAR-1 tolerance; preservation of NOAA cache/quarantine on failure;
failed-run effective windows; propagation through summary formats; and
preservation of the original failure when a diagnostic manifest is unreadable.

These fixtures are synthetic engineering tests, not observational evidence or
fallback source data. The patch does not supply missing data, make a failing
source pass, or guarantee that the next scheduled Action will be green.

## Separate cold-start ingestion issue exposed by the new tests

The first full CI run (33968119076, job 101311730444) passed 165 tests and failed
one new NOAA fixture before reaching its baseline diagnostic. With an entirely
empty configured cache under the locked pandas 3 environment, the unchanged
`_merge_operational_history` concatenates an empty object-typed frame with current
rows. Its plasma `time` column can remain object-typed, while the canonical core
converts magnetic time to a timezone-aware datetime. The unchanged plasma merge
then raises a datetime/object-key mismatch. This is not #219's populated-cache
coverage failure and is not caused by the diagnostic changes.

The coverage/persistence regression now explicitly starts from a populated
cache, as #219 did. The cold-start case remains as a separate strict expected-
failure regression: only the observed datetime/object merge error is accepted
as an expected failure; other errors still fail CI, and a future fix must remove
the marker. It is deliberately NOT counted as a passing test. No history-merge
or plasma-pairing logic was changed to hide it in this diagnostic-only patch.
