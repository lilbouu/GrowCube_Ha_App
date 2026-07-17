"""GrowCube HAOS add-on backend bridge."""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from growcube_client import (
    Command,
    DelayedTimedWateringStateReport,
    DeviceVersionReport,
    ExtendedWateringRecordReport,
    GrowCubeClient,
    HistoryCompleteReport,
    LockStateReport,
    MoistureHistoryReport,
    MoistureReport,
    OutletBlockedReport,
    PumpReport,
    Report,
    SensorDisconnectedReport,
    TankForecastReport,
    TankStateReport,
    WaterStateReport,
    WateringExceptionReport,
    WateringLockedReport,
    WateringRecordReport,
)
from growcube_protocol import scheduled_watering_payload, watering_mode_payload
from mqtt_bridge import MqttBridge, MqttOptions

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("growcube-addon")

DATA_DIR = Path(os.environ.get("GROWCUBE_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "growcube_state.json"
OPTIONS_PATH = DATA_DIR / "options.json"
APP_DIR = Path(__file__).parent
CARD_SOURCE_PATH = APP_DIR / "www" / "growcube-card.js"
CARD_IMAGE_SOURCE_DIR = APP_DIR / "www" / "images"
FIRMWARE_DATA_IMAGE_PATH = DATA_DIR / "firmware" / "growcube-local.bin"
FIRMWARE_BUNDLED_IMAGE_PATH = APP_DIR / "firmware" / "growcube-local.bin"
FIRMWARE_DOWNLOAD_PATH = DATA_DIR / "firmware" / "GrowCube-Software.bin"
FIRMWARE_UPDATE_CHECK_URL = "https://www.growcube.cc/software/2.4G/"
FIRMWARE_LATEST_MESSAGE = "当前已是最新版本！"
PLANT_PHOTO_DIR = DATA_DIR / "plant_photos"
CARD_API_URL_PLACEHOLDER = "__GROWCUBE_ADDON_API_URL__"
DEFAULT_INGRESS_PORT = 8099
DEFAULT_INGRESS_ALLOWED_CIDRS = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
    "fe80::/10",
)
CLOUD_CATALOG_HOSTS = ("https://api.growcube.cc", "http://api.growcube.cc")
CLOUD_CATALOG_LIMIT = 40
CLOUD_CATALOG_TIMEOUT_SECONDS = 20
_PLANT_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_PLANT_ID_CACHE: dict[int, tuple[float, dict[str, Any] | None]] = {}
PLANT_SEARCH_CACHE_TTL_SECONDS = 15 * 60
_SUPERVISOR_INGRESS_URL_CACHE: str | None = None
CARD_TARGET_PATHS = (
    Path("/config/www/growcube/growcube-card.js"),
)
CHANNEL_NAMES = ("A", "B", "C", "D")
GROWCUBE_TANK_CAPACITY_ML = 1500
GROWCUBE_TANK_UNUSABLE_RESERVE_ML = 300
RECONNECT_DELAY_SECONDS = 10
CONNECTION_NOTIFICATION_GRACE_SECONDS = 20
WATERING_APPLY_DELAY_SECONDS = 0.75
DISCOVERY_PORT_TIMEOUT_SECONDS = 0.35
DISCOVERY_DEVICE_TIMEOUT_SECONDS = 8
DISCOVERY_CONCURRENCY = 64
DISCOVERY_MAX_HOSTS = 254
HISTORY_RETRY_CHECK_SECONDS = 15
HISTORY_LOADING_STALE_SECONDS = 45
TIMED_HISTORY_REFRESH_GRACE_SECONDS = 5
TIMED_HISTORY_REFRESH_RETRY_SECONDS = 15
WATERING_HISTORY_REFRESH_DELAY_SECONDS = 25
HISTORY_TRAILING_GAP_RETRY_SECONDS = 60 * 60
HISTORY_TRAILING_GAP_HOURS = 0
FIRMWARE_OTA_READY_DELAY_SECONDS = 20
FIRMWARE_UPLOAD_TIMEOUT_SECONDS = 120
FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS = 60
FIRMWARE_MAX_BYTES = 4 * 1024 * 1024
NETWORK_TIME_URLS = (
    # UTC time is read from the HTTP Date header, not from the response body.
    "https://api.growcube.cc/",
    "https://www.growcube.cc/",
    "https://www.baidu.com/",
    "https://www.qq.com/",
    "https://www.aliyun.com/",
    "https://www.tencent.com/",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://api.github.com/",
    "https://www.google.com/generate_204",
    "http://www.google.com/generate_204",
    "http://worldtimeapi.org/api/timezone/Etc/UTC",
)
NETWORK_TIME_TIMEOUT_SECONDS = 5


@dataclass(slots=True)
class ChannelConfig:
    configured: bool = False
    plant_id: int = 0
    plant_name: str = ""
    photo_url: str = ""
    type_category: str = ""
    type_description: str = ""
    temp_min: int = 0
    temp_max: int = 0
    air_humidity_min: int = 0
    air_humidity_max: int = 0
    mode: str = "Disabled"
    manual_duration_seconds: int = 50
    duration_seconds: int = 10
    amount_ml: int = 50
    interval_hours: int = 24
    first_watering_time: str = "08:00:00"
    smart_min_moisture: int = 20
    smart_max_moisture: int = 60
    smart_daytime_watering: bool = True


@dataclass(slots=True)
class ChannelState:
    moisture: int | None = None
    pump_open: bool = False
    last_watering: str | None = None
    next_watering: str | None = None
    plant_configured: bool = False
    outlet_locked: bool = False
    outlet_blocked: bool = False
    sensor_fault: bool = False
    sensor_disconnected: bool = False
    watering_issue: bool = False
    watering_locked: bool = False
    history_loading: bool = False
    history_complete: bool = False
    watering_events_complete: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    watering_events: list[dict[str, Any]] = field(default_factory=list)
    config: ChannelConfig = field(default_factory=ChannelConfig)


@dataclass(slots=True)
class DeviceState:
    id: str
    name: str
    host: str
    port: int = 8800
    connected: bool = False
    connecting: bool = False
    error: str = ""
    connection_problem_since: str | None = None
    device_id: str | None = None
    version: str | None = None
    temperature: int | None = None
    humidity: int | None = None
    water_warning: bool = False
    device_locked: bool = False
    tank_capacity_ml: int = 1500
    tank_remaining_ml: int = 1500
    tank_used_ml: int = 0
    tank_forecast: dict[str, Any] = field(default_factory=dict)
    firmware_update_status: str = "idle"
    firmware_update_error: str = ""
    firmware_update_started_at: str | None = None
    updated_at: str | None = None
    channels: list[ChannelState] = field(default_factory=lambda: [ChannelState() for _ in range(4)])


class DeviceRuntime:
    def __init__(self, manager: "GrowCubeManager", state: DeviceState) -> None:
        self.manager = manager
        self.state = state
        self.client: GrowCubeClient | None = None
        self.task: asyncio.Task | None = None
        self.reconnect_task: asyncio.Task | None = None
        self.reconnect_enabled = True
        self.connect_lock = asyncio.Lock()
        self.pending_manual_amounts: dict[int, int] = {}
        self.history_lock = asyncio.Lock()
        self.history_loading_since: list[datetime | None] = [None] * 4
        self.timed_history_refresh_requested_at: list[datetime | None] = [None] * 4
        self.history_gap_retry_at: list[datetime | None] = [None] * 4

    async def connect(self, *, schedule_retry: bool = True) -> None:
        async with self.connect_lock:
            if self.client is not None and self.client.connected:
                return
            async with self.manager.async_lock:
                self.state.connecting = True
                self.state.error = ""
                self.manager.touch_locked(self.state)

            client = GrowCubeClient(
                self.state.host,
                self.state.port,
                on_report=lambda report: self.manager.handle_report(self.state.id, report),
                on_connected=lambda: self.manager.handle_connected(self.state.id),
                on_disconnected=lambda: self.manager.handle_disconnected(self.state.id),
                time_provider=self.manager.current_time_for_device_sync,
            )
            self.client = client
            ok, error = await client.connect()
            if not ok:
                async with self.manager.async_lock:
                    self.state.connected = False
                    self.state.connecting = False
                    self.state.error = error
                    if self.state.connection_problem_since is None:
                        self.state.connection_problem_since = now_iso()
                    self.manager.touch_locked(self.state)
                if schedule_retry:
                    self.schedule_reconnect()

    def schedule_reconnect(self) -> None:
        if not self.reconnect_enabled:
            return
        if self.reconnect_task is not None and not self.reconnect_task.done():
            return
        self.reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self.reconnect_enabled:
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            if not self.reconnect_enabled:
                return
            if self.client is not None and self.client.connected:
                return
            LOGGER.info("Retrying GrowCube connection to %s:%s", self.state.host, self.state.port)
            await self.connect(schedule_retry=False)
            if self.client is not None and self.client.connected:
                return

    async def disconnect(self) -> None:
        self.reconnect_enabled = False
        if self.reconnect_task is not None:
            self.reconnect_task.cancel()
            self.reconnect_task = None
        if self.client is not None:
            await self.client.disconnect()
        async with self.manager.async_lock:
            self.state.connected = False
            self.state.connecting = False
            self.manager.touch_locked(self.state)


class GrowCubeManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.async_lock = asyncio.Lock()
        self.devices: dict[str, DeviceState] = {}
        self.runtimes: dict[str, DeviceRuntime] = {}
        self.pending_apply_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self.pending_history_refresh_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self.history_retry_task: asyncio.Task | None = None
        self.notification_signatures: dict[str, tuple[str, ...]] = {}
        self.homeassistant_time_zone: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mqtt_bridge: MqttBridge | None = None

    def load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        FIRMWARE_DATA_IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        stored = self._read_json(STATE_PATH, {})
        options = self._read_json(OPTIONS_PATH, {})
        self.homeassistant_time_zone = homeassistant_time_zone()
        if self.homeassistant_time_zone:
            LOGGER.info("Using Home Assistant time zone for GrowCube sync: %s", self.homeassistant_time_zone)
        else:
            LOGGER.info("Using add-on local time zone for GrowCube sync: %s", local_timezone())

        stored_devices: list[dict[str, Any]] = []
        if isinstance(stored.get("devices"), list):
            stored_devices.extend(item for item in stored["devices"] if isinstance(item, dict))
        option_devices = self._option_devices(options)

        with self.lock:
            self.devices = {}
            devices_by_host: dict[str, DeviceState] = {}
            for item in stored_devices:
                state = self._state_from_dict(item)
                if state.host:
                    devices_by_host[state.host.strip().lower()] = state
            for item in option_devices:
                host = str(item.get("host") or "").strip()
                if not host:
                    continue
                key = host.lower()
                name = str(item.get("name") or host).strip()
                port = max(1, min(65535, int(item.get("port") or 8800)))
                existing = devices_by_host.get(key)
                if existing is not None:
                    existing.name = name or existing.name or host
                    existing.port = port
                else:
                    devices_by_host[key] = self._state_from_dict(
                        {
                            "name": name or host,
                            "host": host,
                            "port": port,
                        }
                    )

            self.devices = {state.id: state for state in devices_by_host.values()}
            LOGGER.info("Loaded %s GrowCube device(s) from add-on data/options", len(self.devices))

    def start_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._run_loop, name="growcube-loop", daemon=True)
        thread.start()
        self.submit(self.history_retry_loop())
        for device_id in list(self.devices):
            self.submit(self.connect(device_id))

    def _run_loop(self) -> None:
        assert self.loop is not None
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        if self.loop is None:
            raise RuntimeError("manager loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def current_time_for_device_sync(self) -> datetime:
        zone = self.device_sync_timezone()
        network_time = await asyncio.to_thread(fetch_network_utc_time)
        if network_time is not None:
            value = network_time.astimezone(zone)
            source = "network"
        else:
            value = datetime.now(zone)
            source = "local-fallback"
        LOGGER.info(
            "GrowCube time-sync source=%s value=%s ha_time_zone=%s env_TZ=%s system_zone=%s",
            source,
            value.isoformat(timespec="seconds"),
            self.homeassistant_time_zone or "",
            os.environ.get("TZ", ""),
            datetime.now().astimezone().tzinfo,
        )
        return value

    def device_sync_timezone(self):
        if self.homeassistant_time_zone:
            try:
                return ZoneInfo(self.homeassistant_time_zone)
            except ZoneInfoNotFoundError:
                LOGGER.warning("Home Assistant time zone is not available in add-on: %s", self.homeassistant_time_zone)
        return local_timezone()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "devices": [self._state_to_dict(device) for device in self.devices.values()],
                "channels": CHANNEL_NAMES,
                "now": now_iso(),
            }

    def dashboard_payload(self) -> dict[str, Any]:
        with self.lock:
            devices = [self._dashboard_device(device) for device in self.devices.values()]
        return {"devices": sorted(devices, key=lambda item: str(item["name"]).lower())}

    def history_payload(self, device_id: str, channel_value: str, request_history: bool) -> dict[str, Any]:
        channel = validate_channel_key(channel_value)
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")

        with self.lock:
            channel_state = state.channels[channel]
            request_started = (
                request_history
                and not channel_state.history_loading
                and not (channel_state.history_complete and channel_state.watering_events_complete)
            )

        if request_started:
            self.submit(self.request_history(state.id, channel))

        with self.lock:
            channel_state = state.channels[channel]
            config = channel_state.config
            return {
                "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
                "channel": "abcd"[channel],
                "history_loading": channel_state.history_loading or request_started,
                "history_complete": channel_state.history_complete,
                "watering_events_complete": channel_state.watering_events_complete,
                "history_points": len(channel_state.history),
                "type_category": config.type_category,
                "type_description": config.type_description,
                "temp_min": config.temp_min,
                "temp_max": config.temp_max,
                "air_humidity_min": config.air_humidity_min,
                "air_humidity_max": config.air_humidity_max,
                "history": channel_state.history,
                "watering_events": channel_state.watering_events,
            }

    def apply_watering_payload(self, device_id: str, channel_value: str) -> dict[str, Any]:
        channel = validate_channel_key(channel_value)
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")
        future = self.submit(self.apply_watering_config(state.id, channel))
        future.result(timeout=5)
        return {
            "ok": True,
            "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
            "channel": "abcd"[channel],
            "mode": state.channels[channel].config.mode,
        }

    def reset_network_payload(self, device_id: str) -> dict[str, Any]:
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")
        future = self.submit(self.reset_network(state.id))
        future.result(timeout=10)
        return {
            "ok": True,
            "message": "network reset requested",
            "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
        }

    def firmware_update_payload(self, device_id: str) -> dict[str, Any]:
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")
        future = self.submit(self.update_firmware(state.id))
        result = future.result(timeout=FIRMWARE_UPLOAD_TIMEOUT_SECONDS + FIRMWARE_OTA_READY_DELAY_SECONDS + 20)
        return {
            "ok": True,
            "message": "firmware update uploaded",
            "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
            **result,
        }

    def firmware_check_payload(self, device_id: str) -> dict[str, Any]:
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")
        return {
            "ok": True,
            "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
            **check_growcube_firmware_update(state.version),
        }

    def discover_payload(self, network_value: str) -> dict[str, Any]:
        future = self.submit(self.discover_devices(network_value))
        devices = future.result(timeout=45)
        return {"devices": devices}

    def add_device_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        host = first_query_value(params, "host")
        name = first_query_value(params, "name") or host
        port = clamp_int(first_query_value(params, "port"), 1, 65535, 8800)
        future = self.submit(self.add_device(name, host, port))
        return {"device": future.result(timeout=15)}

    def remove_device_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        device_id = first_query_value(params, "device_id")
        state = self.find_device(device_id)
        if state is None:
            raise KeyError("device not found")
        future = self.submit(self.remove_device(state.id))
        future.result(timeout=10)
        return {"ok": True, "device_id": device_id}

    def rename_device_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        device_id = first_query_value(params, "device_id")
        name = first_query_value(params, "name").strip()[:64]
        if not name:
            raise ValueError("name is required")
        state = self.find_device(device_id)
        if state is None:
            raise KeyError("device not found")
        future = self.submit(self.rename_device(state.id, name))
        future.result(timeout=5)
        return {"ok": True, "device_id": device_id, "name": name}

    def entity_command_payload(self, params: dict[str, list[str]]) -> dict[str, Any]:
        entity_id_value = first_query_value(params, "entity_id")
        service = first_query_value(params, "service")
        value = first_query_value(params, "value")
        state, key = self.entity_from_id(entity_id_value)
        if state is None or not key:
            raise KeyError("entity not found")
        if service in {"turn_on", "turn_off", "toggle"}:
            if service == "toggle":
                current = self.entity_state_value(state, key)
                value = "OFF" if str(current).upper() == "ON" else "ON"
            else:
                value = "ON" if service == "turn_on" else "OFF"
        elif service == "press":
            value = "PRESS"
        future = self.submit(self.handle_entity_command(state, key, value))
        future.result(timeout=10)
        return {"ok": True, "entity_id": entity_id_value, "key": key}

    def configure_channel_payload(self, device_id: str, channel_value: str, params: dict[str, list[str]]) -> dict[str, Any]:
        channel = validate_channel_key(channel_value)
        state = self.find_device(device_id) if device_id else next(iter(self.devices.values()), None)
        if state is None:
            raise KeyError("device not found")
        apply_config = query_bool(params, "apply", False)
        future = self.submit(self.configure_channel(state.id, channel, params, apply_config))
        future.result(timeout=5)
        config = state.channels[channel].config
        return {
            "ok": True,
            "device_id": mqtt_device_unique_id(self._state_to_dict(state)),
            "channel": "abcd"[channel],
            "configured": state.channels[channel].plant_configured and config.configured,
            "plant_id": config.plant_id,
            "plant_name": config.plant_name,
            "photo_url": config.photo_url,
            "image_url": config.photo_url,
            "type_category": config.type_category,
            "type_description": config.type_description,
            "temp_min": config.temp_min,
            "temp_max": config.temp_max,
            "air_humidity_min": config.air_humidity_min,
            "air_humidity_max": config.air_humidity_max,
            "mode": config.mode,
            "first_watering_time": config.first_watering_time,
            "amount_ml": config.amount_ml,
            "interval_hours": config.interval_hours,
            "smart_min_moisture": config.smart_min_moisture,
            "smart_max_moisture": config.smart_max_moisture,
            "smart_daytime_watering": config.smart_daytime_watering,
        }

    def _dashboard_device(self, state: DeviceState) -> dict[str, Any]:
        device = self._state_to_dict(state)
        device_id = mqtt_device_unique_id(device)
        entities = dashboard_device_entities(device_id)
        channels = {
            channel: {
                **dashboard_channel_entities(device_id, channel),
                "plant_id": state.channels[index].config.plant_id,
                "plant_name": state.channels[index].config.plant_name,
                "photo_url": state.channels[index].config.photo_url,
                "photo_url_value": state.channels[index].config.photo_url,
                "image_url": state.channels[index].config.photo_url,
                "photo_url_entity": dashboard_channel_entities(device_id, channel)["photo_url"],
                "configured": state.channels[index].plant_configured and state.channels[index].config.configured,
            }
            for index, channel in enumerate("abcd")
        }
        return {
            "device_id": device_id,
            "host": state.host,
            "name": state.name or f"GrowCube {state.host}",
            "connected": state.connected,
            "connecting": state.connecting,
            "error": state.error,
            "version": state.version or "",
            "firmware_update_status": state.firmware_update_status,
            "firmware_update_error": state.firmware_update_error,
            "firmware_update_started_at": state.firmware_update_started_at,
            "addon_api_url": device.get("addon_api_url") or "",
            "entities": entities,
            "channels": channels,
            "states": dashboard_entity_states(device_id, device, entities, channels),
        }

    def entity_from_id(self, entity_id_value: str) -> tuple[DeviceState | None, str]:
        object_id = str(entity_id_value or "").split(".", 1)[-1]
        with self.lock:
            for state in self.devices.values():
                device_id = mqtt_device_unique_id(self._state_to_dict(state))
                prefix = f"growcube_{device_id}_"
                if object_id.startswith(prefix):
                    return state, object_id[len(prefix) :]
        return None, ""

    def entity_state_value(self, state: DeviceState, key: str) -> str:
        device = self._state_to_dict(state)
        device_id = mqtt_device_unique_id(device)
        entities = dashboard_device_entities(device_id)
        channels = {channel: dashboard_channel_entities(device_id, channel) for channel in "abcd"}
        for entity_id_value, entity_state in dashboard_entity_states(device_id, device, entities, channels).items():
            if entity_id_value.endswith(f"_{key}"):
                return str(entity_state.get("state") or "")
        return ""

    async def add_device(self, name: str, host: str, port: int = 8800) -> dict[str, Any]:
        host = host.strip()
        if not host:
            raise ValueError("host is required")
        existing_id = None
        async with self.async_lock:
            for device_id, existing in self.devices.items():
                if existing.host.strip().lower() == host.lower():
                    existing.name = name.strip() or existing.name or host
                    existing.port = max(1, min(65535, int(port or 8800)))
                    existing_id = device_id
                    self.touch_locked(existing)
                    break
        if existing_id is not None:
            await self.publish_device_to_mqtt(self.devices[existing_id])
            await self.connect(existing_id)
            return self._state_to_dict(self.devices[existing_id])
        state = DeviceState(
            id=str(uuid.uuid4()),
            name=name.strip() or host,
            host=host,
            port=max(1, min(65535, int(port or 8800))),
        )
        async with self.async_lock:
            self.devices[state.id] = state
            self.touch_locked(state)
        await self.publish_device_to_mqtt(state)
        await self.connect(state.id)
        return self._state_to_dict(state)

    async def remove_device(self, device_id: str) -> None:
        runtime = self.runtimes.pop(device_id, None)
        if runtime is not None:
            await runtime.disconnect()
        self.cancel_pending_apply(device_id)
        self.cancel_pending_history_refresh(device_id)
        async with self.async_lock:
            self.devices.pop(device_id, None)
            self.save_locked()

    async def rename_device(self, device_id: str, name: str) -> None:
        state = self.devices.get(device_id)
        if state is None:
            raise KeyError(device_id)
        async with self.async_lock:
            state.name = name.strip()[:64] or state.name
            self.touch_locked(state)

    async def connect(self, device_id: str) -> None:
        state = self.devices.get(device_id)
        if state is None:
            raise KeyError(device_id)
        runtime = self.runtimes.get(device_id)
        if runtime is None:
            runtime = DeviceRuntime(self, state)
            self.runtimes[device_id] = runtime
        await runtime.connect()

    async def water(self, device_id: str, channel: int, amount_ml: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        channel = validate_channel(channel)
        amount_ml = clamp_int(amount_ml, 30, 500, 50)
        duration = watering_duration_seconds(amount_ml)
        runtime.pending_manual_amounts[channel] = amount_ml
        await runtime.client.water(channel, duration)

    async def stop_watering(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        await runtime.client.close_pump(validate_channel(channel))

    async def reset_network(self, device_id: str) -> None:
        state = self.devices.get(device_id)
        if state is None:
            raise KeyError(device_id)
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        LOGGER.warning("Reset network requested device=%s", state.host)
        await runtime.client.reset_network()
        async with self.async_lock:
            state.connected = False
            state.connecting = False
            if state.connection_problem_since is None:
                state.connection_problem_since = now_iso()
            self.touch_locked(state)

    async def update_firmware(self, device_id: str) -> dict[str, Any]:
        state = self.devices.get(device_id)
        if state is None:
            raise KeyError(device_id)
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        async with self.async_lock:
            state.firmware_update_status = "updating"
            state.firmware_update_error = ""
            state.firmware_update_started_at = now_iso()
            self.touch_locked(state)
        LOGGER.warning("Firmware update requested device=%s current_version=%s", state.host, state.version or "unknown")
        try:
            firmware = await asyncio.to_thread(download_growcube_firmware_update, state.version)
            LOGGER.warning("Firmware image downloaded device=%s firmware=%s bytes=%s", state.host, firmware.name, firmware.stat().st_size)
            await runtime.client.start_firmware_update()
            await asyncio.sleep(FIRMWARE_OTA_READY_DELAY_SECONDS)
            upload_result = await asyncio.to_thread(upload_firmware_image, state.host, firmware)
        except Exception as err:
            async with self.async_lock:
                state.firmware_update_status = "error"
                state.firmware_update_error = str(err)
                state.connected = False
                state.connecting = True
                if state.connection_problem_since is None:
                    state.connection_problem_since = now_iso()
                self.touch_locked(state)
            if runtime.client is not None:
                await runtime.client.disconnect()
            runtime.schedule_reconnect()
            raise
        else:
            async with self.async_lock:
                state.firmware_update_status = "uploaded"
                state.firmware_update_error = ""
                state.connected = False
                state.connecting = True
                if state.connection_problem_since is None:
                    state.connection_problem_since = now_iso()
                self.touch_locked(state)
            if runtime.client is not None:
                await runtime.client.disconnect()
            runtime.schedule_reconnect()
            return upload_result

    async def request_history(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        channel = validate_channel(channel)
        async with runtime.history_lock:
            async with self.async_lock:
                state = self.devices[device_id]
                state.channels[channel].history_loading = True
                state.channels[channel].history_complete = False
                state.channels[channel].watering_events_complete = False
                runtime.history_loading_since[channel] = datetime.now(timezone.utc)
                self.touch_locked(state)
            await runtime.client.request_history(channel)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                async with self.async_lock:
                    channel_state = self.devices[device_id].channels[channel]
                    if not channel_state.history_loading:
                        return
                await asyncio.sleep(0.25)
            async with self.async_lock:
                self.touch_locked(self.devices[device_id])
            LOGGER.warning("History request timed out device=%s channel=%s", self.devices[device_id].host, CHANNEL_NAMES[channel])

    async def set_tank_level(self, device_id: str, capacity_ml: int, remaining_ml: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        capacity_ml = max(1, int(capacity_ml))
        remaining_ml = min(capacity_ml, max(0, int(remaining_ml)))
        LOGGER.info(
            "Set tank level device=%s capacity_ml=%s remaining_ml=%s",
            self.devices[device_id].host,
            capacity_ml,
            remaining_ml,
        )
        await runtime.client.send(Command(52, f"{capacity_ml}@{remaining_ml}"))
        await runtime.client.send(Command(54, ""))

    async def reset_plant(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        channel = validate_channel(channel)
        self.cancel_pending_apply(device_id, channel)
        LOGGER.info("Reset plant device=%s channel=%s", self.devices[device_id].host, CHANNEL_NAMES[channel])
        await runtime.client.send(Command(47, f"{channel}@0"))
        await runtime.client.send(Command(45, f"{channel}"))
        await runtime.client.send(Command(46, f"{channel}"))
        await runtime.client.send(Command(49, watering_mode_payload(channel, 0, 0, 0, 0)))
        channel_state = self.devices[device_id].channels[channel]
        channel_state.last_watering = None
        channel_state.next_watering = None
        channel_state.plant_configured = False
        channel_state.history_loading = False
        channel_state.history_complete = False
        channel_state.watering_events_complete = False
        channel_state.history.clear()
        channel_state.watering_events.clear()
        channel_state.config = ChannelConfig()
        self.touch_locked(self.devices[device_id])

    async def apply_watering_config(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        channel = validate_channel(channel)
        config = self.devices[device_id].channels[channel].config
        mode = config.mode
        if mode == "Repeating":
            duration = watering_duration_seconds(config.amount_ml or config.duration_seconds)
            interval = max(1, int(config.interval_hours or 24))
            start_time = next_watering_datetime(config.first_watering_time)
            LOGGER.info(
                "Apply timed watering device=%s channel=%s amount_ml=%s duration_s=%s interval_h=%s first=%s",
                self.devices[device_id].host,
                CHANNEL_NAMES[channel],
                config.amount_ml,
                duration,
                interval,
                start_time.isoformat(),
            )
            await self._reset_watering_mode(runtime, channel, config.plant_id)
            await runtime.client.send(
                Command(49, watering_mode_payload(channel, 1, duration, interval, config.plant_id))
            )
            await runtime.client.send(
                Command(51, scheduled_watering_payload(channel, duration, interval, start_time, config.plant_id))
            )
        elif mode == "Smart":
            smart_mode = 3 if config.smart_daytime_watering else 2
            LOGGER.info(
                "Apply smart watering device=%s channel=%s protocol_mode=%s min=%s max=%s daytime=%s",
                self.devices[device_id].host,
                CHANNEL_NAMES[channel],
                smart_mode,
                config.smart_min_moisture,
                config.smart_max_moisture,
                config.smart_daytime_watering,
            )
            await self._reset_watering_mode(runtime, channel, config.plant_id)
            await runtime.client.send(
                Command(
                    49,
                    watering_mode_payload(
                        channel,
                        smart_mode,
                        config.smart_min_moisture,
                        config.smart_max_moisture,
                        config.plant_id,
                    ),
                )
            )
        else:
            LOGGER.info("Disable watering device=%s channel=%s", self.devices[device_id].host, CHANNEL_NAMES[channel])
            await self._reset_watering_mode(runtime, channel, config.plant_id)

    async def discover_devices(self, network_value: str) -> list[dict[str, Any]]:
        networks = discovery_networks(network_value)
        semaphore = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        time_lock = asyncio.Lock()
        sync_time: datetime | None = None
        found: dict[str, dict[str, Any]] = {}

        async def discovery_time_provider() -> datetime:
            nonlocal sync_time
            if sync_time is None:
                async with time_lock:
                    if sync_time is None:
                        sync_time = await self.current_time_for_device_sync()
            return sync_time

        async def check_host(host: str) -> None:
            async with semaphore:
                try:
                    if not await tcp_port_open(host, 8800, DISCOVERY_PORT_TIMEOUT_SECONDS):
                        return
                    device = await probe_growcube_device(host, discovery_time_provider)
                    if device is None:
                        return
                    found[device["device_id"] or host] = device
                except Exception as err:
                    LOGGER.debug("GrowCube discovery probe ignored host=%s error=%s", host, err)

        await asyncio.gather(
            *[
                check_host(str(host))
                for network in networks
                for host in network.hosts()
            ]
        )
        return sorted(found.values(), key=lambda item: str(item.get("host", "")))

    def schedule_watering_apply(self, device_id: str, channel: int) -> None:
        channel = validate_channel(channel)
        state = self.devices.get(device_id)
        if state is None or not state.channels[channel].config.configured:
            return
        self.cancel_pending_apply(device_id, channel)
        task = asyncio.create_task(self._apply_watering_after_delay(device_id, channel))
        self.pending_apply_tasks[(device_id, channel)] = task

    def cancel_pending_apply(self, device_id: str, channel: int | None = None) -> None:
        keys = [
            key for key in self.pending_apply_tasks
            if key[0] == device_id and (channel is None or key[1] == channel)
        ]
        for key in keys:
            task = self.pending_apply_tasks.pop(key)
            task.cancel()

    async def _apply_watering_after_delay(self, device_id: str, channel: int) -> None:
        key = (device_id, channel)
        try:
            await asyncio.sleep(WATERING_APPLY_DELAY_SECONDS)
            state = self.devices.get(device_id)
            if state is None or not state.channels[channel].config.configured:
                return
            await self.apply_watering_config(device_id, channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            state = self.devices.get(device_id)
            LOGGER.warning(
                "Debounced watering apply failed device=%s channel=%s error=%s",
                state.host if state is not None else device_id,
                CHANNEL_NAMES[channel],
                err,
            )
        finally:
            if self.pending_apply_tasks.get(key) is asyncio.current_task():
                self.pending_apply_tasks.pop(key, None)

    def schedule_history_refresh_after_watering(self, device_id: str, channel: int) -> None:
        channel = validate_channel(channel)
        if device_id not in self.devices:
            return
        key = (device_id, channel)
        existing = self.pending_history_refresh_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._refresh_history_after_watering_delay(device_id, channel))
        self.pending_history_refresh_tasks[key] = task

    def cancel_pending_history_refresh(self, device_id: str, channel: int | None = None) -> None:
        keys = [
            key for key in self.pending_history_refresh_tasks
            if key[0] == device_id and (channel is None or key[1] == channel)
        ]
        for key in keys:
            task = self.pending_history_refresh_tasks.pop(key)
            task.cancel()

    async def _refresh_history_after_watering_delay(self, device_id: str, channel: int) -> None:
        key = (device_id, channel)
        try:
            await asyncio.sleep(WATERING_HISTORY_REFRESH_DELAY_SECONDS)
            state = self.devices.get(device_id)
            if state is None:
                return
            LOGGER.info("Requesting watering history after pump activity device=%s channel=%s", state.host, CHANNEL_NAMES[channel])
            await self.request_history(device_id, channel)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            state = self.devices.get(device_id)
            LOGGER.warning(
                "Delayed watering history refresh failed device=%s channel=%s error=%s",
                state.host if state is not None else device_id,
                CHANNEL_NAMES[channel],
                err,
            )
        finally:
            if self.pending_history_refresh_tasks.get(key) is asyncio.current_task():
                self.pending_history_refresh_tasks.pop(key, None)

    async def history_retry_loop(self) -> None:
        while True:
            await asyncio.sleep(HISTORY_RETRY_CHECK_SECONDS)
            try:
                await self.history_retry_tick()
            except Exception:
                LOGGER.exception("GrowCube history retry tick failed")

    async def history_retry_tick(self) -> None:
        for device_id, state in list(self.devices.items()):
            runtime = self.runtimes.get(device_id)
            if runtime is None or runtime.client is None or not runtime.client.connected:
                continue
            now = datetime.now(timezone.utc)
            if await self.request_stale_history_retry(device_id, runtime, state, now):
                continue
            if await self.request_due_timed_watering_history(device_id, runtime, state, now):
                continue
            await self.request_trailing_gap_history_retry(device_id, runtime, state, now)

    async def request_stale_history_retry(self, device_id: str, runtime: DeviceRuntime, state: DeviceState, now: datetime) -> bool:
        for channel, channel_state in enumerate(state.channels):
            if not channel_state.history_loading:
                runtime.history_loading_since[channel] = None
                continue
            loading_since = runtime.history_loading_since[channel]
            if loading_since is None:
                runtime.history_loading_since[channel] = now
                continue
            if (now - loading_since).total_seconds() < HISTORY_LOADING_STALE_SECONDS:
                continue
            LOGGER.warning("Retrying stuck GrowCube history load device=%s channel=%s", state.host, CHANNEL_NAMES[channel])
            await self.request_history(device_id, channel)
            return True
        return False

    async def request_due_timed_watering_history(self, device_id: str, runtime: DeviceRuntime, state: DeviceState, now: datetime) -> bool:
        for channel, channel_state in enumerate(state.channels):
            config = channel_state.config
            if (
                not config.configured
                or config.mode != "Repeating"
                or config.interval_hours <= 0
                or channel_state.last_watering is None
                or channel_state.history_loading
            ):
                continue
            last_watering = parse_iso_datetime(channel_state.last_watering)
            if last_watering is None:
                continue
            expected = last_watering + timedelta(hours=config.interval_hours)
            if expected > now - timedelta(seconds=TIMED_HISTORY_REFRESH_GRACE_SECONDS):
                continue
            last_request = runtime.timed_history_refresh_requested_at[channel]
            if last_request is not None and (now - last_request).total_seconds() < TIMED_HISTORY_REFRESH_RETRY_SECONDS:
                continue
            runtime.timed_history_refresh_requested_at[channel] = now
            LOGGER.info("Requesting timed watering history refresh device=%s channel=%s", state.host, CHANNEL_NAMES[channel])
            await self.request_history(device_id, channel)
            return True
        return False

    async def request_trailing_gap_history_retry(self, device_id: str, runtime: DeviceRuntime, state: DeviceState, now: datetime) -> bool:
        current_hour = history_hour_key(now)
        if current_hour is None:
            return False
        for channel, channel_state in enumerate(state.channels):
            if (
                not channel_state.config.configured
                or channel_state.history_loading
                or channel_state.moisture is None
                or not channel_state.history_complete
                or not channel_state.history
            ):
                continue
            last_request = runtime.history_gap_retry_at[channel]
            if last_request is not None and (now - last_request).total_seconds() < HISTORY_TRAILING_GAP_RETRY_SECONDS:
                continue
            last_hour = 0
            for point in channel_state.history:
                point_hour = history_hour_key(parse_iso_datetime(point.get("timestamp")))
                if point_hour is not None and point_hour > last_hour:
                    last_hour = point_hour
            gap_hours = current_hour - last_hour if last_hour > 0 else HISTORY_TRAILING_GAP_HOURS + 1
            if gap_hours <= HISTORY_TRAILING_GAP_HOURS:
                continue
            runtime.history_gap_retry_at[channel] = now
            LOGGER.info("Retrying history for trailing gap device=%s channel=%s gap=%s h", state.host, CHANNEL_NAMES[channel], gap_hours)
            await self.request_history(device_id, channel)
            return True
        return False

    async def sync_notifications(self, device: dict[str, Any]) -> None:
        device_id = str(device.get("id") or device.get("device_id") or device.get("host") or "growcube")
        signature = notification_signature(device)
        if self.notification_signatures.get(device_id) == signature:
            return
        self.notification_signatures[device_id] = signature
        await asyncio.to_thread(sync_homeassistant_notifications, device, signature)

    async def restore_channel_plant_profile(self, device_id: str, channel_index: int, plant_id: int, force: bool = False) -> None:
        if plant_id <= 0:
            return
        channel_name = CHANNEL_NAMES[channel_index] if 0 <= channel_index < len(CHANNEL_NAMES) else str(channel_index)
        try:
            plant = await asyncio.to_thread(fetch_plant_by_id, plant_id)
        except Exception as err:
            LOGGER.warning("Plant profile restore failed device=%s channel=%s plant_id=%s error=%s", device_id, channel_name, plant_id, err)
            return
        if not plant:
            LOGGER.warning("Plant profile restore found no catalog plant device=%s channel=%s plant_id=%s", device_id, channel_name, plant_id)
            return

        async with self.async_lock:
            state = self.devices.get(device_id)
            if state is None or not (0 <= channel_index < len(state.channels)):
                return
            channel = state.channels[channel_index]
            config = channel.config
            if config.plant_id != plant_id:
                return
            changed = apply_catalog_plant_profile(config, plant, force=force)
            if not changed:
                return
            channel.plant_configured = True
            config.configured = True
            LOGGER.info(
                "Restored plant profile device=%s channel=%s plant_id=%s fields=%s",
                state.host,
                channel_name,
                plant_id,
                ",".join(changed),
            )
            self.touch_locked(state)

    async def _reset_watering_mode(self, runtime: DeviceRuntime, channel: int, plant_id: int = 0) -> None:
        if runtime.client is None:
            return
        await runtime.client.send(Command(47, f"{channel}@0"))
        await runtime.client.send(Command(46, f"{channel}"))
        await runtime.client.send(Command(49, watering_mode_payload(channel, 0, 0, 0, plant_id)))

    async def configure_channel(
        self,
        device_id: str,
        channel: int,
        params: dict[str, list[str]],
        apply_config: bool,
    ) -> None:
        channel = validate_channel(channel)
        async with self.async_lock:
            state = self.devices[device_id]
            channel_state = state.channels[channel]
            config = channel_state.config
            changed: list[str] = []

            if query_has(params, "plant_name"):
                config.plant_name = first_query_value(params, "plant_name")[:64]
                changed.append(f"plant_name={config.plant_name!r}")
            if query_has(params, "plant_id"):
                config.plant_id = clamp_int(first_query_value(params, "plant_id"), 0, 2147483647, config.plant_id)
                changed.append(f"plant_id={config.plant_id}")
            if query_has(params, "photo_url"):
                config.photo_url = first_query_value(params, "photo_url")[:512]
                changed.append("photo_url updated")
            if query_has(params, "type_category"):
                config.type_category = first_query_value(params, "type_category")[:128]
                changed.append("type_category updated")
            if query_has(params, "type_description"):
                config.type_description = first_query_value(params, "type_description")[:10000]
                changed.append("type_description updated")
            if query_has(params, "temp_min"):
                config.temp_min = clamp_int(first_query_value(params, "temp_min"), -50, 100, config.temp_min)
                changed.append(f"temp_min={config.temp_min}")
            if query_has(params, "temp_max"):
                config.temp_max = clamp_int(first_query_value(params, "temp_max"), -50, 100, config.temp_max)
                changed.append(f"temp_max={config.temp_max}")
            if query_has(params, "air_humidity_min"):
                config.air_humidity_min = clamp_int(first_query_value(params, "air_humidity_min"), 0, 100, config.air_humidity_min)
                changed.append(f"air_humidity_min={config.air_humidity_min}")
            if query_has(params, "air_humidity_max"):
                config.air_humidity_max = clamp_int(first_query_value(params, "air_humidity_max"), 0, 100, config.air_humidity_max)
                changed.append(f"air_humidity_max={config.air_humidity_max}")
            if query_has(params, "mode"):
                mode = first_query_value(params, "mode")
                config.mode = mode if mode in {"Disabled", "Repeating", "Smart"} else "Disabled"
                changed.append(f"mode={config.mode}")
            if query_has(params, "amount_ml"):
                config.amount_ml = clamp_int(first_query_value(params, "amount_ml"), 10, 500, config.amount_ml)
                config.duration_seconds = watering_duration_seconds(config.amount_ml)
                changed.append(f"amount_ml={config.amount_ml}")
            if query_has(params, "duration_seconds"):
                config.amount_ml = clamp_int(first_query_value(params, "duration_seconds"), 10, 500, config.amount_ml)
                config.duration_seconds = watering_duration_seconds(config.amount_ml)
                changed.append(f"amount_ml={config.amount_ml}")
            if query_has(params, "interval_hours"):
                config.interval_hours = clamp_int(first_query_value(params, "interval_hours"), 1, 240, config.interval_hours)
                changed.append(f"interval_hours={config.interval_hours}")
            if query_has(params, "first_watering_time"):
                config.first_watering_time = normalize_time(
                    first_query_value(params, "first_watering_time"),
                    config.first_watering_time,
                )
                changed.append(f"first_watering_time={config.first_watering_time}")
            if query_has(params, "smart_min_moisture"):
                config.smart_min_moisture = clamp_int(
                    first_query_value(params, "smart_min_moisture"),
                    1,
                    max(1, config.smart_max_moisture - 1),
                    config.smart_min_moisture,
                )
                changed.append(f"smart_min={config.smart_min_moisture}")
            if query_has(params, "smart_max_moisture"):
                config.smart_max_moisture = clamp_int(
                    first_query_value(params, "smart_max_moisture"),
                    min(99, config.smart_min_moisture + 1),
                    99,
                    config.smart_max_moisture,
                )
                changed.append(f"smart_max={config.smart_max_moisture}")
            if query_has(params, "smart_daytime_watering"):
                config.smart_daytime_watering = query_bool(params, "smart_daytime_watering", config.smart_daytime_watering)
                changed.append(f"smart_daytime={config.smart_daytime_watering}")
            if query_bool(params, "configured", False) or config.plant_name or config.mode != "Disabled":
                channel_state.plant_configured = True
                config.configured = True

            LOGGER.info(
                "Direct channel config device=%s channel=%s apply=%s %s",
                state.host,
                CHANNEL_NAMES[channel],
                apply_config,
                ", ".join(changed) if changed else "no changes",
            )
            self.touch_locked(state)

        if apply_config:
            await self.apply_watering_config(device_id, channel)

    async def handle_mqtt_command(self, device_key: str, topic_key: str, payload: str) -> None:
        state = self.find_device(device_key)
        if state is None:
            LOGGER.warning("Ignoring MQTT command for unknown GrowCube device %s", device_key)
            return

        LOGGER.info("MQTT command device=%s topic=%s payload=%r", state.host, topic_key, payload)

        if topic_key != "command":
            await self.handle_entity_command(state, topic_key, payload)
            return

        action, _, raw_channel = payload.partition("_")
        if action not in {"water", "stop", "history"} or not raw_channel.isdigit():
            LOGGER.warning("Ignoring unsupported MQTT command payload %s", payload)
            return

        channel = validate_channel(int(raw_channel))
        if action == "water":
            await self.water(state.id, channel, state.channels[channel].config.manual_duration_seconds)
        elif action == "stop":
            await self.stop_watering(state.id, channel)
        elif action == "history":
            await self.request_history(state.id, channel)

    async def handle_entity_command(self, state: DeviceState, key: str, payload: str) -> None:
        channel = channel_from_key(key)
        changed = ""
        tank_update: tuple[int, int] | None = None
        reset_channel: int | None = None
        schedule_apply = False
        async with self.async_lock:
            if key == "tank_capacity":
                state.tank_capacity_ml = clamp_int(payload, 500, 50000, state.tank_capacity_ml)
                state.tank_remaining_ml = min(state.tank_remaining_ml, state.tank_capacity_ml)
                state.tank_used_ml = min(state.tank_used_ml, state.tank_capacity_ml)
                tank_update = (state.tank_capacity_ml, state.tank_remaining_ml)
                changed = f"tank_capacity={state.tank_capacity_ml}"
            elif key == "mark_tank_full":
                state.tank_remaining_ml = state.tank_capacity_ml
                state.tank_used_ml = 0
                tank_update = (state.tank_capacity_ml, state.tank_remaining_ml)
                changed = "tank marked full"
            elif key == "reset_network":
                changed = "network reset requested"
            elif key == "update_firmware":
                changed = "firmware update requested"
            elif channel is not None:
                config = state.channels[channel].config
                if key.startswith("water_plant_"):
                    pass
                elif key.startswith("stop_watering_"):
                    pass
                elif key.startswith("load_history_"):
                    pass
                elif key.startswith("save_schedule_"):
                    pass
                elif key.startswith("plant_name_"):
                    config.plant_name = payload.strip()[:64]
                    changed = f"plant_name={config.plant_name!r}"
                elif key.startswith("plant_photo_url_"):
                    config.photo_url = payload.strip()[:512]
                    changed = "plant_photo_url updated"
                elif key.startswith("watering_mode_"):
                    config.mode = payload if payload in {"Disabled", "Repeating", "Smart"} else "Disabled"
                    changed = f"mode={config.mode}"
                    schedule_apply = True
                elif key.startswith("manual_duration_seconds_"):
                    config.manual_duration_seconds = clamp_int(payload, 30, 500, config.manual_duration_seconds)
                    changed = f"manual_amount_ml={config.manual_duration_seconds}"
                elif key.startswith("duration_seconds_"):
                    config.amount_ml = clamp_int(payload, 10, 500, config.amount_ml)
                    config.duration_seconds = watering_duration_seconds(config.amount_ml)
                    changed = f"amount_ml={config.amount_ml}"
                    schedule_apply = True
                elif key.startswith("interval_hours_"):
                    config.interval_hours = clamp_int(payload, 1, 240, config.interval_hours)
                    changed = f"interval_hours={config.interval_hours}"
                    schedule_apply = True
                elif key.startswith("first_watering_time_"):
                    config.first_watering_time = normalize_time(payload, config.first_watering_time)
                    changed = f"first_watering_time={config.first_watering_time}"
                    schedule_apply = True
                elif key.startswith("smart_min_moisture_"):
                    config.smart_min_moisture = clamp_int(payload, 1, max(1, config.smart_max_moisture - 1), config.smart_min_moisture)
                    changed = f"smart_min={config.smart_min_moisture}"
                    schedule_apply = True
                elif key.startswith("smart_max_moisture_"):
                    config.smart_max_moisture = clamp_int(payload, min(99, config.smart_min_moisture + 1), 99, config.smart_max_moisture)
                    changed = f"smart_max={config.smart_max_moisture}"
                    schedule_apply = True
                elif key.startswith("smart_daytime_watering_"):
                    config.smart_daytime_watering = payload.upper() in {"ON", "TRUE", "1"}
                    changed = f"smart_daytime={config.smart_daytime_watering}"
                    schedule_apply = True
                elif key.startswith("add_plant_"):
                    state.channels[channel].plant_configured = True
                    config.configured = True
                    changed = "plant configured"
                elif key.startswith("reset_plant_"):
                    reset_channel = channel
                    changed = "plant reset requested"
                else:
                    LOGGER.warning("Ignoring unsupported MQTT entity command %s", key)
                    return
            else:
                LOGGER.warning("Ignoring unsupported MQTT entity command %s", key)
                return
            if changed:
                LOGGER.info(
                    "Updated config device=%s channel=%s %s",
                    state.host,
                    CHANNEL_NAMES[channel] if channel is not None else "-",
                    changed,
                )
            self.touch_locked(state)

        if channel is not None and key.startswith("water_plant_"):
            await self.water(state.id, channel, state.channels[channel].config.manual_duration_seconds)
        elif channel is not None and key.startswith("stop_watering_"):
            await self.stop_watering(state.id, channel)
        elif channel is not None and key.startswith("load_history_"):
            await self.request_history(state.id, channel)
        elif channel is not None and key.startswith("save_schedule_"):
            await self.apply_watering_config(state.id, channel)
        elif channel is not None and key.startswith("add_plant_"):
            mode = state.channels[channel].config.mode
            if mode in {"Smart", "Repeating"}:
                await self.apply_watering_config(state.id, channel)
        elif channel is not None and reset_channel is not None:
            await self.reset_plant(state.id, reset_channel)
            async with self.async_lock:
                state.channels[reset_channel] = ChannelState()
                self.touch_locked(state)
        elif key == "reset_network":
            await self.reset_network(state.id)
        elif key == "update_firmware":
            await self.update_firmware(state.id)
        elif tank_update is not None:
            await self.set_tank_level(state.id, tank_update[0], tank_update[1])
        elif channel is not None and schedule_apply:
            self.schedule_watering_apply(state.id, channel)

    def find_device(self, device_key: str) -> DeviceState | None:
        lookup = str(device_key or "").strip()
        safe_lookup = mqtt_safe_id(lookup)
        with self.lock:
            if lookup in {"growcube", "local_growcube"} and len(self.devices) == 1:
                return next(iter(self.devices.values()))
            for state in self.devices.values():
                aliases = {
                    state.id,
                    state.device_id or "",
                    state.host,
                    mqtt_safe_id(state.id),
                    mqtt_safe_id(state.device_id or ""),
                    mqtt_safe_id(state.host),
                    f"growcube_{mqtt_safe_id(state.host)}",
                    mqtt_device_unique_id(self._state_to_dict(state)),
                }
                if lookup in aliases or safe_lookup in aliases:
                    return state
        return None

    async def handle_connected(self, device_id: str) -> None:
        async with self.async_lock:
            state = self.devices.get(device_id)
            if state is None:
                return
            state.connected = True
            state.connecting = False
            state.error = ""
            state.connection_problem_since = None
            self.touch_locked(state)

    async def handle_disconnected(self, device_id: str) -> None:
        async with self.async_lock:
            state = self.devices.get(device_id)
            if state is None:
                return
            state.connected = False
            state.connecting = False
            if state.connection_problem_since is None:
                state.connection_problem_since = now_iso()
            self.touch_locked(state)
        runtime = self.runtimes.get(device_id)
        if runtime is not None:
            runtime.schedule_reconnect()

    async def handle_report(self, device_id: str, report: Report) -> None:
        async with self.async_lock:
            state = self.devices.get(device_id)
            if state is None:
                return

            if isinstance(report, WaterStateReport):
                state.water_warning = report.water_warning
            elif isinstance(report, DeviceVersionReport):
                state.device_id = report.device_id
                state.version = report.version
            elif isinstance(report, MoistureReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.moisture = report.moisture
                channel.sensor_fault = False
                channel.sensor_disconnected = False
                if report.humidity is not None:
                    state.humidity = report.humidity
                if report.temperature is not None:
                    state.temperature = report.temperature
            elif isinstance(report, PumpReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.pump_open = report.open
                if report.open:
                    self.schedule_history_refresh_after_watering(device_id, report.channel)
                    amount = None
                    runtime = self.runtimes.get(device_id)
                    if runtime is not None:
                        amount = runtime.pending_manual_amounts.pop(report.channel, None)
                    if amount is not None:
                        timestamp = now_iso()
                        channel.last_watering = timestamp
                        if all(item.get("timestamp") != timestamp for item in channel.watering_events):
                            channel.watering_events.append(
                                {
                                    "timestamp": timestamp,
                                    "amount_ml": amount,
                                    "source": "manual",
                                }
                            )
                            channel.watering_events = sorted(
                                channel.watering_events,
                                key=lambda item: item["timestamp"],
                            )[-128:]
            elif isinstance(report, WateringExceptionReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.watering_issue = True
            elif isinstance(report, OutletBlockedReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.outlet_blocked = True
            elif isinstance(report, SensorDisconnectedReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.moisture = None
                channel.sensor_disconnected = True
            elif isinstance(report, LockStateReport):
                state.device_locked = report.locked
                if report.locked and report.reason == 1:
                    state.water_warning = True
                if not report.locked:
                    for channel in state.channels:
                        channel.outlet_locked = False
                        channel.watering_issue = False
                        channel.watering_locked = False
            elif isinstance(report, WateringLockedReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                channel.watering_issue = True
                channel.watering_locked = True
            elif isinstance(report, MoistureHistoryReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                existing = {point["timestamp"]: point for point in channel.history}
                for hour, moisture in enumerate(report.values[:24]):
                    if moisture <= 0:
                        continue
                    try:
                        timestamp = datetime(
                            report.year,
                            report.month,
                            report.day,
                            hour,
                            tzinfo=local_timezone(),
                        ).isoformat()
                    except ValueError:
                        continue
                    existing[timestamp] = {
                        "timestamp": timestamp,
                        "moisture": max(0, min(100, moisture)),
                    }
                channel.history = sorted(existing.values(), key=lambda item: item["timestamp"])[-24 * 30 :]
            elif isinstance(report, WateringRecordReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                timestamp = report.timestamp.replace(tzinfo=local_timezone()).isoformat()
                channel.last_watering = timestamp
                if all(abs_iso_seconds(item.get("timestamp"), timestamp) > 30 for item in channel.watering_events):
                    channel.watering_events.append({"timestamp": timestamp, "amount_ml": None, "source": "last"})
                    channel.watering_events = sorted(
                        channel.watering_events,
                        key=lambda item: item["timestamp"],
                    )[-128:]
            elif isinstance(report, ExtendedWateringRecordReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                timestamp = report.timestamp.replace(tzinfo=local_timezone()).isoformat()
                channel.last_watering = timestamp
                for item in channel.watering_events:
                    if abs_iso_seconds(item.get("timestamp"), timestamp) <= 30:
                        item["source"] = report.source
                        if "amount_ml" not in item:
                            item["amount_ml"] = None
                        break
                else:
                    channel.watering_events.append(
                        {"timestamp": timestamp, "amount_ml": None, "source": report.source}
                    )
                channel.watering_events = sorted(
                    channel.watering_events,
                    key=lambda item: item["timestamp"],
                )[-128:]
            elif isinstance(report, HistoryCompleteReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                if report.history_kind == "moisture":
                    channel.history_complete = report.success
                else:
                    channel.watering_events_complete = report.success
                channel.history_loading = not (channel.history_complete and channel.watering_events_complete)
                if not channel.history_loading:
                    runtime = self.runtimes.get(device_id)
                    if runtime is not None:
                        runtime.history_loading_since[report.channel] = None
            elif isinstance(report, TankStateReport):
                state.tank_capacity_ml = clamp_int(report.capacity_ml, 500, 50000, state.tank_capacity_ml)
                state.tank_remaining_ml = clamp_int(report.remaining_ml, 0, state.tank_capacity_ml, state.tank_remaining_ml)
                state.tank_used_ml = clamp_int(report.used_ml, 0, state.tank_capacity_ml, state.tank_used_ml)
                LOGGER.info(
                    "Tank state device=%s remaining_ml=%s capacity_ml=%s used_ml=%s",
                    state.host,
                    state.tank_remaining_ml,
                    state.tank_capacity_ml,
                    state.tank_used_ml,
                )
            elif isinstance(report, TankForecastReport):
                state.tank_forecast = {
                    "known": True,
                    "flags": report.flags,
                    "valid_days": report.valid_days,
                    "confidence": report.confidence,
                    "smart_daily_x10": report.smart_daily_x10,
                    "manual_daily_x10": report.manual_daily_x10,
                    "unknown_daily_x10": report.unknown_daily_x10,
                    "smart_events": report.smart_events,
                    "manual_events": report.manual_events,
                    "unknown_events": report.unknown_events,
                    "today_smart_ml": report.today_smart_ml,
                    "today_manual_ml": report.today_manual_ml,
                    "today_unknown_ml": report.today_unknown_ml,
                }
                LOGGER.info(
                    "Tank forecast device=%s valid_days=%s confidence=%s smart_daily_ml=%.1f manual_daily_ml=%.1f",
                    state.host,
                    report.valid_days,
                    report.confidence,
                    report.smart_daily_x10 / 10,
                    report.manual_daily_x10 / 10,
                )
            elif isinstance(report, DelayedTimedWateringStateReport) and 0 <= report.channel < len(state.channels):
                channel = state.channels[report.channel]
                restore_plant_id = 0
                restore_force = False
                if report.has_plant_id:
                    previous_plant_id = channel.config.plant_id
                    channel.config.plant_id = report.plant_id
                    restore_plant_id = report.plant_id
                    restore_force = previous_plant_id != report.plant_id
                plant_removed = report.has_plant_id and report.plant_id == 0 and report.mode == 0
                if plant_removed:
                    channel.last_watering = None
                    channel.next_watering = None
                    channel.plant_configured = False
                    channel.history_loading = False
                    channel.history_complete = False
                    channel.watering_events_complete = False
                    channel.history.clear()
                    channel.watering_events.clear()
                    channel.config = ChannelConfig()
                    LOGGER.info(
                        "Plant removed from GrowCube device=%s channel=%s",
                        state.host,
                        CHANNEL_NAMES[report.channel],
                    )
                elif report.mode == 1 and report.duration_seconds > 0 and report.interval_hours > 0:
                    next_watering = datetime_from_growcube_local_epoch(report.next_start_epoch)
                    channel.next_watering = next_watering.isoformat() if next_watering is not None else None
                    channel.config.mode = "Repeating"
                    channel.plant_configured = True
                    channel.config.configured = True
                    channel.config.duration_seconds = report.duration_seconds
                    channel.config.amount_ml = stable_watering_amount_ml(report.duration_seconds, channel.config.amount_ml)
                    channel.config.interval_hours = report.interval_hours
                    if next_watering is not None:
                        channel.config.first_watering_time = next_watering.strftime("%H:%M:%S")
                    LOGGER.info(
                        "Timed watering state device=%s channel=%s enabled duration_s=%s interval_h=%s next=%s",
                        state.host,
                        CHANNEL_NAMES[report.channel],
                        report.duration_seconds,
                        report.interval_hours,
                        channel.next_watering,
                    )
                elif report.mode in (2, 3) and 0 < report.smart_min_moisture < report.smart_max_moisture <= 100:
                    channel.next_watering = None
                    channel.config.mode = "Smart"
                    channel.plant_configured = True
                    channel.config.configured = True
                    channel.config.smart_min_moisture = report.smart_min_moisture
                    channel.config.smart_max_moisture = report.smart_max_moisture
                    channel.config.smart_daytime_watering = report.mode == 3
                    LOGGER.info(
                        "Smart watering state device=%s channel=%s mode=%s min=%s max=%s",
                        state.host,
                        CHANNEL_NAMES[report.channel],
                        report.mode,
                        report.smart_min_moisture,
                        report.smart_max_moisture,
                    )
                else:
                    channel.next_watering = None
                    if report.plant_id > 0:
                        channel.plant_configured = True
                        channel.config.configured = True
                    if channel.config.mode in ("Repeating", "Smart"):
                        channel.config.mode = "Disabled"
                    LOGGER.info("Automatic watering state device=%s channel=%s disabled", state.host, CHANNEL_NAMES[report.channel])
                if restore_plant_id > 0:
                    self.schedule_loop_task(
                        self.restore_channel_plant_profile(device_id, report.channel, restore_plant_id, force=restore_force)
                    )

            self.touch_locked(state)

    def touch_locked(self, state: DeviceState) -> None:
        state.updated_at = now_iso()
        self.save_locked()
        bridge = self.mqtt_bridge
        if bridge is not None and self.loop is not None:
            snapshot = self._state_to_dict(state)
            self.schedule_loop_task(bridge.publish_device(snapshot, raise_on_error=False))
            self.schedule_loop_task(self.sync_notifications(snapshot))

    async def publish_device_to_mqtt(self, state: DeviceState) -> None:
        bridge = self.mqtt_bridge
        if bridge is None:
            return
        await bridge.publish_device(self._state_to_dict(state), raise_on_error=False)

    def schedule_loop_task(self, coro) -> None:
        if self.loop is None:
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self.loop:
            self.loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def save_locked(self) -> None:
        with self.lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps(
                    {"devices": [self._state_to_dict(device) for device in self.devices.values()]},
                    indent=2,
                ),
                encoding="utf-8",
            )

    @staticmethod
    def _read_json(path: Path, fallback: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _option_devices(options: Any) -> list[dict[str, Any]]:
        if not isinstance(options, dict) or not isinstance(options.get("devices"), list):
            return []

        devices: list[dict[str, Any]] = []
        for item in options["devices"]:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or "").strip()
            if not host:
                continue
            devices.append(
                {
                    "name": str(item.get("name") or host).strip(),
                    "host": host,
                    "port": max(1, min(65535, int(item.get("port") or 8800))),
                }
            )
        return devices

    @staticmethod
    def _state_from_dict(item: dict[str, Any]) -> DeviceState:
        channels = []
        for raw_channel in item.get("channels", [])[:4]:
            channel = ChannelState()
            if isinstance(raw_channel, dict):
                channel.moisture = optional_int(raw_channel.get("moisture"))
                channel.pump_open = bool(raw_channel.get("pump_open", False))
                channel.last_watering = raw_channel.get("last_watering")
                channel.next_watering = raw_channel.get("next_watering")
                channel.plant_configured = bool(raw_channel.get("plant_configured", False))
                channel.outlet_locked = bool(raw_channel.get("outlet_locked", False))
                channel.outlet_blocked = bool(raw_channel.get("outlet_blocked", False))
                channel.sensor_fault = bool(raw_channel.get("sensor_fault", False))
                channel.sensor_disconnected = bool(raw_channel.get("sensor_disconnected", False))
                channel.watering_issue = bool(raw_channel.get("watering_issue", False))
                channel.watering_locked = bool(raw_channel.get("watering_locked", False))
                channel.history = list(raw_channel.get("history") or [])[-24 * 30 :]
                channel.watering_events = list(raw_channel.get("watering_events") or [])[-128:]
                config = raw_channel.get("config")
                if isinstance(config, dict):
                    amount_ml = clamp_int(config.get("amount_ml"), 10, 500, 50)
                    channel.config = ChannelConfig(
                        configured=bool(config.get("configured", channel.plant_configured)),
                        plant_id=clamp_int(config.get("plant_id"), 0, 2147483647, 0),
                        plant_name=str(config.get("plant_name") or ""),
                        photo_url=str(config.get("photo_url") or ""),
                        type_category=str(config.get("type_category") or ""),
                        type_description=str(config.get("type_description") or ""),
                        temp_min=clamp_int(config.get("temp_min"), -50, 100, 0),
                        temp_max=clamp_int(config.get("temp_max"), -50, 100, 0),
                        air_humidity_min=clamp_int(config.get("air_humidity_min"), 0, 100, 0),
                        air_humidity_max=clamp_int(config.get("air_humidity_max"), 0, 100, 0),
                        mode=str(config.get("mode") or "Disabled"),
                        manual_duration_seconds=clamp_int(config.get("manual_duration_seconds"), 30, 500, 50),
                        duration_seconds=watering_duration_seconds(amount_ml),
                        amount_ml=amount_ml,
                        interval_hours=clamp_int(config.get("interval_hours"), 1, 240, 24),
                        first_watering_time=normalize_time(config.get("first_watering_time"), "08:00:00"),
                        smart_min_moisture=clamp_int(config.get("smart_min_moisture"), 1, 98, 20),
                        smart_max_moisture=clamp_int(config.get("smart_max_moisture"), 2, 99, 60),
                        smart_daytime_watering=bool(config.get("smart_daytime_watering", True)),
                    )
                if not isinstance(config, dict):
                    channel.config.configured = channel.plant_configured
                if is_empty_placeholder_channel(channel):
                    channel.plant_configured = False
                    channel.config.configured = False
            channels.append(channel)
        while len(channels) < 4:
            channels.append(ChannelState())

        return DeviceState(
            id=str(item.get("id") or uuid.uuid4()),
            name=str(item.get("name") or item.get("host") or "GrowCube"),
            host=str(item.get("host") or ""),
            port=max(1, min(65535, int(item.get("port") or 8800))),
            connection_problem_since=item.get("connection_problem_since"),
            device_id=item.get("device_id"),
            version=item.get("version"),
            temperature=optional_int(item.get("temperature")),
            humidity=optional_int(item.get("humidity")),
            water_warning=bool(item.get("water_warning", False)),
            device_locked=bool(item.get("device_locked", False)),
            tank_capacity_ml=clamp_int(item.get("tank_capacity_ml"), 500, 50000, 1500),
            tank_remaining_ml=clamp_int(item.get("tank_remaining_ml"), 0, 50000, 1500),
            tank_used_ml=clamp_int(item.get("tank_used_ml"), 0, 50000, 0),
            tank_forecast=item.get("tank_forecast") if isinstance(item.get("tank_forecast"), dict) else {},
            firmware_update_status=str(item.get("firmware_update_status") or "idle"),
            firmware_update_error=str(item.get("firmware_update_error") or ""),
            firmware_update_started_at=item.get("firmware_update_started_at"),
            updated_at=item.get("updated_at"),
            channels=channels,
        )

    @staticmethod
    def _state_to_dict(state: DeviceState) -> dict[str, Any]:
        daily_usage_ml = estimated_daily_usage_ml(state)
        unusable_reserve_ml = tank_unusable_reserve_ml(state.tank_capacity_ml)
        usable_remaining_ml = max(0, state.tank_remaining_ml - unusable_reserve_ml)
        tank_days_left = round(usable_remaining_ml / daily_usage_ml, 1) if daily_usage_ml > 0 else None
        return {
            "id": state.id,
            "name": state.name,
            "host": state.host,
            "port": state.port,
            "connected": state.connected,
            "connecting": state.connecting,
            "error": state.error,
            "connection_problem_since": state.connection_problem_since,
            "device_id": state.device_id,
            "version": state.version,
            "addon_api_url": cached_supervisor_ingress_url(),
            "temperature": state.temperature,
            "humidity": state.humidity,
            "water_warning": state.water_warning,
            "device_locked": state.device_locked,
            "tank_capacity_ml": state.tank_capacity_ml,
            "tank_remaining_ml": state.tank_remaining_ml,
            "tank_level": round(state.tank_remaining_ml / max(1, state.tank_capacity_ml) * 100),
            "tank_used_ml": state.tank_used_ml,
            "tank_days_left": tank_days_left,
            "tank_daily_usage_ml": round(daily_usage_ml, 1) if daily_usage_ml > 0 else None,
            "tank_unusable_reserve_ml": unusable_reserve_ml,
            "tank_usable_remaining_ml": usable_remaining_ml,
            "tank_forecast": state.tank_forecast,
            "firmware_update_status": state.firmware_update_status,
            "firmware_update_error": state.firmware_update_error,
            "firmware_update_started_at": state.firmware_update_started_at,
            "updated_at": state.updated_at,
            "channels": [
                {
                    "moisture": channel.moisture,
                    "pump_open": channel.pump_open,
                    "last_watering": channel.last_watering,
                    "next_watering": channel.next_watering,
                    "plant_configured": channel.plant_configured and channel.config.configured,
                    "outlet_locked": channel.outlet_locked,
                    "outlet_blocked": channel.outlet_blocked,
                    "sensor_fault": channel.sensor_fault,
                    "sensor_disconnected": channel.sensor_disconnected,
                    "watering_issue": channel.watering_issue,
                    "watering_locked": channel.watering_locked,
                    "history_loading": channel.history_loading,
                    "history_complete": channel.history_complete,
                    "watering_events_complete": channel.watering_events_complete,
                    "history_count": len(channel.history),
                    "history": channel.history,
                    "watering_events": channel.watering_events,
                    "config": {
                        "configured": channel.config.configured,
                        "plant_id": channel.config.plant_id,
                        "plant_name": channel.config.plant_name,
                        "photo_url": channel.config.photo_url,
                        "type_category": channel.config.type_category,
                        "type_description": channel.config.type_description,
                        "temp_min": channel.config.temp_min,
                        "temp_max": channel.config.temp_max,
                        "air_humidity_min": channel.config.air_humidity_min,
                        "air_humidity_max": channel.config.air_humidity_max,
                        "mode": channel.config.mode,
                        "manual_duration_seconds": channel.config.manual_duration_seconds,
                        "duration_seconds": channel.config.duration_seconds,
                        "amount_ml": channel.config.amount_ml,
                        "interval_hours": channel.config.interval_hours,
                        "first_watering_time": channel.config.first_watering_time,
                        "smart_min_moisture": channel.config.smart_min_moisture,
                        "smart_max_moisture": channel.config.smart_max_moisture,
                        "smart_daytime_watering": channel.config.smart_daytime_watering,
                    },
                }
                for channel in state.channels
            ],
        }


def validate_channel(value: int) -> int:
    channel = int(value)
    if channel < 0 or channel > 3:
        raise ValueError("channel must be 0-3")
    return channel


def optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def tank_unusable_reserve_ml(capacity_ml: int) -> int:
    return GROWCUBE_TANK_UNUSABLE_RESERVE_ML if int(capacity_ml) == GROWCUBE_TANK_CAPACITY_ML else 0


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def history_hour_key(value: datetime | None) -> int | None:
    if value is None or value.year < 2020:
        return None
    local_value = value.astimezone()
    return local_value.toordinal() * 24 + local_value.hour


def normalize_time(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) < 2:
        return fallback
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1])))
        second = max(0, min(59, int(parts[2]) if len(parts) > 2 else 0))
    except ValueError:
        return fallback
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def channel_from_key(key: str) -> int | None:
    suffix = key.rsplit("_", 1)[-1]
    if suffix in "abcd":
        return "abcd".index(suffix)
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_timezone():
    tz_name = os.environ.get("TZ", "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name.removeprefix(":"))
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def abs_iso_seconds(first: Any, second: Any) -> float:
    try:
        first_dt = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
        second_dt = datetime.fromisoformat(str(second).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return float("inf")
    return abs((first_dt - second_dt).total_seconds())


def watering_duration_seconds(amount_ml: int) -> int:
    amount_ml = max(30, int(amount_ml))
    seconds = (amount_ml * 10 + 99) // 84
    return max(4, min(60, seconds))


def watering_amount_ml(duration_seconds: int) -> int:
    seconds = max(0, int(duration_seconds))
    if seconds == 0:
        return 0
    if seconds <= 1:
        return 15
    if seconds <= 3:
        return 15 + ((seconds - 1) * (26 - 15) + (3 - 1) // 2) // (3 - 1)
    if seconds <= 4:
        return 26 + ((seconds - 3) * (37 - 26) + (4 - 3) // 2) // (4 - 3)
    if seconds <= 6:
        return 37 + ((seconds - 4) * (59 - 37) + (6 - 4) // 2) // (6 - 4)
    if seconds <= 10:
        return 59 + ((seconds - 6) * (97 - 59) + (10 - 6) // 2) // (10 - 6)
    return 97 + ((seconds - 10) * (97 - 59) + (10 - 6) // 2) // (10 - 6)


def stable_watering_amount_ml(duration_seconds: int, preferred_amount_ml: int | None = None) -> int:
    if preferred_amount_ml is not None:
        preferred = max(10, min(500, int(preferred_amount_ml)))
        if watering_duration_seconds(preferred) == duration_seconds:
            return preferred
    amount = max(10, min(500, watering_amount_ml(duration_seconds)))
    return ((amount + 5) // 10) * 10


def datetime_from_growcube_local_epoch(epoch: int) -> datetime | None:
    try:
        utc_components = datetime.fromtimestamp(int(epoch), timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return datetime(
        utc_components.year,
        utc_components.month,
        utc_components.day,
        utc_components.hour,
        utc_components.minute,
        utc_components.second,
        tzinfo=local_timezone(),
    )


def estimated_daily_usage_ml(state: DeviceState) -> float:
    forecast_usage = firmware_forecast_daily_usage_ml(state)
    if forecast_usage is not None:
        return forecast_usage

    usage = 0.0
    for channel in state.channels:
        config = channel.config
        if not config.configured or config.mode != "Repeating":
            continue
        if config.amount_ml <= 0 or config.interval_hours <= 0:
            continue
        usage += config.amount_ml * 24 / max(config.interval_hours, 1)
    return usage


def firmware_forecast_daily_usage_ml(state: DeviceState) -> float | None:
    forecast = state.tank_forecast or {}
    if (
        not smart_watering_active(state)
        or not bool(forecast.get("known"))
        or int(forecast.get("valid_days") or 0) <= 0
        or int(forecast.get("smart_events") or 0) <= 0
    ):
        return None
    usage_x10 = timed_daily_usage_x10(state)
    usage_x10 += max(0, int(forecast.get("smart_daily_x10") or 0))
    usage_x10 += max(0, int(forecast.get("unknown_daily_x10") or 0))
    return usage_x10 / 10


def timed_daily_usage_x10(state: DeviceState) -> int:
    usage_x10 = 0
    for channel in state.channels:
        config = channel.config
        if (
            not config.configured
            or config.mode != "Repeating"
            or config.interval_hours <= 0
            or config.amount_ml <= 0
        ):
            continue
        usage_x10 += (config.amount_ml * 24 * 10 + config.interval_hours // 2) // config.interval_hours
    return usage_x10


def smart_watering_active(state: DeviceState) -> bool:
    return any(channel.config.configured and channel.config.mode == "Smart" for channel in state.channels)


def next_watering_datetime(value: str) -> datetime:
    normalized = normalize_time(value, "08:00:00")
    hour, minute, second = (int(part) for part in normalized.split(":"))
    now = datetime.now().astimezone()
    start = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if start <= now:
        start = start + timedelta(days=1)
    return start


def is_empty_placeholder_channel(channel: ChannelState) -> bool:
    config = channel.config
    return (
        not config.plant_name.strip()
        and not config.photo_url.strip()
        and config.mode == "Disabled"
        and not channel.last_watering
        and not channel.next_watering
        and not channel.history
        and not channel.watering_events
    )


def mqtt_safe_id(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")


def mqtt_options() -> MqttOptions:
    options = GrowCubeManager._read_json(OPTIONS_PATH, {})
    return MqttOptions(
        host=str(options.get("mqtt_host") or os.environ.get("MQTT_HOST") or "core-mosquitto"),
        port=int(options.get("mqtt_port") or os.environ.get("MQTT_PORT") or 1883),
        username=str(options.get("mqtt_username") or os.environ.get("MQTT_USERNAME") or ""),
        password=str(options.get("mqtt_password") or os.environ.get("MQTT_PASSWORD") or ""),
    )


def web_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GrowCube</title>
  <style>
    :root {
      color-scheme: light dark;
      --primary-text-color: #172026;
      --secondary-text-color: #66757f;
      --divider-color: #d9e0e4;
      --card-background-color: #ffffff;
      --ha-card-background: #ffffff;
      --primary-color: #0b7fab;
      --error-color: #b42318;
      --warning-color: #b15d00;
      --success-color: #17803d;
      --bg: #eef2f4;
      --panel: var(--ha-card-background);
      --text: var(--primary-text-color);
      --muted: var(--secondary-text-color);
      --line: var(--divider-color);
      --accent: var(--primary-color);
      --ok: var(--success-color);
      --warn: var(--warning-color);
      --bad: var(--error-color);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --primary-text-color: #edf3f6;
        --secondary-text-color: #9aabb4;
        --divider-color: #2d3a41;
        --card-background-color: #171f24;
        --ha-card-background: #171f24;
        --primary-color: #4db6d8;
        --bg: #11161a;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main { max-width: 1120px; margin: 0 auto; padding: 18px; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 42px;
      margin-bottom: 8px;
      padding: 0 12px;
    }
    .topbar-title {
      display: inline-flex;
      min-width: 0;
      align-items: center;
      gap: 10px;
    }
    h1 { margin: 0; font-size: 22px; font-weight: 650; }
    h2 { margin: 0 0 12px; font-size: 17px; font-weight: 650; }
    p { margin: 4px 0 0; color: var(--muted); }
    .icon-control {
      display: inline-flex;
      width: 42px;
      height: 42px;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      padding: 0;
    }
    .icon-control svg { width: 22px; height: 22px; }
    .settings-header { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
    .settings-header h1 { font-size: 20px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      margin: 14px 0;
    }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    input {
      min-width: 220px;
      flex: 1 1 260px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: transparent;
      color: var(--text);
      padding: 10px 12px;
      font: inherit;
    }
    button {
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      padding: 10px 13px;
      font: inherit;
      cursor: pointer;
    }
    button.secondary { background: transparent; color: var(--accent); }
    button.danger { border-color: var(--bad); background: transparent; color: var(--bad); }
    button:disabled { cursor: wait; opacity: .65; }
    .section-header {
      justify-content: space-between;
      margin-bottom: 14px;
    }
    .section-header h2 { margin: 0; }
    section > .row + p { margin-top: 8px; }
    .discover-actions {
      align-items: stretch;
    }
    .discover-actions button {
      min-width: 132px;
    }
    .network-row {
      margin-top: 10px;
    }
    #discoverStatus:not(:empty),
    #discoverResults:not(:empty) {
      margin-top: 12px;
    }
    .list { display: grid; gap: 10px; }
    .item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .item > button { justify-self: end; }
    .item-actions {
      display: inline-flex;
      gap: 8px;
      justify-self: end;
      align-items: center;
      flex-wrap: wrap;
    }
    .item-actions button { min-width: 92px; }
    .title { font-weight: 650; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-top: 6px;
      font-size: 13px;
      color: var(--muted);
    }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
    .dot.ok { background: var(--ok); }
    .dot.warn { background: var(--warn); }
    .dot.bad { background: var(--bad); }
    .spinner {
      width: 18px;
      height: 18px;
      border: 2px solid color-mix(in srgb, var(--accent) 24%, transparent);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.9s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 40;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.42);
      padding: 18px;
    }
    .modal {
      width: min(560px, 100%);
      max-height: calc(100vh - 36px);
      overflow: auto;
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.28);
    }
    .modal-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .modal-header h2 { margin: 0; }
    .modal-title-row {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .modal-title-row h2 { overflow-wrap: anywhere; }
    .round-edit {
      display: inline-flex;
      width: 28px;
      height: 28px;
      min-width: 28px;
      align-items: center;
      justify-content: center;
      border: 0;
      border-radius: 50%;
      padding: 0;
      background: color-mix(in srgb, var(--text) 8%, transparent);
      color: var(--muted);
    }
    .round-edit:hover {
      background: color-mix(in srgb, var(--accent) 18%, transparent);
      color: var(--text);
    }
    .round-edit svg { width: 15px; height: 15px; }
    .modal-body { display: grid; gap: 14px; }
    .settings-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .settings-stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
    }
    .settings-stat:only-child { grid-column: 1 / -1; }
    .settings-stat .label {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 3px;
    }
    .settings-stat .value { font-weight: 650; overflow-wrap: anywhere; }
    .settings-stat input {
      min-width: 0;
      width: 100%;
      flex: 0 0 auto;
      border: 0;
      border-radius: 0;
      padding: 0;
      font-weight: 650;
      background: transparent;
    }
    .settings-stat input:focus { outline: none; }
    .settings-stat:focus-within { border-color: var(--accent); }
    .warning-box {
      border: 1px solid color-mix(in srgb, var(--warn) 48%, transparent);
      border-radius: 8px;
      padding: 10px;
      color: var(--text);
      background: color-mix(in srgb, var(--warn) 12%, transparent);
    }
    .modal-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .modal-status {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 24px;
      color: var(--muted);
    }
    .empty { color: var(--muted); padding: 10px 0; }
    .error { color: var(--bad); }
    .hidden { display: none; }
    growcube-card { display: block; }
    ha-card {
      display: block;
      color: var(--primary-text-color);
      background: var(--ha-card-background);
      border-radius: 8px;
    }
    ha-icon {
      display: inline-flex;
      width: 24px;
      height: 24px;
      align-items: center;
      justify-content: center;
      vertical-align: middle;
    }
    ha-icon svg {
      display: block;
      width: 100%;
      height: 100%;
    }
    @media (max-width: 640px) {
      main { padding: 16px; }
      .topbar { display: grid; grid-template-columns: minmax(0, 1fr) auto; padding: 0; }
      .item { grid-template-columns: 1fr; display: grid; }
      .item-actions { width: 100%; justify-self: stretch; }
      .item button:not(.icon-control) { width: 100%; justify-self: stretch; }
      .settings-grid { grid-template-columns: 1fr; }
      .discover-actions button { flex: 1 1 100%; }
    }
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <div class="topbar-title">
      <button class="icon-control hidden" id="plantBackBtn" type="button" aria-label="Back to dashboard">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M19 12H5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
          <path d="m12 5-7 7 7 7" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      </button>
      <h1>GrowCube</h1>
    </div>
    <button class="icon-control" id="settingsBtn" type="button" aria-label="Settings">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.9"></circle>
        <path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-5l-.4 3.1a7 7 0 0 0-1.7 1L5 6.1l-2 3.4L5.1 11a7 7 0 0 0 0 2L3 14.5l2 3.4 2.4-1a7 7 0 0 0 1.7 1l.4 3.1h5l.4-3.1a7 7 0 0 0 1.7-1l2.4 1 2-3.4-2.1-1.5c.1-.3.1-.7.1-1Z" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
      </svg>
    </button>
  </div>

  <div id="dashboardView">
    <growcube-card id="growcubeDashboard"></growcube-card>
  </div>

  <div id="settingsView" class="hidden">
    <div class="settings-header">
      <button class="icon-control" id="settingsBackBtn" type="button" aria-label="Back to dashboard">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M19 12H5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path>
          <path d="m12 5-7 7 7 7" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
      </button>
      <h1>Settings</h1>
    </div>

    <section>
      <div class="row section-header">
        <h2>Devices</h2>
        <button class="secondary" id="refreshBtn">Refresh</button>
      </div>
      <div id="devices" class="list"><div class="empty">Loading devices...</div></div>
    </section>

    <section>
      <h2>Discover GrowCube</h2>
      <div class="row discover-actions">
        <button id="discoverBtn">Automatic search</button>
        <button class="secondary" id="networkOptionsBtn" type="button" aria-expanded="false" aria-controls="networkOptionsRow">Network</button>
      </div>
      <div class="row network-row hidden" id="networkOptionsRow">
        <input id="networkInput" placeholder="Network, for example 192.168.1.0/24">
      </div>
      <div id="discoverStatus" class="meta"></div>
      <div id="discoverResults" class="list"></div>
    </section>

    <section>
      <h2>Manual add</h2>
      <div class="row">
        <input id="hostInput" placeholder="GrowCube IP or host">
        <input id="nameInput" placeholder="Name">
        <button id="addManualBtn">Add device</button>
      </div>
    </section>
  </div>
  <div id="deviceSettingsModal" class="modal-backdrop hidden"></div>
</main>
<script>
window.GROWCUBE_STANDALONE_WEBUI = true;
window.GROWCUBE_STANDALONE_ADDON_API_URL = "";
if (!customElements.get("ha-card")) customElements.define("ha-card", class extends HTMLElement {});
if (!customElements.get("ha-icon")) {
  customElements.define("ha-icon", class extends HTMLElement {
    static get observedAttributes() { return ["icon"]; }
    connectedCallback() { this.render(); }
    attributeChangedCallback() { this.render(); }
    render() {
      const icon = String(this.getAttribute("icon") || "");
      this.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${iconSvg(icon)}</svg>`;
    }
  });
}
</script>
<script src="growcube-card.js"></script>
<script>

const devicesEl = document.getElementById("devices");
const resultsEl = document.getElementById("discoverResults");
const statusEl = document.getElementById("discoverStatus");
const dashboardCard = document.getElementById("growcubeDashboard");
const basePath = window.location.pathname.endsWith("/") ? (window.location.pathname.slice(0, -1) || "/") : window.location.pathname;
const addonApiUrl = `${window.location.origin}${basePath === "/" ? "" : basePath}`;
window.GROWCUBE_STANDALONE_ADDON_API_URL = addonApiUrl;
let dashboardPayload = {devices: []};
let deviceSettingsId = "";
let deviceSettingsBusy = "";
let deviceSettingsMessage = "";
let deviceSettingsTone = "";
let deviceSettingsUpdateAvailable = false;
let deviceSettingsLatestVersion = "";
let deviceSettingsConfirm = "";
let deviceSettingsUpdateAcknowledged = false;
let deviceSettingsRenameValue = "";

function iconSvg(icon) {
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
    "mdi:cog": `<circle ${common} cx="12" cy="12" r="3"/><path ${common} d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1a7 7 0 0 0-1.7-1L14.5 3h-5l-.4 3.1a7 7 0 0 0-1.7 1L5 6.1l-2 3.4L5.1 11a7 7 0 0 0 0 2L3 14.5l2 3.4 2.4-1a7 7 0 0 0 1.7 1l.4 3.1h5l.4-3.1a7 7 0 0 0 1.7-1l2.4 1 2-3.4-2.1-1.5c.1-.3.1-.7.1-1Z"/>`,
  };
  return icons[icon] || `<circle ${common} cx="12" cy="12" r="8"/><path ${common} d="M12 8v4l3 3"/>`;
}

dashboardCard._defaultNavigationPath = function(channel = undefined, deviceId = "") {
  const params = new URLSearchParams(window.location.search || "");
  const channelKey = this._channelKey(channel);
  if (channelKey) params.set("view", `plant-${channelKey}`);
  if (deviceId) params.set("device", deviceId);
  return `${basePath}?${params.toString()}`;
};

dashboardCard._detailBackPath = function() {
  const params = new URLSearchParams(window.location.search || "");
  params.delete("view");
  const query = params.toString();
  return `${basePath}${query ? "?" + query : ""}`;
};

async function fetchJson(path) {
  const url = /^https?:[/][/]/i.test(String(path)) ? path : `${addonApiUrl}/${String(path).replace(/^[/]+/, "")}`;
  const response = await fetch(url, {cache: "no-store"});
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (error) {
    throw new Error(`API returned non-JSON for ${url}: ${response.status} ${text.slice(0, 160)}`);
  }
  if (!response.ok || data.error) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function deviceStatus(device) {
  if (device.connected) return {text: "Connected", cls: "ok"};
  if (device.connecting) return {text: "Connecting", cls: "warn"};
  if (device.error) return {text: device.error, cls: "bad"};
  return {text: "Disconnected", cls: "bad"};
}

function mergeStates(payload) {
  const states = {};
  for (const device of payload.devices || []) {
    Object.assign(states, device.states || {});
  }
  return states;
}

function formatEntityState(entity) {
  const unit = entity?.attributes?.unit_of_measurement || "";
  if (!entity || entity.state === "unknown" || entity.state === "unavailable") {
    return "Unknown";
  }
  return unit ? `${entity.state} ${unit}` : entity.state;
}

async function callService(domain, service, data = {}) {
  const entityId = data.entity_id;
  if (!entityId) return;
  const params = new URLSearchParams({entity_id: entityId, domain, service});
  if (Object.prototype.hasOwnProperty.call(data, "value")) params.set("value", data.value);
  if (Object.prototype.hasOwnProperty.call(data, "option")) params.set("value", data.option);
  if (Object.prototype.hasOwnProperty.call(data, "time")) params.set("value", data.time);
  await fetchJson("entity/command?" + params.toString());
  await refreshDashboard(true);
}

function syncDashboardCardDevices() {
  if (!dashboardCard._mergeDashboardDeviceMetadata) {
    return;
  }
  dashboardCard._dashboardDevices = dashboardCard._mergeDashboardDeviceMetadata(
    dashboardPayload.devices || [],
    dashboardCard._dashboardDevices || [],
  );
  dashboardCard._dashboardDevicesLoadedAt = Date.now();
}

function updateDashboardCard() {
  const states = mergeStates(dashboardPayload);
  syncDashboardCardDevices();
  dashboardCard.hass = {
    states,
    callService,
    formatEntityState,
    callApi: async (method, path) => fetchJson(path.replace(/^growcube\\//, "")),
  };
}

function cardConfigFromLocation() {
  const params = new URLSearchParams(window.location.search || "");
  const view = String(params.get("view") || "");
  const match = view.match(/^plant-([abcd])$/i);
  if (match) {
    const channel = match[1].toLowerCase();
    return {
      title: `Plant ${channel.toUpperCase()}`,
      name: `Plant ${channel.toUpperCase()}`,
      channel: `Channel ${channel.toUpperCase()}`,
      detail: true,
      addon_api_url: addonApiUrl,
    };
  }
  return {title: "GrowCube", overview: "dashboard", addon_api_url: addonApiUrl};
}

function applyCardConfigFromLocation() {
  const next = cardConfigFromLocation();
  const current = dashboardCard._webUiRouteKey || "";
  const routeKey = JSON.stringify(next);
  document.getElementById("plantBackBtn").classList.toggle("hidden", !next.detail);
  if (current !== routeKey) {
    dashboardCard.setConfig(next);
    dashboardCard._webUiRouteKey = routeKey;
  }
}

async function refreshDashboard(force = false) {
  dashboardPayload = await fetchJson("dashboard");
  applyCardConfigFromLocation();
  updateDashboardCard();
  if (force) {
    renderDevices(dashboardPayload);
  }
}

function renderDevices(payload) {
  const devices = payload.devices || [];
  if (!devices.length) {
    devicesEl.innerHTML = '<div class="empty">No devices added yet.</div>';
    renderDeviceSettingsModal();
    return;
  }
  devicesEl.innerHTML = devices.map((device) => {
    const status = deviceStatus(device);
    const configured = Object.values(device.channels || {}).filter((channel) => channel.configured).length;
    const version = device.version ? "Version " + escapeHtml(device.version) + " · " : "";
    return `
      <div class="item">
        <div>
          <div class="title">${escapeHtml(device.name)}</div>
          <div class="meta">${escapeHtml(device.host)} · ${version}${configured}/4 channels configured</div>
          <div class="status"><span class="dot ${status.cls}"></span>${escapeHtml(status.text)}</div>
        </div>
        <div class="item-actions">
          <button class="secondary" data-device-settings="${escapeHtml(device.device_id)}">Settings</button>
          <button class="danger" data-remove="${escapeHtml(device.device_id)}">Remove</button>
        </div>
      </div>
    `;
  }).join("");
  renderDeviceSettingsModal();
}

async function refreshDevices() {
  dashboardPayload = await fetchJson("dashboard");
  renderDevices(dashboardPayload);
  updateDashboardCard();
}

async function addDevice(host, name) {
  const params = new URLSearchParams({host, name: name || host});
  await fetchJson("devices/add?" + params.toString());
  await refreshDashboard(true);
}

async function removeDevice(deviceId) {
  const params = new URLSearchParams({device_id: deviceId});
  await fetchJson("devices/remove?" + params.toString());
  if (deviceSettingsId === deviceId) closeDeviceSettings();
  await refreshDashboard(true);
}

function findDevice(deviceId) {
  return (dashboardPayload.devices || []).find((device) => device.device_id === deviceId);
}

function openDeviceSettings(deviceId) {
  deviceSettingsId = deviceId;
  deviceSettingsBusy = "checking";
  deviceSettingsMessage = "Checking firmware version...";
  deviceSettingsTone = "";
  deviceSettingsUpdateAvailable = false;
  deviceSettingsLatestVersion = "";
  deviceSettingsConfirm = "";
  deviceSettingsUpdateAcknowledged = false;
  deviceSettingsRenameValue = "";
  renderDeviceSettingsModal();
  checkFirmware(deviceId);
}

function closeDeviceSettings() {
  deviceSettingsId = "";
  deviceSettingsBusy = "";
  deviceSettingsMessage = "";
  deviceSettingsTone = "";
  deviceSettingsUpdateAvailable = false;
  deviceSettingsLatestVersion = "";
  deviceSettingsConfirm = "";
  deviceSettingsUpdateAcknowledged = false;
  deviceSettingsRenameValue = "";
  renderDeviceSettingsModal();
}

function setDeviceSettingsStatus(message, tone = "", busy = "") {
  deviceSettingsMessage = message;
  deviceSettingsTone = tone;
  deviceSettingsBusy = busy;
  renderDeviceSettingsModal();
}

function renderDeviceSettingsModal() {
  const modal = document.getElementById("deviceSettingsModal");
  const device = findDevice(deviceSettingsId);
  if (!device) {
    modal.classList.add("hidden");
    modal.innerHTML = "";
    return;
  }
  const status = deviceStatus(device);
  const updateStatus = device.firmware_update_status || "idle";
  const updateError = device.firmware_update_error || "";
  const busy = Boolean(deviceSettingsBusy) || updateStatus === "updating";
  const message = deviceSettingsMessage || (updateError ? updateError : "");
  const messageClass = deviceSettingsTone === "error" || updateError ? "error" : "meta";
  const updateAvailable = deviceSettingsUpdateAvailable && deviceSettingsLatestVersion;
  const confirmingUpdate = deviceSettingsConfirm === "update_firmware";
  const confirmingReset = deviceSettingsConfirm === "reset_network";
  const renamingDevice = deviceSettingsConfirm === "rename_device";
  modal.classList.remove("hidden");
  if (renamingDevice) {
    modal.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="Rename GrowCube">
        <div class="modal-header">
          <div>
            <h2>Rename GrowCube</h2>
            <div class="meta">${escapeHtml(device.host)}</div>
          </div>
          <button class="icon-control" type="button" data-close-device-settings aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path></svg>
          </button>
        </div>
        <div class="modal-body">
          <label class="settings-stat">
            <div class="label">Name</div>
            <input id="deviceRenameInput" value="${escapeHtml(deviceSettingsRenameValue)}" maxlength="64">
          </label>
          <div class="modal-actions">
            <button class="secondary" data-cancel-device-confirm>Cancel</button>
            <button data-confirm-rename-device="${escapeHtml(device.device_id)}">Save</button>
          </div>
          <div class="modal-status">
            ${busy ? '<span class="spinner"></span>' : ""}
            <span class="${messageClass}">${escapeHtml(message)}</span>
          </div>
        </div>
      </div>
    `;
    setTimeout(() => {
      const input = document.getElementById("deviceRenameInput");
      input?.focus();
      input?.select();
    }, 0);
    return;
  }
  if (confirmingReset) {
    modal.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="Reset network">
        <div class="modal-header">
          <div>
            <h2>Reset network</h2>
            <div class="meta">${escapeHtml(device.name || "GrowCube")} · ${escapeHtml(device.host)}</div>
          </div>
          <button class="icon-control" type="button" data-close-device-settings aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path></svg>
          </button>
        </div>
        <div class="modal-body">
          <div class="warning-box">
            Reset Wi-Fi settings? GrowCube will restart, leave this network, and must be configured again.
          </div>
          <div class="modal-actions">
            <button class="secondary" data-cancel-device-confirm>Cancel</button>
            <button class="danger" data-confirm-reset-network="${escapeHtml(device.device_id)}">Reset network</button>
          </div>
          <div class="modal-status">
            ${busy ? '<span class="spinner"></span>' : ""}
            <span class="${messageClass}">${escapeHtml(message)}</span>
          </div>
        </div>
      </div>
    `;
    return;
  }
  modal.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="Device settings">
      <div class="modal-header">
        <div>
          <div class="modal-title-row">
            <h2>${escapeHtml(device.name || "GrowCube")}</h2>
            <button class="round-edit" type="button" data-rename-device="${escapeHtml(device.device_id)}" aria-label="Rename GrowCube" title="Rename GrowCube" ${busy ? "disabled" : ""}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4.2L18.7 9.5a2.1 2.1 0 0 0 0-3L17.5 5.3a2.1 2.1 0 0 0-3 0L4 15.8V20Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"></path><path d="m13.6 6.2 4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path></svg>
            </button>
          </div>
          <div class="meta">${escapeHtml(device.host)}</div>
        </div>
        <button class="icon-control" type="button" data-close-device-settings aria-label="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path></svg>
        </button>
      </div>
      <div class="modal-body">
        ${confirmingUpdate ? `
          <div class="warning-box">
            <div class="title">Update firmware</div>
            <p>Do not power off GrowCube during the update process. Interrupting the update may make the device unavailable.</p>
            <label class="row" style="margin-top:12px">
              <input type="checkbox" data-update-ack ${deviceSettingsUpdateAcknowledged ? "checked" : ""} style="min-width:auto; flex:0 0 auto">
              <span>I understand that GrowCube must stay powered on during the update.</span>
            </label>
            <div class="modal-actions" style="margin-top:14px">
              <button class="secondary" data-cancel-device-confirm>Cancel</button>
              <button class="danger" data-confirm-update-firmware="${escapeHtml(device.device_id)}" ${deviceSettingsUpdateAcknowledged ? "" : "disabled"}>Update firmware</button>
            </div>
          </div>
        ` : `
        <div class="settings-grid">
          <div class="settings-stat">
            <div class="label">Current firmware version</div>
            <div class="value">${escapeHtml(device.version || "Unknown")}</div>
          </div>
        </div>
        <div class="modal-actions">
          <button class="secondary" data-check-firmware="${escapeHtml(device.device_id)}" ${busy ? "disabled" : ""}>Check for updates</button>
          ${updateAvailable ? `<button data-update-firmware="${escapeHtml(device.device_id)}" ${busy || !device.connected || confirmingUpdate ? "disabled" : ""}>Update firmware</button>` : ""}
          <button class="danger" data-reset-network="${escapeHtml(device.device_id)}" ${busy || !device.connected || confirmingReset ? "disabled" : ""}>Reset network</button>
        </div>
        <div class="modal-status">
          ${busy ? '<span class="spinner"></span>' : ""}
          <span class="${messageClass}">${escapeHtml(message || (busy ? "Working..." : "Your GrowCube is up to date."))}</span>
        </div>
        `}
      </div>
    </div>
  `;
}

async function checkFirmware(deviceId) {
  if (!findDevice(deviceId)) return;
  deviceSettingsUpdateAvailable = false;
  deviceSettingsLatestVersion = "";
  setDeviceSettingsStatus("Checking GrowCube firmware server...", "", "checking");
  try {
    const params = new URLSearchParams({device_id: deviceId});
    const payload = await fetchJson("devices/firmware/check?" + params.toString());
    if (deviceSettingsId !== deviceId) return;
    deviceSettingsUpdateAvailable = Boolean(payload.update_available);
    deviceSettingsLatestVersion = payload.latest_version || "";
    if (deviceSettingsUpdateAvailable) {
      const suffix = deviceSettingsLatestVersion ? ` Version ${deviceSettingsLatestVersion} is available.` : " A firmware update is available.";
      setDeviceSettingsStatus(suffix.trim(), "ok", "");
    } else {
      setDeviceSettingsStatus("Your GrowCube is up to date.", "ok", "");
    }
  } catch (err) {
    if (deviceSettingsId !== deviceId) return;
    setDeviceSettingsStatus(err.message || "Could not check firmware updates", "error", "");
  }
}

async function updateFirmware(deviceId) {
  const device = findDevice(deviceId);
  if (!device) return;
  if (!deviceSettingsUpdateAcknowledged) {
    setDeviceSettingsStatus("Please confirm that you have read the warning.", "error", "");
    deviceSettingsConfirm = "update_firmware";
    return;
  }
  deviceSettingsConfirm = "";
  deviceSettingsUpdateAcknowledged = false;
  setDeviceSettingsStatus("Downloading firmware from GrowCube server...", "", "downloading");
  try {
    const params = new URLSearchParams({device_id: deviceId});
    await fetchJson("devices/firmware/update?" + params.toString());
    setDeviceSettingsStatus("Firmware uploaded. GrowCube is restarting; waiting for reconnect.", "ok", "");
    await refreshDashboard(true);
  } catch (err) {
    setDeviceSettingsStatus(err.message || "Firmware update failed", "error", "");
  }
}

function askUpdateFirmware(deviceId) {
  if (!findDevice(deviceId)) return;
  deviceSettingsConfirm = "update_firmware";
  deviceSettingsUpdateAcknowledged = false;
  deviceSettingsRenameValue = "";
  deviceSettingsMessage = "";
  deviceSettingsTone = "";
  renderDeviceSettingsModal();
}

function askRenameDevice(deviceId) {
  const device = findDevice(deviceId);
  if (!device) return;
  deviceSettingsConfirm = "rename_device";
  deviceSettingsRenameValue = device.name || "GrowCube";
  deviceSettingsMessage = "";
  deviceSettingsTone = "";
  renderDeviceSettingsModal();
}

async function renameDevice(deviceId) {
  const device = findDevice(deviceId);
  if (!device) return;
  const input = document.getElementById("deviceRenameInput");
  const name = String(input?.value || "").trim();
  deviceSettingsRenameValue = name;
  if (!name) {
    setDeviceSettingsStatus("Name is required.", "error", "");
    return;
  }
  if (name === (device.name || "GrowCube")) {
    deviceSettingsConfirm = "";
    deviceSettingsRenameValue = "";
    renderDeviceSettingsModal();
    return;
  }
  setDeviceSettingsStatus("Saving device name...", "", "saving");
  try {
    const params = new URLSearchParams({device_id: deviceId, name});
    await fetchJson("devices/rename?" + params.toString());
    deviceSettingsConfirm = "";
    deviceSettingsRenameValue = "";
    await refreshDashboard(true);
    setDeviceSettingsStatus("Device name saved.", "ok", "");
  } catch (err) {
    setDeviceSettingsStatus(err.message || "Could not save device name", "error", "");
  }
}

function askResetNetwork(deviceId) {
  if (!findDevice(deviceId)) return;
  deviceSettingsConfirm = "reset_network";
  deviceSettingsRenameValue = "";
  deviceSettingsMessage = "";
  deviceSettingsTone = "";
  renderDeviceSettingsModal();
}

function cancelDeviceConfirm() {
  deviceSettingsConfirm = "";
  deviceSettingsUpdateAcknowledged = false;
  deviceSettingsRenameValue = "";
  renderDeviceSettingsModal();
}

async function resetNetwork(deviceId) {
  const device = findDevice(deviceId);
  if (!device) return;
  deviceSettingsConfirm = "";
  setDeviceSettingsStatus("Resetting network settings...", "", "resetting");
  try {
    const params = new URLSearchParams({device_id: deviceId});
    await fetchJson("devices/reset_network?" + params.toString());
    setDeviceSettingsStatus("Network reset requested. GrowCube will restart and leave this network.", "ok", "");
    await refreshDashboard(true);
  } catch (err) {
    setDeviceSettingsStatus(err.message || "Network reset failed", "error", "");
  }
}

async function discoverDevices() {
  const button = document.getElementById("discoverBtn");
  button.disabled = true;
  statusEl.textContent = "Searching...";
  resultsEl.innerHTML = "";
  try {
    const network = document.getElementById("networkInput").value.trim();
    const query = network ? "?" + new URLSearchParams({network}).toString() : "";
    const payload = await fetchJson("devices/discover" + query);
    const devices = payload.devices || [];
    statusEl.textContent = devices.length ? `Found ${devices.length} device(s).` : "No GrowCube devices found.";
    resultsEl.innerHTML = devices.map((device) => `
      <div class="item">
        <div>
          <div class="title">${escapeHtml(device.name || "GrowCube")}</div>
          <div class="meta">${escapeHtml(device.host)}:${escapeHtml(device.port || 8800)} ${device.version ? "· Version " + escapeHtml(device.version) : ""}</div>
        </div>
        <button data-add="${escapeHtml(device.host)}" data-name="${escapeHtml(device.name || "GrowCube")}">Add</button>
      </div>
    `).join("") || '<div class="empty">If the cube is open in the GrowCube mobile app or another controller, close that app first so it releases the TCP connection, then run automatic search again. You can also enter the network manually, for example 192.168.1.0/24.</div>';
  } catch (err) {
    statusEl.innerHTML = '<span class="error">' + escapeHtml(err.message) + '</span>';
  } finally {
    button.disabled = false;
  }
}

document.getElementById("refreshBtn").addEventListener("click", refreshDevices);
document.getElementById("discoverBtn").addEventListener("click", discoverDevices);
document.getElementById("networkOptionsBtn").addEventListener("click", () => {
  const row = document.getElementById("networkOptionsRow");
  const hidden = row.classList.toggle("hidden");
  document.getElementById("networkOptionsBtn").setAttribute("aria-expanded", hidden ? "false" : "true");
  if (!hidden) document.getElementById("networkInput").focus();
});
document.getElementById("settingsBtn").addEventListener("click", () => setActiveView("settings"));
document.getElementById("plantBackBtn").addEventListener("click", () => {
  const params = new URLSearchParams(window.location.search || "");
  params.delete("view");
  const query = params.toString();
  window.history.pushState(null, "", `${basePath}${query ? "?" + query : ""}`);
  window.dispatchEvent(new CustomEvent("location-changed"));
});
document.getElementById("settingsBackBtn").addEventListener("click", () => setActiveView("dashboard"));
document.getElementById("addManualBtn").addEventListener("click", async () => {
  const host = document.getElementById("hostInput").value.trim();
  const name = document.getElementById("nameInput").value.trim();
  if (!host) return;
  await addDevice(host, name);
});

function setActiveView(view) {
  const settings = view === "settings";
  document.getElementById("dashboardView").classList.toggle("hidden", settings);
  document.getElementById("settingsView").classList.toggle("hidden", !settings);
  document.querySelector(".topbar").classList.toggle("hidden", settings);
  if (settings) refreshDevices().catch(() => {});
}

window.addEventListener("location-changed", () => {
  applyCardConfigFromLocation();
  updateDashboardCard();
});
window.addEventListener("popstate", () => {
  applyCardConfigFromLocation();
  updateDashboardCard();
});
document.addEventListener("click", async (event) => {
  const actionTarget = event.target?.closest?.("[data-add],[data-remove],[data-device-settings],[data-rename-device],[data-confirm-rename-device],[data-check-firmware],[data-update-firmware],[data-confirm-update-firmware],[data-reset-network],[data-confirm-reset-network],[data-cancel-device-confirm],[data-close-device-settings]");
  const addHost = actionTarget?.dataset?.add;
  const removeId = actionTarget?.dataset?.remove;
  const settingsId = actionTarget?.dataset?.deviceSettings;
  const renameId = actionTarget?.dataset?.renameDevice;
  const confirmRenameId = actionTarget?.dataset?.confirmRenameDevice;
  const checkId = actionTarget?.dataset?.checkFirmware;
  const updateId = actionTarget?.dataset?.updateFirmware;
  const confirmUpdateId = actionTarget?.dataset?.confirmUpdateFirmware;
  const resetId = actionTarget?.dataset?.resetNetwork;
  const confirmResetId = actionTarget?.dataset?.confirmResetNetwork;
  if (actionTarget?.dataset?.closeDeviceSettings !== undefined || event.target?.id === "deviceSettingsModal") {
    closeDeviceSettings();
    return;
  }
  if (actionTarget?.dataset?.cancelDeviceConfirm !== undefined) {
    cancelDeviceConfirm();
    return;
  }
  if (addHost) await addDevice(addHost, actionTarget.dataset.name || addHost);
  if (settingsId) openDeviceSettings(settingsId);
  if (renameId) askRenameDevice(renameId);
  if (confirmRenameId) await renameDevice(confirmRenameId);
  if (checkId) await checkFirmware(checkId);
  if (updateId) askUpdateFirmware(updateId);
  if (confirmUpdateId) await updateFirmware(confirmUpdateId);
  if (resetId) askResetNetwork(resetId);
  if (confirmResetId) await resetNetwork(confirmResetId);
  if (removeId) await removeDevice(removeId);
});

document.addEventListener("input", (event) => {
  if (event.target?.id === "deviceRenameInput") {
    deviceSettingsRenameValue = event.target.value;
  }
});

document.addEventListener("change", (event) => {
  if (event.target?.id === "deviceRenameInput") {
    return;
  }
  if (event.target?.dataset?.updateAck !== undefined) {
    deviceSettingsUpdateAcknowledged = Boolean(event.target.checked);
    renderDeviceSettingsModal();
  }
});

document.addEventListener("keydown", async (event) => {
  if (event.key !== "Enter" || event.target?.id !== "deviceRenameInput" || !deviceSettingsId) {
    return;
  }
  event.preventDefault();
  await renameDevice(deviceSettingsId);
});

refreshDashboard(true).catch((err) => {
  devicesEl.innerHTML = '<div class="error">' + escapeHtml(err.message) + '</div>';
});
setInterval(() => refreshDashboard(true).catch(() => {}), 5000);
</script>
</body>
</html>
"""


def ingress_allowed_networks() -> list[ipaddress._BaseNetwork]:
    raw_value = os.environ.get("GROWCUBE_INGRESS_ALLOWED_CIDRS", "")
    values = [item.strip() for item in raw_value.split(",") if item.strip()] or list(DEFAULT_INGRESS_ALLOWED_CIDRS)
    networks: list[ipaddress._BaseNetwork] = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            LOGGER.warning("Ignoring invalid ingress allowed CIDR %r", value)
    return networks


class GrowCubeApiHandler(BaseHTTPRequestHandler):
    server_version = "GrowCubeAddon/0.2"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        LOGGER.debug(
            "Ingress API request remote=%s path=%s query=%r",
            self.client_address[0],
            parsed.path,
            parsed.query,
        )
        if not self._allow_request():
            LOGGER.warning("Ingress API forbidden remote=%s path=%s", self.client_address[0], parsed.path)
            self._write_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return

        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self._write_html(web_ui_html())
            elif parsed.path == "/growcube-card.js":
                self._write_text(rendered_lovelace_card(), "application/javascript; charset=utf-8")
            elif parsed.path.startswith("/local/growcube/images/") or parsed.path.startswith("/images/"):
                image_name = Path(parsed.path).name
                image_path = CARD_IMAGE_SOURCE_DIR / image_name
                if not image_path.is_file():
                    self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._write_bytes(image_path.read_bytes(), image_content_type(image_path))
            elif parsed.path.startswith("/plant_photos/"):
                image_name = Path(parsed.path).name
                image_path = PLANT_PHOTO_DIR / image_name
                if not image_path.is_file():
                    self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                else:
                    self._write_bytes(image_path.read_bytes(), image_content_type(image_path))
            elif parsed.path == "/health":
                self._write_json({"ok": True})
            elif parsed.path == "/plants/search":
                query = first_query_value(params, "query")
                plants = search_plants(query)
                LOGGER.info("Plant search finished query=%r results=%s", query, len(plants))
                self._write_json({"plants": plants})
            elif parsed.path == "/plants/id":
                plant_id = clamp_int(first_query_value(params, "id"), 0, 2147483647, 0)
                plant = fetch_plant_by_id(plant_id) if plant_id > 0 else None
                self._write_json({"plant": plant})
            elif parsed.path == "/plants/image":
                body, content_type = fetch_remote_image(first_query_value(params, "url"))
                self._write_bytes(body, content_type)
            elif parsed.path == "/dashboard":
                self._write_json(manager.dashboard_payload())
            elif parsed.path == "/devices":
                self._write_json(manager.dashboard_payload())
            elif parsed.path == "/devices/discover":
                self._write_json(manager.discover_payload(first_query_value(params, "network")))
            elif parsed.path == "/devices/add":
                self._write_json(manager.add_device_payload(params))
            elif parsed.path == "/devices/remove":
                self._write_json(manager.remove_device_payload(params))
            elif parsed.path == "/devices/rename":
                self._write_json(manager.rename_device_payload(params))
            elif parsed.path == "/devices/reset_network":
                self._write_json(manager.reset_network_payload(first_query_value(params, "device_id")))
            elif parsed.path == "/devices/firmware/check":
                self._write_json(manager.firmware_check_payload(first_query_value(params, "device_id")))
            elif parsed.path == "/devices/firmware/update":
                self._write_json(manager.firmware_update_payload(first_query_value(params, "device_id")))
            elif parsed.path == "/entity/command":
                self._write_json(manager.entity_command_payload(params))
            elif parsed.path == "/history":
                self._write_json(
                    manager.history_payload(
                        first_query_value(params, "device_id"),
                        first_query_value(params, "channel") or "a",
                        first_query_value(params, "request") == "1",
                    )
                )
            elif parsed.path == "/apply_watering":
                self._write_json(
                    manager.apply_watering_payload(
                        first_query_value(params, "device_id"),
                        first_query_value(params, "channel") or "a",
                    )
                )
            elif parsed.path == "/channel/config":
                self._write_json(
                    manager.configure_channel_payload(
                        first_query_value(params, "device_id"),
                        first_query_value(params, "channel") or "a",
                        params,
                    )
                )
            else:
                self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except KeyError as err:
            self._write_json({"error": str(err)}, HTTPStatus.NOT_FOUND)
        except ValueError as err:
            self._write_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as err:
            self._write_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
        except Exception as err:
            LOGGER.exception("GrowCube ingress API request failed: %s", self.path)
            self._write_json({"error": str(err)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        LOGGER.debug(
            "Ingress API POST request remote=%s path=%s query=%r",
            self.client_address[0],
            parsed.path,
            parsed.query,
        )
        if not self._allow_request():
            LOGGER.warning("Ingress API forbidden remote=%s path=%s", self.client_address[0], parsed.path)
            self._write_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            if parsed.path == "/plants/photo":
                payload = self._read_json_body(2 * 1024 * 1024)
                self._write_json(save_uploaded_plant_photo(payload))
            elif parsed.path == "/channel/config":
                payload = self._read_json_body(64 * 1024)
                params = {
                    key: ["1" if value is True else "0" if value is False else str(value)]
                    for key, value in payload.items()
                    if value is not None
                }
                self._write_json(
                    manager.configure_channel_payload(
                        first_query_value(params, "device_id"),
                        first_query_value(params, "channel") or "a",
                        params,
                    )
                )
            else:
                self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except ValueError as err:
            self._write_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
        except Exception as err:
            LOGGER.exception("GrowCube ingress API POST failed: %s", self.path)
            self._write_json({"error": str(err)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug("Ingress API: " + format, *args)

    def _allow_request(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(address in network for network in ingress_allowed_networks())

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")

    def _read_json_body(self, max_bytes: int) -> dict[str, Any]:
        length = optional_int(self.headers.get("Content-Length")) or 0
        if length <= 0:
            raise ValueError("empty request body")
        if length > max_bytes:
            raise ValueError("request body too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as err:
            raise ValueError("invalid json body") from err
        if not isinstance(payload, dict):
            raise ValueError("json body must be an object")
        return payload

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        LOGGER.debug(
            "Ingress API response remote=%s path=%s status=%s bytes=%s",
            self.client_address[0],
            urlparse(self.path).path,
            int(status),
            len(body),
        )
        self.send_response(int(status))
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        LOGGER.debug(
            "Ingress UI response remote=%s path=%s status=%s bytes=%s",
            self.client_address[0],
            urlparse(self.path).path,
            int(status),
            len(body),
        )
        self.send_response(int(status))
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_text(self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._write_bytes(text.encode("utf-8"), content_type, status)

    def _write_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        LOGGER.debug(
            "Ingress static response remote=%s path=%s status=%s bytes=%s",
            self.client_address[0],
            urlparse(self.path).path,
            int(status),
            len(body),
        )
        self.send_response(int(status))
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def start_ingress_api_server() -> ThreadingHTTPServer:
    port = int(os.environ.get("GROWCUBE_INGRESS_PORT") or DEFAULT_INGRESS_PORT)
    server = ThreadingHTTPServer(("0.0.0.0", port), GrowCubeApiHandler)
    thread = threading.Thread(target=server.serve_forever, name="growcube-ingress-api", daemon=True)
    thread.start()
    LOGGER.info("GrowCube ingress API listening on port %s", port)
    return server


def first_query_value(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return str(values[0]).strip() if values else ""


def query_has(params: dict[str, list[str]], key: str) -> bool:
    return key in params


def query_bool(params: dict[str, list[str]], key: str, fallback: bool) -> bool:
    if not query_has(params, key):
        return fallback
    return first_query_value(params, key).lower() in {"1", "true", "yes", "on"}


def search_plants(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if len(query) < 2:
        return []
    cache_key = query.casefold()
    cached = _PLANT_SEARCH_CACHE.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < PLANT_SEARCH_CACHE_TTL_SECONDS:
        LOGGER.info("Plant search cache hit query=%r results=%s", query, len(cached[1]))
        return cached[1]
    LOGGER.info("Searching GrowCube cloud catalog for %r", query)
    try:
        data = fetch_catalog_json(f"/api/en/plants/name/{quote(query, safe='')}")
    except Exception:
        if cached is not None:
            LOGGER.warning("Plant search cloud failed; returning stale cache query=%r results=%s", query, len(cached[1]))
            return cached[1]
        raise
    plants = data.get("plants")
    if not isinstance(plants, list):
        LOGGER.warning("GrowCube cloud catalog returned no plants list for query=%r keys=%s", query, sorted(data.keys()))
        return []
    result = [plant_from_api(plant) for plant in plants[:CLOUD_CATALOG_LIMIT] if isinstance(plant, dict)]
    _PLANT_SEARCH_CACHE[cache_key] = (now, result)
    LOGGER.info("Plant search cache stored query=%r results=%s", query, len(result))
    return result


def fetch_plant_by_id(plant_id: int) -> dict[str, Any] | None:
    if plant_id <= 0:
        return None
    now = time.monotonic()
    cached = _PLANT_ID_CACHE.get(plant_id)
    if cached is not None and now - cached[0] < PLANT_SEARCH_CACHE_TTL_SECONDS:
        LOGGER.info("Plant id cache hit id=%s found=%s", plant_id, cached[1] is not None)
        return cached[1]
    data = fetch_catalog_json(f"/api/en/plants/id/{plant_id}")
    plants = data.get("plants")
    if not isinstance(plants, list):
        LOGGER.warning("GrowCube cloud catalog returned no plants list for id=%s keys=%s", plant_id, sorted(data.keys()))
        _PLANT_ID_CACHE[plant_id] = (now, None)
        return None
    for plant in plants:
        if isinstance(plant, dict) and optional_int(plant.get("id")) == plant_id:
            result = plant_from_api(plant)
            _PLANT_ID_CACHE[plant_id] = (now, result)
            return result
    _PLANT_ID_CACHE[plant_id] = (now, None)
    return None


def apply_catalog_plant_profile(config: ChannelConfig, plant: dict[str, Any], force: bool = False) -> list[str]:
    changed: list[str] = []

    def set_text(field: str, value: Any, limit: int) -> None:
        text = str_or_empty(value).strip()[:limit]
        if text and (force or not str(getattr(config, field) or "").strip()):
            if getattr(config, field) != text:
                setattr(config, field, text)
                changed.append(field)

    def set_int(field: str, value: Any, minimum: int, maximum: int) -> None:
        parsed = optional_int(value)
        if parsed is None:
            return
        parsed = max(minimum, min(maximum, parsed))
        current = int(getattr(config, field) or 0)
        if force or current == 0:
            if current != parsed:
                setattr(config, field, parsed)
                changed.append(field)

    set_text("plant_name", plant.get("display_name") or plant.get("name"), 64)
    set_text("photo_url", plant.get("image_url"), 512)
    set_text("type_category", plant.get("category"), 128)
    set_text("type_description", plant.get("description"), 10000)
    set_int("temp_min", plant.get("temp_min"), -50, 100)
    set_int("temp_max", plant.get("temp_max"), -50, 100)
    set_int("air_humidity_min", plant.get("air_humidity_min"), 0, 100)
    set_int("air_humidity_max", plant.get("air_humidity_max"), 0, 100)
    return changed


def discovery_networks(network_value: str) -> list[ipaddress.IPv4Network]:
    text = str(network_value or "").strip()
    if text:
        network = ipaddress.ip_network(text, strict=False)
        if network.version != 4:
            raise ValueError("Only IPv4 discovery networks are supported")
        return [limit_discovery_network(network)]
    networks = local_ipv4_networks()
    if not networks:
        raise ValueError("network is required when no local IPv4 network can be detected")
    return networks


def local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        addresses = []
    for _family, _type, _proto, _canon, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_loopback and address.is_private:
            networks.append(ipaddress.ip_network(f"{address}/24", strict=False))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.connect(("8.8.8.8", 80))
            address = ipaddress.ip_address(udp_socket.getsockname()[0])
            if not address.is_loopback and address.is_private:
                networks.append(ipaddress.ip_network(f"{address}/24", strict=False))
    except OSError:
        pass
    return list(dict.fromkeys(networks))


def limit_discovery_network(network: ipaddress.IPv4Network) -> ipaddress.IPv4Network:
    if network.num_addresses <= DISCOVERY_MAX_HOSTS + 2:
        return network
    first_host = next(network.hosts())
    return ipaddress.ip_network(f"{first_host}/24", strict=False)


async def tcp_port_open(host: str, port: int, timeout_seconds: float) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_seconds)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


async def probe_growcube_device(host: str, time_provider: Callable[[], Any]) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    device_future: asyncio.Future[DeviceVersionReport] = loop.create_future()

    def on_report(report: Report) -> None:
        if isinstance(report, DeviceVersionReport) and not device_future.done():
            device_future.set_result(report)

    client = GrowCubeClient(host, 8800, on_report=on_report, time_provider=time_provider)
    ok, error = await client.connect()
    if not ok:
        LOGGER.debug("GrowCube discovery probe failed host=%s error=%s", host, error)
        return None
    try:
        report = await asyncio.wait_for(device_future, timeout=DISCOVERY_DEVICE_TIMEOUT_SECONDS)
        return {
            "host": host,
            "port": 8800,
            "device_id": report.device_id,
            "version": report.version,
            "name": f"GrowCube {report.device_id}",
        }
    except (TimeoutError, asyncio.TimeoutError):
        return None
    finally:
        await client.disconnect()


def fetch_catalog_json(path: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for host in CLOUD_CATALOG_HOSTS:
        request = Request(
            f"{host}{path}",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "GrowCube/4.1",
            },
        )
        try:
            LOGGER.info("GrowCube cloud catalog request url=%s%s", host, path)
            started = time.monotonic()
            with urlopen(request, timeout=CLOUD_CATALOG_TIMEOUT_SECONDS) as response:
                body = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                data = json.loads(body.decode("utf-8"))
                LOGGER.info(
                    "GrowCube cloud catalog response url=%s%s status=%s bytes=%s elapsed_ms=%s keys=%s",
                    host,
                    path,
                    response.status,
                    len(body),
                    round((time.monotonic() - started) * 1000),
                    sorted(data.keys()) if isinstance(data, dict) else type(data).__name__,
                )
                return data if isinstance(data, dict) else {}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as err:
            LOGGER.warning("GrowCube cloud catalog request failed url=%s%s error=%s", host, path, err)
            last_error = err
    if last_error is not None:
        raise last_error
    return {}


def plant_from_api(plant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": optional_int(plant.get("id")) or 0,
        "name": str_or_empty(plant.get("name")),
        "display_name": str_or_empty(plant.get("display_name")),
        "category": str_or_empty(plant.get("category")),
        "description": str_or_empty(plant.get("description")).strip(),
        "image_url": normalize_catalog_image_url(plant.get("image")),
        "moisture_min": clamp_int(plant.get("min_soil_moist"), 0, 100, 30),
        "moisture_max": clamp_int(plant.get("max_soil_moist"), 0, 100, 60),
        "temp_min": optional_int(plant.get("min_temp")) or 0,
        "temp_max": optional_int(plant.get("max_temp")) or 0,
        "air_humidity_min": optional_int(plant.get("min_env_humid")) or 0,
        "air_humidity_max": optional_int(plant.get("max_env_humid")) or 0,
    }


def str_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def normalize_catalog_image_url(value: Any) -> str:
    text = str_or_empty(value).strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return f"https://{text[len('http://'):]}"
    if text.startswith("https://"):
        return text
    if text.startswith("/"):
        return f"https://api.growcube.cc{text}"
    return f"https://api.growcube.cc/{text}"


def dashboard_device_entities(device_id: str) -> dict[str, str]:
    return {
        "temperature": entity_id("sensor", device_id, "temperature"),
        "humidity": entity_id("sensor", device_id, "humidity"),
        "connection_problem": entity_id("binary_sensor", device_id, "connection_problem"),
        "water_warning": entity_id("binary_sensor", device_id, "water_warning"),
        "device_locked": entity_id("binary_sensor", device_id, "device_locked"),
        "tank_remaining": entity_id("sensor", device_id, "tank_remaining"),
        "tank_level": entity_id("sensor", device_id, "tank_level"),
        "tank_days_left": entity_id("sensor", device_id, "tank_days_left"),
        "firmware_update_status": entity_id("sensor", device_id, "firmware_update_status"),
        "tank_capacity": entity_id("number", device_id, "tank_capacity"),
        "mark_tank_full": entity_id("button", device_id, "mark_tank_full"),
        "reset_network": entity_id("button", device_id, "reset_network"),
        "update_firmware": entity_id("button", device_id, "update_firmware"),
    }


def dashboard_channel_entities(device_id: str, channel: str) -> dict[str, str]:
    return {
        "name": entity_id("text", device_id, f"plant_name_{channel}"),
        "photo_url": entity_id("text", device_id, f"plant_photo_url_{channel}"),
        "plant_configured": entity_id("binary_sensor", device_id, f"plant_{channel}_configured"),
        "moisture": entity_id("sensor", device_id, f"moisture_{channel}"),
        "last_watering": entity_id("sensor", device_id, f"last_watering_{channel}"),
        "history_count": entity_id("sensor", device_id, f"history_count_{channel}"),
        "next_watering": entity_id("sensor", device_id, f"next_watering_{channel}"),
        "mode": entity_id("select", device_id, f"watering_mode_{channel}"),
        "first_watering_time": entity_id("time", device_id, f"first_watering_time_{channel}"),
        "duration": entity_id("number", device_id, f"duration_seconds_{channel}"),
        "interval": entity_id("number", device_id, f"interval_hours_{channel}"),
        "smart_min_moisture": entity_id("number", device_id, f"smart_min_moisture_{channel}"),
        "smart_max_moisture": entity_id("number", device_id, f"smart_max_moisture_{channel}"),
        "smart_daytime_watering": entity_id("switch", device_id, f"smart_daytime_watering_{channel}"),
        "manual_duration": entity_id("number", device_id, f"manual_duration_seconds_{channel}"),
        "add_plant": entity_id("button", device_id, f"add_plant_{channel}"),
        "load_history": entity_id("button", device_id, f"load_history_{channel}"),
        "save": entity_id("button", device_id, f"save_schedule_{channel}"),
        "reset": entity_id("button", device_id, f"reset_plant_{channel}"),
        "water": entity_id("button", device_id, f"water_plant_{channel}"),
        "stop": entity_id("button", device_id, f"stop_watering_{channel}"),
        "outlet_blocked": entity_id("binary_sensor", device_id, f"outlet_{channel}_blocked"),
        "outlet_locked": entity_id("binary_sensor", device_id, f"outlet_{channel}_locked"),
        "sensor_fault": entity_id("binary_sensor", device_id, f"sensor_{channel}_fault"),
        "sensor_disconnected": entity_id("binary_sensor", device_id, f"sensor_{channel}_disconnected"),
        "watering_issue": entity_id("binary_sensor", device_id, f"watering_issue_{channel}"),
        "watering_locked": entity_id("binary_sensor", device_id, f"watering_locked_{channel}"),
    }


def entity_id(domain: str, device_id: str, key: str) -> str:
    return f"{domain}.growcube_{device_id}_{key}"


def mqtt_device_unique_id(device: dict[str, Any]) -> str:
    value = str(device.get("host") or device.get("device_id") or device.get("id") or "growcube")
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "growcube"


def dashboard_entity_states(
    device_id: str,
    device: dict[str, Any],
    entities: dict[str, str],
    channels: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}

    def add(entity_id_value: str, state: Any, **attributes: Any) -> None:
        if not entity_id_value:
            return
        if state is None:
            state = "unknown"
        elif isinstance(state, bool):
            state = "on" if state else "off"
        else:
            state = str(state)
        states[entity_id_value] = {"entity_id": entity_id_value, "state": state, "attributes": attributes}

    device_name = device.get("name") or device_id
    add(entities["temperature"], device.get("temperature"), unit_of_measurement="°C", friendly_name=f"{device_name} Temperature")
    add(entities["humidity"], device.get("humidity"), unit_of_measurement="%", friendly_name=f"{device_name} Humidity")
    add(entities["connection_problem"], not bool(device.get("connected")), friendly_name=f"{device_name} Connection problem")
    add(entities["water_warning"], bool(device.get("water_warning")), friendly_name=f"{device_name} Water warning")
    add(entities["device_locked"], bool(device.get("device_locked")), friendly_name=f"{device_name} Device locked")
    add(entities["tank_remaining"], device.get("tank_remaining_ml"), unit_of_measurement="mL")
    add(entities["tank_level"], device.get("tank_level"), unit_of_measurement="%")
    add(
        entities["tank_days_left"],
        device.get("tank_days_left"),
        unit_of_measurement="d",
        daily_usage_ml=device.get("tank_daily_usage_ml"),
        usable_remaining_ml=device.get("tank_usable_remaining_ml"),
        unusable_reserve_ml=device.get("tank_unusable_reserve_ml"),
        forecast=device.get("tank_forecast") or {},
    )
    add(entities["tank_capacity"], device.get("tank_capacity_ml"), unit_of_measurement="mL")
    add(
        entities["firmware_update_status"],
        device.get("firmware_update_status") or "idle",
        installed_version=device.get("version"),
        firmware_update_error=device.get("firmware_update_error") or "",
        firmware_update_started_at=device.get("firmware_update_started_at"),
    )
    add(entities["mark_tank_full"], "unknown")
    add(entities["reset_network"], "unknown")
    add(entities["update_firmware"], "unknown")

    device_channels = device.get("channels") or []
    for index, channel_key in enumerate("abcd"):
        channel = device_channels[index] if index < len(device_channels) and isinstance(device_channels[index], dict) else {}
        config = channel.get("config") or {}
        channel_entities = channels[channel_key]
        add(channel_entities["name"], config.get("plant_name") or "")
        add(channel_entities["photo_url"], config.get("photo_url") or "")
        add(channel_entities["plant_configured"], bool(channel.get("plant_configured")))
        add(channel_entities["moisture"], channel.get("moisture"), unit_of_measurement="%")
        add(channel_entities["last_watering"], channel.get("last_watering"))
        add(
            channel_entities["history_count"],
            channel.get("history_count") or 0,
            history_loading=bool(channel.get("history_loading")),
            history_complete=bool(channel.get("history_complete")),
            watering_events_complete=bool(channel.get("watering_events_complete")),
            history_points=channel.get("history_count") or 0,
            addon_api_url=device.get("addon_api_url") or "",
            history=channel.get("history") or [],
            watering_events=channel.get("watering_events") or [],
        )
        add(channel_entities["next_watering"], channel.get("next_watering"))
        add(channel_entities["mode"], config.get("mode") or "Disabled", options=["Disabled", "Repeating", "Smart"])
        add(channel_entities["first_watering_time"], config.get("first_watering_time") or "08:00:00")
        add(channel_entities["duration"], config.get("amount_ml") or config.get("duration_seconds") or 50, unit_of_measurement="mL")
        add(channel_entities["interval"], config.get("interval_hours") or 24, unit_of_measurement="h")
        add(channel_entities["smart_min_moisture"], config.get("smart_min_moisture") or 20, unit_of_measurement="%")
        add(channel_entities["smart_max_moisture"], config.get("smart_max_moisture") or 60, unit_of_measurement="%")
        add(channel_entities["smart_daytime_watering"], bool(config.get("smart_daytime_watering", True)))
        add(channel_entities["manual_duration"], config.get("manual_duration_seconds") or 50, unit_of_measurement="mL")
        for key in ("add_plant", "load_history", "save", "reset", "water", "stop"):
            add(channel_entities[key], "unknown")
        for key in ("outlet_blocked", "outlet_locked", "sensor_fault", "sensor_disconnected", "watering_issue", "watering_locked"):
            add(channel_entities[key], bool(channel.get(key)))

    return states


def validate_channel_key(value: str) -> int:
    text = str(value).strip().lower()
    if text in "abcd":
        return "abcd".index(text)
    if text.isdigit():
        return validate_channel(int(text))
    if text.endswith(("a", "b", "c", "d")):
        return "abcd".index(text[-1])
    raise ValueError("channel must be a, b, c, d, or 0-3")


def install_lovelace_card() -> None:
    if not CARD_SOURCE_PATH.is_file():
        LOGGER.warning("GrowCube Lovelace card source is missing: %s", CARD_SOURCE_PATH)
        return
    card_source = rendered_lovelace_card()
    card_version = lovelace_card_version(card_source)
    copied = False
    for target_path in CARD_TARGET_PATHS:
        mount_root = card_target_mount_root(target_path)
        if mount_root is not None and not mount_root.exists():
            LOGGER.info("Skipping GrowCube Lovelace card install path %s; mount root %s is not mounted", target_path, mount_root)
            continue
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(card_source, encoding="utf-8")
            LOGGER.info("GrowCube Lovelace card copied to %s", target_path)
            if card_version:
                versioned_target_path = target_path.with_name(f"growcube-card-{card_version}.js")
                versioned_target_path.write_text(card_source, encoding="utf-8")
                LOGGER.info("GrowCube Lovelace card copied to %s", versioned_target_path)
            else:
                LOGGER.warning("GrowCube Lovelace card version was not detected; versioned card copy skipped")
            if CARD_IMAGE_SOURCE_DIR.is_dir():
                shutil.copytree(
                    CARD_IMAGE_SOURCE_DIR,
                    target_path.parent / "images",
                    dirs_exist_ok=True,
                )
                LOGGER.info("GrowCube Lovelace card images copied to %s", target_path.parent / "images")
            copied = True
        except OSError as err:
            LOGGER.warning("Could not copy GrowCube Lovelace card to %s: %s", target_path, err)
    if not copied:
        LOGGER.warning("GrowCube Lovelace card was not installed; Home Assistant config directory is not mounted")


def card_target_mount_root(target_path: Path) -> Path | None:
    if not target_path.is_absolute() or len(target_path.parts) < 2:
        return None
    return Path(target_path.parts[0], target_path.parts[1])


def rendered_lovelace_card() -> str:
    source = CARD_SOURCE_PATH.read_text(encoding="utf-8")
    api_url = cached_supervisor_ingress_url()
    if api_url:
        LOGGER.debug("GrowCube Lovelace card will use ingress API %s", api_url)
    else:
        LOGGER.warning("GrowCube ingress URL was not discovered; card will use fallback API paths")
    return source.replace(CARD_API_URL_PLACEHOLDER, api_url)


def image_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def fetch_remote_image(url_value: str) -> tuple[bytes, str]:
    url = str(url_value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid image url")
    request = Request(
        url,
        headers={
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8",
            "User-Agent": "GrowCube/4.1",
        },
    )
    with urlopen(request, timeout=20) as response:
        content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
        if not content_type.startswith("image/"):
            raise ValueError("remote url is not an image")
        return response.read(), content_type


def check_growcube_firmware_update(current_version: str | None) -> dict[str, Any]:
    version = normalize_firmware_version(current_version)
    query_url = f"{FIRMWARE_UPDATE_CHECK_URL}?v={quote(version)}"
    request = Request(query_url, headers={"User-Agent": "GrowCubeAddon/0.2"}, method="GET")
    LOGGER.info("Checking GrowCube firmware update url=%s current=%s", query_url, version)
    try:
        with urlopen(request, timeout=FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            line = response.readline(2048).decode("utf-8", errors="replace").strip()
    except HTTPError as err:
        body_text = err.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware check failed: HTTP {err.code}: {body_text[:160]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware check failed: {err.reason}") from err
    if not line:
        raise RuntimeError("firmware check failed: empty server response")
    if line == FIRMWARE_LATEST_MESSAGE:
        return {
            "update_available": False,
            "current_version": current_version or "",
            "latest_version": current_version or "",
            "download_url": "",
            "message": "latest installed",
        }
    download_url = urljoin(FIRMWARE_UPDATE_CHECK_URL, line)
    parsed = urlparse(download_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.lower().endswith(".bin"):
        raise RuntimeError(f"firmware check failed: unexpected server response: {line[:160]}")
    latest_version = firmware_version_from_url(download_url) or ""
    return {
        "update_available": True,
        "current_version": current_version or "",
        "latest_version": latest_version,
        "download_url": download_url,
        "message": "update available",
    }


def normalize_firmware_version(version: str | None) -> str:
    text = str(version or "").strip()
    return text if text else "0"


def firmware_version_from_url(url: str) -> str | None:
    filename = Path(urlparse(url).path).name
    match = re.search(r"(?:^|_)V(\d+(?:\.\d+)*)(?:_|\.bin$)", filename, flags=re.IGNORECASE)
    return match.group(1) if match else None


def download_growcube_firmware_update(current_version: str | None) -> Path:
    info = check_growcube_firmware_update(current_version)
    if not info.get("update_available"):
        raise RuntimeError("device firmware is already up to date")
    download_url = str(info.get("download_url") or "")
    FIRMWARE_DOWNLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    request = Request(download_url, headers={"User-Agent": "GrowCubeAddon/0.2"}, method="GET")
    LOGGER.info(
        "Downloading GrowCube firmware url=%s latest=%s",
        download_url,
        info.get("latest_version") or "unknown",
    )
    try:
        with urlopen(request, timeout=FIRMWARE_DOWNLOAD_TIMEOUT_SECONDS) as response:
            body = response.read(FIRMWARE_MAX_BYTES + 1)
    except HTTPError as err:
        body_text = err.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware download failed: HTTP {err.code}: {body_text[:160]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware download failed: {err.reason}") from err
    if len(body) > FIRMWARE_MAX_BYTES:
        raise RuntimeError(f"firmware image is too large: {len(body)} bytes")
    if not body:
        raise RuntimeError("firmware download failed: empty file")
    FIRMWARE_DOWNLOAD_PATH.write_bytes(body)
    return validate_firmware_image(FIRMWARE_DOWNLOAD_PATH)


def validate_firmware_image(path: Path) -> Path:
    if not path.is_file():
        raise RuntimeError(f"firmware image not found: {path}")
    if path.suffix.lower() != ".bin":
        raise RuntimeError(f"firmware image must be a .bin file: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"firmware image is empty: {path.name}")
    if size > FIRMWARE_MAX_BYTES:
        raise RuntimeError(f"firmware image is too large: {size} bytes")
    return path


def firmware_image_path() -> Path:
    if FIRMWARE_DATA_IMAGE_PATH.is_file():
        return validate_firmware_image(FIRMWARE_DATA_IMAGE_PATH)
    if FIRMWARE_BUNDLED_IMAGE_PATH.is_file():
        return validate_firmware_image(FIRMWARE_BUNDLED_IMAGE_PATH)
    raise RuntimeError(
        "firmware image not found. Put growcube-local.bin into /data/firmware/growcube-local.bin"
    )


def upload_firmware_image(host: str, path: Path) -> dict[str, Any]:
    firmware = validate_firmware_image(path)
    boundary = f"----GrowCubeFirmware{uuid.uuid4().hex}"
    filename = "GrowCube-Software.bin"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n"
        "\r\n"
    ).encode("ascii")
    footer = f"\r\n--{boundary}--\r\n".encode("ascii")
    body = header + firmware.read_bytes() + footer
    url = f"http://{http_host(host)}/update"
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data;boundary={boundary}",
            "Content-Length": str(len(body)),
            "Connection": "close",
        },
        method="POST",
    )
    LOGGER.info("Uploading firmware to %s bytes=%s image=%s", url, firmware.stat().st_size, firmware.name)
    try:
        with urlopen(request, timeout=FIRMWARE_UPLOAD_TIMEOUT_SECONDS) as response:
            response_body = response.read(4096).decode("utf-8", errors="replace")
            return {
                "status": int(response.status),
                "firmware": firmware.name,
                "bytes": firmware.stat().st_size,
                "response": response_body[:240],
            }
    except HTTPError as err:
        body_text = err.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"firmware upload failed: HTTP {err.code}: {body_text[:240]}") from err
    except URLError as err:
        raise RuntimeError(f"firmware upload failed: {err.reason}") from err


def http_host(host: str) -> str:
    text = str(host or "").strip()
    if ":" in text and not text.startswith("["):
        return f"[{text}]"
    return text


def save_uploaded_plant_photo(payload: dict[str, Any]) -> dict[str, Any]:
    content_type = str(payload.get("content_type") or "").split(";", 1)[0].strip().lower()
    extension_by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    suffix = extension_by_type.get(content_type)
    if suffix is None:
        raise ValueError("photo must be JPEG, PNG, or WebP")
    raw_data = str(payload.get("data") or "")
    if "," in raw_data and raw_data.lower().startswith("data:"):
        raw_data = raw_data.split(",", 1)[1]
    try:
        body = base64.b64decode(raw_data, validate=True)
    except (ValueError, binascii.Error) as err:
        raise ValueError("invalid photo data") from err
    if not body:
        raise ValueError("empty photo")
    if len(body) > 1024 * 1024:
        raise ValueError("photo must be 1 MB or smaller")
    PLANT_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    photo_name = f"{uuid.uuid4().hex}{suffix}"
    photo_path = PLANT_PHOTO_DIR / photo_name
    photo_path.write_bytes(body)
    LOGGER.info("Saved uploaded plant photo path=%s bytes=%s content_type=%s", photo_path, len(body), content_type)
    return {
        "url": f"/plant_photos/{photo_name}",
        "content_type": content_type,
        "bytes": len(body),
    }


def lovelace_card_version(card_source: str) -> str:
    match = re.search(r'GROWCUBE_CARD_VERSION\s*=\s*["\'](\d+\.\d+\.\d+)', card_source)
    return match.group(1) if match else ""


def cached_supervisor_ingress_url() -> str:
    global _SUPERVISOR_INGRESS_URL_CACHE
    if _SUPERVISOR_INGRESS_URL_CACHE is None:
        _SUPERVISOR_INGRESS_URL_CACHE = supervisor_ingress_url()
    return _SUPERVISOR_INGRESS_URL_CACHE


def supervisor_ingress_url() -> str:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return ""
    request = Request(
        "http://supervisor/addons/self/info",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as err:
        LOGGER.warning("Could not query Supervisor add-on info: %s", err)
        return ""

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ""
    return normalize_ingress_url(
        data.get("ingress_url")
        or data.get("ingress_entry")
        or data.get("ingress_path")
        or ""
    )


def homeassistant_time_zone() -> str:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return ""
    request = Request(
        "http://supervisor/core/api/config",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as err:
        LOGGER.warning("Could not query Home Assistant config for time zone: %s", err)
        return ""

    time_zone = str(payload.get("time_zone") or "").strip() if isinstance(payload, dict) else ""
    if not time_zone:
        return ""
    try:
        ZoneInfo(time_zone)
    except ZoneInfoNotFoundError:
        LOGGER.warning("Home Assistant returned unknown time zone: %s", time_zone)
        return ""
    return time_zone


def fetch_network_utc_time() -> datetime | None:
    headers = {
        "User-Agent": "GrowCubeAddon/0.2",
        "Accept": "*/*",
    }
    for url in NETWORK_TIME_URLS:
        for method in ("HEAD", "GET"):
            request = Request(url, headers=headers, method=method)
            try:
                with urlopen(request, timeout=NETWORK_TIME_TIMEOUT_SECONDS) as response:
                    date_header = response.headers.get("Date", "")
                    if method == "GET":
                        response.read(1)
            except HTTPError as err:
                date_header = err.headers.get("Date", "") if err.headers is not None else ""
            except (URLError, TimeoutError, OSError) as err:
                LOGGER.warning("Could not fetch network time from %s with %s: %s", url, method, err)
                continue
            try:
                parsed = parsedate_to_datetime(date_header)
            except (TypeError, ValueError) as err:
                LOGGER.warning("Network time response from %s with %s had invalid Date header %r: %s", url, method, date_header, err)
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            value = parsed.astimezone(timezone.utc)
            LOGGER.info("Fetched network time from %s with %s: %s", url, method, value.isoformat(timespec="seconds"))
            return value
    return None


def normalize_ingress_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        return parsed.path.rstrip("/")
    if text.startswith("/"):
        return text.rstrip("/")
    if text.startswith("api/hassio_ingress/"):
        return f"/{text.rstrip('/')}"
    return f"/api/hassio_ingress/{text.strip('/')}"


def notification_signature(device: dict[str, Any]) -> tuple[str, ...]:
    parts: list[str] = []
    if not device.get("connected") and not device.get("connecting"):
        problem_since = parse_iso_datetime(device.get("connection_problem_since"))
        if problem_since is not None and datetime.now(timezone.utc) - problem_since >= timedelta(seconds=CONNECTION_NOTIFICATION_GRACE_SECONDS):
            parts.append("connection")
    if device.get("water_warning"):
        parts.append("water_warning")
    if device.get("device_locked"):
        parts.append("device_locked")
    for index, channel in enumerate(device.get("channels") or []):
        label = CHANNEL_NAMES[index] if index < len(CHANNEL_NAMES) else str(index)
        for key in ("outlet_blocked", "sensor_disconnected", "sensor_fault", "watering_issue", "watering_locked"):
            if channel.get(key):
                parts.append(f"{label}:{key}")
    return tuple(parts)


def sync_homeassistant_notifications(device: dict[str, Any], signature: tuple[str, ...]) -> None:
    device_id = mqtt_safe_id(str(device.get("host") or device.get("device_id") or device.get("id") or "growcube"))
    host = str(device.get("host") or "GrowCube")

    connection_id = f"growcube_{device_id}_connection_problem"
    if "connection" in signature:
        create_persistent_notification(
            connection_id,
            "GrowCube connection problem",
            f"GrowCube at {host} is disconnected. The add-on is retrying in the background.",
        )
    else:
        dismiss_persistent_notification(connection_id)

    alerts: list[str] = []
    if device.get("water_warning"):
        alerts.append("- Water tank is low")
    if device.get("device_locked"):
        alerts.append("- GrowCube is locked")

    for index, channel in enumerate(device.get("channels") or []):
        label = CHANNEL_NAMES[index] if index < len(CHANNEL_NAMES) else str(index)
        if channel.get("outlet_blocked"):
            alerts.append(f"- Channel {label}: pump stall or block detected")
        if channel.get("sensor_disconnected"):
            alerts.append(f"- Channel {label}: soil sensor is not connected")
        elif channel.get("sensor_fault"):
            alerts.append(f"- Channel {label}: sensor reported an exception")

        issue_id = f"growcube_{device_id}_watering_issue_{label.lower()}"
        locked_id = f"growcube_{device_id}_watering_locked_{label.lower()}"
        if channel.get("watering_locked"):
            dismiss_persistent_notification(issue_id)
            create_persistent_notification(
                locked_id,
                "GrowCube watering alert",
                f"Channel {label}: moisture did not rise after repeated smart watering.",
            )
        elif channel.get("watering_issue"):
            dismiss_persistent_notification(locked_id)
            create_persistent_notification(
                issue_id,
                "GrowCube watering alert",
                f"Channel {label}: moisture did not rise after smart watering.",
            )
        else:
            dismiss_persistent_notification(issue_id)
            dismiss_persistent_notification(locked_id)

    alerts_id = f"growcube_{device_id}_alerts"
    if alerts:
        create_persistent_notification(alerts_id, "GrowCube alerts", "\n".join(alerts))
    else:
        dismiss_persistent_notification(alerts_id)


def create_persistent_notification(notification_id: str, title: str, message: str) -> None:
    call_homeassistant_service(
        "persistent_notification",
        "create",
        {"notification_id": notification_id, "title": title, "message": message},
    )


def dismiss_persistent_notification(notification_id: str) -> None:
    call_homeassistant_service("persistent_notification", "dismiss", {"notification_id": notification_id})


def call_homeassistant_service(domain: str, service: str, payload: dict[str, Any]) -> None:
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return
    request = Request(
        f"http://supervisor/core/api/services/{domain}/{service}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5):
            return
    except (HTTPError, URLError, TimeoutError, OSError) as err:
        LOGGER.warning("Home Assistant service call failed %s.%s: %s", domain, service, err)


manager = GrowCubeManager()


def main() -> None:
    install_lovelace_card()
    manager.load()
    manager.start_loop()
    start_ingress_api_server()
    manager.mqtt_bridge = MqttBridge(mqtt_options(), manager.handle_mqtt_command)
    manager.submit(manager.mqtt_bridge.run_forever(manager.snapshot))
    LOGGER.info("GrowCube add-on bridge started")
    threading.Event().wait()


if __name__ == "__main__":
    main()
