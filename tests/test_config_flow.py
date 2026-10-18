"""Config flow tests: discovery, search, manual topic, reconfigure."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

from custom_components.novolto_mqtt.config_flow import (
    normalize_topic,
    parse_power_stages,
)
from custom_components.novolto_mqtt.const import (
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_CONNECTION,
    CONF_SERIAL,
    DOMAIN,
    default_options,
)

from .conftest import BASE, FULL_TELEGRAM, fire, make_entry

FLOW = "custom_components.novolto_mqtt.config_flow"
OTHER = "TEST.SERIAL.000002"


@pytest.fixture(autouse=True)
def short_listen():
    """Listen for a fraction of a second instead of 35 s."""
    with patch(f"{FLOW}.LISTEN_SECONDS", 0.2):
        yield


def telegram(serial: str = BASE, age: float = 0, **extra: Any) -> dict[str, Any]:
    return {**FULL_TELEGRAM, "serial": serial, "unix_time": int(time.time() - age), **extra}


async def start_user(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def finish_progress(hass: HomeAssistant, result, messages=()):
    """Feed messages while a progress step listens, return the next step."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS, result
    await asyncio.sleep(0.01)  # let the listener subscribe
    for topic, payload, retain in messages:
        fire(hass, payload, topic=topic, retain=retain)
    await hass.async_block_till_done()
    await asyncio.sleep(0.25)
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


def other_entry(title: str = "Other", topic: str = OTHER):
    return make_entry(
        title=title,
        data={CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: topic, CONF_SERIAL: OTHER},
        unique_id=OTHER,
        entry_id="other_entry",
    )


async def discover(hass: HomeAssistant, payload: Any, topic: str = f"{BASE}/info"):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic=topic,
            payload=json.dumps(payload) if not isinstance(payload, str) else payload,
            qos=0,
            retain=False,
            subscribed_topic="+/info",
            timestamp=time.time(),
        ),
    )


# --- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"  {BASE}  ", BASE),
        (f"/{BASE}/", BASE),
        (f"{BASE}/info", BASE),
        (f"{BASE}/#", BASE),
        (f"{BASE}/+", BASE),
        (f"{BASE}/info/#", BASE),
        (f"{BASE}/control/", BASE),
        ("haus/heizstab/info", "haus/heizstab"),
    ],
)
def test_normalize_topic(raw: str, expected: str) -> None:
    assert normalize_topic(raw) == expected


@pytest.mark.parametrize(
    ("raw", "max_power", "expected"),
    [
        ("0, 1000, 2000, 3000", 3000, [0, 1000, 2000, 3000]),
        ("3000;0, 1000,1000", 3000, [0, 1000, 3000]),
        ("", 3000, []),
        ("0, 2000", 3000, [0, 2000]),
        ("0, 2400", 2300, "stages_invalid"),
        ("0, -5", 3000, "stages_invalid"),
        ("0, 1.5", 3000, "stages_invalid"),
        ("0, abc", 3000, "stages_invalid"),
        ("1000, 2000", 3000, "stages_no_zero"),
    ],
)
def test_parse_power_stages(raw, max_power, expected) -> None:
    assert parse_power_stages(raw, max_power) == expected


# --- pre-checks ------------------------------------------------------------


async def test_user_aborts_without_mqtt(hass: HomeAssistant) -> None:
    result = await start_user(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"
    placeholders = result["description_placeholders"]
    assert placeholders["addon_url"].startswith("https://my.home-assistant.io/")
    assert placeholders["mqtt_url"].startswith("https://my.home-assistant.io/")
    assert "README.md#step-2" in placeholders["readme_url"]


async def test_user_aborts_when_mqtt_not_connected(hass: HomeAssistant, mqtt_mock) -> None:
    with patch(f"{FLOW}.mqtt.is_connected", return_value=False):
        result = await start_user(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_connected"


async def test_readme_link_german(hass: HomeAssistant) -> None:
    hass.config.language = "de"
    result = await start_user(hass)
    assert "README.de.md#schritt-2" in result["description_placeholders"]["readme_url"]


# --- way 1: discovery ------------------------------------------------------


async def test_discovery_creates_entry(hass: HomeAssistant, mqtt_mock) -> None:
    result = await discover(hass, telegram(age=5))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    placeholders = result["description_placeholders"]
    assert placeholders["serial"] == BASE
    assert placeholders["water_temp"] == "23.8"
    assert placeholders["age"].endswith(" s")
    assert placeholders["default_sptw_max"] == "50"
    assert placeholders["default_sptwh_max"] == "10"
    assert placeholders["stages_p3000"] == "0, 1000, 2000, 3000"
    assert placeholders["stages_p2300"] == "0, 1000, 2000, 2300"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Heizstab Keller", "model": "p3000"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.title == "Heizstab Keller"
    assert entry.unique_id == BASE
    assert entry.data == {CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: BASE, CONF_SERIAL: BASE}
    assert entry.options == default_options("p3000")
    assert (entry.version, entry.minor_version) == (1, 2)
    assert entry.state is ConfigEntryState.LOADED


async def test_discovery_old_telegram_shows_offline_note(
    hass: HomeAssistant, mqtt_mock
) -> None:
    result = await discover(hass, telegram(age=3 * 3600))
    assert result["step_id"] == "discovery_confirm_offline"
    assert result["description_placeholders"]["age"] == "3 h"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Novolto P3000", "model": "p3000"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_discovery_custom_topic(hass: HomeAssistant, mqtt_mock) -> None:
    """Topic differs from the serial: entry keyed by serial, topic stored."""
    result = await discover(hass, telegram(), topic="heizstab/info")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Novolto P3000", "model": "p3000"}
    )
    entry = result["result"]
    assert entry.unique_id == BASE
    assert entry.data[CONF_BASE_TOPIC] == "heizstab"


@pytest.mark.parametrize(
    "payload",
    [
        "garbage",
        {"serial": BASE, "unix_time": 1, "ret": 0},  # an ack
        {"temperature": 21.5},  # some other device
        {**FULL_TELEGRAM, "serial": ""},
        {**FULL_TELEGRAM, "serial": "a/b"},
        {**FULL_TELEGRAM, "serial": 12345},
        {k: v for k, v in FULL_TELEGRAM.items() if k != "msi"},
    ],
)
async def test_discovery_rejects_foreign_payloads(
    hass: HomeAssistant, mqtt_mock, payload
) -> None:
    result = await discover(hass, payload)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_novolto_device"


async def test_discovery_already_configured(hass: HomeAssistant, mqtt_mock) -> None:
    make_entry().add_to_hass(hass)
    result = await discover(hass, telegram())
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_already_in_progress(hass: HomeAssistant, mqtt_mock) -> None:
    await discover(hass, telegram())
    result = await discover(hass, telegram(avtw=30))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_name_taken_and_model_default_name(hass: HomeAssistant, mqtt_mock) -> None:
    other_entry(title="Novolto P2300").add_to_hass(hass)
    result = await discover(hass, telegram())
    assert result["data_schema"]({})["name"] == "Novolto P3000"
    # default name of the other model left in place -> follows the model,
    # which collides with the existing entry
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Novolto P3000", "model": "p2300"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "  ", "model": "p2300"}
    )
    assert result["errors"] == {"name": "name_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "novolto p2300", "model": "p2300"}
    )
    assert result["errors"] == {"name": "name_taken"}  # case-insensitive
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Garage", "model": "p2300"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["model"] == "p2300"
    assert result["options"]["power_stages"] == [0, 1000, 2000, 2300]


async def test_default_name_follows_model(hass: HomeAssistant, mqtt_mock) -> None:
    result = await discover(hass, telegram())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Novolto P3000", "model": "p2300"}
    )
    assert result["title"] == "Novolto P2300"


# --- way 2: search ---------------------------------------------------------


async def test_search_lists_devices_and_creates_entry(
    hass: HomeAssistant, mqtt_mock
) -> None:
    result = await start_user(hass)
    result = await finish_progress(
        hass,
        result,
        [
            (f"{BASE}/info", telegram(age=4), True),
            ("haus/heizstab/info", telegram(OTHER), False),
            ("zigbee2mqtt/lamp", {"state": "ON"}, False),
            (f"{BASE}/info", {"serial": BASE, "ret": 0}, False),
        ],
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select"
    assert result["description_placeholders"]["count"] == "2"
    selector = result["data_schema"].schema["device"]
    options = {o["value"]: o["label"] for o in selector.config["options"]}
    assert set(options) == {BASE, OTHER, "__manual__"}
    assert options[OTHER].startswith(f"{OTHER} (haus/heizstab) – 23.8 °C – ")
    assert options["__manual__"] == "Enter topic manually …"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": OTHER}
    )
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Garage", "model": "p3000"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BASE_TOPIC] == "haus/heizstab"
    assert result["result"].unique_id == OTHER


async def test_search_german_labels(hass: HomeAssistant, mqtt_mock) -> None:
    hass.config.language = "de"
    result = await start_user(hass)
    result = await finish_progress(hass, result, [(f"{BASE}/info", telegram(age=4), False)])
    options = result["data_schema"].schema["device"].config["options"]
    assert options[0]["label"].startswith(f"{BASE} – 23,8 °C – vor ")
    assert options[1]["label"] == "Topic selbst eingeben …"


async def test_search_skips_configured_devices(hass: HomeAssistant, mqtt_mock) -> None:
    make_entry().add_to_hass(hass)
    result = await start_user(hass)
    result = await finish_progress(hass, result, [(f"{BASE}/info", telegram(), False)])
    # only the configured device was found -> straight to manual entry
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "none_new"}


async def test_search_finds_nothing(hass: HomeAssistant, mqtt_mock) -> None:
    result = await start_user(hass)
    result = await finish_progress(hass, result, [])
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "none_found"}


async def test_search_choose_manual(hass: HomeAssistant, mqtt_mock) -> None:
    result = await start_user(hass)
    result = await finish_progress(hass, result, [(f"{BASE}/info", telegram(), False)])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device": "__manual__"}
    )
    assert result["step_id"] == "manual"
    assert result["errors"] == {}


async def test_listener_unsubscribed_after_search(hass: HomeAssistant, mqtt_mock) -> None:
    from custom_components.novolto_mqtt import config_flow

    calls: list[str] = []
    real_subscribe = config_flow.mqtt.async_subscribe

    async def tracking_subscribe(hass, topic, cb, *args, **kwargs):
        unsub = await real_subscribe(hass, topic, cb, *args, **kwargs)
        calls.append(f"sub {topic}")

        def _unsub():
            calls.append(f"unsub {topic}")
            unsub()

        return _unsub

    with patch(f"{FLOW}.mqtt.async_subscribe", tracking_subscribe):
        result = await start_user(hass)
        await finish_progress(hass, result)
    assert calls == ["sub #", "unsub #"]


async def test_aborting_search_cancels_listener(hass: HomeAssistant, mqtt_mock) -> None:
    with patch(f"{FLOW}.LISTEN_SECONDS", 30):
        result = await start_user(hass)
        await asyncio.sleep(0.01)
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress()


# --- way 3: manual topic ---------------------------------------------------


async def _to_manual(hass: HomeAssistant):
    result = await start_user(hass)
    result = await finish_progress(hass, result)
    assert result["step_id"] == "manual"
    return result


async def test_manual_topic_with_data(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": f" {BASE}/info "}
    )
    result = await finish_progress(hass, result, [(f"{BASE}/info", telegram(), False)])
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Novolto P3000", "model": "p3000"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_CONNECTION: "mqtt", CONF_BASE_TOPIC: BASE, CONF_SERIAL: BASE}


@pytest.mark.parametrize(
    "raw",
    ["mqtt://192.168.1.5", f"{BASE} (Keller)", "a b", "x/#/y", "+", "", "  /  ", "a+b"],
)
async def test_manual_topic_invalid(hass: HomeAssistant, mqtt_mock, raw) -> None:
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": raw}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"topic": "invalid_topic"}


async def test_manual_topic_already_configured(hass: HomeAssistant, mqtt_mock) -> None:
    make_entry().add_to_hass(hass)
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": BASE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_no_data_menu_and_case_hint(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": BASE.lower()}
    )
    result = await finish_progress(hass, result, [(f"{BASE}/info", telegram(), False)])
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_data_case"
    assert result["menu_options"] == ["manual", "search", "anyway"]
    assert result["description_placeholders"] == {"topic": BASE.lower(), "found": BASE}

    # correct the topic
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"
    assert result["data_schema"]({})["topic"] == BASE.lower()


async def test_manual_no_data_search_again(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": "nothing/here"}
    )
    result = await finish_progress(hass, result)
    assert result["step_id"] == "no_data"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "search"}
    )
    result = await finish_progress(hass, result, [("nothing/here/info", telegram(), False)])
    assert result["step_id"] == "select"


async def test_manual_setup_anyway(hass: HomeAssistant, mqtt_mock) -> None:
    result = await _to_manual(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": "keller/heizstab"}
    )
    result = await finish_progress(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "anyway"}
    )
    assert result["step_id"] == "anyway"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "device"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Keller", "model": "p3000"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == "keller/heizstab"
    assert entry.data == {
        CONF_CONNECTION: "mqtt",
        CONF_BASE_TOPIC: "keller/heizstab",
        CONF_SERIAL: None,
        CONF_AWAITING_DATA: True,
    }
    assert entry.state is ConfigEntryState.LOADED


# --- reconfigure -----------------------------------------------------------


async def _reconfigure(hass: HomeAssistant, entry, topic: str, messages=()):
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": topic}
    )
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        result = await finish_progress(hass, result, messages)
    return result


async def test_reconfigure_changes_topic(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await _reconfigure(
        hass, entry, "neu/heizstab/info", [("neu/heizstab/info", telegram(), False)]
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_TOPIC] == "neu/heizstab"
    assert entry.unique_id == BASE
    assert entry.state is ConfigEntryState.LOADED
    subscribed = [c.args[0] for c in mqtt_mock.async_subscribe.call_args_list]
    assert "neu/heizstab/info" in subscribed


async def test_reconfigure_other_device(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await _reconfigure(
        hass, entry, "neu", [("neu/info", telegram(OTHER), False)]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"topic": "other_device"}
    assert result["description_placeholders"] == {"serial": OTHER}
    assert entry.data[CONF_BASE_TOPIC] == BASE


async def test_reconfigure_invalid_and_in_use(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    other_entry(topic="used").add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": "a b"}
    )
    assert result["errors"] == {"topic": "invalid_topic"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"topic": "used/info"}
    )
    assert result["errors"] == {"topic": "topic_in_use"}


async def test_reconfigure_without_data_accepts(hass: HomeAssistant, mqtt_mock) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await _reconfigure(hass, entry, "spaeter")
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_TOPIC] == "spaeter"


async def test_search_aborts_when_mqtt_fails(hass: HomeAssistant, mqtt_mock) -> None:
    """MQTT gone while searching: clean abort instead of an unknown error."""
    from homeassistant.exceptions import HomeAssistantError

    async def broken_subscribe(*args, **kwargs):
        raise HomeAssistantError("MQTT not available")

    with patch(f"{FLOW}.mqtt.async_subscribe", broken_subscribe):
        result = await start_user(hass)
        await hass.async_block_till_done()
        with patch(f"{FLOW}.mqtt.is_connected", return_value=False):
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_connected"
