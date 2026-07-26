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
    UnitOfEnergy,
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
    FIELD_R1ON,
    FIELD_R2ON,
    FIELD_ROD_STATUS,
    FIELD_RSSI,
    FIELD_STATUS,
    FIELD_TRIACON,
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
        NovoltoEnergySensor(device),
        NovoltoStatusRawSensor(device),
        NovoltoRodStatusRawSensor(device),
        NovoltoTriaconRawSensor(device),
        NovoltoHeatingStage1RawSensor(device),
        NovoltoHeatingStage2RawSensor(device),
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


class NovoltoEnergySensor(NovoltoEntity, SensorEntity):
    """Energy integrated from measured power (`avp`), persisted on disk.

    Deliberately not the device's own `wel` field, which resets to 0 on
    every Novolto reboot - same reasoning as dbus-novolto's `integrate`
    energy_source.
    """

    _attr_translation_key = "energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the energy sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_energy"

    @property
    def native_value(self) -> float:
        """Return the accumulated energy in kWh."""
        return round(self.device.energy_kwh, 3)


class NovoltoStatusRawSensor(_NovoltoFieldSensor):
    """Raw status/warning bitmask (`st`) - see NovoltoStatusSensor for the decoded version."""

    _field = FIELD_STATUS
    _attr_translation_key = "status_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize with a distinct unique_id from NovoltoStatusSensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.base_topic}_{FIELD_STATUS}_raw"


class NovoltoRodStatusRawSensor(_NovoltoFieldSensor):
    """Raw `rod_st` value - see NOVOLTO-MQTT.md for why this is unreliable
    as an actual on/off indicator (kept only for parity/diagnostics).
    """

    _field = FIELD_ROD_STATUS
    _attr_translation_key = "rod_status_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC


class NovoltoTriaconRawSensor(_NovoltoFieldSensor):
    """Raw `triacon` counter - undocumented by the manufacturer."""

    _field = FIELD_TRIACON
    _attr_translation_key = "triacon_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC


class NovoltoHeatingStage1RawSensor(_NovoltoFieldSensor):
    """Raw `r1on` value - undocumented by the manufacturer."""

    _field = FIELD_R1ON
    _attr_translation_key = "heating_stage_1_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC


class NovoltoHeatingStage2RawSensor(_NovoltoFieldSensor):
    """Raw `r2on` value - undocumented by the manufacturer."""

    _field = FIELD_R2ON
    _attr_translation_key = "heating_stage_2_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
