# NVCPP Hourly Observatory

## Purpose

The hourly observatory turns NVCPP into a continuously operating, evidence-preserving physics monitor. It is designed to catch solar-wind structures, preserve the original provider records, evaluate the unclipped magnetic and plasma state, generate charts, and create evidence-first teaching capsules.

The current hourly sources are:

1. **NOAA SWPC operational L1 feed** — near-real-time magnetic and plasma context. The endpoint may use the provider-selected active upstream spacecraft, so it is intentionally not labeled as an independent DSCOVR measurement unless identity is separately resolved.
2. **SOLAR-1 MAG science-quality HAPI** — independently identified SOLAR-1 GSE magnetic vectors governed by the frozen source contract.

Definitive DSCOVR CDAWeb replication remains a separate historical/daily path because its high-resolution product is larger and may have different publication latency.

## Schedule

The workflow runs at minute 17 of every UTC hour:

```text
17 * * * *
```

Each scheduled run uses the latest whole UTC hour that was complete before a provider safety delay. The default package retrieves 30 hours, calculates the prior-only 24-hour median baseline, retains six hours of current analysis, and focuses event detection on the latest hour.

## Evidence package

Every run is immutable and receives a unique path:

```text
runs/hourly/YYYY/MM/DD/HH/nvcpp-hourly-<UTC>-run-<ACTIONS_ID>/
```

A run package can contain:

```text
observatory_run_manifest.json
result_index.jsonl
RUN_LESSON.md
status/latest.json
status/LATEST.md
missions/<source>/raw provider responses
missions/<source>/canonical CSV
missions/<source>/quarantine CSV
missions/<source>/source manifest
missions/<source>/observatory/event_candidates.json
missions/<source>/observatory/event_candidates.csv
missions/<source>/observatory/charts/*.png
missions/<source>/observatory/capsules/*.md
missions/<source>/observatory/capsules/*.json
drive_upload_receipt.json
```

## Candidate physics

The detector preserves multiple physical views rather than using χ alone:

- signed `delta_B24M` for compression versus depression;
- absolute `chi_B24M` for severity;
- Bx, By, and Bz for field rotation;
- magnetic magnitude for rapid jumps;
- density, speed, temperature, dynamic pressure, proton beta, Alfvén speed, and Alfvén Mach number when verified plasma data are available.

Default candidate triggers are configuration values, not universal laws:

```text
χ research watch      >= 0.15
significant |Δ|       >= 0.50
severe χ              >= 1.00
vector rotation       >= 45 degrees
one-minute |B| change >= 25 percent
```

The 0.15 value remains a research watch threshold so its actual frequency, persistence, mission dependence, quiet-state behavior, and event selectivity can be tested rather than assumed.

## Event states

Every automatically generated event starts as:

```text
CANDIDATE_UNRESOLVED
```

The capsule states what was measured, what does not follow, and which independent tests are needed. It never converts an unusual number into a mechanism or discovery by prose alone.

## Failure behavior

- One source can fail while another source still produces a degraded evidence package.
- If all configured sources fail, the workflow becomes red after preserving the failure manifest and any partial evidence.
- Google Drive failure becomes fatal only after Drive credentials have been configured. Until then, the workflow preserves a 90-day GitHub artifact and writes a `DRY_RUN` Drive receipt.

## Storage rule

```text
GitHub = code, tests, workflows, and temporary evidence artifact
Drive  = durable raw, canonical, quarantine, charts, capsules, and manifests
```

GitHub-hosted artifacts are temporary. The durable vault is `LUFT CLINE/NVCPP_DATA_VAULT`.
