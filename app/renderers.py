"""UI rendering layer for Streamlit app."""

from __future__ import annotations

import hashlib
import json
import re
from html import escape
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

TWIN_PANEL_HEIGHT = 760


def format_plan(plan: Any) -> str:
    """Render planned tool steps as readable lines."""
    if not isinstance(plan, list) or not plan:
        return "(empty plan)"
    lines = []
    for i, step in enumerate(plan, start=1):
        tool = step.get("tool", "-")
        save = step.get("save", "-")
        lines.append(f"{i}. {tool} -> {save}")
    return "\n".join(lines)


def render_tool_plan_card(route: Dict[str, Any], plan: Any) -> None:
    """Render ChatGPT-like tool-call plan card for non-query execution confirmation."""
    intent = escape(str(route.get("intent", "unknown")))
    mode = escape(str(route.get("mode", "unknown")))
    rows = []
    if isinstance(plan, list):
        for idx, step in enumerate(plan, start=1):
            tool = escape(str(step.get("tool", "-")))
            save = escape(str(step.get("save", "-")))
            rows.append(
                f'<div class="tool-row"><div class="tool-index">{idx}</div>'
                f'<div class="tool-main"><div class="tool-name">{tool}</div>'
                f'<div class="tool-save">save -> {save}</div></div></div>'
            )
    rows_html = "".join(rows) if rows else '<div class="tool-save">(empty plan)</div>'
    st.markdown(
        f"""
        <div class="tool-card">
          <div class="tool-head">
            <div class="tool-title">AI Tool Plan</div>
            <div class="tool-tags">
              <span class="tool-tag">intent: {intent}</span>
              <span class="tool-tag muted">mode: {mode}</span>
            </div>
          </div>
          <div class="tool-status">Detected -> Analyzed -> Planned (waiting confirm)</div>
          <div class="tool-rows">{rows_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_analysis_output(final: Dict[str, Any]) -> str:
    """Create concise assistant output from final workflow result."""
    analysis = str(final.get("analysis", "")).strip()
    if analysis:
        return analysis
    facts = final.get("facts", {})
    return json.dumps(
        {
            "intent": facts.get("intent"),
            "summary": "No LLM analysis returned; see facts.",
            "facts_keys": list(facts.keys()),
        },
        ensure_ascii=False,
        indent=2,
    )


def parse_analysis_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse LLM analysis text into JSON object when possible."""
    if not text:
        return None

    raw = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()

    if not (raw.startswith("{") and raw.endswith("}")):
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0).strip()

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def build_result_message(
    *,
    intent: str,
    plan: Any,
    final: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """Build structured assistant message payload for component rendering."""
    analysis_text = render_analysis_output(final)
    branch_proposal = final.get("facts", {}).get("branch_fix_proposal")
    event = final.get("facts", {}).get("event") or final.get("event") or {}
    return {
        "kind": "workflow_result",
        "mode": mode,
        "intent": intent,
        "plan": plan if isinstance(plan, list) else [],
        "analysis_text": analysis_text,
        "analysis_json": parse_analysis_json(analysis_text),
        "branch_proposal": branch_proposal if isinstance(branch_proposal, dict) else None,
        "event": event if isinstance(event, dict) else {},
    }


def render_result_card(
    payload: Dict[str, Any],
    execute_pending_branch_change_fn: Any,
) -> None:
    """Render workflow result payload with user-friendly cards."""
    intent = payload.get("intent", "unknown")
    mode = payload.get("mode", "query")
    plan = payload.get("plan", [])
    analysis_json = payload.get("analysis_json")
    analysis_text = payload.get("analysis_text", "")
    proposal = payload.get("branch_proposal")
    event = payload.get("event", {})

    st.markdown(f"**Result**  \nintent: `{intent}` | mode: `{mode}`")

    if isinstance(event, dict) and event:
        st.markdown("### Detected Event")
        e_thing = event.get("thingId", "-")
        e_type = event.get("alert_type", "-")
        e_time = event.get("time", "-")
        e_msg = event.get("message", "")
        st.markdown(f"- thingId: `{e_thing}`")
        st.markdown(f"- alert_type: `{e_type}`")
        st.markdown(f"- time: `{e_time}`")
        if e_msg:
            st.warning(str(e_msg))

    with st.expander("Plan", expanded=False):
        st.code(format_plan(plan), language="text")

    if isinstance(analysis_json, dict):
        root_cause = analysis_json.get("root_cause", "-")
        confidence = analysis_json.get("confidence", "-")
        st.markdown("### Root Cause")
        st.info(str(root_cause))

        c1, c2 = st.columns([3, 1])
        c1.markdown("### Confidence")
        c2.metric("Score", f"{confidence}")

        chain = analysis_json.get("causal_chain", [])
        if isinstance(chain, list) and chain:
            st.markdown("### Causal Chain")
            for idx, item in enumerate(chain, start=1):
                if isinstance(item, dict):
                    st.markdown(f"{idx}. {item.get('description', '')}")
                else:
                    st.markdown(f"{idx}. {item}")

        evidence = analysis_json.get("evidence", [])
        if isinstance(evidence, list) and evidence:
            st.markdown("### Evidence")
            for i, e in enumerate(evidence, start=1):
                st.markdown(f"{i}. {e}")

        checks = analysis_json.get("next_checks", [])
        if isinstance(checks, list) and checks:
            st.markdown("### Next Checks")
            for i, c in enumerate(checks, start=1):
                st.markdown(f"{i}. {c}")

        with st.expander("Raw JSON", expanded=False):
            st.json(analysis_json)
    else:
        st.markdown("### Analysis")
        st.write(analysis_text)

    if isinstance(proposal, dict):
        st.markdown("### Recommended Next Action")
        if proposal.get("can_fix"):
            branch_key = proposal.get("branch_key", "-")
            old = proposal.get("current_value", "-")
            new = proposal.get("recommended_value", "-")
            reason = proposal.get("reason", "-")
            st.success(f"Suggested change `{branch_key}`: `{old}` -> `{new}`")
            st.caption(f"Reason: {reason}")

            identity = {
                "event_id": event.get("event_id"),
                "time": event.get("time"),
                "thing": event.get("thingId"),
                "branch": branch_key,
                "old": old,
                "new": new,
            }
            action_id = hashlib.sha1(
                json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:10]
            c1, c2 = st.columns(2)
            if c1.button(
                "Approve and Apply",
                key=f"proposal_confirm_{action_id}",
                use_container_width=True,
                type="primary",
            ):
                thing_id_for_verify = str(event.get("thingId") or "")
                with st.spinner("Applying branch change and verifying movement..."):
                    msg = execute_pending_branch_change_fn(proposal, thing_id_for_verify)
                st.session_state.messages.append({"role": "assistant", "content": msg})
                st.session_state.pending_branch_action = None
                st.rerun()
            if c2.button(
                "Reject and Keep Current",
                key=f"proposal_reject_{action_id}",
                use_container_width=True,
            ):
                st.session_state.messages.append(
                    {"role": "assistant", "content": "Branch change cancelled. Keeping current values."}
                )
                st.session_state.pending_branch_action = None
                st.rerun()
        else:
            st.warning(str(proposal.get("message", "No actionable branch fix proposal.")))


def render_chat_message(msg: Dict[str, Any], execute_pending_branch_change_fn: Any) -> None:
    """Render one chat message with support for structured assistant payloads."""
    role = msg.get("role", "assistant")
    content = msg.get("content")
    with st.chat_message(role):
        if isinstance(content, dict) and content.get("kind") == "workflow_result":
            render_result_card(content, execute_pending_branch_change_fn)
        else:
            st.write(content)


def render_task_card(task: Dict[str, Any], idx: int, set_influx_task_status_fn: Any) -> None:
    """Render one Influx task card with enable/disable controls."""
    tid = str(task.get("id", "-"))
    name = str(task.get("name", "unnamed-task"))
    status = str(task.get("status", "unknown"))
    flux = str(task.get("flux", ""))
    every = "-"
    offset = "-"
    if "every:" in flux:
        matched = re.search(r"every:\s*([^\s,\n]+)", flux)
        if matched:
            every = matched.group(1)
    if "offset:" in flux:
        matched = re.search(r"offset:\s*([^\s,\n]+)", flux)
        if matched:
            offset = matched.group(1)

    st.markdown(
        f"""
        <div class="task-card">
          <div class="task-title">{escape(name)}</div>
          <div class="task-meta">id: <code>{escape(tid)}</code></div>
          <div class="task-meta">status: <b>{escape(status)}</b> | every: {escape(every)} | offset: {escape(offset)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    if c1.button("Enable", key=f"task_enable_{idx}_{tid}", use_container_width=True):
        result = set_influx_task_status_fn(tid, "active")
        if result.get("ok"):
            st.success(f"Task enabled: {name}")
        else:
            st.error(f"Enable failed: {result.get('error')}")
        st.rerun()
    if c2.button("Disable", key=f"task_disable_{idx}_{tid}", use_container_width=True):
        result = set_influx_task_status_fn(tid, "inactive")
        if result.get("ok"):
            st.success(f"Task disabled: {name}")
        else:
            st.error(f"Disable failed: {result.get('error')}")
        st.rerun()


def render_threejs_panel(twin_html_path: Path, height: int = TWIN_PANEL_HEIGHT) -> None:
    """Render local Three.js digital twin page inside Streamlit."""
    st.markdown("### Digital Twin")
    if not twin_html_path.exists():
        st.error(f"Three.js page not found: {twin_html_path}")
        return
    try:
        html_text = twin_html_path.read_text(encoding="utf-8")
    except Exception as exc:
        st.error(f"Failed to read Three.js page: {exc}")
        return
    components.html(html_text, height=height, scrolling=True)
