# Changelog

## 0.2.82

- Clear the HA channel plant profile and cached plant history when GrowCube reports `plant_id=0` after deletion from Android.

## 0.2.81

- Send complete plant profiles through `POST /channel/config` so long descriptions cannot exceed ingress URL limits.
- Prevent catalog plants from falling back to a watering update with `plant_id=0` when the add-on API fails.
- Verify that the saved catalog plant ID matches the selected plant before reporting success.

## 0.2.78

- Format GrowCube card dates in English regardless of the browser or Home Assistant locale.

## 0.2.77

- Proxy GrowCube catalog images through the add-on API in both Web UI and Home Assistant dashboard cards, including `www.growcube.cc` image URLs, so paged plant search results load images reliably.

## 0.2.76

- Allow the direct dashboard API on port `8099` from private LAN clients by default so Home Assistant dashboard cards can search the plant catalog without relying on rotating ingress tokens.

## 0.2.75

- Prefer the direct add-on API on port `8099` for Home Assistant dashboard cards before falling back to MQTT/bundled ingress URLs, avoiding stale ingress tokens in plant search.

## 0.2.74

- Prefer runtime Home Assistant Supervisor ingress discovery for dashboard cards so plant search does not use a stale baked `/api/hassio_ingress/...` URL.
- Retry add-on API calls once with a refreshed ingress URL when Home Assistant returns 401, 403, 404, or 503.

## 0.2.73

- Make plant catalog search in Home Assistant dashboard cards load add-on dashboard metadata before falling back to custom-only results.
- Bump the Lovelace card resource version so Home Assistant reloads the dashboard search fix.

## 0.2.72

- Document the two supported usage paths: the built-in Web UI and a Home Assistant dashboard.
- Restore optional static GrowCube device configuration in add-on options while keeping Web UI device management.
- Point dashboard setup to the ready-to-paste YAML in `docs/lovelace-growcube-mqtt-dashboard.yaml`.
- Keep GrowCube TCP devices on port `8800` by default when they are configured in add-on options.

## 0.2.71

- Refresh standalone Web UI dashboard card state immediately after device add/remove and service calls.
- Stop carrying old plant metadata and cached photos forward when a dashboard channel is explicitly unconfigured.
- Clear backend channel state immediately after reset plant commands so deleted plants disappear without a page reload.

## 0.2.70

- Show Recent activity watering entries as Last watering instead of Timed, Smart, or Manual watering.
- Store protocol watering history records with a neutral source because the GrowCube history payload contains event time, not the watering mode that caused it.

## 0.2.69

- Skip the transient unconfigured plant setup screen after adding a plant by honoring optimistic channel metadata immediately.
- Navigate to the newly added plant detail page before rendering the success toast and background dashboard refresh.

## 0.2.68

- Keep uploaded Home Assistant ingress plant photo URLs on their original HTTP scheme so local photos render after upload.
- Replace custom growing-condition sliders with numeric minimum and maximum inputs.
- Harden custom profile creation so saving returns to the custom plant library instead of continuing to channel selection.

## 0.2.67

- Change custom plant creation into a profile-only flow that returns to the custom plant library instead of adding directly to a channel.
- Page the custom plant library three profiles at a time and let selected custom profiles use the normal add-to-channel flow.
- Stop re-rendering the add-plant wizard while dragging custom growing-condition sliders.

## 0.2.66

- Move custom plant access into a Custom button beside Search on the first add-plant step.
- Add a custom plant library dialog that lists saved custom profiles, shows an empty state when none exist, and offers an Add custom plant action.

## 0.2.65

- Restore the short catalog plant add flow while keeping the extended name, photo, description, and growing conditions flow only for custom plants.
- Improve the add-plant dialog layout and use sliders for custom plant moisture, temperature, and air humidity ranges.

## 0.2.64

- Redesign the custom plant flow as a step-by-step wizard: choose catalog/custom, set name and photo, set growing conditions, choose channel, then configure watering.
- Add local plant photo upload through the add-on API so custom plants can use device-selected JPEG, PNG, or WebP images instead of external URLs.

## 0.2.63

- Add a custom plant path to the Web UI add-plant wizard with editable name, photo URL, category, description, temperature range, humidity range, and watering settings.
- Let catalog plant profiles be customized before saving so users can override catalog metadata per channel.

## 0.2.62

- Prefer the GrowCube cloud catalog HTTP endpoint because HTTPS can stall inside HAOS add-on networking before falling back to the working HTTP endpoint.
- Use a shorter timeout for the HTTPS fallback so catalog searches do not block the ingress API for a full catalog timeout when HTTP is unavailable.

## 0.2.61

- Request the GrowCube cloud catalog without gzip so larger plant searches use a `Content-Length` response instead of gzip chunked transfer, avoiding read timeouts on multi-result queries.
- Stop logging full raw catalog JSON bodies and base64 wire chunks at info level; keep size, encoding, and key diagnostics in the response log.

## 0.2.60

- Read GrowCube cloud catalog responses in chunks with `Connection: close` so larger plant searches do not hang waiting for the server to close the response.

## 0.2.59

- Increase the GrowCube cloud catalog timeout and log compressed/uncompressed response sizes so larger plant searches such as `rose` can complete instead of timing out too early.

## 0.2.58

- Remove static GrowCube device setup from the add-on configuration; devices are now managed from the Web UI and stored in add-on data.
- Publish MQTT Discovery/state explicitly when a device is added from the Web UI so Home Assistant can discover it without an add-on restart.
- Make Web UI discovery default to one-click local network search while keeping manual network entry behind an optional control.
- Restrict the ingress API handler to loopback and configured Home Assistant/Supervisor CIDRs instead of allowing all private LAN addresses.
- Keep plant search usable when the GrowCube cloud catalog times out by returning an empty result instead of a Web UI API error, and avoid repeated slow retries for the same query.

## 0.2.57

- Restore the GrowCube title in the Web UI top bar with the settings gear on the same row.
- Align the standalone device switcher with the Web UI top bar.
- Place the plant-detail back button in the title row so the plant title shifts right like the Settings page header.

## 0.2.56

- Replace the Web UI text settings tab with a dashboard gear button and a settings back button.
- Move the standalone plant-detail back button above the card content.
- Proxy GrowCube catalog plant images through the add-on Web UI.
- Keep watering markers visible across the selected history window even when history points start later.

## 0.2.55

- Restore the original watering marker styling on the moisture chart.

## 0.2.54

- Move the standalone Web UI plant-detail back button to the upper-left overlay position so it no longer shifts the plant detail layout.
- Increase watering marker contrast on the moisture chart.

## 0.2.53

- Route standalone Web UI API calls through the absolute Home Assistant ingress URL.
- Render standalone Web UI card icons inside the card shadow DOM without depending on Home Assistant `ha-icon`.
- Show backend error details for manual watering and tank update failures.

## 0.2.52

- Add standalone Web UI icon rendering for the GrowCube card inside Home Assistant ingress.
- Add a Web UI-only back button on plant detail pages.

## 0.2.51

- Make the add-on Web UI open the full GrowCube dashboard by default using the existing Lovelace card implementation.
- Move GrowCube discovery, manual add, and removal controls into a Settings view.
- Add standalone Web UI state and service-call adapters so dashboard controls work without manual Lovelace setup.

## 0.2.50

- Remove temporary Lovelace ingress-resource fallback and extra card install paths now that `/config/www/growcube` installs correctly.
- Keep port 8099 as the internal ingress API port only, without exposing an extra add-on port mapping.

## 0.2.49

- Fix Lovelace card installation on a clean Home Assistant system by checking only the mounted root such as `/config`, then creating `/config/www/growcube` when needed.

## 0.2.48

- Serve the Lovelace card directly from the add-on ingress API when Home Assistant does not mount `/config` into the add-on container.
- Log a ready-to-use ingress Lovelace resource URL during startup.

## 0.2.47

- Restore the add-on mount layout that worked in earlier builds: `addon_config:rw` plus `config:rw`.
- Prefer copying the Lovelace card to `/config/www/growcube`, matching the original working add-on layout.

## 0.2.46

- Mount the Home Assistant configuration directory with the standard `config:rw` add-on map so the Lovelace card can be copied to `/config/www/growcube`.

## 0.2.45

- Keep GrowCube discovery from failing when a probe connection is reset by a device or emulator during TCP close.
- Ignore per-host discovery probe errors so one bad scan target no longer makes `/devices/discover` return 500.

## 0.2.44

- Handle GrowCube watering exception, outlet blocked, sensor disconnected, device lock, and watering locked reports like the HACS integration.
- Store GrowCube history and watering record timestamps with the local timezone to match HACS chart behavior.

## 0.2.43

- Keep plant photos visible after a hard browser reload by caching resolved image URLs per device and channel.
- Reuse cached plant photos when the card initially starts from MQTT-discovered entities before dashboard metadata arrives.

## 0.2.42

- Bump the add-on metadata to match the updated Lovelace card asset version.
- Include the latest plant image rendering changes in the installable add-on update.

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
