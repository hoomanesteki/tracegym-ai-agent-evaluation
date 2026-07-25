"""Wiring shared by the demo build and the `tg demo` command.

Maps a suite to its agent and local responder, provides the trivial canary agent,
and measures seeded-regression recall by gating each buggy variant against the
baseline. All keyless and deterministic.
"""

from __future__ import annotations

from tracegym.config import GateConfig
from tracegym.demos.bugs import BUGS, buggy_agent
from tracegym.demos.data import CORPUS, SCHEMA_TEXT, SQL_QA
from tracegym.demos.sql_analyst import make_sql_analyst, sql_responder
from tracegym.demos.support_rag import context_responder, make_support_rag
from tracegym.gate import gate_runs
from tracegym.replay import run_suite


def canary_agent(rt, case) -> dict:
    """Meta-judge agent: return the canary's authored answer verbatim."""
    return {"answer": case["input"].get("answer", "")}


def agent_and_responder(suite_id: str):
    """Return (agent, responder) for a demo suite."""
    if suite_id == "support-rag":
        return make_support_rag(CORPUS), context_responder
    if suite_id == "sql-analyst":
        sql_map = dict(SQL_QA)
        return make_sql_analyst(SCHEMA_TEXT), sql_responder(sql_map)
    if suite_id == "meta-judge":
        return canary_agent, None
    raise ValueError(f"unknown demo suite: {suite_id}")


def seeded_bug_recall(conn, blob_root, suites: dict) -> dict:
    """Run every seeded bug in frozen mode and gate it against the baseline.

    suites maps suite_id -> {"cases": [...], "baseline_run": run_id}. Returns the
    recall summary: which bugs the gate blocked and which it missed.
    """
    cfg = GateConfig()
    caught, missed = [], []
    for bug in BUGS:
        suite_id = bug["suite"]
        cases = suites[suite_id]["cases"]
        base_run = suites[suite_id]["baseline_run"]
        base_agent, responder = agent_and_responder(suite_id)
        agent = buggy_agent(base_agent, bug["fn"])
        buggy_run = run_suite(
            conn,
            blob_root,
            suite_id=suite_id,
            cases=cases,
            agent=agent,
            mode="frozen",
            responder=responder,
        )
        result = gate_runs(conn, buggy_run, base_run, cfg)
        record = {
            "id": bug["id"],
            "desc": bug["desc"],
            "caught_by": bug["caught_by"],
            "blocked": result.blocked,
        }
        (caught if result.blocked else missed).append(record)
    return {
        "total": len(BUGS),
        "caught": len(caught),
        "missed": len(missed),
        "details": caught + missed,
    }
