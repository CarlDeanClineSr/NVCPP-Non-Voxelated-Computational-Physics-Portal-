# JWST January 5, 2026 retrospective audit protocol

**Status:** frozen retrospective protocol before the new data acquisition run  
**Owner / project lead:** Carl Dean Cline Sr.  
**Implementation branch:** `audit/jwst-jan5-retrospective-v1`  
**Scientific state at freeze:** `QUESTION_OPEN`  

## 1. Question

Did independently recorded JWST engineering or guidance behavior on January 5, 2026 differ from ordinary commanded spacecraft operations, and did any such behavior coincide with independently recorded heliospheric structure?

The historical words **re-addressing** and **expansion event** are retained only as labels for the question that motivated the search. They are not accepted mechanisms, classifications, or conclusions.

## 2. Why this is retrospective

January 5 was selected previously from LUFT-era calculations and discussion. The date and approximate times were therefore known before this clean audit, but the old calculations and prose were not a valid blind preregistration. This audit is retrospective and must report look-elsewhere and control limitations honestly.

## 3. Frozen candidate windows

Two intervals are retained from the pre-existing historical record:

| Window | UTC interval | Historical reason only |
|---|---|---|
| `JAN5_PRIMARY` | 2026-01-05 00:15:00 to 02:00:00 | Encloses the old 00:41–01:13 table and the later 01:10–01:30 narrative |
| `JAN5_SECONDARY` | 2026-01-05 14:30:00 to 16:00:00 | Encloses the old 15:04–15:06 record labeled `Ambiguous` |

The acquisition envelope for environmental context is 2026-01-01 00:00:00 through 2026-01-10 00:00:00 UTC.

No candidate window may be moved after inspecting the newly acquired JWST telemetry.

## 4. Source hierarchy

Primary evidence must come from the original provider endpoints and be preserved byte-for-byte with URL, retrieval time, HTTP status, response headers where available, SHA-256, and software revision.

1. **STScI MAST Engineering Data Portal (EDP)**
   - mnemonic metadata must be queried before values are interpreted;
   - both frequent (`fqa`) and sporadic (`spa`) access tables may be tested;
   - a mnemonic is used only if the provider metadata resolves it;
   - raw responses are preserved even when empty or unsuccessful.
2. **STScI MAST observation metadata**
   - used to identify what JWST was commanded to observe and whether ordinary acquisition, slew, dither, or mode changes can account for engineering transitions;
   - short exposure duration alone is not accepted as proof of loss of lock.
3. **NASA SPDF CDAWeb HAPI**
   - definitive OMNI one-minute data provide Earth-upstream environmental context;
   - raw DSCOVR, ACE, and Wind products may be acquired for the frozen candidate windows when the official dataset exists;
   - source identity, coordinate frame, cadence, fill values, and time semantics must remain explicit.
4. **NASA CCMC DONKI**
   - used only as an independent catalog of reported flares, CMEs, shocks/arrivals, geomagnetic storms, and particle events;
   - absence from DONKI is not proof that nothing occurred.

No simulated or synthetic fallback is permitted.

## 5. JWST quantities to acquire

The old repository began with these documented engineering tuples:

- `SA_ZATTEST1` through `SA_ZATTEST4`: attitude quaternion components;
- `SA_ZADUCMDX`, `SA_ZADUCMDY`: commanded fine-steering-mirror position.

The clean discovery pass also queries provider metadata for:

- `SA_ZFGGSCMDX`, `SA_ZFGGSCMDY`: commanded guide-star position;
- `SA_ZFGGSPOSX`, `SA_ZFGGSPOSY`: guide-star position when available;
- `SA_ZFGDETID`: guider identification;
- `IFGS_ID_XPOSG`, `IFGS_ID_YPOSG`;
- `IFGS_ACQ_XPOSG`, `IFGS_ACQ_YPOSG`;
- `IFGS_CTDGS_X`, `IFGS_CTDGS_Y`.

Partial metadata searches for `SA_ZATT`, `SA_ZADU`, `SA_ZFG`, and `IFGS_` are preserved so additional state/quality mnemonics can be selected from provider documentation rather than invented.

## 6. Primary analysis endpoints

No single telemetry sample proves a physical jolt. Analysis must distinguish commands, estimates, measured guide-star positions, and observation modes.

1. Quaternion normalization and incremental rotation angle, with discontinuities evaluated against local sampling and command state.
2. Fine-steering-mirror command-vector magnitude and step changes.
3. Commanded-versus-measured guide-star residuals where both quantities exist.
4. Explicit mode, detector, and quality-state transitions where provider metadata identifies suitable mnemonics.
5. Observation/visit boundaries, target acquisitions, dithers, slews, and ordinary reacquisition sequences.

A whole-day mean and standard deviation are not sufficient anomaly definitions. Robust local baselines, cadence-aware derivatives, and matched operational controls are required.

## 7. Environmental endpoints

Primary environmental quantities are conventional, reversible observables:

- GSE magnetic-vector components and component-derived magnitude;
- one-minute vector rotation and relative magnitude change;
- proton density, speed, temperature, and flow pressure where verified;
- independently cataloged interplanetary shocks, CME arrivals, flares, geomagnetic storms, and particle events.

The old LUFT `chi` values are not a primary endpoint. If reconstructed, they must be labeled secondary and exploratory, calculated without clipping or capping, and accompanied by the exact formula and input provenance.

## 8. Controls

The first acquisition run is source discovery and candidate-window preservation. Before candidate scores are interpreted, a second frozen control record must select comparison intervals by a rule based on non-anomaly metadata, preferably matching:

- JWST observation/guidance mode;
- instrument and exposure class;
- visit/acquisition state;
- comparable duration and data availability;
- nearby calendar dates without reusing candidate-window values to choose the controls.

At minimum the analysis must compare the candidate windows with adjacent-day same-UTC windows and report all tested mnemonics and both historical candidate windows in the multiplicity count.

## 9. Outcome states

Only these bounded states are allowed before a later mechanism study:

- `SOURCE_UNAVAILABLE`
- `INSUFFICIENT_JWST_DATA`
- `ORDINARY_COMMANDED_OPERATION`
- `JWST_CANDIDATE_WITHOUT_ENVIRONMENTAL_SUPPORT`
- `ENVIRONMENTAL_STRUCTURE_WITHOUT_JWST_RESPONSE`
- `COINCIDENT_CANDIDATE_UNRESOLVED`
- `NO_DISTINGUISHING_SIGNAL`

`READDRESSING_CONFIRMED`, `EXPANSION_CONFIRMED`, `SUBSTRATE_SHIFT`, and any source-attribution claim are prohibited outcomes of this audit.

## 10. Interpretation boundary

A timing coincidence can establish only that two recorded structures occurred within a declared comparison rule. It cannot by itself establish:

- propagation from L1 to JWST;
- a common plasma parcel or moving surface;
- a force on the JWST sunshield;
- a micrometeoroid strike;
- a CME mechanism;
- a change in space, vacuum, coordinates, or fundamental physics.

Those require geometry, ephemerides, timing uncertainty, spacecraft operational context, and alternative-cause tests.

## 11. Repository boundary

The archived `JWST-Solar-System-Re-address-Jan-5th` repository remains untouched as a historical record. This branch is isolated from the sealed Gannon V2 holdout and changes no frozen registry, detector, threshold, denominator, source contract, or hourly observatory path.

The first run acquires and inventories evidence only. It does not issue a scientific conclusion.
