"""Diagnostic sensors. State is API status and access expiry, never a PIN."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN, VERSION
from .coordinator import IglooCoordinator
from .snapshot import AccountSnapshot

SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="status",
        translation_key="api_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:cloud-check",
    ),
    SensorEntityDescription(
        key="ends",
        translation_key="access_ends",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-end",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: IglooCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    async_add_entities(IglooStatusSensor(coordinator, entry, description) for description in SENSORS)


class IglooStatusSensor(CoordinatorEntity[IglooCoordinator], SensorEntity):
    """One diagnostic value from server health."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IglooCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}-{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account:{entry.entry_id}")},
            manufacturer=BRAND,
            model="igloohome API",
            name=entry.title,
            sw_version=VERSION,
        )

    @property
    def native_value(self):
        data: AccountSnapshot = self.coordinator.data
        if self.entity_description.key == "ends":
            return data.ends_at
        return data.status
