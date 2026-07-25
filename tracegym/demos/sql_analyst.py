"""SQL-analyst demo agent: read the schema, write one SELECT.

The agent reads the table schema, asks the model for a single SQL statement (the
local responder returns the canned query for the question), and returns it. Two
independent checks grade it: sql_select_only (a safety invariant) and
sql_exec_accuracy (correctness, benchmarked against a gold query run over the
bundled database). Seeded bugs are output transforms (see bugs.py).
"""

from __future__ import annotations


def sql_responder(question_to_sql: dict[str, str]):
    """A local stand-in model that returns the canned SQL for a known question."""

    def responder(messages: list[dict], model: str) -> str:
        user = messages[-1].get("content", "")
        for question, sql in question_to_sql.items():
            if question in user:
                return sql
        return "SELECT 1"

    return responder


def make_sql_analyst(schema_text: str):
    """Build a sql-analyst agent(runtime, case) bound to a schema description."""

    def agent(rt, case) -> dict:
        question = case["input"]["question"]

        @rt.tool
        def schema():
            return {"schema": schema_text}

        schema()
        prompt = (
            f"Write exactly one SQL SELECT to answer.\nQuestion: {question}\nSchema: {schema_text}"
        )
        sql = rt.chat([{"role": "user", "content": prompt}])["content"]
        return {"sql": sql}

    return agent
