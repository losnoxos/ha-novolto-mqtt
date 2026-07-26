# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
  stage values (kept for parity with the existing manual MQTT setup)
- English and German translations (config flow, options flow, entity names)
- CI validation workflow (hassfest + HACS) in preparation for an eventual
  HACS default-store submission
- Local brand icon (`custom_components/novolto_mqtt/brand/icon.png`)

### Fixed
- `OptionsFlow` no longer overrides `config_entry` in `__init__` - it's a
  read-only property on current Home Assistant core and doing so raised
  `AttributeError` when opening the options
- MQTT subscription and availability timer are now registered via
  `entry.async_on_unload` instead of manual bookkeeping, so they can't be
  leaked if setup is retried/aborted partway through
- Availability transitions (device starts/stops reporting data) are now
  logged, making future connectivity issues diagnosable from the log alone
