"""Structured, diagnostic-only source failures.

These exceptions describe rejected inputs; they do not change any acceptance
rule. Values must be JSON-native so manifests and human reports agree.
"""

from __future__ import annotations

import json
from typing import Any


class SourceDiagnosticError(ValueError):
    reason_code = "SOURCE_DATA_INCOMPLETE"

    def __init__(self, **diagnostics: Any) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            f"{self.reason_code}: "
            + json.dumps(diagnostics, sort_keys=True, allow_nan=False)
        )

    def as_dict(self) -> dict[str, Any]:
        return {"reason_code": self.reason_code, "diagnostics": self.diagnostics}


class InsufficientCoverageError(SourceDiagnosticError):
    reason_code = "BASELINE_INSUFFICIENT_COVERAGE"


class BaselineWarmupError(SourceDiagnosticError):
    reason_code = "BASELINE_WARMUP"


class BaselineNonpositiveError(SourceDiagnosticError):
    reason_code = "BASELINE_NONPOSITIVE"


class BaselineUnavailableError(SourceDiagnosticError):
    reason_code = "BASELINE_UNAVAILABLE"


class SourcePrerollIncompleteError(SourceDiagnosticError):
    reason_code = "SOURCE_PREROLL_INCOMPLETE"


class SourceEndIncompleteError(SourceDiagnosticError):
    reason_code = "SOURCE_END_INCOMPLETE"
