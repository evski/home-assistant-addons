"""Services that return a PIN only to the caller.

``SupportsResponse.ONLY`` means Home Assistant will not run the service
unless the caller accepts a response. The PIN is that response. It is not
written to an entity, the logbook, an event, or a Store.
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .api import IglooApiError, IglooAuthError, IglooError, IglooPaymentError
from .const import (
    CONF_ACCESS_NAME,
    CONF_BRIDGE_ID,
    CONF_DEVICE_ID,
    CONF_END,
    CONF_PIN,
    CONF_START,
    CONF_VARIANCE,
    DOMAIN,
    SERVICE_CREATE_DAILY,
    SERVICE_CREATE_HOURLY,
    SERVICE_REVOKE,
)
from .schedule import WindowError, clean_access_name, clean_pin, clean_variance, validate_window
from .snapshot import LockView

_LOGGER = logging.getLogger(__name__)

_CREATE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_ACCESS_NAME): cv.string,
        vol.Required(CONF_START): cv.datetime,
        vol.Required(CONF_END): cv.datetime,
        vol.Optional(CONF_VARIANCE, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
    }
)

_REVOKE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Required(CONF_PIN): cv.string,
        vol.Optional(CONF_BRIDGE_ID): cv.string,
    }
)


def _home_zone(hass: HomeAssistant):
    name = hass.config.time_zone
    if not name:
        raise ServiceValidationError("set a timezone for Home Assistant before creating an AlgoPIN")
    try:
        return ZoneInfo(name)
    except Exception as err:
        raise ServiceValidationError("Home Assistant timezone is not valid") from err


def _find_lock(hass: HomeAssistant, device_id: str):
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    igloo_id = None
    entry_id = None
    loaded = hass.data.get(DOMAIN, {})
    if device is not None:
        for domain, ident in device.identifiers:
            if domain == DOMAIN and not str(ident).startswith("account:"):
                igloo_id = ident
        for candidate in device.config_entries:
            if candidate in loaded:
                entry_id = candidate
                break
    if igloo_id is None:
        igloo_id = device_id
        for candidate, runtime in loaded.items():
            data = runtime.coordinator.data
            if data and any(lock.device_id == igloo_id for lock in data.locks):
                entry_id = candidate
                break
    if entry_id is None or not igloo_id:
        raise ServiceValidationError("select a lock that was chosen from the igloohome account device list")
    runtime = loaded[entry_id]
    data = runtime.coordinator.data
    lock: LockView | None = None
    if data is not None:
        lock = next((item for item in data.locks if item.device_id == igloo_id), None)
    if lock is None:
        raise ServiceValidationError("that lock is not on the latest igloohome AlgoPIN device list")
    return runtime, lock


def _bridge_for(lock: LockView, requested: str | None) -> str:
    if requested:
        bridge_id = requested.strip()
        if bridge_id not in lock.bridge_ids:
            raise ServiceValidationError(
                "that bridge was not linked to this lock on the latest device list. "
                "Deleting a PIN is a Bridge Bluetooth job. A lock the Bridge cannot "
                "reach is left unchanged."
            )
        return bridge_id
    if len(lock.bridge_ids) == 1:
        return lock.bridge_ids[0]
    if not lock.bridge_ids:
        raise ServiceValidationError(
            "no Bridge was linked to this lock on the latest device list. "
            "The home API deletes a PIN with bridge job type 5, which the Bridge "
            "delivers over Bluetooth. Creating an AlgoPIN does not need a Bridge, "
            "and this service cannot delete a code from an offline lock."
        )
    raise ServiceValidationError(
        "this lock has more than one Bridge on the device list. Pass bridge_id. Linked: "
        + ", ".join(lock.bridge_ids)
    )


def _service_error(err: IglooError) -> HomeAssistantError:
    if isinstance(err, IglooPaymentError):
        return HomeAssistantError(
            "igloohome returned HTTP 402. After the trial the published price is "
            "$2 per active lock per month."
        )
    if isinstance(err, IglooApiError) and err.status == 406:
        return HomeAssistantError(
            "the Bridge could not be contacted, so the PIN was not deleted from the lock. " + str(err)
        )
    if isinstance(err, IglooApiError) and err.status == 418:
        return HomeAssistantError("this device SKU does not support AlgoPIN")
    if isinstance(err, IglooApiError) and err.status == 409:
        return HomeAssistantError("the igloohome property has no timezone set. " + str(err))
    return HomeAssistantError(str(err))


def async_register_services(hass: HomeAssistant) -> None:
    """Register the three services. Safe to call again after a reload."""

    if hass.services.has_service(DOMAIN, SERVICE_CREATE_HOURLY):
        return

    async def create_hourly(call: ServiceCall) -> dict:
        return await _create(hass, call, "hourly")

    async def create_daily(call: ServiceCall) -> dict:
        return await _create(hass, call, "daily")

    async def revoke(call: ServiceCall) -> dict:
        runtime, lock = _find_lock(hass, call.data[CONF_DEVICE_ID])
        try:
            pin = clean_pin(call.data[CONF_PIN])
            bridge_id = _bridge_for(lock, call.data.get(CONF_BRIDGE_ID))
        except WindowError as err:
            raise ServiceValidationError(str(err)) from err
        try:
            result = await runtime.client.revoke(lock.device_id, bridge_id, pin)
        except IglooAuthError as err:
            runtime.entry.async_start_reauth(hass)
            raise _service_error(err) from err
        except IglooError as err:
            raise _service_error(err) from err
        _LOGGER.info("Requested AlgoPIN delete job %s for %s", result.job_id, lock.device_id)
        return {
            "job_id": result.job_id,
            "completed": result.completed,
            "job_status": result.job_status,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_HOURLY,
        create_hourly,
        schema=_CREATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_DAILY,
        create_daily,
        schema=_CREATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REVOKE,
        revoke,
        schema=_REVOKE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def _create(hass: HomeAssistant, call: ServiceCall, kind: str) -> dict:
    runtime, lock = _find_lock(hass, call.data[CONF_DEVICE_ID])
    try:
        window = validate_window(
            kind,
            call.data[CONF_START],
            call.data[CONF_END],
            _home_zone(hass),
            dt_util.utcnow(),
        )
        access_name = clean_access_name(call.data[CONF_ACCESS_NAME])
        variance = clean_variance(call.data.get(CONF_VARIANCE, 1))
    except WindowError as err:
        raise ServiceValidationError(str(err)) from err
    method = runtime.client.create_hourly if kind == "hourly" else runtime.client.create_daily
    try:
        result = await method(lock.device_id, access_name, window.start_api, window.end_api, variance)
    except IglooAuthError as err:
        runtime.entry.async_start_reauth(hass)
        raise _service_error(err) from err
    except IglooError as err:
        raise _service_error(err) from err
    _LOGGER.info("Created %s AlgoPIN for %s", kind, lock.device_id)
    return {"pin": result.pin, "pin_id": result.pin_id}
