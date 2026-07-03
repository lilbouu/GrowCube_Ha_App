const state = {
  devices: [],
  selectedHours: 24,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

async function loadState() {
  const data = await api("api/state");
  state.devices = data.devices || [];
  render();
}

function render() {
  const root = $("#devices");
  const empty = $("#empty");
  empty.hidden = state.devices.length > 0;
  root.innerHTML = state.devices.map(deviceTemplate).join("");
  root.querySelectorAll("[data-action]").forEach((element) => {
    element.addEventListener("click", handleAction);
  });
}

function deviceTemplate(device) {
  const classes = `status ${device.connected ? "connected" : ""}`;
  return `
    <article class="device">
      <div class="device-head">
        <div>
          <h2>${escapeHtml(device.name || device.host)}</h2>
          <p class="muted">${escapeHtml(device.host)}:${Number(device.port || 8800)}</p>
        </div>
        <div class="${classes}">
          <span class="dot"></span>
          <span>${device.connected ? "Connected" : device.connecting ? "Connecting" : "Disconnected"}</span>
        </div>
      </div>
      ${device.error ? `<p class="error">${escapeHtml(device.error)}</p>` : ""}
      <div class="actions" style="margin-top: 12px">
        <button data-action="connect" data-device="${device.id}">Connect</button>
        <button class="danger" data-action="remove" data-device="${device.id}">Remove</button>
      </div>
      <div class="device-grid">
        ${(device.channels || []).map((channel, index) => channelTemplate(device, channel, index)).join("")}
      </div>
    </article>
  `;
}

function channelTemplate(device, channel, index) {
  const moisture = Number.isFinite(Number(channel.moisture)) ? `${Number(channel.moisture)}%` : "--";
  return `
    <section class="channel">
      <div class="card-head">
        <h3>Channel ${["A", "B", "C", "D"][index] || index + 1}</h3>
        <span class="muted">${channel.pump_open ? "Watering" : "Idle"}</span>
      </div>
      <div class="moisture">${moisture}<span> moisture</span></div>
      ${historySvg(channel)}
      <div class="actions">
        <button class="primary" data-action="water" data-device="${device.id}" data-channel="${index}">Water</button>
        <button data-action="stop" data-device="${device.id}" data-channel="${index}">Stop</button>
        <button data-action="history" data-device="${device.id}" data-channel="${index}">
          ${channel.history_loading ? "Loading..." : "History"}
        </button>
      </div>
    </section>
  `;
}

function historySvg(channel) {
  const points = (channel.history || [])
    .map((point) => ({
      t: new Date(point.timestamp).getTime(),
      v: Number(point.moisture),
    }))
    .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.v));
  if (!points.length) {
    return `<div class="chart muted" style="display:grid;place-items:center">No history</div>`;
  }
  points.sort((a, b) => a.t - b.t);
  const minT = points[0].t;
  const maxT = points[points.length - 1].t || minT + 1;
  const coords = points.map((point) => {
    const x = 8 + ((point.t - minT) / Math.max(1, maxT - minT)) * 284;
    const y = 10 + (1 - Math.max(0, Math.min(100, point.v)) / 100) * 96;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const events = (channel.watering_events || []).map((event) => {
    const t = new Date(event.timestamp).getTime();
    if (!Number.isFinite(t) || t < minT || t > maxT) {
      return "";
    }
    const x = 8 + ((t - minT) / Math.max(1, maxT - minT)) * 284;
    return `<circle cx="${x.toFixed(1)}" cy="106" r="4" fill="#5fc3ff"></circle>`;
  }).join("");
  return `
    <svg class="chart" viewBox="0 0 300 120" role="img">
      <line x1="8" y1="10" x2="292" y2="10" stroke="#36393b"></line>
      <line x1="8" y1="58" x2="292" y2="58" stroke="#36393b"></line>
      <line x1="8" y1="106" x2="292" y2="106" stroke="#36393b"></line>
      <polyline points="${coords.join(" ")}" fill="none" stroke="#13a8d6" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
      ${events}
    </svg>
  `;
}

async function handleAction(event) {
  const target = event.currentTarget;
  const device = target.dataset.device;
  const channel = Number(target.dataset.channel || 0);
  try {
    if (target.dataset.action === "connect") {
      await api(`api/devices/${device}/connect`, { method: "POST", body: "{}" });
    } else if (target.dataset.action === "remove") {
      await fetch(`api/devices/${device}`, { method: "DELETE" });
    } else if (target.dataset.action === "water") {
      await api(`api/devices/${device}/water`, {
        method: "POST",
        body: JSON.stringify({ channel, duration: 7 }),
      });
    } else if (target.dataset.action === "stop") {
      await api(`api/devices/${device}/stop`, {
        method: "POST",
        body: JSON.stringify({ channel }),
      });
    } else if (target.dataset.action === "history") {
      await api(`api/devices/${device}/history`, {
        method: "POST",
        body: JSON.stringify({ channel }),
      });
    }
    await loadState();
  } catch (error) {
    alert(error.message || "Request failed");
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

$("#device-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await api("api/devices", {
      method: "POST",
      body: JSON.stringify({
        name: form.get("name"),
        host: form.get("host"),
        port: Number(form.get("port") || 8800),
      }),
    });
    event.currentTarget.reset();
    event.currentTarget.elements.port.value = "8800";
    await loadState();
  } catch (error) {
    alert(error.message || "Could not add device");
  }
});

$("#refresh").addEventListener("click", loadState);
loadState();
setInterval(loadState, 5000);
