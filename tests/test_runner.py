"""The run pipeline: record once, replay frozen, and prove it is deterministic."""

from __future__ import annotations

import pytest

from tracegym.capture.tools import FixtureMiss
from tracegym.metrics import suite_scorecard
from tracegym.replay import run_suite
from tracegym.store import connect

CASES = [
    {
        "id": "c1",
        "input": {"question": "What is the refund window?"},
        "checks": [
            {"type": "contains", "value": "Refund"},
            {"type": "citation"},
            {"type": "tool_selection", "first": "retrieve"},
        ],
    }
]


def _agent(rt, case):
    @rt.tool
    def retrieve(query):
        return {"hits": [{"id": "doc-1", "text": "Refunds within 30 days."}]}

    retrieve(query=case["input"]["question"])
    rt.chat([{"role": "user", "content": case["input"]["question"]}])
    return {"answer": "Refunds within 30 days.", "citations": ["doc-1"]}


def _rows(conn, run_id):
    return conn.execute(
        "SELECT case_id, score, output_sha FROM results WHERE run_id = ? ORDER BY case_id",
        (run_id,),
    ).fetchall()


def test_frozen_without_fixtures_refuses_to_fabricate(tmp_path):
    conn = connect()
    with pytest.raises(FixtureMiss):
        run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen")


def test_record_then_two_frozen_runs_are_identical(tmp_path):
    conn = connect()
    run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="record")
    r1 = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen")
    r2 = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen")

    rows1, rows2 = _rows(conn, r1), _rows(conn, r2)
    assert [(x["case_id"], x["score"], x["output_sha"]) for x in rows1] == [
        (x["case_id"], x["score"], x["output_sha"]) for x in rows2
    ]
    assert rows1[0]["score"] == 1.0  # all three checks pass


def test_frozen_replay_makes_no_live_calls(tmp_path):
    conn = connect()
    hits = {"n": 0}

    def responder(messages, model):
        hits["n"] += 1
        return "Refunds within 30 days."

    run_suite(
        conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="record", responder=responder
    )
    calls_after_record = hits["n"]
    run_suite(
        conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen", responder=responder
    )
    assert hits["n"] == calls_after_record  # frozen replay never invoked the model


def test_scorecard_reports_agent_metrics(tmp_path):
    conn = connect()
    run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="record")
    run_id = run_suite(conn, tmp_path, suite_id="s", cases=CASES, agent=_agent, mode="frozen")
    sc = suite_scorecard(conn, run_id)
    assert sc["cases"] == 1
    assert sc["task_success_rate"] == 1.0
    assert sc["check_pass_rates"]["citation"] == 1.0
    assert sc["total_tool_calls"] == 1
