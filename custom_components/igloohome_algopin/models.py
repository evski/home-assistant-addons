"""Prefix check against the published igloohome supported-devices table.

Source: https://docs.igloohome.co/home/supported-devices
Table last updated 15 October 2026.

The table is used only to accept or reject a device id prefix, together
with the ``type`` value the device list actually returns (Lock, Keypad,
Bridge, Fob). It is not a map from a retail product name to a lock.

IGB4 on that table is "Deadbolt 2S Metal Grey" and AlgoPIN is yes.
OE1X is "Retrofit Lock" and AlgoPIN is no. "Smart Deadbolt 4 Metal Grey"
is not a row. This module does not treat that retail text as a device id,
and it does not assign a model name to a Home Assistant device.
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


# ``type`` values documented on GET /home/devices: Lock, Keypad, Bridge, Fob.


def classify_algopin(device_id: str, device_type: str | None) -> str:
    """Return ``allow``, ``deny``, or ``unconfirmed``.

    ``device_type`` is the API ``type`` field as returned. An empty string
    means the field was absent. A retail product name is not a type and is
    not accepted here.

    * Bridge and Fob are documented device types and are not AlgoPIN devices.
    * A prefix on the supported-devices table with AlgoPIN no is denied.
    * A prefix on that table with AlgoPIN yes is allowed when ``type`` is
      missing, Lock, or Keypad.
    * Any other prefix stays ``unconfirmed``. The caller may still show it
      when ``GET /home/devices/support-pins`` returned it, without naming a model.
    """

    reported = (device_type or "").strip().lower()
    if reported in {"bridge", "fob"}:
        return "deny"
    info = lookup_model(device_id)
    if info is not None and (not info.algopin or info.kind in {"bridge", "fob"}):
        return "deny"
    if info is not None and info.algopin:
        if reported in {"", "lock", "keypad"}:
            return "allow"
        return "unconfirmed"
    return "unconfirmed"


def device_label(device_name: str, device_id: str, device_type: str = "") -> str:
    """Picker label. Uses the account name and device id, not a product model."""

    name = " ".join((device_name or device_id).split())
    status = classify_algopin(device_id, device_type)
    info = lookup_model(device_id)
    if status == "deny":
        shown = info.prefix if info else (device_id[:4] if device_id else "")
        return f"{name} — {device_id} — prefix {shown} is not offered for AlgoPIN"
    if status == "allow" and info is not None:
        return (
            f"{name} — {device_id} — prefix {info.prefix} is AlgoPIN yes on the "
            "supported-devices table. Confirm this device id in the igloohome account."
        )
    leading = device_id[:4] if device_id else ""
    return (
        f"{name} — {device_id} — leading characters {leading} are not a published prefix. "
        "Read the device id in the igloohome account before use."
    )
