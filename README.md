# NVCPP — Non-Voxelated Computational Physics Portal

**Program state report and operator guide**  
**State snapshot:** August 29, 2026  
**Creator and project lead:** Carl Dean Cline Sr.

NVCPP is an evidence-preserving telemetry, observatory-readiness, and cross-mission analysis system. It is not one equation, one event claim, or one spacecraft downloader. It is a Python program for acquiring measurements, proving where they came from, preserving the original responses, rejecting malformed or mismatched data, calculating reversible physical observables, comparing independent instruments, generating charts and teaching capsules, and recording both positive and contrary results.

The central rule is simple:

> The numerical record, source contract, exclusions, and hashes outrank the prose written about them.

NVCPP is the clean successor to the useful parts of the LUFT Portal. It keeps the LUFT learning-loop idea—observe, calculate, chart, report, question, and return—but excludes the old repository bulk and prevents unrelated data domains from being mixed together.

---

## 1. What the program is for

NVCPP is being built to provide an independent, repeatable second analysis of physical data without silently clipping, replacing, smoothing, or relabeling valid measurements.

It is designed to answer questions such as:

- What did the provider actually return?
- Which spacecraft, instrument, product, coordinate frame, cadence, and units produced the record?
- Which rows were admitted, rejected, or quarantined, and why?
- How far did the measured state depart from its prior state?
- Did the change have a sign, direction, rotation, density, pressure, or temperature context?
- Did another independently identified instrument observe related structure?
- Does a candidate remain unusual after controls that break simultaneity?
- What is still unknown after the calculation?

NVCPP is also intended to operate as a teaching engine. Each run can become a compact lesson containing the source evidence, transformations, charts, candidate detections, contrary results, and unresolved questions that should be revisited later.

---

## 2. Two domains with a hard firewall

NVCPP contains two related automation domains. They share provenance, testing, storage, and reporting tools, but they do not share physical equations.

| Domain | Present sources | What it evaluates | Boundary |
|---|---|---|---|
| **L1 space weather** | NOAA SWPC operational L1, DSCOVR, SOLAR-1, ACE, Wind | Magnetic vectors, magnetic magnitude, rolling and event-local departures, plasma context when verified, cross-spacecraft similarity, gate selectivity, and null controls | Only verified time-series telemetry may enter L1 equations |
| **Astronomical observatories** | Roman readiness and authenticated export intake | Archive registration, bounded metadata, image fixtures, source detection, matching, completeness, purity, and anomaly records | Telescope arrays are never labeled `chi_B24M` and never enter L1 plasma equations |

JWST and HST remain reserved for the astronomical-observatory domain. Mission-specific production adapters for them have not yet been implemented.

---

## 3. Current program state

| Component | State | What that means now |
|---|---|---|
| Repository CI and security scan | **Operational** | Python compilation, contract validation, repository credential scanning, and the test suite run on repository changes |
| Hourly observatory | **Active** | Scheduled at minute 17 of each UTC hour; produces an evidence package even when one source is degraded |
| NOAA operational rolling state | **Active** | A bounded 36-hour cache survives separate GitHub runners and preserves provider-revision counts |
| Google Drive publisher | **Conditional** | A non-writing preflight checks credentials and destination access; durable upload occurs only when that check passes |
| DSCOVR historical MAG | **Operational** | NASA CDAWeb source bytes, strict parsing, component-first one-minute canonicalization, quarantine, manifests, and unclipped physics |
| SOLAR-1 MAG | **Operational under its frozen contract** | Historical and hourly one-minute GSE magnetic processing; early commissioning intervals retain their mission-phase labels |
| SOLAR-1 SWiPS | **Discovery only** | Public product and schema discovery exist; SOLAR-1 plasma calculations remain disabled until a verified product is available |
| NOAA SWPC plasma context | **Operational but provider-selected** | Useful near-real-time plasma context; not treated as an independently identified DSCOVR, ACE, IMAP, or SOLAR-1 measurement without separate identity proof |
| L1 MAG coherence engine | **Operational research tool** | Exact UTC overlap, no interpolation, lag scan, peak width, moving-block uncertainty, and circular-shift controls |
| Event-reference overlay | **Operational audit tool** | Keeps the live 24-hour observable and a separate automatically frozen pre-event reference |
| Gannon multipoint controls | **Implemented** | DSCOVR, ACE, and Wind gate density, timing support, plasma slices where available, and hard controls |
| Gannon V2 prospective holdout | **Frozen and ready; not dispatched** | The 43-date registry and consumer are merged, hash-locked, manual-only, and still sealed from holdout MAG inspection |
| Roman readiness and truth recovery | **Implemented** | Six-hour archive watch, bounded public queries, page hashing, deterministic fixtures, truth matching, and authenticated-export inventory |
| IMAP mission-specific adapter | **Not enabled** | No canonical mission-specific production path yet |
| JWST/HST production adapters | **Not enabled** | Domain reserved; no live archive pipeline presently claimed |

At this snapshot, the post-merge `main` CI completed successfully, and the next scheduled hourly observatory run on the same `main` state also completed successfully.

---

## 4. What has been built and repaired

### 4.1 Clean foundation

The project was separated from the legacy `luft-portal` bulk and rebuilt as a smaller code-control repository. GitHub stores code, tests, contracts, compact audit records, and temporary artifacts. Heavy telemetry, FITS files, charts, and long-term evidence belong in the Drive vault or a mission cloud environment.

### 4.2 Unclipped magnetic path

Legacy clipping, capping, saturation behavior, generic `chi` layouts, and hidden denominator repair are prohibited in the clean path. The code keeps three different quantities separate:

```text
ratio_B24M = B / B0
delta_B24M = (B - B0) / B0
chi_B24M = abs(delta_B24M)
```

A large valid measurement remains large. A missing, invalid, or suspect record is named and quarantined rather than forced into range.

### 4.3 Source identity before physics

The source adapters now fail closed on product identity, schema, cadence, units, coordinate frame, fill values, and declared quality rules. This repaired the class of failures that previously allowed terrestrial-scale magnetic values, generic `F/B/baseline/chi` tables, malformed rows, or wrong product assumptions to reach downstream calculations.

### 4.4 DSCOVR historical ingestion

The DSCOVR path was rebuilt around NASA CDAWeb source responses and header-based parsing. It now:

- preserves raw provider bytes and response metadata;
- rejects CDAWeb fill sentinels and invalid timestamps;
- averages native GSE vector components within each UTC minute;
- computes magnitude from those component means;
- retrieves the required prior interval for a complete baseline;
- preserves quarantine counts, hashes, and run manifests;
- never averages an already-derived `chi` series and calls it canonical.

### 4.5 SOLAR-1 discovery-first ingestion

SOLAR-1 was implemented by interrogating the NOAA endpoints before writing physics assumptions. The repository froze the discovered product identity, GSE parameter names, units, cadence, and fill behavior in a machine-readable contract. Exact-zero vectors are preserved as named suspect records rather than silently admitted.

### 4.6 Continuous hourly operation

The hourly observatory now restores a bounded NOAA rolling cache, retrieves new records, preserves provider revisions, rebuilds the prior-only baseline, evaluates recent telemetry, creates candidate records, produces charts and teaching capsules, and uploads a temporary 90-day GitHub evidence package on every run that reaches the packaging stage.

### 4.7 Durable storage boundary

Google Drive publication was separated from the physics run. A no-write preflight validates the credential shape, Drive API access, and destination-folder capability before telemetry is processed. A Drive failure cannot erase the telemetry result: the evidence remains in the GitHub artifact, while a configured but broken vault is still reported as an operational failure.

### 4.8 Live versus frozen event reference

The Gannon work showed that a rolling 24-hour median and a frozen pre-event reference answer different questions. NVCPP therefore keeps both:

```text
live chi_B24M
    How unusual is |B| relative to the recent prior 24-hour state?

frozen chi_event_ref_absB
    How unusual is |B| relative to the last valid pre-gate baseline?
```

The frozen reference never overwrites the live observable. Its reference time is derived automatically from the last valid row before the named gate.

### 4.9 Multipoint restraint and controls

The first three-spacecraft Gannon comparison showed that a wide timing window can make support too easy to obtain during a disturbed interval. NVCPP responded by preserving the original detector, measuring its gate density, tightening the interpretation rather than rewriting history, and building quiet, moderate, isolated, complex, circular-shift, and mismatched-day controls.

The surviving Gannon interpretation remains narrow: multiple L1 magnetometers observed real vector structure in the same disturbed interval, but the available records did not by themselves establish one common moving surface or a unique physical class.

### 4.10 Prospective V2 holdout

The V2 registry was selected without inspecting its spacecraft MAG values. The first attempt to fill every class equally failed honestly: only seven qualifying complex-ejecta intervals survived the independent catalog and spacing rules. Amendment 1 changed only the allowed class denominator and preserved the underfill record.

The frozen registry is now:

```text
QUIET_SOLAR_WIND              12
MODERATE_VARIABILITY          12
ISOLATED_SHOCK_OR_SHEATH      12
COMPLEX_INTERACTING_EJECTA     7
TOTAL                         43
```

Registry file SHA-256:

```text
8c1510e026aa68dca21d42181bbe8a2fe1876a9738426be1378605f8bfe947af
```

The immutable consumer verifies this exact registry before any network call, retains failed intervals in the registered denominator, executes the unchanged detector and clustering statistic, runs the preregistered nulls, and keeps geometry closed.

### 4.11 Source-specific magnitude provenance

The three Gannon holdout products are not treated as interchangeable scalar-magnitude products:

| Mission | Canonical vector | Provider scalar | Scalar role |
|---|---|---|---|
| DSCOVR | `B1GSE` component means | none requested | not applicable |
| ACE | `BGSEc_x/y/z` component means | `Magnitude` | audit only |
| Wind | `B3GSE_x/y/z` component means | `B3F1` | audit only |

For every mission, canonical magnitude is computed only as the Euclidean norm of the three one-minute component means. Synthetic tests deliberately supply false provider scalar magnitudes and verify that those values cannot enter canonical `B_mag_nT`.

### 4.12 Roman readiness without cross-domain contamination

Roman support was added as a separate observatory-readiness path. It can watch MAST registration and bounded metadata, hash official pages, run deterministic image fixtures, measure source-recovery completeness and purity, and inventory authenticated Roman Research Nexus or I-Sim exports. It cannot infer launch success from a clock, cannot call a synthetic fixture flight data, and cannot route an image through L1 plasma equations.

---

## 5. Canonical magnetic observable

For the current component-based magnetic paths:

```text
Bx_1min(t) = mean of valid native Bx_GSE samples in minute t
By_1min(t) = mean of valid native By_GSE samples in minute t
Bz_1min(t) = mean of valid native Bz_GSE samples in minute t

B(t) = sqrt(Bx_1min(t)^2 + By_1min(t)^2 + Bz_1min(t)^2)

B0(t) = median{B(τ): t - 24 h <= τ < t}
ratio_B24M = B / B0
delta_B24M = (B - B0) / B0
chi_B24M = abs(delta_B24M)
```

The current sample is excluded from its own baseline. A row is valid only after the declared warm-up, at least 95% cadence coverage, and a finite positive baseline. Invalid baselines receive named status values; they are not repaired silently.

The sign is important:

```text
delta_B24M > 0   magnitude above the prior baseline
delta_B24M < 0   magnitude below the prior baseline
chi_B24M         absolute departure only; sign removed
```

Vector rotations, `Bz_GSE`, plasma state, and image statistics remain separate observables. They are not smuggled into `chi_B24M`.

---

## 6. Candidate thresholds are tests, not laws

Default observatory thresholds are configuration values used to find records worth examining:

```text
chi research watch       >= 0.15
significant |delta|      >= 0.50
severe chi               >= 1.00
one-minute rotation      >= 45 degrees
one-minute |B| change    >= 25 percent
```

The `0.15` value is retained as a research-watch threshold so its frequency, persistence, mission dependence, quiet-state behavior, and selectivity can be measured. A threshold crossing begins a candidate record; it does not create a mechanism or discovery by itself.

Every automatic event starts as:

```text
CANDIDATE_UNRESOLVED
```

---

## 7. Hourly observatory flow

The scheduled workflow runs at:

```text
17 * * * *
```

Its operating sequence is:

```text
restore bounded NOAA rolling state
→ run non-writing Drive preflight when configured
→ acquire current NOAA SWPC and SOLAR-1 records
→ preserve provider responses
→ validate source contracts and quality rules
→ canonicalize time and vectors
→ calculate rolling magnetic and verified plasma fields
→ detect candidate changes in the latest hour
→ generate charts, manifests, capsules, and status files
→ publish to Drive when authorized
→ always preserve the temporary GitHub evidence package when available
```

The NOAA operational source may be a provider-selected active upstream spacecraft. NVCPP therefore labels it as operational L1 context, not as an independent named spacecraft measurement unless identity is separately resolved.

### Hourly failure behavior

- One source may fail while another source still produces a degraded run package.
- If all configured sources fail, the workflow becomes red after preserving available failure evidence.
- Missing Drive secrets produce `NOT_CONFIGURED` and dry-run publication behavior.
- Configured but invalid Drive credentials leave the telemetry in the GitHub artifact and make the storage failure visible.
- A shorter provider response never authorizes reducing the 24-hour baseline requirement.

---

## 8. Evidence package

A successful or degraded run can preserve:

```text
raw provider responses
source URLs and response hashes
source and run manifests
contract and schema fingerprints
canonical magnetic and plasma rows
rolling-state revision counts
quarantine rows and reason codes
cadence, gap, duplicate, and coverage statistics
baseline status counts
candidate-event JSON and CSV
magnitude, vector, signed-departure, and plasma charts
teaching capsules and run lessons
latest status JSON and Markdown
result index
Drive preflight and publication receipt
output inventory with SHA-256 values
```

Typical hourly path:

```text
runs/hourly/YYYY/MM/DD/HH/nvcpp-hourly-<UTC>-run-<ACTIONS_ID>/
```

Storage policy:

```text
GitHub = code, tests, contracts, workflows, compact audit records,
         and temporary 90-day evidence artifacts

Drive  = durable raw, canonical, quarantine, charts, capsules,
         manifests, reports, and larger mission products
```

---

## 9. Teaching engine

The retained LUFT organizational idea is implemented as this loop:

```text
observe
→ preserve raw evidence
→ validate source and quality
→ calculate reversible canonical fields
→ detect candidate structures
→ compare independent instruments
→ generate charts
→ write an evidence-first capsule
→ record what is known, unknown, contrary, or unresolved
→ return on the next scheduled run
```

Capsule categories include:

```text
EVENT_CANDIDATE
METHOD
DATA_QUALITY_ANOMALY
CONTRARY_RESULT
LESSON
UNRESOLVED_QUESTION
REPLICATION
```

A failed test, missing source, or cross-spacecraft disagreement is part of the lesson. It is not smoothed away.

---

## 10. Repository layout

```text
core/        canonical baseline math, event detection, and coherence analysis
historical/  NASA CDAWeb acquisition, Gannon audits, controls, and holdout consumer
sources/     NOAA/NCEI, NOAA/SWPC, SOLAR-1, and mission-specific adapters
observatory/ hourly orchestration plus separate Roman readiness/image analysis
pipelines/   command routing, Drive preflight, and create-only publication
config/      authoritative source, observatory, detector, and holdout contracts
capsules/    compact evidence-first lessons committed deliberately
provenance/  frozen inventories, migration records, and registry audit material
tests/       offline unit, integration, contract, security, and integrity tests
tools/       repository security and maintenance helpers
docs/        operating protocols, audit decisions, and interpretation limits
.github/     CI, hourly, historical, discovery, control, holdout, and Roman workflows
```

---

## 11. Install and validate

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt

python -m compileall -q core historical observatory pipelines sources tests tools
python tools/repository_security_scan.py
python -m sources.solar1.validate_contract \
  config/solar1_mag_contract.v1.json
python -m json.tool config/hourly_observatory.v1.json >/dev/null
python -c "from pathlib import Path; from observatory.roman.contracts import load_contract; load_contract(Path('config/roman_prelaunch.v1.json'))"
python -m pytest -q
```

The test suite is intentionally offline where possible. Pull-request validation must not open the sealed Gannon V2 holdout or depend on a temporary NASA/NOAA outage.

---

## 12. Operator instructions

### 12.1 Run the hourly observatory locally

```bash
python -m observatory.run_hourly \
  --config config/hourly_observatory.v1.json \
  --outdir runs/hourly
```

A deterministic replay time may be supplied through the module’s `--now` option or the GitHub workflow input.

### 12.2 Check Drive access without writing

```bash
python -m pipelines.drive_preflight \
  --output drive_preflight_result.json
```

GitHub repository secrets used by the workflow:

```text
NVCPP_GOOGLE_AUTH_B64
NVCPP_DRIVE_PARENT_FOLDER_ID
```

Never commit credential JSON, OAuth tokens, refresh tokens, or decoded secret material to the repository.

### 12.3 Run the DSCOVR Gannon historical interval

```bash
python -m historical.download_dscovr_cdaweb \
  --run dscovr_gannon_may_2024 \
  --start 2024-05-09T00:00:00.000Z \
  --analysis-start 2024-05-10T00:00:00.000Z \
  --end 2024-05-13T00:00:00.000Z \
  --outdir runs/historical
```

### 12.4 Run the event-reference audit

```bash
python -m historical.gannon_event_reference_audit \
  --input runs/historical/dscovr_gannon_may_2024/cline_l1_rows.csv \
  --manifest runs/historical/dscovr_gannon_may_2024/dscovr_run_manifest.json \
  --outdir runs/audits/gannon_event_reference
```

### 12.5 Run the SOLAR-1 MAG regression

```bash
python -m sources.solar1.download_solar1 \
  --run solar1_regression_june_2026 \
  --start 2026-06-01T00:00:00.000Z \
  --analysis-start 2026-06-02T00:00:00.000Z \
  --end 2026-06-05T00:00:00.000Z \
  --outdir runs/historical
```

The run manifest must retain the applicable commissioning or operational mission-phase label. A passing parser does not erase product-generation context.

### 12.6 Run the DSCOVR–SOLAR-1 coherence audit

```bash
python -m core.temporal_pairing \
  --dscovr runs/historical/dscovr_overlap/cline_l1_rows.csv \
  --solar1 runs/historical/solar1_overlap/solar1_cline_l1_rows.csv \
  --dscovr-manifest runs/historical/dscovr_overlap/dscovr_run_manifest.json \
  --solar1-manifest runs/historical/solar1_overlap/solar1_run_manifest.json \
  --outdir runs/pairing
```

Possible conservative classifications:

```text
NO_STABLE_COHERENCE
COHERENT_BUT_LAG_UNRESOLVED
LAG_CANDIDATE_REQUIRES_EPHEMERIS
```

Correlation is evidence of similarity. It is not proof of propagation or mechanism.

### 12.7 Run SWiPS discovery only

```bash
python -m sources.solar1.download_swips_discovery
python -m sources.solar1.swips_archive_discovery_v2
```

Neither command authorizes SOLAR-1 plasma calculations.

### 12.8 Run Roman readiness

```bash
python -m observatory.roman.prelaunch_probe \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/readiness
```

Inventory an authenticated export:

```bash
python -m observatory.roman.intake \
  --input /path/to/export \
  --source-class ROMAN_RESEARCH_NEXUS_EXPORT \
  --config config/roman_prelaunch.v1.json \
  --outdir runs/roman/intake \
  --copy-small-files
```

A green deterministic fixture proves reproducibility on that fixture only. It is not Roman flight performance or a life-detection result.

### 12.9 Open the sealed Gannon V2 holdout

Do not run this workflow as an ordinary smoke test. Its first authorized execution opens the prospective holdout.

In GitHub:

```text
Actions
→ NVCPP Gannon V2 Immutable Holdout Consumer
→ Run workflow
→ branch: main
→ execute_frozen_holdout: true
→ registry_file_sha256:
  8c1510e026aa68dca21d42181bbe8a2fe1876a9738426be1378605f8bfe947af
```

The workflow must:

- verify the exact registry and all frozen hashes before network access;
- emit all 43 rows without substitution;
- run at most three interval jobs concurrently;
- retain provider failures as `INCOMPLETE_MULTIPOINT`;
- report registered and evaluable denominators;
- run circular-shift, moving-block, and mismatched-day controls;
- write the final capsule to an Actions artifact;
- leave ephemeris, MVA, propagation, physical class, and common-surface claims closed.

---

## 13. How to read important status labels

| Label | Meaning |
|---|---|
| `VALID` | The row passed the declared source, timing, coverage, and baseline rules |
| `CANDIDATE_UNRESOLVED` | A configured detector fired; interpretation remains open |
| `PRE_EVENT` | Event-reference overlay is still before its derived gate |
| `MEDIAN_ADAPTING` | The live rolling median is changing as the event occupies more of the prior window |
| `EVENT_ABSORBED_BY_LIVE_BASELINE` | The live baseline has adapted enough to mute magnitude departure while the frozen pre-event departure remains large |
| `COHERENT_BUT_LAG_UNRESOLVED` | Two series are similar, but a stable arrival lag has not been established |
| `INCOMPLETE_MULTIPOINT` | A frozen interval lacks the predeclared multipoint completeness; it stays in the registered denominator |
| `NOT_CONFIGURED` | An optional operational boundary, such as Drive publication, lacks required settings |
| `SCHEDULED_LAUNCH_WINDOW_UNVERIFIED` | A scheduled time passed without separately verified mission-status evidence |

These are computational and audit states. They are not automatically physical storm phases or discovery claims.

---

## 14. What NVCPP can establish

With a completed evidence package, NVCPP can establish that:

- a named source returned specific bytes at a specific time;
- the response matched or failed a declared contract;
- certain rows were admitted or excluded under named rules;
- canonical vectors and magnitudes were calculated by a recorded transformation;
- a rolling or frozen-reference departure crossed a configured threshold;
- a vector rotation or magnitude jump occurred in the retained coordinate frame;
- two or more series showed measured similarity under a stated alignment rule;
- a candidate was common or uncommon under the implemented controls;
- a test produced a positive, negative, contrary, incomplete, or unresolved result.

---

## 15. What NVCPP does not establish by itself

NVCPP does not turn one unusual number into proof of:

```text
a universal physical law
a shock, ICME, discontinuity, or common moving surface
a propagation direction or speed without geometry
a physical mechanism from correlation alone
geoeffectiveness from |B| alone
a GSM clock angle from GSE Bz or GSE rotation
independent spacecraft agreement from a provider-selected blended feed
Roman launch or commissioning success from a date or webpage change
Roman flight performance from a synthetic fixture
life, biosignatures, or extraterrestrial technology from an anomaly alone
```

Those questions require their own contracts, measurements, controls, and interpretation stages.

---

## 16. Explicitly not enabled yet

```text
verified SOLAR-1 SWiPS plasma ingestion
mission-specific IMAP canonical ingestion
production JWST and HST archive adapters
Roman flight-product calibration and science claims
ephemeris/MVA common-surface analysis for the Gannon V2 holdout
nearby-star evidence/life-target registry and navigator bridge
```

These are future work areas, not hidden production features.

---

## 17. Repository operating rules

- Keep `main` protected.
- Require repository CI and the V2 holdout integrity guard for changes that affect the frozen holdout path.
- Do not make provider-dependent telemetry workflows required for unrelated merges.
- Do not replace a failed registered interval with a nearby date.
- Do not change a frozen date, threshold, radius, cadence, fill rule, or denominator after inspecting holdout measurements.
- Do not commit raw telemetry, heavy products, credentials, or generated vault contents to Git.
- Preserve failures and contrary results with the same hashes and manifests used for favorable results.

The repository owner should periodically confirm in GitHub Settings that the intended required checks are actually attached to the `main` ruleset.

---

## 18. Near-term work order

1. Keep the hourly observatory and Drive publication boundary healthy; inspect red runs rather than hiding them.
2. Maintain the frozen contracts and regression intervals while providers revise their services.
3. Open the Gannon V2 holdout only as a deliberate experiment, then interpret the class-wise and null-control results before considering geometry.
4. Continue SWiPS and IMAP discovery without inventing product schemas.
5. Expand Roman processing only when an official public or authenticated export can be identified and read under a pinned environment.
6. Build the result index and question archive so old evidence, failures, and unresolved questions can be revisited automatically.
7. Connect future nearby-star or telescope-target tools through a separate evidence registry rather than mixing them with the L1 plasma engine.

---

## 19. Key documentation

```text
docs/HOURLY_OBSERVATORY.md
docs/TEACHING_ENGINE.md
docs/EVENT_REFERENCE_OVERLAY.md
docs/DRIVE_PREFLIGHT.md
docs/DRIVE_VAULT_SETUP.md
docs/GANNON_V2_IMMUTABLE_CONSUMER.md
docs/ROMAN_PRELAUNCH_READINESS.md
docs/ROMAN_UNTHOUGHT_OFS.md
```

---

## 20. Program identity

NVCPP is an independent computational and observational project created and led by **Carl Dean Cline Sr.** Its purpose is to preserve the evidence, make the transformations visible, test alternate readings of the data, record failures honestly, and keep the program capable of changing when the measurements say no.
