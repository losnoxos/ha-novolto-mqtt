"""Sensor platform for Novolto MQTT."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NovoltoDevice
from .const import (
    CONF_ENABLE_BOARD_TEMPERATURE,
    DEFAULT_ENABLE_BOARD_TEMPERATURE,
    DOMAIN,
    FIELD_BOARD_TEMP,
    FIELD_CURRENT,
    FIELD_FREQUENCY,
    FIELD_MSI,
    FIELD_RSSI,
    FIELD_STATUS,
    FIELD_VOLTAGE,
    FIELD_WATER_TEMP,
    STATUS_BITS,
)
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Novolto sensors from a config entry."""
    device: NovoltoDevice = hass.data[DOMAIN][entry.entry_id]["device"]

    entities: list[NovoltoEntity] = [
        NovoltoVoltageSensor(device),
        NovoltoCurrentSensor(device),
        NovoltoFrequencySensor(device),
        NovoltoWaterTemperatureSensor(device),
        NovoltoStatusSensor(device),
        NovoltoRssiSensor(device),
        NovoltoMeasurementIntervalSensor(device),
    ]

    if entry.options.get(
        CONF_ENABLE_BOARD_TEMPERATURE, DEFAULT_ENABLE_BOARD_TEMPERATURE
    ):
        entities.append(NovoltoBoardTemperatureSensor(device))

    async_add_entities(entities)


class _NovoltoFieldSensor(NovoltoEntity, SensorEntity):
    """Base for sensors that just read one numeric field from the telegram."""

    _field: str

    def __init__(self, device: NovoltoDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_{self._field}"

    @property
    def native_value(self):
        """Return the field's current value, or None if never received."""
        return self.device.data.get(self._field)


class NovoltoVoltageSensor(_NovoltoFieldSensor):
    """Mains voltage (`avv`), averaged by the device over 5s."""

    _field = FIELD_VOLTAGE
    _attr_translation_key = "voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoCurrentSensor(_NovoltoFieldSensor):
    """Current draw (`avi`), averaged by the device over 5s."""

    _field = FIELD_CURRENT
    _attr_translation_key = "current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoFrequencySensor(_NovoltoFieldSensor):
    """Mains frequency (`avf`)."""

    _field = FIELD_FREQUENCY
    _attr_translation_key = "frequency"
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoWaterTemperatureSensor(_NovoltoFieldSensor):
    """Tank water temperature (`avtw`) - same value the water_heater shows."""

    _field = FIELD_WATER_TEMP
    _attr_translation_key = "water_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoBoardTemperatureSensor(_NovoltoFieldSensor):
    """Electronics/board temperature (`avt1`) - opt-in, not every unit needs it."""

    _field = FIELD_BOARD_TEMP
    _attr_translation_key = "board_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC


class NovoltoRssiSensor(_NovoltoFieldSensor):
    """WiFi signal strength (`rssi`) - diagnostic, off by default."""

    _field = FIELD_RSSI
    _attr_translation_key = "rssi"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class NovoltoMeasurementIntervalSensor(_NovoltoFieldSensor):
    """How often the device itself sends telegrams (`msi`) - diagnostic."""

    _field = FIELD_MSI
    _attr_translation_key = "measurement_interval"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class NovoltoStatusSensor(NovoltoEntity, SensorEntity):
    """Decoded status/warning bitflags (`st`), e.g. 'STB tripped'."""

    _attr_translation_key = "status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: NovoltoDevice) -> None:
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_{FIELD_STATUS}"

    @property
    def native_value(self) -> str | None:
        """Return a comma-separated list of active warnings, or 'OK'."""
        raw = self.device.data.get(FIELD_STATUS)
        if raw is None:
            return None
        active = [message for bit, message in STATUS_BITS.items() if raw & bit]
        return ", ".join(active) if active else "OK"

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Expose the raw bitmask for automations that want to check a bit."""
        raw = self.device.data.get(FIELD_STATUS)
        return None if raw is None else {"status_raw": raw}
