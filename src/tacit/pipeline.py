"""Wire ingest → mine → clarify → compile → eval → report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tacit.clarify import clarify
from tacit.compile_harness import compile_harness
from tacit.consolidate import consolidate
from tacit.evaluate import evaluate
from tacit.inventory import extract_zip, scan_estate
from tacit.miners.code_rules import mine_code
from tacit.miners.data_rules import mine_tables
from tacit.miners.text_rules import mine_text
from tacit.report import write_report
from tacit.synth import write_bundle

SEEDS = [7, 19, 42, 73, 101]
TEMPLATES = ["distribution", "claims"]


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ingest(source: Path, work: Path) -> dict[str, Any]:
    estate = work / "estate"
    if estate.exists():
        for child in sorted(estate.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
    if source.is_dir():
        # Copying via zip extract is unnecessary; scan the directory in place.
        estate = source
    else:
        extract_zip(source, estate)
        sibling = source.parent / "ground_truth.json"
        if sibling.exists() and not (work / "ground_truth.json").exists():
            (work / "ground_truth.json").write_text(sibling.read_text(encoding="utf-8"), encoding="utf-8")
    documents, _frames = scan_estate(estate if source.is_dir() else work / "estate")
    # When source is a directory, still record it as the estate root.
    if source.is_dir():
        (work / "estate_path.txt").write_text(str(source), encoding="utf-8")
    else:
        pointer = work / "estate_path.txt"
        if pointer.exists():
            pointer.unlink()
    summary = {
        "documents": [
            {
                "path": doc["path"],
                "kind": doc["kind"],
                "tables": doc.get("tables") or [],
                "profile": doc.get("profile"),
                "chars": len(doc.get("text") or ""),
            }
            for doc in documents
        ]
    }
    _dump(work / "inventory.json", summary)
    return summary


def _estate(work: Path) -> Path:
    pointer = work / "estate_path.txt"
    if pointer.exists():
        return Path(pointer.read_text(encoding="utf-8").strip())
    return work / "estate"


def _truth(work: Path) -> dict[str, Any] | None:
    path = work / "ground_truth.json"
    if path.exists():
        return _load(path)
    return None


def mine(work: Path) -> list[dict[str, Any]]:
    documents, frames = scan_estate(_estate(work))
    raw = mine_code(documents) + mine_tables(frames) + mine_text(documents)
    _dump(work / "mined_raw.json", raw)
    rules = consolidate(raw)
    _dump(work / "rules.json", rules)
    return rules


def clarify_work(work: Path, simulate: bool) -> dict[str, Any]:
    rules = _load(work / "rules.json")
    _documents, frames = scan_estate(_estate(work))
    truth = _truth(work)
    truths = truth["rules"] if truth else []
    seed = int(truth["seed"]) if truth else 0
    result = clarify(rules, frames, truths, seed=seed, simulate=simulate)
    _dump(work / "rules.json", rules)
    _dump(work / "clarify.json", result)
    return result


def compile_work(work: Path) -> dict[str, str]:
    rules = _load(work / "rules.json")
    truth = _truth(work)
    company = truth["company"] if truth else "Customer estate"
    domain = truth["domain"] if truth else "customer"
    return compile_harness(work, company, domain, rules)


def eval_work(work: Path) -> dict[str, Any]:
    rules = _load(work / "rules.json")
    truth = _truth(work)
    ranking = {}
    clarify_path = work / "clarify.json"
    if clarify_path.exists():
        ranking = _load(clarify_path)
    report = evaluate(rules, truth, ranking)
    tasks = report.pop("heldout_tasks", None)
    if tasks:
        evals = work / "evals"
        evals.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({"id": task["id"], "focus": task["focus"], "gold": task["gold"], "case": task["case"]}, ensure_ascii=False) for task in tasks]
        (evals / "heldout.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _dump(work / "eval_report.json", report)
    return report


def report_work(work: Path) -> Path:
    truth = _truth(work)
    rules = _load(work / "rules.json")
    inventory = _load(work / "inventory.json")
    metrics = _load(work / "eval_report.json") if (work / "eval_report.json").exists() else {}
    questions = []
    if (work / "clarify.json").exists():
        questions = _load(work / "clarify.json").get("questions") or []
    conflicts = [question for question in questions if question.get("conflict")]
    company = truth["company"] if truth else "Customer estate"
    headline = "Confirmed rules, the questions that resolved them, and the ablation against held-out cases."
    payload = {
        "company": company,
        "headline": headline,
        "seed": truth.get("seed") if truth else None,
        "inventory": inventory["documents"],
        "rules": rules,
        "conflicts": conflicts,
        "metrics": metrics,
    }
    path = work / "report.html"
    write_report(path, payload)
    return path


def run_pipeline(template: str, seed: int, work: Path) -> dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    bundle = write_bundle(template, seed, work / "synth")
    # Keep the answer key beside the workspace, never inside the extracted estate.
    (work / "ground_truth.json").write_text(Path(bundle["ground_truth"]).read_text(encoding="utf-8"), encoding="utf-8")
    ingest(Path(bundle["zip"]), work)
    mine(work)
    clarify_work(work, simulate=True)
    compile_work(work)
    metrics = eval_work(work)
    report_work(work)
    metrics["report"] = str(work / "report.html")
    return metrics


def _mean_std(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "stdev": None, "n": 0}
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / len(values)
    return {"mean": round(mean, 4), "stdev": round(var**0.5, 4), "n": len(values)}


def _pick(rows: list[dict], path: list[str]) -> list[float]:
    found = []
    for row in rows:
        cursor: Any = row
        ok = True
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor or cursor[key] is None:
                ok = False
                break
            cursor = cursor[key]
        if ok and isinstance(cursor, (int, float)):
            found.append(float(cursor))
    return found


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = {
        "tacit_recall": _mean_std(_pick(rows, ["tacit_recall"])),
        "precision": _mean_std(_pick(rows, ["precision"])),
        "success_none": _mean_std(_pick(rows, ["ablation", "none", "success_rate"])),
        "success_explicit": _mean_std(_pick(rows, ["ablation", "explicit", "success_rate"])),
        "success_tacit": _mean_std(_pick(rows, ["ablation", "tacit", "success_rate"])),
        "violation_none": _mean_std(_pick(rows, ["ablation", "none", "violation_rate"])),
        "violation_explicit": _mean_std(_pick(rows, ["ablation", "explicit", "violation_rate"])),
        "violation_tacit": _mean_std(_pick(rows, ["ablation", "tacit", "violation_rate"])),
        "questions_per_confirmed_rule": _mean_std(
            _pick(rows, ["clarification", "ranking", "questions_per_confirmed_rule"])
        ),
        "voi_index": _mean_std(_pick(rows, ["clarification", "ranking", "voi", "impact_weighted_index"])),
        "random_index": _mean_std(
            _pick(rows, ["clarification", "ranking", "random_impact_weighted_index_mean"])
        ),
    }
    by_source: dict[str, Any] = {}
    keys: set[str] = set()
    for row in rows:
        keys.update((row.get("recall_by_source") or {}).keys())
    for key in sorted(keys):
        by_source[key] = _mean_std(
            [float(row["recall_by_source"][key]) for row in rows if row.get("recall_by_source") and key in row["recall_by_source"] and row["recall_by_source"][key] is not None]
        )
    metrics["recall_by_source"] = by_source
    return metrics


def run_benchmark(out: Path, seeds: list[int] | None = None, templates: list[str] | None = None) -> dict[str, Any]:
    seeds = seeds or SEEDS
    templates = templates or TEMPLATES
    rows = []
    for template in templates:
        for seed in seeds:
            metrics = run_pipeline(template, seed, out / f"{template}-{seed}")
            rows.append(metrics)
    payload = {"seeds": seeds, "templates": templates, "summary": summarize(rows), "runs": rows}
    _dump(out / "benchmark.json", payload)
    return payload
