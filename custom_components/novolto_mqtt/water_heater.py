"""Water heater platform for Novolto MQTT (target water temperature)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FIELD_POWER,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP,
    FIELD_WATER_TEMP,
)
from .device import NovoltoConfigEntry
from .entity import NovoltoEntity

OPERATION_HEATING = "heating"
OPERATION_IDLE = "idle"
OPERATION_OFF = "off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Novolto water heater entity from a config entry."""
    async_add_entities([NovoltoWaterHeater(entry.runtime_data)])


class NovoltoWaterHeater(NovoltoEntity, WaterHeaterEntity):
    """Tank temperature: current reading (`avtw`) and setpoint (`sptw`).

    In HA a water heater's state is its operation mode. The Novolto has no
    selectable modes, so - deliberately deviating from that convention - the
    state shows what the rod is doing: heating (power above the threshold),
    idle (ready, not heating) or off (target power 0 W).
    """

    # Main feature of the device -> entity takes the device name.
    _attr_name = None
    _attr_translation_key = "novolto"
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _unique_suffix = "heating"

    @property
    def min_temp(self) -> float:
        """Lowest settable target temperature (options)."""
        return self.device.min_temp

    @property
    def max_temp(self) -> float:
        """Highest settable target temperature (options)."""
        return self.device.max_temp

    @property
    def current_operation(self) -> str | None:
        """Heating / idle / off, derived from target and measured power."""
        target_power = self.device.get(FIELD_TARGET_POWER)
        if target_power == 0:
            return OPERATION_OFF
        power = self.device.get(FIELD_POWER)
        if power is None:
            return None
        return (
            OPERATION_HEATING if power > self.device.heating_threshold else OPERATION_IDLE
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the measured tank water temperature (`avtw`)."""
        return self.device.get(FIELD_WATER_TEMP)

    @property
    def target_temperature(self) -> float | None:
        """Return the target water temperature (`sptw`)."""
        return self.device.get(FIELD_TARGET_WATER_TEMP)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send a new target water temperature (range-checked by the device).

        HA core does not range-check water_heater.set_temperature itself.
        """
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.device.async_set_target_temperature(temperature)
