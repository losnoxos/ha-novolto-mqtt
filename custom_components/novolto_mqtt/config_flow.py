"""Config flow for the Novolto MQTT integration.

Ways to add a heating rod:
1. Discovery: Home Assistant offers "New device found" for every Novolto
   telegram on `+/info` (single-level topics, i.e. the default serial topic).
2. "Add integration": search for 35 s on all topics, recognise telegrams by
   content, pick from a list.
3. Enter the topic manually; the flow listens whether data arrives and offers
   correct / search again / set up anyway if not.
All ways end with name + model.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from .connection import decode_payload
from .const import (
    ADVANCED_DEFAULTS,
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_HEATING_THRESHOLD_W,
    CONF_MODEL,
    CONF_POWER_STAGES,
    CONF_POWER_STEP,
    CONF_SERIAL,
    CONF_SPTW_MAX,
    CONF_SPTW_MIN,
    CONF_SPTWH_MAX,
    CONF_SPTWH_MIN,
    CONF_TIMEOUT_SECONDS,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    CONNECTION_MQTT,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOPIC_INFO,
    DOMAIN,
    FIELD_MSI,
    FIELD_SERIAL,
    FIELD_WATER_TEMP,
    LIMIT_SPTW,
    LIMIT_SPTWH,
    LISTEN_SECONDS,
    MODELS,
    MSI_TIMEOUT_FACTOR,
    SAFE_SPTW_MAX,
    URL_MOSQUITTO_ADDON,
    URL_MQTT_SETUP,
    URL_README,
    URL_README_DE,
    default_options,
)
from .device import as_number, is_novolto_telegram, telegram_age

_LOGGER = logging.getLogger(__name__)

CONF_DEVICE = "device"
CONF_TOPIC = "topic"
CONF_CONFIRM = "confirm"
CONF_EDIT_ADVANCED = "edit_advanced"
CONF_RESET_DEFAULTS = "reset_defaults"
_MANUAL = "__manual__"
_INFO_SUFFIX = f"/{DEFAULT_TOPIC_INFO}"
# Suffixes users paste by accident (from MQTT Explorer etc.).
_TOPIC_SUFFIXES = ("/#", "/+", "/info", "/control")


# --- helpers ---------------------------------------------------------------


def _is_de(hass: HomeAssistant) -> bool:
    return (hass.config.language or "").lower().startswith("de")


def _format_age(seconds: float | None) -> str:
    """Compact, language-neutral age: '12 s', '5 min', '3 h'."""
    if seconds is None:
        return "?"
    if seconds < 120:
        return f"{int(seconds)} s"
    if seconds < 7200:
        return f"{int(seconds // 60)} min"
    return f"{int(seconds // 3600)} h"


def _readme_url(hass: HomeAssistant) -> str:
    return URL_README_DE if _is_de(hass) else URL_README


def normalize_topic(raw: str) -> str:
    """Strip whitespace, slashes and accidentally pasted suffixes."""
    topic = raw.strip().strip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _TOPIC_SUFFIXES:
            if topic.endswith(suffix):
                topic = topic.removesuffix(suffix).rstrip("/")
                changed = True
    return topic


def is_valid_topic(topic: str) -> bool:
    """Wildcard-free topic without spaces or URL scheme."""
    if not topic or any(c.isspace() for c in topic) or "://" in topic:
        return False
    if "+" in topic or "#" in topic:
        return False
    try:
        mqtt.valid_publish_topic(topic)  # NUL, too long, ...
    except vol.Invalid:
        return False
    return True


def is_valid_topic_suffix(suffix: str) -> bool:
    """Info/control topic name: one level, no wildcards."""
    return is_valid_topic(suffix) and "/" not in suffix


def parse_power_stages(raw: str, max_power: int) -> list[int] | str:
    """Parse "0, 1000, 2000" into a sorted list, or return an error key."""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    stages: set[int] = set()
    for part in parts:
        try:
            value = int(part)
        except ValueError:
            return "stages_invalid"
        if not 0 <= value <= max_power:
            return "stages_invalid"
        stages.add(value)
    if stages and 0 not in stages:
        return "stages_no_zero"
    return sorted(stages)


def _stages_text(stages: list[int]) -> str:
    return ", ".join(str(s) for s in stages)


def _mqtt_problem(hass: HomeAssistant) -> str | None:
    """Abort reason if MQTT isn't usable, else None."""
    if not any(
        not entry.disabled_by for entry in hass.config_entries.async_entries("mqtt")
    ):
        return "mqtt_not_configured"
    try:
        connected = mqtt.is_connected(hass)
    except (KeyError, AttributeError):
        connected = False
    return None if connected else "mqtt_not_connected"


def _model_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(MODELS),
            translation_key=CONF_MODEL,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


@dataclass
class FoundDevice:
    """A Novolto telegram seen during discovery/search."""

    serial: str
    base_topic: str
    water_temp: float | None
    age: float | None
    msi: int | None

    @property
    def stale(self) -> bool:
        """Telegram older than the availability timeout (device off?)."""
        timeout = max(DEFAULT_TIMEOUT_SECONDS, MSI_TIMEOUT_FACTOR * (self.msi or 0))
        return self.age is not None and self.age > timeout


def found_from_message(topic: str, payload: Any) -> FoundDevice | None:
    """FoundDevice for a Novolto telegram on '<base>/info', else None."""
    if not topic.endswith(_INFO_SUFFIX) or not is_novolto_telegram(payload):
        return None
    base_topic = topic.removesuffix(_INFO_SUFFIX)
    if not base_topic:
        return None
    return FoundDevice(
        serial=payload[FIELD_SERIAL],
        base_topic=base_topic,
        water_temp=as_number(payload.get(FIELD_WATER_TEMP), False),
        age=telegram_age(payload),
        msi=as_number(payload.get(FIELD_MSI), True),
    )


async def async_listen(
    hass: HomeAssistant,
    seconds: float,
    stop_when: Callable[[FoundDevice], bool] | None = None,
) -> dict[str, FoundDevice]:
    """Listen on all topics for Novolto telegrams, keyed by base topic."""
    found: dict[str, FoundDevice] = {}
    done = asyncio.Event()

    @callback
    def _message(msg: mqtt.ReceiveMessage) -> None:
        if not msg.topic.endswith(_INFO_SUFFIX):
            return
        device = found_from_message(msg.topic, decode_payload(msg.payload))
        if device is None:
            return
        found[device.base_topic] = device
        if stop_when is not None and stop_when(device):
            done.set()

    # encoding=None: "#" also carries binary payloads (camera snapshots ...),
    # which would otherwise log a decode warning each.
    unsubscribe = await mqtt.async_subscribe(hass, "#", _message, encoding=None)
    try:
        async with asyncio.timeout(seconds):
            await done.wait()
    except TimeoutError:
        pass
    finally:
        unsubscribe()
    return found


# --- config flow -----------------------------------------------------------


class NovoltoMqttConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Novolto MQTT."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._found: dict[str, FoundDevice] = {}
        self._choice: FoundDevice | None = None
        self._topic: str | None = None
        self._case_hint: str | None = None
        self._none_found = False
        self._task: asyncio.Task | None = None

    @callback
    def async_remove(self) -> None:
        """Stop listening when the flow is closed."""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # --- shared ---

    def _abort_placeholders(self) -> dict[str, str]:
        return {
            "addon_url": URL_MOSQUITTO_ADDON,
            "mqtt_url": URL_MQTT_SETUP,
            "readme_url": _readme_url(self.hass),
        }

    def _abort_mqtt(self) -> ConfigFlowResult | None:
        if (reason := _mqtt_problem(self.hass)) is not None:
            return self.async_abort(
                reason=reason, description_placeholders=self._abort_placeholders()
            )
        return None

    def _configured_topics(self) -> set[str]:
        return {
            entry.data.get(CONF_BASE_TOPIC)
            for entry in self._async_current_entries(include_ignore=False)
        }

    def _configured_serials(self) -> set[str]:
        ids = set()
        for entry in self._async_current_entries(include_ignore=False):
            ids.add(entry.unique_id)
            ids.add(entry.data.get(CONF_SERIAL))
        ids.discard(None)
        return ids

    def _found_label(self, device: FoundDevice) -> str:
        label = device.serial
        if device.base_topic != device.serial:
            label += f" ({device.base_topic})"
        if device.water_temp is not None:
            label += f" – {device.water_temp:g} °C"
        age = _format_age(device.age)
        label += f" – vor {age}" if _is_de(self.hass) else f" – {age} ago"
        return label

    async def _async_progress(
        self,
        step_id: str,
        action: str,
        start: Callable[[], Any],
        next_step: str,
    ) -> ConfigFlowResult:
        """Run `start()` as a progress task, then continue with next_step.

        step_id is passed explicitly: these steps are also entered from other
        steps, whose id would otherwise be recorded.
        """
        if self._task is None:
            self._task = self.hass.async_create_task(start())
        if not self._task.done():
            return self.async_show_progress(
                step_id=step_id, progress_action=action, progress_task=self._task
            )
        return self.async_show_progress_done(next_step_id=next_step)

    def _take_result(self) -> Any:
        """Result of the finished progress task; MQTT errors -> None."""
        task, self._task = self._task, None
        if task is None:
            return None
        try:
            return task.result()
        except HomeAssistantError as err:
            _LOGGER.warning("Listening for Novolto telegrams failed: %s", err)
            return None

    # --- way 1: MQTT discovery ---

    async def async_step_mqtt(
        self, discovery_info: MqttServiceInfo
    ) -> ConfigFlowResult:
        """A telegram arrived on '+/info'."""
        device = found_from_message(
            discovery_info.topic, decode_payload(discovery_info.payload)
        )
        if device is None:
            return self.async_abort(reason="not_novolto_device")
        await self.async_set_unique_id(device.serial)
        self._abort_if_unique_id_configured()
        if device.base_topic in self._configured_topics():
            return self.async_abort(reason="already_configured")
        self._choice = device
        self.context["title_placeholders"] = {"serial": device.serial}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered device: name and model."""
        assert self._choice is not None
        step_id = (
            "discovery_confirm_offline" if self._choice.stale else "discovery_confirm"
        )
        return await self._async_name_model(
            step_id,
            user_input,
            {
                "serial": self._choice.serial,
                "water_temp": (
                    f"{self._choice.water_temp:g}"
                    if self._choice.water_temp is not None
                    else "?"
                ),
                "age": _format_age(self._choice.age),
            },
        )

    async_step_discovery_confirm_offline = async_step_discovery_confirm

    # --- way 2: search ---

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Started by the user: check MQTT, then search."""
        if (abort := self._abort_mqtt()) is not None:
            return abort
        return await self.async_step_search()

    async def async_step_search(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen on all topics for Novolto telegrams."""
        if self._task is None and (abort := self._abort_mqtt()) is not None:
            return abort
        return await self._async_progress(
            "search",
            "search",
            lambda: async_listen(self.hass, LISTEN_SECONDS),
            "search_done",
        )

    async def async_step_search_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Evaluate the search."""
        found = self._take_result()
        if found is None and (abort := self._abort_mqtt()) is not None:
            return abort
        found = found or {}
        configured = self._configured_serials()
        topics = self._configured_topics()
        self._found = {
            d.serial: d
            for d in found.values()
            if d.serial not in configured and d.base_topic not in topics
        }
        if not self._found:
            self._none_found = True
            return await self.async_step_manual()
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a found device (or enter the topic manually)."""
        if user_input is not None:
            if user_input[CONF_DEVICE] == _MANUAL:
                return await self.async_step_manual()
            self._choice = self._found[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(self._choice.serial, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_device()

        options = [
            SelectOptionDict(value=serial, label=self._found_label(device))
            for serial, device in self._found.items()
        ]
        options.append(
            SelectOptionDict(
                value=_MANUAL,
                label="Topic selbst eingeben …"
                if _is_de(self.hass)
                else "Enter topic manually …",
            )
        )
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE, default=options[0]["value"]): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            description_placeholders={
                "count": str(len(self._found)),
                "readme_url": _readme_url(self.hass),
            },
        )

    # --- way 3: manual topic ---

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the device's MQTT topic."""
        errors: dict[str, str] = {}
        if self._none_found:
            self._none_found = False
            errors["base"] = "none_found"
        if user_input is not None:
            topic = normalize_topic(user_input[CONF_TOPIC])
            if not is_valid_topic(topic):
                errors[CONF_TOPIC] = "invalid_topic"
            elif topic in self._configured_topics():
                return self.async_abort(reason="already_configured")
            else:
                self._topic = topic
                return await self.async_step_listen()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_TOPIC, default=self._topic or ""): str}
            ),
            errors=errors,
        )

    async def async_step_listen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen whether the entered topic carries data."""
        topic = self._topic
        assert topic is not None
        return await self._async_progress(
            "listen",
            "listen",
            lambda: async_listen(
                self.hass, LISTEN_SECONDS, lambda d: d.base_topic == topic
            ),
            "listen_done",
        )

    async def async_step_listen_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Data arrived -> name/model; else the 'no data' menu."""
        found = self._take_result()
        if found is None and (abort := self._abort_mqtt()) is not None:
            return abort
        found = found or {}
        topic = self._topic
        assert topic is not None
        if (device := found.get(topic)) is not None:
            if device.serial in self._configured_serials():
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(device.serial, raise_on_progress=False)
            self._choice = device
            return await self.async_step_device()

        self._case_hint = next(
            (
                d.base_topic
                for d in found.values()
                if d.base_topic.lower() == topic.lower()
            ),
            None,
        )
        return await self.async_step_no_data()

    async def async_step_no_data(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing received: correct / search again / set up anyway."""
        placeholders = {"topic": self._topic or ""}
        if self._case_hint:
            placeholders["found"] = self._case_hint
        return self.async_show_menu(
            step_id="no_data_case" if self._case_hint else "no_data",
            menu_options=["manual", "search", "anyway"],
            description_placeholders=placeholders,
        )

    async_step_no_data_case = async_step_no_data

    async def async_step_anyway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up without data: entities stay unavailable until it sends."""
        if user_input is not None:
            topic = self._topic
            assert topic is not None
            await self.async_set_unique_id(topic, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._choice = None
            return await self.async_step_device()
        return self.async_show_form(step_id="anyway", data_schema=vol.Schema({}))

    # --- name and model (all ways) ---

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name and model."""
        return await self._async_name_model("device", user_input, {})

    async def _async_name_model(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        placeholders: dict[str, str],
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            model = user_input[CONF_MODEL]
            name = user_input[CONF_NAME].strip()
            # Default name of the other model left unchanged -> follow model.
            if name in {f"Novolto {m.name}" for m in MODELS.values()}:
                name = f"Novolto {MODELS[model].name}"
            taken = {
                entry.title.casefold()
                for entry in self._async_current_entries(include_ignore=False)
            }
            if not name:
                errors[CONF_NAME] = "name_required"
            elif name.casefold() in taken:
                errors[CONF_NAME] = "name_taken"
            else:
                return self._async_create(name, model)

        default_model = (user_input or {}).get(CONF_MODEL, DEFAULT_MODEL)
        default_name = (user_input or {}).get(
            CONF_NAME, f"Novolto {MODELS[default_model].name}"
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=default_name): str,
                    vol.Required(CONF_MODEL, default=default_model): _model_selector(),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    @callback
    def _async_create(self, name: str, model: str) -> ConfigFlowResult:
        if self._choice is not None:
            data: dict[str, Any] = {
                CONF_CONNECTION: CONNECTION_MQTT,
                CONF_BASE_TOPIC: self._choice.base_topic,
                CONF_SERIAL: self._choice.serial,
            }
        else:
            data = {
                CONF_CONNECTION: CONNECTION_MQTT,
                CONF_BASE_TOPIC: self._topic,
                CONF_SERIAL: None,
                CONF_AWAITING_DATA: True,
            }
        return self.async_create_entry(
            title=name, data=data, options=default_options(model)
        )

    # --- reconfigure: change the topic ---

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device's MQTT topic; device, entities, energy stay."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            topic = normalize_topic(user_input[CONF_TOPIC])
            other_topics = {
                e.data.get(CONF_BASE_TOPIC)
                for e in self._async_current_entries(include_ignore=False)
                if e.entry_id != entry.entry_id
            }
            if not is_valid_topic(topic):
                errors[CONF_TOPIC] = "invalid_topic"
            elif topic in other_topics:
                errors[CONF_TOPIC] = "topic_in_use"
            elif (abort := self._abort_mqtt()) is not None:
                return abort
            else:
                self._topic = topic
                return await self.async_step_reconfigure_listen()
        elif self._case_hint is not None:
            # Back from listening: data of another device under this topic.
            errors[CONF_TOPIC] = "other_device"
            placeholders["serial"] = self._case_hint
            self._case_hint = None

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TOPIC, default=self._topic or entry.data[CONF_BASE_TOPIC]
                    ): str
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure_listen(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check which device sends under the new topic."""
        topic = self._topic
        assert topic is not None
        return await self._async_progress(
            "reconfigure_listen",
            "listen",
            lambda: async_listen(
                self.hass, LISTEN_SECONDS, lambda d: d.base_topic == topic
            ),
            "reconfigure_done",
        )

    async def async_step_reconfigure_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the topic unless another device sends there."""
        entry = self._get_reconfigure_entry()
        found: dict[str, FoundDevice] = self._take_result() or {}
        if (abort := self._abort_mqtt()) is not None:
            return abort
        device = found.get(self._topic or "")
        serial = entry.data.get(CONF_SERIAL)
        if device is not None and serial and device.serial != serial:
            self._case_hint = device.serial
            return await self.async_step_reconfigure()
        return self.async_update_reload_and_abort(
            entry, data_updates={CONF_BASE_TOPIC: self._topic}
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return NovoltoMqttOptionsFlow()


# --- options flow ----------------------------------------------------------


class NovoltoMqttOptionsFlow(OptionsFlow):
    """Settings (step 1) and, on request, advanced settings (step 2).

    Deliberately no custom __init__: `self.config_entry` is provided by the
    base class once the flow is initialized.
    """

    def __init__(self) -> None:
        """Initialize."""
        self._new: dict[str, Any] = {}
        self._edit_advanced = False

    @property
    def _options(self) -> dict[str, Any]:
        return {**default_options(), **self.config_entry.options, **self._new}

    def _advanced_placeholders(self) -> dict[str, str]:
        o = self._options
        return {
            "power_step": f"{o[CONF_POWER_STEP]:g}",
            "threshold": f"{o[CONF_HEATING_THRESHOLD_W]:g}",
            "sptw_min": f"{o[CONF_SPTW_MIN]:g}",
            "sptwh_min": f"{o[CONF_SPTWH_MIN]:g}",
            "sptwh_max": f"{o[CONF_SPTWH_MAX]:g}",
            "timeout": f"{o[CONF_TIMEOUT_SECONDS]:g}",
            "topic_info": str(o[CONF_TOPIC_INFO]),
            "topic_control": str(o[CONF_TOPIC_CONTROL]),
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: power stages, model, maximum target temperature."""
        current = self._options
        errors: dict[str, str] = {}
        model = (user_input or {}).get(CONF_MODEL, current[CONF_MODEL])
        if model not in MODELS:
            model = DEFAULT_MODEL
        placeholders = {
            **self._advanced_placeholders(),
            "max_power": str(MODELS[model].max_power),
        }

        if user_input is not None:
            stages_raw = user_input.get(CONF_POWER_STAGES, "")
            old_model = current[CONF_MODEL]
            # Model changed and stages untouched from the old model's
            # suggestion -> take the new model's suggestion.
            if (
                model != old_model
                and old_model in MODELS
                and parse_power_stages(stages_raw, 10**6)
                == list(MODELS[old_model].power_stages)
            ):
                stages_raw = _stages_text(list(MODELS[model].power_stages))
            stages = parse_power_stages(stages_raw, MODELS[model].max_power)
            if isinstance(stages, str):
                errors[CONF_POWER_STAGES] = stages
            sptw_max = float(user_input[CONF_SPTW_MAX])
            if sptw_max <= current[CONF_SPTW_MIN]:
                errors[CONF_SPTW_MAX] = "min_not_below_max"
            if not errors:
                self._new = {
                    CONF_MODEL: model,
                    CONF_POWER_STAGES: stages,
                    CONF_SPTW_MAX: sptw_max,
                }
                self._edit_advanced = bool(user_input.get(CONF_EDIT_ADVANCED))
                if (
                    sptw_max > SAFE_SPTW_MAX
                    and sptw_max != self.config_entry.options.get(CONF_SPTW_MAX)
                ):
                    return await self.async_step_high_temperature()
                return await self._async_next()

        stages_default = (user_input or {}).get(
            CONF_POWER_STAGES, _stages_text(current[CONF_POWER_STAGES])
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POWER_STAGES, default=stages_default): TextSelector(),
                    vol.Required(CONF_MODEL, default=model): _model_selector(),
                    vol.Required(
                        CONF_SPTW_MAX,
                        default=(user_input or {}).get(
                            CONF_SPTW_MAX, current[CONF_SPTW_MAX]
                        ),
                    ): _temperature_selector(LIMIT_SPTW),
                    vol.Required(CONF_EDIT_ADVANCED, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_high_temperature(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a maximum target temperature above 50 °C."""
        errors: dict[str, str] = {}
        temperature = self._new[CONF_SPTW_MAX]
        if user_input is not None:
            temperature = float(user_input[CONF_SPTW_MAX])
            if temperature <= self._options[CONF_SPTW_MIN]:
                errors[CONF_SPTW_MAX] = "min_not_below_max"
            elif temperature <= SAFE_SPTW_MAX or user_input.get(CONF_CONFIRM):
                self._new[CONF_SPTW_MAX] = temperature
                return await self._async_next()
            else:
                errors["base"] = "confirm_required"

        return self.async_show_form(
            step_id="high_temperature",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SPTW_MAX, default=temperature): _temperature_selector(
                        LIMIT_SPTW
                    ),
                    vol.Required(CONF_CONFIRM, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"temperature": f"{temperature:g}"},
        )

    async def _async_next(self) -> ConfigFlowResult:
        if self._edit_advanced:
            return await self.async_step_advanced()
        return self.async_create_entry(data=self._options)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: technical settings, behind a warning."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_RESET_DEFAULTS):
                return self.async_create_entry(
                    data={**self._options, **ADVANCED_DEFAULTS}
                )
            values = {k: v for k, v in user_input.items() if k != CONF_RESET_DEFAULTS}
            for key in (CONF_TOPIC_INFO, CONF_TOPIC_CONTROL):
                values[key] = values[key].strip().strip("/")
                if not is_valid_topic_suffix(values[key]):
                    errors[key] = "invalid_topic_suffix"
            if not errors and values[CONF_TOPIC_INFO] == values[CONF_TOPIC_CONTROL]:
                errors[CONF_TOPIC_CONTROL] = "same_topic"
            if values[CONF_SPTW_MIN] >= self._options[CONF_SPTW_MAX]:
                errors[CONF_SPTW_MIN] = "min_not_below_max"
            if values[CONF_SPTWH_MIN] >= values[CONF_SPTWH_MAX]:
                errors[CONF_SPTWH_MAX] = "min_not_below_max"
            if not errors:
                for key in (CONF_POWER_STEP, CONF_TIMEOUT_SECONDS):
                    values[key] = int(values[key])
                return self.async_create_entry(data={**self._options, **values})

        current = {**self._options, **(user_input or {})}

        def _number(lo: float, hi: float, step: float, unit: str) -> NumberSelector:
            return NumberSelector(
                NumberSelectorConfig(
                    min=lo,
                    max=hi,
                    step=step,
                    unit_of_measurement=unit,
                    mode=NumberSelectorMode.BOX,
                )
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POWER_STEP, default=current[CONF_POWER_STEP]
                ): _number(1, 1000, 1, "W"),
                vol.Required(
                    CONF_HEATING_THRESHOLD_W, default=current[CONF_HEATING_THRESHOLD_W]
                ): _number(0, 1000, 1, "W"),
                vol.Required(
                    CONF_SPTW_MIN, default=current[CONF_SPTW_MIN]
                ): _number(0, LIMIT_SPTW, 1, "°C"),
                vol.Required(
                    CONF_SPTWH_MIN, default=current[CONF_SPTWH_MIN]
                ): _number(0, LIMIT_SPTWH, 0.5, "°C"),
                vol.Required(
                    CONF_SPTWH_MAX, default=current[CONF_SPTWH_MAX]
                ): _number(0, LIMIT_SPTWH, 0.5, "°C"),
                vol.Required(
                    CONF_TIMEOUT_SECONDS, default=current[CONF_TIMEOUT_SECONDS]
                ): _number(10, 86400, 1, "s"),
                vol.Required(CONF_TOPIC_INFO, default=current[CONF_TOPIC_INFO]): str,
                vol.Required(
                    CONF_TOPIC_CONTROL, default=current[CONF_TOPIC_CONTROL]
                ): str,
                vol.Required(CONF_RESET_DEFAULTS, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="advanced", data_schema=schema, errors=errors
        )


def _temperature_selector(maximum: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=1,
            max=maximum,
            step=1,
            unit_of_measurement="°C",
            mode=NumberSelectorMode.BOX,
        )
    )

