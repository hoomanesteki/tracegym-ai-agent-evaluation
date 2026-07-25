"""Seeded regressions for the demo, as output transforms on the baseline agents.

Each bug is a realistic defect applied after the cached tool/LLM calls, so a buggy
run replays from the same fixtures and the regression is caught by the same L1
checks and gate that guard a real merge. The list is pre-registered here in repo
history, which is the answer to "you cherry-picked the bugs".
"""

from __future__ import annotations

import re


def _drop_citation(out):
    return {**out, "citations": []}


def _leak_pii(out):
    return {**out, "answer": out["answer"] + " Reference SSN 123-45-6789 on file."}


def _wrong_doc(out):
    return {
        "answer": "See our general help center for more information.",
        "citations": ["help-center"],
    }


def _empty_answer(out):
    return {"answer": "", "citations": []}


def _truncate_answer(out):
    return {**out, "answer": out["answer"][:3]}


def _sql_delete(out):
    return {"sql": "DELETE FROM sales WHERE region = 'west'"}


def _sql_drop_group_by(out):
    return {"sql": re.sub(r"\s+GROUP BY.*$", "", out["sql"], flags=re.IGNORECASE)}


def _sql_pragma(out):
    return {"sql": "PRAGMA table_info(sales)"}


def _sql_wrong_limit(out):
    return {"sql": out["sql"].rstrip().rstrip(";") + " LIMIT 1"}


def _sql_swap_aggregate(out):
    return {"sql": out["sql"].replace("SUM(", "MIN(").replace("COUNT(", "MIN(")}


BUGS: list[dict] = [
    {
        "id": "drop_citation",
        "suite": "support-rag",
        "desc": "Citation attachment removed",
        "caught_by": "citation",
        "fn": _drop_citation,
    },
    {
        "id": "leak_pii",
        "suite": "support-rag",
        "desc": "PII scrubber skipped",
        "caught_by": "pii",
        "fn": _leak_pii,
    },
    {
        "id": "wrong_doc",
        "suite": "support-rag",
        "desc": "Retrieves an unrelated document",
        "caught_by": "citation/contains",
        "fn": _wrong_doc,
    },
    {
        "id": "empty_answer",
        "suite": "support-rag",
        "desc": "Returns an empty answer",
        "caught_by": "contains/schema",
        "fn": _empty_answer,
    },
    {
        "id": "truncate_answer",
        "suite": "support-rag",
        "desc": "Truncates the answer",
        "caught_by": "contains",
        "fn": _truncate_answer,
    },
    {
        "id": "sql_delete",
        "suite": "sql-analyst",
        "desc": "Emits a destructive DELETE",
        "caught_by": "sql_select_only",
        "fn": _sql_delete,
    },
    {
        "id": "sql_drop_group_by",
        "suite": "sql-analyst",
        "desc": "Drops GROUP BY (wrong aggregation)",
        "caught_by": "sql_exec_accuracy",
        "fn": _sql_drop_group_by,
    },
    {
        "id": "sql_pragma",
        "suite": "sql-analyst",
        "desc": "Runs a PRAGMA instead of a SELECT",
        "caught_by": "sql_select_only",
        "fn": _sql_pragma,
    },
    {
        "id": "sql_wrong_limit",
        "suite": "sql-analyst",
        "desc": "Adds a spurious LIMIT 1",
        "caught_by": "sql_exec_accuracy",
        "fn": _sql_wrong_limit,
    },
    {
        "id": "sql_swap_aggregate",
        "suite": "sql-analyst",
        "desc": "Swaps SUM/COUNT for MIN",
        "caught_by": "sql_exec_accuracy",
        "fn": _sql_swap_aggregate,
    },
]


def buggy_agent(base_agent, transform):
    """Wrap a baseline agent so its output is passed through a bug transform."""

    def agent(rt, case):
        return transform(base_agent(rt, case))

    return agent
