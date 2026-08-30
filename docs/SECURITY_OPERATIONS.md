# NVCPP Security Operations

This document complements `SECURITY.md` with repository-specific operating rules.

## Security boundaries

NVCPP processes public telemetry and archive metadata but may also use repository secrets to publish evidence packages to Google Drive. The scientific pipeline and credential boundary must remain separate.

### Credentials

- Never commit OAuth access tokens, refresh tokens, service-account private keys, client secrets, Drive credentials, or GitHub tokens.
- GitHub Actions credentials belong only in repository or environment secrets.
- `NVCPP_GOOGLE_AUTH_B64` and `NVCPP_DRIVE_PARENT_FOLDER_ID` must be supplied through GitHub secrets/settings, never source files.
- Logs, manifests, capsules, error messages, and uploaded artifacts must not echo credential values.
- A credential preflight must be non-writing.

### Scientific integrity

A security or provider failure must not silently change the experiment. In particular, code must not respond to an outage by:

- changing a frozen date;
- substituting another spacecraft or product without a declared contract change;
- relaxing a coverage threshold;
- clipping or repairing physical values;
- changing a detector threshold or timing radius;
- dropping an `INCOMPLETE_MULTIPOINT` interval from a frozen denominator.

## Automated repository defenses

The repository uses or supports:

- NVCPP CI and repository security scanning;
- GitHub dependency graph and Dependabot alerts;
- GitHub CodeQL code scanning;
- GitHub secret scanning;
- GitHub push protection for supported secret patterns;
- private vulnerability reporting;
- pull-request integrity guards for the frozen Gannon V2 experiment.

Provider-dependent telemetry workflows should not be required merge checks because NASA/NOAA availability is external to repository correctness.

## If a secret is exposed

Treat a secret pasted into a chat, issue, pull request, log, commit, screenshot, or public repository as compromised even if it is later deleted.

1. Revoke or rotate the credential at the issuing provider immediately.
2. Replace the corresponding GitHub secret with the new credential.
3. Do not reuse the exposed token or refresh token.
4. Remove the exposed value from current repository content and public discussion where possible.
5. Review GitHub secret-scanning alerts and relevant Actions logs/artifacts.
6. Run the repository security scan and the non-writing Drive preflight before resuming publication.
7. Preserve a sanitized incident record containing timestamps and actions, but never the credential itself.

Deleting a token from a page or commit does not make the old credential trustworthy again; rotation is the security boundary.

## Drive publisher

The Drive boundary follows this order:

```text
GitHub secret
→ decode/credential validation
→ non-writing Drive metadata preflight
→ telemetry processing
→ create-only evidence publication when authorized
```

If Drive is not configured, telemetry may still run and produce the temporary GitHub evidence artifact. If Drive is configured but authentication or destination validation fails, that failure remains visible rather than being silently treated as successful durable storage.

## Dependency and CodeQL changes

Dependabot and CodeQL findings are evidence requiring review, not automatic permission to alter scientific behavior.

A dependency/security repair should be tested through normal CI. If a proposed fix changes numerical libraries, parsing, time handling, dataframe behavior, or network clients used by a frozen experiment, rerun the relevant contract/integrity tests and document the potential scientific impact before merging.

## Required merge checks

The recommended repository ruleset requires:

```text
NVCPP CI / audit
NVCPP V2 Holdout Integrity Guard / guard
```

Do not make live provider workflows required merge checks.

## Reporting

Use GitHub private vulnerability reporting for vulnerabilities that could expose credentials, modify trusted artifacts, bypass provenance checks, corrupt frozen experiment state, or permit unintended writes. Public issues are appropriate for ordinary bugs that do not disclose a security weakness.
