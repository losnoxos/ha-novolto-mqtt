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

from homeassistant.config_entries import SOURCE_IGNORE, ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
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
    FIELD_RST_CAUSE,
    FIELD_S_ERR,
    FIELD_SERIAL,
    FIELD_STATUS,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP,
    FIELD_TARGET_WATER_TEMP_HYSTERESIS,
    FIELD_UNIX_TIME,
    FIELD_VERSION,
    FOREIGN_BLOCK_TELEGRAMS,
    FOREIGN_MAX_DEVICES,
    FOREIGN_WINDOW_MAX_SECONDS,
    INTEGER_FIELDS,
    ISSUE_DUPLICATE_DEVICE,
    ISSUE_INVALID_SERIAL,
    ISSUE_NO_DATA,
    ISSUE_OTHER_DEVICE,
    ISSUE_RETAINED_CONTROL,
    ISSUE_SETTING_REJECTED,
    ISSUE_STALE_RETAINED,
    ISSUE_STB_TRIPPED,
    MEASUREMENT_FIELDS,
    MODELS,
    MSI_TIMEOUT_FACTOR,
    NO_DATA_ISSUE_MINUTES,
    RETAINED_CONTROL_MAX_CHARS,
    SERIAL_MAX_LENGTH,
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


def valid_serial(value: Any) -> bool:
    """Whether a serial number can identify a device.

    A string of 1-64 characters, without surrounding spaces and without the
    MQTT separator and wildcards. Anything else can't be told apart from
    another device and is never used.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= SERIAL_MAX_LENGTH
        and value.strip() == value
        and not any(c in value for c in "/+#")
    )


def is_novolto_telegram(payload: Any) -> bool:
    """Strict check whether a payload is a Novolto measurement telegram.

    Used by discovery and search, which see every message on a topic pattern
    (other devices, acks, garbage): recognised by content, not topic name.
    """
    if not isinstance(payload, dict) or not DISCOVERY_REQUIRED_FIELDS <= payload.keys():
        return False
    return (
        valid_serial(payload[FIELD_SERIAL])
        and as_number(payload[FIELD_MSI], True) is not None
    )


def shorten_payload(raw: str) -> str:
    """A stored command made safe for a repair text (inside backticks)."""
    text = "".join(c if c.isprintable() else " " for c in raw).replace("`", "'")
    if len(text) > RETAINED_CONTROL_MAX_CHARS:
        text = text[:RETAINED_CONTROL_MAX_CHARS] + "…"
    return text


def _parse_time(value: Any) -> datetime | None:
    """A stored UTC timestamp, None if it isn't one."""
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def other_entry_for_serial(
    hass: HomeAssistant, entry: ConfigEntry, serial: str
) -> ConfigEntry | None:
    """Another (not ignored) entry that is already set up for this serial."""
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.entry_id == entry.entry_id or other.source == SOURCE_IGNORE:
            continue
        if serial in (other.unique_id, other.data.get(CONF_SERIAL)):
            return other
    return None


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


@dataclass
class _Foreign:
    """A device with another serial number seen on the topic."""

    first: datetime
    last: datetime
    count: int
    window: float

    def active(self, now: datetime) -> bool:
        """Still counts as present on the topic."""
        return (now - self.last).total_seconds() < self.window


class NovoltoDevice:
    """Runtime state and logic for one Novolto device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NovoltoConfigEntry,
        connection: NovoltoConnection,
        store: Store,
        energy_kwh: float = 0.0,
        on_serial_learned: Callable[[str], Any] | None = None,
        stored_foreign: Any = None,
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
        # "Sends no data": the 10 minutes have passed / the issue is shown.
        self._no_data_due = False
        self._no_data_shown = False
        self._started: datetime | None = None
        # Foreign devices on the topic (serial -> record), persisted in the
        # Store together with the topic they were seen on.
        self._stored_foreign = stored_foreign
        self._foreign: dict[str, _Foreign] = {}
        self._foreign_warned: set[str] = set()
        # (issue, serial) of the foreign-device issue currently shown.
        self._foreign_issue: tuple[str, str] | None = None
        # Last live message without a valid serial (issue shown while set).
        self._invalid_last: datetime | None = None
        self._invalid_warned = False
        # Check for a retained command on the control topic.
        self._control_check_running = False
        self._control_issue = False
        self._check_control_on_return = False
        self._sw_version: str | None = None

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

        Only the device's own live telegrams count: retained telegrams
        (replayed by the broker on subscribe, possibly from yesterday) and
        other devices on the topic never make it available - nor unavailable.
        """
        if self.last_seen is None:
            return False
        age = (dt_util.utcnow() - self.last_seen).total_seconds()
        return age <= self.effective_timeout

    def own_device_may_be_active(self) -> bool:
        """Whether the own device may still be reporting.

        True while it is available, and right after the start, when its
        silence isn't proven yet. The repair "adopt" is refused then.
        """
        if self.available:
            return True
        return (
            self._started is None
            or (dt_util.utcnow() - self._started).total_seconds()
            < self.effective_timeout
        )

    # --- lifecycle ---

    async def async_start(self) -> Callable[[], None]:
        """Start receiving; returns a callable that stops everything."""
        self._started = dt_util.utcnow()
        if ir.async_get(self.hass).async_get_issue(
            DOMAIN, self._issue_id(ISSUE_INVALID_SERIAL)
        ):
            # Shown before a reload: ends after a timeout without such messages.
            self._invalid_last = self._started
        self._load_foreign(self._stored_foreign)
        self._stored_foreign = None
        unsubscribe = await self.connection.async_start(self.handle_payload)
        if self.entry.data.get(CONF_AWAITING_DATA):
            self._no_data_unsub = async_call_later(
                self.hass,
                timedelta(minutes=NO_DATA_ISSUE_MINUTES),
                self._async_no_data_timeout,
            )
        self._schedule_control_check()

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
        self._no_data_due = True
        self._update_no_data_issue()

    def _update_no_data_issue(self) -> None:
        """Show "sends no data" once due, unless a more precise issue stands.

        Messages without a valid serial or a foreign device on the topic
        explain the missing data better; their issues replace this one.
        """
        self._prune_foreign()
        show = (
            self._no_data_due
            and bool(self.entry.data.get(CONF_AWAITING_DATA))
            and self._invalid_last is None
            and not self._foreign
        )
        if show == self._no_data_shown:
            return
        self._no_data_shown = show
        issue_id = self._issue_id(ISSUE_NO_DATA)
        if not show:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
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
        """Handle a decoded payload from the connection.

        The serial is checked before anything is taken over: messages
        without a valid serial and telegrams and acks of another device
        change no value, availability, energy or setting.
        """
        serial = payload.get(FIELD_SERIAL)
        if not valid_serial(serial):
            if not retained:
                self._invalid_serial_received()
            return
        if is_ack(payload):
            if not retained and self._ack_is_ours(serial):
                self._handle_ack(payload)
            return

        _LOGGER.debug(
            "Novolto %s telegram (retained=%s): %s", self.base_topic, retained, payload
        )
        if retained:
            self._handle_retained(serial, payload)
            return
        awaiting_data = self.entry.data.get(CONF_AWAITING_DATA)
        if not self._identify(serial, payload):
            return
        cleaned = clean_telegram(payload)
        self.data.update(cleaned)
        self.last_telegram_retained = False

        now = dt_util.utcnow()
        if awaiting_data:
            self._first_data_received()
        if not self._was_available:
            _LOGGER.info("Novolto %s is now reporting data", self.base_topic)
            self._was_available = True
            if self._check_control_on_return:
                # Back after an outage: the device has just reconnected and
                # executed whatever was stored on its control topic.
                self._check_control_on_return = False
                self._schedule_control_check()
        if payload.get(FIELD_RST_CAUSE) is not None:
            # First telegram after a device restart: its consumption during
            # the restart is unknown - start a new energy chain - and it has
            # just executed whatever was stored on its control topic.
            _LOGGER.info(
                "Novolto %s restarted (%s)", self.base_topic, payload[FIELD_RST_CAUSE]
            )
            self._last_energy_ts = None
            self._last_power_w = None
            self._schedule_control_check()
        self._update_sw_version(payload.get(FIELD_VERSION))
        if FIELD_POWER in cleaned:
            self._update_energy(cleaned[FIELD_POWER], now)
        self._confirm_pending(cleaned)
        self._update_stb_issue(cleaned.get(FIELD_STATUS))
        self._check_stored_message(cleaned.get(FIELD_UNIX_TIME))
        self.last_seen = now
        async_dispatcher_send(self.hass, self.signal)

    # --- identity ---

    def _identify(self, serial: str, payload: dict[str, Any]) -> bool:
        """Whether a live telegram (valid serial) is this device's own.

        - Serial known: it must match.
        - Serial not known yet (added without data, migrated from 0.5.0): it
          is learned from the first live telegram, unless that serial is
          already set up as another entry.
        Anything else is a foreign device: reported, never taken over.
        """
        known = self.entry.data.get(CONF_SERIAL)
        if known is not None:
            if serial == known:
                return True
        elif other_entry_for_serial(self.hass, self.entry, serial) is None:
            self._forget_foreign(serial)
            if self._on_serial_learned is not None:
                self._on_serial_learned(serial)
            return True
        self._foreign_received(serial, payload)
        return False

    def _handle_retained(self, serial: str, payload: dict[str, Any]) -> None:
        """A telegram replayed by the broker on subscribe (maybe yesterday's).

        Shown only if it is the own device's and the device isn't reporting
        live: it never makes the device available, books no energy and never
        overwrites live values. Foreign ones are dropped silently.
        """
        known = self.entry.data.get(CONF_SERIAL)
        if known is None:
            # Own serial unknown: it may stem from a predecessor on the topic.
            # Only its time is kept for the outdated-message check.
            if (
                not self._retained_checked
                and other_entry_for_serial(self.hass, self.entry, serial) is None
            ):
                self._retained_unix_time = as_number(
                    payload.get(FIELD_UNIX_TIME), True
                )
            return
        if serial != known or self.available:
            return
        cleaned = clean_telegram(payload)
        self.data.update(cleaned)
        self.last_telegram_retained = True
        self._last_energy_ts = None
        self._last_power_w = None
        if not self._retained_checked:
            self._retained_unix_time = cleaned.get(FIELD_UNIX_TIME)
        async_dispatcher_send(self.hass, self.signal)

    def _ack_is_ours(self, serial: str) -> bool:
        """Whether an ack (valid serial) may confirm or reject our settings.

        Only with the own known serial - also while another device is on the
        topic. While the own serial is unknown, no ack counts.
        """
        known = self.entry.data.get(CONF_SERIAL)
        return known is not None and serial == known

    def _invalid_serial_received(self) -> None:
        """A live message without a valid serial: ignored, reported."""
        if not self._invalid_warned:
            self._invalid_warned = True
            _LOGGER.warning(
                "Topic %s carries messages without a valid serial number - ignored",
                self.base_topic,
            )
        first = self._invalid_last is None
        self._invalid_last = dt_util.utcnow()
        if not first:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(ISSUE_INVALID_SERIAL),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_INVALID_SERIAL,
            translation_placeholders={"name": self.name},
        )
        self._update_no_data_issue()

    # --- foreign devices on the topic ---

    def _foreign_received(self, serial: str, payload: dict[str, Any]) -> None:
        """A live telegram of another device: remember it, report it."""
        now = dt_util.utcnow()
        self._prune_foreign(now)
        window = self.effective_timeout
        msi = as_number(payload.get(FIELD_MSI), True)
        if msi is not None and msi > 0:
            window = max(
                window, min(MSI_TIMEOUT_FACTOR * msi, FOREIGN_WINDOW_MAX_SECONDS)
            )
        record = self._foreign.get(serial)
        if record is not None:
            record.last = now
            record.count += 1
            record.window = window
        else:
            if len(self._foreign) >= FOREIGN_MAX_DEVICES:
                del self._foreign[min(self._foreign, key=lambda s: self._foreign[s].last)]
            self._foreign[serial] = _Foreign(now, now, 1, window)
            if serial not in self._foreign_warned:
                if len(self._foreign_warned) >= FOREIGN_MAX_DEVICES:
                    self._foreign_warned.pop()
                self._foreign_warned.add(serial)
                _LOGGER.warning(
                    "Topic %s carries data of another device (%s) - ignored; "
                    "if it keeps sending, no settings are sent to the topic "
                    "except switching off",
                    self.base_topic,
                    serial,
                )
        self._schedule_save()
        self._update_foreign_issue()
        self._update_no_data_issue()

    def _forget_foreign(self, serial: str) -> None:
        """A serial became the own one (learned or adopted)."""
        if self._foreign.pop(serial, None) is None:
            return
        self._schedule_save()
        self._update_foreign_issue()
        self._update_no_data_issue()

    def _prune_foreign(self, now: datetime | None = None) -> None:
        """Drop foreign devices that have not been seen for their window."""
        now = now or dt_util.utcnow()
        expired = [s for s, r in self._foreign.items() if not r.active(now)]
        if not expired:
            return
        for serial in expired:
            del self._foreign[serial]
        self._schedule_save()
        self._update_foreign_issue()

    def _issue_serial(self) -> str | None:
        """The foreign device present the longest (the one reported)."""
        if not self._foreign:
            return None
        return min(self._foreign, key=lambda s: self._foreign[s].first)

    def _blocking_serial(self) -> str | None:
        """Foreign device that blocks sending settings, if any.

        Blocked once the foreign devices present sent at least two telegrams
        in total: a single stray telegram doesn't block.
        """
        self._prune_foreign()
        if sum(r.count for r in self._foreign.values()) < FOREIGN_BLOCK_TELEGRAMS:
            return None
        return self._issue_serial()

    def _update_foreign_issue(self, force: bool = False) -> None:
        """Show the issue matching the foreign device present the longest.

        "Duplicate" if that device is set up as another entry, otherwise
        "other device" (fixable: adopt it). Re-determined on every foreign
        telegram, on expiry and on load, so it follows changes of the setup.
        """
        serial = self._issue_serial()
        known = self.entry.data.get(CONF_SERIAL)
        wanted: tuple[str, str] | None = None
        placeholders: dict[str, str] = {}
        if serial is not None:
            other = other_entry_for_serial(self.hass, self.entry, serial)
            if other is not None:
                wanted = (ISSUE_DUPLICATE_DEVICE, serial)
                placeholders = {"other": other.title}
            elif known is not None:
                wanted = (ISSUE_OTHER_DEVICE, serial)
                placeholders = {"known": known}
        previous = self._foreign_issue
        if wanted == previous and not force:
            return
        self._foreign_issue = wanted
        for issue in (ISSUE_OTHER_DEVICE, ISSUE_DUPLICATE_DEVICE):
            if wanted is None or issue != wanted[0] or (
                previous is not None and previous != wanted
            ):
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))
        if wanted is None:
            return
        if previous is not None and previous[1] == wanted[1] and previous[0] != wanted[0]:
            _LOGGER.info(
                "Topic %s: device %s is now reported as %s",
                self.base_topic,
                wanted[1],
                wanted[0],
            )
        issue, serial = wanted
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(issue),
            is_fixable=issue == ISSUE_OTHER_DEVICE,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue,
            translation_placeholders={"name": self.name, "serial": serial, **placeholders},
            data={"entry_id": self.entry.entry_id, "serial": serial},
        )

    def _load_foreign(self, stored: Any) -> None:
        """Restore the foreign devices of the last run (reload, HA restart).

        Only for the same topic ("Reconfigure" to another topic starts
        fresh), without the own serial (adopted meanwhile), without expired
        or unreadable records; timestamps in the future count as now.
        """
        devices: Any = None
        if isinstance(stored, dict) and stored.get("topic") == self.connection.address:
            devices = stored.get("devices")
        now = dt_util.utcnow()
        known = self.entry.data.get(CONF_SERIAL)
        for serial, record in (devices.items() if isinstance(devices, dict) else ()):
            if not valid_serial(serial) or serial == known or not isinstance(record, dict):
                continue
            first = _parse_time(record.get("first"))
            last = _parse_time(record.get("last"))
            count = record.get("count")
            window = as_number(record.get("window"), False)
            if (
                first is None
                or last is None
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
                or window is None
                or window <= 0
            ):
                continue
            last = min(last, now)
            foreign = _Foreign(min(first, last), last, count, float(window))
            if foreign.active(now):
                self._foreign[serial] = foreign
        while len(self._foreign) > FOREIGN_MAX_DEVICES:
            del self._foreign[min(self._foreign, key=lambda s: self._foreign[s].last)]
        self._update_foreign_issue(force=True)

    def _first_data_received(self) -> None:
        if self._no_data_unsub:
            self._no_data_unsub()
            self._no_data_unsub = None
        self._no_data_shown = False
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(ISSUE_NO_DATA))
        if CONF_AWAITING_DATA not in self.entry.data:
            return  # already cleared by the serial adoption
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
        if not self._pending:
            # Nothing of ours is open: maybe a command stored on the control
            # topic, executed by the device on reconnect.
            self._schedule_control_check()
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

    @callback
    def _schedule_control_check(self) -> None:
        """Look for a retained command on the control topic (one at a time).

        Run at setup, when the device reconnects (back after an outage,
        restarted), on a rejection nobody here asked for, and while the
        issue stands (so it goes once the message is deleted).
        """
        if self._control_check_running:
            return
        self._control_check_running = True
        self.entry.async_create_background_task(
            self.hass, self._async_check_control(), f"{DOMAIN}_check_control"
        )

    async def _async_check_control(self) -> None:
        try:
            raw = await self.connection.async_fetch_retained_control()
        except HomeAssistantError as err:
            _LOGGER.debug("Control topic of %s not checked: %s", self.base_topic, err)
            return
        finally:
            self._control_check_running = False
        issue_id = self._issue_id(ISSUE_RETAINED_CONTROL)
        if raw is None:
            if self._control_issue:
                _LOGGER.info("Retained message on the control topic of %s is gone",
                             self.base_topic)
            self._control_issue = False
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        if not self._control_issue:
            _LOGGER.warning(
                "A retained message lies on the control topic of %s; the device "
                "executes it on every reconnect: %s",
                self.base_topic,
                shorten_payload(raw),
            )
        self._control_issue = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_RETAINED_CONTROL,
            translation_placeholders={"name": self.name, "payload": shorten_payload(raw)},
        )

    def _update_sw_version(self, version: Any) -> None:
        """Firmware version (sent after a device restart) into the registry."""
        if (
            not isinstance(version, str)
            or not 0 < len(version) <= SERIAL_MAX_LENGTH
            or version == self._sw_version
        ):
            return
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get_device(identifiers={(DOMAIN, self.key)})
        if device is None:
            return
        self._sw_version = version
        if device.sw_version != version:
            dev_reg.async_update_device(device.id, sw_version=version)

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
                self._schedule_save()
        self._last_energy_ts = now
        self._last_power_w = power_w

    def _schedule_save(self) -> None:
        """Write energy counter and foreign devices with a delay.

        async_delay_save is a debounce (each call restarts the delay), so
        only schedule when no write is pending - otherwise frequent
        telegrams would postpone it forever.
        """
        if not self._save_pending:
            self._save_pending = True
            self.store.async_delay_save(self._store_data, ENERGY_SAVE_DELAY_SECONDS)

    @callback
    def _store_data(self) -> dict[str, Any]:
        """Data written to the Store (evaluated at write time)."""
        self._save_pending = False
        return {
            "energy_kwh": self.energy_kwh,
            "foreign": {
                "topic": self.connection.address,
                "devices": {
                    serial: {
                        "first": record.first.isoformat(),
                        "last": record.last.isoformat(),
                        "count": record.count,
                        "window": record.window,
                    }
                    for serial, record in self._foreign.items()
                },
            },
        }

    async def async_save(self) -> None:
        """Persist energy counter and foreign devices right now (unload)."""
        await self.store.async_save(self._store_data())

    @callback
    def handle_availability_tick(self, now: datetime) -> None:
        """Periodic check: device gone silent, issues that have run out."""
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
            self._check_control_on_return = True
            self._last_energy_ts = None
            self._last_power_w = None
            self._pending.clear()
            async_dispatcher_send(self.hass, self.signal)
        self._prune_foreign(now)
        if (
            self._invalid_last is not None
            and (now - self._invalid_last).total_seconds() > self.effective_timeout
        ):
            self._invalid_last = None
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(ISSUE_INVALID_SERIAL))
        self._update_no_data_issue()
        if self._control_issue:
            self._schedule_control_check()

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
        # Another device keeps sending on the topic: it reads the same
        # control topic. Switching off stays possible (safe for both).
        switch_off = name == CONTROL_NAME_TARGET_POWER and value == 0
        if not switch_off and (serial := self._blocking_serial()) is not None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="foreign_device_on_topic",
                translation_placeholders={"name": self.name, "serial": serial},
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
        ISSUE_OTHER_DEVICE,
        ISSUE_DUPLICATE_DEVICE,
        ISSUE_INVALID_SERIAL,
        ISSUE_RETAINED_CONTROL,
    ):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry_id}")
