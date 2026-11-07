"""Paket C: serial check, foreign devices on the topic, retained messages on
info and control, device restarts.

All telegrams and acks are written by hand (FULL_TELEGRAM, format as
measured on a P3000 with firmware 1.1.0-rc.1); they don't prove device
behaviour. Test ids (S1, N2, ...) refer to the concept "Paket C".
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.novolto_mqtt.connection import MqttConnection
from custom_components.novolto_mqtt.const import (
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_SERIAL,
    DOMAIN,
)

from . import conftest
from .conftest import (
    BASE,
    CONTROL_TOPIC,
    ENTRY_ID,
    FULL_TELEGRAM,
    INFO_TOPIC,
    STORE_KEY,
    eid,
    errors,
    fire,
    make_entry,
    setup_entry,
)
from .test_behaviour import ACK_ERR, ACK_OK, AWAITING, OTHER, published
from .test_init import tick

THIRD = "TEST.SERIAL.000003"
NO_SERIAL = {k: v for k, v in FULL_TELEGRAM.items() if k != "serial"}
RETAINED_CMD = '{"sensor":[{"name":"spp","value":3000}]}'


def issue(hass: HomeAssistant, name: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{name}_{ENTRY_ID}")


def foreign(serial: str = OTHER, **values: Any) -> dict[str, Any]:
    return {**FULL_TELEGRAM, "serial": serial, "avp": 2999, "avtw": 99.0, **values}


async def send(hass: HomeAssistant, payload: Any, **kwargs: Any) -> None:
    fire(hass, payload, **kwargs)
    await hass.async_block_till_done()


async def start(hass: HomeAssistant, **kwargs: Any):
    """Entry set up, own device (A) reported once and is available."""
    entry = await setup_entry(hass, **kwargs)
    await send(hass, FULL_TELEGRAM)
    assert entry.runtime_data.available
    return entry


async def set_power(hass: HomeAssistant, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": eid(hass, "number", "target_power"), "value": value},
        blocking=True,
    )


async def assert_send_blocked(hass: HomeAssistant, mqtt_mock, serial: str = OTHER) -> None:
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(HomeAssistantError) as exc:
        await set_power(hass, 500)
    assert exc.value.translation_key == "foreign_device_on_topic"
    assert exc.value.translation_placeholders["serial"] == serial
    assert published(mqtt_mock) == []


async def assert_send_allowed(hass: HomeAssistant, mqtt_mock, value: int = 500) -> None:
    mqtt_mock.async_publish.reset_mock()
    await set_power(hass, value)
    assert published(mqtt_mock) == [
        (CONTROL_TOPIC, {"sensor": [{"name": "spp", "value": value}]})
    ]


async def confirm_fix_flow(hass: HomeAssistant, entry_id: str, serial: str = OTHER):
    from custom_components.novolto_mqtt.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(
        hass, f"other_device_{entry_id}", {"entry_id": entry_id, "serial": serial}
    )
    flow.hass = hass
    result = await flow.async_step_confirm({})
    await hass.async_block_till_done()
    return result


def stored_foreign(devices: dict[str, dict[str, Any]], topic: str = INFO_TOPIC) -> dict:
    return {
        "version": 1, "minor_version": 1, "key": STORE_KEY,
        "data": {"energy_kwh": 1.5, "foreign": {"topic": topic, "devices": devices}},
    }


def foreign_record(last_ago: float, count: int = 2, window: float = 120) -> dict:
    last = dt_util.utcnow() - timedelta(seconds=last_ago)
    return {"first": last.isoformat(), "last": last.isoformat(), "count": count,
            "window": window}


# --- F1: serial number missing or invalid -----------------------------------


async def test_s1_telegram_without_serial_dropped(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await start(hass)
    await send(hass, {**NO_SERIAL, "avp": 1234, "avtw": 50.0})
    assert entry.runtime_data.data["avp"] == 395
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"


@pytest.mark.parametrize("serial", [None, 123, ""])
async def test_s2a_invalid_serial_type_dropped(hass: HomeAssistant, mqtt_mock, serial) -> None:
    entry = await start(hass)
    await send(hass, {**FULL_TELEGRAM, "serial": serial, "avp": 1234})
    assert entry.runtime_data.data["avp"] == 395
    assert issue(hass, "other_device") is None
    assert issue(hass, "duplicate_device") is None


@pytest.mark.parametrize("serial", [" X", "a/b"])
async def test_s2b_malformed_serial_is_no_foreign_device(
    hass: HomeAssistant, mqtt_mock, serial
) -> None:
    entry = await start(hass)
    await send(hass, {**FULL_TELEGRAM, "serial": serial, "avp": 1234})
    assert entry.runtime_data.data["avp"] == 395
    assert entry.runtime_data.available
    assert issue(hass, "other_device") is None
    assert issue(hass, "invalid_serial") is not None


async def test_s2c_malformed_serial_not_learned(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    await send(hass, {**FULL_TELEGRAM, "serial": "a/b"}, topic="keller/stab/info")
    assert entry.data[CONF_SERIAL] is None
    assert entry.unique_id == "keller/stab"
    assert not entry.runtime_data.available


async def test_s3_unknown_entry_takes_nothing_without_serial(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    await send(hass, NO_SERIAL, topic="keller/stab/info")
    assert entry.runtime_data.data == {}
    assert not entry.runtime_data.available
    assert entry.data[CONF_AWAITING_DATA] is True


@pytest.mark.parametrize("serial", ["missing", None, 123, ""])
async def test_s4a_ack_without_valid_serial_ignored(
    hass: HomeAssistant, mqtt_mock, serial
) -> None:
    await start(hass)
    await set_power(hass, 500)
    ack = {k: v for k, v in ACK_ERR.items() if k != "serial"}
    if serial != "missing":
        ack["serial"] = serial
    await send(hass, ack)
    assert hass.states.get(eid(hass, "number", "target_power")).state == "500"
    assert issue(hass, "setting_rejected") is None


async def test_s4b_ack_ignored_while_own_serial_unknown(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    await send(hass, {**ACK_ERR, "serial": OTHER}, topic="keller/stab/info")
    assert issue(hass, "setting_rejected") is None


async def test_s5_warns_once(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await start(hass)
    for _ in range(5):
        await send(hass, NO_SERIAL)
    assert caplog.text.count("without a valid serial number") == 1


async def test_s6_retained_without_serial_not_taken(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    await send(hass, NO_SERIAL, topic="keller/stab/info", retain=True)
    assert entry.runtime_data.data == {}
    assert issue(hass, "invalid_serial") is None  # retained: dropped silently


async def test_s7_invalid_serial_replaces_no_data(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    for _ in range(21):  # 10.5 min
        await send(hass, NO_SERIAL, topic="keller/stab/info")
        await tick(hass, freezer, 30)
    assert issue(hass, "invalid_serial") is not None
    assert issue(hass, "no_data") is None


async def test_s8_invalid_serial_issue_ends_after_timeout(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass)
    await send(hass, NO_SERIAL)
    found = issue(hass, "invalid_serial")
    assert found is not None
    assert found.severity is ir.IssueSeverity.WARNING
    assert not found.is_fixable
    assert found.translation_placeholders == {"name": "Novolto Test"}
    for _ in range(3):  # 90 s: still there
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    assert issue(hass, "invalid_serial") is not None
    for _ in range(2):  # 150 s without an invalid message
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    assert issue(hass, "invalid_serial") is None


async def test_s9_invalid_serial_issue_does_not_flap(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass)
    await send(hass, NO_SERIAL)
    created = issue(hass, "invalid_serial").created
    for _ in range(5):
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
        assert issue(hass, "invalid_serial").created == created
        await tick(hass, freezer, 30)
        await send(hass, NO_SERIAL)
        assert issue(hass, "invalid_serial").created == created


async def test_s10_only_invalid_serials_no_switch_off(hass: HomeAssistant, mqtt_mock) -> None:
    """Deviation i: without a valid serial the device is never available."""
    entry = await setup_entry(hass)
    await send(hass, NO_SERIAL)
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(HomeAssistantError) as exc:
        await entry.runtime_data.async_set_target_power(0)
    assert exc.value.translation_key == "device_offline"
    assert published(mqtt_mock) == []


async def test_l1_no_data_after_invalid_serial_ends(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    for _ in range(21):
        await send(hass, NO_SERIAL, topic="keller/stab/info")
        await tick(hass, freezer, 30)
    assert issue(hass, "no_data") is None
    for _ in range(5):  # 150 s of silence
        await tick(hass, freezer, 30)
    assert issue(hass, "invalid_serial") is None
    assert issue(hass, "no_data") is not None


# --- F2-F4: foreign device on the topic --------------------------------------


async def test_n1_single_foreign_telegram_reports_only(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    device = entry.runtime_data
    assert device.available
    assert device.data["avp"] == 395
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"
    found = issue(hass, "other_device")
    assert found.is_fixable
    assert found.data == {"entry_id": ENTRY_ID, "serial": OTHER}
    assert caplog.text.count("carries data of another device") == 1
    await assert_send_allowed(hass, mqtt_mock)


async def test_n2_alternating_devices_block_sending(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await start(hass)
    for payload in (foreign(), FULL_TELEGRAM, foreign()):
        await send(hass, payload)
        assert entry.runtime_data.available
    await assert_send_blocked(hass, mqtt_mock)


async def test_n3_block_ends_after_window(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    await assert_send_blocked(hass, mqtt_mock)
    for _ in range(5):  # 150 s without B, A keeps reporting
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    assert issue(hass, "other_device") is None
    await assert_send_allowed(hass, mqtt_mock)


async def test_n4_window_capped_for_huge_msi(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass)
    await send(hass, foreign(msi=100000))
    await send(hass, foreign(msi=100000))
    for _ in range(29):  # 870 s
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    await tick(hass, freezer, 29)  # 899 s
    await send(hass, FULL_TELEGRAM)
    await assert_send_blocked(hass, mqtt_mock)
    await tick(hass, freezer, 2)  # 901 s
    await send(hass, FULL_TELEGRAM)
    await assert_send_allowed(hass, mqtt_mock)


async def test_n5_replacement_old_device_times_out(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await start(hass)
    device = entry.runtime_data
    await tick(hass, freezer, 30)
    await send(hass, foreign())
    await tick(hass, freezer, 30)  # 60 s
    await send(hass, foreign())
    assert device.available
    assert device.data["avp"] == 395
    await assert_send_blocked(hass, mqtt_mock)
    for _ in range(3):  # 150 s
        await tick(hass, freezer, 30)
        await send(hass, foreign())
    assert not device.available
    assert issue(hass, "other_device").is_fixable


async def test_n6_block_survives_reload(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    await tick(hass, freezer, 30)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)  # 90 s after the last B
    await send(hass, FULL_TELEGRAM)
    await assert_send_blocked(hass, mqtt_mock)
    assert issue(hass, "other_device") is not None
    await tick(hass, freezer, 31)  # 121 s
    await send(hass, FULL_TELEGRAM)
    await assert_send_allowed(hass, mqtt_mock)


async def test_n7_entry_learns_serial_after_other_entry_removed(
    hass: HomeAssistant, mqtt_mock
) -> None:
    make_entry(entry_id="first", title="First").add_to_hass(hass)  # owns BASE
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab", title="Second")
    await send(hass, FULL_TELEGRAM, topic="keller/stab/info")
    await send(hass, FULL_TELEGRAM, topic="keller/stab/info")
    assert issue(hass, "duplicate_device") is not None
    await hass.config_entries.async_remove("first")
    await hass.async_block_till_done()
    await send(hass, FULL_TELEGRAM, topic="keller/stab/info")
    assert entry.data[CONF_SERIAL] == BASE
    await send(hass, FULL_TELEGRAM, topic="keller/stab/info")
    assert entry.runtime_data.available
    assert issue(hass, "duplicate_device") is None
    mqtt_mock.async_publish.reset_mock()
    await set_power(hass, 500)
    assert published(mqtt_mock) == [
        ("keller/stab/control", {"sensor": [{"name": "spp", "value": 500}]})
    ]


async def test_n8_two_foreign_devices(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    await start(hass)
    await send(hass, foreign())
    await send(hass, foreign(THIRD))
    await assert_send_blocked(hass, mqtt_mock)
    assert issue(hass, "other_device").translation_placeholders["serial"] == OTHER
    for _ in range(3):
        await send(hass, foreign())
        await send(hass, foreign(THIRD))
    assert caplog.text.count("carries data of another device") == 2


async def test_n9_adopt_after_old_device_silent(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await start(hass)
    for _ in range(5):  # 150 s: A gone
        await tick(hass, freezer, 30)
    await send(hass, foreign())
    await send(hass, foreign())
    result = await confirm_fix_flow(hass, ENTRY_ID)
    assert result["type"] == "create_entry"
    assert entry.data[CONF_SERIAL] == OTHER
    await send(hass, foreign(avp=777))
    assert entry.runtime_data.available
    assert hass.states.get("sensor.novolto_test_power").state == "777"
    await assert_send_allowed(hass, mqtt_mock)


async def test_n10_issue_kind_follows_setup(hass: HomeAssistant, mqtt_mock) -> None:
    garage = make_entry(
        entry_id="garage_entry", title="Garage",
        data={CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: "garage", CONF_SERIAL: OTHER},
        unique_id=OTHER,
    )
    garage.add_to_hass(hass)
    await start(hass)
    await send(hass, foreign())
    assert issue(hass, "duplicate_device").translation_placeholders["other"] == "Garage"
    await hass.config_entries.async_remove("garage_entry")
    await hass.async_block_till_done()
    await send(hass, foreign())
    assert issue(hass, "duplicate_device") is None
    assert issue(hass, "other_device").is_fixable

    # and back: the foreign device gets set up as an entry of its own
    make_entry(
        entry_id="garage_entry2", title="Garage 2",
        data={CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: "garage", CONF_SERIAL: OTHER},
        unique_id=OTHER,
    ).add_to_hass(hass)
    await send(hass, foreign())
    assert issue(hass, "other_device") is None
    assert issue(hass, "duplicate_device").translation_placeholders["other"] == "Garage 2"


async def test_n11_switch_off_allowed_while_blocked(hass: HomeAssistant, mqtt_mock) -> None:
    await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    await assert_send_allowed(hass, mqtt_mock, value=0)


async def test_n12_energy_chain_ignores_foreign(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    await tick(hass, freezer, 30)
    await send(hass, foreign(avp=3000))
    await tick(hass, freezer, 30)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    assert entry.runtime_data.energy_kwh == pytest.approx(1000 / 1000 * 60 / 3600)


async def test_n14_block_restored_after_restart(
    hass: HomeAssistant, mqtt_mock, hass_storage
) -> None:
    hass_storage[STORE_KEY] = stored_foreign({OTHER: foreign_record(60)})
    await start(hass)
    assert issue(hass, "other_device") is not None
    await assert_send_blocked(hass, mqtt_mock)
    assert hass.config_entries.async_get_entry(ENTRY_ID).runtime_data.energy_kwh == 1.5


async def test_n14_expired_block_cleared_after_restart(
    hass: HomeAssistant, mqtt_mock, hass_storage
) -> None:
    hass_storage[STORE_KEY] = stored_foreign({OTHER: foreign_record(3600)})
    ir.async_create_issue(
        hass, DOMAIN, f"other_device_{ENTRY_ID}", is_fixable=True,
        severity=ir.IssueSeverity.ERROR, translation_key="other_device",
        translation_placeholders={"name": "Novolto Test", "serial": OTHER, "known": BASE},
        data={"entry_id": ENTRY_ID, "serial": OTHER},
    )
    await start(hass)
    assert issue(hass, "other_device") is None
    await assert_send_allowed(hass, mqtt_mock)


async def test_n15_reload_after_one_foreign_telegram(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await send(hass, FULL_TELEGRAM)
    await assert_send_allowed(hass, mqtt_mock)
    await send(hass, foreign())
    await assert_send_blocked(hass, mqtt_mock)


async def test_n16_adopt_refused_while_own_device_reports(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    result = await confirm_fix_flow(hass, ENTRY_ID)
    assert result["type"] == "abort"
    assert result["reason"] == "own_device_active"
    assert result["description_placeholders"] == {"seconds": "120"}
    assert entry.data[CONF_SERIAL] == BASE
    assert issue(hass, "other_device") is not None


async def test_n17_own_serial_in_store_ignored(hass: HomeAssistant, mqtt_mock, hass_storage) -> None:
    hass_storage[STORE_KEY] = stored_foreign({BASE: foreign_record(10)})
    await start(hass)
    assert issue(hass, "other_device") is None
    await assert_send_allowed(hass, mqtt_mock)


async def test_n18_own_ack_counts_after_foreign_telegram(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await start(hass)
    await set_power(hass, 500)
    await send(hass, foreign())
    await send(hass, ACK_ERR)
    assert entry.runtime_data.get("spp") == 400
    assert issue(hass, "setting_rejected") is not None

    # also while sending is blocked ("off" is still sent)
    ir.async_delete_issue(hass, DOMAIN, f"setting_rejected_{ENTRY_ID}")
    await send(hass, foreign())
    await set_power(hass, 0)
    assert entry.runtime_data.get("spp") == 0
    await send(hass, ACK_ERR)
    assert entry.runtime_data.get("spp") == 400
    assert issue(hass, "setting_rejected") is not None


async def test_n18b_switch_off_needs_available_device(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await start(hass)
    for _ in range(5):
        await tick(hass, freezer, 30)
        await send(hass, foreign())
    assert not entry.runtime_data.available
    with pytest.raises(HomeAssistantError) as exc:
        await entry.runtime_data.async_set_target_power(0)
    assert exc.value.translation_key == "device_offline"


async def test_n19_reconfigure_to_other_topic_drops_block(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_BASE_TOPIC: "neu"}
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await send(hass, FULL_TELEGRAM, topic="neu/info")
    assert issue(hass, "other_device") is None
    mqtt_mock.async_publish.reset_mock()
    await set_power(hass, 500)
    assert published(mqtt_mock) == [
        ("neu/control", {"sensor": [{"name": "spp", "value": 500}]})
    ]


async def test_n20_other_settings_blocked(hass: HomeAssistant, mqtt_mock) -> None:
    await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(HomeAssistantError) as exc:
        await hass.services.async_call(
            "water_heater", "set_temperature",
            {"entity_id": "water_heater.novolto_test", "temperature": 45},
            blocking=True,
        )
    assert exc.value.translation_key == "foreign_device_on_topic"
    assert exc.value.translation_placeholders == {"name": "Novolto Test", "serial": OTHER}
    assert published(mqtt_mock) == []


async def test_n21_ack_ok_while_blocked(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await start(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    await set_power(hass, 0)
    await send(hass, ACK_OK)
    assert entry.runtime_data._pending["spp"].acked  # noqa: SLF001
    await send(hass, {**FULL_TELEGRAM, "spp": 0})
    assert not entry.runtime_data._pending  # noqa: SLF001


async def test_n22_adopt_refused_when_entry_not_loaded(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)  # not set up
    result = await confirm_fix_flow(hass, ENTRY_ID)
    assert result["type"] == "abort"
    assert result["reason"] == "device_not_loaded"
    assert entry.data[CONF_SERIAL] == BASE


async def test_n23_adopt_refused_right_after_start(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    await send(hass, foreign())
    await send(hass, foreign())
    result = await confirm_fix_flow(hass, ENTRY_ID)
    assert result["reason"] == "own_device_active"
    for _ in range(5):  # 150 s without the old device
        await tick(hass, freezer, 30)
        await send(hass, foreign())
    result = await confirm_fix_flow(hass, ENTRY_ID)
    assert result["type"] == "create_entry"
    assert entry.data[CONF_SERIAL] == OTHER


async def test_l2_issue_moves_to_next_foreign_device(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await start(hass)
    await send(hass, foreign())
    await tick(hass, freezer, 60)
    await send(hass, FULL_TELEGRAM)
    await send(hass, foreign(THIRD))
    assert issue(hass, "other_device").translation_placeholders["serial"] == OTHER
    await tick(hass, freezer, 30)
    await send(hass, FULL_TELEGRAM)
    await tick(hass, freezer, 31)  # B expired (121 s), C still active
    assert issue(hass, "other_device").translation_placeholders["serial"] == THIRD


async def test_l3_future_timestamp_clamped(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    hass_storage[STORE_KEY] = stored_foreign({OTHER: foreign_record(-86400)})
    await start(hass)
    await assert_send_blocked(hass, mqtt_mock)
    for _ in range(5):  # 150 s
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    await assert_send_allowed(hass, mqtt_mock)


@pytest.mark.parametrize(
    "record",
    [{"first": "x", "last": "y", "count": 2, "window": 120},
     {"count": "2"}, "garbage", None],
)
async def test_l3_unreadable_store_entries_dropped(
    hass: HomeAssistant, mqtt_mock, hass_storage, record, caplog
) -> None:
    hass_storage[STORE_KEY] = stored_foreign({OTHER: record})
    await start(hass)
    await assert_send_allowed(hass, mqtt_mock)
    assert not errors(caplog)


async def test_l10_old_store_without_foreign(hass: HomeAssistant, mqtt_mock, hass_storage) -> None:
    hass_storage[STORE_KEY] = {
        "version": 1, "minor_version": 1, "key": STORE_KEY, "data": {"energy_kwh": 7.25},
    }
    entry = await start(hass)
    assert entry.runtime_data.energy_kwh == 7.25
    await assert_send_allowed(hass, mqtt_mock)


async def test_l12_foreign_list_is_bounded(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await start(hass)
    for n in range(10):
        await send(hass, foreign(f"TEST.SERIAL.1000{n:02d}"))
    assert len(entry.runtime_data._foreign) <= 8  # noqa: SLF001
    await assert_send_blocked(hass, mqtt_mock, serial="TEST.SERIAL.100002")


async def test_st1_foreign_devices_persisted(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    await start(hass)
    await send(hass, foreign(msi=300))
    await send(hass, foreign(msi=300))
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    stored = hass_storage[STORE_KEY]["data"]["foreign"]
    assert stored["topic"] == INFO_TOPIC
    assert stored["devices"][OTHER]["count"] == 2
    assert stored["devices"][OTHER]["window"] == 900


async def test_st1_foreign_devices_saved_delayed(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    await start(hass)
    await send(hass, foreign(msi=300))
    await send(hass, foreign(msi=300))
    for _ in range(11):  # 330 s
        await tick(hass, freezer, 30)
        await send(hass, FULL_TELEGRAM)
    assert OTHER in hass_storage[STORE_KEY]["data"]["foreign"]["devices"]


async def test_st2_energy_and_foreign_saved_together(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    await send(hass, foreign())
    await send(hass, foreign())
    await tick(hass, freezer, 30)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    data = hass_storage[STORE_KEY]["data"]
    assert data["energy_kwh"] == pytest.approx(1000 / 1000 * 30 / 3600)
    assert data["foreign"]["devices"][OTHER]["count"] == 2


# --- F5/F6: retained telegrams on the info topic -----------------------------


async def test_f5_foreign_retained_not_taken_while_serial_unknown(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    await send(hass, foreign(msi=600), topic="keller/stab/info", retain=True)
    assert entry.runtime_data.data == {}
    assert entry.runtime_data.effective_timeout == 120


async def test_r1_retained_ignored_while_available(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    await tick(hass, freezer, 10)
    await send(hass, {**FULL_TELEGRAM, "avp": 3000, "avtw": 10.0}, retain=True)
    assert entry.runtime_data.data["avtw"] == 23.8
    assert not entry.runtime_data.last_telegram_retained
    await tick(hass, freezer, 20)
    await send(hass, {**FULL_TELEGRAM, "avp": 1000})
    assert entry.runtime_data.energy_kwh == pytest.approx(1000 / 1000 * 30 / 3600)


async def test_r2_retained_taken_while_unavailable(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await start(hass)
    for _ in range(5):
        await tick(hass, freezer, 30)
    await send(hass, {**FULL_TELEGRAM, "avtw": 10.0}, retain=True)
    assert entry.runtime_data.data["avtw"] == 10.0
    assert not entry.runtime_data.available


# --- F10: retained command on the control topic -------------------------------


class FetchControl:
    """Replacement for the control check; returns `result`, counts calls."""

    def __init__(self, result: str | None = None) -> None:
        self.result = result
        self.calls = 0

    def patch(self):
        fetch = self

        async def _fetch(_connection) -> str | None:
            fetch.calls += 1
            return fetch.result

        return patch.object(
            MqttConnection, "async_fetch_retained_control", _fetch, create=True
        )


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_rc1_retained_command_reported_at_setup(hass: HomeAssistant, mqtt_mock) -> None:
    with FetchControl(RETAINED_CMD).patch():
        await setup_entry(hass)
        await settle(hass)
    found = issue(hass, "retained_control")
    assert found.severity is ir.IssueSeverity.ERROR
    assert not found.is_fixable
    assert found.translation_placeholders == {"name": "Novolto Test", "payload": RETAINED_CMD}


async def test_rc1_nothing_retained_no_issue(hass: HomeAssistant, mqtt_mock) -> None:
    with FetchControl(None).patch():
        await setup_entry(hass)
        await settle(hass)
    assert issue(hass, "retained_control") is None


async def _run_real_fetch(hass, entry, fire_messages):
    with patch("custom_components.novolto_mqtt.connection.STORED_MESSAGE_WAIT_SECONDS", 0.2):
        task = hass.async_create_task(
            conftest.REAL_FETCH_RETAINED_CONTROL(entry.runtime_data.connection)
        )
        await asyncio.sleep(0.05)
        for payload, retain in fire_messages:
            fire(hass, payload, topic=CONTROL_TOPIC, retain=retain)
        return await task


async def test_rc2_live_command_is_not_retained(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    assert await _run_real_fetch(hass, entry, [(RETAINED_CMD, False)]) is None


async def test_rc3_real_check_finds_and_unsubscribes(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    found = await _run_real_fetch(hass, entry, [("not json", True)])
    assert found == "not json"  # raw, also non-JSON
    subs = [c.args[0] for c in mqtt_mock.async_subscribe.call_args_list]
    assert CONTROL_TOPIC in subs
    # nothing retained: None after the wait, subscription gone again
    assert await _run_real_fetch(hass, entry, []) is None
    assert await _run_real_fetch(hass, entry, [("", True)]) is None  # empty = none


async def test_rc7_check_when_device_returns(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    fetch = FetchControl(None)
    with fetch.patch():
        await start(hass)
        await settle(hass)
        for _ in range(5):  # unavailable
            await tick(hass, freezer, 30)
        await settle(hass)
        assert issue(hass, "retained_control") is None
        fetch.result = RETAINED_CMD
        await send(hass, FULL_TELEGRAM)
        await settle(hass)
    assert issue(hass, "retained_control") is not None


async def test_rc7_check_after_device_restart(hass: HomeAssistant, mqtt_mock) -> None:
    fetch = FetchControl(None)
    with fetch.patch():
        await start(hass)
        await settle(hass)
        fetch.result = RETAINED_CMD
        await send(hass, FULL_TELEGRAM)  # normal telegram: no check
        await settle(hass)
        assert issue(hass, "retained_control") is None
        await send(hass, {**FULL_TELEGRAM, "rst_cause": "sw"})
        await settle(hass)
    assert issue(hass, "retained_control") is not None


async def test_rc7_foreign_restart_triggers_nothing(hass: HomeAssistant, mqtt_mock) -> None:
    fetch = FetchControl(None)
    with fetch.patch():
        await start(hass)
        await settle(hass)
        calls = fetch.calls
        await send(hass, foreign(rst_cause="sw"))
        await settle(hass)
    assert fetch.calls == calls


async def test_rc10_rejection_without_own_setting_triggers_check(
    hass: HomeAssistant, mqtt_mock
) -> None:
    fetch = FetchControl(None)
    with fetch.patch():
        await start(hass)
        await settle(hass)
        fetch.result = RETAINED_CMD
        await set_power(hass, 500)
        await send(hass, ACK_ERR)  # own setting rejected: no check
        await settle(hass)
        assert issue(hass, "retained_control") is None
        await send(hass, ACK_ERR)  # nothing open: someone else's command
        await settle(hass)
    assert issue(hass, "setting_rejected") is not None
    assert issue(hass, "retained_control") is not None


async def test_rc11_issue_gone_when_message_deleted(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    fetch = FetchControl(RETAINED_CMD)
    with fetch.patch():
        await start(hass)
        await settle(hass)
        assert issue(hass, "retained_control") is not None
        fetch.result = None
        await tick(hass, freezer, 30)
        await settle(hass)
    assert issue(hass, "retained_control") is None


async def test_rc12_payload_escaped_and_shortened(hass: HomeAssistant, mqtt_mock) -> None:
    raw = "`x`\n" + "y" * 200
    with FetchControl(raw).patch():
        await setup_entry(hass)
        await settle(hass)
    payload = issue(hass, "retained_control").translation_placeholders["payload"]
    assert payload.startswith("'x' ")
    assert "`" not in payload and "\n" not in payload
    assert len(payload) == 121 and payload.endswith("…")


async def test_rc_check_failure_keeps_state(hass: HomeAssistant, mqtt_mock, caplog) -> None:
    async def _fail(_connection):
        raise HomeAssistantError("MQTT not connected")

    with patch.object(MqttConnection, "async_fetch_retained_control", _fail, create=True):
        await setup_entry(hass)
        await settle(hass)
    assert issue(hass, "retained_control") is None
    assert not errors(caplog)


# --- F11: device restart -----------------------------------------------------

RESTART_FIELDS = {"version": "1.1.0-rc.1", "reg": "EU", "rst_cause": "sw", "exch": "abc"}


async def test_rs1_restart_fields_are_harmless(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, **RESTART_FIELDS})
    assert entry.runtime_data.available
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"
    assert not errors(caplog)


async def test_rs2_restart_gap_not_booked(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, "avp": 3000})
    await tick(hass, freezer, 100)
    await send(hass, {**FULL_TELEGRAM, "avp": 141, "rst_cause": "sw"})
    assert entry.runtime_data.energy_kwh == 0.0
    await tick(hass, freezer, 30)
    await send(hass, {**FULL_TELEGRAM, "avp": 141})
    assert entry.runtime_data.energy_kwh == pytest.approx(141 / 1000 * 30 / 3600)


async def test_rs3_triacon_back_to_zero(hass: HomeAssistant, mqtt_mock, caplog) -> None:
    await setup_entry(hass, enable_all=True)
    await send(hass, FULL_TELEGRAM)
    await send(hass, {**FULL_TELEGRAM, "triacon": 0, "rst_cause": "sw"})
    assert hass.states.get(eid(hass, "sensor", "triacon")).state == "0"
    assert not errors(caplog)


async def test_rs4_firmware_version_in_registry(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    await send(hass, {**FULL_TELEGRAM, **RESTART_FIELDS})
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == "1.1.0-rc.1"
    # a foreign device's version is never taken over
    await send(hass, foreign(version="9.9.9"))
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == "1.1.0-rc.1"


# --- E3: new issues are cleaned up --------------------------------------------


async def _with_new_issues(hass: HomeAssistant):
    with FetchControl(RETAINED_CMD).patch():
        entry = await start(hass)
        await settle(hass)
    await send(hass, NO_SERIAL)
    assert issue(hass, "invalid_serial") is not None
    assert issue(hass, "retained_control") is not None
    return entry


async def test_e3a_new_issues_removed_with_entry(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await _with_new_issues(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, "invalid_serial") is None
    assert issue(hass, "retained_control") is None


async def test_e3b_new_issues_removed_when_disabled(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await _with_new_issues(hass)
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    await hass.async_block_till_done()
    assert issue(hass, "invalid_serial") is None
    assert issue(hass, "retained_control") is None
