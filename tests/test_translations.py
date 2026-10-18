"""Every text exists in strings.json, en.json and de.json (same keys)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "novolto_mqtt"


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _flatten(node: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


STRINGS = _flatten(_load("strings.json"))
EN = _flatten(_load("translations/en.json"))
DE = _flatten(_load("translations/de.json"))
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in COMPONENT.glob("*.py"))


def test_same_keys_everywhere() -> None:
    assert set(STRINGS) == set(EN) == set(DE)
    assert STRINGS == EN


@pytest.mark.parametrize("key", sorted(STRINGS))
def test_placeholders_match(key: str) -> None:
    placeholders = lambda text: set(re.findall(r"\{(\w+)\}", text))  # noqa: E731
    assert placeholders(EN[key]) == placeholders(DE[key]), key


def test_no_empty_texts() -> None:
    assert all(EN.values()) and all(DE.values())


def _keys(section: str) -> set[str]:
    return {k.split(".")[2] for k in EN if k.startswith(section)}


def test_flow_keys_used_in_code_exist() -> None:
    errors = set(re.findall(r'errors\[[^\]]+\] = "(\w+)"', SOURCE))
    aborts = set(re.findall(r'reason="(\w+)"', SOURCE)) | {
        "mqtt_not_configured", "mqtt_not_connected", "already_in_progress",
        "reconfigure_successful",
    }
    config_errors = _keys("config.error.")
    option_errors = _keys("options.error.")
    assert errors <= config_errors | option_errors, errors - config_errors - option_errors
    assert {"stages_invalid", "stages_no_zero"} <= option_errors
    fix_flow_aborts = {
        k.rsplit(".", 1)[1] for k in EN if ".fix_flow.abort." in k
    }
    assert aborts <= _keys("config.abort.") | fix_flow_aborts, aborts - _keys(
        "config.abort."
    )


def test_step_ids_have_texts() -> None:
    steps = set(re.findall(r'step_id="(\w+)"', SOURCE))
    steps |= {"discovery_confirm", "discovery_confirm_offline", "no_data_case"}
    config_steps = _keys("config.step.")
    option_steps = _keys("options.step.")
    option_steps |= {k.split(".")[4] for k in EN if ".fix_flow.step." in k}
    progress_only = {"search", "listen", "reconfigure_listen"}
    assert steps - progress_only <= config_steps | option_steps
    assert {k.split(".")[2] for k in EN if k.startswith("config.progress.")} == {
        "search", "listen"
    }


def test_translation_keys_used_in_code_exist() -> None:
    keys = set(re.findall(r'translation_key(?:=|\s*=\s*)"(\w+)"', SOURCE))
    keys |= set(re.findall(r'_attr_translation_key = "(\w+)"', SOURCE))
    known = {k.split(".")[2] for k in EN if k.startswith(("entity.", "exceptions.", "issues."))}
    known |= {k.split(".")[1] for k in EN if k.startswith(("exceptions.", "issues."))}
    known |= {"model"}  # selector
    assert keys <= known, keys - known


def test_status_states_translated() -> None:
    from custom_components.novolto_mqtt.const import (
        STATUS_BITS,
        STATUS_OK,
        STATUS_UNKNOWN_BIT,
    )

    for state in (STATUS_OK, STATUS_UNKNOWN_BIT, *STATUS_BITS.values()):
        assert f"entity.sensor.status.state.{state}" in DE


def test_no_real_serials_in_texts() -> None:
    """Only obviously made-up example serials (AAA.BBB.123456)."""
    for text in (*EN.values(), *DE.values()):
        for serial in re.findall(r"\b[A-Z0-9]{2,}\.[A-Z0-9]{2,}\.\d+\b", text):
            assert serial == "AAA.BBB.123456", serial
