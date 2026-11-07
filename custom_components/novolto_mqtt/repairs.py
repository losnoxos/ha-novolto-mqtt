"""Repair flows for Novolto MQTT."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from . import async_adopt_serial
from .const import DOMAIN, ISSUE_OTHER_DEVICE, ISSUE_STALE_RETAINED
from .device import other_entry_for_serial


class StaleRetainedFlow(RepairsFlow):
    """Confirm, then delete the outdated retained message on the broker."""

    def __init__(self, issue_id: str, entry_id: str | None) -> None:
        """Initialize for one config entry."""
        self._issue_id = issue_id
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start: ask for confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete the stored message once confirmed."""
        if user_input is not None:
            entry = (
                self.hass.config_entries.async_get_entry(self._entry_id)
                if self._entry_id
                else None
            )
            device = getattr(entry, "runtime_data", None) if entry else None
            if device is None:
                return self.async_abort(reason="device_not_loaded")
            await device.async_clear_stored_message()
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=dict(
                (issue.translation_placeholders or {}) if issue else {}
            ),
        )


class OtherDeviceFlow(RepairsFlow):
    """Confirm, then adopt the device now on the topic (e.g. a replacement)."""

    def __init__(self, issue_id: str, entry_id: str | None, serial: str | None) -> None:
        """Initialize for one config entry and the new serial."""
        self._issue_id = issue_id
        self._entry_id = entry_id
        self._serial = serial

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Start: ask for confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Adopt the new serial once confirmed."""
        if user_input is not None:
            entry = (
                self.hass.config_entries.async_get_entry(self._entry_id)
                if self._entry_id
                else None
            )
            device = getattr(entry, "runtime_data", None) if entry else None
            if (
                entry is None
                or not self._serial
                or entry.state is not ConfigEntryState.LOADED
                or device is None
            ):
                return self.async_abort(reason="device_not_loaded")
            if other_entry_for_serial(self.hass, entry, self._serial) is not None:
                return self.async_abort(reason="already_configured")
            if device.own_device_may_be_active():
                # Adopting while the old device still sends would make it
                # the foreign one - or adopt a stray telegram.
                return self.async_abort(
                    reason="own_device_active",
                    description_placeholders={
                        "seconds": str(round(device.effective_timeout))
                    },
                )
            if not async_adopt_serial(self.hass, entry, self._serial):
                return self.async_abort(reason="already_configured")
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=dict(
                (issue.translation_placeholders or {}) if issue else {}
            ),
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a fixable issue."""
    data = data or {}
    if issue_id.startswith(f"{ISSUE_STALE_RETAINED}_"):
        return StaleRetainedFlow(issue_id, data.get("entry_id"))
    if issue_id.startswith(f"{ISSUE_OTHER_DEVICE}_"):
        return OtherDeviceFlow(issue_id, data.get("entry_id"), data.get("serial"))
    raise ValueError(f"Unknown fixable issue {issue_id}")
