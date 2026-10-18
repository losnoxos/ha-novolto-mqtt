"""Diagnostics download for Novolto MQTT (serial number and topics redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_BASE_TOPIC, CONF_SERIAL, FIELD_SERIAL, MODELS
from .device import NovoltoConfigEntry

TO_REDACT = {CONF_BASE_TOPIC, CONF_SERIAL, FIELD_SERIAL, "unique_id", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NovoltoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device = entry.runtime_data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "version": f"{entry.version}.{entry.minor_version}",
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "model": device.model.name,
        "available": device.available,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "last_telegram_retained": device.last_telegram_retained,
        "effective_timeout": device.effective_timeout,
        "energy_kwh": device.energy_kwh,
        "telegram": async_redact_data(dict(device.data), TO_REDACT),
        "known_models": sorted(MODELS),
    }
