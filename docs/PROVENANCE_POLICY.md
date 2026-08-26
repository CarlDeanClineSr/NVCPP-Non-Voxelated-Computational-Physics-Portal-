# NVCPP provenance policy

NVCPP does not maintain a hand-edited repository-wide `SHA256SUMS.txt`. Such a file becomes stale as soon as code changes and can create a false appearance of integrity.

Every scientific or discovery run instead writes a machine-readable manifest containing, as applicable:

- source identity and resolved request URL;
- raw response byte count and SHA-256;
- normalized schema fingerprint;
- authoritative contract path, version, and SHA-256;
- Git commit and GitHub Actions run metadata;
- software versions;
- requested and returned time ranges;
- quality, quarantine, cadence, duplicate, and gap counts;
- protocol ID and version;
- output inventory, byte counts, and SHA-256 hashes;
- explicit success or failure state.

Git itself remains the integrity and history system for source files. Release tags may carry a generated checksum inventory, but it must be created from the tagged tree rather than maintained manually.
