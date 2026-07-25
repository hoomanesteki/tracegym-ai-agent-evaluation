"""Turn a stored run into human-readable per-case detail and regression evidence.

Everything here reads the traces.db plus the blob store that a run already
produced, so it is keyless and deterministic. It is the data behind the report's
drill-down and the `tg cases` / `tg show` / `tg diff` commands: the actual input,
the agent's actual output, every check with its detail, the judge's rationale, and
the trace of tool and LLM calls.
"""

from __future__ import annotations

import json

from tracegym.capture.otel import COST_USD, LATENCY_MS, USAGE_INPUT_TOKENS, USAGE_OUTPUT_TOKENS
from tracegym.store.blobs import get


def _spans(conn, trace_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, name, start_ns, end_ns, attributes FROM spans "
        "WHERE trace_id = ? ORDER BY start_ns, id",
        (trace_id,),
    ).fetchall()
    out = []
    for r in rows:
        a = json.loads(r["attributes"] or "{}")
        out.append(
            {
                "kind": r["kind"],
                "name": r["name"],
                "input_tokens": int(a.get(USAGE_INPUT_TOKENS, 0) or 0),
                "output_tokens": int(a.get(USAGE_OUTPUT_TOKENS, 0) or 0),
                "latency_ms": round(float(a.get(LATENCY_MS, 0) or 0), 3),
                "cost_usd": round(float(a.get(COST_USD, 0) or 0), 8),
            }
        )
    return out


def list_cases(conn, run_id: str) -> list[dict]:
    """One row per case: id, score, invariant failures, judge state."""
    rows = conn.execute(
        "SELECT case_id, score, l1_invariant_fail, judge_state FROM results "
        "WHERE run_id = ? ORDER BY case_id",
        (run_id,),
    ).fetchall()
    return [
        {
            "case_id": r["case_id"],
            "score": r["score"],
            "invariant_fail": r["l1_invariant_fail"],
            "judge_state": r["judge_state"],
        }
        for r in rows
    ]


def case_detail(
    conn, blob_root, run_id: str, case_id: str, case: dict | None = None
) -> dict | None:
    """Full drill-down for one case: input, output, checks, judge, trace."""
    r = conn.execute(
        "SELECT * FROM results WHERE run_id = ? AND case_id = ?", (run_id, case_id)
    ).fetchone()
    if r is None:
        return None
    output = get(blob_root, r["output_sha"])
    checks = json.loads(r["l1_results"] or "[]")
    rationale = conn.execute(
        "SELECT rationale FROM judgments WHERE output_sha = ? AND rationale != '' LIMIT 1",
        (r["output_sha"],),
    ).fetchone()
    return {
        "case_id": case_id,
        "input": (case or {}).get("input"),
        "output": output,
        "checks": checks,
        "judge": {
            "state": r["judge_state"],
            "confidence": r["judge_confidence"],
            "passed": r["judge_pass"],
            "rationale": rationale["rationale"] if rationale else "",
        },
        "score": r["score"],
        "cost_usd": r["cost_usd"],
        "latency_ms": r["latency_ms"],
        "invariant_fail": r["l1_invariant_fail"],
        "spans": _spans(conn, r["trace_id"]),
    }


def regression_examples(conn, blob_root, suites: dict) -> list[dict]:
    """For every seeded bug, one concrete example: baseline vs buggy output plus the
    exact check(s) that caught it. Replays the buggy variant frozen, keyless."""
    from tracegym.demos.bugs import BUGS, buggy_agent
    from tracegym.demos.harness import agent_and_responder
    from tracegym.replay import run_suite

    gallery = []
    for bug in BUGS:
        suite_id = bug["suite"]
        cases = suites[suite_id]["cases"]
        base_run = suites[suite_id]["baseline_run"]
        base_agent, responder = agent_and_responder(suite_id)
        buggy_run = run_suite(
            conn,
            blob_root,
            suite_id=suite_id,
            cases=cases,
            agent=buggy_agent(base_agent, bug["fn"]),
            mode="frozen",
            responder=responder,
        )
        example = None
        for c in cases:
            buggy = case_detail(conn, blob_root, buggy_run, c["id"], c)
            failing = [ch for ch in buggy["checks"] if not ch["passed"]]
            if failing:
                baseline = case_detail(conn, blob_root, base_run, c["id"], c)
                example = {
                    "case_id": c["id"],
                    "input": c.get("input"),
                    "baseline_output": baseline["output"],
                    "buggy_output": buggy["output"],
                    "failing_checks": [
                        {"name": ch["name"], "detail": ch["detail"], "invariant": ch["invariant"]}
                        for ch in failing
                    ],
                }
                break
        gallery.append(
            {
                "id": bug["id"],
                "suite": suite_id,
                "desc": bug["desc"],
                "caught": example is not None,
                "example": example,
            }
        )
    return gallery
