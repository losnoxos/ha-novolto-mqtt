"""0.6.0 runtime behaviour: retained telegrams, optimistic values, repairs,
water heater states, power stages, migration, serial adoption, two devices,
diagnostics."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.novolto_mqtt.const import (
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.novolto_mqtt.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    BASE,
    CONTROL_TOPIC,
    ENTRY_ID,
    FULL_TELEGRAM,
    eid,
    fire,
    make_entry,
    make_legacy_entry,
    setup_entry,
)
from .test_init import tick

OTHER = "TEST.SERIAL.000002"
ACK_OK = {"serial": BASE, "unix_time": 1, "ret": 0}
ACK_ERR = {"serial": BASE, "unix_time": 1, "ret": 13, "s_err": "Module SENSOR: SPP -> wrong type"}


def published(mqtt_mock) -> list[tuple[str, dict]]:
    return [
        (c.args[0], json.loads(c.args[1]))
        for c in mqtt_mock.async_publish.call_args_list
    ]


# --- Pkt. 1: retained telegrams --------------------------------------------


async def test_retained_telegram_never_makes_device_available(
    hass: HomeAssistant, mqtt_mock
) -> None:
    entry = await setup_entry(hass)
    fire(hass, FULL_TELEGRAM, retain=True)
    await hass.async_block_till_done()
    device = entry.runtime_data
    assert device.data["avtw"] == 23.8  # values taken over ...
    assert not device.available  # ... but not available
    assert hass.states.get("water_heater.novolto_test").state == STATE_UNAVAILABLE
    assert hass.states.get(eid(hass, "sensor", "avp")).state == STATE_UNAVAILABLE

    fire(hass, {**FULL_TELEGRAM, "avp": 500})  # live telegram
    await hass.async_block_till_done()
    assert device.available
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "500"


async def test_retained_telegram_books_no_energy(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 3000}, retain=True)
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    fire(hass, {**FULL_TELEGRAM, "avp": 3000})
    await hass.async_block_till_done()
    assert entry.runtime_data.energy_kwh == 0.0


async def test_retained_ack_ignored(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    fire(hass, ACK_ERR, retain=True)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"setting_rejected_{ENTRY_ID}")


# --- Pkt. 3 / A6: water heater states --------------------------------------


@pytest.mark.parametrize(
    ("spp", "avp", "state"),
    [(400, 395, "heating"), (400, 3, "idle"), (400, 15, "idle"), (0, 0, "off"),
     (0, 395, "off")],
)
async def test_water_heater_states(hass: HomeAssistant, mqtt_mock, spp, avp, state) -> None:
    await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "spp": spp, "avp": avp})
    await hass.async_block_till_done()
    assert hass.states.get("water_heater.novolto_test").state == state


# --- Pkt. 5: power stage select ---------------------------------------------


async def test_power_stage_select(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    select_id = eid(hass, "select", "power_stage")
    fire(hass, {**FULL_TELEGRAM, "spp": 2000})
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "2000 W"
    fire(hass, {**FULL_TELEGRAM, "spp": 730})  # e.g. PV surplus control
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == STATE_UNKNOWN

    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "off"}, blocking=True
    )
    assert published(mqtt_mock) == [
        (CONTROL_TOPIC, {"sensor": [{"name": "spp", "value": 0}]})
    ]
    assert hass.states.get(select_id).state == "off"  # optimistic
    assert hass.states.get("water_heater.novolto_test").state == "off"
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "3000 W"},
        blocking=True,
    )
    assert published(mqtt_mock)[-1][1] == {"sensor": [{"name": "spp", "value": 3000}]}


# --- A7: optimistic values --------------------------------------------------


async def test_optimistic_value_confirmed_by_telegram(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 1200}, blocking=True
    )
    assert hass.states.get(number_id).state == "1200"
    # an older telegram still in flight doesn't make the slider jump back
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "1200"
    fire(hass, {**FULL_TELEGRAM, "spp": 1200})
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "1200"
    assert not entry.runtime_data._pending  # noqa: SLF001


async def test_optimistic_value_after_ack_follows_device(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 1200}, blocking=True
    )
    fire(hass, ACK_OK)
    fire(hass, {**FULL_TELEGRAM, "spp": 1000})  # device clamped it
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "1000"


async def test_rejection_reverts_and_raises_issue(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 1200}, blocking=True
    )
    fire(hass, ACK_ERR)
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "400"  # back to the device value
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"setting_rejected_{ENTRY_ID}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {
        "name": "Novolto Test", "ret": "13", "s_err": "Module SENSOR: SPP -> wrong type"
    }
    fire(hass, ACK_OK)  # next setting accepted -> issue gone
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"setting_rejected_{ENTRY_ID}")


async def test_unconfirmed_value_expires(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass)
    number_id = eid(hass, "number", "target_power")
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number_id, "value": 1200}, blocking=True
    )
    for _ in range(3):
        await tick(hass, freezer, 30)
        fire(hass, FULL_TELEGRAM)  # device keeps reporting 400, never acks
        await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "400"


# --- offline: nothing is sent -------------------------------------------------


async def test_set_while_offline_raises(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    mqtt_mock.async_publish.reset_mock()
    with pytest.raises(HomeAssistantError) as exc:
        await entry.runtime_data.async_set_target_power(1000)
    assert exc.value.translation_key == "device_offline"
    assert exc.value.translation_placeholders == {"name": "Novolto Test"}
    mqtt_mock.async_publish.assert_not_called()


# --- Pkt. 12: STB repair issue ------------------------------------------------


async def test_stb_issue(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    issue_id = f"stb_tripped_{ENTRY_ID}"
    fire(hass, {**FULL_TELEGRAM, "st": 0x0401})
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.severity is ir.IssueSeverity.CRITICAL
    assert issue.translation_placeholders == {"name": "Novolto Test"}
    fire(hass, {**FULL_TELEGRAM, "st": 0})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_stb_from_retained_telegram_ignored(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "st": 0x0400}, retain=True)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stb_tripped_{ENTRY_ID}") is None


# --- "no data" repair issue (only for devices added without data) -------------

AWAITING = {CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: "keller/stab", CONF_SERIAL: None,
            CONF_AWAITING_DATA: True}


async def test_no_data_issue_after_10_minutes(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    issue_id = f"no_data_{ENTRY_ID}"
    await tick(hass, freezer, 9 * 60)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await tick(hass, freezer, 61)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.translation_placeholders == {"name": "Novolto Test", "minutes": "10"}

    # first telegram: issue gone, device keyed by its serial from now on
    fire(hass, FULL_TELEGRAM, topic="keller/stab/info")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert CONF_AWAITING_DATA not in entry.data
    assert entry.data[CONF_SERIAL] == BASE
    assert entry.unique_id == BASE


async def test_no_issue_for_normal_device_going_silent(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    await tick(hass, freezer, 3600)  # switched off overnight
    assert not [
        i for (domain, _), i in ir.async_get(hass).issues.items() if domain == DOMAIN
    ]


async def test_serial_adoption_keeps_entities(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    ent_reg = er.async_get(hass)
    wh_id = ent_reg.async_get_entity_id("water_heater", DOMAIN, "keller/stab_heating")
    assert wh_id == "water_heater.novolto_test"

    fire(hass, FULL_TELEGRAM, topic="keller/stab/info")
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert ent_reg.async_get_entity_id("water_heater", DOMAIN, f"{BASE}_heating") == wh_id
    assert ent_reg.async_get_entity_id("water_heater", DOMAIN, "keller/stab_heating") is None
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, BASE)}
    assert device.serial_number == BASE
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    # data flows normally afterwards
    fire(hass, {**FULL_TELEGRAM, "avp": 777}, topic="keller/stab/info")
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "777"


async def test_serial_adoption_refused_if_serial_set_up_elsewhere(
    hass: HomeAssistant, mqtt_mock
) -> None:
    make_entry(entry_id="first", title="First").add_to_hass(hass)  # owns BASE
    entry = await setup_entry(
        hass, data=AWAITING, unique_id="keller/stab", title="Second"
    )
    fire(hass, FULL_TELEGRAM, topic="keller/stab/info")
    await hass.async_block_till_done()
    assert entry.unique_id == "keller/stab"
    assert entry.data[CONF_SERIAL] is None


async def test_data_update_does_not_reload(hass: HomeAssistant, mqtt_mock) -> None:
    """Clearing 'awaiting data' changes entry data, but must not reload."""
    entry = await setup_entry(
        hass, data={**AWAITING, CONF_BASE_TOPIC: BASE}, unique_id=BASE
    )
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    device = entry.runtime_data
    fire(hass, {**FULL_TELEGRAM, "avp": 500})
    await hass.async_block_till_done()
    assert entry.runtime_data is device  # same instance -> no reload
    assert entry.data[CONF_SERIAL] == BASE


# --- migration from 0.5.0 -----------------------------------------------------


async def test_migration_keeps_old_values(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_legacy_entry(
        {"max_power": 3000, "enable_board_temperature": False, "timeout_seconds": 300}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (entry.version, entry.minor_version) == (1, 2)
    # serial taken from the first telegram
    assert entry.data == {CONF_BASE_TOPIC: BASE, CONF_CONNECTION: "mqtt", CONF_SERIAL: None}
    assert entry.options == {
        "timeout_seconds": 300,
        "model": "p3000",
        "power_stages": [0, 1000, 2000, 3000],
        "sptw_max": 80,  # old default kept, not silently lowered to 50
        "sptwh_max": 20,  # old default kept, not silently lowered to 10
    }
    wh = hass.states.get("water_heater.novolto_test")
    assert wh.attributes["max_temp"] == 80


async def test_migration_without_options(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_legacy_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["sptw_max"] == 80
    assert entry.options["sptwh_max"] == 20
    assert entry.options["model"] == "p3000"


async def test_migration_p2300_from_max_power(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_legacy_entry({"max_power": 2300, "sptw_max": 60.0, "sptwh_max": 5.0})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options["model"] == "p2300"
    assert entry.options["sptw_max"] == 60.0
    assert entry.options["sptwh_max"] == 5.0
    assert entry.options["power_stages"] == [0, 1000, 2000, 2300]


async def test_migration_keeps_entity_ids(hass: HomeAssistant, mqtt_mock) -> None:
    """Existing entities (unique_id = serial + suffix) stay the same."""
    ent_reg = er.async_get(hass)
    entry = make_legacy_entry()
    entry.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "water_heater", DOMAIN, f"{BASE}_heating", config_entry=entry,
        suggested_object_id="novolto_heizstab",
    )
    ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{BASE}_energy", config_entry=entry,
        suggested_object_id="novolto_heizstab_energie",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("water_heater.novolto_heizstab") is not None
    assert hass.states.get("sensor.novolto_heizstab_energie") is not None


async def test_future_version_refused(hass: HomeAssistant, mqtt_mock) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=dict(make_entry().data), version=2, unique_id=BASE
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# --- Pkt. 10: two devices -----------------------------------------------------


async def test_two_devices_independent(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass, title="Keller")
    other = make_entry(
        title="Garage",
        data={CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: "garage", CONF_SERIAL: OTHER},
        unique_id=OTHER,
        entry_id="garage_entry",
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    fire(hass, {**FULL_TELEGRAM, "serial": OTHER, "avp": 2000}, topic="garage/info")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.keller_power").state == "1000"
    assert hass.states.get("sensor.garage_power").state == "2000"
    assert hass.states.get("water_heater.keller") is not None
    assert hass.states.get("water_heater.garage") is not None
    assert not [s for s in hass.states.async_entity_ids() if s.endswith("_2")]

    mqtt_mock.async_publish.reset_mock()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.garage_target_power", "value": 500},
        blocking=True,
    )
    assert published(mqtt_mock) == [
        ("garage/control", {"sensor": [{"name": "spp", "value": 500}]})
    ]
    dev_reg = dr.async_get(hass)
    serials = {
        d.serial_number
        for e in (ENTRY_ID, "garage_entry")
        for d in dr.async_entries_for_config_entry(dev_reg, e)
    }
    assert serials == {BASE, OTHER}
    keller = hass.config_entries.async_get_entry(ENTRY_ID).runtime_data
    garage = other.runtime_data
    assert keller.energy_kwh == 0.0 and garage.energy_kwh == 0.0
    assert keller.store.key != garage.store.key


# --- A21: diagnostics -----------------------------------------------------------


async def test_diagnostics_redacts_serial(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert BASE not in json.dumps(diag)
    assert diag["available"] is True
    assert diag["model"] == "P3000"
    assert diag["telegram"]["avtw"] == 23.8
    assert diag["entry"]["options"]["sptw_max"] == 50


async def test_device_model_in_registry(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass, {"model": "p2300"})
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.model == "P2300"
    assert device.manufacturer == "Novolto"


async def test_remove_entry_deletes_issues(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "st": 0x0400})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stb_tripped_{ENTRY_ID}")
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stb_tripped_{ENTRY_ID}") is None


async def test_migration_serial_from_first_telegram(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_legacy_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert entry.data[CONF_SERIAL] == BASE
    assert entry.unique_id == BASE
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.serial_number == BASE
    assert device.identifiers == {(DOMAIN, BASE)}


async def test_migration_custom_topic_adopts_real_serial(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    """0.5.0 entry with a custom topic: keyed by the serial afterwards."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Novolto Test", data={CONF_BASE_TOPIC: "boiler"},
        unique_id="boiler", entry_id=ENTRY_ID, version=1, minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    wh_id = "water_heater.novolto_test"
    assert hass.states.get(wh_id) is not None
    fire(hass, FULL_TELEGRAM, topic="boiler/info")
    await hass.async_block_till_done()
    assert entry.unique_id == BASE
    assert er.async_get(hass).async_get_entity_id(
        "water_heater", DOMAIN, f"{BASE}_heating"
    ) == wh_id
    assert "another device" not in caplog.text


async def test_refused_adoption_warns_once(
    hass: HomeAssistant, mqtt_mock, caplog: pytest.LogCaptureFixture
) -> None:
    make_entry(entry_id="first", title="First").add_to_hass(hass)  # owns BASE
    await setup_entry(hass, data=AWAITING, unique_id="keller/stab", title="Second")
    for _ in range(5):
        fire(hass, FULL_TELEGRAM, topic="keller/stab/info")
        await hass.async_block_till_done()
    assert caplog.text.count("already set up as First") == 1


async def test_adoption_replaces_ignored_discovery(hass: HomeAssistant, mqtt_mock) -> None:
    ignored = MockConfigEntry(domain=DOMAIN, unique_id=BASE, source="ignore",
                              entry_id="ignored")
    ignored.add_to_hass(hass)
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    fire(hass, FULL_TELEGRAM, topic="keller/stab/info")
    await hass.async_block_till_done()
    assert entry.unique_id == BASE
    assert hass.config_entries.async_get_entry("ignored") is None


async def test_reconfigure_provisional_entry_keeps_entities(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Topic changed before the first telegram: no duplicate entities."""
    entry = await setup_entry(hass, data=AWAITING, unique_id="keller/stab")
    ent_reg = er.async_get(hass)
    before = len(er.async_entries_for_config_entry(ent_reg, entry.entry_id))
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_BASE_TOPIC: "neu/stab"}
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(ent_reg, entry.entry_id)) == before
    fire(hass, FULL_TELEGRAM, topic="neu/stab/info")
    await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert len(entities) == before
    assert all(e.unique_id.startswith(f"{BASE}_") for e in entities)
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1


async def test_issues_removed_when_disabled_kept_on_reload(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from homeassistant.config_entries import ConfigEntryDisabler

    entry = await setup_entry(hass)
    issue_id = f"stb_tripped_{ENTRY_ID}"
    fire(hass, {**FULL_TELEGRAM, "st": 0x0400})
    await hass.async_block_till_done()
    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    fire(hass, {**FULL_TELEGRAM, "st": 0x0400})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id).dismissed_version
    await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_rejection_during_publish_reverts(hass: HomeAssistant, mqtt_mock) -> None:
    """The ack can arrive before the publish call returns."""
    entry = await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    device = entry.runtime_data
    real_send = device.connection.async_send_setting

    async def send_and_reject(name, value):
        await real_send(name, value)
        device.handle_payload(ACK_ERR, False)

    device.connection.async_send_setting = send_and_reject
    await device.async_set_target_power(1200)
    assert device.get("spp") == 400


# --- outdated retained message (retain switched off on the device) ----------

LIVE_TIME = 1_760_000_000


async def _start_with_retained(
    hass, retained_age: int, stored_again: Any = "same", live_delay: int = 5
):
    """Retained telegram, then a live one; the re-fetch returns stored_again.

    stored_again: "same" = the old retained telegram is still stored (retain
    off), "live" = the broker now stores the live telegram (retain on),
    None = nothing stored.
    """
    from custom_components.novolto_mqtt.connection import MqttConnection

    old = {**FULL_TELEGRAM, "unix_time": LIVE_TIME - retained_age}
    live = {**FULL_TELEGRAM, "unix_time": LIVE_TIME + live_delay}
    refetch = {"same": old, "live": live, None: None}[stored_again]

    async def fetch(self):
        return refetch

    with patch.object(MqttConnection, "async_fetch_stored_message", fetch):
        entry = await setup_entry(hass)
        fire(hass, old, retain=True)
        fire(hass, live)
        await hass.async_block_till_done()
    return entry


def _stale_issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"stale_retained_{ENTRY_ID}")


async def test_stale_retained_message_raises_fixable_issue(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.language = "de"
    await _start_with_retained(hass, retained_age=86400)
    issue = _stale_issue(hass)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders["name"] == "Novolto Test"
    # 1_759_913_600 = 2025-10-08 08:53:20 UTC -> 10:53 in Berlin
    assert issue.translation_placeholders["date"] == "08.10.2025 10:53"
    assert issue.data == {"entry_id": ENTRY_ID}


async def test_device_offline_at_start_with_retain_on_no_issue(
    hass: HomeAssistant, mqtt_mock
) -> None:
    """Retain on, rod switched off overnight: old stored state is no error."""
    await _start_with_retained(hass, retained_age=9 * 3600, stored_again="live")
    assert _stale_issue(hass) is None


async def test_stored_message_gone_on_recheck_no_issue(
    hass: HomeAssistant, mqtt_mock
) -> None:
    await _start_with_retained(hass, retained_age=86400, stored_again=None)
    assert _stale_issue(hass) is None


@pytest.mark.parametrize("retained_age", [0, 25, 110])
async def test_fresh_retained_message_no_issue(
    hass: HomeAssistant, mqtt_mock, retained_age
) -> None:
    """Stored telegram at most one interval old: no re-check needed."""
    from custom_components.novolto_mqtt.connection import MqttConnection

    with patch.object(
        MqttConnection, "async_fetch_stored_message", side_effect=AssertionError
    ):
        await _start_with_retained(hass, retained_age=retained_age, stored_again=None)
    assert _stale_issue(hass) is None


async def test_no_retained_message_no_issue(hass: HomeAssistant, mqtt_mock) -> None:
    """Retain off and nothing stored: nothing to report."""
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert _stale_issue(hass) is None


async def test_stale_retained_checked_once_per_run(hass: HomeAssistant, mqtt_mock) -> None:
    await _start_with_retained(hass, retained_age=86400)
    ir.async_delete_issue(hass, DOMAIN, f"stale_retained_{ENTRY_ID}")
    fire(hass, {**FULL_TELEGRAM, "unix_time": LIVE_TIME + 100})
    await hass.async_block_till_done()
    assert _stale_issue(hass) is None


async def test_fetch_stored_message_resubscribes(hass: HomeAssistant, mqtt_mock) -> None:
    """The real re-fetch: a new subscription receives the retained message."""
    import asyncio

    entry = await setup_entry(hass)
    connection = entry.runtime_data.connection
    with patch(
        "custom_components.novolto_mqtt.connection.STORED_MESSAGE_WAIT_SECONDS", 1
    ):
        task = hass.async_create_task(connection.async_fetch_stored_message())
        await asyncio.sleep(0.05)
        fire(hass, {**FULL_TELEGRAM, "avp": 1})  # live, not stored
        fire(hass, {**FULL_TELEGRAM, "avp": 2}, retain=True)
        stored = await task
    assert stored["avp"] == 2
    with patch(
        "custom_components.novolto_mqtt.connection.STORED_MESSAGE_WAIT_SECONDS", 0.05
    ):
        assert await connection.async_fetch_stored_message() is None


async def test_stale_retained_fix_flow_deletes_message(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
    from homeassistant.setup import async_setup_component

    from custom_components.novolto_mqtt.repairs import async_create_fix_flow

    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    await _start_with_retained(hass, retained_age=86400)
    issue_id = f"stale_retained_{ENTRY_ID}"
    issue = _stale_issue(hass)

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"]["name"] == "Novolto Test"
    mqtt_mock.async_publish.reset_mock()
    result = await flow.async_step_confirm({})
    assert result["type"] == "create_entry"
    mqtt_mock.async_publish.assert_called_once()
    assert mqtt_mock.async_publish.call_args.args[:4] == (f"{BASE}/info", "", 0, True)
    assert _stale_issue(hass) is None
    # the empty retained payload coming back is ignored
    fire(hass, "", retain=True)
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "avp")).state == "395"


async def test_stale_retained_fix_flow_device_not_loaded(
    hass: HomeAssistant, mqtt_mock
) -> None:
    from custom_components.novolto_mqtt.repairs import async_create_fix_flow

    flow = await async_create_fix_flow(
        hass, f"stale_retained_{ENTRY_ID}", {"entry_id": "missing"}
    )
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] == "abort"
    assert result["reason"] == "device_not_loaded"


async def test_stale_retained_issue_removed_with_entry(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await _start_with_retained(hass, retained_age=86400)
    assert _stale_issue(hass) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _stale_issue(hass) is None
