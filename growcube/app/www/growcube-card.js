class GrowcubeMqttCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = {
      title: "GrowCube",
      device: "",
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 6;
  }

  _deviceKey() {
    if (this._config.device) {
      return this._sanitize(this._config.device);
    }
    const states = this._hass?.states || {};
    const entityId = Object.keys(states).find((id) => /^(sensor|button|binary_sensor)\.growcube_.*_(temperature|moisture_a|water_plant_a)$/.test(id));
    if (!entityId) {
      return "";
    }
    return entityId
      .replace(/^(sensor|button|binary_sensor)\.growcube_/, "")
      .replace(/_(temperature|moisture_a|water_plant_a)$/, "");
  }

  _entity(domain, key) {
    const device = this._deviceKey();
    if (!device) {
      return "";
    }
    const direct = `${domain}.growcube_${device}_${key}`;
    if (this._hass?.states?.[direct]) {
      return direct;
    }
    const prefix = `${domain}.growcube_${device}_${key}`;
    return Object.keys(this._hass?.states || {}).find((id) => id === direct || id.startsWith(`${prefix}_`)) || direct;
  }

  _state(entityId) {
    return entityId ? this._hass?.states?.[entityId] : undefined;
  }

  _value(entityId, fallback = "unavailable") {
    const state = this._state(entityId);
    if (!state || state.state === "unknown" || state.state === "unavailable" || state.state === "") {
      return fallback;
    }
    return state.state;
  }

  _unit(entityId) {
    return this._state(entityId)?.attributes?.unit_of_measurement || "";
  }

  _callButton(entityId) {
    if (!this._hass || !entityId || !this._state(entityId)) {
      return;
    }
    this._hass.callService("button", "press", { entity_id: entityId });
  }

  _sanitize(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "");
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }
    const device = this._deviceKey();
    const temperature = this._entity("sensor", "temperature");
    const humidity = this._entity("sensor", "humidity");
    const connected = this._entity("binary_sensor", "connected");
    const waterWarning = this._entity("binary_sensor", "water_warning");
    const channels = ["a", "b", "c", "d"].map((id) => ({
      id,
      name: id.toUpperCase(),
      moisture: this._entity("sensor", `moisture_${id}`),
      pump: this._entity("binary_sensor", `pump_${id}`),
      water: this._entity("button", `water_plant_${id}`),
      stop: this._entity("button", `stop_watering_${id}`),
      history: this._entity("button", `load_history_${id}`),
    }));

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        ha-card {
          overflow: hidden;
        }
        .shell {
          padding: 18px;
        }
        .head {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 12px;
          margin-bottom: 18px;
        }
        .title {
          font-size: 24px;
          font-weight: 650;
          line-height: 1.1;
        }
        .sub {
          color: var(--secondary-text-color);
          margin-top: 4px;
          font-size: 13px;
        }
        .status {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          padding: 6px 10px;
          white-space: nowrap;
          color: var(--secondary-text-color);
        }
        .dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--error-color);
        }
        .dot.on {
          background: var(--success-color);
        }
        .metrics {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
          margin-bottom: 14px;
        }
        .metric {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 12px;
          min-width: 0;
        }
        .label {
          color: var(--secondary-text-color);
          font-size: 12px;
          margin-bottom: 6px;
        }
        .metric-value {
          font-size: 22px;
          font-weight: 650;
        }
        .channels {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
        }
        .channel {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 12px;
          min-width: 0;
        }
        .channel-top {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          margin-bottom: 10px;
        }
        .channel-name {
          font-size: 18px;
          font-weight: 650;
        }
        .pump {
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .pump.on {
          color: var(--warning-color);
        }
        .moisture {
          font-size: 34px;
          font-weight: 720;
          line-height: 1;
          margin-bottom: 12px;
        }
        .moisture span {
          color: var(--secondary-text-color);
          font-size: 14px;
          font-weight: 400;
        }
        .actions {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 6px;
        }
        mwc-button {
          --mdc-typography-button-font-size: 12px;
          min-width: 0;
        }
        .empty {
          color: var(--secondary-text-color);
          padding: 12px 0 2px;
        }
        @media (max-width: 900px) {
          .metrics {
            grid-template-columns: 1fr;
          }
          .channels {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
        }
        @media (max-width: 520px) {
          .head {
            display: block;
          }
          .status {
            margin-top: 12px;
          }
          .channels {
            grid-template-columns: 1fr;
          }
        }
      </style>
      <ha-card>
        <div class="shell">
          <div class="head">
            <div>
              <div class="title">${this._config.title}</div>
              <div class="sub">${device ? `MQTT device ${device}` : "Waiting for MQTT Discovery entities"}</div>
            </div>
            <div class="status">
              <span class="dot ${this._value(connected, "off") === "on" ? "on" : ""}"></span>
              <span>${this._value(connected, "offline") === "on" ? "Connected" : "Disconnected"}</span>
            </div>
          </div>
          ${device ? `
            <div class="metrics">
              ${this._metric("Temperature", temperature)}
              ${this._metric("Humidity", humidity)}
              ${this._metric("Water warning", waterWarning, true)}
            </div>
            <div class="channels">
              ${channels.map((channel) => this._channel(channel)).join("")}
            </div>
          ` : `
            <div class="empty">
              No GrowCube MQTT entities found yet. Check that the GrowCube add-on is running and MQTT Discovery is enabled.
            </div>
          `}
        </div>
      </ha-card>
    `;

    channels.forEach((channel) => {
      this.shadowRoot.getElementById(`water-${channel.id}`)?.addEventListener("click", () => this._callButton(channel.water));
      this.shadowRoot.getElementById(`stop-${channel.id}`)?.addEventListener("click", () => this._callButton(channel.stop));
      this.shadowRoot.getElementById(`history-${channel.id}`)?.addEventListener("click", () => this._callButton(channel.history));
    });
  }

  _metric(label, entityId, binary = false) {
    const value = this._value(entityId, "unknown");
    const text = binary ? (value === "on" ? "Yes" : "No") : `${value}${this._unit(entityId)}`;
    return `
      <div class="metric">
        <div class="label">${label}</div>
        <div class="metric-value">${text}</div>
      </div>
    `;
  }

  _channel(channel) {
    const moisture = this._value(channel.moisture, "--");
    const pumpOn = this._value(channel.pump, "off") === "on";
    return `
      <div class="channel">
        <div class="channel-top">
          <div class="channel-name">Plant ${channel.name}</div>
          <div class="pump ${pumpOn ? "on" : ""}">${pumpOn ? "Watering" : "Idle"}</div>
        </div>
        <div class="moisture">${moisture}<span>${moisture === "--" ? "" : "%"}</span></div>
        <div class="actions">
          <mwc-button id="water-${channel.id}" dense unelevated ${this._state(channel.water) ? "" : "disabled"}>Water</mwc-button>
          <mwc-button id="stop-${channel.id}" dense outlined ${this._state(channel.stop) ? "" : "disabled"}>Stop</mwc-button>
          <mwc-button id="history-${channel.id}" dense outlined ${this._state(channel.history) ? "" : "disabled"}>History</mwc-button>
        </div>
      </div>
    `;
  }
}

customElements.define("growcube-card", GrowcubeMqttCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "growcube-card",
  name: "GrowCube Card",
  description: "GrowCube MQTT dashboard card",
});
