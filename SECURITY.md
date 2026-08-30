# Security Policy

NVCPP is an evidence-preserving telemetry and observatory-analysis repository. Security problems can affect not only software execution but also credential safety, provenance, stored evidence, and the integrity of frozen experiments.

## Supported version

NVCPP does not currently publish a stable numbered release series. Security fixes are applied to the current `main` branch.

| Version | Supported |
| --- | --- |
| Current `main` | Yes |
| Older commits, archived branches, and superseded experiment branches | No |

Historical commits remain part of the audit trail, but they are not maintained as independent supported releases.

## Reporting a vulnerability

Please use GitHub **Private vulnerability reporting** for this repository rather than opening a public issue when the report could expose a security weakness.

Good private reports include:

- the affected file, workflow, endpoint, or commit when known;
- steps to reproduce the problem;
- the expected and observed behavior;
- the likely impact;
- whether credentials, stored artifacts, provenance, frozen experiment state, or write permissions may be affected.

Do **not** include live access tokens, refresh tokens, service-account private keys, client secrets, or other credentials in a vulnerability report. If a credential has already been exposed, revoke or rotate it first and report only sanitized identifiers and timestamps.

## Response expectations

This repository is independently maintained, so fixed commercial response-time guarantees are not promised. Reports will be triaged as availability permits. A report may result in a code change, workflow/configuration repair, credential rotation, documentation update, or a finding that the reported behavior is expected and bounded by an existing contract.

Security-sensitive details may remain private until a repair or mitigation is available.

## Security-sensitive areas

Please report issues involving any of the following privately:

- credential or secret exposure;
- unintended Google Drive or repository writes;
- bypass of source identity, schema, provenance, or hash validation;
- path traversal or unsafe file handling;
- command or workflow injection;
- dependency or deserialization vulnerabilities;
- ability to alter a frozen experiment, registry, denominator, detector, or result without the integrity guards detecting it;
- leakage of private configuration into logs, manifests, capsules, or Actions artifacts.

Ordinary scientific disagreements, numerical questions, provider outages, missing telemetry, and documented `INCOMPLETE_MULTIPOINT` results are not security vulnerabilities by themselves.

## Credential rule

Repository credentials belong in GitHub secrets or other approved secret stores, never in tracked files. NVCPP must not log or preserve credential values in evidence packages.

If a credential is exposed in a chat, issue, pull request, commit, log, screenshot, or public page, treat it as compromised even if the text is later deleted. Revoke or rotate it at the issuing provider and replace the corresponding stored secret.

## Automated security controls

The repository uses or supports:

- GitHub secret scanning and push protection;
- GitHub CodeQL code scanning;
- dependency graph and Dependabot alerts;
- repository credential/security scanning in NVCPP CI;
- private vulnerability reporting;
- integrity guards for frozen experiment state.

Provider-dependent telemetry workflows are deliberately separate from repository security/CI checks because an external NASA or NOAA outage is not evidence of an insecure code change.

## Scientific-integrity boundary

Security repairs must not silently alter a frozen scientific contract. A security change that affects parsing, numerical libraries, time handling, canonicalization, detector behavior, experiment registries, or null controls must pass the applicable scientific integrity tests and document the effect before results are compared across versions.

See `docs/SECURITY_OPERATIONS.md` for repository-specific incident and credential-handling procedures.
