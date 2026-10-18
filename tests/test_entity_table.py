"""Dump all created entities (run with -s to see the table)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .conftest import BASE, FULL_TELEGRAM, fire, setup_entry


async def test_entity_table(hass: HomeAssistant, mqtt_mock) -> None:
    entry = await setup_entry(hass)
    ent_reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if e.disabled_by:
            ent_reg.async_update_entity(e.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    fire(hass, FULL_TELEGRAM)
    await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    assert len(devices) == 1
    dev = devices[0]
    print(f"\nDEVICE: name={dev.name!r} manufacturer={dev.manufacturer!r} "
          f"serial={dev.serial_number!r} identifiers={dev.identifiers}")

    rows = []
    for e in sorted(er.async_entries_for_config_entry(ent_reg, entry.entry_id),
                    key=lambda x: x.entity_id):
        st = hass.states.get(e.entity_id)
        assert st is not None, e.entity_id
        a = dict(st.attributes)
        rows.append((
            e.entity_id, e.unique_id.removeprefix(BASE + "_"), st.state,
            a.pop("unit_of_measurement", ""), a.pop("device_class", ""),
            a.pop("state_class", ""), e.entity_category or "",
            "disabled-by-default" if e.unique_id.endswith(("_rssi", "_msi")) else "",
            {k: v for k, v in a.items() if k not in ("friendly_name",)},
            st.attributes.get("friendly_name"),
        ))
    print("| entity_id | uid | state | unit | device_class | state_class | category | default | friendly_name | other attributes |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x) for x in (*r[:8], r[9], r[8])) + " |")
    assert len(rows) == 21
    ids = {r[0] for r in rows}
    assert "water_heater.novolto_test" in ids
    assert "binary_sensor.novolto_test_heating" in ids
