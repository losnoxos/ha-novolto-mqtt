# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

Initial version, not yet tagged/released.

### Added
- Config flow (UI setup), multiple Novolto devices supported (one config
  entry per device, keyed by its MQTT base topic/serial)
- `water_heater` entity (current + target water temperature)
- `number` entities: target power (`spp`), hysteresis (`sptwh`)
- `binary_sensor`: heating right now, derived from measured power rather
  than the unreliable `rod_st` field
- `sensor` entities: voltage, current, frequency, water temperature,
  decoded status/warning flags; optional board temperature; diagnostic
  WiFi signal and measurement-interval sensors (disabled by default)
- English and German translations (config flow, options flow, entity names)
- CI validation workflow (hassfest + HACS) in preparation for an eventual
  HACS default-store submission
