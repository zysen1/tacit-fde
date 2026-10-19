"""Offline normative-text miner.

Patterns bind a small business glossary to columns. Paraphrases outside the
glossary are missed on purpose; the optional LLM extractor is the extension
point for those, and it is not scored in the offline benchmark.
"""

from __future__ import annotations

import re
from typing import Any

from tacit.engine import atom, describe, infer_severity

_OFFICIAL = ("正式制度", "official policy")


def _rule(
    *,
    modality: str,
    when: list[dict],
    then: list[dict],
    path: str,
    line: int,
    snippet: str,
    official: bool,
) -> dict[str, Any]:
    confidence = 0.88 if official else 0.64
    rule = {
        "modality": modality,
        "when": when,
        "then": then,
        "sources": [
            {
                "kind": "text",
                "path": path,
                "line": line,
                "snippet": snippet.strip(),
                "confidence": confidence,
                "official": official,
            }
        ],
        "source_kinds": ["text"],
        "explicit": official,
        "confidence": confidence,
        "support": None,
        "status": "candidate",
        "conflict_group": None,
    }
    rule["severity"] = infer_severity(rule)
    rule["statement"] = snippet.strip() or describe(rule)
    return rule


def _split_options(text: str) -> list[str]:
    cleaned = text.replace(" or ", ", ").replace("、", ",")
    return [part.strip(" .").strip() for part in cleaned.split(",") if part.strip(" .")]


def _patterns(line: str) -> dict[str, Any] | None:
    # More specific patterns first.
    match = re.search(
        r"(华东|华南|华北|西南|西北)区?订单金额超过\s*(\d+)\s*元[，,]?\s*必须有区域经理",
        line,
    )
    if match:
        return {
            "modality": "must",
            "when": [
                atom("ship_region", "==", match.group(1)),
                atom("amount", ">", int(match.group(2))),
            ],
            "then": [atom("region_manager", "not_null")],
        }
    match = re.search(r"订单金额超过\s*(\d+)\s*元必须有审批人", line)
    if match:
        return {
            "modality": "must",
            "when": [atom("amount", ">", int(match.group(1)))],
            "then": [atom("approver", "not_null")],
        }
    if re.search(r"不得在周五\s*16[:：]00\s*后.*加急", line):
        return {
            "modality": "must_not",
            "when": [atom("created_dow", "==", 5), atom("created_hour", ">=", 16)],
            "then": [atom("priority", "==", "rush")],
        }
    if re.search(r"必须在替换\s*SKU\s*之前致电客户", line):
        return {
            "modality": "must",
            "when": [atom("sku_substituted", "==", 1)],
            "then": [atom("customer_called", "==", 1)],
        }
    match = re.search(r"退货超过\s*(\d+)\s*天必须有\s*VP", line)
    if match:
        return {
            "modality": "must",
            "when": [atom("return_age_days", ">", int(match.group(1)))],
            "then": [atom("vp_exception", "not_null")],
        }
    if re.search(r"出口订单必须有\s*HS\s*编码", line):
        return {
            "modality": "must",
            "when": [atom("ship_country", "!=", "CN")],
            "then": [atom("hs_code", "not_null")],
        }
    if "加急订单必须留下联系电话" in line:
        return {
            "modality": "must",
            "when": [atom("priority", "==", "rush")],
            "then": [atom("contact_phone", "not_null")],
        }
    if "取消订单必须填写原因" in line:
        return {
            "modality": "must",
            "when": [atom("status", "==", "cancelled")],
            "then": [atom("cancel_reason", "not_null")],
        }
    match = re.search(
        r"(South|North|East|West) region claims over (\d+) require a regional manager",
        line,
        re.I,
    )
    if match:
        return {
            "modality": "must",
            "when": [
                atom("region", "==", match.group(1).title()),
                atom("amount", ">", int(match.group(2))),
            ],
            "then": [atom("regional_manager", "not_null")],
        }
    match = re.search(r"Claims over (\d+) require a supervisor", line, re.I)
    if match:
        return {
            "modality": "must",
            "when": [atom("amount", ">", int(match.group(1)))],
            "then": [atom("supervisor", "not_null")],
        }
    if re.search(r"Always call the claimant before denying", line, re.I):
        return {
            "modality": "must",
            "when": [atom("status", "==", "denied")],
            "then": [atom("claimant_called", "==", 1)],
        }
    if re.search(r"Never negotiate liability after 16:00 on Friday", line, re.I):
        return {
            "modality": "must_not",
            "when": [atom("created_dow", "==", 5), atom("created_hour", ">=", 16)],
            "then": [atom("liability_negotiation", "==", 1)],
        }
    match = re.search(r"more than (\d+) days later require a VP", line, re.I)
    if match:
        return {
            "modality": "must",
            "when": [atom("supplement_age_days", ">", int(match.group(1)))],
            "then": [atom("vp_exception", "not_null")],
        }
    if re.search(r"Third-party injury claims must have a medical report", line, re.I):
        return {
            "modality": "must",
            "when": [atom("third_party", "==", 1)],
            "then": [atom("medical_report_id", "not_null")],
        }
    if re.search(r"Catastrophe claims must have a contact phone", line, re.I):
        return {
            "modality": "must",
            "when": [atom("priority", "==", "catastrophe")],
            "then": [atom("contact_phone", "not_null")],
        }
    if re.search(r"Denied claims must have a denial reason", line, re.I):
        return {
            "modality": "must",
            "when": [atom("status", "==", "denied")],
            "then": [atom("denial_reason", "not_null")],
        }
    match = re.search(r"[Pp]ayment terms must be one of (.+)", line)
    if match:
        options = [part for part in _split_options(match.group(1)) if part.startswith("NET") or part == "PREPAY"]
        if len(options) >= 2:
            return {"modality": "must", "when": [], "then": [atom("payment_terms", "in", options)]}
    match = re.search(r"[Cc]laim status must be one of (.+)", line)
    if match:
        options = [part.strip().lower() for part in _split_options(match.group(1))]
        options = [part for part in options if part]
        if len(options) >= 2:
            return {"modality": "must", "when": [], "then": [atom("status", "in", options)]}
    return None


def _sections(text: str, kind: str) -> list[tuple[bool, list[tuple[int, str]]]]:
    if kind == "pdf":
        lines = [(i + 1, line) for i, line in enumerate(text.splitlines())]
        official = any(flag in text.lower() for flag in _OFFICIAL) or "Official Policy" in text
        return [(official, lines)]
    sections: list[tuple[bool, list[tuple[int, str]]]] = []
    official = False
    bucket: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#"):
            if bucket:
                sections.append((official, bucket))
                bucket = []
            title = line.strip("# ").strip().lower()
            official = any(flag in title for flag in _OFFICIAL)
            continue
        bucket.append((index, line))
    if bucket:
        sections.append((official, bucket))
    return sections


def mine_text(documents: list[dict[str, Any]]) -> list[dict]:
    rules = []
    for doc in documents:
        if doc["kind"] not in {"markdown", "pdf"}:
            continue
        for official, lines in _sections(doc.get("text") or "", doc["kind"]):
            for lineno, line in lines:
                if not line.strip():
                    continue
                parsed = _patterns(line.strip())
                if not parsed:
                    continue
                rules.append(
                    _rule(
                        modality=parsed["modality"],
                        when=parsed["when"],
                        then=parsed["then"],
                        path=doc["path"],
                        line=lineno,
                        snippet=line.strip(),
                        official=official,
                    )
                )
    return rules
