"""Test-collection support for the standalone retrospective module.

`test_jan5_2026_retrospective.py` loads one script directly from its file path so
it can be tested without changing the package layout. Python's dataclass
machinery expects that directly loaded module to be present in `sys.modules`
while the module body executes. Register only that one named module.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import Any


_original_module_from_spec = importlib.util.module_from_spec


def _registered_module_from_spec(spec: Any) -> ModuleType:
    module = _original_module_from_spec(spec)
    if getattr(spec, "name", None) == "jan5_retrospective":
        sys.modules[spec.name] = module
    return module


importlib.util.module_from_spec = _registered_module_from_spec
