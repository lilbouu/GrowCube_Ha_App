const GROWCUBE_CARD_VERSION = "0.2.81-channel-config-post";
const GROWCUBE_ADDON_API_URL = "__GROWCUBE_ADDON_API_URL__";

class GrowcubeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._wateringOpen = false;
    this._wateringSeconds = 25;
    this._reservoirOpen = false;
    this._reservoirAmount = 1500;
    this._reservoirTargetEntity = "";
    this._reservoirGuideOpen = false;
    this._reservoirGuideStep = 0;
    this._reservoirGuideDontShow = false;
    this._pendingReservoirEntity = "";
    this._pendingReservoirAmount = undefined;
    this._editDialog = null;
    this._detailMenuOpen = false;
    this._aboutDialogOpen = false;
    this._deletePlantDialogOpen = false;
    this._aboutProfileCache = {};
    this._aboutProfileLoading = false;
    this._addonApiUrlCache = undefined;
    this._plantWizardOpen = false;
    this._plantWizardStep = 0;
    this._plantWizardName = "";
    this._plantWizardPhotoUrl = "";
    this._plantWizardChannel = "a";
    this._plantWizardMode = "Smart";
    this._plantWizardAmount = 50;
    this._plantWizardIntervalDays = 1;
    this._plantWizardStartHour = 8;
    this._plantWizardStartMinute = 0;
    this._plantWizardSmartMin = 20;
    this._plantWizardSmartMax = 60;
    this._plantWizardDaytime = true;
    this._plantWizardCategory = "";
    this._plantWizardDescription = "";
    this._plantWizardTempMin = 0;
    this._plantWizardTempMax = 0;
    this._plantWizardAirHumidityMin = 0;
    this._plantWizardAirHumidityMax = 0;
    this._plantWizardPhotoUploading = false;
    this._plantWizardPhotoFileName = "";
    this._plantWizardSearch = "";
    this._plantWizardResults = [];
    this._plantWizardResultPage = 0;
    this._plantWizardSelected = null;
    this._plantWizardCustom = false;
    this._plantWizardCreateCustomOnly = false;
    this._plantWizardLoading = false;
    this._plantWizardError = "";
    this._customPlantsOpen = false;
    this._customPlantsPage = 0;
    this._history = [];
    this._historyEntity = "";
    this._historyLoading = false;
    this._historyError = "";
    this._historyLoadedAt = 0;
    this._historyWindowHours = 72;
    this._cubeHistoryRequestedAt = {};
    this._cubeHistory = {};
    this._cubeHistoryLoading = {};
    this._cubeHistoryLoadedAt = {};
    this._cubeHistoryPollTimer = null;
    this._dashboardDevices = [];
    this._dashboardDevicesLoadedAt = 0;
    this._dashboardDevicesLoading = false;
    this._deviceMenuOpen = false;
    this._toast = "";
    this._toastTimer = null;
    this._dismissedProblems = this._loadDismissedProblems();
    this._modeWizardOpen = false;
    this._modeWizardStep = 0;
    this._modeWizardMode = "Smart";
    this._modeWizardAmount = 50;
    this._modeWizardIntervalDays = 1;
    this._modeWizardStartHour = 8;
    this._modeWizardStartMinute = 0;
    this._modeWizardSmartMin = 20;
    this._modeWizardSmartMax = 60;
    this._modeWizardDaytime = true;
  }

  setConfig(config) {
    if (!config.entities && !config.channel && !config.overview && !config.graph) {
      throw new Error("GrowCube card requires channel, overview, graph, or entities");
    }
    this._config = {
      ...config,
      entities: config.entities || {},
    };
    this._historyWindowHours = this._clamp(Number(config.hours_to_show) || this._historyWindowHours || 72, 1, 168);
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._hasActiveInputDialog()) {
      return;
    }
    this._render();
  }

  getCardSize() {
    if (this._config.graph) {
      return 4;
    }
    return this._config.detail ? 8 : 3;
  }

  _state(entityId) {
    return entityId && this._hass ? this._hass.states[entityId] : undefined;
  }

  _looksLikeEntityId(value) {
    return /^[a-z_]+\.[a-z0-9_]+$/i.test(String(value || "").trim());
  }

  _selectedDeviceId() {
    const queryDevice = new URLSearchParams(window.location?.search || "").get("device");
    const configured = this._config.device_id || this._config.device_prefix || this._config.entity_prefix || this._config.device || "";
    return String(queryDevice || configured || this._deviceIdHint()).trim();
  }

  _deviceRecord(deviceId = this._selectedDeviceId()) {
    if (!deviceId) {
      return this._dashboardDevices[0];
    }
    return this._dashboardDevices.find((item) => item.device_id === deviceId) || this._dashboardDevices[0];
  }

  _applyOptimisticChannelMetadata(channel, values = {}) {
    let record = this._deviceRecord();
    if (!record) {
      record = {
        device_id: this._selectedDeviceId() || this._deviceIdHint() || "growcube",
        name: this._selectedDeviceId() || this._deviceIdHint() || "GrowCube",
        entities: {},
        channels: {},
      };
      this._dashboardDevices = [record];
    }
    record.channels = record.channels || {};
    const hasPlantId = Object.prototype.hasOwnProperty.call(values, "plant_id");
    record.channels[channel] = {
      ...(record.channels[channel] || {}),
      plant_id: hasPlantId ? Number(values.plant_id) || 0 : record.channels[channel]?.plant_id || 0,
      plant_name: values.plant_name || record.channels[channel]?.plant_name || "",
      photo_url: this._plantImageUrl(values.photo_url || record.channels[channel]?.photo_url || ""),
      image_url: this._plantImageUrl(values.image_url || values.photo_url || record.channels[channel]?.image_url || record.channels[channel]?.photo_url || ""),
      configured: values.configured ?? record.channels[channel]?.configured ?? true,
    };
    this._rememberPlantPhotoUrl(record.device_id, channel, record.channels[channel].image_url || record.channels[channel].photo_url);
  }

  _clearOptimisticChannelMetadata(channel = this._channelKey(), deviceId = this._selectedDeviceId()) {
    const record = this._deviceRecord(deviceId);
    if (!record?.channels?.[channel]) {
      this._forgetPlantPhotoUrl(deviceId || record?.device_id, channel);
      return;
    }
    const previous = record.channels[channel] || {};
    record.channels[channel] = {
      ...previous,
      plant_name: "",
      photo_url: "",
      photo_url_value: "",
      image_url: "",
      plant_id: 0,
      configured: false,
    };
    this._forgetPlantPhotoUrl(record.device_id || deviceId, channel);
  }

  _deviceRecords() {
    return this._dashboardDevices.length ? this._dashboardDevices : [this._fallbackDeviceRecord()].filter(Boolean);
  }

  _setSelectedDevice(deviceId) {
    const value = String(deviceId || "").trim();
    if (!value) {
      return;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("device", value);
    window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`);
    this._historyEntity = "";
    this._historyLoadedAt = 0;
    this._cubeHistory = {};
    this._cubeHistoryLoadedAt = {};
    this._cubeHistoryRequestedAt = {};
    this._deviceMenuOpen = false;
    this._render();
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _fallbackDeviceRecord() {
    const deviceId = this._deviceIdHint();
    if (!deviceId) {
      return undefined;
    }
    return {
      device_id: deviceId,
      name: deviceId.replace(/^growcube_/, "GrowCube "),
      entities: {},
      channels: {},
    };
  }

  async _loadDashboardDevicesIfNeeded(force = false) {
    if (!this._hass) {
      return;
    }
    const cacheMs = 30 * 1000;
    if (!force && this._dashboardDevicesLoadedAt && Date.now() - this._dashboardDevicesLoadedAt < cacheMs) {
      return;
    }
    if (this._dashboardDevicesLoading) {
      return;
    }
    this._dashboardDevicesLoading = true;
    try {
      const previousDevices = this._dashboardDevices;
      const result = await this._dashboardApi();
      let devices = Array.isArray(result?.devices) ? result.devices : [];
      if (!devices.length || !this._dashboardHasKnownEntities(devices)) {
        devices = this._discoverMqttDashboardDevices();
      }
      this._dashboardDevices = this._mergeDashboardDeviceMetadata(devices, previousDevices);
      this._dashboardDevicesLoadedAt = Date.now();
    } catch (error) {
      this._dashboardDevices = this._mergeDashboardDeviceMetadata(this._discoverMqttDashboardDevices(), this._dashboardDevices);
      this._dashboardDevicesLoadedAt = Date.now();
    } finally {
      this._dashboardDevicesLoading = false;
      this._renderAfterBackgroundUpdate();
    }
  }

  async _dashboardApi() {
    try {
      const addonResult = await this._fetchAddonApi("dashboard");
      if (addonResult) {
        return addonResult;
      }
    } catch (error) {
      // Fall back to the legacy HACS API or MQTT entity discovery below.
    }
    if (this._hass?.callApi) {
      return this._hass.callApi("GET", "growcube/dashboard");
    }
    return {};
  }

  async _fetchAddonApi(path, retried = false, requestOptions = {}) {
    const baseUrl = await this._addonApiUrl();
    if (!baseUrl) {
      console.warn("[GrowCube] add-on API URL is unavailable", { path });
      return undefined;
    }
    const url = `${baseUrl}/${String(path).replace(/^\/+/, "")}`;
    console.info("[GrowCube] add-on API request", { url });
    const started = performance.now();
    const response = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Accept": "application/json",
        ...(requestOptions.body ? { "Content-Type": "application/json" } : {}),
      },
      ...requestOptions,
    });
    if (!response.ok) {
      const body = await response.text();
      if (!retried && [401, 403, 404, 503].includes(response.status)) {
        this._addonApiUrlCache = undefined;
        const refreshedUrl = await this._discoverAddonApiUrl();
        if (refreshedUrl && refreshedUrl !== baseUrl) {
          this._addonApiUrlCache = refreshedUrl;
          console.info("[GrowCube] retrying add-on API with refreshed ingress URL", { oldUrl: baseUrl, newUrl: refreshedUrl });
          return this._fetchAddonApi(path, true, requestOptions);
        }
      }
      throw new Error(`GrowCube add-on API failed: ${response.status} ${response.statusText}: ${body.slice(0, 240)}`);
    }
    const body = await response.text();
    if (String(path).replace(/^\/+/, "").startsWith("plants/search")) {
      console.info("[GrowCube] add-on API raw response", {
        url,
        elapsedMs: Math.round(performance.now() - started),
        body,
      });
    }
    let result;
    try {
      result = body ? JSON.parse(body) : {};
    } catch (error) {
      throw new Error(`GrowCube add-on API returned non-JSON: ${url}: ${body.slice(0, 240)}`);
    }
    console.info("[GrowCube] add-on API response", { url, elapsedMs: Math.round(performance.now() - started), keys: Object.keys(result || {}) });
    return result;
  }

  async _postAddonApi(path, payload = {}) {
    const baseUrl = await this._addonApiUrl();
    if (!baseUrl) {
      throw new Error("Add-on API URL is unavailable");
    }
    const cleanPath = String(path).replace(/^\/+/, "");
    const url = `${baseUrl}/${cleanPath}`;
    const started = performance.now();
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`GrowCube add-on API failed: ${response.status} ${response.statusText}: ${body.slice(0, 240)}`);
    }
    const body = await response.text();
    const result = body ? JSON.parse(body) : {};
    console.info("[GrowCube] add-on API POST response", { url, elapsedMs: Math.round(performance.now() - started), keys: Object.keys(result || {}) });
    return result;
  }

  async _addonApiUrl() {
    if (this._addonApiUrlCache) {
      return this._addonApiUrlCache;
    }
    const value = String(this._config.addon_api_url || "").trim();
    if (value && value !== "__GROWCUBE_ADDON_API_URL__") {
      this._addonApiUrlCache = this._normalizeAddonApiUrl(value);
      console.info("[GrowCube] using configured add-on API URL", { url: this._addonApiUrlCache });
      return this._addonApiUrlCache;
    }
    const discoveredUrl = await this._discoverAddonApiUrl();
    if (discoveredUrl) {
      this._addonApiUrlCache = discoveredUrl;
      console.info("[GrowCube] discovered add-on API URL", { url: discoveredUrl });
      return discoveredUrl;
    }
    const directUrl = this._directAddonApiUrl();
    if (directUrl) {
      this._addonApiUrlCache = directUrl;
      console.info("[GrowCube] using direct add-on API URL", { url: this._addonApiUrlCache });
      return this._addonApiUrlCache;
    }
    const entityUrl = this._addonApiUrlFromState();
    if (entityUrl) {
      this._addonApiUrlCache = entityUrl;
      console.info("[GrowCube] using add-on API URL from entity state", { url: this._addonApiUrlCache });
      return this._addonApiUrlCache;
    }
    const bundled = String(window.GROWCUBE_ADDON_API_URL || GROWCUBE_ADDON_API_URL || "").trim();
    if (bundled && bundled !== "__GROWCUBE_ADDON_API_URL__") {
      this._addonApiUrlCache = this._normalizeAddonApiUrl(bundled);
      console.info("[GrowCube] using bundled add-on API URL", { url: this._addonApiUrlCache });
      return this._addonApiUrlCache;
    }
    return "";
  }

  _directAddonApiUrl() {
    const configured = this._config.direct_addon_api_url || window.GROWCUBE_DIRECT_ADDON_API_URL || "";
    const configuredUrl = this._normalizeAddonApiUrl(configured);
    if (configuredUrl) {
      return configuredUrl;
    }
    const { protocol, hostname } = window.location || {};
    if (!hostname) {
      return "";
    }
    return `${protocol || "http:"}//${hostname}:8099`;
  }

  _addonApiUrlFromState() {
    const records = this._deviceRecords();
    for (const record of records) {
      const url = this._normalizeAddonApiUrl(record?.addon_api_url || "");
      if (url) {
        return url;
      }
    }
    for (const channel of this._channels()) {
      const state = this._state(this._entities(channel).history_count);
      const url = this._normalizeAddonApiUrl(state?.attributes?.addon_api_url || "");
      if (url) {
        return url;
      }
    }
    if (this._hass?.states) {
      for (const state of Object.values(this._hass.states)) {
        const url = this._normalizeAddonApiUrl(state?.attributes?.addon_api_url || "");
        if (url) {
          return url;
        }
      }
    }
    return "";
  }

  async _discoverAddonApiUrl() {
    if (!this._hass?.callApi) {
      return "";
    }
    const slugs = [
      this._config.addon_slug,
      window.GROWCUBE_ADDON_SLUG,
      "growcube",
      "local_growcube",
    ].filter(Boolean);
    for (const slug of [...new Set(slugs.map((item) => String(item).trim()).filter(Boolean))]) {
      const url = await this._addonInfoUrl(slug);
      if (url) {
        console.info("[GrowCube] matched add-on slug", { slug, url });
        return url;
      }
    }
    try {
      const result = await this._hass.callApi("GET", "hassio/addons");
      const addons = Array.isArray(result?.data?.addons) ? result.data.addons : (Array.isArray(result?.addons) ? result.addons : []);
      const match = addons.find((item) => {
        const slug = String(item?.slug || "");
        const name = String(item?.name || "");
        return slug === "growcube" || slug.endsWith("_growcube") || name.toLowerCase() === "growcube";
      });
      if (match?.slug) {
        const url = await this._addonInfoUrl(match.slug);
        if (url) {
          console.info("[GrowCube] matched add-on from Supervisor list", { slug: match.slug, url });
          return url;
        }
      }
    } catch (error) {
      // Supervisor add-on discovery is unavailable for this frontend session.
    }
    return "";
  }

  async _addonInfoUrl(slug) {
    try {
      const result = await this._hass.callApi("GET", `hassio/addons/${encodeURIComponent(slug)}/info`);
      const data = result?.data || result || {};
      return this._normalizeAddonApiUrl(data.ingress_url || data.ingress_entry || data.ingress_path || "");
    } catch (error) {
      console.warn("[GrowCube] add-on info lookup failed", { slug, error: error?.message || String(error) });
      return "";
    }
  }

  _normalizeAddonApiUrl(value) {
    const text = String(value || "").trim();
    if (!text || text === "__GROWCUBE_ADDON_API_URL__") {
      return "";
    }
    if (/^https?:\/\//i.test(text)) {
      try {
        const url = new URL(text);
        return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
      } catch (error) {
        return "";
      }
    }
    if (text.startsWith("/")) {
      return text.replace(/\/+$/, "");
    }
    if (text.startsWith("api/hassio_ingress/")) {
      return `/${text.replace(/\/+$/, "")}`;
    }
    return `/api/hassio_ingress/${text.replace(/^\/+|\/+$/g, "")}`;
  }

  _discoverMqttDashboardDevices() {
    if (!this._hass?.states) {
      return [];
    }
    const prefixes = new Set();
    Object.keys(this._hass.states).forEach((entityId) => {
      const objectId = entityId.split(".", 2)[1] || "";
      [
        "_temperature",
        "_humidity",
        "_tank_remaining",
        "_tank_level",
        "_moisture_a",
        "_moisture_b",
        "_moisture_c",
        "_moisture_d",
        "_load_history_a",
        "_water_plant_a",
      ].some((suffix) => {
        if (objectId.startsWith("growcube_") && objectId.endsWith(suffix)) {
          prefixes.add(objectId.slice(0, -suffix.length));
          return true;
        }
        return false;
      });
    });
    return Array.from(prefixes)
      .sort()
      .map((prefix) => this._mqttDashboardDevice(prefix))
      .filter((device) => Object.values(device.entities).some(Boolean) || Object.values(device.channels).some((channel) => Object.values(channel).some(Boolean)));
  }

  _dashboardHasKnownEntities(devices) {
    if (!this._hass?.states || !Array.isArray(devices)) {
      return false;
    }
    return devices.some((device) => {
      const deviceEntities = Object.values(device?.entities || {});
      const channelEntities = Object.values(device?.channels || {})
        .flatMap((channel) => Object.values(channel || {}));
      return deviceEntities.concat(channelEntities).some((entityId) => entityId && this._hass.states[entityId]);
    });
  }

  _mergeDashboardDeviceMetadata(devices, previousDevices = []) {
    if ((!devices || !devices.length) && previousDevices?.length) {
      return previousDevices;
    }
    const previousById = new Map((previousDevices || []).map((device) => [device?.device_id, device]));
    return (devices || []).map((device) => {
      const previous = previousById.get(device?.device_id) || {};
      const channels = { ...(device.channels || {}) };
      this._channels().forEach((channel) => {
        const nextChannel = channels[channel] || {};
        const previousChannel = previous.channels?.[channel] || {};
        const hasConfigured = Object.prototype.hasOwnProperty.call(nextChannel, "configured");
        const hasPlantId = Object.prototype.hasOwnProperty.call(nextChannel, "plant_id");
        const configured = hasConfigured ? Boolean(nextChannel.configured) : previousChannel.configured;
        if (hasConfigured && !configured) {
          channels[channel] = {
            ...nextChannel,
            plant_name: "",
            photo_url: "",
            photo_url_value: "",
            image_url: "",
            plant_id: 0,
            photo_url_entity: nextChannel.photo_url_entity || previousChannel.photo_url_entity || "",
            configured: false,
          };
          this._forgetPlantPhotoUrl(device?.device_id, channel);
          return;
        }
        channels[channel] = {
          ...nextChannel,
          plant_id: hasPlantId ? Number(nextChannel.plant_id) || 0 : Number(previousChannel.plant_id) || 0,
          plant_name: nextChannel.plant_name || previousChannel.plant_name || "",
          photo_url: nextChannel.photo_url_value || nextChannel.photo_url || previousChannel.photo_url_value || previousChannel.photo_url || "",
          photo_url_value: nextChannel.photo_url_value || previousChannel.photo_url_value || "",
          image_url: nextChannel.image_url || previousChannel.image_url || nextChannel.photo_url || previousChannel.photo_url || "",
          photo_url_entity: nextChannel.photo_url_entity || previousChannel.photo_url_entity || "",
          configured,
        };
        this._rememberPlantPhotoUrl(device?.device_id, channel, channels[channel].image_url || channels[channel].photo_url);
      });
      return {
        ...device,
        channels,
      };
    });
  }

  _mqttDashboardDevice(prefix) {
    const temperature = this._mqttEntity("sensor", prefix, "temperature");
    const name = this._state(temperature)?.attributes?.friendly_name
      ? this._state(temperature).attributes.friendly_name.replace(/\s+Temperature$/i, "")
      : prefix.replace(/^growcube_/, "GrowCube ").replace(/_/g, ".");
    return {
      device_id: prefix,
      host: prefix.replace(/^growcube_/, "").replace(/_/g, "."),
      name,
      connected: this._entityState(this._mqttEntity("binary_sensor", prefix, "connection_problem"), "OFF") !== "ON",
      entities: {
        temperature,
        humidity: this._mqttEntity("sensor", prefix, "humidity"),
        connection_problem: this._mqttEntity("binary_sensor", prefix, "connection_problem"),
        water_warning: this._mqttEntity("binary_sensor", prefix, "water_warning"),
        device_locked: this._mqttEntity("binary_sensor", prefix, "device_locked"),
        tank_remaining: this._mqttEntity("sensor", prefix, "tank_remaining"),
        tank_level: this._mqttEntity("sensor", prefix, "tank_level"),
        tank_days_left: this._mqttEntity("sensor", prefix, "tank_days_left"),
        tank_capacity: this._mqttEntity("number", prefix, "tank_capacity"),
        mark_tank_full: this._mqttEntity("button", prefix, "mark_tank_full"),
      },
      channels: Object.fromEntries(this._channels().map((channel) => [channel, this._mqttChannelEntities(prefix, channel)])),
    };
  }

  _mqttChannelEntities(prefix, channel) {
    return {
      name: this._mqttEntity("text", prefix, `plant_name_${channel}`),
      photo_url: this._mqttEntity("text", prefix, `plant_photo_url_${channel}`),
      plant_configured: this._mqttEntity("binary_sensor", prefix, `plant_${channel}_configured`),
      moisture: this._mqttEntity("sensor", prefix, `moisture_${channel}`),
      last_watering: this._mqttEntity("sensor", prefix, `last_watering_${channel}`),
      history_count: this._mqttEntity("sensor", prefix, `history_count_${channel}`),
      next_watering: this._mqttEntity("sensor", prefix, `next_watering_${channel}`),
      mode: this._mqttEntity("select", prefix, `watering_mode_${channel}`),
      first_watering_time: this._mqttEntity("time", prefix, `first_watering_time_${channel}`)
        || this._mqttEntity("text", prefix, `first_watering_time_${channel}`),
      duration: this._mqttEntity("number", prefix, `duration_seconds_${channel}`),
      interval: this._mqttEntity("number", prefix, `interval_hours_${channel}`),
      smart_min_moisture: this._mqttEntity("number", prefix, `smart_min_moisture_${channel}`),
      smart_max_moisture: this._mqttEntity("number", prefix, `smart_max_moisture_${channel}`),
      smart_daytime_watering: this._mqttEntity("switch", prefix, `smart_daytime_watering_${channel}`),
      manual_duration: this._mqttEntity("number", prefix, `manual_duration_seconds_${channel}`),
      add_plant: this._mqttEntity("button", prefix, `add_plant_${channel}`),
      load_history: this._mqttEntity("button", prefix, `load_history_${channel}`),
      save: this._mqttEntity("button", prefix, `save_schedule_${channel}`),
      reset: this._mqttEntity("button", prefix, `reset_plant_${channel}`),
      water: this._mqttEntity("button", prefix, `water_plant_${channel}`),
      stop: this._mqttEntity("button", prefix, `stop_watering_${channel}`),
      outlet_blocked: this._mqttEntity("binary_sensor", prefix, `outlet_${channel}_blocked`),
      outlet_locked: this._mqttEntity("binary_sensor", prefix, `outlet_${channel}_locked`),
      sensor_fault: this._mqttEntity("binary_sensor", prefix, `sensor_${channel}_fault`),
      sensor_disconnected: this._mqttEntity("binary_sensor", prefix, `sensor_${channel}_disconnected`),
      watering_issue: this._mqttEntity("binary_sensor", prefix, `watering_issue_${channel}`),
      watering_locked: this._mqttEntity("binary_sensor", prefix, `watering_locked_${channel}`),
    };
  }

  _mqttEntity(domain, prefix, key) {
    const entityId = `${domain}.${prefix}_${key}`;
    return this._hass?.states?.[entityId] ? entityId : "";
  }

  _channelKey(channelValue = undefined) {
    const explicit = channelValue ?? this._config.channel ?? this._config.channel_id;
    const pathMatch = window.location?.pathname?.toLowerCase().match(/growcube-plant-([abcd])(?:\/|$)/);
    const channel = String(explicit ?? pathMatch?.[1] ?? "A").trim().toLowerCase();
    if (/^[0-3]$/.test(channel)) {
      return "abcd"[Number(channel)];
    }
    const numericMatch = channel.match(/(?:channel|plant)?\s*([0-3])$/);
    if (numericMatch) {
      return "abcd"[Number(numericMatch[1])];
    }
    const match = channel.match(/[abcd]$/);
    return match ? match[0] : "a";
  }

  _channelLabel() {
    return this._channelKey().toUpperCase();
  }

  _entityBySuffix(domain, suffixes) {
    if (!this._hass) {
      return undefined;
    }
    const wanted = (Array.isArray(suffixes) ? suffixes : [suffixes]).flatMap((suffix) => {
      const text = String(suffix);
      return text.startsWith("_") ? [text, text.slice(1)] : [text];
    });
    const prefix = this._config.entity_prefix || this._config.device_prefix || this._config.device || "";
    return Object.keys(this._hass.states)
      .sort()
      .find((entityId) => {
        if (!entityId.startsWith(`${domain}.`)) {
          return false;
        }
        const objectId = entityId.slice(domain.length + 1);
        if (prefix && !objectId.startsWith(prefix) && !objectId.startsWith(`growcube_${prefix}`)) {
          return false;
        }
        return wanted.some((suffix) => objectId.endsWith(suffix));
      });
  }

  _entityBySuffixFiltered(domain, suffixes, { includes = [], excludes = [] } = {}) {
    if (!this._hass) {
      return undefined;
    }
    const wanted = (Array.isArray(suffixes) ? suffixes : [suffixes]).flatMap((suffix) => {
      const text = String(suffix);
      return text.startsWith("_") ? [text, text.slice(1)] : [text];
    });
    const includeList = (Array.isArray(includes) ? includes : [includes]).filter(Boolean).map(String);
    const excludeList = (Array.isArray(excludes) ? excludes : [excludes]).filter(Boolean).map(String);
    const prefix = this._config.entity_prefix || this._config.device_prefix || this._config.device || "";
    return Object.keys(this._hass.states)
      .sort()
      .find((entityId) => {
        if (!entityId.startsWith(`${domain}.`)) {
          return false;
        }
        const objectId = entityId.slice(domain.length + 1);
        if (prefix && !objectId.startsWith(prefix) && !objectId.startsWith(`growcube_${prefix}`)) {
          return false;
        }
        if (!wanted.some((suffix) => objectId.endsWith(suffix))) {
          return false;
        }
        if (includeList.length && !includeList.some((text) => objectId.includes(text))) {
          return false;
        }
        if (excludeList.some((text) => objectId.includes(text))) {
          return false;
        }
        return true;
      });
  }

  _entityFromPeer(domain, peerEntityId, fromSuffixes, toSuffix) {
    if (!peerEntityId || !this._hass) {
      return undefined;
    }
    const [peerDomain, objectId] = peerEntityId.split(".", 2);
    if (!peerDomain || !objectId) {
      return undefined;
    }
    for (const fromSuffix of fromSuffixes) {
      if (objectId.endsWith(fromSuffix)) {
        const candidate = `${domain}.${objectId.slice(0, -fromSuffix.length)}${toSuffix}`;
        if (this._hass.states[candidate]) {
          return candidate;
        }
      }
    }
    return undefined;
  }

  _entities(channelValue = undefined) {
    const channel = this._channelKey(channelValue);
    const explicit = this._config.entities || {};
    const selectedDevice = this._deviceRecord();
    const mappedChannelEntities = selectedDevice?.channels?.[channel] || {};
    const mappedDeviceEntities = selectedDevice?.entities || {};
    const moisture = explicit.moisture || this._entityBySuffix("sensor", `_moisture_${channel}`);
    const loadHistory = explicit.load_history || this._entityBySuffix("button", `_load_history_${channel}`);
    const historyCount = explicit.history_count
      || this._entityBySuffix("sensor", [
        `_history_count_${channel}`,
        `_history_${channel}`,
        `_moisture_history_${channel}`,
      ])
      || this._entityFromPeer("sensor", loadHistory, [`_load_history_${channel}`], `_history_count_${channel}`)
      || this._entityFromPeer("sensor", moisture, [`_moisture_${channel}`], `_history_count_${channel}`);
    const entities = {
      name: mappedChannelEntities.name || this._entityBySuffix("text", `_plant_name_${channel}`),
      photo_url: mappedChannelEntities.photo_url_entity
        || (this._looksLikeEntityId(mappedChannelEntities.photo_url) ? mappedChannelEntities.photo_url : "")
        || this._entityBySuffix("text", `_plant_photo_url_${channel}`),
      plant_configured: mappedChannelEntities.plant_configured || this._entityBySuffix("binary_sensor", [
        `_plant_${channel}_configured`,
        `_plant_configured_${channel}`,
      ]),
      configured: mappedChannelEntities.configured,
      moisture: mappedChannelEntities.moisture || moisture,
      last_watering: mappedChannelEntities.last_watering || this._entityBySuffix("sensor", `_last_watering_${channel}`),
      next_watering: mappedChannelEntities.next_watering || this._entityBySuffix("sensor", `_next_watering_${channel}`),
      mode: mappedChannelEntities.mode || this._entityBySuffix("select", `_watering_mode_${channel}`),
      first_watering_time: mappedChannelEntities.first_watering_time
        || this._entityBySuffix("time", `_first_watering_time_${channel}`)
        || this._entityBySuffix("text", `_first_watering_time_${channel}`),
      duration: mappedChannelEntities.duration || this._entityBySuffixFiltered(
        "number",
        [`_watering_amount_${channel}`, `_duration_seconds_${channel}`],
        { excludes: ["_manual_"] },
      ),
      interval: mappedChannelEntities.interval || this._entityBySuffix("number", [`_watering_interval_${channel}`, `_interval_hours_${channel}`]),
      smart_min_moisture: mappedChannelEntities.smart_min_moisture || this._entityBySuffix("number", [
        `_smart_min_moisture_${channel}`,
        `_minimum_moisture_${channel}`,
      ]),
      smart_max_moisture: mappedChannelEntities.smart_max_moisture || this._entityBySuffix("number", [
        `_smart_max_moisture_${channel}`,
        `_maximum_moisture_${channel}`,
      ]),
      smart_daytime_watering: mappedChannelEntities.smart_daytime_watering || this._entityBySuffix("switch", [
        `_smart_daytime_watering_${channel}`,
        `_daytime_watering_${channel}`,
      ]),
      history_count: mappedChannelEntities.history_count || historyCount,
      manual_duration: mappedChannelEntities.manual_duration || this._entityBySuffixFiltered(
        "number",
        [`_manual_watering_amount_${channel}`, `_manual_duration_seconds_${channel}`],
        { includes: ["_manual_"] },
      ),
      add_plant: mappedChannelEntities.add_plant || this._entityBySuffix("button", `_add_plant_${channel}`),
      load_history: mappedChannelEntities.load_history || loadHistory,
      save: mappedChannelEntities.save || this._entityBySuffix("button", `_save_schedule_${channel}`),
      reset: mappedChannelEntities.reset || this._entityBySuffix("button", `_reset_plant_${channel}`),
      water: mappedChannelEntities.water || this._entityBySuffix("button", `_water_plant_${channel}`),
      stop: mappedChannelEntities.stop || this._entityBySuffix("button", `_stop_watering_${channel}`),
      connection_problem: mappedDeviceEntities.connection_problem || this._entityBySuffix("binary_sensor", "_connection_problem"),
      water_warning: mappedDeviceEntities.water_warning || this._entityBySuffix("binary_sensor", ["_water_warning", "_problem_2"]),
      device_locked: mappedDeviceEntities.device_locked || this._entityBySuffix("binary_sensor", ["_device_locked", "_problem"]),
      outlet_blocked: mappedChannelEntities.outlet_blocked || this._entityBySuffix("binary_sensor", `_outlet_${channel}_blocked`),
      outlet_locked: mappedChannelEntities.outlet_locked || this._entityBySuffix("binary_sensor", `_outlet_${channel}_locked`),
      sensor_fault: mappedChannelEntities.sensor_fault || this._entityBySuffix("binary_sensor", `_sensor_${channel}_fault`),
      sensor_disconnected: mappedChannelEntities.sensor_disconnected || this._entityBySuffix("binary_sensor", `_sensor_${channel}_disconnected`),
      watering_issue: mappedChannelEntities.watering_issue || this._entityBySuffix("binary_sensor", `_watering_issue_${channel}`),
      watering_locked: mappedChannelEntities.watering_locked || this._entityBySuffix("binary_sensor", `_watering_locked_${channel}`),
      temperature: mappedDeviceEntities.temperature || this._entityBySuffix("sensor", "_temperature"),
      humidity: mappedDeviceEntities.humidity || this._entityBySuffix("sensor", "_humidity"),
      tank_remaining: mappedDeviceEntities.tank_remaining || this._entityBySuffix("sensor", "_tank_remaining"),
      tank_level: mappedDeviceEntities.tank_level || this._entityBySuffix("sensor", "_tank_level"),
      tank_days_left: mappedDeviceEntities.tank_days_left || this._entityBySuffix("sensor", "_tank_days_left"),
      tank_capacity: mappedDeviceEntities.tank_capacity || this._entityBySuffix("number", "_tank_capacity"),
      mark_tank_full: mappedDeviceEntities.mark_tank_full || this._entityBySuffix("button", "_mark_tank_full"),
    };
    return {
      ...entities,
      ...explicit,
      moisture: entities.moisture,
      load_history: entities.load_history,
      history_count: entities.history_count,
    };
  }

  async _loadGraphHistory() {
    if (!this._hass || (!this._config?.graph && !this._config?.detail) || this._historyLoading) {
      return;
    }
    const entityId = this._entities().moisture;
    if (!entityId || !this._hass.callApi) {
      return;
    }
    const hours = this._historyHours();
    const cacheMs = 60 * 1000;
    if (
      this._historyEntity === entityId
      && this._historyLoadedAt
      && Date.now() - this._historyLoadedAt < cacheMs
    ) {
      return;
    }

    this._historyLoading = true;
    this._historyError = "";
    const start = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
    try {
      const result = await this._hass.callApi(
        "GET",
        `history/period/${encodeURIComponent(start)}?filter_entity_id=${encodeURIComponent(entityId)}&minimal_response&no_attributes`,
      );
      const states = Array.isArray(result?.[0]) ? result[0] : [];
      this._history = this._smoothGraphPoints(
        states
          .map((item) => ({
            t: new Date(item.last_changed || item.last_updated).getTime(),
            v: Number(item.state),
          }))
          .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.v) && point.v >= 0 && point.v <= 100),
      );
      this._historyEntity = entityId;
      this._historyLoadedAt = Date.now();
    } catch (error) {
      this._historyError = "History unavailable";
    } finally {
      this._historyLoading = false;
      this._renderAfterBackgroundUpdate();
    }
  }

  _historyHours() {
    return this._clamp(Number(this._historyWindowHours) || 168, 1, 168);
  }

  _setHistoryHours(hours) {
    this._historyWindowHours = this._clamp(Number(hours) || 168, 1, 168);
    this._historyLoadedAt = 0;
    this._render();
    this._loadGraphHistory();
  }

  _smoothGraphPoints(points) {
    if (points.length < 3) {
      return points;
    }
    const filtered = points.map((point, index) => {
      const prev = points[index - 1];
      const next = points[index + 1];
      if (!prev || !next) {
        return point;
      }
      const isolatedSpike = Math.abs(point.v - prev.v) > 18
        && Math.abs(point.v - next.v) > 18
        && Math.abs(prev.v - next.v) < 8;
      return isolatedSpike ? { ...point, v: (prev.v + next.v) / 2 } : point;
    });

    return filtered.map((point, index) => {
      const prev = filtered[index - 1] || point;
      const next = filtered[index + 1] || point;
      return {
        ...point,
        v: (prev.v + point.v * 2 + next.v) / 4,
      };
    });
  }

  _extendFreshHistoryToNow(points, now) {
    if (!points.length) {
      return points;
    }
    const last = points[points.length - 1];
    const maxHoldMs = 2 * 60 * 60 * 1000;
    const ageMs = now - last.t;
    if (ageMs <= 0 || ageMs > maxHoldMs) {
      return points;
    }
    return [
      ...points,
      {
        ...last,
        t: now,
      },
    ];
  }

  _graphPath(points, width, height, padding, leftPadding = padding, timeStart = undefined, timeEnd = undefined) {
    if (!points.length) {
      return "";
    }
    const coords = this._graphCoordinates(points, width, height, padding, leftPadding, timeStart, timeEnd);
    if (coords.length === 1) {
      const { x, y } = coords[0];
      const startX = Math.max(leftPadding, x - 14);
      const endX = Math.min(width - padding, x + 2);
      return `M ${startX} ${y} L ${endX} ${y}`;
    }
    return coords.reduce((path, point, index) => {
      if (index === 0) {
        return `M ${point.x} ${point.y}`;
      }
      const prev = coords[index - 1];
      const cx = (prev.x + point.x) / 2;
      return `${path} C ${cx} ${prev.y}, ${cx} ${point.y}, ${point.x} ${point.y}`;
    }, "");
  }

  _graphCoordinates(points, width, height, padding, leftPadding = padding, timeStart = undefined, timeEnd = undefined) {
    if (!points.length) {
      return [];
    }
    const minTime = Number.isFinite(timeStart) ? timeStart : points[0].t;
    const maxTime = Number.isFinite(timeEnd) ? timeEnd : points[points.length - 1].t;
    const timeRange = Math.max(1, maxTime - minTime);
    const xFor = (point) => leftPadding + ((point.t - minTime) / timeRange) * (width - leftPadding - padding);
    const yFor = (point) => padding + (1 - this._clamp(point.v, 0, 100) / 100) * (height - padding * 2);
    return points.map((point) => ({ ...point, x: xFor(point), y: yFor(point) }));
  }

  _dateLocale() {
    const haLanguage = this._hass?.locale?.language || this._hass?.language;
    if (haLanguage) {
      return haLanguage;
    }
    if (navigator.languages?.length) {
      return navigator.languages;
    }
    return navigator.language || undefined;
  }

  _dateTimeOptions(options = {}) {
    const result = { ...options };
    if (!Object.prototype.hasOwnProperty.call(result, "hour") || Object.prototype.hasOwnProperty.call(result, "hour12")) {
      return result;
    }
    const timeFormat = this._hass?.locale?.time_format;
    if (timeFormat === "24") {
      result.hour12 = false;
    } else if (timeFormat === "12") {
      result.hour12 = true;
    }
    return result;
  }

  _formatChartHoverDate(timestamp) {
    if (!timestamp) {
      return "";
    }
    return new Date(timestamp).toLocaleString(this._dateLocale(), this._dateTimeOptions({
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }));
  }

  _entityState(entityId, fallback = "Unknown") {
    const state = this._state(entityId);
    if (!state || state.state === "unknown" || state.state === "unavailable") {
      return fallback;
    }
    return state.state;
  }

  _deviceIdHint() {
    const queryDevice = new URLSearchParams(window.location?.search || "").get("device");
    if (queryDevice) {
      return String(queryDevice).trim();
    }
    const prefix = this._config.entity_prefix || this._config.device_prefix || this._config.device || "";
    if (prefix) {
      return String(prefix).replace(/^sensor\./, "");
    }
    const entityId = this._entityBySuffix("sensor", "_temperature")
      || this._entityBySuffix("sensor", "_moisture_a")
      || this._entityBySuffix("button", "_load_history_a")
      || "";
    const objectId = entityId.split(".", 2)[1] || "";
    const deviceMatch = objectId.match(/^(growcube_[^_]+(?:_[^_]+)*)_(?:temperature|humidity|tank_remaining|tank_level|tank_days_left|moisture_[abcd]|load_history_[abcd])$/);
    return deviceMatch ? deviceMatch[1] : "";
  }

  _apiDeviceIdHint() {
    const record = this._deviceRecord();
    const recordHint = String(record?.host || record?.device_id || "").trim();
    if (recordHint && !["growcube", "local_growcube"].includes(recordHint)) {
      return recordHint;
    }
    const fallback = String(this._deviceIdHint() || "").trim();
    if (!fallback || ["growcube", "local_growcube"].includes(fallback)) {
      return "";
    }
    return fallback;
  }

  _apiHistoryState(channel = this._channelKey()) {
    const data = this._cubeHistory[channel];
    if (!data) {
      return undefined;
    }
    return {
      state: String(data.history_points ?? data.history?.length ?? 0),
      attributes: {
        history_loading: Boolean(data.history_loading || this._cubeHistoryLoading[channel]),
        history_complete: Boolean(data.history_complete),
        watering_events_complete: Boolean(data.watering_events_complete),
        history_points: data.history_points ?? data.history?.length ?? 0,
        type_category: data.type_category || "",
        type_description: data.type_description || "",
        temp_min: Number(data.temp_min) || 0,
        temp_max: Number(data.temp_max) || 0,
        air_humidity_min: Number(data.air_humidity_min) || 0,
        air_humidity_max: Number(data.air_humidity_max) || 0,
        history: Array.isArray(data.history) ? data.history : [],
        watering_events: Array.isArray(data.watering_events) ? data.watering_events : [],
      },
    };
  }

  _detailHistoryState(entities) {
    const channel = this._channelKey();
    const apiState = this._apiHistoryState(channel);
    const entityState = this._state(entities.history_count);
    if (!apiState) {
      return entityState;
    }
    if (!entityState) {
      return apiState;
    }
    const apiAttributes = apiState.attributes || {};
    const entityAttributes = entityState.attributes || {};
    const history = this._mergeHistoryItems(entityAttributes.history, apiAttributes.history, "timestamp");
    const wateringEvents = this._mergeHistoryItems(entityAttributes.watering_events, apiAttributes.watering_events, (item) => (
      typeof item === "string" ? item : item?.timestamp
    ));
    const apiComplete = Boolean(apiAttributes.history_complete && apiAttributes.watering_events_complete);
    return {
      ...entityState,
      state: String(Math.max(Number(entityState.state) || 0, Number(apiState.state) || 0, history.length)),
      attributes: {
        ...entityAttributes,
        ...apiAttributes,
        history_loading: apiComplete ? false : Boolean(entityAttributes.history_loading || apiAttributes.history_loading),
        history_complete: Boolean(entityAttributes.history_complete || apiAttributes.history_complete),
        watering_events_complete: Boolean(
          entityAttributes.watering_events_complete || apiAttributes.watering_events_complete,
        ),
        history_points: Math.max(
          Number(entityAttributes.history_points) || 0,
          Number(apiAttributes.history_points) || 0,
          history.length,
        ),
        history,
        watering_events: wateringEvents,
      },
    };
  }

  _mergeHistoryItems(first, second, keySelector) {
    const keyFn = typeof keySelector === "function" ? keySelector : (item) => item?.[keySelector];
    const merged = new Map();
    [first, second].forEach((items) => {
      if (!Array.isArray(items)) {
        return;
      }
      items.forEach((item) => {
        const key = keyFn(item);
        if (key) {
          merged.set(key, item);
        }
      });
    });
    return Array.from(merged.values());
  }

  async _loadCubeHistoryApiIfNeeded(force = false) {
    if (!this._hass || !this._config?.detail) {
      return;
    }
    const channel = this._channelKey();
    const now = Date.now();
    const cached = this._cubeHistory[channel];
    const cacheMs = cached?.history_complete && cached?.watering_events_complete ? 5 * 60 * 1000 : 2500;
    if (!force && this._cubeHistoryLoadedAt[channel] && now - this._cubeHistoryLoadedAt[channel] < cacheMs) {
      return;
    }
    if (this._cubeHistoryLoading[channel]) {
      return;
    }
    this._cubeHistoryLoading[channel] = true;
    try {
      const shouldRequest = !cached?.history_complete || !cached?.watering_events_complete;
      const params = new URLSearchParams({
        channel,
        request: shouldRequest ? "1" : "0",
      });
      const deviceId = this._apiDeviceIdHint();
      if (deviceId) {
        params.set("device_id", deviceId);
      }
      const result = await this._historyApi(params);
      this._cubeHistory[channel] = result || {};
      this._cubeHistoryLoadedAt[channel] = Date.now();
    } catch (error) {
      this._cubeHistory[channel] = {
        history_loading: false,
        history_complete: false,
        watering_events_complete: false,
        history_points: 0,
        history: [],
        watering_events: [],
      };
      this._cubeHistoryLoadedAt[channel] = Date.now();
    } finally {
      this._cubeHistoryLoading[channel] = false;
      const complete = Boolean(this._cubeHistory[channel]?.history_complete);
      const eventsComplete = Boolean(this._cubeHistory[channel]?.watering_events_complete);
      if (complete && eventsComplete && this._cubeHistoryPollTimer) {
        clearTimeout(this._cubeHistoryPollTimer);
        this._cubeHistoryPollTimer = null;
      } else if (
        this._config?.detail
        && (!complete || !eventsComplete)
      ) {
        if (this._cubeHistoryPollTimer) {
          clearTimeout(this._cubeHistoryPollTimer);
        }
        this._cubeHistoryPollTimer = setTimeout(() => {
          this._loadCubeHistoryApiIfNeeded(true);
        }, 3000);
      }
      this._renderAfterBackgroundUpdate();
    }
  }

  async _historyApi(params) {
    try {
      const addonResult = await this._fetchAddonApi(`history?${params.toString()}`);
      if (addonResult) {
        return addonResult;
      }
    } catch (error) {
      // Fall back to the legacy HACS API if the add-on API is not available.
    }
    if (this._hass?.callApi) {
      return this._hass.callApi("GET", `growcube/history?${params.toString()}`);
    }
    return {};
  }

  async _applyWateringApi(channel = this._channelKey()) {
    const params = new URLSearchParams({ channel });
    const deviceId = this._apiDeviceIdHint();
    if (deviceId) {
      params.set("device_id", deviceId);
    }
    return this._fetchAddonApi(`apply_watering?${params.toString()}`);
  }

  async _configureChannelApi(channel = this._channelKey(), values = {}, apply = true) {
    const payload = { channel, apply };
    const deviceId = this._apiDeviceIdHint();
    if (deviceId) {
      payload.device_id = deviceId;
    }
    Object.entries(values).forEach(([key, value]) => {
      if (value === undefined || value === null) {
        return;
      }
      payload[key] = value;
    });
    return this._fetchAddonApi("channel/config", false, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  _entityDisplay(entityId, fallback = "Unknown") {
    const state = this._state(entityId);
    if (!state || state.state === "unknown" || state.state === "unavailable") {
      return fallback;
    }
    try {
      if (this._hass.formatEntityState) {
        return this._hass.formatEntityState(state);
      }
    } catch (error) {
      return state.state;
    }
    return state.state;
  }

  _nextWateringDisplay(entityId, fallback = "Unknown") {
    const state = this._state(entityId);
    if (!state || state.state === "unknown" || state.state === "unavailable") {
      return fallback;
    }
    const timestamp = new Date(state.state);
    if (Number.isNaN(timestamp.getTime())) {
      return this._entityDisplay(entityId, fallback);
    }
    const dateText = timestamp.toLocaleDateString(this._dateLocale(), {
      day: "numeric",
      month: "long",
    });
    const timeText = timestamp.toLocaleTimeString(this._dateLocale(), this._dateTimeOptions({
      hour: "2-digit",
      minute: "2-digit",
    }));
    return `${dateText} ${timeText}`;
  }

  _entityTimeValue(entityId, fallback = "") {
    const value = this._entityState(entityId, "");
    if (value && value !== "unknown" && value !== "unavailable") {
      return value;
    }
    return fallback;
  }

  _firstWateringValue(entities) {
    const configured = this._entityTimeValue(entities.first_watering_time, "");
    if (configured) {
      return configured;
    }
    return this._entityTimeValue(entities.next_watering, "");
  }

  _number(entityId, fallback = 0) {
    const value = Number(this._entityState(entityId, fallback));
    return Number.isFinite(value) ? value : fallback;
  }

  _plantName() {
    const channelMeta = this._deviceRecord()?.channels?.[this._channelKey()] || {};
    return channelMeta.plant_name || this._entityDisplay(this._entities().name, this._config.name || `Plant ${this._channelLabel()}`);
  }

  _currentPlantPhotoUrl(channel = this._channelKey()) {
    const channelMeta = this._deviceRecord()?.channels?.[channel] || {};
    return this._resolvedPlantPhotoUrl(
      channelMeta.photo_url_value,
      channelMeta.image_url,
      channelMeta.photo_url,
      this._entities(channel).photo_url,
      this._cachedPlantPhotoUrl(this._deviceRecord()?.device_id, channel),
    );
  }

  _modeOptions() {
    return ["Disabled", "Repeating", "Smart"];
  }

  _normalizeMode(mode) {
    return mode === "Timed" || mode === "Repeating" ? "Repeating" : mode;
  }

  _modeDisplay(mode) {
    const normalized = this._normalizeMode(mode);
    return normalized === "Repeating" ? "Timed" : normalized;
  }

  async _call(domain, service, data) {
    if (!this._hass) {
      return;
    }
    await this._hass.callService(domain, service, data);
  }

  async _setNumber(entityId, value) {
    await this._call("number", "set_value", {
      entity_id: entityId,
      value: Number(value),
    });
  }

  async _setText(entityId, value) {
    await this._call("text", "set_value", { entity_id: entityId, value });
  }

  async _setTime(entityId, value) {
    if (String(entityId || "").startsWith("text.")) {
      await this._setText(entityId, value.length === 5 ? `${value}:00` : value);
      return;
    }
    await this._call("time", "set_value", {
      entity_id: entityId,
      time: value.length === 5 ? `${value}:00` : value,
    });
  }

  async _setSelect(entityId, option) {
    await this._call("select", "select_option", { entity_id: entityId, option });
  }

  async _setSwitch(entityId, value) {
    if (!entityId) {
      return;
    }
    await this._call("switch", value ? "turn_on" : "turn_off", { entity_id: entityId });
  }

  async _press(entityId) {
    await this._call("button", "press", { entity_id: entityId });
  }

  async _toggleSwitch(entityId) {
    if (!entityId) {
      throw new Error("Daytime watering entity is unavailable");
    }
    await this._call("switch", "toggle", { entity_id: entityId });
  }

  _isOn(entityId) {
    return this._state(entityId)?.state === "on";
  }

  _loadDismissedProblems() {
    try {
      return JSON.parse(localStorage.getItem("growcube.dismissedProblems") || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  _problemDismissKey(entityId, kind) {
    return entityId ? `${kind}:${entityId}` : "";
  }

  _isProblemDismissed(entityId, kind) {
    const key = this._problemDismissKey(entityId, kind);
    return Boolean(key && this._dismissedProblems[key]);
  }

  _dismissProblem(entityId, kind) {
    const key = this._problemDismissKey(entityId, kind);
    if (!key) {
      return;
    }
    this._dismissedProblems = {
      ...this._dismissedProblems,
      [key]: Date.now(),
    };
    localStorage.setItem("growcube.dismissedProblems", JSON.stringify(this._dismissedProblems));
    this._showToast("Alert hidden");
  }

  _isPlantConfigured(entities, missingFallback = true) {
    if (typeof entities?.configured === "boolean") {
      return entities.configured;
    }
    const mappedConfigured = String(entities?.configured ?? "").trim().toLowerCase();
    if (["true", "on", "1", "yes"].includes(mappedConfigured)) {
      return true;
    }
    if (["false", "off", "0", "no"].includes(mappedConfigured)) {
      return false;
    }
    const configured = this._state(entities.plant_configured);
    if (!configured) {
      return missingFallback;
    }
    return configured.state === "on";
  }

  _problemItems(entities) {
    const items = [];
    if (this._isOn(entities.connection_problem)) {
      items.push({ label: "Connection problem", severity: "danger" });
    }
    if (this._isOn(entities.water_warning)) {
      items.push({ label: "Water low", severity: "danger" });
    }
    if (this._isOn(entities.device_locked)) {
      items.push({ label: "Device locked", severity: "danger" });
    }
    if (this._isOn(entities.outlet_blocked)) {
      items.push({ label: "Pump blocked", severity: "danger" });
    }
    if (this._isOn(entities.outlet_locked)) {
      items.push({ label: "Outlet locked", severity: "danger" });
    }
    if (this._isOn(entities.watering_locked)) {
      items.push({ label: "Smart watering locked", severity: "danger" });
    } else if (this._isOn(entities.watering_issue) && !this._isProblemDismissed(entities.watering_issue, "watering_issue")) {
      items.push({
        label: "Smart watering issue",
        severity: "warning",
        dismissible: true,
        dismissEntity: entities.watering_issue,
        dismissKind: "watering_issue",
      });
    }
    if (this._isOn(entities.sensor_fault) || this._isOn(entities.sensor_disconnected)) {
      items.push({ label: "Sensor offline", severity: "warning" });
    }
    return items;
  }

  _wateringBlocked(problemItems) {
    return problemItems.some((item) => item.label !== "Sensor offline");
  }

  _showToast(message) {
    this._toast = message;
    if (this._toastTimer) {
      clearTimeout(this._toastTimer);
    }
    this._toastTimer = setTimeout(() => {
      this._toast = "";
      this._renderAfterBackgroundUpdate();
    }, 2600);
    this._render();
  }

  _showError(message) {
    this._showToast(message || "Action failed");
  }

  _hasActiveInputDialog() {
    return Boolean(
      this._editDialog
      || this._plantWizardOpen
      || this._modeWizardOpen
      || this._wateringOpen
      || this._reservoirOpen
      || this._reservoirGuideOpen
      || this._customPlantsOpen
      || this._deletePlantDialogOpen
      || this._aboutDialogOpen
    );
  }

  _renderAfterBackgroundUpdate() {
    if (!this._hasActiveInputDialog()) {
      this._render();
    }
  }

  _openWateringDialog() {
    const manualDuration = this._entities().manual_duration;
    this._wateringSeconds = this._number(manualDuration, 50);
    this._wateringOpen = true;
    this._render();
  }

  _closeWateringDialog() {
    this._wateringOpen = false;
    this._render();
  }

  _openReservoirDialog(entityId = this._entities().tank_capacity, currentAmount = undefined) {
    const tankCapacity = entityId || this._entities().tank_capacity;
    this._reservoirAmount = this._number(tankCapacity, 1500);
    if (Number.isFinite(Number(currentAmount))) {
      this._reservoirAmount = Number(currentAmount);
    }
    this._reservoirTargetEntity = tankCapacity;
    this._reservoirOpen = true;
    this._render();
  }

  _openReservoirGuide(entityId = this._entities().tank_capacity, currentAmount = undefined) {
    this._pendingReservoirEntity = entityId || this._entities().tank_capacity;
    this._pendingReservoirAmount = currentAmount;
    if (localStorage.getItem("growcube.hideExternalReservoirGuide") === "1") {
      this._openReservoirDialog(this._pendingReservoirEntity, this._pendingReservoirAmount);
      return;
    }
    this._reservoirGuideOpen = true;
    this._reservoirGuideStep = 0;
    this._reservoirGuideDontShow = false;
    this._render();
  }

  _closeReservoirGuide() {
    this._reservoirGuideOpen = false;
    this._pendingReservoirEntity = "";
    this._pendingReservoirAmount = undefined;
    this._render();
  }

  _setReservoirGuideStep(step) {
    this._reservoirGuideStep = this._clamp(Number(step), 0, this._reservoirGuideSteps().length - 1);
    this._render();
  }

  _continueReservoirGuide() {
    if (this._reservoirGuideDontShow) {
      localStorage.setItem("growcube.hideExternalReservoirGuide", "1");
    }
    const entityId = this._pendingReservoirEntity;
    const amount = this._pendingReservoirAmount;
    this._reservoirGuideOpen = false;
    this._pendingReservoirEntity = "";
    this._pendingReservoirAmount = undefined;
    this._openReservoirDialog(entityId, amount);
  }

  _closeReservoirDialog() {
    this._reservoirOpen = false;
    this._reservoirTargetEntity = "";
    this._render();
  }

  _openModeWizard() {
    const entities = this._entities();
    const mode = this._normalizeMode(this._entityState(entities.mode, "Smart"));
    const [hour, minute] = this._splitTime(this._firstWateringValue(entities));
    this._modeWizardMode = mode === "Repeating" ? "Repeating" : "Smart";
    this._modeWizardAmount = this._clamp(this._number(entities.duration, 50), 10, 500);
    this._modeWizardIntervalDays = this._clamp(Math.max(1, Math.round(this._number(entities.interval, 24) / 24)), 1, 10);
    this._modeWizardStartHour = hour;
    this._modeWizardStartMinute = minute;
    this._modeWizardSmartMin = this._clamp(this._number(entities.smart_min_moisture, 20), 1, 98);
    this._modeWizardSmartMax = this._clamp(
      this._number(entities.smart_max_moisture, 60),
      this._modeWizardSmartMin + 1,
      99,
    );
    this._modeWizardDaytime = this._isOn(entities.smart_daytime_watering);
    this._modeWizardStep = 0;
    this._modeWizardOpen = true;
    this._render();
  }

  _closeModeWizard() {
    this._modeWizardOpen = false;
    this._render();
  }

  _setModeWizardStep(step) {
    this._modeWizardStep = this._clamp(Number(step), 0, 1);
    this._render();
  }

  _canAdvanceModeWizard() {
    return this._modeWizardMode === "Smart" || this._modeWizardMode === "Repeating";
  }

  _modeWizardStartTime() {
    const hour = this._clamp(Number(this._modeWizardStartHour), 0, 23);
    const minute = this._clamp(Number(this._modeWizardStartMinute), 0, 59);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`;
  }

  async _disableModeWizard() {
    const entities = this._entities();
    try {
      if (entities.mode) {
        await this._setSelect(entities.mode, "Disabled");
      }
      await this._saveScheduleAfterEdit("Automatic watering disabled", { mode: "Disabled" });
      this._modeWizardOpen = false;
      this._render();
    } catch (error) {
      this._showError(error?.message || "Could not disable automatic watering");
    }
  }

  _openPlantWizard() {
    const channel = this._firstAvailableChannel();
    this._plantWizardChannel = channel;
    this._plantWizardStep = 0;
    this._plantWizardName = "";
    this._plantWizardPhotoUrl = "";
    this._plantWizardMode = "Smart";
    this._plantWizardAmount = 50;
    this._plantWizardIntervalDays = 1;
    this._plantWizardStartHour = 8;
    this._plantWizardStartMinute = 0;
    this._plantWizardSmartMin = 20;
    this._plantWizardSmartMax = 60;
    this._plantWizardDaytime = true;
    this._plantWizardCategory = "";
    this._plantWizardDescription = "";
    this._plantWizardTempMin = 0;
    this._plantWizardTempMax = 0;
    this._plantWizardAirHumidityMin = 0;
    this._plantWizardAirHumidityMax = 0;
    this._plantWizardPhotoUploading = false;
    this._plantWizardPhotoFileName = "";
    this._plantWizardSearch = "";
    this._plantWizardResults = [];
    this._plantWizardResultPage = 0;
    this._plantWizardSelected = null;
    this._plantWizardCustom = false;
    this._plantWizardCreateCustomOnly = false;
    this._plantWizardLoading = false;
    this._plantWizardError = "";
    this._plantWizardOpen = true;
    this._render();
  }

  _closePlantWizard() {
    this._plantWizardOpen = false;
    this._customPlantsOpen = false;
    this._render();
  }

  _setPlantWizardStep(step) {
    if (!this._plantWizardCreateCustomOnly && Number(step) >= this._plantWizardChannelStepIndex() && !this._availablePlantWizardChannels().includes(this._plantWizardChannel)) {
      this._plantWizardChannel = this._firstAvailableChannel();
    }
    this._plantWizardStep = this._clamp(Number(step), 0, this._plantWizardLastStep());
    this._render();
  }

  _canAdvancePlantWizard() {
    if (this._plantWizardCreateCustomOnly && this._plantWizardStep === 0) {
      return Boolean(this._plantWizardSelected) && this._plantWizardName.trim().length > 0;
    }
    if (this._plantWizardStep === 0) {
      return Boolean(this._plantWizardSelected);
    }
    if (this._plantWizardCustom && this._plantWizardStep === 1) {
      return Boolean(this._plantWizardSelected) && this._plantWizardName.trim().length > 0;
    }
    if (this._plantWizardStep === this._plantWizardChannelStepIndex()) {
      return this._availablePlantWizardChannels().includes(this._plantWizardChannel);
    }
    if (this._plantWizardStep === this._plantWizardModeStepIndex()) {
      return this._plantWizardMode === "Smart" || this._plantWizardMode === "Repeating";
    }
    return true;
  }

  _plantWizardLastStep() {
    if (this._plantWizardCreateCustomOnly) {
      return 2;
    }
    return this._plantWizardCustom ? 6 : 5;
  }

  _plantWizardChannelStepIndex() {
    return this._plantWizardCustom ? 3 : 2;
  }

  _plantWizardModeStepIndex() {
    return this._plantWizardCustom ? 4 : 3;
  }

  _plantWizardSettingsStepIndex() {
    return this._plantWizardCustom ? 5 : 4;
  }

  async _confirmWatering() {
    const entities = this._entities();
    const amountMl = this._clamp(Number(this._entityState(entities.manual_duration, 50)) || 50, 30, 150);
    try {
      if (entities.manual_duration) {
        await this._setNumber(entities.manual_duration, amountMl);
      }
      await this._press(entities.water);
      this._refreshCurrentHistorySoon();
      this._wateringOpen = false;
      this._showToast(`Watering started: ${amountMl} mL`);
    } catch (error) {
      this._wateringOpen = false;
      this._showError(error?.message || "Watering failed");
    }
  }

  _refreshCurrentHistorySoon() {
    const channel = this._channelKey();
    delete this._cubeHistory[channel];
    delete this._cubeHistoryLoadedAt[channel];
    window.setTimeout(() => this._loadCubeHistoryApiIfNeeded(true), 1000);
  }

  async _refreshCurrentHistory() {
    const entities = this._entities();
    const channel = this._channelKey();
    delete this._cubeHistory[channel];
    delete this._cubeHistoryLoadedAt[channel];
    if (entities.history_count) {
      delete this._cubeHistoryRequestedAt[entities.history_count];
    }
    try {
      if (entities.load_history) {
        await this._press(entities.load_history);
      }
      await this._loadCubeHistoryApiIfNeeded(true);
      this._showToast("Refreshing moisture history");
    } catch (error) {
      this._showError("Could not refresh history");
    }
  }

  async _confirmReservoir() {
    const amountMl = this._clamp(Number(this._reservoirAmount) || 1500, 500, 50000);
    try {
      await this._setNumber(this._reservoirTargetEntity || this._entities().tank_capacity, amountMl);
      this._reservoirOpen = false;
      this._reservoirTargetEntity = "";
      this._showToast(`Reservoir set to ${amountMl} mL`);
    } catch (error) {
      this._reservoirOpen = false;
      this._reservoirTargetEntity = "";
      this._showError(error?.message || "Could not update capacity");
    }
  }

  async _confirmAddPlant() {
    if (this._plantWizardCreateCustomOnly) {
      if (!this._plantWizardName.trim()) {
        this._showError("Plant name cannot be empty");
        return;
      }
      this._rememberCustomPlantProfileFromWizard();
      this._plantWizardOpen = false;
      this._plantWizardCustom = false;
      this._plantWizardCreateCustomOnly = false;
      this._plantWizardSelected = null;
      this._plantWizardStep = 0;
      this._customPlantsOpen = true;
      this._customPlantsPage = 0;
      this._showToast("Custom plant saved");
      this._render();
      return;
    }
    const availableChannels = this._availablePlantWizardChannels();
    const channel = availableChannels.includes(this._plantWizardChannel) ? this._plantWizardChannel : availableChannels[0];
    if (!channel) {
      this._showError("No free channels available");
      return;
    }
    const entities = this._entities(channel);
    const mode = this._normalizeMode(this._plantWizardMode || "Disabled");
    const profile = this._plantWizardSelected || {};
    const photoUrl = this._plantImageUrl(this._plantWizardPhotoUrl || this._catalogImageUrl(profile));
    const values = {
      configured: true,
      plant_id: this._plantWizardCustom ? 0 : Number(profile.id) || 0,
      plant_name: this._plantWizardName.trim(),
      photo_url: photoUrl,
      type_category: this._plantWizardCategory.trim(),
      type_description: this._plantWizardDescription.trim(),
      temp_min: this._plantWizardTempMin,
      temp_max: this._plantWizardTempMax,
      air_humidity_min: this._plantWizardAirHumidityMin,
      air_humidity_max: this._plantWizardAirHumidityMax,
      mode,
      first_watering_time: this._plantWizardStartTime(),
      amount_ml: this._plantWizardAmount,
      interval_hours: this._plantWizardIntervalDays * 24,
      smart_min_moisture: this._plantWizardSmartMin,
      smart_max_moisture: this._plantWizardSmartMax,
      smart_daytime_watering: this._plantWizardDaytime,
    };
    try {
      let apiResult;
      try {
        apiResult = await this._configureChannelApi(channel, values, mode === "Smart" || mode === "Repeating");
      } catch (error) {
        console.warn("[GrowCube] add plant via add-on API failed", {
          channel,
          mode,
          error: error?.message || String(error),
        });
        apiResult = undefined;
      }
      if (apiResult) {
        const returnedPlantId = Number(apiResult.plant_id) || 0;
        if (values.plant_id > 0 && returnedPlantId !== values.plant_id) {
          throw new Error(`GrowCube add-on returned plant ID ${returnedPlantId} instead of ${values.plant_id}.`);
        }
        console.info("[GrowCube] add plant via add-on API succeeded", { channel, mode });
        this._rememberCustomPlantProfileFromWizard();
        this._plantWizardOpen = false;
        this._customPlantsOpen = false;
        const apiResultHasPlantId = Object.prototype.hasOwnProperty.call(apiResult, "plant_id");
        this._applyOptimisticChannelMetadata(channel, {
          ...values,
          plant_id: apiResultHasPlantId ? returnedPlantId : values.plant_id,
          plant_name: apiResult.plant_name || values.plant_name,
          photo_url: apiResult.photo_url || values.photo_url,
          image_url: apiResult.image_url || apiResult.photo_url || values.photo_url,
          configured: apiResult.configured ?? values.configured,
        });
        this._dashboardDevicesLoadedAt = 0;
        this._navigateToChannel(channel);
        this._loadDashboardDevicesIfNeeded(true);
        this._showToast("Plant added");
        this._render();
        return;
      }
      if (values.plant_id > 0) {
        throw new Error("Could not save the catalog plant ID. Check the GrowCube add-on connection and try again.");
      }
      if (entities.name && this._plantWizardName.trim()) {
        await this._setText(entities.name, this._plantWizardName.trim());
      }
      if (entities.photo_url && photoUrl) {
        await this._setText(entities.photo_url, photoUrl);
      }
      if (entities.mode) {
        await this._setSelect(entities.mode, mode);
      }
      if (this._plantWizardMode === "Smart") {
        if (entities.smart_min_moisture) {
          await this._setNumber(entities.smart_min_moisture, this._plantWizardSmartMin);
        }
        if (entities.smart_max_moisture) {
          await this._setNumber(entities.smart_max_moisture, this._plantWizardSmartMax);
        }
        if (entities.smart_daytime_watering) {
          await this._setSwitch(entities.smart_daytime_watering, this._plantWizardDaytime);
        }
      } else if (this._plantWizardMode === "Repeating") {
        if (entities.first_watering_time) {
          await this._setTime(entities.first_watering_time, this._plantWizardStartTime());
        }
        if (entities.duration) {
          await this._setNumber(entities.duration, this._plantWizardAmount);
        }
        if (entities.interval) {
          await this._setNumber(entities.interval, this._plantWizardIntervalDays * 24);
        }
      }
      if (entities.add_plant) {
        await this._press(entities.add_plant);
      }
      if ((mode === "Smart" || mode === "Repeating") && entities.save) {
        await this._press(entities.save);
      }
      this._plantWizardOpen = false;
      this._customPlantsOpen = false;
      this._rememberCustomPlantProfileFromWizard();
      this._applyOptimisticChannelMetadata(channel, values);
      this._dashboardDevicesLoadedAt = 0;
      this._navigateToChannel(channel);
      this._loadDashboardDevicesIfNeeded(true);
      this._showToast("Plant added");
      this._render();
    } catch (error) {
      this._showError(error?.message || "Could not add plant");
    }
  }

  async _searchPlantCatalog() {
    if (this._plantWizardSearch.trim().length < 2) {
      this._plantWizardResults = [];
      this._plantWizardResultPage = 0;
      this._plantWizardError = this._plantWizardSearch.trim() ? "Type at least 2 characters" : "";
      this._render();
      return;
    }
    this._plantWizardLoading = true;
    this._plantWizardError = "";
    this._render();
    try {
      this._plantWizardResults = this._normalizeCatalogPlants(await this._catalogSearch(this._plantWizardSearch.trim()));
      this._plantWizardResultPage = 0;
      this._plantWizardError = this._plantWizardResults.length ? "" : "No plants found";
    } catch (error) {
      this._plantWizardResults = this._normalizeCatalogPlants(this._customPlantCatalogResult(this._plantWizardSearch.trim()));
      this._plantWizardResultPage = 0;
      this._plantWizardError = this._plantWizardResults.length
        ? "Online catalog unavailable; using custom profile"
        : "Catalog unavailable";
    } finally {
      this._plantWizardLoading = false;
      this._render();
    }
  }

  async _catalogSearch(query) {
    if (!this._addonApiUrlCache) {
      await this._loadDashboardDevicesIfNeeded(true);
    }
    try {
      const result = await this._fetchAddonApi(`plants/search?query=${encodeURIComponent(query)}`);
      const plants = Array.isArray(result?.plants) ? result.plants : [];
      console.info("[GrowCube] add-on plant search result", { query, count: plants.length });
      if (plants.length) {
        return this._normalizeCatalogPlants(plants);
      }
    } catch (error) {
      console.warn("[GrowCube] add-on plant search failed", { query, error: error?.message || String(error) });
    }

    if (this._hass?.callApi) {
      try {
        const result = await this._hass.callApi(
          "GET",
          `growcube/plants/search?query=${encodeURIComponent(query)}`,
        );
        const plants = Array.isArray(result?.plants) ? result.plants : [];
        console.info("[GrowCube] Home Assistant plant search result", { query, count: plants.length });
        if (plants.length) {
          return this._normalizeCatalogPlants(plants);
        }
      } catch (error) {
        console.warn("[GrowCube] Home Assistant plant search failed", { query, error: error?.message || String(error) });
      }
    }

    try {
      const result = await this._fetchGrowCubeCatalog(query);
      console.info("[GrowCube] direct GrowCube catalog result", { query, count: result.length });
      if (result.length) {
        return this._normalizeCatalogPlants(result);
      }
    } catch (error) {
      console.warn("[GrowCube] direct GrowCube catalog failed", { query, error: error?.message || String(error) });
    }

    console.warn("[GrowCube] using custom plant fallback", { query });
    return this._normalizeCatalogPlants(this._customPlantCatalogResult(query));
  }

  async _fetchGrowCubeCatalog(query) {
    const response = await fetch(`https://api.growcube.cc/api/en/plants/name/${encodeURIComponent(query)}`, {
      headers: {
        "Accept": "application/json",
      },
    });
    if (!response.ok) {
      throw new Error(`Catalog request failed: ${response.status}`);
    }
    const data = await response.json();
    const plants = Array.isArray(data?.plants) ? data.plants : [];
    return plants.slice(0, 40).map((plant) => this._plantFromGrowCubeApi(plant));
  }

  _plantFromGrowCubeApi(plant) {
    return {
      id: Number(plant?.id) || 0,
      name: String(plant?.name || ""),
      display_name: String(plant?.display_name || plant?.name || ""),
      category: String(plant?.category || ""),
      description: String(plant?.description || "").trim(),
      image_url: this._normalizePlantImageUrl(plant?.image),
      moisture_min: this._clamp(Number(plant?.min_soil_moist) || 30, 0, 100),
      moisture_max: this._clamp(Number(plant?.max_soil_moist) || 60, 0, 100),
      temp_min: Number(plant?.min_temp) || 0,
      temp_max: Number(plant?.max_temp) || 0,
      air_humidity_min: Number(plant?.min_env_humid) || 0,
      air_humidity_max: Number(plant?.max_env_humid) || 0,
    };
  }

  _customPlantCatalogResult(query) {
    const displayName = this._normalizeProfileText(query);
    if (displayName.length < 2) {
      return [];
    }
    return [{
      id: 0,
      name: displayName.toLowerCase(),
      display_name: displayName,
      category: "Custom plant",
      description: "Local custom profile created from the search text.",
      image_url: "",
      moisture_min: 20,
      moisture_max: 60,
      temp_min: 0,
      temp_max: 0,
      air_humidity_min: 0,
      air_humidity_max: 0,
    }];
  }

  _selectPlantCatalogItem(index) {
    const item = this._normalizeCatalogPlant(this._plantWizardResults[Number(index)]);
    if (!item) {
      return;
    }
    this._plantWizardSelected = item;
    this._plantWizardCustom = false;
    this._plantWizardName = item.display_name || item.name || this._plantWizardName;
    this._plantWizardPhotoUrl = this._plantImageUrl(item.image_url);
    this._plantWizardCategory = item.category || "";
    this._plantWizardDescription = item.description || "";
    this._plantWizardTempMin = this._clamp(Number(item.temp_min) || 0, -50, 100);
    this._plantWizardTempMax = this._clamp(Number(item.temp_max) || 0, -50, 100);
    this._plantWizardAirHumidityMin = this._clamp(Number(item.air_humidity_min) || 0, 0, 100);
    this._plantWizardAirHumidityMax = this._clamp(Number(item.air_humidity_max) || 0, 0, 100);
    this._plantWizardMode = "Smart";
    this._plantWizardSmartMin = this._clamp(Number(item.moisture_min) || 20, 1, 98);
    this._plantWizardSmartMax = this._clamp(Number(item.moisture_max) || 60, this._plantWizardSmartMin + 1, 99);
    this._plantWizardStep = 1;
    this._render();
  }

  _startCustomPlantWizard() {
    const name = this._normalizeProfileText(this._plantWizardSearch) || "Custom plant";
    const item = this._normalizeCatalogPlant({
      id: 0,
      name: name.toLowerCase(),
      display_name: name,
      category: "Custom plant",
      description: "",
      image_url: "",
      moisture_min: this._plantWizardSmartMin || 20,
      moisture_max: this._plantWizardSmartMax || 60,
      temp_min: 0,
      temp_max: 0,
      air_humidity_min: 0,
      air_humidity_max: 0,
    });
    this._plantWizardSelected = item;
    this._plantWizardCustom = true;
    this._plantWizardCreateCustomOnly = true;
    this._plantWizardOpen = true;
    this._customPlantsOpen = false;
    this._plantWizardName = name;
    this._plantWizardPhotoUrl = "";
    this._plantWizardPhotoFileName = "";
    this._plantWizardCategory = "Custom plant";
    this._plantWizardDescription = "";
    this._plantWizardTempMin = 0;
    this._plantWizardTempMax = 0;
    this._plantWizardAirHumidityMin = 0;
    this._plantWizardAirHumidityMax = 0;
    this._plantWizardMode = "Smart";
    this._plantWizardSmartMin = 20;
    this._plantWizardSmartMax = 60;
    this._plantWizardStep = 0;
    this._render();
  }

  _customPlantStorageKey() {
    return "growcube.customPlants";
  }

  _customPlantProfiles() {
    try {
      const items = JSON.parse(window.localStorage?.getItem(this._customPlantStorageKey()) || "[]");
      return Array.isArray(items)
        ? items.map((item) => this._normalizeCatalogPlant(item)).filter(Boolean)
        : [];
    } catch (_error) {
      return [];
    }
  }

  _writeCustomPlantProfiles(items) {
    try {
      window.localStorage?.setItem(this._customPlantStorageKey(), JSON.stringify(items.slice(0, 40)));
    } catch (error) {
      console.warn("[GrowCube] custom plant library save failed", { error: error?.message || String(error) });
    }
  }

  _customPlantProfileFromWizard() {
    return {
      id: 0,
      name: this._plantWizardName.trim().toLowerCase(),
      display_name: this._plantWizardName.trim(),
      category: this._plantWizardCategory.trim() || "Custom plant",
      description: this._plantWizardDescription.trim(),
      image_url: this._plantWizardPhotoUrl,
      moisture_min: this._plantWizardSmartMin,
      moisture_max: this._plantWizardSmartMax,
      temp_min: this._plantWizardTempMin,
      temp_max: this._plantWizardTempMax,
      air_humidity_min: this._plantWizardAirHumidityMin,
      air_humidity_max: this._plantWizardAirHumidityMax,
    };
  }

  _rememberCustomPlantProfileFromWizard() {
    if (!this._plantWizardCustom || !this._plantWizardName.trim()) {
      return;
    }
    const profile = this._customPlantProfileFromWizard();
    const key = profile.display_name.toLowerCase();
    const existing = this._customPlantProfiles().filter((item) => String(item.display_name || item.name || "").toLowerCase() !== key);
    this._writeCustomPlantProfiles([profile, ...existing]);
  }

  _openCustomPlantsDialog() {
    this._customPlantsOpen = true;
    this._customPlantsPage = this._clamp(this._customPlantsPage, 0, Math.max(0, this._customPlantPages() - 1));
    this._render();
  }

  _closeCustomPlantsDialog() {
    this._customPlantsOpen = false;
    this._render();
  }

  _selectCustomPlantProfile(index) {
    const item = this._normalizeCatalogPlant(this._customPlantProfiles()[Number(index)]);
    if (!item) {
      return;
    }
    this._plantWizardSelected = item;
    this._plantWizardCustom = false;
    this._plantWizardCreateCustomOnly = false;
    this._customPlantsOpen = false;
    this._plantWizardName = item.display_name || item.name || "Custom plant";
    this._plantWizardPhotoUrl = this._plantImageUrl(item.image_url);
    this._plantWizardPhotoFileName = "";
    this._plantWizardCategory = item.category || "Custom plant";
    this._plantWizardDescription = item.description || "";
    this._plantWizardTempMin = this._clamp(Number(item.temp_min) || 0, -50, 100);
    this._plantWizardTempMax = this._clamp(Number(item.temp_max) || 0, -50, 100);
    this._plantWizardAirHumidityMin = this._clamp(Number(item.air_humidity_min) || 0, 0, 100);
    this._plantWizardAirHumidityMax = this._clamp(Number(item.air_humidity_max) || 0, 0, 100);
    this._plantWizardSmartMin = this._clamp(Number(item.moisture_min) || 20, 1, 98);
    this._plantWizardSmartMax = this._clamp(Number(item.moisture_max) || 60, this._plantWizardSmartMin + 1, 99);
    this._plantWizardMode = "Smart";
    this._plantWizardStep = 1;
    this._render();
  }

  _customPlantPages() {
    return Math.max(1, Math.ceil(this._customPlantProfiles().length / 3));
  }

  _changeCustomPlantPage(delta) {
    this._customPlantsPage = this._clamp(this._customPlantsPage + delta, 0, Math.max(0, this._customPlantPages() - 1));
    this._render();
  }

  async _uploadPlantWizardPhoto(file) {
    if (!file) {
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      this._showError("Photo must be JPEG, PNG, or WebP");
      return;
    }
    if (file.size > 1024 * 1024) {
      this._showError("Photo must be 1 MB or smaller");
      return;
    }
    this._plantWizardPhotoUploading = true;
    this._plantWizardPhotoFileName = file.name || "plant photo";
    this._render();
    try {
      const dataUrl = await this._readFileAsDataUrl(file);
      const result = await this._postAddonApi("plants/photo", {
        filename: file.name || "plant-photo",
        content_type: file.type,
        data: String(dataUrl).split(",", 2)[1] || "",
      });
      const baseUrl = await this._addonApiUrl();
      const url = String(result?.url || "");
      this._plantWizardPhotoUrl = url.startsWith("/") ? `${baseUrl}${url}` : url;
      this._plantWizardPhotoUploading = false;
      this._showToast("Photo uploaded");
      this._render();
    } catch (error) {
      this._plantWizardPhotoUploading = false;
      this._showError(error?.message || "Photo upload failed");
      this._render();
    }
  }

  _readFileAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(reader.result || ""));
      reader.addEventListener("error", () => reject(reader.error || new Error("Could not read photo")));
      reader.readAsDataURL(file);
    });
  }

  _changePlantWizardResultPage(delta) {
    const pages = this._plantWizardResultPages();
    this._plantWizardResultPage = this._clamp(this._plantWizardResultPage + delta, 0, Math.max(0, pages - 1));
    this._render();
  }

  _plantWizardResultPages() {
    return Math.max(1, Math.ceil(this._plantWizardResults.length / 3));
  }

  _navigate() {
    const path = this._config.navigation_path || this._defaultNavigationPath(undefined, this._selectedDeviceId());
    if (!path) {
      return;
    }
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _navigateToChannel(channel, deviceId = this._selectedDeviceId()) {
    const path = this._defaultNavigationPath(channel, deviceId);
    if (!path) {
      return;
    }
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _defaultNavigationPath(channel = undefined, deviceId = "") {
    if (this._config.detail) {
      return "";
    }
    const parts = window.location.pathname.split("/").filter(Boolean);
    if (!parts.length) {
      return "";
    }
    const query = deviceId ? `?device=${encodeURIComponent(deviceId)}` : "";
    return `/${parts[0]}/growcube-plant-${this._channelKey(channel)}${query}`;
  }

  _showMoreInfo(entityId) {
    if (!entityId) {
      return;
    }
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    }));
  }

  _detailBackPath() {
    const currentPath = window.location.pathname || "/";
    const withoutPlant = currentPath.replace(/\/growcube-plant-[abcd]\/?$/i, "");
    const search = window.location.search || "";
    return `${withoutPlant || "/"}${search}`;
  }

  _aboutProfileCacheKey() {
    return `${this._channelKey()}::${this._plantName().trim().toLowerCase()}`;
  }

  _normalizeProfileText(value) {
    return String(value || "").trim();
  }

  _normalizePlantImageUrl(value) {
    const rawText = String(value || "").trim();
    const markdownMatch = rawText.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/i);
    const text = (markdownMatch ? markdownMatch[2] : rawText).trim();
    if (!text) {
      return "";
    }
    if (this._looksLikeEntityId(text)) {
      return "";
    }
    if (/^https:\/\/api\.growcube\.cc\/[a-z_]+\./i.test(text)) {
      return "";
    }
    if (/\/plants\/image\?url=https%3A%2F%2Fapi\.growcube\.cc%2F[a-z_]+\./i.test(text)) {
      return "";
    }
    if (text.startsWith("//")) {
      return `https:${text}`;
    }
    if (/^http:\/\//i.test(text)) {
      if (/^http:\/\/api\.growcube\.cc\//i.test(text)) {
        return `https://${text.slice("http://".length)}`;
      }
      return text;
    }
    if (/^https:\/\//i.test(text)) {
      return text;
    }
    if (text.startsWith("/plant_photos/")) {
      return this._addonApiUrlCache ? `${this._addonApiUrlCache}${text}` : text;
    }
    if (text.startsWith("/api/hassio_ingress/")) {
      return text;
    }
    if (text.startsWith("/")) {
      return `https://api.growcube.cc${text}`;
    }
    return `https://api.growcube.cc/${text.replace(/^\/+/, "")}`;
  }

  _plantImageUrl(value) {
    const url = this._normalizePlantImageUrl(value);
    if (!url) {
      return url;
    }
    const addonApiUrl = window.GROWCUBE_STANDALONE_ADDON_API_URL || this._addonApiUrlCache || "";
    if (!addonApiUrl) {
      return url;
    }
    try {
      const parsed = new URL(url);
      if (["api.growcube.cc", "www.growcube.cc"].includes(parsed.hostname)) {
        return `${addonApiUrl}/plants/image?url=${encodeURIComponent(url)}`;
      }
    } catch (error) {
      return url;
    }
    return url;
  }

  _plantPhotoCacheKey(deviceId, channel) {
    const device = String(deviceId || this._selectedDeviceId() || this._deviceIdHint() || "growcube").trim();
    return `growcube-photo:${device}:${this._channelKey(channel)}`;
  }

  _rememberPlantPhotoUrl(deviceId, channel, value) {
    const url = this._plantImageUrl(value);
    if (!url) {
      return;
    }
    try {
      window.localStorage?.setItem(this._plantPhotoCacheKey(deviceId, channel), url);
    } catch (error) {
      // Browser storage can be unavailable in some Home Assistant webviews.
    }
  }

  _forgetPlantPhotoUrl(deviceId, channel) {
    try {
      window.localStorage?.removeItem(this._plantPhotoCacheKey(deviceId, channel));
    } catch (error) {
      // Browser storage can be unavailable in some Home Assistant webviews.
    }
  }

  _cachedPlantPhotoUrl(deviceId, channel) {
    try {
      return this._plantImageUrl(window.localStorage?.getItem(this._plantPhotoCacheKey(deviceId, channel)) || "");
    } catch (error) {
      return "";
    }
  }

  _resolvedPlantPhotoUrl(...values) {
    for (const value of values) {
      const raw = String(value || "").trim();
      if (!raw) {
        continue;
      }
      const state = this._state(raw);
      if (!state && this._looksLikeEntityId(raw)) {
        continue;
      }
      const candidate = state ? state.state : raw;
      const url = this._plantImageUrl(candidate);
      if (url) {
        return url;
      }
    }
    return "";
  }

  _catalogImageUrl(item = {}) {
    return this._plantImageUrl(
      item.image_url
      || item.photo_url
      || item.picture_url
      || item.thumbnail_url
      || item.image
      || item.picture
      || item.thumbnail
      || "",
    );
  }

  _normalizeCatalogPlant(item) {
    if (!item || typeof item !== "object") {
      return null;
    }
    const imageUrl = this._catalogImageUrl(item);
    return {
      ...item,
      id: Number(item.id) || 0,
      display_name: item.display_name || item.name || "",
      image_url: imageUrl,
    };
  }

  _normalizeCatalogPlants(plants) {
    return Array.isArray(plants)
      ? plants.map((item) => this._normalizeCatalogPlant(item)).filter(Boolean)
      : [];
  }

  _profileHasDetails(profile) {
    return Boolean(
      this._normalizeProfileText(profile.category)
      || this._normalizeProfileText(profile.description)
      || this._hasRange(profile.tempMin, profile.tempMax)
      || this._hasRange(profile.airHumidityMin, profile.airHumidityMax)
    );
  }

  _profileDescriptionLooksTruncated(value) {
    return this._normalizeProfileText(value).endsWith("...");
  }

  _selectCatalogProfile(results, plantName) {
    if (!Array.isArray(results) || !results.length) {
      return null;
    }
    const normalizedPlantName = this._normalizeProfileText(plantName).toLowerCase();
    const exact = results.find((item) => {
      const displayName = this._normalizeProfileText(item.display_name).toLowerCase();
      const name = this._normalizeProfileText(item.name).toLowerCase();
      return displayName === normalizedPlantName || name === normalizedPlantName;
    });
    return exact || results[0] || null;
  }

  async _ensureAboutProfileData(entities) {
    const current = this._profileMetadata(entities);
    if (this._profileHasDetails(current) && !this._profileDescriptionLooksTruncated(current.description)) {
      return;
    }
    const plantName = this._plantName();
    const query = this._normalizeProfileText(plantName);
    const cacheKey = this._aboutProfileCacheKey();
    if (!query || this._aboutProfileCache[cacheKey] || this._aboutProfileLoading) {
      return;
    }
    this._aboutProfileLoading = true;
    this._render();
    try {
      const item = this._selectCatalogProfile(await this._catalogSearch(query), query);
      if (item) {
        this._aboutProfileCache[cacheKey] = {
          category: this._normalizeProfileText(item.category),
          description: this._normalizeProfileText(item.description),
          tempMin: Number(item.temp_min) || 0,
          tempMax: Number(item.temp_max) || 0,
          airHumidityMin: Number(item.air_humidity_min) || 0,
          airHumidityMax: Number(item.air_humidity_max) || 0,
        };
      }
    } catch (error) {
      // Keep About usable even if catalog lookup fails.
    } finally {
      this._aboutProfileLoading = false;
      this._renderAfterBackgroundUpdate();
    }
  }

  _profileMetadata(entities) {
    const historyState = this._detailHistoryState(entities);
    const attrs = historyState?.attributes || {};
    const fallback = this._aboutProfileCache[this._aboutProfileCacheKey()] || {};
    const attrDescription = this._normalizeProfileText(attrs.type_description);
    const fallbackDescription = this._normalizeProfileText(fallback.description);
    return {
      category: this._normalizeProfileText(attrs.type_category) || this._normalizeProfileText(fallback.category),
      description: (
        this._profileDescriptionLooksTruncated(attrDescription) && fallbackDescription
          ? fallbackDescription
          : attrDescription || fallbackDescription
      ),
      tempMin: Number(attrs.temp_min) || Number(fallback.tempMin) || 0,
      tempMax: Number(attrs.temp_max) || Number(fallback.tempMax) || 0,
      airHumidityMin: Number(attrs.air_humidity_min) || Number(fallback.airHumidityMin) || 0,
      airHumidityMax: Number(attrs.air_humidity_max) || Number(fallback.airHumidityMax) || 0,
    };
  }

  _hasRange(min, max) {
    return Number(min) !== 0 || Number(max) !== 0;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    this._loadDashboardDevicesIfNeeded();

    const entities = this._entities();
    const deviceRecord = this._deviceRecord();
    const overview = this._config.overview;
    const graph = this._config.graph;
    const detail = Boolean(this._config.detail);
    const dashboard = overview === "dashboard";
    const mode = this._normalizeMode(this._entityState(entities.mode, "Disabled"));
    const next = this._nextWateringDisplay(entities.next_watering, "Unknown");
    const moisture = this._entityDisplay(entities.moisture, "Unknown");
    const manualDuration = this._number(entities.manual_duration, 50);
    const firstWatering = this._firstWateringValue(entities);
    const [firstWateringHour, firstWateringMinute] = this._splitTime(firstWatering);
    const scheduleDuration = this._number(entities.duration, 10);
    const interval = this._number(entities.interval, 24);
    const smartMinMoisture = this._number(entities.smart_min_moisture, 20);
    const smartMaxMoisture = this._number(entities.smart_max_moisture, 60);
    const smartRange = `${smartMinMoisture}-${smartMaxMoisture}%`;
    const smartDaytimeWatering = this._isOn(entities.smart_daytime_watering);
    const problems = this._problemItems(entities);
    const plantConfigured = this._isPlantConfigured(entities);
    const legacyChannelCard = !overview && !detail && !graph;
    const renderLegacyAsPlants = legacyChannelCard && this._channelKey() === "a";
    if ((legacyChannelCard && !renderLegacyAsPlants) || graph) {
      this.shadowRoot.innerHTML = "<style>:host { display: none; }</style>";
      return;
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          box-sizing: border-box;
          width: 100%;
          max-width: none;
          margin: 0 auto;
          overflow: visible;
        }

        ha-card {
          overflow: hidden;
          border-radius: 14px;
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
        }

        .gc-icon {
          display: inline-flex;
          width: 24px;
          height: 24px;
          align-items: center;
          justify-content: center;
          color: currentColor;
          flex: 0 0 auto;
        }

        .gc-icon svg {
          display: block;
          width: 100%;
          height: 100%;
        }

        ha-card.detail-card {
          width: calc(100vw - 48px);
          max-width: 960px;
          margin-top: 22px;
          margin-left: 50%;
          transform: translateX(-50%);
          overflow: visible;
          background: transparent;
          border: 0;
          box-shadow: none;
        }

        ha-card.dashboard-host-card {
          overflow: visible;
          background: transparent;
          border: 0;
          box-shadow: none;
        }

        .card {
          box-sizing: border-box;
          padding: 18px;
          container-type: inline-size;
        }

        .summary {
          cursor: pointer;
        }

        .overview-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin-top: 16px;
        }

        .plants-list {
          display: grid;
          gap: 12px;
          margin-top: 16px;
        }

        .activity-panel {
          margin-top: 16px;
          padding: 14px;
          border: 1px solid var(--divider-color);
          border-radius: 12px;
          background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
        }

        .activity-list {
          display: grid;
          gap: 10px;
          margin-top: 10px;
        }

        .activity-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          padding-bottom: 10px;
          border-bottom: 1px solid color-mix(in srgb, var(--primary-text-color) 10%, transparent);
        }

        .activity-row:last-child {
          padding-bottom: 0;
          border-bottom: 0;
        }

        .activity-row.problem .activity-title {
          color: var(--error-color);
        }

        .activity-title {
          font-size: 14px;
          font-weight: 650;
          color: var(--primary-text-color);
        }

        .activity-detail,
        .activity-time,
        .activity-empty {
          margin-top: 2px;
          font-size: 12px;
          color: var(--secondary-text-color);
        }

        .activity-time {
          margin-top: 0;
          text-align: right;
          white-space: nowrap;
        }

        .plant-row {
          display: grid;
          grid-template-columns: 64px minmax(0, 1fr) auto;
          gap: 12px;
          align-items: center;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
          cursor: pointer;
        }

        .plant-photo {
          width: 64px;
          height: 64px;
          overflow: hidden;
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-color) 16%, transparent);
          display: grid;
          place-items: center;
          color: var(--primary-color);
        }

        .plant-photo img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .plant-meta {
          min-width: 0;
        }

        .plant-stats {
          text-align: right;
          color: var(--secondary-text-color);
        }

        .tank-meter {
          margin-top: 16px;
        }

        .meter-track {
          height: 10px;
          overflow: hidden;
          border-radius: 999px;
          background: color-mix(in srgb, var(--primary-text-color) 12%, transparent);
        }

        .meter-fill {
          height: 100%;
          border-radius: inherit;
          background: var(--primary-color);
        }

        .graph-card {
          cursor: pointer;
        }

        .chart {
          width: 100%;
          height: 180px;
          margin-top: 18px;
          overflow: visible;
        }

        .chart-grid {
          stroke: color-mix(in srgb, var(--primary-text-color) 12%, transparent);
          stroke-width: 1;
        }

        .chart-range-line {
          stroke: color-mix(in srgb, var(--primary-color) 62%, transparent);
          stroke-width: 1.6;
          stroke-dasharray: 6 5;
        }

        .chart-range-label {
          fill: var(--primary-color);
          font-size: 12px;
          font-weight: 650;
        }

        .chart-path {
          fill: none;
          stroke: var(--primary-color);
          stroke-width: 3;
          stroke-linecap: round;
          stroke-linejoin: round;
        }

        .chart-fill {
          fill: transparent;
        }

        .chart-empty {
          margin-top: 18px;
          min-height: 120px;
          display: grid;
          place-items: center;
          color: var(--secondary-text-color);
        }

        .chart-footer {
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: flex-start;
          margin-top: 6px;
          font-size: 12px;
          color: var(--secondary-text-color);
        }

        .chart-time {
          display: grid;
          gap: 2px;
        }

        .chart-time.end {
          text-align: right;
        }

        .chart-time-primary {
          font-size: 15px;
          font-weight: 650;
          line-height: 1.1;
          color: var(--primary-text-color);
        }

        .chart-time-secondary {
          font-size: 11px;
          line-height: 1.15;
          color: var(--secondary-text-color);
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }

        .header {
          display: grid;
          grid-template-columns: auto 1fr auto;
          gap: 14px;
          align-items: center;
          min-width: 0;
        }

        .header > :nth-child(2) {
          min-width: 0;
        }

        .dashboard-card {
          position: relative;
          display: grid;
          gap: 14px;
          box-sizing: border-box;
          width: 100%;
          max-width: 1660px;
          margin: 0 auto;
          padding: 0 12px;
        }

        .dashboard-toolbar {
          position: absolute;
          top: 10px;
          right: 22px;
          z-index: 5;
        }

        .dashboard-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 14px;
          align-items: start;
          padding-top: 18px;
        }

        .dashboard-card.has-device-switcher .dashboard-grid {
          padding-top: 62px;
        }

        .dashboard-card.webui-standalone {
          padding-top: 0;
        }

        .dashboard-card.webui-standalone .dashboard-toolbar {
          top: -50px;
          right: 72px;
        }

        .dashboard-card.webui-standalone.has-device-switcher .dashboard-grid {
          padding-top: 0;
        }

        .dashboard-column {
          display: grid;
          gap: 14px;
          min-width: 0;
        }

        .dashboard-card .card {
          overflow: hidden;
          border-radius: 14px;
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
          box-shadow: var(--ha-card-box-shadow, none);
        }

        .global-device-switcher {
          position: relative;
          display: block;
        }

        .device-pill {
          min-width: 0;
          min-height: 38px;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 7px 12px;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          box-shadow: var(--ha-card-box-shadow, 0 8px 18px rgba(0, 0, 0, 0.22));
          font-weight: 650;
          cursor: pointer;
        }

        .device-pill ha-icon:first-child {
          color: var(--primary-color);
        }

        .device-pill ha-icon {
          --mdc-icon-size: 18px;
        }

        .device-menu {
          position: absolute;
          top: calc(100% + 8px);
          right: 0;
          min-width: 230px;
          padding: 6px;
          border-radius: 12px;
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
          box-shadow: var(--ha-card-box-shadow, 0 14px 30px rgba(0, 0, 0, 0.32));
        }

        .device-menu button {
          width: 100%;
          min-height: 40px;
          display: grid;
          grid-template-columns: 22px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          padding: 8px 10px;
          border: 0;
          border-radius: 8px;
          color: var(--primary-text-color);
          background: transparent;
          text-align: left;
          font: inherit;
          cursor: pointer;
        }

        .device-menu button:hover,
        .device-menu button.active {
          background: color-mix(in srgb, var(--primary-color) 16%, transparent);
        }

        .device-menu .check {
          color: var(--primary-color);
        }

        .device-menu .name {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .header-side {
          display: grid;
          justify-items: end;
          gap: 6px;
          min-width: 0;
        }

        .plant-icon {
          width: 42px;
          height: 42px;
          border-radius: 50%;
          overflow: hidden;
          display: grid;
          place-items: center;
          color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 16%, transparent);
        }

        .plant-icon img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .title {
          font-size: 20px;
          line-height: 1.2;
          font-weight: 650;
          color: var(--primary-text-color);
          overflow-wrap: anywhere;
        }

        .subtitle {
          margin-top: 4px;
          font-size: 14px;
          color: var(--secondary-text-color);
          overflow-wrap: anywhere;
        }

        .stats {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin-top: 16px;
        }

        .stat {
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .stat {
          padding: 12px;
          min-width: 0;
        }

        .stat[data-action="more-info"],
        .stat[data-action^="edit-"],
        .stat[data-action="toggle-smart-daytime"] {
          cursor: pointer;
        }

        .stat[data-action="more-info"]:hover,
        .stat[data-action^="edit-"]:hover,
        .stat[data-action="toggle-smart-daytime"]:hover {
          background: color-mix(in srgb, var(--primary-color) 9%, transparent);
        }

        .label {
          font-size: 12px;
          line-height: 1.2;
          color: var(--secondary-text-color);
        }

        .value {
          margin-top: 5px;
          font-size: 18px;
          line-height: 1.25;
          color: var(--primary-text-color);
          word-break: break-word;
        }

        .actions {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
          margin-top: 16px;
        }

        .tank-tools {
          display: grid;
          gap: 10px;
          margin-top: 16px;
        }

        .tool-panel {
          display: grid;
          gap: 10px;
          min-width: 0;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .tool-title {
          font-size: 14px;
          font-weight: 650;
          color: var(--primary-text-color);
        }

        .compact-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 8px;
          align-items: end;
        }

        .choice-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
        }

        .reservoir-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }

        button.reservoir-choice {
          height: auto;
          min-height: 58px;
          display: grid;
          gap: 3px;
          justify-items: start;
          align-content: center;
          text-align: left;
        }

        .choice-title {
          font-size: 14px;
          font-weight: 650;
        }

        .choice-meta {
          font-size: 12px;
          font-weight: 500;
          color: var(--secondary-text-color);
        }

        .wide-button {
          width: 100%;
          margin-top: 16px;
        }

        button {
          height: 44px;
          border: 0;
          border-radius: 10px;
          padding: 0 14px;
          font: inherit;
          font-weight: 650;
          cursor: pointer;
          color: var(--text-primary-color);
          background: var(--primary-color);
        }

        button.secondary {
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 9%, transparent);
          border: 1px solid var(--divider-color);
        }

        button.danger {
          color: var(--error-color, #db4437);
          background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent);
          border: 1px solid color-mix(in srgb, var(--error-color, #db4437) 38%, transparent);
        }

        button.state-button {
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 9%, transparent);
          border: 1px solid var(--divider-color);
        }

        button.state-button.active {
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-color) 18%, transparent);
          border-color: color-mix(in srgb, var(--primary-color) 45%, transparent);
        }

        button:disabled {
          cursor: not-allowed;
          opacity: 0.48;
        }

        .problem-banner {
          display: grid;
          grid-template-columns: auto 1fr auto;
          gap: 10px;
          align-items: center;
          margin-top: 16px;
          padding: 11px 12px;
          border-radius: 10px;
          color: var(--primary-text-color);
          background: color-mix(in srgb, #ffb15c 16%, transparent);
          border: 1px solid color-mix(in srgb, #ffb15c 42%, transparent);
        }

        .problem-banner.danger {
          background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent);
          border-color: color-mix(in srgb, var(--error-color, #db4437) 40%, transparent);
        }

        .problem-title {
          font-weight: 650;
        }

        .problem-text {
          margin-top: 2px;
          font-size: 13px;
          color: var(--secondary-text-color);
        }

        .problem-dismiss {
          align-self: start;
        }

        .detail {
          display: grid;
          gap: 20px;
        }

        .detail-flat {
          position: relative;
          width: 100%;
          max-width: 100%;
          padding: 0;
          overflow: visible;
          border: 0;
          border-radius: 0;
          background: transparent;
        }

        .plant-dashboard {
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          gap: 14px;
          align-items: stretch;
        }

        .plant-section {
          box-sizing: border-box;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .plant-side,
        .plant-main,
        .plant-section {
          display: grid;
          gap: 14px;
          min-width: 0;
        }

        .plant-main {
          align-content: start;
          align-self: start;
        }

        .plant-titlebar {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          gap: 14px;
          align-items: center;
          padding-top: 6px;
        }

        .plant-titlebar .plant-photo {
          width: 72px;
          height: 72px;
          cursor: default;
        }

        .plant-titlebar .title {
          cursor: pointer;
          line-height: 1.08;
        }

        .plant-titlebar .title:hover {
          color: var(--primary-color);
        }

        .plant-menu-anchor {
          position: relative;
          justify-self: end;
          align-self: start;
        }

        .icon-button {
          width: 40px;
          height: 40px;
          min-width: 40px;
          border: 0;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          background: color-mix(in srgb, var(--primary-text-color) 4%, transparent);
          color: var(--secondary-text-color);
          cursor: pointer;
        }

        .icon-button:hover {
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-color) 12%, transparent);
        }

        .icon-button ha-icon {
          --mdc-icon-size: 20px;
          transform: none;
        }

        .detail-menu {
          position: absolute;
          top: calc(100% + 8px);
          right: 0;
          z-index: 12;
          width: max-content;
          min-width: 210px;
          max-width: min(280px, calc(100vw - 44px));
          display: grid;
          gap: 4px;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: var(--ha-card-background, var(--card-background-color));
          box-shadow: 0 16px 34px rgba(0, 0, 0, 0.24);
          transform-origin: top right;
        }

        .detail-menu button {
          display: grid;
          grid-template-columns: 22px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          width: 100%;
          justify-content: flex-start;
          text-align: left;
          min-height: 42px;
          padding: 0 12px;
          border: 0;
          border-radius: 8px;
          background: transparent;
          color: var(--primary-text-color);
          box-shadow: none;
        }

        .detail-menu button:hover {
          background: color-mix(in srgb, var(--primary-text-color) 7%, transparent);
        }

        .detail-menu ha-icon {
          --mdc-icon-size: 20px;
          color: var(--secondary-text-color);
        }

        .detail-menu .danger {
          color: #ff5a48;
        }

        .detail-menu .danger ha-icon {
          color: #ff5a48;
        }

        .channel-pill {
          display: inline-grid;
          grid-template-columns: auto auto;
          gap: 6px;
          align-items: center;
          width: fit-content;
          margin-top: 8px;
          color: var(--secondary-text-color);
        }

        .channel-pill ha-icon {
          --mdc-icon-size: 15px;
          color: var(--primary-color);
        }

        .chart-panel {
          position: relative;
          box-sizing: border-box;
          display: grid;
          grid-template-rows: auto minmax(0, 1fr) auto;
          gap: 10px;
          height: 100%;
          padding: 12px 12px 10px;
          min-width: 0;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .chart-visual {
          position: relative;
          display: flex;
          align-items: stretch;
          min-height: 0;
          min-height: clamp(360px, 50vh, 560px);
        }

        .chart-panel .chart {
          display: block;
          flex: 1 1 auto;
          width: 100%;
          height: auto;
          min-height: 100%;
        }

        .chart-visual[data-chart-visual] {
          cursor: crosshair;
        }

        .chart-placeholder {
          cursor: default;
        }

        .chart-placeholder .chart-empty {
          flex: 1 1 auto;
          margin-top: 0;
          min-height: 0;
        }

        .chart-current-stat {
          display: grid;
          gap: 0;
          text-align: right;
          align-self: center;
          white-space: nowrap;
          cursor: pointer;
        }

        .chart-current-stat.floating {
          position: absolute;
          top: 10px;
          right: 12px;
          z-index: 1;
          align-self: auto;
        }

        .chart-current-stat .label {
          font-size: 11px;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }

        .chart-current-stat .value {
          font-size: 32px;
          font-weight: 700;
          line-height: 1.05;
          color: var(--primary-text-color);
        }

        .chart-axis-label {
          fill: var(--secondary-text-color);
          font-size: 16px;
          font-weight: 650;
        }

        .chart-hover-guide {
          stroke: color-mix(in srgb, var(--primary-color) 44%, transparent);
          stroke-width: 1.5;
          stroke-dasharray: 4 4;
        }

        .chart-hover-dot {
          fill: var(--ha-card-background, var(--card-background-color));
          stroke: var(--primary-color);
          stroke-width: 3;
        }

        .chart-hover {
          pointer-events: none;
        }

        .chart-hover[hidden] {
          display: none;
        }

        .chart-tooltip-box {
          fill: var(--ha-card-background, var(--card-background-color));
          stroke: color-mix(in srgb, var(--primary-color) 34%, var(--divider-color));
          stroke-width: 1;
          filter: drop-shadow(0 8px 16px rgba(0, 0, 0, 0.24));
        }

        .chart-tooltip-value {
          fill: var(--primary-text-color);
          font-size: 17px;
          font-weight: 700;
        }

        .chart-tooltip-time {
          fill: var(--secondary-text-color);
          font-size: 12px;
          font-weight: 550;
        }

        .chart-header {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 10px;
          align-items: center;
        }

        .chart-header-meta {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 14px;
          min-width: 0;
        }

        .chart-history-actions {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
        }

        .chart-refresh-button {
          width: 34px;
          height: 34px;
          flex: 0 0 34px;
        }

        .chart-refresh-button ha-icon {
          --mdc-icon-size: 18px;
        }

        .history-scale-row {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 8px;
          margin-top: 0;
        }

        .history-scale-row .label {
          margin-right: 2px;
        }

        .chart-actions {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 6px;
        }

        .history-scale-row .history-scale {
          height: 34px;
          min-width: 44px;
          padding: 0 10px;
        }

        .history-scale-row .history-scale.selected {
          color: var(--text-primary-color);
          background: var(--primary-color);
          border-color: var(--primary-color);
        }

        .watering-marker {
          fill: #66c7ff;
          opacity: 0.95;
        }

        .watering-marker-dot {
          fill: #66c7ff;
          opacity: 1;
        }

        .quick-actions {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
          gap: 10px;
        }

        .quick-actions button {
          min-height: 52px;
        }

        .plant-actions {
          align-self: stretch;
          align-content: end;
        }

        .plant-main .stats {
          grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        }

        @media (min-width: 700px) {
          .plant-dashboard {
            grid-template-columns: minmax(0, 1fr) minmax(360px, 430px);
            grid-template-rows: minmax(0, 1fr) auto;
            grid-template-areas:
              "chart controls"
              "chart actions";
            gap: 16px;
          }

          .plant-side {
            grid-area: chart;
            height: 100%;
          }

          .plant-main {
            grid-area: controls;
            height: 100%;
          }

          .plant-main .stats {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }

          .plant-actions {
            grid-area: actions;
          }

          .quick-actions {
            grid-template-columns: repeat(auto-fit, minmax(104px, 1fr));
          }
        }

        @media (max-width: 520px) {
          ha-card.detail-card {
            width: 100%;
            max-width: 100%;
            margin-top: 12px;
            margin-left: 0;
            transform: none;
            overflow: hidden;
          }

          .dashboard-toolbar,
          .dashboard-grid {
            grid-template-columns: 1fr;
          }

          .dashboard-toolbar {
            top: 8px;
            right: 18px;
          }

          .dashboard-card.webui-standalone .dashboard-toolbar {
            top: -50px;
            right: 54px;
          }

          .device-pill {
            max-width: min(260px, calc(100vw - 48px));
          }

          .device-pill .name {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }

          .device-menu {
            max-width: calc(100vw - 48px);
          }

          .plant-main .stats,
          .quick-actions,
          .chart-header {
            grid-template-columns: 1fr;
          }

          .plant-actions {
            align-content: start;
          }

          .chart-header-meta {
            justify-content: space-between;
            align-items: center;
          }

          .chart-current-stat {
            text-align: left;
          }

          .chart-current-stat .value {
            font-size: 28px;
          }
        }

        .section-title {
          margin: 4px 0 -4px;
          font-size: 16px;
          font-weight: 650;
          color: var(--primary-text-color);
        }

        .grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 18px 20px;
        }

        .field {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-width: 0;
        }

        .field.wide {
          grid-column: 1 / -1;
        }

        .field.time-field {
          grid-column: 1 / -1;
        }

        input,
        select,
        textarea {
          width: 100%;
          box-sizing: border-box;
          margin-top: 0;
          min-height: 38px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 9px 11px;
          font: inherit;
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
        }

        input:focus,
        select:focus,
        textarea:focus {
          outline: none;
          border-color: var(--primary-color);
          box-shadow: inset 0 0 0 1px var(--primary-color);
        }

        textarea {
          resize: vertical;
          line-height: 1.4;
        }

        input[type="number"] {
          -moz-appearance: textfield;
        }

        input[type="number"]::-webkit-outer-spin-button,
        input[type="number"]::-webkit-inner-spin-button {
          -webkit-appearance: none;
          margin: 0;
        }

        .time-pair {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 8px;
          margin-top: 0;
        }

        .time-pair span {
          display: block;
          font-size: 11px;
          line-height: 1.2;
          color: var(--secondary-text-color);
        }

        .time-pair input {
          margin-top: 4px;
          text-align: center;
        }

        .action-strip {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(116px, auto) minmax(116px, auto);
          gap: 12px;
          align-items: end;
        }

        .catalog-search-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(88px, auto) minmax(88px, auto);
          gap: 10px;
          align-items: center;
        }

        .dialog-backdrop {
          position: fixed;
          inset: 0;
          z-index: 1000;
          display: grid;
          place-items: center;
          padding: 20px;
          background: rgba(0, 0, 0, 0.54);
        }

        .dialog {
          width: min(560px, 100%);
          max-height: calc(100vh - 40px);
          overflow: auto;
          border-radius: 14px;
          padding: 18px;
          background: var(--ha-card-background, var(--card-background-color));
          box-shadow: var(--ha-card-box-shadow, 0 12px 28px rgba(0, 0, 0, 0.3));
        }

        .plant-wizard-dialog {
          width: min(680px, calc(100vw - 40px));
          display: grid;
          grid-template-rows: auto auto minmax(0, 1fr) auto;
          overflow: hidden;
        }

        .plant-wizard-dialog .wizard-step {
          min-height: 0;
          overflow: auto;
          padding-right: 2px;
        }

        .custom-plants-dialog {
          width: min(640px, calc(100vw - 40px));
        }

        .custom-library-list {
          margin-top: 14px;
          max-height: min(52vh, 440px);
          overflow: auto;
        }

        .custom-library-pager {
          display: grid;
          grid-template-columns: minmax(92px, auto) minmax(0, 1fr) minmax(92px, auto);
          gap: 10px;
          align-items: center;
          margin-top: 12px;
        }

        .custom-library-pager .label {
          text-align: center;
        }

        .empty-state {
          display: grid;
          gap: 6px;
          margin-top: 14px;
          padding: 18px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .empty-state .subtitle {
          color: var(--secondary-text-color);
          line-height: 1.4;
        }

        .edit-dialog {
          width: min(420px, 100%);
        }

        .about-dialog {
          width: min(760px, calc(100vw - 40px));
          max-height: calc(100vh - 40px);
          margin-top: 56px;
          padding: 0;
          overflow: hidden;
        }

        .about-dialog-body {
          display: grid;
          gap: 18px;
          max-height: calc(100vh - 40px);
          overflow: auto;
          padding: 20px;
        }

        .about-dialog-header {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 40px;
          gap: 12px;
          align-items: start;
        }

        .about-dialog-header .icon-button {
          justify-self: end;
        }

        .dialog-title {
          font-size: 19px;
          font-weight: 650;
          color: var(--primary-text-color);
        }

        .guide-dialog {
          width: min(720px, 100%);
        }

        .guide-body {
          display: grid;
          gap: 14px;
          margin-top: 14px;
        }

        .guide-image-wrap {
          display: grid;
          place-items: center;
          min-height: 220px;
          overflow: hidden;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: #fff;
        }

        .guide-image {
          display: block;
          width: 100%;
          height: auto;
          max-height: min(54vh, 560px);
          object-fit: contain;
        }

        .guide-copy {
          display: grid;
          gap: 4px;
        }

        .guide-step-title {
          font-size: 16px;
          font-weight: 650;
          color: var(--primary-text-color);
        }

        .guide-step-text,
        .guide-counter {
          color: var(--secondary-text-color);
        }

        .guide-nav {
          display: grid;
          grid-template-columns: minmax(92px, auto) 1fr minmax(92px, auto);
          gap: 10px;
          align-items: center;
        }

        .guide-counter {
          text-align: center;
          font-size: 13px;
        }

        .guide-checkbox {
          display: flex;
          gap: 10px;
          align-items: center;
          color: var(--secondary-text-color);
          cursor: pointer;
        }

        .guide-checkbox input {
          width: 18px;
          height: 18px;
        }

        .dialog-actions {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
          margin-top: 16px;
        }

        .custom-plant-card {
          width: 100%;
          display: grid;
          grid-template-columns: 44px minmax(0, 1fr);
          gap: 12px;
          align-items: center;
          padding: 14px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
          color: var(--primary-text-color);
          text-align: left;
        }

        .custom-plant-card .subtitle {
          margin-top: 3px;
          color: var(--secondary-text-color);
          font-size: 13px;
          line-height: 1.35;
        }

        .custom-plant-icon {
          width: 44px;
          height: 44px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          background: color-mix(in srgb, var(--primary-color) 14%, transparent);
          color: var(--primary-color);
        }

        .wizard-progress {
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 6px;
          margin: 14px 0 16px;
        }

        .wizard-dot {
          height: 4px;
          border-radius: 999px;
          background: color-mix(in srgb, var(--primary-text-color) 18%, transparent);
        }

        .wizard-dot.active {
          background: var(--primary-color);
        }

        .wizard-step {
          min-height: 0;
        }

        .range-stack {
          display: grid;
          gap: 16px;
        }

        .range-control {
          display: grid;
          gap: 12px;
          padding: 14px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
        }

        .range-head {
          display: flex;
          gap: 12px;
          align-items: baseline;
          justify-content: space-between;
        }

        .range-value {
          color: var(--primary-text-color);
          font-weight: 650;
        }

        .range-inputs {
          display: grid;
          gap: 10px;
        }

        .range-inputs label {
          display: grid;
          grid-template-columns: 42px minmax(0, 1fr);
          gap: 10px;
          align-items: center;
          color: var(--secondary-text-color);
          font-size: 13px;
        }

        input[type="range"] {
          min-height: 24px;
          padding: 0;
          border: 0;
          background: transparent;
        }

        .profile-panel {
          display: grid;
          gap: 16px;
        }

        .profile-hero {
          display: grid;
          grid-template-columns: 132px minmax(0, 1fr);
          gap: 16px;
          align-items: center;
        }

        .profile-photo {
          width: 132px;
          height: 132px;
          overflow: hidden;
          border-radius: 12px;
          background: color-mix(in srgb, var(--primary-color) 16%, transparent);
          display: grid;
          place-items: center;
          color: var(--primary-color);
        }

        .profile-photo img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .profile-about {
          max-height: 260px;
          overflow: auto;
          margin-top: 8px;
          padding: 14px;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 5%, transparent);
          line-height: 1.45;
          white-space: pre-line;
        }

        .profile-stats {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }

        .channel-grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
        }

        .channel-choice {
          min-height: 96px;
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
          border: 1px solid var(--divider-color);
        }

        .channel-choice.active {
          background: color-mix(in srgb, var(--primary-color) 18%, transparent);
          border-color: var(--primary-color);
        }

        .mode-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }

        .mode-choice {
          min-height: 116px;
          padding: 16px;
          text-align: left;
          color: var(--primary-text-color);
          background: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
          border: 1px solid var(--divider-color);
        }

        .mode-choice.compact {
          min-height: 52px;
          text-align: center;
        }

        .mode-choice.active {
          background: color-mix(in srgb, var(--primary-color) 18%, transparent);
          border-color: var(--primary-color);
        }

        .mode-title {
          font-size: 17px;
          font-weight: 650;
        }

        .mode-text {
          margin-top: 8px;
          font-size: 13px;
          line-height: 1.35;
          color: var(--secondary-text-color);
        }

        .wizard-review {
          display: grid;
          grid-template-columns: 84px minmax(0, 1fr);
          gap: 14px;
          align-items: center;
        }

        @keyframes wizard-slide {
          from {
            opacity: 0;
            transform: translateX(12px);
          }
          to {
            opacity: 1;
            transform: translateX(0);
          }
        }

        .toast {
          position: fixed;
          left: 50%;
          bottom: 24px;
          z-index: 20;
          transform: translateX(-50%);
          max-width: min(420px, calc(100vw - 32px));
          padding: 12px 16px;
          border-radius: 999px;
          color: var(--primary-text-color);
          background: var(--ha-card-background, var(--card-background-color));
          border: 1px solid var(--divider-color);
          box-shadow: var(--ha-card-box-shadow, 0 12px 28px rgba(0, 0, 0, 0.3));
        }

        @media (max-width: 520px) {
          .dialog-backdrop {
            padding: 12px;
          }

          .about-dialog {
            width: calc(100vw - 24px);
            max-height: calc(100vh - 24px);
            margin-top: 32px;
          }

          .about-dialog-body {
            max-height: calc(100vh - 24px);
            padding: 16px;
          }

          .stats,
          .overview-grid,
          .compact-row,
          .choice-row,
          .reservoir-grid,
          .channel-grid,
          .mode-grid,
          .grid,
          .profile-hero,
          .profile-stats,
          .quick-actions,
          .actions,
          .action-strip {
            grid-template-columns: 1fr;
          }

          .profile-photo {
            width: min(160px, 100%);
            height: auto;
            aspect-ratio: 1;
          }
        }
      </style>

      <ha-card class="${detail ? "detail-card" : dashboard ? "dashboard-host-card" : ""}">
        ${renderLegacyAsPlants ? this._plantsTemplate() : overview ? this._overviewTemplate({ entities, overview }) : detail ? this._detailTemplate({
          deviceRecord,
          entities,
          plantConfigured,
          mode,
          next,
          moisture,
          manualDuration,
          firstWatering,
          firstWateringHour,
          firstWateringMinute,
          scheduleDuration,
          interval,
          smartMinMoisture,
          smartMaxMoisture,
          smartRange,
          smartDaytimeWatering,
          problems,
        }) : this._summaryTemplate({ entities, plantConfigured, mode, next, moisture, manualDuration, smartRange, problems })}
      </ha-card>

      ${this._reservoirGuideOpen ? this._reservoirGuideTemplate() : ""}
      ${this._reservoirOpen ? this._reservoirDialogTemplate() : ""}
      ${this._editDialog ? this._editDialogTemplate() : ""}
      ${this._plantWizardOpen ? this._plantWizardDialogTemplate() : ""}
      ${this._customPlantsOpen ? this._customPlantsDialogTemplate() : ""}
      ${this._modeWizardOpen ? this._modeWizardDialogTemplate() : ""}
      ${this._deletePlantDialogOpen ? this._deletePlantDialogTemplate() : ""}
      ${this._toast ? `<div class="toast">${this._escape(this._toast)}</div>` : ""}
    `;

    this._renderStandaloneIcons();
    this._bindEvents();
    this._mountHistoryGraph(entities);
    if (detail) {
      this._loadCubeHistoryApiIfNeeded();
    } else {
      this._loadGraphHistory();
    }
  }

  _overviewTemplate({ entities, overview }) {
    if (overview === "dashboard") {
      return this._dashboardTemplate({ entities });
    }
    if (overview === "tank") {
      return this._tankTemplate({ entities });
    }
    if (overview === "plants") {
      return this._plantsTemplate();
    }
    if (overview === "activity") {
      return this._activityOverviewTemplate();
    }
    return this._statusTemplate({ entities });
  }

  _renderStandaloneIcons() {
    if (!window.GROWCUBE_STANDALONE_WEBUI || !this.shadowRoot) {
      return;
    }
    this.shadowRoot.querySelectorAll("ha-icon").forEach((element) => {
      const icon = element.getAttribute("icon") || "";
      const replacement = document.createElement("span");
      replacement.className = "gc-icon";
      replacement.setAttribute("aria-hidden", "true");
      replacement.innerHTML = `<svg viewBox="0 0 24 24" focusable="false">${this._standaloneIconSvg(icon)}</svg>`;
      element.replaceWith(replacement);
    });
  }

  _standaloneIconSvg(icon) {
    const common = 'fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"';
    const filled = 'fill="currentColor"';
    const icons = {
      "mdi:refresh": `<path ${common} d="M20 6v5h-5"/><path ${common} d="M19 11a7 7 0 1 0-2.1 5"/>`,
      "mdi:dots-vertical": `<circle ${filled} cx="12" cy="5" r="1.8"/><circle ${filled} cx="12" cy="12" r="1.8"/><circle ${filled} cx="12" cy="19" r="1.8"/>`,
      "mdi:chevron-up": `<path ${common} d="m6 15 6-6 6 6"/>`,
      "mdi:chevron-down": `<path ${common} d="m6 9 6 6 6-6"/>`,
      "mdi:chevron-right": `<path ${common} d="m9 6 6 6-6 6"/>`,
      "mdi:arrow-left": `<path ${common} d="M19 12H5"/><path ${common} d="m12 5-7 7 7 7"/>`,
      "mdi:check": `<path ${common} d="m5 12 4 4 10-10"/>`,
      "mdi:close": `<path ${common} d="M6 6l12 12M18 6 6 18"/>`,
      "mdi:information-outline": `<circle ${common} cx="12" cy="12" r="9"/><path ${common} d="M12 10v6"/><circle ${filled} cx="12" cy="7" r="1"/>`,
      "mdi:delete-outline": `<path ${common} d="M6 7h12M10 7V5h4v2M8 7l1 12h6l1-12"/>`,
      "mdi:alert-circle": `<circle ${common} cx="12" cy="12" r="9"/><path ${common} d="M12 7v6"/><circle ${filled} cx="12" cy="17" r="1"/>`,
      "mdi:alert": `<path ${common} d="M12 4 3 20h18L12 4Z"/><path ${common} d="M12 9v5"/><circle ${filled} cx="12" cy="17" r="1"/>`,
      "mdi:cube-outline": `<path ${common} d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path ${common} d="M4 7.5 12 12l8-4.5M12 12v9"/>`,
      "mdi:sprout": `<path ${common} d="M12 20V9"/><path ${common} d="M12 10C8 5 4 6 3 11c5 1 8-1 9-1Z"/><path ${common} d="M12 12c2-5 7-6 9-2-4 3-7 3-9 2Z"/>`,
      "mdi:flower": `<circle ${common} cx="12" cy="12" r="2.2"/><circle ${common} cx="12" cy="5" r="2.5"/><circle ${common} cx="12" cy="19" r="2.5"/><circle ${common} cx="5" cy="12" r="2.5"/><circle ${common} cx="19" cy="12" r="2.5"/>`,
      "mdi:pot-mix-outline": `<path ${common} d="M7 10h10l-1 9H8l-1-9Z"/><path ${common} d="M6 10h12M10 10c-1-4-4-4-5-2M14 10c1-5 5-5 6-2"/>`,
      "mdi:water-pump": `<path ${common} d="M5 19h14M7 19V9h8v10M9 9V6h4v3M15 12h3v4"/><path ${common} d="M19 16c1.5 1.6 1.5 3 0 4-1.5-1-1.5-2.4 0-4Z"/>`,
      "mdi:message-alert-outline": `<path ${common} d="M5 5h14v10H9l-4 4V5Z"/><path ${common} d="M12 7v4"/><circle ${filled} cx="12" cy="13" r="1"/>`,
      "mdi:chart-bell-curve": `<path ${common} d="M4 18h16"/><path ${common} d="M5 17c3 0 3-10 7-10s4 10 7 10"/>`,
      "mdi:checkbox-blank-circle-outline": `<circle ${common} cx="12" cy="12" r="7"/>`,
      "mdi:water-alert": `<path ${common} d="M12 3c4 5 6 8 6 12a6 6 0 0 1-12 0c0-4 2-7 6-12Z"/><path ${common} d="M12 9v4"/><circle ${filled} cx="12" cy="16" r="1"/>`,
      "mdi:lock-alert": `<rect ${common} x="6" y="10" width="12" height="10" rx="2"/><path ${common} d="M9 10V7a3 3 0 0 1 6 0v3M12 13v3"/>`,
    };
    return icons[icon] || `<circle ${common} cx="12" cy="12" r="8"/><path ${common} d="M12 8v4l3 3"/>`;
  }

  _channels() {
    return ["a", "b", "c", "d"];
  }

  _channelName(channel) {
    return channel.toUpperCase();
  }

  _configuredChannels() {
    return this._channels()
      .map((channel) => ({
        channel,
        entities: this._entities(channel),
      }))
      .filter((item) => this._isPlantConfigured(item.entities, false));
  }

  _configuredChannelsForDevice(device) {
    return this._channels()
      .map((channel) => ({
        channel,
        deviceId: device.device_id,
        deviceName: device.name || device.device_id,
        metadata: device.channels?.[channel] || {},
        entities: {
          ...(device.entities || {}),
          ...(device.channels?.[channel] || {}),
        },
      }))
      .filter((item) => item.metadata?.configured || this._isPlantConfigured(item.entities, false));
  }

  _dashboardTemplate({ entities }) {
    const device = this._deviceRecord();
    const hasDeviceSwitcher = this._deviceRecords().length > 1;
    const standalone = window.GROWCUBE_STANDALONE_WEBUI;
    return `
      <div class="dashboard-card ${hasDeviceSwitcher ? "has-device-switcher" : ""} ${standalone ? "webui-standalone" : ""}">
        <div class="dashboard-toolbar">
          ${this._globalDeviceSwitcherTemplate(device?.device_id)}
        </div>
        <div class="dashboard-grid">
          <div class="dashboard-column">
            ${this._plantsTemplate()}
            ${this._statusTemplate({ entities })}
          </div>
          <div class="dashboard-column">
            ${this._tankTemplate({ entities })}
            ${this._activityOverviewTemplate()}
          </div>
        </div>
      </div>
    `;
  }

  _globalDeviceSwitcherTemplate(selectedDeviceId = this._selectedDeviceId()) {
    const devices = this._deviceRecords();
    if (devices.length <= 1) {
      return "";
    }
    const selected = devices.find((device) => device.device_id === selectedDeviceId) || devices[0];
    return `
      <div class="global-device-switcher">
        <button type="button" class="device-pill" data-action="toggle-device-menu" aria-label="Select GrowCube device" aria-expanded="${this._deviceMenuOpen ? "true" : "false"}">
          <ha-icon icon="mdi:cube-outline"></ha-icon>
          <span class="name">${this._escape(selected?.name || selected?.device_id || "GrowCube")}</span>
          <ha-icon icon="${this._deviceMenuOpen ? "mdi:chevron-up" : "mdi:chevron-down"}"></ha-icon>
        </button>
        ${this._deviceMenuOpen ? `
          <div class="device-menu" role="menu">
            ${devices.map((device) => {
              const active = device.device_id === selectedDeviceId;
              return `
                <button type="button" class="${active ? "active" : ""}" data-action="select-device" data-device-id="${this._escape(device.device_id)}" role="menuitem">
                  <span class="check">${active ? '<ha-icon icon="mdi:check"></ha-icon>' : ""}</span>
                  <span class="name">${this._escape(device.name || device.device_id)}</span>
                </button>
              `;
            }).join("")}
          </div>
        ` : ""}
      </div>
    `;
  }

  _firstAvailableChannel() {
    return this._availablePlantWizardChannels()[0] || "";
  }

  _availablePlantWizardChannels() {
    const device = this._deviceRecord();
    return this._channels().filter((channel) => {
      const mappedChannelEntities = device?.channels?.[channel] || {};
      if (Object.keys(mappedChannelEntities).length) {
        return !this._isPlantConfigured({
          ...(device?.entities || {}),
          ...mappedChannelEntities,
        }, false);
      }
      return !this._isPlantConfigured(this._entities(channel), false);
    });
  }

  _plantsTemplate() {
    const device = this._deviceRecord();
    const plants = device ? this._configuredChannelsForDevice(device) : [];
    const hasFreeChannel = plants.length < this._channels().length;
    const plantCountText = plants.length
      ? `${plants.length} active plant${plants.length === 1 ? "" : "s"}`
      : "No plants added yet";
    return `
      <div class="card">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:sprout"></ha-icon></div>
          <div>
            <div class="title">Plants</div>
            <div class="subtitle">${this._escape(plantCountText)}</div>
          </div>
        </div>
        ${
          plants.length
            ? `<div class="plants-list">${plants.map((plant) => this._plantRowTemplate(plant)).join("")}</div>`
            : `<button type="button" class="wide-button" data-action="open-add-plant">Add plant</button>`
        }
        ${plants.length && hasFreeChannel ? '<button type="button" class="wide-button secondary" data-action="open-add-plant">Add another plant</button>' : ""}
      </div>
    `;
  }

  _plantRowTemplate({ channel, entities, deviceId = "", metadata = {} }) {
    const photoUrl = this._resolvedPlantPhotoUrl(
      metadata.photo_url_value,
      metadata.image_url,
      metadata.photo_url,
      entities.photo_url,
      this._cachedPlantPhotoUrl(deviceId, channel),
    );
    this._rememberPlantPhotoUrl(deviceId, channel, photoUrl);
    const name = metadata.plant_name || this._entityDisplay(entities.name, this._channelName(channel));
    const moisture = this._entityDisplay(entities.moisture, "Unknown");
    const mode = this._normalizeMode(this._entityState(entities.mode, "Disabled"));
    return `
      <div class="plant-row" data-action="navigate-channel" data-channel="${this._escape(channel)}" data-device-id="${this._escape(deviceId)}" role="button" tabindex="0">
        <div class="plant-photo">
          ${photoUrl ? `<img src="${this._escape(photoUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
        </div>
        <div class="plant-meta">
          <div class="title">${this._escape(name)}</div>
          <div class="subtitle">${this._escape(this._channelName(channel))} · ${this._escape(this._modeDisplay(mode))}</div>
        </div>
        <div class="plant-stats">
          <div class="label">Moisture</div>
          <div class="value">${this._escape(moisture)}</div>
        </div>
      </div>
    `;
  }

  _statusTemplate({ entities }) {
    const device = this._deviceRecord();
    const deviceEntities = device?.entities || entities;
    const tankRemaining = this._entityDisplay(deviceEntities.tank_remaining, "Unknown");
    const tankDaysLeft = this._entityDisplay(deviceEntities.tank_days_left, "Unknown");
    return `
      <div class="card">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:cube-outline"></ha-icon></div>
          <div>
            <div class="title">Status</div>
            <div class="subtitle">Tank and room status</div>
          </div>
        </div>
        <div class="overview-grid">
          <div class="stat" data-action="more-info" data-entity="${this._escape(deviceEntities.temperature)}" role="button" tabindex="0">
            <div class="label">Temperature</div>
            <div class="value">${this._escape(this._entityDisplay(deviceEntities.temperature, "Unknown"))}</div>
          </div>
          <div class="stat" data-action="more-info" data-entity="${this._escape(deviceEntities.humidity)}" role="button" tabindex="0">
            <div class="label">Humidity</div>
            <div class="value">${this._escape(this._entityDisplay(deviceEntities.humidity, "Unknown"))}</div>
          </div>
          <div class="stat">
            <div class="label">Tank</div>
            <div class="value">${this._escape(tankRemaining)}</div>
          </div>
          <div class="stat">
            <div class="label">Days left</div>
            <div class="value">${this._escape(tankDaysLeft)}</div>
          </div>
        </div>
      </div>
    `;
  }

  _activityOverviewTemplate() {
    const device = this._deviceRecord();
    return `
      <div class="card">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:message-alert-outline"></ha-icon></div>
          <div>
            <div class="title">Recent activity</div>
            <div class="subtitle">Watering history and alerts</div>
          </div>
          <div class="header-side">
            <button type="button" class="icon-button" data-action="refresh-activity" aria-label="Refresh watering history">
              <ha-icon icon="mdi:refresh"></ha-icon>
            </button>
          </div>
        </div>
        ${this._activityFeedTemplate()}
      </div>
    `;
  }

  async _refreshAllHistory() {
    const requests = this._channels()
      .map((channel) => this._entities(channel))
      .filter((entities) => entities.load_history)
      .map((entities) => {
        if (entities.history_count) {
          delete this._cubeHistoryRequestedAt[entities.history_count];
        }
        return this._press(entities.load_history);
      });
    if (!requests.length) {
      this._showError("History controls are unavailable");
      return;
    }
    this._cubeHistory = {};
    this._cubeHistoryLoadedAt = {};
    try {
      await Promise.allSettled(requests);
      this._showToast("Refreshing watering history");
    } catch (error) {
      this._showError("Could not refresh history");
    }
  }

  _tankTemplate({ entities }) {
    const device = this._deviceRecord();
    const deviceEntities = device?.entities || entities;
    const growcubeCapacity = 1500;
    const tankLevelText = this._entityDisplay(deviceEntities.tank_level, "Unknown");
    const tankLevel = this._clamp(this._number(deviceEntities.tank_level, 0), 0, 100);
    const tankCapacity = this._number(deviceEntities.tank_capacity, 1500);
    const customCapacity = tankCapacity !== growcubeCapacity;
    return `
      <div class="card">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:water-pump"></ha-icon></div>
          <div>
            <div class="title">Tank</div>
            <div class="subtitle">Reservoir and fill status</div>
          </div>
        </div>
        <div class="tank-meter stat" data-action="more-info" data-entity="${this._escape(deviceEntities.tank_level)}" role="button" tabindex="0">
          <div class="label">Tank level ${this._escape(tankLevelText)} of ${tankCapacity} mL</div>
          <div class="meter-track"><div class="meter-fill" style="width: ${tankLevel}%"></div></div>
        </div>
        ${this._problemBannerTemplate(this._problemItems({ ...deviceEntities, ...(device?.channels?.[this._channelKey()] || {}) }))}
        <div class="tank-tools">
          <div class="tool-panel">
            <div class="tool-title">Reservoir</div>
            <div class="reservoir-grid">
              <button type="button" class="state-button reservoir-choice ${customCapacity ? "" : "active"}" data-action="set-reservoir-capacity" data-entity="${this._escape(deviceEntities.tank_capacity)}" data-capacity="${growcubeCapacity}">
                <span class="choice-title">GrowCube</span>
                <span class="choice-meta">${growcubeCapacity} mL</span>
              </button>
              <button type="button" class="state-button reservoir-choice ${customCapacity ? "active" : ""}" data-action="custom-capacity" data-entity="${this._escape(deviceEntities.tank_capacity)}" data-current-capacity="${tankCapacity}">
                <span class="choice-title">Custom</span>
                <span class="choice-meta">${customCapacity ? `${tankCapacity} mL` : "Set any size"}</span>
              </button>
            </div>
          </div>
        </div>
        <button type="button" class="wide-button" data-action="mark-tank-full" data-entity="${this._escape(deviceEntities.mark_tank_full)}">Mark tank full</button>
      </div>
    `;
  }

  _graphTemplate({ entities }) {
    const title = this._config.name || `${this._plantName()} moisture`;
    if (!this._isPlantConfigured(entities)) {
      return `
        <ha-card>
          <div class="card">
            <div class="header">
              <div class="plant-icon"><ha-icon icon="mdi:pot-mix-outline"></ha-icon></div>
              <div>
                <div class="title">${this._escape(this._config.name || this._channelLabel())}</div>
                <div class="subtitle">Add a plant to show moisture history</div>
              </div>
            </div>
          </div>
        </ha-card>
      `;
    }
    if (!entities.moisture) {
      return `
        <ha-card>
          <div class="card">
            <div class="header">
              <div class="plant-icon"><ha-icon icon="mdi:chart-bell-curve"></ha-icon></div>
              <div>
                <div class="title">${this._escape(title)}</div>
                <div class="subtitle">Moisture entity not found</div>
              </div>
            </div>
          </div>
        </ha-card>
      `;
    }
    return `
      <div
        class="native-history-graph"
        data-native-history-graph
        data-entity="${this._escape(entities.moisture)}"
        data-title="${this._escape(title)}"
      ></div>
    `;
  }

  _mountHistoryGraph(entities) {
    if (!this._config.graph || !entities.moisture) {
      return;
    }
    const host = this.shadowRoot.querySelector("[data-native-history-graph]");
    if (!host) {
      return;
    }
    const mount = async () => {
      if (!this.shadowRoot.contains(host)) {
        return;
      }
      const cardConfig = {
        type: "history-graph",
        title: host.dataset.title || this._config.name || `${this._plantName()} moisture`,
        hours_to_show: this._clamp(Number(this._config.hours_to_show) || 72, 1, 168),
        entities: [
          {
            entity: entities.moisture,
            name: "Moisture",
          },
        ],
      };

      let card;
      try {
        if (window.loadCardHelpers) {
          const helpers = await window.loadCardHelpers();
          card = helpers.createCardElement(cardConfig);
        } else {
          card = document.createElement("hui-history-graph-card");
          card.setConfig(cardConfig);
        }
      } catch (error) {
        host.innerHTML = `
          <ha-card>
            <div class="card">
              <div class="chart-empty">History graph unavailable</div>
            </div>
          </ha-card>
        `;
        return;
      }
      card.hass = this._hass;
      host.replaceChildren(card);
    };

    mount();
  }

  _summaryTemplate({ entities, plantConfigured, mode, next, moisture, manualDuration, smartRange, problems }) {
    if (!plantConfigured) {
      return this._emptyChannelTemplate({ entities });
    }
    const problemBanner = this._problemBannerTemplate(problems);
    const blocked = this._wateringBlocked(problems);
    const automaticLabel = mode === "Smart" ? "Moisture range" : "Next watering";
    const automaticValue = mode === "Smart" ? smartRange : next;
    const photoUrl = this._currentPlantPhotoUrl();
    return `
      <div class="card summary" data-action="navigate">
        <div class="header">
          <div class="plant-icon">${photoUrl ? `<img src="${this._escape(photoUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}</div>
          <div>
            <div class="title">${this._escape(this._plantName())}</div>
            <div class="subtitle">${this._escape(this._config.channel || this._channelLabel())}</div>
          </div>
          <ha-icon icon="mdi:chevron-right"></ha-icon>
        </div>
        <div class="stats">
          <div class="stat">
            <div class="label">Moisture</div>
            <div class="value">${this._escape(moisture)}</div>
          </div>
          <div class="stat">
            <div class="label">${this._escape(automaticLabel)}</div>
            <div class="value">${this._escape(automaticValue)}</div>
          </div>
        </div>
        ${problemBanner}
        <div class="actions">
          <button type="button" data-action="water" ${blocked ? "disabled" : ""}>Water ${manualDuration} mL</button>
          <button type="button" class="danger" data-action="stop">Stop</button>
        </div>
      </div>
    `;
  }

  _detailTemplate(data) {
    if (!data.plantConfigured) {
      return this._plantSetupTemplate(data);
    }
    const profile = this._profileMetadata(data.entities);
    const showNextWatering = data.mode === "Repeating";
    const nextLabel = "Next watering";
    const nextValue = data.next;
    const modeLabel = "Mode";
    const modeAction = "edit-mode";
    const modeValue = this._modeDisplay(data.mode);
    const rangeLabel = "Moisture range";
    const rangeAction = "edit-smart-range";
    const rangeValue = data.smartRange;
    const secondaryLabel = data.mode === "Smart" ? "Daytime watering" : "Schedule";
    const secondaryAction = data.mode === "Smart" ? "edit-daytime" : "edit-repeating-schedule";
    const secondaryValue = data.mode === "Smart"
      ? (data.smartDaytimeWatering ? "On" : "Off")
      : `${data.scheduleDuration} mL / ${Math.max(1, Math.round(data.interval / 24))}d`;
    const photoUrl = this._currentPlantPhotoUrl();
    return `
      <div class="card detail detail-flat">
        <div class="plant-dashboard">
          <div class="plant-main plant-section">
            <div class="plant-titlebar">
              <div class="plant-photo">
                ${photoUrl ? `<img src="${this._escape(photoUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
              </div>
              <div>
                <div class="title" data-action="edit-plant-name" role="button" tabindex="0">${this._escape(this._plantName())}</div>
                <div class="channel-pill"><ha-icon icon="mdi:checkbox-blank-circle-outline"></ha-icon><span>${this._escape(this._config.channel || this._channelLabel())}</span></div>
              </div>
              <div class="plant-menu-anchor">
                <button type="button" class="icon-button" data-action="toggle-detail-menu" aria-label="Plant menu" aria-expanded="${this._detailMenuOpen ? "true" : "false"}">
                  <ha-icon icon="mdi:dots-vertical"></ha-icon>
                </button>
                ${this._detailMenuOpen ? `
                  <div class="detail-menu">
                    <button type="button" data-action="open-about"><ha-icon icon="mdi:information-outline"></ha-icon><span>About</span></button>
                    ${data.entities.reset ? '<button type="button" class="danger" data-action="delete-plant"><ha-icon icon="mdi:delete-outline"></ha-icon><span>Delete plant</span></button>' : ""}
                  </div>
                ` : ""}
              </div>
            </div>
            <div class="stats">
              ${showNextWatering ? `
                <div class="stat" data-action="edit-first-watering" role="button" tabindex="0">
                  <div class="label">${this._escape(nextLabel)}</div>
                  <div class="value">${this._escape(nextValue)}</div>
                </div>
              ` : ""}
              <div class="stat" data-action="${this._escape(modeAction)}" role="button" tabindex="0">
                <div class="label">${this._escape(modeLabel)}</div>
                <div class="value">${this._escape(modeValue)}</div>
              </div>
              <div class="stat" data-action="${this._escape(rangeAction)}" role="button" tabindex="0">
                <div class="label">${this._escape(rangeLabel)}</div>
                <div class="value">${this._escape(rangeValue)}</div>
              </div>
              <div class="stat" data-action="${this._escape(secondaryAction)}" role="button" tabindex="0">
                <div class="label">${this._escape(secondaryLabel)}</div>
                <div class="value">${this._escape(secondaryValue)}</div>
              </div>
              <div class="stat" data-action="edit-manual-amount" role="button" tabindex="0">
                <div class="label">Manual amount</div>
                <div class="value">${this._escape(data.manualDuration)} mL</div>
              </div>
            </div>
            ${this._problemBannerTemplate(data.problems)}
          </div>
          <div class="plant-actions plant-section">
            <div class="section-title">Actions</div>
            <div class="quick-actions">
              <button type="button" data-action="water" ${this._wateringBlocked(data.problems) ? "disabled" : ""}>Water</button>
              <button type="button" class="danger" data-action="stop">Stop</button>
            </div>
          </div>
          <div class="plant-side">
            ${this._detailChartTemplate(data)}
          </div>
        </div>
      </div>
      ${this._aboutDialogOpen ? this._aboutDialogTemplate(data, profile) : ""}
    `;
  }

  _aboutDialogTemplate(data, profile) {
    const aboutText = profile.description || (this._aboutProfileLoading ? "Loading plant information..." : "No plant information is available for this profile yet.");
    const category = profile.category || "Plant profile";
    const channelMeta = this._deviceRecord()?.channels?.[this._channelKey()] || {};
    const photoUrl = this._resolvedPlantPhotoUrl(
      channelMeta.photo_url_value,
      channelMeta.image_url,
      channelMeta.photo_url,
      data.entities.photo_url,
      this._catalogImageUrl(this._plantWizardSelected || {}),
      this._cachedPlantPhotoUrl(this._deviceRecord()?.device_id, this._channelKey()),
    );
    this._rememberPlantPhotoUrl(this._deviceRecord()?.device_id, this._channelKey(), photoUrl);
    const statCards = [
      this._profileStatTemplate("Soil moisture", this._rangeText(data.smartMinMoisture, data.smartMaxMoisture, "%")),
      this._hasRange(profile.tempMin, profile.tempMax)
        ? this._profileStatTemplate("Temperature", this._rangeText(profile.tempMin, profile.tempMax, "°C"))
        : "",
      this._hasRange(profile.airHumidityMin, profile.airHumidityMax)
        ? this._profileStatTemplate("Air humidity", this._rangeText(profile.airHumidityMin, profile.airHumidityMax, "%"))
        : "",
    ].filter(Boolean).join("");
    return `
      <div class="dialog-backdrop" data-action="close-about-dialog">
        <div class="dialog about-dialog" role="dialog" aria-modal="true" aria-label="About this plant">
          <div class="about-dialog-body">
            <div class="about-dialog-header">
              <div>
                <div class="dialog-title">About this plant</div>
                <div class="subtitle">${this._escape(this._config.channel || this._channelLabel())}</div>
              </div>
              <button type="button" class="icon-button" data-action="close-about-button" aria-label="Close about">
                <ha-icon icon="mdi:close"></ha-icon>
              </button>
            </div>
            <div class="profile-panel">
              <div class="profile-hero">
                <div class="profile-photo">
                  ${photoUrl ? `<img src="${this._escape(photoUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
                </div>
                <div>
                  <div class="title">${this._escape(this._plantName())}</div>
                  <div class="subtitle">${this._escape(category)}</div>
                </div>
              </div>
              ${statCards ? `<div class="profile-stats">${statCards}</div>` : ""}
              <div>
                <div class="section-title">About</div>
                <div class="profile-about">${this._escape(aboutText)}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  _detailChartMetricTemplate(entities, moisture, floating = false) {
    if (!entities?.moisture) {
      return "";
    }
    return `
      <div class="chart-current-stat${floating ? " floating" : ""}" data-action="more-info" data-entity="${this._escape(entities.moisture)}" role="button" tabindex="0">
        <div class="label">Moisture</div>
        <div class="value">${this._escape(moisture)}</div>
      </div>
    `;
  }

  _detailChartHeaderTemplate(historyHours, entities, moisture, includeMetric = true) {
    return `
      <div class="chart-header">
        <div class="section-title">Moisture history</div>
        <div class="chart-header-meta">
          ${includeMetric ? this._detailChartMetricTemplate(entities, moisture) : ""}
          <div class="chart-history-actions">
            <button type="button" class="icon-button chart-refresh-button" data-action="refresh-current-history" aria-label="Refresh moisture history">
              <ha-icon icon="mdi:refresh"></ha-icon>
            </button>
            ${this._historyScaleRowTemplate(historyHours)}
          </div>
        </div>
      </div>
    `;
  }

  _historyTimeTemplate(timestamp, align = "start") {
    if (!Number.isFinite(timestamp)) {
      return `<div class="chart-time${align === "end" ? " end" : ""}"></div>`;
    }
    const date = new Date(timestamp);
    const timeText = date.toLocaleTimeString(this._dateLocale(), this._dateTimeOptions({
      hour: "2-digit",
      minute: "2-digit",
    }));
    const dateText = date.toLocaleDateString(this._dateLocale(), {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
    return `
      <div class="chart-time${align === "end" ? " end" : ""}">
        <span class="chart-time-primary">${this._escape(timeText)}</span>
        <span class="chart-time-secondary">${this._escape(dateText)}</span>
      </div>
    `;
  }

  _detailChartTemplate(data) {
    const { entities, moisture } = data;
    const width = 520;
    const height = 280;
    const padding = 20;
    const leftPadding = 52;
    const historyHours = this._historyHours();
    const historyState = this._detailHistoryState(entities);
    const visibleHistoryPoints = this._detailHistoryPoints(entities, historyHours);
    const wateringEvents = this._detailWateringEvents(entities);
    const windowEnd = Date.now();
    const windowStart = windowEnd - historyHours * 60 * 60 * 1000;
    if (historyState?.attributes?.history_loading) {
      return `
        <div class="chart-panel">
          ${this._detailChartHeaderTemplate(historyHours, entities, moisture, false)}
          <div class="chart-visual chart-placeholder">
            ${this._detailChartMetricTemplate(entities, moisture, true)}
            <div class="chart-empty">Loading history...</div>
          </div>
        </div>
      `;
    }
    const usingFallbackHistory = !visibleHistoryPoints.length;
    const graphSourcePoints = usingFallbackHistory
      ? this._fallbackMoistureHistoryPoints(entities, windowEnd)
      : visibleHistoryPoints;
    if (!graphSourcePoints.length) {
      return `
        <div class="chart-panel">
          ${this._detailChartHeaderTemplate(historyHours, entities, moisture, false)}
          <div class="chart-visual chart-placeholder">
            ${this._detailChartMetricTemplate(entities, moisture, true)}
            <div class="chart-empty">${this._historyEmptyText(historyState, historyHours)}</div>
          </div>
        </div>
      `;
    }
    const points = usingFallbackHistory
      ? graphSourcePoints
      : this._extendFreshHistoryToNow(this._smoothGraphPoints(graphSourcePoints), windowEnd);
    const coords = this._graphCoordinates(points, width, height, padding, leftPadding, windowStart, windowEnd);
    const minRange = this._clamp(Number(data.smartMinMoisture), 0, 100);
    const maxRange = this._clamp(Number(data.smartMaxMoisture), 0, 100);
    const rangeLineFor = (value, label, offset = -4) => {
      const y = padding + (1 - value / 100) * (height - padding * 2);
      return `
        <line class="chart-range-line" x1="${leftPadding}" y1="${y}" x2="${width - padding}" y2="${y}"></line>
        <text class="chart-range-label" x="${width - padding - 42}" y="${y + offset}">${label} ${value}%</text>
      `;
    };
    const rangeLines = [
      rangeLineFor(maxRange, "Max", maxRange - minRange < 8 ? -8 : -4),
      rangeLineFor(minRange, "Min", maxRange - minRange < 8 ? 14 : 14),
    ].join("");
    const chartPoints = this._escape(JSON.stringify(coords.map((point) => ({
      t: point.t,
      v: Math.round(point.v),
      x: Math.round(point.x * 10) / 10,
      y: Math.round(point.y * 10) / 10,
    }))));
    const path = this._graphPath(points, width, height, padding, leftPadding, windowStart, windowEnd);
    const firstCoord = coords[0];
    const lastCoord = coords[coords.length - 1];
    const areaPath = path && firstCoord && lastCoord && coords.length > 1
      ? `M ${firstCoord.x} ${height - padding} ${path} L ${lastCoord.x} ${height - padding} Z`
      : "";
    return `
      <div class="chart-panel">
        ${this._detailChartHeaderTemplate(historyHours, entities, moisture, false)}
        <div class="chart-visual" data-chart-visual>
          ${this._detailChartMetricTemplate(entities, moisture, true)}
          <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Moisture history" data-chart-svg data-chart-points="${chartPoints}">
            <text class="chart-axis-label" x="6" y="${padding + 5}">100%</text>
            <text class="chart-axis-label" x="14" y="${padding + (height - padding * 2) * 0.25 + 5}">75%</text>
            <text class="chart-axis-label" x="14" y="${height / 2 + 5}">50%</text>
            <text class="chart-axis-label" x="14" y="${padding + (height - padding * 2) * 0.75 + 5}">25%</text>
            <text class="chart-axis-label" x="27" y="${height - padding + 5}">0%</text>
            <line class="chart-grid" x1="${leftPadding}" y1="${padding}" x2="${width - padding}" y2="${padding}"></line>
            <line class="chart-grid" x1="${leftPadding}" y1="${padding + (height - padding * 2) * 0.25}" x2="${width - padding}" y2="${padding + (height - padding * 2) * 0.25}"></line>
            <line class="chart-grid" x1="${leftPadding}" y1="${height / 2}" x2="${width - padding}" y2="${height / 2}"></line>
            <line class="chart-grid" x1="${leftPadding}" y1="${padding + (height - padding * 2) * 0.75}" x2="${width - padding}" y2="${padding + (height - padding * 2) * 0.75}"></line>
            <line class="chart-grid" x1="${leftPadding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
            ${rangeLines}
            <path class="chart-fill" d="${areaPath}"></path>
            <path class="chart-path" d="${path}"></path>
            ${this._wateringMarkersTemplate(wateringEvents, points, width, height, padding, leftPadding, windowStart, windowEnd)}
            <g class="chart-hover" data-chart-hover hidden>
              <line class="chart-hover-guide" data-chart-hover-guide x1="${leftPadding}" y1="${padding}" x2="${leftPadding}" y2="${height - padding}"></line>
              <circle class="chart-hover-dot" data-chart-hover-dot cx="${leftPadding}" cy="${height - padding}" r="5"></circle>
              <g data-chart-tooltip transform="translate(${leftPadding} ${padding})">
                <rect class="chart-tooltip-box" width="124" height="48" rx="8"></rect>
                <text class="chart-tooltip-value" data-chart-tooltip-value x="10" y="20"></text>
                <text class="chart-tooltip-time" data-chart-tooltip-time x="10" y="38"></text>
              </g>
            </g>
          </svg>
        </div>
        <div class="chart-footer">
          ${this._historyTimeTemplate(windowStart)}
          ${this._historyTimeTemplate(windowEnd, "end")}
        </div>
      </div>
    `;
  }

  _historyScaleButtonTemplate(hours, label, currentHours) {
    const selected = Number(currentHours) === hours;
    return `<button type="button" class="secondary history-scale${selected ? " selected" : ""}" data-action="history-scale" data-hours="${hours}" aria-pressed="${selected ? "true" : "false"}">${label}</button>`;
  }

  _historyScaleRowTemplate(currentHours) {
    return `
      <div class="history-scale-row" aria-label="History scale">
        ${this._historyScaleButtonTemplate(24, "24h", currentHours)}
        ${this._historyScaleButtonTemplate(72, "3d", currentHours)}
        ${this._historyScaleButtonTemplate(168, "7d", currentHours)}
      </div>
    `;
  }

  _detailHistoryPoints(entities, hours = 168) {
    const state = this._detailHistoryState(entities);
    const attrHistory = state?.attributes?.history;
    const now = Date.now();
    const cutoff = now - this._clamp(Number(hours) || 168, 1, 168) * 60 * 60 * 1000;
    if (Array.isArray(attrHistory) && attrHistory.length) {
      return attrHistory
        .map((item) => ({
          t: new Date(item.timestamp).getTime(),
          v: Number(item.moisture),
        }))
        .filter((point) => Number.isFinite(point.t) && point.t >= cutoff && point.t <= now && Number.isFinite(point.v) && point.v > 0 && point.v <= 100)
        .sort((a, b) => a.t - b.t);
    }
    return [];
  }

  _allDetailHistoryPoints(historyState) {
    const attrHistory = historyState?.attributes?.history;
    if (!Array.isArray(attrHistory)) {
      return [];
    }
    return attrHistory
      .map((item) => ({
        t: new Date(item.timestamp).getTime(),
        v: Number(item.moisture),
      }))
      .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.v) && point.v > 0 && point.v <= 100)
      .sort((a, b) => a.t - b.t);
  }

  _fallbackMoistureHistoryPoints(entities, windowEnd) {
    const moisture = Number(this._entityState(entities.moisture, NaN));
    if (!Number.isFinite(moisture) || moisture < 0 || moisture > 100) {
      return [];
    }
    return [{ t: windowEnd, v: moisture }];
  }

  _historyEmptyText(historyState, hours) {
    const allPoints = this._allDetailHistoryPoints(historyState);
    if (!allPoints.length) {
      return "No history received yet";
    }
    return `No points in ${this._historyWindowLabel(hours)}; try a wider scale`;
  }

  _historyWindowLabel(hours) {
    const value = Number(hours);
    if (value === 24) {
      return "24h";
    }
    if (value === 72) {
      return "3d";
    }
    if (value === 168) {
      return "7d";
    }
    return `${value}h`;
  }

  _requestCubeHistoryIfNeeded(entities) {
    if (!entities?.load_history || !entities?.history_count) {
      return;
    }
    const state = this._state(entities.history_count);
    if (!state || state.attributes?.history_loading) {
      return;
    }
    const history = state.attributes?.history;
    if (Array.isArray(history) && history.length) {
      if (state.attributes?.history_complete && state.attributes?.watering_events_complete) {
        return;
      }
    } else if (state.attributes?.history_complete && state.attributes?.watering_events_complete) {
      return;
    }
    const key = entities.history_count;
    const now = Date.now();
    if (this._cubeHistoryRequestedAt[key] && now - this._cubeHistoryRequestedAt[key] < 5 * 60 * 1000) {
      return;
    }
    this._cubeHistoryRequestedAt[key] = now;
    this._press(entities.load_history).catch(() => undefined);
  }

  _detailWateringEvents(entities) {
    const events = this._detailHistoryState(entities)?.attributes?.watering_events;
    if (!Array.isArray(events)) {
      return [];
    }
    return events
      .map((event) => new Date(typeof event === "string" ? event : event?.timestamp).getTime())
      .filter((timestamp) => Number.isFinite(timestamp))
      .sort((a, b) => a - b);
  }

  _formatActivityTimestamp(timestamp) {
    if (!timestamp) {
      return "";
    }
    return new Date(timestamp).toLocaleString(this._dateLocale(), this._dateTimeOptions({
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }));
  }

  _overviewActivityItems() {
    const items = [];
    const now = Date.now();
    const pushItem = (item) => {
      if (!item?.ts || !Number.isFinite(item.ts)) {
        return;
      }
      items.push(item);
    };

    const device = this._deviceRecord();
    const deviceEntities = device?.entities || {};
    if (this._isOn(deviceEntities.connection_problem)) {
      pushItem({ ts: now + 1, kind: "problem", title: "Device not connected", detail: "Device" });
    }
    if (this._isOn(deviceEntities.water_warning)) {
      pushItem({ ts: now, kind: "problem", title: "Water tank low", detail: "Tank" });
    }
    if (this._isOn(deviceEntities.device_locked)) {
      pushItem({ ts: now - 1, kind: "problem", title: "Device locked", detail: "Device" });
    }

    this._channels().forEach((channel) => {
      const entities = { ...deviceEntities, ...(device?.channels?.[channel] || {}) };
      const channelLabel = this._channelName(channel);
      const problemLabels = [];
      if (this._isOn(entities.watering_locked)) {
        problemLabels.push("Smart watering locked");
      } else if (this._isOn(entities.watering_issue) && !this._isProblemDismissed(entities.watering_issue, "watering_issue")) {
        problemLabels.push("Smart watering issue");
      }
      if (this._isOn(entities.sensor_disconnected)) {
        problemLabels.push("Sensor disconnected");
      } else if (this._isOn(entities.sensor_fault)) {
        problemLabels.push("Sensor fault");
      }
      if (this._isOn(entities.outlet_blocked)) {
        problemLabels.push("Pump blocked");
      }
      if (this._isOn(entities.outlet_locked)) {
        problemLabels.push("Outlet locked");
      }
      problemLabels.forEach((label, index) => {
        pushItem({
          ts: now - index,
          kind: "problem",
          title: label,
          detail: channelLabel,
        });
      });

      const historyState = this._state(entities.history_count);
      const events = Array.isArray(historyState?.attributes?.watering_events)
        ? historyState.attributes.watering_events
        : [];
      events.forEach((event) => {
        const timestamp = new Date(typeof event === "string" ? event : event?.timestamp).getTime();
        if (!Number.isFinite(timestamp)) {
          return;
        }
        const source = typeof event === "string" ? "" : String(event?.source || "");
        pushItem({
          ts: timestamp,
          kind: "watering",
          title: this._wateringActivityTitle(source),
          detail: this._wateringActivityDetail(channelLabel, event),
        });
      });

      if (!events.length) {
        const rawTimestamp = this._state(entities.last_watering)?.state;
        const timestamp = rawTimestamp ? new Date(rawTimestamp).getTime() : NaN;
        if (Number.isFinite(timestamp)) {
          pushItem({
            ts: timestamp,
            kind: "watering",
            title: "Watering",
            detail: channelLabel,
          });
        }
      }
    });

    return items
      .sort((a, b) => b.ts - a.ts)
      .slice(0, 6);
  }

  _wateringActivityTitle(source) {
    if (source === "manual") {
      return "Manual watering";
    }
    if (source === "timed") {
      return "Timed watering";
    }
    if (source === "smart") {
      return "Smart watering";
    }
    return "Watering";
  }

  _wateringActivityDetail(channelLabel, event) {
    return channelLabel;
  }

  _activityFeedTemplate() {
    const items = this._overviewActivityItems();
    const rows = items.length
      ? items.map((item) => `
          <div class="activity-row ${item.kind}">
            <div>
              <div class="activity-title">${this._escape(item.title)}</div>
              <div class="activity-detail">${this._escape(item.detail)}</div>
            </div>
            <div class="activity-time">${this._escape(this._formatActivityTimestamp(item.ts))}</div>
          </div>
        `).join("")
      : '<div class="activity-empty">No watering or active errors yet</div>';
    return `
      <div class="activity-panel">
        <div class="activity-list">${rows}</div>
      </div>
    `;
  }

  _wateringMarkersTemplate(events, points, width, height, padding, leftPadding = padding, windowStart = undefined, windowEnd = undefined) {
    if (!events.length || !points.length) {
      return "";
    }
    const minTime = Number.isFinite(windowStart) ? windowStart : points[0].t;
    const maxTime = Number.isFinite(windowEnd) ? windowEnd : points[points.length - 1].t;
    const timeRange = Math.max(1, maxTime - minTime);
    return events
      .filter((timestamp) => (
        timestamp >= minTime
        && timestamp <= maxTime
      ))
      .map((timestamp) => {
        const x = leftPadding + ((timestamp - minTime) / timeRange) * (width - leftPadding - padding);
        const y = height - padding - 18;
        return `
          <g class="watering-marker-group">
            <circle class="watering-marker-dot" cx="${x}" cy="${y + 22}" r="4"></circle>
            <path class="watering-marker" transform="translate(${x - 8} ${y}) scale(0.72)" d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0L12 2.69z"></path>
          </g>
        `;
      })
      .join("");
  }

  _emptyChannelTemplate({ entities }) {
    return `
      <div class="card summary">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:pot-mix-outline"></ha-icon></div>
          <div>
            <div class="title">${this._channelLabel()}</div>
            <div class="subtitle">No plant added</div>
          </div>
        </div>
        <div class="stats">
          <div class="stat">
            <div class="label">Moisture</div>
            <div class="value">${this._escape(this._entityDisplay(entities.moisture, "Unknown"))}</div>
          </div>
          <div class="stat">
            <div class="label">Watering</div>
            <div class="value">Not configured</div>
          </div>
        </div>
        <button type="button" class="wide-button" data-action="add-plant">Add plant</button>
      </div>
    `;
  }

  _plantSetupTemplate(data) {
    const options = this._modeOptions()
      .map((option) => `<option value="${this._escape(option)}" ${this._normalizeMode(option) === this._normalizeMode(data.mode) ? "selected" : ""}>${this._escape(this._modeDisplay(option))}</option>`)
      .join("");
    return `
      <div class="card detail">
        <div class="header">
          <div class="plant-icon"><ha-icon icon="mdi:pot-mix-outline"></ha-icon></div>
          <div>
            <div class="title">${this._channelLabel()}</div>
            <div class="subtitle">Add a plant before enabling watering</div>
          </div>
        </div>
        <div class="grid">
          <label class="field wide">
            <div class="label">Name</div>
            <input data-entity="${this._escape(data.entities.name)}" data-domain="text" value="${this._escape(this._plantName())}">
          </label>
          <label class="field wide">
            <div class="label">Initial watering mode</div>
            <select data-entity="${this._escape(data.entities.mode)}" data-domain="select">${options}</select>
          </label>
        </div>
        <button type="button" data-action="add-plant">Add plant</button>
      </div>
    `;
  }

  _problemBannerTemplate(problems) {
    if (!problems.length) {
      return "";
    }
    const danger = problems.some((item) => item.severity === "danger");
    const title = problems.map((item) => item.label).join(" / ");
    const text = danger
      ? "Watering is blocked until this is fixed."
      : "Check the probe before relying on automatic watering.";
    const dismissible = problems.find((item) => item.dismissible);
    return `
      <div class="problem-banner ${danger ? "danger" : ""}">
        <ha-icon icon="${danger ? "mdi:alert-circle" : "mdi:alert"}"></ha-icon>
        <div>
          <div class="problem-title">${this._escape(title)}</div>
          <div class="problem-text">${this._escape(text)}</div>
        </div>
        ${dismissible ? `
          <button
            type="button"
            class="icon-button problem-dismiss"
            data-action="dismiss-problem"
            data-entity="${this._escape(dismissible.dismissEntity)}"
            data-kind="${this._escape(dismissible.dismissKind)}"
            aria-label="Hide alert"
          >
            <ha-icon icon="mdi:close"></ha-icon>
          </button>
        ` : ""}
      </div>
    `;
  }

  _wateringDialogTemplate() {
    return `
      <div class="dialog-backdrop" data-action="close-dialog">
        <div class="dialog" role="dialog" aria-modal="true" aria-label="Manual watering">
          <div class="dialog-title">Manual watering</div>
          <label class="field wide">
            <div class="label">Amount, mL</div>
            <input type="number" min="30" max="150" step="10" data-action="dialog-seconds" value="${this._wateringSeconds}">
          </label>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="close-dialog">Cancel</button>
            <button type="button" data-action="confirm-water">Water</button>
          </div>
        </div>
      </div>
    `;
  }

  _reservoirGuideSteps() {
    return [
      {
        image: "/local/growcube/images/external-reservoir-01.png",
        title: "Remove the side plug",
        text: "Unplug the rubber plug on the back of GrowCube.",
      },
      {
        image: "/local/growcube/images/external-reservoir-02.png",
        title: "Insert the water tube",
        text: "Insert the tube into the water cube through the external hole.",
      },
      {
        image: "/local/growcube/images/external-reservoir-03.png",
        title: "Remove the internal filter",
        text: "Unplug the filter at the water inlet inside the GrowCube box.",
      },
      {
        image: "/local/growcube/images/external-reservoir-04.png",
        title: "Connect the tube inside",
        text: "Insert the water pipe into the internal water inlet.",
      },
      {
        image: "/local/growcube/images/external-reservoir-05.png",
        title: "Attach the external intake",
        text: "Connect the external intake adapter to the water tube.",
      },
      {
        image: "/local/growcube/images/external-reservoir-06.png",
        title: "Place the tube in water",
        text: "Put the tube end into the external reservoir and keep the inlet and outlet pipes full of water before automatic watering.",
      },
    ];
  }

  _reservoirGuideTemplate() {
    const steps = this._reservoirGuideSteps();
    const stepIndex = this._clamp(Number(this._reservoirGuideStep), 0, steps.length - 1);
    const step = steps[stepIndex];
    const isLast = stepIndex >= steps.length - 1;
    return `
      <div class="dialog-backdrop" data-action="close-reservoir-guide">
        <div class="dialog guide-dialog" role="dialog" aria-modal="true" aria-label="External reservoir setup">
          <div class="dialog-title">External reservoir setup</div>
          <div class="guide-body">
            <div class="guide-image-wrap">
              <img class="guide-image" src="${this._escape(step.image)}" alt="">
            </div>
            <div class="guide-copy">
              <div class="guide-step-title">${this._escape(step.title)}</div>
              <div class="guide-step-text">${this._escape(step.text)}</div>
            </div>
            <div class="guide-nav">
              <button type="button" class="secondary" data-action="reservoir-guide-prev" ${stepIndex <= 0 ? "disabled" : ""}>Back</button>
              <div class="guide-counter">${stepIndex + 1} / ${steps.length}</div>
              <button type="button" data-action="${isLast ? "continue-reservoir-guide" : "reservoir-guide-next"}">${isLast ? "Continue" : "Next"}</button>
            </div>
            <label class="guide-checkbox">
              <input type="checkbox" data-action="reservoir-guide-dont-show" ${this._reservoirGuideDontShow ? "checked" : ""}>
              <span>Don't show again</span>
            </label>
          </div>
        </div>
      </div>
    `;
  }

  _reservoirDialogTemplate() {
    return `
      <div class="dialog-backdrop" data-action="close-reservoir-dialog">
        <div class="dialog" role="dialog" aria-modal="true" aria-label="Custom reservoir">
          <div class="dialog-title">Custom reservoir</div>
          <label class="field wide">
            <div class="label">Capacity, mL</div>
            <input type="number" min="500" max="50000" step="50" data-action="reservoir-amount" value="${this._reservoirAmount}">
          </label>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="close-reservoir-dialog">Cancel</button>
            <button type="button" data-action="confirm-reservoir">Save</button>
          </div>
        </div>
      </div>
    `;
  }

  _plantWizardDialogTemplate() {
    const titles = this._plantWizardCreateCustomOnly
      ? ["Name and photo", "Growing conditions", "Review"]
      : this._plantWizardCustom
      ? ["Find plant", "Name and photo", "Growing conditions", "Choose channel", "Watering mode", "Watering settings", "Review"]
      : ["Find plant", "Plant details", "Choose channel", "Watering mode", "Watering settings", "Review"];
    const steps = Array.from({ length: this._plantWizardLastStep() + 1 }, (_item, index) => index);
    const channelStep = this._plantWizardChannelStepIndex();
    const modeStep = this._plantWizardModeStepIndex();
    const settingsStep = this._plantWizardSettingsStepIndex();
    const lastStep = this._plantWizardLastStep();
    return `
      <div class="dialog-backdrop" data-action="close-plant-wizard">
        <div class="dialog plant-wizard-dialog" role="dialog" aria-modal="true" aria-label="Add plant">
          <div class="dialog-title">${titles[this._plantWizardStep] || "Add plant"}</div>
          <div class="wizard-progress" style="grid-template-columns: repeat(${steps.length}, 1fr)">
            ${steps.map((step) => `<div class="wizard-dot ${step <= this._plantWizardStep ? "active" : ""}"></div>`).join("")}
          </div>
          <div class="wizard-step">
            ${this._plantWizardCreateCustomOnly && this._plantWizardStep === 0 ? this._plantWizardDetailsStep() : ""}
            ${this._plantWizardCreateCustomOnly && this._plantWizardStep === 1 ? this._plantWizardConditionsStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardStep === 0 ? this._plantWizardSearchStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardStep === 1 ? this._plantWizardDetailsStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardCustom && this._plantWizardStep === 2 ? this._plantWizardConditionsStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardStep === channelStep ? this._plantWizardChannelStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardStep === modeStep ? this._plantWizardWateringModeStep() : ""}
            ${!this._plantWizardCreateCustomOnly && this._plantWizardStep === settingsStep ? this._plantWizardWateringSettingsStep() : ""}
            ${this._plantWizardStep === lastStep ? this._plantWizardReviewStep() : ""}
          </div>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="${this._plantWizardStep === 0 ? "close-plant-wizard-button" : "plant-wizard-back"}">
              ${this._plantWizardStep === 0 ? "Cancel" : "Back"}
            </button>
            <button type="button" data-action="${this._plantWizardStep === lastStep ? "confirm-add-plant" : "plant-wizard-next"}" ${this._canAdvancePlantWizard() ? "" : "disabled"}>
              ${this._plantWizardStep === lastStep ? (this._plantWizardCreateCustomOnly ? "Save plant" : "Add plant") : "Next"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _modeWizardDialogTemplate() {
    const titles = ["Watering mode", "Watering settings"];
    return `
      <div class="dialog-backdrop" data-action="close-mode-wizard">
        <div class="dialog" role="dialog" aria-modal="true" aria-label="Change watering mode">
          <div class="dialog-title">${titles[this._modeWizardStep] || "Change watering mode"}</div>
          <div class="wizard-progress">
            ${[0, 1].map((step) => `<div class="wizard-dot ${step <= this._modeWizardStep ? "active" : ""}"></div>`).join("")}
          </div>
          <div class="wizard-step">
            ${this._modeWizardStep === 0 ? this._modeWizardModeStep() : ""}
            ${this._modeWizardStep === 1 ? this._modeWizardSettingsStep() : ""}
          </div>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="${this._modeWizardStep === 0 ? "close-mode-wizard-button" : "mode-wizard-back"}">
              ${this._modeWizardStep === 0 ? "Cancel" : "Back"}
            </button>
            <button type="button" data-action="${this._modeWizardStep === 1 ? "confirm-mode-wizard" : "mode-wizard-next"}" ${this._canAdvanceModeWizard() ? "" : "disabled"}>
              ${this._modeWizardStep === 1 ? "Save" : "Next"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _modeWizardModeStep() {
    return `
      <div class="mode-grid">
        <button type="button" class="mode-choice ${this._modeWizardMode === "Smart" ? "active" : ""}" data-action="mode-wizard-mode-choice" data-mode="Smart">
          <div class="mode-title">Smart watering</div>
          <div class="mode-text">Water when soil moisture drops below the selected range.</div>
        </button>
        <button type="button" class="mode-choice ${this._modeWizardMode === "Repeating" ? "active" : ""}" data-action="mode-wizard-mode-choice" data-mode="Repeating">
          <div class="mode-title">Timed watering</div>
          <div class="mode-text">Water a fixed amount on a repeating interval.</div>
        </button>
      </div>
    `;
  }

  _modeWizardSettingsStep() {
    return `
      ${this._modeWizardMode === "Repeating" ? `
        <div class="grid">
          <div class="field time-field">
            <div class="label">First watering</div>
            <input type="time" data-action="mode-wizard-start-time" value="${this._modeWizardStartTime().slice(0, 5)}">
          </div>
          <label class="field">
            <div class="label">Amount, mL</div>
            <input type="number" min="10" max="500" step="10" data-action="mode-wizard-amount" value="${this._modeWizardAmount}">
          </label>
          <label class="field">
            <div class="label">Interval, days</div>
            <input type="number" min="1" max="10" step="1" data-action="mode-wizard-interval-days" value="${this._modeWizardIntervalDays}">
          </label>
        </div>
      ` : ""}
      ${this._modeWizardMode === "Smart" ? `
        <div class="grid">
          <div class="field wide">
            <div class="section-title">Moisture range</div>
          </div>
          <label class="field">
            <div class="label">Minimum moisture, %</div>
            <input type="number" min="1" max="98" step="1" data-action="mode-wizard-smart-min" value="${this._modeWizardSmartMin}">
          </label>
          <label class="field">
            <div class="label">Maximum moisture, %</div>
            <input type="number" min="2" max="99" step="1" data-action="mode-wizard-smart-max" value="${this._modeWizardSmartMax}">
          </label>
        </div>
        <div class="grid">
          <div class="field wide">
            <div class="label">Daytime watering</div>
            <div class="mode-grid">
              <button type="button" class="mode-choice compact ${this._modeWizardDaytime ? "active" : ""}" data-action="mode-wizard-daytime-choice" data-daytime="on">On</button>
              <button type="button" class="mode-choice compact ${!this._modeWizardDaytime ? "active" : ""}" data-action="mode-wizard-daytime-choice" data-daytime="off">Off</button>
            </div>
          </div>
        </div>
      ` : ""}
    `;
  }

  _plantWizardSearchStep() {
    const selected = this._plantWizardSelected;
    const pageSize = 3;
    const pageCount = this._plantWizardResultPages();
    const page = this._clamp(this._plantWizardResultPage, 0, pageCount - 1);
    const visibleResults = this._plantWizardResults.slice(page * pageSize, page * pageSize + pageSize);
    return `
      <label class="field wide">
        <div class="label">Search plant catalog</div>
        <div class="catalog-search-row">
          <input data-action="plant-wizard-search" value="${this._escape(this._plantWizardSearch)}" placeholder="basil, monstera, tomato">
          <button type="button" data-action="search-plant-catalog">Search</button>
          <button type="button" class="secondary" data-action="open-custom-plants">Custom</button>
        </div>
      </label>
      ${
        this._plantWizardLoading
          ? '<div class="label">Loading plant catalog...</div>'
          : this._plantWizardError
            ? `<div class="label">${this._escape(this._plantWizardError)}</div>`
            : ""
      }
      ${visibleResults.length ? `<div class="plants-list">
        ${visibleResults.map((item, index) => this._catalogResultTemplate(item, page * pageSize + index)).join("")}
      </div>` : ""}
      ${this._plantWizardResults.length > pageSize ? `
        <div class="dialog-actions">
          <button type="button" class="secondary" data-action="plant-results-prev" ${page <= 0 ? "disabled" : ""}>Previous</button>
          <button type="button" class="secondary" data-action="plant-results-next" ${page >= pageCount - 1 ? "disabled" : ""}>Next ${page + 1}/${pageCount}</button>
        </div>
      ` : ""}
      ${selected ? `<div class="label">Selected: ${this._escape(selected.category || "Plant profile")} · moisture ${this._escape(selected.moisture_min)}-${this._escape(selected.moisture_max)}%</div>` : ""}
    `;
  }

  _customPlantsDialogTemplate() {
    const profiles = this._customPlantProfiles();
    const pageSize = 3;
    const pageCount = this._customPlantPages();
    const page = this._clamp(this._customPlantsPage, 0, pageCount - 1);
    const visibleProfiles = profiles.slice(page * pageSize, page * pageSize + pageSize);
    return `
      <div class="dialog-backdrop" data-action="close-custom-plants">
        <div class="dialog custom-plants-dialog" role="dialog" aria-modal="true" aria-label="Custom plants">
          <div class="dialog-title">Custom plants</div>
          ${
            profiles.length
              ? `<div class="plants-list custom-library-list">
                  ${visibleProfiles.map((item, index) => this._customPlantLibraryRowTemplate(item, page * pageSize + index)).join("")}
                </div>`
              : `<div class="empty-state">
                  <div class="title">No custom plants yet</div>
                  <div class="subtitle">Create a custom plant profile to reuse it when adding plants to channels.</div>
                </div>`
          }
          ${profiles.length > pageSize ? `
            <div class="custom-library-pager">
              <button type="button" class="secondary" data-action="custom-plants-prev" ${page <= 0 ? "disabled" : ""}>Previous</button>
              <div class="label">Page ${page + 1} / ${pageCount}</div>
              <button type="button" class="secondary" data-action="custom-plants-next" ${page >= pageCount - 1 ? "disabled" : ""}>Next</button>
            </div>
          ` : ""}
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="close-custom-plants-button">Close</button>
            <button type="button" data-action="plant-wizard-custom">Add custom plant</button>
          </div>
        </div>
      </div>
    `;
  }

  _customPlantLibraryRowTemplate(item, index) {
    const name = item.display_name || item.name || "Custom plant";
    const imageUrl = this._catalogImageUrl(item);
    return `
      <div class="plant-row" data-action="select-custom-plant" data-index="${index}" role="button" tabindex="0">
        <div class="plant-photo">
          ${imageUrl ? `<img src="${this._escape(imageUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
        </div>
        <div class="plant-meta">
          <div class="title">${this._escape(name)}</div>
          <div class="subtitle">${this._escape(item.category || "Custom plant")}</div>
        </div>
        <div class="plant-stats">
          <div class="label">Moisture</div>
          <div class="value">${this._escape(item.moisture_min ?? 20)}-${this._escape(item.moisture_max ?? 60)}%</div>
        </div>
      </div>
    `;
  }

  _plantWizardDetailsStep() {
    const item = this._plantWizardSelected || {};
    const name = item.display_name || item.name || this._plantWizardName || "Unknown plant";
    const imageUrl = this._plantImageUrl(this._plantWizardPhotoUrl || this._catalogImageUrl(item));
    if (!this._plantWizardCustom) {
      const about = item.description || "No catalog description is available for this plant.";
      return `
        <div class="profile-panel">
          <div class="profile-hero">
            <div class="profile-photo">
              ${imageUrl ? `<img src="${this._escape(imageUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
            </div>
            <div>
              <div class="title">${this._escape(name)}</div>
              <div class="subtitle">${this._escape(item.category || "Plant profile")}</div>
            </div>
          </div>
          <div>
            <div class="section-title">About</div>
            <div class="profile-about">${this._escape(about)}</div>
          </div>
          <div class="profile-stats">
            ${this._profileStatTemplate("Soil moisture", this._rangeText(item.moisture_min, item.moisture_max, "%"))}
            ${this._profileStatTemplate("Temperature", this._rangeText(item.temp_min, item.temp_max, "°C"))}
            ${this._profileStatTemplate("Air humidity", this._rangeText(item.air_humidity_min, item.air_humidity_max, "%"))}
          </div>
        </div>
      `;
    }
    return `
      <div class="profile-panel">
        <div class="profile-hero">
          <div class="profile-photo">
            ${imageUrl ? `<img src="${this._escape(imageUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
          </div>
          <div>
            <div class="title">${this._escape(name)}</div>
            <div class="subtitle">${this._escape(this._plantWizardCategory || item.category || "Plant profile")}</div>
          </div>
        </div>
        <div class="grid">
          <label class="field">
            <div class="label">Plant name</div>
            <input data-action="plant-wizard-name" value="${this._escape(this._plantWizardName || name)}" maxlength="64">
          </label>
          <label class="field">
            <div class="label">Category</div>
            <input data-action="plant-wizard-category" value="${this._escape(this._plantWizardCategory || item.category || "")}" maxlength="128">
          </label>
          <label class="field wide">
            <div class="label">Photo</div>
            <input type="file" accept="image/jpeg,image/png,image/webp" data-action="plant-wizard-photo-file">
            <div class="label">${this._plantWizardPhotoUploading ? "Uploading photo..." : this._plantWizardPhotoFileName ? `Selected: ${this._escape(this._plantWizardPhotoFileName)}` : "JPEG, PNG, or WebP up to 1 MB"}</div>
          </label>
          <label class="field wide">
            <div class="label">Description</div>
            <textarea data-action="plant-wizard-description" rows="5">${this._escape(this._plantWizardDescription || "")}</textarea>
          </label>
        </div>
      </div>
    `;
  }

  _plantWizardConditionsStep() {
    return `
      <div class="grid">
        ${this._numberRangeFieldsTemplate("Soil moisture, %", "plant-wizard-smart-min", "plant-wizard-smart-max", this._plantWizardSmartMin, this._plantWizardSmartMax, 1, 99)}
        ${this._numberRangeFieldsTemplate("Temperature, °C", "plant-wizard-temp-min", "plant-wizard-temp-max", this._plantWizardTempMin, this._plantWizardTempMax, -50, 100)}
        ${this._numberRangeFieldsTemplate("Air humidity, %", "plant-wizard-air-humidity-min", "plant-wizard-air-humidity-max", this._plantWizardAirHumidityMin, this._plantWizardAirHumidityMax, 0, 100)}
      </div>
    `;
  }

  _numberRangeFieldsTemplate(label, minAction, maxAction, minValue, maxValue, minLimit, maxLimit) {
    return `
      <div class="field wide">
        <div class="section-title">${this._escape(label)}</div>
      </div>
      <label class="field">
        <div class="label">Minimum</div>
        <input type="number" min="${minLimit}" max="${maxLimit - 1}" step="1" data-action="${minAction}" value="${this._escape(minValue)}">
      </label>
      <label class="field">
        <div class="label">Maximum</div>
        <input type="number" min="${minLimit + 1}" max="${maxLimit}" step="1" data-action="${maxAction}" value="${this._escape(maxValue)}">
      </label>
    `;
  }

  _plantWizardChannelStep() {
    const availableChannels = this._availablePlantWizardChannels();
    if (!availableChannels.length) {
      return '<div class="label">All channels already have plants.</div>';
    }
    return `
      <div class="channel-grid">
        ${availableChannels.map((channel) => `
          <button type="button" class="channel-choice ${channel === this._plantWizardChannel ? "active" : ""}" data-action="plant-wizard-channel-choice" data-channel="${channel}">
            ${this._channelName(channel)}
          </button>
        `).join("")}
      </div>
    `;
  }

  _plantWizardWateringModeStep() {
    return `
      <div class="mode-grid">
        <button type="button" class="mode-choice ${this._plantWizardMode === "Smart" ? "active" : ""}" data-action="plant-wizard-mode-choice" data-mode="Smart">
          <div class="mode-title">Smart watering</div>
          <div class="mode-text">Water when soil moisture drops below the selected range.</div>
        </button>
        <button type="button" class="mode-choice ${this._plantWizardMode === "Repeating" ? "active" : ""}" data-action="plant-wizard-mode-choice" data-mode="Repeating">
          <div class="mode-title">Timed watering</div>
          <div class="mode-text">Water a fixed amount on a repeating interval.</div>
        </button>
      </div>
    `;
  }

  _plantWizardWateringSettingsStep() {
    return `
      ${this._plantWizardMode === "Repeating" ? `
        <div class="grid">
          <div class="field time-field">
            <div class="label">First watering</div>
            <input type="time" data-action="plant-wizard-start-time" value="${this._plantWizardStartTime().slice(0, 5)}">
          </div>
          <label class="field">
            <div class="label">Amount, mL</div>
            <input type="number" min="10" max="500" step="10" data-action="plant-wizard-amount" value="${this._plantWizardAmount}">
          </label>
          <label class="field">
            <div class="label">Interval, days</div>
            <input type="number" min="1" max="10" step="1" data-action="plant-wizard-interval-days" value="${this._plantWizardIntervalDays}">
          </label>
        </div>
      ` : ""}
      ${this._plantWizardMode === "Smart" ? `
        <div class="grid">
          <label class="field">
            <div class="label">Minimum moisture, %</div>
            <input type="number" min="1" max="98" step="1" data-action="plant-wizard-smart-min" value="${this._plantWizardSmartMin}">
          </label>
          <label class="field">
            <div class="label">Maximum moisture, %</div>
            <input type="number" min="2" max="99" step="1" data-action="plant-wizard-smart-max" value="${this._plantWizardSmartMax}">
          </label>
          <div class="field wide">
            <div class="label">Daytime watering</div>
            <div class="mode-grid">
              <button type="button" class="mode-choice compact ${this._plantWizardDaytime ? "active" : ""}" data-action="plant-wizard-daytime-choice" data-daytime="on">On</button>
              <button type="button" class="mode-choice compact ${!this._plantWizardDaytime ? "active" : ""}" data-action="plant-wizard-daytime-choice" data-daytime="off">Off</button>
            </div>
          </div>
        </div>
      ` : ""}
    `;
  }

  _plantWizardReviewStep() {
    const photoUrl = this._plantImageUrl(this._plantWizardPhotoUrl);
    const subtitle = this._plantWizardCreateCustomOnly
      ? `${this._plantWizardCategory || "Custom plant"} · Custom profile`
      : `${this._channelName(this._plantWizardChannel)} · ${this._plantWizardCategory || "Plant profile"} · ${this._plantWizardModeLabel()}`;
    const detail = this._plantWizardCreateCustomOnly
      ? `${this._plantWizardSmartMin}-${this._plantWizardSmartMax}% moisture · ${this._plantWizardTempMin}-${this._plantWizardTempMax}°C · ${this._plantWizardAirHumidityMin}-${this._plantWizardAirHumidityMax}% humidity`
      : this._plantWizardMode === "Smart"
        ? `${this._plantWizardSmartMin}-${this._plantWizardSmartMax}% moisture · daytime ${this._plantWizardDaytime ? "on" : "off"}`
        : `${this._plantWizardAmount} mL every ${this._plantWizardIntervalDays} day${this._plantWizardIntervalDays === 1 ? "" : "s"} · starts ${this._plantWizardStartTime().slice(0, 5)}`;
    return `
      <div class="wizard-review">
        <div class="plant-photo">
          ${photoUrl ? `<img src="${this._escape(photoUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
        </div>
        <div>
          <div class="title">${this._escape(this._plantWizardName || "New plant")}</div>
          <div class="subtitle">${this._escape(subtitle)}</div>
          <div class="label">${this._escape(detail)}</div>
        </div>
      </div>
    `;
  }

  _catalogResultTemplate(item, index) {
    const name = item.display_name || item.name || "Unknown plant";
    const imageUrl = this._catalogImageUrl(item);
    return `
      <div class="plant-row" data-action="select-catalog-plant" data-index="${index}" role="button" tabindex="0">
        <div class="plant-photo">
          ${imageUrl ? `<img src="${this._escape(imageUrl)}" alt="" referrerpolicy="no-referrer">` : '<ha-icon icon="mdi:flower"></ha-icon>'}
        </div>
        <div class="plant-meta">
          <div class="title">${this._escape(name)}</div>
          <div class="subtitle">${this._escape(item.category || "Plant profile")}</div>
        </div>
        <div class="plant-stats">
          <div class="label">Moisture</div>
          <div class="value">${this._escape(item.moisture_min ?? 20)}-${this._escape(item.moisture_max ?? 60)}%</div>
        </div>
      </div>
    `;
  }

  _profileStatTemplate(label, value) {
    return `
      <div class="stat">
        <div class="label">${this._escape(label)}</div>
        <div class="value">${this._escape(value || "Unknown")}</div>
      </div>
    `;
  }

  _rangeText(min, max, unit) {
    const minNumber = Number(min);
    const maxNumber = Number(max);
    if (Number.isFinite(minNumber) && Number.isFinite(maxNumber) && (minNumber || maxNumber)) {
      return `${minNumber}-${maxNumber}${unit}`;
    }
    if (Number.isFinite(minNumber) && minNumber) {
      return `${minNumber}${unit}`;
    }
    if (Number.isFinite(maxNumber) && maxNumber) {
      return `${maxNumber}${unit}`;
    }
    return "Unknown";
  }

  _plantWizardModeLabel() {
    return this._plantWizardMode === "Repeating" ? "Timed watering" : "Smart watering";
  }

  async _saveScheduleAfterEdit(message, values = {}) {
    const hasDirectValues = Object.keys(values).length > 0;
    let apiError = null;
    if (hasDirectValues) {
      try {
        const result = await this._configureChannelApi(this._channelKey(), values, true);
        if (result) {
          this._showToast(message);
          return;
        }
      } catch (error) {
        apiError = error;
        console.warn("[GrowCube] add-on schedule update failed, trying entity fallback", {
          channel: this._channelKey(),
          error: error?.message || String(error),
        });
      }
    }
    const save = this._entities().save;
    if (save) {
      await this._press(save);
    } else if (hasDirectValues && apiError) {
      throw apiError;
    } else {
      await this._applyWateringApi();
    }
    this._showToast(message);
  }

  _openEditDialog(kind) {
    if (kind === "mode") {
      this._openModeWizard();
      return;
    }
    this._editDialog = { kind };
    this._render();
  }

  _closeEditDialog() {
    this._editDialog = null;
    this._render();
  }

  _editDialogTemplate() {
    const kind = this._editDialog?.kind;
    const entities = this._entities();
    const mode = this._normalizeMode(this._entityState(entities.mode, "Smart"));
    const [hour, minute] = this._splitTime(this._firstWateringValue(entities));
    const currentDays = Math.max(1, Math.round(this._number(entities.interval, 24) / 24));
    const modeOptions = this._modeOptions()
      .map((option) => `<option value="${this._escape(option)}" ${this._normalizeMode(option) === mode ? "selected" : ""}>${this._escape(this._modeDisplay(option))}</option>`)
      .join("");
    const configs = {
      plantName: {
        title: "Plant name",
        body: `
          <label class="field wide">
            <input data-edit-field="plant_name" value="${this._escape(this._plantName())}">
          </label>
        `,
      },
      mode: {
        title: "Watering settings",
        body: `
          <label class="field wide">
            <div class="label">Mode</div>
            <select data-edit-field="mode">${modeOptions}</select>
          </label>
          <div class="section-title">Smart watering</div>
          <label class="field">
            <div class="label">Minimum moisture, %</div>
            <input type="number" min="1" max="98" step="1" data-edit-field="smart_min" value="${this._number(entities.smart_min_moisture, 20)}">
          </label>
          <label class="field">
            <div class="label">Maximum moisture, %</div>
            <input type="number" min="2" max="99" step="1" data-edit-field="smart_max" value="${this._number(entities.smart_max_moisture, 60)}">
          </label>
          <label class="field wide">
            <div class="label">Daytime watering</div>
            <select data-edit-field="daytime">
              <option value="on" ${this._isOn(entities.smart_daytime_watering) ? "selected" : ""}>On</option>
              <option value="off" ${!this._isOn(entities.smart_daytime_watering) ? "selected" : ""}>Off</option>
            </select>
          </label>
          <div class="section-title">Timed watering</div>
          <div class="field time-field">
            <div class="label">First watering</div>
            <input type="time" data-edit-field="first_time" value="${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}">
          </div>
          <label class="field">
            <div class="label">Watering amount, mL</div>
            <input type="number" min="10" max="500" step="10" data-edit-field="schedule_amount" value="${this._number(entities.duration, 50)}">
          </label>
          <label class="field">
            <div class="label">Interval, days</div>
            <input type="number" min="1" max="10" step="1" data-edit-field="schedule_days" value="${currentDays}">
          </label>
        `,
      },
      manual: {
        title: "Manual watering",
        body: `
          <label class="field wide">
            <div class="label">Manual amount, mL</div>
            <input type="number" min="30" max="150" step="10" data-edit-field="manual_amount" value="${this._number(entities.manual_duration, 50)}">
          </label>
        `,
      },
      smartRange: {
        title: "Moisture range",
        body: `
          <label class="field">
            <div class="label">Minimum moisture, %</div>
            <input type="number" min="1" max="98" step="1" data-edit-field="smart_min" value="${this._number(entities.smart_min_moisture, 20)}">
          </label>
          <label class="field">
            <div class="label">Maximum moisture, %</div>
            <input type="number" min="2" max="99" step="1" data-edit-field="smart_max" value="${this._number(entities.smart_max_moisture, 60)}">
          </label>
        `,
      },
      daytime: {
        title: "Daytime watering",
        body: `
          <label class="field wide">
            <div class="label">Daytime watering</div>
            <select data-edit-field="daytime">
              <option value="on" ${this._isOn(entities.smart_daytime_watering) ? "selected" : ""}>On</option>
              <option value="off" ${!this._isOn(entities.smart_daytime_watering) ? "selected" : ""}>Off</option>
            </select>
          </label>
        `,
      },
      firstWatering: {
        title: "First watering",
        body: `
          <div class="field time-field">
            <input type="time" data-edit-field="first_time" value="${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}">
          </div>
        `,
      },
      schedule: {
        title: "Schedule",
        body: `
          <label class="field">
            <div class="label">Watering amount, mL</div>
            <input type="number" min="10" max="500" step="10" data-edit-field="schedule_amount" value="${this._number(entities.duration, 50)}">
          </label>
          <label class="field">
            <div class="label">Interval, days</div>
            <input type="number" min="1" max="10" step="1" data-edit-field="schedule_days" value="${currentDays}">
          </label>
        `,
      },
    };
    const config = configs[kind] || configs.mode;
    return `
      <div class="dialog-backdrop" data-action="close-edit-dialog">
        <div class="dialog edit-dialog" role="dialog" aria-modal="true" aria-label="${this._escape(config.title)}">
          <div class="dialog-title">${this._escape(config.title)}</div>
          <div class="grid">${config.body}</div>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="close-edit-button">Cancel</button>
            <button type="button" data-action="confirm-edit-dialog">Save</button>
          </div>
        </div>
      </div>
    `;
  }

  _editValue(field) {
    return this.shadowRoot.querySelector(`[data-edit-field="${field}"]`)?.value;
  }

  _currentTimedWateringValues(entities, overrides = {}) {
    const [hour, minute] = this._splitTime(this._firstWateringValue(entities));
    const firstTime = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`;
    return {
      mode: "Repeating",
      first_watering_time: firstTime,
      amount_ml: this._clamp(this._number(entities.duration, 50), 10, 500),
      interval_hours: this._clamp(this._number(entities.interval, 24), 1, 240),
      ...overrides,
    };
  }

  _deletePlantDialogTemplate() {
    const plantName = this._plantName();
    return `
      <div class="dialog-backdrop" data-action="close-delete-plant-dialog">
        <div class="dialog edit-dialog" role="dialog" aria-modal="true" aria-label="Delete plant">
          <div class="dialog-title">Delete plant</div>
          <div class="subtitle">Delete ${this._escape(plantName)}? This clears the plant setup, schedules, history, watering marks, and saved photo.</div>
          <div class="dialog-actions">
            <button type="button" class="secondary" data-action="close-delete-plant-button">Cancel</button>
            <button type="button" class="danger" data-action="confirm-delete-plant">Delete</button>
          </div>
        </div>
      </div>
    `;
  }

  _closeDeletePlantDialog() {
    this._deletePlantDialogOpen = false;
    this._render();
  }

  async _confirmDeletePlant() {
    try {
      const channel = this._channelKey();
      const deviceId = this._selectedDeviceId();
      await this._press(this._entities().reset);
      this._deletePlantDialogOpen = false;
      this._aboutDialogOpen = false;
      this._clearOptimisticChannelMetadata(channel, deviceId);
      this._dashboardDevicesLoadedAt = 0;
      this._loadDashboardDevicesIfNeeded(true);
      this._showToast("Plant deleted");
      const path = this._detailBackPath();
      window.history.pushState(null, "", path);
      window.dispatchEvent(new CustomEvent("location-changed"));
    } catch (error) {
      this._showError("Delete failed");
    }
  }

  async _confirmModeWizard() {
    try {
      // Apply the full watering configuration through the add-on API once.
      // Sending HA entity updates here as well causes duplicate reset/set commands.
      await this._saveScheduleAfterEdit("Watering settings updated", {
        mode: this._normalizeMode(this._modeWizardMode),
        first_watering_time: this._modeWizardStartTime(),
        amount_ml: this._modeWizardAmount,
        interval_hours: this._modeWizardIntervalDays * 24,
        smart_min_moisture: this._modeWizardSmartMin,
        smart_max_moisture: this._modeWizardSmartMax,
        smart_daytime_watering: this._modeWizardDaytime,
      });
      this._modeWizardOpen = false;
      this._render();
    } catch (error) {
      this._showError(error?.message || "Could not update watering mode");
    }
  }

  async _confirmEditDialog() {
    const kind = this._editDialog?.kind;
    const entities = this._entities();
    try {
      if (kind === "mode") {
        const modeValue = this._normalizeMode(this._editValue("mode"));
        const smartMin = this._clamp(Number(this._editValue("smart_min")), 1, 98);
        const smartMax = this._clamp(Number(this._editValue("smart_max")), smartMin + 1, 99);
        const smartDaytime = this._editValue("daytime") === "on";
        const [firstHour, firstMinute] = this._splitTime(this._editValue("first_time"));
        const firstTime = `${String(firstHour).padStart(2, "0")}:${String(firstMinute).padStart(2, "0")}:00`;
        const amount = this._clamp(Number(this._editValue("schedule_amount")), 10, 500);
        const intervalHours = this._clamp(Number(this._editValue("schedule_days")), 1, 10) * 24;
        // Apply the full watering configuration through the add-on API once.
        // Sending HA entity updates here as well causes duplicate reset/set commands.
        await this._saveScheduleAfterEdit("Watering settings updated", {
          mode: modeValue,
          smart_min_moisture: smartMin,
          smart_max_moisture: smartMax,
          smart_daytime_watering: smartDaytime,
          first_watering_time: firstTime,
          amount_ml: amount,
          interval_hours: intervalHours,
        });
      } else if (kind === "plantName") {
        if (!entities.name) {
          throw new Error("Plant name entity is unavailable");
        }
        const name = String(this._editValue("plant_name") || "").trim();
        if (!name) {
          throw new Error("Plant name cannot be empty");
        }
        await this._setText(entities.name, name);
        this._showToast("Plant name updated");
      } else if (kind === "manual") {
        if (!entities.manual_duration) {
          throw new Error("Manual amount entity is unavailable");
        }
        await this._setNumber(entities.manual_duration, this._clamp(Number(this._editValue("manual_amount")), 30, 150));
        this._showToast("Manual amount updated");
      } else if (kind === "smartRange") {
        const min = this._clamp(Number(this._editValue("smart_min")), 1, 98);
        const max = this._clamp(Number(this._editValue("smart_max")), min + 1, 99);
        // Apply both limits together through the add-on API once.
        await this._saveScheduleAfterEdit("Moisture range updated", {
          smart_min_moisture: min,
          smart_max_moisture: max,
        });
      } else if (kind === "daytime") {
        const daytime = this._editValue("daytime") === "on";
        // Apply through the add-on API once to avoid a duplicate firmware reset/set.
        await this._saveScheduleAfterEdit("Daytime watering updated", {
          smart_daytime_watering: daytime,
        });
      } else if (kind === "firstWatering") {
        const [hour, minute] = this._splitTime(this._editValue("first_time"));
        const firstTime = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`;
        if (entities.first_watering_time) {
          await this._setTime(entities.first_watering_time, firstTime);
        }
        await this._saveScheduleAfterEdit("First watering updated", {
          ...this._currentTimedWateringValues(entities),
          first_watering_time: firstTime,
        });
      } else if (kind === "schedule") {
        const amount = this._clamp(Number(this._editValue("schedule_amount")), 10, 500);
        const intervalHours = this._clamp(Number(this._editValue("schedule_days")), 1, 10) * 24;
        if (entities.duration) {
          await this._setNumber(entities.duration, amount);
        }
        if (entities.interval) {
          await this._setNumber(entities.interval, intervalHours);
        }
        await this._saveScheduleAfterEdit("Schedule updated", {
          ...this._currentTimedWateringValues(entities),
          amount_ml: amount,
          interval_hours: intervalHours,
        });
      }
      this._editDialog = null;
      this._render();
    } catch (error) {
      this._showError(error?.message || "Could not update setting");
    }
  }

  _chartPointerPosition(svg, event) {
    const rect = svg.getBoundingClientRect();
    const viewBox = svg.viewBox?.baseVal;
    if (!rect.width || !rect.height || !viewBox) {
      return null;
    }
    return {
      x: viewBox.x + ((event.clientX - rect.left) / rect.width) * viewBox.width,
      y: viewBox.y + ((event.clientY - rect.top) / rect.height) * viewBox.height,
    };
  }

  _nearestChartPoint(points, x) {
    if (!points.length) {
      return null;
    }
    if (x < points[0].x || x > points[points.length - 1].x) {
      return null;
    }
    return points.reduce((nearest, point) => (
      Math.abs(point.x - x) < Math.abs(nearest.x - x) ? point : nearest
    ), points[0]);
  }

  _chartPoints(svg) {
    if (svg._growcubeChartPoints) {
      return svg._growcubeChartPoints;
    }
    try {
      svg._growcubeChartPoints = JSON.parse(svg.dataset.chartPoints || "[]");
    } catch (_error) {
      svg._growcubeChartPoints = [];
    }
    return svg._growcubeChartPoints;
  }

  _updateChartHover(container, event) {
    const svg = container.matches?.("[data-chart-svg]") ? container : container.querySelector("[data-chart-svg]");
    if (!svg) {
      return;
    }
    const points = this._chartPoints(svg);
    const position = this._chartPointerPosition(svg, event);
    if (!points.length || !position) {
      return;
    }
    const nearest = this._nearestChartPoint(points, position.x);
    if (!nearest) {
      return;
    }
    const viewBox = svg.viewBox.baseVal;
    const hover = svg.querySelector("[data-chart-hover]");
    const guide = svg.querySelector("[data-chart-hover-guide]");
    const dot = svg.querySelector("[data-chart-hover-dot]");
    const tooltip = svg.querySelector("[data-chart-tooltip]");
    const value = svg.querySelector("[data-chart-tooltip-value]");
    const time = svg.querySelector("[data-chart-tooltip-time]");
    if (!hover || !guide || !dot || !tooltip || !value || !time) {
      return;
    }
    const tooltipWidth = 124;
    const tooltipHeight = 48;
    const tooltipGap = 12;
    const tooltipX = nearest.x + tooltipWidth + tooltipGap > viewBox.width
      ? Math.max(0, nearest.x - tooltipWidth - tooltipGap)
      : Math.min(viewBox.width - tooltipWidth, nearest.x + tooltipGap);
    const tooltipY = nearest.y - tooltipHeight - tooltipGap < 0
      ? Math.min(viewBox.height - tooltipHeight, nearest.y + tooltipGap)
      : nearest.y - tooltipHeight - tooltipGap;
    guide.setAttribute("x1", nearest.x);
    guide.setAttribute("x2", nearest.x);
    dot.setAttribute("cx", nearest.x);
    dot.setAttribute("cy", nearest.y);
    tooltip.setAttribute("transform", `translate(${tooltipX} ${tooltipY})`);
    value.textContent = `${Math.round(nearest.v)}%`;
    time.textContent = this._formatChartHoverDate(nearest.t);
    hover.removeAttribute("hidden");
  }

  _hideChartHover(container) {
    const svg = container.matches?.("[data-chart-svg]") ? container : container.querySelector("[data-chart-svg]");
    if (!svg) {
      return;
    }
    const hover = svg.querySelector("[data-chart-hover]");
    if (hover) {
      hover.setAttribute("hidden", "");
    }
  }

  _bindEvents() {
    this.shadowRoot.querySelectorAll("[data-action]").forEach((element) => {
      element.addEventListener("click", (event) => {
        const action = element.dataset.action;
        if (action === "navigate" && event.target.closest("button")) {
          return;
        }
        if (action === "more-info") {
          event.stopPropagation();
          this._deviceMenuOpen = false;
          this._showMoreInfo(element.dataset.entity);
        } else if (action === "navigate") {
          this._deviceMenuOpen = false;
          this._navigate();
        } else if (action === "navigate-channel") {
          this._deviceMenuOpen = false;
          this._navigateToChannel(element.dataset.channel, element.dataset.deviceId || this._selectedDeviceId());
        } else if (action === "toggle-device-menu") {
          event.stopPropagation();
          this._deviceMenuOpen = !this._deviceMenuOpen;
          this._render();
        } else if (action === "select-device") {
          event.stopPropagation();
          this._setSelectedDevice(element.dataset.deviceId);
        } else if (action === "open-add-plant") {
          event.stopPropagation();
          this._deviceMenuOpen = false;
          this._openPlantWizard();
        } else if (action === "search-plant-catalog") {
          event.stopPropagation();
          this._searchPlantCatalog();
        } else if (action === "open-custom-plants") {
          event.stopPropagation();
          this._openCustomPlantsDialog();
        } else if (action === "plant-wizard-custom") {
          event.stopPropagation();
          this._startCustomPlantWizard();
        } else if (action === "select-custom-plant") {
          event.stopPropagation();
          this._selectCustomPlantProfile(element.dataset.index);
        } else if (action === "custom-plants-prev") {
          event.stopPropagation();
          this._changeCustomPlantPage(-1);
        } else if (action === "custom-plants-next") {
          event.stopPropagation();
          this._changeCustomPlantPage(1);
        } else if (action === "select-catalog-plant") {
          event.stopPropagation();
          this._selectPlantCatalogItem(element.dataset.index);
        } else if (action === "plant-results-prev") {
          event.stopPropagation();
          this._changePlantWizardResultPage(-1);
        } else if (action === "plant-results-next") {
          event.stopPropagation();
          this._changePlantWizardResultPage(1);
        } else if (action === "plant-wizard-next") {
          event.stopPropagation();
          if (this._canAdvancePlantWizard()) {
            this._setPlantWizardStep(this._plantWizardStep + 1);
          }
        } else if (action === "plant-wizard-back") {
          event.stopPropagation();
          this._setPlantWizardStep(this._plantWizardStep - 1);
        } else if (action === "plant-wizard-channel-choice") {
          event.stopPropagation();
          this._plantWizardChannel = element.dataset.channel || this._plantWizardChannel;
          this._render();
        } else if (action === "plant-wizard-mode-choice") {
          event.stopPropagation();
          this._plantWizardMode = element.dataset.mode || this._plantWizardMode;
          this._render();
        } else if (action === "plant-wizard-daytime-choice") {
          event.stopPropagation();
          this._plantWizardDaytime = element.dataset.daytime === "on";
          this._render();
        } else if (action === "mode-wizard-next") {
          event.stopPropagation();
          if (this._canAdvanceModeWizard()) {
            this._setModeWizardStep(this._modeWizardStep + 1);
          }
        } else if (action === "mode-wizard-back") {
          event.stopPropagation();
          this._setModeWizardStep(this._modeWizardStep - 1);
        } else if (action === "mode-wizard-mode-choice") {
          event.stopPropagation();
          this._modeWizardMode = element.dataset.mode || this._modeWizardMode;
          this._render();
        } else if (action === "mode-wizard-daytime-choice") {
          event.stopPropagation();
          this._modeWizardDaytime = element.dataset.daytime === "on";
          this._render();
        } else if (action === "disable-mode-wizard") {
          event.stopPropagation();
          this._disableModeWizard();
        } else if (action === "water") {
          event.stopPropagation();
          this._confirmWatering();
        } else if (action === "stop") {
          event.stopPropagation();
          this._press(this._entities().stop)
            .then(() => this._showToast("Watering stopped"))
            .catch(() => this._showError("Stop failed"));
        } else if (action === "mark-tank-full") {
          event.stopPropagation();
          this._press(element.dataset.entity || this._entities().mark_tank_full)
            .then(() => this._showToast("Tank marked full"))
            .catch((error) => this._showError(error?.message || "Could not update tank"));
        } else if (action === "refresh-activity") {
          event.stopPropagation();
          this._refreshAllHistory();
        } else if (action === "refresh-current-history") {
          event.stopPropagation();
          this._refreshCurrentHistory();
        } else if (action === "dismiss-problem") {
          event.stopPropagation();
          this._dismissProblem(element.dataset.entity, element.dataset.kind);
        } else if (action === "edit-plant-name") {
          event.stopPropagation();
          this._openEditDialog("plantName");
        } else if (action === "edit-mode") {
          event.stopPropagation();
          this._openEditDialog("mode");
        } else if (action === "edit-manual-amount") {
          event.stopPropagation();
          this._openEditDialog("manual");
        } else if (action === "edit-smart-range") {
          event.stopPropagation();
          this._openEditDialog("smartRange");
        } else if (action === "edit-daytime") {
          event.stopPropagation();
          this._openEditDialog("daytime");
        } else if (action === "edit-first-watering") {
          event.stopPropagation();
          this._openEditDialog("firstWatering");
        } else if (action === "edit-repeating-schedule") {
          event.stopPropagation();
          this._openEditDialog("schedule");
        } else if (action === "toggle-detail-menu") {
          event.stopPropagation();
          this._detailMenuOpen = !this._detailMenuOpen;
          this._render();
        } else if (action === "open-about") {
          event.stopPropagation();
          this._detailMenuOpen = false;
          this._aboutDialogOpen = true;
          this._render();
          this._ensureAboutProfileData(this._entities());
        } else if (action === "history-scale") {
          event.stopPropagation();
          this._setHistoryHours(Number(element.dataset.hours));
        } else if (action === "set-reservoir-capacity") {
          event.stopPropagation();
          const capacity = Number(element.dataset.capacity) || 1500;
          this._setNumber(element.dataset.entity || this._entities().tank_capacity, capacity)
            .then(() => this._showToast(`Reservoir set to ${capacity} mL`))
            .catch((error) => this._showError(error?.message || "Could not update capacity"));
        } else if (action === "custom-capacity") {
          event.stopPropagation();
          this._openReservoirGuide(element.dataset.entity, element.dataset.currentCapacity);
        } else if (action === "add-plant") {
          event.stopPropagation();
          this._openPlantWizard();
        } else if (action === "toggle-smart-daytime") {
          event.stopPropagation();
          const nextDaytime = !this._isOn(this._entities().smart_daytime_watering);
          const daytimeEntity = this._entities().smart_daytime_watering;
          const entityUpdate = daytimeEntity ? this._toggleSwitch(daytimeEntity) : Promise.resolve();
          entityUpdate.then(() => this._saveScheduleAfterEdit("Daytime watering updated", {
            smart_daytime_watering: nextDaytime,
          }))
            .catch((error) => this._showError(error?.message || "Could not update daytime watering"));
        } else if (action === "reset" || action === "delete-plant") {
          event.stopPropagation();
          this._detailMenuOpen = false;
          this._deletePlantDialogOpen = true;
          this._render();
        } else if (action === "close-about-dialog" && event.target === element) {
          this._aboutDialogOpen = false;
          this._render();
        } else if (action === "close-about-button") {
          this._aboutDialogOpen = false;
          this._render();
        } else if (action === "close-delete-plant-dialog" && event.target === element) {
          this._closeDeletePlantDialog();
        } else if (action === "close-delete-plant-button") {
          this._closeDeletePlantDialog();
        } else if (action === "confirm-delete-plant") {
          this._confirmDeletePlant();
        } else if (action === "close-edit-dialog" && event.target === element) {
          this._closeEditDialog();
        } else if (action === "close-edit-button") {
          this._closeEditDialog();
        } else if (action === "confirm-edit-dialog") {
          this._confirmEditDialog();
        } else if (action === "close-reservoir-dialog" && event.target === element) {
          this._closeReservoirDialog();
        } else if (action === "confirm-reservoir") {
          this._confirmReservoir();
        } else if (action === "close-reservoir-guide" && event.target === element) {
          this._closeReservoirGuide();
        } else if (action === "reservoir-guide-prev") {
          event.stopPropagation();
          this._setReservoirGuideStep(this._reservoirGuideStep - 1);
        } else if (action === "reservoir-guide-next") {
          event.stopPropagation();
          this._setReservoirGuideStep(this._reservoirGuideStep + 1);
        } else if (action === "continue-reservoir-guide") {
          event.stopPropagation();
          this._continueReservoirGuide();
        } else if (action === "close-plant-wizard" && event.target === element) {
          this._closePlantWizard();
        } else if (action === "close-plant-wizard-button") {
          this._closePlantWizard();
        } else if (action === "close-custom-plants" && event.target === element) {
          this._closeCustomPlantsDialog();
        } else if (action === "close-custom-plants-button") {
          this._closeCustomPlantsDialog();
        } else if (action === "confirm-add-plant") {
          this._confirmAddPlant();
        } else if (action === "close-mode-wizard" && event.target === element) {
          this._closeModeWizard();
        } else if (action === "close-mode-wizard-button") {
          this._closeModeWizard();
        } else if (action === "confirm-mode-wizard") {
          this._confirmModeWizard();
        }
      });
    });

    this.shadowRoot.querySelectorAll('[data-action="more-info"], [data-action^="edit-"], [data-action="toggle-smart-daytime"]').forEach((element) => {
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          element.click();
        }
      });
    });

    this.shadowRoot.querySelectorAll("[data-chart-visual]").forEach((element) => {
      element.addEventListener("pointermove", (event) => this._updateChartHover(element, event));
      element.addEventListener("pointerdown", (event) => this._updateChartHover(element, event));
      element.addEventListener("pointerleave", () => this._hideChartHover(element));
      element.addEventListener("pointercancel", () => this._hideChartHover(element));
      element.addEventListener("mousemove", (event) => this._updateChartHover(element, event));
      element.addEventListener("mouseleave", () => this._hideChartHover(element));
    });

    const reservoirAmount = this.shadowRoot.querySelector('[data-action="reservoir-amount"]');
    if (reservoirAmount) {
      reservoirAmount.addEventListener("input", (event) => {
        this._reservoirAmount = event.target.value;
      });
      reservoirAmount.addEventListener("click", (event) => event.stopPropagation());
    }

    const reservoirGuideDontShow = this.shadowRoot.querySelector('[data-action="reservoir-guide-dont-show"]');
    if (reservoirGuideDontShow) {
      reservoirGuideDontShow.addEventListener("change", (event) => {
        this._reservoirGuideDontShow = Boolean(event.target.checked);
      });
      reservoirGuideDontShow.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardName = this.shadowRoot.querySelector('[data-action="plant-wizard-name"]');
    if (wizardName) {
      wizardName.addEventListener("input", (event) => {
        this._plantWizardName = event.target.value;
      });
      wizardName.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardSearch = this.shadowRoot.querySelector('[data-action="plant-wizard-search"]');
    if (wizardSearch) {
      wizardSearch.addEventListener("input", (event) => {
        this._plantWizardSearch = event.target.value;
      });
      wizardSearch.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          this._searchPlantCatalog();
        }
      });
      wizardSearch.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardPhotoUrl = this.shadowRoot.querySelector('[data-action="plant-wizard-photo-url"]');
    if (wizardPhotoUrl) {
      wizardPhotoUrl.addEventListener("input", (event) => {
        this._plantWizardPhotoUrl = event.target.value;
      });
      wizardPhotoUrl.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardPhotoFile = this.shadowRoot.querySelector('[data-action="plant-wizard-photo-file"]');
    if (wizardPhotoFile) {
      wizardPhotoFile.addEventListener("change", (event) => {
        const file = event.target.files?.[0];
        this._uploadPlantWizardPhoto(file);
      });
      wizardPhotoFile.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardCategory = this.shadowRoot.querySelector('[data-action="plant-wizard-category"]');
    if (wizardCategory) {
      wizardCategory.addEventListener("input", (event) => {
        this._plantWizardCategory = event.target.value;
      });
      wizardCategory.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardDescription = this.shadowRoot.querySelector('[data-action="plant-wizard-description"]');
    if (wizardDescription) {
      wizardDescription.addEventListener("input", (event) => {
        this._plantWizardDescription = event.target.value;
      });
      wizardDescription.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardChannel = this.shadowRoot.querySelector('[data-action="plant-wizard-channel"]');
    if (wizardChannel) {
      wizardChannel.addEventListener("change", (event) => {
        this._plantWizardChannel = event.target.value;
      });
      wizardChannel.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardMode = this.shadowRoot.querySelector('[data-action="plant-wizard-mode"]');
    if (wizardMode) {
      wizardMode.addEventListener("change", (event) => {
        this._plantWizardMode = event.target.value;
        this._render();
      });
      wizardMode.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardAmount = this.shadowRoot.querySelector('[data-action="plant-wizard-amount"]');
    if (wizardAmount) {
      wizardAmount.addEventListener("input", (event) => {
        this._plantWizardAmount = this._clamp(Number(event.target.value), 10, 500);
      });
      wizardAmount.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardIntervalDays = this.shadowRoot.querySelector('[data-action="plant-wizard-interval-days"]');
    if (wizardIntervalDays) {
      wizardIntervalDays.addEventListener("input", (event) => {
        this._plantWizardIntervalDays = this._clamp(Number(event.target.value), 1, 10);
      });
      wizardIntervalDays.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardStartHour = this.shadowRoot.querySelector('[data-action="plant-wizard-start-hour"]');
    if (wizardStartHour) {
      wizardStartHour.addEventListener("input", (event) => {
        this._plantWizardStartHour = this._clamp(Number(event.target.value), 0, 23);
      });
      wizardStartHour.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardStartMinute = this.shadowRoot.querySelector('[data-action="plant-wizard-start-minute"]');
    if (wizardStartMinute) {
      wizardStartMinute.addEventListener("input", (event) => {
        this._plantWizardStartMinute = this._clamp(Number(event.target.value), 0, 59);
      });
      wizardStartMinute.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardStartTime = this.shadowRoot.querySelector('[data-action="plant-wizard-start-time"]');
    if (wizardStartTime) {
      wizardStartTime.addEventListener("input", (event) => {
        const [hour, minute] = this._splitTime(event.target.value);
        this._plantWizardStartHour = hour;
        this._plantWizardStartMinute = minute;
      });
      wizardStartTime.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardSmartMin = this.shadowRoot.querySelector('[data-action="plant-wizard-smart-min"]');
    if (wizardSmartMin) {
      wizardSmartMin.addEventListener("input", (event) => {
        this._plantWizardSmartMin = this._clamp(Number(event.target.value), 1, Math.max(1, this._plantWizardSmartMax - 1));
      });
      wizardSmartMin.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardSmartMax = this.shadowRoot.querySelector('[data-action="plant-wizard-smart-max"]');
    if (wizardSmartMax) {
      wizardSmartMax.addEventListener("input", (event) => {
        this._plantWizardSmartMax = this._clamp(Number(event.target.value), Math.min(99, this._plantWizardSmartMin + 1), 99);
      });
      wizardSmartMax.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardTempMin = this.shadowRoot.querySelector('[data-action="plant-wizard-temp-min"]');
    if (wizardTempMin) {
      wizardTempMin.addEventListener("input", (event) => {
        this._plantWizardTempMin = this._clamp(Number(event.target.value), -50, Math.max(-50, this._plantWizardTempMax - 1));
      });
      wizardTempMin.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardTempMax = this.shadowRoot.querySelector('[data-action="plant-wizard-temp-max"]');
    if (wizardTempMax) {
      wizardTempMax.addEventListener("input", (event) => {
        this._plantWizardTempMax = this._clamp(Number(event.target.value), Math.min(100, this._plantWizardTempMin + 1), 100);
      });
      wizardTempMax.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardAirHumidityMin = this.shadowRoot.querySelector('[data-action="plant-wizard-air-humidity-min"]');
    if (wizardAirHumidityMin) {
      wizardAirHumidityMin.addEventListener("input", (event) => {
        this._plantWizardAirHumidityMin = this._clamp(Number(event.target.value), 0, Math.max(0, this._plantWizardAirHumidityMax - 1));
      });
      wizardAirHumidityMin.addEventListener("click", (event) => event.stopPropagation());
    }

    const wizardAirHumidityMax = this.shadowRoot.querySelector('[data-action="plant-wizard-air-humidity-max"]');
    if (wizardAirHumidityMax) {
      wizardAirHumidityMax.addEventListener("input", (event) => {
        this._plantWizardAirHumidityMax = this._clamp(Number(event.target.value), Math.min(100, this._plantWizardAirHumidityMin + 1), 100);
      });
      wizardAirHumidityMax.addEventListener("click", (event) => event.stopPropagation());
    }

    const modeWizardAmount = this.shadowRoot.querySelector('[data-action="mode-wizard-amount"]');
    if (modeWizardAmount) {
      modeWizardAmount.addEventListener("input", (event) => {
        this._modeWizardAmount = this._clamp(Number(event.target.value), 10, 500);
      });
      modeWizardAmount.addEventListener("click", (event) => event.stopPropagation());
    }

    const modeWizardIntervalDays = this.shadowRoot.querySelector('[data-action="mode-wizard-interval-days"]');
    if (modeWizardIntervalDays) {
      modeWizardIntervalDays.addEventListener("input", (event) => {
        this._modeWizardIntervalDays = this._clamp(Number(event.target.value), 1, 10);
      });
      modeWizardIntervalDays.addEventListener("click", (event) => event.stopPropagation());
    }

    const modeWizardStartTime = this.shadowRoot.querySelector('[data-action="mode-wizard-start-time"]');
    if (modeWizardStartTime) {
      modeWizardStartTime.addEventListener("input", (event) => {
        const [hour, minute] = this._splitTime(event.target.value);
        this._modeWizardStartHour = hour;
        this._modeWizardStartMinute = minute;
      });
      modeWizardStartTime.addEventListener("click", (event) => event.stopPropagation());
    }

    const modeWizardSmartMin = this.shadowRoot.querySelector('[data-action="mode-wizard-smart-min"]');
    if (modeWizardSmartMin) {
      modeWizardSmartMin.addEventListener("input", (event) => {
        this._modeWizardSmartMin = this._clamp(Number(event.target.value), 1, Math.max(1, this._modeWizardSmartMax - 1));
      });
      modeWizardSmartMin.addEventListener("click", (event) => event.stopPropagation());
    }

    const modeWizardSmartMax = this.shadowRoot.querySelector('[data-action="mode-wizard-smart-max"]');
    if (modeWizardSmartMax) {
      modeWizardSmartMax.addEventListener("input", (event) => {
        this._modeWizardSmartMax = this._clamp(Number(event.target.value), Math.min(99, this._modeWizardSmartMin + 1), 99);
      });
      modeWizardSmartMax.addEventListener("click", (event) => event.stopPropagation());
    }

    const dashboardGrid = this.shadowRoot.querySelector(".dashboard-grid");
    if (dashboardGrid) {
      dashboardGrid.addEventListener("click", () => {
        if (this._deviceMenuOpen) {
          this._deviceMenuOpen = false;
          this._render();
        }
      });
    }

    this.shadowRoot.querySelectorAll("input[data-entity], select[data-entity]").forEach((element) => {
      element.addEventListener("change", async (event) => {
        const target = event.currentTarget;
        const entityId = target.dataset.entity;
        const domain = target.dataset.domain;
        if (target.dataset.timePart) {
          await this._setTimeParts(entityId);
          return;
        }
        if (domain === "number") {
          const min = Number(target.getAttribute("min"));
          const max = Number(target.getAttribute("max"));
          const value = this._clamp(
            Number(target.value),
            Number.isFinite(min) ? min : Number.MIN_SAFE_INTEGER,
            Number.isFinite(max) ? max : Number.MAX_SAFE_INTEGER,
          );
          target.value = String(value);
          await this._setNumber(entityId, value);
        } else if (domain === "select") {
          await this._setSelect(entityId, target.value);
        } else if (domain === "time") {
          await this._setTime(entityId, target.value);
        } else if (domain === "text") {
          await this._setText(entityId, target.value);
        }
      });
    });
  }

  async _setTimeParts(entityId) {
    const inputs = Array.from(this.shadowRoot.querySelectorAll("input[data-time-part]"));
    const hourInput = inputs.find((input) => input.dataset.timePart === "hour" && input.dataset.entity === entityId);
    const minuteInput = inputs.find((input) => input.dataset.timePart === "minute" && input.dataset.entity === entityId);
    const hour = this._clamp(Number(hourInput?.value), 0, 23);
    const minute = this._clamp(Number(minuteInput?.value), 0, 59);
    if (hourInput) {
      hourInput.value = String(hour);
    }
    if (minuteInput) {
      minuteInput.value = String(minute);
    }
    await this._setTime(entityId, `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`);
  }

  _splitTime(value) {
    const text = String(value || "");
    const match = text.match(/^(\d{1,2}):(\d{1,2})/);
    if (!match) {
      const date = new Date(text);
      if (!Number.isNaN(date.getTime())) {
        return [date.getHours(), date.getMinutes()];
      }
      return [8, 0];
    }
    return [
      this._clamp(Number(match[1]), 0, 23),
      this._clamp(Number(match[2]), 0, 59),
    ];
  }

  _plantWizardStartTime() {
    const hour = this._clamp(Number(this._plantWizardStartHour), 0, 23);
    const minute = this._clamp(Number(this._plantWizardStartMinute), 0, 59);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00`;
  }

  _clamp(value, min, max) {
    if (!Number.isFinite(value)) {
      return min;
    }
    return Math.min(max, Math.max(min, Math.round(value)));
  }

  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
}

if (!customElements.get("growcube-card")) {
  customElements.define("growcube-card", GrowcubeCard);
}

console.info(`GrowCube card ${GROWCUBE_CARD_VERSION}`);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "growcube-card",
  name: "GrowCube Card",
  description: "A compact GrowCube plant control card",
});
