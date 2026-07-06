# GrowCube HAOS Add-on

Home Assistant OS add-on backend for GrowCube devices.

This project is separate from the existing Home Assistant GrowCube custom
integration used as the protocol and UI reference.

The add-on can be installed from a GitHub add-on repository URL and started from
HAOS. It connects to GrowCube devices directly over the local network and
publishes Home Assistant entities through MQTT Discovery.

The built-in Web UI is the fastest way to start using the add-on: it opens from
the Home Assistant add-on page, searches for GrowCube devices, adds them to the
add-on, and controls them through the add-on backend. The Web UI talks to the
add-on over Home Assistant ingress; the add-on then sends GrowCube TCP protocol
commands directly to each device on port `8800`.

MQTT is still used for the Home Assistant integration surface. After a device is
added, the add-on publishes MQTT Discovery and state topics so Home Assistant can
create entities/devices for dashboards, automations, and manual customization.

The existing Home Assistant GrowCube custom integration remains the canonical
reference for protocol behavior, entity naming, watering controls, and
dashboard/card ideas.

## Current MVP

The first add-on version includes:

- Home Assistant OS add-on repository metadata
- `GrowCube` add-on packaging
- standalone Python backend bridge with no third-party Python dependencies
- add-on Web UI for GrowCube discovery, add/remove, connection status, dashboard
  controls, plant setup, watering, tank, and history
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
6. Configure MQTT credentials if your broker requires them.
7. Start it.
8. Open the add-on Web UI and add GrowCube devices from **Settings -> Discover**
   or **Settings -> Manual add**.

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

## Usage Option 1: Built-in Web UI

This is the recommended setup path.

1. Open the **GrowCube** add-on.
2. Open **Web UI**.
3. Click the settings gear.
4. Use **Discover -> Search network** to find GrowCube devices automatically, or
   use **Manual add** if you already know the device IP address.
5. Add the device.
6. Use the dashboard in the Web UI to configure plants, manual watering,
   timed/smart watering, tank state, and history.

The Web UI does not need Home Assistant entities to exist first. It calls the
add-on ingress API, and the add-on sends commands directly to the GrowCube over
TCP `8800`.

## Usage Option 2: Home Assistant Dashboard YAML

If you want a Home Assistant dashboard made from MQTT-discovered entities, use
the Lovelace card and dashboard YAML.

The add-on copies its Lovelace card to:

```text
/config/www/growcube/growcube-card.js
```

The GrowCube add-on log should contain:

```text
GrowCube Lovelace card copied to /config/www/growcube/growcube-card.js
GrowCube Lovelace card copied to /config/www/growcube/growcube-card-0.2.58.js
```

Home Assistant serves that file as:

```text
/local/growcube/growcube-card-0.2.58.js
```

Add it as a Lovelace resource:

1. Open **Settings -> Dashboards**.
2. Open the three-dot menu and choose **Resources**.
3. Add a JavaScript module resource:

```text
/local/growcube/growcube-card-0.2.58.js
```

Then create or edit a dashboard and add a manual card:

```yaml
type: custom:growcube-card
title: GrowCube
overview: dashboard
```

You can also import the portable dashboard YAML from:

```text
docs/lovelace-growcube-mqtt-dashboard.yaml
```

The dashboard/card uses the MQTT entities published by this add-on. Add devices
from the Web UI first so the add-on can connect to the cubes and publish MQTT
Discovery.

The card auto-detects the MQTT entities created by this add-on. If multiple
GrowCube devices are present, pass the sanitized device host explicitly:

```yaml
type: custom:growcube-card
title: GrowCube Office
device: 192_168_1_50
```

Plant search, dashboard metadata, and detailed history use the add-on ingress
API automatically. The add-on injects its current ingress URL into the copied
card at startup, so a normal install does not need an `addon_api_url` setting.

If Home Assistant generated unexpected entity IDs, pass them explicitly:

```yaml
type: custom:growcube-card
title: GrowCube
entities:
  temperature: sensor.growcube_temperature
  humidity: sensor.growcube_humidity
  connected: binary_sensor.growcube_connected
  water_warning: binary_sensor.growcube_water_warning
  moisture_a: sensor.growcube_moisture_a
  pump_a: binary_sensor.growcube_pump_a
  water_plant_a: button.growcube_water_plant_a
  stop_watering_a: button.growcube_stop_watering_a
  load_history_a: button.growcube_load_history_a
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

## Notes

- Device state is stored in `/data/growcube_state.json` inside the add-on.
- GrowCube devices are managed from the add-on Web UI, not from the add-on
  configuration.
- The add-on publishes MQTT Discovery configs under `homeassistant/...`.
- Runtime state is published under `growcube/<device>/state`.
- MQTT commands are received under `growcube/<device>/+/set`.
