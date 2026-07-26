"""Shared base entity for the Novolto MQTT integration."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from . import NovoltoDevice
from .const import DOMAIN


class NovoltoEntity(Entity):
    """Base entity: shared device info, availability and update wiring."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize the entity for one Novolto device."""
        self.device = device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.base_topic)},
            name=device.entry.title,
            manufacturer="Novolto",
            serial_number=device.base_topic,
        )

    @property
    def available(self) -> bool:
        """Return whether the device has reported in recently enough."""
        return self.device.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to device updates once added to hass."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.device.signal, self._handle_device_update
            )
        )

    @callback
    def _handle_device_update(self) -> None:
        """Handle a new telegram (or availability tick) from the device."""
        self.async_write_ha_state()
