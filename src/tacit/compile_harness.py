"""Compile confirmed rules into SKILL.md, a FastMCP server, and acceptance cases."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from tacit.engine import _paint, describe_atom

_SERVER = '''"""Read-only FastMCP server compiled by Tacit.

The tools query a local SQLite extract and validate records against confirmed
rules. They do not write, and `where_sql` cannot smuggle a second statement.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
from fastmcp import FastMCP

from tacit.engine import decision, violates
from tacit.sqlguard import safe_predicate

ROOT = Path(__file__).resolve().parent
RULES = json.loads((ROOT / "rules_confirmed.json").read_text(encoding="utf-8"))
DB_PATH = ROOT / "estate.sqlite"
TABLES = set({tables!r})

mcp = FastMCP("{company}")


def _connect():
    connection = duckdb.connect()
    escaped = str(DB_PATH).replace("'", "''")
    connection.execute(f"ATTACH '{{escaped}}' AS estate (TYPE SQLITE, READ_ONLY)")
    return connection


@mcp.tool(annotations={{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}})
def list_rules() -> list[dict]:
    """Return the confirmed business rules and their provenance."""
    return [
        {{
            "id": rule["id"],
            "statement": rule["statement"],
            "severity": rule["severity"],
            "explicit": rule["explicit"],
            "confidence": rule["confidence"],
            "sources": rule["sources"],
        }}
        for rule in RULES
    ]


@mcp.tool(annotations={{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}})
def query_table(table: str, where_sql: str = "TRUE", limit: int = 50) -> list[dict]:
    """Read rows from one estate table. `where_sql` is a boolean expression, not a statement."""
    if table not in TABLES:
        raise ValueError(f"unknown table {{table!r}}")
    if not safe_predicate(where_sql):
        raise ValueError("where_sql must be a single read-only boolean expression")
    capped = max(1, min(int(limit), 200))
    predicate = where_sql.strip()
    connection = _connect()
    try:
        frame = connection.execute(
            f'SELECT * FROM estate."{{table}}" WHERE ({{predicate}}) LIMIT {{capped}}'
        ).fetchdf()
    finally:
        connection.close()
    return json.loads(frame.to_json(orient="records"))


@mcp.tool(annotations={{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}})
def check_order(order_json: str) -> dict:
    """Validate one order or claim. Returns the decision and the rules it violated."""
    record = json.loads(order_json)
    label, fired = decision(record, RULES)
    return {{
        "decision": label,
        "violations": [
            {{
                "id": rule["id"],
                "statement": rule["statement"],
                "severity": rule["severity"],
                "violates": violates(rule, record),
            }}
            for rule in fired
        ],
    }}


if __name__ == "__main__":
    mcp.run()
'''


def _skill(company: str, domain: str, rules: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        f"name: {domain}-rules",
        "description: Confirmed business rules compiled by Tacit. Each rule cites the evidence it was mined from.",
        "---",
        "",
        f"# {company}",
        "",
        "Use these rules before approving, pricing, shipping, or paying a record.",
        "When a rule's evidence conflicts, the confirmed statement below is the one the stakeholder accepted.",
        "",
    ]
    for rule in rules:
        lines.append(f"## {rule['id']}. {rule['statement']}")
        lines.append("")
        lines.append(f"- Status: {rule['status']}")
        lines.append(f"- Modality: {rule['modality']}")
        lines.append(f"- Severity: {rule['severity']}")
        lines.append(f"- Explicit: {str(rule['explicit']).lower()}")
        lines.append(f"- Confidence: {rule['confidence']}")
        when = ", ".join(describe_atom(item) for item in rule.get("when") or []) or "always"
        then = ", ".join(describe_atom(item) for item in rule.get("then") or [])
        lines.append(f"- When: {when}")
        lines.append(f"- Then: {then}")
        lines.append("- Provenance:")
        for source in rule.get("sources") or []:
            where = source.get("path", "")
            if source.get("line"):
                where += f":{source['line']}"
            detail = source.get("snippet") or ""
            detail = " ".join(detail.split())
            if len(detail) > 220:
                detail = detail[:217] + "..."
            extra = ""
            if source.get("support") is not None:
                extra = f" support={source['support']} confidence={source.get('confidence')}"
            lines.append(f"  - {source.get('kind')} `{where}`{extra}: {detail}")
        lines.append("")
    return "\n".join(lines)


def _acceptance(rules: list[dict[str, Any]]) -> str:
    rows = []
    for rule in rules:
        for violate, name in ((True, "neg"), (False, "pos")):
            case = _paint(rule, violate)
            rows.append(
                {
                    "id": f"{rule['id']}-{name}",
                    "rule_id": rule["id"],
                    "case": case,
                    "expect_violation": violate,
                }
            )
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def compile_harness(work: Path, company: str, domain: str, rules: list[dict[str, Any]]) -> dict[str, str]:
    confirmed = [rule for rule in rules if rule.get("status") == "confirmed"]
    skill_dir = work / "skills" / domain
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(_skill(company, domain, confirmed), encoding="utf-8")
    harness = work / "harness"
    harness.mkdir(parents=True, exist_ok=True)
    rules_path = harness / "rules_confirmed.json"
    rules_path.write_text(json.dumps(confirmed, ensure_ascii=False, indent=2), encoding="utf-8")
    estate = work / "estate"
    sqlite_files = sorted(estate.rglob("*.sqlite"))
    if not sqlite_files:
        raise RuntimeError("estate has no sqlite database to attach")
    shutil.copy(sqlite_files[0], harness / "estate.sqlite")
    inventory = json.loads((work / "inventory.json").read_text(encoding="utf-8"))
    tables = sorted({name for doc in inventory["documents"] for name in doc.get("tables") or []})
    server = _SERVER.format(company=company, tables=tables)
    server_path = harness / "mcp_server.py"
    server_path.write_text(server, encoding="utf-8")
    evals = work / "evals"
    evals.mkdir(parents=True, exist_ok=True)
    acceptance = evals / "acceptance.jsonl"
    acceptance.write_text(_acceptance(confirmed), encoding="utf-8")
    return {
        "skill": str(skill_path),
        "server": str(server_path),
        "rules": str(rules_path),
        "acceptance": str(acceptance),
    }
