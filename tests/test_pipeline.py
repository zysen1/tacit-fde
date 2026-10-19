import json
import zipfile
from pathlib import Path

import pandas as pd

from tacit.inventory import scan_estate
from tacit.pipeline import run_pipeline
from tacit.synth import build_bundle


def test_ground_truth_stays_out_of_the_zip_and_twelve_rules_are_planted():
    for template in ("distribution", "claims"):
        files, truth = build_bundle(template, 7)
        assert "ground_truth.json" not in files
        tacit = [rule for rule in truth["rules"] if rule["tacit"] and rule["authoritative"]]
        assert len(tacit) == 12
        buckets = {rule["bucket"] for rule in tacit}
        assert buckets == {"data", "code", "text", "conflict", "multi"}
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            assert "ground_truth.json" not in archive.namelist()


def test_spreadsheet_parser(tmp_path: Path):
    frame = pd.DataFrame({"sku": ["A-1"], "warehouse": ["WH-1"]})
    frame.to_excel(tmp_path / "items.xlsx", index=False)
    docs, frames = scan_estate(tmp_path)
    assert docs[0]["kind"] == "xlsx"
    assert "items" in frames


def _assert_separated(metrics: dict):
    ablation = metrics["ablation"]
    assert ablation["none"]["success_rate"] < ablation["explicit"]["success_rate"] < ablation["tacit"]["success_rate"]
    assert ablation["none"]["violation_rate"] > ablation["explicit"]["violation_rate"] > ablation["tacit"]["violation_rate"]
    assert metrics["tacit_recall"] >= 0.9
    assert metrics["precision"] >= 0.9
    ranking = metrics["clarification"]["ranking"]
    assert ranking["voi"]["impact_weighted_index"] < ranking["random_impact_weighted_index_mean"]
    assert ranking["questions_per_confirmed_rule"] < 1


def test_distribution_end_to_end(tmp_path: Path):
    metrics = run_pipeline("distribution", 42, tmp_path)
    _assert_separated(metrics)
    assert metrics["tacit_missed"] == []
    report = Path(metrics["report"]).read_text(encoding="utf-8")
    assert "Acme Distribution" in report
    assert "华东" in report
    skill = (tmp_path / "skills" / "distribution" / "SKILL.md").read_text(encoding="utf-8")
    assert "Provenance:" in skill


def test_claims_end_to_end(tmp_path: Path):
    metrics = run_pipeline("claims", 19, tmp_path)
    _assert_separated(metrics)
    assert (tmp_path / "evals" / "acceptance.jsonl").exists()
    assert (tmp_path / "evals" / "heldout.jsonl").exists()


def test_generated_mcp_server_is_read_only(tmp_path: Path):
    run_pipeline("distribution", 42, tmp_path)
    server_path = tmp_path / "harness" / "mcp_server.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("acme_mcp", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flagged = module.check_order(
        json.dumps(
            {
                "ship_region": "华东",
                "amount": 8000,
                "region_manager": None,
                "approver": "AP1",
                "carrier": "顺丰",
                "discount_pct": 4,
                "channel": "direct",
                "ship_country": "CN",
                "priority": "normal",
                "status": "approved",
                "payment_terms": "NET30",
                "tier": "standard",
            }
        )
    )
    assert flagged["decision"] != "approve"
    assert flagged["violations"]
    rows = module.query_table("orders", "amount > 5000", 5)
    assert rows and rows[0]["amount"] > 5000
    try:
        module.query_table("orders", "amount > 1; DROP TABLE orders", 5)
    except ValueError:
        pass
    else:
        raise AssertionError("stacked SQL should be rejected")
    try:
        module.query_table("not_a_table", "TRUE", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown tables should be rejected")
