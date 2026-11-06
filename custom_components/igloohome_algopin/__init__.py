"""Igloo AlgoPIN custom integration.

This is not an add-on. Setup talks to the igloohome cloud API only. It does
not open a Bluetooth connection to a lock, and it does not create lock,
unlock, or battery entities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import IglooClient
from .const import BRAND, DOMAIN, PLATFORMS, VERSION
from .coordinator import IglooCoordinator
from .services import async_register_services
from .snapshot import AccountSnapshot, entity_locks

_LOGGER = logging.getLogger(__name__)


@dataclass
class IglooRuntime:
    """In-memory objects for one config entry. The PIN is not one of them."""

    entry: ConfigEntry
    client: IglooClient
    coordinator: IglooCoordinator


def account_identifier(entry_id: str) -> str:
    return f"account:{entry_id}"


def sync_devices(hass: HomeAssistant, entry: ConfigEntry, snapshot: AccountSnapshot) -> None:
    """Create a device only for each selected lock that is still offered."""

    registry = dr.async_get(hass)
    account = (DOMAIN, account_identifier(entry.entry_id))
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={account},
        manufacturer=BRAND,
        name=entry.title,
        model="igloohome API",
        sw_version=VERSION,
    )
    locks = entity_locks(snapshot)
    keep = {lock.device_id for lock in locks}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        for domain, ident in device.identifiers:
            if domain != DOMAIN or ident.startswith("account:"):
                continue
            if ident not in keep:
                registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
    for lock in locks:
        if lock.device_name == lock.device_id:
            device_name = lock.device_id
        else:
            device_name = f"{lock.device_name} ({lock.device_id})"
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, lock.device_id)},
            manufacturer=BRAND,
            name=device_name,
            sw_version=VERSION,
            via_device=account,
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register services once. YAML config is not used."""

    hass.data.setdefault(DOMAIN, {})
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Authenticate, then load billing status and the chosen locks."""

    client = IglooClient(
        async_get_clientsession(hass),
        entry.data["client_id"],
        entry.data["client_secret"],
    )
    coordinator = IglooCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    runtime = IglooRuntime(entry=entry, client=client, coordinator=coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    sync_devices(hass, entry, coordinator.data)
    entry.async_on_unload(coordinator.async_add_listener(lambda: sync_devices(hass, entry, coordinator.data)))
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info(
        "Igloo AlgoPIN ready for %s lock(s); status %s",
        len(coordinator.data.locks),
        coordinator.data.status,
    )
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload sensors. Services stay registered and read live entry data."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
