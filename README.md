# GrowCube HAOS Add-on

Home Assistant OS add-on backend for GrowCube devices.

This project is separate from the existing Home Assistant custom integration in:

`/Users/vladislav/esp/HomeAssistant_Growcube_Integration`

The goal is to build an add-on that can be installed from a GitHub add-on
repository URL and started from HAOS. The add-on connects to GrowCube devices
directly and exposes them back into Home Assistant as native MQTT-discovered
entities.

This is not intended to be a separate web UI or a HACS installer for the
integration. The existing integration remains the canonical reference for
protocol behavior, entity naming, watering controls, and dashboard/card ideas.

Home Assistant should own the user experience: devices appear as entities, and a
GrowCube dashboard can be created in HA from those entities.

## Current MVP

The first add-on version includes:

- Home Assistant OS add-on repository metadata
- `GrowCube` add-on packaging
- standalone Python backend bridge with no third-party Python dependencies
- manual GrowCube add by host/IP
- direct TCP connection to GrowCube on port `8800`
- MQTT Discovery for Home Assistant entities
- temperature and humidity sensors
- moisture sensors for channels A-D
- pump/water warning binary sensors
- manual watering, stop, and history buttons for channels A-D
- custom Lovelace card copied to `/config/www/growcube/growcube-card.js`
- portable dashboard YAML in `docs/lovelace-growcube-mqtt-dashboard.yaml`

## Install In HAOS

Publish this folder as a GitHub repository, then in Home Assistant OS:

1. Open **Settings -> Add-ons -> Add-on Store**.
2. Open the menu and choose **Repositories**.
3. Paste the GitHub repository URL for this project.
4. Find **GrowCube** in the add-on store.
5. Install it.
6. Configure one or more GrowCube devices by IP address or hostname.
7. Start it.

The add-on connects directly to each device. It does not require the Home
Assistant GrowCube custom integration to be installed.

MQTT must be available in Home Assistant. The default configuration expects the
official Mosquitto broker add-on at `core-mosquitto`.

If the add-on log shows `MQTT connection failed: not authorized (0005)`, create
or choose a Home Assistant user for MQTT access and set those credentials in the
GrowCube add-on configuration:

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: growcube_mqtt
mqtt_password: your-password
```

## Repository Layout

```text
repository.yaml
growcube/
  config.yaml
  build.yaml
  Dockerfile
  run.sh
  app/
    main.py
    growcube_client.py
    growcube_protocol.py
    mqtt_bridge.py
    www/
      growcube-card.js
docs/
  lovelace-growcube-mqtt-dashboard.yaml
```

## Updating The Add-on

Do not uninstall the add-on just to update it. Uninstalling can remove add-on
data.

After pushing a new version to GitHub:

1. Open **Settings -> Add-ons -> Add-on Store**.
2. Open the menu and reload repositories, or open the GrowCube add-on page and
   use the available update/rebuild action.
3. Restart the GrowCube add-on.

The add-on configuration and stored state under `/data` should remain in place.

## Dashboard

The add-on copies its Lovelace card to:

```text
/config/www/growcube/growcube-card.js
```

The GrowCube add-on log should contain:

```text
GrowCube Lovelace card copied to /config/www/growcube/growcube-card.js
```

Home Assistant serves that file as:

```text
/local/growcube/growcube-card.js
```

If this URL returns `404 Not Found`, check that the log line above is present.
If `/config/www` did not exist before, restart Home Assistant Core once so the
`/local` static path is picked up.

Add it as a Lovelace resource:

1. Open **Settings -> Dashboards**.
2. Open the three-dot menu and choose **Resources**.
3. Add a JavaScript module resource:

```text
/local/growcube/growcube-card.js
```

Then create or edit a dashboard and add a manual card:

```yaml
type: custom:growcube-card
title: GrowCube
```

You can also import the portable dashboard YAML from:

```text
docs/lovelace-growcube-mqtt-dashboard.yaml
```

The card auto-detects the MQTT entities created by this add-on. If multiple
GrowCube devices are present, pass the sanitized device host explicitly:

```yaml
type: custom:growcube-card
title: GrowCube Office
device: 192_168_1_50
```

## Notes

- Device state is stored in `/data/growcube_state.json` inside the add-on.
- HAOS add-on options can also provide initial devices via `/data/options.json`.
- The add-on publishes MQTT Discovery configs under `homeassistant/...`.
- Runtime state is published under `growcube/<device>/state`.
- Commands are received under `growcube/<device>/command/set`.
