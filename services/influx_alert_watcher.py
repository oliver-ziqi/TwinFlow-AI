import os
import time
import json
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path

from influxdb_client import InfluxDBClient
from confluent_kafka import Producer

# Ensure project root is on sys.path when launched from services directory.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEDUP_TTL_SEC = int(os.getenv("DEDUP_TTL_SEC", "600"))
EVENT_BUCKET_SEC = int(os.getenv("EVENT_BUCKET_SEC", "60"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.17.128:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN",
                         "FSz97hCBRzRTSStDZhOpADGikMpgqjG7EzhvyhreJmr6cZnhBf8Qt-rJ2vLpWhq2FcSznjP11iyCBH5pF6QNPw==")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
ALERT_BUCKET = os.getenv("ALERT_BUCKET", "alerts")

MEASUREMENT = os.getenv("ALERT_MEASUREMENT", "stationary_alert")
FIELD = os.getenv("ALERT_FIELD", "message")

POLL_SEC = float(os.getenv("POLL_SEC", "5"))
DEFAULT_CURSOR_FILE = str(Path(__file__).resolve().parent / "alert_cursor.txt")
CURSOR_FILE = os.getenv("CURSOR_FILE", DEFAULT_CURSOR_FILE)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "192.168.17.128:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC_ALERTS", "alert-events")


def to_rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_cursor() -> Optional[datetime]:
    if not os.path.exists(CURSOR_FILE):
        return None
    try:
        s = open(CURSOR_FILE, "r", encoding="utf-8").read().strip()
        if not s:
            return None
        if s.isdigit():
            return datetime.fromtimestamp(float(s), tz=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def save_cursor(dt: datetime) -> None:
    tmp = CURSOR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(to_rfc3339(dt))
    os.replace(tmp, CURSOR_FILE)


class DedupCache:
    def __init__(self, ttl_sec: int):
        self.ttl = ttl_sec
        self._m = {}

    def _cleanup(self):
        now = time.time()
        expired = [k for k, t in self._m.items() if now - t > self.ttl]
        for k in expired:
            self._m.pop(k, None)

    def seen(self, key: str) -> bool:
        self._cleanup()
        now = time.time()
        if key in self._m:
            return True
        self._m[key] = now
        return False


def make_dedup_key(thing_id: str, alert_type: str, t: datetime, msg: str) -> str:
    raw = f"{thing_id}|{alert_type}|{t.isoformat()}|{msg}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_message(msg: str) -> str:
    return " ".join(msg.strip().split()).lower()


def bucket_start_iso(t: datetime, bucket_sec: int) -> str:
    epoch = int(t.timestamp())
    start_epoch = epoch - (epoch % max(1, bucket_sec))
    return datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat()


def make_event_id(thing_id: str, alert_type: str, t: datetime, msg: str) -> str:
    # Stable id used across repeated timer-generated alerts in same time bucket.
    bkt = bucket_start_iso(t, EVENT_BUCKET_SEC)
    raw = f"{thing_id}|{alert_type}|{bkt}|{normalize_message(msg)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main():
    last_seen = load_cursor()
    if last_seen is None:
        last_seen = utc_now() - timedelta(minutes=2)

    print(f"[watcher] influx={INFLUX_URL} org={INFLUX_ORG} bucket={ALERT_BUCKET}")
    print(f"[watcher] watching measurement={MEASUREMENT} field={FIELD}")
    print(f"[watcher] kafka={KAFKA_BOOTSTRAP} topic={KAFKA_TOPIC}")
    print(f"[watcher] start cursor={to_rfc3339(last_seen)} poll={POLL_SEC}s")

    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
    dedup = DedupCache(DEDUP_TTL_SEC)

    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        q = client.query_api()

        while True:
            try:
                start = last_seen
                stop = utc_now()

                flux = f'''
from(bucket: "{ALERT_BUCKET}")
  |> range(start: {to_rfc3339(start)}, stop: {to_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT}")
  |> filter(fn: (r) => r._field == "{FIELD}")
  |> sort(columns: ["_time"], desc: false)
'''

                tables = q.query(flux)
                max_time = last_seen
                sent = 0

                for table in tables:
                    for rec in table.records:
                        t = rec.get_time()
                        if t is None:
                            continue

                        thing_id = str(rec.values.get("thingId", "unknown"))
                        alert_type = str(rec.values.get("alert_type", "unknown"))
                        message = str(rec.get_value())

                        # OPTIONAL: you can carry car_position / branch_value from Influx tags/fields if present
                        # car_position = rec.values.get("car_position")  # e.g. stored as string/array
                        # branch_key = rec.values.get("branch_key", "branch_3")
                        # branch_value = rec.values.get("branch_value")

                        dk = make_dedup_key(thing_id, alert_type, t, message)
                        if dedup.seen(dk):
                            if t > max_time:
                                max_time = t
                            continue

                        event_id = make_event_id(thing_id, alert_type, t, message)
                        event = {
                            "event_id": event_id,
                            "time": t.isoformat(),
                            "thingId": thing_id,
                            "alert_type": alert_type,
                            "message": message,
                            # "car_position": car_position,
                            # "branch_key": branch_key,
                            # "branch_value": branch_value,
                            "source": "influx-alerts-watcher",
                        }

                        k = event_id.encode("utf-8")
                        v = json.dumps(event, ensure_ascii=False).encode("utf-8")
                        producer.produce(KAFKA_TOPIC, key=k, value=v)
                        producer.poll(0)

                        sent += 1
                        if t > max_time:
                            max_time = t

                if sent > 0:
                    producer.flush(2)
                    print(f"[watcher] sent={sent} cursor->{to_rfc3339(max_time)}")

                last_seen = max_time - timedelta(seconds=1)
                save_cursor(last_seen)

                time.sleep(POLL_SEC)

            except KeyboardInterrupt:
                print("\n[watcher] stopped")
                break
            except Exception as e:
                print(f"[watcher] error: {e}")
                time.sleep(max(2, POLL_SEC))


if __name__ == "__main__":
    main()
