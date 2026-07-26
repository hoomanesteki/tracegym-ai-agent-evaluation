"""Drive an agent over a suite of cases and record a scored run.

An agent is any callable ``agent(runtime, case) -> output``. The runner gives each
case its own Runtime (so its spans and fixtures are isolated by trace id), lets the
agent produce an output, rebuilds the trace from stored spans, runs the L1 checks,
scores the case, and writes a results row. In frozen mode the whole loop needs no
keys and is byte-identical across reruns, which is the determinism contract.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tracegym.capture.otel import (
    COST_USD,
    LATENCY_MS,
    USAGE_INPUT_TOKENS,
    USAGE_OUTPUT_TOKENS,
)
from tracegym.capture.tools import Runtime
from tracegym.checks import CheckResult, run_checks
from tracegym.store.blobs import get, put

Agent = Callable[[Runtime, dict], object]

_MODE_TO_RUNTIME = {"frozen": "frozen_strict", "record": "frozen_record", "live": "live"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def score_case(results: list[CheckResult]) -> tuple[float, int]:
    """Per-case quality in [0,1] plus the count of failed invariants.

    A failed invariant forces the score to 0 (a hard failure cannot be averaged
    away). Otherwise the score is the fraction of checks that passed.
    """
    invariant_fail = sum(1 for r in results if r.invariant and not r.passed)
    if invariant_fail:
        return 0.0, invariant_fail
    if not results:
        return 1.0, 0
    return sum(1 for r in results if r.passed) / len(results), 0


def build_trace(conn, blob_root: Path, trace_id: str, case: dict, output: object) -> dict:
    """Reassemble the scored trace from stored spans plus the agent's output."""
    rows = conn.execute(
        "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ns, id", (trace_id,)
    ).fetchall()
    spans = []
    for r in rows:
        span = {
            "kind": r["kind"],
            "name": r["name"],
            "start_ns": r["start_ns"],
            "end_ns": r["end_ns"],
            "attributes": json.loads(r["attributes"] or "{}"),
        }
        if r["output_sha"]:
            try:
                span["output"] = get(blob_root, r["output_sha"])
            except Exception:
                span["output"] = None
        spans.append(span)
    return {"input": case.get("input"), "output": output, "spans": spans}


def _span_metrics(trace: dict) -> dict:
    cost = latency = in_tok = out_tok = 0.0
    tool_calls = 0
    for span in trace["spans"]:
        attrs = span["attributes"]
        cost += float(attrs.get(COST_USD, 0) or 0)
        latency += float(attrs.get(LATENCY_MS, 0) or 0)
        if span["kind"] == "llm":
            in_tok += float(attrs.get(USAGE_INPUT_TOKENS, 0) or 0)
            out_tok += float(attrs.get(USAGE_OUTPUT_TOKENS, 0) or 0)
        elif span["kind"] == "tool":
            tool_calls += 1
    return {
        "cost_usd": round(cost, 8),
        "latency_ms": round(latency, 3),
        "input_tokens": int(in_tok),
        "output_tokens": int(out_tok),
        "tool_calls": tool_calls,
    }


def run_suite(
    conn,
    blob_root: str | Path,
    *,
    suite_id: str,
    cases: list[dict],
    agent: Agent,
    mode: str = "frozen",
    model: str = "llama-3.1-8b-instant",
    provider: str = "local",
    prices: dict | None = None,
    responder: Callable | None = None,
    seed: int = 0,
    config_sha: str | None = None,
) -> str:
    """Run every case, store results, and return the run id."""
    if mode not in _MODE_TO_RUNTIME:
        raise ValueError(f"unknown run mode: {mode}")
    blob_root = Path(blob_root)
    run_id = "run-" + uuid.uuid4().hex[:12]
    rt_mode = _MODE_TO_RUNTIME[mode]

    scores = []
    for case in cases:
        trace_id = f"{run_id}-{case['id']}"
        rt = Runtime(
            conn,
            blob_root,
            mode=rt_mode,
            trace_id=trace_id,
            provider=provider,
            model=model,
            prices=prices,
            responder=responder,
            seed=seed,
        )
        output = agent(rt, case)
        trace = build_trace(conn, blob_root, trace_id, case, output)
        results = run_checks(trace, case)
        score, invariant_fail = score_case(results)
        scores.append(score)
        metrics = _span_metrics(trace)
        output_sha = put(blob_root, output)

        conn.execute(
            "INSERT OR REPLACE INTO traces (id, case_id, input_sha, output_sha, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (trace_id, case["id"], put(blob_root, case.get("input")), output_sha, _now()),
        )
        conn.execute(
            """
            INSERT INTO results
                (id, run_id, case_id, trace_id, output_sha, l1_results, l1_invariant_fail,
                 judge_pass, score, cost_usd, latency_ms, input_tokens, output_tokens,
                 tool_calls, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "res-" + uuid.uuid4().hex[:12],
                run_id,
                case["id"],
                trace_id,
                output_sha,
                json.dumps([r.to_dict() for r in results]),
                invariant_fail,
                score,
                metrics["cost_usd"],
                metrics["latency_ms"],
                metrics["input_tokens"],
                metrics["output_tokens"],
                metrics["tool_calls"],
                _now(),
            ),
        )

    # Snapshot the scorecard into the run's summary so a run row is self-describing
    # for over-time trends even if its per-case results are later pruned.
    from tracegym.metrics import suite_scorecard

    sc = suite_scorecard(conn, run_id)
    summary = {
        "cases": len(cases),
        "mean_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
        "task_success_rate": sc.get("task_success_rate", 0.0),
        "cost_usd": sc.get("total_cost_usd", 0.0),
        "p95_latency_ms": sc.get("latency_ms", {}).get("p95", 0.0),
        "invariant_failures": sc.get("invariant_failures", 0),
        "tool_calls": sc.get("total_tool_calls", 0),
    }
    conn.execute(
        "INSERT INTO runs (id, suite_id, mode, model, git_sha, config_sha, created_at, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, suite_id, mode, model, _git_sha(), config_sha, _now(), json.dumps(summary)),
    )
    conn.commit()
    return run_id
