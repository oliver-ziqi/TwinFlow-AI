"""Workflow tool registry and execution helpers.

Implementation functions live under ai.tools.* submodules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from ai.tools import (
    apply_branch_fix,
    rollback_branch_fix,
    tool_assemble_inputs,
    tool_detect_intent,
    tool_extract_event_facts,
    tool_infer_loading,
    tool_influx_queue_metrics,
    tool_llm_incident_summary,
    tool_llm_query_summary,
    tool_locate_car_park,
    tool_pg_get_config,
    tool_propose_branch_fix,
    tool_query_product_change_1m,
    tool_read_branch_values,
    tool_required_input_queue_ids,
    tool_rule_based_rca,
    verify_car_movement,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    fn: Callable[..., Any]


TOOL_REGISTRY: Dict[str, ToolSpec] = {
    "detect_intent": ToolSpec(
        "detect_intent",
        "Route input to incident/query intent.",
        {"event": "dict"},
        {"intent": "dict"},
        tool_detect_intent,
    ),
    "extract_event_facts": ToolSpec(
        "extract_event_facts",
        "Extract normalized event fields, position, branch hints from raw event.",
        {"event": "dict"},
        {"event facts": "dict"},
        tool_extract_event_facts,
    ),
    "pg_get_config": ToolSpec(
        "pg_get_config",
        "Fetch one jaamsim_config JSON by config_key from PostgreSQL.",
        {"config_key": "str"},
        {"config": "dict"},
        tool_pg_get_config,
    ),
    "locate_car_park": ToolSpec(
        "locate_car_park",
        "Locate nearest configured car park for a given XYZ position.",
        {"car_position_xyz": "list[float]"},
        {"car_park_match": "dict"},
        tool_locate_car_park,
    ),
    "infer_loading": ToolSpec(
        "infer_loading",
        "Resolve branch routing to infer loading action/product.",
        {"branch_key": "str", "branch_value": "int"},
        {"branch_resolution": "dict"},
        tool_infer_loading,
    ),
    "assemble_inputs": ToolSpec(
        "assemble_inputs",
        "Get required upstream material map for target product.",
        {"product": "str"},
        {"required_inputs": "dict[str,int]"},
        tool_assemble_inputs,
    ),
    "required_input_queue_ids": ToolSpec(
        "required_input_queue_ids",
        "Map required inputs to queue thing IDs in current schema.",
        {"required_inputs": "dict[str,int]"},
        {"thing_ids": "list[str]"},
        tool_required_input_queue_ids,
    ),
    "influx_queue_metrics": ToolSpec(
        "influx_queue_metrics",
        "Query queue counts/statistics from InfluxDB for a time window.",
        {"thing_ids": "list[str]", "lookback": "str"},
        {"metrics": "dict"},
        tool_influx_queue_metrics,
    ),
    "query_product_change_1m": ToolSpec(
        "query_product_change_1m",
        "Query 1-minute product queue changes for default product queues.",
        {},
        {"summary": "dict"},
        tool_query_product_change_1m,
    ),
    "rule_based_rca": ToolSpec(
        "rule_based_rca",
        "Classify likely root cause using deterministic queue rules.",
        {"required_inputs": "dict", "metrics": "dict"},
        {"classification": "dict"},
        tool_rule_based_rca,
    ),
    "llm_incident_summary": ToolSpec(
        "llm_incident_summary",
        "Generate concise incident RCA JSON text from event and facts.",
        {"event": "dict", "facts": "dict"},
        {"analysis": "str"},
        tool_llm_incident_summary,
    ),
    "llm_query_summary": ToolSpec(
        "llm_query_summary",
        "Generate concise query answer JSON from query text and result.",
        {"query_text": "str", "query_result": "dict"},
        {"analysis": "str"},
        tool_llm_query_summary,
    ),
    "propose_branch_fix": ToolSpec(
        "propose_branch_fix",
        "Propose one branch change using overview config and current branch files.",
        {
            "required_inputs": "dict",
            "target_product": "str",
            "rule_classification": "dict",
            "observed_branch_key": "str",
        },
        {"proposal": "dict"},
        tool_propose_branch_fix,
    ),
}


def list_tools() -> List[Dict[str, Any]]:
    """Return public metadata for all registered tools."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "output_schema": t.output_schema,
        }
        for t in TOOL_REGISTRY.values()
    ]


def run_tool(name: str, **kwargs: Any) -> Any:
    """Execute one registered tool by name with keyword arguments."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        raise RuntimeError(f"Tool not found: {name}")
    return spec.fn(**kwargs)


def get_by_path(root: Dict[str, Any], path: str) -> Any:
    """Read nested dict value by dotted path (e.g., facts.branch.product)."""
    cur: Any = root
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def set_by_path(root: Dict[str, Any], path: str, value: Any) -> None:
    """Write nested dict value by dotted path, creating dict nodes as needed."""
    cur = root
    parts = path.split(".")
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def resolve_args(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve $path argument references against execution context."""
    out: Dict[str, Any] = {}
    for key, val in args.items():
        if isinstance(val, str) and val.startswith("$"):
            out[key] = get_by_path(ctx, val[1:])
        else:
            out[key] = val
    return out


__all__ = [
    "ToolSpec",
    "TOOL_REGISTRY",
    "list_tools",
    "run_tool",
    "get_by_path",
    "set_by_path",
    "resolve_args",
    "tool_read_branch_values",
    "apply_branch_fix",
    "rollback_branch_fix",
    "verify_car_movement",
]
