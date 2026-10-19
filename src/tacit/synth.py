"""Synthetic customer estates with planted ground truth.

The generator is part of the product on purpose. Rules that leave no trace in
code, tables, or normative text are not planted, and the README lists the
rule shapes this search cannot see.
"""

from __future__ import annotations

import io
import json
import random
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from tacit.engine import atom, infer_severity

TEMPLATES = ("distribution", "claims")


def _rule(
    rule_id: str,
    *,
    modality: str,
    when: list[dict],
    then: list[dict],
    bucket: str,
    tacit: bool,
    authoritative: bool,
    explicit: bool,
    statement: str,
    note: str = "",
) -> dict[str, Any]:
    rule = {
        "id": rule_id,
        "modality": modality,
        "when": when,
        "then": then,
        "bucket": bucket,
        "tacit": tacit,
        "authoritative": authoritative,
        "explicit": explicit,
        "statement": statement,
        "note": note,
    }
    rule["severity"] = infer_severity(rule)
    return rule


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8")


def _sqlite_bytes(tables: dict[str, pd.DataFrame]) -> bytes:
    handle = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    handle.close()
    path = Path(handle.name)
    connection = sqlite3.connect(path)
    try:
        for name, frame in tables.items():
            frame.to_sql(name, connection, index=False, if_exists="replace")
        connection.commit()
    finally:
        connection.close()
    data = path.read_bytes()
    path.unlink()
    return data


def _pdf(text: str) -> bytes:
    lines: list[str] = []
    for raw in text.splitlines():
        raw = raw.encode("latin-1", "replace").decode("latin-1")
        while len(raw) > 88:
            lines.append(raw[:88])
            raw = raw[88:]
        lines.append(raw)
    ops = ["BT", "/F1 11 Tf"]
    y = 740
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 54 {y} Tm ({safe}) Tj")
        y -= 16
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return bytes(out)


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _distribution_rules() -> list[dict[str, Any]]:
    return [
        _rule(
            "D01",
            modality="must",
            when=[atom("tier", "==", "strategic")],
            then=[atom("payment_terms", "==", "NET60")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Strategic customers are always on NET60.",
        ),
        _rule(
            "D02",
            modality="must",
            when=[atom("ship_region", "==", "华东"), atom("amount", ">", 2000)],
            then=[atom("carrier", "==", "顺丰")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="East China orders over 2000 ship with SF Express.",
        ),
        _rule(
            "D03",
            modality="must",
            when=[atom("sku", "prefix", "COLD-")],
            then=[atom("warehouse", "==", "WH-COLD")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="COLD- SKUs are stored only in WH-COLD.",
        ),
        _rule(
            "D04",
            modality="must",
            when=[atom("channel", "==", "marketplace")],
            then=[atom("discount_pct", "<=", 8)],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Marketplace discounts stay at or below 8%.",
        ),
        _rule(
            "C01",
            modality="must",
            when=[],
            then=[atom("discount_pct", "<=", 15)],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Discount cannot exceed the configured maximum of 15%.",
        ),
        _rule(
            "C02",
            modality="must",
            when=[],
            then=[atom("on_hand", "minus_ge", None, value_field="reserved", rhs_field="qty")],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Available stock (on_hand - reserved) must cover qty.",
        ),
        _rule(
            "C03",
            modality="must_not",
            when=[atom("status", "==", "shipped")],
            then=[atom("action", "==", "cancel")],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Do not cancel an order that has already shipped.",
        ),
        _rule(
            "T01",
            modality="must",
            when=[atom("sku_substituted", "==", 1)],
            then=[atom("customer_called", "==", 1)],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Call the customer before substituting a SKU.",
        ),
        _rule(
            "T02",
            modality="must_not",
            when=[atom("created_dow", "==", 5), atom("created_hour", ">=", 16)],
            then=[atom("priority", "==", "rush")],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Do not accept rush orders after 16:00 on Friday.",
        ),
        _rule(
            "T03",
            modality="must",
            when=[atom("return_age_days", ">", 30)],
            then=[atom("vp_exception", "not_null")],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Returns older than 30 days need a VP written exception.",
        ),
        _rule(
            "X01",
            modality="must",
            when=[atom("amount", ">", 3000)],
            then=[atom("approver", "not_null")],
            bucket="conflict",
            tacit=True,
            authoritative=True,
            explicit=True,
            statement="Orders over 3000 require an approver.",
            note="The March 2026 policy update set the approver threshold at 3000. billing.py still checks 5000 and is stale.",
        ),
        _rule(
            "X01S",
            modality="must",
            when=[atom("amount", ">", 5000)],
            then=[atom("approver", "not_null")],
            bucket="stale",
            tacit=False,
            authoritative=False,
            explicit=False,
            statement="Legacy code requires an approver only above 5000.",
            note="The March 2026 policy update set the approver threshold at 3000. billing.py still checks 5000 and is stale.",
        ),
        _rule(
            "M01",
            modality="must",
            when=[atom("ship_region", "==", "华东"), atom("amount", ">", 5000)],
            then=[atom("region_manager", "not_null")],
            bucket="multi",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="East China orders over 5000 require a region manager.",
        ),
        _rule(
            "E1",
            modality="must",
            when=[atom("ship_country", "!=", "CN")],
            then=[atom("hs_code", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Export orders must carry an HS code.",
        ),
        _rule(
            "E2",
            modality="must",
            when=[atom("priority", "==", "rush")],
            then=[atom("contact_phone", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Rush orders must leave a contact phone.",
        ),
        _rule(
            "E3",
            modality="must",
            when=[],
            then=[atom("payment_terms", "in", ["NET30", "NET45", "NET60", "PREPAY"])],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Payment terms must be one of NET30, NET45, NET60, or PREPAY.",
        ),
        _rule(
            "E4",
            modality="must",
            when=[atom("status", "==", "cancelled")],
            then=[atom("cancel_reason", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Cancelled orders must record a reason.",
        ),
    ]


def _claims_rules() -> list[dict[str, Any]]:
    return [
        _rule(
            "D01",
            modality="must",
            when=[atom("tier", "==", "vip")],
            then=[atom("settlement_terms", "==", "NET45")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="VIP policyholders always settle on NET45.",
        ),
        _rule(
            "D02",
            modality="must",
            when=[atom("region", "==", "South"), atom("amount", ">", 8000)],
            then=[atom("doc_pack", "==", "photos")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="South region claims over 8000 use a photo pack.",
        ),
        _rule(
            "D03",
            modality="must",
            when=[atom("coverage_code", "prefix", "FLOOD-")],
            then=[atom("book", "==", "CAT-BOOK")],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="FLOOD- coverages sit in the CAT book.",
        ),
        _rule(
            "D04",
            modality="must",
            when=[atom("channel", "==", "portal")],
            then=[atom("payout_discount", "<=", 5)],
            bucket="data",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Portal payout discounts stay at or below 5.",
        ),
        _rule(
            "C01",
            modality="must",
            when=[],
            then=[atom("payout_discount", "<=", 10)],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Payout discount cannot exceed the configured maximum of 10.",
        ),
        _rule(
            "C02",
            modality="must",
            when=[],
            then=[atom("reserves", "ge_field", None, value_field="amount")],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Reserves must cover the claimed amount.",
        ),
        _rule(
            "C03",
            modality="must_not",
            when=[atom("status", "==", "paid")],
            then=[atom("action", "==", "reopen")],
            bucket="code",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Do not reopen a claim that has already been paid.",
        ),
        _rule(
            "T01",
            modality="must",
            when=[atom("status", "==", "denied")],
            then=[atom("claimant_called", "==", 1)],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Always call the claimant before denying a claim.",
        ),
        _rule(
            "T02",
            modality="must_not",
            when=[atom("created_dow", "==", 5), atom("created_hour", ">=", 16)],
            then=[atom("liability_negotiation", "==", 1)],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Never negotiate liability after 16:00 on Friday.",
        ),
        _rule(
            "T03",
            modality="must",
            when=[atom("supplement_age_days", ">", 60)],
            then=[atom("vp_exception", "not_null")],
            bucket="text",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="Supplemental claims after 60 days need a VP written exception.",
        ),
        _rule(
            "X01",
            modality="must",
            when=[atom("amount", ">", 10000)],
            then=[atom("supervisor", "not_null")],
            bucket="conflict",
            tacit=True,
            authoritative=True,
            explicit=True,
            statement="Claims over 10000 require a supervisor.",
            note="Claims ops reset the supervisor threshold to 10000 in January 2026. The legacy script still checks 20000.",
        ),
        _rule(
            "X01S",
            modality="must",
            when=[atom("amount", ">", 20000)],
            then=[atom("supervisor", "not_null")],
            bucket="stale",
            tacit=False,
            authoritative=False,
            explicit=False,
            statement="Legacy code requires a supervisor only above 20000.",
            note="Claims ops reset the supervisor threshold to 10000 in January 2026. The legacy script still checks 20000.",
        ),
        _rule(
            "M01",
            modality="must",
            when=[atom("region", "==", "South"), atom("amount", ">", 15000)],
            then=[atom("regional_manager", "not_null")],
            bucket="multi",
            tacit=True,
            authoritative=True,
            explicit=False,
            statement="South region claims over 15000 require a regional manager.",
        ),
        _rule(
            "E1",
            modality="must",
            when=[atom("third_party", "==", 1)],
            then=[atom("medical_report_id", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Third-party injury claims must have a medical report.",
        ),
        _rule(
            "E2",
            modality="must",
            when=[atom("priority", "==", "catastrophe")],
            then=[atom("contact_phone", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Catastrophe claims must have a contact phone.",
        ),
        _rule(
            "E3",
            modality="must",
            when=[],
            then=[atom("status", "in", ["open", "investigating", "approved", "denied", "closed", "paid"])],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Claim status must be one of open, investigating, approved, denied, closed, or paid.",
        ),
        _rule(
            "E4",
            modality="must",
            when=[atom("status", "==", "denied")],
            then=[atom("denial_reason", "not_null")],
            bucket="explicit",
            tacit=False,
            authoritative=True,
            explicit=True,
            statement="Denied claims must have a denial reason.",
        ),
    ]


_DIST_BASE = {
    "tier": "standard",
    "payment_terms": "NET30",
    "ship_region": "华北",
    "region": "华北",
    "amount": 800.0,
    "carrier": "中通",
    "channel": "direct",
    "discount_pct": 4.0,
    "sku": "GEN-001",
    "warehouse": "WH-NORTH",
    "region_manager": "RM1",
    "approver": "AP1",
    "ship_country": "CN",
    "hs_code": "HS1000",
    "priority": "normal",
    "contact_phone": "13800000000",
    "status": "approved",
    "cancel_reason": "noted",
    "action": "ship",
    "on_hand": 100,
    "reserved": 10,
    "qty": 2,
    "sku_substituted": 0,
    "customer_called": 1,
    "created_dow": 1,
    "created_hour": 10,
    "return_age_days": 3,
    "vp_exception": "VP-1",
    "customer_id": "C0001",
    "order_id": "O-CLEAN",
}

_CLAIMS_BASE = {
    "tier": "standard",
    "settlement_terms": "NET15",
    "region": "North",
    "amount": 500.0,
    "doc_pack": "notes",
    "channel": "phone",
    "payout_discount": 2.0,
    "coverage_code": "GEN-001",
    "book": "STD-BOOK",
    "regional_manager": "RM1",
    "supervisor": "SV1",
    "third_party": 0,
    "medical_report_id": "MR1",
    "priority": "normal",
    "contact_phone": "5550100",
    "status": "approved",
    "denial_reason": "noted",
    "action": "hold",
    "reserves": 800.0,
    "claimant_called": 1,
    "created_dow": 1,
    "created_hour": 10,
    "liability_negotiation": 0,
    "supplement_age_days": 5,
    "vp_exception": "VP-1",
    "policyholder_id": "P0001",
    "claim_id": "CL-CLEAN",
}


def _distribution_files(seed: int) -> dict[str, bytes]:
    rng = random.Random(seed)
    tiers = ["strategic"] * 80 + ["preferred"] * 120 + ["standard"] * 300
    rng.shuffle(tiers)
    regions = ["华东", "华南", "华北", "西南", "西北"]
    customers = []
    for index, tier in enumerate(tiers, start=1):
        # Ten preferred accounts also use NET60 so the converse is not a near-invariant.
        if tier == "strategic":
            terms = "NET60"
        elif tier == "preferred" and sum(row["tier"] == "preferred" and row["payment_terms"] == "NET60" for row in customers) < 10:
            terms = "NET60"
        else:
            terms = rng.choice(["NET30", "NET45", "PREPAY"])
        customers.append(
            {
                "customer_id": f"C{index:04d}",
                "name": f"Outlet {index:04d}",
                "tier": tier,
                "payment_terms": terms,
                "region": regions[index % 5],
                "credit_limit": rng.randint(1000, 50000),
            }
        )
    inventory = []
    for index in range(24):
        inventory.append(
            {
                "sku": f"COLD-{index:03d}",
                "warehouse": "WH-COLD",
                "on_hand": rng.randint(0, 40),
                "reserved": rng.randint(0, 60),
            }
        )
    for index in range(56):
        inventory.append(
            {
                "sku": f"GEN-{index:03d}",
                "warehouse": rng.choice(["WH-EAST", "WH-SOUTH", "WH-NORTH"]),
                "on_hand": rng.randint(0, 40),
                "reserved": rng.randint(0, 60),
            }
        )
    specs = (
        [{"region": "华东", "band": "low"}] * 250
        + [{"region": "华东", "band": "mid"}] * 275
        + [{"region": "华东", "band": "high"}] * 125
    )
    others = ["华南", "华北", "西南", "西北"]
    for index in range(950):
        specs.append({"region": others[index % 4], "band": rng.choice(["low", "mid", "high"])})
    rng.shuffle(specs)
    channels = ["marketplace"] * 480 + ["direct"] * 1120
    rng.shuffle(channels)
    countries = ["US"] * 60 + ["DE"] * 60 + ["JP"] * 60 + ["CN"] * 1420
    rng.shuffle(countries)
    statuses = ["cancelled"] * 80 + ["shipped"] * 360 + ["approved"] * 500 + ["picking"] * 360 + ["new"] * 300
    rng.shuffle(statuses)
    priorities = ["rush"] * 120 + ["normal"] * 1480
    rng.shuffle(priorities)
    east_mid = [i for i, spec in enumerate(specs) if spec["region"] == "华东" and spec["band"] == "mid"]
    east_high = [i for i, spec in enumerate(specs) if spec["region"] == "华东" and spec["band"] == "high"]
    carrier_bad = set(east_mid[:2] + east_high[:1])
    manager_bad = set(east_high[:1])
    market_idx = [i for i, channel in enumerate(channels) if channel == "marketplace"]
    discount_bad = set(market_idx[:4])
    orders = []
    for index, spec in enumerate(specs):
        band = spec["band"]
        if band == "low":
            amount = round(rng.uniform(120, 1990), 2)
        elif band == "mid":
            amount = round(rng.uniform(2001, 4990), 2)
        else:
            amount = round(rng.uniform(5001, 18000), 2)
        region = spec["region"]
        channel = channels[index]
        if channel == "marketplace":
            discount = round(rng.uniform(8.2, 11), 2) if index in discount_bad else round(rng.uniform(0, 8), 2)
        else:
            discount = round(rng.uniform(0, 20), 2)
        if region == "华东" and amount > 2000:
            carrier = rng.choice(["中通", "圆通", "京东"]) if index in carrier_bad else "顺丰"
        else:
            carrier = rng.choice(["顺丰", "中通", "圆通", "京东"])
        if region == "华东" and amount > 5000:
            manager = None if index in manager_bad else f"RM{rng.randint(1, 9)}"
        else:
            manager = f"RM{rng.randint(1, 9)}" if rng.random() < 0.15 else None
        if amount > 3000:
            approver = f"AP{rng.randint(1, 5)}" if rng.random() < 0.62 else None
        else:
            approver = f"AP{rng.randint(1, 5)}" if rng.random() < 0.08 else None
        country = countries[index]
        hs_code = f"HS{rng.randint(1000, 9999)}" if country != "CN" else None
        priority = priorities[index]
        phone = f"138{rng.randint(10_000_000, 99_999_999)}" if priority == "rush" or rng.random() < 0.35 else None
        status = statuses[index]
        reason = rng.choice(["customer", "stock", "duplicate"]) if status == "cancelled" else None
        sku = rng.choice(inventory)
        orders.append(
            {
                "order_id": f"O{index + 1:05d}",
                "customer_id": rng.choice(customers)["customer_id"],
                "sku": sku["sku"],
                "qty": rng.randint(1, 20),
                "amount": amount,
                "discount_pct": discount,
                "ship_region": region,
                "ship_country": country,
                "carrier": carrier,
                "status": status,
                "region_manager": manager,
                "approver": approver,
                "hs_code": hs_code,
                "priority": priority,
                "contact_phone": phone,
                "cancel_reason": reason,
                "channel": channel,
                "warehouse": sku["warehouse"],
            }
        )
    tables = {
        "customers": pd.DataFrame(customers),
        "orders": pd.DataFrame(orders),
        "inventory": pd.DataFrame(inventory),
    }
    sop = """# 订单与审批

## 正式制度

1. 订单金额超过 3000 元必须有审批人。
2. 出口订单必须有 HS 编码。
3. 加急订单必须留下联系电话。
4. 取消订单必须填写原因。

## 仓储备注

退货超过 30 天必须有 VP 书面例外，否则仓库拒收。
值班表贴在仓库门口，承运商周末只跑已进仓的货。
"""
    warehouse = """# 仓库值班

早班 8:00 盘点冷链月台。
午班核对装箱单和承运商交接。
晚班只处理已进仓的正常单，不加开新波次。
"""
    returns = """# 退货台账

客户寄回的货先拍照再上架。
外箱破损单独放在黄色托盘。
财务每周五上午来对退货金额。
"""
    emails = {
        7: (
            "From: ops-director@acme.example\nTo: warehouse-east@acme.example\n"
            "Date: 2025-11-12\nSubject: 华东大额订单\n\n"
            "请注意：华东区订单金额超过 5000 元，必须有区域经理审批后才能出库。\n"
            "未盖章的单子先放在待发区。\n"
        ),
        11: (
            "From: scheduling@acme.example\nDate: 2025-06-02\nSubject: 加急截单\n\n"
            "不得在周五 16:00 后接受加急订单。周末值班只处理已进仓的货。\n"
        ),
        15: (
            "From: cs-lead@acme.example\nDate: 2025-08-19\nSubject: 替换 SKU\n\n"
            "必须在替换 SKU 之前致电客户确认。先换后说的情况上月有三起投诉。\n"
        ),
    }
    fillers = [
        "From: warehouse@acme.example\nDate: 2025-04-0{day}\nSubject: 日清\n\n今日出库 {n} 箱，残次 2 箱已隔离。\n",
        "From: finance@acme.example\nDate: 2025-05-1{day}\nSubject: 对账\n\n本周发票 {n} 张，差异在承运商燃油附加。\n",
        "From: planning@acme.example\nDate: 2025-07-0{day}\nSubject: 补货\n\n华东仓本周发货 {n} 单，排班照旧。\n",
    ]
    files: dict[str, bytes] = {}
    for name, frame in tables.items():
        files[f"{name}.csv"] = _csv_bytes(frame)
    files["acme.sqlite"] = _sqlite_bytes(tables)
    files["sops/01_order_policy.md"] = sop.encode()
    files["sops/02_warehouse.md"] = warehouse.encode()
    files["sops/03_returns_log.md"] = returns.encode()
    files["sops/04_payment_terms.pdf"] = _pdf(
        "Official Policy\n"
        "Payment terms must be one of NET30, NET45, NET60, or PREPAY.\n"
        "Warehouse staff do not renegotiate this schedule.\n"
    )
    for number in range(1, 21):
        if number in emails:
            body = emails[number]
        else:
            template = fillers[number % 3]
            body = template.format(day=number % 9 + 1, n=40 + number)
        files[f"emails/msg_{number:02d}.md"] = body.encode()
    files["legacy/billing.py"] = '''"""Legacy billing checks. Last hand edit 2024-11."""


def validate_order(order, inventory, config):
    if order["discount_pct"] > config["max_discount_pct"]:
        raise ValueError("discount exceeds configured maximum")
    if (
        order["ship_region"] == "华东"
        and order["amount"] > 5000
        and not order.get("region_manager")
    ):
        raise ValueError("east china high value requires region manager")
    if order["amount"] > 5000 and not order.get("approver"):
        raise ValueError("amount threshold requires approver")
    if inventory["on_hand"] - inventory["reserved"] < order["qty"]:
        raise ValueError("insufficient available stock")
'''.encode()
    files["legacy/rules.sql"] = (
        "-- forbidden: cancelling an order that has already shipped\n"
        "SELECT order_id FROM orders WHERE status = 'shipped' AND action = 'cancel';\n"
    ).encode()
    files["config/billing.yaml"] = "max_discount_pct: 15\ncurrency: CNY\n".encode()
    return files


def _claims_files(seed: int) -> dict[str, bytes]:
    rng = random.Random(seed)
    tiers = ["vip"] * 80 + ["preferred"] * 140 + ["standard"] * 280
    rng.shuffle(tiers)
    regions = ["South", "North", "East", "West"]
    holders = []
    for index, tier in enumerate(tiers, start=1):
        if tier == "vip":
            terms = "NET45"
        elif tier == "preferred" and sum(row["tier"] == "preferred" and row["settlement_terms"] == "NET45" for row in holders) < 10:
            terms = "NET45"
        else:
            terms = rng.choice(["NET15", "NET30"])
        holders.append(
            {
                "policyholder_id": f"P{index:04d}",
                "name": f"Holder {index:04d}",
                "tier": tier,
                "settlement_terms": terms,
                "region": regions[index % 4],
            }
        )
    coverages = []
    for index in range(24):
        coverages.append(
            {
                "coverage_code": f"FLOOD-{index:03d}",
                "book": "CAT-BOOK",
                "limit_amt": rng.randint(20000, 80000),
            }
        )
    for index in range(56):
        coverages.append(
            {
                "coverage_code": f"GEN-{index:03d}",
                "book": rng.choice(["STD-BOOK", "MARINE-BOOK"]),
                "limit_amt": rng.randint(5000, 60000),
            }
        )
    specs = (
        [{"region": "South", "band": "low"} for _ in range(220)]
        + [{"region": "South", "band": "mid"} for _ in range(275)]
        + [{"region": "South", "band": "high"} for _ in range(125)]
    )
    nonsouth = ["North", "East", "West"]
    for index in range(980):
        specs.append({"region": nonsouth[index % 3], "band": rng.choice(["low", "mid", "high"])})
    rng.shuffle(specs)
    channels = ["portal"] * 480 + ["agent"] * 560 + ["phone"] * 560
    rng.shuffle(channels)
    statuses = (
        ["denied"] * 80
        + ["paid"] * 200
        + ["approved"] * 420
        + ["open"] * 360
        + ["investigating"] * 300
        + ["closed"] * 240
    )
    rng.shuffle(statuses)
    priorities = ["catastrophe"] * 100 + ["normal"] * 1500
    rng.shuffle(priorities)
    third = [1] * 100 + [0] * 1500
    rng.shuffle(third)
    south_mid = [i for i, spec in enumerate(specs) if spec["region"] == "South" and spec["band"] == "mid"]
    south_high = [i for i, spec in enumerate(specs) if spec["region"] == "South" and spec["band"] == "high"]
    photo_bad = set(south_mid[:2])
    manager_bad = set(south_high[:1])
    portal_idx = [i for i, channel in enumerate(channels) if channel == "portal"]
    discount_bad = set(portal_idx[:4])
    packs = ["photos", "notes", "none"]
    claims = []
    for index, spec in enumerate(specs):
        band = spec["band"]
        if band == "low":
            amount = round(rng.uniform(200, 7900), 2)
        elif band == "mid":
            amount = round(rng.uniform(8001, 14990), 2)
        else:
            amount = round(rng.uniform(15001, 40000), 2)
        region = spec["region"]
        if region == "South" and amount > 8000:
            doc_pack = rng.choice(["notes", "none"]) if index in photo_bad else "photos"
        else:
            doc_pack = rng.choice(packs)
        if region == "South" and amount > 15000:
            manager = None if index in manager_bad else f"RM{rng.randint(1, 8)}"
        else:
            manager = f"RM{rng.randint(1, 8)}" if rng.random() < 0.12 else None
        if amount > 10000:
            supervisor = f"SV{rng.randint(1, 6)}" if rng.random() < 0.60 else None
        else:
            supervisor = f"SV{rng.randint(1, 6)}" if rng.random() < 0.1 else None
        channel = channels[index]
        if channel == "portal":
            discount = round(rng.uniform(5.4, 9), 2) if index in discount_bad else round(rng.uniform(0, 5), 2)
        else:
            discount = round(rng.uniform(0, 18), 2)
        status = statuses[index]
        priority = priorities[index]
        is_third = third[index]
        claims.append(
            {
                "claim_id": f"CL{index + 1:05d}",
                "policyholder_id": rng.choice(holders)["policyholder_id"],
                "coverage_code": rng.choice(coverages)["coverage_code"],
                "amount": amount,
                "region": region,
                "loss_type": rng.choice(["water", "fire", "theft", "glass"]),
                "channel": channel,
                "status": status,
                "doc_pack": doc_pack,
                "payout_discount": discount,
                "regional_manager": manager,
                "supervisor": supervisor,
                "third_party": is_third,
                "medical_report_id": f"MR{rng.randint(1000, 9999)}" if is_third else None,
                "priority": priority,
                "contact_phone": f"555{rng.randint(1000, 9999)}" if priority == "catastrophe" or rng.random() < 0.3 else None,
                "denial_reason": rng.choice(["wear", "exclusion", "late"]) if status == "denied" else None,
            }
        )
    # Attach book from coverage for the coverage table only. Claims reference coverage_code;
    # the prefix rule lives on the coverages table. Also copy book onto claims so the
    # prefix rule is visible without a join — same single-table miner the tool ships.
    book_of = {row["coverage_code"]: row["book"] for row in coverages}
    for row in claims:
        row["book"] = book_of[row["coverage_code"]]
    tables = {
        "policyholders": pd.DataFrame(holders),
        "claims": pd.DataFrame(claims),
        "coverages": pd.DataFrame(coverages),
    }
    sop = """# Claims handling

## Official Policy

1. Claims over 10000 require a supervisor.
2. Third-party injury claims must have a medical report.
3. Catastrophe claims must have a contact phone.
4. Denied claims must have a denial reason.

## Floor notes

Supplemental claims filed more than 60 days later require a VP written exception.
Adjusters keep photos on the share drive, not in this note.
"""
    files: dict[str, bytes] = {}
    for name, frame in tables.items():
        files[f"{name}.csv"] = _csv_bytes(frame)
    files["acme.sqlite"] = _sqlite_bytes(tables)
    files["sops/01_claims_policy.md"] = sop.encode()
    files["sops/02_desk.md"] = (
        "# Desk rota\n\nMorning triage starts at 8:30.\n"
        "Vendor invoices land with finance on Thursday.\n"
    ).encode()
    files["sops/03_photos.md"] = (
        "# Photo desk\n\nLabel files with the claim id.\n"
        "Do not rename customer uploads.\n"
    ).encode()
    files["sops/04_status.pdf"] = _pdf(
        "Official Policy\n"
        "Claim status must be one of open, investigating, approved, denied, closed, or paid.\n"
        "Desk staff do not invent extra statuses.\n"
    )
    emails = {
        4: (
            "From: claims-director@acme.example\nDate: 2025-11-03\nSubject: South payments\n\n"
            "South region claims over 15000 require a regional manager before payment.\n"
        ),
        9: (
            "From: quality@acme.example\nDate: 2025-05-14\nSubject: Denials\n\n"
            "Always call the claimant before denying a claim.\n"
        ),
        16: (
            "From: legal@acme.example\nDate: 2025-09-01\nSubject: Friday cutoff\n\n"
            "Never negotiate liability after 16:00 on Friday.\n"
        ),
    }
    fillers = [
        "From: desk@acme.example\nDate: 2025-03-0{day}\nSubject: Queue\n\nOpen files this morning: {n}.\n",
        "From: finance@acme.example\nDate: 2025-04-1{day}\nSubject: Reserves\n\nPosted {n} reserve updates today.\n",
        "From: field@acme.example\nDate: 2025-08-0{day}\nSubject: Travel\n\nTwo site visits slipped to next week.\n",
    ]
    for number in range(1, 21):
        body = emails.get(number) or fillers[number % 3].format(day=number % 9 + 1, n=15 + number)
        files[f"emails/msg_{number:02d}.md"] = body.encode()
    files["legacy/billing.py"] = '''"""Legacy claim checks. Last hand edit 2024-08."""


def validate_claim(claim, config):
    if claim["payout_discount"] > config["max_payout_discount"]:
        raise ValueError("payout discount exceeds configured maximum")
    if claim["amount"] > 20000 and not claim.get("supervisor"):
        raise ValueError("amount threshold requires supervisor")
    if (
        claim["region"] == "South"
        and claim["amount"] > 15000
        and not claim.get("regional_manager")
    ):
        raise ValueError("south high value requires regional manager")
    if claim["reserves"] < claim["amount"]:
        raise ValueError("reserves below claimed amount")
'''.encode()
    files["legacy/rules.sql"] = (
        "-- forbidden: reopening a claim that has already been paid\n"
        "SELECT claim_id FROM claims WHERE status = 'paid' AND action = 'reopen';\n"
    ).encode()
    files["config/billing.yaml"] = "max_payout_discount: 10\ncurrency: USD\n".encode()
    return files


def build_bundle(template: str, seed: int) -> tuple[dict[str, bytes], dict[str, Any]]:
    if template == "distribution":
        files = _distribution_files(seed)
        rules = _distribution_rules()
        company, domain, base = "Acme Distribution", "distribution", _DIST_BASE
    elif template == "claims":
        files = _claims_files(seed)
        rules = _claims_rules()
        company, domain, base = "Acme Claims", "claims", _CLAIMS_BASE
    else:
        raise ValueError(f"unknown template {template!r}; expected one of {TEMPLATES}")
    tacit = [rule for rule in rules if rule["tacit"] and rule["authoritative"]]
    if len(tacit) != 12:
        raise RuntimeError(f"{template} planted {len(tacit)} tacit rules, expected 12")
    ground_truth = {
        "template": template,
        "seed": seed,
        "company": company,
        "domain": domain,
        "base_case": base,
        "rules": rules,
    }
    return files, ground_truth


def write_bundle(template: str, seed: int, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files, ground_truth = build_bundle(template, seed)
    zip_path = out_dir / "estate.zip"
    zip_path.write_bytes(_zip(files))
    truth_path = out_dir / "ground_truth.json"
    truth_path.write_text(json.dumps(ground_truth, ensure_ascii=False, indent=2), encoding="utf-8")
    # The answer key stays beside the zip. It is not a member of the archive.
    with zipfile.ZipFile(zip_path) as archive:
        if "ground_truth.json" in archive.namelist():
            raise RuntimeError("ground truth leaked into the estate zip")
    return {"zip": str(zip_path), "ground_truth": str(truth_path)}
