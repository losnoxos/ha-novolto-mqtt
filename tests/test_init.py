"""Runtime tests: setup, telegrams, acks, availability, unload."""
from __future__ import annotations

from datetime import timedelta
import logging
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.novolto_mqtt.const import CONF_MAX_POWER

from .conftest import (
    BASE,
    FULL_TELEGRAM,
    INFO_TOPIC,
    STORE_KEY,
    UNIX_TIME,
    eid,
    errors,
    fire,
    make_entry,
    setup_entry,
    unexpected_warnings,
)

# (domain, unique_id suffix) -> expected state after FULL_TELEGRAM
EXPECTED_STATES = {
    ("sensor", "avp"): "395",
    ("sensor", "avv"): "231",
    ("sensor", "avi"): "1.7",
    ("sensor", "avf"): "50.0",
    ("sensor", "avtw"): "23.8",
    ("sensor", "st"): "OK",
    ("sensor", "st_raw"): "0",
    ("sensor", "rod_st"): "1",
    ("sensor", "triacon"): "123",
    ("sensor", "r1on"): "1",
    ("sensor", "r2on"): "0",
    ("sensor", "energy"): "0.0",
    ("binary_sensor", "heating"): "on",
    ("binary_sensor", "rod_st"): "on",
    ("number", "target_power"): "400",
    ("number", "water_hysteresis"): "2.0",
    ("water_heater", "heating"): "electric",
}


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Advance frozen time and run due timers."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def novolto_states(hass: HomeAssistant):
    ent_reg = er.async_get(hass)
    return [
        hass.states.get(e.entity_id)
        for e in ent_reg.entities.values()
        if e.config_entry_id == "novolto_test_entry" and not e.disabled_by
    ]


async def test_setup_loads_all_platforms_without_warnings(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    subs = [c.args for c in mqtt_mock.async_subscribe.call_args_list
            if c.args[0].startswith(BASE)]
    assert len(subs) == 1
    assert subs[0][0] == INFO_TOPIC

    bad = unexpected_warnings(caplog)
    assert not bad, [f"{r.name}: {r.getMessage()}" for r in bad]

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.domain for e in entities} == {
        "sensor", "binary_sensor", "number", "water_heater"
    }
    # 14 sensors (2 disabled by default) + 2 binary sensors + 2 numbers + 1 water heater
    assert len(entities) == 19
    assert {e.unique_id for e in entities if e.disabled_by} == {
        f"{BASE}_rssi", f"{BASE}_msi"
    }
    for state in novolto_states(hass):
        assert state.state == STATE_UNAVAILABLE, state.entity_id


async def test_entity_naming(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    assert eid(hass, "water_heater", "heating") == "water_heater.novolto_test"
    assert hass.states.get("water_heater.novolto_test").attributes[
        "friendly_name"
    ] == "Novolto Test"
    assert eid(hass, "binary_sensor", "heating") == "binary_sensor.novolto_test_heating"
    assert eid(hass, "sensor", "avp") == "sensor.novolto_test_power"
    assert eid(hass, "sensor", "energy") == "sensor.novolto_test_energy"
    assert eid(hass, "number", "target_power") == "number.novolto_test_target_power"


async def test_setup_retries_when_mqtt_unavailable(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.components.mqtt.async_wait_for_mqtt_client", return_value=False
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not [c for c in mqtt_mock.async_subscribe.call_args_list
                if c.args[0].startswith(BASE)]
    # MQTT is back -> next attempt succeeds
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_full_telegram_sets_every_entity(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()

    for (domain, suffix), expected in EXPECTED_STATES.items():
        state = hass.states.get(eid(hass, domain, suffix))
        assert state.state == expected, (domain, suffix, state.state)

    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["current_temperature"] == 23.8
    assert wh.attributes["temperature"] == 38.0
    assert wh.attributes["min_temp"] == 0
    assert wh.attributes["max_temp"] == 80
    assert hass.states.get(eid(hass, "sensor", "st")).attributes["status_raw"] == 0
    assert not unexpected_warnings(caplog)


async def test_disabled_diagnostic_sensors_when_enabled(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await setup_entry(hass)
    ent_reg = er.async_get(hass)
    for suffix in ("rssi", "msi"):
        ent_reg.async_update_entity(eid(hass, "sensor", suffix), disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "rssi")).state == "-60"
    assert hass.states.get(eid(hass, "sensor", "msi")).state == "30"


async def test_board_temperature_opt_in(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass, {"enable_board_temperature": True})
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avt1")).state == "35"


async def test_heating_threshold_option(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass, {"heating_threshold_w": 500})
    fire(hass, FULL_TELEGRAM)  # 395 W
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "binary_sensor", "heating")).state == "off"
    fire(hass, {**FULL_TELEGRAM, "avp": 501})
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "binary_sensor", "heating")).state == "on"


async def test_ack_ok_does_not_blank_sensors(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    seen = entry.runtime_data.last_seen

    fire(hass, {"serial": BASE, "unix_time": UNIX_TIME + 1, "ret": 0})
    await hass.async_block_till_done()
    for (domain, suffix), expected in EXPECTED_STATES.items():
        assert hass.states.get(eid(hass, domain, suffix)).state == expected
    assert entry.runtime_data.last_seen == seen  # an ack is not a telegram
    assert not unexpected_warnings(caplog)


async def test_ack_error_logs_warning(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    fire(hass, {"serial": BASE, "unix_time": UNIX_TIME, "ret": 13,
                "s_err": "Module SENSOR: SPTW -> wrong type"})
    await hass.async_block_till_done()
    warns = [r for r in unexpected_warnings(caplog)
             if r.name.startswith("custom_components.novolto_mqtt")]
    assert len(warns) == 1
    assert "rejected a setting" in warns[0].getMessage()
    assert "SPTW -> wrong type" in warns[0].getMessage()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"

    caplog.clear()
    fire(hass, {"serial": BASE, "unix_time": UNIX_TIME, "ret": 13})  # no s_err
    await hass.async_block_till_done()
    assert any("rejected a setting" in r.getMessage() for r in caplog.records)


async def test_partial_telegram_keeps_other_values(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    fire(hass, {"serial": BASE, "unix_time": UNIX_TIME + 30, "avp": 2.7, "avtw": 24.1})
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "2.7"
    assert hass.states.get(eid(hass, "sensor", "avtw")).state == "24.1"
    assert hass.states.get(eid(hass, "sensor", "avv")).state == "231"
    assert hass.states.get(eid(hass, "binary_sensor", "heating")).state == "off"
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["current_temperature"] == 24.1


async def test_partial_first_telegram(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, {"serial": BASE, "avtw": 30.5})
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avtw")).state == "30.5"
    assert hass.states.get(eid(hass, "sensor", "avp")).state == STATE_UNKNOWN
    assert hass.states.get(eid(hass, "sensor", "st")).state == STATE_UNKNOWN
    assert hass.states.get(eid(hass, "binary_sensor", "heating")).state == STATE_UNKNOWN
    assert hass.states.get(eid(hass, "number", "target_power")).state == STATE_UNKNOWN
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.state == "electric"
    assert wh.attributes["temperature"] is None


@pytest.mark.parametrize(
    "payload",
    ["not json at all", "", "{broken", "[1, 2, 3]", "42", "null", '"string"',
     b"\xff\xfe\x00garbage"],
)
async def test_garbage_payloads_ignored(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture, payload
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    caplog.clear()
    fire(hass, payload)
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"
    assert not errors(caplog)


async def test_null_power(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    caplog.clear()
    fire(hass, {"serial": BASE, "avp": None, "avtw": 25.0})
    await hass.async_block_till_done()
    assert not errors(caplog)
    assert hass.states.get(eid(hass, "sensor", "avtw")).state == "25.0"
    assert hass.states.get(eid(hass, "sensor", "avp")).state == STATE_UNKNOWN
    assert hass.states.get(eid(hass, "binary_sensor", "heating")).state == STATE_UNKNOWN
    assert hass.states.get(eid(hass, "sensor", "avv")).state == "231"


@pytest.mark.parametrize(
    ("field", "value", "unique_suffix", "expected"),
    [
        ("avp", "395.5", "avp", "395.5"),  # numeric string -> number
        ("avp", "n/a", "avp", STATE_UNKNOWN),
        ("avp", True, "avp", STATE_UNKNOWN),  # bool is not a number
        ("avtw", "NaN", "avtw", STATE_UNKNOWN),
        ("avtw", "inf", "avtw", STATE_UNKNOWN),
        ("avv", {"x": 1}, "avv", STATE_UNKNOWN),
        ("rssi", "-60", "rssi_skip", None),
        ("st", "1", "st_raw", "1"),
        ("st", 1.0, "st_raw", "1"),
        ("st", 1.5, "st_raw", STATE_UNKNOWN),  # integer field, non-integral
        ("st", "abc", "st", STATE_UNKNOWN),
        ("spp", 400.0, "target_power", "400"),
        ("spp", 400.5, "target_power", STATE_UNKNOWN),
        ("triacon", False, "triacon", STATE_UNKNOWN),
        ("rod_st", "1", "rod_st", "1"),
    ],
)
async def test_non_numeric_and_string_values(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture,
    field, value, unique_suffix, expected,
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, field: value})
    await hass.async_block_till_done()
    assert not errors(caplog)
    if unique_suffix == "rssi_skip":  # entity disabled by default: check data
        assert entry.runtime_data.data["rssi"] == -60
        assert type(entry.runtime_data.data["rssi"]) is int
        return
    domain = "number" if unique_suffix == "target_power" else "sensor"
    assert hass.states.get(eid(hass, domain, unique_suffix)).state == expected


async def test_integer_fields_typed_as_int(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "spp": 400.0, "st": 1024.0, "msi": "30"})
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    for key in ("unix_time", "msi", "spp", "rssi", "st", "rod_st", "triacon",
                "r1on", "r2on"):
        assert type(data[key]) is int, key
    assert data["serial"] == BASE


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        (0, "OK"),
        (0x0001, "Sensor missing"),
        (0x0401, "Sensor missing, STB tripped"),
        (0x0480, "Hub disconnected, STB tripped"),
        (0x07FF, "Sensor missing, Water temp read fail, Meter reading mismatch, "
         "Fan RPM mismatch, Board temp exceeded, Board temp read fail, "
         "Meter read fail, Hub disconnected, Power freq mismatch, "
         "Missing settings, STB tripped"),
        (0x0800, "Unknown (0x0800)"),
        (0x0C01, "Sensor missing, STB tripped, Unknown (0x0800)"),
        (0xF000, "Unknown (0xF000)"),
    ],
)
async def test_status_bitflags(hass: HomeAssistant, mqtt_mock, raw, text) -> None:
    await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "st": raw})
    await hass.async_block_till_done()
    st = hass.states.get(eid(hass, "sensor", "st"))
    assert st.state == text
    assert st.attributes["status_raw"] == raw
    assert hass.states.get(eid(hass, "sensor", "st_raw")).state == str(raw)


async def test_availability_timeout_and_recovery(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    power_id = eid(hass, "sensor", "avp")

    await tick(hass, freezer, 90)
    assert hass.states.get(power_id).state == "395"
    await tick(hass, freezer, 60)  # 150 s > 120 s
    await tick(hass, freezer, 30)
    for state in novolto_states(hass):
        assert state.state == STATE_UNAVAILABLE, state.entity_id
    assert any("stopped reporting data" in r.getMessage() for r in caplog.records)

    fire(hass, {**FULL_TELEGRAM, "avp": 500})
    await hass.async_block_till_done()
    assert hass.states.get(power_id).state == "500"
    assert hass.states.get("water_heater.novolto_test").state == "electric"


async def test_availability_tick_dispatches_only_on_transition(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    calls: list[None] = []
    entry.async_on_unload(
        async_dispatcher_connect(hass, entry.runtime_data.signal, lambda: calls.append(None))
    )
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert len(calls) == 1
    for _ in range(3):  # 90 s, still available: no dispatch
        await tick(hass, freezer, 30)
    assert len(calls) == 1
    for _ in range(3):  # becomes unavailable: exactly one dispatch
        await tick(hass, freezer, 30)
    assert len(calls) == 2
    for _ in range(10):  # stays unavailable: no further dispatches
        await tick(hass, freezer, 30)
    assert len(calls) == 2


async def test_custom_timeout_option(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass, {"timeout_seconds": 600})
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    power_id = eid(hass, "sensor", "avp")
    for _ in range(10):  # 300 s
        await tick(hass, freezer, 30)
    assert hass.states.get(power_id).state == "395"
    for _ in range(12):  # 660 s
        await tick(hass, freezer, 30)
    assert hass.states.get(power_id).state == STATE_UNAVAILABLE


async def test_effective_timeout_follows_msi(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    """effective_timeout = max(timeout option, 3 * msi)."""
    entry = await setup_entry(hass)
    device = entry.runtime_data
    assert device.effective_timeout == 120
    fire(hass, {**FULL_TELEGRAM, "msi": 30})
    await hass.async_block_till_done()
    assert device.effective_timeout == 120
    fire(hass, {**FULL_TELEGRAM, "msi": 100})
    await hass.async_block_till_done()
    assert device.effective_timeout == 300

    power_id = eid(hass, "sensor", "avp")
    for _ in range(9):  # 270 s
        await tick(hass, freezer, 30)
    assert hass.states.get(power_id).state == "395"
    for _ in range(2):  # 330 s
        await tick(hass, freezer, 30)
    assert hass.states.get(power_id).state == STATE_UNAVAILABLE


async def test_reload_is_clean(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert not errors(caplog)
    # exactly one live subscription after reload: one message -> one update
    fire(hass, {**FULL_TELEGRAM, "avp": 777})
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "777"


async def test_options_change_applies(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MAX_POWER: 2000}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(number_id).attributes["max"] == 2000


async def test_unload_entry_cleanly(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1200})
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    fire(hass, {**FULL_TELEGRAM, "avp": 1200})
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not errors(caplog)
    # explicit save on unload, without waiting for the delayed write
    assert hass_storage[STORE_KEY]["data"]["energy_kwh"] == pytest.approx(
        1200 / 1000 * 30 / 3600, abs=1e-9
    )
    for state in novolto_states(hass):
        assert state.state == STATE_UNAVAILABLE

    # nothing is processed after unload, timers are gone
    fire(hass, {**FULL_TELEGRAM, "avp": 9999})
    await tick(hass, freezer, 600)
    assert not errors(caplog)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_remove_entry_deletes_store(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert STORE_KEY in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert STORE_KEY not in hass_storage
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
