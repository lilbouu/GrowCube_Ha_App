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
        self._pending_devices: dict[str, dict] = {}

    async def run_forever(self, snapshot_provider) -> None:
        while True:
            try:
                client = MqttClient(self.options)
                await client.connect()
                await client.subscribe("growcube/+/+/set")
                self.client = client
                await self.publish_all(snapshot_provider())
                if self._pending_devices:
                    LOGGER.info("Clearing %s queued MQTT device publish(es) after full publish", len(self._pending_devices))
                    self._pending_devices.clear()
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
        client = self.client
        assert client is not None
        while True:
            packet_type, body = await client.read_packet()
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

    async def publish_device(self, device: dict, raise_on_error: bool = True) -> None:
        unique_id = _device_unique_id(device)
        if self.client is None:
            self._queue_device(device, unique_id, "until MQTT reconnects")
            return
        try:
            if unique_id not in self._published_discovery:
                LOGGER.info("Publishing MQTT Discovery configs for GrowCube device %s", unique_id)
                await self._publish_discovery(device, unique_id)
                self._published_discovery.add(unique_id)
            await self._publish_state(device, unique_id)
            self._pending_devices.pop(unique_id, None)
        except Exception as err:
            self._queue_device(device, unique_id, f"after publish failure: {err}")
            self._drop_client()
            if raise_on_error:
                raise

    def _queue_device(self, device: dict, unique_id: str, reason: str) -> None:
        self._pending_devices[unique_id] = dict(device)
        LOGGER.debug("Queued MQTT Discovery publish for GrowCube device %s %s", unique_id, reason)

    def _drop_client(self) -> None:
        client = self.client
        self.client = None
        if client is not None and client.writer is not None:
            client.writer.close()

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

        await self._clear_legacy_configs(unique_id)
        await self._sensor(unique_id, "temperature", "Temperature", base, device_info, "°C", "temperature", "{{ value_json.temperature }}")
        await self._sensor(unique_id, "humidity", "Humidity", base, device_info, "%", "humidity", "{{ value_json.humidity }}")
        await self._sensor(unique_id, "tank_remaining", "Tank remaining", base, device_info, "mL", None, "{{ value_json.tank_remaining_ml }}", "mdi:cup-water")
        await self._sensor(unique_id, "tank_level", "Tank level", base, device_info, "%", None, "{{ value_json.tank_level }}", "mdi:water-percent")
        await self._sensor(unique_id, "tank_used", "Tank used", base, device_info, "mL", None, "{{ value_json.tank_used_ml }}", "mdi:water-minus")
        await self._sensor(
            unique_id,
            "tank_days_left",
            "Tank days left",
            base,
            device_info,
            "d",
            None,
            "{{ value_json.tank_days_left }}",
            "mdi:calendar-range",
            (
                "{"
                "\"daily_usage_ml\":{{ value_json.tank_daily_usage_ml | tojson }},"
                "\"usable_remaining_ml\":{{ value_json.tank_usable_remaining_ml | tojson }},"
                "\"unusable_reserve_ml\":{{ value_json.tank_unusable_reserve_ml | tojson }},"
                "\"forecast\":{{ value_json.tank_forecast | tojson }}"
                "}"
            ),
        )
        await self._binary(unique_id, "connection_problem", "Connection problem", base, device_info, "{{ 'ON' if not value_json.connected else 'OFF' }}", "problem", "mdi:wifi-alert", "diagnostic")
        await self._binary(unique_id, "device_locked", "Device locked", base, device_info, "{{ 'ON' if value_json.device_locked else 'OFF' }}", "problem", None, "diagnostic")
        await self._binary(unique_id, "water_warning", "Water warning", base, device_info, "{{ 'ON' if value_json.water_warning else 'OFF' }}", "problem", "mdi:water-alert", "diagnostic")
        await self._number(unique_id, "tank_capacity", "Tank capacity", base, device_info, 500, 50000, 50, "mL", "mdi:cup-water", "{{ value_json.tank_capacity_ml }}")
        await self._button(unique_id, "mark_tank_full", "Mark tank full", base, device_info, "mark_tank_full", "mdi:cup-water")

        for channel in range(4):
            channel_id = "abcd"[channel]
            channel_name = channel_id.upper()
            channel_base = f"value_json.channels[{channel}]"
            await self._sensor(
                unique_id,
                f"moisture_{channel_id}",
                f"Moisture {channel_name}",
                base,
                device_info,
                "%",
                "moisture",
                f"{{{{ {channel_base}.moisture }}}}",
                "mdi:cup-water",
            )
            await self._sensor(
                unique_id,
                f"last_watering_{channel_id}",
                f"Last watering {channel_name}",
                base,
                device_info,
                None,
                "timestamp",
                f"{{{{ {channel_base}.last_watering }}}}",
                "mdi:water-clock",
            )
            await self._sensor(
                unique_id,
                f"history_count_{channel_id}",
                f"History count {channel_name}",
                base,
                device_info,
                None,
                None,
                f"{{{{ {channel_base}.history_count }}}}",
                "mdi:chart-timeline-variant",
                (
                    "{"
                    f"\"history_loading\":{{{{ {channel_base}.history_loading | tojson }}}},"
                    f"\"history_complete\":{{{{ {channel_base}.history_complete | tojson }}}},"
                    f"\"watering_events_complete\":{{{{ {channel_base}.watering_events_complete | tojson }}}},"
                    f"\"history_points\":{{{{ {channel_base}.history_count }}}},"
                    "\"addon_api_url\":{{ value_json.addon_api_url | tojson }},"
                    f"\"history\":{{{{ {channel_base}.history | tojson }}}},"
                    f"\"watering_events\":{{{{ {channel_base}.watering_events | tojson }}}}"
                    "}"
                ),
            )
            await self._sensor(
                unique_id,
                f"next_watering_{channel_id}",
                f"Next watering {channel_name}",
                base,
                device_info,
                None,
                "timestamp",
                f"{{{{ {channel_base}.next_watering }}}}",
                "mdi:calendar-clock",
            )
            await self._binary(
                unique_id,
                f"plant_{channel_id}_configured",
                f"Plant {channel_name} configured",
                base,
                device_info,
                f"{{{{ 'ON' if {channel_base}.plant_configured else 'OFF' }}}}",
                None,
                "mdi:sprout",
            )
            await self._binary(
                unique_id,
                f"pump_{channel_id}_open",
                f"Pump {channel_name} open",
                base,
                device_info,
                f"{{{{ 'ON' if {channel_base}.pump_open else 'OFF' }}}}",
                "opening",
                "mdi:water",
                None,
                False,
            )
            await self._binary(unique_id, f"outlet_{channel_id}_locked", f"Outlet {channel_name} locked", base, device_info, f"{{{{ 'ON' if {channel_base}.outlet_locked else 'OFF' }}}}", "problem", "mdi:pump-off", "diagnostic")
            await self._binary(unique_id, f"outlet_{channel_id}_blocked", f"Outlet {channel_name} blocked", base, device_info, f"{{{{ 'ON' if {channel_base}.outlet_blocked else 'OFF' }}}}", "problem", "mdi:water-pump-off", "diagnostic")
            await self._binary(unique_id, f"sensor_{channel_id}_fault", f"Sensor {channel_name} fault", base, device_info, f"{{{{ 'ON' if {channel_base}.sensor_fault else 'OFF' }}}}", "problem", "mdi:thermometer-probe-off", "diagnostic")
            await self._binary(unique_id, f"sensor_{channel_id}_disconnected", f"Sensor {channel_name} disconnected", base, device_info, f"{{{{ 'ON' if {channel_base}.sensor_disconnected else 'OFF' }}}}", "problem", "mdi:thermometer-probe-off", "diagnostic")
            await self._binary(unique_id, f"watering_issue_{channel_id}", f"Watering issue {channel_name}", base, device_info, f"{{{{ 'ON' if {channel_base}.watering_issue else 'OFF' }}}}", "problem", "mdi:water-alert", "diagnostic")
            await self._binary(unique_id, f"watering_locked_{channel_id}", f"Watering locked {channel_name}", base, device_info, f"{{{{ 'ON' if {channel_base}.watering_locked else 'OFF' }}}}", "problem", "mdi:lock-alert", "diagnostic")
            await self._binary(unique_id, f"history_{channel_id}_loading", f"History {channel_name} loading", base, device_info, f"{{{{ 'ON' if {channel_base}.history_loading else 'OFF' }}}}", "running", "mdi:progress-clock", "diagnostic", False)
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
            await self._button(unique_id, f"save_schedule_{channel_id}", f"Save watering {channel_name}", base, device_info, f"save_schedule_{channel_id}", "mdi:content-save")
            await self._button(unique_id, f"add_plant_{channel_id}", f"Add plant {channel_name}", base, device_info, f"add_plant_{channel_id}", "mdi:plus-circle-outline")
            await self._button(unique_id, f"reset_plant_{channel_id}", f"Reset plant {channel_name}", base, device_info, f"reset_plant_{channel_id}", "mdi:delete-outline")
            await self._number(unique_id, f"manual_duration_seconds_{channel_id}", f"Manual watering amount {channel_name}", base, device_info, 30, 150, 10, "mL", "mdi:watering-can", f"{{{{ {channel_base}.config.manual_duration_seconds }}}}")
            await self._number(unique_id, f"duration_seconds_{channel_id}", f"Watering amount {channel_name}", base, device_info, 10, 500, 10, "mL", "mdi:timer-outline", f"{{{{ {channel_base}.config.amount_ml }}}}")
            await self._number(unique_id, f"interval_hours_{channel_id}", f"Watering interval {channel_name}", base, device_info, 1, 240, 1, "h", "mdi:calendar-clock", f"{{{{ {channel_base}.config.interval_hours }}}}")
            await self._number(unique_id, f"smart_min_moisture_{channel_id}", f"Minimum moisture {channel_name}", base, device_info, 1, 99, 1, "%", "mdi:water-percent", f"{{{{ {channel_base}.config.smart_min_moisture }}}}")
            await self._number(unique_id, f"smart_max_moisture_{channel_id}", f"Maximum moisture {channel_name}", base, device_info, 1, 99, 1, "%", "mdi:water-percent", f"{{{{ {channel_base}.config.smart_max_moisture }}}}")
            await self._select(unique_id, f"watering_mode_{channel_id}", f"Watering mode {channel_name}", base, device_info, ["Disabled", "Repeating", "Smart"], "mdi:sprinkler-variant", f"{{{{ {channel_base}.config.mode }}}}")
            await self._switch(unique_id, f"smart_daytime_watering_{channel_id}", f"Daytime watering {channel_name}", base, device_info, "mdi:white-balance-sunny", f"{{{{ 'ON' if {channel_base}.config.smart_daytime_watering else 'OFF' }}}}")
            await self._text(unique_id, f"plant_name_{channel_id}", f"Plant name {channel_name}", base, device_info, 64, "mdi:flower", f"{{{{ {channel_base}.config.plant_name }}}}")
            await self._text(unique_id, f"plant_photo_url_{channel_id}", f"Plant photo URL {channel_name}", base, device_info, 512, "mdi:image-outline", f"{{{{ {channel_base}.config.photo_url }}}}")
            await self._time(unique_id, f"first_watering_time_{channel_id}", f"First watering time {channel_name}", base, device_info, "mdi:clock-start", f"{{{{ {channel_base}.config.first_watering_time }}}}")

    async def _clear_legacy_configs(self, device_id: str) -> None:
        assert self.client is not None
        legacy = [("binary_sensor", "connected")]
        legacy.extend(("binary_sensor", f"pump_{channel}") for channel in "abcd")
        legacy.extend(("text", f"first_watering_time_{channel}") for channel in "abcd")
        for component, key in legacy:
            topic = f"homeassistant/{component}/growcube/{device_id}_{key}/config"
            await self.client.publish(topic, "", retain=True)

    async def _sensor(
        self,
        device_id: str,
        key: str,
        name: str,
        base: str,
        device: dict,
        unit: str | None,
        device_class: str | None,
        template: str,
        icon: str | None = None,
        json_attributes_template: str | None = None,
    ) -> None:
        config = {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "availability_topic": f"{base}/availability",
            "device": device,
        }
        if unit:
            config["unit_of_measurement"] = unit
        if device_class:
            config["device_class"] = device_class
        if icon:
            config["icon"] = icon
        if json_attributes_template:
            config["json_attributes_topic"] = f"{base}/state"
            config["json_attributes_template"] = json_attributes_template
        await self._config("sensor", device_id, key, config)

    async def _binary(
        self,
        device_id: str,
        key: str,
        name: str,
        base: str,
        device: dict,
        template: str,
        device_class: str | None = None,
        icon: str | None = None,
        entity_category: str | None = None,
        enabled_by_default: bool = True,
    ) -> None:
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
        if icon:
            config["icon"] = icon
        if entity_category:
            config["entity_category"] = entity_category
        if not enabled_by_default:
            config["enabled_by_default"] = False
        await self._config("binary_sensor", device_id, key, config)

    async def _button(self, device_id: str, key: str, name: str, base: str, device: dict, payload: str, icon: str) -> None:
        await self._config("button", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "command_topic": f"{base}/{key}/set",
            "payload_press": payload,
            "availability_topic": f"{base}/availability",
            "icon": icon,
            "device": device,
        })

    async def _number(self, device_id: str, key: str, name: str, base: str, device: dict, minimum: int, maximum: int, step: int, unit: str, icon: str, template: str) -> None:
        await self._config("number", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "command_topic": f"{base}/{key}/set",
            "min": minimum,
            "max": maximum,
            "step": step,
            "unit_of_measurement": unit,
            "mode": "box",
            "icon": icon,
            "entity_category": "config",
            "availability_topic": f"{base}/availability",
            "device": device,
        })

    async def _select(self, device_id: str, key: str, name: str, base: str, device: dict, options: list[str], icon: str, template: str) -> None:
        await self._config("select", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "command_topic": f"{base}/{key}/set",
            "options": options,
            "icon": icon,
            "entity_category": "config",
            "availability_topic": f"{base}/availability",
            "device": device,
        })

    async def _switch(self, device_id: str, key: str, name: str, base: str, device: dict, icon: str, template: str) -> None:
        await self._config("switch", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "command_topic": f"{base}/{key}/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": icon,
            "entity_category": "config",
            "availability_topic": f"{base}/availability",
            "device": device,
        })

    async def _text(self, device_id: str, key: str, name: str, base: str, device: dict, maximum: int, icon: str, template: str) -> None:
        await self._config("text", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "command_topic": f"{base}/{key}/set",
            "max": maximum,
            "icon": icon,
            "entity_category": "config",
            "availability_topic": f"{base}/availability",
            "device": device,
        })

    async def _time(self, device_id: str, key: str, name: str, base: str, device: dict, icon: str, template: str) -> None:
        await self._config("time", device_id, key, {
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "state_topic": f"{base}/state",
            "value_template": template,
            "command_topic": f"{base}/{key}/set",
            "icon": icon,
            "entity_category": "config",
            "availability_topic": f"{base}/availability",
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
        LOGGER.debug("Published MQTT state for GrowCube device %s (%s)", unique_id, availability)


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
