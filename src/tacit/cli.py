"""Command line for the offline Tacit pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tacit import __version__
from tacit.pipeline import (
    SEEDS,
    TEMPLATES,
    clarify_work,
    compile_work,
    eval_work,
    ingest,
    mine,
    report_work,
    run_benchmark,
    run_pipeline,
)
from tacit.synth import write_bundle


def _print_metrics(metrics: dict) -> None:
    ablation = metrics.get("ablation") or {}
    ranking = (metrics.get("clarification") or {}).get("ranking") or {}
    print(f"template={metrics.get('template')} seed={metrics.get('seed')}")
    print(f"tacit recall={metrics.get('tacit_recall')} precision={metrics.get('precision')}")
    print(f"recall by source={json.dumps(metrics.get('recall_by_source'), ensure_ascii=False)}")
    if metrics.get("tacit_missed"):
        print(f"missed={metrics['tacit_missed']}")
    if metrics.get("false_positives"):
        print(f"false positives={len(metrics['false_positives'])}")
    if ranking:
        print(
            "questions/confirmed="
            f"{ranking.get('questions_per_confirmed_rule')} "
            f"voi_index={ranking.get('voi', {}).get('impact_weighted_index')} "
            f"random_index={ranking.get('random_impact_weighted_index_mean')}"
        )
    for name in ("none", "explicit", "tacit"):
        block = ablation.get(name)
        if block:
            print(f"{name}: success={block['success_rate']} violation={block['violation_rate']}")
    if metrics.get("report"):
        print(f"report={metrics['report']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tacit", description="Mine tacit business rules and compile them into an agent harness.")
    parser.add_argument("--version", action="version", version=f"tacit {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    synth = sub.add_parser("synth", help="Write a synthetic estate zip and a ground-truth file beside it")
    synth.add_argument("--template", choices=TEMPLATES, default="distribution")
    synth.add_argument("--seed", type=int, default=42)
    synth.add_argument("--out", type=Path, required=True)

    ingest_p = sub.add_parser("ingest", help="Inventory a zip or directory")
    ingest_p.add_argument("source", type=Path)
    ingest_p.add_argument("--workspace", type=Path, required=True)

    mine_p = sub.add_parser("mine", help="Mine and consolidate rule candidates")
    mine_p.add_argument("--workspace", type=Path, required=True)

    clarify_p = sub.add_parser("clarify", help="Rank clarifying questions")
    clarify_p.add_argument("--workspace", type=Path, required=True)
    clarify_p.add_argument("--sim", action="store_true", help="Answer with the scripted stakeholder")

    compile_p = sub.add_parser("compile", help="Write SKILL.md, the FastMCP server, and acceptance cases")
    compile_p.add_argument("--workspace", type=Path, required=True)

    eval_p = sub.add_parser("eval", help="Score recall, precision, questions, and the ablation")
    eval_p.add_argument("--workspace", type=Path, required=True)

    report_p = sub.add_parser("report", help="Write the offline HTML report")
    report_p.add_argument("--workspace", type=Path, required=True)

    demo = sub.add_parser("demo", help="Run Acme Distribution end to end (offline, seed 42)")
    demo.add_argument("--out", type=Path, default=Path("tacit-demo"))
    demo.add_argument("--seed", type=int, default=42)

    bench = sub.add_parser("benchmark", help="Repeat the pipeline across seeds and both industry templates")
    bench.add_argument("--out", type=Path, default=Path("benchmark"))
    bench.add_argument("--seeds", type=int, nargs="*", default=SEEDS)

    args = parser.parse_args(argv)
    if args.cmd == "synth":
        written = write_bundle(args.template, args.seed, args.out)
        print(json.dumps(written, indent=2))
        return
    if args.cmd == "ingest":
        args.workspace.mkdir(parents=True, exist_ok=True)
        summary = ingest(args.source, args.workspace)
        print(f"files={len(summary['documents'])}")
        return
    if args.cmd == "mine":
        rules = mine(args.workspace)
        print(f"candidates={len(rules)}")
        return
    if args.cmd == "clarify":
        result = clarify_work(args.workspace, simulate=args.sim)
        print(f"questions={len(result['questions'])}")
        return
    if args.cmd == "compile":
        paths = compile_work(args.workspace)
        print(json.dumps(paths, indent=2))
        return
    if args.cmd == "eval":
        _print_metrics(eval_work(args.workspace))
        return
    if args.cmd == "report":
        print(report_work(args.workspace))
        return
    if args.cmd == "demo":
        metrics = run_pipeline("distribution", args.seed, args.out)
        _print_metrics(metrics)
        return
    if args.cmd == "benchmark":
        payload = run_benchmark(args.out, seeds=args.seeds)
        print(json.dumps(payload["summary"], indent=2))
        return
    parser.error(args.cmd)


if __name__ == "__main__":
    main()
