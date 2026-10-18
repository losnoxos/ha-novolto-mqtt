"""Energy integration (trapezoid), gaps and persistence."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant

from .conftest import FULL_TELEGRAM, STORE_KEY, eid, errors, fire, setup_entry
from .test_init import tick


async def feed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, power: float, samples: int,
    interval: float = 30,
) -> None:
    """Send `samples` telegrams, `interval` seconds apart, at `power` W."""
    for _ in range(samples):
        await tick(hass, freezer, interval)
        fire(hass, {**FULL_TELEGRAM, "avp": power})
    await hass.async_block_till_done()


def energy(hass: HomeAssistant, entry) -> float:
    return entry.runtime_data.energy_kwh


async def test_energy_sensor_attributes(hass: HomeAssistant, mqtt_mock) -> None:
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    state = hass.states.get(eid(hass, "sensor", "energy"))
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["unit_of_measurement"] == "kWh"
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass).async_get(eid(hass, "sensor", "energy"))
    assert reg.options["sensor"]["suggested_display_precision"] == 2


async def test_energy_trapezoid_and_persistence(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    entry = await setup_entry(hass)
    energy_id = eid(hass, "sensor", "energy")

    # t=0..3600 s: 121 samples at 1000 W -> exactly 1.0 kWh
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await feed(hass, freezer, 1000, 120)
    assert energy(hass, entry) == pytest.approx(1.0, abs=1e-9)
    assert float(hass.states.get(energy_id).state) == pytest.approx(1.0, abs=1e-3)

    # t=3630..5400 s: 60 samples at 2000 W. First interval is the trapezoid
    # (1000+2000)/2 W for 30 s, then 59 intervals at 2000 W.
    await feed(hass, freezer, 2000, 60)
    expected = 1.0 + (1500 * 30 + 2000 * 30 * 59) / 3_600_000
    assert expected == pytest.approx(1.995833, abs=1e-6)
    assert energy(hass, entry) == pytest.approx(expected, abs=1e-9)

    # reload: explicit save on unload, value restored from the Store
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["energy_kwh"] == pytest.approx(expected)
    assert energy(hass, entry) == pytest.approx(expected)

    # first sample after reload starts a new chain: nothing booked
    await tick(hass, freezer, 30)
    fire(hass, {**FULL_TELEGRAM, "avp": 3000})
    await hass.async_block_till_done()
    assert energy(hass, entry) == pytest.approx(expected)
    assert hass.states.get(energy_id).state == str(round(expected, 3))


async def test_energy_ramp_is_trapezoid(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 0})
    await hass.async_block_till_done()
    await tick(hass, freezer, 60)
    fire(hass, {**FULL_TELEGRAM, "avp": 3600})
    await hass.async_block_till_done()
    # (0 + 3600)/2 W * 60 s = 108000 Ws = 0.03 kWh
    assert energy(hass, entry) == pytest.approx(0.03, abs=1e-12)


async def test_energy_delayed_save_after_quiet_period(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    """A change is written once 300 s pass without further changes."""
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await feed(hass, freezer, 1000, 1)
    assert STORE_KEY not in hass_storage  # write is delayed
    for _ in range(9):  # 270 s
        await tick(hass, freezer, 30)
    assert STORE_KEY not in hass_storage
    for _ in range(2):  # 330 s
        await tick(hass, freezer, 30)
    assert hass_storage[STORE_KEY]["data"]["energy_kwh"] == pytest.approx(
        energy(hass, entry)
    )


async def test_energy_saved_periodically_while_reporting(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    """After 30 min of continuous telegrams something must be on disk."""
    await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await feed(hass, freezer, 1000, 60)
    assert STORE_KEY in hass_storage
    assert hass_storage[STORE_KEY]["data"]["energy_kwh"] >= 0.4


async def test_energy_flushed_on_final_write(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 2400})
    await hass.async_block_till_done()
    await feed(hass, freezer, 2400, 2)
    assert STORE_KEY not in hass_storage
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    assert hass_storage[STORE_KEY]["data"]["energy_kwh"] == pytest.approx(
        2400 / 1000 * 60 / 3600
    )
    assert energy(hass, entry) == pytest.approx(0.04)


async def test_energy_restored_from_existing_store(
    hass: HomeAssistant, mqtt_mock, hass_storage
) -> None:
    hass_storage[STORE_KEY] = {
        "version": 1, "minor_version": 1, "key": STORE_KEY,
        "data": {"energy_kwh": 123.4567},
    }
    await setup_entry(hass)
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "sensor", "energy")).state == "123.457"


@pytest.mark.parametrize(("before", "after"), [(0, 3000), (3000, 0), (1500, 1500)])
async def test_energy_gap_after_silence_books_nothing(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, before, after
) -> None:
    """2 h silence with availability ticks running: gap is not integrated."""
    entry = await setup_entry(hass)
    energy_id = eid(hass, "sensor", "energy")
    fire(hass, {**FULL_TELEGRAM, "avp": before})
    await hass.async_block_till_done()
    for _ in range(240):
        await tick(hass, freezer, 30)
    assert hass.states.get(energy_id).state == STATE_UNAVAILABLE
    fire(hass, {**FULL_TELEGRAM, "avp": after})
    await hass.async_block_till_done()
    assert energy(hass, entry) == 0.0
    # the chain restarts normally afterwards
    await feed(hass, freezer, after, 1)
    assert energy(hass, entry) == pytest.approx(after / 1000 * 30 / 3600)


async def test_energy_gap_without_tick_books_nothing(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    """Gap longer than the timeout but no availability tick ran in between."""
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 3000})
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=2))
    fire(hass, {**FULL_TELEGRAM, "avp": 3000})
    await hass.async_block_till_done()
    assert energy(hass, entry) == 0.0


@pytest.mark.parametrize(("gap", "booked"), [(119, True), (120, True), (121, False)])
async def test_energy_gap_boundary(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, gap, booked
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=gap))
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    expected = 1000 / 1000 * gap / 3600 if booked else 0.0
    assert energy(hass, entry) == pytest.approx(expected, abs=1e-12)


async def test_energy_null_power_breaks_chain(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await feed(hass, freezer, None, 1)
    assert energy(hass, entry) == 0.0
    await feed(hass, freezer, 1000, 1)  # previous sample unknown -> nothing
    assert energy(hass, entry) == 0.0
    await feed(hass, freezer, 1000, 1)
    assert energy(hass, entry) == pytest.approx(1000 / 1000 * 30 / 3600)
    await feed(hass, freezer, "garbage", 1)
    await feed(hass, freezer, 1000, 1)
    assert energy(hass, entry) == pytest.approx(1000 / 1000 * 30 / 3600)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_energy_negative_power_clamped(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": -500})
    await hass.async_block_till_done()
    await feed(hass, freezer, -500, 4)
    assert energy(hass, entry) == 0.0
    await feed(hass, freezer, 1000, 1)  # (0 + 1000)/2 W for 30 s
    assert energy(hass, entry) == pytest.approx(500 / 1000 * 30 / 3600)


async def test_energy_partial_telegram_without_avp_keeps_chain(
    hass: HomeAssistant, mqtt_mock, freezer: FrozenDateTimeFactory, caplog
) -> None:
    entry = await setup_entry(hass)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    fire(hass, {"serial": FULL_TELEGRAM["serial"], "avtw": 30.0})
    await hass.async_block_till_done()
    await tick(hass, freezer, 30)
    fire(hass, {**FULL_TELEGRAM, "avp": 1000})
    await hass.async_block_till_done()
    assert energy(hass, entry) == pytest.approx(1000 / 1000 * 60 / 3600)
    assert not errors(caplog)
