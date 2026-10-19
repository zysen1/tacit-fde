"""Rank clarifying questions and answer them with a scripted stakeholder.

Value of information is binary entropy of the candidate's confidence times the
fraction of rows the antecedent covers. The scripted stakeholder answers from
the planted key, the same way FDE-Gym's stakeholders answer from a script.
Mining itself never reads that key.
"""

from __future__ import annotations

import random
import statistics
from typing import Any

import pandas as pd

from tacit.engine import agrees, binary_entropy


def _mask(frame: pd.DataFrame, item: dict[str, Any]) -> pd.Series:
    column = frame[item["field"]]
    op = item["op"]
    value = item.get("value")
    if op == "==":
        return column == value
    if op == "!=":
        return column != value
    if op == "prefix":
        return column.astype(str).str.startswith(str(value))
    if op == ">":
        return column > value
    if op == ">=":
        return column >= value
    if op == "<":
        return column < value
    if op == "<=":
        return column <= value
    if op == "not_null":
        return column.notna()
    if op == "in":
        return column.isin(value or [])
    return pd.Series(False, index=frame.index)


def impact_fraction(rule: dict[str, Any], frames: dict[str, pd.DataFrame]) -> float:
    if not rule.get("when"):
        for frame in frames.values():
            if any(item["field"] in frame.columns for item in rule.get("then") or []):
                return 1.0
        return 0.12
    best = None
    for frame in frames.values():
        if any(item["field"] not in frame.columns for item in rule["when"]):
            continue
        mask = pd.Series(True, index=frame.index)
        for item in rule["when"]:
            mask = mask & _mask(frame, item)
        best = float(mask.mean()) if best is None else max(best, float(mask.mean()))
    if best is None:
        return 0.12
    return max(best, 0.02)


def _auto_confirm(rule: dict[str, Any]) -> bool:
    if rule.get("conflict_group"):
        return False
    kinds = set(rule.get("source_kinds") or [])
    if rule.get("explicit") and "text" in kinds:
        return True
    if "data" in kinds and float(rule.get("confidence") or 0) >= 0.97 and int(rule.get("support") or 0) >= 20:
        return True
    return False


def _question_for(rules: list[dict[str, Any]], frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    conflict = any(rule.get("conflict_group") for rule in rules)
    if conflict:
        uncertainty = 1.0
        confidence = 0.5
    else:
        confidence = float(rules[0].get("confidence") or 0.5)
        uncertainty = binary_entropy(confidence)
    impact = max(impact_fraction(rule, frames) for rule in rules)
    parts = []
    for rule in rules:
        kinds = ", ".join(rule.get("source_kinds") or [])
        snippet = next((source.get("snippet") for source in rule["sources"] if source.get("snippet")), rule["statement"])
        parts.append(f"[{kinds}] {snippet}")
    if conflict:
        prompt = "These sources disagree. Which rule is in force?\n" + "\n".join(parts)
    else:
        prompt = "Is this a real policy?\n" + parts[0]
    return {
        "rule_ids": [rule["id"] for rule in rules],
        "prompt": prompt,
        "voi": round(uncertainty * impact, 4),
        "impact": round(impact, 4),
        "uncertainty": round(uncertainty, 4),
        "confidence": confidence,
        "conflict": conflict,
    }


def _attach_truth(rule: dict[str, Any], truths: list[dict[str, Any]]) -> str | None:
    for truth in truths:
        if truth.get("authoritative") and agrees(rule, truth):
            return truth["id"]
    return None


def _answer(rules: list[dict[str, Any]], truths: list[dict[str, Any]]) -> dict[str, Any]:
    confirms: list[str] = []
    rejects: list[str] = []
    note = ""
    for rule in rules:
        hits = [truth for truth in truths if agrees(rule, truth)]
        authoritative = [truth for truth in hits if truth.get("authoritative")]
        if authoritative:
            confirms.append(authoritative[0]["id"])
            rule["status"] = "confirmed"
            rule["gt_id"] = authoritative[0]["id"]
            note = authoritative[0].get("note") or note
        else:
            rule["status"] = "rejected"
            if hits:
                rejects.append(hits[0]["id"])
                note = hits[0].get("note") or note
            else:
                rejects.append(rule["id"])
    if confirms and note:
        answer = note
    elif confirms:
        answer = "Yes. That rule is in force."
    else:
        answer = note or "No. That is not a real policy."
    return {"confirms_gt": confirms, "rejects_gt": rejects, "answer": answer}


def _score(questions: list[dict], auto_ids: set[str], tacit_ids: list[str]) -> dict[str, float]:
    needed = [rule_id for rule_id in tacit_ids if rule_id not in auto_ids]
    found: dict[str, int] = {}
    weights: dict[str, float] = {}
    for index, question in enumerate(questions, start=1):
        for rule_id in question.get("confirms_gt") or []:
            found.setdefault(rule_id, index)
            weights.setdefault(rule_id, float(question.get("impact") or 0.1))
    weighted_num = 0.0
    weighted_den = 0.0
    indexes = []
    for rule_id in needed:
        if rule_id not in found:
            continue
        weight = weights.get(rule_id, 0.1)
        weighted_num += found[rule_id] * weight
        weighted_den += weight
        indexes.append(found[rule_id])
    return {
        "impact_weighted_index": round(weighted_num / weighted_den, 4) if weighted_den else 0.0,
        "mean_index": round(statistics.mean(indexes), 4) if indexes else 0.0,
        "confirmed_by_question": len(indexes),
        "needed": len(needed),
    }


def clarify(
    rules: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    truths: list[dict[str, Any]] | None = None,
    *,
    seed: int = 0,
    simulate: bool = False,
) -> dict[str, Any]:
    truths = truths or []
    for rule in rules:
        if _auto_confirm(rule):
            rule["status"] = "confirmed"
            if truths:
                matched = _attach_truth(rule, truths)
                if matched:
                    rule["gt_id"] = matched
    groups: dict[str, list[dict[str, Any]]] = {}
    singles: list[dict[str, Any]] = []
    for rule in rules:
        if rule.get("status") == "confirmed":
            continue
        if rule.get("conflict_group"):
            groups.setdefault(rule["conflict_group"], []).append(rule)
        else:
            singles.append(rule)
    questions = [_question_for(group, frames) for group in groups.values()]
    questions.extend(_question_for([rule], frames) for rule in singles)
    questions.sort(key=lambda question: question["voi"], reverse=True)
    for index, question in enumerate(questions, start=1):
        question["id"] = f"Q{index:02d}"
    if simulate:
        if not truths:
            raise RuntimeError("clarify --sim needs ground_truth.json; mining does not use it")
        by_id = {rule["id"]: rule for rule in rules}
        for question in questions:
            targeted = [by_id[rule_id] for rule_id in question["rule_ids"]]
            question.update(_answer(targeted, truths))
    auto_before = {
        rule.get("gt_id")
        for rule in rules
        if rule.get("gt_id") and rule.get("status") == "confirmed" and rule["id"] not in {rid for q in questions for rid in q["rule_ids"]}
    }
    tacit_ids = [truth["id"] for truth in truths if truth.get("tacit") and truth.get("authoritative")]
    ranking = {}
    if simulate and tacit_ids:
        voi_score = _score(questions, auto_before, tacit_ids)
        rng = random.Random(seed)
        random_scores = []
        for _ in range(64):
            shuffled = list(questions)
            rng.shuffle(shuffled)
            random_scores.append(_score(shuffled, auto_before, tacit_ids)["impact_weighted_index"])
        confirmed = set(auto_before)
        for question in questions:
            confirmed.update(question.get("confirms_gt") or [])
        n_confirmed = len([rule_id for rule_id in tacit_ids if rule_id in confirmed])
        ranking = {
            "voi": voi_score,
            "random_impact_weighted_index_mean": round(statistics.mean(random_scores), 4) if random_scores else 0.0,
            "random_impact_weighted_index_stdev": round(statistics.pstdev(random_scores), 4) if len(random_scores) > 1 else 0.0,
            "questions": len(questions),
            "tacit_confirmed": n_confirmed,
            "questions_per_confirmed_rule": round(len(questions) / n_confirmed, 4) if n_confirmed else None,
        }
    return {
        "questions": questions,
        "auto_confirmed_gt": sorted(auto_before),
        "ranking": ranking,
    }

