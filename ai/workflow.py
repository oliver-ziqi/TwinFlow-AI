"""Compatibility facade for workflow APIs.
This module re-exports public functions from modular workflow files.
"""

from ai.workflow_tools import (
    apply_branch_fix,
    rollback_branch_fix,
    verify_car_movement,
    list_tools,
    run_tool,
    resolve_args,
    TOOL_REGISTRY,
    ToolSpec,
)
from ai.workflow_graph import (
    build_workflow,
    run_workflow_with_trace,
    prepare_workflow_preview,
    execute_planned_workflow,
)

__all__ = [
    "apply_branch_fix",
    "rollback_branch_fix",
    "verify_car_movement",
    "list_tools",
    "run_tool",
    "resolve_args",
    "TOOL_REGISTRY",
    "ToolSpec",
    "build_workflow",
    "run_workflow_with_trace",
    "prepare_workflow_preview",
    "execute_planned_workflow",
]
