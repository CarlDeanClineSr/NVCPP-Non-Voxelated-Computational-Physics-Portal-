# Legacy Gannon Control Selector

`historical/select_gannon_control_intervals.py` is retained only for provenance of the V1 development path.

Its GitHub Actions workflow was retired after PR #33 because that selector depends on NASA DONKI IPS availability and can fail on transient provider errors such as HTTP 503. The failure does not alter the frozen detector or any accepted control result.

The active control-selection path is the later frozen OMNI/event-class workflow merged through PRs #29, #30, and #31, followed by the hard-null harness merged through PR #35 and the V2 prospective holdout preregistration merged through PR #37.

Do not use this legacy selector to populate the V2 holdout registry. V2 execution is tracked in Issue #36 and must preserve its preregistered detector, clustering statistic, interval-count requirements, failure denominators, and no-MAG-before-freeze boundary.

The legacy source file remains in the repository so the development history is inspectable. Retiring its workflow prevents an external DONKI service outage from appearing as a current NVCPP science or CI failure.
