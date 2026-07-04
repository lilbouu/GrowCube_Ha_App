"""GrowCube HAOS add-on backend bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from growcube_client import (
    DeviceVersionReport,
    GrowCubeClient,
    HistoryCompleteReport,
    MoistureHistoryReport,
    MoistureReport,
    PumpReport,
    Report,
    WaterStateReport,
    WateringRecordReport,
)
from mqtt_bridge import MqttBridge, MqttOptions

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("growcube-addon")

DATA_DIR = Path(os.environ.get("GROWCUBE_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "growcube_state.json"
OPTIONS_PATH = DATA_DIR / "options.json"
APP_DIR = Path(__file__).parent
CARD_SOURCE_PATH = APP_DIR / "www" / "growcube-card.js"
CARD_TARGET_PATHS = (
    Path("/homeassistant/www/growcube/growcube-card.js"),
    Path("/homeassistant_config/www/growcube/growcube-card.js"),
    Path("/config/www/growcube/growcube-card.js"),
)
CHANNEL_NAMES = ("A", "B", "C", "D")


@dataclass(slots=True)
class ChannelConfig:
    configured: bool = True
    plant_name: str = ""
    photo_url: str = ""
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
    plant_configured: bool = True
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
    device_id: str | None = None
    version: str | None = None
    temperature: int | None = None
    humidity: int | None = None
    water_warning: bool = False
    device_locked: bool = False
    tank_capacity_ml: int = 1500
    tank_remaining_ml: int = 1500
    tank_used_ml: int = 0
    updated_at: str | None = None
    channels: list[ChannelState] = field(default_factory=lambda: [ChannelState() for _ in range(4)])


class DeviceRuntime:
    def __init__(self, manager: "GrowCubeManager", state: DeviceState) -> None:
        self.manager = manager
        self.state = state
        self.client: GrowCubeClient | None = None
        self.task: asyncio.Task | None = None

    async def connect(self) -> None:
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
        )
        self.client = client
        ok, error = await client.connect()
        if not ok:
            async with self.manager.async_lock:
                self.state.connected = False
                self.state.connecting = False
                self.state.error = error
                self.manager.touch_locked(self.state)

    async def disconnect(self) -> None:
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
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mqtt_bridge: MqttBridge | None = None

    def load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        stored = self._read_json(STATE_PATH, {})
        options = self._read_json(OPTIONS_PATH, {})

        devices: list[dict[str, Any]] = []
        if isinstance(stored.get("devices"), list):
            devices.extend(stored["devices"])
        if isinstance(options.get("devices"), list):
            for item in options["devices"]:
                if isinstance(item, dict):
                    devices.append(item)

        with self.lock:
            for item in devices:
                state = self._state_from_dict(item)
                if state.host:
                    self.devices[state.id] = state
            LOGGER.info("Loaded %s GrowCube device(s) from add-on configuration", len(self.devices))

    def start_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self._run_loop, name="growcube-loop", daemon=True)
        thread.start()
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

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "devices": [self._state_to_dict(device) for device in self.devices.values()],
                "channels": CHANNEL_NAMES,
                "now": now_iso(),
            }

    async def add_device(self, name: str, host: str, port: int = 8800) -> dict[str, Any]:
        host = host.strip()
        if not host:
            raise ValueError("host is required")
        state = DeviceState(
            id=str(uuid.uuid4()),
            name=name.strip() or host,
            host=host,
            port=max(1, min(65535, int(port or 8800))),
        )
        async with self.async_lock:
            self.devices[state.id] = state
            self.save_locked()
        await self.connect(state.id)
        return self._state_to_dict(state)

    async def remove_device(self, device_id: str) -> None:
        runtime = self.runtimes.pop(device_id, None)
        if runtime is not None:
            await runtime.disconnect()
        async with self.async_lock:
            self.devices.pop(device_id, None)
            self.save_locked()

    async def connect(self, device_id: str) -> None:
        state = self.devices.get(device_id)
        if state is None:
            raise KeyError(device_id)
        runtime = self.runtimes.get(device_id)
        if runtime is None:
            runtime = DeviceRuntime(self, state)
            self.runtimes[device_id] = runtime
        await runtime.connect()

    async def water(self, device_id: str, channel: int, duration: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        await runtime.client.water(validate_channel(channel), max(1, min(60, int(duration))))

    async def stop_watering(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        await runtime.client.close_pump(validate_channel(channel))

    async def request_history(self, device_id: str, channel: int) -> None:
        runtime = self.runtimes.get(device_id)
        if runtime is None or runtime.client is None or not runtime.client.connected:
            raise RuntimeError("device is not connected")
        channel = validate_channel(channel)
        async with self.async_lock:
            state = self.devices[device_id]
            state.channels[channel].history_loading = True
            state.channels[channel].history_complete = False
            state.channels[channel].watering_events_complete = False
            self.touch_locked(state)
        await runtime.client.request_history(channel)

    async def handle_mqtt_command(self, device_key: str, topic_key: str, payload: str) -> None:
        state = self.find_device(device_key)
        if state is None:
            LOGGER.warning("Ignoring MQTT command for unknown GrowCube device %s", device_key)
            return

        if topic_key != "command":
            await self.handle_entity_command(state, topic_key, payload)
            return

        action, _, raw_channel = payload.partition("_")
        if action not in {"water", "stop", "history"} or not raw_channel.isdigit():
            LOGGER.warning("Ignoring unsupported MQTT command payload %s", payload)
            return

        channel = validate_channel(int(raw_channel))
        if action == "water":
            await self.water(state.id, channel, 7)
        elif action == "stop":
            await self.stop_watering(state.id, channel)
        elif action == "history":
            await self.request_history(state.id, channel)

    async def handle_entity_command(self, state: DeviceState, key: str, payload: str) -> None:
        channel = channel_from_key(key)
        async with self.async_lock:
            if key == "tank_capacity":
                state.tank_capacity_ml = clamp_int(payload, 500, 50000, state.tank_capacity_ml)
                state.tank_remaining_ml = min(state.tank_remaining_ml, state.tank_capacity_ml)
            elif key == "mark_tank_full":
                state.tank_remaining_ml = state.tank_capacity_ml
                state.tank_used_ml = 0
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
                elif key.startswith("plant_photo_url_"):
                    config.photo_url = payload.strip()[:512]
                elif key.startswith("watering_mode_"):
                    config.mode = payload if payload in {"Disabled", "Repeating", "Smart"} else "Disabled"
                elif key.startswith("manual_duration_seconds_"):
                    config.manual_duration_seconds = clamp_int(payload, 30, 150, config.manual_duration_seconds)
                elif key.startswith("duration_seconds_"):
                    config.duration_seconds = clamp_int(payload, 10, 500, config.duration_seconds)
                    config.amount_ml = config.duration_seconds
                elif key.startswith("interval_hours_"):
                    config.interval_hours = clamp_int(payload, 1, 240, config.interval_hours)
                elif key.startswith("first_watering_time_"):
                    config.first_watering_time = normalize_time(payload, config.first_watering_time)
                elif key.startswith("smart_min_moisture_"):
                    config.smart_min_moisture = clamp_int(payload, 1, max(1, config.smart_max_moisture - 1), config.smart_min_moisture)
                elif key.startswith("smart_max_moisture_"):
                    config.smart_max_moisture = clamp_int(payload, min(99, config.smart_min_moisture + 1), 99, config.smart_max_moisture)
                elif key.startswith("smart_daytime_watering_"):
                    config.smart_daytime_watering = payload.upper() in {"ON", "TRUE", "1"}
                elif key.startswith("add_plant_"):
                    state.channels[channel].plant_configured = True
                    config.configured = True
                elif key.startswith("reset_plant_"):
                    state.channels[channel].plant_configured = False
                    config.configured = False
                    config.plant_name = ""
                    config.photo_url = ""
                    config.mode = "Disabled"
                else:
                    LOGGER.warning("Ignoring unsupported MQTT entity command %s", key)
                    return
            else:
                LOGGER.warning("Ignoring unsupported MQTT entity command %s", key)
                return
            self.touch_locked(state)

        if channel is not None and key.startswith("water_plant_"):
            await self.water(state.id, channel, state.channels[channel].config.manual_duration_seconds)
        elif channel is not None and key.startswith("stop_watering_"):
            await self.stop_watering(state.id, channel)
        elif channel is not None and key.startswith("load_history_"):
            await self.request_history(state.id, channel)

    def find_device(self, device_key: str) -> DeviceState | None:
        with self.lock:
            for state in self.devices.values():
                if device_key in {
                    mqtt_safe_id(state.id),
                    mqtt_safe_id(state.device_id or ""),
                    mqtt_safe_id(state.host),
                }:
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
            self.touch_locked(state)

    async def handle_disconnected(self, device_id: str) -> None:
        async with self.async_lock:
            state = self.devices.get(device_id)
            if state is None:
                return
            state.connected = False
            state.connecting = False
            self.touch_locked(state)

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
                if report.humidity is not None:
                    state.humidity = report.humidity
                if report.temperature is not None:
                    state.temperature = report.temperature
            elif isinstance(report, PumpReport) and 0 <= report.channel < len(state.channels):
                state.channels[report.channel].pump_open = report.open
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
                            tzinfo=timezone.utc,
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
                timestamp = report.timestamp.replace(tzinfo=timezone.utc).isoformat()
                channel.last_watering = timestamp
                if all(item.get("timestamp") != timestamp for item in channel.watering_events):
                    channel.watering_events.append({"timestamp": timestamp})
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

            self.touch_locked(state)

    def touch_locked(self, state: DeviceState) -> None:
        state.updated_at = now_iso()
        self.save_locked()
        bridge = self.mqtt_bridge
        if bridge is not None and self.loop is not None:
            self.loop.create_task(bridge.publish_device(self._state_to_dict(state)))

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
    def _state_from_dict(item: dict[str, Any]) -> DeviceState:
        channels = []
        for raw_channel in item.get("channels", [])[:4]:
            channel = ChannelState()
            if isinstance(raw_channel, dict):
                channel.moisture = optional_int(raw_channel.get("moisture"))
                channel.pump_open = bool(raw_channel.get("pump_open", False))
                channel.last_watering = raw_channel.get("last_watering")
                channel.plant_configured = bool(raw_channel.get("plant_configured", True))
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
                    channel.config = ChannelConfig(
                        configured=bool(config.get("configured", channel.plant_configured)),
                        plant_name=str(config.get("plant_name") or ""),
                        photo_url=str(config.get("photo_url") or ""),
                        mode=str(config.get("mode") or "Disabled"),
                        manual_duration_seconds=clamp_int(config.get("manual_duration_seconds"), 30, 150, 50),
                        duration_seconds=clamp_int(config.get("duration_seconds"), 10, 500, 10),
                        amount_ml=clamp_int(config.get("amount_ml"), 10, 500, 50),
                        interval_hours=clamp_int(config.get("interval_hours"), 1, 240, 24),
                        first_watering_time=normalize_time(config.get("first_watering_time"), "08:00:00"),
                        smart_min_moisture=clamp_int(config.get("smart_min_moisture"), 1, 98, 20),
                        smart_max_moisture=clamp_int(config.get("smart_max_moisture"), 2, 99, 60),
                        smart_daytime_watering=bool(config.get("smart_daytime_watering", True)),
                    )
            channels.append(channel)
        while len(channels) < 4:
            channels.append(ChannelState())

        return DeviceState(
            id=str(item.get("id") or uuid.uuid4()),
            name=str(item.get("name") or item.get("host") or "GrowCube"),
            host=str(item.get("host") or ""),
            port=max(1, min(65535, int(item.get("port") or 8800))),
            device_id=item.get("device_id"),
            version=item.get("version"),
            temperature=optional_int(item.get("temperature")),
            humidity=optional_int(item.get("humidity")),
            water_warning=bool(item.get("water_warning", False)),
            device_locked=bool(item.get("device_locked", False)),
            tank_capacity_ml=clamp_int(item.get("tank_capacity_ml"), 500, 50000, 1500),
            tank_remaining_ml=clamp_int(item.get("tank_remaining_ml"), 0, 50000, 1500),
            tank_used_ml=clamp_int(item.get("tank_used_ml"), 0, 50000, 0),
            updated_at=item.get("updated_at"),
            channels=channels,
        )

    @staticmethod
    def _state_to_dict(state: DeviceState) -> dict[str, Any]:
        return {
            "id": state.id,
            "name": state.name,
            "host": state.host,
            "port": state.port,
            "connected": state.connected,
            "connecting": state.connecting,
            "error": state.error,
            "device_id": state.device_id,
            "version": state.version,
            "temperature": state.temperature,
            "humidity": state.humidity,
            "water_warning": state.water_warning,
            "device_locked": state.device_locked,
            "tank_capacity_ml": state.tank_capacity_ml,
            "tank_remaining_ml": state.tank_remaining_ml,
            "tank_level": round(state.tank_remaining_ml / max(1, state.tank_capacity_ml) * 100),
            "tank_used_ml": state.tank_used_ml,
            "tank_days_left": None,
            "updated_at": state.updated_at,
            "channels": [
                {
                    "moisture": channel.moisture,
                    "pump_open": channel.pump_open,
                    "last_watering": channel.last_watering,
                    "next_watering": None,
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
                        "plant_name": channel.config.plant_name,
                        "photo_url": channel.config.photo_url,
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


def install_lovelace_card() -> None:
    if not CARD_SOURCE_PATH.is_file():
        LOGGER.warning("GrowCube Lovelace card source is missing: %s", CARD_SOURCE_PATH)
        return
    copied = False
    for target_path in CARD_TARGET_PATHS:
        if not target_path.parent.parent.exists():
            LOGGER.info("Skipping GrowCube Lovelace card install path %s; base directory is not mounted", target_path)
            continue
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CARD_SOURCE_PATH, target_path)
            LOGGER.info("GrowCube Lovelace card copied to %s", target_path)
            copied = True
        except OSError as err:
            LOGGER.warning("Could not copy GrowCube Lovelace card to %s: %s", target_path, err)
    if not copied:
        LOGGER.warning("GrowCube Lovelace card was not installed; Home Assistant config directory is not mounted")


manager = GrowCubeManager()


def main() -> None:
    install_lovelace_card()
    manager.load()
    manager.start_loop()
    manager.mqtt_bridge = MqttBridge(mqtt_options(), manager.handle_mqtt_command)
    manager.submit(manager.mqtt_bridge.run_forever(manager.snapshot))
    LOGGER.info("GrowCube add-on bridge started")
    threading.Event().wait()


if __name__ == "__main__":
    main()
