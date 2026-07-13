"""GrowCube ELEA protocol helpers for the HAOS add-on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

HEADER = "elea"
DELIMITER = "#"


@dataclass(frozen=True, slots=True)
class EleaMessage:
    command: int
    payload: str
    raw: str


def build_message(command: int, payload: str | None = None) -> bytes:
    if payload is None:
        return f"{HEADER}{command}".encode("ascii")
    return f"{HEADER}{command}#{len(payload)}#{payload}#".encode("ascii")


def parse_messages(buffer: bytearray) -> list[EleaMessage]:
    messages: list[EleaMessage] = []

    while True:
        try:
            text = buffer.decode("ascii")
        except UnicodeDecodeError:
            del buffer[:]
            return messages

        start = text.find(HEADER)
        if start < 0:
            del buffer[:]
            return messages
        if start > 0:
            del buffer[:start]
            continue

        parts = text[len(HEADER):].split(DELIMITER, 2)
        if len(parts) < 3:
            return messages

        command_text, length_text, _rest = parts
        try:
            command = int(command_text)
            payload_len = int(length_text)
        except ValueError:
            del buffer[: len(HEADER)]
            continue

        total_len = len(HEADER) + len(command_text) + 1 + len(length_text) + 1 + payload_len + 1
        if len(buffer) < total_len:
            return messages

        if buffer[total_len - 1:total_len] != DELIMITER.encode("ascii"):
            payload_start = len(HEADER) + len(command_text) + 1 + len(length_text) + 1
            payload_end = text.find(DELIMITER, payload_start)
            if payload_end < 0:
                return messages
            total_len = payload_end + 1

        raw = buffer[:total_len].decode("ascii")
        payload = raw.rsplit(DELIMITER, 2)[1]
        if len(payload) >= payload_len:
            messages.append(EleaMessage(command=command, payload=payload, raw=raw))
        del buffer[:total_len]


def channel_payload(channel: int) -> str:
    if channel < 0 or channel > 3:
        raise ValueError("channel must be 0-3")
    return str(channel)


def manual_watering_payload(channel: int, enabled: bool) -> str:
    return f"{channel_payload(channel)}@{1 if enabled else 0}"


def watering_mode_payload(channel: int, mode: int, first_value: int, second_value: int, plant_id: int = 0) -> str:
    return f"{channel_payload(channel)}@{mode}@{first_value}@{second_value}@{max(0, int(plant_id))}"


def scheduled_watering_payload(channel: int, duration: int, interval: int, start_time: datetime, plant_id: int = 0) -> str:
    return f"{channel_payload(channel)}@{duration}@{interval}@{growcube_local_epoch(start_time)}@{max(0, int(plant_id))}"


def time_sync_payload(value: datetime) -> str:
    return value.strftime("%Y@%m@%d@%H@%M@%S")


def growcube_local_epoch(value: datetime) -> int:
    if value.tzinfo is None:
        local_value = value
    else:
        local_value = value.astimezone()

    growcube_dt = datetime(
        local_value.year,
        local_value.month,
        local_value.day,
        local_value.hour,
        local_value.minute,
        local_value.second,
        tzinfo=timezone.utc,
    )
    return int(growcube_dt.timestamp())
