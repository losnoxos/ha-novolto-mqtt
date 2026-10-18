"""Shared fixtures and helpers for the Novolto MQTT tests."""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)

from custom_components.novolto_mqtt.const import (
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_SERIAL,
    CONNECTION_MQTT,
    DOMAIN,
    default_options,
)

BASE = "TEST.SERIAL.000001"
INFO_TOPIC = f"{BASE}/info"
CONTROL_TOPIC = f"{BASE}/control"
UNIX_TIME = 1760000000
ENTRY_ID = "novolto_test_entry"
STORE_KEY = f"{DOMAIN}.{ENTRY_ID}"

FULL_TELEGRAM: dict[str, Any] = {
    "serial": BASE,
    "unix_time": UNIX_TIME,
    "msi": 30,
    "avt1": 35,
    "avtw": 23.8,
    "sptw": 38.0,
    "sptwh": 2.0,
    "spp": 400,
    "avv": 231,
    "avp": 395,
    "avi": 1.7,
    "avf": 50.0,
    "rssi": -60,
    "st": 0,
    "wel": 1.2,
    "rod_st": 1,
    "triacon": 123,
    "r1on": 1,
    "r2on": 0,
}

# Warnings Home Assistant emits for every custom integration.
_EXPECTED_WARNING_SNIPPETS = ("We found a custom integration",)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components in every test."""
    yield


@pytest.fixture
async def mqtt_mock(hass: HomeAssistant, mqtt_mock):
    """MQTT mock that also stops MQTT's internal periodic timer on teardown.

    The mocked paho client never reports a socket close, so the MQTT client's
    own periodic "misc" timer would otherwise be reported as a lingering timer
    by the test harness, even for a test that does nothing.
    """
    yield mqtt_mock
    client = getattr(hass.data.get("mqtt"), "client", None)
    timer = getattr(client, "_misc_timer", None)
    if timer is not None:
        timer.cancel()
        client._misc_timer = None


def make_entry(
    options: dict[str, Any] | None = None,
    title: str = "Novolto Test",
    data: dict[str, Any] | None = None,
    unique_id: str = BASE,
    entry_id: str = ENTRY_ID,
) -> MockConfigEntry:
    """Return a (not yet added) current-version config entry for the test device."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data=data
        if data is not None
        else {CONF_CONNECTION: CONNECTION_MQTT, CONF_BASE_TOPIC: BASE, CONF_SERIAL: BASE},
        options={**default_options(), **(options or {})},
        unique_id=unique_id,
        entry_id=entry_id,
        version=1,
        minor_version=2,
    )


def make_legacy_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Config entry as created by 0.5.0 (version 1.1)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Novolto Test",
        data={CONF_BASE_TOPIC: BASE},
        options=options or {},
        unique_id=BASE,
        entry_id=ENTRY_ID,
        version=1,
        minor_version=1,
    )


async def setup_entry(
    hass: HomeAssistant,
    options: dict[str, Any] | None = None,
    enable_all: bool = False,
    **entry_kwargs: Any,
) -> MockConfigEntry:
    """Add and set up a config entry, return it.

    enable_all: also enable the entities that are disabled by default.
    """
    entry = make_entry(options, **entry_kwargs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if enable_all:
        ent_reg = er.async_get(hass)
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if ent.disabled_by:
                ent_reg.async_update_entity(ent.entity_id, disabled_by=None)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def fire(
    hass: HomeAssistant, payload: Any, topic: str = INFO_TOPIC, retain: bool = False
) -> None:
    """Fire an MQTT message; anything but str/bytes is JSON encoded."""
    if not isinstance(payload, (str, bytes)):
        payload = json.dumps(payload)
    async_fire_mqtt_message(hass, topic, payload, retain=retain)


def eid(hass: HomeAssistant, domain: str, unique_suffix: str) -> str:
    """Resolve an entity_id from its domain and unique_id suffix."""
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{BASE}_{unique_suffix}"
    )
    assert entity_id, f"no {domain} entity with unique_id {BASE}_{unique_suffix}"
    return entity_id


def unexpected_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """WARNING+ records except the generic custom-integration notice."""
    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and not any(s in r.getMessage() for s in _EXPECTED_WARNING_SNIPPETS)
    ]


def errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Messages of all ERROR+ records."""
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
