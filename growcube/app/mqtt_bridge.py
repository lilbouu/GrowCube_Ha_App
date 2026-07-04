"""MQTT Discovery bridge for exposing GrowCube devices to Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

LOGGER = logging.getLogger("growcube-addon.mqtt")

CommandCallback = Callable[[str, str, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MqttOptions:
    host: str
    port: int
    username: str = ""
    password: str = ""
    client_id: str = "growcube-addon"


class MqttClient:
    """Small MQTT 3.1.1 client with QoS 0 publish/subscribe support."""

    def __init__(self, options: MqttOptions) -> None:
        self.options = options
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._packet_id = 0

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.options.host, self.options.port)
        flags = 0x02
        payload = _encode_string(self.options.client_id)
        if self.options.username:
            flags |= 0x80
            payload += _encode_string(self.options.username)
        if self.options.password:
            flags |= 0x40
            payload += _encode_string(self.options.password)

        variable_header = _encode_string("MQTT") + bytes([4, flags]) + struct.pack("!H", 0)
        self._write_packet(0x10, variable_header + payload)
        await self.writer.drain()
        packet_type, body = await self.read_packet()
        if packet_type != 0x20 or len(body) < 2 or body[1] != 0:
            raise RuntimeError(mqtt_connack_error(body))

    async def disconnect(self) -> None:
        if self.writer is None:
            return
        self._write_packet(0xE0, b"")
        await self.writer.drain()
        self.writer.close()
        await self.writer.wait_closed()
        self.reader = None
        self.writer = None

    async def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        header = _encode_string(topic) + payload.encode("utf-8")
        self._write_packet(0x31 if retain else 0x30, header)
        await self.writer.drain()

    async def subscribe(self, topic: str) -> None:
        self._packet_id = (self._packet_id % 65535) + 1
        body = struct.pack("!H", self._packet_id) + _encode_string(topic) + b"\x00"
        self._write_packet(0x82, body)
        await self.writer.drain()
        packet_type, _payload = await self.read_packet()
        if packet_type != 0x90:
            raise RuntimeError(f"MQTT subscribe failed for {topic}")

    async def read_packet(self) -> tuple[int, bytes]:
        if self.reader is None:
            raise RuntimeError("MQTT client is not connected")
        first = await self.reader.readexactly(1)
        remaining = await _read_remaining_length(self.reader)
        return first[0] & 0xF0, await self.reader.readexactly(remaining)

    def _write_packet(self, fixed_header: int, body: bytes) -> None:
        if self.writer is None:
            raise RuntimeError("MQTT client is not connected")
        self.writer.write(bytes([fixed_header]) + _encode_remaining_length(len(body)) + body)


class MqttBridge:
    def __init__(self, options: MqttOptions, on_command: CommandCallback) -> None:
        self.options = options
        self.on_command = on_command
        self.client: MqttClient | None = None
        self._published_discovery: set[str] = set()

    async def run_forever(self, snapshot_provider) -> None:
        while True:
            try:
                client = MqttClient(self.options)
                await client.connect()
                await client.subscribe("growcube/+/+/set")
                self.client = client
                await self.publish_all(snapshot_provider())
                LOGGER.info("Connected to MQTT at %s:%s", self.options.host, self.options.port)
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                LOGGER.warning("MQTT bridge disconnected: %s", err)
            finally:
                self.client = None
            await asyncio.sleep(10)

    async def _read_loop(self) -> None:
        assert self.client is not None
        while True:
            packet_type, body = await self.client.read_packet()
            if packet_type == 0x30:
                topic, payload = _decode_publish(body)
                parts = topic.split("/")
                if len(parts) == 4 and parts[0] == "growcube" and parts[3] == "set":
                    await self.on_command(parts[1], parts[2], payload)
            elif packet_type == 0xD0:
                continue

    async def publish_all(self, snapshot: dict) -> None:
        devices = snapshot.get("devices", [])
        LOGGER.info("Publishing MQTT Discovery for %s GrowCube device(s)", len(devices))
        for device in devices:
            await self.publish_device(device)

    async def publish_device(self, device: dict) -> None:
        if self.client is None:
            return
        unique_id = _device_unique_id(device)
        if unique_id not in self._published_discovery:
            LOGGER.info("Publishing MQTT Discovery configs for GrowCube device %s", unique_id)
            await self._publish_discovery(device, unique_id)
            self._published_discovery.add(unique_id)
        await self._publish_state(device, unique_id)

    async def _publish_discovery(self, device: dict, unique_id: str) -> None:
        assert self.client is not None
        base = f"growcube/{unique_id}"
        device_info = {
            "identifiers": [unique_id],
            "name": device.get("name") or "GrowCube",
            "manufacturer": "Elecrow",
            "model": "GrowCube",
            "sw_version": device.get("version"),
            "configuration_url": f"http://{device.get('host')}",
        }

        await self._sensor(unique_id, "temperature", "Temperature", base, device_info, "°C", "temperature", "{{ value_json.temperature }}")
        await self._sensor(unique_id, "humidity", "Humidity", base, device_info, "%", "humidity", "{{ value_json.humidity }}")
        await self._binary(unique_id, "connected", "Connected", base, device_info, "{{ 'ON' if value_json.connected else 'OFF' }}")
        await self._binary(unique_id, "water_warning", "Water warning", base, device_info, "{{ 'ON' if value_json.water_warning else 'OFF' }}", "moisture")

        for channel in range(4):
            channel_id = "abcd"[channel]
            channel_name = channel_id.upper()
            await self._sensor(
                unique_id,
                f"moisture_{channel_id}",
                f"Moisture {channel_name}",
                base,
                device_info,
                "%",
                "moisture",
                f"{{{{ value_json.channels[{channel}].moisture }}}}",
            )
            await self._binary(
                unique_id,
                f"pump_{channel_id}",
                f"Pump {channel_name}",
                base,
                device_info,
                f"{{{{ 'ON' if value_json.channels[{channel}].pump_open else 'OFF' }}}}",
                "running",
            )
            await self._button(
                unique_id,
                f"water_plant_{channel_id}",
                f"Water plant {channel_name}",
                base,
                device_info,
                f"water_{channel}",
                "mdi:watering-can",
            )
            await self._button(
                unique_id,
                f"stop_watering_{channel_id}",
                f"Stop watering {channel_name}",
                base,
                device_info,
                f"stop_{channel}",
                "mdi:water-off",
            )
            await self._button(
                unique_id,
                f"load_history_{channel_id}",
                f"Load history {channel_name}",
                base,
                device_info,
                f"history_{channel}",
                "mdi:chart-line",
            )

    async def _sensor(self, device_id: str, key: str, name: str, base: str, device: dict, unit: str, device_class: str, template: str) -> None:
        await self._config("sensor", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "unit_of_measurement": unit,
            "device_class": device_class,
            "availability_topic": f"{base}/availability",
            "device": device,
        })

    async def _binary(self, device_id: str, key: str, name: str, base: str, device: dict, template: str, device_class: str | None = None) -> None:
        config = {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability_topic": f"{base}/availability",
            "device": device,
        }
        if device_class:
            config["device_class"] = device_class
        await self._config("binary_sensor", device_id, key, config)

    async def _button(self, device_id: str, key: str, name: str, base: str, device: dict, payload: str, icon: str) -> None:
        await self._config("button", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "command_topic": f"{base}/command/set",
            "payload_press": payload,
            "availability_topic": f"{base}/availability",
            "icon": icon,
            "device": device,
        })

    async def _config(self, component: str, device_id: str, key: str, config: dict) -> None:
        assert self.client is not None
        config.setdefault("object_id", f"growcube_{device_id}_{key}")
        topic = f"homeassistant/{component}/growcube/{device_id}_{key}/config"
        await self.client.publish(topic, json.dumps(config, separators=(",", ":")), retain=True)

    async def _publish_state(self, device: dict, unique_id: str) -> None:
        assert self.client is not None
        base = f"growcube/{unique_id}"
        availability = "online" if device.get("connected") else "offline"
        await self.client.publish(f"{base}/availability", availability, retain=True)
        await self.client.publish(f"{base}/state", json.dumps(device, separators=(",", ":")), retain=True)
        LOGGER.info("Published MQTT state for GrowCube device %s (%s)", unique_id, availability)


def _device_unique_id(device: dict) -> str:
    value = str(device.get("host") or device.get("device_id") or device.get("id") or "growcube")
    return "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_") or "growcube"


def _encode_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("!H", len(data)) + data


def mqtt_connack_error(body: bytes) -> str:
    if len(body) < 2:
        return f"MQTT connection failed: malformed CONNACK {body.hex()}"
    reason = {
        1: "unacceptable protocol version",
        2: "identifier rejected",
        3: "server unavailable",
        4: "bad username or password",
        5: "not authorized",
    }.get(body[1], f"unknown return code {body[1]}")
    hint = ""
    if body[1] in (4, 5):
        hint = "; set mqtt_username/mqtt_password in the GrowCube add-on configuration"
    return f"MQTT connection failed: {reason} ({body.hex()}){hint}"


def _encode_remaining_length(value: int) -> bytes:
    encoded = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value > 0:
            digit |= 0x80
        encoded.append(digit)
        if value == 0:
            return bytes(encoded)


async def _read_remaining_length(reader: asyncio.StreamReader) -> int:
    multiplier = 1
    value = 0
    while True:
        digit = (await reader.readexactly(1))[0]
        value += (digit & 127) * multiplier
        if digit & 128 == 0:
            return value
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise RuntimeError("Malformed MQTT remaining length")


def _decode_publish(body: bytes) -> tuple[str, str]:
    topic_len = struct.unpack("!H", body[:2])[0]
    topic = body[2:2 + topic_len].decode("utf-8")
    payload = body[2 + topic_len:].decode("utf-8")
    return topic, payload
