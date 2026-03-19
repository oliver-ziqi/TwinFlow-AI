from typing import Any, Dict, List

from langgraph.graph import END, StateGraph

from ai.types import WorkflowState
from ai.utils import deep_merge
from ai.workflow_tools import resolve_args, run_tool, list_tools, set_by_path

def analyze_node(state: WorkflowState) -> WorkflowState:
    """LangGraph node: detect intent and build initial normalized facts."""
    event = state["event"]
    route = run_tool("detect_intent", event=event)
    facts: Dict[str, Any] = {"event": event, "intent": route.get("intent")}

    if route.get("intent") == "incident_rca":
        facts.update(run_tool("extract_event_facts", event=event))
    else:
        facts["query_text"] = route.get("query_text", "")

    return {**state, "route": route, "facts": facts}


def plan_node(state: WorkflowState) -> WorkflowState:
    """LangGraph node: build tool execution plan based on intent."""
    route = state.get("route", {})
    intent = route.get("intent", "unknown")
    plan: List[Dict[str, Any]] = []

    if intent == "incident_rca":
        if state.get("facts", {}).get("car_position_xyz"):
            plan.append({
                "tool": "locate_car_park",
                "args": {"car_position_xyz": "$facts.car_position_xyz"},
                "save": "facts.car_park_match",
            })
        plan.extend([
            {
                "tool": "infer_loading",
                "args": {
                    "branch_key": "$facts.branch_key",
                    "branch_value": "$facts.branch_value",
                },
                "save": "facts.branch",
            },
            {
                "tool": "assemble_inputs",
                "args": {"product": "$facts.branch.product"},
                "save": "facts.assemble.required_inputs",
            },
            {
                "tool": "required_input_queue_ids",
                "args": {"required_inputs": "$facts.assemble.required_inputs"},
                "save": "facts.assemble.queue_thing_ids",
            },
            {
                "tool": "influx_queue_metrics",
                "args": {
                    "thing_ids": "$facts.assemble.queue_thing_ids",
                    "lookback": "-1m",
                },
                "save": "facts.influx_queue_metrics_1m",
            },
            {
                "tool": "rule_based_rca",
                "args": {
                    "required_inputs": "$facts.assemble.required_inputs",
                    "metrics": "$facts.influx_queue_metrics_1m",
                },
                "save": "facts.rule_classification",
            },
            {
                "tool": "propose_branch_fix",
                "args": {
                    "required_inputs": "$facts.assemble.required_inputs",
                    "target_product": "$facts.branch.product",
                    "rule_classification": "$facts.rule_classification",
                    "observed_branch_key": "$facts.branch_key",
                },
                "save": "facts.branch_fix_proposal",
            },
            {
                "tool": "llm_incident_summary",
                "args": {"event": "$event", "facts": "$facts"},
                "save": "analysis",
            },
        ])
    elif intent == "query_product_change_1m":
        plan.extend([
            {"tool": "query_product_change_1m", "args": {}, "save": "facts.query_result"},
            {
                "tool": "llm_query_summary",
                "args": {
                    "query_text": "$facts.query_text",
                    "query_result": "$facts.query_result",
                },
                "save": "analysis",
            },
        ])
    else:
        plan.append({
            "tool": "llm_query_summary",
            "args": {
                "query_text": "$facts.query_text",
                "query_result": {"note": "unsupported intent"},
            },
            "save": "analysis",
        })

    return {**state, "plan": plan}


def execute_node(state: WorkflowState) -> WorkflowState:
    """LangGraph node: execute planned tools and persist outputs in state."""
    event = state.get("event", {})
    facts = state.get("facts", {})
    analysis = state.get("analysis", "")
    execution_log: List[Dict[str, Any]] = []
    plan = state.get("plan", [])

    ctx: Dict[str, Any] = {"event": event, "facts": facts, "analysis": analysis}
    for i, step in enumerate(plan, start=1):
        tool_name = step["tool"]
        save_path = step.get("save")
        raw_args = step.get("args", {})
        args = resolve_args(raw_args, ctx)

        result = run_tool(tool_name, **args)
        execution_log.append({"step": i, "tool": tool_name, "args": args, "save": save_path})

        if save_path:
            set_by_path(ctx, save_path, result)

    return {
        **state,
        "facts": ctx.get("facts", facts),
        "analysis": ctx.get("analysis", analysis),
        "execution_log": execution_log,
    }


# ---------------- Graph ----------------


def build_workflow():
    """Build and compile the 3-node LangGraph workflow."""
    g = StateGraph(WorkflowState)
    g.add_node("analyze", analyze_node)
    g.add_node("plan", plan_node)
    g.add_node("execute", execute_node)
    g.set_entry_point("analyze")
    g.add_edge("analyze", "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", END)
    return g.compile()


def run_workflow_with_trace(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run workflow and return merged state with node-by-node trace."""
    workflow = build_workflow()
    final_state: Dict[str, Any] = {}
    trace: List[Dict[str, Any]] = []

    for update in workflow.stream({"event": event}, stream_mode="updates"):
        for node, data in update.items():
            trace.append({"node": node, "data": data})
            deep_merge(final_state, data)

    final_state["trace"] = trace
    final_state["tools"] = list_tools()
    return final_state


def prepare_workflow_preview(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run analyze+plan only, returning route/facts/plan for user confirmation."""
    state: WorkflowState = {"event": event}
    state = analyze_node(state)
    state = plan_node(state)
    return {
        "event": event,
        "route": state.get("route", {}),
        "facts": state.get("facts", {}),
        "plan": state.get("plan", []),
        "tools": list_tools(),
    }


def execute_planned_workflow(preview_state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a precomputed plan state and return the final merged workflow state."""
    state: WorkflowState = {
        "event": preview_state.get("event", {}),
        "route": preview_state.get("route", {}),
        "facts": preview_state.get("facts", {}),
        "plan": preview_state.get("plan", []),
        "analysis": preview_state.get("analysis", ""),
    }
    return execute_node(state)
