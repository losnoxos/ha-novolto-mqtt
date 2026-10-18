"""Select platform for Novolto MQTT: fixed power stages."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_POWER_STAGES, DOMAIN, FIELD_TARGET_POWER
from .device import NovoltoConfigEntry, NovoltoDevice
from .entity import NovoltoEntity

OPTION_OFF = "off"


def stage_option(watts: int) -> str:
    """Select option for a power stage: "off" for 0 W, else "<n> W"."""
    return OPTION_OFF if watts == 0 else f"{watts} W"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NovoltoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power stage select, unless no stages are configured."""
    device = entry.runtime_data
    if device.option(CONF_POWER_STAGES, None):
        async_add_entities([NovoltoPowerStageSelect(device)])
        return
    # Stages removed in the options: drop the entity instead of leaving it
    # behind as "unavailable".
    ent_reg = er.async_get(hass)
    unique_id = f"{device.key}_{NovoltoPowerStageSelect._unique_suffix}"
    if entity_id := ent_reg.async_get_entity_id("select", DOMAIN, unique_id):
        ent_reg.async_remove(entity_id)


class NovoltoPowerStageSelect(NovoltoEntity, SelectEntity):
    """Choose one of the configured power stages; writes `spp`.

    Shows no option while the target power matches none of the stages
    (e.g. a PV surplus automation set 730 W).
    """

    _attr_translation_key = "power_stage"
    _unique_suffix = "power_stage"

    def __init__(self, device: NovoltoDevice) -> None:
        """Initialize with the stages from the options."""
        super().__init__(device)
        self._stages: dict[str, int] = {
            stage_option(watts): watts for watts in device.option(CONF_POWER_STAGES, [])
        }
        self._attr_options = list(self._stages)

    @property
    def current_option(self) -> str | None:
        """Stage matching the current target power, if any."""
        target = self.device.get(FIELD_TARGET_POWER)
        if target is None:
            return None
        option = stage_option(int(target))
        return option if option in self._stages and target == int(target) else None

    async def async_select_option(self, option: str) -> None:
        """Send the stage's power as new target power."""
        await self.device.async_set_target_power(self._stages[option])
