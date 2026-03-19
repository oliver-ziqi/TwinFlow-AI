import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from confluent_kafka import Producer
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from influxdb_client import InfluxDBClient
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ai.settings import INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL
from ai.pg import pg_get_incident, pg_list_incidents
from ai.workflow import apply_branch_fix, execute_planned_workflow, prepare_workflow_preview

app = FastAPI(title="JaamSim UI Backend API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "192.168.17.128:9092")
TOPIC = os.getenv("KAFKA_TOPIC_ALERTS", "alert-events")
producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})

SEEN_TTL_SEC = 300
_seen: Dict[str, float] = {}

FACTORY_BUCKET = os.getenv("INFLUX_FACTORY_BUCKET", "factory_data")
FACTORY_MEASUREMENT = os.getenv("INFLUX_FACTORY_MEASUREMENT", "factory")
FACTORY_FIELD = os.getenv("INFLUX_FACTORY_FIELD", "count")
FACTORY_THING_IDS = [
    "my_factory_factoryA",
    "my_factory_factoryB",
    "my_factory_factoryC",
    "my_factory_factoryD",
]

INFLUX_TASKS_BASE = INFLUX_URL.rstrip("/")
INFLUX_TASKS_ORG = INFLUX_ORG
INFLUX_TASKS_TOKEN = INFLUX_TOKEN

WATCHER_SCRIPT = ROOT_DIR / "services" / "influx_alert_watcher.py"
CONSUMER_SCRIPT = ROOT_DIR / "services" / "alert_event_consumer.py"
PURGE_SCRIPT = ROOT_DIR / "scripts" / "purge_influx_buckets.py"
SIM_CFG_PATH = ROOT_DIR / "simulation" / "logistics" / "logistics.cfg"
if (ROOT_DIR / "simulation" / "JaamSim.jar").exists():
    JAAMSIM_JAR_PATH = ROOT_DIR / "simulation" / "JaamSim.jar"
else:
    JAAMSIM_JAR_PATH = ROOT_DIR / "JaamSim.jar"
LOG_DIR = ROOT_DIR / "services" / "logs"

_procs: Dict[str, subprocess.Popen] = {}


class AnalyzeRequest(BaseModel):
    text: str = ""
    event: Optional[Dict[str, Any]] = None


class BranchApplyRequest(BaseModel):
    proposal: Dict[str, Any]


def _is_proc_running(proc: Optional[subprocess.Popen]) -> bool:
    return proc is not None and proc.poll() is None


def _start_background_service(name: str, script_path: Path) -> Dict[str, Any]:
    proc = _procs.get(name)
    if _is_proc_running(proc):
        return {"ok": True, "message": f"{name} already running", "pid": proc.pid}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"
    log_fp = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if name == "watcher":
        env.setdefault("POLL_SEC", "5")

    creation_flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(ROOT_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=creation_flags,
    )
    _procs[name] = proc
    return {"ok": True, "message": f"{name} started", "pid": proc.pid, "log": str(log_path)}


def _stop_background_service(name: str) -> Dict[str, Any]:
    proc = _procs.get(name)
    if not _is_proc_running(proc):
        _procs[name] = None
        return {"ok": True, "message": f"{name} is not running"}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    _procs[name] = None
    return {"ok": True, "message": f"{name} stopped"}


def _tail_text_file(path: Path, max_lines: int = 120) -> str:
    if not path.exists():
        return "(no log yet)"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not lines:
            return "(empty log)"
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"(read failed: {exc})"


def _reset_event_pipeline_state() -> Dict[str, Any]:
    watcher_running = _is_proc_running(_procs.get("watcher"))
    consumer_running = _is_proc_running(_procs.get("consumer"))
    actions = []
    if watcher_running:
        actions.append(_stop_background_service("watcher")["message"])
    if consumer_running:
        actions.append(_stop_background_service("consumer")["message"])

    removed = []
    for path in [
        ROOT_DIR / "services" / "alert_cursor.txt",
        ROOT_DIR / "alert_cursor.txt",
        ROOT_DIR / "services" / "alert_event_ids.json",
        ROOT_DIR / "alert_event_ids.json",
    ]:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception:
            pass

    if watcher_running:
        actions.append(_start_background_service("watcher", WATCHER_SCRIPT)["message"])
    if consumer_running:
        actions.append(_start_background_service("consumer", CONSUMER_SCRIPT)["message"])
    return {"ok": True, "removed": removed, "actions": actions}


def _start_simulation() -> Dict[str, Any]:
    proc = _procs.get("simulation")
    if _is_proc_running(proc):
        return {"ok": True, "message": "simulation already running", "pid": proc.pid}
    if not SIM_CFG_PATH.exists():
        raise HTTPException(status_code=400, detail=f"cfg not found: {SIM_CFG_PATH}")
    if not JAAMSIM_JAR_PATH.exists():
        raise HTTPException(status_code=400, detail=f"jar not found: {JAAMSIM_JAR_PATH}")
    creation_flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        ["java", "-jar", str(JAAMSIM_JAR_PATH), str(SIM_CFG_PATH), "-b"],
        cwd=str(ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    _procs["simulation"] = proc
    return {"ok": True, "message": "simulation started", "pid": proc.pid}


def _stop_simulation() -> Dict[str, Any]:
    proc = _procs.get("simulation")
    if not _is_proc_running(proc):
        _procs["simulation"] = None
        return {"ok": True, "message": "simulation is not running"}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    _procs["simulation"] = None
    return {"ok": True, "message": "simulation stopped"}


def _influx_api_request(method: str, path: str, query: Dict[str, str], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    q = urllib.parse.urlencode(query)
    url = f"{INFLUX_TASKS_BASE}{path}"
    if q:
        url = f"{url}?{q}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=body, method=method)
    req.add_header("Authorization", f"Token {INFLUX_TASKS_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            status = getattr(resp, "status", 200)
        data = json.loads(raw) if raw else {}
        return {"ok": True, "status": status, "data": data}
    except urllib.error.HTTPError as exc:
        err = ""
        try:
            err = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        return {"ok": False, "status": exc.code, "error": err or str(exc)}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def dedup_key(payload: Dict[str, Any]) -> str:
    raw = (
        f"{payload.get('thingId')}|{payload.get('alert_type')}|"
        f"{payload.get('time')}|{payload.get('message')}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cleanup_seen() -> None:
    now = time.time()
    expired = [k for k, ts in _seen.items() if now - ts > SEEN_TTL_SEC]
    for key in expired:
        _seen.pop(key, None)


def query_factory_inventory(lookback_min: int) -> List[Dict[str, Any]]:
    start = (datetime.now(UTC) - timedelta(minutes=max(1, int(lookback_min)))).isoformat()
    ids_filter = " or ".join([f'r.thingId == "{thing_id}"' for thing_id in FACTORY_THING_IDS])
    flux = f"""
from(bucket: "{FACTORY_BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "{FACTORY_MEASUREMENT}")
  |> filter(fn: (r) => r._field == "{FACTORY_FIELD}")
  |> filter(fn: (r) => {ids_filter})
  |> keep(columns: ["_time", "_value", "thingId"])
  |> sort(columns: ["_time"])
"""

    rows: List[Dict[str, Any]] = []
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        tables = client.query_api().query(flux, org=INFLUX_ORG)
        for table in tables:
            for rec in table.records:
                t = rec.get_time()
                v = rec.get_value()
                if t is None or v is None:
                    continue
                rows.append(
                    {
                        "time": t.isoformat(),
                        "thingId": str(rec.values.get("thingId", "")),
                        "count": float(v),
                    }
                )
    return rows


def build_query_event(text: str) -> Dict[str, Any]:
    return {
        "source": "ui-index-new",
        "query": text,
        "message": text,
        "alert_type": "",
        "thingId": "chat:user",
    }


@app.get("/ui-api/health")
def ui_health() -> Dict[str, Any]:
    return {"ok": True, "service": "ui-backend"}


@app.get("/ui-api/inventory")
def ui_inventory(lookback_min: int = 30) -> Dict[str, Any]:
    series = query_factory_inventory(lookback_min=lookback_min)
    return {"source": "influx", "series": series, "lookback_min": lookback_min}


@app.post("/ui-api/analyze")
def ui_analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    if req.event and isinstance(req.event, dict):
        event = req.event
    else:
        event = build_query_event(req.text.strip())
    preview = prepare_workflow_preview(event)
    try:
        final = execute_planned_workflow(preview)
        analysis = str(final.get("analysis", "")).strip()
        if not analysis:
            analysis = json.dumps(final.get("facts", {}), ensure_ascii=False)
        branch_fix_proposal = final.get("facts", {}).get("branch_fix_proposal")
        model_ok = True
        model_error = None
    except Exception as exc:
        # Keep UI responsive when LLM quota/network/provider fails.
        analysis = (
            "LLM analysis is temporarily unavailable. "
            f"Reason: {type(exc).__name__}: {exc}. "
            "Please switch model/key or retry later."
        )
        branch_fix_proposal = None
        model_ok = False
        model_error = f"{type(exc).__name__}: {exc}"
    return {
        "answer": analysis,
        "intent": preview.get("route", {}).get("intent", "unknown"),
        "mode": preview.get("route", {}).get("mode", "query"),
        "branch_fix_proposal": branch_fix_proposal,
        "model_ok": model_ok,
        "model_error": model_error,
    }


@app.post("/ui-api/branch/apply")
def ui_branch_apply(req: BranchApplyRequest) -> Dict[str, Any]:
    result = apply_branch_fix(req.proposal or {})
    return {"ok": bool(result.get("applied")), "result": result}


@app.get("/ui-api/runtime/status")
def ui_runtime_status() -> Dict[str, Any]:
    return {
        "watcher": {"running": _is_proc_running(_procs.get("watcher"))},
        "consumer": {"running": _is_proc_running(_procs.get("consumer"))},
        "simulation": {"running": _is_proc_running(_procs.get("simulation"))},
        "cfg": str(SIM_CFG_PATH),
        "jar": str(JAAMSIM_JAR_PATH),
    }


@app.post("/ui-api/runtime/watcher/start")
def ui_start_watcher() -> Dict[str, Any]:
    return _start_background_service("watcher", WATCHER_SCRIPT)


@app.post("/ui-api/runtime/watcher/stop")
def ui_stop_watcher() -> Dict[str, Any]:
    return _stop_background_service("watcher")


@app.post("/ui-api/runtime/consumer/start")
def ui_start_consumer() -> Dict[str, Any]:
    return _start_background_service("consumer", CONSUMER_SCRIPT)


@app.post("/ui-api/runtime/consumer/stop")
def ui_stop_consumer() -> Dict[str, Any]:
    return _stop_background_service("consumer")


@app.post("/ui-api/runtime/reset-pipeline")
def ui_reset_pipeline() -> Dict[str, Any]:
    return _reset_event_pipeline_state()


@app.post("/ui-api/runtime/simulation/start")
def ui_start_sim() -> Dict[str, Any]:
    return _start_simulation()


@app.post("/ui-api/runtime/simulation/stop")
def ui_stop_sim() -> Dict[str, Any]:
    return _stop_simulation()


@app.post("/ui-api/runtime/purge-influx")
def ui_purge_influx() -> Dict[str, Any]:
    run = subprocess.run(
        [sys.executable, str(PURGE_SCRIPT)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
    )
    return {
        "ok": run.returncode == 0,
        "returncode": run.returncode,
        "stdout": run.stdout[-8000:],
        "stderr": run.stderr[-8000:],
    }


@app.get("/ui-api/runtime/logs/{name}")
def ui_runtime_logs(name: str, lines: int = 120) -> Dict[str, Any]:
    if name not in {"watcher", "consumer"}:
        raise HTTPException(status_code=400, detail="invalid log name")
    return {"ok": True, "name": name, "text": _tail_text_file(LOG_DIR / f"{name}.log", max_lines=max(20, lines))}


@app.get("/ui-api/incidents")
def ui_incidents(limit: int = 20) -> Dict[str, Any]:
    rows = pg_list_incidents(limit=max(1, min(limit, 100)))
    out = []
    if isinstance(rows, list):
        for row in rows:
            out.append(
                {
                    "id": int(row.get("id")),
                    "thing_id": row.get("thing_id"),
                    "alert_type": row.get("alert_type"),
                    "event_time": row.get("event_time").isoformat() if hasattr(row.get("event_time"), "isoformat") else row.get("event_time"),
                    "received_at": row.get("received_at").isoformat() if hasattr(row.get("received_at"), "isoformat") else row.get("received_at"),
                }
            )
    return {"ok": True, "items": out}


@app.get("/ui-api/incidents/{incident_id}")
def ui_incident_detail(incident_id: int) -> Dict[str, Any]:
    row = pg_get_incident(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail="incident not found")
    item: Dict[str, Any] = {}
    for key, val in dict(row).items():
        if hasattr(val, "isoformat"):
            item[key] = val.isoformat()
        else:
            item[key] = val
    return {"ok": True, "item": item}


@app.get("/ui-api/tasks")
def ui_tasks() -> Dict[str, Any]:
    return _influx_api_request("GET", "/api/v2/tasks", {"org": INFLUX_TASKS_ORG})


@app.post("/ui-api/tasks/{task_id}/{status}")
def ui_task_status(task_id: str, status: str) -> Dict[str, Any]:
    if status not in {"active", "inactive"}:
        raise HTTPException(status_code=400, detail="status must be active or inactive")
    return _influx_api_request("PATCH", f"/api/v2/tasks/{task_id}", {}, {"status": status})


@app.post("/webhook/alerts")
async def receive_alert(req: Request) -> Dict[str, Any]:
    payload = await req.json()

    cleanup_seen()
    key_dedup = dedup_key(payload)
    if key_dedup in _seen:
        return {"ok": True, "dedup": True}

    _seen[key_dedup] = time.time()

    key = (payload.get("thingId") or "unknown").encode("utf-8")
    value = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    producer.produce(TOPIC, key=key, value=value)
    producer.poll(0)
    return {"ok": True, "sent_to_kafka": True, "topic": TOPIC}
