# GrowCube HAOS App

Standalone Home Assistant OS add-on application for GrowCube.

This project is separate from the existing Home Assistant custom integration in:

`/Users/vladislav/esp/HomeAssistant_Growcube_Integration`

The goal is to build an add-on that can be installed from a GitHub add-on
repository URL, started from HAOS, and opened through the add-on Web UI. It
should connect to GrowCube devices directly and provide its own dashboard,
watering controls, history charts, settings, discovery/manual setup, and
diagnostics.

This is not intended to be a HACS installer for the integration. The existing
integration remains a reference for protocol and UI behavior.

## Current MVP

The first add-on version includes:

- Home Assistant OS add-on repository metadata
- `GrowCube` add-on packaging
- standalone Python backend with no third-party Python dependencies
- Web UI served through add-on Ingress
- manual GrowCube add by host/IP
- direct TCP connection to GrowCube on port `8800`
- current moisture display for channels A-D
- manual watering and stop commands
- stored moisture/watering history request and basic charts

## Install In HAOS

Publish this folder as a GitHub repository, then in Home Assistant OS:

1. Open **Settings -> Add-ons -> Add-on Store**.
2. Open the menu and choose **Repositories**.
3. Paste the GitHub repository URL for this project.
4. Find **GrowCube** in the add-on store.
5. Install it.
6. Start it.
7. Open **Web UI**.

Inside the Web UI, add a GrowCube by IP address or hostname. The app then
connects directly to the device. It does not require the Home Assistant
GrowCube custom integration to be installed.

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
    static/
      index.html
      style.css
      app.js
```

## Notes

- Device state is stored in `/data/growcube_state.json` inside the add-on.
- HAOS add-on options can also provide initial devices via `/data/options.json`.
- This app does not currently create Home Assistant entities. MQTT Discovery can
  be added later if we want optional HA dashboard entities.
