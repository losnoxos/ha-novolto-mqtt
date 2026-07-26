"""Config flow for the Novolto MQTT integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BASE_TOPIC,
    CONF_ENABLE_BOARD_TEMPERATURE,
    CONF_HEATING_THRESHOLD_W,
    CONF_MAX_POWER,
    CONF_POWER_STEP,
    CONF_SPTW_MAX,
    CONF_SPTW_MIN,
    CONF_SPTWH_MAX,
    CONF_SPTWH_MIN,
    CONF_TIMEOUT_SECONDS,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    DEFAULT_ENABLE_BOARD_TEMPERATURE,
    DEFAULT_HEATING_THRESHOLD_W,
    DEFAULT_MAX_POWER,
    DEFAULT_POWER_STEP,
    DEFAULT_SPTW_MAX,
    DEFAULT_SPTW_MIN,
    DEFAULT_SPTWH_MAX,
    DEFAULT_SPTWH_MIN,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_CONTROL,
    DEFAULT_TOPIC_INFO,
    DOMAIN,
)

# base_topic is the Novolto's serial number, e.g. "AAA.BBB.123456" - it is
# also the MQTT topic prefix, so it doubles as this integration's unique_id.
# That's what makes multiple Novolto devices in one HA instance work: each
# gets its own config entry, keyed by its own serial.
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_TOPIC): str,
        vol.Optional(CONF_NAME): str,
    }
)


class NovoltoMqttConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Novolto MQTT."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step: ask for the device's base_topic (serial)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_topic = user_input[CONF_BASE_TOPIC].strip()

            if not base_topic:
                errors[CONF_BASE_TOPIC] = "invalid_base_topic"
            else:
                await self.async_set_unique_id(base_topic)
                self._abort_if_unique_id_configured()

                title = user_input.get(CONF_NAME) or f"Novolto {base_topic}"
                return self.async_create_entry(
                    title=title,
                    data={CONF_BASE_TOPIC: base_topic},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return NovoltoMqttOptionsFlow()


class NovoltoMqttOptionsFlow(OptionsFlow):
    """Handle options for an existing Novolto MQTT device.

    Deliberately no custom __init__: `self.config_entry` is provided as a
    read-only property by the base class and is only available once the
    flow has been initialized (not inside __init__) - see
    home-assistant/core config_entries.py, OptionsFlow.config_entry.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TOPIC_INFO,
                    default=options.get(CONF_TOPIC_INFO, DEFAULT_TOPIC_INFO),
                ): str,
                vol.Optional(
                    CONF_TOPIC_CONTROL,
                    default=options.get(CONF_TOPIC_CONTROL, DEFAULT_TOPIC_CONTROL),
                ): str,
                vol.Optional(
                    CONF_MAX_POWER,
                    default=options.get(CONF_MAX_POWER, DEFAULT_MAX_POWER),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_POWER_STEP,
                    default=options.get(CONF_POWER_STEP, DEFAULT_POWER_STEP),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_HEATING_THRESHOLD_W,
                    default=options.get(
                        CONF_HEATING_THRESHOLD_W, DEFAULT_HEATING_THRESHOLD_W
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_ENABLE_BOARD_TEMPERATURE,
                    default=options.get(
                        CONF_ENABLE_BOARD_TEMPERATURE,
                        DEFAULT_ENABLE_BOARD_TEMPERATURE,
                    ),
                ): bool,
                vol.Optional(
                    CONF_SPTW_MIN,
                    default=options.get(CONF_SPTW_MIN, DEFAULT_SPTW_MIN),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_SPTW_MAX,
                    default=options.get(CONF_SPTW_MAX, DEFAULT_SPTW_MAX),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_SPTWH_MIN,
                    default=options.get(CONF_SPTWH_MIN, DEFAULT_SPTWH_MIN),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_SPTWH_MAX,
                    default=options.get(CONF_SPTWH_MAX, DEFAULT_SPTWH_MAX),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_TIMEOUT_SECONDS,
                    default=options.get(
                        CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS
                    ),
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
