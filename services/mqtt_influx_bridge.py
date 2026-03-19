"""MQTT to InfluxDB bridge for JaamSim factory inventory telemetry.

Default behavior:
- Subscribe to MQTT topic pattern `factory/+`
- Write points to Influx bucket `factory_data`
- Measurement = topic first segment (e.g. `factory`)
- Tags include: factory_id, thingId, sub_topic, topic
- Numeric payload is written to field `count`
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any, Dict

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision


MQTT_HOST = os.getenv("MQTT_HOST", "192.168.17.128")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "factory/+")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "jaamsim-mqtt-influx-bridge")

INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.17.128:8086")
INFLUX_TOKEN = os.getenv(
    "INFLUX_TOKEN",
    "x1okc3hgLOH7AzkTfgw4-bGl4lOVdmTanTRsccguA5kZPK20X0guSekAfiF83bMYdaavTunqtaDjD8hs7txLwA==",
)
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_FACTORY_BUCKET", "factory_data")

# f1/f2/f3/f4 -> factoryA/B/C/D (customizable by env JSON).
FACTORY_ID_MAP = json.loads(
    os.getenv(
        "FACTORY_ID_MAP_JSON",
        '{"f1":"my_factory_factoryA","f2":"my_factory_factoryB","f3":"my_factory_factoryC","f4":"my_factory_factoryD"}',
    )
)

_STOP = threading.Event()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_fields(raw_payload: str) -> Dict[str, Any]:
    """Parse payload into Influx fields.

    Supported payload forms:
    - "12" -> {"count": 12.0}
    - {"count": 12, "x": 1} -> keeps numeric/bool/string fields
    """
    payload = raw_payload.strip()
    if not payload:
        return {"count": 0.0}

    # Try direct numeric first.
    try:
        return {"count": float(payload)}
    except ValueError:
        pass

    # Try JSON object or scalar.
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return {"value_str": payload}

    if isinstance(obj, (int, float)):
        return {"count": float(obj)}
    if isinstance(obj, bool):
        return {"flag": obj}
    if isinstance(obj, str):
        try:
            return {"count": float(obj)}
        except ValueError:
            return {"value_str": obj}
    if not isinstance(obj, dict):
        return {"value_str": payload}

    fields: Dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, (int, float, bool, str)):
            fields[key] = value
    if "count" not in fields:
        numeric_candidates = [v for v in fields.values() if isinstance(v, (int, float))]
        if numeric_candidates:
            fields["count"] = float(numeric_candidates[0])
    return fields or {"value_str": payload}


def _build_point(topic: str, fields: Dict[str, Any]) -> Point:
    parts = topic.split("/")
    measurement = parts[0] if len(parts) > 0 else "mqtt_data"
    factory_id = parts[1] if len(parts) > 1 else "unknown"
    sub_topic = parts[2] if len(parts) > 2 else "generic"
    thing_id = FACTORY_ID_MAP.get(factory_id, f"my_factory_{factory_id}")

    point = (
        Point(measurement)
        .tag("factory_id", factory_id)
        .tag("thingId", thing_id)
        .tag("sub_topic", sub_topic)
        .tag("topic", topic)
    )

    for key, value in fields.items():
        point = point.field(key, value)
    point = point.time(_now_utc(), WritePrecision.NS)
    return point


def main() -> int:
    print(
        f"[bridge] mqtt={MQTT_HOST}:{MQTT_PORT} topic={MQTT_TOPIC} -> "
        f"influx={INFLUX_URL} bucket={INFLUX_BUCKET} org={INFLUX_ORG}"
    )

    influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = influx.write_api()
    client = mqtt.Client(client_id=MQTT_CLIENT_ID)

    def on_connect(mqtt_client: mqtt.Client, _userdata: Any, _flags: Any, rc: int) -> None:
        if rc != 0:
            print(f"[bridge] mqtt connect failed rc={rc}")
            return
        mqtt_client.subscribe(MQTT_TOPIC, qos=1)
        print(f"[bridge] subscribed topic={MQTT_TOPIC}")

    def on_message(_mqtt_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8", errors="ignore")
            fields = _parse_fields(payload)
            point = _build_point(topic, fields)
            write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
            print(f"[bridge] wrote topic={topic} fields={fields}")
        except Exception as exc:
            print(f"[bridge] write failed topic={topic} err={exc}")

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    def _handle_stop(_sig: int, _frame: Any) -> None:
        _STOP.set()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        while not _STOP.is_set():
            time.sleep(0.5)
    finally:
        client.loop_stop()
        client.disconnect()
        write_api.flush()
        influx.close()
        print("[bridge] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

