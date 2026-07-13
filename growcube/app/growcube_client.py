"""Async GrowCube TCP client used by the standalone add-on."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Awaitable, Callable

from growcube_protocol import (
    build_message,
    channel_payload,
    manual_watering_payload,
    parse_messages,
    time_sync_payload,
)

GROWCUBE_PORT = 8800
LOGGER = logging.getLogger("growcube-addon.client")
WATERING_SOURCE_BY_CODE = {
    1: "smart",
    2: "timed",
    3: "manual",
}


class Channel(IntEnum):
    A = 0
    B = 1
    C = 2
    D = 3


@dataclass(frozen=True, slots=True)
class Report:
    command: int
    raw: str


@dataclass(frozen=True, slots=True)
class WaterStateReport(Report):
    water_warning: bool


@dataclass(frozen=True, slots=True)
class DeviceVersionReport(Report):
    version: str
    device_id: str


@dataclass(frozen=True, slots=True)
class MoistureReport(Report):
    channel: int
    moisture: int
    humidity: int | None = None
    temperature: int | None = None


@dataclass(frozen=True, slots=True)
class PumpReport(Report):
    channel: int
    open: bool


@dataclass(frozen=True, slots=True)
class WateringExceptionReport(Report):
    channel: int


@dataclass(frozen=True, slots=True)
class OutletBlockedReport(Report):
    channel: int


@dataclass(frozen=True, slots=True)
class SensorDisconnectedReport(Report):
    channel: int


@dataclass(frozen=True, slots=True)
class LockStateReport(Report):
    locked: bool
    reason: int = 0


@dataclass(frozen=True, slots=True)
class WateringLockedReport(Report):
    channel: int


@dataclass(frozen=True, slots=True)
class MoistureHistoryReport(Report):
    channel: int
    year: int
    month: int
    day: int
    values: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WateringRecordReport(Report):
    channel: int
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ExtendedWateringRecordReport(Report):
    channel: int
    timestamp: datetime
    source: str


@dataclass(frozen=True, slots=True)
class HistoryCompleteReport(Report):
    channel: int
    success: bool
    history_kind: str


@dataclass(frozen=True, slots=True)
class TankStateReport(Report):
    remaining_ml: int
    capacity_ml: int
    used_ml: int


@dataclass(frozen=True, slots=True)
class TankForecastReport(Report):
    flags: int
    valid_days: int
    confidence: int
    smart_daily_x10: int
    manual_daily_x10: int
    unknown_daily_x10: int
    smart_events: int
    manual_events: int
    unknown_events: int
    today_smart_ml: int
    today_manual_ml: int
    today_unknown_ml: int


@dataclass(frozen=True, slots=True)
class DelayedTimedWateringStateReport(Report):
    channel: int
    mode: int
    enabled: bool
    duration_seconds: int
    interval_hours: int
    next_start_epoch: int
    smart_min_moisture: int = 0
    smart_max_moisture: int = 0
    plant_id: int = 0
    has_plant_id: bool = False


ReportCallback = Callable[[Report], Awaitable[None] | None]
ConnectionCallback = Callable[[], Awaitable[None] | None]


class Command:
    def __init__(self, command: int, payload: str | None = None) -> None:
        self.command = int(command)
        self.payload = payload

    def to_bytes(self) -> bytes:
        return build_message(self.command, self.payload)


class GrowCubeClient:
    def __init__(
        self,
        host: str,
        port: int = GROWCUBE_PORT,
        on_report: ReportCallback | None = None,
        on_connected: ConnectionCallback | None = None,
        on_disconnected: ConnectionCallback | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_report = on_report
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._manual_tasks: set[asyncio.Task] = set()
        self._disconnecting = False

    async def connect(self) -> tuple[bool, str]:
        if self.connected:
            return True, ""
        self._disconnecting = False
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except OSError as err:
            return False, str(err)

        self.connected = True
        self._read_task = asyncio.create_task(self._read_loop())
        await _maybe_call(self.on_connected)
        await self.send(Command(44, time_sync_payload(datetime.now())))
        await self.send(Command(52, ""))
        await self.send(Command(54, ""))
        await self.send(Command(55, "v3"))
        return True, ""

    async def disconnect(self) -> None:
        self._disconnecting = True
        self.connected = False
        for task in list(self._manual_tasks):
            task.cancel()
        self._manual_tasks.clear()
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                LOGGER.debug("GrowCube disconnect ignored socket close error for %s:%s", self.host, self.port)
        self._reader = None
        self._writer = None

    async def send(self, command: Command | bytes) -> None:
        if self._writer is None or self._writer.is_closing():
            return
        data = command.to_bytes() if isinstance(command, Command) else command
        text = data.decode("ascii", errors="replace")
        log_outgoing_command(self.host, self.port, command, text)
        self._writer.write(data)
        await self._writer.drain()

    async def request_history(self, channel: int) -> None:
        await self.send(Command(48, channel_payload(channel)))
        await self.send(Command(56, channel_payload(channel)))

    async def open_pump(self, channel: int) -> None:
        await self.send(Command(47, manual_watering_payload(channel, True)))

    async def close_pump(self, channel: int) -> None:
        await self.send(Command(47, manual_watering_payload(channel, False)))

    async def water(self, channel: int, duration: int) -> None:
        await self.open_pump(channel)
        task = asyncio.create_task(self._close_after(channel, duration))
        self._manual_tasks.add(task)
        task.add_done_callback(self._manual_tasks.discard)

    async def _close_after(self, channel: int, duration: int) -> None:
        await asyncio.sleep(max(1, int(duration)))
        await self.close_pump(channel)

    async def _read_loop(self) -> None:
        buffer = bytearray()
        try:
            while self._reader is not None:
                chunk = await self._reader.read(1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                for message in parse_messages(buffer):
                    if message.command not in {20, 21}:
                        LOGGER.info("GrowCube RX %s:%s %s", self.host, self.port, message.raw)
                    await _maybe_call(self.on_report, report_from_message(message.command, message.payload, message.raw))
        except asyncio.CancelledError:
            return
        finally:
            was_connected = self.connected
            self.connected = False
            if was_connected and not self._disconnecting:
                await _maybe_call(self.on_disconnected)


def report_from_message(command: int, payload: str, raw: str) -> Report:
    try:
        if command == 20:
            return WaterStateReport(command, raw, int(payload) == 0)
        if command == 21:
            parts = _split_ints(payload)
            if len(parts) >= 2:
                return MoistureReport(
                    command,
                    raw,
                    channel=parts[0],
                    moisture=parts[1],
                    humidity=parts[2] if len(parts) > 2 else None,
                    temperature=parts[3] if len(parts) > 3 else None,
                )
        if command == 22:
            fields = payload.split("@", 4)
            if len(fields) == 5:
                return MoistureHistoryReport(
                    command,
                    raw,
                    channel=int(fields[0]),
                    year=int(fields[1]),
                    month=int(fields[2]),
                    day=int(fields[3]),
                    values=tuple(_safe_int(value) for value in fields[4].split(",") if value != ""),
                )
        if command == 23:
            parts = _split_ints(payload)
            if len(parts) == 6:
                return WateringRecordReport(
                    command,
                    raw,
                    channel=parts[0],
                    timestamp=datetime(parts[1], parts[2], parts[3], parts[4], parts[5]),
                )
        if command == 56:
            parts = _split_ints(payload)
            if len(parts) == 7:
                return ExtendedWateringRecordReport(
                    command,
                    raw,
                    channel=parts[0],
                    timestamp=datetime(parts[1], parts[2], parts[3], parts[4], parts[5]),
                    source=WATERING_SOURCE_BY_CODE.get(parts[6], "last"),
                )
        if command == 24:
            fields = payload.split("@")
            version = fields[0] if fields else ""
            device_id = fields[1] if len(fields) > 1 else version
            return DeviceVersionReport(command, raw, version, device_id)
        if command in (26, 27):
            return PumpReport(command, raw, channel=int(payload), open=command == 26)
        if command == 28:
            return WateringExceptionReport(command, raw, channel=int(payload))
        if command == 29:
            return OutletBlockedReport(command, raw, channel=int(payload))
        if command == 30:
            return SensorDisconnectedReport(command, raw, channel=int(payload))
        if command == 33:
            parts = _split_ints(payload)
            if parts:
                return LockStateReport(
                    command,
                    raw,
                    locked=parts[0] == 1,
                    reason=parts[1] if len(parts) > 1 else 0,
                )
        if command == 34:
            return WateringLockedReport(command, raw, channel=int(payload))
        if command in (35, 36):
            parts = _split_ints(payload)
            if len(parts) >= 2:
                return HistoryCompleteReport(
                    command,
                    raw,
                    channel=parts[0],
                    success=parts[1] == 1,
                    history_kind="moisture" if command == 35 else "watering",
                )
        if command == 53:
            parts = _split_ints(payload)
            if len(parts) == 3 and parts[1] > 0:
                return TankStateReport(
                    command,
                    raw,
                    remaining_ml=max(0, min(parts[0], parts[1])),
                    capacity_ml=parts[1],
                    used_ml=max(0, min(parts[2], parts[1])),
                )
        if command == 54:
            parts = _split_ints(payload)
            if len(parts) == 13 and parts[0] == 1:
                return TankForecastReport(
                    command,
                    raw,
                    flags=max(0, min(parts[1], 255)),
                    valid_days=max(0, min(parts[2], 255)),
                    confidence=max(0, min(parts[3], 255)),
                    smart_daily_x10=max(0, parts[4]),
                    manual_daily_x10=max(0, parts[5]),
                    unknown_daily_x10=max(0, parts[6]),
                    smart_events=max(0, parts[7]),
                    manual_events=max(0, parts[8]),
                    unknown_events=max(0, parts[9]),
                    today_smart_ml=max(0, min(parts[10], 65535)),
                    today_manual_ml=max(0, min(parts[11], 65535)),
                    today_unknown_ml=max(0, min(parts[12], 65535)),
                )
        if command == 55:
            parts = _split_ints(payload)
            if len(parts) >= 6 and parts[0] in (2, 3):
                mode = parts[2]
                if mode in (0, 1, 2, 3):
                    return DelayedTimedWateringStateReport(
                        command,
                        raw,
                        channel=parts[1],
                        mode=mode,
                        enabled=mode != 0,
                        duration_seconds=max(0, parts[3]) if mode == 1 else 0,
                        interval_hours=max(0, parts[4]) if mode == 1 else 0,
                        next_start_epoch=max(0, parts[5]) if mode == 1 else 0,
                        smart_min_moisture=max(0, parts[3]) if mode in (2, 3) else 0,
                        smart_max_moisture=max(0, parts[4]) if mode in (2, 3) else 0,
                        plant_id=max(0, parts[6]) if parts[0] == 3 and len(parts) > 6 else 0,
                        has_plant_id=parts[0] == 3 and len(parts) > 6,
                    )
            if len(parts) == 5:
                return DelayedTimedWateringStateReport(
                    command,
                    raw,
                    channel=parts[0],
                    mode=1 if parts[1] == 1 else 0,
                    enabled=parts[1] == 1,
                    duration_seconds=max(0, parts[2]),
                    interval_hours=max(0, parts[3]),
                    next_start_epoch=max(0, parts[4]),
                )
    except (TypeError, ValueError):
        pass
    return Report(command, raw)


def log_outgoing_command(host: str, port: int, command: Command | bytes, text: str) -> None:
    if not isinstance(command, Command):
        LOGGER.debug("GrowCube TX %s:%s %s", host, port, text)
        return
    parts = command.payload.split("@") if command.payload else []
    if command.command == 49 and len(parts) >= 4:
        plant_id = parts[4] if len(parts) >= 5 else ""
        LOGGER.info(
            "GrowCube TX watering-mode host=%s:%s channel=%s mode=%s first=%s second=%s plant_id=%s raw=%s",
            host,
            port,
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            plant_id,
            text,
        )
        return
    if command.command == 51 and len(parts) >= 4:
        plant_id = parts[4] if len(parts) >= 5 else ""
        LOGGER.info(
            "GrowCube TX scheduled-watering host=%s:%s channel=%s duration_s=%s interval_h=%s epoch=%s plant_id=%s raw=%s",
            host,
            port,
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            plant_id,
            text,
        )
        return
    if command.command == 55:
        LOGGER.info("GrowCube TX watering-state-request host=%s:%s payload=%r raw=%s", host, port, command.payload, text)
        return
    LOGGER.debug("GrowCube TX %s:%s %s", host, port, text)


async def _maybe_call(callback, *args) -> None:
    if callback is None:
        return
    result = callback(*args)
    if hasattr(result, "__await__"):
        await result


def _split_ints(payload: str) -> list[int]:
    return [int(part) for part in payload.split("@") if part != ""]


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
