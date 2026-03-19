"""PostgreSQL-backed config tools and fallback topology helpers."""

from __future__ import annotations

from typing import Any, Dict, List

from ai.pg import pg_fetch_config

# Built-in fallback config used when PostgreSQL config table/key is unavailable.
DEFAULT_JAAMSIM_OVERVIEW: Dict[str, Any] = {
    "legend": {"fx": "factory", "gx": "good", "px": "part"},
    "produce": {"fa": ["p1"], "fb": ["p2"], "fc": ["p3"], "fd": ["p4"]},
    "assemble": {
        "fb": [{"in": {"p1": 2, "p2": 1}, "out": "p12"}],
        "fc": [{"in": {"p1": 1, "p3": 2}, "out": "ga"}, {"in": {"p2": 2, "p3": 2}, "out": "p23"}],
        "fd": [{"in": {"p4": 3, "p23": 1}, "out": "gb"}],
    },
    "logistics_edges": [
        {"id": "e1", "to": "fb", "from": "fa", "branch": "branch1", "products": ["p1"], "branch_value": 1},
        {"id": "e2", "to": "fc", "from": "fa", "branch": "branch1", "products": ["p1"], "branch_value": 2},
        {"id": "e3", "to": "fc", "from": "fb", "branch": "branch2", "products": ["p2"], "branch_value": 1},
        {"id": "e4", "to": "fd", "from": "fb", "branch": "branch3", "products": ["p12"], "branch_value": 2},
        {"id": "e5", "to": "fd", "from": "fc", "branch": "branch4", "products": ["p23"], "branch_value": 1},
    ],
}


def tool_pg_get_config(config_key: str) -> Dict[str, Any]:
    """Load one config document from PostgreSQL by config_key."""
    try:
        cfg = pg_fetch_config(config_key)
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


def load_overview_config() -> Dict[str, Any]:
    """Load overview config with fallback keys to support multiple DB schemas."""
    for key in ("jaamsim_config", "factory_overview_v1"):
        try:
            cfg = tool_pg_get_config(key)
            if isinstance(cfg, dict) and cfg:
                return cfg
        except Exception:
            continue
    return DEFAULT_JAAMSIM_OVERVIEW


def produce_map(overview: Dict[str, Any]) -> Dict[str, List[str]]:
    """Return normalized produce map: factory -> products."""
    produce = overview.get("produce", {})
    if not isinstance(produce, dict):
        return {}

    out: Dict[str, List[str]] = {}
    for fac, goods in produce.items():
        if isinstance(goods, list):
            out[str(fac)] = [str(g) for g in goods if isinstance(g, str)]
    return out
