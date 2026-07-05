# Changelog

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
