import os
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from confluent_kafka import Consumer, Producer

# Ensure project root is on sys.path for `from ai...` imports.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai.workflow import run_workflow_with_trace
from ai.pg import pg_insert_incident
from ai.tools import rca_tools

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "192.168.17.128:9092")
TOPIC_ALERTS = os.getenv("KAFKA_TOPIC_ALERTS", "alert-events")
TOPIC_RESULTS = os.getenv("KAFKA_TOPIC_RESULTS", "analysis-results")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "monitor-task")
DEFAULT_DEDUP_PATH = str(Path(__file__).resolve().parent / "alert_event_ids.json")
EVENT_DEDUP_FILE = os.getenv("EVENT_DEDUP_FILE", DEFAULT_DEDUP_PATH)
EVENT_ID_TTL_SEC = int(os.getenv("EVENT_ID_TTL_SEC", "300"))
MIN_LLM_INTERVAL_SEC = float(os.getenv("MIN_LLM_INTERVAL_SEC", "10"))
THING_ALERT_COOLDOWN_SEC = float(os.getenv("THING_ALERT_COOLDOWN_SEC", "60"))

conf = {
    "bootstrap.servers": KAFKA_BOOTSTRAP,
    "group.id": GROUP_ID,
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
}


class EventIdStore:
    def __init__(self, path: str, ttl_sec: int):
        self.path = path
        self.ttl_sec = ttl_sec
        self._m: Dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if isinstance(raw, dict):
                        self._m = {str(k): float(v) for k, v in raw.items()}
        except Exception:
            self._m = {}
        self._cleanup()

    def _cleanup(self) -> None:
        now = time.time()
        self._m = {k: t for k, t in self._m.items() if now - t <= self.ttl_sec}

    def _save(self) -> None:
        path = Path(self.path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._m, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        last_err = None
        for _ in range(5):
            try:
                os.replace(str(tmp), str(path))
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.15)
        if last_err is not None:
            raise last_err

    def seen(self, event_id: str) -> bool:
        self._cleanup()
        return event_id in self._m

    def mark(self, event_id: str) -> None:
        self._cleanup()
        self._m[event_id] = time.time()
        try:
            self._save()
        except Exception as e:
            # Do not crash consumer on dedup persistence failure.
            print(f"[consumer] warning: dedup save failed: {type(e).__name__}: {e}")


def fallback_event_id(event: dict) -> str:
    base = {
        "thingId": event.get("thingId", "unknown"),
        "alert_type": event.get("alert_type", "unknown"),
        "time": event.get("time"),
        "message": event.get("message", ""),
    }
    return json.dumps(base, ensure_ascii=False, sort_keys=True)


class RateLimiter:
    def __init__(self, min_interval_sec: float, thing_alert_cooldown_sec: float):
        self.min_interval_sec = max(0.0, min_interval_sec)
        self.thing_alert_cooldown_sec = max(0.0, thing_alert_cooldown_sec)
        self.last_global_ts = 0.0
        self.last_by_key: Dict[str, float] = {}

    def allow(self, thing_id: str, alert_type: str) -> Tuple[bool, str]:
        now = time.time()
        if self.min_interval_sec > 0 and now - self.last_global_ts < self.min_interval_sec:
            wait = self.min_interval_sec - (now - self.last_global_ts)
            return False, f"global_interval({wait:.1f}s remaining)"

        key = f"{thing_id}|{alert_type}"
        last = self.last_by_key.get(key, 0.0)
        if self.thing_alert_cooldown_sec > 0 and now - last < self.thing_alert_cooldown_sec:
            wait = self.thing_alert_cooldown_sec - (now - last)
            return False, f"thing_alert_cooldown({wait:.1f}s remaining)"

        self.last_global_ts = now
        self.last_by_key[key] = now
        return True, ""

def main():
    consumer = Consumer(conf)
    consumer.subscribe([TOPIC_ALERTS])

    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
    dedup = EventIdStore(EVENT_DEDUP_FILE, EVENT_ID_TTL_SEC)
    limiter = RateLimiter(MIN_LLM_INTERVAL_SEC, THING_ALERT_COOLDOWN_SEC)

    print(f"[consumer] listening topic={TOPIC_ALERTS} group={GROUP_ID} bootstrap={KAFKA_BOOTSTRAP}")
    print(
        f"[consumer] rate-limit global={MIN_LLM_INTERVAL_SEC}s "
        f"thing_alert={THING_ALERT_COOLDOWN_SEC}s"
    )
    print(
        "[consumer] runtime="
        f"python={sys.executable} "
        f"rca_tools={getattr(rca_tools, '__file__', 'unknown')} "
        f"model={getattr(rca_tools, 'HARDCODED_LLM_MODEL', 'n/a')} "
        f"key_prefix={str(getattr(rca_tools, 'HARDCODED_AIHUBMIX_API_KEY', ''))[:8]}"
    )

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                print("[consumer] error:", msg.error())
                continue

            try:
                event = json.loads(msg.value().decode("utf-8"))
            except Exception as e:
                print("[consumer] invalid json:", e)
                consumer.commit(message=msg, asynchronous=False)
                continue

            event_id = str(event.get("event_id") or fallback_event_id(event))
            if dedup.seen(event_id):
                print(f"[consumer] duplicate event skipped event_id={event_id}")
                consumer.commit(message=msg, asynchronous=False)
                continue

            thing_id = str(event.get("thingId") or "unknown")
            alert_type = str(event.get("alert_type") or "unknown")
            allowed, reason = limiter.allow(thing_id=thing_id, alert_type=alert_type)
            if not allowed:
                print(f"[consumer] rate-limited event_id={event_id} reason={reason}")
                out = {
                    "event_id": event_id,
                    "thingId": thing_id,
                    "alert_type": alert_type,
                    "time": event.get("time"),
                    "status": "skipped_rate_limited",
                    "reason": reason,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
                key = thing_id.encode("utf-8")
                producer.produce(TOPIC_RESULTS, key=key, value=json.dumps(out, ensure_ascii=False).encode("utf-8"))
                producer.poll(0)
                producer.flush(2)
                consumer.commit(message=msg, asynchronous=False)
                continue

            print("[consumer] got alert:", event)

            try:
                state = run_workflow_with_trace(event)
                facts = state.get("facts", {})
                trace = state.get("trace", [])
                analysis = state.get("analysis", "")

                incident_id = pg_insert_incident(event=event, facts=facts, trace=trace, analysis=analysis)
                print(f"[consumer] stored incident id={incident_id}")
                print("[consumer] analysis result:")
                print(analysis)

                out = {
                    "event_id": event_id,
                    "incident_id": incident_id,
                    "thingId": event.get("thingId"),
                    "alert_type": event.get("alert_type"),
                    "time": event.get("time"),
                    "analysis": analysis,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
                key = (event.get("thingId") or "unknown").encode("utf-8")
                producer.produce(TOPIC_RESULTS, key=key, value=json.dumps(out, ensure_ascii=False).encode("utf-8"))
                producer.poll(0)
                producer.flush(2)
                print(f"[consumer] published result topic={TOPIC_RESULTS}")

                dedup.mark(event_id)
                consumer.commit(message=msg, asynchronous=False)
            except Exception as e:
                print("[consumer] analysis error:", e)
    finally:
        consumer.close()

if __name__ == "__main__":
    main()
