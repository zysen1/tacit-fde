"""Shared rule IR: atoms, violation checks, probes, and decision labels.

Miners, the scripted stakeholder, the mock agent, and the generated MCP
validator all call this module. The ablation then measures which rules were
discovered, not how a prompt happened to be worded.
"""

from __future__ import annotations

import copy
import math
from typing import Any

APPROVAL_FIELDS = {
    "region_manager",
    "approver",
    "supervisor",
    "regional_manager",
    "director",
    "siu_cleared",
}
INFO_FIELDS = {
    "contact_phone",
    "customer_called",
    "claimant_called",
    "vp_exception",
    "medical_report_id",
    "denial_reason",
    "hs_code",
    "cancel_reason",
}
SEVERITY_RANK = {"approve": 0, "needs_approval": 1, "needs_contact": 2, "reject": 3}
_CMP = {">", ">=", "<", "<="}


def atom(field: str, op: str, value: Any = None, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"field": field, "op": op, "value": value}
    for key, val in extra.items():
        if val is not None:
            item[key] = val
    return item


def _num(value: Any) -> float:
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def atom_holds(item: dict[str, Any], case: dict[str, Any]) -> bool:
    field = item["field"]
    op = item["op"]
    if op == "minus_ge":
        left = _num(case.get(field)) - _num(case.get(item.get("value_field")))
        return left >= _num(case.get(item.get("rhs_field")))
    if op == "ge_field":
        return _num(case.get(field)) >= _num(case.get(item.get("value_field")))
    present = field in case and case.get(field) is not None and case.get(field) != ""
    if op == "not_null":
        return present
    if op == "is_null":
        return not present
    if not present:
        return False
    value = case.get(field)
    target = item.get("value")
    if op == "==":
        return value == target or str(value) == str(target)
    if op == "!=":
        return not (value == target or str(value) == str(target))
    if op == "prefix":
        return str(value).startswith(str(target))
    if op == "in":
        options = target or []
        return value in options or str(value) in {str(v) for v in options}
    if op in _CMP:
        left = _num(value)
        right = _num(target)
        if math.isnan(left) or math.isnan(right):
            return False
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        return left <= right
    return False


def antecedent(rule: dict[str, Any], case: dict[str, Any]) -> bool:
    return all(atom_holds(item, case) for item in rule.get("when") or [])


def violates(rule: dict[str, Any], case: dict[str, Any]) -> bool:
    if not antecedent(rule, case):
        return False
    then = rule.get("then") or []
    if rule.get("modality") == "must_not":
        return all(atom_holds(item, case) for item in then) if then else False
    return not all(atom_holds(item, case) for item in then)


def infer_severity(rule: dict[str, Any]) -> str:
    if rule.get("modality") == "must_not":
        return "reject"
    then = rule.get("then") or []
    if any(item["op"] in _CMP or item["op"] in {"minus_ge", "ge_field"} for item in then):
        return "reject"
    fields = [item["field"] for item in then]
    if any(item["op"] in {"==", "!=", "in", "prefix"} for item in then):
        if any(field in INFO_FIELDS for field in fields):
            return "needs_contact"
        if any(field in APPROVAL_FIELDS for field in fields):
            return "needs_approval"
        return "reject"
    if any(field in APPROVAL_FIELDS for field in fields):
        return "needs_approval"
    return "needs_contact"


def decision(case: dict[str, Any], rules: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    fired = [rule for rule in rules if violates(rule, case)]
    if not fired:
        return "approve", []
    fired.sort(key=lambda rule: SEVERITY_RANK[rule["severity"]], reverse=True)
    return fired[0]["severity"], fired


def describe_atom(item: dict[str, Any]) -> str:
    op = item["op"]
    field = item["field"]
    if op == "not_null":
        return f"{field} is present"
    if op == "is_null":
        return f"{field} is empty"
    if op == "prefix":
        return f"{field} starts with {item.get('value')}"
    if op == "in":
        return f"{field} in {item.get('value')}"
    if op == "minus_ge":
        return f"{field} - {item.get('value_field')} >= {item.get('rhs_field')}"
    if op == "ge_field":
        return f"{field} >= {item.get('value_field')}"
    return f"{field} {op} {item.get('value')}"


def describe(rule: dict[str, Any]) -> str:
    when = " AND ".join(describe_atom(item) for item in rule.get("when") or []) or "always"
    then = " AND ".join(describe_atom(item) for item in rule.get("then") or [])
    if rule.get("modality") == "must_not":
        return f"When {when}, forbid {then}"
    return f"When {when}, require {then}"


def rule_fields(rule: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in list(rule.get("when") or []) + list(rule.get("then") or []):
        found.add(item["field"])
        if item.get("value_field"):
            found.add(item["value_field"])
        if item.get("rhs_field"):
            found.add(item["rhs_field"])
    return found


def realize(item: dict[str, Any], satisfy: bool) -> dict[str, Any]:
    """Build field assignments that satisfy or break one atom, a little off the boundary."""
    op = item["op"]
    field = item["field"]
    value = item.get("value")
    if op == "==":
        if satisfy:
            return {field: value}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return {field: 0 if value != 0 else 1}
        return {field: f"not:{value}"}
    if op == "!=":
        if satisfy:
            sentinel = 0 if value == 1 else f"not:{value}"
            return {field: sentinel}
        return {field: value}
    if op == ">":
        number = float(value)
        delta = max(1.0, abs(number) * 0.02)
        return {field: number + delta if satisfy else number - delta}
    if op == ">=":
        number = float(value)
        delta = max(1.0, abs(number) * 0.02)
        return {field: number if satisfy else number - delta}
    if op == "<":
        number = float(value)
        delta = max(1.0, abs(number) * 0.02)
        return {field: number - delta if satisfy else number + delta}
    if op == "<=":
        number = float(value)
        delta = max(0.5, abs(number) * 0.05)
        return {field: number if satisfy else number + delta}
    if op == "not_null":
        return {field: "SET" if satisfy else None}
    if op == "is_null":
        return {field: None if satisfy else "SET"}
    if op == "prefix":
        text = str(value)
        return {field: f"{text}001" if satisfy else f"OTHER-{text}"}
    if op == "in":
        options = list(value or [])
        return {field: options[0] if satisfy and options else "NOT-IN-SET"}
    if op == "minus_ge":
        if satisfy:
            return {field: 50, item["value_field"]: 10, item["rhs_field"]: 5}
        return {field: 5, item["value_field"]: 4, item["rhs_field"]: 20}
    if op == "ge_field":
        if satisfy:
            return {field: 100, item["value_field"]: 40}
        return {field: 10, item["value_field"]: 80}
    return {}


def _paint(rule: dict[str, Any], violate: bool) -> dict[str, Any]:
    case: dict[str, Any] = {}
    for item in rule.get("when") or []:
        case.update(realize(item, True))
    satisfy_then = (rule.get("modality") == "must_not" and violate) or (
        rule.get("modality") != "must_not" and not violate
    )
    for item in rule.get("then") or []:
        case.update(realize(item, satisfy_then))
    return case


def probes_for(rule: dict[str, Any]) -> list[tuple[dict[str, Any], bool]]:
    """Comfortable and near-boundary cases. `flag` is whether `rule` itself is violated."""
    probes = [(_paint(rule, True), True), (_paint(rule, False), False)]
    if rule.get("when"):
        outside = _paint(rule, True)
        outside.update(realize(rule["when"][0], False))
        probes.append((outside, False))
    return probes


def agrees(mined: dict[str, Any], truth: dict[str, Any]) -> bool:
    if not (rule_fields(mined) & rule_fields(truth)):
        return False
    for case, flag in probes_for(truth):
        if violates(mined, case) != flag:
            return False
    return True


def match_rules(
    mined: list[dict[str, Any]], truths: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    """Greedy one-to-one match. Returns pairs of (mined index, truth index)."""
    pairs: list[tuple[int, int, int]] = []
    for i, rule in enumerate(mined):
        for j, truth in enumerate(truths):
            if agrees(rule, truth):
                shared = len(rule_fields(rule) & rule_fields(truth))
                pairs.append((shared, i, j))
    pairs.sort(reverse=True)
    used_m: set[int] = set()
    used_t: set[int] = set()
    chosen: list[tuple[int, int]] = []
    for _shared, i, j in pairs:
        if i in used_m or j in used_t:
            continue
        used_m.add(i)
        used_t.add(j)
        chosen.append((i, j))
    return chosen


def family_key(rule: dict[str, Any]) -> str:
    def norm(item: dict[str, Any], keep_value: bool) -> tuple[Any, ...]:
        value = item.get("value") if keep_value else None
        if item["op"] in _CMP:
            value = None
        return (item["field"], item["op"], value, item.get("value_field"), item.get("rhs_field"))

    when = tuple(sorted(norm(item, item["op"] not in _CMP) for item in rule.get("when") or []))
    then = tuple(sorted(norm(item, True) for item in rule.get("then") or []))
    return repr((rule.get("modality"), when, then))


def numeric_params(rule: dict[str, Any]) -> list[float]:
    found = []
    for item in list(rule.get("when") or []) + list(rule.get("then") or []):
        if item["op"] in _CMP and isinstance(item.get("value"), (int, float)):
            found.append(float(item["value"]))
    return found


def thresholds_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a = numeric_params(left)
    b = numeric_params(right)
    if len(a) != len(b):
        return False
    for x, y in zip(sorted(a), sorted(b), strict=True):
        if abs(x - y) / max(abs(x), abs(y), 1.0) > 0.02:
            return False
    return True


def binary_entropy(probability: float) -> float:
    p = min(max(probability, 1e-6), 1 - 1e-6)
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _force(item: dict[str, Any], case: dict[str, Any], satisfy: bool) -> dict[str, Any]:
    """Change as few fields as possible so `item` holds or fails in `case`.

    Covering constraints such as ``reserves >= amount`` stay relative to the
    amount already chosen for the focused rule. Absolute probe values would
    pull that amount back down and contaminate the held-out case.
    """
    op = item["op"]
    if op == "ge_field":
        right = _num(case.get(item.get("value_field")))
        if math.isnan(right):
            return realize(item, satisfy)
        return {item["field"]: right if satisfy else right - 1}
    if op == "minus_ge":
        sub = _num(case.get(item.get("value_field")))
        rhs = _num(case.get(item.get("rhs_field")))
        if math.isnan(sub) or math.isnan(rhs):
            return realize(item, satisfy)
        covered = sub + rhs
        return {item["field"]: covered if satisfy else covered - 1}
    return realize(item, satisfy)


def _repair(rule: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Assignments that stop `rule` violating `case`."""
    updates: dict[str, Any] = {}
    satisfy_then = rule.get("modality") == "must_not"
    for item in rule.get("then") or []:
        updates.update(_force(item, {**case, **updates}, not satisfy_then))
    return updates


def apply_focus(base: dict[str, Any], rule: dict[str, Any], violate: bool) -> dict[str, Any]:
    case = copy.deepcopy(base)
    case.update(_paint(rule, violate))
    return case


def isolate(
    base: dict[str, Any],
    focus: dict[str, Any],
    rules: list[dict[str, Any]],
    violate: bool,
) -> dict[str, Any]:
    """Mutate `base` for `focus` without leaving other authoritative rules broken."""
    case = apply_focus(base, focus, violate)
    others = [rule for rule in rules if rule["id"] != focus["id"]]
    for _ in range(5):
        changed = False
        for other in others:
            if not violates(other, case):
                continue
            repair = _repair(other, case)
            for key, value in repair.items():
                updated = {**case, key: value}
                # Keep the focused rule in the state the caller asked for.
                if violate and not violates(focus, updated):
                    continue
                if not violate and violates(focus, updated):
                    continue
                if case.get(key) != value:
                    case[key] = value
                    changed = True
        if not changed:
            break
    return case
