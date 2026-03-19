from typing import Any, Dict, List
from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi

from ai.settings import (
    INFLUX_BUCKET,
    INFLUX_ORG,
    INFLUX_TOKEN,
    INFLUX_URL,
    QUEUE_FIELD,
    QUEUE_MEASUREMENT,
    QUEUE_TAG_KEY,
)

# Backward-compatible aliases used by existing imports.
DEFAULT_MEASUREMENT = QUEUE_MEASUREMENT
DEFAULT_FIELD = QUEUE_FIELD
DEFAULT_TAG_KEY = QUEUE_TAG_KEY

def query_counts_last_window(
    query_api: QueryApi,
    bucket: str,
    org: str,
    thing_ids: List[str],
    lookback: str = "-1m",
    measurement: str = DEFAULT_MEASUREMENT,
    field: str = DEFAULT_FIELD,
    tag_key: str = DEFAULT_TAG_KEY,
) -> Dict[str, Any]:
    # build (r.thingId == "x" or r.thingId == "y")
    cond = " or ".join([f'r.{tag_key} == "{tid}"' for tid in thing_ids])

    flux = f'''
from(bucket: "{bucket}")
  |> range(start: {lookback})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r._field == "{field}")
  |> filter(fn: (r) => {cond})
  |> keep(columns: ["_time", "_value", "{tag_key}"])
  |> sort(columns: ["_time"])
'''

    tables = query_api.query(flux, org=org)

    stats: Dict[str, Dict[str, Any]] = {
        tid: {"count": 0, "min": None, "max": None, "last": None, "last_time": None}
        for tid in thing_ids
    }

    for table in tables:
        for rec in table.records:
            tid = rec.values.get(tag_key)
            if tid is None:
                continue
            val = float(rec.get_value())
            t = rec.get_time()
            s = stats.setdefault(tid, {"count": 0, "min": None, "max": None, "last": None, "last_time": None})
            s["count"] += 1
            s["min"] = val if s["min"] is None else min(s["min"], val)
            s["max"] = val if s["max"] is None else max(s["max"], val)
            if s["last_time"] is None or t.isoformat() > s["last_time"]:
                s["last"] = val
                s["last_time"] = t.isoformat()

    return {"flux": flux, "stats": stats}

def with_influx_client():
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
