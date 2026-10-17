"""Runtime state for one Novolto device (= one config entry)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONTROL_MODULE_SENSOR,
    ENERGY_SAVE_DELAY_SECONDS,
    FIELD_MSI,
    FIELD_POWER,
    FIELD_RET,
    FIELD_S_ERR,
    FIELD_SERIAL,
    INTEGER_FIELDS,
    MEASUREMENT_FIELDS,
    MSI_TIMEOUT_FACTOR,
    SIGNAL_UPDATE,
)

_LOGGER = logging.getLogger(__name__)

type NovoltoConfigEntry = ConfigEntry[NovoltoDevice]


def _as_number(value: Any, as_int: bool) -> float | int | None:
    """Return a numeric telegram value, or None if it isn't one.

    Guards every entity (and the energy integration) against firmware
    variants that send null, strings or booleans for numeric fields.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN/inf
        return None
    if as_int:
        return int(number) if float(number).is_integer() else None
    return number


@dataclass
class NovoltoDevice:
    """Runtime state for one Novolto device."""

    hass: HomeAssistant
    entry: NovoltoConfigEntry
    base_topic: str
    topic_control: str
    timeout_seconds: int
    store: Store
    data: dict[str, Any] = field(default_factory=dict)
    last_seen: datetime | None = None
    energy_kwh: float = 0.0
    _was_available: bool = False
    _last_energy_ts: datetime | None = None
    _last_power_w: float | None = None
    _save_pending: bool = False

    @property
    def signal(self) -> str:
        """Dispatcher signal used to notify this device's entities."""
        return SIGNAL_UPDATE.format(entry_id=self.entry.entry_id)

    @property
    def effective_timeout(self) -> float:
        """Timeout before the device counts as gone, respecting its `msi`."""
        msi = self.data.get(FIELD_MSI)
        if isinstance(msi, (int, float)) and msi > 0:
            return max(self.timeout_seconds, MSI_TIMEOUT_FACTOR * msi)
        return self.timeout_seconds

    @property
    def available(self) -> bool:
        """Whether the device has sent a telegram recently enough."""
        if self.last_seen is None:
            return False
        age = (dt_util.utcnow() - self.last_seen).total_seconds()
        return age <= self.effective_timeout

    @callback
    def handle_message(self, msg: mqtt.ReceiveMessage) -> None:
        """Handle a payload on <base_topic>/<info suffix>."""
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.debug("Ignoring non-JSON payload on %s", msg.topic)
            return

        if not isinstance(payload, dict):
            return

        # Control-command acknowledgements land on the very same info topic
        # (serial/unix_time/ret/s_err only, no measurement fields) - treat
        # them as a distinct message, not a (partial) telegram, so we never
        # blank out sensors that simply weren't part of this payload.
        if MEASUREMENT_FIELDS.isdisjoint(payload):
            if payload.get(FIELD_RET) not in (0, None):
                _LOGGER.warning(
                    "Novolto %s rejected a setting: %s",
                    self.base_topic,
                    payload.get(FIELD_S_ERR, payload),
                )
            return

        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if key == FIELD_SERIAL:
                cleaned[key] = value
            else:
                cleaned[key] = _as_number(value, key in INTEGER_FIELDS)

        _LOGGER.debug("Novolto %s telegram: %s", self.base_topic, payload)
        now = dt_util.utcnow()
        self.data.update(cleaned)
        if not self._was_available:
            _LOGGER.info("Novolto %s is now reporting data", self.base_topic)
            self._was_available = True
        if FIELD_POWER in cleaned:
            self._update_energy(cleaned[FIELD_POWER], now)
        self.last_seen = now
        async_dispatcher_send(self.hass, self.signal)

    def _update_energy(self, power_w: float | None, now: datetime) -> None:
        """Integrate measured power into a persistent energy counter.

        Trapezoidal integration between consecutive `avp` samples. Intervals
        longer than the availability timeout (device or broker outage) are
        not integrated at all - the real consumption during a gap is unknown,
        and booking it at either sample's power would invent energy. Negative
        power is clamped to 0 so the total_increasing counter never drops.
        """
        if power_w is None:
            # Unknown sample: break the integration chain instead of guessing.
            self._last_energy_ts = None
            self._last_power_w = None
            return

        power_w = max(float(power_w), 0.0)
        if self._last_energy_ts is not None and self._last_power_w is not None:
            elapsed = (now - self._last_energy_ts).total_seconds()
            if 0 < elapsed <= self.effective_timeout:
                avg_w = (self._last_power_w + power_w) / 2
                self.energy_kwh += avg_w / 1000 * elapsed / 3600
                # async_delay_save is a debounce (each call restarts the
                # delay), so only schedule when no write is pending -
                # otherwise frequent telegrams would postpone it forever.
                if not self._save_pending:
                    self._save_pending = True
                    self.store.async_delay_save(
                        self._energy_data, ENERGY_SAVE_DELAY_SECONDS
                    )
        self._last_energy_ts = now
        self._last_power_w = power_w

    @callback
    def _energy_data(self) -> dict[str, float]:
        """Data written to the Store (evaluated at write time)."""
        self._save_pending = False
        return {"energy_kwh": self.energy_kwh}

    async def async_save_energy(self) -> None:
        """Persist the accumulated energy value to disk right now."""
        await self.store.async_save(self._energy_data())

    @callback
    def handle_availability_tick(self, _now: datetime) -> None:
        """Mark the device unavailable once it has gone silent."""
        if self._was_available and not self.available:
            _LOGGER.warning(
                "Novolto %s stopped reporting data (last telegram %s ago, "
                "timeout is %ss) - marking unavailable",
                self.base_topic,
                (dt_util.utcnow() - self.last_seen) if self.last_seen else "never",
                self.effective_timeout,
            )
            self._was_available = False
            self._last_energy_ts = None
            self._last_power_w = None
            async_dispatcher_send(self.hass, self.signal)

    async def async_set_value(self, name: str, value: Any) -> None:
        """Publish a setting change to <base_topic>/<topic_control>.

        Per-field JSON type matters here and is not uniform: the firmware
        rejects the wrong JSON type with ret=13. Callers pass an
        already-correctly-typed value (int for spp, float for sptw/sptwh).
        """
        payload = {CONTROL_MODULE_SENSOR: [{"name": name, "value": value}]}
        await mqtt.async_publish(self.hass, self.topic_control, json.dumps(payload))
