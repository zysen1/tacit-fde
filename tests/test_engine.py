
import pytest

from tacit.engine import agrees, atom, decision, infer_severity, isolate, realize, violates
from tacit.sqlguard import safe_predicate


def test_threshold_rules_do_not_match_across_a_real_gap():
    strict = {
        "modality": "must",
        "when": [{"field": "amount", "op": ">", "value": 3000}],
        "then": [{"field": "approver", "op": "not_null", "value": None}],
        "severity": "needs_approval",
    }
    loose = {
        "modality": "must",
        "when": [{"field": "amount", "op": ">", "value": 5000}],
        "then": [{"field": "approver", "op": "not_null", "value": None}],
        "severity": "needs_approval",
    }
    assert not agrees(strict, loose)
    assert not agrees(loose, strict)
    near = {
        **loose,
        "when": [{"field": "amount", "op": ">", "value": 5050}],
    }
    assert agrees(near, loose)


def test_violation_and_decision_order():
    rule = {
        "modality": "must",
        "when": [{"field": "amount", "op": ">", "value": 3000}],
        "then": [{"field": "approver", "op": "not_null", "value": None}],
    }
    rule["severity"] = infer_severity(rule)
    case = {"amount": 3060, "approver": None}
    assert violates(rule, case)
    assert not violates(rule, {"amount": 3060, "approver": "AP1"})
    label, fired = decision(case, [rule])
    assert label == "needs_approval"
    assert fired


def test_realize_prefix_and_inventory():
    stock = {"field": "on_hand", "op": "minus_ge", "value": None, "value_field": "reserved", "rhs_field": "qty"}
    assert realize(stock, True)["on_hand"] - realize(stock, True)["reserved"] >= realize(stock, True)["qty"]
    broken = realize(stock, False)
    assert broken["on_hand"] - broken["reserved"] < broken["qty"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "amount > 1; DROP TABLE orders",
        "amount > 1 UNION SELECT 1",
        "read_csv('x')",
        "amount > (SELECT 1)",
    ],
)
def test_sql_guard_rejects_statements(sql):
    assert safe_predicate(sql) is False


def test_isolate_keeps_a_covering_constraint_above_the_focused_amount():
    focus = {
        "id": "X",
        "modality": "must",
        "when": [atom("amount", ">", 10000)],
        "then": [atom("supervisor", "not_null")],
    }
    cover = {
        "id": "C",
        "modality": "must",
        "when": [],
        "then": [atom("reserves", "ge_field", None, value_field="amount")],
    }
    case = isolate({"amount": 500, "reserves": 800, "supervisor": "SV1"}, focus, [focus, cover], True)
    assert case["amount"] > 10000
    assert case["supervisor"] is None
    assert violates(focus, case)
    assert not violates(cover, case)
    assert case["reserves"] >= case["amount"]


def test_sql_guard_allows_a_predicate():
    assert safe_predicate("TRUE")
    assert safe_predicate("amount > 5000 AND ship_region = '华东'")
    assert safe_predicate("") is False
