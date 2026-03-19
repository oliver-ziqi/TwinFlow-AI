"""InfluxDB query tools used by workflow planning and RCA."""

from __future__ import annotations

from typing import Any, Dict, List

from influxdb_client import InfluxDBClient

from ai.influx import query_counts_last_window
from ai.settings import INFLUX_BUCKET, INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL


def tool_influx_queue_metrics(thing_ids: List[str], lookback: str = "-1m") -> Dict[str, Any]:
    """Query Influx queue count metrics for given thing IDs and window."""
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        query_api = client.query_api()
        return query_counts_last_window(
            query_api=query_api,
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
            thing_ids=thing_ids,
            lookback=lookback,
        )


def tool_query_product_change_1m() -> Dict[str, Any]:
    """Query last-minute product queue changes for default queues."""
    thing_ids = ["my_queue_fc_p2", "my_queue_fc_p23", "my_queue_fc_p3"]
    metrics = tool_influx_queue_metrics(thing_ids=thing_ids, lookback="-1m")
    summary: Dict[str, Any] = {}
    for tid, stat in metrics.get("stats", {}).items():
        lo = stat.get("min")
        hi = stat.get("max")
        summary[tid] = {
            "samples": stat.get("count", 0),
            "min": lo,
            "max": hi,
            "last": stat.get("last"),
            "delta_estimate": None if (lo is None or hi is None) else float(hi) - float(lo),
        }
    return {"thing_ids": thing_ids, "lookback": "-1m", "summary": summary}
