"""Options flow: settings (step 1), high temperature warning, advanced (step 2)."""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.novolto_mqtt.const import ADVANCED_DEFAULTS, default_options

from .conftest import BASE, eid, setup_entry

STEP1 = {"power_stages": "0, 1000, 2000, 3000", "model": "p3000", "sptw_max": 50,
         "edit_advanced": False}
ADVANCED = {"power_step": 50, "heating_threshold_w": 20, "sptw_min": 10,
            "sptwh_min": 1, "sptwh_max": 8, "timeout_seconds": 300,
            "topic_info": "info", "topic_control": "control", "reset_defaults": False}


async def _init(hass: HomeAssistant, options: dict[str, Any] | None = None):
    entry = await setup_entry(hass, options)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    return entry, result


async def _configure(hass: HomeAssistant, result, user_input: dict[str, Any]):
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    await hass.async_block_till_done()
    return result


async def test_step1_fields_and_view_of_advanced(hass: HomeAssistant, mqtt_mock) -> None:
    _, result = await _init(hass)
    assert [str(k) for k in result["data_schema"].schema] == [
        "power_stages", "model", "sptw_max", "edit_advanced"
    ]
    defaults = result["data_schema"]({})
    assert defaults == {**STEP1, "edit_advanced": False}
    placeholders = result["description_placeholders"]
    assert placeholders["power_step"] == "20"
    assert placeholders["sptwh_max"] == "10"
    assert placeholders["topic_info"] == "info"


async def test_save_stages_reloads_with_select(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    select_id = eid(hass, "select", "power_stage")
    result = await _configure(hass, result, {**STEP1, "power_stages": "2000, 0,500, 500"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["power_stages"] == [0, 500, 2000]
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(select_id).attributes["options"] == ["off", "500 W", "2000 W", "custom"]


async def test_empty_stages_remove_select(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "power_stages": ""})
    assert entry.options["power_stages"] == []
    assert hass.states.get("select.novolto_test_power_stage") is None


@pytest.mark.parametrize(
    ("stages", "model", "error"),
    [
        ("0, 3500", "p3000", "stages_invalid"),
        ("0, 2500", "p2300", "stages_invalid"),
        ("0, x", "p3000", "stages_invalid"),
        ("1000, 2000", "p3000", "stages_no_zero"),
    ],
)
async def test_stage_errors(hass: HomeAssistant, mqtt_mock, stages, model, error) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "power_stages": stages, "model": model})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"power_stages": error}
    assert result["description_placeholders"]["max_power"] == (
        "2300" if model == "p2300" else "3000"
    )
    assert entry.options == default_options()
    # the form keeps what the user typed
    assert result["data_schema"]({})["power_stages"] == stages


async def test_model_change_takes_new_suggestion(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "model": "p2300"})
    # Step 1 again with the new model's stages, nothing saved yet.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init_model"
    assert result["description_placeholders"]["model"] == "P2300"
    shown = result["data_schema"]({})
    assert shown["power_stages"] == "0, 1000, 2000, 2300"
    assert shown["model"] == "p2300"
    assert entry.options["model"] == "p3000"
    result = await _configure(hass, result, shown)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["model"] == "p2300"
    assert entry.options["power_stages"] == [0, 1000, 2000, 2300]
    assert hass.states.get(eid(hass, "number", "target_power")).attributes["max"] == 2300


async def test_model_change_with_own_stages_validated(hass: HomeAssistant, mqtt_mock) -> None:
    _, result = await _init(hass)
    result = await _configure(
        hass, result, {**STEP1, "model": "p2300", "power_stages": "0, 1500, 3000"}
    )
    assert result["errors"] == {"power_stages": "stages_invalid"}


async def test_high_temperature_requires_confirmation(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "sptw_max": 65})
    assert result["step_id"] == "high_temperature"
    assert result["description_placeholders"] == {"temperature": "65"}
    result = await _configure(hass, result, {"sptw_max": 65, "confirm": False})
    assert result["errors"] == {"base": "confirm_required"}
    assert entry.options["sptw_max"] == 50  # unchanged without confirmation
    result = await _configure(hass, result, {"sptw_max": 65, "confirm": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["sptw_max"] == 65
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["max_temp"] == 65


async def test_high_temperature_lowered_instead(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "sptw_max": 70})
    result = await _configure(hass, result, {"sptw_max": 45, "confirm": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["sptw_max"] == 45


async def test_existing_high_value_not_asked_again(hass: HomeAssistant, mqtt_mock) -> None:
    """A value above 50 °C saved before (e.g. 80 from 0.5.0) isn't questioned."""
    entry, result = await _init(hass, {"sptw_max": 80})
    result = await _configure(hass, result, {**STEP1, "sptw_max": 80})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["sptw_max"] == 80


async def test_sptw_max_must_exceed_min(hass: HomeAssistant, mqtt_mock) -> None:
    _, result = await _init(hass, {"sptw_min": 40})
    result = await _configure(hass, result, {**STEP1, "sptw_max": 40})
    assert result["errors"] == {"sptw_max": "min_not_below_max"}


async def test_advanced_only_when_ticked(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "edit_advanced": True})
    assert result["step_id"] == "advanced"
    assert set(result["data_schema"]({})) == set(ADVANCED)
    result = await _configure(hass, result, ADVANCED)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["power_step"] == 50
    assert type(entry.options["power_step"]) is int
    assert entry.options["timeout_seconds"] == 300
    assert entry.options["sptwh_max"] == 8
    assert "reset_defaults" not in entry.options
    number = hass.states.get(eid(hass, "number", "water_hysteresis"))
    assert number.attributes["max"] == 8


async def test_advanced_after_high_temperature(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "sptw_max": 60, "edit_advanced": True})
    result = await _configure(hass, result, {"sptw_max": 60, "confirm": True})
    assert result["step_id"] == "advanced"
    result = await _configure(hass, result, ADVANCED)
    assert entry.options["sptw_max"] == 60
    assert entry.options["sptw_min"] == 10


async def test_advanced_reset_defaults(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass, {"power_step": 77, "topic_info": "tele"})
    result = await _configure(hass, result, {**STEP1, "edit_advanced": True})
    result = await _configure(hass, result, {**ADVANCED, "reset_defaults": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for key, value in ADVANCED_DEFAULTS.items():
        assert entry.options[key] == value


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        ({"topic_info": "in+fo"}, {"topic_info": "invalid_topic_suffix"}),
        ({"topic_info": "a/b"}, {"topic_info": "invalid_topic_suffix"}),
        ({"topic_control": " / "}, {"topic_control": "invalid_topic_suffix"}),
        ({"topic_control": "info"}, {"topic_control": "same_topic"}),
        ({"sptw_min": 50}, {"sptw_min": "min_not_below_max"}),
        ({"sptwh_min": 5, "sptwh_max": 4}, {"sptwh_max": "min_not_below_max"}),
    ],
)
async def test_advanced_errors(hass: HomeAssistant, mqtt_mock, change, errors) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "edit_advanced": True})
    result = await _configure(hass, result, {**ADVANCED, **change})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert entry.options == default_options()


@pytest.mark.parametrize(
    "change",
    [{"power_step": 0}, {"heating_threshold_w": -1}, {"sptwh_max": 51},
     {"timeout_seconds": 5}, {"timeout_seconds": 100000}],
)
async def test_advanced_out_of_range(hass: HomeAssistant, mqtt_mock, change) -> None:
    _, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "edit_advanced": True})
    with pytest.raises(InvalidData):
        await _configure(hass, result, {**ADVANCED, **change})


async def test_sptw_max_out_of_range(hass: HomeAssistant, mqtt_mock) -> None:
    _, result = await _init(hass)
    with pytest.raises(InvalidData):
        await _configure(hass, result, {**STEP1, "sptw_max": 96})


async def test_custom_topics_applied(hass: HomeAssistant, mqtt_mock) -> None:
    entry, result = await _init(hass)
    result = await _configure(hass, result, {**STEP1, "edit_advanced": True})
    await _configure(hass, result, {**ADVANCED, "topic_info": "/tele/"})
    assert entry.options["topic_info"] == "tele"
    subscribed = [c.args[0] for c in mqtt_mock.async_subscribe.call_args_list]
    assert f"{BASE}/tele" in subscribed
