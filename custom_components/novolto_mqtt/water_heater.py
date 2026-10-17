"""Water heater platform for Novolto MQTT (target water temperature)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ELECTRIC,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import NovoltoConfigEntry, NovoltoDevice
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
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Novolto water heater entity from a config entry."""
    device = entry.runtime_data
    async_add_entities([NovoltoWaterHeater(device)])


class NovoltoWaterHeater(NovoltoEntity, WaterHeaterEntity):
    """Tank temperature: current reading (`avtw`) and setpoint (`sptw`).

    In HA, a water heater's state is its *operation mode*, not its activity
    (WaterHeaterEntity.state returns `current_operation`, @final). The
    Novolto is an electric heater with no selectable mode and no on/off
    switch, so the honest mode is a constant "electric". Whether the rod is
    heating right now is exposed by the "Heating" binary sensor
    (device_class running), the HA-intended place for activity.
    """

    # Main feature of the device -> entity takes the device name
    # (e.g. "Novolto Boiler"), leaving "Heating" to the binary sensor.
    _attr_name = None
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_current_operation = STATE_ELECTRIC

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
        # HA core does not range-check water_heater.set_temperature itself.
        if not self.min_temp <= temperature <= self.max_temp:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "temperature": str(temperature),
                    "min_temp": str(self.min_temp),
                    "max_temp": str(self.max_temp),
                },
            )
        await self.device.async_set_value(
            CONTROL_NAME_TARGET_WATER_TEMP, float(temperature)
        )
