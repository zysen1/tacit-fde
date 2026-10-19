"""Near-invariant constraints over CSV / SQLite tables, measured in DuckDB.

The search is deliberately narrow: low-cardinality conditions, business-round
numeric cutpoints, and prefix buckets. A rule at an unrounded threshold, a
cross-table join, or a pure negative ("this never happens") will not be found.
That limitation is part of the published eval, not an accident.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import duckdb
import pandas as pd

from tacit.engine import atom, describe, infer_severity

ROUND_CUTS = [
    1,
    2,
    5,
    8,
    10,
    15,
    30,
    60,
    100,
    500,
    1000,
    2000,
    3000,
    5000,
    8000,
    10000,
    15000,
    20000,
]
MIN_CONF = 0.97
MIN_LIFT = 0.20


def min_support(nrows: int) -> int:
    return max(20, int(0.02 * nrows))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _clause(item: dict[str, Any]) -> tuple[str, list[Any]]:
    field = _quote(item["field"])
    op = item["op"]
    if op == "not_null":
        return f"{field} IS NOT NULL", []
    if op == "is_null":
        return f"{field} IS NULL", []
    if op == "prefix":
        return f"starts_with(CAST({field} AS VARCHAR), ?)", [item["value"]]
    if op == "==":
        return f"{field} = ?", [item["value"]]
    if op == "!=":
        return f"{field} <> ?", [item["value"]]
    if op in {">", ">=", "<", "<="}:
        return f"{field} {op} ?", [item["value"]]
    raise ValueError(op)


def _measure(con: duckdb.DuckDBPyConnection, table: str, rule: dict[str, Any]) -> tuple[int, float, str]:
    where_sql, params = [], []
    for item in rule["when"]:
        sql, extra = _clause(item)
        where_sql.append(sql)
        params.extend(extra)
    then_sql, then_params = [], []
    for item in rule["then"]:
        sql, extra = _clause(item)
        then_sql.append(sql)
        then_params.extend(extra)
    where = " AND ".join(where_sql) if where_sql else "TRUE"
    then = " AND ".join(then_sql)
    sql = (
        f"SELECT count(*) AS support, "
        f"count(*) FILTER (WHERE {then}) AS hits "
        f"FROM {_quote(table)} WHERE {where}"
    )
    # Placeholders in the SELECT list are bound before the WHERE clause.
    support, hits = con.execute(sql, then_params + params).fetchone()
    support = int(support or 0)
    confidence = float(hits) / support if support else 0.0
    rendered = sql
    for value in then_params + params:
        shown = "'" + str(value).replace("'", "''") + "'" if isinstance(value, str) else str(value)
        rendered = rendered.replace("?", shown, 1)
    return support, confidence, rendered


def _as_rule(
    *,
    table: str,
    when: list[dict],
    then: list[dict],
    support: int,
    confidence: float,
    rendered: str,
) -> dict[str, Any]:
    rule = {
        "modality": "must",
        "when": when,
        "then": then,
        "sources": [
            {
                "kind": "data",
                "path": table,
                "table": table,
                "snippet": rendered,
                "confidence": round(confidence, 4),
                "support": support,
            }
        ],
        "source_kinds": ["data"],
        "explicit": False,
        "confidence": round(confidence, 4),
        "support": support,
        "status": "candidate",
        "conflict_group": None,
    }
    rule["severity"] = infer_severity(rule)
    rule["statement"] = describe(rule)
    return rule


def _confirm(con, table, when, then, floor) -> dict[str, Any] | None:
    draft = {"when": when, "then": then}
    support, confidence, rendered = _measure(con, table, draft)
    if support < floor or confidence < MIN_CONF:
        return None
    return _as_rule(
        table=table, when=when, then=then, support=support, confidence=confidence, rendered=rendered
    )


def _code_like(series: pd.Series) -> bool:
    """Surrogate ids (RM1, AP3, HS4402) are not business dimensions.

    Payment terms such as NET45 stay eligible: the prefix is a whole word, not a 1–2 letter code.
    """
    values = [str(value) for value in series.dropna().unique()[:40]]
    if len(values) < 2:
        return False
    pattern = re.compile(r"^[A-Z]{1,2}\d+$")
    return sum(bool(pattern.match(value)) for value in values) / len(values) >= 0.8


def _categoricals(df: pd.DataFrame) -> list[str]:
    found = []
    for column in df.columns:
        if column.endswith("_id") or column in {"name"}:
            continue
        series = df[column]
        unique = series.nunique(dropna=True)
        if not 2 <= unique <= 12:
            continue
        if float(series.notna().mean()) < 0.5:
            continue
        if _code_like(series):
            continue
        found.append(column)
    return found


def _too_common(frame: pd.DataFrame, then: list[dict]) -> bool:
    """Skip consequents that are already the majority value; those restatements explode."""
    for item in then:
        if item["op"] != "==" or item["field"] not in frame.columns:
            continue
        rate = float((frame[item["field"]] == item["value"]).mean())
        if rate > 0.6:
            return True
    return False


def _numerics(df: pd.DataFrame) -> list[str]:
    found = []
    for column in df.columns:
        if column.endswith("_id"):
            continue
        series = df[column]
        if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 12:
            found.append(column)
    return found


def _prefix_column(series: pd.Series) -> pd.Series:
    def one(value: Any) -> str | None:
        text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
        if "-" not in text:
            return None
        head, _tail = text.split("-", 1)
        if head.isalpha() and len(head) >= 2:
            return head + "-"
        return None

    return series.map(one)


def _lift(inside: float, outside: float) -> bool:
    return inside >= MIN_CONF and inside - outside >= MIN_LIFT


def mine_frame(
    df: pd.DataFrame,
    table: str,
    *,
    min_conf: float = MIN_CONF,
    support_floor: int | None = None,
) -> list[dict]:
    del min_conf  # duckdb confirm uses the module constant; tests pass support_floor
    if df.empty:
        return []
    floor = support_floor if support_floor is not None else min_support(len(df))
    frame = df.copy()
    prefix_sources: list[tuple[str, pd.Series]] = []
    for column in list(frame.columns):
        if not (pd.api.types.is_object_dtype(frame[column]) or pd.api.types.is_string_dtype(frame[column])):
            continue
        derived = _prefix_column(frame[column])
        if derived.nunique(dropna=True) >= 2 and derived.notna().sum() >= floor:
            prefix_sources.append((column, derived))

    con = duckdb.connect()
    con.register(table, frame)
    cats = _categoricals(frame)
    nums = _numerics(frame)
    rules: list[dict] = []

    slices: list[tuple[list[dict], pd.Series]] = []
    for column in cats:
        for value, group in frame.groupby(column, dropna=True):
            if len(group) < floor:
                continue
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            mask = frame[column] == group[column].iloc[0]
            # groupby value may differ by type from the cell; use index membership
            mask = frame.index.isin(group.index)
            slices.append(([atom(column, "==", value if not hasattr(value, "item") else value)], mask))
    for column, derived in prefix_sources:
        for prefix, _group in frame.groupby(derived, dropna=True):
            if prefix is None or (isinstance(prefix, float) and pd.isna(prefix)):
                continue
            mask = derived == prefix
            if int(mask.sum()) < floor:
                continue
            slices.append(([atom(column, "prefix", prefix)], mask))

    def add_equality_and_required(when: list[dict], mask: pd.Series, skip: set[str]) -> None:
        inside = frame.loc[mask]
        outside = frame.loc[~mask]
        if len(inside) < floor or outside.empty:
            return
        for target in cats:
            if target in skip:
                continue
            observed = inside[target].dropna()
            if len(observed) < floor:
                continue
            mode = observed.mode(dropna=True)
            if mode.empty:
                continue
            chosen = mode.iloc[0]
            if hasattr(chosen, "item") and not isinstance(chosen, str):
                try:
                    chosen = chosen.item()
                except Exception:
                    pass
            in_rate = float((observed == chosen).mean())
            out_obs = outside[target].dropna()
            out_rate = float((out_obs == chosen).mean()) if len(out_obs) else 0.0
            if not _lift(in_rate, out_rate) or _too_common(frame, [atom(target, "==", chosen)]):
                continue
            rule = _confirm(con, table, when, [atom(target, "==", chosen)], floor)
            if rule and rule["confidence"] - out_rate >= MIN_LIFT:
                rules.append(rule)
        for target in frame.columns:
            if target in skip or target.endswith("_id"):
                continue
            if float(frame[target].notna().mean()) >= 0.85:
                continue
            in_rate = float(inside[target].notna().mean())
            out_rate = float(outside[target].notna().mean())
            if len(inside) < floor or not _lift(in_rate, out_rate):
                continue
            rule = _confirm(con, table, when, [atom(target, "not_null")], floor)
            if rule and rule["confidence"] - out_rate >= MIN_LIFT:
                rules.append(rule)

    for when, mask in slices:
        skip = {item["field"] for item in when}
        add_equality_and_required(when, mask, skip)
        inside_idx = mask
        for number_col in nums:
            if number_col in skip:
                continue
            for target in cats:
                if target in skip:
                    continue
                for cut in ROUND_CUTS:
                    right = inside_idx & frame[number_col].gt(cut)
                    left = inside_idx & frame[number_col].le(cut)
                    if int(right.sum()) < floor or int(left.sum()) < floor:
                        continue
                    observed = frame.loc[right, target].dropna()
                    if len(observed) < floor:
                        continue
                    mode = observed.mode(dropna=True)
                    if mode.empty:
                        continue
                    chosen = mode.iloc[0]
                    in_rate = float((observed == chosen).mean())
                    out_obs = frame.loc[left, target].dropna()
                    out_rate = float((out_obs == chosen).mean()) if len(out_obs) else 0.0
                    if not _lift(in_rate, out_rate) or _too_common(frame, [atom(target, "==", chosen)]):
                        continue
                    when_cut = when + [atom(number_col, ">", cut)]
                    rule = _confirm(con, table, when_cut, [atom(target, "==", chosen)], floor)
                    if rule and rule["confidence"] - out_rate >= MIN_LIFT:
                        rules.append(rule)
                        break
            for target in frame.columns:
                if target in skip or target == number_col or target.endswith("_id"):
                    continue
                if float(frame[target].notna().mean()) >= 0.85:
                    continue
                for cut in ROUND_CUTS:
                    right = inside_idx & frame[number_col].gt(cut)
                    left = inside_idx & frame[number_col].le(cut)
                    if int(right.sum()) < floor or int(left.sum()) < floor:
                        continue
                    in_rate = float(frame.loc[right, target].notna().mean())
                    out_rate = float(frame.loc[left, target].notna().mean())
                    if not _lift(in_rate, out_rate):
                        continue
                    when_cut = when + [atom(number_col, ">", cut)]
                    rule = _confirm(con, table, when_cut, [atom(target, "not_null")], floor)
                    if rule and rule["confidence"] - out_rate >= MIN_LIFT:
                        rules.append(rule)
                        break
        # numeric upper bounds inside the slice, compared with rows outside the slice
        outside_idx = ~inside_idx
        if int(outside_idx.sum()) < floor:
            continue
        for number_col in nums:
            if number_col in skip:
                continue
            series_in = frame.loc[inside_idx, number_col].dropna()
            series_out = frame.loc[outside_idx, number_col].dropna()
            if len(series_in) < floor or series_out.empty:
                continue
            for cut in ROUND_CUTS:
                in_rate = float((series_in <= cut).mean())
                out_rate = float((series_out <= cut).mean())
                if not _lift(in_rate, out_rate):
                    continue
                rule = _confirm(con, table, when, [atom(number_col, "<=", cut)], floor)
                if rule and rule["confidence"] - out_rate >= MIN_LIFT:
                    rules.append(rule)
                    break

    return _collapse_exceptions(rules, frame, table, con, floor)


def _collapse_exceptions(rules, frame, table, con, floor) -> list[dict]:
    """Replace value-by-value required-field rules with one `!=` rule when a single exception remains."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    keep: list[dict] = []
    for rule in rules:
        when = rule["when"]
        then = rule["then"]
        if (
            len(when) == 1
            and when[0]["op"] == "=="
            and len(then) == 1
            and then[0]["op"] == "not_null"
        ):
            groups[(when[0]["field"], then[0]["field"])].append(rule)
        else:
            keep.append(rule)
    for (field, target), grouped in groups.items():
        if field not in frame.columns or len(grouped) < 2:
            keep.extend(grouped)
            continue
        covered = {rule["when"][0]["value"] for rule in grouped}
        observed = set(frame[field].dropna().unique())
        missing = observed - covered
        if len(missing) != 1:
            keep.extend(grouped)
            continue
        exception = next(iter(missing))
        exception_rows = frame.loc[frame[field] == exception, target]
        exception_rate = float(exception_rows.notna().mean()) if len(exception_rows) else 1.0
        if exception_rate > 0.5:
            keep.extend(grouped)
            continue
        rule = _confirm(con, table, [atom(field, "!=", exception)], [atom(target, "not_null")], floor)
        if rule:
            keep.append(rule)
        else:
            keep.extend(grouped)
    return keep


def mine_tables(frames: dict[str, pd.DataFrame]) -> list[dict]:
    rules: list[dict] = []
    for name, frame in frames.items():
        rules.extend(mine_frame(frame, name))
    return rules
