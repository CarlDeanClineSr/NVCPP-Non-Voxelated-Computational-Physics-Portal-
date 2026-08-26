# NVCPP — Non-Voxelated Computational Physics Portal

NVCPP is a clean, provenance-first codebase for continuous physical telemetry and mission archive adapters. It starts from the audited CLINE L1 V1 package preserved in Google Drive and deliberately excludes the legacy `luft-portal` repository bulk.

## Non-negotiable rules

- no clipping, capping, winsorizing, saturation, or hidden replacement of finite measurements;
- prior-only trailing baselines for `B0`;
- canonical observable name `chi_B24M`, never generic `chi` in the clean path;
- explicit source identity, dataset, coordinate frame, cadence, and pairing declarations;
- raw response bytes, descriptors, and SHA-256 provenance for historical retrievals;
- fail-closed mission capability checks before any physics computation;
- GitHub stores code and small audit records; heavy data products remain outside the repository.

The initial DSCOVR implementation preserves:

- definitive NASA CDAWeb magnetic and Faraday-cup products where the frozen inventory supports them;
- NASA CDAWeb REST-CSV descriptors, raw-byte preservation, and SHA-256 records;
- fail-closed rejection of generic `F`, `B`, `baseline`, or `chi` layouts;
- independent engineering quarantine for ground-scale magnetic values;
- proton beta, Alfvén speed, Alfvén Mach number, and dynamic pressure only when verified paired plasma exists.

The audited Drive package passed its original **23 tests** before migration. NVCPP adds source-identity, mission-capability, transition-physics, and Drive-sink tests.

## Mission boundaries

| Mission | Orbit/context | NVCPP domain | CLINE L1 physics |
|---|---|---|---|
| DSCOVR | Sun–Earth L1 | Solar-wind magnetic and plasma | Enabled when source and pairing contracts pass |
| JWST | Sun–Earth L2 halo | MAST observatory archive and authenticated engineering context | Prohibited |
| HST | Low-Earth orbit | MAST observatory archive and ephemeris context | Prohibited |
| Roman | Planned Sun–Earth L2 | Early-mission/archive scaffold | Prohibited |

Orbit location alone never grants permission to run a plasma equation. Mission adapters declare their capabilities, and the router rejects cross-domain calculations.

## Repository layout

```text
core/        audited baseline math, source guards, transition physics
historical/  definitive DSCOVR CDAWeb downloaders and frozen epochs
missions/    capability registry and bounded MAST archive queries
pipelines/   command router and create-only Google Drive sink
tests/       original audit tests plus NVCPP integrity tests
config/      source declarations and pipeline examples
docs/        protocols, architecture, setup, and mission-status records
provenance/  Drive-package manifests and migration hashes
```

## Install and test

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest -q
```

## DSCOVR historical runs

May 2024 remains magnetic-only under the frozen V1 source inventory:

```bash
python -m pipelines.run_pipeline dscovr-historical \
  --run gannon_may_2024_dscovr_mag_only \
  --outdir runs/historical
```

Paired active-event validation:

```bash
python -m pipelines.run_pipeline dscovr-historical \
  --run september_2017_dscovr_full \
  --outdir runs/historical
```

Data-selected quiet interval:

```bash
python -m pipelines.run_pipeline dscovr-historical \
  --run quiet_dscovr_scan \
  --outdir runs/historical
```

## Observatory archive adapters

The supported public MAST query path is bounded and metadata-only:

```bash
python -m pipelines.run_pipeline archive-query \
  --mission jwst \
  --filter dataproduct_type=image \
  --page-size 100 \
  --out outputs/jwst_query.json
```

Roman archive querying remains disabled until a public collection is verified and enabled in `missions/registry.py`.

## Google Drive data vault

GitHub tracks code, tests, small configuration, and provenance records. Raw telemetry, FITS files, CSV results, reports, and plots belong in the Drive vault.

The Drive sink is create-only and fail-closed. Live service-account publication requires a writable folder in a dedicated Google Shared Drive, verifies that destination before writing, creates a timestamped run folder, uploads allowlisted outputs, and never overwrites or deletes existing vault files.

Dry run:

```bash
python -m pipelines.drive_sink \
  --source runs/historical/example \
  --folder-id example-shared-drive-folder-id \
  --run-name example \
  --dry-run
```

Live Actions publication requires encrypted repository secrets plus a Shared Drive destination described in `docs/DRIVE_VAULT_SETUP.md`. The existing audit folder remains a read-only migration source and is not assumed to be the output vault.

## Audit source

Primary migration source:

```text
LUFT_CLINE/rebuild_v1_audit/CLINE_L1_REBUILD_CLEAN_STAGE_2026-08-25.zip
SHA-256: b9420c87780bbe0a6ded9eb7a1199cd64faa83f1a6828f0d47c14127eefa5d4a
```

The earlier V1 ZIP remains preserved in Drive and was not imported wholesale. Generated runs, plots, multi-megabyte CSVs, patches, and legacy audit rows were intentionally excluded from this code repository.
