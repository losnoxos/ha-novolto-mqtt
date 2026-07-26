# Novolto MQTT

*[Deutsche Version](README.de.md)*

Home Assistant custom integration (via HACS) for the Novolto electric
heating rod (P2300/P3000), over MQTT. Talks directly to the same local
broker the Novolto itself publishes to - **no Venus OS / Victron dbus
required**. Protocol-compatible sibling project to
[dbus-novolto](https://github.com/losnoxos/dbus-novolto) (Venus OS driver)
and [heatpump-novolto](https://github.com/losnoxos/victronenergy.heatpump.novolto).

## What you get

- Config flow setup (UI) - no YAML required
- Multiple Novolto devices supported, each as its own config entry
- `water_heater` entity: current + target water temperature
- `number` entities: target power, hysteresis
- `binary_sensor`: heating right now (derived from measured power, not the
  `rod_st` field, which proved unreliable at low power levels)
- `sensor` entities: voltage, current, frequency, water temperature,
  decoded status/warning flags - plus an optional board-temperature sensor
  and diagnostic WiFi/measurement-interval sensors (disabled by default)
- English and German UI translations

## Requirements

- Home Assistant with the core `mqtt` integration already set up and
  connected to the same broker the Novolto publishes to
- The Novolto's MQTT base topic (its serial number, e.g.
  `AAA.BBB.123456` - visible in MQTT Explorer)

## Installation via HACS

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

This integration deliberately does **not** ship its own energy counter.
Add Home Assistant's built-in
[Riemann sum integral](https://www.home-assistant.io/integrations/integration/)
helper on the power sensor instead - simpler and more robust than
reimplementing persistence, and it plugs straight into the Energy
dashboard. (The device's own `wel` field resets to 0 on every Novolto
reboot, which is why it isn't used here.)

## Known limitations

- Setting acknowledgements (`ret`/`s_err`) are only logged as a warning,
  not yet surfaced as a repair/notification in the UI
- No MQTT-based auto-discovery during setup - the base topic must be
  entered manually
- `rod_st`, `triacon`, `r1on`, `r2on` are intentionally not exposed -
  unreliable or undocumented by the manufacturer

## Protocol reference

See
[NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
in dbus-novolto for the full field-by-field MQTT protocol documentation
(this integration follows the same protocol).

## License

MIT, see [LICENSE](LICENSE).
