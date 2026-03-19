import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai.influx import INFLUX_TOKEN as PROJECT_INFLUX_TOKEN


DEFAULT_INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.17.128:8086")
DEFAULT_INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
DEFAULT_INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", PROJECT_INFLUX_TOKEN)
DEFAULT_BUCKETS = ["alerts", "logistics", "factory_data", "queue_data"]


def delete_bucket_data(
    influx_url: str,
    org: str,
    token: str,
    bucket: str,
    start: str = "1970-01-01T00:00:00Z",
    stop: str = "2100-01-01T00:00:00Z",
) -> Tuple[bool, str]:
    """Delete all points in one Influx bucket by time range."""
    base = influx_url.rstrip("/")
    q = urllib.parse.urlencode({"org": org, "bucket": bucket})
    url = f"{base}/api/v2/delete?{q}"
    payload = json.dumps({"start": start, "stop": stop}).encode("utf-8")

    req = urllib.request.Request(url=url, data=payload, method="POST")
    req.add_header("Authorization", f"Token {token}")
    req.add_header("Content-type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            code = getattr(resp, "status", 200)
        if code in (200, 204):
            return True, f"bucket={bucket} deleted (status={code})"
        return False, f"bucket={bucket} unexpected status={code}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        return False, f"bucket={bucket} http_error={e.code} {body}"
    except Exception as e:
        return False, f"bucket={bucket} error={type(e).__name__}: {e}"


def purge_buckets(influx_url: str, org: str, token: str, buckets: List[str]) -> int:
    """Purge provided buckets. Return non-zero if any failure occurs."""
    failed = 0
    for b in buckets:
        ok, msg = delete_bucket_data(influx_url=influx_url, org=org, token=token, bucket=b)
        print(msg)
        if not ok:
            failed += 1
    return 1 if failed > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete historical data from multiple InfluxDB buckets.")
    parser.add_argument("--influx-url", default=DEFAULT_INFLUX_URL)
    parser.add_argument("--org", default=DEFAULT_INFLUX_ORG)
    parser.add_argument("--token", default=DEFAULT_INFLUX_TOKEN)
    parser.add_argument("--buckets", nargs="*", default=DEFAULT_BUCKETS)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("INFLUX token is required. Set --token or INFLUX_TOKEN env.")

    code = purge_buckets(
        influx_url=args.influx_url,
        org=args.org,
        token=args.token,
        buckets=args.buckets,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
