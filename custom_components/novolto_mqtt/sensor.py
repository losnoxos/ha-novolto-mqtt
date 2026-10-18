"""Sensor platform for Novolto MQTT."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FIELD_BOARD_TEMP,
    FIELD_CURRENT,
    FIELD_FREQUENCY,
    FIELD_MSI,
    FIELD_POWER,
    FIELD_R1ON,
    FIELD_R2ON,
    FIELD_ROD_STATUS,
    FIELD_RSSI,
    FIELD_STATUS,
    FIELD_TRIACON,
    FIELD_VOLTAGE,
    FIELD_WATER_TEMP,
    STATUS_BITS,
    STATUS_OK,
    STATUS_UNKNOWN_BIT,
)
from .device import NovoltoConfigEntry
from .entity import NovoltoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Novolto sensors from a config entry."""
    device = entry.runtime_data
    async_add_entities(
        cls(device)
        for cls in (
            NovoltoPowerSensor,
            NovoltoEnergySensor,
            NovoltoWaterTemperatureSensor,
            NovoltoBoardTemperatureSensor,
            NovoltoVoltageSensor,
            NovoltoCurrentSensor,
            NovoltoFrequencySensor,
            NovoltoStatusSensor,
            NovoltoRssiSensor,
            NovoltoMeasurementIntervalSensor,
            NovoltoStatusRawSensor,
            NovoltoRodStatusRawSensor,
            NovoltoTriaconRawSensor,
            NovoltoHeatingStage1RawSensor,
            NovoltoHeatingStage2RawSensor,
        )
    )


class _NovoltoFieldSensor(NovoltoEntity, SensorEntity):
    """Base for sensors that just read one numeric field from the telegram."""

    _field: str

    def __init__(self, device) -> None:
        """Initialize; unique_id suffix defaults to the field name."""
        if not hasattr(self, "_unique_suffix"):
            self._unique_suffix = self._field
        super().__init__(device)

    @property
    def native_value(self):
        """Return the field's current value, or None if never received."""
        return self.device.get(self._field)


class NovoltoPowerSensor(_NovoltoFieldSensor):
    """Measured power (`avp`), averaged by the device over 5s."""

    _field = FIELD_POWER
    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT


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
    """Electronics temperature (`avt1`) - always present and enabled."""

    _field = FIELD_BOARD_TEMP
    _attr_translation_key = "board_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoRssiSensor(_NovoltoFieldSensor):
    """WiFi signal strength (`rssi`) - only useful on WiFi, off by default."""

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


def decode_status(raw: int) -> tuple[str, list[str], int]:
    """Return (most important state, all active states, unknown bits)."""
    if raw < 0:
        return STATUS_UNKNOWN_BIT, [STATUS_UNKNOWN_BIT], raw
    active = [state for bit, state in STATUS_BITS.items() if raw & bit]
    unknown = raw & ~sum(STATUS_BITS)
    if unknown:
        active.append(STATUS_UNKNOWN_BIT)
    return (active[0] if active else STATUS_OK), active, unknown


class NovoltoStatusSensor(NovoltoEntity, SensorEntity):
    """Decoded status/warning bitflags (`st`).

    Shows the most important active message (several bits can be set at
    once); all active messages are listed in the attributes.
    """

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_OK, *STATUS_BITS.values(), STATUS_UNKNOWN_BIT]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unique_suffix = FIELD_STATUS

    @property
    def native_value(self) -> str | None:
        """Most important active message, "ok" if none.

        Bits this integration doesn't know count as "unknown_bit" - never
        as "ok".
        """
        raw = self.device.get(FIELD_STATUS)
        return None if raw is None else decode_status(raw)[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Raw bitmask and all active messages, for automations."""
        raw = self.device.get(FIELD_STATUS)
        if raw is None:
            return None
        _, active, unknown = decode_status(raw)
        attributes: dict[str, Any] = {"status_raw": raw, "active": active}
        if unknown:
            attributes["unknown_bits"] = f"0x{unknown & 0xFFFF:04X}" if unknown > 0 else str(unknown)
        return attributes


class NovoltoEnergySensor(NovoltoEntity, SensorEntity):
    """Energy integrated from measured power (`avp`), persisted on disk.

    Deliberately not the device's own `wel` field, which resets to 0 on
    every Novolto reboot.
    """

    _attr_translation_key = "energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _unique_suffix = "energy"

    @property
    def native_value(self) -> float:
        """Return the accumulated energy in kWh."""
        return round(self.device.energy_kwh, 3)


class _NovoltoRawSensor(_NovoltoFieldSensor):
    """Undocumented raw values - diagnostics, disabled by default.

    With a state_class they don't flood the logbook and get long-term
    statistics instead of single values when enabled.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT


class NovoltoStatusRawSensor(_NovoltoRawSensor):
    """Raw status/warning bitmask (`st`)."""

    _field = FIELD_STATUS
    _unique_suffix = f"{FIELD_STATUS}_raw"
    _attr_translation_key = "status_raw"


class NovoltoRodStatusRawSensor(_NovoltoRawSensor):
    """Raw `rod_st` value - unreliable as an on/off indicator."""

    _field = FIELD_ROD_STATUS
    _attr_translation_key = "rod_status_raw"


class NovoltoTriaconRawSensor(_NovoltoRawSensor):
    """Raw `triacon` counter - undocumented by the manufacturer."""

    _field = FIELD_TRIACON
    _attr_translation_key = "triacon_raw"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING


class NovoltoHeatingStage1RawSensor(_NovoltoRawSensor):
    """Raw `r1on` value - relay of element stage 1."""

    _field = FIELD_R1ON
    _attr_translation_key = "heating_stage_1_raw"


class NovoltoHeatingStage2RawSensor(_NovoltoRawSensor):
    """Raw `r2on` value - relay of element stage 2."""

    _field = FIELD_R2ON
    _attr_translation_key = "heating_stage_2_raw"
