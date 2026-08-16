"""MQTT client for a Saveeye Base reader's live telemetry.

A Saveeye Base clips onto a Danish smart meter's HAN port and publishes JSON
telemetry to a single shared MQTT topic (`saveeye/telemetry` by default) on
whatever broker the Saveeye app is pointed at — commonly Home Assistant's own
Mosquitto broker add-on. The payload shape and field names here are taken
from Saveeye's own official Home Assistant integration
(github.com/saveeye/SaveEye-HomeAssistant), which parses the same messages;
this module is a from-scratch client rather than a dependency on that add-on,
since only two fields are actually needed here.
"""
import json
import logging

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

DEFAULT_TOPIC = "saveeye/telemetry"


def _extract(payload, *path):
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def parse_telemetry(payload):
    """Pull the fields this add-on cares about out of a raw Saveeye
    telemetry payload. Returns None if it doesn't look like one (no device
    serial — the one field every real message carries)."""
    serial = payload.get("saveeyeDeviceSerialNumber")
    if serial is None:
        return None
    return {
        "device_serial": str(serial),
        "device_timestamp": payload.get("timestamp"),
        # Instant power draw, Watts — the "instant reading".
        "instant_power_w": _extract(payload, "activeActualConsumption", "total"),
        # Ever-increasing energy counter, Wh — diffed between two points in
        # time to get accurate consumption for the period between them,
        # the same principle a physical meter's own tally uses.
        "cumulative_wh": _extract(payload, "activeTotalConsumption", "total"),
    }


class SaveeyeClient:
    """A background MQTT subscriber.

    `on_telemetry(parsed)` fires on the MQTT network thread for every
    message that parses successfully and matches `device_serial` (if set —
    unset accepts telemetry from any device publishing on the topic, which is
    the common case of exactly one Base reader per household).
    `on_status(connected, detail)` fires on connect/disconnect/failure.
    """

    def __init__(
        self,
        host,
        port,
        topic,
        on_telemetry,
        on_status=None,
        username=None,
        password=None,
        device_serial=None,
    ):
        self._topic = topic or DEFAULT_TOPIC
        self._on_telemetry = on_telemetry
        self._on_status = on_status
        self._device_serial = device_serial or None
        self._host = host
        self._port = port

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def _report(self, connected, detail):
        if self._on_status:
            try:
                self._on_status(connected, detail)
            except Exception:  # noqa: BLE001 - a status callback must not kill the MQTT loop
                log.exception("Saveeye status callback failed")

    def _handle_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = reason_code == 0 or getattr(reason_code, "value", reason_code) == 0
        if ok:
            client.subscribe(self._topic)
            self._report(True, f"connected, subscribed to {self._topic}")
        else:
            self._report(False, f"connect failed: {reason_code}")

    def _handle_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        self._report(False, f"disconnected: {reason_code}" if reason_code else "disconnected")

    def _handle_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            log.warning("Saveeye MQTT: could not parse JSON on topic %s", msg.topic)
            return
        parsed = parse_telemetry(payload)
        if parsed is None:
            return
        if self._device_serial and parsed["device_serial"] != self._device_serial:
            return
        self._on_telemetry(parsed)

    def start(self):
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def stop(self):
        self._client.loop_stop()
        self._client.disconnect()
