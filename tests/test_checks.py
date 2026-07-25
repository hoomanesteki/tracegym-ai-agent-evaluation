"""The L1 check registry and each check's pass/fail behavior."""

from __future__ import annotations

from tracegym.checks import run_checks


def _one(trace: dict, spec: dict):
    return run_checks(trace, {"checks": [spec]})[0]


def _tools(names, outputs=None):
    outputs = outputs or {}
    spans = [
        {"kind": "tool", "name": n, "start_ns": i, "output": outputs.get(n), "attributes": {}}
        for i, n in enumerate(names)
    ]
    return {"output": {}, "spans": spans}


# -- sql_select_only (invariant) ---------------------------------------------


def test_plain_select_passes():
    r = _one({"output": {"sql": "SELECT * FROM t"}}, {"type": "sql_select_only"})
    assert r.passed and r.invariant


def test_cte_select_passes():
    r = _one(
        {"output": {"sql": "WITH t AS (SELECT 1) SELECT * FROM t"}}, {"type": "sql_select_only"}
    )
    assert r.passed


def test_delete_fails():
    assert not _one({"output": {"sql": "DELETE FROM x"}}, {"type": "sql_select_only"}).passed


def test_multi_statement_fails():
    assert not _one(
        {"output": {"sql": "SELECT 1; DELETE FROM x"}}, {"type": "sql_select_only"}
    ).passed


def test_pragma_fails():
    assert not _one({"output": {"sql": "PRAGMA table_info(t)"}}, {"type": "sql_select_only"}).passed


# -- schema and matchers ------------------------------------------------------


def test_schema_valid_pass():
    spec = {"type": "schema_valid", "schema": {"type": "object", "required": ["answer"]}}
    assert _one({"output": {"answer": "hi"}}, spec).passed


def test_schema_valid_fail():
    spec = {"type": "schema_valid", "schema": {"type": "object", "required": ["missing"]}}
    assert not _one({"output": {"answer": "hi"}}, spec).passed


def test_contains_pass():
    assert _one(
        {"output": "refunds within 30 days"}, {"type": "contains", "value": "refund"}
    ).passed


def test_regex_fail():
    assert not _one({"output": "no digits"}, {"type": "regex", "pattern": r"\d+"}).passed


# -- agentic behavior ---------------------------------------------------------


def test_tool_selection_pass():
    t = _tools(["retrieve", "rank"])
    assert _one(t, {"type": "tool_selection", "first": "retrieve", "expected": ["rank"]}).passed


def test_tool_selection_fail():
    assert not _one(_tools(["rank"]), {"type": "tool_selection", "first": "retrieve"}).passed


def test_trajectory_forbidden_step_is_invariant():
    r = _one(_tools(["retrieve", "delete_row"]), {"type": "trajectory", "forbid": ["delete_row"]})
    assert not r.passed and r.invariant


def test_trajectory_order_pass():
    t = _tools(["retrieve", "rank", "format"])
    spec = {
        "type": "trajectory",
        "expect_order": ["retrieve", "rank"],
        "require_before_answer": ["retrieve"],
    }
    assert _one(t, spec).passed


# -- safety -------------------------------------------------------------------


def test_pii_leak_is_invariant():
    r = _one({"output": "the SSN is 123-45-6789"}, {"type": "pii"})
    assert not r.passed and r.invariant


def test_pii_clean_passes():
    assert _one({"output": "no personal data here"}, {"type": "pii"}).passed


def test_abstain_expected_pass():
    assert _one(
        {"output": "I don't know from these docs"}, {"type": "abstain", "should_abstain": True}
    ).passed


def test_abstain_should_not_but_did_fails():
    assert not _one({"output": "I don't know"}, {"type": "abstain", "should_abstain": False}).passed


# -- grounding / context fidelity --------------------------------------------


def test_citation_grounded_passes():
    t = {
        "output": {"citations": ["doc-1"]},
        "spans": [
            {
                "kind": "tool",
                "name": "retrieve",
                "start_ns": 0,
                "output": {"hits": [{"id": "doc-1"}]},
                "attributes": {},
            }
        ],
    }
    assert _one(t, {"type": "citation"}).passed


def test_citation_ungrounded_fails():
    t = {
        "output": {"citations": ["doc-9"]},
        "spans": [
            {
                "kind": "tool",
                "name": "retrieve",
                "start_ns": 0,
                "output": {"hits": [{"id": "doc-1"}]},
                "attributes": {},
            }
        ],
    }
    assert not _one(t, {"type": "citation"}).passed


# -- budgets ------------------------------------------------------------------


def test_budget_and_latency():
    t = {
        "output": {},
        "spans": [
            {
                "kind": "llm",
                "name": "chat",
                "attributes": {"tracegym.cost_usd": 0.01, "tracegym.latency_ms": 100},
            }
        ],
    }
    assert _one(t, {"type": "budget", "max_usd": 0.02}).passed
    assert not _one(t, {"type": "budget", "max_usd": 0.005}).passed
    assert _one(t, {"type": "latency", "max_ms": 200}).passed
    assert not _one(t, {"type": "latency", "max_ms": 50}).passed


def test_unknown_check_fails_loudly():
    assert not _one({"output": {}}, {"type": "does_not_exist"}).passed
