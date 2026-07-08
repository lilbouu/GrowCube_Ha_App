# GrowCube HAOS Add-on (V. 1.0)

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
6. Configure MQTT and, optionally, static GrowCube devices.
7. Start it.
8. Open the add-on Web UI and add GrowCube devices from **Settings -> Discover**
   or **Settings -> Manual add**, unless you already configured them in the
   add-on options.

The add-on connects directly to each device. It does not require the Home
Assistant GrowCube custom integration to be installed.

MQTT must be available in Home Assistant. The default configuration expects the
official Mosquitto broker add-on at `core-mosquitto`.

Configure MQTT in the GrowCube add-on options. If your broker does not require
credentials, leave `mqtt_username` and `mqtt_password` empty.

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: growcube_mqtt
mqtt_password: your-password
```

You can also preconfigure GrowCube devices in the same add-on options. The
GrowCube TCP port defaults to `8800`; in normal setups you only need to fill in
the name and IP address.

```yaml
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: growcube_mqtt
mqtt_password: your-password
devices:
  - name: Kitchen GrowCube
    host: 192.168.1.50
    port: 8800
```

If the add-on log shows `MQTT connection failed: not authorized (0005)`, create
or choose a Home Assistant user for MQTT access and set those credentials in the
GrowCube add-on configuration.

## Usage Option 1: Built-in Web UI

This is the simplest setup path and does not require creating a Home Assistant
dashboard.

1. Open the **GrowCube** add-on.
2. Open **Web UI**.
3. Click the settings gear.
4. Use **Discover -> Search network** to find GrowCube devices automatically,
   use **Manual add** if you already know the device IP address, or configure
   devices in the add-on options.
5. Add the device if it was not configured already.
6. Use the dashboard in the Web UI to configure plants, manual watering,
   timed/smart watering, tank state, and history.

The Web UI does not need Home Assistant entities to exist first. It calls the
add-on ingress API, and the add-on sends commands directly to the GrowCube over
TCP `8800`.

## Usage Option 2: Home Assistant Dashboard

If you want a native Home Assistant dashboard made from MQTT-discovered
entities, use the Lovelace card and the ready-to-paste dashboard YAML.

Before creating the dashboard:

1. Configure MQTT in the add-on options.
2. Add at least one GrowCube device from the Web UI or through the add-on
   `devices` option.
3. Start or restart the add-on.
4. Wait for Home Assistant to discover the GrowCube MQTT entities.

The add-on copies its Lovelace card to:

```text
/config/www/growcube/growcube-card.js
```

The GrowCube add-on log should contain:

```text
GrowCube Lovelace card copied to /config/www/growcube/growcube-card.js
GrowCube Lovelace card copied to /config/www/growcube/growcube-card-0.2.76-addon-compat.js
```

Home Assistant serves that file as:

```text
/local/growcube/growcube-card.js
```

Add it as a Lovelace resource:

1. Open **Settings -> Dashboards**.
2. Open the three-dot menu and choose **Resources**.
3. Add a JavaScript module resource:

```text
/local/growcube/growcube-card.js
```

Use the ready-to-paste dashboard YAML:
[docs/lovelace-growcube-mqtt-dashboard.yaml](docs/lovelace-growcube-mqtt-dashboard.yaml)

That file contains the overview page and four plant detail pages. It is designed
to be copied into a Home Assistant dashboard YAML editor.

The dashboard/card uses the MQTT entities published by this add-on. Add devices
from the Web UI or the add-on `devices` option first so the add-on can connect
to the cubes and publish MQTT Discovery.

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

## Notes

- Device state is stored in `/data/growcube_state.json` inside the add-on.
- GrowCube devices can be managed from the add-on Web UI or preconfigured in
  the add-on `devices` option.
- The add-on publishes MQTT Discovery configs under `homeassistant/...`.
- Runtime state is published under `growcube/<device>/state`.
- MQTT commands are received under `growcube/<device>/+/set`.
