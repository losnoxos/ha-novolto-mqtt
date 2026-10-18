"""Repair flows for Novolto MQTT."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_STALE_RETAINED


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


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a fixable issue."""
    if issue_id.startswith(f"{ISSUE_STALE_RETAINED}_"):
        return StaleRetainedFlow(issue_id, (data or {}).get("entry_id"))
    raise ValueError(f"Unknown fixable issue {issue_id}")
