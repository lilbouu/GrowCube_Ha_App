"""GrowCube HAOS add-on backend bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
CHANNEL_NAMES = ("A", "B", "C", "D")


@dataclass(slots=True)
class ChannelState:
    moisture: int | None = None
    pump_open: bool = False
    history_loading: bool = False
    history_complete: bool = False
    watering_events_complete: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    watering_events: list[dict[str, Any]] = field(default_factory=list)


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

    async def handle_mqtt_command(self, device_key: str, _topic: str, payload: str) -> None:
        state = self.find_device(device_key)
        if state is None:
            LOGGER.warning("Ignoring MQTT command for unknown GrowCube device %s", device_key)
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
                channel.history = list(raw_channel.get("history") or [])[-24 * 30 :]
                channel.watering_events = list(raw_channel.get("watering_events") or [])[-128:]
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
            "updated_at": state.updated_at,
            "channels": [
                {
                    "moisture": channel.moisture,
                    "pump_open": channel.pump_open,
                    "history_loading": channel.history_loading,
                    "history_complete": channel.history_complete,
                    "watering_events_complete": channel.watering_events_complete,
                    "history": channel.history,
                    "watering_events": channel.watering_events,
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


manager = GrowCubeManager()


def main() -> None:
    manager.load()
    manager.start_loop()
    manager.mqtt_bridge = MqttBridge(mqtt_options(), manager.handle_mqtt_command)
    manager.submit(manager.mqtt_bridge.run_forever(manager.snapshot))
    LOGGER.info("GrowCube add-on bridge started")
    threading.Event().wait()


if __name__ == "__main__":
    main()
