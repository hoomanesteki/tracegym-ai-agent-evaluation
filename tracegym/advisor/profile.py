"""The Ledger: a read-only cost/token/speed profile of a run.

Everything here is a plain aggregation over stored results and spans, so it is
keyless and deterministic. It answers "where did the cost, tokens, latency, and
tool calls go?" and surfaces the hot spots the advisor's rules act on. Costs are
counterfactual (free-tier spend is $0); the profile tracks them so a change that
would blow a paid budget is visible before you pay for it.
"""

from __future__ import annotations

import json

import numpy as np

from tracegym.capture.otel import COST_USD, REQUEST_MODEL


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    arr = np.asarray(values, dtype=float)
    return {
        "p50": round(float(np.percentile(arr, 50)), 3),
        "p95": round(float(np.percentile(arr, 95)), 3),
        "max": round(float(arr.max()), 3),
    }


def build_profile(conn, run_id: str, *, top_k: int = 10, budget_cap_usd: float = 5.0) -> dict:
    rows = conn.execute("SELECT * FROM results WHERE run_id = ?", (run_id,)).fetchall()
    n = len(rows)
    if n == 0:
        return {"run_id": run_id, "cases": 0}

    input_tokens = int(sum(r["input_tokens"] for r in rows))
    output_tokens = int(sum(r["output_tokens"] for r in rows))
    total_cost = float(sum(r["cost_usd"] for r in rows))

    cost_by_model = conn.execute(
        f"""
        SELECT json_extract(s.attributes, '$."{REQUEST_MODEL}"') AS model,
               COUNT(*) AS spans,
               SUM(CAST(json_extract(s.attributes, '$."{COST_USD}"') AS REAL)) AS cost
        FROM spans s JOIN results r ON r.trace_id = s.trace_id
        WHERE r.run_id = ? AND s.kind = 'llm'
        GROUP BY model ORDER BY cost DESC
        """,
        (run_id,),
    ).fetchall()

    top_cost = conn.execute(
        "SELECT case_id, cost_usd, latency_ms, input_tokens, output_tokens, tool_calls "
        "FROM results WHERE run_id = ? ORDER BY cost_usd DESC LIMIT ?",
        (run_id, top_k),
    ).fetchall()
    top_latency = conn.execute(
        "SELECT case_id, latency_ms, cost_usd FROM results WHERE run_id = ? "
        "ORDER BY latency_ms DESC LIMIT ?",
        (run_id, top_k),
    ).fetchall()

    return {
        "run_id": run_id,
        "cases": n,
        "totals": {
            "cost_usd": round(total_cost, 8),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tool_calls": int(sum(r["tool_calls"] for r in rows)),
            "oi_ratio": round(output_tokens / input_tokens, 4) if input_tokens else 0.0,
        },
        "latency_ms": _percentiles([r["latency_ms"] for r in rows]),
        "cost_usd_dist": _percentiles([r["cost_usd"] for r in rows]),
        "cost_by_model": [
            {"model": r["model"], "spans": r["spans"], "cost_usd": round(r["cost"] or 0, 8)}
            for r in cost_by_model
        ],
        "top_cost_cases": [dict(r) for r in top_cost],
        "top_latency_cases": [dict(r) for r in top_latency],
        "budget": {
            "cap_usd": budget_cap_usd,
            "spent_usd": round(total_cost, 8),
            "pct_used": round(total_cost / budget_cap_usd * 100, 2) if budget_cap_usd else 0.0,
            "basis": "counterfactual cost; free-tier spend is $0",
        },
    }


def profile_json(conn, run_id: str, **kwargs) -> str:
    return json.dumps(build_profile(conn, run_id, **kwargs), indent=2)
