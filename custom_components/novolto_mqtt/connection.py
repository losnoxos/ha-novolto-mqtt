"""Transport layer: how the integration talks to a Novolto device.

The device logic (device.py) only knows the Novolto payloads - values,
acknowledgements, settings. How they travel is the job of a connection
class. Today there is only the local MQTT connection; a later cloud
connection would be a second implementation of `NovoltoConnection`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .const import CONTROL_MODULE_SENSOR, STORED_MESSAGE_WAIT_SECONDS

_LOGGER = logging.getLogger(__name__)

# (payload, retained) - payload is the decoded JSON object.
PayloadCallback = Callable[[dict[str, Any], bool], None]


class NovoltoConnection(ABC):
    """A way of receiving Novolto payloads and sending settings."""

    @abstractmethod
    async def async_start(self, on_payload: PayloadCallback) -> Callable[[], None]:
        """Start receiving; returns a callable that stops it again."""

    @abstractmethod
    async def async_send_setting(self, name: str, value: Any) -> None:
        """Send one setting (`spp`, `sptw`, `sptwh`) to the device."""

    @abstractmethod
    async def async_clear_stored_message(self) -> None:
        """Delete a message the transport stored for the device (if any)."""

    @abstractmethod
    async def async_fetch_stored_message(self) -> dict[str, Any] | None:
        """Return the message currently stored for the device, if any."""


def decode_payload(raw: str | bytes) -> dict[str, Any] | None:
    """Decode a JSON object payload, None for anything else."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


class MqttConnection(NovoltoConnection):
    """Local MQTT: the device publishes to the broker Home Assistant uses."""

    def __init__(
        self, hass: HomeAssistant, base_topic: str, info_suffix: str, control_suffix: str
    ) -> None:
        """Initialize for one device topic."""
        self.hass = hass
        self.topic_info = f"{base_topic}/{info_suffix}"
        self.topic_control = f"{base_topic}/{control_suffix}"

    async def async_start(self, on_payload: PayloadCallback) -> Callable[[], None]:
        """Subscribe to the info topic."""

        @callback
        def _message(msg: mqtt.ReceiveMessage) -> None:
            payload = decode_payload(msg.payload)
            if payload is None:
                _LOGGER.debug("Ignoring non-JSON payload on %s", msg.topic)
                return
            on_payload(payload, bool(msg.retain))

        return await mqtt.async_subscribe(self.hass, self.topic_info, _message)

    async def async_fetch_stored_message(self) -> dict[str, Any] | None:
        """Subscribe once more and return the retained telegram, if any.

        A new subscription gets the retained message delivered again (Home
        Assistant tracks retained delivery per subscription).
        """
        stored: list[dict[str, Any]] = []
        received = asyncio.Event()

        @callback
        def _message(msg: mqtt.ReceiveMessage) -> None:
            if msg.retain and (payload := decode_payload(msg.payload)) is not None:
                stored.append(payload)
                received.set()

        unsubscribe = await mqtt.async_subscribe(self.hass, self.topic_info, _message)
        try:
            async with asyncio.timeout(STORED_MESSAGE_WAIT_SECONDS):
                await received.wait()
        except TimeoutError:
            pass
        finally:
            unsubscribe()
        return stored[-1] if stored else None

    async def async_clear_stored_message(self) -> None:
        """Delete the retained message on the info topic.

        An empty retained publish removes it from the broker. The device
        doesn't subscribe to its info topic; our own handler and HA discovery
        ignore empty payloads (other subscribers see one empty message).
        """
        await mqtt.async_publish(self.hass, self.topic_info, "", retain=True)

    async def async_send_setting(self, name: str, value: Any) -> None:
        """Publish a setting change to the control topic.

        Per-field JSON type matters and is not uniform: the firmware rejects
        the wrong JSON type with ret=13. Callers pass an already correctly
        typed value (int for spp, float for sptw/sptwh).
        """
        payload = {CONTROL_MODULE_SENSOR: [{"name": name, "value": value}]}
        await mqtt.async_publish(self.hass, self.topic_control, json.dumps(payload))
