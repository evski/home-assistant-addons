"""Config-entry diagnostics. The payload is the status snapshot and nothing else."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .snapshot import diagnostics_payload


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is None or runtime.coordinator.data is None:
        return {"status": "not_loaded"}
    return diagnostics_payload(runtime.coordinator.data)
