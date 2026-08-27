# NVCPP Teaching Engine

The LUFT Portal contained a valuable organizational idea: data ingestion, analysis, charts, results, reports, and research capsules should form one learning loop. NVCPP keeps that idea while separating code from durable evidence and requiring the numerical record to outrank the prose.

## Learning loop

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

## Capsule categories

```text
EVENT_CANDIDATE
METHOD
DATA_QUALITY_ANOMALY
CONTRARY_RESULT
LESSON
UNRESOLVED_QUESTION
REPLICATION
```

## Rules

1. A capsule must reference the source manifest, protocol, and hashes.
2. Numerical fields are generated from the output; they are not typed into a confirmation paragraph.
3. Every capsule includes interpretation limits.
4. A failed test remains in the record.
5. A cross-spacecraft disagreement is evidence, not something to smooth away.
6. χ remains separate from signed Δ, vector components, plasma state, and detector/image statistics.
7. Telescope products are response/context records and never become L1 plasma telemetry by renaming columns.

## Questions archive

Future work can add structured Carl Questions:

```yaml
question_id: CQ-YYYY-NNNN
question: Why did magnetic magnitude collapse while speed remained stable?
trigger_event: NVCPP-...
status: OPEN
tests:
  - inspect Bx, By, Bz rotation
  - compare another L1 spacecraft
  - inspect plasma state
  - inspect coronagraph imagery
resolution: null
```

As new data arrive, the observatory can revisit unresolved questions automatically.
