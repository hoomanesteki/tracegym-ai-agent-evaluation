"""rt.agent nests spans; metrics attribute cost per agent; trajectory has depth."""

from __future__ import annotations

from tracegym.inspection import trajectory
from tracegym.metrics import agent_breakdown, suite_scorecard
from tracegym.replay import run_suite
from tracegym.store import connect


def _responder(messages, model):
    return "ok"


def _multi_agent(rt, case):
    @rt.tool
    def search(query):
        return ["hit"]

    with rt.agent("orchestrator"):
        with rt.agent("retriever"):
            search(query="q")
            rt.chat([{"role": "user", "content": "rank"}])
        with rt.agent("writer"):
            rt.chat([{"role": "user", "content": "write"}])
    return {"answer": "ok"}


def _single_agent(rt, case):
    rt.chat([{"role": "user", "content": "hi"}])
    return {"answer": "ok"}


CASES = [{"id": "c1", "input": {}, "checks": [{"type": "contains", "value": "ok"}]}]


def _run(tmp_path, agent):
    conn = connect()
    run = run_suite(
        conn, tmp_path, suite_id="s", cases=CASES, agent=agent, mode="record", responder=_responder
    )
    return conn, run


def test_agent_spans_nest_via_parent_id(tmp_path):
    conn, run = _run(tmp_path, _multi_agent)
    spans = trajectory(conn, f"{run}-c1")
    by_name = {s["name"]: s for s in spans}
    assert by_name["orchestrator"]["kind"] == "agent"
    assert by_name["orchestrator"]["depth"] == 0
    assert by_name["retriever"]["depth"] == 1
    assert by_name["writer"]["depth"] == 1
    # the search tool sits under the retriever, two levels deep
    assert by_name["search"]["depth"] == 2
    assert by_name["search"]["parent_id"] == by_name["retriever"]["id"]


def test_agent_span_encloses_its_children_in_time(tmp_path):
    conn, run = _run(tmp_path, _multi_agent)
    spans = {s["name"]: s for s in trajectory(conn, f"{run}-c1")}
    orch = spans["orchestrator"]
    for child in ("retriever", "writer", "search"):
        assert orch["start_ns"] <= spans[child]["start_ns"]
        assert orch["end_ns"] >= spans[child]["end_ns"]


def test_agent_breakdown_attributes_calls_to_nearest_agent(tmp_path):
    conn, run = _run(tmp_path, _multi_agent)
    by_agent = {a["agent"]: a for a in agent_breakdown(conn, run)}
    # the tool call belongs to the retriever, not the orchestrator or writer
    assert by_agent["retriever"]["tool_calls"] == 1
    assert by_agent["retriever"]["llm_calls"] == 1
    assert by_agent["writer"]["tool_calls"] == 0
    assert by_agent["writer"]["llm_calls"] == 1
    assert "orchestrator" not in by_agent  # it delegates, it makes no direct calls


def test_single_agent_run_has_no_breakdown(tmp_path):
    conn, run = _run(tmp_path, _single_agent)
    assert agent_breakdown(conn, run) == []
    assert "agents" not in suite_scorecard(conn, run)


def test_multi_agent_scorecard_includes_agents(tmp_path):
    conn, run = _run(tmp_path, _multi_agent)
    sc = suite_scorecard(conn, run)
    assert "agents" in sc
    assert {a["agent"] for a in sc["agents"]} == {"retriever", "writer"}


def test_waterfall_geometry():
    from tracegym.report.charts import waterfall

    assert waterfall([]) is None
    spans = [
        {"kind": "agent", "name": "orchestrator", "start_ns": 0, "end_ns": 100, "depth": 0},
        {"kind": "tool", "name": "search", "start_ns": 0, "end_ns": 0, "depth": 1},  # zero width
    ]
    chart = waterfall(spans)
    assert len(chart["rows"]) == 2
    assert chart["rows"][1]["w"] >= 2.0  # zero-duration span still gets a visible bar
    assert chart["rows"][1]["label_x"] > chart["rows"][0]["label_x"]  # deeper indents further
