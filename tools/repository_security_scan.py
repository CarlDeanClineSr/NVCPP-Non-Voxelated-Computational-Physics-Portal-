#!/usr/bin/env python3
"""Fail closed on credential material and obsolete OAuth workflow paths.

This scanner is intentionally small and deterministic so it can run in every
pull request without network access. It does not replace GitHub secret scanning;
it prevents the specific credential mistakes already encountered by NVCPP.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "runs",
}

FORBIDDEN_FILES = {
    Path("scraper.py"),
    Path(".github/workflows/run-scraper.yml"),
    Path(".github/workflows/run-scraper2.yml"),
}

# These expressions are deliberately shaped like real Google credentials.
# Placeholder strings such as "..." do not match.
CREDENTIAL_PATTERNS = {
    "google_access_token": re.compile(r"ya29\.[A-Za-z0-9._-]{20,}"),
    "google_refresh_token": re.compile(r"1//[A-Za-z0-9_-]{20,}"),
    "google_oauth_client_secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
}

LEGACY_WORKFLOW_SECRET_REFERENCES = {
    "secrets.GOOGLE_CLIENT_ID",
    "secrets.GOOGLE_CLIENT_SECRET",
    "secrets.GOOGLE_REFRESH_TOKEN",
}

REQUIRED_PUBLISHER_SECRET_REFERENCES = {
    "secrets.NVCPP_GOOGLE_AUTH_B64",
    "secrets.NVCPP_DRIVE_PARENT_FOLDER_ID",
}


def iter_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def scan_repository() -> list[str]:
    errors: list[str] = []

    for relative in sorted(FORBIDDEN_FILES):
        if (ROOT / relative).exists():
            errors.append(f"obsolete credential path must not exist: {relative.as_posix()}")

    for path in iter_text_files():
        relative = path.relative_to(ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for label, pattern in CREDENTIAL_PATTERNS.items():
            for match in pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"possible {label} in {relative.as_posix()}:{line_number}"
                )

        if relative.parts[:2] == (".github", "workflows"):
            for reference in sorted(LEGACY_WORKFLOW_SECRET_REFERENCES):
                if reference in text:
                    errors.append(
                        f"legacy split OAuth secret reference {reference} in "
                        f"{relative.as_posix()}"
                    )

    hourly_path = ROOT / ".github/workflows/hourly_observatory.yml"
    if not hourly_path.exists():
        errors.append("hourly observatory workflow is missing")
    else:
        hourly = hourly_path.read_text(encoding="utf-8")
        for reference in sorted(REQUIRED_PUBLISHER_SECRET_REFERENCES):
            if reference not in hourly:
                errors.append(
                    f"hourly observatory does not reference required secret {reference}"
                )

    return errors


def main() -> None:
    errors = scan_repository()
    if errors:
        print("NVCPP repository security scan failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(2)
    print("NVCPP repository security scan: PASS")


if __name__ == "__main__":
    main()
