"""Water heater platform for Novolto MQTT (target water temperature)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NovoltoDevice
from .const import (
    CONF_SPTW_MAX,
    CONF_SPTW_MIN,
    CONTROL_NAME_TARGET_WATER_TEMP,
    DEFAULT_SPTW_MAX,
    DEFAULT_SPTW_MIN,
    DOMAIN,
    FIELD_TARGET_WATER_TEMP,
    FIELD_WATER_TEMP,
)
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Novolto water heater entity from a config entry."""
    device: NovoltoDevice = hass.data[DOMAIN][entry.entry_id]["device"]
    async_add_entities([NovoltoWaterHeater(device)])


class NovoltoWaterHeater(NovoltoEntity, WaterHeaterEntity):
    """Tank temperature: current reading (`avtw`) and setpoint (`sptw`).

    The Novolto has no selectable operation mode - only a target
    temperature - so this entity only declares TARGET_TEMPERATURE support.
    `current_operation` is fixed to "off": WaterHeaterEntity.state always
    returns current_operation (it's a @final property), so leaving it at
    its None default would show as "unknown" in the UI. The MQTT water_heater
    platform in HA core defaults to the same STATE_OFF when no mode topic is
    configured - matching that keeps parity with the existing manual setup.
    """

    _attr_translation_key = "heating"
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_operation = STATE_OFF

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the water heater entity, reading limits from options."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_heating"
        options = device.entry.options
        self._attr_min_temp = options.get(CONF_SPTW_MIN, DEFAULT_SPTW_MIN)
        self._attr_max_temp = options.get(CONF_SPTW_MAX, DEFAULT_SPTW_MAX)

    @property
    def current_temperature(self) -> float | None:
        """Return the measured tank water temperature (`avtw`)."""
        return self.device.data.get(FIELD_WATER_TEMP)

    @property
    def target_temperature(self) -> float | None:
        """Return the confirmed target water temperature (`sptw`)."""
        return self.device.data.get(FIELD_TARGET_WATER_TEMP)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Publish a new target water temperature.

        Sent as a float - the firmware rejects `sptw` as int with
        ret=13 "wrong type" (see NOVOLTO-MQTT.md).
        """
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.device.async_set_value(
            CONTROL_NAME_TARGET_WATER_TEMP, float(temperature)
        )
