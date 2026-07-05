# Changelog

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
