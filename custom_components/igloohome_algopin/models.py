"""Official device-id prefixes from the igloohome home API.

Source: https://docs.igloohome.co/home/supported-devices
Table last updated 15 October 2026.

The Metal Grey deadbolt prefix on that table is IGB4, named
"Deadbolt 2S Metal Grey", and AlgoPIN is Yes. OE1X is "Retrofit Lock"
and AlgoPIN is No. There is no separate "Smart Deadbolt 4" row. A device
id that starts with IGB4 and not IGB4A is the Metal Grey / Deadbolt 2S
model. This module does not invent a model name for an unknown prefix.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    """One row of the official supported-devices table."""

    prefix: str
    name: str
    algopin: bool
    kind: str


_ROWS: tuple[ModelInfo, ...] = (
    ModelInfo("DBX1", "Deadbolt Go (Satin Nickel)", True, "lock"),
    ModelInfo("DBX2", "Deadbolt Go (Matte Black)", True, "lock"),
    ModelInfo("IGB4A", "Deadbolt 2E", True, "lock"),
    ModelInfo("IGB4", "Deadbolt 2S Metal Grey", True, "lock"),
    ModelInfo("IGK3", "Keybox 3", True, "lock"),
    ModelInfo("IGM3", "Mortise 2", True, "lock"),
    ModelInfo("IGM4", "Mortise 2+", True, "lock"),
    ModelInfo("IGP1", "Padlock", True, "lock"),
    ModelInfo("IGR1", "Rim Lock for Metal Gates", True, "lock"),
    ModelInfo("ML5X", "Smart Lever Mortise", True, "lock"),
    ModelInfo("MP1F", "Push-Pull Mortise", True, "lock"),
    ModelInfo("MT1X", "Mortise Touch", True, "lock"),
    ModelInfo("OE1X", "Retrofit Lock", False, "lock"),
    ModelInfo("RG1X", "Glass Door Lock", True, "lock"),
    ModelInfo("RM2F", "Gate Lock (with Fingerprint)", True, "lock"),
    ModelInfo("RM2X", "Gate Lock", True, "lock"),
    ModelInfo("RW1X", "Rim Lock for Wooden Doors", True, "lock"),
    ModelInfo("SK3E", "Keybox 3E", True, "lock"),
    ModelInfo("SK4X", "Keybox 4", True, "lock"),
    ModelInfo("SP1E", "Padlock E", True, "lock"),
    ModelInfo("SP2E", "Padlock 2E", True, "lock"),
    ModelInfo("SP2X", "Padlock 2", True, "lock"),
    ModelInfo("SP3X", "Padlock 3", False, "lock"),
    ModelInfo("SW1X", "Switch", False, "lock"),
    ModelInfo("EB1X", "Bridge", False, "bridge"),
    ModelInfo("EB2X", "Bridge 2", False, "bridge"),
    ModelInfo("EK1X", "Keypad", True, "keypad"),
    ModelInfo("IEF1", "Key Fob", False, "fob"),
)

# Longest prefix first so IGB4A wins over IGB4.
MODELS: tuple[ModelInfo, ...] = tuple(sorted(_ROWS, key=lambda row: len(row.prefix), reverse=True))


def lookup_model(device_id: str) -> ModelInfo | None:
    """Return the official row for a device id, or None if the prefix is unknown."""

    if not device_id:
        return None
    for info in MODELS:
        if device_id.startswith(info.prefix):
            return info
    return None


def model_name(device_id: str) -> str | None:
    """Official model name after a device id has been read. Unknown prefixes stay blank."""

    info = lookup_model(device_id)
    if info is None:
        return None
    return info.name


def device_label(device_name: str, device_id: str) -> str:
    """Label for a lock the API already returned and the user may choose."""

    name = " ".join((device_name or device_id).split())
    model = model_name(device_id)
    if model:
        return f"{name} — igloohome {model}"
    return f"{name} — igloohome"
