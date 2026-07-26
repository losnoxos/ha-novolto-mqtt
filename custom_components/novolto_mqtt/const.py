"""Constants for the Novolto MQTT integration."""
from __future__ import annotations

DOMAIN = "novolto_mqtt"

# --- Config entry keys (set once, in the config flow "user" step) ---
CONF_BASE_TOPIC = "base_topic"

# --- Options keys (changeable later via the options flow) ---
CONF_TOPIC_INFO = "topic_info"
CONF_TOPIC_CONTROL = "topic_control"
CONF_MAX_POWER = "max_power"
CONF_POWER_STEP = "power_step"
CONF_HEATING_THRESHOLD_W = "heating_threshold_w"
CONF_ENABLE_BOARD_TEMPERATURE = "enable_board_temperature"
CONF_SPTW_MIN = "sptw_min"
CONF_SPTW_MAX = "sptw_max"
CONF_SPTWH_MIN = "sptwh_min"
CONF_SPTWH_MAX = "sptwh_max"
CONF_TIMEOUT_SECONDS = "timeout_seconds"

DEFAULT_TOPIC_INFO = "info"
DEFAULT_TOPIC_CONTROL = "control"
DEFAULT_MAX_POWER = 3000
DEFAULT_POWER_STEP = 20
DEFAULT_HEATING_THRESHOLD_W = 15
DEFAULT_ENABLE_BOARD_TEMPERATURE = False
DEFAULT_SPTW_MIN = 0
DEFAULT_SPTW_MAX = 80
DEFAULT_SPTWH_MIN = 1
DEFAULT_SPTWH_MAX = 20
# Novolto info telegrams arrive every `msi` seconds (device-controlled).
# We multiply that by this factor before marking entities unavailable, so a
# single missed telegram doesn't flap availability.
DEFAULT_TIMEOUT_SECONDS = 120

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
# type per field (see "Ist-Stand MQTT YAML" note in Obsidian for why this
# matters: without it, every ack would blank out all sensors).
MEASUREMENT_FIELDS = {
    FIELD_WATER_TEMP,
    FIELD_POWER,
    FIELD_VOLTAGE,
    FIELD_CURRENT,
    FIELD_FREQUENCY,
    FIELD_TARGET_POWER,
    FIELD_TARGET_WATER_TEMP,
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
# be set at once.
STATUS_BITS: dict[int, str] = {
    0x0001: "Sensor missing",
    0x0002: "Water temp read fail",
    0x0004: "Meter reading mismatch",
    0x0008: "Fan RPM mismatch",
    0x0010: "Board temp exceeded",
    0x0020: "Board temp read fail",
    0x0040: "Meter read fail",
    0x0080: "Hub disconnected",
    0x0100: "Power freq mismatch",
    0x0200: "Missing settings",
    0x0400: "STB tripped",
}

SIGNAL_UPDATE = f"{DOMAIN}_update_{{entry_id}}"

# Persisted energy counter (see NovoltoDevice._update_energy in __init__.py) -
# integrated from avp, like dbus-novolto's `integrate` energy_source, instead
# of the device's own `wel` (which resets to 0 on every Novolto reboot).
STORAGE_VERSION = 1
# How often the accumulated energy value is written to disk - matches the
# 5-minute cadence dbus-novolto uses for its energy.json.
ENERGY_SAVE_EVERY_N_TICKS = 10
