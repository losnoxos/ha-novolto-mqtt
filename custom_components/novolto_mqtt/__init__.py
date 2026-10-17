"""The Novolto MQTT integration.

Talks directly to the same local MQTT broker the Novolto heating rod itself
publishes to - the same protocol used by the Venus OS driver dbus-novolto,
just consumed by Home Assistant's own `mqtt` integration. See NOVOLTO-MQTT.md
in dbus-novolto for the full protocol reference.
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BASE_TOPIC,
    CONF_TIMEOUT_SECONDS,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_CONTROL,
    DEFAULT_TOPIC_INFO,
    DOMAIN,
    STORAGE_VERSION,
)
from .device import NovoltoConfigEntry, NovoltoDevice

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]

# How often we re-check "did the device go silent", independent of new MQTT
# traffic.
_AVAILABILITY_CHECK_INTERVAL = timedelta(seconds=30)


def _store(hass: HomeAssistant, entry_id: str) -> Store:
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")


async def async_setup_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> bool:
    """Set up Novolto MQTT from a config entry."""
    # `dependencies: [mqtt]` only guarantees the component is loaded, not that
    # the broker connection is set up - retry later instead of failing hard.
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT integration is not available")

    base_topic = entry.data[CONF_BASE_TOPIC]
    options = entry.options
    topic_info_suffix = options.get(CONF_TOPIC_INFO, DEFAULT_TOPIC_INFO)
    topic_control_suffix = options.get(CONF_TOPIC_CONTROL, DEFAULT_TOPIC_CONTROL)

    store = _store(hass, entry.entry_id)
    stored_data = await store.async_load()
    initial_energy_kwh = float((stored_data or {}).get("energy_kwh", 0.0))

    device = NovoltoDevice(
        hass=hass,
        entry=entry,
        base_topic=base_topic,
        topic_control=f"{base_topic}/{topic_control_suffix}",
        timeout_seconds=options.get(CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS),
        store=store,
        energy_kwh=initial_energy_kwh,
    )
    entry.runtime_data = device

    # Unload callbacks run in reverse order of registration: the final energy
    # save is registered first so it runs last, after MQTT and timer are gone.
    entry.async_on_unload(device.async_save_energy)
    entry.async_on_unload(
        await mqtt.async_subscribe(
            hass, f"{base_topic}/{topic_info_suffix}", device.handle_message
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, device.handle_availability_tick, _AVAILABILITY_CHECK_INTERVAL
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NovoltoConfigEntry) -> None:
    """Delete the persisted energy counter when the device is removed."""
    await _store(hass, entry.entry_id).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: NovoltoConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
