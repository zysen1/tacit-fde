"""Deduplicate miner output, corroborate across sources, and flag conflicts."""

from __future__ import annotations

import hashlib
from typing import Any

from tacit.engine import describe, family_key, infer_severity, numeric_params, thresholds_close


def _merge(block: list[dict[str, Any]]) -> dict[str, Any]:
    sources: list[dict] = []
    kinds: list[str] = []
    explicit = False
    support = 0
    confidences = []
    for rule in block:
        sources.extend(rule.get("sources") or [])
        for kind in rule.get("source_kinds") or []:
            if kind not in kinds:
                kinds.append(kind)
        explicit = explicit or bool(rule.get("explicit"))
        support = max(support, int(rule.get("support") or 0))
        confidences.append(float(rule.get("confidence") or 0))
    surviving = 1.0
    for confidence in confidences:
        surviving *= 1 - min(max(confidence, 0.0), 0.99)
    combined = round(min(0.99, 1 - surviving), 4)
    snippets = [source.get("snippet") for source in sources if source.get("kind") == "text" and source.get("snippet")]
    official = [source["snippet"] for source in sources if source.get("official") and source.get("snippet")]
    base = block[0]
    rule = {
        "modality": base["modality"],
        "when": base["when"],
        "then": base["then"],
        "sources": sources,
        "source_kinds": kinds,
        "explicit": explicit,
        "confidence": combined,
        "support": support or None,
        "status": "candidate",
        "conflict_group": None,
        "thresholds": numeric_params(base),
    }
    rule["severity"] = infer_severity(rule)
    rule["statement"] = official[0] if official else (snippets[0] if snippets else describe(rule))
    return rule


def consolidate(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        clusters.setdefault(family_key(rule), []).append(rule)
    merged: list[dict[str, Any]] = []
    for key, group in clusters.items():
        blocks: list[list[dict[str, Any]]] = []
        for rule in group:
            placed = False
            for block in blocks:
                if thresholds_close(block[0], rule):
                    block.append(rule)
                    placed = True
                    break
            if not placed:
                blocks.append([rule])
        conflict_id = None
        if len(blocks) > 1:
            conflict_id = "C" + hashlib.sha1(key.encode()).hexdigest()[:8]
        for block in blocks:
            rule = _merge(block)
            rule["conflict_group"] = conflict_id
            merged.append(rule)
    merged.sort(key=lambda rule: (-len(rule["source_kinds"]), -rule["confidence"], rule["statement"]))
    for index, rule in enumerate(merged, start=1):
        rule["id"] = f"R{index:03d}"
    return merged
