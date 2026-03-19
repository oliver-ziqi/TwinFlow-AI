"""RCA and intent-analysis tools."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import openai

from ai.settings import TEMPERATURE
from ai.tools.branch_tools import (
    normalize_branch_key,
    normalize_thing_id,
    parse_car_position_from_msg,
    tool_read_branch_values,
)
from ai.tools.pg_tools import load_overview_config, produce_map

HARDCODED_LLM_MODEL = "gemini-2.0-flash"
HARDCODED_AIHUBMIX_BASE_URL = "https://aihubmix.com/v1"
HARDCODED_AIHUBMIX_API_KEY = "sk-pPu8NXQEM6v3eoVz8aB8Be7588784058Ac28D7A3E30f227b"


def _build_client() -> openai.OpenAI:
    """Build OpenAI-compatible client with fixed parameters."""
    return openai.OpenAI(
        api_key=HARDCODED_AIHUBMIX_API_KEY,
        base_url=HARDCODED_AIHUBMIX_BASE_URL,
    )


def _invoke_llm(system_prompt: str, human_prompt: str) -> str:
    """Call chat.completions using OpenAI-compatible endpoint (AiHubMix)."""
    client = _build_client()
    resp = client.chat.completions.create(
        model=HARDCODED_LLM_MODEL,
        temperature=TEMPERATURE,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": human_prompt},
        ],
    )
    content = resp.choices[0].message.content if resp.choices else ""
    return str(content or "")


def _extract_shortage_products(rule_classification: Dict[str, Any]) -> List[str]:
    """Extract product IDs considered starved from structured fields or evidence text."""
    products: List[str] = []

    for key in ("shortage_products", "weak_supply_products"):
        vals = rule_classification.get(key, [])
        if isinstance(vals, list):
            for product in vals:
                if isinstance(product, str) and product:
                    products.append(product)

    if products:
        return list(dict.fromkeys(products))

    for line in rule_classification.get("evidence", []):
        if not isinstance(line, str):
            continue
        matched = re.match(r"^([A-Za-z]\w*)\s+queue\b", line.strip())
        if matched:
            products.append(matched.group(1))

    return list(dict.fromkeys(products))


def tool_extract_event_facts(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and normalize event fields into structured workflow facts."""
    msg = str(event.get("message", ""))
    pos = event.get("car_position")
    if not (isinstance(pos, list) and len(pos) >= 2):
        pos = parse_car_position_from_msg(msg)

    branch_key = event.get("branch_key")
    branch_value = event.get("branch_value")
    if not branch_key:
        key_match = re.search(r"\b(branch[_-]?\d+)\b", msg, re.IGNORECASE)
        branch_key = key_match.group(1) if key_match else "branch_3"
    if branch_value is None:
        value_match = re.search(r"branch(?:\s*value)?\s*[=:]\s*(\d+)", msg, re.IGNORECASE)
        branch_value = int(value_match.group(1)) if value_match else 1

    return {
        "event_id": event.get("event_id"),
        "event_time": event.get("time"),
        "thing_id": event.get("thingId", "unknown"),
        "thing_id_norm": normalize_thing_id(str(event.get("thingId", "unknown"))),
        "alert_type": event.get("alert_type", "unknown"),
        "message": msg,
        "car_position_xyz": pos,
        "branch_key": normalize_branch_key(branch_key) or str(branch_key),
        "branch_value": int(branch_value),
    }


def tool_detect_intent(event: Dict[str, Any]) -> Dict[str, Any]:
    """Classify input as incident RCA or user query intent."""
    alert_type = str(event.get("alert_type", "")).strip()
    query = str(event.get("query", "") or event.get("user_query", "")).strip()
    message = str(event.get("message", "")).strip()
    text = query or message
    lower = text.lower()

    if alert_type:
        return {"intent": "incident_rca", "mode": "event", "query_text": text}
    if "1min" in lower or "1 min" in lower or "last 1 min" in lower or "past 1 min" in lower:
        if "product" in lower:
            return {"intent": "query_product_change_1m", "mode": "query", "query_text": text}

    return {"intent": "unknown", "mode": "query", "query_text": text}


def tool_rule_based_rca(required_inputs: Dict[str, int], metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Classify likely root-cause using deterministic supply rules."""
    stats = metrics.get("stats", {})
    shortages = []
    weak_supply = []
    for product, required_qty in required_inputs.items():
        tid = f"my_queue_fc_{product}"
        last = (stats.get(tid) or {}).get("last")
        if last is None:
            weak_supply.append((product, "no_data"))
        elif float(last) <= 0:
            shortages.append((product, float(last)))
        elif float(last) < required_qty:
            weak_supply.append((product, float(last)))

    shortage_products = [product for product, _ in shortages]
    weak_supply_products = [product for product, _ in weak_supply if product not in shortage_products]

    if shortages:
        root = "input-starved"
        evidence = [f"{product} queue last={value} <= 0" for product, value in shortages]
    elif weak_supply:
        root = "input-weak-supply"
        evidence = [f"{product} queue last={value} < required" for product, value in weak_supply]
    else:
        root = "unknown-or-downstream-blocked"
        evidence = ["Required inputs are not obviously empty in last snapshot."]

    return {
        "classification": root,
        "evidence": evidence,
        "shortage_products": shortage_products,
        "weak_supply_products": weak_supply_products,
    }


def tool_propose_branch_fix(
    required_inputs: Dict[str, int],
    target_product: str,
    rule_classification: Dict[str, Any],
    observed_branch_key: str = "",
) -> Dict[str, Any]:
    """Propose branch change with transport-aware reasoning."""
    overview = load_overview_config()
    edges = overview.get("logistics_edges", [])
    current = tool_read_branch_values()
    needed = set(required_inputs.keys())
    shortage = set(_extract_shortage_products(rule_classification)) or set(needed)
    produce = produce_map(overview)
    observed_branch_key = normalize_branch_key(observed_branch_key)
    observed_branch_value = current.get(observed_branch_key)

    policy_rules = (
        overview.get("diagnostics_policy", {}).get("transport_blocking_rules", [])
        if isinstance(overview.get("diagnostics_policy"), dict)
        else []
    )

    shortage_source_factories = set()
    for factory, products in produce.items():
        if set(products) & shortage:
            shortage_source_factories.add(factory)

    candidates: List[Dict[str, Any]] = []
    for edge in edges:
        products = set(edge.get("products", []))
        branch_key = str(edge.get("branch", ""))
        branch_value = int(edge.get("branch_value", 0))
        source = str(edge.get("from", ""))
        if not branch_key.startswith("branch"):
            continue

        score = 0
        reason_bits = []
        current_value = current.get(branch_key)
        is_change_needed = current_value != branch_value

        if products & shortage:
            score += 100
            reason_bits.append(f"direct shortage route for {sorted(products & shortage)}")
            if is_change_needed:
                score += 30
                reason_bits.append(f"current {branch_key}={current_value} differs from expected {branch_value}")
            else:
                score -= 20
                reason_bits.append(f"{branch_key} already set for shortage route")

        if source in shortage_source_factories and is_change_needed:
            score += 45
            reason_bits.append(
                f"source factory '{source}' is responsible for shortage {sorted(shortage)} and {branch_key} mismatch may trap carriers on non-shortage route"
            )

        if target_product in products:
            score += 8
            reason_bits.append(f"edge routes target product {target_product}")
        if str(rule_classification.get("classification", "")).startswith("input-"):
            score += 4
            reason_bits.append("input-side issue detected")

        if observed_branch_key and branch_key == observed_branch_key and is_change_needed:
            score += 120
            reason_bits.append(
                f"observed incident branch is {observed_branch_key} (current={observed_branch_value}), prioritize unlocking active carrier path"
            )

        for rule in policy_rules:
            if not isinstance(rule, dict):
                continue
            when_short = set(rule.get("when_shortage_products", []) or [])
            if when_short and not (when_short & shortage):
                continue
            for preferred in rule.get("prefer_branch_changes", []) or []:
                if not isinstance(preferred, dict):
                    continue
                preferred_key = normalize_branch_key(preferred.get("branch", ""))
                preferred_set_to = preferred.get("set_to")
                if preferred_key == branch_key and preferred_set_to is not None and int(preferred_set_to) == branch_value:
                    score += 200
                    reason_bits.append(f"policy priority '{rule.get('id', 'unnamed')}'")

        if score > 0:
            candidates.append(
                {
                    "branch_key": branch_key,
                    "recommended_value": branch_value,
                    "score": score,
                    "reason": "; ".join(reason_bits),
                    "edge": edge,
                }
            )

    if not candidates:
        return {
            "can_fix": False,
            "message": "No valid branch candidate found from overview edges.",
            "current_branch_values": current,
        }

    best = sorted(candidates, key=lambda item: item["score"], reverse=True)[0]
    best_branch_key = best["branch_key"]
    cur = current.get(best_branch_key, 0)
    rec = int(best["recommended_value"])

    return {
        "can_fix": cur != rec,
        "branch_key": best_branch_key,
        "current_value": cur,
        "recommended_value": rec,
        "reason": best["reason"],
        "edge_id": best["edge"].get("id"),
        "target_product": target_product,
        "required_inputs": sorted(list(needed)),
        "shortage_products": sorted(list(shortage)),
        "observed_branch_key": observed_branch_key,
        "current_branch_values": current,
        "dry_run": True,
    }


def tool_llm_incident_summary(event: Dict[str, Any], facts: Dict[str, Any]) -> str:
    """Ask LLM to summarize incident RCA as JSON text."""
    system_prompt = (
        "You are an expert in logistics incident root-cause analysis for a JaamSim-based simulation.\n"
        "Use only provided facts and keep output machine-readable JSON.\n"
    )
    human_prompt = (
        "Abnormal event:\n"
        f"{json.dumps(event, ensure_ascii=False)}\n\n"
        "Facts (JSON):\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
        "Output JSON with keys:\n"
        "- root_cause\n- causal_chain (array)\n- evidence (array)\n- confidence (0..1)\n- next_checks (array)\n"
    )
    return _invoke_llm(system_prompt=system_prompt, human_prompt=human_prompt)


def tool_llm_query_summary(query_text: str, query_result: Dict[str, Any]) -> str:
    """Ask LLM to summarize query result as JSON text."""
    system_prompt = "You summarize industrial data query results as concise JSON."
    human_prompt = (
        f"User query:\n{query_text}\n\n"
        f"Result data:\n{json.dumps(query_result, ensure_ascii=False, indent=2)}\n\n"
        "Output JSON with keys: answer, highlights(array), caveats(array)."
    )
    return _invoke_llm(system_prompt=system_prompt, human_prompt=human_prompt)
