"""Execution accuracy: benchmark the agent's SQL against a gold query.

This is the "benchmark an agent against a domain solver" metric (the standard
Spider/BIRD execution-accuracy idea): run the gold SQL and the agent's SQL against
the same bundled database and compare result sets as multisets (row order does not
matter, values within a row do). Both queries go through the read-only guard, so
the oracle can never mutate the database either. Keyless and deterministic.
"""

from __future__ import annotations

from tracegym.checks.base import CheckResult, get_field, register
from tracegym.checks.sql_guard import run_select


def _multiset(rows: list[tuple]) -> list[tuple]:
    return sorted(tuple(str(v) for v in row) for row in rows)


@register("sql_exec_accuracy")
def check_sql_exec_accuracy(trace: dict, spec: dict) -> CheckResult:
    db = spec.get("db")
    gold = spec.get("gold_sql")
    field = spec.get("field", "sql")
    agent_sql = get_field(trace, field)
    if not db or not gold:
        return CheckResult("sql_exec_accuracy", False, "spec needs db and gold_sql")
    if not isinstance(agent_sql, str) or not agent_sql.strip():
        return CheckResult("sql_exec_accuracy", False, f"no agent SQL at output.{field}")
    try:
        gold_rows = run_select(db, gold)
    except Exception as exc:
        return CheckResult("sql_exec_accuracy", False, f"gold query error: {exc}")
    try:
        agent_rows = run_select(db, agent_sql)
    except Exception as exc:
        return CheckResult("sql_exec_accuracy", False, f"agent query refused/errored: {exc}")
    ok = _multiset(gold_rows) == _multiset(agent_rows)
    detail = f"gold={len(gold_rows)} rows, agent={len(agent_rows)} rows, match={ok}"
    return CheckResult("sql_exec_accuracy", ok, detail)
