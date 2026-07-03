"""Standalone GrowCube HAOS add-on web application."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("growcube-addon")

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("GROWCUBE_DATA_DIR", "/data"))
STATE_PATH = DATA_DIR / "growcube_state.json"
OPTIONS_PATH = DATA_DIR / "options.json"
HTTP_PORT = int(os.environ.get("GROWCUBE_HTTP_PORT", "8099"))
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


class GrowCubeHandler(BaseHTTPRequestHandler):
    server_version = "GrowCubeAddon/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self.send_json(self.server.manager.snapshot())
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self.read_json_body()
            result = self.handle_api_post(parsed.path, body)
            self.send_json(result)
        except ValueError as err:
            self.send_json({"error": str(err)}, HTTPStatus.BAD_REQUEST)
        except KeyError:
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except RuntimeError as err:
            self.send_json({"error": str(err)}, HTTPStatus.CONFLICT)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        match = re.fullmatch(r"/api/devices/([^/]+)", parsed.path)
        if not match:
            self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        self.wait(self.server.manager.remove_device(match.group(1)))
        self.send_json({"ok": True})

    def handle_api_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        manager = self.server.manager
        if path == "/api/devices":
            device = self.wait(manager.add_device(str(body.get("name", "")), str(body.get("host", "")), int(body.get("port") or 8800)))
            return {"device": device}

        match = re.fullmatch(r"/api/devices/([^/]+)/(connect|water|stop|history)", path)
        if not match:
            raise KeyError(path)
        device_id, action = match.groups()
        if action == "connect":
            self.wait(manager.connect(device_id))
        elif action == "water":
            self.wait(manager.water(device_id, int(body.get("channel", 0)), int(body.get("duration", 7))))
        elif action == "stop":
            self.wait(manager.stop_watering(device_id, int(body.get("channel", 0))))
        elif action == "history":
            self.wait(manager.request_history(device_id, int(body.get("channel", 0))))
        return {"ok": True}

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        try:
            value = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as err:
            raise ValueError("invalid JSON") from err
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def wait(self, future):
        return manager.submit(future).result(timeout=30)

    def send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, request_path: str) -> None:
        path = unquote(request_path)
        if path in ("", "/"):
            path = "/index.html"
        normalized = posixpath.normpath(path).lstrip("/")
        target = STATIC_DIR / normalized
        if not target.is_file():
            target = STATIC_DIR / "index.html"
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)


class GrowCubeHTTPServer(ThreadingHTTPServer):
    manager: GrowCubeManager


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


manager = GrowCubeManager()


def main() -> None:
    manager.load()
    manager.start_loop()
    server = GrowCubeHTTPServer(("0.0.0.0", HTTP_PORT), GrowCubeHandler)
    server.manager = manager
    LOGGER.info("GrowCube add-on UI listening on port %s", HTTP_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()

