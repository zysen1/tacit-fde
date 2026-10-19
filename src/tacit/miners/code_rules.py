"""Mine thresholds and guards from Python AST, SQL WHERE clauses, and YAML constants."""

from __future__ import annotations

import ast
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from tacit.engine import atom, describe, infer_severity

_CMP_AST = {
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}
_NEGATE = {">": "<=", ">=": "<", "<": ">=", "<=": ">", "==": "!=", "!=": "=="}


def _constants_from_yaml(text: str) -> dict[str, float]:
    try:
        loaded = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    found: dict[str, float] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    found[str(key)] = float(value)
                else:
                    walk(value)

    walk(loaded)
    return found


def _subscript(node: ast.AST) -> tuple[str | None, str | None]:
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        if isinstance(node.value, ast.Name) and isinstance(node.slice.value, str):
            return node.value.id, node.slice.value
    return None, None


def _missing_field(node: ast.AST) -> str | None:
    if not isinstance(node, ast.UnaryOp) or not isinstance(node.op, ast.Not):
        return None
    inner = node.operand
    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
        if inner.func.attr == "get" and inner.args and isinstance(inner.args[0], ast.Constant):
            if isinstance(inner.args[0].value, str):
                return inner.args[0].value
    _root, key = _subscript(inner)
    return key


def _number(node: ast.AST, constants: dict[str, float]) -> tuple[float | None, str | None]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
        node.value, bool
    ):
        return float(node.value), None
    root, key = _subscript(node)
    if root == "config" and key in constants:
        return constants[key], key
    return None, None


def _field_name(node: ast.AST) -> str | None:
    _root, key = _subscript(node)
    return key


def _contains_raise(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Raise) for child in ast.walk(node))


def _candidate(
    *,
    modality: str,
    when: list[dict],
    then: list[dict],
    path: str,
    line: int,
    snippet: str,
    confidence: float,
    config_key: str | None = None,
) -> dict[str, Any]:
    sources = [
        {
            "kind": "code",
            "path": path,
            "line": line,
            "snippet": snippet.strip(),
            "confidence": confidence,
        }
    ]
    if config_key:
        sources.append(
            {
                "kind": "code",
                "path": path,
                "line": line,
                "snippet": f"config.{config_key}",
                "confidence": confidence,
                "config_key": config_key,
            }
        )
    rule = {
        "modality": modality,
        "when": when,
        "then": then,
        "sources": sources,
        "source_kinds": ["code"],
        "explicit": False,
        "confidence": confidence,
        "support": None,
        "status": "candidate",
        "conflict_group": None,
    }
    rule["severity"] = infer_severity(rule)
    rule["statement"] = describe(rule)
    return rule


def _from_guard(
    terms: list[ast.AST],
    path: str,
    line: int,
    snippet: str,
    constants: dict[str, float],
) -> dict[str, Any] | None:
    missing: list[str] = []
    guards: list[dict] = []
    config_key = None
    for term in terms:
        missed = _missing_field(term)
        if missed:
            missing.append(missed)
            continue
        if not isinstance(term, ast.Compare) or len(term.ops) != 1:
            return None
        op = _CMP_AST.get(type(term.ops[0]))
        if op is None:
            return None
        left = term.left
        right = term.comparators[0]
        # available = on_hand - reserved < qty
        if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Sub) and op == "<":
            minuend = _field_name(left.left)
            subtrahend = _field_name(left.right)
            rhs = _field_name(right)
            if minuend and subtrahend and rhs:
                return _candidate(
                    modality="must",
                    when=[],
                    then=[atom(minuend, "minus_ge", None, value_field=subtrahend, rhs_field=rhs)],
                    path=path,
                    line=line,
                    snippet=snippet,
                    confidence=0.74,
                )
        left_field = _field_name(left)
        right_field = _field_name(right)
        number, key = _number(right, constants)
        if key:
            config_key = key
        if left_field and number is not None:
            guards.append(atom(left_field, op, number if number % 1 else int(number)))
            continue
        if left_field and right_field and op == "<":
            return _candidate(
                modality="must",
                when=[],
                then=[atom(left_field, "ge_field", None, value_field=right_field)],
                path=path,
                line=line,
                snippet=snippet,
                confidence=0.74,
            )
        if left_field and right_field and op == "==":
            guards.append(atom(left_field, "==", right_field))
            continue
        if (
            left_field
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
            and op in ("==", "!=")
        ):
            guards.append(atom(left_field, op, right.value))
            continue
        number_left, key_left = _number(left, constants)
        if key_left:
            config_key = key_left
        if right_field and number_left is not None:
            guards.append(atom(right_field, _NEGATE[op], number_left if number_left % 1 else int(number_left)))
            continue
        return None
    if missing:
        return _candidate(
            modality="must",
            when=guards,
            then=[atom(name, "not_null") for name in missing],
            path=path,
            line=line,
            snippet=snippet,
            confidence=0.74,
            config_key=config_key,
        )
    if not guards:
        return None
    *head, last = guards
    negated = atom(last["field"], _NEGATE[last["op"]], last.get("value"))
    if last["op"] == "<" and last.get("value_field"):
        return None
    return _candidate(
        modality="must",
        when=head,
        then=[negated],
        path=path,
        line=line,
        snippet=snippet,
        confidence=0.74,
        config_key=config_key,
    )


def mine_python(text: str, path: str, constants: dict[str, float]) -> list[dict]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    rules = []
    lines = text.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _contains_raise(node):
            continue
        test = node.test
        terms = test.values if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else [test]
        snippet = "\n".join(lines[node.lineno - 1 : min(len(lines), node.lineno + 3)])
        rule = _from_guard(list(terms), path, node.lineno, snippet, constants)
        if rule:
            rules.append(rule)
    return rules


def _sql_literal(node: exp.Expression) -> Any:
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        try:
            return int(node.this) if str(node.this).isdigit() else float(node.this)
        except ValueError:
            return node.this
    return None


def _sql_column(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Column):
        return node.name
    return None


def _sql_atom(node: exp.Expression) -> dict | None:
    if isinstance(node, exp.EQ):
        col = _sql_column(node.left)
        val = _sql_literal(node.right)
        if col is not None and val is not None:
            return atom(col, "==", val)
    if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
        mapping = {exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
        col = _sql_column(node.left)
        val = _sql_literal(node.right)
        if col is not None and val is not None:
            return atom(col, mapping[type(node)], val)
    return None


def _and_atoms(node: exp.Expression) -> list[dict]:
    if isinstance(node, exp.And):
        return _and_atoms(node.left) + _and_atoms(node.right)
    parsed = _sql_atom(node)
    return [parsed] if parsed else []


def mine_sql(text: str, path: str) -> list[dict]:
    if "forbidden" not in text.lower():
        return []
    rules = []
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except Exception:
        return []
    for statement in statements:
        if statement is None:
            continue
        where = statement.find(exp.Where)
        if where is None:
            continue
        atoms = _and_atoms(where.this)
        if len(atoms) < 1:
            continue
        *head, last = atoms
        rule = _candidate(
            modality="must_not",
            when=head,
            then=[last],
            path=path,
            line=1,
            snippet=statement.sql(dialect="duckdb"),
            confidence=0.7,
        )
        rules.append(rule)
    return rules


def mine_code(documents: list[dict[str, Any]]) -> list[dict]:
    constants: dict[str, float] = {}
    for doc in documents:
        if doc["kind"] == "yaml":
            constants.update(_constants_from_yaml(doc.get("text") or ""))
    rules: list[dict] = []
    for doc in documents:
        text = doc.get("text") or ""
        if doc["kind"] == "python":
            rules.extend(mine_python(text, doc["path"], constants))
        elif doc["kind"] == "sql":
            rules.extend(mine_sql(text, doc["path"]))
    return rules
