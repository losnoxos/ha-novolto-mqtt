"""Device logic for one Novolto heating rod (= one config entry).

Independent of the transport: values, availability, energy counter,
setting checks and repair issues live here; receiving and sending is done by
a `NovoltoConnection` (connection.py).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .connection import NovoltoConnection
from .const import (
    CONF_AWAITING_DATA,
    CONF_BASE_TOPIC,
    CONF_HEATING_THRESHOLD_W,
    CONF_MODEL,
    CONF_SERIAL,
    CONF_SPTW_MAX,
    CONF_SPTW_MIN,
    CONF_TIMEOUT_SECONDS,
    CONTROL_NAME_TARGET_POWER,
    CONTROL_NAME_TARGET_WATER_TEMP,
    CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS,
    DEFAULT_HEATING_THRESHOLD_W,
    DEFAULT_MODEL,
    DEFAULT_SPTW_MAX,
    DEFAULT_SPTW_MIN,
    DEFAULT_TIMEOUT_SECONDS,
    DISCOVERY_REQUIRED_FIELDS,
    DOMAIN,
    ENERGY_SAVE_DELAY_SECONDS,
    FIELD_MSI,
    FIELD_POWER,
    FIELD_RET,
    FIELD_S_ERR,
    FIELD_SERIAL,
    FIELD_STATUS,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP,
    FIELD_TARGET_WATER_TEMP_HYSTERESIS,
    FIELD_UNIX_TIME,
    INTEGER_FIELDS,
    ISSUE_NO_DATA,
    ISSUE_SETTING_REJECTED,
    ISSUE_STALE_RETAINED,
    ISSUE_STB_TRIPPED,
    MEASUREMENT_FIELDS,
    MODELS,
    MSI_TIMEOUT_FACTOR,
    NO_DATA_ISSUE_MINUTES,
    SIGNAL_UPDATE,
    STATUS_STB_TRIPPED,
    ModelSpec,
)

_LOGGER = logging.getLogger(__name__)

type NovoltoConfigEntry = ConfigEntry[NovoltoDevice]

# Setting name on the control topic -> telegram field that reports it back.
_SETTING_FIELDS = {
    CONTROL_NAME_TARGET_POWER: FIELD_TARGET_POWER,
    CONTROL_NAME_TARGET_WATER_TEMP: FIELD_TARGET_WATER_TEMP,
    CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS: FIELD_TARGET_WATER_TEMP_HYSTERESIS,
}


def as_number(value: Any, as_int: bool) -> float | int | None:
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


def is_ack(payload: dict[str, Any]) -> bool:
    """Acknowledgement of a setting: no measurement field at all."""
    return MEASUREMENT_FIELDS.isdisjoint(payload)


def is_novolto_telegram(payload: Any) -> bool:
    """Strict check whether a payload is a Novolto measurement telegram.

    Used by discovery and search, which see every message on a topic pattern
    (other devices, acks, garbage): recognised by content, not topic name.
    """
    if not isinstance(payload, dict) or not DISCOVERY_REQUIRED_FIELDS <= payload.keys():
        return False
    serial = payload[FIELD_SERIAL]
    return (
        isinstance(serial, str)
        and 0 < len(serial) <= 64
        and serial.strip() == serial
        and not any(c in serial for c in "/+#")
        and as_number(payload[FIELD_MSI], True) is not None
    )


def clean_telegram(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert all telegram values to numbers (or None), keep the serial."""
    return {
        key: value if key == FIELD_SERIAL else as_number(value, key in INTEGER_FIELDS)
        for key, value in payload.items()
    }


def telegram_age(payload: dict[str, Any]) -> float | None:
    """Age of a telegram in seconds from its device timestamp (display only)."""
    unix_time = as_number(payload.get(FIELD_UNIX_TIME), True)
    if unix_time is None:
        return None
    return max(dt_util.utcnow().timestamp() - unix_time, 0.0)


@dataclass
class _Pending:
    """A setting sent to the device but not yet confirmed by a telegram."""

    value: float | int
    sent: datetime
    acked: bool = False


class NovoltoDevice:
    """Runtime state and logic for one Novolto device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NovoltoConfigEntry,
        connection: NovoltoConnection,
        store: Store,
        energy_kwh: float = 0.0,
        on_serial_learned: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the device."""
        self.hass = hass
        self.entry = entry
        self.connection = connection
        self.store = store
        self.energy_kwh = energy_kwh
        self.data: dict[str, Any] = {}
        self.last_seen: datetime | None = None
        self.last_telegram_retained = False
        # unix_time of the retained telegram replayed on subscribe; compared
        # with the first live telegram to detect an outdated stored message.
        self._retained_unix_time: int | None = None
        self._retained_checked = False
        self._on_serial_learned = on_serial_learned
        self._was_available = False
        self._last_energy_ts: datetime | None = None
        self._last_power_w: float | None = None
        self._save_pending = False
        self._pending: dict[str, _Pending] = {}
        self._no_data_unsub: CALLBACK_TYPE | None = None
        self._serial_mismatch_logged = False
        self._serial_offered = False

    # --- identity and configuration ---

    @property
    def base_topic(self) -> str:
        """MQTT topic prefix of the device."""
        return self.entry.data[CONF_BASE_TOPIC]

    @property
    def key(self) -> str:
        """Stable identifier: the serial number.

        Until the serial is known (device added without data, entry migrated
        from 0.5.0) it is the entry's unique_id, i.e. the topic it was set up
        with - not the current topic, which "Reconfigure" can change.
        """
        return (
            self.entry.data.get(CONF_SERIAL) or self.entry.unique_id or self.base_topic
        )

    @property
    def name(self) -> str:
        """User-facing device name."""
        return self.entry.title

    @property
    def model(self) -> ModelSpec:
        """Model-specific defaults (max power, stages)."""
        return MODELS.get(self.entry.options.get(CONF_MODEL), MODELS[DEFAULT_MODEL])

    def option(self, key: str, default: Any) -> Any:
        """Read one option."""
        return self.entry.options.get(key, default)

    @property
    def heating_threshold(self) -> float:
        """Measured power above which the rod counts as heating."""
        return self.option(CONF_HEATING_THRESHOLD_W, DEFAULT_HEATING_THRESHOLD_W)

    @property
    def signal(self) -> str:
        """Dispatcher signal used to notify this device's entities."""
        return SIGNAL_UPDATE.format(entry_id=self.entry.entry_id)

    def _issue_id(self, issue: str) -> str:
        return f"{issue}_{self.entry.entry_id}"

    # --- values ---

    def get(self, field: str) -> Any:
        """Current value of a field, including settings just sent."""
        if (pending := self._pending.get(field)) is not None:
            return pending.value
        return self.data.get(field)

    @property
    def effective_timeout(self) -> float:
        """Timeout before the device counts as gone, respecting its `msi`."""
        timeout = self.option(CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)
        msi = self.data.get(FIELD_MSI)
        if isinstance(msi, (int, float)) and msi > 0:
            return max(timeout, MSI_TIMEOUT_FACTOR * msi)
        return timeout

    @property
    def available(self) -> bool:
        """Whether the device has sent a live telegram recently enough.

        Retained telegrams (replayed by the broker on subscribe, possibly
        from yesterday) never make the device available.
        """
        if self.last_seen is None:
            return False
        age = (dt_util.utcnow() - self.last_seen).total_seconds()
        return age <= self.effective_timeout

    # --- lifecycle ---

    async def async_start(self) -> Callable[[], None]:
        """Start receiving; returns a callable that stops everything."""
        unsubscribe = await self.connection.async_start(self.handle_payload)
        if self.entry.data.get(CONF_AWAITING_DATA):
            self._no_data_unsub = async_call_later(
                self.hass,
                timedelta(minutes=NO_DATA_ISSUE_MINUTES),
                self._async_no_data_timeout,
            )

        @callback
        def _stop() -> None:
            unsubscribe()
            if self._no_data_unsub:
                self._no_data_unsub()
                self._no_data_unsub = None

        return _stop

    @callback
    def _async_no_data_timeout(self, _now: datetime) -> None:
        self._no_data_unsub = None
        if self.entry.data.get(CONF_AWAITING_DATA):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(ISSUE_NO_DATA),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_DATA,
                translation_placeholders={
                    "name": self.name,
                    "minutes": str(NO_DATA_ISSUE_MINUTES),
                },
            )

    # --- incoming payloads ---

    @callback
    def handle_payload(self, payload: dict[str, Any], retained: bool) -> None:
        """Handle a decoded payload from the connection."""
        if is_ack(payload):
            if not retained:
                self._handle_ack(payload)
            return

        cleaned = clean_telegram(payload)
        _LOGGER.debug(
            "Novolto %s telegram (retained=%s): %s", self.base_topic, retained, payload
        )
        self.data.update(cleaned)
        self.last_telegram_retained = retained

        if retained:
            # Replayed by the broker on subscribe: show the values, but the
            # device is not known to be alive - no availability, no energy.
            self._last_energy_ts = None
            self._last_power_w = None
            if not self._retained_checked:
                self._retained_unix_time = cleaned.get(FIELD_UNIX_TIME)
            async_dispatcher_send(self.hass, self.signal)
            return

        now = dt_util.utcnow()
        if self.entry.data.get(CONF_AWAITING_DATA):
            self._first_data_received()
        self._check_serial(cleaned.get(FIELD_SERIAL))
        if not self._was_available:
            _LOGGER.info("Novolto %s is now reporting data", self.base_topic)
            self._was_available = True
        if FIELD_POWER in cleaned:
            self._update_energy(cleaned[FIELD_POWER], now)
        self._confirm_pending(cleaned)
        self._update_stb_issue(cleaned.get(FIELD_STATUS))
        self._check_stored_message(cleaned.get(FIELD_UNIX_TIME))
        self.last_seen = now
        async_dispatcher_send(self.hass, self.signal)

    def _check_serial(self, serial: Any) -> None:
        if not isinstance(serial, str) or not serial:
            return
        known = self.entry.data.get(CONF_SERIAL)
        if known is None:
            # Offered once per run; a refused adoption is logged once.
            if self._on_serial_learned is not None and not self._serial_offered:
                self._serial_offered = True
                self._on_serial_learned(serial)
        elif serial != known and not self._serial_mismatch_logged:
            self._serial_mismatch_logged = True
            _LOGGER.warning(
                "Topic %s now carries data of another device (%s); expected %s",
                self.base_topic,
                serial,
                known,
            )

    def _first_data_received(self) -> None:
        if self._no_data_unsub:
            self._no_data_unsub()
            self._no_data_unsub = None
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(ISSUE_NO_DATA))
        data = {k: v for k, v in self.entry.data.items() if k != CONF_AWAITING_DATA}
        self.hass.config_entries.async_update_entry(self.entry, data=data)

    def _handle_ack(self, payload: dict[str, Any]) -> None:
        ret = payload.get(FIELD_RET)
        if ret is None:
            return
        if ret == 0:
            for pending in self._pending.values():
                pending.acked = True
            ir.async_delete_issue(
                self.hass, DOMAIN, self._issue_id(ISSUE_SETTING_REJECTED)
            )
            return
        s_err = str(payload.get(FIELD_S_ERR, ""))
        _LOGGER.warning("Novolto %s rejected a setting: %s", self.base_topic, s_err)
        # The ack doesn't say which setting failed: show the device's real
        # values again for everything still open.
        self._pending.clear()
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(ISSUE_SETTING_REJECTED),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SETTING_REJECTED,
            translation_placeholders={
                "name": self.name,
                "ret": str(ret),
                "s_err": s_err,
            },
        )
        async_dispatcher_send(self.hass, self.signal)

    def _confirm_pending(self, cleaned: dict[str, Any]) -> None:
        """Drop optimistic values the device has confirmed (or acked)."""
        for field in list(self._pending):
            pending = self._pending[field]
            if pending.acked or cleaned.get(field) == pending.value:
                del self._pending[field]

    def _check_stored_message(self, live_unix_time: Any) -> None:
        """First live telegram: is the retained one replayed before outdated?

        A large gap between the retained and the first live telegram has two
        causes: retain switched off on the device (the old message lingers),
        or the device was simply offline while Home Assistant started (retain
        on, the broker held the last state before switching off). To tell
        them apart, the stored message is fetched again once live data
        arrived: with retain on it is now the live telegram, with retain off
        still the old one. Timestamps are the device's own, so a wrong device
        clock doesn't matter.
        """
        if self._retained_checked:
            return
        self._retained_checked = True
        retained = self._retained_unix_time
        if not isinstance(live_unix_time, int):
            return
        if retained is None or live_unix_time - retained <= self.effective_timeout:
            # Nothing stored, or the stored telegram is current.
            ir.async_delete_issue(
                self.hass, DOMAIN, self._issue_id(ISSUE_STALE_RETAINED)
            )
            return
        self.entry.async_create_background_task(
            self.hass,
            self._async_recheck_stored_message(live_unix_time),
            f"{DOMAIN}_recheck_stored_message",
        )

    async def _async_recheck_stored_message(self, live_unix_time: int) -> None:
        stored = await self.connection.async_fetch_stored_message()
        stored_time = (
            as_number(stored.get(FIELD_UNIX_TIME), True) if stored else None
        )
        issue_id = self._issue_id(ISSUE_STALE_RETAINED)
        if stored_time is None or live_unix_time - stored_time <= self.effective_timeout:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        stored_at = dt_util.as_local(dt_util.utc_from_timestamp(stored_time))
        date_format = (
            "%d.%m.%Y %H:%M"
            if (self.hass.config.language or "").startswith("de")
            else "%Y-%m-%d %H:%M"
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STALE_RETAINED,
            translation_placeholders={
                "name": self.name,
                "date": stored_at.strftime(date_format),
            },
            data={"entry_id": self.entry.entry_id},
        )

    async def async_clear_stored_message(self) -> None:
        """Delete the outdated stored message (repair fix)."""
        await self.connection.async_clear_stored_message()
        ir.async_delete_issue(
            self.hass, DOMAIN, self._issue_id(ISSUE_STALE_RETAINED)
        )

    def _update_stb_issue(self, status: Any) -> None:
        if not isinstance(status, int) or status < 0:
            return
        issue_id = self._issue_id(ISSUE_STB_TRIPPED)
        if status & STATUS_STB_TRIPPED:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.CRITICAL,
                translation_key=ISSUE_STB_TRIPPED,
                translation_placeholders={"name": self.name},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

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
    def handle_availability_tick(self, now: datetime) -> None:
        """Mark the device unavailable once it has gone silent."""
        # Unconfirmed optimistic values don't outlive the timeout.
        for field in list(self._pending):
            if (now - self._pending[field].sent).total_seconds() > self.effective_timeout:
                del self._pending[field]
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
            self._pending.clear()
            async_dispatcher_send(self.hass, self.signal)

    # --- settings ---

    @property
    def min_temp(self) -> float:
        """Lowest settable target water temperature."""
        return self.option(CONF_SPTW_MIN, DEFAULT_SPTW_MIN)

    @property
    def max_temp(self) -> float:
        """Highest settable target water temperature."""
        return self.option(CONF_SPTW_MAX, DEFAULT_SPTW_MAX)

    async def async_set_target_temperature(self, temperature: float) -> None:
        """Send a new target water temperature (checked against the limits).

        Sent as a float - the firmware rejects `sptw` as int with ret=13.
        """
        if not self.min_temp <= temperature <= self.max_temp:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_out_of_range",
                translation_placeholders={
                    "temperature": f"{temperature:g}",
                    "min_temp": f"{self.min_temp:g}",
                    "max_temp": f"{self.max_temp:g}",
                },
            )
        await self._async_send(CONTROL_NAME_TARGET_WATER_TEMP, float(temperature))

    async def async_set_target_power(self, power: float) -> None:
        """Send a new power setpoint (int - firmware rejects a float)."""
        await self._async_send(CONTROL_NAME_TARGET_POWER, int(round(power)))

    async def async_set_hysteresis(self, hysteresis: float) -> None:
        """Send a new hysteresis (float - firmware rejects an int)."""
        await self._async_send(
            CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS, float(hysteresis)
        )

    async def _async_send(self, name: str, value: float | int) -> None:
        if not self.available:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_offline",
                translation_placeholders={"name": self.name},
            )
        field = _SETTING_FIELDS[name]
        # Show the new value right away; the next telegram (or a rejection)
        # replaces it with what the device really uses. Registered before
        # publishing, so a rejection arriving during the publish reverts it.
        self._pending[field] = pending = _Pending(value, dt_util.utcnow())
        try:
            await self.connection.async_send_setting(name, value)
        except Exception:
            if self._pending.get(field) is pending:
                del self._pending[field]
            raise
        async_dispatcher_send(self.hass, self.signal)

    # --- cleanup ---

    @callback
    def async_delete_issues(self) -> None:
        """Remove all repair issues of this device."""
        async_delete_issues(self.hass, self.entry.entry_id)


@callback
def async_delete_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove all repair issues of a config entry."""
    for issue in (
        ISSUE_NO_DATA,
        ISSUE_SETTING_REJECTED,
        ISSUE_STB_TRIPPED,
        ISSUE_STALE_RETAINED,
    ):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry_id}")
