"""Binary sensor platform for Novolto MQTT."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FIELD_POWER, FIELD_ROD_STATUS
from .device import NovoltoConfigEntry
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Novolto binary sensors from a config entry."""
    device = entry.runtime_data
    async_add_entities(
        [NovoltoHeatingBinarySensor(device), NovoltoRodStatusBinarySensor(device)]
    )


class NovoltoHeatingBinarySensor(NovoltoEntity, BinarySensorEntity):
    """Whether the rod is actually heating right now.

    The Novolto's own `rod_st` field proved unreliable in testing (it can
    stay stuck at 1 at low power levels, see NOVOLTO-MQTT.md). We derive this
    from measured power exceeding a configurable threshold instead.
    """

    _attr_translation_key = "heating"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _unique_suffix = "heating"

    @property
    def is_on(self) -> bool | None:
        """Return True if measured power is above the heating threshold."""
        power = self.device.get(FIELD_POWER)
        if power is None:
            return None
        return power > self.device.heating_threshold


class NovoltoRodStatusBinarySensor(NovoltoEntity, BinarySensorEntity):
    """Raw `rod_st` field - diagnostics only, disabled by default."""

    _attr_translation_key = "rod_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _unique_suffix = FIELD_ROD_STATUS

    @property
    def is_on(self) -> bool | None:
        """Return the raw rod_st value as a boolean."""
        rod_status = self.device.get(FIELD_ROD_STATUS)
        return None if rod_status is None else rod_status > 0
