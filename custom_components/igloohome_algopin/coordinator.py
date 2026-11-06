"""Polling coordinator for API status. It does not poll the lock."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IglooApiError, IglooAuthError, IglooClient, IglooConnectionError, IglooPaymentError
from .const import DOMAIN, POLL_INTERVAL
from .models import model_name
from .snapshot import AccountSnapshot, LockView, parse_chosen

_LOGGER = logging.getLogger(__name__)


class IglooCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    """Refresh billing status and the account device list."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: IglooClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> AccountSnapshot:
        chosen = {device_id for device_id, _name in parse_chosen(dict(self.entry.options))}
        try:
            return await self.client.fetch_snapshot(chosen)
        except IglooPaymentError as err:
            _LOGGER.debug("igloohome API billing rejected a status refresh: %s", err)
            previous = self.data.locks if self.data else ()
            if not previous:
                previous = tuple(
                    LockView(
                        device_id=device_id,
                        device_name=name,
                        model=model_name(device_id),
                        bridge_ids=(),
                    )
                    for device_id, name in parse_chosen(dict(self.entry.options))
                )
            return AccountSnapshot(
                status="payment_required",
                ends_at=None,
                tier=None,
                locks=previous,
                devices_fresh=False,
            )
        except IglooAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except IglooConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except IglooApiError as err:
            raise UpdateFailed(str(err)) from err
