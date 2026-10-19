import pandas as pd

from tacit.miners.code_rules import mine_python, mine_sql
from tacit.miners.data_rules import mine_frame
from tacit.miners.llm_extract import UnavailableLLMExtractor
from tacit.miners.text_rules import mine_text
from tacit.synth import _pdf


def test_code_miner_reads_thresholds_and_config():
    source = '''
def validate_order(order, inventory, config):
    if order["discount_pct"] > config["max_discount_pct"]:
        raise ValueError("discount exceeds configured maximum")
    if order["ship_region"] == "华东" and order["amount"] > 5000 and not order.get("region_manager"):
        raise ValueError("east")
    if inventory["on_hand"] - inventory["reserved"] < order["qty"]:
        raise ValueError("stock")
'''
    rules = mine_python(source, "legacy/billing.py", {"max_discount_pct": 15})
    statements = [rule["statement"] for rule in rules]
    assert any(rule["then"][0]["field"] == "discount_pct" and rule["then"][0]["value"] == 15 for rule in rules)
    assert any(atom["field"] == "region_manager" for rule in rules for atom in rule["then"])
    assert any(atom["op"] == "minus_ge" for rule in rules for atom in rule["then"])
    assert statements


def test_sql_forbidden_combination():
    sql = "SELECT order_id FROM orders WHERE status = 'shipped' AND action = 'cancel';\n"
    rules = mine_sql("-- forbidden\n" + sql, "legacy/rules.sql")
    assert len(rules) == 1
    assert rules[0]["modality"] == "must_not"


def test_text_miner_chinese_and_english():
    documents = [
        {
            "kind": "markdown",
            "path": "sops/01.md",
            "text": "# 订单\n\n## 正式制度\n\n订单金额超过 3000 元必须有审批人。\n\n## 备注\n\n退货超过 30 天必须有 VP 书面例外。\n",
        },
        {
            "kind": "markdown",
            "path": "emails/a.md",
            "text": "请注意：华东区订单金额超过 5000 元，必须有区域经理审批后才能出库。\n",
        },
        {
            "kind": "markdown",
            "path": "emails/b.md",
            "text": "Always call the claimant before denying a claim.\n",
        },
    ]
    rules = mine_text(documents)
    assert any(rule["explicit"] and rule["when"][0]["value"] == 3000 for rule in rules)
    assert any(not rule["explicit"] and rule["when"][0]["field"] == "return_age_days" for rule in rules)
    assert any(rule["when"] and rule["when"][0]["value"] == "华东" for rule in rules)
    assert any(rule["then"][0]["field"] == "claimant_called" for rule in rules)


def test_pdf_roundtrip_reaches_the_text_miner():
    blob = _pdf("Official Policy\nPayment terms must be one of NET30, NET45, NET60, or PREPAY.\n")
    path = "/tmp/tacit-terms.pdf"
    with open(path, "wb") as handle:
        handle.write(blob)
    from pypdf import PdfReader

    text = PdfReader(path).pages[0].extract_text()
    rules = mine_text([{"kind": "pdf", "path": "sops/terms.pdf", "text": text}])
    assert rules and rules[0]["explicit"]
    assert "NET60" in rules[0]["then"][0]["value"]


def test_data_miner_finds_a_conditional_and_skips_noise():
    rows = []
    for _ in range(40):
        rows.append({"tier": "strategic", "payment_terms": "NET60", "region": "East"})
    for index in range(80):
        rows.append(
            {
                "tier": "standard",
                "payment_terms": "NET30" if index % 2 == 0 else "NET45",
                "region": "West" if index % 2 == 0 else "East",
            }
        )
    rules = mine_frame(pd.DataFrame(rows), "customers", support_floor=20)
    assert any(
        rule["when"][0]["field"] == "tier" and rule["then"][0]["value"] == "NET60" for rule in rules
    )


def test_llm_extractor_refuses_to_invent_results():
    try:
        UnavailableLLMExtractor().extract("必须", "a.md")
    except RuntimeError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected the offline extractor to refuse")
