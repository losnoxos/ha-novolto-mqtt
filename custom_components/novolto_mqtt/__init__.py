"""The Novolto MQTT integration.

Talks directly to the same local MQTT broker the Novolto heating rod itself
publishes to - the same protocol used by the Venus OS drivers dbus-novolto /
heatpump-novolto, just consumed by Home Assistant's own `mqtt` integration
instead of Victron dbus. See NOVOLTO-MQTT.md for the full protocol
reference.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_TOPIC,
    CONF_TIMEOUT_SECONDS,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    CONTROL_MODULE_SENSOR,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_CONTROL,
    DEFAULT_TOPIC_INFO,
    DOMAIN,
    MEASUREMENT_FIELDS,
    SIGNAL_UPDATE,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]

# How often we re-check "did the device go silent" and nudge entities to
# re-render their availability, independent of new MQTT traffic.
_AVAILABILITY_CHECK_INTERVAL_SECONDS = 30


@dataclass
class NovoltoDevice:
    """Runtime state for one Novolto device (= one config entry)."""

    hass: HomeAssistant
    entry: ConfigEntry
    base_topic: str
    topic_control: str
    timeout_seconds: int
    data: dict[str, Any] = field(default_factory=dict)
    last_seen: datetime | None = None

    @property
    def signal(self) -> str:
        """Dispatcher signal used to notify this device's entities."""
        return SIGNAL_UPDATE.format(entry_id=self.entry.entry_id)

    @property
    def available(self) -> bool:
        """Whether the device has sent a telegram recently enough."""
        if self.last_seen is None:
            return False
        age = (dt_util.utcnow() - self.last_seen).total_seconds()
        return age <= self.timeout_seconds

    @callback
    def _handle_message(self, msg: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.debug("Ignoring non-JSON payload on %s", msg.topic)
            return

        if not isinstance(payload, dict):
            return

        # Control-command acknowledgements land on the very same info topic
        # (serial/unix_time/ret/s_err only, no measurement fields) - treat
        # them as a distinct message, not a (partial) telegram, so we never
        # blank out sensors that simply weren't part of this payload.
        if MEASUREMENT_FIELDS.isdisjoint(payload):
            if "ret" in payload and payload.get("ret") not in (0, None):
                _LOGGER.warning(
                    "Novolto %s rejected a setting: %s",
                    self.base_topic,
                    payload.get("s_err", payload),
                )
            return

        self.data.update(payload)
        self.last_seen = dt_util.utcnow()
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _handle_availability_tick(self, _now: datetime) -> None:
        async_dispatcher_send(self.hass, self.signal)

    async def async_set_value(self, name: str, value: Any) -> None:
        """Publish a setting change to <base_topic>/<topic_control>.

        Per-field JSON type matters here and is not uniform - see
        NOVOLTO-MQTT.md ("Real-World-Erkenntnis"): the firmware rejects the
        wrong JSON type with ret=13. Callers pass an already-correctly-typed
        value (int for spp, float for sptw/sptwh).
        """
        payload = {CONTROL_MODULE_SENSOR: [{"name": name, "value": value}]}
        await mqtt.async_publish(
            self.hass, self.topic_control, json.dumps(payload)
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Novolto MQTT from a config entry."""
    base_topic = entry.data[CONF_BASE_TOPIC]
    options = entry.options

    topic_info_suffix = options.get(CONF_TOPIC_INFO, DEFAULT_TOPIC_INFO)
    topic_control_suffix = options.get(CONF_TOPIC_CONTROL, DEFAULT_TOPIC_CONTROL)
    timeout_seconds = options.get(CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)

    device = NovoltoDevice(
        hass=hass,
        entry=entry,
        base_topic=base_topic,
        topic_control=f"{base_topic}/{topic_control_suffix}",
        timeout_seconds=timeout_seconds,
    )

    unsub_mqtt = await mqtt.async_subscribe(
        hass, f"{base_topic}/{topic_info_suffix}", device._handle_message
    )
    unsub_timer = async_track_time_interval(
        hass,
        device._handle_availability_tick,
        timedelta(seconds=_AVAILABILITY_CHECK_INTERVAL_SECONDS),
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "device": device,
        "unsub_mqtt": unsub_mqtt,
        "unsub_timer": unsub_timer,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        stored = hass.data[DOMAIN].pop(entry.entry_id)
        stored["unsub_mqtt"]()
        stored["unsub_timer"]()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
