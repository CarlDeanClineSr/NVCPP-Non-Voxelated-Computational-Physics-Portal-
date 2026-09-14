# September 10 reporting maintenance

This change repairs future reporting, not past evidence. It is based on the
review of Roman readiness run 34447159834 and hourly run 34474914143 at source
commit c0f8ec8d55f9ae619caadc42b099f5daa77c8eed. Neither saved run is rewritten.

## Roman response capture versus page access

The previous report counted all five captured page responses as successes. The
saved response set actually included a NASA 404 and a Nexus final login URL.
Their bodies and hashes were valid captures, not five successful content visits.

Future manifests use version 1.2.0. `official_pages.summary` separates:

- captured responses;
- HTTP 2xx responses and non-2xx responses;
- explicit final-URL login-page captures (a subset of 2xx);
- other 2xx captures whose requested-content access remains unverified;
- transport errors, which are not captured responses.

Each captured page gets a `capture_state`. HTTP status, requested/final URL,
bytes, title, size and SHA-256 remain intact. Classification makes no extra
request and does not attempt authentication. Login recognition is limited to
explicit final-URL path segments (`login`, `signin`, `sign-in`, `log-in`); it is
not a general login-wall detector. A non-login URL and HTTP 2xx do not establish
that the intended content was delivered.

A captured non-2xx response makes a future watcher report `PARTIAL`; it does not
make the saved capture invalid and does not stop fixture generation or evidence
packaging. The pre-existing hard-failure condition for unavailable MAST transport
is unchanged. A login capture is recorded separately as the expected access
boundary; no authentication retry is attempted. `READY`/`PARTIAL` describe the
watcher, not the spacecraft or the availability of flight science.

## Archive wording

`NO_MATCHING_ROMAN_CAOM_ROWS` replaces the phase-loaded
`PRELAUNCH_NO_ROMAN_CAOM_HOLDINGS` for future reports. The other archive labels,
query construction, collection candidates, query limits, response parsing and
mission-phase calculation remain unchanged. Counts apply only to configured
queries and must be read with their response statuses and errors. No query count
establishes launch, commissioning, hardware state or the absence of all Roman
material from every archive. Consumers of the old label need to recognize this
new label; old manifests remain valid historical records.

## Unavailable hourly event counts

Companion PR #54 handles `RUN_LESSON.md`: an unavailable/failed evaluation must
not display a zero as though an event search succeeded. That PR is separate;
this Roman repair does not replace it or change the hourly calculation path.

## Action runtime maintenance

Only the two relevant provider workflow definitions are updated:

- checkout v4 -> v6;
- setup-python v5 -> v6 (Python remains 3.12);
- upload-artifact v4 -> v7 (ZIP upload defaults and 90-day retention retained);
- hourly cache restore/save v4 -> v5 (same keys and cache paths).

These majors support Node 24. Official action documentation:

- https://github.com/actions/checkout
- https://github.com/actions/setup-python
- https://github.com/actions/upload-artifact
- https://github.com/actions/cache

No trigger, permission, source URL, credential, cache key, scientific threshold,
or retention interval is changed. One offline reporting test is added to the
Roman validation step. Other workflows are outside this bounded runtime update.

## Tests and publication boundary

Offline tests cover HTTP errors, final login URLs, misleading login substrings,
exact body/hash preservation, count reconciliation, transport failures, and a
mocked five-response probe with an unchanged synthetic array/benchmark result.
Workflow checks retain the manual-only hourly trigger and the existing Roman
six-hour schedule. Tests do not fetch live MAST/NOAA measurements or open the
Gannon holdout.

No physics, canonicalization, baseline coverage rule, Gannon code/contracts,
Roman fixture generator, source catalog, or detector/matching threshold changes.
The 95% prior-24-hour acceptance rule stays unchanged. No old run is regenerated.

Review before merging. This PR itself runs deterministic CI only. Merging it to
main matches the existing Roman path-trigger and may start the normal public
readiness watch; that existing trigger is intentionally not disabled. The hourly
observatory and the Gannon holdout remain manual-only. No provider workflow was
manually dispatched to test this maintenance.
