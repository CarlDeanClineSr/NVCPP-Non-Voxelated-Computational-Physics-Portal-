# NVCPP Audit and Realignment — 2026-08-26

## Finding 1: the Run #7 coherence is real, but the +2-minute lag is unresolved

The archived Run #7 paired 4,317 minutes and reported a maximum Pearson
correlation of 0.905995 at +2 minutes. Zero lag was already 0.900981.

Independent checks of the archived pair showed:

- the 99.5% peak plateau spans +1, +2, and +3 minutes;
- moving-block resampling placed most best lags at 0 or +1 minute, with a 95%
  interval of approximately 0–2 minutes;
- daily best lags were +3, +3, and 0 minutes;
- first-difference correlations were weak and unstable;
- a circular-shift null that repeated the complete ±60-minute search did not
  approach the observed coherence.

Conclusion:

```text
COHERENT_BUT_LAG_UNRESOLVED
```

The result supports a shared large-scale magnetic pattern. It does not by itself
prove a front, a propagation direction, or an exact transit time. Spacecraft
ephemeris and vector/plasma context are required before a propagation claim.

## Finding 2: the original SWiPS Archive V2 probe queried the wrong level

The earlier code queried:

```text
https://archive.data.noaa.gov/?prefix=SWFO/...
```

The documented NCEI archive bucket is:

```text
https://archive.data.noaa.gov/satellite-spaceweather
```

HTTP 200 from the service root did not establish that the documented bucket was
empty. Archive Discovery V2.1 now uses the bucket path, saves every raw XML page,
follows continuation tokens, and separates:

```text
PUBLIC_OBJECTS_DISCOVERED
PUBLIC_PREFIXES_DISCOVERED_NO_FILES_AT_PROBED_LEVEL
NO_MATCHING_PUBLIC_OBJECTS_AT_DOCUMENTED_BUCKET
ACCESS_DENIED
PROBE_PARTIAL_FAILURE
```

## Finding 3: contract validation was called but its error list was ignored

The historical SOLAR-1 runner previously executed `validate_contract(data)` and
printed success without checking the returned errors. The validator now exposes
`validate_contract_or_raise()` and the runtime loads one authoritative JSON
contract through that fail-closed function.

## Finding 4: the two MAG products were not statistically symmetric

DSCOVR native one-second chi had been averaged into minute bins, while SOLAR-1
used a provider one-minute product. Because chi is nonlinear, these are not the
same transformation.

The DSCOVR adapter now performs:

```text
native BX/BY/BZ
→ component-wise one-minute means
→ vector magnitude
→ prior-only B0
→ ratio, signed delta, absolute chi
```

Both missions therefore reach the pairing layer as canonical exact one-minute
products.

## Remaining scientific boundary

A high MAG-to-MAG correlation substantially reduces the likelihood of an
isolated sensor artifact. It does not remove common processing effects, broad
solar-wind trends, baseline-induced autocorrelation, or geometry. The signed
delta, vector components, plasma quantities, and ephemeris must remain part of
the next analysis.
