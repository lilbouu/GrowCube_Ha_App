# Changelog

## 0.2.41

- Use relative Web UI API requests so Home Assistant ingress routes dashboard and discovery calls back to the add-on.

## 0.2.40

- Add an add-on Web UI for GrowCube device discovery, manual add, remove, and live connection status.
- Keep static `devices` configuration optional so devices can be configured either from YAML or from the Web UI.
- Return connection, error, and GrowCube firmware version details in the dashboard API payload.

## 0.2.39

- Publish `first_watering_time_*` as MQTT `time` entities instead of text entities to match the HACS integration.
- Add `/devices/discover` LAN scanning for GrowCube devices by network, with automatic local `/24` fallback when possible.
- Add HACS-style history retry checks for stuck history loads, due scheduled watering refreshes, and trailing history gaps.
- Create and dismiss Home Assistant persistent notifications for connection, tank, lock, sensor, outlet, and smart watering alerts.

## 0.2.38

- Apply the 300 mL unusable reserve only when the reservoir capacity is the built-in 1500 mL GrowCube tank.
- Use the full remaining amount for custom reservoir capacities when calculating tank days left.

## 0.2.37

- Match the HACS tank-days-left calculation by using the same 300 mL unusable tank reserve.
- Retry GrowCube TCP connections every 10 seconds after startup failures or later disconnects.
- Debounce and apply watering mode, amount, interval, first-time, and smart-threshold entity edits so MQTT entity changes behave closer to the HACS integration.

## 0.2.36

- Return saved plant photo metadata from the direct channel config API after adding a plant.
- Keep the confirmed backend `photo_url` in the card's local dashboard state so the plant image does not fall back to the flower icon immediately after save.

## 0.2.35

- Bump the add-on and Lovelace card asset version after the 0.2.34 card copy was not visible in Home Assistant.
- Derive the versioned Lovelace card filename from the JavaScript card version so future releases do not miss the copied `growcube-card-*.js` asset.

## 0.2.34

- Render saved plant photos in the summary and detail card headers instead of falling back to the flower icon after a plant is added.
- Reuse the saved plant photo resolver for the active channel so dashboard metadata and Home Assistant text entity values both work after save.

## 0.2.33

- Resolve saved plant photo URLs from dashboard metadata, Home Assistant text entities, and markdown-style copied URLs before rendering images.
- Show fallback moisture as a single current point on the right side of the selected chart window instead of stretching it across the whole range.

## 0.2.32

- Keep catalog plant photos attached when adding plants and show them immediately in the plant About view.
- Normalize catalog image fields from add-on, Home Assistant, and direct GrowCube catalog responses.
- Draw the moisture chart from the current known moisture value when no history points exist in the selected time window.

## 0.2.31

- Preserve plant photo URLs in dashboard and plant detail views by returning plant metadata from the add-on dashboard API.
- Refresh the dashboard device cache after adding a plant so newly saved images and names appear immediately.
- Prevent history views from staying in a loading state after both GrowCube history completion reports are received.
- Serialize GrowCube history requests per device to avoid overlapping channel history loads.
- Label recent watering activity as manual, timed, smart, or last watering.

## 0.2.30

- Improve GrowCube Lovelace card compatibility with the standalone add-on API.
- Publish versioned Lovelace card assets for Home Assistant cache busting.
