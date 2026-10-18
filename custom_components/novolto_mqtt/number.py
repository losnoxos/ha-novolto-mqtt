"""Number platform for Novolto MQTT (writable setpoints)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_POWER_STEP,
    CONF_SPTWH_MAX,
    CONF_SPTWH_MIN,
    DEFAULT_POWER_STEP,
    DEFAULT_SPTWH_MAX,
    DEFAULT_SPTWH_MIN,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP_HYSTERESIS,
)
from .device import NovoltoConfigEntry, NovoltoDevice
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Novolto number entities from a config entry."""
    device = entry.runtime_data
    async_add_entities(
        [NovoltoTargetPowerNumber(device), NovoltoWaterHysteresisNumber(device)]
    )


class NovoltoTargetPowerNumber(NovoltoEntity, NumberEntity):
    """Power setpoint (`spp`) - `0 W` is the Novolto's only "off"."""

    _attr_translation_key = "target_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0
    _unique_suffix = "target_power"

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize; the maximum follows the model."""
        super().__init__(device)
        self._attr_native_max_value = device.model.max_power
        self._attr_native_step = device.option(CONF_POWER_STEP, DEFAULT_POWER_STEP)

    @property
    def native_value(self) -> float | None:
        """Return the power setpoint (just sent or confirmed)."""
        return self.device.get(FIELD_TARGET_POWER)

    async def async_set_native_value(self, value: float) -> None:
        """Send a new power setpoint."""
        await self.device.async_set_target_power(value)


class NovoltoWaterHysteresisNumber(NovoltoEntity, NumberEntity):
    """Hysteresis (`sptwh`) around the target water temperature."""

    _attr_translation_key = "water_hysteresis"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 0.5
    _unique_suffix = "water_hysteresis"

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the number entity, reading limits from options."""
        super().__init__(device)
        self._attr_native_min_value = device.option(CONF_SPTWH_MIN, DEFAULT_SPTWH_MIN)
        self._attr_native_max_value = device.option(CONF_SPTWH_MAX, DEFAULT_SPTWH_MAX)

    @property
    def native_value(self) -> float | None:
        """Return the hysteresis (just sent or confirmed)."""
        return self.device.get(FIELD_TARGET_WATER_TEMP_HYSTERESIS)

    async def async_set_native_value(self, value: float) -> None:
        """Send a new hysteresis value."""
        await self.device.async_set_hysteresis(value)
