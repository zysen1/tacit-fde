"""Score discovery and the three-harness ablation on held-out cases.

Held-out cases are generated from the answer key. They are not rows in the
estate, so the data miner cannot memorize the test items. The mock agent may
use only the rules in its harness; that is what isolates discovery from model
choice.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from tacit.engine import SEVERITY_RANK, decision, isolate, match_rules


def _tasks(ground_truth: dict[str, Any]) -> list[dict[str, Any]]:
    authoritative = [rule for rule in ground_truth["rules"] if rule.get("authoritative")]
    base = ground_truth["base_case"]
    label, fired = decision(base, authoritative)
    if label != "approve":
        raise RuntimeError(f"base case violates {fired[0]['id']}: {fired[0]['statement']}")
    tasks = []
    for rule in authoritative:
        for violate, name in ((True, "bad"), (False, "good")):
            case = isolate(base, rule, authoritative, violate)
            tasks.append({"id": f"{rule['id']}-{name}", "focus": rule["id"], "case": case})
    for index in range(8):
        case = copy.deepcopy(base)
        case["order_id"] = f"CLEAN-{index}"
        case["claim_id"] = f"CLEAN-{index}"
        tasks.append({"id": f"clean-{index}", "focus": None, "case": case})
    for task in tasks:
        gold, _fired = decision(task["case"], authoritative)
        task["gold"] = gold
    return tasks


def _rates(cases: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, float]:
    success = 0
    violations = 0
    for case in cases:
        label, _fired = decision(case["case"], rules)
        if label == case["gold"]:
            success += 1
        if SEVERITY_RANK[label] < SEVERITY_RANK[case["gold"]]:
            violations += 1
    total = len(cases) or 1
    return {
        "success_rate": round(success / total, 4),
        "violation_rate": round(violations / total, 4),
        "n": len(cases),
    }


def _bucket(rule: dict[str, Any]) -> str:
    if rule.get("conflict_group"):
        return "conflict"
    kinds = list(rule.get("source_kinds") or [])
    if len(kinds) >= 2:
        return "multi"
    return kinds[0] if kinds else "unknown"


def evaluate(rules: list[dict[str, Any]], ground_truth: dict[str, Any] | None, ranking: dict[str, Any]) -> dict[str, Any]:
    pairs = []
    truths = []
    if ground_truth:
        truths = ground_truth["rules"]
        pairs = match_rules(rules, truths)
    matched_truth = {j for _i, j in pairs}
    matched_mined = {i for i, _j in pairs}
    truth_by_index = {j: truths[j] for _i, j in pairs}
    mined_for_truth = {j: rules[i] for i, j in pairs}

    tacit = [rule for rule in truths if rule.get("tacit") and rule.get("authoritative")]
    tacit_hit = []
    missed = []
    by_bucket: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "n": 0})
    for index, rule in enumerate(truths):
        if not (rule.get("tacit") and rule.get("authoritative")):
            continue
        by_bucket[rule["bucket"]]["n"] += 1
        mined = mined_for_truth.get(index)
        ok = mined is not None
        if rule["bucket"] == "conflict":
            ok = bool(mined and mined.get("conflict_group"))
        if ok:
            by_bucket[rule["bucket"]]["hit"] += 1
            tacit_hit.append(rule["id"])
        else:
            missed.append(rule["id"])

    recall = round(len(tacit_hit) / len(tacit), 4) if tacit else None
    recall_by_source = {
        bucket: round(counts["hit"] / counts["n"], 4) if counts["n"] else None
        for bucket, counts in sorted(by_bucket.items())
    }

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "n": 0})
    false_positives = []
    for index, rule in enumerate(rules):
        bucket = _bucket(rule)
        buckets[bucket]["n"] += 1
        if index in matched_mined:
            buckets[bucket]["tp"] += 1
        else:
            false_positives.append({"id": rule["id"], "statement": rule["statement"], "bucket": bucket})
    precision = round(len(matched_mined) / len(rules), 4) if rules else None
    precision_by_source = {
        bucket: round(counts["tp"] / counts["n"], 4) if counts["n"] else None
        for bucket, counts in sorted(buckets.items())
    }

    ablation = None
    if ground_truth:
        tasks = _tasks(ground_truth)
        confirmed = [rule for rule in rules if rule.get("status") == "confirmed"]
        explicit = [rule for rule in confirmed if rule.get("explicit")]
        ablation = {
            "none": _rates(tasks, []),
            "explicit": _rates(tasks, explicit),
            "tacit": _rates(tasks, confirmed),
            "n_confirmed": len(confirmed),
            "n_explicit_confirmed": len(explicit),
        }
        # Keep the held-out set next to the report, outside the estate.
        ground_truth = {**ground_truth, "_tasks": tasks}

    return {
        "template": (ground_truth or {}).get("template"),
        "seed": (ground_truth or {}).get("seed"),
        "planted_tacit": len(tacit),
        "tacit_recall": recall,
        "precision": precision,
        "recall_by_source": recall_by_source,
        "precision_by_source": precision_by_source,
        "tacit_hit": tacit_hit,
        "tacit_missed": missed,
        "false_positives": false_positives,
        "n_candidates": len(rules),
        "matched_truth_ids": [truth_by_index[j]["id"] for j in sorted(matched_truth) if j in truth_by_index],
        "clarification": ranking,
        "ablation": ablation,
        "heldout_tasks": (ground_truth or {}).get("_tasks"),
    }
