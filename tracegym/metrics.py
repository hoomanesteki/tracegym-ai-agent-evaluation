"""Agent-level metrics: turn per-case results into an agent scorecard.

Everything here reads only the stored run (results + spans), so it is keyless and
deterministic. These are the numbers the report's "Agent Scorecard" renders and
that a hiring manager recognizes: task completion, tool selection, grounding,
trajectory validity, redundant-call rate, and cost/latency budgets.
"""

from __future__ import annotations

import json

import numpy as np

from tracegym.capture.otel import COST_USD, LATENCY_MS


def _nearest_agent_name(by_id: dict, span) -> str | None:
    """Walk parent_id up to the closest ancestor agent span; return its name."""
    pid = span["parent_id"]
    guard = 0
    while pid is not None and guard < 64:
        parent = by_id.get(pid)
        if parent is None:
            return None
        if parent["kind"] == "agent":
            return parent["name"]
        pid = parent["parent_id"]
        guard += 1
    return None


def agent_breakdown(conn, run_id: str) -> list[dict]:
    """Attribute cost, latency and calls to each agent by nearest-ancestor span.

    Returns [] for a single-agent run (no agent spans), so the scorecard only
    grows an agent breakdown when the trace actually used rt.agent().
    """
    rows = conn.execute(
        "SELECT s.id, s.parent_id, s.kind, s.name, s.attributes "
        "FROM spans s JOIN results r ON r.trace_id = s.trace_id WHERE r.run_id = ?",
        (run_id,),
    ).fetchall()
    if not any(r["kind"] == "agent" for r in rows):
        return []
    by_id = {r["id"]: r for r in rows}
    agg: dict[str, dict] = {}
    for r in rows:
        if r["kind"] not in ("llm", "tool"):
            continue
        name = _nearest_agent_name(by_id, r) or "(root)"
        a = agg.setdefault(
            name,
            {"agent": name, "llm_calls": 0, "tool_calls": 0, "cost_usd": 0.0, "latency_ms": 0.0},
        )
        attrs = json.loads(r["attributes"] or "{}")
        if r["kind"] == "llm":
            a["llm_calls"] += 1
            a["cost_usd"] += float(attrs.get(COST_USD, 0.0))
        else:
            a["tool_calls"] += 1
        a["latency_ms"] += float(attrs.get(LATENCY_MS, 0.0))
    for a in agg.values():
        a["cost_usd"] = round(a["cost_usd"], 8)
        a["latency_ms"] = round(a["latency_ms"], 3)
    return [agg[k] for k in sorted(agg)]


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    arr = np.asarray(values, dtype=float)
    return {
        "p50": round(float(np.percentile(arr, 50)), 3),
        "p95": round(float(np.percentile(arr, 95)), 3),
        "max": round(float(arr.max()), 3),
    }


def redundant_tool_rate(conn, run_id: str) -> float:
    """Fraction of tool calls that repeat an identical (name, input) within a trace."""
    rows = conn.execute(
        """
        SELECT s.trace_id AS trace_id, s.name AS name, s.input_sha AS input_sha
        FROM spans s JOIN results r ON r.trace_id = s.trace_id
        WHERE r.run_id = ? AND s.kind = 'tool'
        """,
        (run_id,),
    ).fetchall()
    if not rows:
        return 0.0
    seen: dict[tuple, int] = {}
    for row in rows:
        key = (row["trace_id"], row["name"], row["input_sha"])
        seen[key] = seen.get(key, 0) + 1
    redundant = sum(n - 1 for n in seen.values())
    return round(redundant / len(rows), 4)


def suite_scorecard(conn, run_id: str, success_threshold: float = 1.0) -> dict:
    """Aggregate one run into an agent scorecard."""
    rows = conn.execute("SELECT * FROM results WHERE run_id = ?", (run_id,)).fetchall()
    n = len(rows)
    if n == 0:
        return {"cases": 0}

    scores = [r["score"] for r in rows]
    costs = [r["cost_usd"] for r in rows]
    latencies = [r["latency_ms"] for r in rows]

    check_tally: dict[str, dict] = {}
    invariant_failures = 0
    for r in rows:
        invariant_failures += r["l1_invariant_fail"]
        for c in json.loads(r["l1_results"] or "[]"):
            t = check_tally.setdefault(c["name"], {"passed": 0, "total": 0})
            t["total"] += 1
            t["passed"] += 1 if c["passed"] else 0

    check_pass_rates = {
        name: round(t["passed"] / t["total"], 4) for name, t in check_tally.items() if t["total"]
    }

    scorecard = {
        "cases": n,
        "task_success_rate": round(sum(1 for s in scores if s >= success_threshold) / n, 4),
        "mean_score": round(float(np.mean(scores)), 4),
        "total_cost_usd": round(float(np.sum(costs)), 8),
        "mean_cost_usd": round(float(np.mean(costs)), 8),
        "latency_ms": _percentiles(latencies),
        "total_tool_calls": int(sum(r["tool_calls"] for r in rows)),
        "redundant_tool_rate": redundant_tool_rate(conn, run_id),
        "invariant_failures": invariant_failures,
        "check_pass_rates": check_pass_rates,
    }
    agents = agent_breakdown(conn, run_id)
    if agents:
        scorecard["agents"] = agents
    return scorecard
