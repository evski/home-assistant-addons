"""Load integration submodules without importing Home Assistant via package __init__."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_PARENT = ROOT / "custom_components"
PACKAGE = "custom_components.igloohome_algopin"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

parent = sys.modules.get("custom_components")
if parent is None:
    parent = types.ModuleType("custom_components")
    parent.__path__ = [str(COMPONENT_PARENT)]  # type: ignore[attr-defined]
    parent.__package__ = "custom_components"
    sys.modules["custom_components"] = parent

if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT_PARENT / "igloohome_algopin")]  # type: ignore[attr-defined]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package
