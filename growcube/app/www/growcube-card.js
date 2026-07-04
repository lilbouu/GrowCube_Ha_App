class GrowcubeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = {
      title: "GrowCube",
      overview: "",
      detail: false,
      channel: "",
      device: "",
      entity_prefix: "",
      entities: {},
      ...config,
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return this._config.detail ? 8 : 6;
  }

  _state(entityId) {
    return entityId && this._hass ? this._hass.states[entityId] : undefined;
  }

  _domainEntity(domain, suffixes) {
    const configured = this._config.entities || {};
    for (const suffix of Array.isArray(suffixes) ? suffixes : [suffixes]) {
      const key = String(suffix).replace(/^_/, "");
      if (configured[key]) {
        return configured[key];
      }
    }
    if (!this._hass) {
      return "";
    }
    const wanted = (Array.isArray(suffixes) ? suffixes : [suffixes]).flatMap((suffix) => {
      const text = String(suffix);
      return text.startsWith("_") ? [text, text.slice(1)] : [text, `_${text}`];
    });
    const prefix = this._config.entity_prefix || this._config.device_prefix || "";
    return Object.keys(this._hass.states).sort().find((entityId) => {
      if (!entityId.startsWith(`${domain}.`)) {
        return false;
      }
      const objectId = entityId.slice(domain.length + 1);
      if (prefix && !objectId.startsWith(prefix)) {
        return false;
      }
      if (this._config.device && !objectId.includes(this._sanitize(this._config.device))) {
        return false;
      }
      return wanted.some((suffix) => objectId.endsWith(suffix));
    }) || "";
  }

  _entities(channel = this._channelKey()) {
    const c = channel;
    return {
      temperature: this._domainEntity("sensor", "_temperature"),
      humidity: this._domainEntity("sensor", "_humidity"),
      tank_remaining: this._domainEntity("sensor", "_tank_remaining"),
      tank_level: this._domainEntity("sensor", "_tank_level"),
      tank_days_left: this._domainEntity("sensor", "_tank_days_left"),
      connection_problem: this._domainEntity("binary_sensor", "_connection_problem"),
      water_warning: this._domainEntity("binary_sensor", "_water_warning"),
      device_locked: this._domainEntity("binary_sensor", "_device_locked"),
      name: this._domainEntity("text", `_plant_name_${c}`),
      photo_url: this._domainEntity("text", `_plant_photo_url_${c}`),
      plant_configured: this._domainEntity("binary_sensor", `_plant_${c}_configured`),
      moisture: this._domainEntity("sensor", `_moisture_${c}`),
      mode: this._domainEntity("select", `_watering_mode_${c}`),
      next_watering: this._domainEntity("sensor", `_next_watering_${c}`),
      last_watering: this._domainEntity("sensor", `_last_watering_${c}`),
      history_count: this._domainEntity("sensor", `_history_count_${c}`),
      pump: this._domainEntity("binary_sensor", [`_pump_${c}_open`, `_pump_${c}`]),
      outlet_blocked: this._domainEntity("binary_sensor", `_outlet_${c}_blocked`),
      outlet_locked: this._domainEntity("binary_sensor", `_outlet_${c}_locked`),
      sensor_fault: this._domainEntity("binary_sensor", `_sensor_${c}_fault`),
      sensor_disconnected: this._domainEntity("binary_sensor", `_sensor_${c}_disconnected`),
      watering_issue: this._domainEntity("binary_sensor", `_watering_issue_${c}`),
      watering_locked: this._domainEntity("binary_sensor", `_watering_locked_${c}`),
      manual_duration: this._domainEntity("number", `_manual_duration_seconds_${c}`),
      duration: this._domainEntity("number", `_duration_seconds_${c}`),
      interval: this._domainEntity("number", `_interval_hours_${c}`),
      smart_min_moisture: this._domainEntity("number", `_smart_min_moisture_${c}`),
      smart_max_moisture: this._domainEntity("number", `_smart_max_moisture_${c}`),
      smart_daytime_watering: this._domainEntity("switch", `_smart_daytime_watering_${c}`),
      first_watering_time: this._domainEntity("time", `_first_watering_time_${c}`),
      water: this._domainEntity("button", `_water_plant_${c}`),
      stop: this._domainEntity("button", `_stop_watering_${c}`),
      load_history: this._domainEntity("button", `_load_history_${c}`),
      add_plant: this._domainEntity("button", `_add_plant_${c}`),
      reset: this._domainEntity("button", `_reset_plant_${c}`),
      save_schedule: this._domainEntity("button", `_save_schedule_${c}`),
    };
  }

  _channelKey() {
    const value = String(this._config.channel || this._config.channel_id || "").toLowerCase();
    const pathMatch = window.location?.pathname?.toLowerCase().match(/growcube-plant-([abcd])(?:\/|$)/);
    const match = value.match(/[abcd]$/) || pathMatch;
    return match ? match[1] || match[0] : "a";
  }

  _isOn(entityId) {
    return this._state(entityId)?.state === "on";
  }

  _entityState(entityId, fallback = "Unknown") {
    const state = this._state(entityId);
    if (!state || ["unknown", "unavailable", ""].includes(state.state)) {
      return fallback;
    }
    return state.state;
  }

  _display(entityId, fallback = "Unknown") {
    const state = this._state(entityId);
    if (!state || ["unknown", "unavailable", ""].includes(state.state)) {
      return fallback;
    }
    return `${state.state}${state.attributes?.unit_of_measurement || ""}`;
  }

  _plantName(channel) {
    const entities = this._entities(channel);
    const name = this._entityState(entities.name, "");
    return name || `Plant ${channel.toUpperCase()}`;
  }

  _plantPhoto(channel) {
    return this._entityState(this._entities(channel).photo_url, "");
  }

  _isConfigured(channel) {
    const entity = this._entities(channel).plant_configured;
    return !entity || this._isOn(entity);
  }

  _problems(entities) {
    const items = [];
    if (this._isOn(entities.water_warning)) items.push("Water tank warning");
    if (this._isOn(entities.device_locked)) items.push("Device locked");
    if (this._isOn(entities.outlet_blocked)) items.push("Outlet blocked");
    if (this._isOn(entities.outlet_locked)) items.push("Outlet locked");
    if (this._isOn(entities.sensor_fault)) items.push("Sensor fault");
    if (this._isOn(entities.sensor_disconnected)) items.push("Sensor disconnected");
    if (this._isOn(entities.watering_issue)) items.push("Smart watering issue");
    if (this._isOn(entities.watering_locked)) items.push("Smart watering locked");
    return items;
  }

  _press(entityId) {
    if (this._hass && entityId && this._state(entityId)) {
      this._hass.callService("button", "press", { entity_id: entityId });
    }
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    }));
  }

  _navigate(channel) {
    const current = window.location?.pathname || "/lovelace/growcube";
    const base = current
      .replace(/\/growcube-plant-[abcd]\/?$/i, "")
      .replace(/\/growcube\/?$/i, "")
      .replace(/\/$/, "");
    const path = `${base || "/lovelace"}/growcube-plant-${channel}`;
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _render() {
    if (!this.shadowRoot) return;
    const dashboard = this._config.overview === "dashboard";
    const detail = Boolean(this._config.detail);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; width:100%; box-sizing:border-box; }
        ha-card { overflow:hidden; border-radius:14px; border:1px solid var(--divider-color); background:var(--ha-card-background,var(--card-background-color)); }
        ha-card.flat { max-width:1660px; margin:18px auto 0; background:transparent; border:0; box-shadow:none; overflow:visible; }
        .dashboard { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; padding:0 12px; }
        .column { display:grid; gap:14px; min-width:0; }
        .card { padding:18px; border-radius:14px; border:1px solid var(--divider-color); background:var(--ha-card-background,var(--card-background-color)); box-shadow:var(--ha-card-box-shadow,none); }
        .header { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:14px; align-items:center; }
        .plant-icon,.photo { width:42px; height:42px; border-radius:50%; display:grid; place-items:center; color:var(--primary-color); background:color-mix(in srgb,var(--primary-color) 16%,transparent); overflow:hidden; }
        .photo { width:64px; height:64px; border-radius:10px; }
        .photo img { width:100%; height:100%; object-fit:cover; }
        .title { font-size:20px; line-height:1.2; font-weight:650; color:var(--primary-text-color); }
        .subtitle,.label,.activity-empty { margin-top:4px; color:var(--secondary-text-color); font-size:13px; }
        .status { display:inline-grid; grid-template-columns:auto auto; gap:7px; align-items:center; color:var(--secondary-text-color); font-weight:600; }
        .dot { width:10px; height:10px; border-radius:50%; background:var(--success-color); }
        .dot.off { background:var(--error-color); }
        .stats { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:16px; }
        .stat { min-width:0; padding:12px; border-radius:10px; border:1px solid var(--divider-color); background:color-mix(in srgb,var(--primary-text-color) 5%,transparent); cursor:pointer; }
        .value { margin-top:5px; font-size:18px; line-height:1.25; color:var(--primary-text-color); word-break:break-word; }
        .value.big { font-size:34px; font-weight:730; line-height:1.05; }
        .plants-list { display:grid; gap:12px; margin-top:16px; }
        .plant-row { display:grid; grid-template-columns:64px minmax(0,1fr) auto; gap:12px; align-items:center; padding:12px; border:1px solid var(--divider-color); border-radius:10px; background:color-mix(in srgb,var(--primary-text-color) 5%,transparent); cursor:pointer; }
        .plant-row:hover,.stat:hover { background:color-mix(in srgb,var(--primary-color) 10%,transparent); }
        .plant-stats { text-align:right; color:var(--secondary-text-color); }
        .meter { margin-top:14px; }
        .meter-track { height:10px; border-radius:999px; overflow:hidden; background:color-mix(in srgb,var(--primary-text-color) 12%,transparent); }
        .meter-fill { height:100%; border-radius:inherit; background:var(--primary-color); }
        .activity-panel { padding:14px; border:1px solid var(--divider-color); border-radius:12px; background:color-mix(in srgb,var(--primary-text-color) 4%,transparent); }
        .activity-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; padding:10px 0; border-bottom:1px solid color-mix(in srgb,var(--primary-text-color) 10%,transparent); }
        .activity-row:last-child { border-bottom:0; }
        .activity-title { font-weight:650; color:var(--primary-text-color); }
        .activity-row.problem .activity-title { color:var(--error-color); }
        .detail { width:calc(100vw - 48px); max-width:960px; margin:22px auto 0; display:grid; gap:14px; }
        .detail-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
        .actions { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-top:16px; }
        button { min-height:44px; border:0; border-radius:10px; padding:0 14px; font:inherit; font-weight:650; cursor:pointer; color:var(--text-primary-color); background:var(--primary-color); }
        button.secondary { color:var(--primary-text-color); background:color-mix(in srgb,var(--primary-text-color) 9%,transparent); border:1px solid var(--divider-color); }
        button:disabled { opacity:.48; cursor:not-allowed; }
        .problem { margin-top:12px; padding:11px 12px; border-radius:10px; color:var(--primary-text-color); background:color-mix(in srgb,var(--error-color,#db4437) 14%,transparent); border:1px solid color-mix(in srgb,var(--error-color,#db4437) 40%,transparent); }
        .empty { padding:22px; color:var(--secondary-text-color); text-align:center; }
        @media (max-width:900px) { .dashboard,.detail-grid { grid-template-columns:1fr; } }
        @media (max-width:620px) { .plant-row { grid-template-columns:50px minmax(0,1fr); } .plant-stats { grid-column:2; text-align:left; } .stats,.actions { grid-template-columns:1fr; } }
      </style>
      ${dashboard ? this._dashboardTemplate() : detail ? this._detailTemplate() : this._legacyTemplate()}
    `;
    this._wire();
  }

  _dashboardTemplate() {
    const device = this._entities("a");
    const connected = !this._isOn(device.connection_problem);
    return `
      <ha-card class="flat">
        <div class="dashboard">
          <div class="column">
            <div class="card">
              <div class="header">
                <div class="plant-icon"><ha-icon icon="mdi:cube-outline"></ha-icon></div>
                <div>
                  <div class="title">GrowCube</div>
                  <div class="subtitle">${connected ? "Connected" : "Disconnected"}</div>
                </div>
                <div class="status"><span class="dot ${connected ? "" : "off"}"></span><span>${connected ? "Online" : "Offline"}</span></div>
              </div>
              <div class="stats">
                ${this._stat("Temperature", device.temperature)}
                ${this._stat("Humidity", device.humidity)}
                ${this._stat("Tank level", device.tank_level)}
                ${this._stat("Days left", device.tank_days_left)}
              </div>
              <div class="meter"><div class="meter-track"><div class="meter-fill" style="width:${this._numberState(device.tank_level)}%"></div></div></div>
            </div>
            <div class="card">
              <div class="title">Plants</div>
              <div class="plants-list">${["a","b","c","d"].map((c) => this._plantRow(c)).join("")}</div>
            </div>
          </div>
          <div class="column">
            <div class="card">
              <div class="title">Controls</div>
              <div class="plants-list">${["a","b","c","d"].map((c) => this._controlRow(c)).join("")}</div>
            </div>
            <div class="activity-panel">
              <div class="title">Activity</div>
              ${this._activityTemplate()}
            </div>
          </div>
        </div>
      </ha-card>
    `;
  }

  _detailTemplate() {
    const c = this._channelKey();
    const e = this._entities(c);
    const problems = this._problems(e);
    return `
      <ha-card class="flat">
        <div class="detail">
          <div class="card">
            <div class="header">
              ${this._photoTemplate(c)}
              <div>
                <div class="title">${this._escape(this._plantName(c))}</div>
                <div class="subtitle">Channel ${c.toUpperCase()} · ${this._entityState(e.mode, "Disabled")}</div>
              </div>
              <div class="value big">${this._display(e.moisture, "--")}</div>
            </div>
            ${problems.map((item) => `<div class="problem">${this._escape(item)}</div>`).join("")}
            <div class="actions">
              <button data-action="water" ${this._state(e.water) ? "" : "disabled"}>Water</button>
              <button class="secondary" data-action="stop" ${this._state(e.stop) ? "" : "disabled"}>Stop</button>
              <button class="secondary" data-action="history" ${this._state(e.load_history) ? "" : "disabled"}>History</button>
            </div>
          </div>
          <div class="detail-grid">
            <div class="card">
              <div class="title">Watering</div>
              <div class="stats">
                ${this._stat("Mode", e.mode)}
                ${this._stat("Next", e.next_watering)}
                ${this._stat("Last", e.last_watering)}
                ${this._stat("History", e.history_count)}
              </div>
            </div>
            <div class="card">
              <div class="title">Environment</div>
              <div class="stats">
                ${this._stat("Temperature", e.temperature)}
                ${this._stat("Humidity", e.humidity)}
                ${this._stat("Tank", e.tank_level)}
                ${this._stat("Water warning", e.water_warning)}
              </div>
            </div>
          </div>
        </div>
      </ha-card>
    `;
  }

  _legacyTemplate() {
    return this._dashboardTemplate();
  }

  _plantRow(channel) {
    const e = this._entities(channel);
    const configured = this._isConfigured(channel);
    return `
      <div class="plant-row" data-channel="${channel}">
        ${this._photoTemplate(channel)}
        <div>
          <div class="title">${configured ? this._escape(this._plantName(channel)) : "No plant added"}</div>
          <div class="subtitle">Channel ${channel.toUpperCase()} · ${this._entityState(e.mode, "Disabled")}</div>
        </div>
        <div class="plant-stats">
          <div class="value">${this._display(e.moisture, "--")}</div>
          <div class="label">${this._isOn(e.pump) ? "Watering" : "Idle"}</div>
        </div>
      </div>
    `;
  }

  _controlRow(channel) {
    const e = this._entities(channel);
    return `
      <div class="plant-row">
        <div class="plant-icon"><ha-icon icon="mdi:sprout"></ha-icon></div>
        <div>
          <div class="title">${this._escape(this._plantName(channel))}</div>
          <div class="subtitle">${this._display(e.moisture, "--")} · ${this._entityState(e.mode, "Disabled")}</div>
        </div>
        <div class="actions">
          <button data-button="${e.water}" ${this._state(e.water) ? "" : "disabled"}>Water</button>
          <button class="secondary" data-button="${e.stop}" ${this._state(e.stop) ? "" : "disabled"}>Stop</button>
        </div>
      </div>
    `;
  }

  _activityTemplate() {
    const rows = [];
    for (const c of ["a", "b", "c", "d"]) {
      const e = this._entities(c);
      this._problems(e).forEach((problem) => rows.push({ title: problem, detail: `Channel ${c.toUpperCase()}`, problem: true }));
      const last = this._entityState(e.last_watering, "");
      if (last) rows.push({ title: "Last watering", detail: `Channel ${c.toUpperCase()}`, time: last });
    }
    if (!rows.length) {
      return '<div class="activity-empty">No watering or active errors yet</div>';
    }
    return rows.slice(0, 6).map((row) => `
      <div class="activity-row ${row.problem ? "problem" : ""}">
        <div><div class="activity-title">${this._escape(row.title)}</div><div class="subtitle">${this._escape(row.detail)}</div></div>
        <div class="subtitle">${this._escape(this._shortTime(row.time || ""))}</div>
      </div>
    `).join("");
  }

  _stat(label, entityId) {
    return `<div class="stat" data-info="${entityId || ""}"><div class="label">${this._escape(label)}</div><div class="value">${this._escape(this._display(entityId))}</div></div>`;
  }

  _photoTemplate(channel) {
    const url = this._plantPhoto(channel);
    return `<div class="photo">${url ? `<img src="${this._escape(url)}" alt="">` : '<ha-icon icon="mdi:flower"></ha-icon>'}</div>`;
  }

  _numberState(entityId) {
    const value = Number(this._state(entityId)?.state);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, value));
  }

  _shortTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  _wire() {
    this.shadowRoot.querySelectorAll("[data-channel]").forEach((element) => {
      element.addEventListener("click", () => this._navigate(element.dataset.channel));
    });
    this.shadowRoot.querySelectorAll("[data-button]").forEach((element) => {
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        this._press(element.dataset.button);
      });
    });
    this.shadowRoot.querySelectorAll("[data-info]").forEach((element) => {
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        this._moreInfo(element.dataset.info);
      });
    });
    const current = this._entities();
    this.shadowRoot.querySelector('[data-action="water"]')?.addEventListener("click", () => this._press(current.water));
    this.shadowRoot.querySelector('[data-action="stop"]')?.addEventListener("click", () => this._press(current.stop));
    this.shadowRoot.querySelector('[data-action="history"]')?.addEventListener("click", () => this._press(current.load_history));
  }

  _sanitize(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  }

  _escape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
}

customElements.define("growcube-card", GrowcubeCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "growcube-card",
  name: "GrowCube Card",
  description: "GrowCube MQTT dashboard card",
});
