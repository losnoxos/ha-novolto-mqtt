# Novolto MQTT

*[Deutsche Version](README.de.md)*

Home Assistant custom integration (via HACS) for the Novolto electric
heating rod (P2300/P3000), over MQTT. Talks directly to the same local
broker the Novolto itself publishes to.

## What you get

- Config flow setup (UI) - no YAML required
- Multiple Novolto devices supported, each as its own config entry
- `water_heater` entity: current + target water temperature
- `number` entities: target power, hysteresis
- `binary_sensor`: heating right now (derived from measured power, not the
  `rod_st` field, which proved unreliable at low power levels), plus the
  raw `rod_st` value as its own diagnostic binary sensor
- `sensor` entities: power, voltage, current, frequency, water temperature,
  decoded status/warning flags, a persistent energy counter - plus an
  optional board-temperature sensor and diagnostic sensors (WiFi signal,
  measurement interval, raw status/rod-status/triacon/heating-stage
  values - the last four kept only for parity with the manufacturer's own
  app/diagnostics, undocumented and not meant for automations)
- English and German UI translations

## Requirements

- Home Assistant with the core `mqtt` integration already set up and
  connected to the same broker the Novolto publishes to
- The Novolto's MQTT base topic (its serial number, e.g.
  `AAA.BBB.123456` - visible in MQTT Explorer)

## Installation via HACS

**Quick way:** click the button below, confirm in HACS, then continue at
step 3.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=losnoxos&repository=ha-novolto-mqtt&category=integration)

**Manual way:**

1. HACS → ⋮ menu → **Custom repositories**
2. Add `https://github.com/losnoxos/ha-novolto-mqtt`, category
   **Integration**
3. Install "Novolto MQTT", then restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → search
   "Novolto MQTT"
5. Enter the device's base topic (serial) and an optional name

## Options

Available after setup via the integration's **Configure** button: topic
name suffixes, max power/step, heating threshold, target-temperature and
hysteresis limits, availability timeout, and the optional board-temperature
sensor.

## Energy tracking

The `Energy` sensor is integrated from measured power (`avp`), the same
approach dbus-novolto uses (`integrate` energy_source), and persisted to
disk so it survives Home Assistant restarts. It deliberately does **not**
use the device's own `wel` field, which resets to 0 on every Novolto
reboot. The sensor uses `state_class: total_increasing`, so it plugs
straight into the Energy dashboard - no extra Riemann-sum helper needed.

## Known limitations

- Setting acknowledgements (`ret`/`s_err`) are only logged as a warning,
  not yet surfaced as a repair/notification in the UI
- No MQTT-based auto-discovery during setup - the base topic must be
  entered manually
- `rod_st`, `triacon`, `r1on`, `r2on` are exposed only as raw diagnostic
  sensors (kept for parity/troubleshooting) - the manufacturer doesn't
  document them, and `rod_st` in particular is not used for any actual
  on/off decision (see `Heating` binary sensor)

## Protocol reference

See
[NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
in dbus-novolto for the full field-by-field MQTT protocol documentation
(this integration follows the same protocol).

## License

MIT, see [LICENSE](LICENSE).
