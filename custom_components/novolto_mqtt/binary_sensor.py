"""Binary sensor platform for Novolto MQTT."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NovoltoDevice
from .const import (
    CONF_HEATING_THRESHOLD_W,
    DEFAULT_HEATING_THRESHOLD_W,
    DOMAIN,
    FIELD_POWER,
    FIELD_ROD_STATUS,
)
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Novolto binary sensors from a config entry."""
    device: NovoltoDevice = hass.data[DOMAIN][entry.entry_id]["device"]
    async_add_entities(
        [
            NovoltoHeatingBinarySensor(device),
            NovoltoRodStatusBinarySensor(device),
        ]
    )


class NovoltoHeatingBinarySensor(NovoltoEntity, BinarySensorEntity):
    """Whether the rod is actually heating right now.

    The Novolto has no real on/off state and its own `rod_st` field proved
    unreliable in testing (it can stay stuck at 1 at low power levels, see
    NOVOLTO-MQTT.md). We derive this instead from measured power exceeding a
    configurable threshold - the same approach validated in dbus-novolto.
    """

    _attr_translation_key = "heating"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the binary sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_heating"

    @property
    def is_on(self) -> bool | None:
        """Return True if measured power is above the heating threshold."""
        power = self.device.data.get(FIELD_POWER)
        if power is None:
            return None
        threshold = self.device.entry.options.get(
            CONF_HEATING_THRESHOLD_W, DEFAULT_HEATING_THRESHOLD_W
        )
        return power > threshold


class NovoltoRodStatusBinarySensor(NovoltoEntity, BinarySensorEntity):
    """Raw `rod_st` field as a binary sensor - kept for parity/diagnostics.

    Prefer NovoltoHeatingBinarySensor for automations: NOVOLTO-MQTT.md
    documents `rod_st` getting stuck at 1 at low power levels.
    """

    _attr_translation_key = "rod_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the binary sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_{FIELD_ROD_STATUS}"

    @property
    def is_on(self) -> bool | None:
        """Return the raw rod_st value as a boolean."""
        rod_status = self.device.data.get(FIELD_ROD_STATUS)
        return None if rod_status is None else rod_status > 0
