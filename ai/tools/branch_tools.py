"""Branch-routing and movement related tools."""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.settings import BRANCH_FILES_DIR, DITTO_HTTP_BASE, DITTO_PASS, DITTO_USER
from ai.tools.pg_tools import load_overview_config, tool_pg_get_config


def euclidean_2d(a: List[float], b: List[float]) -> float:
    """Return Euclidean distance between two 2D points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def normalize_thing_id(thing_id: str) -> str:
    """Normalize underscore-style thing IDs to Ditto-style namespace format."""
    if ":" in thing_id:
        return thing_id
    matched = re.match(r"^my_(logistics|factory|queue)_(.+)$", thing_id)
    if matched:
        return f"my.{matched.group(1)}:{matched.group(2)}"
    return thing_id


def normalize_branch_key(branch_key: Any) -> str:
    """Normalize branch key variants such as branch_3 / branch-3 -> branch3."""
    value = str(branch_key or "").strip().lower()
    matched = re.fullmatch(r"branch[_-]?([1-9]\d*)", value)
    if matched:
        return f"branch{matched.group(1)}"
    return value


def parse_car_position_from_msg(msg: str) -> Optional[List[float]]:
    """Extract {x m, y m} coordinates from alert message text if present."""
    pattern = r"\{\s*([-\d.]+)\s*m\s*,\s*([-\d.]+)\s*m\s*\}"
    matched = re.search(pattern, msg, re.IGNORECASE)
    if not matched:
        return None
    return [float(matched.group(1)), float(matched.group(2)), 0.0]


def locate_car_park(car_pos_xyz: List[float], car_park_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Find nearest configured car park to the given vehicle XYZ position."""
    car_parks = car_park_cfg.get("car_parks", {})
    best_id: Optional[str] = None
    best_dist = float("inf")
    for park_id, meta in car_parks.items():
        pos = meta.get("position")
        if not pos or len(pos) < 2:
            continue
        dist = euclidean_2d([car_pos_xyz[0], car_pos_xyz[1]], [pos[0], pos[1]])
        if dist < best_dist:
            best_dist = dist
            best_id = park_id

    if best_id is None:
        raise RuntimeError("No car park matched (check car_park_position_config).")

    return {
        "car_park_id": best_id,
        "distance": best_dist,
        "car_pos": car_pos_xyz,
        "car_park_pos": car_parks[best_id]["position"],
    }


def infer_loading_from_edges(overview_cfg: Dict[str, Any], branch_key: str, branch_value: int) -> Dict[str, Any]:
    """Resolve loading target from overview logistics_edges using branch/value."""
    edges = overview_cfg.get("logistics_edges", [])
    norm_key = normalize_branch_key(branch_key)
    for edge in edges:
        edge_key = normalize_branch_key(edge.get("branch", ""))
        if edge_key != norm_key:
            continue
        try:
            edge_value = int(edge.get("branch_value", 0))
        except Exception:
            continue
        if edge_value != int(branch_value):
            continue

        products = edge.get("products", [])
        product = str(products[0]) if isinstance(products, list) and products else None
        return {
            "branch_key": norm_key,
            "branch_value": int(branch_value),
            "action": "load",
            "product": product,
            "edge_id": edge.get("id"),
            "from": edge.get("from"),
            "to": edge.get("to"),
            "description": "resolved from logistics_edges",
        }
    raise RuntimeError(f"No logistics_edges route for {norm_key} value={branch_value}")


def get_assemble_inputs(assemble_cfg: Dict[str, Any], output_product: str) -> Dict[str, int]:
    """Return required input materials for a target output product."""
    assembles = assemble_cfg.get("assembles", {})
    for _, rule in assembles.items():
        if rule.get("output") == output_product:
            inputs = rule.get("inputs", {})
            return {k: int(v) for k, v in inputs.items()}

    assemble_map = assemble_cfg.get("assemble", {})
    if isinstance(assemble_map, dict):
        for _, rules in assemble_map.items():
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                if rule.get("out") == output_product:
                    inputs = rule.get("in", {})
                    return {k: int(v) for k, v in inputs.items()}

    raise RuntimeError(f"No assemble recipe found producing {output_product}")


def tool_locate_car_park(car_position_xyz: List[float]) -> Dict[str, Any]:
    """Locate nearest car park using configured park positions."""
    cfg = tool_pg_get_config("car_park_position_config")
    return locate_car_park(car_position_xyz, cfg)


def tool_read_branch_values() -> Dict[str, int]:
    """Read branch1..branch4 values from simulation files."""
    values: Dict[str, int] = {}
    for i in range(1, 5):
        key = f"branch{i}"
        path_txt = BRANCH_FILES_DIR / f"{key}.txt"
        path_legacy = BRANCH_FILES_DIR / key
        path = path_txt if path_txt.exists() else path_legacy
        try:
            raw = path.read_text(encoding="utf-8").strip()
            values[key] = int(raw)
        except Exception:
            values[key] = 0
    return values


def tool_infer_loading(branch_key: str, branch_value: int) -> Dict[str, Any]:
    """Infer loading product from branch files + overview edges."""
    norm_key = normalize_branch_key(branch_key)
    file_values = tool_read_branch_values()
    effective_value = file_values.get(norm_key)
    if effective_value is None or int(effective_value) <= 0:
        effective_value = int(branch_value)

    overview = load_overview_config()
    try:
        result = infer_loading_from_edges(overview, norm_key, int(effective_value))
    except Exception:
        fallback = {
            ("branch1", 1): "p1",
            ("branch1", 2): "p1",
            ("branch2", 1): "p2",
            ("branch3", 1): "p23",
            ("branch3", 2): "p12",
            ("branch4", 1): "p23",
        }
        product = fallback.get((norm_key, int(effective_value)))
        result = {
            "branch_key": norm_key,
            "branch_value": int(effective_value),
            "action": "load",
            "product": product,
            "description": "fallback mapping (no DB route found)",
        }

    result["branch_value_from_file"] = int(effective_value)
    result["current_branch_values"] = file_values
    return result


def tool_assemble_inputs(product: str) -> Dict[str, int]:
    """Map product to upstream assemble input requirements."""
    assemble_cfg = tool_pg_get_config("assemble_config")
    if not assemble_cfg:
        assemble_cfg = load_overview_config()
    return get_assemble_inputs(assemble_cfg, product)


def tool_required_input_queue_ids(required_inputs: Dict[str, int]) -> List[str]:
    """Map required inputs to queue thing IDs in current naming convention."""
    return [f"my_queue_fc_{product}" for product in required_inputs.keys()]


def apply_branch_fix(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Apply confirmed branch file change with backup."""
    if not proposal or not proposal.get("can_fix"):
        return {"applied": False, "message": "No actionable proposal."}

    branch_key = str(proposal.get("branch_key", ""))
    if not re.fullmatch(r"branch[1-4]", branch_key):
        raise RuntimeError(f"Invalid branch key: {branch_key}")

    rec = int(proposal.get("recommended_value"))
    path_txt = BRANCH_FILES_DIR / f"{branch_key}.txt"
    path_legacy = BRANCH_FILES_DIR / branch_key
    path = path_txt if path_txt.exists() else path_legacy
    if not path.exists():
        raise RuntimeError(f"Branch file not found: {path}")

    old_raw = path.read_text(encoding="utf-8").strip()
    old_val = int(old_raw)
    backup = path.with_suffix(".bak")
    backup.write_text(str(old_val), encoding="utf-8")

    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(rec), encoding="utf-8")
    os.replace(tmp, path)

    return {
        "applied": True,
        "branch_key": branch_key,
        "old_value": old_val,
        "new_value": rec,
        "file_path": str(path),
        "backup_path": str(backup),
    }


def rollback_branch_fix(change_result: Dict[str, Any]) -> Dict[str, Any]:
    """Rollback branch change from backup file."""
    if not change_result or not change_result.get("applied"):
        return {"rolled_back": False, "message": "No applied change to rollback."}

    branch_key = str(change_result.get("branch_key", ""))
    path_txt = BRANCH_FILES_DIR / f"{branch_key}.txt"
    path_legacy = BRANCH_FILES_DIR / branch_key
    path = path_txt if path_txt.exists() else path_legacy
    backup = Path(str(change_result.get("backup_path", "")))
    if not backup.exists():
        return {"rolled_back": False, "message": f"Backup not found: {backup}"}

    backup_value = int(backup.read_text(encoding="utf-8").strip())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(backup_value), encoding="utf-8")
    os.replace(tmp, path)
    return {
        "rolled_back": True,
        "branch_key": branch_key,
        "restored_value": backup_value,
        "file_path": str(path),
    }


def _extract_ditto_position(payload: Dict[str, Any]) -> Optional[List[float]]:
    try:
        rows = payload if isinstance(payload, list) else [payload]
        if not rows:
            return None
        item = rows[0]
        pos = (
            item.get("features", {})
            .get("status", {})
            .get("properties", {})
            .get("features", {})
            .get("status", {})
            .get("properties", {})
            .get("position", {})
        )
        if not isinstance(pos, dict):
            return None
        x = float(pos.get("x"))
        y = float(pos.get("y"))
        z = float(pos.get("z", 0))
        return [x, y, z]
    except Exception:
        return None


def _ditto_get_thing_position(thing_id_norm: str) -> Optional[List[float]]:
    thing_q = urllib.parse.quote(thing_id_norm, safe="")
    fields = urllib.parse.quote("thingId,features", safe=",")
    url = f"{DITTO_HTTP_BASE}/api/2/things?ids={thing_q}&fields={fields}"

    req = urllib.request.Request(url=url, method="GET")
    token = f"{DITTO_USER}:{DITTO_PASS}".encode("utf-8")
    req.add_header("Authorization", "Basic " + base64.b64encode(token).decode("utf-8"))
    req.add_header("accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        return _extract_ditto_position(data)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def verify_car_movement(
    thing_id: str,
    wait_sec: int = 20,
    min_delta: float = 0.15,
    poll_interval_sec: int = 4,
) -> Dict[str, Any]:
    """Verify whether a car starts moving by polling Ditto position before/after change."""
    thing_id_norm = normalize_thing_id(thing_id)
    start = _ditto_get_thing_position(thing_id_norm)
    if not start:
        return {
            "verified": False,
            "moved": False,
            "reason": "start_position_unavailable",
            "thing_id": thing_id_norm,
        }

    deadline = time.time() + max(1, wait_sec)
    last = start
    while time.time() < deadline:
        time.sleep(max(1, poll_interval_sec))
        pos = _ditto_get_thing_position(thing_id_norm)
        if pos:
            last = pos
            dist = euclidean_2d([start[0], start[1]], [pos[0], pos[1]])
            if dist >= min_delta:
                return {
                    "verified": True,
                    "moved": True,
                    "distance": dist,
                    "start_position": start,
                    "last_position": pos,
                    "thing_id": thing_id_norm,
                }

    end_dist = euclidean_2d([start[0], start[1]], [last[0], last[1]])
    return {
        "verified": True,
        "moved": end_dist >= min_delta,
        "distance": end_dist,
        "start_position": start,
        "last_position": last,
        "thing_id": thing_id_norm,
    }
