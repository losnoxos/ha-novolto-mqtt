# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
