# Novolto MQTT

*[Deutsche Version](README.de.md)*

Home Assistant integration for the Novolto heating rod (P2300/P3000). The
heating rod sends its measurements to an MQTT broker in your home network;
Home Assistant reads them there and sends settings back. Everything stays
local, no cloud.

## What you get

**To control the heating rod**

| Entity | Purpose |
|---|---|
| *Device name* (water heater) | Current and target temperature. State: **Heating**, **Ready** or **Off (0 W)** |
| Power stage (select) | Fixed stages in one click, e.g. Off / 1000 / 2000 / 3000 W |
| Target power (number) | Set any power, 0 W = off |
| Switching differential (hysteresis) (number) | How far the temperature may swing around the target |

**Display only**

Heating (on/off), power, energy (for the energy dashboard), water
temperature, electronics temperature, voltage, current, frequency, status.
Hidden by default and available on demand: WiFi signal, measurement interval
and raw values for troubleshooting.

## What you need

- Home Assistant 2024.12 or newer
- [HACS](https://hacs.xyz)
- A Novolto heating rod P2300 or P3000 in the same network (WiFi or LAN)
- The login for the heating rod's configuration page (available from
  Novolto)

> [!WARNING]
> **The Novolto app stops working after the switch.** Once the heating rod
> sends to your own broker, it is disconnected from the Novolto cloud. From
> then on it is controlled through Home Assistant only.

## Step 1 – Set up an MQTT broker in Home Assistant

If you already use an MQTT broker and the MQTT integration, continue with
step 2.

1. **Install the Mosquitto broker:** Settings → Add-ons → Add-on store →
   "Mosquitto broker" → Install → Start.
   [Open directly](https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_mosquitto)
2. **Set up the MQTT integration:** Home Assistant usually suggests it on its
   own afterwards (Settings → Devices & services → "Discovered"). Otherwise:
   [Add the MQTT integration](https://my.home-assistant.io/redirect/config_flow_start/?domain=mqtt).
3. **Create a user for the heating rod:** Settings → People → Add person,
   e.g. `novolto`, allow it to log in and set its own password.
   The Mosquitto add-on accepts Home Assistant users as MQTT logins.
4. **Note the IP address of Home Assistant:** Settings → System → Network.
   Best give Home Assistant a fixed IP in your router, otherwise the heating
   rod loses the connection when the address changes.

## Step 2 – Connect the heating rod to the broker

1. **Press the button on the heating rod once briefly.** It opens a WiFi
   hotspot.
2. Connect to the hotspot and open the configuration page (Novolto provides
   the login).
3. Write down the current MQTT settings first, so you can go back.
4. Enter in the heating rod's **MQTT settings** (field names may differ
   slightly depending on the firmware):
   - Broker address: IP address of Home Assistant (from step 1)
   - Port: `1883`
   - User and password: the user from step 1
   - **Enable retain** (recommended): the broker then always knows the latest
     state and Home Assistant finds the heating rod immediately during setup.
     Without retain everything works the same; during setup the heating rod
     is only found while it is sending.
5. This is also where the heating rod's **MQTT topic** is shown. Unless
   changed, it is the serial number, e.g. `AAA.BBB.123456`. You only need it
   if the automatic search doesn't find the heating rod.
6. Save.

## Step 3 – Is data arriving?

Optional, but helps with troubleshooting: Settings → Devices & services →
MQTT → **Configure** → enter `+/info` under "Listen to a topic" → **Start
listening**. Within 30 seconds a message with `"serial"`, `"avtw"` etc.
appears. If nothing arrives, see [Troubleshooting](#troubleshooting).

## Step 4 – Install the integration with HACS

[![Open repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=losnoxos&repository=ha-novolto-mqtt&category=integration)

Or manually: HACS → ⋮ → **Custom repositories** →
`https://github.com/losnoxos/ha-novolto-mqtt`, category **Integration** →
download "Novolto MQTT".

Then **restart Home Assistant** and reload the browser page with
**Ctrl+F5**, otherwise the integration doesn't show up in the list yet.

## Step 5 – Add the heating rod

- **Automatically:** Home Assistant usually reports "New device found:
  Novolto heating rod …" by itself (Settings → Devices & services). Click
  it, choose a name and the model, done.
- **Manually:** Settings → Devices & services → **Add integration** →
  "Novolto MQTT". The integration searches for heating rods for up to 35
  seconds and lists the ones it found. If yours isn't there, you can enter
  the MQTT topic yourself; the integration then checks whether data arrives
  and helps with typos.

> [!TIP]
> **Choose the name carefully.** The entity IDs are derived from it, e.g.
> `water_heater.novolto_p3000`. Renaming later does not change the IDs.

Automatic discovery finds heating rods whose topic has a single level (the
usual case: the serial number). Multi-level topics like `house/heatingrod`
are found by the search when adding manually.

## Operation

- **Temperature *and* power decide whether it heats.** The heating rod only
  heats when the water is below the target temperature *and* the target
  power is above 0 W. With 0 W the water heater shows **Off (0 W)**,
  whatever temperature is set.
- **Switching differential:** the heating rod heats below *target −
  differential/2* and stops above *target + differential/2*. Example: 60 °C
  target and 10 °C differential → heats below 55 °C, stops above 65 °C.
  Usual values are 2–5 °C.
- **In-between values fluctuate:** the P3000 has three heating elements of
  about 1000 W each. It reaches in-between values by switching between the
  whole stages: 1500 W means alternating 1000 and 2000 W, visible e.g. on an
  external power meter. For a steady grid load use whole stages only
  (0/1000/2000/3000 W); the power stage select is set up that way.
- **Maximum target temperature:** 50 °C by default. Higher values need a
  confirmed warning in the options (scalding risk at the tap).
- New values show up in the slider immediately. If the heating rod rejects a
  setting, the slider jumps back and a message appears under Settings →
  Repairs.

> [!WARNING]
> **Without a connection the heating rod keeps heating.** After a power cut
> it starts with the last values set. If Home Assistant or the broker goes
> down, it keeps heating with the last power, even if e.g. a PV surplus
> automation last set 3000 W. This automation sets the power to 0 W after
> every Home Assistant start:
>
> ```yaml
> alias: Novolto to 0 W after HA start
> triggers:
>   - trigger: homeassistant
>     event: start
> actions:
>   # wait until the heating rod reports in again
>   - wait_template: >-
>       {{ states('number.novolto_p3000_target_power')
>          not in ['unavailable', 'unknown'] }}
>     timeout: "00:05:00"
>     continue_on_timeout: false
>   - action: number.set_value
>     target:
>       entity_id: number.novolto_p3000_target_power
>     data:
>       value: 0
> ```

## Dashboard

Example card (adjust the entity IDs to your device name):

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: water_heater.novolto_p3000
  - type: tile
    entity: select.novolto_p3000_power_stage
    features:
      - type: select-options
  - type: entities
    entities:
      - number.novolto_p3000_target_power
      - number.novolto_p3000_switching_differential_hysteresis
      - sensor.novolto_p3000_power
      - binary_sensor.novolto_p3000_heating
      - sensor.novolto_p3000_energy
```

Entity IDs follow the language of your Home Assistant at the time the
device is added.

## Energy dashboard

Settings → Dashboards → Energy → **Individual devices** → Add device →
the heating rod's "Energy" sensor.

Don't add it under "Grid consumption", that is for the main meter. The
counter starts at 0 when the device is added and survives restarts. Deleting
the integration also deletes the counter.

## Options

Settings → Devices & services → Novolto MQTT → **Configure**:

- **Power stages:** values for the "Power stage" select, e.g.
  `0, 1000, 2000, 3000`. 0 must be included. An empty list hides the select.
  If the target power matches none of the stages (e.g. set by an
  automation), the select shows "Custom value".
  The heating rod makes in-between values like 1500 W fluctuate between the
  whole stages (see [Operation](#operation)), so for a steady grid load enter
  whole stages only.
- **Model:** P2300 or P3000, sets the maximum power. After changing the
  model the settings are shown again with the new model's power stages;
  the second OK saves.
- **Maximum target temperature:** see [Operation](#operation).
- **Advanced settings** (only after ticking the box): target power step,
  "Heating" threshold, minimum target temperature, switching differential
  range, availability timeout, topic names, reset to defaults.

**Changed the MQTT topic?** Integration menu (⋮) → **Reconfigure**. Device,
entities and energy counter are kept.

## Switching from a manual MQTT configuration (YAML)

If you set up the heating rod with your own `mqtt:` entries in
`configuration.yaml` before:

1. Note automations and the energy dashboard that use the old entities.
2. Remove the Novolto `mqtt:` entries from the YAML.
3. Developer tools → YAML → reload **Manually configured MQTT entities**.
4. Delete the orphaned old entities under Settings → Devices & services →
   Entities.
5. Only then add the integration. This avoids IDs ending in `_2` and double
   energy counting.

## Several heating rods

Add each heating rod separately; each gets its own entities, options and
energy counter. Use different names, e.g. "Heating rod basement" and
"Heating rod garage".

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Home Assistant needs an MQTT broker …" | MQTT missing | Step 1 |
| Search finds nothing | Heating rod off, different broker, wrong user/password | Check steps 2 and 3 |
| "No data received" | Typo in the topic (upper/lower case!) | Copy the topic from the heating rod's configuration |
| Everything "Unavailable" | Heating rod off or no network | Check power and WiFi/LAN. After an HA restart it takes up to 30 s |
| Target temperature set, nothing happens | Target power is 0 W | Raise the power |
| Integration missing in the list | HA not restarted after the HACS download | Restart, Ctrl+F5 |
| Message "rejected a setting" | Value not accepted by the heating rod | Error code in the message, check the value |
| Message "outdated stored message on the broker" | Retain was on in the heating rod and has been switched off; the broker still holds the old message | Click **Fix** in the message and confirm, it gets deleted |

For a bug report on GitHub: Settings → Devices & services → Novolto MQTT →
⋮ → **Download diagnostics** (the serial number is redacted).

## Advanced

- Topics: `<topic>/info` (measurements every `msi` seconds, plus
  acknowledgements of settings) and `<topic>/control` (settings `spp`,
  `sptw`, `sptwh`).
- A telegram the broker delivers as "retained" shows the last values but
  does not make the heating rod available; only a live message does.
- The status sensor shows the most important active message. All active
  messages are in the attribute `active`, the bitmask in `status_raw`.
- Protocol reference:
  [NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
  in dbus-novolto.

## License

MIT, see [LICENSE](LICENSE).
