"""Number platform for Novolto MQTT (writable setpoints)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NovoltoDevice
from .const import (
    CONF_MAX_POWER,
    CONF_POWER_STEP,
    CONF_SPTWH_MAX,
    CONF_SPTWH_MIN,
    CONTROL_NAME_TARGET_POWER,
    CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS,
    DEFAULT_MAX_POWER,
    DEFAULT_POWER_STEP,
    DEFAULT_SPTWH_MAX,
    DEFAULT_SPTWH_MIN,
    DOMAIN,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP_HYSTERESIS,
)
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Novolto number entities from a config entry."""
    device: NovoltoDevice = hass.data[DOMAIN][entry.entry_id]["device"]
    async_add_entities(
        [
            NovoltoTargetPowerNumber(device),
            NovoltoWaterHysteresisNumber(device),
        ]
    )


class NovoltoTargetPowerNumber(NovoltoEntity, NumberEntity):
    """Manual power setpoint (`spp`) - `0 W` is the Novolto's only "off"."""

    _attr_translation_key = "target_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_min_value = 0

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the number entity, reading limits from options."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_target_power"
        options = device.entry.options
        self._attr_native_max_value = options.get(CONF_MAX_POWER, DEFAULT_MAX_POWER)
        self._attr_native_step = options.get(CONF_POWER_STEP, DEFAULT_POWER_STEP)

    @property
    def native_value(self) -> float | None:
        """Return the currently confirmed power setpoint."""
        return self.device.data.get(FIELD_TARGET_POWER)

    async def async_set_native_value(self, value: float) -> None:
        """Publish a new power setpoint.

        Sent as an int - the firmware rejects `spp` as float with
        ret=13 "wrong type" (see NOVOLTO-MQTT.md).
        """
        await self.device.async_set_value(CONTROL_NAME_TARGET_POWER, int(round(value)))


class NovoltoWaterHysteresisNumber(NovoltoEntity, NumberEntity):
    """Hysteresis (`sptwh`) around the target water temperature."""

    _attr_translation_key = "water_hysteresis"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 0.5

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the number entity, reading limits from options."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_water_hysteresis"
        options = device.entry.options
        self._attr_native_min_value = options.get(CONF_SPTWH_MIN, DEFAULT_SPTWH_MIN)
        self._attr_native_max_value = options.get(CONF_SPTWH_MAX, DEFAULT_SPTWH_MAX)

    @property
    def native_value(self) -> float | None:
        """Return the currently confirmed hysteresis value."""
        return self.device.data.get(FIELD_TARGET_WATER_TEMP_HYSTERESIS)

    async def async_set_native_value(self, value: float) -> None:
        """Publish a new hysteresis value.

        Sent as a float - the firmware rejects `sptwh` as int with
        ret=13 "wrong type" (see NOVOLTO-MQTT.md).
        """
        await self.device.async_set_value(
            CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS, float(value)
        )
