"""Config and options flow tests."""
from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.novolto_mqtt.const import (
    CONF_BASE_TOPIC,
    CONF_ENABLE_BOARD_TEMPERATURE,
    CONF_MAX_POWER,
    CONF_SPTW_MAX,
    CONF_SPTW_MIN,
    CONF_SPTWH_MAX,
    CONF_SPTWH_MIN,
    CONF_TIMEOUT_SECONDS,
    CONF_TOPIC_CONTROL,
    CONF_TOPIC_INFO,
    DOMAIN,
)

from .conftest import BASE, eid, setup_entry

OPTION_KEYS = {
    "topic_info", "topic_control", "max_power", "power_step",
    "heating_threshold_w", "enable_board_temperature", "sptw_min",
    "sptw_max", "sptwh_min", "sptwh_max", "timeout_seconds",
}


async def _start_user_flow(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_step_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: f"  {BASE}  ", "name": "Boiler"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Boiler"
    assert result["data"] == {CONF_BASE_TOPIC: BASE}
    entry = result["result"]
    assert entry.unique_id == BASE
    assert entry.state is ConfigEntryState.LOADED


async def test_user_step_default_title(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: BASE}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Novolto {BASE}"


@pytest.mark.parametrize(
    "raw",
    [
        f"/{BASE}/",
        f"{BASE}/info",
        f"{BASE}/control",
        f" /{BASE}/info/ ",
        f"{BASE}/control/",
    ],
)
async def test_user_step_normalizes_base_topic(
    hass: HomeAssistant, mqtt_mock, raw: str
) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: raw}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_BASE_TOPIC: BASE}
    assert result["result"].unique_id == BASE


@pytest.mark.parametrize(
    "bad", ["", "   ", "/", "//", "#", "+", f"{BASE}/#", f"{BASE}/+", "AAA+BBB"]
)
async def test_user_step_invalid_base_topic(
    hass: HomeAssistant, mqtt_mock, bad: str
) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE_TOPIC: "invalid_base_topic"}
    # the flow recovers after the error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: BASE}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("raw", [BASE, f"{BASE}/info", f" {BASE}/ "])
async def test_user_step_duplicate_aborts(
    hass: HomeAssistant, mqtt_mock, raw: str
) -> None:
    await setup_entry(hass)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_TOPIC: raw}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_step_aborts_without_mqtt(hass: HomeAssistant) -> None:
    """No MQTT config entry -> abort instead of creating a dead entry."""
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_options_flow_saves_and_reloads(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    assert hass.states.get(number_id).attributes["max"] == 3000
    assert hass.states.get("sensor.novolto_test_board_temperature") is None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert {str(k) for k in result["data_schema"].schema} == OPTION_KEYS

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MAX_POWER: 2000,
            CONF_ENABLE_BOARD_TEMPERATURE: True,
            CONF_TIMEOUT_SECONDS: 300,
            CONF_TOPIC_INFO: "/tele/",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MAX_POWER] == 2000
    assert entry.options[CONF_ENABLE_BOARD_TEMPERATURE] is True
    assert entry.options[CONF_TOPIC_INFO] == "tele"  # slashes stripped
    assert entry.options[CONF_TOPIC_CONTROL] == "control"  # default filled in
    assert entry.options["power_step"] == 20

    # reloaded with the new options
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(number_id).attributes["max"] == 2000
    assert eid(hass, "sensor", "avt1")
    assert entry.runtime_data.timeout_seconds == 300
    subscribed = [c.args[0] for c in mqtt_mock.async_subscribe.call_args_list]
    assert f"{BASE}/tele" in subscribed

    # the form now shows the saved values as defaults
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults[CONF_MAX_POWER] == 2000
    assert defaults[CONF_ENABLE_BOARD_TEMPERATURE] is True
    assert defaults[CONF_TOPIC_INFO] == "tele"


@pytest.mark.parametrize(
    ("user_input", "expected_errors"),
    [
        ({CONF_TOPIC_INFO: "in+fo"}, {CONF_TOPIC_INFO: "invalid_topic"}),
        ({CONF_TOPIC_INFO: "#"}, {CONF_TOPIC_INFO: "invalid_topic"}),
        ({CONF_TOPIC_CONTROL: " / "}, {CONF_TOPIC_CONTROL: "invalid_topic"}),
        ({CONF_TOPIC_CONTROL: "info"}, {CONF_TOPIC_CONTROL: "same_topic"}),
        ({CONF_TOPIC_INFO: "x", CONF_TOPIC_CONTROL: "/x/"},
         {CONF_TOPIC_CONTROL: "same_topic"}),
        ({CONF_SPTW_MIN: 60, CONF_SPTW_MAX: 60}, {CONF_SPTW_MAX: "min_not_below_max"}),
        ({CONF_SPTW_MIN: 70, CONF_SPTW_MAX: 50}, {CONF_SPTW_MAX: "min_not_below_max"}),
        ({CONF_SPTWH_MIN: 5, CONF_SPTWH_MAX: 4}, {CONF_SPTWH_MAX: "min_not_below_max"}),
        (
            {CONF_TOPIC_INFO: "a#", CONF_SPTW_MIN: 90, CONF_SPTWH_MIN: 30,
             CONF_SPTWH_MAX: 30},
            {CONF_TOPIC_INFO: "invalid_topic", CONF_SPTW_MAX: "min_not_below_max",
             CONF_SPTWH_MAX: "min_not_below_max"},
        ),
    ],
)
async def test_options_flow_errors(
    hass: HomeAssistant, mqtt_mock, user_input, expected_errors
) -> None:
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected_errors
    assert entry.options == {}  # nothing saved

    # the re-shown form keeps what the user typed
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    for key, value in user_input.items():
        if key not in (CONF_TOPIC_INFO, CONF_TOPIC_CONTROL):
            assert defaults[key] == value

    # fixing the input saves
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_TOPIC_INFO: "info", CONF_TOPIC_CONTROL: "control",
         CONF_SPTW_MIN: 10, CONF_SPTW_MAX: 70, CONF_SPTWH_MIN: 1, CONF_SPTWH_MAX: 10},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SPTW_MAX] == 70


@pytest.mark.parametrize(
    "user_input",
    [
        {CONF_MAX_POWER: 50},
        {CONF_MAX_POWER: 20000},
        {"power_step": 0},
        {"heating_threshold_w": -1},
        {CONF_SPTW_MAX: 96},
        {CONF_SPTWH_MAX: 51},
        {CONF_TIMEOUT_SECONDS: 5},
        {CONF_TIMEOUT_SECONDS: 100000},
    ],
)
async def test_options_flow_rejects_out_of_range(
    hass: HomeAssistant, mqtt_mock, user_input
) -> None:
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert entry.options == {}
