# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.0] - 2026-10-18

### Breaking
- The water heater state now shows what the heating rod is doing:
  `heating`, `idle` ("Ready") or `off` ("Off (0 W)", target power 0 W)
  instead of the constant `electric`. Automations that use the state need
  to be adjusted.
- Removed options: "Maximum power" (now set by the model) and "Enable board
  temperature sensor" (the electronics temperature sensor now always exists
  and is enabled, as a regular sensor instead of a diagnostic one).
- The status sensor is now an enum sensor with translated states
  (`ok`, `stb_tripped`, `sensor_missing`, ...) instead of an English text.
  It shows the most important active message; all active messages are in
  the attribute `active`, unknown bits in `unknown_bits` (hex). Automations
  that compare the state with the old texts need to be adjusted.
- Maximum power now follows the model (P2300: 2300 W, P3000: 3000 W). A
  lower maximum set in the options before is not taken over; restrict the
  power in automations if needed.
- After a Home Assistant restart the device stays unavailable until the
  first live telegram (up to `msi` seconds, 30 s by default). A retained
  telegram on the broker shows the last values but no longer counts as
  "device reachable".

### Added
- Setup without typing: Home Assistant discovers heating rods by itself
  ("New device found"); "Add integration" searches all topics for 35 s and
  lists the heating rods found. Telegrams are recognised by content, so
  freely named and multi-level topics work too.
- Manual topic entry checks whether data arrives; if not: correct the
  topic, search again or set up anyway, with a hint on upper/lower case
  typos. Setup is aborted with links if MQTT isn't set up or connected.
- Model selection (P2300/P3000) during setup and in the options; it sets
  the maximum power and the suggested power stages and is shown in the
  device info.
- "Power stage" select with configurable stages (default
  `0, 1000, 2000, 3000`; P2300: `0, 1000, 2000, 2300`).
- Options in two steps: settings (power stages, model, maximum target
  temperature) and, only on request, advanced settings behind a warning,
  including "reset to defaults". Explanations for every field.
- Maximum target temperature above 50 °C requires a confirmed scalding
  warning.
- Repairs: "rejected a setting" (with error code), "safety temperature
  limiter tripped" (critical) and "sends no data" for devices set up
  without data.
- Repair with fix button for an outdated stored (retained) message on the
  broker, left behind when retain is switched off on the device; confirming
  the fix deletes it.
- "Reconfigure" to change the MQTT topic; device, entities and energy
  counter are kept.
- Diagnostics download with the serial number redacted.
- Setup retries (MQTT not ready yet) show a translated reason.

### Changed
- Sliders show a new value immediately and only jump back if the device
  rejects it.
- Setting a value while the device is unreachable raises an error and sends
  nothing.
- Raw diagnostic values (status raw, rod enable raw, triacon, heating stage
  1/2) and the rod enable binary sensor are disabled by default and have a
  `state_class`, so they no longer flood the logbook. Existing
  installations keep them enabled; disable them under Settings → Entities if
  not needed.
- Entity names: the hysteresis entity is now called "Switching
  differential (hysteresis)", "Rod status" is now "Rod enable (raw)", "Board
  temperature" is now "Electronics temperature". Entity IDs don't change.
- Acknowledgements replayed by the broker (retained) are ignored.
- New defaults: maximum target temperature 50 °C (was 80 °C), maximum
  switching differential 10 °C (was 20 °C). Existing installations keep
  their previous values.
- The device is identified by the serial number from its telegrams, no
  longer by its topic. Existing installations are migrated automatically.
- Internal: device logic separated from the MQTT connection.
- Tests run against Home Assistant 2024.12 (minimum version) and the
  current release.

## [0.5.0] - 2026-10-17

### Breaking
- The `water_heater` entity is now the device's main entity and uses the
  device name as its friendly name; "Heating" now only refers to the binary
  sensor. Existing entity IDs are kept, new installations get an entity ID
  based on the device name.
- The `water_heater` state is now always `electric` instead of `off`. In
  Home Assistant the water heater state is the operation mode, not the
  activity; the Novolto has no selectable modes. Use the "Heating" binary
  sensor to see whether the rod is heating right now.
- Minimum Home Assistant version is now 2024.12.0.

### Fixed
- Reloading, reconfiguring (options) or removing the integration failed and
  left the device without entities until Home Assistant was restarted.
- Energy counter no longer books energy for periods without data (device or
  broker outage); negative power readings are ignored.
- Energy counter is saved periodically and on shutdown, so a restart no
  longer loses the last minutes of counted energy.
- Setting a target temperature outside the configured limits is now
  rejected instead of being sent to the device.
- Setup now waits for the MQTT integration and retries automatically
  instead of failing when the broker connection isn't ready yet.
- Telegrams with missing or non-numeric values no longer break entity
  updates or the energy counter.
- Devices with a long measurement interval (`msi`) no longer flap between
  available and unavailable: the timeout is now at least 3 × `msi`.
- Unknown status bits are shown as "Unknown (0x....)" instead of "OK".

### Changed
- Energy is integrated with the trapezoidal rule between samples.
- Config flow normalizes the base topic (surrounding whitespace, slashes
  and an accidentally pasted `/info` suffix are removed) and rejects MQTT
  wildcards. Setup is aborted with a hint if MQTT isn't set up yet.
- Options are range-checked (min below max, sensible limits).
- Persisted energy data is deleted when a device is removed.
- Energy sensor shows 2 decimals by default.

## [0.4.0] - 2026-07-26

### Added
- `sensor`: Power (`avp`) - measured power was already used internally
  (heating threshold, energy integration) but was never exposed as its
  own entity

### Fixed
- `water_heater` entity no longer shows "unknown" as its state.
  `WaterHeaterEntity.state` always returns `current_operation`, which we
  never set; fixed by fixing it to "off" (the Novolto has no real
  operation modes), matching the fallback HA's own MQTT `water_heater`
  platform uses when no mode topic is configured

## [0.3.0] - 2026-07-26

### Added
- Config flow (UI setup), multiple Novolto devices supported (one config
  entry per device, keyed by its MQTT base topic/serial)
- `water_heater` entity (current + target water temperature)
- `number` entities: target power (`spp`), hysteresis (`sptwh`)
- `binary_sensor`: heating right now, derived from measured power rather
  than the unreliable `rod_st` field; plus `rod_st` itself as its own
  diagnostic binary sensor
- `sensor` entities: voltage, current, frequency, water temperature,
  decoded status/warning flags, a persistent energy counter integrated
  from measured power (same approach as dbus-novolto's `integrate`
  energy_source); optional board temperature; diagnostic sensors for WiFi
  signal, measurement interval, and raw status/rod-status/triacon/heating-
  stage values
- English and German translations (config flow, options flow, entity names)
- CI validation workflow (hassfest + HACS) in preparation for an eventual
  HACS default-store submission
- Bundled brand icon (`custom_components/novolto_mqtt/brand/icon.png`)

### Fixed
- `OptionsFlow` no longer overrides `config_entry` in `__init__` - it's a
  read-only property on current Home Assistant core and doing so raised
  `AttributeError` when opening the options
- MQTT subscription and availability timer are now registered via
  `entry.async_on_unload` instead of manual bookkeeping, so they can't be
  leaked if setup is retried/aborted partway through
- Availability transitions (device starts/stops reporting data) are now
  logged, making future connectivity issues diagnosable from the log alone
