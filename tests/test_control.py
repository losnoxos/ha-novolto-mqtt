"""Control-topic publishing tests (JSON types matter for the firmware)."""
from __future__ import annotations

import json

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .conftest import CONTROL_TOPIC, FULL_TELEGRAM, eid, fire, setup_entry


def _published(mqtt_mock) -> tuple[str, str, dict]:
    mqtt_mock.async_publish.assert_called_once()
    topic, payload, qos, retain = mqtt_mock.async_publish.call_args.args
    return topic, payload, json.loads(payload)


@pytest.mark.parametrize("value", [1500, 1500.0, 1499.6])
async def test_set_target_power_publishes_int(
    hass: HomeAssistant, mqtt_mock, value
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "target_power"), "value": value},
        blocking=True,
    )
    topic, raw, payload = _published(mqtt_mock)
    assert topic == CONTROL_TOPIC
    assert payload == {"sensor": [{"name": "spp", "value": 1500}]}
    assert type(payload["sensor"][0]["value"]) is int
    assert '"value": 1500}' in raw


async def test_set_target_power_zero(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "target_power"), "value": 0},
        blocking=True,
    )
    _, raw, payload = _published(mqtt_mock)
    assert payload["sensor"][0] == {"name": "spp", "value": 0}
    assert type(payload["sensor"][0]["value"]) is int


async def test_set_target_power_out_of_range(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": eid(hass, "number", "target_power"), "value": 3500},
            blocking=True,
        )
    mqtt_mock.async_publish.assert_not_called()


@pytest.mark.parametrize(("value", "expected_raw"), [(2, "2.0"), (3.5, "3.5")])
async def test_set_hysteresis_publishes_float(
    hass: HomeAssistant, mqtt_mock, value, expected_raw
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "water_hysteresis"), "value": value},
        blocking=True,
    )
    topic, raw, payload = _published(mqtt_mock)
    assert topic == CONTROL_TOPIC
    assert payload["sensor"][0]["name"] == "sptwh"
    assert type(payload["sensor"][0]["value"]) is float
    assert f'"value": {expected_raw}}}' in raw


@pytest.mark.parametrize(("value", "expected_raw"), [(45, "45.0"), (52.5, "52.5")])
async def test_water_heater_set_temperature_publishes_float(
    hass: HomeAssistant, mqtt_mock, value, expected_raw
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "water_heater", "set_temperature",
        {"entity_id": "water_heater.novolto_test", "temperature": value},
        blocking=True,
    )
    topic, raw, payload = _published(mqtt_mock)
    assert topic == CONTROL_TOPIC
    assert payload["sensor"][0]["name"] == "sptw"
    assert type(payload["sensor"][0]["value"]) is float
    assert f'"value": {expected_raw}}}' in raw
    # state only changes once the device confirms via the next telegram
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["temperature"] == 38.0
    fire(hass, {**FULL_TELEGRAM, "sptw": float(value)})
    await hass.async_block_till_done()
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["temperature"] == float(value)


@pytest.mark.parametrize("value", [95, 80.5, -10, -0.1])
async def test_water_heater_set_temperature_out_of_range(
    hass: HomeAssistant, mqtt_mock, value
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(
            "water_heater", "set_temperature",
            {"entity_id": "water_heater.novolto_test", "temperature": value},
            blocking=True,
        )
    assert exc.value.translation_key == "temperature_out_of_range"
    assert exc.value.translation_domain == "novolto_mqtt"
    assert "outside the allowed range" in str(exc.value)
    mqtt_mock.async_publish.assert_not_called()


@pytest.mark.parametrize("value", [0, 80])
async def test_water_heater_set_temperature_limits_inclusive(
    hass: HomeAssistant, mqtt_mock, value
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "water_heater", "set_temperature",
        {"entity_id": "water_heater.novolto_test", "temperature": value},
        blocking=True,
    )
    _, _, payload = _published(mqtt_mock)
    assert payload == {"sensor": [{"name": "sptw", "value": float(value)}]}


async def test_water_heater_range_from_options(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass, {"sptw_min": 30.0, "sptw_max": 60.0})
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    wh = hass.states.get("water_heater.novolto_test")
    assert (wh.attributes["min_temp"], wh.attributes["max_temp"]) == (30, 60)
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "water_heater", "set_temperature",
            {"entity_id": "water_heater.novolto_test", "temperature": 65},
            blocking=True,
        )
    mqtt_mock.async_publish.assert_not_called()


async def test_custom_control_topic(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass, {"topic_control": "cmd", "topic_info": "tele"})
    fire(hass, FULL_TELEGRAM, topic="TEST.SERIAL.000001/tele")
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"
    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "target_power"), "value": 100},
        blocking=True,
    )
    topic, _, _ = _published(mqtt_mock)
    assert topic == "TEST.SERIAL.000001/cmd"
