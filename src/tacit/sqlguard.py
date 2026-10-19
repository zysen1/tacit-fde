"""Read-only predicate sandbox for generated MCP query tools."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

_BANNED = re.compile(
    r"[;]|/\*|\*/|--|\b("
    r"select|insert|update|delete|drop|attach|detach|copy|pragma|call|from|join|"
    r"union|create|alter|truncate|replace|grant|revoke|load|install|export|import|"
    r"read_csv|read_parquet|glob|sqlite_scan|parquet_scan"
    r")\b",
    re.IGNORECASE,
)


def safe_predicate(sql: str) -> bool:
    """True when `sql` is the literal TRUE or one boolean expression with no table access.

    An empty predicate is rejected. Callers that want every row pass TRUE.
    Generated query tools wrap the predicate themselves:
    ``SELECT * FROM table WHERE (<predicate>) LIMIT n``.
    """
    if sql is None:
        return False
    text = sql.strip()
    if not text:
        return False
    if text.upper() == "TRUE":
        return True
    if _BANNED.search(text):
        return False
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except Exception:
        return False
    if len(statements) != 1 or statements[0] is None:
        return False
    tree = statements[0]
    banned_types = (
        exp.Select,
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Command,
        exp.Union,
        exp.Subquery,
        exp.Table,
    )
    if isinstance(tree, banned_types):
        return False
    for node in tree.walk():
        if isinstance(node, banned_types):
            return False
    return True
