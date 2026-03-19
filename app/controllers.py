"""Controller/actions layer for Streamlit app side effects and orchestration."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

from ai.influx import INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL
from ai.pg import pg_get_incident, pg_list_incidents
from ai.workflow import apply_branch_fix

ROOT_DIR = Path(__file__).resolve().parent.parent
WATCHER_SCRIPT = ROOT_DIR / "services" / "influx_alert_watcher.py"
CONSUMER_SCRIPT = ROOT_DIR / "services" / "alert_event_consumer.py"
PURGE_SCRIPT = ROOT_DIR / "scripts" / "purge_influx_buckets.py"
SIM_CFG_PATH = ROOT_DIR / "simulation" / "logistics" / "logistics.cfg"

_jar_env = os.getenv("JAAMSIM_JAR", "").strip()
if _jar_env:
    JAAMSIM_JAR_PATH = Path(_jar_env)
elif (ROOT_DIR / "simulation" / "JaamSim.jar").exists():
    JAAMSIM_JAR_PATH = ROOT_DIR / "simulation" / "JaamSim.jar"
else:
    JAAMSIM_JAR_PATH = ROOT_DIR / "JaamSim.jar"

TWIN_HTML_PATH = ROOT_DIR / "ui" / "threejs-demo" / "index.html"
LOG_DIR = ROOT_DIR / "services" / "logs"
INFLUX_TASKS_BASE = os.getenv("INFLUX_URL", INFLUX_URL).rstrip("/")
INFLUX_TASKS_ORG = os.getenv("INFLUX_ORG", INFLUX_ORG)
INFLUX_TASKS_TOKEN = os.getenv("INFLUX_TOKEN", INFLUX_TOKEN)


def try_parse_event_json(text: str) -> Optional[Dict[str, Any]]:
    """Try parse chat text as event JSON payload."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def build_query_event(user_text: str) -> Dict[str, Any]:
    """Build unified workflow event from plain chat query."""
    return {
        "source": "streamlit-chat",
        "query": user_text,
        "message": user_text,
        "alert_type": "",
        "thingId": "chat:user",
    }


def parse_branch_approval(text: str) -> Optional[bool]:
    """Parse user approval intent for pending branch change."""
    value = str(text or "").strip().lower()
    if not value:
        return None
    yes_words = {"yes", "y", "ok", "confirm", "agree", "approve"}
    no_words = {"no", "n", "cancel", "reject", "deny"}
    if value in yes_words:
        return True
    if value in no_words:
        return False
    return None


def execute_pending_branch_change(proposal: Dict[str, Any], thing_id_for_verify: str) -> str:
    """Apply branch fix only. No automatic verification/rollback."""
    _ = thing_id_for_verify
    result = apply_branch_fix(proposal)
    if not result.get("applied"):
        return f"No change applied: {result.get('message', 'unknown')}"

    return (
        f"Branch updated successfully: `{result.get('branch_key')}` "
        f"`{result.get('old_value')}` -> `{result.get('new_value')}`\n\n"
        "No auto-rollback is performed. Please verify downstream behavior manually.\n"
        f"file: `{result.get('file_path')}`\nbackup: `{result.get('backup_path')}`"
    )


def is_proc_running(proc: Optional[subprocess.Popen[Any]]) -> bool:
    return proc is not None and proc.poll() is None


def start_background_service(name: str, script_path: Path) -> str:
    """Start one background python service if not running."""
    proc_key = f"proc_{name}"
    proc = st.session_state.get(proc_key)
    if is_proc_running(proc):
        return f"{name} already running (pid={proc.pid})"

    creation_flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"
    log_fp = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if name == "watcher":
        env.setdefault("POLL_SEC", "5")

    new_proc = subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=str(ROOT_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=creation_flags,
    )
    st.session_state[f"log_{name}"] = str(log_path)
    st.session_state[proc_key] = new_proc
    return f"{name} started (pid={new_proc.pid}) log={log_path}"


def stop_background_service(name: str) -> str:
    """Stop one background python service."""
    proc_key = f"proc_{name}"
    proc = st.session_state.get(proc_key)
    if not is_proc_running(proc):
        st.session_state[proc_key] = None
        return f"{name} is not running"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    st.session_state[proc_key] = None
    return f"{name} stopped"


def tail_text_file(path: Path, max_lines: int = 80) -> str:
    """Return last lines of text file for quick runtime diagnostics."""
    if not path.exists():
        return "(no log yet)"
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not lines:
            return "(empty log)"
        return "\n".join(lines[-max_lines:])
    except Exception as exc:
        return f"(read log failed: {exc})"


def reset_event_pipeline_state() -> str:
    """Reset watcher/consumer runtime state in one click."""
    watcher_was_running = is_proc_running(st.session_state.get("proc_watcher"))
    consumer_was_running = is_proc_running(st.session_state.get("proc_consumer"))

    actions = []
    if watcher_was_running:
        actions.append(stop_background_service("watcher"))
    if consumer_was_running:
        actions.append(stop_background_service("consumer"))

    removed = []
    candidates = [
        ROOT_DIR / "services" / "alert_cursor.txt",
        ROOT_DIR / "alert_cursor.txt",
        ROOT_DIR / "services" / "alert_event_ids.json",
        ROOT_DIR / "alert_event_ids.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception:
            pass

    if watcher_was_running:
        actions.append(start_background_service("watcher", WATCHER_SCRIPT))
    if consumer_was_running:
        actions.append(start_background_service("consumer", CONSUMER_SCRIPT))

    removed_msg = "no state files removed" if not removed else ("removed: " + ", ".join(removed))
    action_msg = " | ".join(actions) if actions else "services were not running"
    return f"{removed_msg} | {action_msg}"


def start_simulation() -> str:
    """Start JaamSim logistics simulation process."""
    proc = st.session_state.get("proc_simulation")
    if is_proc_running(proc):
        return f"simulation already running (pid={proc.pid})"
    if not SIM_CFG_PATH.exists():
        return f"simulation cfg not found: {SIM_CFG_PATH}"
    if not JAAMSIM_JAR_PATH.exists():
        return f"JaamSim.jar not found: {JAAMSIM_JAR_PATH} (set JAAMSIM_JAR env)"

    creation_flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    cmd = ["java", "-jar", str(JAAMSIM_JAR_PATH), str(SIM_CFG_PATH), "-b"]
    new_proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    st.session_state.proc_simulation = new_proc
    st.session_state.sim_timer_running = True
    st.session_state.sim_timer_base_sec = 0.0
    st.session_state.sim_timer_started_at = time.time()
    return f"simulation started (pid={new_proc.pid})"


def stop_simulation() -> str:
    """Stop JaamSim simulation process."""
    proc = st.session_state.get("proc_simulation")
    if not is_proc_running(proc):
        st.session_state.proc_simulation = None
        return "simulation is not running"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

    st.session_state.proc_simulation = None
    if st.session_state.sim_timer_running and st.session_state.sim_timer_started_at:
        st.session_state.sim_timer_base_sec += max(0.0, time.time() - float(st.session_state.sim_timer_started_at))
    st.session_state.sim_timer_running = False
    st.session_state.sim_timer_started_at = None
    return "simulation stopped"


def format_elapsed_seconds(seconds: float) -> str:
    """Format elapsed seconds as HH:MM:SS."""
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_sim_elapsed_seconds() -> float:
    """Return current simulation timer seconds (supports running + paused)."""
    base = float(st.session_state.get("sim_timer_base_sec", 0.0))
    if st.session_state.get("sim_timer_running") and st.session_state.get("sim_timer_started_at"):
        return base + max(0.0, time.time() - float(st.session_state.sim_timer_started_at))
    return base


def extract_plan_from_trace(trace: Any) -> Any:
    """Extract planned steps from persisted workflow trace when available."""
    if not isinstance(trace, list):
        return []
    for item in trace:
        if not isinstance(item, dict):
            continue
        if item.get("node") == "plan":
            data = item.get("data", {})
            if isinstance(data, dict) and isinstance(data.get("plan"), list):
                return data.get("plan", [])
    return []


def sync_incidents_from_db(build_result_message_fn: Any, limit: int = 20) -> int:
    """Poll latest incidents and append unseen ones into chat messages."""
    latest = pg_list_incidents(limit=limit)
    if not isinstance(latest, list):
        return 0

    seen_ids = st.session_state.incident_seen_ids
    baseline_id = int(st.session_state.get("incident_baseline_id", 0))
    added = 0

    for row in reversed(latest):
        incident_id = int(row.get("id"))
        if incident_id <= baseline_id:
            continue
        if incident_id in seen_ids:
            continue

        detail = pg_get_incident(incident_id)
        if not detail:
            continue

        facts = detail.get("facts") or {}
        trace = detail.get("trace") or []
        analysis = detail.get("analysis") or ""
        plan = extract_plan_from_trace(trace)
        intent = str(facts.get("intent", "incident_rca"))

        final = {"analysis": analysis, "facts": facts}
        payload = build_result_message_fn(intent=intent, plan=plan, final=final, mode="event")
        st.session_state.messages.append({"role": "assistant", "content": payload})

        proposal = (facts or {}).get("branch_fix_proposal", {})
        if isinstance(proposal, dict) and proposal.get("can_fix") and not st.session_state.get("pending_branch_action"):
            thing_id_for_verify = str(facts.get("thing_id_norm") or facts.get("thing_id") or "")
            st.session_state.pending_branch_action = {
                "proposal": proposal,
                "thing_id": thing_id_for_verify,
            }
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"Suggested branch change: `{proposal.get('branch_key')}`: "
                        f"`{proposal.get('current_value')}` -> `{proposal.get('recommended_value')}`. "
                        "Reply with 'approve/confirm' to execute, or 'reject/cancel' to keep current values."
                    ),
                }
            )

        seen_ids.add(incident_id)
        added += 1

    st.session_state.incident_seen_ids = seen_ids
    return added


def influx_api_request(
    method: str,
    path: str,
    query: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call InfluxDB v2 HTTP API and return parsed JSON if possible."""
    qs = urllib.parse.urlencode(query)
    url = f"{INFLUX_TASKS_BASE}{path}"
    if qs:
        url = f"{url}?{qs}"

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=body, method=method)
    req.add_header("Authorization", f"Token {INFLUX_TASKS_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            status = getattr(resp, "status", 200)
        try:
            data = json.loads(raw) if raw else {}
        except Exception:
            data = {"raw": raw}
        return {"ok": True, "status": status, "data": data}
    except urllib.error.HTTPError as exc:
        txt = ""
        try:
            txt = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        return {"ok": False, "status": exc.code, "error": txt or str(exc)}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def list_influx_tasks() -> Dict[str, Any]:
    """Fetch task list for current org."""
    return influx_api_request("GET", "/api/v2/tasks", {"org": INFLUX_TASKS_ORG})


def set_influx_task_status(task_id: str, status: str) -> Dict[str, Any]:
    """Set task status to active/inactive."""
    return influx_api_request("PATCH", f"/api/v2/tasks/{task_id}", {}, {"status": status})
