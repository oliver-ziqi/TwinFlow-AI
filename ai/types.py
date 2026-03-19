from typing import Any, Dict, List, TypedDict, Optional

class AlertEvent(TypedDict, total=False):
    time: str
    thingId: str
    alert_type: str
    message: str
    source: str
    car_position: List[float]
    branch_key: str
    branch_value: int

class WorkflowState(TypedDict, total=False):
    event: Dict[str, Any]
    route: Dict[str, Any]
    plan: List[Dict[str, Any]]
    facts: Dict[str, Any]
    execution_log: List[Dict[str, Any]]
    trace: List[Dict[str, Any]]   # list of per-node updates
    analysis: str
    tools: List[Dict[str, Any]]
