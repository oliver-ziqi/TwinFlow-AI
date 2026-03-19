"""Streamlit entrypoint: compose UI rendering and controller/action layers."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from influxdb_client import InfluxDBClient
import pandas as pd
import streamlit as st

from ai.pg import pg_list_incidents
from ai.settings import INFLUX_ORG, INFLUX_TOKEN, INFLUX_URL
from ai.workflow import execute_planned_workflow, prepare_workflow_preview
from app.controllers import (
    CONSUMER_SCRIPT,
    INFLUX_TASKS_TOKEN,
    JAAMSIM_JAR_PATH,
    LOG_DIR,
    PURGE_SCRIPT,
    ROOT_DIR,
    SIM_CFG_PATH,
    TWIN_HTML_PATH,
    WATCHER_SCRIPT,
    build_query_event,
    execute_pending_branch_change,
    format_elapsed_seconds,
    get_sim_elapsed_seconds,
    is_proc_running,
    list_influx_tasks,
    parse_branch_approval,
    reset_event_pipeline_state,
    set_influx_task_status,
    start_background_service,
    start_simulation,
    stop_background_service,
    stop_simulation,
    sync_incidents_from_db,
    tail_text_file,
    try_parse_event_json,
)
from app.renderers import (
    build_result_message,
    render_chat_message,
    render_task_card,
    render_threejs_panel,
)

TWIN_PANEL_HEIGHT = 420
INVENTORY_PANEL_HEIGHT = 420
CHAT_PANEL_HEIGHT = 900
FACTORY_BUCKET = os.getenv("INFLUX_FACTORY_BUCKET", "factory_data")
FACTORY_MEASUREMENT = os.getenv("INFLUX_FACTORY_MEASUREMENT", "factory")
FACTORY_FIELD = os.getenv("INFLUX_FACTORY_FIELD", "count")
FACTORY_THING_IDS = [
    "my_factory_factoryA",
    "my_factory_factoryB",
    "my_factory_factoryC",
    "my_factory_factoryD",
]


def query_factory_inventory_df(lookback_minutes: int = 30) -> pd.DataFrame:
    """Query factory inventory time-series from InfluxDB."""
    start = (datetime.now(UTC) - timedelta(minutes=max(1, lookback_minutes))).isoformat()
    ids = " or ".join([f'r.thingId == "{thing_id}"' for thing_id in FACTORY_THING_IDS])
    flux = f"""
from(bucket: "{FACTORY_BUCKET}")
  |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "{FACTORY_MEASUREMENT}")
  |> filter(fn: (r) => r._field == "{FACTORY_FIELD}")
  |> filter(fn: (r) => {ids})
  |> keep(columns: ["_time", "_value", "thingId"])
  |> sort(columns: ["_time"])
"""
    rows = []
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG) as client:
        tables = client.query_api().query(flux, org=INFLUX_ORG)
        for table in tables:
            for rec in table.records:
                rows.append(
                    {
                        "time": rec.get_time(),
                        "thingId": str(rec.values.get("thingId", "")),
                        "count": float(rec.get_value()),
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["time", "thingId", "count"])
    return pd.DataFrame(rows)


def render_factory_inventory_panel(lookback_minutes: int = 30) -> None:
    """Render factory inventory trend and latest counts with Streamlit native charts."""
    st.markdown("### Factory Inventory")
    try:
        df = query_factory_inventory_df(lookback_minutes=lookback_minutes)
    except Exception as exc:
        st.error(f"Failed to query Influx factory inventory: {exc}")
        return

    if df.empty:
        st.info("No inventory data in selected time window.")
        return

    pivot = (
        df.pivot_table(index="time", columns="thingId", values="count", aggfunc="last")
        .sort_index()
        .ffill()
    )
    st.line_chart(pivot, height=260, use_container_width=True)

    latest_by_id = (
        df.sort_values("time")
        .groupby("thingId", as_index=False)
        .last()[["thingId", "count"]]
        .set_index("thingId")["count"]
        .to_dict()
    )
    labels = [
        ("Factory A", "my_factory_factoryA"),
        ("Factory B", "my_factory_factoryB"),
        ("Factory C", "my_factory_factoryC"),
        ("Factory D", "my_factory_factoryD"),
    ]
    cols = st.columns(4)
    for idx, (label, thing_id) in enumerate(labels):
        val = latest_by_id.get(thing_id)
        cols[idx].metric(label, "-" if val is None else f"{val:.0f}")


def init_session_state() -> None:
    """Initialize Streamlit session keys used by the app."""
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    "You can ask directly, or paste an incident JSON. "
                    "Non-query requests show a plan first and wait for your confirmation."
                ),
            }
        ]
    if "pending_workflow" not in st.session_state:
        st.session_state.pending_workflow = None
    if "pending_branch_action" not in st.session_state:
        st.session_state.pending_branch_action = None
    if "incident_seen_ids" not in st.session_state:
        st.session_state.incident_seen_ids = set()
    if "incident_baseline_id" not in st.session_state:
        st.session_state.incident_baseline_id = 0
    if "auto_sync_db_incidents" not in st.session_state:
        st.session_state.auto_sync_db_incidents = True
    if "proc_watcher" not in st.session_state:
        st.session_state.proc_watcher = None
    if "proc_consumer" not in st.session_state:
        st.session_state.proc_consumer = None
    if "proc_simulation" not in st.session_state:
        st.session_state.proc_simulation = None
    if "sim_timer_running" not in st.session_state:
        st.session_state.sim_timer_running = False
    if "sim_timer_started_at" not in st.session_state:
        st.session_state.sim_timer_started_at = None
    if "sim_timer_base_sec" not in st.session_state:
        st.session_state.sim_timer_base_sec = 0.0


def clear_chat_history() -> None:
    """Clear messages and reset baseline for incremental incident sync."""
    baseline = 0
    try:
        current = pg_list_incidents(limit=1)
        if isinstance(current, list) and current:
            baseline = int(current[0].get("id") or 0)
    except Exception:
        baseline = int(st.session_state.get("incident_baseline_id", 0))

    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "You can ask directly, or paste an incident JSON. "
                "Non-query requests show a plan first and wait for your confirmation."
            ),
        }
    ]
    st.session_state.pending_workflow = None
    st.session_state.pending_branch_action = None
    st.session_state.incident_seen_ids = set()
    st.session_state.incident_baseline_id = baseline
    st.success("Chat history cleared.")
    st.rerun()


st.set_page_config(page_title="AI Incident Copilot", page_icon="A", layout="wide")

st.markdown(
    """
<style>
  .block-container {max-width: 1600px; padding-top: 4.8rem !important; padding-bottom: 1.2rem;}
  [data-testid="stAppViewContainer"] > .main {padding-top: 0 !important;}
  .app-title {font-size: 1.45rem; font-weight: 700; margin-bottom: .25rem;}
  .app-subtitle {color: #64748b; font-size: .95rem; margin-bottom: 1rem;}
  .pending-box {
    border: 1px solid #dbe3ef; border-radius: 12px; padding: 12px;
    background: linear-gradient(180deg,#ffffff 0%,#f8fafc 100%);
  }
  .small-note {color:#64748b; font-size:.85rem;}
  .tool-card {
    border: 1px solid #d4ddec;
    border-radius: 14px;
    padding: 12px 12px 8px 12px;
    background: linear-gradient(180deg,#ffffff 0%,#f6f9ff 100%);
    box-shadow: 0 8px 22px rgba(12, 25, 50, .08);
    margin-bottom: 10px;
  }
  .tool-head {display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px;}
  .tool-title {font-weight:700; color:#0f172a;}
  .tool-tags {display:flex; gap:8px; flex-wrap:wrap;}
  .tool-tag {font-size:.78rem; border:1px solid #c7d4ea; border-radius:999px; padding:2px 8px; background:#eff6ff; color:#1e3a8a;}
  .tool-tag.muted {background:#f1f5f9; color:#334155; border-color:#d6dee9;}
  .tool-status {font-size:.86rem; color:#334155; margin-bottom:8px;}
  .tool-row {display:flex; gap:10px; align-items:flex-start; padding:8px 0; border-top:1px dashed #e3eaf5;}
  .tool-row:first-child {border-top:0;}
  .tool-index {
    width:22px; height:22px; border-radius:50%;
    background:#dbeafe; color:#1e40af; font-size:.78rem; font-weight:700;
    display:flex; align-items:center; justify-content:center; margin-top:1px;
  }
  .tool-name {font-size:.9rem; font-weight:600; color:#0f172a;}
  .tool-save {font-size:.8rem; color:#64748b;}
  .task-card {
    border: 1px solid #dbe3ef;
    border-radius: 12px;
    padding: 12px;
    background: linear-gradient(180deg,#ffffff 0%,#f8fbff 100%);
    margin-bottom: 8px;
  }
  .task-title {font-size: 1rem; font-weight: 700; color: #0f172a;}
  .task-meta {font-size: .85rem; color: #475569; margin-top: 2px;}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown('<div class="app-title">AI Incident Copilot</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="app-subtitle">Incident-driven analysis with confirmation before execution. Query requests return results directly.</div>',
    unsafe_allow_html=True,
)

init_session_state()

with st.sidebar:
    st.header("Runtime")
    st.session_state.auto_sync_db_incidents = st.toggle(
        "Auto sync DB incidents (3s)", value=st.session_state.auto_sync_db_incidents
    )
    if st.button("Clear Chat History", use_container_width=True):
        clear_chat_history()

    st.divider()
    st.subheader("Influx")
    if st.button("Delete history (4 buckets)", use_container_width=True):
        with st.spinner("Deleting historical series data..."):
            run = subprocess.run(
                [sys.executable, str(PURGE_SCRIPT)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
        if run.returncode == 0:
            st.success("Influx buckets purged.")
        else:
            st.error("Influx purge failed.")
        if run.stdout:
            st.code(run.stdout, language="text")
        if run.stderr:
            st.code(run.stderr, language="text")

    st.divider()
    st.subheader("Kafka Services")
    c1, c2 = st.columns(2)
    if c1.button("Start Watcher", use_container_width=True):
        st.info(start_background_service("watcher", WATCHER_SCRIPT))
    if c2.button("Stop Watcher", use_container_width=True):
        st.info(stop_background_service("watcher"))

    c3, c4 = st.columns(2)
    if c3.button("Start Consumer", use_container_width=True):
        st.info(start_background_service("consumer", CONSUMER_SCRIPT))
    if c4.button("Stop Consumer", use_container_width=True):
        st.info(stop_background_service("consumer"))
    if st.button("Reset Watcher/Consumer State", use_container_width=True):
        st.info(reset_event_pipeline_state())

    watcher_proc = st.session_state.get("proc_watcher")
    consumer_proc = st.session_state.get("proc_consumer")
    st.caption(f"Watcher: {'running' if is_proc_running(watcher_proc) else 'stopped'}")
    st.caption(f"Consumer: {'running' if is_proc_running(consumer_proc) else 'stopped'}")
    with st.expander("Kafka Logs", expanded=False):
        st.caption("Watcher log")
        st.code(tail_text_file(LOG_DIR / "watcher.log"), language="text")
        st.caption("Consumer log")
        st.code(tail_text_file(LOG_DIR / "consumer.log"), language="text")

    st.divider()
    st.subheader("Simulation")
    s1, s2 = st.columns(2)
    if s1.button("Start Simulation", use_container_width=True):
        st.info(start_simulation())
    if s2.button("Stop Simulation", use_container_width=True):
        st.info(stop_simulation())

    @st.fragment(run_every="1s")
    def simulation_status_fragment() -> None:
        sim_proc = st.session_state.get("proc_simulation")
        sim_running = is_proc_running(sim_proc)
        if not sim_running and st.session_state.get("sim_timer_running"):
            st.session_state.sim_timer_base_sec = get_sim_elapsed_seconds()
            st.session_state.sim_timer_running = False
            st.session_state.sim_timer_started_at = None

        st.caption(f"Simulation: {'running' if sim_running else 'stopped'}")
        st.caption(f"Elapsed: {format_elapsed_seconds(get_sim_elapsed_seconds())}")

    simulation_status_fragment()
    st.caption(f"cfg: {SIM_CFG_PATH}")
    st.caption(f"jar: {JAAMSIM_JAR_PATH}")


@st.fragment(run_every="3s")
def incident_sync_fragment() -> None:
    """Background-ish polling fragment for near-real-time incident ingestion."""
    if not st.session_state.auto_sync_db_incidents:
        return
    try:
        added = sync_incidents_from_db(build_result_message, limit=20)
        if added > 0:
            st.rerun()
    except Exception as exc:
        st.caption(f"DB sync error: {exc}")


incident_sync_fragment()

tab_live, tab_task = st.tabs(["Live", "Task"])

with tab_live:
    col_left, col_chat = st.columns([3, 2], gap="large")

    with col_left:
        render_threejs_panel(TWIN_HTML_PATH, height=TWIN_PANEL_HEIGHT)
        render_factory_inventory_panel(lookback_minutes=30)

    with col_chat:
        st.markdown("### AI Chat")
        chat_scroll_box = st.container(height=CHAT_PANEL_HEIGHT, border=True)
        with chat_scroll_box:
            for msg in st.session_state.messages[-30:]:
                render_chat_message(msg, execute_pending_branch_change)

            if st.session_state.pending_branch_action:
                pending_action = st.session_state.pending_branch_action
                proposal = pending_action.get("proposal", {})
                thing_id_for_verify = str(pending_action.get("thing_id", ""))
                st.markdown('<div class="pending-box">', unsafe_allow_html=True)
                st.markdown("**Pending Confirmation: Branch Change**")
                if proposal.get("can_fix"):
                    st.code(
                        f"{proposal.get('branch_key')} : {proposal.get('current_value')} -> {proposal.get('recommended_value')}",
                        language="text",
                    )
                    st.markdown(
                        f'<div class="small-note">reason: {proposal.get("reason", "-")}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.warning(str(proposal.get("message", "No actionable branch fix proposal.")))
                c1, c2 = st.columns(2)
                if proposal.get("can_fix"):
                    if c1.button("Confirm Branch Change", use_container_width=True, type="primary"):
                        with st.spinner("Applying branch change and verifying movement..."):
                            msg = execute_pending_branch_change(proposal, thing_id_for_verify)
                        st.session_state.messages.append({"role": "assistant", "content": msg})
                        st.session_state.pending_branch_action = None
                        st.rerun()
                if c2.button("Cancel", use_container_width=True):
                    st.session_state.messages.append(
                        {"role": "assistant", "content": "Branch change cancelled. Keeping current values."}
                    )
                    st.session_state.pending_branch_action = None
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

        user_text = st.chat_input("Enter a query, or paste an incident JSON...")
        if user_text:
            st.session_state.messages.append({"role": "user", "content": user_text})

            pending_action = st.session_state.get("pending_branch_action")
            if pending_action:
                decision = parse_branch_approval(user_text)
                proposal = pending_action.get("proposal", {}) if isinstance(pending_action, dict) else {}
                thing_id_for_verify = str((pending_action or {}).get("thing_id", ""))
                if decision is True:
                    with st.spinner("Applying branch change and verifying movement..."):
                        msg = execute_pending_branch_change(proposal, thing_id_for_verify)
                    st.session_state.messages.append({"role": "assistant", "content": msg})
                    st.session_state.pending_branch_action = None
                    st.rerun()
                if decision is False:
                    st.session_state.messages.append(
                        {"role": "assistant", "content": "Branch change cancelled. Keeping current values."}
                    )
                    st.session_state.pending_branch_action = None
                    st.rerun()

                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "A branch change is waiting for approval. "
                            "Reply with 'approve/confirm' or 'reject/cancel' first."
                        ),
                    }
                )
                st.rerun()

            event = try_parse_event_json(user_text) or build_query_event(user_text)
            preview = prepare_workflow_preview(event)
            route = preview.get("route", {})
            intent = str(route.get("intent", "unknown"))
            mode = str(route.get("mode", "query"))

            if mode == "query":
                with st.spinner("AI is running query analysis..."):
                    with st.status("Executing query workflow", expanded=True) as status:
                        status.write("Running tools...")
                        final = execute_planned_workflow(preview)
                        status.write("Formatting result...")
                        status.update(label="Query complete", state="complete")
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": build_result_message(
                            intent=intent,
                            plan=preview.get("plan", []),
                            final=final,
                            mode=mode,
                        ),
                    }
                )
            else:
                with st.spinner("AI is analyzing incident and generating a fix proposal..."):
                    with st.status("Analyzing incident", expanded=True) as status:
                        status.write("Running RCA tools...")
                        final = execute_planned_workflow(preview)
                        status.write("Generating branch change proposal...")
                        status.update(label="Analysis complete", state="complete")
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": build_result_message(
                            intent=intent,
                            plan=preview.get("plan", []),
                            final=final,
                            mode=mode,
                        ),
                    }
                )
                proposal = final.get("facts", {}).get("branch_fix_proposal")
                if isinstance(proposal, dict):
                    thing_id_for_verify = str(
                        final.get("facts", {}).get("thing_id_norm")
                        or final.get("facts", {}).get("thing_id")
                        or ""
                    )
                    st.session_state.pending_branch_action = {
                        "proposal": proposal,
                        "thing_id": thing_id_for_verify,
                    }
                    if proposal.get("can_fix"):
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
            st.rerun()

with tab_task:
    st.markdown("### Influx Tasks")
    st.caption("This page auto-loads tasks in the current org. Each card can be toggled independently.")
    if not INFLUX_TASKS_TOKEN:
        st.error("Missing Influx token. Set INFLUX_TOKEN to load tasks.")
    else:
        resp = list_influx_tasks()
        if not resp.get("ok"):
            st.error(f"Failed to load tasks: {resp.get('error')}")
        else:
            tasks = resp.get("data", {}).get("tasks", [])
            if not tasks:
                st.info("No tasks found in the current org.")
            else:
                cols = st.columns(2)
                for i, task in enumerate(tasks):
                    with cols[i % 2]:
                        render_task_card(task, i, set_influx_task_status)
