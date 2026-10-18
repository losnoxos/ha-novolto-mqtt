"""Constants for the Novolto MQTT integration."""
from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "novolto_mqtt"

# --- Config entry data (set in the config flow) ---
CONF_CONNECTION = "connection"
CONNECTION_MQTT = "mqtt"
# MQTT topic prefix of the device ("<base_topic>/info", "<base_topic>/control").
# Defaults to the serial number on the device, but can be changed there.
CONF_BASE_TOPIC = "base_topic"
# Serial number from the telegram (`serial` field). It identifies the device
# (unique_id, device registry, entity unique_ids), independent of its topic.
# None only while a device set up without data has never reported in.
CONF_SERIAL = "serial"
# Set when the device was added without having received any data; cleared
# with the first telegram. Drives the "sends no data" repair issue.
CONF_AWAITING_DATA = "awaiting_data"

# --- Options (changeable later via the options flow) ---
CONF_MODEL = "model"
CONF_POWER_STAGES = "power_stages"
CONF_SPTW_MAX = "sptw_max"
# Advanced options
CONF_POWER_STEP = "power_step"
CONF_HEATING_THRESHOLD_W = "heating_threshold_w"
CONF_SPTW_MIN = "sptw_min"
CONF_SPTWH_MIN = "sptwh_min"
CONF_SPTWH_MAX = "sptwh_max"
CONF_TIMEOUT_SECONDS = "timeout_seconds"
CONF_TOPIC_INFO = "topic_info"
CONF_TOPIC_CONTROL = "topic_control"
# Options of 0.5.0 and earlier that no longer exist (removed on migration):
# max power now follows the model, the board temperature sensor always exists.
LEGACY_CONF_MAX_POWER = "max_power"
LEGACY_CONF_ENABLE_BOARD_TEMPERATURE = "enable_board_temperature"


@dataclass(frozen=True)
class ModelSpec:
    """Model-specific defaults."""

    name: str
    max_power: int
    power_stages: tuple[int, ...]


MODEL_P3000 = "p3000"
MODEL_P2300 = "p2300"
MODELS: dict[str, ModelSpec] = {
    # P3000: three ~1000 W elements; in-between values are pulsed between
    # whole element stages (confirmed by measurement).
    MODEL_P3000: ModelSpec("P3000", 3000, (0, 1000, 2000, 3000)),
    # P2300: element split not yet confirmed by the manufacturer; stages
    # assume 1000 W elements plus remainder.
    MODEL_P2300: ModelSpec("P2300", 2300, (0, 1000, 2000, 2300)),
}
DEFAULT_MODEL = MODEL_P3000

DEFAULT_TOPIC_INFO = "info"
DEFAULT_TOPIC_CONTROL = "control"
DEFAULT_POWER_STEP = 20
DEFAULT_HEATING_THRESHOLD_W = 15
DEFAULT_SPTW_MIN = 0
DEFAULT_SPTW_MAX = 50
DEFAULT_SPTWH_MIN = 1
DEFAULT_SPTWH_MAX = 10
# Above this maximum target temperature the options flow asks for an explicit
# confirmation (scalding risk at the tap).
SAFE_SPTW_MAX = 50
# Hard upper limits for the option fields.
LIMIT_SPTW = 95
LIMIT_SPTWH = 50
# Values stored implicitly by 0.5.0 and earlier (their defaults back then).
LEGACY_DEFAULT_SPTW_MAX = 80
LEGACY_DEFAULT_SPTWH_MAX = 20
# Minimum time without a telegram before entities go unavailable. The
# effective timeout is max(this option, MSI_TIMEOUT_FACTOR * msi), so devices
# with a long measurement interval (`msi`) don't flap between telegrams.
DEFAULT_TIMEOUT_SECONDS = 120
MSI_TIMEOUT_FACTOR = 3

ADVANCED_DEFAULTS: dict[str, object] = {
    CONF_POWER_STEP: DEFAULT_POWER_STEP,
    CONF_HEATING_THRESHOLD_W: DEFAULT_HEATING_THRESHOLD_W,
    CONF_SPTW_MIN: DEFAULT_SPTW_MIN,
    CONF_SPTWH_MIN: DEFAULT_SPTWH_MIN,
    CONF_SPTWH_MAX: DEFAULT_SPTWH_MAX,
    CONF_TIMEOUT_SECONDS: DEFAULT_TIMEOUT_SECONDS,
    CONF_TOPIC_INFO: DEFAULT_TOPIC_INFO,
    CONF_TOPIC_CONTROL: DEFAULT_TOPIC_CONTROL,
}


def default_options(model: str = DEFAULT_MODEL) -> dict[str, object]:
    """Complete option set for a newly added device of the given model."""
    return {
        CONF_MODEL: model,
        CONF_POWER_STAGES: list(MODELS[model].power_stages),
        CONF_SPTW_MAX: DEFAULT_SPTW_MAX,
        **ADVANCED_DEFAULTS,
    }


# --- Setup / discovery ---
# The device sends a telegram every `msi` seconds (default 30), so listening
# for 35 s catches every powered device at least once.
LISTEN_SECONDS = 35
# Telegrams the discovery manifest subscribes to (single-level topics only;
# deeper topics are found by the manual search, which listens on "#").
DISCOVERY_TOPIC = "+/info"
# Repair issue "sends no data" for devices added without data.
NO_DATA_ISSUE_MINUTES = 10

# --- Fields from the Novolto info telegram (<base_topic>/info), see
# NOVOLTO-MQTT.md in dbus-novolto for the full protocol reference. ---
FIELD_SERIAL = "serial"
FIELD_UNIX_TIME = "unix_time"
FIELD_MSI = "msi"
FIELD_BOARD_TEMP = "avt1"
FIELD_WATER_TEMP = "avtw"
FIELD_TARGET_WATER_TEMP = "sptw"
FIELD_TARGET_WATER_TEMP_HYSTERESIS = "sptwh"
FIELD_TARGET_POWER = "spp"
FIELD_VOLTAGE = "avv"
FIELD_POWER = "avp"
FIELD_CURRENT = "avi"
FIELD_FREQUENCY = "avf"
FIELD_RSSI = "rssi"
FIELD_STATUS = "st"
FIELD_ENERGY_ESTIMATE = "wel"
FIELD_ROD_STATUS = "rod_st"
# "miscellaneous diagnostic data" per Novolto's own docs - not further
# documented, triacon is a monotonically increasing counter (not a status).
FIELD_TRIACON = "triacon"
FIELD_R1ON = "r1on"
FIELD_R2ON = "r2on"

# Control-command acknowledgements are published on the *same* info topic but
# only ever carry serial/unix_time/ret/s_err - never any measurement field.
# We use this to tell a real telegram apart from an ack without hardcoding a
# type per field - without it, every ack would blank out all sensors.
MEASUREMENT_FIELDS = {
    FIELD_WATER_TEMP,
    FIELD_POWER,
    FIELD_VOLTAGE,
    FIELD_CURRENT,
    FIELD_FREQUENCY,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP,
}
# Fields a payload must carry to be recognised as a Novolto telegram during
# discovery/search (by content, not by topic name).
DISCOVERY_REQUIRED_FIELDS = {FIELD_SERIAL, FIELD_WATER_TEMP, FIELD_MSI}

# Fields that are integers on the wire (bitmasks, counters, setpoints that
# the firmware only accepts as int). Everything else numeric is a float.
INTEGER_FIELDS = {
    FIELD_UNIX_TIME,
    FIELD_MSI,
    FIELD_TARGET_POWER,
    FIELD_RSSI,
    FIELD_STATUS,
    FIELD_ROD_STATUS,
    FIELD_TRIACON,
    FIELD_R1ON,
    FIELD_R2ON,
}

FIELD_RET = "ret"
FIELD_S_ERR = "s_err"

# Novolto SENSOR module names used on <base_topic>/control - note the quirky,
# per-field JSON type requirement confirmed against real device acks:
# SPP must be an int, SPTW/SPTWH must be floats. Sending the wrong JSON type
# gets rejected with ret=13 "wrong type".
CONTROL_MODULE_SENSOR = "sensor"
CONTROL_NAME_TARGET_POWER = "spp"
CONTROL_NAME_TARGET_WATER_TEMP = "sptw"
CONTROL_NAME_TARGET_WATER_TEMP_HYSTERESIS = "sptwh"

# Status bitflags (`st`), one bit per independent warning/error, several can
# be set at once. Ordered by importance: the status sensor shows the first
# active one. Values are the (translated) enum states of the status sensor.
STATUS_STB_TRIPPED = 0x0400
STATUS_BITS: dict[int, str] = {
    STATUS_STB_TRIPPED: "stb_tripped",
    0x0010: "board_temp_exceeded",
    0x0001: "sensor_missing",
    0x0002: "water_temp_read_fail",
    0x0020: "board_temp_read_fail",
    0x0008: "fan_rpm_mismatch",
    0x0004: "meter_reading_mismatch",
    0x0040: "meter_read_fail",
    0x0100: "power_freq_mismatch",
    0x0200: "missing_settings",
    0x0080: "hub_disconnected",
}
STATUS_OK = "ok"
STATUS_UNKNOWN_BIT = "unknown_bit"

# Repair issue ids (suffixed with the config entry id).
ISSUE_NO_DATA = "no_data"
ISSUE_SETTING_REJECTED = "setting_rejected"
ISSUE_STB_TRIPPED = "stb_tripped"
# Fixable: retained telegram on the broker is outdated (retain switched off
# on the device later), the fix deletes it.
ISSUE_STALE_RETAINED = "stale_retained"
# How long to wait for the broker to deliver the stored message again.
STORED_MESSAGE_WAIT_SECONDS = 5

SIGNAL_UPDATE = f"{DOMAIN}_update_{{entry_id}}"

# Persisted energy counter (see NovoltoDevice._update_energy in device.py) -
# integrated from avp, like dbus-novolto's `integrate` energy_source, instead
# of the device's own `wel` (which resets to 0 on every Novolto reboot).
STORAGE_VERSION = 1
# Max delay before a changed energy value is written to disk. Pending writes
# are also flushed by Home Assistant on shutdown.
ENERGY_SAVE_DELAY_SECONDS = 300

# Links used in setup messages.
URL_MOSQUITTO_ADDON = (
    "https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_mosquitto"
)
URL_MQTT_SETUP = (
    "https://my.home-assistant.io/redirect/config_flow_start/?domain=mqtt"
)
# Section "Step 2 - Connect the heating rod to the broker".
URL_README = (
    "https://github.com/losnoxos/ha-novolto-mqtt/blob/master/README.md"
    "#step-2--connect-the-heating-rod-to-the-broker"
)
URL_README_DE = (
    "https://github.com/losnoxos/ha-novolto-mqtt/blob/master/README.de.md"
    "#schritt-2--heizstab-mit-dem-broker-verbinden"
)
