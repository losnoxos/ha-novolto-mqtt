"""The Novolto MQTT integration.

Talks directly to the same local MQTT broker the Novolto heating rod itself
publishes to - the same protocol used by the Venus OS driver dbus-novolto,
just consumed by Home Assistant's own `mqtt` integration.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.config_entries import SOURCE_IGNORE
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .connection import MqttConnection
from .const import (
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_MODEL,
    CONF_POWER_STAGES,
    CONF_SERIAL,
    CONF_SPTW_MAX,
    CONF_SPTWH_MAX,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    CONNECTION_MQTT,
    DEFAULT_TOPIC_CONTROL,
    DEFAULT_TOPIC_INFO,
    DOMAIN,
    LEGACY_CONF_ENABLE_BOARD_TEMPERATURE,
    LEGACY_CONF_MAX_POWER,
    LEGACY_DEFAULT_SPTW_MAX,
    LEGACY_DEFAULT_SPTWH_MAX,
    MODEL_P2300,
    MODEL_P3000,
    MODELS,
    STORAGE_VERSION,
)
from .device import NovoltoConfigEntry, NovoltoDevice, async_delete_issues

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]

# How often we re-check "did the device go silent", independent of new MQTT
# traffic.
_AVAILABILITY_CHECK_INTERVAL = timedelta(seconds=30)


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


async def async_migrate_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> bool:
    """Migrate entries of 0.5.0 and earlier (version 1.1) to 1.2.

    Writes every value the old version used implicitly into the entry, so
    changed defaults never alter an existing installation silently.
    """
    if entry.version > 1:
        return False
    if entry.version == 1 and entry.minor_version < 2:
        data = {**entry.data}
        data.setdefault(CONF_CONNECTION, CONNECTION_MQTT)
        # 0.5.0 keyed devices by topic, normally the serial number. The real
        # serial is taken from the first telegram (see _async_adopt_serial),
        # which also covers installations with a custom topic.
        data.setdefault(CONF_SERIAL, None)

        options = {**entry.options}
        max_power = options.pop(LEGACY_CONF_MAX_POWER, None)
        options.pop(LEGACY_CONF_ENABLE_BOARD_TEMPERATURE, None)
        model = MODEL_P2300 if max_power == MODELS[MODEL_P2300].max_power else MODEL_P3000
        options.setdefault(CONF_MODEL, model)
        options.setdefault(CONF_POWER_STAGES, list(MODELS[model].power_stages))
        options.setdefault(CONF_SPTW_MAX, LEGACY_DEFAULT_SPTW_MAX)
        options.setdefault(CONF_SPTWH_MAX, LEGACY_DEFAULT_SPTWH_MAX)

        hass.config_entries.async_update_entry(
            entry, data=data, options=options, minor_version=2
        )
        _LOGGER.debug("Migrated %s to version 1.2", entry.title)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> bool:
    """Set up Novolto MQTT from a config entry."""
    # `dependencies: [mqtt]` only guarantees the component is loaded, not that
    # the broker connection is set up - retry later instead of failing hard.
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="mqtt_unavailable"
        )

    options = entry.options
    connection = MqttConnection(
        hass,
        entry.data[CONF_BASE_TOPIC],
        options.get(CONF_TOPIC_INFO, DEFAULT_TOPIC_INFO),
        options.get(CONF_TOPIC_CONTROL, DEFAULT_TOPIC_CONTROL),
    )

    store = _store(hass, entry.entry_id)
    stored_data = await store.async_load()
    initial_energy_kwh = float((stored_data or {}).get("energy_kwh", 0.0))

    device = NovoltoDevice(
        hass=hass,
        entry=entry,
        connection=connection,
        store=store,
        energy_kwh=initial_energy_kwh,
        on_serial_learned=lambda serial: _async_adopt_serial(hass, entry, serial),
    )
    entry.runtime_data = device
    loaded_options = dict(entry.options)

    # Unload callbacks run in reverse order of registration: the final energy
    # save is registered first so it runs last, after MQTT and timer are gone.
    entry.async_on_unload(device.async_save_energy)
    entry.async_on_unload(await device.async_start())
    entry.async_on_unload(
        async_track_time_interval(
            hass, device.handle_availability_tick, _AVAILABILITY_CHECK_INTERVAL
        )
    )

    async def _async_update_listener(
        hass: HomeAssistant, entry: NovoltoConfigEntry
    ) -> None:
        """Reload when the options change (data updates need no reload)."""
        if dict(entry.options) != loaded_options:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_adopt_serial(
    hass: HomeAssistant, entry: NovoltoConfigEntry, serial: str
) -> None:
    """First telegram of a device added without data: switch to its serial.

    Until then the device was keyed by its topic. Device, entities and the
    energy counter are kept; only their identifiers change.
    """
    old_key = entry.unique_id or entry.data[CONF_BASE_TOPIC]
    dev_reg = dr.async_get(hass)
    if serial == old_key:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_SERIAL: serial}
        )
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            dev_reg.async_update_device(device.id, serial_number=serial)
        return
    other = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, serial)
    if (
        other is not None
        and other.entry_id != entry.entry_id
        and other.source != SOURCE_IGNORE
    ):
        _LOGGER.warning(
            "%s receives data of %s, which is already set up as %s",
            entry.title,
            serial,
            other.title,
        )
        return

    ent_reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if ent.unique_id.startswith(f"{old_key}_"):
            ent_reg.async_update_entity(
                ent.entity_id,
                new_unique_id=serial + ent.unique_id.removeprefix(old_key),
            )
    if other is not None and other.entry_id != entry.entry_id:
        # An ignored discovery of the same device: drop it.
        hass.async_create_task(hass.config_entries.async_remove(other.entry_id))
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if (DOMAIN, old_key) in device.identifiers:
            dev_reg.async_update_device(
                device.id, new_identifiers={(DOMAIN, serial)}, serial_number=serial
            )
    hass.config_entries.async_update_entry(
        entry,
        unique_id=serial,
        data={
            k: v
            for k, v in {**entry.data, CONF_SERIAL: serial}.items()
            if k != CONF_AWAITING_DATA
        },
    )
    _LOGGER.info("%s identified as %s", entry.title, serial)
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> bool:
    """Unload a config entry.

    Repair issues survive a reload (so an ignored issue stays ignored) but
    not disabling the device.
    """
    if entry.disabled_by:
        async_delete_issues(hass, entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> None:
    """Delete the persisted energy counter and issues when the device is removed."""
    async_delete_issues(hass, entry.entry_id)
    await _store(hass, entry.entry_id).async_remove()
